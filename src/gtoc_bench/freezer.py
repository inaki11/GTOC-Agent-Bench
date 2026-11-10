"""Confirmed CPU freeze with bounded engine calls and host-owned diagnostics."""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

from .supervision import counters

COMMAND_TIMEOUT = 15
TRANSITION_TIMEOUT = 10


def process_cgroup(pid):
    return next(
        line[3:]
        for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
        if line.startswith("0::")
    )


def podman_cgroup(inspected):
    """Only the exact local container leaf, never a user/host/parent cgroup."""
    cid, pid = inspected["Id"], inspected["State"]["Pid"]
    if not re.fullmatch(r"[0-9a-f]{64}", cid) or pid <= 1:
        raise ValueError("Invalid local container identity")
    root = Path("/sys/fs/cgroup")
    directory = (root / process_cgroup(pid).lstrip("/")).resolve()
    known_leaf = (
        directory.name == "container" and directory.parent.name == f"libpod-{cid}.scope"
    ) or directory.name == f"libpod-{cid}"
    own = (root / process_cgroup("self").lstrip("/")).resolve()
    if not known_leaf or not directory.is_relative_to(root) or own.is_relative_to(directory):
        raise ValueError("Cannot safely identify a dedicated Podman container cgroup")
    if not os.access(directory / "cgroup.freeze", os.W_OK):
        raise PermissionError("The container freezer is not delegated to this host user")
    return directory


class ContainerFreezer:
    def __init__(self, runner):
        self.runner = runner
        self.backend = "container_engine"
        self.directory = None
        self.inode = None

    def inspect(self):
        result = subprocess.run(
            ["docker", "inspect", self.runner.name],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return json.loads(result.stdout)[0]

    def prepare(self, engine):
        reason = None
        if engine == "podman":
            try:
                self.directory = podman_cgroup(self.inspect())
                info = self.directory.stat()
                self.inode = (info.st_dev, info.st_ino)
                self.backend = "host_cgroup_v2"
            except (
                OSError,
                ValueError,
                KeyError,
                StopIteration,
                subprocess.SubprocessError,
            ) as exc:
                self.directory = None
                reason = type(exc).__name__
        report = {
            "backend": self.backend,
            "container": self.runner.name,
            "cgroup": str(self.directory) if self.directory else None,
            "fallback_reason": reason,
            "engine_command_timeout_seconds": COMMAND_TIMEOUT,
            "kernel_transition_timeout_seconds": TRANSITION_TIMEOUT,
            "engine_state_authoritative": self.backend == "container_engine",
        }
        self.runner.store.write("freezer.json", report)
        return report

    def identity(self):
        info = self.directory.stat()
        if (info.st_dev, info.st_ino) != self.inode:
            raise RuntimeError("Container cgroup was replaced; refusing access")

    def observe(self):
        if self.directory:
            self.identity()
            return {
                "freeze_requested": (self.directory / "cgroup.freeze").read_text().strip(),
                **counters(self.directory / "cgroup.events"),
            }
        state = self.inspect()["State"]
        return {"frozen": int(state["Paused"]), "populated": int(state["Running"])}

    def diagnostics(self):
        try:
            result = self.observe()
        except Exception as exc:
            result = {"observation_error": type(exc).__name__}
        if self.directory:
            for name in ("cpu.stat", "cpu.pressure", "memory.events", "memory.pressure"):
                try:
                    result[name] = (self.directory / name).read_text()[:2000]
                except OSError:
                    pass
            try:
                pids = (self.directory / "cgroup.procs").read_text().split()
                result["process_count"] = len(pids)
                result["processes"] = []
                for pid in pids[:32]:
                    proc = Path("/proc") / pid
                    try:
                        # Never collect command lines, environments or agent file contents.
                        result["processes"].append(
                            {
                                "pid": int(pid),
                                "comm": (proc / "comm").read_text().strip(),
                                "wchan": (proc / "wchan").read_text().strip(),
                            }
                        )
                    except OSError:
                        pass
            except OSError:
                pass
        return result

    def record(self, **data):
        self.runner.store.append(
            "freezer.jsonl", "freezer_transition", backend=self.backend, **data
        )

    def engine_command(self, action):
        # Kill the whole short-lived CLI group on timeout, including its OCI child.
        # Do not leave a late pause capable of racing a subsequent unpause.
        with subprocess.Popen(
            ["docker", action, self.runner.name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            try:
                _, stderr = process.communicate(timeout=COMMAND_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=5)
                raise
            if process.returncode:
                raise RuntimeError(f"Container {action} failed: {stderr[-1000:]}")

    def transition(self, frozen):
        action, began = ("pause" if frozen else "unpause"), time.monotonic()
        target = int(frozen)
        if self.directory:
            try:
                self.identity()
                (self.directory / "cgroup.freeze").write_text(str(target))
                deadline = time.monotonic() + TRANSITION_TIMEOUT
                while True:
                    observed = self.observe()
                    if observed["frozen"] == target and observed["populated"]:
                        self.record(
                            action=action,
                            outcome="confirmed",
                            seconds=time.monotonic() - began,
                            observed=observed,
                        )
                        return
                    if time.monotonic() >= deadline or (
                        frozen and self.runner.budget.stopped.is_set()
                    ):
                        raise RuntimeError(f"Kernel did not confirm container {action}")
                    time.sleep(0.05)
            except Exception as exc:
                self.record(
                    action=action,
                    outcome="failed",
                    error_type=type(exc).__name__,
                    seconds=time.monotonic() - began,
                    observed=self.diagnostics(),
                )
                raise
        else:
            # A lost CLI acknowledgement does not prove that the transition failed.
            # Reconcile state before one bounded, idempotent retry.
            for attempt in range(2):
                error = None
                try:
                    self.engine_command(action)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    error = type(exc).__name__
                observed = self.diagnostics()
                confirmed = observed.get("frozen") == target and observed.get("populated") == 1
                self.record(
                    action=action,
                    attempt=attempt + 1,
                    outcome="confirmed" if confirmed else "unconfirmed",
                    command_error=error,
                    seconds=time.monotonic() - began,
                    observed=observed,
                )
                if confirmed:
                    return
                if frozen and self.runner.budget.stopped.is_set():
                    break
            raise RuntimeError(f"Cannot confirm container {action}; see freezer.jsonl")
