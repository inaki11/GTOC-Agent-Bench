"""Host-owned, bounded traces. Client events are evidence, not trusted instructions."""

import gzip
import json
import os
import re
import threading
import time
import zlib
from pathlib import Path

SECRET_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api_key",
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
}


def redact_text(text):
    text = re.sub(r"\b(?:sk-[\w-]{16,}|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b", "[REDACTED]", text)
    return re.sub(
        r"(?i)((?:authorization|api[_-]?key|access_token|refresh_token|id_token|client_secret|cookie)"
        r"[\"']?\s*[:=]\s*[\"']?)(?:Bearer\s+)?[^\s\"',;}]+",
        r"\1[REDACTED]",
        text,
    )


def sanitize(value, redact=redact_text):
    if isinstance(value, str):
        return redact_text(redact(value))
    if isinstance(value, list):
        return [sanitize(item, redact) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SECRET_KEYS else sanitize(item, redact)
            for key, item in value.items()
        }
    return value


def activity(event):
    """A small common vocabulary; retain the complete native event alongside it."""
    kind = str(event.get("type", "unknown"))
    item = event.get("item", {})
    item = item if isinstance(item, dict) else {}
    if kind.startswith("item."):
        return {"kind": item.get("type", kind), "id": item.get("id"), "phase": kind.split(".")[-1]}
    if kind in {"assistant", "user"}:
        message = event.get("message", {})
        blocks = message.get("content", []) if isinstance(message, dict) else []
        tools = (
            [
                {
                    "id": b.get("id", b.get("tool_use_id")),
                    "name": b.get("name"),
                    "phase": "started" if b.get("type") == "tool_use" else "completed",
                }
                for b in blocks
                if isinstance(b, dict) and b.get("type") in {"tool_use", "tool_result"}
            ]
            if isinstance(blocks, list)
            else []
        )
        return {"kind": kind, "tools": tools}
    return {"kind": kind}


class AgentTrace:
    """Buffer complete lines so UTF-8 and secrets split across reads stay intact."""

    def __init__(self, store, budget, agent):
        self.store, self.budget, self.agent = store, budget, agent
        self.pending = {"stdout": b"", "stderr": b""}
        self.pending_tools = set()
        self.size = 0
        self.closed = False
        self.lock = threading.RLock()

    def line(self, channel, raw, partial=False):
        text = raw.decode(errors="replace")
        try:
            event = json.loads(text) if channel == "stdout" and not partial else None
        except ValueError:
            event = None
        if not isinstance(event, dict):
            event = None
        summary = activity(event) if event else {"kind": "text"}
        actions = summary.get("tools", [])
        if summary.get("kind") in {"command_execution", "mcp_tool_call"}:
            actions = [summary]
        for action in actions:
            identity = action.get("id")
            if identity:
                if action.get("phase") == "started":
                    self.pending_tools.add(identity)
                elif action.get("phase") in {"completed", "failed", "cancelled"}:
                    self.pending_tools.discard(identity)
        self.store.append(
            "agent/transcript.jsonl",
            "agent_activity",
            source=self.agent,
            channel=channel,
            activity=summary,
            native_event=event,
            text=None if event else text,
            partial=partial,
            known_tokens=self.budget.tokens,
        )
        path = self.store.path / "agent" / f"{channel}.log"
        with path.open("ab", buffering=0) as stream:
            cleaned = self.store.clean(event) if event else self.store.clean(text)
            stream.write((json.dumps(cleaned, ensure_ascii=False) if event else cleaned).encode())
            if not partial:
                stream.write(b"\n")
            os.fsync(stream.fileno())

    def feed(self, channel, data):
        if channel not in self.pending:
            raise ValueError("Invalid agent output channel")
        with self.lock:
            if self.closed:
                return
            self.size += len(data)
            if self.size > 64 * 1024**2:
                self.store.event("audit_output_truncated", channel=channel, reason="64_MiB_limit")
                self.budget.stop("output_limit")
                self.close()
                return
            self.pending[channel] += data
            while b"\n" in self.pending[channel]:
                line, self.pending[channel] = self.pending[channel].split(b"\n", 1)
                self.line(channel, line)
            if len(self.pending[channel]) > 16 * 1024**2:
                self.pending[channel] = b""
                self.store.event(
                    "audit_output_truncated", channel=channel, reason="16_MiB_line_limit"
                )
                self.budget.stop("output_limit")
                self.close()

    def close(self):
        with self.lock:
            if self.closed:
                return
            for channel, data in self.pending.items():
                if data:
                    self.line(channel, data, partial=True)
            self.pending = {"stdout": b"", "stderr": b""}
            self.store.event(
                "agent_trace_closed",
                pending_tools=sorted(self.pending_tools),
                reason=self.budget.reason,
                output_bytes=self.size,
            )
            self.closed = True


class ResponseTrace:
    """Persist already framed provider chunks; headers and credentials are never captured."""

    def __init__(self, store, relative, limit=64 * 1024**2):
        self.store, self.relative, self.limit = store, relative + ".gz", limit
        path = store.path / self.relative
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("xb", buffering=0)
        self.compressed = gzip.GzipFile(
            filename="", mode="wb", fileobj=self.file, compresslevel=3, mtime=0
        )
        self.size, self.dropped, self.synced = 0, 0, time.monotonic()

    def feed(self, chunk):
        clean = self.store.clean(chunk.decode(errors="replace"))
        record = (
            json.dumps(
                {"elapsed_seconds": self.store.elapsed(), "data": clean}, ensure_ascii=False
            ).encode()
            + b"\n"
        )
        if self.dropped or self.size + len(record) > self.limit:
            self.dropped += len(chunk)
            return
        self.compressed.write(record)
        self.size += len(record)
        if time.monotonic() - self.synced >= 1:
            self.compressed.flush(zlib.Z_SYNC_FLUSH)
            os.fsync(self.file.fileno())
            self.synced = time.monotonic()

    def close(self, complete):
        self.compressed.close()
        os.fsync(self.file.fileno())
        stored_bytes = self.file.tell()
        self.file.close()
        return {
            "path": self.relative,
            "bytes": self.size,
            "stored_bytes": stored_bytes,
            "compression": "gzip",
            "dropped_bytes": self.dropped,
            "complete": complete and not self.dropped,
            "redacted": True,
        }


def read_records(path):
    path = Path(path)
    if not path.exists():
        return
    with gzip.open(path, "rt") if path.suffix == ".gz" else path.open() as stream:
        try:
            for line in stream:
                if not line.endswith("\n"):
                    break
                yield json.loads(line)
        except EOFError:
            # A live/crashed gzip stream may have synced records but no trailer.
            if path.suffix != ".gz":
                raise
