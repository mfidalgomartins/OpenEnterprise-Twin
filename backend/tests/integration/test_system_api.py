"""System liveness, readiness and safe build metadata contracts."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openenterprise_twin import __version__
from openenterprise_twin.api.app import create_app
from openenterprise_twin.infrastructure.settings import Settings


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'system.db'}",
        artifact_directory=tmp_path / "artifacts",
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
        **overrides,
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


@pytest.fixture
def production_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = _settings(
        tmp_path,
        deployment_environment="production",
        api_key=SecretStr("x" * 32),
        trusted_hosts=("testserver",),
        build_commit="a1b2c3d4",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_liveness_and_readiness_have_distinct_contracts(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {"artifacts": "ready", "database": "ready"},
    }


def test_system_info_is_protected_and_safe(
    production_client: TestClient,
) -> None:
    assert production_client.get("/api/v1/system/info").status_code == 401
    response = production_client.get(
        "/api/v1/system/info", headers={"X-API-Key": "x" * 32}
    )
    assert response.status_code == 200
    assert response.json() == {
        "name": "OpenEnterprise Twin",
        "version": __version__,
        "environment": "production",
        "build_commit": "a1b2c3d4",
        "capabilities": [
            "adaptive_policies",
            "calibration",
            "decision_ledger",
            "monitoring",
            "optimization",
            "paired_simulation",
            "secure_csv",
        ],
    }
    assert "database_url" not in response.text
    assert "api_key" not in response.text
