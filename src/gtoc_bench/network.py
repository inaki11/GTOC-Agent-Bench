"""Explicit documentation routes and a small PyPI mirror; never an open HTTP proxy."""

import hashlib
import html
import ipaddress
import json
import socket
from urllib.parse import unquote, urlsplit

import httpx

from .storage import atomic_write

DOC_PREFIXES = (
    "https://docs.python.org/3/",
    "https://numpy.org/doc/",
    "https://docs.scipy.org/doc/",
    "https://docs.pytorch.org/docs/",
    "https://esa.github.io/pykep/documentation/",
    "https://esa.github.io/kep3/",
    "https://docs.sympy.org/latest/",
    "https://numba.readthedocs.io/en/stable/",
    "https://ziglang.org/documentation/0.17.0/",
    "https://web.casadi.org/docs/",
    "https://cyipopt.readthedocs.io/en/stable/",
    "https://coin-or.github.io/Ipopt/",
    "https://nlopt.readthedocs.io/en/latest/",
    "https://pybind11.readthedocs.io/en/stable/",
)
PACKAGES = frozenset(
    {
        "numpy",
        "scipy",
        "sympy",
        "mpmath",
        "numba",
        "llvmlite",
        "pandas",
        "matplotlib",
        "contourpy",
        "cycler",
        "fonttools",
        "kiwisolver",
        "pillow",
        "pybind11",
        "pyparsing",
        "python-dateutil",
        "six",
        "pytz",
        "tzdata",
        "networkx",
        "packaging",
        "tqdm",
        "joblib",
        "threadpoolctl",
        "scikit-learn",
        "cma",
        "setuptools",
        "wheel",
        "ziglang",
        "casadi",
        "cyipopt",
        "cython",
    }
)


class PackageDenied(ValueError):
    """An intentional policy refusal, not a missing PyPI distribution."""


def allowed_document(url):
    parsed = urlsplit(url)
    decoded = unquote(parsed.path).lower()
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.port in (None, 443)
        and not any(s in decoded for s in ("..", "gtoc", "solution", "tutorial", "example"))
        and any(url.startswith(prefix) for prefix in DOC_PREFIXES)
    )


def public_destination(url):
    hostname = urlsplit(url).hostname
    addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Non-public destination blocked")


class Internet:
    def __init__(self, store, stopped):
        self.store = store
        self.stopped = stopped
        self.files = {}
        self.client = httpx.Client(timeout=30, trust_env=False)

    def document(self, url):
        for _ in range(4):
            if not allowed_document(url):
                raise ValueError("Documentation URL is outside the fixed policy")
            public_destination(url)
            with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024**2 or self.stopped.is_set():
                        raise ValueError("Document exceeds limit or run has stopped")
                    chunks.append(chunk)
            data = b"".join(chunks)
            digest = hashlib.sha256(data).hexdigest()
            atomic_write(self.store.path / "downloads/docs" / (digest + ".txt"), data)
            self.store.event("document_read", url=url, sha256=digest)
            return data
        raise ValueError("Too many redirects")

    def index(self, name):
        name = name.lower().replace("_", "-")
        if name not in PACKAGES:
            raise PackageDenied(
                f"Package '{name}' is not approved by GTOC-Bench; run 'gtoc packages' for the permitted list"
            )
        response = self.client.get(f"https://pypi.org/pypi/{name}/json")
        response.raise_for_status()
        metadata = response.json()
        links = []
        for release in metadata["releases"].values():
            for entry in release:
                url, digest, filename = entry["url"], entry["digests"]["sha256"], entry["filename"]
                if (
                    entry.get("yanked")
                    or urlsplit(url).hostname != "files.pythonhosted.org"
                    or not url.startswith("https://")
                ):
                    continue
                path = f"/packages/files/{digest}/{filename}"
                self.files[path] = (url, digest, filename)
                requires = html.escape(entry.get("requires_python") or "", quote=True)
                links.append(
                    f'<a href="{html.escape(path)}#sha256={digest}" data-requires-python="{requires}">{html.escape(filename)}</a>'
                )
        self.store.event("package_index", package=name)
        return ("<!DOCTYPE html><html><body>" + "\n".join(links) + "</body></html>").encode()

    def package(self, path, emit):
        if path not in self.files:
            raise ValueError("Package file was not issued by the approved index")
        url, expected, filename = self.files[path]
        hasher, size = hashlib.sha256(), 0
        with self.client.stream("GET", url) as response:
            response.raise_for_status()
            for data in response.iter_bytes(32768):
                size += len(data)
                if size > 512 * 1024**2 or self.stopped.is_set():
                    raise ValueError("Package transfer stopped")
                hasher.update(data)
                emit(data)
        if hasher.hexdigest() != expected:
            raise ValueError("Package checksum mismatch")
        self.store.event("package_download", filename=filename, sha256=expected, bytes=size)

    def policy(self):
        return {
            "schema_version": 1,
            "documentation_prefixes": DOC_PREFIXES,
            "packages": sorted(PACKAGES),
            "sha256": hashlib.sha256(
                json.dumps([DOC_PREFIXES, sorted(PACKAGES)]).encode()
            ).hexdigest(),
        }
