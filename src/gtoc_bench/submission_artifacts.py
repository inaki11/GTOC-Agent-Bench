"""Store the exact submitted bytes compressed; retain legacy candidate readers."""

import gzip
import hashlib
from pathlib import Path

from .artifact_io import read_bytes
from .storage import atomic_write


def save_submission(directory, data):
    compressed = gzip.compress(data, compresslevel=3, mtime=0)
    atomic_write(Path(directory) / "solution.txt.gz", compressed)
    return {
        "path": "solution.txt.gz",
        "compression": "gzip",
        "logical_bytes": len(data),
        "stored_bytes": len(compressed),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def read_submission(directory, *, limit=100_000_000):
    """Return original bytes; callers can compare them with receipt/verdict hashes."""
    directory = Path(directory)
    path = directory / "solution.txt.gz"
    if path.exists() or path.is_symlink():
        return read_bytes(path, limit=limit, compressed=True)
    return read_bytes(directory / "solution.txt", limit=limit)
