"""Host-only credentials and official ChatGPT public-client OAuth sessions."""

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import jwt

from . import ROOT

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = ISSUER + "/api/accounts/authorize"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
DEFAULT_AUTH_DIR = "~/.config/gtoc-bench/auth"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
TOKEN_FIELDS = {"access_token", "refresh_token", "id_token"}
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}


class AuthError(ValueError):
    def __init__(self, message, reason="configuration_error"):
        super().__init__(message)
        self.reason = reason


def auth_path(directory=DEFAULT_AUTH_DIR):
    path = Path(directory).expanduser().absolute()
    if path.is_symlink() or path.resolve().is_relative_to(ROOT.resolve()):
        raise AuthError("Keep ChatGPT credentials outside the repository, without symlinks")
    return path


def checked_profile(profile):
    if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", profile):
        raise AuthError("Profile must be 1–64 letters, digits, underscores or hyphens")
    return profile


class Profiles:
    """Private files are canonical; flock serializes rotating refresh-token use."""

    def __init__(self, directory=DEFAULT_AUTH_DIR, profile="default"):
        self.directory = auth_path(directory)
        self.profile = checked_profile(profile)

    def prepare(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise AuthError("ChatGPT credential directory must be owned by you with mode 700")

    def _open(self, name, flags):
        self.prepare()
        fd = os.open(self.directory / name, flags | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            os.close(fd)
            raise AuthError("ChatGPT credential files must be private regular files (mode 600)")
        return fd

    @contextlib.contextmanager
    def lock(self, name=None, timeout=30, *, shared=False):
        """Exclusive for credential changes; native-client runs can share a read lease."""
        fd = self._open(name or f"{self.profile}.lock", os.O_RDWR | os.O_CREAT)
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise AuthError(
                            "ChatGPT session is busy; try again", "provider_unavailable"
                        )
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)

    def read(self, name=None):
        try:
            fd = self._open(name or f"{self.profile}.json", os.O_RDONLY)
        except FileNotFoundError:
            return {}
        with os.fdopen(fd, "rb") as file:
            data = file.read(1024**2 + 1)
        try:
            value = json.loads(data) if len(data) <= 1024**2 else None
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise AuthError("Invalid ChatGPT credential file; sign in again") from None

    def write(self, value, name=None):
        temporary = f".{uuid.uuid4().hex}.tmp"
        fd = self._open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(value, file, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(
                self.directory / temporary, self.directory / (name or f"{self.profile}.json")
            )
            parent = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            (self.directory / temporary).unlink(missing_ok=True)

    def host_id(self):
        with self.lock(".host.lock"):
            value = self.read(".host.json")
            if not value:
                value = {"ext_agent_host_id": "urn:uuid:" + str(uuid.uuid4())}
                self.write(value, ".host.json")
            return value["ext_agent_host_id"]


def http_client():
    return httpx.Client(timeout=httpx.Timeout(20, connect=10), trust_env=False)


def validate_id_token(encoded, client_id, client, nonce=None):
    """Never use unverified identity claims to choose or replace an account."""
    try:
        response = client.get(ISSUER + "/.well-known/jwks.json")
        response.raise_for_status()
        header = jwt.get_unverified_header(encoded)
        key = next(k for k in response.json()["keys"] if k["kid"] == header.get("kid"))
        claims = jwt.decode(
            encoded,
            jwt.PyJWK.from_dict(key, algorithm="RS256").key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
        if not claims["sub"] or (nonce is not None and claims.get("nonce") != nonce):
            raise ValueError
        if claims.get("azp", client_id) != client_id:
            raise ValueError
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and "azp" not in claims:
            raise ValueError
        return claims
    except httpx.HTTPError:
        raise AuthError("Cannot obtain ChatGPT signing keys; try signing in again") from None
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, StopIteration):
        raise AuthError("ChatGPT ID token failed identity verification") from None


def token_record(data, previous, claims=None):
    try:
        lifetime = data["expires_in"]
        if type(lifetime) not in (float, int) or not math.isfinite(lifetime) or lifetime <= 0:
            raise ValueError
        if data["token_type"].lower() != "bearer":
            raise ValueError
        for field in ("access_token", "refresh_token"):
            token = data[field]
            if not isinstance(token, str) or not token or any(c.isspace() for c in token):
                raise ValueError
        scope = data.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ValueError
        earliest = data.get("earliest_refresh_at") or 0
        if isinstance(earliest, str):
            try:
                earliest = float(earliest)
            except ValueError:
                earliest = datetime.fromisoformat(earliest.replace("Z", "+00:00")).timestamp()
        if type(earliest) not in (float, int) or not math.isfinite(earliest):
            raise ValueError
        record = previous | {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "token_type": "Bearer",
            "expires_at": time.time() + lifetime,
            "earliest_refresh_at": earliest,
            "scopes": scope.split() if scope is not None else previous.get("scopes", []),
            "refresh_pending": False,
        }
        if claims is not None:
            record.update(issuer=ISSUER, subject=claims["sub"], email=claims.get("email"))
            record["id_token"] = data["id_token"]
        return record
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AuthError("Incomplete ChatGPT token response; sign in again") from None


def clear_tokens(record):
    return {k: v for k, v in record.items() if k not in TOKEN_FIELDS} | {
        "expires_at": 0,
        "refresh_pending": False,
    }


def public_session(record, profile):
    account = None
    if record.get("subject"):
        identity = [record.get(k) for k in ("issuer", "client_id", "subject")]
        account = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return {
        "auth_method": "chatgpt",
        "billing_mode": "subscription",
        "profile": profile,
        "account_ref": account,
        "actual_cost": None,
    }


class ApiKeyCredential:
    def __init__(self, key):
        self.key = key

    def token(self):
        return self.key

    def redact(self, text):
        return text.replace(self.key, "[REDACTED]")

    def public_info(self):
        return {"auth_method": "api_key", "billing_mode": "api"}


class ChatGPTCredential:
    def __init__(self, profiles, client=None):
        self.profiles, self.client = profiles, client or http_client()
        record = profiles.read()
        self.identity = self._identity(record)
        self.info = public_session(record, profiles.profile)
        self.secrets = set()
        self._remember(record)

    @staticmethod
    def _identity(record):
        return tuple(record.get(k) for k in ("issuer", "client_id", "subject", "session_id"))

    def _remember(self, record):
        self.secrets.update(
            record[k] for k in TOKEN_FIELDS if isinstance(record.get(k), str) and record[k]
        )

    def redact(self, text):
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text

    def public_info(self):
        return self.info.copy()

    def token(self, rejected_token=None):
        with self.profiles.lock():
            record = self.profiles.read()
            self._remember(record)
            if self._identity(record) != self.identity:
                raise AuthError("ChatGPT profile changed during this run; start a new run")
            if not record.get("access_token") or record.get("refresh_pending"):
                raise AuthError("ChatGPT session needs sign-in: gtoc auth login chatgpt")
            if PLAN_SCOPE not in record.get("scopes", []):
                raise AuthError("ChatGPT plan use is not enabled; sign in with --enable-plan")
            now = time.time()
            if record["expires_at"] > now + 60 and (
                rejected_token is None or rejected_token != record["access_token"]
            ):
                return record["access_token"]
            if now < record.get("earliest_refresh_at", 0):
                if rejected_token is None and record["expires_at"] > now:
                    return record["access_token"]
                raise AuthError("ChatGPT session cannot be refreshed yet", "provider_unavailable")
            if not record.get("refresh_token"):
                raise AuthError("ChatGPT session needs sign-in: gtoc auth login chatgpt")
            # If the process dies after rotation, never replay an ambiguously consumed token.
            self.profiles.write(record | {"refresh_pending": True})
            try:
                response = self.client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    },
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                self.profiles.write(record)
                raise AuthError(
                    "ChatGPT refresh service unavailable", "provider_unavailable"
                ) from None
            except httpx.HTTPError:
                raise AuthError("ChatGPT refresh outcome unknown; sign in again") from None
            if response.status_code != 200:
                try:
                    error = response.json().get("error")
                    code = error.get("code") if isinstance(error, dict) else error
                except (ValueError, AttributeError):
                    code = None
                if code in TERMINAL_REFRESH_ERRORS:
                    self.profiles.write(clear_tokens(record))
                    raise AuthError("ChatGPT session expired or revoked; sign in again")
                self.profiles.write(record)
                if response.status_code == 429 or response.status_code >= 500:
                    raise AuthError("ChatGPT refresh service unavailable", "provider_unavailable")
                raise AuthError("ChatGPT refresh rejected; check the account registration")
            try:
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                raise AuthError("Invalid ChatGPT refresh response; sign in again") from None
            self._remember(data)
            claims = None
            if data.get("id_token"):
                claims = validate_id_token(data["id_token"], record["client_id"], self.client)
                if claims["sub"] != record["subject"]:
                    raise AuthError("ChatGPT refresh returned a different account; sign in again")
            updated = token_record(data, record, claims)
            self.profiles.write(updated)
            if PLAN_SCOPE not in updated["scopes"]:
                raise AuthError("ChatGPT plan permission was removed; sign in with --enable-plan")
            return updated["access_token"]


def create_credential(config):
    provider = config["provider"]
    if provider.get("auth_method") == "antigravity":
        from .antigravity import AntigravityCredential

        return AntigravityCredential(
            provider["auth_dir"],
            provider["profile"],
            provider["base_url"],
            keepalive_seconds=provider.get("keepalive_seconds", 15),
        )
    if provider.get("auth_method", "api_key") == "chatgpt":
        return ChatGPTCredential(Profiles(provider["auth_dir"], provider["profile"]))
    from .config import load_key

    return ApiKeyCredential(load_key(config))


def model_catalog(credential, client):
    try:
        token = credential.token()
        response = client.get(
            RESOURCE + "/models",
            headers={
                "Authorization": "Bearer " + token,
            },
        )
        if response.status_code == 401 and isinstance(credential, ChatGPTCredential):
            token = credential.token(rejected_token=token)
            response = client.get(
                RESOURCE + "/models", headers={"Authorization": "Bearer " + token}
            )
        response.raise_for_status()
        models = response.json()["models"]
        if not isinstance(models, list) or any(not isinstance(m, dict) for m in models):
            raise ValueError
        return models
    except AuthError:
        raise
    except httpx.HTTPError:
        raise AuthError(
            "Cannot read ChatGPT models; check account access and availability"
        ) from None
    except (ValueError, KeyError, TypeError):
        raise AuthError("Unexpected ChatGPT model catalog") from None


def logout(profiles, client):
    with profiles.lock():
        record = profiles.read()
        confirmed = not record.get("refresh_token")
        if not confirmed:
            try:
                discovery = client.get(ISSUER + "/.well-known/openid-configuration")
                discovery.raise_for_status()
                endpoint = discovery.json()["revocation_endpoint"]
                url = urlsplit(endpoint)
                if url.scheme != "https" or url.netloc != "auth.openai.com":
                    raise ValueError
                response = client.post(
                    endpoint,
                    data={
                        "token": record["refresh_token"],
                        "token_type_hint": "refresh_token",
                        "client_id": record["client_id"],
                    },
                )
                confirmed = response.status_code == 200
            except (httpx.HTTPError, ValueError, KeyError):
                pass
        profiles.write(clear_tokens(record))
        return confirmed
