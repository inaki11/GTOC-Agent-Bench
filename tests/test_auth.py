import base64
import hashlib
import json
import multiprocessing
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa

from gtoc_bench import ROOT
from gtoc_bench.auth import (
    ISSUER,
    PLAN_SCOPE,
    RESOURCE,
    TOKEN_URL,
    AuthError,
    ChatGPTCredential,
    Profiles,
    logout,
    public_session,
    validate_id_token,
)
from gtoc_bench.budget import Budget
from gtoc_bench.cli import main
from gtoc_bench.config import load_config, public_config
from gtoc_bench.gateway import Gateway, UsageTracker, locked_request
from gtoc_bench.login import login
from gtoc_bench.storage import RunStore


def session_record(**changes):
    return {
        "issuer": ISSUER,
        "subject": "test-account",
        "client_id": "oaiapp_test",
        "session_id": "test-session",
        "access_token": "synthetic-access-secret",
        "refresh_token": "synthetic-refresh-secret",
        "id_token": "synthetic-id-secret",
        "expires_at": time.time() + 3600,
        "scopes": [PLAN_SCOPE, "resource.invoke"],
        "earliest_refresh_at": 0,
        "refresh_pending": False,
    } | changes


def refresh_response(**changes):
    return {
        "access_token": "rotated-access-secret",
        "refresh_token": "rotated-refresh-secret",
        "expires_in": 3600,
        "token_type": "Bearer",
    } | changes


@pytest.fixture
def profiles(tmp_path):
    result = Profiles(tmp_path / "private", "test")
    result.write(session_record())
    return result


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def signed_token(key, **changes):
    return jwt.encode(
        {
            "iss": ISSUER,
            "aud": "oaiapp_test",
            "sub": "test-account",
            "exp": int(time.time()) + 3600,
            "nonce": "expected-nonce",
        }
        | changes,
        key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def jwks_response(key):
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return httpx.Response(200, json={"keys": [jwk | {"kid": "test-key"}]})


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "different-client"},
        {"iss": "https://attacker.invalid"},
        {"nonce": "wrong-nonce"},
        {"exp": 1},
        {"sub": ""},
        {"azp": "another-client"},
        {"aud": ["oaiapp_test", "other"]},
    ],
)
def test_oidc_rejects_untrusted_identity(signing_key, changes):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: jwks_response(signing_key))
    ) as client:
        with pytest.raises(AuthError, match="identity verification"):
            validate_id_token(
                signed_token(signing_key, **changes), "oaiapp_test", client, nonce="expected-nonce"
            )


def test_oidc_checks_signature(signing_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: jwks_response(signing_key))
    ) as client:
        claims = validate_id_token(
            signed_token(signing_key), "oaiapp_test", client, "expected-nonce"
        )
        assert claims["sub"] == "test-account"
        with pytest.raises(AuthError, match="identity verification"):
            validate_id_token(signed_token(other), "oaiapp_test", client, "expected-nonce")


def test_login_pkce_callback_validation_and_returning_client(
    tmp_path, signing_key, monkeypatch, capsys
):
    profiles = Profiles(tmp_path / "private")
    authorized, exchanged = [], []

    def browser(url):
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        authorized.append(query)
        callback = {"state": query["state"], "code": "synthetic-code", "client_id": "oaiapp_test"}
        with httpx.Client(trust_env=False) as local:
            bad = local.get(query["redirect_uri"], params=callback | {"state": "wrong"})
            assert bad.status_code == 400
            good = local.get(query["redirect_uri"], params=callback)
            assert good.status_code == 200
        return True

    def endpoint(request):
        if request.url.path.endswith("jwks.json"):
            return jwks_response(signing_key)
        assert str(request.url) == TOKEN_URL
        data = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        exchanged.append(data)
        assert data["grant_type"] == "authorization_code"
        assert data["client_id"] == "oaiapp_test"
        assert data["resource"] == RESOURCE
        assert data["redirect_uri"] == authorized[-1]["redirect_uri"]
        digest = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest())
        assert digest.rstrip(b"=").decode() == authorized[-1]["code_challenge"]
        return httpx.Response(
            200,
            json=refresh_response(
                id_token=signed_token(signing_key, nonce=authorized[-1]["nonce"]),
                scope=f"openid offline_access resource.invoke {PLAN_SCOPE}",
            ),
        )

    monkeypatch.setattr("gtoc_bench.login.webbrowser.open", browser)
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        first = login(profiles, client, port=0, timeout=5)
        second = login(profiles, client, port=0, timeout=5)
    assert authorized[0]["client_id"] == "dynamic_agent_client"
    assert authorized[0]["agent_name_hint"] == "GTOC-Bench"
    assert authorized[1]["client_id"] == "oaiapp_test" and "agent_name_hint" not in authorized[1]
    assert authorized[0]["ext_agent_host_id"] == authorized[1]["ext_agent_host_id"]
    assert first["session_id"] != second["session_id"]
    assert first["subject"] == second["subject"] == "test-account"
    assert first["access_token"] not in capsys.readouterr().out
    assert profiles.directory.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in profiles.directory.iterdir())


