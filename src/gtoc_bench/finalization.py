"""Independent cleanup steps and bounded container termination on the host."""

import json
import subprocess
import sys
import time
from pathlib import Path

from .storage import utc_now


class FinalizationLog:
    def __init__(self, store):
        self.store = store
        self.data = {
            "schema_version": 1,
            "started_at": utc_now(),
            "status": "running",
            "phase": "cancel_model",
            "steps": [],
            "errors": [],
        }

    def save(self):
        try:
            self.store.write("finalization.json", self.data)
            return True
        except Exception as exc:
            # Disk failures must not prevent cancellation or subsequent cleanup.
            message = self.store.clean(str(exc))[:2000]
            try:
                print(f"gtoc: finalization diagnostics unavailable: {message}", file=sys.stderr)
            except OSError:
                pass
            return False

    def error(self, stage, exc):
        error = {"stage": stage, "error_type": type(exc).__name__, "message": str(exc)[:2000]}
        if isinstance(exc, subprocess.TimeoutExpired):
            error.update(command=exc.cmd, timeout_seconds=exc.timeout)
        if isinstance(exc, subprocess.CalledProcessError):
            error.update(command=exc.cmd, returncode=exc.returncode)
        for name in ("stdout", "stderr"):
            value = getattr(exc, name, None)
            if isinstance(value, bytes):
                value = value.decode(errors="replace")
            if isinstance(value, str) and value:
                error[name] = value[-4096:]
        self.data["errors"].append(error)
        try:
            self.store.event("finalization_error", **error)
        except Exception:
            try:
                print(json.dumps(self.store.clean(error)), file=sys.stderr)
            except OSError:
                pass

    def step(self, name, operation, *, persist_start=True):
        entry = {"name": name, "started_at": utc_now(), "status": "running"}
        self.data["phase"] = name
        self.data["steps"].append(entry)
        started = time.monotonic()
        if persist_start:
            self.save()
        try:
            value = operation()
        except Exception as exc:
            entry["status"] = "failed"
            self.error(name, exc)
            return False, None
        else:
            entry["status"] = "completed"
            return True, value
        finally:
            entry.update(finished_at=utc_now(), duration_seconds=time.monotonic() - started)
            self.save()

    def finish(self, safe_for_successor):
        self.data.update(
            status=(
                "incomplete"
                if not safe_for_successor
                else "completed_with_errors"
                if self.data["errors"]
                else "completed"
            ),
            phase="finished",
            finished_at=utc_now(),
            safe_for_successor=safe_for_successor,
        )
        return self.save()

    def summary(self):
        return {
            k: self.data[k]
            for k in ("status", "safe_for_successor", "container_stopped")
            if k in self.data
        } | {
            "phase": self.data["phase"] if self.data["status"] == "running" else "finished",
            "error_count": len(self.data["errors"]),
            "report": "finalization.json",
        }


def stop_container(process, reader, name, log):
    """A dead Docker client alone does not confirm that the container stopped."""

    def command(*args, timeout=15):
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout, check=True
        )

    stopped = True if process is None else None
    if process is not None:
        waited, _ = log.step("transport_wait", lambda: process.wait(timeout=30))
        if not waited:
            # No command line or environment: these can contain private values.
            snapshot = {"pid": getattr(process, "pid", None)}
            for filename in ("stat", "status", "wchan"):
                try:
                    snapshot[filename] = (
                        Path("/proc") / str(snapshot["pid"]) / filename
                    ).read_text()[:4096]
                except (OSError, UnicodeError):
                    snapshot[filename] = None
            log.data["transport_at_timeout"] = snapshot
            log.step("docker_kill", lambda: command("kill", name))
            log.step("transport_wait_after_kill", lambda: process.wait(timeout=15))
        inspected, result = log.step("docker_inspect", lambda: command("inspect", name))
        if inspected:

            def state():
                value = json.loads(result.stdout)[0]["State"]
                if not isinstance(value.get("Running"), bool):
                    raise ValueError("Container state does not declare Running")
                log.store.write("container-state.json", value)
                if value.get("OOMKilled"):
                    log.store.event("container_oom")
                return value

            observed, value = log.step("container_state", state)
            if observed:
                stopped = not value["Running"]
        removed, _ = log.step("docker_remove", lambda: command("rm", "-f", name))
        if removed:
            stopped = True
        else:
            # Handles a container removed externally without parsing localized stderr.
            listed, result = log.step(
                "docker_verify_absent",
                lambda: command("ps", "-a", "--filter", "name=" + name, "--format", "{{.ID}}"),
            )
            if listed and not result.stdout.strip():
                stopped = True
        if process.poll() is None:
            log.step("transport_terminate", process.terminate)
            waited, _ = log.step("transport_reap", lambda: process.wait(timeout=5))
            if not waited:
                log.step("transport_kill", process.kill)
                log.step("transport_final_wait", lambda: process.wait(timeout=5))
    if reader:
        log.step("reader_join", lambda: reader.join(timeout=10))
    return {
        "container_stopped": stopped,
        "transport_stopped": process is None or process.poll() is not None,
        "reader_stopped": reader is None or not reader.is_alive(),
    }
