"""Paused accounting, buffered turn replay and bounded external outage recovery."""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.gateway import Gateway
from gtoc_bench.provider_wait import ProviderWait, model_timeout
from gtoc_bench.storage import RunStore, read_events


class PauseFixture:
    def __init__(self, budget):
        self.budget = budget
        self.suspended = threading.Event()
        self.entered = threading.Event()
        self.pauses = self.resumes = 0

    def pause(self):
        if not self.suspended.is_set():
            self.budget.pause()
            self.suspended.set()
            self.entered.set()
            self.pauses += 1

    def resume(self, stopping=False):
        if self.suspended.is_set():
            self.suspended.clear()
            self.budget.resume()
            self.resumes += 1


def completed(model):
    return httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        content=(
            "data: "
            + json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "recovered",
                        "model": model,
                        "service_tier": "default",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                }
            )
            + "\n\n"
        ).encode(),
    )


def make_gateway(tmp_path, handler, *, adapted=False, interval=0.04, window=0.2):
    cfg = load_config(ROOT / "configs" / ("swe-agent-chatgpt.yaml" if adapted else "codex.yaml"))
    cfg["recovery"].update(
        clock_policy="provider_wait",
        max_transient_retries=0,
        provider_retry_interval_seconds=interval,
        provider_wait_seconds=window,
    )
    store, budget = RunStore(tmp_path), Budget(30)
    budget.start()
    store.clock = budget.timeline_elapsed
    gateway = Gateway(
        cfg, "synthetic-provider-key", store, budget, transport=httpx.MockTransport(handler)
    )
    gateway.provider_wait = PauseFixture(budget)
    return gateway


def proxy(gateway, emitted, adapted=False, client_id=None):
    gateway.proxy(
        "/chat/completions" if adapted else "/responses",
        {},
        json.dumps(
            {
                "model": gateway.config["model"]["id"],
                **(
                    {"messages": [{"role": "user", "content": "synthetic task"}]}
                    if adapted
                    else {"input": "synthetic task"}
                ),
                "stream": not adapted,
            }
        ).encode(),
        lambda *args: emitted.append(args),
        client_request_id=client_id,
    )


@pytest.mark.parametrize("adapted", [False, True])
@pytest.mark.parametrize("envelope", ["error", "response.failed"])
@pytest.mark.parametrize("code", ["context_length_exceeded", "context_window_exceeded"])
def test_permanent_context_error_is_not_retried_or_paused(tmp_path, adapted, envelope, code):
    calls, emitted = [], []
    error = {
        "type": "invalid_request_error",
        "code": code,
        "message": "Reduce the input context.",
        "param": "input",
    }
    event = {"type": envelope}
    event["error" if envelope == "error" else "response"] = (
        error if envelope == "error" else {"error": error}
    )

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"data: " + json.dumps(event).encode() + b"\n\n",
            )
        return completed(calls[-1]["model"])

    gateway = make_gateway(tmp_path, provider, adapted=adapted, interval=300, window=3600)
    try:
        proxy(gateway, emitted, adapted)
        assert len(calls) == 1 and gateway.provider_wait.pauses == 0
        assert gateway.budget.reason is None
        assert code.encode() in b"".join(frame[2] for frame in emitted)
        if adapted:
            assert len(emitted) == 1 and emitted[0][0] == 400
            assert json.loads(emitted[0][2])["error"] == error
        events = list(read_events(tmp_path / "events.jsonl"))
        assert not any(e["type"] == "provider_wait_started" for e in events)
        assert (
            next(e for e in events if e["type"] == "provider_stream_error")["classification"]
            == "request"
        )
        recovered = []
        proxy(gateway, recovered, adapted)
        assert len(calls) == 2 and gateway.budget.reason is None
        assert b"done" in b"".join(frame[2] for frame in recovered)
    finally:
        gateway.close()


