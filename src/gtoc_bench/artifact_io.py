"""Bounded, non-executable reads of plain or gzip audit artifacts."""

import gzip
import os
import stat
from pathlib import Path


def read_bytes(path, *, limit, compressed=False):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Audit artifacts cannot follow symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("Audit artifacts must be regular files")
        if compressed:
            with gzip.GzipFile(fileobj=source) as stream:
                data = stream.read(limit + 1)
        else:
            data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Audit artifact exceeds its logical size limit")
    return data
