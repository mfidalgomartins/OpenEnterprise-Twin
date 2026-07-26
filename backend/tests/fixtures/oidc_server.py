"""Minimal OIDC authorization-code + PKCE provider for browser release tests."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

HOST = os.getenv("OIDC_FIXTURE_HOST", "127.0.0.1")
PORT = int(os.getenv("OIDC_FIXTURE_PORT", "18081"))
BASE_URL = f"http://{HOST}:{PORT}"
ISSUER = f"{BASE_URL}/"
API_AUDIENCE = os.getenv("OIDC_API_AUDIENCE", "openenterprise-twin")
TENANT_ID = os.getenv("OIDC_TENANT_ID", "northstar")
SUBJECT = os.getenv("OIDC_SUBJECT", "e2e-analyst")
ROLES = tuple(
    role.strip()
    for role in os.getenv("OIDC_ROLES", "analyst,viewer").split(",")
    if role.strip()
)
ALLOWED_ORIGIN = os.getenv(
    "OIDC_ALLOWED_ORIGIN",
    "http://127.0.0.1:4173",
)

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_jwk = json.loads(RSAAlgorithm.to_jwk(_private_key.public_key()))
_public_jwk.update({"alg": "RS256", "kid": "e2e-key-1", "use": "sig"})
_authorization_codes: dict[str, dict[str, str]] = {}


def _encode_token(
    *,
    audience: str,
    nonce: str | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": audience,
        "sub": SUBJECT,
        "tenant_id": TENANT_ID,
        "roles": list(ROLES),
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    if nonce:
        claims["nonce"] = nonce
    return jwt.encode(
        claims,
        _private_key,
        algorithm="RS256",
        headers={"kid": "e2e-key-1"},
    )


class OidcFixtureHandler(BaseHTTPRequestHandler):
    server_version = "OpenEnterpriseTwinOidcFixture/1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/openid-configuration":
            self._json(
                {
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{BASE_URL}/authorize",
                    "token_endpoint": f"{BASE_URL}/token",
                    "jwks_uri": f"{BASE_URL}/jwks",
                    "end_session_endpoint": f"{BASE_URL}/logout",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"],
                }
            )
            return
        if parsed.path == "/jwks":
            self._json({"keys": [_public_jwk]})
            return
        if parsed.path == "/authorize":
            self._authorize(parse_qs(parsed.query))
            return
        if parsed.path == "/logout":
            query = parse_qs(parsed.query)
            destination = query.get(
                "post_logout_redirect_uri",
                [ALLOWED_ORIGIN],
            )[0]
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", destination)
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/token":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            self._oauth_error("invalid_request", HTTPStatus.BAD_REQUEST)
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        code = form.get("code", [""])[0]
        verifier = form.get("code_verifier", [""])[0]
        record = _authorization_codes.pop(code, None)
        if record is None or not verifier:
            self._oauth_error("invalid_grant", HTTPStatus.BAD_REQUEST)
            return
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        if challenge != record["code_challenge"]:
            self._oauth_error("invalid_grant", HTTPStatus.BAD_REQUEST)
            return
        client_id = record["client_id"]
        self._json(
            {
                "access_token": _encode_token(audience=API_AUDIENCE),
                "id_token": _encode_token(
                    audience=client_id,
                    nonce=record.get("nonce"),
                ),
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": record["scope"],
            }
        )

    def _authorize(self, query: dict[str, list[str]]) -> None:
        required = (
            "client_id",
            "redirect_uri",
            "state",
            "code_challenge",
        )
        if any(not query.get(field, [""])[0] for field in required):
            self._oauth_error("invalid_request", HTTPStatus.BAD_REQUEST)
            return
        redirect_uri = query["redirect_uri"][0]
        if not redirect_uri.startswith(f"{ALLOWED_ORIGIN}/"):
            self._oauth_error("invalid_request", HTTPStatus.BAD_REQUEST)
            return
        code = token_urlsafe(24)
        _authorization_codes[code] = {
            "client_id": query["client_id"][0],
            "code_challenge": query["code_challenge"][0],
            "nonce": query.get("nonce", [""])[0],
            "scope": query.get("scope", ["openid profile"])[0],
        }
        separator = "&" if "?" in redirect_uri else "?"
        location = (
            f"{redirect_uri}{separator}"
            + urlencode({"code": code, "state": query["state"][0]})
        )
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def _json(
        self,
        payload: dict[str, object],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _oauth_error(self, code: str, status: HTTPStatus) -> None:
        self._json({"error": code}, status)

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Vary", "Origin")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), OidcFixtureHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
