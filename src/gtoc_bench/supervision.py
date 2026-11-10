"""Independent liveness and host-side memory guard, outside the agent's cgroup."""

import errno
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .storage import atomic_write

CONTROL_DIRECTORY = "/run/gtoc-control"
HEARTBEAT_TIMEOUT = 15
MEMORY_WARNING_FRACTION = 0.75
MEMORY_STOP_FRACTION = 0.90


class HeartbeatFile:
    def __init__(self, directory):
        self.path = Path(directory) / "heartbeat.json"
        self.sequence = 0

    def beat(self):
        self.sequence += 1
        atomic_write(
            self.path, json.dumps({"sequence": self.sequence, "pid": os.getpid()}).encode()
        )


class HeartbeatReader:
    def __init__(self, directory=CONTROL_DIRECTORY, clock=time.monotonic):
        self.path = Path(directory) / "heartbeat.json"
        self.clock = clock
        self.sequence = 0
        self.last_received = clock()

    def verify_mount(self):
        # Some amd64-on-ARM runtimes omit ST_RDONLY for a read-only bind mount.
        # Test enforcement, not translated statvfs flags. Only EROFS proves this;
        # a permissions error on a writable mount must never pass the check.
        probe = self.path.parent / (".readonly-check-" + uuid.uuid4().hex)
        try:
            fd = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            if exc.errno == errno.EROFS:
                return
            raise ValueError("Cannot confirm a read-only controller heartbeat mount") from exc
        else:
            os.close(fd)
            probe.unlink()
            raise ValueError("Controller heartbeat mount must be read-only inside the container")

    def poll(self):
        try:
            value = json.loads(self.path.read_text())
            sequence = value.get("sequence")
            if type(sequence) is int and sequence > self.sequence:
                self.sequence = sequence
                self.last_received = self.clock()
        except (OSError, ValueError):
            pass  # Keep the previous confirmed observation, never extend its deadline.
        return {
            "channel": "private_read_only_file",
            "sequence": self.sequence,
            "age_seconds": self.clock() - self.last_received,
            "timeout_seconds": HEARTBEAT_TIMEOUT,
        }


def counters(path):
    return {
        key: int(value) for key, value in (line.split() for line in path.read_text().splitlines())
    }


def memory_sample(directory):
    directory = Path(directory)
    current = int((directory / "memory.current").read_text())
    limit = (directory / "memory.max").read_text().strip()
    stats = counters(directory / "memory.stat")
    return {
        "source": "host_cgroup_v2",
        "current_bytes": current,
        "limit_bytes": None if limit == "max" else int(limit),
        "working_set_bytes": max(0, current - stats.get("inactive_file", 0)),
        "inactive_file_bytes": stats.get("inactive_file", 0),
        "anonymous_bytes": stats.get("anon"),
        "events": counters(directory / "memory.events"),
        "pressure": (directory / "memory.pressure").read_text().strip()
        if (directory / "memory.pressure").exists()
        else None,
    }


def size_bytes(text):
    match = re.fullmatch(r"\s*([\d.]+)\s*([KMGTPE]?i?B)\s*", text, flags=re.I)
    if not match:
        raise ValueError("Invalid container memory size")
    value, unit = float(match[1]), match[2].upper()
    power = "KMGTPE".find(unit[0]) + 1 if unit != "B" else 0
    return int(value * (1024 if "I" in unit else 1000) ** power)


