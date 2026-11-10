"""Experimental Antigravity OAuth bridge; the private proxy runs only on the host.

Google does not support consumer subscription access from third-party clients.
This integration is explicitly identified as CLIProxyAPI, not an official API.
The official Antigravity SDK agent is independently configured in python_agents.
"""

import hashlib
import io
import json
import os
import platform
import secrets
import select
import signal
import stat
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path

import httpx

from . import ROOT
from .auth import ApiKeyCredential, AuthError, Profiles, auth_path, checked_profile
from .storage import utc_now

DEFAULT_DIR = "~/.config/gtoc-bench/antigravity"


def pin():
    return json.loads((ROOT / "environments/providers.lock.json").read_text())["cliproxyapi"]


def runtime_dir():
    return auth_path(
        Path("~/.cache/gtoc-bench/providers/cliproxyapi").expanduser() / pin()["version"]
    )


def installed_binary():
    directory = runtime_dir()
    with Profiles(directory).lock():
        metadata = Profiles(directory).read("install.json")
        binary = directory / "cliproxyapi"
        if not metadata or not binary.exists():
            raise AuthError("Install the pinned bridge first: gtoc install-provider antigravity")
        info = binary.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or metadata.get("version") != pin()["version"]
            or hashlib.sha256(binary.read_bytes()).hexdigest() != metadata["binary_sha256"]
        ):
            raise AuthError("The installed Antigravity bridge changed; reinstall it")
    return binary


