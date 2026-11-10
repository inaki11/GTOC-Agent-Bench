"""Explicit continuations of sealed attempts; never restore live process memory."""

import copy
import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .observation import regular_files
from .storage import write_json


def open_regular(root, relative):
    """Open beneath a trusted root without traversing symlinks, including directories."""
    parts = PurePosixPath(relative).parts
    if (
        not parts
        or str(PurePosixPath(relative)) != relative
        or any(p in {"..", "/"} for p in parts)
    ):
        raise ValueError(f"Unsafe artifact path: {relative}")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(leaf).st_mode):
            os.close(leaf)
            raise ValueError(f"Artifact is not a regular file: {relative}")
        return os.fdopen(leaf, "rb")
    finally:
        os.close(fd)


def read_json(root, relative):
    with open_regular(root, relative) as stream:
        return json.load(stream)


def digest_stream(stream):
    digest = hashlib.sha256()
    while chunk := stream.read(1024**2):
        digest.update(chunk)
    return digest.hexdigest()


def verify_seal(run):
    seal = read_json(run, "integrity.json")
    if seal.get("algorithm") != "sha256":
        raise ValueError("Unsupported integrity seal")
    for name in (
        "manifest.json",
        "summary.json",
        "workspace-manifest.json",
        "agent/transcript.jsonl",
    ):
        if name not in seal["files"]:
            raise ValueError(f"Parent checkpoint is not sealed: {name}")
    for name, expected in seal["files"].items():
        with open_regular(run, name) as stream:
            if digest_stream(stream) != expected:
                raise ValueError(f"Parent artifact changed: {name}")
    return seal


def codex_checkpoint(run):
    manifest = read_json(run, "manifest.json")
    if manifest["config"]["agent"]["id"] != "codex":
        raise ValueError("Session continuation currently supports Codex only")
    threads = set()
    with open_regular(run, "agent/transcript.jsonl") as stream:
        for line in stream:
            event = json.loads(line).get("native_event") or {}
            if event.get("type") == "thread.started":
                threads.add(str(uuid.UUID(event["thread_id"])))
    if len(threads) != 1:
        raise ValueError("Cannot identify a unique saved Codex session")
    session_id = threads.pop()
    inventory = read_json(run, "workspace-manifest.json")
    matches = []
    for relative, info in inventory["files"].items():
        if not relative.startswith(".codex/sessions/") or not relative.endswith(".jsonl"):
            continue
        with open_regular(run / "workspace", relative) as stream:
            head = json.loads(stream.readline())
            payload = head.get("payload", {})
            if head.get("type") != "session_meta" or payload.get("id") != session_id:
                continue
            if (
                payload.get("cwd") != "/workspace"
                or payload.get("model_provider") != "bench"
                or payload.get("cli_version") != manifest["config"]["agent"]["version"]
            ):
                raise ValueError("Saved session does not match the pinned agent/workspace")
            for line in stream:
                json.loads(line)  # Data only: reject a corrupt/partially written checkpoint.
        matches.append(
            {"session_id": session_id, "session_file": relative, "session_sha256": info["sha256"]}
        )
    if len(matches) != 1:
        raise ValueError("No unique, complete Codex session checkpoint found")
    return matches[0]


def antigravity_checkpoint(run):
    """Identify an opaque SDK database; only the isolated SDK opens its contents."""
    inventory = read_json(run, "workspace-manifest.json")
    matches = []
    for relative, info in inventory["files"].items():
        match = re.fullmatch(r"antigravity/([A-Za-z0-9-]{32,128})\.db", relative)
        if match is None:
            continue
        session_id = match[1]
        transcript = f".antigravity/brain/{session_id}/.system_generated/logs/transcript.jsonl"
        if not inventory["files"].get(transcript, {}).get("bytes"):
            raise ValueError("Antigravity checkpoint has no matching conversation transcript")
        with open_regular(run / "workspace", relative) as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                raise ValueError("Invalid Antigravity checkpoint database header")
            stream.seek(0)
            if digest_stream(stream) != info["sha256"]:
                raise ValueError("Saved Antigravity checkpoint changed")
        matches.append(
            {
                "session_id": session_id,
                "session_file": relative,
                "session_sha256": info["sha256"],
                "session_format": "antigravity-sqlite",
            }
        )
    if len(matches) != 1:
        raise ValueError("No unique Antigravity conversation checkpoint found")
    return matches[0]


