"""Regressions for immediate follow-up requests and attributable local failures."""

import io
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.gateway import Gateway
from gtoc_bench.runner import Runner, create_run
from gtoc_bench.storage import RunStore, read_events


@pytest.mark.parametrize("streaming", [False, True])
def test_next_request_waits_for_previous_response_accounting(tmp_path, streaming):
    cfg = load_config(ROOT / "configs/codex.yaml")
    budget, store = Budget(30), RunStore(tmp_path)
    budget.start()
    finishing, release, queued = (threading.Event() for _ in range(3))
    calls, errors, responses = [], [], []
    event = store.event

    def record(kind, **data):
        result = event(kind, **data)
        if kind == "model_request_queued":
            queued.set()
        if kind == "model_request_finished" and len(calls) == 1:
            finishing.set()
            assert release.wait(3)
        return result

    store.event = record

    def provider(request):
        calls.append(request)
        payload = {"model": cfg["model"]["id"], "usage": {"input_tokens": 3, "output_tokens": 2}}
        if streaming:
            content = (
                b"data: "
                + json.dumps({"type": "response.completed", "response": payload}).encode()
                + b"\n\n"
            )
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, content=content
            )
        return httpx.Response(200, json=payload)

    gateway = Gateway(cfg, "synthetic-key", store, budget, transport=httpx.MockTransport(provider))
    body = json.dumps({"model": cfg["model"]["id"], "stream": streaming}).encode()

    def run(emit, client_id):
        try:
            gateway.proxy("/responses", {}, body, emit, client_request_id=client_id)
        except BaseException as exc:
            errors.append(exc)

    second = threading.Thread(target=lambda: run(lambda *args: responses.append(args), "second"))

    def emit(status, headers, data, end):
        if (end or b'"response.completed"' in data) and second.ident is None:
            second.start()

    first = threading.Thread(target=lambda: run(emit, "first"))
    first.start()
    try:
        assert finishing.wait(2) and queued.wait(2)
        assert len(calls) == 1  # No overlapping upstream inference.
        release.set()
        first.join(3)
        second.join(3)
        assert not first.is_alive() and not second.is_alive() and not errors
        assert len(calls) == 2 and budget.tokens == 10
        assert responses and all(r[0] == 200 for r in responses)
        assert {
            json.loads(f.read_text())["client_request_id"]
            for f in (tmp_path / "requests").glob("*/metadata.json")
        } == {"first", "second"}
    finally:
        release.set()
        first.join(3)
        if second.ident:
            second.join(3)
        gateway.close()


@pytest.mark.parametrize("reason", ["time_limit", "token_limit", "operator_cancelled"])
def test_waiting_request_stops_without_contacting_provider(tmp_path, reason):
    cfg = load_config(ROOT / "configs/codex.yaml")
    budget, store = Budget(30, 100), RunStore(tmp_path)
    budget.start()
    queued = threading.Event()
    event = store.event

    def record(kind, **data):
        result = event(kind, **data)
        if kind == "model_request_queued":
            queued.set()
        return result

    store.event = record
    calls, responses = [], []
    gateway = Gateway(
        cfg,
        "synthetic-key",
        store,
        budget,
        transport=httpx.MockTransport(lambda r: calls.append(r)),
    )
    gateway.slot.acquire()
    worker = threading.Thread(
        target=lambda: gateway.proxy(
            "/responses",
            {},
            json.dumps({"model": cfg["model"]["id"]}).encode(),
            lambda *args: responses.append(args),
        )
    )
    worker.start()
    try:
        assert queued.wait(2)
        if reason == "time_limit":
            budget.wall_seconds = 0.001
        elif reason == "token_limit":
            budget.add_usage({"input_tokens": 100, "output_tokens": 0, "quality": "reported"})
        else:
            budget.stop(reason)
        worker.join(2)
        assert not worker.is_alive() and not calls and responses[0][0] == 503
        rejection = next(
            e for e in read_events(tmp_path / "events.jsonl") if e["type"] == "request_rejected"
        )
        assert rejection["origin"] == "gateway_admission" and rejection["reason"] == "run_inactive"
        assert budget.reason == reason
    finally:
        gateway.slot.release()
        worker.join(2)
        gateway.close()


