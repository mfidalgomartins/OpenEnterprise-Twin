"""Contracts for environment-backed infrastructure settings."""

from pytest import MonkeyPatch

from openenterprise_twin.infrastructure.settings import Settings


def test_settings_loads_optional_build_commit_from_environment(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENENTERPRISE_TWIN_BUILD_COMMIT", "a1b2c3d4")

    settings = Settings(_env_file=None)

    assert settings.build_commit == "a1b2c3d4"
