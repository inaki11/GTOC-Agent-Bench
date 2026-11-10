"""Real HTTP silence/heartbeats, attempt deadlines and authoritative run cutoffs."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.gateway import Gateway
from gtoc_bench.storage import RunStore, read_events


class SlowProvider:
    def __init__(self, mode, delay=0.2):
        self.stop = threading.Event()
        self.arrived = threading.Event()
        self.disconnected = threading.Event()
        self.calls = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                provider.calls += 1
                provider.arrived.set()
                try:
                    if mode == "headers" and provider.stop.wait(delay):
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    if mode == "heartbeats":
                        while not provider.stop.wait(0.02):
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                        return
                    if mode == "body":
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        if provider.stop.wait(delay):
                            return
                    event = {
                        "type": "response.completed",
                        "response": {
                            "id": "synthetic-slow",
                            "model": body["model"],
                            "service_tier": "default",
                            "output": [],
                            "usage": {"input_tokens": 10, "output_tokens": 2},
                        },
                    }
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    provider.disconnected.set()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


def setup(tmp_path, provider, *, read=0.5, attempt=1, wall=5, token_limit=None):
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["provider"].update(
        base_url=provider.url,
        timeouts={
            "connect_seconds": 1,
            "read_seconds": read,
            "attempt_seconds": attempt,
        },
    )
    cfg["recovery"].update(clock_policy="continuous_wall", max_transient_retries=0)
    budget = Budget(wall, token_limit)
    budget.start()
    store = RunStore(tmp_path)
    store.clock = budget.timeline_elapsed
    return Gateway(cfg, "synthetic-only", store, budget)


def request(gateway):
    replies = []
    gateway.proxy(
        "/responses",
        {},
        json.dumps(
            {
                "model": gateway.config["model"]["id"],
                "input": "synthetic",
                "stream": True,
            }
        ).encode(),
        lambda *args: replies.append(args),
    )
    return replies


def metadata(path):
    return json.loads(next((path / "requests").glob("*/metadata.json")).read_text())


@pytest.mark.parametrize("mode,phase", [("headers", "response_headers"), ("body", "response_body")])
def test_idle_timeout_identifies_silence_before_headers_or_after_data(tmp_path, mode, phase):
    provider = SlowProvider(mode)
    gateway = setup(tmp_path, provider, read=0.06)
    try:
        request(gateway)
        row = metadata(tmp_path)
        assert provider.calls == 1 and gateway.budget.reason == "provider_unavailable"
        assert row["stream"]["timeout"]["code"] == "model_read_timeout"
        assert row["stream"]["timeout"]["phase"] == phase
        assert row["stream"]["timeout"]["origin"] == "benchmark_gateway"
        assert row["timeouts"]["read_seconds"] == 0.06
        events = list(read_events(tmp_path / "events.jsonl"))
        assert any(e["type"] == "model_attempt_timeout" for e in events)
    finally:
        gateway.close()
        provider.close()


def test_longer_configured_generation_wait_accepts_a_legitimately_slow_response(tmp_path):
    provider = SlowProvider("headers", delay=0.2)
    gateway = setup(tmp_path, provider, read=0.5)
    try:
        request(gateway)
        row = metadata(tmp_path)
        assert provider.calls == 1 and gateway.budget.reason is None
        assert gateway.budget.tokens == 12
        assert row["headers_seconds"] >= 0.18
        assert row["first_provider_event_seconds"] >= 0.18
        assert "timeout" not in row["stream"]
    finally:
        gateway.close()
        provider.close()


@pytest.mark.parametrize(
    "mode,phase", [("headers", "response_headers"), ("heartbeats", "response_body")]
)
def test_total_attempt_deadline_bounds_silent_and_heartbeat_only_requests(tmp_path, mode, phase):
    provider = SlowProvider(mode, delay=2)
    gateway = setup(tmp_path, provider, read=1, attempt=0.18)
    began = time.monotonic()
    try:
        request(gateway)
        assert time.monotonic() - began < 0.8
        row = metadata(tmp_path)
        assert row["stream"]["ending"] == "attempt_timeout"
        assert row["stream"]["timeout"]["code"] == "model_attempt_timeout"
        assert row["stream"]["timeout"]["phase"] == phase
        assert row["first_provider_event_seconds"] is None
        assert gateway.budget.tokens == 0 and gateway.budget.unknown_requests == 1
        if mode == "heartbeats":
            assert row["stream"]["keepalive_frames"] >= 2
            assert provider.disconnected.wait(1), "Attempt cap must close the HTTP connection"
    finally:
        gateway.close()
        provider.close()


@pytest.mark.parametrize("limit", ["time", "tokens"])
def test_run_cutoffs_cancel_before_large_generation_timeouts(tmp_path, limit):
    provider = SlowProvider("heartbeats")
    gateway = setup(
        tmp_path,
        provider,
        read=30,
        attempt=60,
        wall=30,
        token_limit=10 if limit == "tokens" else None,
    )
    worker = threading.Thread(target=request, args=(gateway,))
    try:
        worker.start()
        assert provider.arrived.wait(3)
        if limit == "time":
            # Test cancellation of an admitted request, rather than requiring
            # imports, fsync and scheduling on every host to finish in 180 ms.
            with gateway.budget.lock:
                gateway.budget.wall_seconds = gateway.budget.timeline_elapsed() + 0.18
        if limit == "tokens":
            gateway.budget.add_usage({"input_tokens": 10, "quality": "reported"})
        worker.join(1)
        assert not worker.is_alive()
        assert gateway.budget.reason == ("time_limit" if limit == "time" else "token_limit")
        row = metadata(tmp_path)
        assert row["stream"]["ending"] == "controller_cancelled"
        assert row["cancellation"]["reason"] == gateway.budget.reason
        assert row["cancellation"]["close_latency_seconds"] < 0.5
    finally:
        gateway.cancel()
        worker.join(2)
        gateway.close()
        provider.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("read_seconds", 0),
        ("attempt_seconds", None),
        ("connect_seconds", True),
        ("read_seconds", float("nan")),
        ("attempt_seconds", float("inf")),
        ("unknown", 10),
    ],
)
def test_invalid_or_unbounded_timeout_configuration_is_rejected(tmp_path, field, value):
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["provider"]["timeouts"][field] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError):
        load_config(path)


def test_defaults_and_antigravity_keepalive_policy_are_explicit():
    cfg = load_config(ROOT / "configs/antigravity.yaml")
    assert cfg["provider"]["timeouts"] == {
        "connect_seconds": 10,
        "read_seconds": 300,
        "attempt_seconds": 1800,
    }
    assert cfg["provider"]["keepalive_seconds"] == 15