class MemoryGuard:
    def __init__(self, runner):
        self.runner = runner
        self.directory = None
        self.latest = {}
        self.baseline_oom = None
        self.stop = threading.Event()
        self.thread = None
        self.warned = False

    def resolve(self):
        try:
            inspected = subprocess.run(
                ["docker", "inspect", self.runner.name],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            pid = json.loads(inspected.stdout)[0]["State"]["Pid"]
            path = next(
                line[3:]
                for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                if line.startswith("0::")
            )
            root = Path("/sys/fs/cgroup")
            directory = (root / path.lstrip("/")).resolve()
            if directory.is_relative_to(root) and (directory / "memory.current").is_file():
                self.directory = directory
        except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
            pass

    def sample(self):
        if self.directory is not None:
            return memory_sample(self.directory)
        # Docker Desktop's Linux cgroup is not visible from the macOS controller.
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", self.runner.name],
            capture_output=True,
            text=True,
            check=True,
            timeout=8,
        )
        used, limit = result.stdout.strip().split("/")
        return {
            "source": "container_engine_stats",
            "working_set_bytes": size_bytes(used),
            "limit_bytes": size_bytes(limit),
            "events": {},
            "current_bytes": None,
        }

    def start(self):
        self.resolve()
        self.runner.store.write(
            "supervision.json",
            {
                "heartbeat_channel": "private_read_only_file",
                "heartbeat_timeout_seconds": HEARTBEAT_TIMEOUT,
                "memory_guard": "host_process_outside_container",
                "memory_source": "host_cgroup_v2" if self.directory else "container_engine_stats",
                "warning_working_set_fraction": MEMORY_WARNING_FRACTION,
                "stop_working_set_fraction": MEMORY_STOP_FRACTION,
                "hard_reservation": False,
                "cgroup_memory_max_is_kernel_enforced": True,
            },
        )
        self.thread = threading.Thread(target=self.watch, daemon=True)
        self.thread.start()

    def observe(self, sample):
        self.latest = sample
        events = sample.get("events", {})
        oom = events.get("oom_kill", 0)
        if self.baseline_oom is None:
            self.baseline_oom = 0
        limit = sample.get("limit_bytes") or self.runner.memory_limit_bytes
        sample["working_set_fraction"] = sample["working_set_bytes"] / limit if limit else None
        self.runner.memory_status = sample
        self.runner.store.append("memory.jsonl", "memory_observed", sample=sample)
        ratio = sample["working_set_fraction"] or 0
        if ratio >= MEMORY_WARNING_FRACTION and not self.warned:
            self.runner.store.event("memory_pressure_warning", sample=sample)
            self.warned = True
        if ratio < MEMORY_WARNING_FRACTION - 0.05:
            self.warned = False
        if ratio >= MEMORY_STOP_FRACTION or oom > self.baseline_oom:
            self.cutoff(
                sample,
                "oom_kill_observed" if oom > self.baseline_oom else "working_set_safety_margin",
            )

    def cutoff(self, sample, cause):
        runner = self.runner
        if runner.budget.stopped.is_set():
            return
        runner.budget.stop("memory_limit")
        if runner.gateway:
            runner.gateway.cancel()
        runner.store.event("memory_limit_reached", cause=cause, sample=sample)
        # The runtime starts this small trusted helper; it does not wait for the
        # model-response pipe or for the memory-starved Python supervisor.
        command = "import os,signal; from pathlib import Path\nfor p in Path('/proc').iterdir():\n if p.name.isdigit():\n  try:\n   if p.stat().st_uid==1000:os.kill(int(p.name),signal.SIGKILL)\n  except (OSError,ProcessLookupError):pass\n"
        try:
            subprocess.run(
                [
                    "docker",
                    "exec",
                    "--user",
                    "0:0",
                    runner.name,
                    "/usr/local/bin/python",
                    "-s",
                    "-P",
                    "-c",
                    command,
                ],
                capture_output=True,
                check=True,
                timeout=8,
            )
            runner.store.event("memory_guard_agent_stopped", method="runtime_exec_uid1000")
        except (OSError, subprocess.SubprocessError) as exc:
            runner.store.event("memory_guard_stop_failed", error=type(exc).__name__)
            try:
                subprocess.run(
                    ["docker", "kill", "--signal", "SIGTERM", runner.name],
                    capture_output=True,
                    check=True,
                    timeout=8,
                )
            except (OSError, subprocess.SubprocessError):
                pass  # The normal finalizer independently enforces container removal.

    def watch(self):
        while not self.stop.is_set() and not self.runner.budget.stopped.is_set():
            try:
                self.observe(self.sample())
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                if not self.stop.is_set() and not self.runner.budget.stopped.is_set():
                    self.runner.store.event(
                        "memory_guard_observation_error", error=type(exc).__name__
                    )
            self.stop.wait(0.5)

    def close(self):
        self.stop.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=18)
            if self.thread.is_alive():
                raise RuntimeError("Host memory guard did not finish; refusing a successful seal")