@pytest.mark.parametrize("failure", ["client", "account", "denied", "scope"])
def test_failed_reauthorization_preserves_selected_session(
    profiles, signing_key, monkeypatch, failure
):
    original = profiles.read()
    nonce, exchanges = [], []

    def browser(url):
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        nonce.append(query["nonce"])
        fields = {"state": query["state"], "code": "code"}
        if failure == "client":
            fields["client_id"] = "oaiapp_wrong"
        if failure == "denied":
            fields["error"] = "access_denied"
        with httpx.Client(trust_env=False) as local:
            local.get(query["redirect_uri"] + "?" + urlencode(fields))

    def endpoint(request):
        if request.url.path.endswith("jwks.json"):
            return jwks_response(signing_key)
        exchanges.append(request)
        data = refresh_response(
            id_token=signed_token(
                signing_key,
                nonce=nonce[-1],
                sub="other-account" if failure == "account" else "test-account",
            ),
            scope=PLAN_SCOPE,
        )
        if failure == "scope":
            data.pop("scope")
        return httpx.Response(200, json=data)

    monkeypatch.setattr("gtoc_bench.login.webbrowser.open", browser)
    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client, pytest.raises(AuthError):
        login(profiles, client, port=0, timeout=5)
    assert profiles.read() == original
    assert len(exchanges) == int(failure in {"account", "scope"})


def test_private_storage_blocks_symlinks_permissions_and_repository(tmp_path, profiles):
    with pytest.raises(AuthError, match="outside"):
        Profiles(ROOT / "secrets")
    with pytest.raises(AuthError, match="Profile"):
        Profiles(tmp_path, "../escape")
    path = profiles.directory / "test.json"
    path.chmod(0o644)
    with pytest.raises(AuthError, match="private"):
        profiles.read()
    path.unlink()
    target = tmp_path / "victim"
    target.write_text("do not read")
    path.symlink_to(target)
    with pytest.raises(OSError):
        profiles.read()
    assert target.read_text() == "do not read"


def _refresh_process(directory, start, output):
    profiles = Profiles(directory, "test")

    def endpoint(request):
        with (profiles.directory / "refresh-count").open("a") as log:
            log.write("refresh\n")
        time.sleep(0.1)
        return httpx.Response(200, json=refresh_response())

    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        credential = ChatGPTCredential(profiles, client)
        start.wait(10)
        output.put(credential.token())


@pytest.mark.parametrize("process_count", [2, 3])
def test_concurrent_processes_rotate_only_once(profiles, process_count):
    profiles.write(session_record(expires_at=1))
    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    processes = [
        context.Process(target=_refresh_process, args=(str(profiles.directory), start, output))
        for _ in range(process_count)
    ]
    for process in processes:
        process.start()
    try:
        start.set()
        assert [output.get(timeout=15) for _ in processes] == ["rotated-access-secret"] * process_count
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert (profiles.directory / "refresh-count").read_text() == "refresh\n"
        assert profiles.read()["refresh_token"] == "rotated-refresh-secret"
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


