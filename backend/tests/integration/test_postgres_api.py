"""PostgreSQL-only acceptance contract executed by the CI service container."""

import os
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from openenterprise_twin.api.app import create_app
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.reference import build_baseline_scenario

POSTGRES_TEST_URL = os.getenv("OPENENTERPRISE_TWIN_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    POSTGRES_TEST_URL is None,
    reason="requires the migrated PostgreSQL 16 CI service",
)


def test_migrated_postgres_runs_a_durable_experiment(tmp_path: Path) -> None:
    assert POSTGRES_TEST_URL is not None
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={
            "scenario_id": "postgres-contract",
            "name": "PostgreSQL contract",
        }
    )
    app = create_app(
        Settings(
            database_url=POSTGRES_TEST_URL,
            artifact_directory=tmp_path / "postgres-artifacts",
            experiment_workers=1,
            replication_workers_per_experiment=1,
            _env_file=None,
        )
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        assert created.status_code in {201, 409}
        submitted = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"iterations": 1, "seed": 987_654_321},
            headers={"Idempotency-Key": "postgres-contract-987654321"},
        )
        assert submitted.status_code == 202
        location = submitted.headers["location"]
        deadline = monotonic() + 10
        while monotonic() < deadline:
            result = client.get(location)
            assert result.status_code == 200
            if result.json()["status"] in {"completed", "failed"}:
                break
            sleep(0.05)
        else:
            raise AssertionError("PostgreSQL experiment did not terminate")

    assert result.json()["status"] == "completed"
    assert result.json()["artifact_digest"]
