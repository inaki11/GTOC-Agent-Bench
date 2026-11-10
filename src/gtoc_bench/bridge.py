"""Trusted container supervisor. Loopback HTTP is relayed over Docker stdin/stdout.

The agent is UID 1000, validators UID 1001. Neither inherits the controller pipes.
No host ports, Docker socket, host filesystem or real model credentials are exposed.
"""

import base64
import hashlib
import json
import os
import queue
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import ROOT
from .agents import cache_environment, launch_spec
from .observation import SnapshotRequests, WorkspaceObserver, resource_sample, workspace_usage
from .problems import client_timeout, specification, supervisor_timeout
from .supervision import CONTROL_DIRECTORY, HEARTBEAT_TIMEOUT, HeartbeatReader
from .validator import verify_materials

MAX_FRAME = 160 * 1024**2  # Includes base64 of a 100 MB GTOC13 candidate.


class Bridge:
    def __init__(self):
        self.write_lock = threading.Lock()
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.config = None
        self.agent = None
        self.workspace_owner = None
        self.observer = WorkspaceObserver()
        self.observing = threading.Event()
        self.observation_stop = threading.Event()
        self.snapshots = SnapshotRequests()
        self.validation_slot = threading.Lock()
        self.output_readers = []
        self.last_workspace_usage = None
        self.codex_catalog_path = None
        self.last_heartbeat = time.monotonic()
        self.heartbeat_reader = (
            HeartbeatReader() if (Path(CONTROL_DIRECTORY) / "heartbeat.json").exists() else None
        )
        self.stop = threading.Event()

    def send(self, kind, **data):
        with self.write_lock:
            sys.stdout.write(json.dumps({"type": kind, **data}, allow_nan=False) + "\n")
            sys.stdout.flush()

    def relay(self, handler):
        if handler.headers.get("Transfer-Encoding"):
            handler.send_error(400, "Chunked requests are not supported")
            return
        try:
            size = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            handler.send_error(400)
            return
        problem = (self.config or {}).get("problem", "gtoc12")
        if not 0 <= size <= specification(problem)["max_bytes"]:
            handler.send_error(413)
            return
        body = handler.rfile.read(size)
        if len(body) != size:
            handler.send_error(400)
            return
        request_id = uuid.uuid4().hex
        replies = queue.Queue()
        with self.pending_lock:
            self.pending[request_id] = replies
        self.send(
            "request",
            id=request_id,
            method=handler.command,
            path=handler.path,
            headers={
                k.lower(): v
                for k, v in handler.headers.items()
                if k.lower()
                in {"content-type", "anthropic-version", "anthropic-beta", "x-gtoc-submission-id"}
            },
            body=base64.b64encode(body).decode(),
        )
        sent_headers = False
        try:
            while not self.stop.is_set():
                try:
                    reply = replies.get(timeout=1)
                except queue.Empty:
                    continue
                if not sent_headers:
                    handler.send_response(reply.get("status", 200))
                    for key, value in reply.get("headers", {}).items():
                        handler.send_header(key, value)
                    handler.send_header("Connection", "close")
                    handler.end_headers()
                    sent_headers = True
                if reply.get("body"):
                    handler.wfile.write(base64.b64decode(reply["body"], validate=True))
                    handler.wfile.flush()
                if reply.get("end"):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.send("client_disconnected", id=request_id)
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)
            handler.close_connection = True

    def prepare_temporary_dirs(self):
        # Podman cannot express arbitrary UID/GID on --tmpfs. Only the trusted
        # supervisor changes ownership, before any agent or validator starts.
        directories = {"/judge-tmp": 1001}
        if self.config["agent"]["id"] == "swe_agent":
            directories["/root"] = 1000
        for directory, identity in directories.items():
            info = os.lstat(directory)
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError(
                    "Protected temporary directories must be private directories (700)"
                )
            os.chown(directory, identity, identity, follow_symlinks=False)

    def prepare(self, config, codex_catalog=None, codex_catalog_source=None):
        try:
            self.config = config
            self.prepare_temporary_dirs()
            if self.heartbeat_reader:
                self.heartbeat_reader.verify_mount()
            problem = config.get("problem", "gtoc12")
            materials = verify_materials(problem)
            self.check_judge(problem)
            self.prepare_problem(problem)
            self.prepare_agent_caches()
            versions = {}
            expected = config["agent"]["version"]
            agent_id = config["agent"]["id"]
            host_claude = (
                agent_id == "claude_code"
                and config.get("provider", {}).get("auth_method") == "claude_subscription"
            )
            if host_claude:
                # Runner verifies the pinned official host binary before creating
                # this container. No Claude executable is used inside it in this
                # mode (and an unused x86 client may abort under ARM emulation).
                actual = None
                versions.update(claude_execution="host", claude_expected_version=expected)
            elif agent_id in {"codex", "claude_code"}:
                name = "codex" if agent_id == "codex" else "claude"
                actual = subprocess.check_output([name, "--version"], timeout=60, text=True).strip()
                versions[name] = actual
            else:
                package = {
                    "openhands": "openhands",
                    "swe_agent": "sweagent",
                    "mini_swe_agent": "mini-swe-agent",
                    "antigravity": "google-antigravity",
                }[agent_id]
                actual = subprocess.check_output(
                    [
                        "/opt/antigravity-python/bin/python"
                        if agent_id == "antigravity"
                        else "/opt/agents-python/bin/python",
                        "-s",
                        "-P",
                        "-c",
                        "from importlib.metadata import version; import sys; print(version(sys.argv[1]))",
                        package,
                    ],
                    text=True,
                    timeout=30,
                ).strip()
                versions[agent_id] = actual
            if actual is not None and expected not in actual.split():
                raise ValueError(f"Agent version mismatch: expected {expected}, got {actual}")
            if agent_id == "codex":
                self.prepare_codex_model(config, codex_catalog, codex_catalog_source)
            probe = subprocess.check_output(
                [
                    sys.executable,
                    "-s",
                    "-P",
                    "-c",
                    "import json; from gtoc_bench.hardware import probe; "
                    "print('__GTOC_PROBE__'+json.dumps(probe()))",
                ],
                text=True,
                timeout=120,
                user=1000,
                group=1000,
                extra_groups=(),
                env=os.environ | {"HOME": "/workspace"} | cache_environment(),
            )
            science = json.loads(
                next(
                    line.removeprefix("__GTOC_PROBE__")
                    for line in probe.splitlines()
                    if line.startswith("__GTOC_PROBE__")
                )
            )
            # The host persists this inventory before checking CUDA access, so a
            # failed GPU preflight retains its measurements as well as its error.
            self.send(
                "ready",
                versions=versions,
                science=science,
                materials=materials,
                cpu_count=os.cpu_count(),
                supervision={
                    "heartbeat_channel": "private_read_only_file"
                    if self.heartbeat_reader
                    else "legacy_stdin",
                    "heartbeat_timeout_seconds": HEARTBEAT_TIMEOUT if self.heartbeat_reader else 10,
                },
                source_hashes={
                    str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted((ROOT / "src").rglob("*.py"))
                },
            )
        except Exception as exc:
            self.send("fatal", message=str(exc))
            self.stop.set()

    def prepare_codex_model(self, config, catalog, source):
        from .codex_metadata import check_compatibility, write_catalog

        # This public metadata must remain stable, outside agent-writable workspace.
        if catalog is not None:
            directory = Path(tempfile.mkdtemp(prefix="gtoc-model-catalog-"))
            directory.chmod(0o755)
            self.codex_catalog_path = write_catalog(catalog, directory)
        with tempfile.TemporaryDirectory(prefix="gtoc-codex-preflight-") as directory:
            try:
                report = check_compatibility(
                    config,
                    directory,
                    self.codex_catalog_path,
                    identity={"user": 1000, "group": 1000, "extra_groups": ()},
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                report = {
                    "status": "incompatible",
                    "model": config["model"]["id"],
                    "client_version": config["agent"]["version"],
                    "model_turns": 0,
                    "error": str(exc),
                }
        report["source"] = source or "bundled_codex_catalog"
        self.send("model_compatibility", report=report)
        if report["status"] != "compatible":
            raise ValueError(report.get("error", "Codex model metadata preflight failed"))

    def prepare_problem(self, problem):
        """Select immutable materials in a root-owned /problem tmpfs before launch."""
        specification(problem)
        target = ROOT / "problems" / problem
        exposed = Path("/problem")
        for entry in sorted(target.iterdir()):
            if entry.name in {"validator", "provenance.json"}:
                continue
            link = exposed / entry.name
            link.symlink_to(entry, target_is_directory=entry.is_dir())
        exposed.chmod(0o755)

    def launch(self, prompt, resume_session=None):
        try:
            self._launch(prompt, resume_session)
        except Exception as exc:
            self.send("fatal", message=f"Agent launch failed: {exc}")
            self.stop.set()

    def _launch(self, prompt, resume_session=None):
        self.remember_workspace_owner()
        command, env = launch_spec(
            self.config, resume_session=resume_session, model_catalog_path=self.codex_catalog_path
        )
        self.prepare_workspace()
        self.observe("initial")
        self.observing.set()
        self.send("agent_command", command=command)
        self.agent = subprocess.Popen(
            command,
            env=env,
            cwd="/workspace",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            user=1000,
            group=1000,
            extra_groups=(),
            start_new_session=True,
        )
        self.agent.stdin.write(prompt.encode())
        self.agent.stdin.close()
        for name in ("stdout", "stderr"):

            def drain(stream=getattr(self.agent, name), channel=name):
                while chunk := stream.read1(16384):
                    self.send(
                        "agent_output", channel=channel, body=base64.b64encode(chunk).decode()
                    )

            reader = threading.Thread(target=drain, daemon=True)
            self.output_readers.append(reader)
            reader.start()
        returncode = self.agent.wait()
        for reader in self.output_readers:
            reader.join(timeout=2)
        self.send("agent_exit", returncode=returncode)

    def observe(self, reason, submission_id=None, triggers=None):
        try:
            self.observer.capture(
                self.send,
                reason,
                submission_id,
                triggers=triggers,
                stop=None if reason in {"initial", "final"} else self.observation_stop,
            )
        except (OSError, ValueError) as exc:
            self.send("observation_error", message=f"Workspace snapshot: {exc}")
            for trigger in triggers or [{"reason": reason, "submission_id": submission_id}]:
                self.send(
                    "observation_skipped",
                    reason="capture_error",
                    submission_id=trigger.get("submission_id"),
                    requested_reason=trigger["reason"],
                )

    def monitor(self):
        sampled_at, captured_at = 0, time.monotonic()
        while not self.observation_stop.wait(0.25):
            if not self.observing.is_set():
                continue
            now = time.monotonic()
            try:
                if now - sampled_at >= 2:
                    measurement = self.workspace_size()
                    if measurement["complete"]:
                        self.last_workspace_usage = measurement
                        self.send("workspace_usage", **measurement, phase="live")
                    sample = resource_sample() | {
                        "workspace_measurement_complete": measurement["complete"]
                    }
                    if self.last_workspace_usage is not None:
                        sample["workspace_bytes"] = self.last_workspace_usage["bytes"]
                        sample["workspace_bytes_source"] = (
                            "live" if measurement["complete"] else "last_complete"
                        )
                    self.send("resources", sample=sample)
                    sampled_at = now
                if now - captured_at >= 10:
                    self.request_snapshot("periodic")
                    captured_at = now
            except (OSError, ValueError) as exc:
                self.send("observation_error", message=f"Resource sampling: {exc}")

    def request_snapshot(self, reason, submission_id=None):
        try:
            self.snapshots.request(reason, submission_id)
        except queue.Full:
            self.send(
                "observation_skipped",
                reason="snapshot_trigger_limit",
                submission_id=submission_id,
                requested_reason=reason,
            )

    def snapshot_worker(self):
        while not self.observation_stop.is_set():
            try:
                triggers = self.snapshots.take(timeout=0.25)
            except queue.Empty:
                continue
            primary = next((t for t in triggers if t.get("submission_id")), triggers[0])
            self.observe(primary["reason"], primary.get("submission_id"), triggers=triggers)
        for trigger in self.snapshots.discard():
            self.send(
                "observation_skipped",
                reason="controller_shutdown",
                submission_id=trigger.get("submission_id"),
                requested_reason=trigger["reason"],
                requests=trigger["count"],
            )

    def prepare_workspace(self):
        self.remember_workspace_owner()
        for path in [Path("/workspace"), *Path("/workspace").rglob("*")]:
            if not path.is_symlink():
                os.chown(path, 1000, 1000)

    def prepare_agent_caches(self):
        # Hardware imports can initialize plotting libraries. Prepare ownership
        # before that preflight, rather than only immediately before agent launch.
        self.prepare_workspace()
        subprocess.run(
            [
                sys.executable,
                "-s",
                "-P",
                "-c",
                "from gtoc_bench.agents import prepare_caches; prepare_caches()",
            ],
            check=True,
            timeout=30,
            user=1000,
            group=1000,
            extra_groups=(),
            env=os.environ | {"HOME": "/workspace"} | cache_environment(),
            umask=0o077,
        )

    def remember_workspace_owner(self):
        if self.workspace_owner is None:
            info = Path("/workspace").stat()
            self.workspace_owner = (info.st_uid, info.st_gid)

    def workspace_size(self, final=False):
        return workspace_usage("/workspace", stop=None if final else self.stop)

    def restore_workspace_owner(self):
        if self.workspace_owner is None:
            return
        # The agent is stopped. Never follow its symlinks while returning artifacts.
        for directory, dirs, files in os.walk("/workspace", followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                try:
                    os.chown(path, *self.workspace_owner, follow_symlinks=False)
                except FileNotFoundError:
                    pass
        os.chown("/workspace", *self.workspace_owner)

    def check_judge(self, problem):
        report = {"problem": problem, "status": "failed", "identity": "1001:1001", "controls": []}
        try:
            directory = ROOT / "problems" / problem / "validator"
            binary = directory / (
                "GTOC11Check_Linux" if problem == "gtoc11" else f"{problem.upper()}_Verify"
            )
            for path, mode in ((directory, 0o750), (binary, 0o550)):
                info = path.stat()
                if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 1001, mode):
                    raise RuntimeError(
                        f"Invalid judge ownership/mode: {path.name}; expected root:1001 {mode:04o}"
                    )
            if problem == "gtoc12":
                for expected in ("valid", "invalid"):
                    data = (
                        ROOT / "environments/judge-checks/gtoc12" / f"{expected}.txt"
                    ).read_bytes()
                    result = self.run_validator(data, problem, timeout=10)
                    report["controls"].append({"expected": expected, "result": result})
                    if result["status"] != expected or (
                        expected == "valid" and result["score"] != 0
                    ):
                        raise RuntimeError(
                            f"Judge control {expected} failed: {result.get('diagnostics')}"
                        )
            report["status"] = "passed"
        except Exception as exc:
            report["error"] = str(exc)
            raise
        finally:
            self.send("validator_preflight", report=report)

    def run_validator(self, data, problem, timeout=None):
        started = time.monotonic()
        proc = None
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "gtoc_bench.validator", problem],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                cwd="/opt/gtoc",
                user=1001,
                group=1001,
                extra_groups=(),
                env={
                    "PATH": os.environ["PATH"],
                    "PYTHONPATH": "/opt/gtoc/src",
                    "GTOC_BENCH_ROOT": "/opt/gtoc",
                    "GTOC_JUDGE_TMP": "/judge-tmp",
                    "OPENBLAS_NUM_THREADS": "1",
                    "OMP_NUM_THREADS": "1",
                },
            )
            stdout, stderr = proc.communicate(data, timeout=timeout or supervisor_timeout(problem))
            if proc.returncode:
                raise RuntimeError(f"Protected validator process failed: exit {proc.returncode}")
            result = json.loads(stdout)
        except subprocess.TimeoutExpired:
            result = {
                "status": "validator_error",
                "score": None,
                "diagnostics": ["Protected validator supervisor timeout"],
                "timeout_phase": "validator_supervisor",
            }
        except Exception as exc:
            result = {"status": "validator_error", "score": None, "diagnostics": [str(exc)]}
        finally:
            # Kill the whole process group, including a native child left behind by
            # a failed Python supervisor, before admitting another validation.
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
        result["supervisor_seconds"] = time.monotonic() - started
        return result

    def validate(self, frame):
        if not self.validation_slot.acquire(blocking=False):
            self.send(
                "verdict",
                id=frame["id"],
                result={
                    "status": "validator_error",
                    "score": None,
                    "diagnostics": ["Protected validator is already active"],
                },
            )
            return
        try:
            data = base64.b64decode(frame["body"], validate=True)
            self.send("validation_phase", id=frame["id"], phase="started")
            result = self.run_validator(data, (self.config or {}).get("problem", "gtoc12"))
        except Exception as exc:
            result = {
                "status": "validator_error",
                "score": None,
                "diagnostics": [str(exc)],
                "supervisor_seconds": 0,
            }
        finally:
            self.validation_slot.release()
        self.send(
            "validation_phase",
            id=frame["id"],
            phase="finished",
            supervisor_seconds=result["supervisor_seconds"],
        )
        self.send("verdict", id=frame["id"], result=result)
        self.request_snapshot("submission", frame["id"])

    def kill_agent(self):
        # Kill all processes of the agent UID, including detached subprocesses.
        for directory in Path("/proc").iterdir():
            if directory.name.isdigit():
                try:
                    if directory.stat().st_uid == 1000:
                        os.kill(int(directory.name), signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    pass

    def read(self):
        try:
            while line := sys.stdin.buffer.readline(MAX_FRAME + 1):
                if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    raise ValueError("Invalid controller frame")
                frame = json.loads(line)
                kind = frame["type"]
                if kind == "heartbeat":
                    self.last_heartbeat = time.monotonic()
                elif kind == "provider_wait_prepare":
                    self.last_heartbeat = time.monotonic()
                    self.send("provider_wait_ack", id=frame["id"])
                elif kind == "provider_wait_resume":
                    self.last_heartbeat = time.monotonic()
                    self.send("provider_wait_ack", id=frame["id"])
                elif kind == "configure" and self.config is None:
                    threading.Thread(
                        target=self.prepare,
                        args=(
                            frame["config"],
                            frame.get("codex_catalog"),
                            frame.get("codex_catalog_source"),
                        ),
                        daemon=True,
                    ).start()
                elif kind == "start" and self.agent is None:
                    threading.Thread(
                        target=self.launch,
                        args=(frame["prompt"], frame.get("resume_session")),
                        daemon=True,
                    ).start()
                elif kind == "start_tools" and self.agent is None:
                    self.prepare_workspace()
                    self.observe("initial")
                    self.observing.set()
                    self.send("tools_ready")
                elif kind == "reply":
                    with self.pending_lock:
                        if frame["id"] in self.pending:
                            self.pending[frame["id"]].put(frame)
                elif kind == "validate":
                    threading.Thread(target=self.validate, args=(frame,), daemon=True).start()
                elif kind == "stop_agent":
                    self.kill_agent()
                elif kind == "shutdown":
                    break
        finally:
            self.stop.set()

    def run(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                bridge.relay(self)

            def do_POST(self):
                bridge.relay(self)

            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(
                    client_timeout((bridge.config or {}).get("problem", "gtoc12"))
                )

        server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=self.read, daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        monitor = threading.Thread(target=self.monitor, daemon=True)
        monitor.start()
        snapshots = threading.Thread(target=self.snapshot_worker, daemon=True)
        snapshots.start()
        previous_poll = time.monotonic()
        while not self.stop.wait(0.25):
            now = time.monotonic()
            # A cgroup-frozen supervisor cannot read queued heartbeats. On return
            # allow its reader the ordinary watchdog interval to catch up.
            if self.heartbeat_reader is None and now - previous_poll > 2:
                self.last_heartbeat = now
            previous_poll = now
            health = (
                self.heartbeat_reader.poll()
                if self.heartbeat_reader
                else {
                    "channel": "legacy_stdin",
                    "age_seconds": now - self.last_heartbeat,
                    "timeout_seconds": 10,
                }
            )
            if health["age_seconds"] > health["timeout_seconds"]:
                # Stop compute before trying to report through a possibly blocked pipe.
                self.kill_agent()
                self.send("fatal", message="Controller heartbeat lost", supervision=health)
                break
        self.kill_agent()
        self.observation_stop.set()
        monitor.join(timeout=10)
        snapshots.join(timeout=10)
        if self.workspace_owner is not None:
            measurement = self.workspace_size(final=True)
            if measurement["complete"]:
                self.last_workspace_usage = measurement
                self.send("workspace_usage", **measurement, phase="after_agent_stop")
            if snapshots.is_alive():
                self.send(
                    "observation_skipped",
                    reason="snapshot_still_running_at_shutdown",
                    requested_reason="final",
                    submission_id=None,
                )
            else:
                self.observe("final")
            sample = resource_sample() | {"workspace_measurement_complete": measurement["complete"]}
            if self.last_workspace_usage is not None:
                sample["workspace_bytes"] = self.last_workspace_usage["bytes"]
                sample["workspace_bytes_source"] = (
                    "after_agent_stop" if measurement["complete"] else "last_complete"
                )
            self.send("resources", sample=sample)
        for reader in self.output_readers:
            reader.join(timeout=2)
        server.shutdown()
        self.restore_workspace_owner()


if __name__ == "__main__":
    Bridge().run()
