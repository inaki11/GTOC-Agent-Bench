"""Explicit, post-run recovery for exhausted budgets whose finalization failed."""

import hashlib
import json
import math
import re
from pathlib import Path

from .storage import read_events
from .submission_artifacts import read_submission


def artifact(root, relative):
    """Recovery references stay inside results and never follow symlinks."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Recovery artifacts must be relative to results")
    path = root
    for part in Path(relative).parts:
        if part == "..":
            raise ValueError("Recovery artifacts must stay inside results")
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Recovery artifacts cannot follow symlinks: {path}")
    return path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_recoveries(results):
    root = Path(results).resolve()
    directory = artifact(root, "comparison-recoveries")
    records = {}
    for path in sorted(directory.glob("*.json")):
        path = artifact(root, str(path.relative_to(root)))
        record = json.loads(path.read_text())
        if (
            record.get("schema_version") != 1
            or record.get("kind") != "comparison_recovery"
            or record.get("processes_stopped") is not True
            or not record.get("note")
        ):
            raise ValueError(f"Invalid operator recovery record: {path}")
        run = artifact(root, record["run_path"])
        if not run.is_dir() or run in records:
            raise ValueError(f"Missing or duplicate recovery source: {run}")
        evidence = {}
        for item in record["evidence"]:
            source = artifact(root, item["path"])
            if sha256(source) != item["sha256"]:
                raise ValueError(f"Recovery evidence hash does not match: {source}")
            if item["role"] in evidence:
                raise ValueError(f"Duplicate recovery evidence role: {path}")
            evidence[item["role"]] = source
        if not {"stop_confirmation", "best_revalidation"} <= evidence.keys():
            raise ValueError(f"Recovery needs stop confirmation and revalidation: {path}")
        records[run] = record | {
            "record_path": str(path),
            "record_sha256": sha256(path),
            "evidence_paths": evidence,
        }
    return records


def recovered_summary(path, manifest, record):
    """Reconstruct from frozen events, never promote a live status by itself."""
    rid = manifest["run_id"]
    if record["run_id"] != rid:
        raise ValueError(f"Recovery run identifiers disagree: {path}")
    for name in ("manifest.json", "status.json", "events.jsonl"):
        source = artifact(path, name)
        if sha256(source) != record["source_sha256"].get(name):
            raise ValueError(f"Recovery source hash does not match: {source}")
    status = json.loads((path / "status.json").read_text())
    wall = manifest["config"]["budget"]["wall_seconds"]
    elapsed = status.get("elapsed_seconds")
    if (
        status.get("run_id") != rid
        or status.get("state") != "finalizing"
        or status.get("stop_reason") != "time_limit"
        or type(elapsed) not in {int, float}
        or not math.isfinite(elapsed)
        or elapsed < wall
    ):
        raise ValueError(f"Recovery requires an exhausted time budget: {rid}")
    tokens, unknown, candidates = 0, 0, {}
    native_pending = set()
    started = stopped = exited = False
    for event in read_events(path / "events.jsonl"):
        if event.get("run_id") != rid:
            raise ValueError(f"Recovery event run identifiers disagree: {rid}")
        kind = event["type"]
        started |= kind == "run_started"
        stopped |= (
            kind == "model_cancel_requested"
            and event.get("reason") == "time_limit"
            and event["elapsed_seconds"] >= wall
        )
        exited |= kind == "agent_exit" and event["elapsed_seconds"] >= wall
        if kind == "model_request_finished":
            usage = event.get("usage") or {}
            incoming, outgoing = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
            if any(type(v) is not int or v < 0 for v in (incoming, outgoing)):
                raise ValueError(f"Invalid recovered usage: {rid}")
            tokens += incoming + outgoing
            if usage.get("accounting_schema") == 2 and usage.get("source") == "official_claude_cli":
                if usage.get("scope") == "response":
                    if usage.get("quality") == "reported":
                        native_pending.discard(event["request_id"])
                    else:
                        native_pending.add(event["request_id"])
            else:
                unknown += usage.get("quality") != "reported"
        elif kind in {"submission_evaluated", "validation_late_verdict"}:
            if isinstance(event.get("verdict"), dict):
                candidates[event["submission_id"]] = event["verdict"]
    if not (started and stopped and exited):
        raise ValueError(f"Recovery lacks recorded budget stop or agent exit: {rid}")
    valid = {sid: v for sid, v in candidates.items() if v.get("status") == "valid"}
    if not valid:
        raise ValueError(f"Recovery requires a valid candidate: {rid}")
    best = max(v["score"] for v in valid.values())
    revalidation = json.loads(record["evidence_paths"]["best_revalidation"].read_text())
    verdict, sid = revalidation["verdict"], revalidation["submission_id"]
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", sid)
        or sid not in valid
        or revalidation["run_id"] != rid
        or revalidation["image_id"] != manifest["image_id"]
        or verdict.get("status") != "valid"
        or not math.isclose(verdict["score"], best, abs_tol=1e-6)
        or not math.isclose(status["best_score"], best, abs_tol=1e-6)
        or verdict.get("artifact_sha256") != valid[sid].get("artifact_sha256")
        or hashlib.sha256(read_submission(artifact(path, f"submissions/{sid}"))).hexdigest()
        != verdict["artifact_sha256"]
    ):
        raise ValueError(f"Recovery revalidation disagrees with the recorded best: {rid}")
    return {
        "run_id": rid,
        "state": "recovered",
        "stop_reason": "time_limit",
        "elapsed_seconds": elapsed,
        "known_tokens": tokens,
        "unknown_usage_requests": unknown,
        "pending_native_usage_responses": len(native_pending),
        "best_score": best,
    }, {k: v for k, v in record.items() if k != "evidence_paths"}
