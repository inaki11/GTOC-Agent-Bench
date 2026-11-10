import hashlib
import json
import socket
import subprocess
import sys
import time

import httpx
import pytest
import yaml

from gtoc_bench import ROOT
from gtoc_bench import antigravity as ag
from gtoc_bench.auth import AuthError, Profiles
from gtoc_bench.budget import Budget
from gtoc_bench.config import load_config, public_config
from gtoc_bench.gateway import Gateway
from gtoc_bench.storage import RunStore


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def profile(tmp_path):
    profiles = ag.profile_store(tmp_path / "private", "test")
    ag.prepare_profile(profiles, free_port())
    Profiles(profiles.directory / "accounts").write(
        {
            "type": "antigravity",
            "email": "synthetic@example.invalid",
            "access_token": "fake-google-token",
            "refresh_token": "fake-google-refresh",
        },
        "account.json",
    )
    return profiles


@pytest.fixture
def fake_binary(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ag, "runtime_dir", lambda: runtime)
    profiles = Profiles(runtime)
    profiles.prepare()
    binary = runtime / "cliproxyapi"
    binary.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """
import json,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
config=json.load(open(sys.argv[sys.argv.index('-config')+1]))
key=config['access']['api-keys'][0]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  allowed=self.headers.get('Authorization')=='Bearer '+key
  self.send_response(200 if allowed else 401); self.end_headers()
  self.wfile.write(json.dumps({'data':[{'id':'gemini-test'},{'id':'partner-claude-test'}]} if allowed else {'error':'unauthorized'}).encode())
HTTPServer(('127.0.0.1',config['server']['port']),Handler).serve_forever()
"""
    )
    binary.chmod(0o700)
    profiles.write(
        {
            "version": ag.pin()["version"],
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        },
        "install.json",
    )
    return binary


def test_bridge_is_private_single_account_and_no_fallback(profile):
    record = profile.read()
    config = profile.read("proxy.yaml")
    assert config["server"]["host"] == "127.0.0.1"
    assert (
        config["management"]["secret-key"] == "" and config["management"]["disable-control-panel"]
    )
    assert config["routing"]["retry"] == {
        "request-retry": 0,
        "max-retry-credentials": 1,
        "max-retry-interval": 0,
    }
    assert not config["oauth"]["providers"]["antigravity"]["antigravity-credits"]
    assert not config["quota-exceeded"]["switch-preview-model"]
    assert config["requests"]["streaming"] == {
        "bootstrap-retries": 0,
        "keepalive-seconds": 15,
        "send-keepalive-before-ft": True,
    }
    assert config["requests"]["nonstream-keepalive-interval"] == 15
    assert (profile.directory / "proxy.yaml").stat().st_mode & 0o777 == 0o600
    assert record["proxy_api_key"] not in json.dumps(ag.public_status(profile))
    assert "fake-google-token" not in json.dumps(config)


def test_bridge_lifecycle_catalog_partner_models_and_redaction(tmp_path, profile, fake_binary):
    credential = ag.AntigravityCredential(profile.directory.parent, "test")
    process = credential.process
    try:
        with httpx.Client(trust_env=False) as client:
            result = client.get(
                credential.base_url + "/models",
                headers={"Authorization": "Bearer " + credential.token()},
            )
            assert {m["id"] for m in result.json()["data"]} == {
                "gemini-test",
                "partner-claude-test",
            }
            assert client.get(credential.base_url + "/models").status_code == 401
        config = load_config(ROOT / "configs/antigravity.yaml")
        config["model"]["id"] = "partner-claude-test"
        config["provider"].update(base_url=credential.base_url)
        store = RunStore(tmp_path / "evidence")
        gateway = Gateway(config, credential, store, Budget(60))
        gateway.preflight()
        assert (
            json.loads((store.path / "provider-model.json").read_text())["id"]
            == "partner-claude-test"
        )
        info = credential.public_info()
        assert info["experimental"] and not info["upstream_standard_speed_verified"]
        assert credential.token() not in json.dumps(info)
        assert credential.redact(credential.token()) == "[REDACTED]"
        gateway.close()
    finally:
        credential.close()
    assert process.poll() is not None


def test_private_profile_cannot_run_twice(profile, fake_binary):
    first = ag.AntigravityCredential(profile.directory.parent, "test")
    try:
        with pytest.raises(AuthError, match="busy"):
            ag.AntigravityCredential(profile.directory.parent, "test")
    finally:
        first.close()


def test_known_legacy_proxy_policy_migrates_without_changing_account(profile, fake_binary):
    before = (profile.directory / "accounts/account.json").read_bytes()
    legacy = ag.proxy_config(profile, profile.read())
    legacy["requests"] = {"streaming": {"bootstrap-retries": 0, "keepalive-seconds": 0}}
    profile.write(legacy, "proxy.yaml")
    credential = ag.AntigravityCredential(profile.directory.parent, "test", keepalive_seconds=2)
    try:
        assert profile.read("proxy.yaml")["requests"]["streaming"]["keepalive-seconds"] == 2
        assert credential.public_info()["keepalive_before_first_token"]
        assert (profile.directory / "accounts/account.json").read_bytes() == before
    finally:
        credential.close()


