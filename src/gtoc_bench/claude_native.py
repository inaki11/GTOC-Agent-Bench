"""Official Claude Code subscription runner. This module never reads OAuth credentials."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import ROOT
from .audit import AgentTrace
from .auth import Profiles, auth_path, checked_profile
from .config import pins
from .gateway import normalize_usage
from .storage import utc_now

DEFAULT_CLAUDE_DIR = "~/.config/gtoc-bench/claude-code"


def binary_path():
    version = pins()["claude_code"]["version"]
    return (
        Path.home() / ".local/share/gtoc-bench/claude-code" / version / "node_modules/.bin/claude"
    )


def install():
    directory = binary_path().parents[2]
    auth_path(directory).mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(ROOT / "environments/claude-host" / name, directory / name)
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=directory, check=True)
    verify_binary()
    return binary_path()


def verify_binary():
    binary = binary_path()
    if not binary.exists():
        raise ValueError("Install the pinned official client: gtoc install-agent claude_code")
    version = subprocess.check_output([str(binary), "--version"], text=True, timeout=20).strip()
    if pins()["claude_code"]["version"] not in version.split():
        raise ValueError("Native Claude Code version does not match the benchmark pin")
    return {"version": version, "sha256": hashlib.sha256(binary.resolve().read_bytes()).hexdigest()}


def profile_environment(directory=DEFAULT_CLAUDE_DIR, profile="default"):
    base = Profiles(directory, checked_profile(profile))
    base.prepare()
    location = None
    if profile != "current":
        location = auth_path(base.directory / profile)
        Profiles(location).prepare()
    env = {
        k: os.environ[k]
        for k in ("HOME", "PATH", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR")
        if k in os.environ
    }
    env.update(
        {
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_UPDATES": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "ENABLE_TOOL_SEARCH": "false",
        }
    )
    if location is not None:
        env["CLAUDE_CONFIG_DIR"] = str(location)
    elif os.environ.get("CLAUDE_CONFIG_DIR"):
        # Let the official client use its existing login/keychain. The bench
        # neither reads nor imports that session's credential files.
        env["CLAUDE_CONFIG_DIR"] = str(auth_path(os.environ["CLAUDE_CONFIG_DIR"]))
    return base, env


def auth_command(action, directory=DEFAULT_CLAUDE_DIR, profile="default"):
    verify_binary()
    base, env = profile_environment(directory, profile)
    with base.lock(timeout=0):
        command = [str(binary_path()), "auth", action]
        if action == "login":
            command += ["--claudeai"]
        elif action == "status":
            command += ["--json"]
        return subprocess.call(
            command, env=env, cwd=env.get("CLAUDE_CONFIG_DIR", str(Path.home())), umask=0o077
        )


def subscription_status(env):
    process = subprocess.run(
        [str(binary_path()), "auth", "status", "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        status = json.loads(process.stdout)
    except ValueError:
        raise ValueError("Cannot read official Claude authentication status") from None
    if process.returncode or not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
        raise ValueError("A Claude subscription login is required: gtoc auth login claude")
    return status


class NativeUsage:
    """Deduplicate message usage and reconcile the official CLI's final cumulative totals."""

    def __init__(self, config, store, budget):
        self.config, self.store, self.budget = config, store, budget
        self.messages, self.totals, self.streams = {}, {}, {}
        self.reported = set()
        self.session_totals_received = False
        self.unattributed = {"input_tokens": 0, "output_tokens": 0}
        self.counter_regressions = 0
        self.retry_started = None
        self.finished = False

    @property
    def pending_responses(self):
        return sum(
            not key.startswith("final:") and key not in self.reported
            for key in tuple(self.messages)
        )

    def status(self):
        return {
            "schema_version": 2,
            "message_count": sum(not key.startswith("final:") for key in tuple(self.messages)),
            "reported_responses": len(self.reported),
            "pending_responses": self.pending_responses,
            "session_totals_received": self.session_totals_received,
            "unattributed_input_tokens": self.unattributed["input_tokens"],
            "unattributed_output_tokens": self.unattributed["output_tokens"],
            "counter_regressions": self.counter_regressions,
            "totals_by_model": {
                model: counts.copy() for model, counts in tuple(self.totals.items())
            },
            "total_quality": "reported"
            if (self.session_totals_received or self.messages and not self.pending_responses)
            and not self.budget.unknown_requests
            and not self.counter_regressions
            else "partial",
            "timeline_quality": "partial"
            if self.pending_responses or any(self.unattributed.values()) or self.counter_regressions
            else "reported",
        }

    def record(
        self, key, model, raw, cumulative=False, complete=False, observation="assistant_block"
    ):
        # Acceptance reads the token count and pending-response count under this
        # same lock, so a candidate cannot race a half-persisted usage update.
        with self.store.lock:
            self._record(key, model, raw, cumulative, complete, observation)

    def _record(self, key, model, raw, cumulative, complete, observation):
        old = self.totals.get(model, {}) if cumulative else self.messages.get(key, {})
        if not cumulative:
            # Assistant blocks can arrive with the message_start placeholder after
            # a larger message_delta. Never reset a counter or add it twice.
            previous_raw = old.get("raw", {})
            merged = previous_raw | raw
            for field in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                values = [
                    v
                    for v in (previous_raw.get(field), raw.get(field))
                    if type(v) is int and v >= 0
                ]
                if values:
                    merged[field] = max(values)
            if previous_raw.get("cache_creation") or raw.get("cache_creation"):
                merged["cache_creation"] = previous_raw.get("cache_creation", {}) | raw.get(
                    "cache_creation", {}
                )
            raw = merged
            complete = complete or key in self.reported
            complete = complete and "input_tokens" in raw and "output_tokens" in raw
        normalized = normalize_usage(raw, "anthropic", complete)
        if cumulative and any(
            normalized[k] < old.get(k, 0) for k in ("input_tokens", "output_tokens")
        ):
            self.counter_regressions += 1
            self.store.event(
                "native_usage_counter_regression",
                model=model,
                reported=normalized,
                already_recorded=old,
            )
        delta = {
            k: max(0, normalized[k] - old.get(k, 0)) for k in ("input_tokens", "output_tokens")
        }
        if not cumulative and normalized == old:
            return
        usage = (
            normalized
            | delta
            | {
                "source": "official_claude_cli",
                "accounting_schema": 2,
                "observation": observation,
                "scope": "session_reconciliation" if cumulative else "response",
                "temporal_quality": "unattributed"
                if cumulative and any(delta.values())
                else "observed",
            }
        )
        self.budget.add_usage(usage, provisional=not complete and observation != "error")
        self.messages[key] = normalized
        if complete and not cumulative:
            self.reported.add(key)
        if cumulative:
            for field in self.unattributed:
                self.unattributed[field] += delta[field]
        total = self.totals.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
        for field in total:
            total[field] += delta[field]
        request_id = hashlib.sha256(key.encode()).hexdigest()[:32]
        self.store.write(
            f"requests/{request_id}/usage.json",
            usage
            if cumulative
            else normalized
            | {
                k: usage[k]
                for k in ("source", "accounting_schema", "observation", "scope", "temporal_quality")
            },
        )
        self.store.write(
            f"requests/{request_id}/metadata.json",
            {
                "request_id": request_id,
                "route": "official_cli",
                "returned_model": model,
                "authentication": {
                    "auth_method": "claude_subscription",
                    "billing_mode": "subscription",
                    "actual_cost": None,
                },
                "configured_parameters": self.config["model"] | {"speed": "standard"},
                "parameter_source": "official_cli_configuration",
                "cumulative_reconciliation": cumulative,
                "usage_accounting_schema": 2,
                "message_id": None if cumulative else key,
            },
        )
        self.store.event("model_request_finished", request_id=request_id, usage=usage)
        self.store.write("native-usage.json", self.status())
        if model != self.config["model"]["id"]:
            self.budget.stop("policy_violation")
        if raw.get("service_tier") not in {None, "standard", "standard_only", "default"}:
            self.budget.stop("policy_violation")

    def stream_event(self, wrapper):
        event = wrapper.get("event", {})
        lane = wrapper.get("parent_tool_use_id") or "main"
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message", {})
            if not message.get("id"):
                return
            self.streams[lane] = {
                "id": message["id"],
                "model": message.get("model"),
                "final_delta": False,
            }
            self.retry_started = None
            self.record(
                message["id"],
                message.get("model"),
                message.get("usage", {}),
                observation="message_start",
            )
        elif kind in {"message_delta", "message_stop", "error"} and lane in self.streams:
            stream = self.streams[lane]
            if kind == "message_delta":
                raw = event.get("usage", {})
                stream["final_delta"] = stream["final_delta"] or (
                    "output_tokens" in raw and event.get("delta", {}).get("stop_reason") is not None
                )
                self.record(stream["id"], stream["model"], raw, observation="message_delta")
            elif kind == "message_stop":
                self.record(
                    stream["id"],
                    stream["model"],
                    {},
                    complete=stream["final_delta"],
                    observation="message_stop",
                )
                del self.streams[lane]
            else:
                stream["final_delta"] = False

    def provider_error(self, event):
        status = event.get("error_status", event.get("api_error_status"))
        error = event.get("error")
        code = error.get("code", error.get("type")) if isinstance(error, dict) else error
        reason = (
            "configuration_error"
            if status in {401, 403}
            or code
            in {
                "authentication_failed",
                "authentication_error",
                "permission_error",
                "invalid_api_key",
                "model_not_found",
            }
            else "provider_unavailable"
        )
        self.store.event(
            "native_provider_error",
            http_status=status,
            error_code=code,
            reason=reason,
            details=event,
        )
        self.budget.stop(reason)

    def observe(self, event):
        if event.get("type") == "stream_event":
            self.stream_event(event)
        elif event.get("type") == "assistant":
            message = event.get("message", {})
            if (
                event.get("error")
                or event.get("is_api_error_message")
                or message.get("model") == "<synthetic>"
            ):
                # Official CLI error messages are local, synthetic assistant events.
                # Their placeholder model/zero usage are not provider inference.
                self.provider_error(event)
                if message.get("model") not in {None, "<synthetic>"} and message.get("usage"):
                    self.record(
                        message.get("id", "error"),
                        message["model"],
                        message["usage"],
                        complete=False,
                        observation="error",
                    )
                return
            if message.get("id") and isinstance(message.get("usage"), dict):
                self.retry_started = None
                self.record(
                    message["id"],
                    message.get("model"),
                    message["usage"],
                    complete=message.get("stop_reason") is not None,
                    observation="assistant_final"
                    if message.get("stop_reason") is not None
                    else "assistant_block",
                )
        elif event.get("type") == "system" and event.get("subtype") == "api_retry":
            self.retry_started = self.retry_started or time.monotonic()
            self.store.event("native_provider_retry", details=event)
            if event.get("error_status") in {401, 403} or event.get("error") in {
                "authentication_failed",
                "authentication_error",
                "permission_error",
            }:
                self.provider_error(event)
                return
            recovery = self.config["recovery"]
            if (
                event.get("attempt", 1) > recovery["max_transient_retries"]
                or time.monotonic() - self.retry_started >= recovery["max_retry_wait_seconds"]
            ):
                self.budget.stop("provider_unavailable")
        elif event.get("type") == "result":
            self.finished = True
            self.session_totals_received = bool(event.get("modelUsage"))
            if event.get("is_error") or event.get("terminal_reason") == "api_error":
                self.provider_error(event)
            for model, counts in event.get("modelUsage", {}).items():
                if model == "<synthetic>":
                    continue
                self.record(
                    "final:" + model,
                    model,
                    {
                        "input_tokens": counts.get("inputTokens", 0),
                        "output_tokens": counts.get("outputTokens", 0),
                        "cache_read_input_tokens": counts.get("cacheReadInputTokens", 0),
                        "cache_creation_input_tokens": counts.get("cacheCreationInputTokens", 0),
                    },
                    cumulative=True,
                    complete=True,
                    observation="session_result",
                )

    def check_retry_wait(self):
        if (
            self.retry_started is not None
            and time.monotonic() - self.retry_started
            >= self.config["recovery"]["max_retry_wait_seconds"]
        ):
            self.budget.stop("provider_unavailable")