@pytest.mark.parametrize("failure", ["connect", "ambiguous", "unavailable", "invalid_grant"])
def test_refresh_failures_preserve_or_invalidate_session_deliberately(profiles, failure):
    original = session_record(expires_at=1)
    profiles.write(original)
    calls = []

    def endpoint(request):
        calls.append(request)
        if failure == "connect":
            raise httpx.ConnectError("synthetic-refresh-secret")
        if failure == "ambiguous":
            raise httpx.ReadTimeout("synthetic-refresh-secret")
        return httpx.Response(503 if failure == "unavailable" else 400, json={"error": failure})

    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        credential = ChatGPTCredential(profiles, client)
        with pytest.raises(AuthError) as error:
            credential.token()
        assert "synthetic-refresh-secret" not in str(error.value)
        saved = profiles.read()
        if failure in {"connect", "unavailable"}:
            assert saved == original
            assert error.value.reason == "provider_unavailable"
        else:
            with pytest.raises(AuthError):
                credential.token()
            assert len(calls) == 1
            if failure == "ambiguous":
                assert saved["refresh_pending"]
            else:
                assert "refresh_token" not in saved and saved["client_id"] == "oaiapp_test"


def test_account_pin_and_permission_check(profiles):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("No HTTP expected"))
    ) as client:
        credential = ChatGPTCredential(profiles, client)
        assert credential.token() == "synthetic-access-secret"
        profiles.write(session_record(subject="different-account"))
        with pytest.raises(AuthError, match="changed"):
            credential.token()
        profiles.write(session_record(scopes=["openid"]))
        with pytest.raises(AuthError, match="not enabled"):
            ChatGPTCredential(profiles, client).token()
        public = json.dumps(public_session(profiles.read(), "test"))
        assert "test-account" not in public and "secret" not in public


def test_logout_revokes_only_at_official_origin_and_retains_registration(profiles):
    sent = []

    def endpoint(request):
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"revocation_endpoint": ISSUER + "/revoke"})
        assert parse_qs(request.content.decode())["token"] == ["synthetic-refresh-secret"]
        return httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        assert logout(profiles, client)
    assert len(sent) == 2
    saved = profiles.read()
    assert not any(k in saved for k in ("access_token", "refresh_token", "id_token"))
    assert saved["client_id"] == "oaiapp_test"
    profiles.write(session_record())
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"revocation_endpoint": "https://attacker.invalid/steal"}
            )
        )
    ) as client:
        assert not logout(profiles, client)
    assert "refresh_token" not in profiles.read()