def test_budget_pause_keeps_remaining_time_and_token_limit_live(monkeypatch, tmp_path):
    tick = [100.0]
    monkeypatch.setattr("gtoc_bench.budget.time.monotonic", lambda: tick[0])
    budget = Budget(20, 100)
    store = RunStore(tmp_path)
    store.clock = budget.timeline_elapsed
    budget.start()
    tick[0] = 105
    budget.pause()
    tick[0] = 3705
    assert budget.check() and budget.status()["elapsed_seconds"] == store.elapsed() == 5
    assert budget.status()["remaining_seconds"] == 15
    assert budget.status()["paused_seconds"] == 3600
    budget.resume()
    tick[0] = 3707
    assert budget.status()["elapsed_seconds"] == 7
    budget.pause()
    budget.add_usage({"input_tokens": 100, "quality": "reported"})
    assert budget.reason == "token_limit" and budget.status()["elapsed_seconds"] == 7
    assert not budget.status()["budget_paused"]


@pytest.mark.parametrize("adapted", [False, True])
def test_sse_overload_is_retried_without_exposing_partial_turn_or_tools(tmp_path, adapted):
    calls, emitted = [], []

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            events = [
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "call_id": "must-not-execute",
                        "name": "bash",
                        "arguments": '{"command":"touch duplicate"}',
                    },
                },
                {"type": "error", "code": "server_is_overloaded", "message": "overloaded"},
            ]
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"".join(("data: " + json.dumps(e) + "\n\n").encode() for e in events),
            )
        return completed(calls[-1]["model"])

    gateway = make_gateway(tmp_path, provider, adapted=adapted)
    began = time.monotonic()
    active_before = gateway.budget.status()["elapsed_seconds"]
    try:
        proxy(gateway, emitted, adapted)
        assert len(calls) == 2 and calls[0] == calls[1]
        assert gateway.budget.reason is None and gateway.budget.tokens == 12
        assert gateway.budget.status()["paused_seconds"] >= 0.035
        assert (
            time.monotonic() - began
            > gateway.budget.status()["elapsed_seconds"] - active_before + 0.03
        )
        assert gateway.provider_wait.pauses == gateway.provider_wait.resumes == 1
        assert all(frame[0] == 200 for frame in emitted)
        assert b"must-not-execute" not in b"".join(frame[2] for frame in emitted)
        assert b"done" in b"".join(frame[2] for frame in emitted)
        events = list(read_events(tmp_path / "events.jsonl"))
        assert any(e["type"] == "provider_wait_recovered" for e in events)
        assert gateway.budget.unknown_requests == 1
    finally:
        gateway.provider_wait.resume()
        gateway.close()


def test_outage_window_exhausts_without_spending_active_budget(tmp_path):
    calls, emitted = [], []

    def provider(request):
        calls.append(time.monotonic())
        return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "temporary outage"})

    gateway = make_gateway(tmp_path, provider, interval=0.03, window=0.1)
    remaining_at_pause = []
    pause = gateway.provider_wait.pause

    def measured_pause():
        pause()
        remaining_at_pause.append(gateway.budget.status()["remaining_seconds"])

    gateway.provider_wait.pause = measured_pause
    try:
        proxy(gateway, emitted)
        assert 1 <= len(calls) <= 4
        assert gateway.budget.reason == "provider_unavailable"
        assert gateway.budget.status()["paused_seconds"] >= 0.09
        # Initial HTTP/audit setup consumes active time and may exceed 100 ms
        # on a loaded host. Verify the paused budget itself, not setup speed.
        assert gateway.budget.status()["remaining_seconds"] == pytest.approx(
            remaining_at_pause[0], abs=0.00001
        )
        assert gateway.provider_wait.suspended.is_set()
        assert len(emitted) == 1 and emitted[0][0] == 503
        assert any(
            e["type"] == "provider_wait_exhausted" for e in read_events(tmp_path / "events.jsonl")
        )
    finally:
        gateway.provider_wait.resume(stopping=True)
        gateway.close()


