import json
import os
import signal
import subprocess
import sys
import time

import pytest
import yaml

from gtoc_bench import ROOT
from gtoc_bench.budget import Budget
from gtoc_bench.claude_native import NativeUsage, profile_environment
from gtoc_bench.config import load_config, public_config
from gtoc_bench.container_tools import arguments
from gtoc_bench.storage import RunStore, read_events


def stream(kind, **data):
    return {"type": "stream_event", "event": {"type": kind, **data}}


def native_usage(tmp_path):
    config = load_config(ROOT / "configs/claude-subscription.yaml")
    budget = Budget(60)
    budget.start()
    return NativeUsage(config, RunStore(tmp_path), budget)


def test_native_blocks_remain_provisional_until_the_response_usage_is_final(tmp_path):
    usage = native_usage(tmp_path)
    model = usage.config["model"]["id"]
    raw = {"input_tokens": 10, "cache_read_input_tokens": 20, "output_tokens": 1}
    usage.observe(stream("message_start", message={"id": "m1", "model": model, "usage": raw}))
    usage.observe({"type": "assistant", "message": {"id": "m1", "model": model, "usage": raw}})
    assert usage.budget.tokens == 31 and usage.pending_responses == 1
    assert usage.budget.unknown_requests == 0  # Pending counters have their own metric.
    assert (
        json.loads(next(tmp_path.glob("requests/*/usage.json")).read_text())["quality"] == "partial"
    )
    usage.observe(
        stream("message_delta", delta={"stop_reason": "tool_use"}, usage={"output_tokens": 351744})
    )
    assert usage.budget.tokens == 351774 and usage.pending_responses == 1
    usage.observe(stream("message_stop"))
    assert usage.pending_responses == 0
    final = json.loads(next(tmp_path.glob("requests/*/usage.json")).read_text())
    assert final["quality"] == "reported" and final["output_tokens"] == 351744
    # An old block or replayed event cannot roll back or duplicate the count.
    usage.observe({"type": "assistant", "message": {"id": "m1", "model": model, "usage": raw}})
    usage.observe(stream("message_stop"))
    assert usage.budget.tokens == 351774
    usage.observe(
        {
            "type": "result",
            "modelUsage": {
                model: {"inputTokens": 10, "cacheReadInputTokens": 20, "outputTokens": 351744}
            },
        }
    )
    assert usage.status()["unattributed_output_tokens"] == 0
    assert usage.status()["timeline_quality"] == "reported"
    assert usage.status()["total_quality"] == "reported"
    usage.store.integrity()
    assert "native-usage.json" in json.loads((tmp_path / "integrity.json").read_text())["files"]


@pytest.mark.parametrize("terminal", [False, True])
def test_message_stop_without_final_usage_never_certifies_the_placeholder(tmp_path, terminal):
    usage = native_usage(tmp_path)
    model = usage.config["model"]["id"]
    for index in range(3):
        usage.observe(
            stream(
                "message_start",
                message={
                    "id": f"m{index}",
                    "model": model,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                },
            )
        )
        if terminal:
            usage.observe(stream("message_stop"))
    assert usage.pending_responses == 3 and usage.status()["reported_responses"] == 0
    assert all(
        json.loads(p.read_text())["quality"] == "partial"
        for p in tmp_path.glob("requests/*/usage.json")
    )


def test_final_session_correction_keeps_totals_but_does_not_invent_response_attribution(tmp_path):
    usage = native_usage(tmp_path)
    model = usage.config["model"]["id"]
    usage.observe(
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": model,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        }
    )
    usage.observe(
        {"type": "result", "modelUsage": {model: {"inputTokens": 10, "outputTokens": 4000}}}
    )
    assert usage.budget.tokens == 4010
    assert usage.status()["pending_responses"] == 1
    assert usage.status()["unattributed_output_tokens"] == 3999
    assert usage.status()["total_quality"] == "reported"
    assert usage.status()["timeline_quality"] == "partial"
    corrections = [
        e["usage"]
        for e in read_events(tmp_path / "events.jsonl")
        if e["usage"]["scope"] == "session_reconciliation"
    ]
    assert corrections[0]["temporal_quality"] == "unattributed"
    usage.observe(
        {"type": "result", "modelUsage": {model: {"inputTokens": 10, "outputTokens": 4000}}}
    )
    assert usage.budget.tokens == 4010


