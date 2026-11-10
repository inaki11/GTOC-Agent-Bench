"""Lossless per-run sharing of repeated JSON messages and client overlays."""

import gzip
import hashlib
import json
import re
from pathlib import Path

from .artifact_io import read_bytes
from .storage import atomic_write

FORMAT = "gtoc-request-parts-v1"
MAX_BYTES = 256 * 1024**2


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def save_parts(store, folder, name, value):
    """Only equal JSON values share a blob; no cross-run or lossy deduplication."""
    new_bytes, reused = 0, 0

    def save_blob(part):
        nonlocal new_bytes, reused
        raw = canonical(part)
        digest = hashlib.sha256(raw).hexdigest()
        path = store.path / "requests/blobs" / (digest + ".json.gz")
        if path.is_symlink():
            raise ValueError("Request blob cannot be a symlink")
        if path.exists():
            if not path.is_file():
                raise ValueError("Request blob must be a regular file")
            reused += 1
        else:
            compressed = gzip.compress(raw, compresslevel=3, mtime=0)
            atomic_write(path, compressed)
            new_bytes += len(compressed)
        return digest

    field = next((key for key in ("input", "messages") if isinstance(value.get(key), list)), None)
    envelope = {key: part for key, part in value.items() if key != field}
    raw = canonical(value)
    manifest = {
        "format": FORMAT,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "logical_bytes": len(raw),
        "envelope": save_blob(envelope),
        "sequence": None
        if field is None
        else {
            "field": field,
            "parts": [save_blob(part) for part in value[field]],
        },
    }
    encoded = canonical(manifest) + b"\n"
    relative = f"{folder}/{name}.manifest.json"
    atomic_write(store.path / relative, encoded)
    return {
        "path": relative,
        "format": FORMAT,
        "compression": "gzip_parts",
        "logical_bytes": len(raw),
        "stored_bytes": len(encoded) + new_bytes,
        "stored_bytes_scope": "manifest_and_new_blobs_shared_with_other_requests_in_this_run",
        "reused_parts": reused,
        "sha256": manifest["sha256"],
    }


def save_requests(store, folder, client, provider):
    client, provider = store.clean(client), store.clean(provider)
    base = store.path / folder

    effective = save_parts(store, folder, "request", provider)
    overlay = {
        "format": "gtoc-request-overlay-v1",
        "base": "request.manifest.json",
        "set": {
            key: value
            for key, value in client.items()
            if key not in provider or provider[key] != value
        },
        "remove": sorted(set(provider) - set(client)),
    }
    encoded = canonical(overlay)
    # A protocol conversion may change the whole schema; then keep a full copy.
    if len(encoded) < min(len(canonical(client)), 4096):
        atomic_write(base / "client_request.json", encoded + b"\n")
        original = {
            "path": f"{folder}/client_request.json",
            "format": overlay["format"],
            "stored_bytes": len(encoded) + 1,
        }
    else:
        original = save_parts(store, folder, "client_request", client)
    return {"provider": effective, "client": original}


def read_request(directory, *, client=False):
    """Read new compact artifacts or legacy plain JSON; never execute contents."""
    directory = Path(directory)
    name = "client_request" if client else "request"
    manifest = directory / (name + ".manifest.json")
    if manifest.exists() or manifest.is_symlink():
        return read_parts(directory, json.loads(read_bytes(manifest, limit=MAX_BYTES)))
    compressed = directory / (name + ".json.gz")
    if compressed.exists() or compressed.is_symlink():
        return json.loads(read_bytes(compressed, limit=MAX_BYTES, compressed=True))
    value = json.loads(read_bytes(directory / (name + ".json"), limit=MAX_BYTES))
    if client and value.get("format") == "gtoc-request-overlay-v1":
        if value.get("base") not in {"request.json.gz", "request.manifest.json"}:
            raise ValueError("Invalid request overlay base")
        result = read_request(directory)
        for key in value["remove"]:
            result.pop(key, None)
        result.update(value["set"])
        return result
    return value


def read_parts(directory, manifest):
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unknown request manifest format")
    logical_bytes = manifest.get("logical_bytes")
    if type(logical_bytes) is not int or not 0 <= logical_bytes <= MAX_BYTES:
        raise ValueError("Invalid logical request size")
    total = 0

    def load(digest):
        nonlocal total
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid request blob digest")
        path = directory.parent / "blobs" / (digest + ".json.gz")
        raw = read_bytes(path, limit=MAX_BYTES - total, compressed=True)
        total += len(raw)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Request blob hash does not match")
        return json.loads(raw)

    value = load(manifest["envelope"])
    if not isinstance(value, dict):
        raise ValueError("Request envelope must be an object")
    sequence = manifest["sequence"]
    if sequence is not None:
        if (
            not isinstance(sequence, dict)
            or sequence.get("field") not in {"input", "messages"}
            or not isinstance(sequence.get("parts"), list)
        ):
            raise ValueError("Invalid request sequence")
        if sequence["field"] in value:
            raise ValueError("Request sequence duplicates an envelope field")
        value[sequence["field"]] = [load(digest) for digest in sequence["parts"]]
    raw = canonical(value)
    if len(raw) != logical_bytes or hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
        raise ValueError("Reconstructed request hash does not match")
    return value