def test_queue_timeout_is_identified_as_local(tmp_path):
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["recovery"]["max_retry_wait_seconds"] = 0.05
    budget = Budget(30)
    budget.start()
    gateway = Gateway(cfg, "synthetic-key", RunStore(tmp_path), budget)
    responses = []
    gateway.slot.acquire()
    try:
        gateway.proxy(
            "/responses",
            {},
            b"{}",
            lambda *args: responses.append(args),
            client_request_id="queued-client",
        )
        assert responses[0][0] == 503 and b"local_model_queue_timeout" in responses[0][2]
        assert budget.reason == "infrastructure_failure"
        events = list(read_events(tmp_path / "events.jsonl"))
        rejection = next(e for e in events if e["type"] == "request_rejected")
        assert rejection["origin"] == "gateway_admission"
        assert rejection["client_request_id"] == "queued-client"
        assert not any(e["type"] == "model_request_started" for e in events)
    finally:
        gateway.slot.release()
        gateway.close()


def test_overlap_longer_than_ten_seconds_uses_configured_wait(tmp_path, monkeypatch):
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["recovery"]["max_retry_wait_seconds"] = 120
    budget = Budget(300)
    budget.start()
    gateway = Gateway(cfg, "synthetic-key", RunStore(tmp_path), budget)
    import time

    now = [time.monotonic()]
    monkeypatch.setattr("gtoc_bench.gateway.time.monotonic", lambda: now[0])
    attempts, admitted = [], []

    class Slot:
        def acquire(self, timeout):
            attempts.append(timeout)
            if len(attempts) == 1:
                now[0] += 11
                return False
            return True

        def release(self):
            pass

    original_slot = gateway.slot
    gateway.slot = Slot()
    monkeypatch.setattr(gateway, "_proxy", lambda *args: admitted.append(args))
    try:
        gateway.proxy("/responses", {}, b"{}", lambda *_: None)
        assert len(attempts) == 2 and len(admitted) == 1
        assert budget.reason is None
    finally:
        gateway.slot = original_slot
        gateway.close()


def test_controller_capacity_rejection_is_audited(tmp_path):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    frame = {
        "type": "request",
        "id": "busy-client",
        "method": "POST",
        "path": "/v1/responses?private=omit",
    }
    runner.process = SimpleNamespace(stdout=io.BytesIO((json.dumps(frame) + "\n").encode()))
    replies = []
    runner.send = lambda kind, **data: replies.append(data)
    for _ in range(4):
        runner.request_slots.acquire()
    try:
        runner.read()
        assert replies[0]["status"] == 429
        events = list(read_events(runner.path / "events.jsonl"))
        received = next(e for e in events if e["type"] == "http_request_received")
        error = next(e for e in events if e["type"] == "http_error")
        assert received["path"] == "/v1/responses"
        assert received["client_request_id"] == error["client_request_id"] == "busy-client"
        assert error["origin"] == "controller" and error["http_status"] == 429
    finally:
        runner.pool.shutdown()


@pytest.mark.parametrize(
    "reason,state",
    [
        ("agent_error", "failed"),
        ("configuration_error", "failed"),
        ("agent_finished", "completed"),
        ("time_limit", "completed"),
        ("token_limit", "completed"),
        ("operator_cancelled", "cancelled"),
        ("provider_unavailable", "interrupted"),
        ("infrastructure_failure", "interrupted"),
    ],
)
def test_final_status_preserves_failure_and_cancellation(tmp_path, reason, state):
    runner = Runner(create_run(load_config(ROOT / "configs/codex.yaml"), tmp_path))
    runner.budget.start()
    runner.budget.stop(reason)
    runner.finalize()
    for name in ("summary.json", "status.json"):
        saved = json.loads((runner.path / name).read_text())
        assert saved["state"] == state and saved["stop_reason"] == reason
    finished = list(read_events(runner.path / "events.jsonl"))[-1]
    assert finished["type"] == "run_finished" and finished["state"] == state
