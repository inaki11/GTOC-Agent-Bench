"""Read historical full snapshots and v2 deltas on demand, without executing artifacts."""

import hashlib
import json
from pathlib import Path, PurePosixPath


def read_snapshot(run, artifact):
    root = Path(run).resolve()

    def path(relative):
        parts = PurePosixPath(relative)
        if parts.is_absolute() or ".." in parts.parts or parts.parts[:1] != ("snapshots",):
            raise ValueError("Invalid snapshot reference")
        target = root.joinpath(*parts.parts)
        for entry in [target, *target.parents]:
            if entry == root:
                break
            if entry.is_symlink():
                raise ValueError("Snapshot references cannot follow symlinks")
        return target

    chain, seen = [], set()
    current = artifact
    while current:
        if current in seen or len(chain) >= 10000:
            raise ValueError("Cyclic or excessive snapshot chain")
        seen.add(current)
        chain.append(current)
        data = json.loads(path(current).read_text())
        current = data.get("base_snapshot") if data.get("layout") == "delta" else None
        if data.get("layout") == "delta" and not current:
            raise ValueError("Delta snapshot has no base")
    files = {}
    for relative in reversed(chain):
        manifest = path(relative)
        data = json.loads(manifest.read_text())
        if data.get("layout") != "delta":
            files.clear()
        for removed in data.get("removed", []):
            files.pop(removed, None)
        for entry in data.get("files", []):
            entry = dict(entry)
            if data.get("schema_version", 1) == 1 and entry.get("original_sha256"):
                name = hashlib.sha256(entry["path"].encode()).hexdigest() + ".json"
                sidecar = str((manifest.parent / "files" / name).relative_to(root))
                if path(sidecar).is_file():
                    entry.update(json.loads(path(sidecar).read_text()))
            files[entry["path"]] = entry
    return {
        **data,
        "files": files,
        "source_artifact": artifact,
        "chain_length": len(chain),
        "view": "materialized_known_files",
    }