@pytest.mark.parametrize("stop_reason", ["time_limit", "token_limit", "operator_cancelled"])
def test_later_run_stop_does_not_overwrite_a_completed_recovery(tmp_path, stop_reason):
    calls, emitted = [], []
    arrived = threading.Event()

    async def provider(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b'data: {"type":"error","code":"server_is_overloaded"}\n\n',
            )
        if len(calls) == 2:
            return completed(json.loads(request.content)["model"])
        arrived.set()
        await asyncio.Event().wait()

    gateway = make_gateway(tmp_path, provider)
    worker = None
    try:
        proxy(gateway, emitted)
        path = tmp_path / "provider-recovery.json"
        recovered = path.read_bytes()
        record = json.loads(recovered)
        assert record["state"] == record["outcome"] == "recovered"
        assert record["cause"] == "server_is_overloaded"
        assert not record["continuation_requires_checkpoint"]
        worker = threading.Thread(target=proxy, args=(gateway, []))
        worker.start()
        assert arrived.wait(2)
        gateway.budget.stop(stop_reason)
        worker.join(2)
        assert not worker.is_alive()
        gateway.close()
        assert path.read_bytes() == recovered
        stop = json.loads((tmp_path / "gateway-stop.json").read_text())
        assert stop["scope"] == "run_termination" and stop["stop_reason"] == stop_reason
        assert stop["last_recovery_state"] == "recovered"
        gateway.store.integrity()
        seal = json.loads((tmp_path / "integrity.json").read_text())
        assert "gateway-stop.json" in seal["files"]
    finally:
        if worker is not None and worker.is_alive():
            gateway.budget.stop("operator_cancelled")
            gateway.cancel()
            worker.join(2)
        if not gateway.loop or not gateway.loop.is_closed():
            gateway.close()


def test_stop_while_delivering_a_recovered_turn_preserves_the_recovery_outcome(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "overload"})
        return completed(json.loads(request.content)["model"])

    gateway = make_gateway(tmp_path, provider)
    replies = []

    class StopOnDelivery(list):
        def append(self, frame):
            replies.append(frame)
            if b"done" in frame[2]:
                gateway.budget.stop("time_limit")

    try:
        proxy(gateway, StopOnDelivery())
        assert gateway.budget.reason == "time_limit"
        record = json.loads((tmp_path / "provider-recovery.json").read_text())
        assert record["state"] == record["outcome"] == "recovered"
        assert b"done" in b"".join(frame[2] for frame in replies)
    finally:
        gateway.close()


@pytest.mark.parametrize("adapted", [False, True])
@pytest.mark.parametrize("envelope", ["error", "response.failed"])
def test_dilithium_sse_302_second_hint_overrides_interval_and_short_window(
    tmp_path, monkeypatch, adapted, envelope
):
    calls, emitted, sleeps = [], [], []
    error = {
        "code": "server_is_overloaded",
        "type": "service_unavailable_error",
        "message": "Our servers are currently overloaded. Please try again later.",
        "headers": {"retry-after": "302", "retry-after-ms": "302000"},
    }
    event = {"type": envelope, "sequence_number": 3}
    event["response" if envelope == "response.failed" else "error"] = (
        {"error": error} if envelope == "response.failed" else error
    )
    wire = ("event: error\ndata: " + json.dumps(event) + "\n\n").encode()

    class Fragmented(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(wire), 11):
                yield wire[offset : offset + 11]

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            # The original provider also omitted Content-Type on its HTTP 200 SSE.
            return httpx.Response(200, stream=Fragmented())
        return completed(calls[-1]["model"])

    async def record_sleep(delay):
        if delay:
            sleeps.append(delay)

    monkeypatch.setattr("gtoc_bench.gateway.asyncio.sleep", record_sleep)
    gateway = make_gateway(tmp_path, provider, adapted=adapted, interval=300, window=3600)
    try:
        proxy(gateway, emitted, adapted)
        assert len(calls) == 2 and calls[0] == calls[1]
        assert sleeps == pytest.approx([302], abs=0.02)
        assert gateway.budget.reason is None
        assert b"server_is_overloaded" not in b"".join(frame[2] for frame in emitted)
        events = list(read_events(tmp_path / "events.jsonl"))
        error_event = next(e for e in events if e["type"] == "provider_stream_error")
        assert error_event["http_status"] == 200
        assert error_event["classification"] == "transient"
        assert error_event["code"] == "server_is_overloaded"
        assert error_event["retry_after"]["seconds"] == 302
        retry = next(e for e in events if e["type"] == "provider_wait_retry_scheduled")
        assert retry["minimum_retry_delay_seconds"] == 302
        assert retry["retry_within_window"]
        recovery = json.loads((tmp_path / "provider-recovery.json").read_text())
        assert recovery["state"] == "recovered"
        assert recovery["retry_after"]["source"] == "error.headers.retry-after"
        metadata = next(
            json.loads(path.read_text())
            for path in (tmp_path / "requests").glob("*/metadata.json")
            if json.loads(path.read_text()).get("provider_error_code")
        )
        assert metadata["provider_error"]["retry_after"]["seconds"] == 302
    finally:
        gateway.provider_wait.resume(stopping=True)
        gateway.close()