def test_lower_final_session_counter_is_diagnosed_instead_of_losing_usage(tmp_path):
    usage = native_usage(tmp_path)
    model = usage.config["model"]["id"]
    usage.observe(
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": model,
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        }
    )
    usage.observe({"type": "result", "modelUsage": {model: {"inputTokens": 10, "outputTokens": 2}}})
    assert usage.budget.tokens == 30 and usage.status()["counter_regressions"] == 1
    assert usage.status()["timeline_quality"] == "partial"


def test_native_reader_retains_usage_events_without_duplicate_text_deltas(tmp_path):
    import base64
    from types import SimpleNamespace

    from gtoc_bench.audit import AgentTrace
    from gtoc_bench.claude_native import NativeClaude

    usage = native_usage(tmp_path)
    model = usage.config["model"]["id"]
    events = [
        stream(
            "message_start",
            message={"id": "m1", "model": model, "usage": {"input_tokens": 10, "output_tokens": 1}},
        ),
        stream("content_block_delta", delta={"type": "text_delta", "text": "repeated" * 20000}),
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": model,
                "usage": {"input_tokens": 10, "output_tokens": 1},
                "content": [{"type": "text", "text": "complete block"}],
            },
        },
        stream("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 150}),
        stream("message_stop"),
        {"type": "result", "modelUsage": {model: {"inputTokens": 10, "outputTokens": 150}}},
    ]
    frames = [
        json.dumps(
            {
                "type": "output",
                "channel": "stdout",
                "body": base64.b64encode((json.dumps(e) + "\n").encode()).decode(),
            }
        )
        for e in events
    ]
    frames.append(json.dumps({"type": "agent_exit", "returncode": 0}))
    client = NativeClaude.__new__(NativeClaude)
    client.store, client.budget, client.usage = usage.store, usage.budget, usage
    client.trace = AgentTrace(usage.store, usage.budget, "claude_code")
    client.process = SimpleNamespace(stdout=frames)
    client.read(tmp_path)
    client.trace.close()
    text = (tmp_path / "agent/stdout.log").read_text()
    assert "complete block" in text and "message_delta" in text
    assert "content_block_delta" not in text and len(text) < 5000
    assert usage.budget.tokens == 160 and usage.budget.unknown_requests == 0


def test_subscription_config_rejects_other_agents_and_provider_overrides(tmp_path):
    cfg = load_config(ROOT / "configs/claude-subscription.yaml")
    assert "auth_dir" not in public_config(cfg)["provider"]
    path = tmp_path / "config.yaml"
    for change in (
        {"base_url": "http://localhost:9999"},
        {"env_file": "/tmp/key.env"},
        {"api_key_variable": "KEY"},
    ):
        path.write_text(yaml.safe_dump(cfg | {"provider": cfg["provider"] | change}))
        with pytest.raises(ValueError, match="cannot use keys"):
            load_config(path)
    for name in ("openhands", "swe_agent", "mini_swe_agent"):
        other = cfg | {"agent": {"id": name}}
        path.write_text(yaml.safe_dump(other))
        with pytest.raises(ValueError, match="only by official Claude Code"):
            load_config(path)


def test_native_environment_does_not_inherit_provider_secrets(tmp_path, monkeypatch):
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.setenv(key, "synthetic-secret")
    _, env = profile_environment(tmp_path / "private", "experiment")
    assert "synthetic-secret" not in str(env)
    assert env["CLAUDE_CONFIG_DIR"].endswith("/private/experiment")
    assert (tmp_path / "private/experiment").stat().st_mode & 0o077 == 0


