import json

import httpx
import pytest

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.gateway import Gateway, UsageTracker, locked_request
from gtoc_bench.storage import RunStore, read_events


def make_gateway(tmp_path, handler):
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["recovery"]["max_transient_retries"] = 2
    store = RunStore(tmp_path)
    budget = Budget(60, 1000)
    budget.start()
    gateway = Gateway(cfg, "host-secret", store, budget, transport=httpx.MockTransport(handler))
    gateway.client.close()
    gateway.client = httpx.Client(transport=httpx.MockTransport(handler))
    return gateway


def test_transient_retries_record_every_attempt_and_usage(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer host-secret"
        body = json.loads(request.content)
        assert body["service_tier"] == "default"
        if len(calls) == 1:
            return httpx.Response(
                429, headers={"retry-after": "0"}, json={"error": {"code": "rate_limit_exceeded"}}
            )
        return httpx.Response(
            200, json={"model": body["model"], "usage": {"input_tokens": 10, "output_tokens": 2}}
        )

    gateway = make_gateway(tmp_path, provider)
    emitted = []
    body = {"model": gateway.config["model"]["id"], "stream": False}
    gateway.proxy(
        "/responses",
        {"authorization": "agent-controlled"},
        json.dumps(body).encode(),
        lambda *args: emitted.append(args),
    )
    assert len(calls) == 2
    assert gateway.budget.tokens == 12
    assert gateway.budget.unknown_requests == 1
    assert len(list((tmp_path / "requests").glob("*/metadata.json"))) == 2
    assert "provider_retry" in [e["type"] for e in read_events(tmp_path / "events.jsonl")]
    replies = [
        e for e in read_events(tmp_path / "events.jsonl") if e["type"] == "provider_response"
    ]
    assert [e["http_status"] for e in replies] == [429, 200]
    assert all(e["origin"] == "provider" and e["client_request_id"] for e in replies)
    assert "host-secret" not in (tmp_path / "events.jsonl").read_text()


@pytest.mark.parametrize("code", ["insufficient_quota", "QUOTA_EXHAUSTED", "quota_exceeded"])
def test_quota_is_not_retried_and_secrets_are_redacted(tmp_path, code):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": code, "message": "host-secret"}})

    gateway = make_gateway(tmp_path, provider)
    emitted = []
    gateway.proxy(
        "/responses",
        {},
        json.dumps({"model": gateway.config["model"]["id"]}).encode(),
        lambda *args: emitted.append(args),
    )
    assert len(calls) == 1 and gateway.budget.reason == "provider_unavailable"
    assert b"host-secret" not in b"".join(e[2] for e in emitted)
    assert all("host-secret" not in p.read_text() for p in tmp_path.rglob("*.json"))


def test_partial_stream_is_not_replayed_and_usage_is_uncertain(tmp_path):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"type":"response.created","response":{"id":"r1"}}\n\n'
            raise httpx.ReadError("connection lost")

    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=BrokenStream()
        )

    gateway = make_gateway(tmp_path, provider)
    gateway.proxy(
        "/responses",
        {},
        json.dumps({"model": gateway.config["model"]["id"]}).encode(),
        lambda *_: None,
    )
    assert len(calls) == 1
    assert gateway.budget.unknown_requests == 1
    usage = json.loads(next((tmp_path / "requests").glob("*/usage.json")).read_text())
    assert usage["quality"] == "unknown"
    metadata = json.loads(next((tmp_path / "requests").glob("*/metadata.json")).read_text())
    assert metadata["stream"]["ending"] == "transport_error"
    assert metadata["stream"]["exception_type"] == "ReadError"
    assert metadata["stream"]["last_event"] == "response.created"
    assert metadata["stream"]["recovery"] == "native_codex_retry"
    assert gateway.budget.reason is None


def test_clean_eof_diagnostics_recovery_and_bounded_failures(tmp_path, monkeypatch):
    async def no_wait(_):
        pass

    monkeypatch.setattr("gtoc_bench.gateway.asyncio.sleep", no_wait)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream",
                "x-request-id": "req-upstream",
                "set-cookie": "host-secret",
            },
            content=b'data: {"type":"response.created","sequence_number":7,"response":{"id":"r1","service_tier":"auto"}}\n\n',
        )

    gateway = make_gateway(tmp_path, provider)
    body = json.dumps({"model": gateway.config["model"]["id"], "stream": True}).encode()
    for _ in range(4):
        gateway.proxy("/responses", {}, body, lambda *_: None)
    assert len(calls) == 3 and gateway.budget.reason == "provider_unavailable"
    assert gateway.budget.unknown_requests == 3
    records = [json.loads(p.read_text()) for p in (tmp_path / "requests").glob("*/metadata.json")]
    for record in records:
        assert record["stream"]["ending"] == "eof_before_terminal"
        assert record["stream"]["last_sequence"] == 7
        assert record["stream"]["transport_bytes"] > 0
        assert record["provider_request_id"] == "req-upstream"
        assert "set-cookie" not in record["response_headers"]
        assert "cancellation" not in record
    events = list(read_events(tmp_path / "events.jsonl"))
    assert sum(e["type"] == "model_transport_closed" for e in events) == 3
    assert not any(e["type"] == "model_cancel_requested" for e in events)