@pytest.mark.parametrize("hint_source", ["http", "http_body", "sse"])
def test_hint_beyond_recovery_window_is_preserved_and_never_retried_early(tmp_path, hint_source):
    calls, emitted = [], []
    error = {"code": "server_is_overloaded", "headers": {"retry-after-ms": "302000"}}

    def provider(request):
        calls.append(request)
        if hint_source == "http":
            return httpx.Response(503, headers={"Retry-After": "302"}, json={"error": "overloaded"})
        if hint_source == "http_body":
            return httpx.Response(503, json={"error": error})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=("data: " + json.dumps({"type": "error", "error": error}) + "\n\n").encode(),
        )

    gateway = make_gateway(tmp_path, provider, interval=0.01, window=0.06)
    try:
        proxy(gateway, emitted)
        assert len(calls) == 1
        assert gateway.budget.reason == "provider_unavailable"
        assert gateway.budget.status()["paused_seconds"] >= 0.05
        recovery = json.loads((tmp_path / "provider-recovery.json").read_text())
        assert recovery["state"] == "interrupted"
        assert recovery["outcome"] == "recovery_window_exhausted"
        assert recovery["retry_after"]["seconds"] == 302
        earliest = datetime.fromisoformat(recovery["retry_not_before_utc"])
        assert (earliest - datetime.now(UTC)).total_seconds() > 301
        assert not recovery["retry_within_window"]
        assert recovery["remaining_seconds"] > 29
    finally:
        gateway.provider_wait.resume(stopping=True)
        gateway.close()


@pytest.mark.parametrize("code", ["QUOTA_EXHAUSTED", "insufficient_quota", "invalid_api_key"])
def test_sse_quota_and_auth_errors_are_terminal_even_with_a_retry_hint(tmp_path, code):
    calls, emitted = [], []

    def provider(request):
        calls.append(request)
        event = {
            "type": "error",
            "code": code,
            "message": "failure",
            "headers": {"retry-after": "302"},
        }
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=("data: " + json.dumps(event) + "\n\n").encode(),
        )

    gateway = make_gateway(tmp_path, provider)
    try:
        proxy(gateway, emitted)
        assert len(calls) == 1 and not gateway.provider_wait.entered.is_set()
        assert gateway.budget.reason == (
            "configuration_error" if code == "invalid_api_key" else "provider_unavailable"
        )
        event = next(
            e
            for e in read_events(tmp_path / "events.jsonl")
            if e["type"] == "provider_stream_error"
        )
        assert event["classification"] == (
            "configuration" if code == "invalid_api_key" else "quota"
        )
    finally:
        gateway.close()


def test_operator_cancel_wakes_wait_immediately_and_does_not_retry(tmp_path):
    calls, emitted = [], []

    def provider(request):
        calls.append(1)
        return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "outage"})

    gateway = make_gateway(tmp_path, provider, interval=2, window=5)
    worker = threading.Thread(target=lambda: proxy(gateway, emitted))
    worker.start()
    try:
        assert gateway.provider_wait.entered.wait(2)
        gateway.budget.stop("operator_cancelled")
        gateway.cancel()
        worker.join(1)
        assert not worker.is_alive() and len(calls) == 1
        assert gateway.budget.reason == "operator_cancelled"
    finally:
        gateway.provider_wait.resume(stopping=True)
        gateway.close()


def test_recovery_deadline_interrupts_an_attempt_stuck_waiting_for_headers(tmp_path):
    calls, emitted = [], []

    async def provider(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "outage"})
        await asyncio.sleep(20)
        return completed(json.loads(request.content)["model"])

    gateway = make_gateway(tmp_path, provider, interval=0.02, window=0.08)
    started = time.monotonic()
    try:
        proxy(gateway, emitted)
        assert len(calls) == 2
        assert time.monotonic() - started < 0.5
        assert gateway.budget.reason == "provider_unavailable"
        assert gateway.budget.status()["paused_seconds"] >= 0.07
    finally:
        gateway.provider_wait.resume(stopping=True)
        gateway.close()


