"""Regress the lost executable bit before any model request is possible."""

import json
import os
import subprocess
import uuid

import pytest
from fake_provider import FakeProvider

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
pytestmark = [pytest.mark.docker, pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE")]


@pytest.mark.parametrize("broken", [False, True])
def test_judge_preflight_runs_controls_and_refuses_missing_execute_bit(tmp_path, broken):
    image = IMAGE
    if broken:
        image = "gtoc-bench:broken-judge-" + uuid.uuid4().hex[:12]
        recipe = f"FROM {IMAGE}\nRUN chmod 0644 /opt/gtoc/problems/gtoc12/validator/GTOC12_Verify\n"
        subprocess.run(
            ["docker", "build", "--platform", "linux/amd64", "-t", image, "-"],
            input=recipe,
            text=True,
            capture_output=True,
            check=True,
        )
    provider = FakeProvider()
    try:
        cfg = load_config(ROOT / "configs" / ("swe-agent.yaml" if broken else "codex.yaml"))
        cfg["hardware"].update(gpus="none", cpus=2)
        cfg["environment"]["image"] = image
        cfg["budget"] = {"wall_seconds": 60, "token_limit": None}
        env = tmp_path / "provider.env"
        env.write_text("OPENAI_API_KEY=synthetic-judge-check\n")
        env.chmod(0o600)
        cfg["provider"].update(base_url=provider.url, env_file=str(env))
        run = create_run(cfg, tmp_path / "results")
        Runner(run, prompt="Synthetic protocol check. Finish immediately.").run()
        report = json.loads((run / "validator-preflight.json").read_text())
        summary = json.loads((run / "summary.json").read_text())
        if broken:
            assert report["status"] == "failed" and "ownership/mode" in report["error"]
            assert summary["state"] == "interrupted"
            assert summary["stop_reason"] == "infrastructure_failure"
            assert summary["known_tokens"] == 0 and provider.calls == []
            assert not any(e["type"] == "agent_command" for e in read_events(run / "events.jsonl"))
        else:
            assert report["status"] == "passed"
            assert [r["result"]["status"] for r in report["controls"]] == ["valid", "invalid"]
            assert summary["stop_reason"] == "agent_finished", summary
        seal = json.loads((run / "integrity.json").read_text())
        assert "validator-preflight.json" in seal["files"]
    finally:
        provider.close()
        if broken:
            subprocess.run(["docker", "image", "rm", image], capture_output=True)
