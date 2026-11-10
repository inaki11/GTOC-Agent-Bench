"""Real SWE-agent and a frozen CPU container; the provider is deterministic/local."""

import json
import os
import shlex
import subprocess
import threading
import time

import httpx
import pytest
from fake_provider import FakeProvider

from gtoc_bench import ROOT
from gtoc_bench.config import load_config
from gtoc_bench.continuation import verify_seal
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events

IMAGE = os.environ.get("GTOC_TEST_IMAGE")
TIMEOUT_SCALE = float(os.environ.get("GTOC_TEST_TIMEOUT_SCALE", "1"))
pytestmark = [pytest.mark.docker, pytest.mark.skipif(not IMAGE, reason="Set GTOC_TEST_IMAGE")]


@pytest.mark.parametrize(
    "agent,failure",
    [
        ("swe-agent", "overload"),
        ("codex", "overload"),
        ("antigravity-openai", "overload"),
        ("antigravity-openai", "idle"),
    ],
)
def test_swe_agent_survives_overload_freezes_work_and_resumes_same_request(
    tmp_path, monkeypatch, agent, failure
):
    counter = "from pathlib import Path; import time; p=Path('/workspace/counter.txt'); i=0\nwhile True:\n i+=1; p.write_text(str(i)); p.chmod(0o644); time.sleep(.03)"
    script = (
        "import subprocess,time; from pathlib import Path; "
        "subprocess.Popen(['/opt/conda/bin/python','-c',"
        + repr(counter)
        + "],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); "
        "time.sleep(.25); Path('/workspace/tool-once.txt').write_text('once')"
    )
    provider = FakeProvider(
        tool_command="/opt/conda/bin/python -c " + shlex.quote(script),
        overload_after=1 if failure == "overload" else None,
        overload_retry_after=12,
        idle_after=1 if failure == "idle" else None,
    )
    cfg = load_config(ROOT / "configs" / f"{agent}.yaml")
    cfg["model"].update(id="gpt-6.1-sol", reasoning="max")
    cfg["hardware"].update(gpus="none", cpus=2)
    cfg["environment"]["image"] = IMAGE
    cfg["recovery"].update(
        clock_policy="provider_wait",
        max_transient_retries=0,
        provider_retry_interval_seconds=0.2 if failure == "overload" else 12,
        provider_wait_seconds=30,
    )
    cfg["budget"] = {"wall_seconds": 90 * TIMEOUT_SCALE, "token_limit": None}
    env = tmp_path / "provider.env"
    env.write_text("OPENAI_API_KEY=synthetic-provider-wait-key\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    run = create_run(cfg, tmp_path / "results")
    (run / "workspace").chmod(0o755)
    runner = Runner(
        run,
        prompt="This is a protocol integration test. Execute the supplied command once, then finish.",
    )
    if failure == "idle":
        from gtoc_bench.gateway import Gateway

        init = Gateway.__init__

        def short_idle(self, *args, **kwargs):
            init(self, *args, **kwargs)
            self.inference.timeout = httpx.Timeout(60, read=1)

        monkeypatch.setattr(Gateway, "__init__", short_idle)
    paused = threading.Event()
    event = runner.store.event

    def record(kind, **data):
        result = event(kind, **data)
        if kind == "provider_wait_paused":
            paused.set()
        return result

    runner.store.event = record
    worker = threading.Thread(target=runner.run)
    began = time.monotonic()
    worker.start()
    try:
        assert paused.wait(70 * TIMEOUT_SCALE), (run / "status.json").read_text()
        assert runner.provider_wait.freezer.observe()["frozen"] == 1
        before = (run / "workspace/counter.txt").read_text()
        active = runner.budget.status()["elapsed_seconds"]
        time.sleep(0.25)
        assert (run / "workspace/counter.txt").read_text() == before
        assert runner.budget.status()["elapsed_seconds"] == pytest.approx(active, abs=0.01)
        worker.join(80 * TIMEOUT_SCALE)
        assert not worker.is_alive()
        summary = json.loads((run / "summary.json").read_text())
        assert summary["state"] == "completed" and summary["stop_reason"] == "agent_finished", (
            summary
        )
        assert summary["paused_seconds"] >= 11.5
        assert time.monotonic() - began > summary["elapsed_seconds"] + 11
        assert len(provider.calls) == 3
        assert provider.calls[1]["body"] == provider.calls[2]["body"]
        assert (run / "workspace/tool-once.txt").read_text() == "once"
        events = list(read_events(run / "events.jsonl"))
        assert any(e["type"] == "provider_wait_recovered" for e in events)
        scheduled = next(e for e in events if e["type"] == "provider_wait_retry_scheduled")
        if failure == "overload":
            assert scheduled["retry_after"]["seconds"] == 12
        else:
            assert any(
                e["type"] == "model_stream_interrupted" and e.get("exception_type") == "ReadTimeout"
                for e in events
            )
        assert scheduled["minimum_retry_delay_seconds"] == 12
        assert not any(e["type"] == "model_client_error" for e in events)
        assert summary["finalization"]["safe_for_successor"]
        assert json.loads((run / "freezer.json").read_text())["preflight"] == "passed"
        assert {"freezer.json", "freezer.jsonl"} <= verify_seal(run)["files"].keys()
    finally:
        if worker.is_alive():
            runner.budget.stop("operator_cancelled")
            if runner.gateway:
                runner.gateway.cancel()
            worker.join(30)
        provider.close()


@pytest.mark.parametrize("failure", ["http", "sse"])
def test_exhausted_provider_wait_closes_frozen_container_and_preserves_budget(tmp_path, failure):
    provider = (
        FakeProvider(error_status=503)
        if failure == "http"
        else FakeProvider(overload_after=0, overload_retry_after=302)
    )
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["hardware"].update(gpus="none", cpus=2)
    cfg["environment"]["image"] = IMAGE
    cfg["recovery"].update(
        clock_policy="provider_wait",
        max_transient_retries=0,
        provider_retry_interval_seconds=0.2,
        provider_wait_seconds=1,
    )
    cfg["budget"] = {"wall_seconds": 60, "token_limit": None}
    env = tmp_path / "provider.env"
    env.write_text("OPENAI_API_KEY=synthetic-provider-wait-exhaustion\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    run = create_run(cfg, tmp_path / "results")
    try:
        Runner(run, prompt="Synthetic provider recovery failure test.").run()
        summary = json.loads((run / "summary.json").read_text())
        assert (
            summary["state"] == "interrupted" and summary["stop_reason"] == "provider_unavailable"
        ), summary
        assert summary["paused_seconds"] >= 0.9 and summary["remaining_seconds"] > 45
        assert summary["finalization"]["safe_for_successor"]
        inspection = subprocess.run(
            ["docker", "inspect", "gtoc-" + run.name.lower()], capture_output=True
        )
        assert inspection.returncode != 0
        assert (run / "integrity.json").is_file() and (run / "workspace-manifest.json").is_file()
        assert any(
            e["type"] == "provider_wait_exhausted" for e in read_events(run / "events.jsonl")
        )
        seal = verify_seal(run)
        assert "provider-recovery.json" in seal["files"]
        recovery = json.loads((run / "provider-recovery.json").read_text())
        assert recovery["state"] == "interrupted"
        assert recovery["outcome"] == "recovery_window_exhausted"
        if failure == "sse":
            assert len(provider.calls) == 1
            assert recovery["retry_after"]["seconds"] == 302
            assert json.loads((run / "checkpoint.json").read_text())["available"]
    finally:
        provider.close()


def test_failed_freezer_preflight_prevents_any_model_request(tmp_path, monkeypatch):
    from gtoc_bench.freezer import ContainerFreezer

    def refuse_pause(self, frozen):
        if frozen:
            raise RuntimeError("Synthetic unconfirmed freeze")

    monkeypatch.setattr(ContainerFreezer, "transition", refuse_pause)
    provider = FakeProvider()
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["hardware"].update(gpus="none", cpus=2, memory_gb=2)
    cfg["environment"]["image"] = IMAGE
    cfg["recovery"]["clock_policy"] = "provider_wait"
    env = tmp_path / "provider.env"
    env.write_text("OPENAI_API_KEY=synthetic-freezer-preflight\n")
    env.chmod(0o600)
    cfg["provider"].update(base_url=provider.url, env_file=str(env))
    run = create_run(cfg, tmp_path / "results")
    try:
        Runner(run).run()
        assert provider.calls == []
        summary = json.loads((run / "summary.json").read_text())
        assert summary["elapsed_seconds"] == 0 and summary["state"] == "failed"
        assert summary["finalization"]["safe_for_successor"]
        assert json.loads((run / "freezer.json").read_text())["preflight"] == "failed"
    finally:
        provider.close()