def session_checkpoint(run):
    agent = read_json(run, "manifest.json")["config"]["agent"]["id"]
    if agent == "codex":
        return codex_checkpoint(run)
    if agent == "antigravity":
        return antigravity_checkpoint(run)
    raise ValueError("Session continuation currently supports Codex and Antigravity only")


def checkpoint_report(run):
    try:
        return {
            "available": True,
            "kind": "conversation_and_workspace",
            **session_checkpoint(run),
            "process_memory_restored": False,
            "requires_integrity_verification": True,
        }
    except (OSError, ValueError, KeyError) as exc:
        return {"available": False, "reason": str(exc), "process_memory_restored": False}


def create_continuation(
    parent, config, results, *, workspace_only=False, memory_gb=None, recovery_reason=None
):
    from .runner import create_run
    from .validator import verify_materials

    parent = Path(parent).resolve()
    seal = verify_seal(parent)
    previous = read_json(parent, "summary.json")
    manifest = read_json(parent, "manifest.json")
    if "provider-recovery.json" in seal["files"]:
        recovery = read_json(parent, "provider-recovery.json")
        not_before = recovery.get("retry_not_before_utc")
        if recovery.get("state") == "interrupted" and not_before:
            earliest = datetime.fromisoformat(not_before)
            if earliest.tzinfo is None:
                raise ValueError("Provider Retry-After checkpoint requires a timezone")
            if datetime.now(UTC) < earliest:
                raise ValueError(
                    f"Provider Retry-After: continuation cannot start before {not_before}"
                )
    old = copy.deepcopy(manifest["config"])
    old["hardware"].setdefault("concurrent_with", None)
    config = copy.deepcopy(config)
    resource_changes = {}
    if memory_gb is not None:
        from .config import positive

        positive(memory_gb, "resume memory_gb")
        config["hardware"]["memory_gb"] = memory_gb
        if memory_gb != old["hardware"].get("memory_gb"):
            resource_changes["memory_gb"] = {
                "before": old["hardware"].get("memory_gb"),
                "after": memory_gb,
                "explicit_override": True,
            }
    if manifest["problem"] != verify_materials(config["problem"]):
        raise ValueError("Continuation requires unchanged problem data and validator")
    if recovery_reason is not None and (
        not isinstance(recovery_reason, str) or not recovery_reason.strip()
    ):
        raise ValueError("An explicit, nonempty recovery reason is required")
    reopen_completed = (
        recovery_reason is not None
        and previous["state"] == "completed"
        and previous.get("stop_reason") == "agent_finished"
    )
    if previous["state"] not in {"interrupted", "cancelled", "failed"} and not reopen_completed:
        raise ValueError("Only a finalized interrupted/cancelled/failed attempt can be continued")
    if previous["remaining_seconds"] <= 0 or previous.get("remaining_tokens") == 0:
        raise ValueError("The original budget is exhausted")
    for name in ("problem", "agent", "model", "hardware", "network", "purpose"):
        current, prior = config[name], old[name]
        if name == "hardware":
            # The explicitly permitted concurrent run may have a different ID;
            # CPU, memory, GPU and workspace resources must still match.
            current = {k: v for k, v in current.items() if k != "concurrent_with"}
            prior = {k: v for k, v in prior.items() if k != "concurrent_with"}
            if memory_gb is not None:
                current = {k: v for k, v in current.items() if k != "memory_gb"}
                prior = {k: v for k, v in prior.items() if k != "memory_gb"}
        if current != prior:
            raise ValueError(f"Continuation must preserve the parent's {name}")
    for name in ("id", "protocol", "auth_method", "base_url"):
        if config["provider"].get(name) != old["provider"].get(name):
            raise ValueError(f"Continuation must preserve provider {name}")
    if workspace_only:
        if old["agent"]["id"] not in {"claude_code", "swe_agent"}:
            raise ValueError("Workspace-only recovery currently supports Claude Code and SWE-agent")
        checkpoint = {"session_restored": False, "recovery_mode": "workspace_only"}
    else:
        checkpoint = session_checkpoint(parent)
    inherited = (
        read_json(parent, "continuation.json") if "continuation.json" in seal["files"] else {}
    )
    config = copy.deepcopy(config)
    config["budget"] = {
        "wall_seconds": previous["remaining_seconds"],
        "token_limit": previous.get("remaining_tokens"),
    }
    continuation = {
        "schema_version": 1,
        "parent_run_id": previous["run_id"],
        "root_run_id": inherited.get("root_run_id", previous["run_id"]),
        "parent_directory": str(parent),
        "parent_integrity_sha256": hashlib.sha256(
            (parent / "integrity.json").read_bytes()
        ).hexdigest(),
        "parent_configuration_id": manifest["configuration_id"],
        "kind": "research_continuation",
        "independent_run": False,
        "process_memory_restored": False,
        "session_restored": not workspace_only,
        "prior_elapsed_seconds": inherited.get("prior_elapsed_seconds", 0)
        + previous["elapsed_seconds"],
        "prior_known_tokens": inherited.get("prior_known_tokens", 0) + previous["known_tokens"],
        "prior_unknown_usage_requests": inherited.get("prior_unknown_usage_requests", 0)
        + previous["unknown_usage_requests"],
        "original_budget": inherited.get("original_budget", old["budget"]),
        "inherited_best_score": previous["best_score"],
        "offline_gap_counts_toward_segment_budget": False,
        "resource_changes": resource_changes,
        "homogeneous_resources": not resource_changes
        and inherited.get("homogeneous_resources", True),
        **checkpoint,
    }
    if recovery_reason is not None:
        continuation["operator_recovery"] = {
            "reason": recovery_reason.strip(),
            "parent_state": previous["state"],
            "parent_stop_reason": previous.get("stop_reason"),
            "reopened_completed_attempt": reopen_completed,
            "original_artifacts_preserved": True,
        }
    if "gpus" in manifest.get("resolved_conditions", {}).get("hardware", {}):
        continuation["prior_gpu_devices"] = manifest["resolved_conditions"]["hardware"]["gpus"]
    inventory = read_json(parent, "workspace-manifest.json")
    if inventory.get("truncated"):
        raise ValueError("Cannot restore an incomplete workspace inventory")
    path = create_run(config, results)
    copied, skipped = {}, []
    try:
        # Copy regular files only, with no executable code or pickle loading on the host.
        for relative, stream, info in regular_files(parent / "workspace", limit=200000):
            parts = PurePosixPath(relative).parts
            if (
                (parts[0] == ".codex" and not relative.startswith(".codex/sessions/"))
                or parts[0] == ".claude"
                or parts[-1] in {"auth.json", "credentials.json", ".credentials.json", ".env"}
                or parts[-1].startswith(".env.")
            ):
                skipped.append(relative)
                continue
            expected = inventory["files"].get(relative)
            if expected is None or digest_stream(stream) != expected["sha256"]:
                raise ValueError(f"Saved workspace file changed: {relative}")
            stream.seek(0)
            target = path / "workspace" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with target.open("xb") as output:
                while chunk := stream.read(1024**2):
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError(f"Saved workspace changed while copying: {relative}")
            target.chmod(stat.S_IMODE(info.st_mode) & 0o777)
            copied[relative] = expected
        if set(inventory["files"]) - set(copied) - set(skipped):
            raise ValueError("Saved workspace has missing/unreadable files")
        if workspace_only:
            relative = f".gtoc-recovery-{path.name}/parent-transcript.jsonl"
            target = path / "workspace" / relative
            target.parent.mkdir()
            with (
                open_regular(parent, "agent/transcript.jsonl") as source,
                target.open("xb") as output,
            ):
                digest = hashlib.sha256()
                size = 0
                while chunk := source.read(1024**2):
                    digest.update(chunk)
                    size += len(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != seal["files"]["agent/transcript.jsonl"]:
                raise ValueError("Parent transcript changed while copying")
            copied[relative] = {"sha256": digest.hexdigest(), "bytes": size}
            continuation["recovery_transcript"] = "/workspace/" + relative
        write_json(
            path / "restored-workspace.json",
            {
                "files": copied,
                "skipped": skipped,
                "symlinks_followed": False,
                "source": str(parent / "workspace"),
            },
        )
        write_json(path / "continuation.json", continuation)
    except Exception:
        write_json(
            path / "status.json",
            {"run_id": path.name, "state": "failed", "stop_reason": "checkpoint_error"},
        )
        raise
    return path


def cumulative_status(continuation, budget):
    if not continuation:
        return {}
    return {
        "parent_run_id": continuation["parent_run_id"],
        "root_run_id": continuation["root_run_id"],
        "independent_run": False,
        "cumulative_elapsed_seconds": continuation["prior_elapsed_seconds"]
        + budget["elapsed_seconds"],
        "cumulative_known_tokens": continuation["prior_known_tokens"] + budget["known_tokens"],
        "cumulative_unknown_usage_requests": continuation["prior_unknown_usage_requests"]
        + budget["unknown_usage_requests"],
        "inherited_best_score": continuation["inherited_best_score"],
    }
