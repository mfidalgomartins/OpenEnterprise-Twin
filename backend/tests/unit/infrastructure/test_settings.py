"""Contracts for environment-backed infrastructure settings."""

import pytest
from pydantic import ValidationError
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
