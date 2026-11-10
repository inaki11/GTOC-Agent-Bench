"""Real sockets that remain silent until the client disconnects; never use paid APIs."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.gateway import Gateway
from gtoc_bench.storage import RunStore, read_events


def test_stop_diagnostic_write_failure_does_not_prevent_network_cleanup(tmp_path, monkeypatch):
    cfg = load_config(ROOT / "configs/codex.yaml")
    store, budget = RunStore(tmp_path), Budget(30)
    gateway = Gateway(cfg, "synthetic-only", store, budget)
    budget.stop("time_limit")
    write = store.write

    def full_disk(path, data):
        if path == "gateway-stop.json":
            raise OSError("simulated full disk")
        return write(path, data)

    monkeypatch.setattr(store, "write", full_disk)
    with pytest.raises(OSError, match="full disk"):
        gateway.close()
    assert gateway.client.is_closed
    assert gateway.inference.is_closed
    assert gateway.loop.is_closed()


class StalledProvider:
    def __init__(self, phase):
        self.arrived = threading.Event()
        self.disconnected = threading.Event()
        self.stop = threading.Event()
        self.calls = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "11")
                self.end_headers()
                self.wfile.write(b'{"data":[]}')

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                provider.calls += 1
                if phase != "headers":
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        "application/json" if phase == "json" else "text/event-stream",
                    )
                    self.send_header("Connection", "close")
                    self.send_header("x-request-id", "synthetic-stalled-request")
                    self.end_headers()
                    if phase == "partial_usage":
                        self.wfile.write(
                            b'data: {"type":"response.created","response":{"usage":{"input_tokens":10,"output_tokens":2}}}\n\n'
                        )
                    elif phase == "fragment":
                        self.wfile.write(
                            b'data: {"type":"response.output_text.delta","delta":"unfinished'
                        )
                    elif phase == "json":
                        self.wfile.write(b'{"model":"unfinished')
                    self.wfile.flush()
                provider.arrived.set()
                self.connection.settimeout(0.1)
                try:
                    while not provider.stop.is_set():
                        try:
                            if self.connection.recv(1) == b"":
                                provider.disconnected.set()
                                return
                        except TimeoutError:
                            continue
                        except ConnectionResetError:
                            provider.disconnected.set()
                            return
                finally:
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.mark.parametrize("phase", ["headers", "stream", "fragment", "json", "partial_usage"])
@pytest.mark.parametrize("reason", ["time_limit", "token_limit", "operator_cancelled"])
def test_budget_actively_closes_silent_connection(tmp_path, phase, reason):
    provider = StalledProvider(phase)
    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["provider"]["base_url"] = provider.url
    budget, store = Budget(60, 100), RunStore(tmp_path)
    budget.start()
    store.started = budget.started
    gateway = Gateway(cfg, "synthetic-cancellation-secret", store, budget)
    body = json.dumps({"model": cfg["model"]["id"], "stream": phase != "json"}).encode()
    failures = []

    def proxy():
        try:
            gateway.proxy("/responses", {}, body, lambda *_: None)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=proxy, daemon=True)
    worker.start()
    try:
        assert provider.arrived.wait(3)
        # Let any initial usage event arrive; the provider will never send completion.
        if phase == "partial_usage":
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                paths = list((tmp_path / "requests").glob("*/response.jsonl.gz"))
                if paths and paths[0].stat().st_size:
                    break
                time.sleep(0.01)
        cutoff = time.monotonic()
        if reason == "time_limit":
            budget.wall_seconds = time.monotonic() - budget.started + 0.1
        elif reason == "token_limit":
            budget.add_usage({"input_tokens": 100, "output_tokens": 0, "quality": "reported"})
        else:
            budget.stop(reason)
        # Observed by the server, not just a cancelled Future or a stopped agent.
        assert provider.disconnected.wait(2), "Upstream socket stayed open after cutoff"
        worker.join(timeout=2)
        assert not worker.is_alive() and not failures
        assert time.monotonic() - cutoff < 2
        assert budget.reason == reason
        gateway.proxy("/responses", {}, body, lambda *_: None)
        assert provider.calls == 1
        metadata = json.loads(next((tmp_path / "requests").glob("*/metadata.json")).read_text())
        usage = json.loads(next((tmp_path / "requests").glob("*/usage.json")).read_text())
        assert metadata["cancelled"] is True
        assert metadata["cancellation"]["reason"] == reason
        assert metadata["cancellation"]["upstream_cancellation_confirmed"] is False
        assert 0 <= metadata["cancellation"]["close_latency_seconds"] < 1
        assert metadata["response_trace"]["complete"] is False
        assert usage["quality"] == ("partial" if phase == "partial_usage" else "unknown")
        assert usage["input_tokens"] == (10 if phase == "partial_usage" else 0)
        assert budget.unknown_requests == 1
        kinds = [event["type"] for event in read_events(tmp_path / "events.jsonl")]
        assert kinds.index("model_cancel_requested") < kinds.index("model_transport_closed")
        assert kinds.index("model_transport_closed") < kinds.index("model_request_finished")
        assert "synthetic-cancellation-secret" not in "".join(
            p.read_text() for p in tmp_path.rglob("*.json")
        )
    finally:
        gateway.cancel()
        worker.join(timeout=3)
        gateway.close()
        provider.close()


def test_cancellation_interrupts_retry_backoff(tmp_path):
    arrived = threading.Event()
    calls = []

    async def provider(request):
        calls.append(request)
        arrived.set()
        return httpx.Response(503, headers={"Retry-After": "30"}, json={"error": "busy"})

    cfg = load_config(ROOT / "configs/codex.yaml")
    cfg["recovery"].update(max_transient_retries=2, max_retry_wait_seconds=90)
    budget, store = Budget(60), RunStore(tmp_path)
    budget.start()
    gateway = Gateway(cfg, "synthetic-key", store, budget, transport=httpx.MockTransport(provider))
    worker = threading.Thread(
        target=lambda: gateway.proxy(
            "/responses", {}, json.dumps({"model": cfg["model"]["id"]}).encode(), lambda *_: None
        )
    )
    worker.start()
    try:
        assert arrived.wait(2)
        budget.stop("operator_cancelled")
        worker.join(timeout=2)
        assert not worker.is_alive() and len(calls) == 1
        assert budget.reason == "operator_cancelled"
    finally:
        gateway.close()


def test_cancellation_while_connecting(tmp_path):
    entered, closed = threading.Event(), threading.Event()

    async def connect(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    cfg = load_config(ROOT / "configs/codex.yaml")
    budget = Budget(60)
    budget.start()
    gateway = Gateway(
        cfg, "synthetic-key", RunStore(tmp_path), budget, transport=httpx.MockTransport(connect)
    )
    worker = threading.Thread(
        target=lambda: gateway.proxy(
            "/responses", {}, json.dumps({"model": cfg["model"]["id"]}).encode(), lambda *_: None
        )
    )
    worker.start()
    try:
        assert entered.wait(2)
        budget.stop("time_limit")
        assert closed.wait(2)
        worker.join(timeout=2)
        assert not worker.is_alive()
    finally:
        gateway.close()