def test_recovery_window_still_bounds_a_retry_sending_only_keepalives(tmp_path):
    calls, emitted = [], []

    class Heartbeats(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b": keep-alive\n\n"
                await asyncio.sleep(0.01)

    def provider(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                503, headers={"Retry-After": "0"}, json={"error": "temporary outage"}
            )
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Heartbeats()
        )

    gateway = make_gateway(tmp_path, provider, interval=0.02, window=0.12)
    began = time.monotonic()
    try:
        proxy(gateway, emitted)
        assert time.monotonic() - began < 0.8
        assert len(calls) == 2
        assert gateway.provider_wait.resumes == 1  # Headers resumed compute, not recovery success.
        assert gateway.budget.reason == "provider_unavailable"
        events = list(read_events(tmp_path / "events.jsonl"))
        assert any(e["type"] == "provider_wait_exhausted" for e in events)
        assert not any(e["type"] == "provider_wait_recovered" for e in events)
    finally:
        gateway.provider_wait.resume(stopping=True)
        gateway.close()


@pytest.mark.parametrize("status,code", [(401, "invalid_api_key"), (429, "insufficient_quota")])
def test_authentication_and_hard_billing_quota_remain_terminal(tmp_path, status, code):
    calls, emitted = [], []

    def provider(request):
        calls.append(1)
        return httpx.Response(status, json={"error": {"code": code}})

    gateway = make_gateway(tmp_path, provider)
    try:
        proxy(gateway, emitted)
        assert len(calls) == 1 and not gateway.provider_wait.entered.is_set()
        assert gateway.budget.reason in {"provider_unavailable", "configuration_error"}
    finally:
        gateway.close()


def test_queue_deadline_excludes_provider_pause(tmp_path):
    calls, first, second = [], [], []

    def provider(request):
        calls.append(1)
        return (
            httpx.Response(503, headers={"Retry-After": "0"}, json={"error": "outage"})
            if len(calls) == 1
            else completed(json.loads(request.content)["model"])
        )

    gateway = make_gateway(tmp_path, provider, interval=0.25, window=1)
    gateway.config["recovery"]["max_retry_wait_seconds"] = 0.15
    workers = [
        threading.Thread(target=lambda: proxy(gateway, first, client_id="first")),
        threading.Thread(target=lambda: proxy(gateway, second, client_id="second")),
    ]
    try:
        workers[0].start()
        assert gateway.provider_wait.entered.wait(2)
        workers[1].start()
        for worker in workers:
            worker.join(2)
        assert all(not w.is_alive() for w in workers)
        assert len(calls) == 3 and gateway.budget.reason is None
        assert all(e[0] == 200 for e in first + second)
    finally:
        gateway.provider_wait.resume()
        gateway.close()


def test_defaults_and_client_timeout_cover_the_pause_window():
    cfg = load_config(ROOT / "configs/swe-agent-chatgpt.yaml")
    assert cfg["recovery"]["clock_policy"] == "provider_wait"
    assert cfg["recovery"]["provider_retry_interval_seconds"] == 300
    assert cfg["recovery"]["provider_wait_seconds"] == 3600
    assert model_timeout(cfg) > cfg["budget"]["wall_seconds"] + 3600


def test_engine_pause_handshake_freezes_only_after_confirmation_and_cleans_up(tmp_path):
    from types import SimpleNamespace

    budget = Budget(60)
    budget.start()
    runner = SimpleNamespace(
        name="synthetic-container",
        budget=budget,
        gpu_allocation={"assigned": "none"},
        evaluation_slot=threading.Lock(),
        store=RunStore(tmp_path),
        status=budget.status,
    )
    wait = ProviderWait(runner)
    messages, actions = [], []

    def send(kind, **data):
        messages.append(kind)
        if "id" in data:
            wait.acknowledge(data["id"])

    runner.send = send
    wait.command = lambda action: actions.append(action)
    wait.pause()
    assert wait.suspended.is_set() and budget.status()["budget_paused"]
    wait.close()
    assert not wait.suspended.is_set() and not budget.status()["budget_paused"]
    assert actions == ["pause", "unpause"]
    assert messages == ["provider_wait_prepare", "stop_agent", "provider_wait_resume"]
    runner.gpu_allocation = {"assigned": "all"}
    with pytest.raises(ValueError, match="CPU-only"):
        wait.pause()
