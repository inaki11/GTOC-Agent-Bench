"""Read-only lifecycle checks for the CLI and sequential host operators."""

import hashlib
import json
import os
from pathlib import Path

TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


def process_start_ticks(pid):
    try:
        return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def controller_alive(pid, start_ticks=None):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # It exists; start-time metadata can still reveal a reused PID.
    observed = process_start_ticks(pid)
    if start_ticks is not None and observed is not None and str(start_ticks) != observed:
        return False  # The PID was reused by an unrelated process.
    try:
        state = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except FileNotFoundError:
        return not Path("/proc").is_dir()
    except (OSError, IndexError):
        return True


def read_status(path):
    path = Path(path)
    result = json.loads((path / "status.json").read_text())
    alive = controller_alive(result.get("pid"), result.get("controller_start_ticks"))
    result.update(controller_alive=alive, integrity_present=(path / "integrity.json").is_file())
    unsealed_terminal = result.get("state") in TERMINAL_STATES and not result["integrity_present"]
    if alive is False and (
        result.get("state") in {"preparing", "running", "finalizing"} or unsealed_terminal
    ):
        previous = result["state"]
        result.update(
            stored_state=previous,
            stored_stop_reason=result.get("stop_reason"),
            state="interrupted",
            status_stale=True,
            stop_reason="infrastructure_failure"
            if previous == "finalizing" or unsealed_terminal
            else "host_failure",
        )
    return result


def predecessor_state(path):
    """A missing seal is never success; dead controllers require explicit recovery.

    Credentials and machine availability are separate admission checks. This
    function neither changes artifacts nor restarts an evaluation.
    """
    path = Path(path)
    try:
        status = read_status(path)
    except (OSError, ValueError, TypeError, AttributeError):
        return {"ready": False, "blocked": True, "reason": "predecessor_status_unavailable"}
    report = status.get("finalization") or {}
    if not isinstance(report, dict):
        return {"ready": False, "blocked": True, "reason": "predecessor_status_invalid"}
    if report.get("safe_for_successor") is False:
        return {"ready": False, "blocked": True, "reason": "predecessor_cleanup_incomplete"}
    if status.get("status_stale"):
        return {"ready": False, "blocked": True, "reason": "predecessor_controller_lost"}
    summary = path / "summary.json"
    if not summary.is_file() or not status["integrity_present"]:
        blocked = status.get("controller_alive") is False
        return {
            "ready": False,
            "blocked": blocked,
            "reason": "predecessor_finalization_incomplete" if blocked else "predecessor_running",
        }
    try:
        seal = json.loads((path / "integrity.json").read_text())["files"]
        if not isinstance(seal, dict):
            raise ValueError("Invalid lifecycle seal")
        for name in ("summary.json", "finalization.json"):
            if name == "finalization.json" and name not in seal and not report:
                continue  # Legacy sealed attempts did not include this report.
            if hashlib.sha256((path / name).read_bytes()).hexdigest() != seal.get(name):
                raise ValueError("Sealed lifecycle metadata differs")
        result = json.loads(summary.read_text())
        if not isinstance(result, dict):
            raise ValueError("Invalid lifecycle summary")
        closing = (
            json.loads((path / "finalization.json").read_text())
            if "finalization.json" in seal
            else {}
        )
        if not isinstance(closing, dict):
            raise ValueError("Invalid finalization report")
    except (OSError, ValueError, KeyError, TypeError):
        return {"ready": False, "blocked": True, "reason": "predecessor_seal_invalid"}
    finalization = result.get("finalization") or {}
    if not isinstance(finalization, dict):
        return {"ready": False, "blocked": True, "reason": "predecessor_seal_invalid"}
    ready = (
        result.get("state") in TERMINAL_STATES
        and result.get("requests_in_flight", 0) == 0
        and finalization.get("safe_for_successor", True) is True
        and closing.get("safe_for_successor", True) is True
    )
    return {
        "ready": ready,
        "blocked": not ready,
        "reason": "predecessor_finalized" if ready else "predecessor_cleanup_incomplete",
    }