def test_config_modes_and_status_do_not_expose_credentials(tmp_path, profiles, capsys):
    cfg = load_config(ROOT / "configs/codex-chatgpt.yaml")
    assert cfg["model"]["max_output_tokens"] is None
    assert "auth_dir" not in public_config(cfg)["provider"]
    path = tmp_path / "experiment.yaml"
    for section, field, value in [
        ("provider", "base_url", "https://attacker.invalid"),
        ("provider", "env_file", "/tmp/fallback.env"),
        ("model", "max_output_tokens", 10),
    ]:
        altered = json.loads(json.dumps(cfg))
        altered[section][field] = value
        path.write_text(yaml.safe_dump(altered))
        with pytest.raises(ValueError):
            load_config(path)
    assert (
        main(
            [
                "auth",
                "status",
                "chatgpt",
                "--profile",
                "test",
                "--auth-dir",
                str(profiles.directory),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"signed_in": true' in output and "secret" not in output


def oauth_gateway(tmp_path, profiles, endpoint):
    cfg = load_config(ROOT / "configs/codex-chatgpt.yaml")
    client = httpx.Client(transport=httpx.MockTransport(endpoint))
    credential = ChatGPTCredential(profiles, client)
    budget = Budget(60, 1000)
    budget.start()
    gateway = Gateway(
        cfg, credential, RunStore(tmp_path / "run"), budget, transport=httpx.MockTransport(endpoint)
    )
    gateway.client.close()
    gateway.client = client
    return gateway


def test_oauth_gateway_refreshes_once_without_replaying_success(tmp_path, profiles):
    calls, auth = [], []

    def endpoint(request):
        calls.append(str(request.url))
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json=refresh_response())
        auth.append(request.headers["authorization"])
        if len(auth) == 1:
            return httpx.Response(401, json={"error": {"code": "invalid_token"}})
        body = json.loads(request.content)
        assert body["store"] is False and body["stream"] is True
        assert "max_output_tokens" not in body and body["service_tier"] == "default"
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=(
                b'data: {"type":"response.completed","response":{"usage":'
                b'{"input_tokens":10,"output_tokens":2},"service_tier":"default"}}\n\n'
            ),
        )

    gateway = oauth_gateway(tmp_path, profiles, endpoint)
    gateway.config["recovery"]["max_transient_retries"] = 0
    body = {"model": gateway.config["model"]["id"], "input": [], "max_output_tokens": 10}
    gateway.proxy("/responses", {}, json.dumps(body).encode(), lambda *_: None)
    assert calls == [RESOURCE + "/responses", TOKEN_URL, RESOURCE + "/responses"]
    assert auth == ["Bearer synthetic-access-secret", "Bearer rotated-access-secret"]
    assert gateway.budget.tokens == 12 and gateway.budget.reason is None
    assert all("secret" not in p.read_text() for p in (tmp_path / "run").rglob("*.json"))
    gateway.client.close()


@pytest.mark.parametrize(
    "terminal,code",
    [
        ("response.failed", "subscription_sharing_usage_limit_exceeded"),
        ("response.failed", "subscription_sharing_usage_unavailable"),
        ("response.failed", "chatpass_v2_scope_not_authorized"),
        ("response.incomplete", None),
    ],
)
def test_http200_failures_stop_and_redact_fragmented_stream(tmp_path, profiles, terminal, code):
    calls = []
    event = {
        "type": terminal,
        "response": {
            "error": {"code": code, "message": "synthetic-access-secret"},
        },
    }
    encoded = b"data: " + json.dumps(event).encode() + b"\n\n"

    class Fragmented(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(encoded), 7):
                yield encoded[offset : offset + 7]

    def endpoint(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, stream=Fragmented()
        )

    gateway = oauth_gateway(tmp_path, profiles, endpoint)
    output = []
    body = {"model": gateway.config["model"]["id"], "input": []}
    for _ in range(2):
        gateway.proxy(
            "/responses", {}, json.dumps(body).encode(), lambda *args: output.append(args)
        )
    expected = (
        "configuration_error"
        if code == "chatpass_v2_scope_not_authorized"
        else "provider_unavailable"
    )
    assert len(calls) == 1 and gateway.budget.reason == expected
    assert gateway.budget.unknown_requests == 1
    assert b"synthetic-access-secret" not in b"".join(part[2] for part in output)
    metadata = json.loads(next((tmp_path / "run/requests").glob("*/metadata.json")).read_text())
    assert metadata["response_terminal"] == terminal
    gateway.client.close()


def test_catalog_preflight_and_unsupported_fields(tmp_path, profiles):
    def endpoint(request):
        assert str(request.url) == RESOURCE + "/models"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "gpt-6.1-sol",
                        "visibility": "list",
                        "supported_reasoning_levels": [{"effort": "high"}],
                    }
                ]
            },
        )

    gateway = oauth_gateway(tmp_path, profiles, endpoint)
    gateway.preflight()
    assert (tmp_path / "run/provider-model.json").exists()
    body = {"model": "gpt-6.1-sol", "input": []}
    for extra in ({"previous_response_id": "other-session"}, {"temperature": 1}):
        with pytest.raises(ValueError, match="Unsupported ChatGPT fields"):
            locked_request(body | extra, gateway.config)
    with pytest.raises(ValueError, match="only /responses"):
        gateway.proxy("/responses/compact", {}, json.dumps(body).encode(), lambda *_: None)
    gateway.config["model"]["reasoning"] = "max"
    with pytest.raises(ValueError, match="reasoning effort"):
        gateway.preflight()
    gateway.config["model"]["id"] = "missing-model"
    with pytest.raises(ValueError, match="absent"):
        gateway.preflight()
    gateway.client.close()


def test_usage_before_a_failed_response_is_not_reported_as_final():
    tracker = UsageTracker("responses")
    tracker.observe({"type": "response.created", "response": {"usage": {"input_tokens": 15}}})
    tracker.observe(
        {
            "type": "response.failed",
            "response": {
                "error": {"code": "subscription_sharing_usage_unavailable"},
            },
        }
    )
    assert tracker.usage()["quality"] == "partial"
    assert tracker.usage()["input_tokens"] == 15
