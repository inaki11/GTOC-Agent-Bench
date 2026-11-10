"""Interactive browser consent on the host; never runs inside an evaluation."""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .auth import (
    AUTHORIZE_URL,
    RESOURCE,
    SCOPES,
    TOKEN_URL,
    AuthError,
    token_record,
    validate_id_token,
)


def login(profiles, client, port=1455, browser=True, enable_plan=False, timeout=300):
    """Authorization code + PKCE, with a loopback-only callback and verified OIDC identity."""
    if not 0 <= port <= 65535 or timeout <= 0:
        raise AuthError("Invalid callback port or login timeout")
    with profiles.lock(f"{profiles.profile}.login.lock", timeout=0):
        original = profiles.read()
        host_id = profiles.host_id()
        selected_client = original.get("client_id", "dynamic_agent_client")
        state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
            b"="
        )
        completed, callback = threading.Event(), {}

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *args):
                pass  # Callback URLs contain an authorization code.

            def do_GET(self):
                target = urlsplit(self.path)
                try:
                    query = parse_qs(target.query, strict_parsing=True, max_num_fields=12)
                    valid = (
                        len(self.path) <= 16384
                        and target.path == "/auth/callback"
                        and all(len(values) == 1 for values in query.values())
                        and hmac.compare_digest(query.get("state", [""])[0], state)
                        and not completed.is_set()
                    )
                except (ValueError, TypeError):
                    valid = False
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(
                    b"Return to the terminal to see the sign-in result."
                    if valid
                    else b"Invalid authorization callback."
                )
                if valid:
                    callback.update({key: values[0] for key, values in query.items()})
                    completed.set()

        server = HTTPServer(("127.0.0.1", port), Handler)
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        parameters = {
            "client_id": selected_client,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge.decode(),
        }
        if selected_client == "dynamic_agent_client":
            parameters["agent_name_hint"] = "GTOC-Bench"
        if enable_plan:
            parameters["prompt"] = "consent"
        # No ID-token hint in a URL printed to the terminal; verify the selected account below.
        url = AUTHORIZE_URL + "?" + urlencode(parameters)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            print("Continue with ChatGPT — open this URL in your browser:\n" + url, flush=True)
            if browser:
                webbrowser.open(url)
            if not completed.wait(timeout):
                raise AuthError("ChatGPT sign-in timed out; start a fresh login")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        if callback.get("error"):
            raise AuthError("ChatGPT authorization was not granted; no credentials changed")
        issued = callback.get("client_id", selected_client)
        if (
            issued == "dynamic_agent_client"
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", issued)
            or (selected_client != "dynamic_agent_client" and issued != selected_client)
            or not callback.get("code")
        ):
            raise AuthError("Incomplete registration or mismatched ChatGPT client ID")
        registration = original | {"client_id": issued, "ext_agent_host_id": host_id}
        if selected_client == "dynamic_agent_client":
            with profiles.lock():
                profiles.write(registration)  # Retain issued ID even if code exchange fails.
        try:
            response = client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": issued,
                    "code": callback["code"],
                    "code_verifier": verifier,
                    "redirect_uri": redirect,
                    "resource": RESOURCE,
                },
            )
            response.raise_for_status()
            data = response.json()
            encoded = data["id_token"]
            # A new authorization must report its own grant, never inherit an earlier one.
            if not isinstance(data.get("scope"), str):
                raise ValueError
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise AuthError("ChatGPT code exchange failed; start a fresh login") from None
        claims = validate_id_token(encoded, issued, client, nonce)
        if original.get("subject") and claims["sub"] != original["subject"]:
            raise AuthError("Selected ChatGPT account changed; use a different --profile")
        record = token_record(data, registration, claims)
        record["session_id"] = uuid.uuid4().hex
        with profiles.lock():
            profiles.write(record)
        return record
