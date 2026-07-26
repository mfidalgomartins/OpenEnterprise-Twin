"""Contracts for environment-backed infrastructure settings."""

import pytest
from pydantic import SecretStr, ValidationError
from pytest import MonkeyPatch

from openenterprise_twin.infrastructure.settings import Settings


def test_settings_loads_optional_build_commit_from_environment(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENENTERPRISE_TWIN_BUILD_COMMIT", "a1b2c3d4")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.build_commit == "a1b2c3d4"


@pytest.mark.parametrize(
    "invalid_build_commit",
    (
        "https://config.example/build/a1b2c3d4",
        "a1b2c3d4\nsecret",
        "a" * 65,
        "abcdefg",
    ),
    ids=("url", "newline", "oversized", "non_hex"),
)
def test_settings_rejects_unsafe_build_commit(
    monkeypatch: MonkeyPatch,
    invalid_build_commit: str,
) -> None:
    monkeypatch.setenv(
        "OPENENTERPRISE_TWIN_BUILD_COMMIT",
        invalid_build_commit,
    )

    with pytest.raises(ValidationError, match="build_commit"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_production_accepts_explicit_api_key_service_account() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        deployment_environment="production",
        authentication_mode="api_key",
        api_key=SecretStr("x" * 32),
        service_account_subject="planning-worker",
        service_account_tenant_id="northstar",
        service_account_roles=("analyst",),
        trusted_hosts=("enterprise.example",),
    )

    assert settings.authentication_mode == "api_key"
    assert settings.service_account_roles == ("analyst",)


def test_production_accepts_complete_oidc_configuration() -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        deployment_environment="production",
        authentication_mode="oidc",
        oidc_issuer="https://identity.example/",
        oidc_audience="openenterprise-twin",
        oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        trusted_hosts=("enterprise.example",),
    )

    assert settings.authentication_mode == "oidc"
    assert settings.oidc_algorithms == ("RS256",)


@pytest.mark.parametrize(
    "values",
    (
        {},
        {"authentication_mode": "local"},
        {"authentication_mode": "api_key"},
        {
            "authentication_mode": "api_key",
            "api_key": SecretStr("short"),
        },
        {"authentication_mode": "oidc"},
        {
            "authentication_mode": "oidc",
            "oidc_issuer": "http://identity.example/",
            "oidc_audience": "openenterprise-twin",
            "oidc_jwks_url": "https://identity.example/jwks",
        },
        {
            "authentication_mode": "oidc",
            "oidc_issuer": "https://identity.example/",
            "oidc_audience": "openenterprise-twin",
            "oidc_jwks_url": "http://identity.example/jwks",
        },
    ),
)
def test_production_rejects_incomplete_or_unsafe_authentication(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="authentication"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            deployment_environment="production",
            trusted_hosts=("enterprise.example",),
            **values,
        )


@pytest.mark.parametrize(
    "algorithms",
    (
        (),
        ("HS256",),
        ("RS256", "none"),
    ),
)
def test_settings_rejects_unsafe_oidc_algorithm_allowlist(
    algorithms: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError, match="oidc_algorithms"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            authentication_mode="oidc",
            oidc_issuer="https://identity.example/",
            oidc_audience="openenterprise-twin",
            oidc_jwks_url="https://identity.example/jwks",
            oidc_algorithms=algorithms,
        )
