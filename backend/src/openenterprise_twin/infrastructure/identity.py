"""Concrete local, API-key and OIDC identity providers."""

import json
from collections.abc import Mapping
from secrets import compare_digest
from time import monotonic
from typing import cast

import httpx
import jwt
from jwt import PyJWK
from pydantic import SecretStr

from openenterprise_twin.application.identity import (
    AuthenticationError,
    IdentityProvider,
    Principal,
    Role,
)
from openenterprise_twin.infrastructure.settings import Settings

_JWKS_UNAVAILABLE = "The identity provider keys are unavailable."
_TOKEN_REJECTED = "The bearer token could not be authenticated."


class LocalIdentityProvider:
    """Development-only identity with no transport credential."""

    def __init__(
        self,
        *,
        subject: str,
        tenant_id: str,
        roles: frozenset[Role],
    ) -> None:
        self._principal = Principal(
            subject=subject,
            tenant_id=tenant_id,
            roles=roles,
            authentication_method="local",
        )

    def authenticate(
        self,
        *,
        api_key: str | None,
        bearer_token: str | None,
    ) -> Principal:
        del api_key, bearer_token
        return self._principal


class ApiKeyIdentityProvider:
    """Constant-time API-key authentication for one service account."""

    def __init__(
        self,
        *,
        expected_api_key: SecretStr,
        subject: str,
        tenant_id: str,
        roles: frozenset[Role],
    ) -> None:
        self._expected_api_key = expected_api_key
        self._principal = Principal(
            subject=subject,
            tenant_id=tenant_id,
            roles=roles,
            authentication_method="api_key",
        )

    def authenticate(
        self,
        *,
        api_key: str | None,
        bearer_token: str | None,
    ) -> Principal:
        if bearer_token is not None or api_key is None:
            raise AuthenticationError()
        expected = self._expected_api_key.get_secret_value()
        if not compare_digest(api_key, expected):
            raise AuthenticationError()
        return self._principal