def test_proxy_stops_if_controller_is_killed(profile, fake_binary):
    program = (
        "from pathlib import Path; import json,time; from gtoc_bench import antigravity as ag; "
        f"ag.runtime_dir=lambda:Path({str(fake_binary.parent)!r}); "
        f"c=ag.AntigravityCredential({str(profile.directory.parent)!r}, 'test'); "
        "print(json.dumps({'guard':c.process.pid,'proxy':c.child_pid}),flush=True); time.sleep(60)"
    )
    worker = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE)
    port = profile.read()["port"]
    try:
        pids = json.loads(worker.stdout.readline())
        worker.kill()
        worker.wait(timeout=5)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.1)
                if probe.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.1)
        else:
            pytest.fail("The proxy survived controller loss")
        assert pids["guard"] != pids["proxy"]
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
        worker.stdout.close()


@pytest.mark.parametrize("bad", ["multiple", "foreign", "permissions", "symlink"])
def test_rejects_unsafe_or_rotating_accounts(profile, tmp_path, bad):
    path = profile.directory / "accounts/account.json"
    accounts = Profiles(path.parent)
    if bad == "multiple":
        accounts.write({"type": "antigravity"}, "second.json")
    elif bad == "foreign":
        accounts.write({"type": "codex"}, "account.json")
    elif bad == "permissions":
        path.chmod(0o644)
    else:
        path.unlink()
        outside = tmp_path / "outside.json"
        outside.write_text("{}")
        path.symlink_to(outside)
    with pytest.raises(AuthError):
        ag.account_files(profile)


def test_account_change_cannot_silently_continue(profile, fake_binary):
    credential = ag.AntigravityCredential(profile.directory.parent, "test")
    try:
        accounts = Profiles(profile.directory / "accounts")
        accounts.write(
            {"type": "antigravity", "email": "different@example.invalid"}, "account.json"
        )
        with pytest.raises(AuthError, match="changed"):
            credential.token()
    finally:
        credential.close()


@pytest.mark.parametrize("ignore_term", [False, True])
def test_cutoff_stops_proxy_before_finalization(profile, fake_binary, ignore_term):
    if ignore_term:
        fake_binary.write_text(
            fake_binary.read_text().replace(
                "import json,sys",
                "import json,sys,signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)",
            )
        )
        installed = Profiles(fake_binary.parent)
        metadata = installed.read("install.json")
        metadata["binary_sha256"] = hashlib.sha256(fake_binary.read_bytes()).hexdigest()
        installed.write(metadata, "install.json")
    credential = ag.AntigravityCredential(profile.directory.parent, "test")
    try:
        assert credential.cancel()
        assert not credential.cancel()
        with pytest.raises(AuthError, match="closing"):
            credential.token()
        deadline = time.monotonic() + 5
        while credential.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert credential.process.poll() is not None
    finally:
        credential.close()


def test_rejects_modified_proxy_config_and_binary(profile, fake_binary):
    config = profile.read("proxy.yaml")
    config["server"]["host"] = "0.0.0.0"
    profile.write(config, "proxy.yaml")
    with pytest.raises(AuthError, match="configuration changed"):
        ag.AntigravityCredential(profile.directory.parent, "test")
    fake_binary.write_text("changed")
    with pytest.raises(AuthError, match="installed Antigravity bridge changed"):
        ag.installed_binary()


@pytest.mark.parametrize(
    "agent,protocol",
    [
        ("codex", "responses"),
        ("claude_code", "anthropic"),
        ("openhands", "chat_completions"),
        ("swe_agent", "chat_completions"),
        ("mini_swe_agent", "chat_completions"),
        ("antigravity", "chat_completions"),
    ],
)
def test_all_agents_accept_antigravity_models_with_their_native_protocol(tmp_path, agent, protocol):
    config = yaml.safe_load((ROOT / "configs/antigravity.yaml").read_text())
    config["agent"] = {"id": agent}
    config["provider"]["protocol"] = protocol
    config["model"]["id"] = "partner-claude-test"
    file = tmp_path / "config.yaml"
    file.write_text(yaml.safe_dump(config))
    loaded = load_config(file)
    assert loaded["model"]["id"] == "partner-claude-test"
    assert "auth_dir" not in public_config(loaded)["provider"]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.com/v1",
        "http://0.0.0.0:8317/v1",
        "http://127.0.0.1:8317/v0/management",
        "http://localhost:8317/v1",
    ],
)
def test_antigravity_cannot_forward_credentials_to_external_endpoints(tmp_path, base_url):
    config = yaml.safe_load((ROOT / "configs/antigravity.yaml").read_text())
    config["provider"]["base_url"] = base_url
    file = tmp_path / "config.yaml"
    file.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="loopback"):
        load_config(file)
