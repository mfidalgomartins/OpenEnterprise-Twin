"""OIDC API integration against an ephemeral JWKS endpoint with rotation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import perf_counter

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from openenterprise_twin.api.app import create_app
from openenterprise_twin.infrastructure.settings import Settings


def _public_jwk(
    private_key: rsa.RSAPrivateKey,
    key_id: str,
) -> dict[str, object]:
    value = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    value.update({"alg": "RS256", "kid": key_id, "use": "sig"})
    return value


@contextmanager
def _rotating_jwks_server(
    keys: list[dict[str, object]],
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_GET(self) -> None:
            if self.path != "/jwks":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = json.dumps({"keys": keys}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/jwk-set+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _token(
    private_key: rsa.RSAPrivateKey,
    key_id: str,
    issuer: str,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "aud": "openenterprise-twin",
            "sub": "finance-analyst",
            "tenant_id": "northstar",
            "roles": ["analyst", "viewer"],
            "iat": now,
            "nbf": now - timedelta(seconds=1),
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": key_id},
    )


def test_unknown_kid_refreshes_jwks_and_authenticates_rotated_key(
    tmp_path: Path,
) -> None:
    first_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    second_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    published_keys = [_public_jwk(first_key, "key-1")]

    with _rotating_jwks_server(published_keys) as issuer:
        normalized_issuer = f"{issuer}/"
        settings = Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path / 'oidc.db'}",
            artifact_directory=tmp_path / "artifacts",
            deployment_environment="test",
            authentication_mode="oidc",
            oidc_issuer=normalized_issuer,
            oidc_audience="openenterprise-twin",
            oidc_jwks_url=f"{issuer}/jwks",
            oidc_jwks_cache_ttl_seconds=300,
            experiment_workers=1,
            database_pool_size=2,
            database_max_overflow=0,
        )
        with TestClient(create_app(settings)) as client:
            first = client.get(
                "/api/v1/session",
                headers={
                    "Authorization": (
                        f"Bearer {_token(first_key, 'key-1', normalized_issuer)}"
                    )
                },
            )
            published_keys[:] = [_public_jwk(second_key, "key-2")]
            rotated = client.get(
                "/api/v1/session",
                headers={
                    "Authorization": (
                        f"Bearer {_token(second_key, 'key-2', normalized_issuer)}"
                    )
                },
            )
            started_at = perf_counter()
            cached_validations = [
                client.get(
                    "/api/v1/session",
                    headers={
                        "Authorization": (
                            "Bearer "
                            + _token(second_key, "key-2", normalized_issuer)
                        )
                    },
                )
                for _ in range(20)
            ]
            elapsed_seconds = perf_counter() - started_at

    assert first.status_code == 200
    assert rotated.status_code == 200
    assert all(response.status_code == 200 for response in cached_validations)
    assert elapsed_seconds < 1.5
    assert rotated.json() == {
        "subject": "finance-analyst",
        "tenant_id": "northstar",
        "roles": ["analyst", "viewer"],
        "authentication_method": "oidc",
    }
