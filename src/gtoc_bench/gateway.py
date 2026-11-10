"""Native Responses/Anthropic gateway. Credentials never cross into the container."""

import asyncio
import copy
import json
import random
import threading
import time
import uuid
from contextlib import aclosing

import httpx

from .audit import ResponseTrace
from .auth import ApiKeyCredential, AuthError, ChatGPTCredential, model_catalog
from .config import inference_timeouts
from .provider_retry import retry_hint, retry_not_before, retry_seconds
from .request_artifacts import save_requests
from .storage import utc_now


def provider_error_reason(code):
    code = str(code or "").lower()
    if code in {"context_length_exceeded", "context_window_exceeded"}:
        return "request_error"
    if code in {
        "subscription_sharing_user_not_eligible",
        "subscription_sharing_route_not_supported",
        "chatpass_v2_scope_not_authorized",
        "chatpass_v2_invalid_authorization_context",
        "subscription_sharing_invalid_user",
        "subscription_sharing_unsupported_capability",
        "invalid_api_key",
        "invalid_auth_context",
    }:
        return "configuration_error"
    return "provider_unavailable"


def hard_quota_error(code):
    return str(code or "").lower() in {
        "insufficient_quota",
        "spend_limit_exceeded",
        "billing_hard_limit",
        "subscription_sharing_usage_limit_exceeded",
        "quota_exhausted",
        "quota_exceeded",
    }


def normalize_usage(raw, protocol, complete):
    def count(name):
        value = raw.get(name, 0)
        return value if type(value) is int and value >= 0 else 0

    if protocol in {"responses", "chat_completions"}:
        if protocol == "chat_completions":
            raw = raw | {
                "input_tokens": raw.get("prompt_tokens", 0),
                "output_tokens": raw.get("completion_tokens", 0),
                "input_tokens_details": raw.get("prompt_tokens_details", {}),
                "output_tokens_details": raw.get("completion_tokens_details", {}),
            }
        tokens_in, tokens_out = count("input_tokens"), count("output_tokens")
        cached = raw.get("input_tokens_details", {}).get("cached_tokens", 0)
        reasoning = raw.get("output_tokens_details", {}).get("reasoning_tokens", 0)
    else:
        cached = count("cache_read_input_tokens")
        tokens_in = count("input_tokens") + cached + count("cache_creation_input_tokens")
        tokens_out, reasoning = count("output_tokens"), None
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
        "quality": "reported" if complete and raw else "partial" if raw else "unknown",
        "raw": raw,
    }


class UsageTracker:
    def __init__(self, protocol):
        self.protocol = protocol
        self.buffer = b""
        self.raw = {}
        self.complete = False
        self.model = None
        self.tier = None
        self.response_id = None
        self.error_code = None
        self.error_retry_hint = None
        self.terminal = None
        self.events = 0
        self.last_event = None
        self.last_sequence = None
        self.malformed_events = 0

    def observe(self, event):
        kind = event.get("type", "")
        self.events += 1
        self.last_event = kind or None
        self.last_sequence = event.get("sequence_number")
        payload = event.get("response") or event.get("message")
        if not isinstance(payload, dict):
            payload = event
        if isinstance(payload.get("usage"), dict):
            self.raw.update(payload["usage"])
            self.tier = payload["usage"].get("service_tier", self.tier)
        self.model = payload.get("model", self.model)
        self.tier = payload.get("service_tier", self.tier)
        self.response_id = payload.get("id", self.response_id)
        if kind in {"response.completed", "response.incomplete", "message_stop"}:
            self.complete = True
            self.terminal = kind
        if kind in {"response.failed", "error"} or payload.get("error"):
            # Earlier usage events need not contain the final billable total on failure.
            self.complete = False
            self.terminal = kind
            error = payload.get("error")
            if not isinstance(error, dict):
                error = event
            self.error_code = error.get("code") or error.get("type") or "unknown_error"
            self.error_retry_hint = retry_hint(error=error)
        if not kind and payload.get("usage") and not self.error_code:
            self.complete = True
        if kind == "message" and payload.get("stop_reason"):
            self.complete = True

    def feed(self, data):
        self.buffer = (self.buffer + data).replace(b"\r\n", b"\n")
        if len(self.buffer) > 16 * 1024**2:
            raise ValueError("Oversized provider event")
        while b"\n\n" in self.buffer:
            block, self.buffer = self.buffer.split(b"\n\n", 1)
            payload = b"\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith(b"data:")
            )
            if payload and payload != b"[DONE]":
                try:
                    self.observe(json.loads(payload))
                except (json.JSONDecodeError, AttributeError):
                    self.malformed_events += 1

    def usage(self):
        return normalize_usage(self.raw, self.protocol, self.complete)


def retry_delay(header, attempt):
    seconds = retry_seconds(header)
    return seconds if seconds is not None else min(2**attempt, 30) + random.uniform(0, 0.5)


def validate_request(body, config):
    model = config["model"]
    if body.get("model") != model["id"]:
        raise ValueError("The run permits only its declared model, including auxiliary calls")
    if body.get("speed", "standard") != "standard":
        raise ValueError("Accelerated inference is disabled")
    if (
        body.get("background")
        or body.get("mcp_servers")
        or body.get("container")
        or body.get("web_search_options")
    ):
        raise ValueError("Background inference and provider-hosted execution are disabled")

    def inline_content(value):
        if isinstance(value, list):
            return all(inline_content(child) for child in value)
        if not isinstance(value, dict):
            return True
        kind = value.get("type")
        if kind in {"input_file", "input_image"}:
            if value.get("file_id") or value.get("file_url"):
                return False
            if value.get("image_url") and not str(value["image_url"]).startswith("data:"):
                return False
        if kind in {"image", "document"} and value.get("source", {}).get("type") in {"url", "file"}:
            return False
        return all(inline_content(child) for child in value.values())

    if not inline_content(body.get("input")) or not inline_content(body.get("messages")):
        raise ValueError("Remote input files and images are disabled; send inline content")

    def local_tool(tool):
        kind = tool.get("type", "custom")
        if kind == "namespace":
            return all(local_tool(t) for t in tool.get("tools", []))
        return kind in {"function", "custom"}

    declared_tools = list(body.get("tools", []))
    # Recent Codex versions also declare tools in Responses history items.
    if isinstance(body.get("input"), list):
        for item in body["input"]:
            if isinstance(item, dict) and item.get("type") == "additional_tools":
                declared_tools.extend(item.get("tools", []))
    for tool in declared_tools:
        if not local_tool(tool):
            raise ValueError("Provider-hosted tools and web access are disabled")


