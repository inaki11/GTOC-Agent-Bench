"""Released SDK integration with synthetic inference, including native session restore."""

import json
import os

import pytest
from fake_provider import FakeProvider

from gtoc_bench import ROOT
from gtoc_bench.audit import read_records
from gtoc_bench.config import load_config
from gtoc_bench.continuation import create_continuation
from gtoc_bench.runner import Runner, create_run

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
pytestmark = [pytest.mark.docker, pytest.mark.skipif(not IMAGE, reason="GTOC_TEST_IMAGE required")]


def test_released_sdk_records_terminal_reason_and_restores_same_conversation(tmp_path):
    command = "echo synthetic-resume-marker > /workspace/marker.txt"
    provider = FakeProvider(tool_command=command)
    cfg = load_config(ROOT / "configs/antigravity-openai.yaml")
    key = tmp_path / "provider.env"
    key.write_text("TEST_API_KEY=synthetic-only\n")
    key.chmod(0o600)
    cfg["provider"] = {
        "id": "synthetic",
        "protocol": "chat_completions",
        "auth_method": "api_key",
        "api_key_variable": "TEST_API_KEY",
        "env_file": str(key),
        "base_url": provider.url,
    }
    cfg["environment"]["image"] = IMAGE
    cfg["hardware"].update(cpus=2, memory_gb=3, gpus="none")
    cfg["budget"] = {"wall_seconds": 180, "token_limit": None}
    cfg["recovery"]["clock_policy"] = "continuous_wall"
    parent = create_run(cfg, tmp_path / "results")
    try:
        Runner(
            parent,
            prompt="Remember context-marker-argon-sdk-317. Execute the requested test and finish.",
        ).run()
        summary = json.loads((parent / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert (parent / "workspace/marker.txt").read_text().strip() == "synthetic-resume-marker"
        events = [
            r.get("native_event", {}) or {} for r in read_records(parent / "agent/transcript.jsonl")
        ]
        assert any(e["type"] == "sdk.stop_requested" for e in events)
        assert any(e["type"] == "session.turn_ended" and e["completed_response"] for e in events)
        assert not any(e["type"] == "session.recovery_requested" for e in events)
        checkpoint = json.loads((parent / "checkpoint.json").read_text())
        assert checkpoint["available"], checkpoint
        original_seal = (parent / "integrity.json").read_bytes()
        child = create_continuation(
            parent,
            cfg,
            tmp_path / "results",
            recovery_reason="Synthetic SDK session restoration test",
        )
        cutoff = len(provider.calls)
        Runner(child).run()
        after = json.loads((child / "summary.json").read_text())
        assert after["stop_reason"] == "agent_finished", after
        assert after["wall_seconds"] == summary["remaining_seconds"]
        assert after["cumulative_known_tokens"] == summary["known_tokens"] + after["known_tokens"]
        assert (
            json.loads((child / "checkpoint.json").read_text())["session_id"]
            == checkpoint["session_id"]
        )
        assert (parent / "integrity.json").read_bytes() == original_seal
        assert "context-marker-argon-sdk-317" in json.dumps(
            [c["body"] for c in provider.calls[cutoff:]]
        )
        assert provider.tool_sent  # No replay: this provider emits its command only once.
    finally:
        provider.close()
