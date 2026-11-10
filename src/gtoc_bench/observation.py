"""Read-only observation inside the trusted supervisor; never follows agent symlinks."""

import errno
import hashlib
import os
import queue
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path

EXCLUDED = {".local", ".codex", ".claude", ".git", "__pycache__", ".venv", "node_modules"}
MAX_FILE = 256 * 1024
MAX_SNAPSHOT = 4 * 1024**2
MAX_ENTRIES = 20000


def workspace_usage(root, stop=None):
    """Logical bytes of regular files, with explicit traversal completeness.

    Includes agent caches and installed packages. Symlinks are never followed;
    hard-linked paths are counted separately, as in the final file inventory.
    A cancelled/failed scan is diagnostic data, never a replacement measurement.
    """
    started = time.monotonic()
    result = {
        "bytes": 0,
        "files": 0,
        "complete": True,
        "symlinks_followed": False,
        "metric": "logical_regular_file_bytes",
        "errors": [],
    }

    def error(exc):
        result["complete"] = False
        if len(result["errors"]) < 20:
            result["errors"].append({"errno": exc.errno, "path": str(exc.filename)})

    for _, _, files, fd in os.fwalk(root, follow_symlinks=False, onerror=error):
        for name in files:
            if stop is not None and stop.is_set():
                result.update(complete=False, cancelled=True)
                break
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # A live file disappeared; the traversal is still complete.
            except OSError as exc:
                error(exc)
                continue
            if stat.S_ISREG(info.st_mode):
                result["bytes"] += info.st_size
                result["files"] += 1
        if result.get("cancelled") or (stop is not None and stop.is_set()):
            result.update(complete=False, cancelled=True)
            break
    result["seconds"] = time.monotonic() - started
    return result


def regular_files(root, excluded=(), omitted=None, limit=MAX_ENTRIES):
    omitted = omitted if omitted is not None else []
    examined = 0

    # fwalk's directory descriptors and O_NOFOLLOW also guard against symlink swaps.
    def error(exc):
        omitted.append({"path": str(exc.filename), "reason": "directory_unreadable"})

    for directory, dirs, files, fd in os.fwalk(root, follow_symlinks=False, onerror=error):
        examined += len(dirs) + len(files)
        if examined > limit:
            omitted.append(
                {"path": str(Path(directory).relative_to(root)), "reason": "entry_limit"}
            )
            return
        dirs[:] = sorted(d for d in dirs if d not in excluded)
        for name in sorted(files):
            relative = str((Path(directory) / name).relative_to(root))
            file = None
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                file = os.fdopen(descriptor, "rb")
                info = os.fstat(file.fileno())
                if stat.S_ISREG(info.st_mode):
                    yield relative, file, info
                else:
                    omitted.append({"path": relative, "reason": "not_regular_file"})
            except OSError as exc:
                omitted.append(
                    {"path": relative, "reason": "unreadable_or_symlink", "errno": exc.errno}
                )
                continue
            finally:
                if file is not None:
                    file.close()


class WorkspaceObserver:
    def __init__(self, root="/workspace"):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.previous = {}
        self.previous_id = None
        self.sent_content = set()
        self.cache = {}

    def capture(self, send, reason, submission_id=None, triggers=None, stop=None):
        with self.lock:
            snapshot_id = uuid.uuid4().hex
            files, size, truncated, omitted = {}, 0, False, []
            cancelled = False
            for relative, stream, info in regular_files(self.root, EXCLUDED, omitted):
                if stop is not None and stop.is_set():
                    cancelled = True
                    break
                entry = {"path": relative, "size": info.st_size}
                if info.st_size > MAX_FILE:
                    files[relative] = entry | {"omitted": "file_size_limit"}
                    continue
                signature = (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ctime_ns,
                )
                cached = self.cache.get(relative)
                if cached and cached[0] == signature:
                    files[relative] = cached[1]
                    continue
                if size + info.st_size > MAX_SNAPSHOT:
                    files[relative] = entry | {"omitted": "snapshot_size_limit"}
                    truncated = True
                    continue
                data = stream.read(min(MAX_FILE + 1, MAX_SNAPSHOT - size))
                size += len(data)
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns) != (info.st_size, info.st_mtime_ns):
                    files[relative] = entry | {"omitted": "changed_during_read"}
                    continue
                try:
                    content = data.decode("utf-8")
                except UnicodeError:
                    content = None
                if content is None or "\x00" in content:
                    files[relative] = entry | {"omitted": "binary"}
                    self.cache[relative] = (signature, files[relative])
                    continue
                digest = hashlib.sha256(data).hexdigest()
                if digest not in self.sent_content:
                    send(
                        "snapshot_file",
                        snapshot_id=snapshot_id,
                        path=relative,
                        original_sha256=digest,
                        content=content,
                    )
                    self.sent_content.add(digest)
                files[relative] = entry | {"original_sha256": digest}
                self.cache[relative] = (signature, files[relative])
            # An incomplete traversal cannot establish that unseen paths disappeared.
            complete = not cancelled and not any(
                e["reason"] in {"entry_limit", "directory_unreadable"}
                or (e["reason"] == "unreadable_or_symlink" and e.get("errno") != errno.ELOOP)
                for e in omitted
            )
            removed = sorted(set(self.previous) - set(files)) if complete else []
            changes = [entry for path, entry in files.items() if self.previous.get(path) != entry]
            current = files if complete else self.previous | files
            triggers = triggers or [{"reason": reason, "submission_id": submission_id, "count": 1}]
            submission_ids = sorted(
                {t["submission_id"] for t in triggers if t.get("submission_id")}
            )
            send(
                "snapshot",
                snapshot_id=snapshot_id,
                reason=reason,
                submission_id=submission_id,
                schema_version=2,
                layout="delta" if self.previous_id else "full",
                base_snapshot_id=self.previous_id,
                files=changes,
                removed=removed,
                file_count=len(current),
                observed_file_count=len(files),
                capture_complete=complete,
                cancelled=cancelled,
                triggers=triggers,
                submission_ids=submission_ids,
                coalesced_requests=sum(t["count"] for t in triggers),
                truncated=truncated or not complete,
                omitted=omitted,
                excluded_directories=sorted(EXCLUDED),
                max_file_bytes=MAX_FILE,
                max_snapshot_bytes=MAX_SNAPSHOT,
                byte_limit_scope="uncached_bytes_read_per_capture",
                consistency="post_validation_observed_without_pausing_agent"
                if reason == "submission"
                else "observed_without_pausing_agent",
            )
            self.previous, self.previous_id = current, snapshot_id
            if complete:
                self.cache = {path: cached for path, cached in self.cache.items() if path in files}