def locked_request(body, config):
    body = copy.deepcopy(body)
    validate_request(body, config)
    model = config["model"]
    protocol = config["provider"]["protocol"]
    if protocol == "responses":
        body["service_tier"] = "default"
        body["reasoning"] = body.get("reasoning", {}) | {"effort": model["reasoning"]}
        body["store"] = False
        if config["provider"].get("auth_method") == "chatgpt":
            if not isinstance(body.get("input"), list):
                raise ValueError("ChatGPT requires the complete input history as an array")
            if any(
                item.get("role") == "system" for item in body["input"] if isinstance(item, dict)
            ):
                raise ValueError("ChatGPT requires instructions or developer messages")
            # This endpoint has no per-response output cap; the run's token budget still applies.
            body.pop("max_output_tokens", None)
            unsupported = {
                "background",
                "conversation",
                "max_tool_calls",
                "metadata",
                "moderation",
                "multi_agent",
                "prompt",
                "prompt_cache_retention",
                "safety_identifier",
                "temperature",
                "top_logprobs",
                "top_p",
                "truncation",
                "user",
                "previous_response_id",
            }
            if unsupported.intersection(body):
                raise ValueError(
                    "Unsupported ChatGPT fields: " + ", ".join(sorted(unsupported & body.keys()))
                )
            body["stream"] = True
        else:
            body["max_output_tokens"] = min(
                body.get("max_output_tokens") or model["max_output_tokens"],
                model["max_output_tokens"],
            )
    elif protocol == "chat_completions":
        if body.get("n", 1) != 1:
            raise ValueError("Only one completion per model request is allowed")
        body["service_tier"] = "default"
        body["reasoning_effort"] = model["reasoning"]
        body["max_completion_tokens"] = min(
            body.get("max_completion_tokens")
            or body.get("max_tokens")
            or model["max_output_tokens"],
            model["max_output_tokens"],
        )
        body.pop("max_tokens", None)
        if body.get("stream"):
            body["stream_options"] = {"include_usage": True}
    else:
        body["service_tier"] = "standard_only"
        body["output_config"] = body.get("output_config", {}) | {"effort": model["reasoning"]}
        body["max_tokens"] = min(
            body.get("max_tokens") or model["max_output_tokens"], model["max_output_tokens"]
        )
    return body