def test_parallel_official_clients_share_login_but_block_account_changes(tmp_path, monkeypatch):
    from gtoc_bench import claude_native
    from gtoc_bench.auth import AuthError

    calls = []
    monkeypatch.setattr(claude_native, "verify_binary", lambda: {})
    monkeypatch.setattr(
        claude_native.subprocess, "call", lambda command, **kwargs: calls.append(command) or 0
    )
    directory = tmp_path / "private"
    clients = []
    try:
        for index, model in enumerate(("claude-fable-5-1", "claude-sonnet-5-5", "claude-opus-5-5")):
            config = load_config(ROOT / "configs/claude-subscription.yaml")
            config["provider"].update(auth_dir=str(directory), profile="parallel")
            config["model"].update(id=model, reasoning="max")
            run = tmp_path / f"run-{index}"
            run.mkdir()
            clients.append(
                claude_native.NativeClaude(config, RunStore(run), Budget(60), f"container-{index}")
            )
        # A native run never reads/imports a token file or gives it to a container.
        assert not list(directory.rglob("*.json"))
        for action in ("login", "logout"):
            with pytest.raises(AuthError, match="busy"):
                claude_native.auth_command(action, directory, "parallel")
        assert not calls
        clients.pop().close()
        with pytest.raises(AuthError, match="busy"):
            claude_native.auth_command("logout", directory, "parallel")
    finally:
        for client in clients:
            client.close()
    assert claude_native.auth_command("logout", directory, "parallel") == 0
    assert calls[-1][-2:] == ["auth", "logout"]


@pytest.mark.parametrize("custom_config", [None, "existing-official-profile"])
def test_current_claude_session_uses_official_login_without_importing_credentials(
    tmp_path, monkeypatch, custom_config
):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    if custom_config:
        directory = tmp_path / custom_config
        directory.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(directory))
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ):
        monkeypatch.setenv(name, "synthetic-secret")
    base, env = profile_environment(tmp_path / "bench-auth", "current")
    assert not (base.directory / "current").exists()
    assert env.get("CLAUDE_CONFIG_DIR") == (str(directory) if custom_config else None)
    assert "synthetic-secret" not in str(env)
    assert not list(base.directory.iterdir())
    # A host-only benchmark lock is still available without changing the login.
    with base.lock(timeout=0):
        assert (base.directory / "current.lock").exists()


def test_native_message_usage_deduplicates_and_reconciles_final_totals(tmp_path):
    cfg = load_config(ROOT / "configs/claude-subscription.yaml")
    budget = Budget(60, 1000)
    budget.start()
    usage = NativeUsage(cfg, RunStore(tmp_path), budget)
    event = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "model": cfg["model"]["id"],
            "usage": {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 20},
        },
    }
    usage.observe(event)
    usage.observe(event)
    assert budget.tokens == 33
    usage.observe(
        {
            "type": "result",
            "modelUsage": {
                cfg["model"]["id"]: {
                    "inputTokens": 15,
                    "outputTokens": 7,
                    "cacheReadInputTokens": 20,
                }
            },
        }
    )
    assert budget.tokens == 42 and budget.unknown_requests == 0
    assert usage.finished
    assert (
        sum(
            e["usage"]["input_tokens"] + e["usage"]["output_tokens"]
            for e in read_events(tmp_path / "events.jsonl")
        )
        == 42
    )
    for path in (tmp_path / "requests").glob("*/metadata.json"):
        assert json.loads(path.read_text())["authentication"]["actual_cost"] is None
    files = [json.loads(p.read_text()) for p in (tmp_path / "requests").glob("*/usage.json")]
    assert sum(u["input_tokens"] + u["output_tokens"] for u in files) == budget.tokens