class NativeClaude:
    def __init__(self, config, store, budget, container, trace=None):
        self.config, self.store, self.budget, self.container = config, store, budget, container
        self.base, self.env = profile_environment(
            config["provider"]["auth_dir"], config["provider"]["profile"]
        )
        # Official CLI instances own their authentication/refresh. Share the
        # profile lease, while login/logout retain an exclusive lease and cannot
        # replace the account beneath any running evaluation.
        self.session_lock = self.base.lock(timeout=0, shared=True)
        self.session_lock.__enter__()
        self.process = None
        self.thread = None
        self.pipe_lock = threading.Lock()
        self.usage = NativeUsage(config, store, budget)
        self.trace = trace or AgentTrace(store, budget, "claude_code")

    def preflight(self):
        binary = verify_binary()
        status = subscription_status(self.env)
        identity = {k: status.get(k) for k in ("email", "orgId", "subscriptionType")}
        return {
            "auth_method": "claude_subscription",
            "billing_mode": "subscription",
            "actual_cost": None,
            "account_ref": hashlib.sha256(
                json.dumps(identity, sort_keys=True).encode()
            ).hexdigest(),
            "profile": self.config["provider"]["profile"],
            "profile_source": "current_official_session"
            if self.config["provider"]["profile"] == "current"
            else "isolated_bench_profile",
            "native_binary": binary,
            "execution_mode": "official_cli_container_tools",
            "usage_source": "official_claude_cli",
        }

    def start(self, prompt, run_path):
        directory = run_path / "native-client"
        directory.mkdir(mode=0o700)
        mcp = {
            "mcpServers": {
                "gtoc": {
                    "command": sys.executable,
                    "args": [
                        "-m",
                        "gtoc_bench.container_tools",
                        self.container,
                        str(directory / "tools.jsonl"),
                        str(self.budget.started),
                    ],
                }
            }
        }
        (directory / "mcp.json").write_text(json.dumps(mcp))
        model = self.config["model"]
        env = self.env | {
            "ANTHROPIC_MODEL": model["id"],
            "ANTHROPIC_SMALL_FAST_MODEL": model["id"],
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model["id"],
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model["id"],
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model["id"],
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(model["max_output_tokens"]),
        }
        command = [
            str(binary_path()),
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--restricted",
            "--tools",
            "",
            "--allowedTools",
            "mcp__gtoc__exec",
            "--permission-mode",
            "dontAsk",
            "--permission-prompts",
            "none",
            "--strict-mcp-config",
            "--mcp-config",
            str(directory / "mcp.json"),
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--settings",
            json.dumps(
                {
                    "disableAllHooks": True,
                    "fastMode": False,
                    "autoMemoryEnabled": False,
                    "claudeMdExcludes": ["**"],
                }
            ),
            "--model",
            model["id"],
            "--effort",
            model["reasoning"],
            "--append-system-prompt",
            "All task files and commands are inside the Linux evaluation container. "
            "Use mcp__gtoc__exec to access /workspace and /problem, execute code, and call gtoc status/evaluate. "
            "The host filesystem is not part of this task.",
        ]
        self.store.event("agent_command", command=command)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "gtoc_bench.native_guard"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.process.stdin.write(
            (
                json.dumps(
                    {"command": command, "env": env, "cwd": str(directory), "prompt": prompt}
                )
                + "\n"
            ).encode()
        )
        self.process.stdin.flush()
        self.thread = threading.Thread(target=self.read, args=(run_path,), daemon=True)
        self.thread.start()

    def heartbeat(self):
        self.usage.check_retry_wait()
        if self.process and self.process.poll() is None:
            with self.pipe_lock:
                if not self.process.stdin.closed:
                    self.process.stdin.write(b"heartbeat\n")
                    self.process.stdin.flush()

    def read(self, run_path):
        pending = b""
        try:
            for line in self.process.stdout:
                frame = json.loads(line)
                if frame["type"] == "agent_exit":
                    self.store.event("agent_exit", returncode=frame["returncode"])
                    self.budget.stop(
                        "agent_finished" if frame["returncode"] == 0 else "agent_error"
                    )
                    break
                data = base64.b64decode(frame["body"])
                if frame["channel"] == "stdout":
                    pending += data
                    if len(pending) > 16 * 1024**2:
                        self.store.event(
                            "audit_output_truncated", channel="stdout", reason="16_MiB_line_limit"
                        )
                        self.budget.stop("output_limit")
                        pending = b""
                        break
                    while b"\n" in pending:
                        item, pending = pending.split(b"\n", 1)
                        try:
                            event = json.loads(item)
                        except json.JSONDecodeError:
                            self.trace.feed("stdout", item + b"\n")
                            continue
                        # Partial text/tool deltas repeat the complete blocks
                        # already captured as assistant events. Retain the raw
                        # usage protocol without multiplying the output log.
                        if isinstance(event, dict):
                            if event.get("type") == "stream_event" and event.get("event", {}).get(
                                "type"
                            ) not in {"message_start", "message_delta", "message_stop", "error"}:
                                continue
                            self.usage.observe(event)
                        self.trace.feed("stdout", item + b"\n")
                else:
                    self.trace.feed(frame["channel"], data)
        except (ValueError, OSError):
            self.budget.stop("infrastructure_failure")
        finally:
            if pending:
                self.trace.feed("stdout", pending)
            if not self.usage.finished or not self.usage.totals:
                self.budget.add_usage({"quality": "unknown"})
            self.store.write("native-usage.json", self.usage.status())
            self.budget.stop("agent_error")

    def close(self):
        cancellation = None
        try:
            if self.process:
                if self.process.poll() is None and not self.usage.finished:
                    cancellation = {
                        "reason": self.budget.reason or "controller_shutdown",
                        "requested_utc": utc_now(),
                        "requested_elapsed_seconds": self.store.elapsed(),
                        "upstream_cancellation_confirmed": False,
                    }
                    self.store.event("native_cancel_requested", **cancellation)
                with self.pipe_lock:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.store.event("native_guard_timeout")
                    self.process.terminate()
                    self.process.wait(timeout=10)
                if self.thread:
                    self.thread.join(timeout=3)
                if cancellation:
                    self.store.event(
                        "native_client_stopped",
                        **cancellation,
                        stopped_utc=utc_now(),
                        stopped_elapsed_seconds=self.store.elapsed(),
                        returncode=self.process.returncode,
                        usage_known_tokens=self.budget.tokens,
                        unknown_usage_requests=self.budget.unknown_requests,
                    )
        finally:
            self.session_lock.__exit__(None, None, None)