class Gateway:
    def __init__(self, config, key, store, budget, transport=None):
        self.config, self.store, self.budget = config, store, budget
        self.credential = ApiKeyCredential(key) if isinstance(key, str) else key
        self.store.redact = self.credential.redact
        self.client = httpx.Client(timeout=httpx.Timeout(60, connect=10), trust_env=False)
        self.timeout_policy = inference_timeouts(config["provider"])
        self.inference = httpx.AsyncClient(
            timeout=httpx.Timeout(
                self.timeout_policy["read_seconds"],
                connect=self.timeout_policy["connect_seconds"],
            ),
            trust_env=False,
            transport=transport,
        )
        self.store.write(
            "provider-transport.json",
            {
                "timeouts": self.timeout_policy,
                "attempt_clock": "continuous_monotonic_wall_from_post_through_body",
                "keepalives_extend_total_attempt_deadline": False,
                "keepalive_seconds": config["provider"].get("keepalive_seconds", 15)
                if config["provider"].get("auth_method") == "antigravity"
                else None,
                "budget_and_token_cancellation_remain_authoritative": True,
            },
        )
        self.slot = threading.Lock()
        self.active_lock = threading.Lock()
        self.loop = None
        self.active = None
        self.request_id = None
        self.cancellation = None
        self.closing = threading.Event()
        self.inflight = 0
        self.transport_active = False
        self.stream_failures = 0
        self.stream_retry_waited = 0.0
        self.recovery_from = None
        self.adapter = None
        self.provider_wait = None
        self.deferred_recovery = False
        self.pending_provider_failure = None
        self.provider_recovery = None
        if config["provider"].get("auth_method") == "chatgpt":
            from .protocols import ChatGPTAdapter

            self.adapter = ChatGPTAdapter()

    def provider_failure(self, reason="provider_unavailable", code=None, retry_after=0, hint=None):
        if reason == "request_error":
            # Waiting cannot repair this payload. Let the client handle the
            # structured error and submit a corrected request within its budget.
            return
        recoverable = reason == "provider_unavailable" and not hard_quota_error(code)
        retry_after = max(retry_after, hint["seconds"] if hint else 0)
        if recoverable:
            self.provider_recovery = {
                "schema_version": 1,
                "state": "pending" if self.deferred_recovery else "interrupted",
                "cause": code,
                "request_id": self.request_id,
                "retry_policy": "respect_provider_minimum",
                "retry_after": hint,
                "retry_not_before_utc": retry_not_before(retry_after) if hint else None,
                "continuation_requires_checkpoint": True,
            }
            self.store.write("provider-recovery.json", self.provider_recovery)
        if self.deferred_recovery and recoverable:
            self.pending_provider_failure = {
                "code": code,
                "retry_after": retry_after,
                "retry_hint": hint,
            }
        else:
            self.budget.stop(reason)

    def recovery_state(self, state, **details):
        if self.provider_recovery is not None:
            self.provider_recovery.update(state=state, **details)
            self.store.write("provider-recovery.json", self.provider_recovery)

    def stream_error(self, tracker, metadata, response_hint):
        hint = max(
            (value for value in (tracker.error_retry_hint, response_hint) if value is not None),
            key=lambda value: value["seconds"],
            default=None,
        )
        reason = provider_error_reason(tracker.error_code)
        details = {
            "code": tracker.error_code or "response.incomplete",
            "classification": "quota"
            if hard_quota_error(tracker.error_code)
            else "configuration"
            if reason == "configuration_error"
            else "request"
            if reason == "request_error"
            else "transient",
            "retry_after": hint,
        }
        metadata["provider_error"] = details
        self.store.event(
            "provider_stream_error",
            request_id=metadata["request_id"],
            client_request_id=metadata["client_request_id"],
            origin="provider",
            http_status=metadata["http_status"],
            **details,
        )
        self.provider_failure(reason, details["code"], hint=hint)

    def headers(self, incoming):
        token = self.credential.token()
        if self.adapter or self.config["provider"]["protocol"] in {"responses", "chat_completions"}:
            return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        headers = {
            "x-api-key": token,
            "anthropic-version": incoming.get("anthropic-version", "2023-06-01"),
            "Content-Type": "application/json",
        }
        if incoming.get("anthropic-beta"):
            headers["anthropic-beta"] = incoming["anthropic-beta"]
        return headers

    def close(self):
        self.cancel()
        with self.slot:
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
            if not self.loop.is_closed():
                self.loop.run_until_complete(self.inference.aclose())
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                self.loop.close()
        self.client.close()
        if isinstance(self.credential, ChatGPTCredential):
            self.credential.client.close()
        elif self.config["provider"].get("auth_method") == "antigravity":
            self.credential.close()
        # Diagnostic I/O must not prevent transport/credential cleanup on a full disk.
        if self.budget.reason:
            self.store.write(
                "gateway-stop.json",
                {
                    "schema_version": 1,
                    "scope": "run_termination",
                    "stop_reason": self.budget.reason,
                    "utc": utc_now(),
                    "last_recovery_state": (self.provider_recovery or {}).get("state"),
                },
            )

    def cancel(self):
        """Wake blocked asynchronous I/O, including connect, headers and body reads."""
        self.closing.set()
        if self.config["provider"].get("auth_method") == "antigravity" and self.credential.cancel():
            self.store.event(
                "provider_bridge_stop_requested",
                bridge="CLIProxyAPI",
                reason=self.budget.reason or "controller_shutdown",
                upstream_cancellation_confirmed=False,
            )
        with self.active_lock:
            if self.active is None or self.active.done() or self.cancellation is not None:
                return
            task, loop = self.active, self.loop
            if not self.transport_active:
                # Backoff/accounting is not an open provider transport. Do not invent
                # a cancellation after an EOF that was already observed and closed.
                loop.call_soon_threadsafe(task.cancel)
                return
            self.cancellation = {
                "reason": self.budget.reason or "controller_shutdown",
                "requested_utc": utc_now(),
                "requested_elapsed_seconds": self.store.elapsed(),
                "upstream_cancellation_confirmed": False,
            }
            # Persist the request before task cancellation; cancel even if logging fails.
            try:
                self.store.event(
                    "model_cancel_requested", request_id=self.request_id, **self.cancellation
                )
            finally:
                loop.call_soon_threadsafe(task.cancel)

    def watch_budget(self, finished):
        while not finished.wait(0.05):
            if not self.budget.check():
                self.cancel()
                return

    def preflight(self):
        if self.config["provider"].get("auth_method") == "chatgpt":
            models = model_catalog(self.credential, self.client)
            selected = next(
                (m for m in models if m.get("slug") == self.config["model"]["id"]), None
            )
            if selected is None:
                raise ValueError("Declared model is absent from this ChatGPT account's catalog")
            efforts = {m["effort"] for m in selected.get("supported_reasoning_levels", [])}
            if efforts and self.config["model"]["reasoning"] not in efforts:
                raise ValueError("Declared reasoning effort is absent from the ChatGPT catalog")
            self.store.write("provider-model.json", selected)
            self.store.event("provider_preflight", status=200, auth_method="chatgpt")
            return
        response = self.client.get(
            self.config["provider"]["base_url"] + "/models", headers=self.headers({})
        )
        if self.config["provider"].get("auth_method") == "antigravity":
            if response.status_code != 200:
                raise ValueError("Antigravity model catalog is unavailable; no evaluation started")
            catalog = response.json().get("data", [])
            selected = next(
                (
                    m
                    for m in catalog
                    if isinstance(m, dict) and m.get("id") == self.config["model"]["id"]
                ),
                None,
            )
            if selected is None:
                raise ValueError(
                    "Declared model is absent from this Antigravity profile's catalog; use gtoc auth models antigravity"
                )
            self.store.write("provider-model.json", selected)
            self.store.event(
                "provider_preflight", status=200, auth_method="antigravity", experimental=True
            )
            return
        if response.status_code in {401, 403}:
            raise ValueError("Provider authentication/authorization failed during preflight")
        if response.status_code == 429 or response.status_code >= 500:
            raise ValueError("Provider unavailable during preflight; no evaluation started")
        self.store.event("provider_preflight", status=response.status_code)

    async def safe_chunks(self, response, streaming, observation):
        """Redact whole SSE events, including secrets split across transport chunks."""
        pending = b""
        async with aclosing(response.aiter_bytes()) as chunks:
            async for chunk in chunks:
                now = time.monotonic()
                if observation["transport_chunks"] == 0:
                    observation["first_byte_seconds"] = now - observation["last_activity"]
                observation["transport_chunks"] += 1
                observation["transport_bytes"] += len(chunk)
                observation["last_chunk_elapsed_seconds"] = self.store.elapsed()
                observation["max_idle_seconds"] = max(
                    observation["max_idle_seconds"], now - observation["last_activity"]
                )
                observation["last_activity"] = now
                # Deliver cancellation while consuming already buffered data, too.
                await asyncio.sleep(0)
                pending += chunk
                if streaming:
                    pending = pending.replace(b"\r\n", b"\n")
                    while b"\n\n" in pending:
                        event, pending = pending.split(b"\n\n", 1)
                        if event.lstrip().startswith(b":") and not any(
                            line.startswith(b"data:") for line in event.splitlines()
                        ):
                            observation["keepalive_frames"] = (
                                observation.get("keepalive_frames", 0) + 1
                            )
                            observation["last_keepalive_elapsed_seconds"] = self.store.elapsed()
                        if len(event) > 16 * 1024**2:
                            raise ValueError("Oversized provider event")
                        yield (
                            self.credential.redact(event.decode(errors="replace")).encode()
                            + b"\n\n"
                        )
                if len(pending) > 16 * 1024**2:
                    raise ValueError("Oversized provider response")
                observation["buffered_bytes"] = len(pending)
        if pending:
            yield self.credential.redact(pending.decode(errors="replace")).encode()

    def stream_interrupted(self, request_id, observation):
        """Only the native Codex client may reconcile/retry a partially delivered turn."""
        self.stream_failures += 1
        recoverable = self.deferred_recovery or (
            self.config["agent"]["id"] == "codex"
            and self.stream_failures <= self.config["recovery"]["max_transient_retries"]
            and self.budget.check()
        )
        observation.update(
            consecutive_failures=self.stream_failures,
            recovery="gateway_buffered_retry"
            if self.deferred_recovery
            else "native_codex_retry"
            if recoverable
            else "interrupt_run",
            provider_internal_cause_known=False,
        )
        self.recovery_from = request_id
        self.store.event("model_stream_interrupted", request_id=request_id, **observation)
        if self.deferred_recovery or not recoverable:
            self.provider_failure(
                code=observation.get("timeout", {}).get("code", "incomplete_stream")
            )

    def proxy(self, route, incoming, data, emit, client_request_id=None):
        client_request_id = client_request_id or uuid.uuid4().hex
        received = time.monotonic()
        received_active = self.budget.timeline_elapsed()
        self.store.event("model_request_received", client_request_id=client_request_id, route=route)

        def audited_emit(status, headers, chunk, end):
            if status >= 400:
                self.store.event(
                    "model_client_error",
                    client_request_id=client_request_id,
                    origin="model_gateway",
                    http_status=status,
                    response_body=chunk[:2048].decode(errors="replace"),
                )
            emit(status, headers, chunk, end)

        def reject(status, code):
            self.store.event(
                "request_rejected",
                client_request_id=client_request_id,
                origin="gateway_admission",
                http_status=status,
                reason=code,
                wait_seconds=time.monotonic() - received,
                active_request_id=self.request_id,
            )
            audited_emit(
                status,
                {"Content-Type": "application/json", "Retry-After": "1"},
                json.dumps({"error": {"code": code, "message": code}}).encode(),
                True,
            )

        # Native clients may overlap requests, including while the previous model
        # response is still streaming. Serialize upstream inference within the
        # configured recovery window; a fixed 10-second tail allowance is too short.
        max_wait = self.config["recovery"]["max_retry_wait_seconds"]
        queued = False
        while True:
            if self.closing.is_set() or not self.budget.check():
                reject(503, "run_inactive")
                return
            if self.slot.acquire(timeout=0.05):
                break
            if not queued:
                self.store.event(
                    "model_request_queued",
                    client_request_id=client_request_id,
                    active_request_id=self.request_id,
                )
                queued = True
            if self.budget.timeline_elapsed() - received_active >= max_wait:
                # A local admission failure is infrastructure, not provider quota
                # or a failure of the agent's optimization code.
                self.budget.stop("infrastructure_failure")
                reject(503, "local_model_queue_timeout")
                return
        try:
            if self.closing.is_set() or not self.budget.check():
                reject(503, "run_inactive")
                return
            self.store.event(
                "model_request_admitted",
                client_request_id=client_request_id,
                wait_seconds=time.monotonic() - received,
            )
            self._proxy(route, incoming, data, audited_emit, client_request_id)
        finally:
            self.slot.release()

    def _proxy(self, route, incoming, data, emit, client_request_id):
        use_wait = (
            self.provider_wait is not None
            and self.config["recovery"].get("clock_policy") == "provider_wait"
        )
        protocol = self.config["provider"]["protocol"]
        allowed = (
            {"/responses", "/chat/completions", "/messages"}
            if self.adapter
            else {
                "responses": {"/responses", "/responses/compact"},
                "chat_completions": {"/chat/completions"},
                "anthropic": {"/messages", "/messages/count_tokens"},
            }[protocol]
        )
        if self.adapter and route == "/responses/compact":
            raise ValueError("ChatGPT plan inference supports only /responses upstream")
        if route not in allowed:
            raise ValueError("Unsupported provider route")
        body = json.loads(data)
        client_body = copy.deepcopy(body)
        requested = {
            k: v
            for k, v in body.items()
            if k
            in {
                "model",
                "reasoning",
                "reasoning_effort",
                "thinking",
                "output_config",
                "temperature",
                "top_p",
                "max_tokens",
                "max_output_tokens",
                "max_completion_tokens",
                "service_tier",
            }
        }
        adaptation = None
        if self.adapter:
            from .protocols import ADAPTER

            validate_request(body, self.config)
            source_protocol = {
                "/responses": "responses",
                "/chat/completions": "chat_completions",
                "/messages": "anthropic",
            }[route]
            streaming = bool(body.get("stream"))
            adaptation = {
                "id": ADAPTER,
                "client_route": route,
                "client_streaming": streaming,
                "buffered_response": source_protocol != "responses" or not streaming,
            }
            if not use_wait:
                emit = self.adapter.receiver(source_protocol, streaming, emit)
            body = self.adapter.request(body, source_protocol)
            wire_config = self.config | {
                "provider": self.config["provider"] | {"protocol": "responses"}
            }
            body = locked_request(body, wire_config)
            route = "/responses"
        else:
            body = locked_request(body, self.config)
        # Compact/count APIs accept a smaller schema and do not select a service tier.
        if route == "/responses/compact":
            body = {
                k: v
                for k, v in body.items()
                if k in {"model", "input", "instructions", "previous_response_id"}
            }
        if route == "/messages/count_tokens":
            body = {
                k: v
                for k, v in body.items()
                if k in {"model", "messages", "system", "tools", "thinking", "tool_choice"}
            }
        self.inflight += 1
        finished = threading.Event()
        watcher = None
        try:
            if self.closing.is_set() or not self.budget.check():
                emit(
                    503,
                    {"Content-Type": "application/json"},
                    b'{"error":{"message":"Run is no longer active"}}',
                    True,
                )
                return
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
            with self.active_lock:
                self.cancellation = None
                self.active = self.loop.create_task(
                    (self._resilient_attempts if use_wait else self._attempts)(
                        route,
                        incoming,
                        body,
                        requested,
                        emit,
                        adaptation,
                        client_body,
                        client_request_id,
                    )
                )
            watcher = threading.Thread(target=self.watch_budget, args=(finished,), daemon=True)
            watcher.start()
            self.loop.run_until_complete(self.active)
        except asyncio.CancelledError:
            # Cancellation before sending a request or during retry backoff has no response.
            emit(
                503,
                {"Content-Type": "application/json"},
                b'{"error":{"message":"Run cancelled"}}',
                True,
            )
        finally:
            finished.set()
            if watcher:
                watcher.join()
            if self.loop is not None and not self.loop.is_closed():
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            with self.active_lock:
                self.active, self.request_id = None, None
            self.inflight -= 1

    async def _resilient_attempts(
        self,
        route,
        incoming,
        body,
        requested,
        emit,
        adaptation=None,
        client_body=None,
        client_request_id=None,
    ):
        """Replay only turns whose bytes/tools have never reached the agent."""
        interval = self.config["recovery"].get("provider_retry_interval_seconds", 300)
        window = self.config["recovery"].get("provider_wait_seconds", 3600)
        began, deadline, attempts = None, None, 0
        deadline_timer = None
        exhausted = False
        recovered = threading.Event()

        def expire_wait():
            nonlocal exhausted
            if self.budget.stopped.is_set() or recovered.is_set():
                return
            exhausted = True
            try:
                self.store.event(
                    "provider_wait_exhausted",
                    client_request_id=client_request_id,
                    wall_seconds=time.monotonic() - began,
                )
            finally:
                self.budget.stop("provider_unavailable")
                self.cancel()

        self.deferred_recovery = True
        try:
            while self.budget.check() and not self.closing.is_set():
                frames, size = [], 0
                self.pending_provider_failure = None

                def capture(status, headers, chunk, end):
                    nonlocal size
                    if size + len(chunk) > 16 * 1024**2:
                        raise ValueError("Buffered provider response exceeds 16 MiB")
                    size += len(chunk)
                    frames.append((status, headers, chunk, end))

                attempt_emit = capture
                if self.adapter:
                    source = {
                        "/responses": "responses",
                        "/chat/completions": "chat_completions",
                        "/messages": "anthropic",
                    }[adaptation["client_route"]]
                    attempt_emit = self.adapter.receiver(
                        source, adaptation["client_streaming"], capture
                    )
                self.stream_failures, self.stream_retry_waited = 0, 0.0
                await self._attempts(
                    route,
                    incoming,
                    body,
                    requested,
                    attempt_emit,
                    adaptation,
                    client_body,
                    client_request_id,
                )
                failure = self.pending_provider_failure
                if not self.budget.check() or self.closing.is_set():
                    break
                if failure is None:
                    recovered.set()
                    if self.provider_wait.suspended.is_set():
                        await asyncio.to_thread(self.provider_wait.resume)
                    if began is not None:
                        self.recovery_state(
                            "recovered",
                            outcome="recovered",
                            attempts=attempts,
                            recovered_utc=utc_now(),
                            continuation_requires_checkpoint=False,
                            next_retry_not_before_utc=None,
                            retry_within_window=False,
                        )
                        self.store.event(
                            "provider_wait_recovered",
                            client_request_id=client_request_id,
                            attempts=attempts,
                            wall_seconds=time.monotonic() - began,
                            paused_seconds=self.budget.status()["paused_seconds"],
                        )
                    for frame in frames:
                        emit(*frame)
                    return
                try:
                    await asyncio.to_thread(self.provider_wait.pause)
                except ValueError as exc:
                    self.store.event("provider_wait_unavailable", reason=str(exc))
                    self.budget.stop("provider_unavailable")
                    break
                except Exception as exc:
                    self.store.event("provider_wait_failed", reason=str(exc))
                    self.budget.stop("infrastructure_failure")
                    break
                if not self.budget.check():
                    break
                if began is None:
                    began = time.monotonic()
                    deadline = began + window
                    deadline_timer = threading.Timer(window, expire_wait)
                    deadline_timer.daemon = True
                    deadline_timer.start()
                    self.store.event(
                        "provider_wait_started",
                        client_request_id=client_request_id,
                        interval_seconds=interval,
                        maximum_wait_seconds=window,
                        cause=failure["code"],
                    )
                now = time.monotonic()
                attempts += 1
                # Both the configured interval and the provider hint are minimums.
                # If neither fits the window, wait for expiry without retrying early.
                minimum_delay = max(interval, failure.get("retry_after") or 0)
                next_try = now + minimum_delay
                next_retry_utc = retry_not_before(minimum_delay)
                self.recovery_state(
                    "waiting",
                    attempts=attempts,
                    interval_seconds=interval,
                    maximum_wait_seconds=window,
                    next_retry_not_before_utc=next_retry_utc,
                    retry_within_window=next_try < deadline,
                )
                self.store.event(
                    "provider_wait_retry_scheduled",
                    client_request_id=client_request_id,
                    attempt=attempts,
                    wait_seconds=max(0, min(next_try, deadline) - now),
                    cause=failure["code"],
                    retry_after=failure.get("retry_hint"),
                    minimum_retry_delay_seconds=minimum_delay,
                    next_retry_not_before_utc=next_retry_utc,
                    retry_within_window=next_try < deadline,
                    retry_policy="respect_provider_minimum",
                )
                await asyncio.sleep(max(0, min(next_try, deadline) - now))
                if time.monotonic() >= deadline:
                    exhausted = True
                    self.store.event(
                        "provider_wait_exhausted",
                        client_request_id=client_request_id,
                        wall_seconds=time.monotonic() - began,
                    )
                    self.budget.stop("provider_unavailable")
                    break
            emit(
                503,
                {"Content-Type": "application/json"},
                b'{"error":{"message":"Provider recovery stopped or exhausted"}}',
                True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.event("provider_wait_failed", reason=str(exc))
            self.budget.stop("infrastructure_failure")
            emit(
                503,
                {"Content-Type": "application/json"},
                b'{"error":{"message":"Provider recovery infrastructure failed"}}',
                True,
            )
        finally:
            if deadline_timer is not None:
                deadline_timer.cancel()
            if (
                self.budget.stopped.is_set()
                and not recovered.is_set()
                and (began is not None or self.pending_provider_failure is not None)
            ):
                self.recovery_state(
                    "interrupted" if self.budget.reason == "provider_unavailable" else "stopped",
                    stop_reason=self.budget.reason,
                    outcome="recovery_window_exhausted" if exhausted else "recovery_stopped",
                    remaining_seconds=self.budget.status()["remaining_seconds"],
                )
            self.deferred_recovery = False

    async def _attempts(
        self,
        route,
        incoming,
        body,
        requested,
        emit,
        adaptation=None,
        client_body=None,
        client_request_id=None,
    ):
        protocol = "responses" if self.adapter else self.config["provider"]["protocol"]
        limits = self.config["recovery"]
        recovery_from = self.recovery_from
        if self.stream_failures:
            delay = min(2 ** (self.stream_failures - 1), 30)
            if self.stream_retry_waited + delay > limits["max_retry_wait_seconds"]:
                self.provider_failure(code="stream_retry_window_exhausted")
                emit(
                    503,
                    {"Content-Type": "application/json"},
                    b'{"error":{"message":"Stream recovery window exhausted"}}',
                    True,
                )
                return
            self.stream_retry_waited += delay
            self.store.event(
                "model_stream_retry",
                previous_request_id=recovery_from,
                client_request_id=client_request_id,
                wait_seconds=delay,
            )
            await asyncio.sleep(delay)
        waited = 0.0
        auth_retried = False
        # One pre-stream OAuth refresh is independent of the transient retry allowance.
        extra = int(isinstance(self.credential, ChatGPTCredential))
        for attempt in range(limits["max_transient_retries"] + 1 + extra):
            if not self.budget.check():
                emit(
                    503,
                    {"Content-Type": "application/json"},
                    b'{"error":{"message":"Run is not active"}}',
                    True,
                )
                return
            request_id = uuid.uuid4().hex
            self.request_id = request_id
            with self.active_lock:
                self.transport_active = True
            metadata = {
                "request_id": request_id,
                "client_request_id": client_request_id,
                "route": route,
                "attempt": attempt,
                "recovery_from_request_id": recovery_from,
                "provider": self.config["provider"]["id"],
                "authentication": self.credential.public_info(),
                "protocol_adapter": adaptation,
                "requested_parameters": requested,
                "timeouts": self.timeout_policy,
                "effective_parameters": {
                    k: v
                    for k, v in body.items()
                    if k in requested
                    or k
                    in {
                        "service_tier",
                        "reasoning",
                        "reasoning_effort",
                        "output_config",
                        "max_output_tokens",
                        "max_tokens",
                        "max_completion_tokens",
                    }
                },
            }
            metadata["request_artifacts"] = save_requests(
                self.store, f"requests/{request_id}", client_body or body, body
            )
            self.store.write(f"requests/{request_id}/metadata.json", metadata)
            trace = ResponseTrace(self.store, f"requests/{request_id}/response.jsonl")
            self.store.event(
                "model_request_started",
                request_id=request_id,
                client_request_id=client_request_id,
                model=body["model"],
            )
            tracker, started, first_byte = UsageTracker(protocol), time.monotonic(), None
            first_event = None
            deadline = asyncio.timeout(self.timeout_policy["attempt_seconds"])
            observation = {
                "ending": None,
                "transport_chunks": 0,
                "transport_bytes": 0,
                "last_chunk_elapsed_seconds": None,
                "max_idle_seconds": 0.0,
                "buffered_bytes": 0,
                "last_activity": started,
            }
            accounted = False
            retry, delay, sent, count_only = False, 0.0, False, route.endswith("count_tokens")
            try:
                headers = self.headers(incoming)
                if self.closing.is_set() or not self.budget.check():
                    self.cancel()
                    await asyncio.sleep(0)
                    raise asyncio.CancelledError
                async with (
                    deadline,
                    self.inference.stream(
                        "POST",
                        self.config["provider"]["base_url"] + route,
                        headers=headers,
                        json=body,
                    ) as response,
                ):
                    self.store.event(
                        "provider_response",
                        request_id=request_id,
                        client_request_id=client_request_id,
                        origin="provider",
                        http_status=response.status_code,
                    )
                    metadata.update(
                        headers_seconds=time.monotonic() - started,
                        http_status=response.status_code,
                        response_content_type=response.headers.get("content-type"),
                        provider_request_id=response.headers.get(
                            "request-id", response.headers.get("x-request-id")
                        ),
                        response_headers={
                            name: self.credential.redact(value)
                            for name, value in response.headers.items()
                            if name
                            in {
                                "date",
                                "content-type",
                                "content-length",
                                "transfer-encoding",
                                "request-id",
                                "x-request-id",
                                "retry-after",
                                "retry-after-ms",
                                "cf-ray",
                            }
                        },
                    )
                    response_hint = retry_hint(response.headers)
                    if response.status_code >= 400:
                        error_body = bytearray()
                        async for chunk in response.aiter_bytes(16384):
                            error_body.extend(chunk)
                            if len(error_body) >= 1024**2 or self.budget.stopped.is_set():
                                break
                        message = self.credential.redact(
                            bytes(error_body[: 1024**2]).decode(errors="replace")
                        )
                        trace.feed(message.encode())
                        raw = message.encode()
                        quota = any(
                            code in message.lower()
                            for code in (
                                "insufficient_quota",
                                "spend_limit_exceeded",
                                "credit balance",
                                "billing_hard_limit",
                                "subscription_sharing_usage_limit_exceeded",
                                "quota_exhausted",
                                "quota_exceeded",
                                "quota exceeded",
                            )
                        )
                        retry = response.status_code in {429, 500, 502, 503, 504} and not quota
                        try:
                            error_json = json.loads(message)
                            error = error_json.get("error", error_json)
                        except (ValueError, AttributeError):
                            error = None
                        hint = retry_hint(response.headers, error)
                        delay = hint["seconds"] if hint else retry_delay(None, attempt)
                        metadata["provider_retry_after"] = hint
                        can_retry = (
                            retry
                            and attempt - int(auth_retried) < limits["max_transient_retries"]
                            and waited + delay <= limits["max_retry_wait_seconds"]
                        )
                        refresh_auth = (
                            response.status_code == 401
                            and isinstance(self.credential, ChatGPTCredential)
                            and not auth_retried
                            and not quota
                        )
                        if refresh_auth:
                            self.credential.token(
                                rejected_token=headers["Authorization"].removeprefix("Bearer ")
                            )
                            auth_retried, retry, can_retry, delay = True, True, True, 0
                            self.store.event("provider_auth_refreshed", request_id=request_id)
                        elif quota:
                            self.budget.stop("provider_unavailable")
                        elif response.status_code in {401, 403}:
                            self.budget.stop("configuration_error")
                        elif not can_retry and retry:
                            self.provider_failure(
                                code=f"http_{response.status_code}", retry_after=delay, hint=hint
                            )
                        elif not can_retry:
                            self.budget.stop("configuration_error")
                        if not can_retry:
                            emit(
                                response.status_code,
                                {
                                    "Content-Type": "application/json",
                                    **(
                                        {"Retry-After": response.headers["retry-after"]}
                                        if "retry-after" in response.headers
                                        else {}
                                    ),
                                },
                                raw,
                                True,
                            )
                            sent = True
                            retry = False
                        metadata["error"] = message
                    else:
                        # SIWC may omit Content-Type even for a successful SSE response.
                        # Use the requested transport only when the header is absent.
                        content_type = response.headers.get("content-type") or (
                            "text/event-stream" if body.get("stream") else "application/json"
                        )
                        streaming = "text/event-stream" in content_type
                        if self.deferred_recovery and self.provider_wait.suspended.is_set():
                            await asyncio.to_thread(self.provider_wait.resume)
                        emit(200, {"Content-Type": content_type}, b"", False)
                        sent = True
                        buffered = bytearray()
                        async with aclosing(
                            self.safe_chunks(response, streaming, observation)
                        ) as chunks:
                            async for chunk in chunks:
                                trace.feed(chunk)
                                if first_byte is None:
                                    first_byte = time.monotonic() - started
                                if streaming:
                                    previous_events = tracker.events
                                    tracker.feed(chunk)
                                    if tracker.events > previous_events:
                                        observation["last_provider_event_elapsed_seconds"] = (
                                            self.store.elapsed()
                                        )
                                        if first_event is None:
                                            first_event = time.monotonic() - started
                                    if tracker.complete and not accounted and not count_only:
                                        self.budget.add_usage(tracker.usage())
                                        accounted = True
                                else:
                                    buffered.extend(chunk)
                                    if len(buffered) > 16 * 1024**2:
                                        raise ValueError("Provider response too large")
                                emit(200, {}, chunk, False)
                                if tracker.error_code or (
                                    self.config["provider"].get("auth_method") == "chatgpt"
                                    and tracker.terminal == "response.incomplete"
                                ):
                                    self.stream_error(tracker, metadata, response_hint)
                                if (
                                    self.pending_provider_failure is not None
                                    and self.deferred_recovery
                                ):
                                    break
                                if not self.budget.check():
                                    if not tracker.complete:
                                        self.cancel()
                                        await asyncio.sleep(0)
                                    break
                        if not streaming and buffered:
                            tracker.observe(json.loads(buffered))
                            first_event = time.monotonic() - started
                            if tracker.error_code:
                                self.stream_error(tracker, metadata, response_hint)
                            if not count_only:
                                self.budget.add_usage(tracker.usage())
                                accounted = True
                        emit(200, {}, b"", True)
                        observation["ending"] = (
                            "terminal_event" if tracker.terminal else "clean_eof"
                        )
                        if streaming and not tracker.complete and not tracker.error_code:
                            observation["ending"] = "eof_before_terminal"
            except asyncio.CancelledError:
                retry = False
                metadata["cancelled"] = True
                observation["ending"] = "controller_cancelled"
                # Exiting the async stream closes the transport before this handler runs.
                if sent:
                    emit(200, {}, b"", True)
                else:
                    emit(
                        503,
                        {"Content-Type": "application/json"},
                        b'{"error":{"message":"Run cancelled"}}',
                        True,
                    )
            except AuthError as exc:
                observation["ending"] = "authentication_error"
                metadata["error"] = str(exc)
                retry = (
                    not sent
                    and exc.reason == "provider_unavailable"
                    and attempt - int(auth_retried) < limits["max_transient_retries"]
                )
                delay = retry_delay(None, attempt)
                if not retry:
                    self.provider_failure(exc.reason, type(exc).__name__)
                    emit(
                        503,
                        {"Content-Type": "application/json"},
                        json.dumps({"error": {"message": str(exc)}}).encode(),
                        True,
                    )
            except (httpx.HTTPError, ValueError, TimeoutError) as exc:
                metadata["error"] = self.credential.redact(str(exc))
                timeout_code = None
                if deadline.expired() or isinstance(exc, httpx.TimeoutException):
                    timeout_code = (
                        "model_attempt_timeout"
                        if deadline.expired()
                        else "model_"
                        + type(exc).__name__.removesuffix("Timeout").lower()
                        + "_timeout"
                    )
                    field = (
                        "attempt_seconds"
                        if deadline.expired()
                        else "connect_seconds"
                        if isinstance(exc, httpx.ConnectTimeout)
                        else "read_seconds"
                    )
                    phase = "response_body" if "http_status" in metadata else "response_headers"
                    if isinstance(exc, httpx.ConnectTimeout):
                        phase = "connect"
                    elif isinstance(exc, httpx.WriteTimeout):
                        phase = "request_write"
                    elif isinstance(exc, httpx.PoolTimeout):
                        phase = "connection_pool"
                    observation["timeout"] = {
                        "code": timeout_code,
                        "origin": "benchmark_gateway",
                        "phase": phase,
                        "bound_seconds": self.timeout_policy[field],
                        "silence_seconds": time.monotonic() - observation["last_activity"],
                        "provider_internal_cause_known": False,
                    }
                    metadata["error"] = (
                        f"{timeout_code}: {observation['timeout']['phase']} ({self.timeout_policy[field]} s limit)"
                    )
                    self.store.event(
                        "model_attempt_timeout",
                        request_id=request_id,
                        client_request_id=client_request_id,
                        **observation["timeout"],
                    )
                observation["ending"] = (
                    "attempt_timeout"
                    if deadline.expired()
                    else "transport_error"
                    if isinstance(exc, httpx.HTTPError)
                    else "invalid_response"
                )
                observation["exception_type"] = type(exc).__name__
                causes = []
                cause = exc
                while cause is not None and len(causes) < 5:
                    causes.append(
                        {
                            "type": type(cause).__name__,
                            "message": self.credential.redact(str(cause)),
                        }
                    )
                    cause = cause.__cause__
                observation["exception_chain"] = causes
                retry = not sent and attempt - int(auth_retried) < limits["max_transient_retries"]
                delay = retry_delay(None, attempt)
                if not retry:
                    if not sent or not isinstance(exc, httpx.HTTPError):
                        self.provider_failure(code=timeout_code or type(exc).__name__)
                    if sent:
                        emit(200, {}, b"", True)
                    else:
                        emit(
                            502,
                            {"Content-Type": "application/json"},
                            b'{"error":{"message":"Provider connection failed"}}',
                            True,
                        )
            finally:
                with self.active_lock:
                    self.transport_active = False
                    cancellation = dict(self.cancellation) if self.cancellation else None
                closed = {
                    "transport_closed_utc": utc_now(),
                    "transport_closed_elapsed_seconds": self.store.elapsed(),
                    "upstream_cancellation_confirmed": False,
                }
                if cancellation is not None:
                    cancellation.update(closed)
                    cancellation["close_latency_seconds"] = max(
                        0,
                        cancellation["transport_closed_elapsed_seconds"]
                        - cancellation["requested_elapsed_seconds"],
                    )
                    metadata["cancellation"] = cancellation
                self.store.event(
                    "model_transport_closed", request_id=request_id, **(cancellation or closed)
                )
                observation.update(
                    events=tracker.events,
                    last_event=tracker.last_event,
                    last_sequence=tracker.last_sequence,
                    malformed_events=tracker.malformed_events,
                    terminal=tracker.terminal,
                    unparsed_bytes=len(tracker.buffer),
                    idle_at_close_seconds=time.monotonic() - observation.pop("last_activity"),
                )
                if (
                    not tracker.complete
                    and not tracker.error_code
                    and sent
                    and observation["ending"]
                    in {"eof_before_terminal", "transport_error", "attempt_timeout"}
                    and self.budget.check()
                ):
                    self.stream_interrupted(request_id, observation)
                elif tracker.complete:
                    if self.stream_failures:
                        self.store.event(
                            "model_stream_recovered",
                            request_id=request_id,
                            previous_request_id=recovery_from,
                            consecutive_failures=self.stream_failures,
                        )
                    self.stream_failures, self.stream_retry_waited, self.recovery_from = (
                        0,
                        0.0,
                        None,
                    )
                metadata["stream"] = observation
                usage = tracker.usage()
                if self.config["provider"].get("auth_method") == "antigravity":
                    usage.update(source="CLIProxyAPI_reported", upstream_verified=False)
                if count_only:
                    usage["quality"] = "not_billed"
                elif not accounted:
                    self.budget.add_usage(usage)
                metadata.update(
                    duration_seconds=time.monotonic() - started,
                    first_byte_seconds=observation.get("first_byte_seconds", first_byte),
                    first_provider_event_seconds=first_event,
                    returned_model=tracker.model,
                    returned_service_tier=tracker.tier,
                    response_id=tracker.response_id,
                    response_terminal=tracker.terminal,
                    provider_error_code=tracker.error_code,
                    response_trace=trace.close(tracker.complete),
                )
                self.store.write(f"requests/{request_id}/usage.json", usage)
                self.store.write(f"requests/{request_id}/metadata.json", metadata)
                self.store.event(
                    "model_request_finished",
                    request_id=request_id,
                    client_request_id=client_request_id,
                    usage=usage,
                    duration_seconds=metadata["duration_seconds"],
                )
                if tracker.tier and tracker.tier not in {
                    "default",
                    "standard",
                    "standard_only",
                    "auto",
                }:
                    self.budget.stop("policy_violation")
                elif tracker.complete and tracker.tier == "auto":
                    self.budget.stop("policy_violation")
                self.request_id = None
            if not retry:
                return
            if waited + delay > limits["max_retry_wait_seconds"]:
                self.provider_failure(code="short_retry_window_exhausted")
                emit(
                    503,
                    {"Content-Type": "application/json"},
                    b'{"error":{"message":"Provider retry window exhausted"}}',
                    True,
                )
                return
            self.store.event("provider_retry", request_id=request_id, wait_seconds=delay)
            waited += delay
            await asyncio.sleep(delay)