class SnapshotRequests:
    """One pending capture, combining triggers without losing submission IDs."""

    def __init__(self, limit=256):
        self.limit = limit
        self.pending = {}
        self.condition = threading.Condition()

    def request(self, reason, submission_id=None):
        with self.condition:
            key = (reason, submission_id)
            now = time.monotonic()
            if key not in self.pending:
                if len(self.pending) >= self.limit:
                    raise queue.Full
                self.pending[key] = {
                    "reason": reason,
                    "submission_id": submission_id,
                    "count": 0,
                    "first_requested_monotonic": now,
                }
            trigger = self.pending[key]
            trigger["count"] += 1
            trigger["last_requested_monotonic"] = now
            self.condition.notify()

    def take(self, timeout=0.25):
        with self.condition:
            if not self.condition.wait_for(lambda: bool(self.pending), timeout=timeout):
                raise queue.Empty
            triggers = list(self.pending.values())
            self.pending.clear()
            return triggers

    def discard(self):
        with self.condition:
            triggers = list(self.pending.values())
            self.pending.clear()
            return triggers


def resource_sample():
    result = {"observer_monotonic": time.monotonic(), "cgroup": {}, "processes": []}
    for name in (
        "cpu.stat",
        "memory.current",
        "memory.peak",
        "memory.events",
        "pids.current",
        "io.stat",
    ):
        try:
            result["cgroup"][name] = Path("/sys/fs/cgroup", name).read_text().strip()
        except OSError:
            result["cgroup"][name] = None
    for path in sorted(Path("/proc").iterdir()):
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid != 1000:
                continue
            status = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines())
            result["processes"].append(
                {
                    "pid": int(path.name),
                    "name": status.get("Name", "").strip(),
                    "state": status.get("State", "").strip(),
                    "rss": status.get("VmRSS", "").strip(),
                    "threads": status.get("Threads", "").strip(),
                }
            )
        except (OSError, ValueError):
            continue
    # NVIDIA counters may be device-wide; do not attribute shared GPU usage to this run.
    try:
        query = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=uuid,utilization.gpu,memory.used,memory.total,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        result["gpu"] = {
            "scope": "visible_device",
            "csv": query.stdout[:16384],
            "returncode": query.returncode,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["gpu"] = {"scope": "visible_device", "unavailable": type(exc).__name__}
    return result


def workspace_inventory(root):
    """Hash final files after the agent has stopped; no copies of session secrets."""
    files, omitted = {}, []
    truncated = False
    for relative, stream, info in regular_files(Path(root), omitted=omitted, limit=100000):
        if len(files) >= 100000:
            truncated = True
            break
        digest = hashlib.sha256()
        while chunk := stream.read(1024**2):
            digest.update(chunk)
        files[relative] = {"sha256": digest.hexdigest(), "bytes": info.st_size}
    return {
        "files": files,
        "omitted": omitted,
        "truncated": truncated or any(e["reason"] == "entry_limit" for e in omitted),
        "symlinks_followed": False,
    }
