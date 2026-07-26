"""Fail-closed local, service-account and OIDC authentication contracts."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from openenterprise_twin.application.identity import AuthenticationError
from openenterprise_twin.infrastructure.identity import (
    ApiKeyIdentityProvider,
    JwksLoader,
    LocalIdentityProvider,
    OidcIdentityProvider,
)


def test_local_provider_returns_only_its_configured_principal() -> None:
    provider = LocalIdentityProvider(
        subject="local-operator",
        tenant_id="default",
        roles=frozenset({"admin"}),
    )

    principal = provider.authenticate(api_key=None, bearer_token=None)

    assert principal.subject == "local-operator"
    assert principal.tenant_id == "default"
    assert principal.roles == frozenset({"admin"})
    assert principal.authentication_method == "local"


def test_api_key_provider_uses_constant_time_secret_and_service_identity() -> None:
    provider = ApiKeyIdentityProvider(
        expected_api_key=SecretStr("service-account-secret"),
        subject="planning-worker",
        tenant_id="northstar",
        roles=frozenset({"analyst"}),
    )

    principal = provider.authenticate(
        api_key="service-account-secret",
        bearer_token=None,
    )

    assert principal.subject == "planning-worker"
    assert principal.tenant_id == "northstar"
    assert principal.roles == frozenset({"analyst"})
    assert principal.authentication_method == "api_key"

    for supplied in (None, "wrong-secret"):
        with pytest.raises(AuthenticationError, match="credentials"):
            provider.authenticate(api_key=supplied, bearer_token=None)


def test_jwks_loader_enforces_response_size_limit() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=b"x" * 257,
            headers={"Content-Type": "application/json"},
        )
    )
    loader = JwksLoader(
        jwks_url="https://identity.example/.well-known/jwks.json",
        cache_ttl_seconds=300,
        timeout_seconds=2,
        max_response_bytes=256,
        client=httpx.Client(transport=transport),
    )

    with pytest.raises(AuthenticationError, match="identity provider"):
        loader.get_key("key-1", "RS256")


def test_oidc_provider_validates_signature_and_required_claims() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "key-1", "alg": "RS256", "use": "sig"})
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"keys": [public_jwk]},
            headers={"Content-Type": "application/json"},
        )
    )
    loader = JwksLoader(
        jwks_url="https://identity.example/.well-known/jwks.json",
        cache_ttl_seconds=300,
        timeout_seconds=2,
        max_response_bytes=16_384,
        client=httpx.Client(transport=transport),
    )
    provider = OidcIdentityProvider(
        issuer="https://identity.example/",
        audience="openenterprise-twin",
        algorithms=("RS256",),
        tenant_claim="tenant_id",
        roles_claim="roles",
        clock_skew_seconds=15,
        jwks_loader=loader,
    )
    now = datetime.now(UTC)
    claims = {
        "iss": "https://identity.example/",
        "aud": "openenterprise-twin",
        "sub": "user:finance-01",
        "tenant_id": "northstar",
        "roles": ["analyst", "viewer"],
        "iat": now,
        "nbf": now - timedelta(seconds=1),
        "exp": now + timedelta(minutes=5),
    }
    token = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )

    principal = provider.authenticate(api_key=None, bearer_token=token)

    assert principal.subject == "user:finance-01"
    assert principal.tenant_id == "northstar"
    assert principal.roles == frozenset({"analyst", "viewer"})
    assert principal.authentication_method == "oidc"

    forged = jwt.encode(
        claims,
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        algorithm="RS256",
        headers={"kid": "key-1"},
    )
    with pytest.raises(AuthenticationError, match="token"):
        provider.authenticate(api_key=None, bearer_token=forged)


@pytest.mark.parametrize(
    ("claim", "value"),
    (
        ("iss", "https://attacker.example/"),
        ("aud", "another-api"),
        ("tenant_id", "../other"),
        ("roles", ["root"]),
        ("roles", "analyst"),
    ),
)
def test_oidc_provider_rejects_invalid_identity_claims(
    claim: str,
    value: object,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "key-1", "alg": "RS256", "use": "sig"})
    loader = JwksLoader(
        jwks_url="https://identity.example/jwks",
        cache_ttl_seconds=300,
        timeout_seconds=2,
        max_response_bytes=16_384,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"keys": [public_jwk]})
            )
        ),
    )
    provider = OidcIdentityProvider(
        issuer="https://identity.example/",
        audience="openenterprise-twin",
        algorithms=("RS256",),
        tenant_claim="tenant_id",
        roles_claim="roles",
        clock_skew_seconds=0,
        jwks_loader=loader,
    )
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": "https://identity.example/",
        "aud": "openenterprise-twin",
        "sub": "user-1",
        "tenant_id": "northstar",
        "roles": ["viewer"],
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    claims[claim] = value
    token = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )

    with pytest.raises(AuthenticationError):
        provider.authenticate(api_key=None, bearer_token=token)