class JwksLoader:
    """Bounded, TTL-cached loader for a configured JWKS endpoint."""

    def __init__(
        self,
        *,
        jwks_url: str,
        cache_ttl_seconds: int,
        timeout_seconds: float,
        max_response_bytes: int,
        client: httpx.Client | None = None,
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._jwks_url = jwks_url
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
        )
        self._keys: dict[str, PyJWK] = {}
        self._expires_at = 0.0

    def get_key(self, key_id: str, algorithm: str) -> PyJWK:
        """Return an exact key/algorithm match, refreshing once for rotation."""

        if not key_id or len(key_id) > 256:
            raise AuthenticationError(_TOKEN_REJECTED)
        now = monotonic()
        if now >= self._expires_at:
            self._refresh(now)
        key = self._matching_key(key_id, algorithm)
        if key is None:
            self._refresh(monotonic())
            key = self._matching_key(key_id, algorithm)
        if key is None:
            raise AuthenticationError(_TOKEN_REJECTED)
        return key

    def _matching_key(self, key_id: str, algorithm: str) -> PyJWK | None:
        key = self._keys.get(key_id)
        if key is None or key.algorithm_name != algorithm:
            return None
        return key

    def _refresh(self, now: float) -> None:
        try:
            body = self._fetch_body()
            payload = json.loads(body)
            keys_value = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(keys_value, list) or not keys_value:
                raise ValueError("JWKS keys are missing")
            parsed: dict[str, PyJWK] = {}
            for value in keys_value:
                if not isinstance(value, Mapping):
                    raise ValueError("JWKS key is not an object")
                key_id = value.get("kid")
                if not isinstance(key_id, str) or not key_id or key_id in parsed:
                    raise ValueError("JWKS key id is missing or duplicated")
                key = PyJWK.from_dict(dict(value))
                if key.public_key_use not in {None, "sig"}:
                    continue
                parsed[key_id] = key
            if not parsed:
                raise ValueError("JWKS has no signing keys")
        except AuthenticationError:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise AuthenticationError(_JWKS_UNAVAILABLE) from error
        self._keys = parsed
        self._expires_at = now + self._cache_ttl_seconds

    def _fetch_body(self) -> bytes:
        chunks: list[bytes] = []
        received = 0
        with self._client.stream(
            "GET",
            self._jwks_url,
            headers={"Accept": "application/json, application/jwk-set+json"},
            timeout=self._timeout_seconds,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "json" not in content_type:
                raise AuthenticationError(_JWKS_UNAVAILABLE)
            content_length = response.headers.get("Content-Length")
            if (
                content_length is not None
                and int(content_length) > self._max_response_bytes
            ):
                raise AuthenticationError(_JWKS_UNAVAILABLE)
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > self._max_response_bytes:
                    raise AuthenticationError(_JWKS_UNAVAILABLE)
                chunks.append(chunk)
        return b"".join(chunks)


class OidcIdentityProvider:
    """Validate OIDC access tokens against an exact deployment contract."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...],
        tenant_claim: str,
        roles_claim: str,
        clock_skew_seconds: int,
        jwks_loader: JwksLoader,
    ) -> None:
        if not algorithms:
            raise ValueError("algorithms must not be empty")
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._tenant_claim = tenant_claim
        self._roles_claim = roles_claim
        self._clock_skew_seconds = clock_skew_seconds
        self._jwks_loader = jwks_loader

    def authenticate(
        self,
        *,
        api_key: str | None,
        bearer_token: str | None,
    ) -> Principal:
        if api_key is not None or bearer_token is None:
            raise AuthenticationError()
        try:
            header = jwt.get_unverified_header(bearer_token)
            algorithm = header.get("alg")
            key_id = header.get("kid")
            if (
                not isinstance(algorithm, str)
                or algorithm not in self._algorithms
                or not isinstance(key_id, str)
            ):
                raise AuthenticationError(_TOKEN_REJECTED)
            signing_key = self._jwks_loader.get_key(key_id, algorithm)
            payload = jwt.decode(
                bearer_token,
                key=signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._clock_skew_seconds,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "iat",
                        "nbf",
                        "exp",
                        self._tenant_claim,
                        self._roles_claim,
                    ]
                },
            )
            subject = payload["sub"]
            tenant_id = payload[self._tenant_claim]
            roles_value = payload[self._roles_claim]
            if (
                not isinstance(subject, str)
                or not isinstance(tenant_id, str)
                or not isinstance(roles_value, list)
                or not roles_value
                or not all(isinstance(role, str) for role in roles_value)
            ):
                raise AuthenticationError(_TOKEN_REJECTED)
            roles = cast(frozenset[Role], frozenset(roles_value))
            return Principal(
                subject=subject,
                tenant_id=tenant_id,
                roles=roles,
                authentication_method="oidc",
            )
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, KeyError, TypeError, ValueError) as error:
            raise AuthenticationError(_TOKEN_REJECTED) from error


def build_identity_provider(settings: Settings) -> IdentityProvider:
    """Build exactly the identity mode selected by deployment settings."""

    if settings.authentication_mode == "local":
        return LocalIdentityProvider(
            subject=settings.local_subject,
            tenant_id=settings.local_tenant_id,
            roles=frozenset(settings.local_roles),
        )
    if settings.authentication_mode == "api_key":
        assert settings.api_key is not None
        return ApiKeyIdentityProvider(
            expected_api_key=settings.api_key,
            subject=settings.service_account_subject,
            tenant_id=settings.service_account_tenant_id,
            roles=frozenset(settings.service_account_roles),
        )
    assert settings.oidc_issuer is not None
    assert settings.oidc_audience is not None
    assert settings.oidc_jwks_url is not None
    return OidcIdentityProvider(
        issuer=str(settings.oidc_issuer),
        audience=settings.oidc_audience,
        algorithms=settings.oidc_algorithms,
        tenant_claim=settings.oidc_tenant_claim,
        roles_claim=settings.oidc_roles_claim,
        clock_skew_seconds=settings.oidc_clock_skew_seconds,
        jwks_loader=JwksLoader(
            jwks_url=str(settings.oidc_jwks_url),
            cache_ttl_seconds=settings.oidc_jwks_cache_ttl_seconds,
            timeout_seconds=settings.oidc_http_timeout_seconds,
            max_response_bytes=settings.oidc_jwks_max_response_bytes,
        ),
    )
