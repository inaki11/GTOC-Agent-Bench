"""Pinned CLIProxyAPI against a loopback-only synthetic upstream; no OAuth/model quota."""

import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gtoc_bench import ROOT
from gtoc_bench.antigravity import installed_binary, proxy_config
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config
from gtoc_bench.gateway import Gateway
from gtoc_bench.storage import RunStore

BINARY = os.environ.get("GTOC_TEST_CLIPROXY_BINARY")
pytestmark = [
    pytest.mark.proxy,
    pytest.mark.skipif(not BINARY, reason="Set GTOC_TEST_CLIPROXY_BINARY"),
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("mode", ["no_keepalive", "streaming", "nonstream", "deadline"])
def test_pinned_proxy_keepalive_before_generation_and_bounded_wait(tmp_path, mode):
    assert Path(BINARY).resolve() == installed_binary().resolve()
    stop = threading.Event()
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if stop.wait(60 if mode == "deadline" else 3.2):
                return
            try:
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/event-stream" if body.get("stream") else "application/json",
                )
                self.end_headers()
                response = {
                    "id": "synthetic-latency",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                }
                if body.get("stream"):
                    response.update(
                        object="chat.completion.chunk",
                        choices=[{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}],
                    )
                    self.wfile.write(
                        b"data: " + json.dumps(response).encode() + b"\n\ndata: [DONE]\n\n"
                    )
                else:
                    self.wfile.write(json.dumps(response).encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    cfg = proxy_config(
        SimpleNamespace(directory=tmp_path),
        {"port": port, "proxy_api_key": "synthetic-client-key"},
        keepalive_seconds=0 if mode == "no_keepalive" else 1,
    )
    cfg["api-keys"] = {
        "openai-compatibility": [
            {
                "name": "loopback-only",
                "base-url": f"http://127.0.0.1:{upstream.server_port}/v1",
                "keys": [{"api-key": "synthetic-upstream-key"}],
                "models": [{"name": "latency-synthetic", "alias": "latency-synthetic"}],
            }
        ]
    }
    (tmp_path / "accounts").mkdir()
    path = tmp_path / "proxy.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    process = None
    gateway = None
    try:
        with (tmp_path / "proxy.log").open("wb") as log:
            process = subprocess.Popen(
                [BINARY, "-config", str(path), "-local-model"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                cwd=tmp_path,
                env={"PATH": os.defpath, "HOME": str(tmp_path)},
            )
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(80):
                assert process.poll() is None, (tmp_path / "proxy.log").read_text()
                try:
                    response = client.get(
                        f"http://127.0.0.1:{port}/v1/models",
                        headers={"Authorization": "Bearer synthetic-client-key"},
                    )
                    if response.status_code == 200 and any(
                        m["id"] == "latency-synthetic" for m in response.json()["data"]
                    ):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Pinned proxy did not expose the synthetic model")
        run = tmp_path / "run"
        settings = load_config(ROOT / "configs/antigravity-openai.yaml")
        settings["provider"].update(
            base_url=f"http://127.0.0.1:{port}/v1",
            timeouts={
                "connect_seconds": 1,
                "read_seconds": 2.2,
                "attempt_seconds": 3.5 if mode == "deadline" else 8,
            },
        )
        settings["model"]["id"] = "latency-synthetic"
        settings["recovery"].update(clock_policy="continuous_wall", max_transient_retries=0)
        budget = Budget(15)
        budget.start()
        gateway = Gateway(settings, "synthetic-client-key", RunStore(run), budget)
        emitted = []
        gateway.proxy(
            "/chat/completions",
            {},
            json.dumps(
                {
                    "model": "latency-synthetic",
                    "messages": [{"role": "user", "content": "synthetic"}],
                    "stream": mode != "nonstream",
                }
            ).encode(),
            lambda *args: emitted.append(args),
        )
        row = json.loads(next((run / "requests").glob("*/metadata.json")).read_text())
        assert len(requests) == 1
        if mode in {"no_keepalive", "deadline"}:
            assert budget.reason == "provider_unavailable"
            assert row["stream"]["timeout"]["code"] == (
                "model_read_timeout" if mode == "no_keepalive" else "model_attempt_timeout"
            )
        else:
            assert budget.reason is None, row
            assert row["first_byte_seconds"] < 2.2
            assert row["first_provider_event_seconds"] >= 3
            assert budget.tokens == 12
        if mode in {"streaming", "deadline"}:
            assert row["stream"]["keepalive_frames"] >= 1
    finally:
        if gateway:
            gateway.close()
        stop.set()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
