"""Atomic artifacts and a single, serialized, append-only event writer."""

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .audit import sanitize


def utc_now():
    return datetime.now(UTC).isoformat()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(
        path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    )


def read_events(path):
    """Ignore only an uncommitted trailing fragment; reject corruption in complete records."""
    expected = 1
    with Path(path).open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            event = json.loads(line)
            if event["sequence"] != expected:
                raise ValueError("Broken event sequence")
            expected += 1
            yield event


class RunStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.started = None
        self.clock = None
        self.sequence = 0
        self.redact = lambda text: text
        self.events_path = self.path / "events.jsonl"
        if self.events_path.exists():
            events = list(read_events(self.events_path))
            self.sequence = events[-1]["sequence"] if events else 0
            raw = self.events_path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                boundary = raw.rfind(b"\n") + 1
                atomic_write(
                    self.path / "recovery" / f"uncommitted-{uuid.uuid4().hex}.bin", raw[boundary:]
                )
                atomic_write(self.events_path, raw[:boundary])

    def elapsed(self):
        if self.clock is not None:
            return self.clock()
        return max(0.0, time.monotonic() - self.started) if self.started is not None else 0.0

    def event(self, kind, **data):
        with self.lock:
            self.sequence += 1
            event = self.clean(
                {
                    "schema_version": 1,
                    "sequence": self.sequence,
                    "event_id": uuid.uuid4().hex,
                    "run_id": self.path.name,
                    "type": kind,
                    "utc": utc_now(),
                    "elapsed_seconds": self.elapsed(),
                    **data,
                }
            )
            raw = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode()
            with self.events_path.open("ab", buffering=0) as f:
                f.write(raw)
                os.fsync(f.fileno())
            return event

    def write(self, relative, obj):
        write_json(self.path / relative, self.clean(obj))

    def clean(self, obj):
        return sanitize(obj, self.redact)

    def append(self, relative, kind, **data):
        """One durable observation plus a small pointer in the canonical timeline."""
        with self.lock:
            record = self.clean(
                {
                    "schema_version": 1,
                    "sequence": self.sequence + 1,
                    "utc": utc_now(),
                    "elapsed_seconds": self.elapsed(),
                    "type": kind,
                    **data,
                }
            )
            raw = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode()
            path = self.path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab", buffering=0) as stream:
                offset = stream.tell()
                stream.write(raw)
                os.fsync(stream.fileno())
            self.event(kind, artifact=relative, offset=offset, bytes=len(raw))
            return record

    def integrity(self):
        hashes = {}
        for folder in (
            "submissions",
            "requests",
            "downloads",
            "agent",
            "snapshots",
            "native-client",
        ):
            for path in sorted((self.path / folder).rglob("*")):
                if path.is_file() and not path.is_symlink():
                    hashes[str(path.relative_to(self.path))] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
        for name in (
            "manifest.json",
            "events.jsonl",
            "hardware.json",
            "preflight.json",
            "validator-preflight.json",
            "storage-preflight.json",
            "storage-status.json",
            "supervision.json",
            "memory.jsonl",
            "freezer.json",
            "freezer.jsonl",
            "prompt.txt",
            "network-policy.json",
            "summary.json",
            "finalization.json",
            "resources.jsonl",
            "workspace-manifest.json",
            "workspace-usage.json",
            "model-compatibility.json",
            "codex-model-catalog.json",
            "provider-model.json",
            "container-state.json",
            "container-engine.json",
            "audit-policy.json",
            "host-sharing.json",
            "continuation.json",
            "restored-workspace.json",
            "checkpoint.json",
            "provider-recovery.json",
            "provider-transport.json",
            "gateway-stop.json",
            "native-usage.json",
        ):
            path = self.path / name
            if path.exists():
                hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write("integrity.json", {"algorithm": "sha256", "files": hashes})