def test_native_retry_wait_is_bounded_without_another_cli_event(tmp_path, monkeypatch):
    cfg = load_config(ROOT / "configs/claude-subscription.yaml")
    cfg["recovery"]["max_retry_wait_seconds"] = 2
    now = [100.0]
    monkeypatch.setattr("gtoc_bench.claude_native.time.monotonic", lambda: now[0])
    budget = Budget(60)
    budget.start()
    usage = NativeUsage(cfg, RunStore(tmp_path), budget)
    usage.observe({"type": "system", "subtype": "api_retry", "attempt": 1})
    now[0] += 3
    usage.check_retry_wait()
    assert budget.reason == "provider_unavailable"


@pytest.mark.parametrize("kind", ["assistant", "retry", "result"])
def test_expired_official_login_is_not_a_model_policy_violation(tmp_path, kind):
    config = load_config(ROOT / "configs/claude-subscription.yaml")
    budget = Budget(60)
    budget.start()
    usage = NativeUsage(config, RunStore(tmp_path), budget)
    if kind == "assistant":
        event = {
            "type": "assistant",
            "error": "authentication_failed",
            "is_api_error_message": True,
            "message": {
                "id": "local-error",
                "model": "<synthetic>",
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }
    elif kind == "retry":
        event = {
            "type": "system",
            "subtype": "api_retry",
            "error_status": 401,
            "error": "authentication_failed",
            "attempt": 1,
        }
    else:
        event = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "terminal_reason": "api_error",
            "api_error_status": 401,
            "modelUsage": {},
        }
    usage.observe(event)
    assert budget.reason == "configuration_error"
    assert budget.tokens == 0 and not list((tmp_path / "requests").glob("*/usage.json"))
    assert any(e["type"] == "native_provider_error" for e in read_events(tmp_path / "events.jsonl"))


def test_native_error_preserves_partial_real_model_usage(tmp_path):
    config = load_config(ROOT / "configs/claude-subscription.yaml")
    budget = Budget(60)
    budget.start()
    usage = NativeUsage(config, RunStore(tmp_path), budget)
    usage.observe(
        {
            "type": "assistant",
            "error": "overloaded_error",
            "message": {
                "id": "partial-model-response",
                "model": config["model"]["id"],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        }
    )
    assert budget.reason == "provider_unavailable" and budget.tokens == 12
    assert budget.unknown_requests == 1
    assert (
        json.loads(next((tmp_path / "requests").glob("*/usage.json")).read_text())["quality"]
        == "partial"
    )


@pytest.mark.parametrize("model,tier", [("another-model", None), ("claude-sonnet-4-6", "priority")])
def test_native_usage_detects_model_and_speed_changes(tmp_path, model, tier):
    cfg = load_config(ROOT / "configs/claude-subscription.yaml")
    budget = Budget(60)
    budget.start()
    usage = NativeUsage(cfg, RunStore(tmp_path), budget)
    usage.record("m1", model, {"input_tokens": 1, "output_tokens": 1, "service_tier": tier})
    assert budget.reason == "policy_violation"


@pytest.mark.parametrize(
    "value",
    [
        {"command": "pwd", "container": "host"},
        {"command": "pwd", "timeout_seconds": True},
        {"command": "pwd", "timeout_seconds": 901},
        {"command": ["sh"]},
    ],
)
def test_mcp_arguments_cannot_select_host_or_extend_execution(value):
    with pytest.raises(ValueError):
        arguments(value)


def test_native_guard_stops_client_on_controller_loss(tmp_path):
    pid_file = tmp_path / "pid"
    command = [
        sys.executable,
        "-c",
        "import os,time,pathlib; pathlib.Path("
        + repr(str(pid_file))
        + ").write_text(str(os.getpid())); time.sleep(60)",
    ]
    process = subprocess.Popen(
        [sys.executable, "-m", "gtoc_bench.native_guard"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(
            (
                json.dumps(
                    {
                        "command": command,
                        "env": dict(os.environ),
                        "cwd": str(tmp_path),
                        "prompt": "test",
                    }
                )
                + "\n"
            ).encode()
        )
        process.stdin.flush()
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        pid = int(pid_file.read_text())
        process.stdin.close()
        process.wait(timeout=8)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=8)
