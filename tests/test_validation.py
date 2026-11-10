"""Validation/audit isolation, bounded failure recovery, and actionable package errors."""

import base64
import hashlib
import json
import signal
import subprocess
import threading

import pytest

from gtoc_bench import ROOT
from gtoc_bench.analysis import export_progress
from gtoc_bench.bridge import Bridge
from gtoc_bench.config import load_config
from gtoc_bench.network import Internet
from gtoc_bench.observation import SnapshotRequests
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import read_events
from gtoc_bench.validator import evaluate


def test_validator_error_stops_run_and_keeps_its_verdict(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    try:
        receipt = {"artifact_sha256": "a" * 64}
        result = runner.record_verdict(
            "b" * 32,
            receipt,
            {"status": "validator_error", "score": None, "diagnostics": ["Permission denied"]},
        )
        assert result["status"] == "validator_error"
        assert runner.budget.reason == "infrastructure_failure"
        assert (runner.path / ("submissions/" + "b" * 32 + "/verdict.json")).is_file()
    finally:
        runner.pool.shutdown()


def test_native_timeout_is_distinct_from_supervisor_and_transport(tmp_path, monkeypatch):
    def timed_out(*_args, **kw):
        raise subprocess.TimeoutExpired("native validator", kw["timeout"])

    monkeypatch.setattr("gtoc_bench.validator.subprocess.run", timed_out)
    result = evaluate((ROOT / "tests/fixtures/zero_mass.txt").read_bytes(), tmp_path)
    assert result["status"] == "validator_error"
    assert result["timeout_phase"] == "native_validator"
    assert result["timeout_seconds"] == 120


@pytest.mark.parametrize("timeout", [False, True])
def test_validator_runs_without_snapshot_and_cleans_entire_process_group(monkeypatch, timeout):
    bridge = Bridge()
    events, killed = [], []

    class Process:
        pid = 99999999
        returncode = 0
        calls = 0

        def communicate(self, data=None, **kw):
            self.calls += 1
            if self.calls == 1 and timeout:
                raise subprocess.TimeoutExpired("validator", kw["timeout"])
            return json.dumps({"status": "valid", "score": 0}).encode(), b""

    def start(*args, **kw):
        assert kw["user"] == kw["group"] == 1001
        assert kw["extra_groups"] == () and kw["start_new_session"]
        return Process()

    monkeypatch.setattr("gtoc_bench.bridge.subprocess.Popen", start)
    monkeypatch.setattr("gtoc_bench.bridge.os.killpg", lambda *args: killed.append(args))
    monkeypatch.setattr(bridge, "observe", lambda *_: pytest.fail("Audit blocked validation"))
    bridge.send = lambda kind, **data: events.append({"type": kind, **data})
    bridge.validate({"id": "test", "body": base64.b64encode(b"candidate").decode()})
    verdict = next(e["result"] for e in events if e["type"] == "verdict")
    assert verdict["status"] == ("validator_error" if timeout else "valid")
    if timeout:
        assert verdict["timeout_phase"] == "validator_supervisor"
    assert killed == [(Process.pid, signal.SIGKILL)]
    triggers = bridge.snapshots.take(timeout=0)
    assert len(triggers) == 1
    assert triggers[0]["reason"] == "submission" and triggers[0]["submission_id"] == "test"
    assert not bridge.validation_slot.locked()
    assert [e["phase"] for e in events if e["type"] == "validation_phase"] == [
        "started",
        "finished",
    ]


def test_slow_snapshot_worker_does_not_block_submission_snapshot_request(monkeypatch):
    bridge = Bridge()
    bridge.snapshots = SnapshotRequests(limit=8)
    active, release = threading.Event(), threading.Event()
    events = []
    bridge.send = lambda kind, **data: events.append({"type": kind, **data})

    def observe(*_, **__):
        active.set()
        assert release.wait(3)

    monkeypatch.setattr(bridge, "observe", observe)
    bridge.request_snapshot("periodic")
    worker = threading.Thread(target=bridge.snapshot_worker)
    worker.start()
    try:
        assert active.wait(2)
        for i in range(9):
            bridge.request_snapshot("submission", str(i))
        assert events[-1]["type"] == "observation_skipped"
        assert events[-1]["reason"] == "snapshot_trigger_limit"
    finally:
        bridge.observation_stop.set()
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert sum(e.get("reason") == "controller_shutdown" for e in events) == 8


@pytest.mark.parametrize("bad_digest", [False, True])
def test_transport_failure_stops_admission_and_preserves_late_verdict(
    tmp_path, monkeypatch, bad_digest
):
    monkeypatch.setattr("gtoc_bench.runner.TRANSPORT_TIMEOUT", 0.001)
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    runner.send = lambda *_args, **_kw: None
    data, sid = b"candidate", "a" * 32
    try:
        first = runner.accept(data, sid)
        assert first["status"] == "validator_error"
        assert first["timeout_phase"] == "controller_transport"
        assert runner.budget.reason == "infrastructure_failure"
        with pytest.raises(ValueError, match="no longer active"):
            runner.accept(data)
        runner.receive_verdict(
            sid,
            {
                "status": "valid",
                "score": 123.4,
                "artifact_sha256": "bad" if bad_digest else hashlib.sha256(data).hexdigest(),
                "stdout": "native result",
            },
        )
        final = json.loads((runner.path / f"submissions/{sid}/verdict.json").read_text())
        assert final["status"] == ("validator_error" if bad_digest else "valid")
        assert final["late_after_transport_timeout"]
        assert (runner.path / f"submissions/{sid}/transport-error.json").exists()
        assert runner.best == (None if bad_digest else 123.4)
        export_progress(runner.path)
        assert len((runner.path / "exports/progress.csv").read_text().splitlines()) == (
            1 if bad_digest else 2
        )
        assert len(list((runner.path / "submissions").iterdir())) == 1
        assert any(
            e["type"] == "validation_late_verdict"
            for e in read_events(runner.path / "events.jsonl")
        )
    finally:
        runner.pool.shutdown()


def test_denied_package_has_distinct_error_and_catalog(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    runner.internet = Internet(runner.store, runner.budget.stopped)
    replies = []
    runner.send = lambda kind, **frame: replies.append(frame)
    try:
        runner.request_slots.acquire()
        runner.handle(
            {
                "id": "r",
                "method": "GET",
                "path": "/packages/simple/unapproved/",
                "headers": {},
                "body": "",
            }
        )
        reply = replies[-1]
        body = json.loads(base64.b64decode(reply["body"]))
        assert reply["status"] == 403
        assert body["code"] == "package_not_allowed" and not body["retryable"]
        assert {"casadi", "cyipopt", "numba", "ziglang"} <= set(body["allowed_packages"])
        assert "gtoc packages" in body["error"]
    finally:
        runner.internet.client.close()
        runner.pool.shutdown()
