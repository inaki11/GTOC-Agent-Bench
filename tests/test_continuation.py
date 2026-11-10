import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from gtoc_bench import ROOT
from gtoc_bench.agents import launch_spec
from gtoc_bench.config import load_config, public_config
from gtoc_bench.continuation import checkpoint_report, create_continuation, open_regular
from gtoc_bench.observation import workspace_inventory
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import RunStore, write_json
from gtoc_bench.validator import verify_materials


def parent_run(tmp_path):
    config = load_config(ROOT / "configs/codex.yaml")
    config["budget"] = {"wall_seconds": 100, "token_limit": 1000}
    run = create_run(config, tmp_path)
    session = str(uuid.uuid4())
    store = RunStore(run)
    store.write(
        "manifest.json",
        {
            "config": public_config(config),
            "configuration_id": "fake-id",
            "problem": verify_materials(),
        },
    )
    store.write(
        "summary.json",
        {
            "run_id": run.name,
            "state": "interrupted",
            "best_score": 123.4,
            "elapsed_seconds": 30,
            "remaining_seconds": 70,
            "known_tokens": 400,
            "unknown_usage_requests": 1,
            "remaining_tokens": 600,
        },
    )
    store.append(
        "agent/transcript.jsonl",
        "agent_activity",
        native_event={"type": "thread.started", "thread_id": session},
    )
    store.write(
        "workspace/.codex/sessions/2026/rollout.jsonl",
        {
            "type": "session_meta",
            "payload": {
                "id": session,
                "cwd": "/workspace",
                "cli_version": config["agent"]["version"],
                "model_provider": "bench",
            },
        },
    )
    # A rollout is JSONL, unlike ordinary pretty-printed JSON artifacts.
    rollout = run / "workspace/.codex/sessions/2026/rollout.jsonl"
    rollout.write_text(json.dumps(json.loads(rollout.read_text())) + "\n")
    (run / "workspace/search.py").write_text("raise RuntimeError('must never execute on host')\n")
    (run / "workspace/.codex/config.toml").write_text("malicious-old-config = true\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("private file")
    (run / "workspace/external-link").symlink_to(outside)
    store.write("workspace-manifest.json", workspace_inventory(run / "workspace"))
    store.integrity()
    return run, config, session


def test_resume_preserves_parent_and_budget_without_executing_workspace(tmp_path):
    parent, config, session = parent_run(tmp_path)
    seal = (parent / "integrity.json").read_bytes()
    config["budget"] = {"wall_seconds": 99999, "token_limit": None}
    run = create_continuation(parent, config, tmp_path)
    restored = json.loads((run / "launch.json").read_text())
    assert restored["budget"] == {"wall_seconds": 70, "token_limit": 600}
    lineage = json.loads((run / "continuation.json").read_text())
    assert lineage["session_id"] == session and lineage["parent_run_id"] == parent.name
    assert lineage["prior_unknown_usage_requests"] == 1
    assert (run / "workspace/search.py").read_bytes() == (
        parent / "workspace/search.py"
    ).read_bytes()
    assert not (run / "workspace/external-link").exists()
    assert not (run / "workspace/.codex/config.toml").exists()
    assert (parent / "integrity.json").read_bytes() == seal
    runner = Runner(run)
    try:
        assert runner.status()["remaining_seconds"] == 70
        assert runner.status()["cumulative_known_tokens"] == 400
        assert runner.status()["cumulative_elapsed_seconds"] == 30
        assert runner.status()["best_score"] == 123.4
    finally:
        runner.pool.shutdown()
    command, _ = launch_spec(restored, str(run / "workspace"), session)
    assert command[:4] == ["codex", "exec", "resume", session]


def test_resume_respects_the_sealed_provider_not_before_and_keeps_remaining_budget(tmp_path):
    parent, config, session = parent_run(tmp_path)
    earliest = datetime.now(UTC) + timedelta(seconds=302)
    recovery = {
        "schema_version": 1,
        "state": "interrupted",
        "outcome": "recovery_window_exhausted",
        "retry_after": {"seconds": 302, "source": "error.headers.retry-after", "value": "302"},
        "retry_not_before_utc": earliest.isoformat(),
    }
    write_json(parent / "provider-recovery.json", recovery)
    store = RunStore(parent)
    store.integrity()
    seal = json.loads((parent / "integrity.json").read_text())
    assert "provider-recovery.json" in seal["files"]
    with pytest.raises(ValueError, match="Provider Retry-After"):
        create_continuation(parent, config, tmp_path)
    recovery["retry_not_before_utc"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    write_json(parent / "provider-recovery.json", recovery)
    with pytest.raises(ValueError, match="Parent artifact changed"):
        create_continuation(parent, config, tmp_path)
    store.integrity()
    child = create_continuation(parent, config, tmp_path)
    lineage = json.loads((child / "continuation.json").read_text())
    restored = json.loads((child / "launch.json").read_text())
    assert lineage["session_id"] == session
    assert restored["budget"] == {"wall_seconds": 70, "token_limit": 600}


@pytest.mark.parametrize(
    "change", ["workspace", "sealed", "symlink", "model", "exhausted", "session"]
)
def test_resume_rejects_modified_or_incompatible_parent(tmp_path, change):
    run, config, _ = parent_run(tmp_path)
    if change == "workspace":
        (run / "workspace/search.py").write_text("changed")
    elif change == "sealed":
        (run / "summary.json").write_text("{}")
    elif change == "symlink":
        target = run / "workspace/search.py"
        target.unlink()
        target.symlink_to(tmp_path / "outside.txt")
    elif change == "model":
        config["model"]["reasoning"] = "low"
    elif change == "session":
        (run / "workspace/.codex/sessions/2026/rollout.jsonl").write_text("bad json")
    else:
        summary = json.loads((run / "summary.json").read_text())
        summary["remaining_tokens"] = 0
        write_json(run / "summary.json", summary)
        RunStore(run).integrity()
    with pytest.raises((ValueError, OSError)):
        create_continuation(run, config, tmp_path)


@pytest.mark.parametrize(
    "relative", ["../outside.txt", "/etc/passwd", "a/../outside.txt", "link/outside.txt"]
)
def test_safe_artifact_reader_rejects_escape(tmp_path, relative):
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        open_regular(tmp_path, relative)


def workspace_parent(tmp_path, agent):
    parent, _, _ = parent_run(tmp_path)
    name = "claude-subscription.yaml" if agent == "claude_code" else "swe-agent-chatgpt.yaml"
    config = load_config(ROOT / "configs" / name)
    config["budget"] = {"wall_seconds": 100, "token_limit": 1000}
    manifest = json.loads((parent / "manifest.json").read_text())
    manifest["config"] = public_config(config)
    write_json(parent / "manifest.json", manifest)
    RunStore(parent).integrity()
    return parent, config


@pytest.mark.parametrize("agent", ["claude_code", "swe_agent"])
def test_workspace_recovery_preserves_evidence_budget_and_history(tmp_path, agent):
    parent, config = workspace_parent(tmp_path, agent)
    before = (parent / "integrity.json").read_bytes()
    with pytest.raises(ValueError, match="Session continuation"):
        create_continuation(parent, config, tmp_path)
    child = create_continuation(parent, config, tmp_path, workspace_only=True)
    lineage = json.loads((child / "continuation.json").read_text())
    assert lineage["session_restored"] is False
    assert lineage["recovery_mode"] == "workspace_only"
    assert "session_id" not in lineage
    assert lineage["prior_known_tokens"] == 400
    assert lineage["inherited_best_score"] == 123.4
    config = json.loads((child / "launch.json").read_text())
    assert config["budget"] == {"wall_seconds": 70, "token_limit": 600}
    history = child / "workspace" / lineage["recovery_transcript"].removeprefix("/workspace/")
    assert history.read_bytes() == (parent / "agent/transcript.jsonl").read_bytes()
    assert (child / "workspace/search.py").read_bytes() == (
        parent / "workspace/search.py"
    ).read_bytes()
    assert (parent / "integrity.json").read_bytes() == before
    runner = Runner(child)
    try:
        assert runner.status()["best_score"] == 123.4
        assert runner.status()["remaining_seconds"] == 70
    finally:
        runner.pool.shutdown()


@pytest.mark.parametrize("changed", ["history", "workspace", "resources"])
@pytest.mark.parametrize("agent", ["claude_code", "swe_agent"])
def test_workspace_recovery_rejects_tampering_or_resource_changes(tmp_path, changed, agent):
    parent, config = workspace_parent(tmp_path, agent)
    if changed == "history":
        (parent / "agent/transcript.jsonl").write_text("changed\n")
    elif changed == "workspace":
        (parent / "workspace/search.py").write_text("changed\n")
    else:
        config["hardware"]["cpus"] = 1
    with pytest.raises(ValueError):
        create_continuation(parent, config, tmp_path, workspace_only=True)


def antigravity_parent(tmp_path):
    parent, _, _ = parent_run(tmp_path)
    config = load_config(ROOT / "configs/antigravity.yaml")
    config["budget"] = {"wall_seconds": 100, "token_limit": 1000}
    manifest = json.loads((parent / "manifest.json").read_text())
    manifest["config"] = public_config(config)
    write_json(parent / "manifest.json", manifest)
    session = uuid.uuid4().hex
    workspace = parent / "workspace"
    database = workspace / f"antigravity/{session}.db"
    database.parent.mkdir()
    # Opaque data only: the host must not open or deserialize SDK database contents.
    database.write_bytes(b"SQLite format 3\x00" + b"checkpoint handled inside container")
    transcript = workspace / f".antigravity/brain/{session}/.system_generated/logs/transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"message": "saved conversation"}\n')
    write_json(parent / "workspace-manifest.json", workspace_inventory(workspace))
    RunStore(parent).integrity()
    return parent, config, session


def test_antigravity_restores_native_checkpoint_and_remaining_budget(tmp_path):
    parent, config, session = antigravity_parent(tmp_path)
    assert checkpoint_report(parent)["session_id"] == session
    # Coordination ID may change; resource limits are preserved.
    config["hardware"]["concurrent_with"] = "another-explicitly-permitted-run"
    child = create_continuation(parent, config, tmp_path)
    restored = json.loads((child / "launch.json").read_text())
    assert restored["budget"] == {"wall_seconds": 70, "token_limit": 600}
    lineage = json.loads((child / "continuation.json").read_text())
    assert lineage["session_id"] == session
    assert lineage["session_format"] == "antigravity-sqlite"
    assert lineage["inherited_best_score"] == 123.4
    relative = f"workspace/antigravity/{session}.db"
    assert (child / relative).read_bytes() == (parent / relative).read_bytes()
    _, env = launch_spec(restored, str(child / "workspace"), session)
    assert env["GTOC_RESUME_SESSION"] == session


def test_completed_attempt_requires_explicit_recovery_and_preserves_original(tmp_path):
    parent, config, session = antigravity_parent(tmp_path)
    summary = json.loads((parent / "summary.json").read_text())
    summary.update(state="completed", stop_reason="agent_finished")
    write_json(parent / "summary.json", summary)
    RunStore(parent).integrity()
    original_summary = (parent / "summary.json").read_bytes()
    original_seal = (parent / "integrity.json").read_bytes()
    with pytest.raises(ValueError, match="Only a finalized"):
        create_continuation(parent, config, tmp_path)
    with pytest.raises(ValueError, match="nonempty"):
        create_continuation(parent, config, tmp_path, recovery_reason=" ")
    child = create_continuation(
        parent, config, tmp_path, recovery_reason="SDK ended without a final response"
    )
    lineage = json.loads((child / "continuation.json").read_text())
    assert lineage["session_id"] == session
    assert lineage["operator_recovery"]["reopened_completed_attempt"] is True
    assert lineage["operator_recovery"]["parent_stop_reason"] == "agent_finished"
    assert lineage["independent_run"] is False
    assert json.loads((child / "launch.json").read_text())["budget"]["wall_seconds"] == 70
    assert (parent / "summary.json").read_bytes() == original_summary
    assert (parent / "integrity.json").read_bytes() == original_seal


@pytest.mark.parametrize("change", ["header", "transcript", "ambiguous", "resources"])
def test_antigravity_rejects_unusable_checkpoint_or_different_resources(tmp_path, change):
    parent, config, session = antigravity_parent(tmp_path)
    workspace = parent / "workspace"
    if change == "resources":
        config["hardware"]["cpus"] = 1
    elif change == "header":
        (workspace / f"antigravity/{session}.db").write_bytes(b"not a database")
    elif change == "transcript":
        (
            workspace / f".antigravity/brain/{session}/.system_generated/logs/transcript.jsonl"
        ).unlink()
    else:
        import shutil

        other = uuid.uuid4().hex
        shutil.copyfile(
            workspace / f"antigravity/{session}.db", workspace / f"antigravity/{other}.db"
        )
        shutil.copytree(
            workspace / f".antigravity/brain/{session}", workspace / f".antigravity/brain/{other}"
        )
    write_json(parent / "workspace-manifest.json", workspace_inventory(workspace))
    RunStore(parent).integrity()
    with pytest.raises(ValueError):
        create_continuation(parent, config, tmp_path)