def test_success_resets_consecutive_stream_failure_allowance(tmp_path, monkeypatch):
    async def no_wait(_):
        pass

    monkeypatch.setattr("gtoc_bench.gateway.asyncio.sleep", no_wait)
    calls = []

    def provider(request):
        calls.append(request)
        completed = len(calls) % 2 == 0
        event = {
            "type": "response.completed" if completed else "response.created",
            "response": {"id": "resp_test", "service_tier": "default"},
        }
        if completed:
            event["response"]["usage"] = {"input_tokens": 5, "output_tokens": 1}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=("data: " + json.dumps(event) + "\n\n").encode(),
        )

    gateway = make_gateway(tmp_path, provider)
    body = json.dumps({"model": gateway.config["model"]["id"], "stream": True}).encode()
    for _ in range(6):
        gateway.proxy("/responses", {}, body, lambda *_: None)
    assert gateway.budget.reason is None and gateway.budget.tokens == 18
    assert gateway.budget.unknown_requests == 3 and gateway.stream_failures == 0
    assert (
        sum(e["type"] == "model_stream_recovered" for e in read_events(tmp_path / "events.jsonl"))
        == 3
    )


def test_native_client_cannot_multiply_exhausted_gateway_retries(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            429, headers={"retry-after": "0"}, json={"error": {"code": "rate_limit_exceeded"}}
        )

    gateway = make_gateway(tmp_path, provider)
    body = json.dumps({"model": gateway.config["model"]["id"]}).encode()
    gateway.proxy("/responses", {}, body, lambda *_: None)
    assert len(calls) == 3 and gateway.budget.reason == "provider_unavailable"
    gateway.proxy("/responses", {}, body, lambda *_: None)
    assert len(calls) == 3


def test_transport_retry_window_stops_run(tmp_path):
    calls = []

    def provider(request):
        calls.append(request)
        raise httpx.ConnectError("Temporary connection failure")

    gateway = make_gateway(tmp_path, provider)
    gateway.config["recovery"]["max_retry_wait_seconds"] = 0.001
    body = json.dumps({"model": gateway.config["model"]["id"]}).encode()
    gateway.proxy("/responses", {}, body, lambda *_: None)
    gateway.proxy("/responses", {}, body, lambda *_: None)
    assert len(calls) == 1
    assert gateway.budget.reason == "provider_unavailable"


def test_chat_completions_locks_conditions_and_counts_cache_once(tmp_path):
    def provider(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer host-secret"
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "high" and body["service_tier"] == "default"
        assert body["max_completion_tokens"] == 50 and "max_tokens" not in body
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 80},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            },
        )

    gateway = make_gateway(tmp_path, provider)
    gateway.config = load_config(ROOT / "configs/mini-swe-agent.yaml")
    body = {
        "model": gateway.config["model"]["id"],
        "max_tokens": 50,
        "reasoning_effort": "low",
        "service_tier": "priority",
    }
    gateway.proxy("/chat/completions", {}, json.dumps(body).encode(), lambda *_: None)
    assert gateway.budget.tokens == 110 and gateway.budget.unknown_requests == 0
    for extra in ({"n": 2}, {"web_search_options": {"search_context_size": "high"}}):
        with pytest.raises(ValueError):
            locked_request(body | extra, gateway.config)


def test_chat_stream_usage_survives_fragmentation():
    tracker = UsageTracker("chat_completions")
    events = [
        {"id": "c1", "model": "test", "choices": [{"index": 0, "delta": {"content": "hi"}}]},
        {"id": "c1", "choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": 4}},
    ]
    stream = (
        b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events) + b"data: [DONE]\n\n"
    )
    for i in range(0, len(stream), 11):
        tracker.feed(stream[i : i + 11])
    assert tracker.usage()["input_tokens"] == 30
    assert tracker.usage()["output_tokens"] == 4
    assert tracker.usage()["quality"] == "reported"


def test_requested_stream_without_content_type_is_counted(tmp_path):
    event = {
        "type": "response.completed",
        "response": {
            "model": "test",
            "service_tier": "default",
            "usage": {"input_tokens": 100, "output_tokens": 7},
        },
    }

    def provider(request):
        return httpx.Response(200, content=b"data: " + json.dumps(event).encode() + b"\n\n")

    gateway = make_gateway(tmp_path, provider)
    emitted = []
    gateway.proxy(
        "/responses",
        {},
        json.dumps({"model": gateway.config["model"]["id"], "stream": True}).encode(),
        lambda *args: emitted.append(args),
    )
    assert gateway.budget.tokens == 107 and gateway.budget.unknown_requests == 0
    assert emitted[0][1]["Content-Type"] == "text/event-stream"
    metadata = json.loads(next((tmp_path / "requests").glob("*/metadata.json")).read_text())
    assert metadata["response_content_type"] is None