def install():
    release = pin()
    system = platform.system() + "-" + platform.machine()
    asset = release["archives"].get(system)
    if asset is None:
        raise ValueError("No pinned CLIProxyAPI build for this platform")
    directory = runtime_dir()
    profiles = Profiles(directory)
    with profiles.lock():
        url = (
            "https://github.com/router-for-me/CLIProxyAPI/releases/download/v"
            + release["version"]
            + "/"
            + asset["name"]
        )
        with httpx.Client(follow_redirects=True, timeout=120, trust_env=False) as client:
            response = client.get(url)
            response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != asset["sha256"]:
            raise ValueError("CLIProxyAPI archive checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
            members = [
                m
                for m in archive.getmembers()
                if m.name in {"cli-proxy-api", "cliproxyapi", "CLIProxyAPI"} and m.isfile()
            ]
            if len(members) != 1 or members[0].size > 200 * 1024**2:
                raise ValueError("Unexpected CLIProxyAPI archive layout")
            stream = archive.extractfile(members[0])
            data = stream.read()
        # Extract only the verified regular executable, never archive paths/links.
        temporary = directory / ".binary.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o700)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, directory / "cliproxyapi")
        finally:
            temporary.unlink(missing_ok=True)
        profiles.write(
            {
                "version": release["version"],
                "archive_sha256": asset["sha256"],
                "binary_sha256": hashlib.sha256(data).hexdigest(),
                "installed_at": utc_now(),
            },
            "install.json",
        )
    return str(directory / "cliproxyapi")


def profile_store(directory=DEFAULT_DIR, profile="default"):
    return Profiles(auth_path(Path(directory).expanduser()) / checked_profile(profile))


def proxy_config(profiles, record, keepalive_seconds=15):
    return {
        "config-version": 8,
        "server": {"host": "127.0.0.1", "port": record["port"], "discovery": {"enabled": False}},
        "management": {
            "allow-remote": False,
            "secret-key": "",
            "disable-control-panel": True,
            "disable-auto-update-panel": True,
        },
        "access": {"api-keys": [record["proxy_api_key"]]},
        "oauth": {
            "auth-dir": str(profiles.directory / "accounts"),
            "providers": {"antigravity": {"antigravity-credits": False}},
        },
        "routing": {
            "strategy": "fill-first",
            "retry": {"request-retry": 0, "max-retry-credentials": 1, "max-retry-interval": 0},
        },
        "requests": {
            "streaming": {
                "bootstrap-retries": 0,
                "keepalive-seconds": keepalive_seconds,
                "send-keepalive-before-ft": True,
            },
            "nonstream-keepalive-interval": keepalive_seconds,
        },
        "quota-exceeded": {"switch-project": False, "switch-preview-model": False},
        "observability": {"logs": {"debug": False, "logging-to-file": False, "request-log": False}},
    }


def prepare_profile(profiles, port=8317):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("Antigravity bridge port must be between 1024 and 65535")
    with profiles.lock():
        record = profiles.read()
        if record and record["port"] != port:
            raise ValueError("Profile already has a different port; use another profile")
        if not record:
            record = {
                "port": port,
                "proxy_api_key": secrets.token_urlsafe(48),
                "created_at": utc_now(),
            }
            profiles.write(record)
        # JSON is also valid YAML. Private Profiles writes avoid broad permissions.
        profiles.write(proxy_config(profiles, record), "proxy.yaml")
        accounts = profiles.directory / "accounts"
        Profiles(accounts).prepare()
    return record


def account_files(profiles):
    directory = profiles.directory / "accounts"
    if not directory.exists():
        return []
    Profiles(directory).prepare()
    files = list(directory.glob("*.json"))
    for file in files:
        info = file.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise AuthError("Antigravity credentials must be private regular files (chmod 600)")
        metadata = Profiles(directory).read(file.name)
        if metadata.get("type") != "antigravity":
            raise AuthError("Only Antigravity credentials are allowed in this bridge profile")
    if len(files) > 1:
        raise AuthError(
            "One Antigravity account per profile is required; account rotation is disabled"
        )
    return files


def account_identity(profiles):
    files = account_files(profiles)
    if not files:
        return None
    record = Profiles(profiles.directory / "accounts").read(files[0].name)
    return hashlib.sha256(str(record.get("email") or files[0].name).encode()).hexdigest()


def public_status(profiles):
    record = profiles.read()
    return {
        "profile": profiles.directory.name,
        "configured": bool(record),
        "account_present": bool(account_files(profiles)),
        "experimental": True,
        "bridge": "CLIProxyAPI",
        "bridge_version": pin()["version"],
        "base_url": f"http://127.0.0.1:{record['port']}/v1" if record else None,
        "subscription_pass_through_officially_supported": False,
    }


class AntigravityCredential(ApiKeyCredential):
    def __init__(
        self, directory, profile, base_url=None, allow_empty=False, *, keepalive_seconds=15
    ):
        self.profiles = profile_store(directory, profile)
        self.allow_empty = allow_empty
        self.account_id = account_identity(self.profiles)
        self.child_pid = None
        self.heartbeat_stop = threading.Event()
        self.heartbeat_thread = None
        self.process, self.lease = None, self.profiles.lock(timeout=1)
        self.lease.__enter__()
        try:
            record = self.profiles.read()
            if not record:
                raise AuthError("Sign in first: gtoc auth login antigravity")
            super().__init__(record["proxy_api_key"])
            self.base_url = f"http://127.0.0.1:{record['port']}/v1"
            if base_url is not None and base_url != self.base_url:
                raise AuthError("provider.base_url does not match this private Antigravity profile")
            if not account_files(self.profiles) and not allow_empty:
                raise AuthError("No Antigravity account; run gtoc auth login antigravity")
            expected = proxy_config(self.profiles, record, keepalive_seconds)
            current = self.profiles.read("proxy.yaml")
            try:
                prior_interval = (
                    current.get("requests", {}).get("streaming", {}).get("keepalive-seconds")
                )
            except AttributeError:
                prior_interval = None
            legacy = proxy_config(self.profiles, record, 0)
            legacy["requests"] = {"streaming": {"bootstrap-retries": 0, "keepalive-seconds": 0}}
            recognized = current == legacy or (
                type(prior_interval) is int
                and prior_interval >= 0
                and current == proxy_config(self.profiles, record, prior_interval)
            )
            if not recognized:
                raise AuthError(
                    "Antigravity proxy configuration changed; run login to prepare it again"
                )
            if current != expected:
                # Only our exact old/generated policies may migrate, under the
                # exclusive profile lease. Never accept unrelated config edits.
                self.profiles.write(expected, "proxy.yaml")
            self.keepalive_seconds = keepalive_seconds
            binary = installed_binary()
            # Fail before launching if another service occupies the fixed private port.
            import socket

            with socket.socket() as probe:
                probe.bind(("127.0.0.1", record["port"]))
            self.process = subprocess.Popen(
                [sys.executable, "-m", "gtoc_bench.antigravity"],
                cwd=self.profiles.directory,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            spec = {
                "command": [
                    str(binary),
                    "-config",
                    str(self.profiles.directory / "proxy.yaml"),
                    "-local-model",
                ],
                "cwd": str(self.profiles.directory),
                "log": str(self.profiles.directory / "proxy.log"),
                "env": {
                    "PATH": os.defpath,
                    "HOME": str(self.profiles.directory),
                    "LANG": "C.UTF-8",
                },
            }
            self.process.stdin.write((json.dumps(spec) + "\n").encode())
            self.process.stdin.flush()
            ready, _, _ = select.select([self.process.stdout], [], [], 10)
            if not ready:
                raise AuthError("Antigravity bridge supervisor startup timed out")
            frame = json.loads(self.process.stdout.readline(4096))
            self.child_pid = frame["pid"]
            self.heartbeat_thread = threading.Thread(target=self.heartbeat, daemon=True)
            self.heartbeat_thread.start()
            with httpx.Client(timeout=1, trust_env=False) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise AuthError(
                            "Antigravity bridge exited during startup; inspect its private proxy.log"
                        )
                    try:
                        response = client.get(
                            self.base_url + "/models",
                            headers={"Authorization": "Bearer " + self.token()},
                        )
                        if response.status_code == 200 and (
                            allow_empty or response.json().get("data")
                        ):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise AuthError("Antigravity bridge startup timed out", "provider_unavailable")
        except Exception:
            self.close()
            raise

    def public_info(self):
        return {
            "auth_method": "antigravity",
            "billing_mode": "upstream_subscription_or_quota",
            "experimental": True,
            "bridge": "CLIProxyAPI",
            "bridge_version": pin()["version"],
            "bridge_binary_sha256": Profiles(runtime_dir()).read("install.json")["binary_sha256"],
            "profile": self.profiles.directory.name,
            "upstream_observability": "translated_proxy_responses_only",
            "upstream_standard_speed_verified": False,
            "account_id_sha256": self.account_id,
            "keepalive_seconds": self.keepalive_seconds,
            "keepalive_before_first_token": self.keepalive_seconds > 0,
        }

    def token(self, rejected_token=None):
        if self.heartbeat_stop.is_set():
            raise AuthError("Antigravity bridge is closing", "provider_unavailable")
        if not account_files(self.profiles) and not self.allow_empty:
            raise AuthError("The Antigravity account was removed; sign in again")
        if account_identity(self.profiles) != self.account_id:
            raise AuthError("The Antigravity account changed during this run")
        if self.process is not None and self.process.poll() is not None:
            raise AuthError("Antigravity bridge stopped", "provider_unavailable")
        return super().token()

    def cancel(self):
        if self.heartbeat_stop.is_set():
            return False
        self.heartbeat_stop.set()
        if self.child_pid is not None:
            try:
                os.killpg(self.child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if self.process is not None and self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return True

    def close(self):
        self.cancel()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=2)
        try:
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.stdin.close()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if self.child_pid is not None:
                            try:
                                os.killpg(self.child_pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        os.killpg(self.process.pid, signal.SIGKILL)
                        self.process.wait(timeout=5)
                except ProcessLookupError:
                    self.process.wait(timeout=5)
        finally:
            if self.process is not None:
                if self.process.stdin and not self.process.stdin.closed:
                    self.process.stdin.close()
                if self.process.stdout:
                    self.process.stdout.close()
            if self.lease is not None:
                self.lease.__exit__(None, None, None)
                self.lease = None

    def heartbeat(self):
        while not self.heartbeat_stop.wait(1):
            try:
                self.process.stdin.write(b"heartbeat\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return


def auth_command(args):
    profiles = profile_store(args.auth_dir or DEFAULT_DIR, args.profile)
    if args.auth_action == "login":
        prepare_profile(profiles, args.bridge_port)
        with profiles.lock():
            if account_files(profiles):
                raise ValueError(
                    "This profile already contains an account; log out before signing in again"
                )
            command = [
                str(installed_binary()),
                "-config",
                str(profiles.directory / "proxy.yaml"),
                "-antigravity-login",
            ]
            if args.no_browser:
                command += ["-no-browser"]
            if args.port != 1455:
                command += ["-oauth-callback-port", str(args.port)]
            result = subprocess.run(
                command,
                cwd=profiles.directory,
                check=False,
                env={
                    "PATH": os.environ.get("PATH", os.defpath),
                    "HOME": str(profiles.directory),
                    "LANG": "C.UTF-8",
                },
            )
            if result.returncode:
                raise AuthError("Antigravity login failed")
            account_files(profiles)
    elif args.auth_action == "logout":
        with profiles.lock():
            for file in account_files(profiles):
                file.unlink()
        # Removing local auth does not revoke consent at Google.
    elif args.auth_action == "models":
        credential = AntigravityCredential(args.auth_dir or DEFAULT_DIR, args.profile)
        try:
            with httpx.Client(timeout=30, trust_env=False) as client:
                result = client.get(
                    credential.base_url + "/models",
                    headers={"Authorization": "Bearer " + credential.token()},
                )
                result.raise_for_status()
                print(json.dumps(result.json(), indent=2))
                return 0
        finally:
            credential.close()
    print(json.dumps(public_status(profiles), indent=2))
    return 0


def proxy_guard():
    """Stop the host proxy on controller EOF or missed heartbeat, even after SIGKILL."""
    spec = json.loads(sys.stdin.buffer.readline(1024**2))
    log = os.open(spec["log"], os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        child = subprocess.Popen(
            spec["command"],
            cwd=spec["cwd"],
            env=spec["env"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
            umask=0o077,
        )
    finally:
        os.close(log)
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    last = time.monotonic()
    print(json.dumps({"pid": child.pid}), flush=True)
    try:
        while child.poll() is None and not stopping.is_set() and time.monotonic() - last < 10:
            ready, _, _ = select.select([sys.stdin], [], [], 0.25)
            if ready:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    break
                if b"heartbeat\n" in data:
                    last = time.monotonic()
    finally:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=2)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    proxy_guard()
