"""Real client/container, simulated provider, liveness separated from model traffic."""

import json
import os
import shlex
import subprocess
import threading
import time

import pytest
from fake_provider import FakeProvider

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events
from gtoc_bench.supervision import HEARTBEAT_TIMEOUT

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
pytestmark = [pytest.mark.docker, pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE")]


def setup_run(tmp_path, provider, memory=2):
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["hardware"].update(cpus=2, gpus="none", memory_gb=memory)
    cfg["environment"]["image"] = IMAGE
    cfg["budget"] = {"wall_seconds": 120, "token_limit": None}
    env = tmp_path / "provider.env"
    env.write_text("OPENAI_API_KEY=synthetic-memory-provider\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    run = create_run(cfg, tmp_path / "results")
    return run, Runner(run, prompt="Execute the supplied protocol test command once and finish.")


def test_full_response_pipe_does_not_block_independent_heartbeat(tmp_path):
    provider = FakeProvider(block=True)
    run, runner = setup_run(tmp_path, provider)
    worker = threading.Thread(target=runner.run)
    worker.start()
    try:
        assert provider.arrived.wait(75), (run / "status.json").read_text()
        with runner.pipe_lock:
            before = json.loads((run / "control/heartbeat.json").read_text())["sequence"]
            time.sleep(HEARTBEAT_TIMEOUT + 2)
            after = json.loads((run / "control/heartbeat.json").read_text())["sequence"]
            assert after > before + 10
            assert runner.budget.reason is None and runner.fatal is None
        denied = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "1000:1000",
                runner.name,
                "/bin/sh",
                "-c",
                "echo spoof > /run/gtoc-control/heartbeat.json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert denied.returncode != 0
        provider.release.set()
        worker.join(75)
        assert not worker.is_alive()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "agent_finished", summary
        assert (
            json.loads((run / "supervision.json").read_text())["heartbeat_channel"]
            == "private_read_only_file"
        )
    finally:
        provider.release.set()
        if worker.is_alive():
            runner.budget.stop("operator_cancelled")
            if runner.gateway:
                runner.gateway.cancel()
            worker.join(40)
        provider.close()


def test_gradual_memory_exhaustion_is_cut_off_and_sealed_without_losing_heartbeat(tmp_path):
    allocation = (
        "import time; data=[]\nwhile True:\n data.append(bytearray(4*1024**2)); time.sleep(.25)"
    )
    provider = FakeProvider(
        tool_command="/opt/conda/bin/python -c " + shlex.quote(allocation), hold_after=1
    )
    run, runner = setup_run(tmp_path, provider, memory=1)
    worker = threading.Thread(target=runner.run)
    worker.start()
    try:
        worker.join(160)
        assert not worker.is_alive()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["stop_reason"] == "memory_limit" and summary["state"] == "interrupted", (
            summary
        )
        assert summary["remaining_seconds"] > 0
        assert summary["fatal"] is None
        events = list(read_events(run / "events.jsonl"))
        assert any(e["type"] == "memory_limit_reached" for e in events)
        assert any(e["type"] == "memory_guard_agent_stopped" for e in events)
        seal = json.loads((run / "integrity.json").read_text())
        assert "memory.jsonl" in seal["files"] and "supervision.json" in seal["files"]
        assert json.loads((run / "checkpoint.json").read_text())["available"]
        assert summary["finalization"]["safe_for_successor"]
    finally:
        if worker.is_alive():
            runner.budget.stop("operator_cancelled")
            if runner.gateway:
                runner.gateway.cancel()
            worker.join(40)
        provider.close()
