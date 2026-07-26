"""Cross-tenant API isolation across persisted business resources."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient

from openenterprise_twin.api.app import create_app
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.repositories import (
    ExperimentRepository,
    ScenarioRepository,
    SqlAlchemyDecisionEvidenceRepository,
    SqlCalibrationRepository,
    SqlDatasetRepository,
    SqlDecisionLedgerRepository,
    SqlMonitoringRepository,
    SqlOptimizationRepository,
)
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.reference import build_baseline_scenario


@contextmanager
def _tenant_client(
    tmp_path: Path,
    tenant_id: str,
) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'shared.db'}",
        artifact_directory=tmp_path / "artifacts",
        deployment_environment="test",
        authentication_mode="local",
        local_subject=f"user:{tenant_id}",
        local_tenant_id=tenant_id,
        local_roles=("admin",),
        experiment_workers=1,
        replication_workers_per_experiment=1,
        database_pool_size=2,
        database_max_overflow=0,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _wait_for_terminal(client: TestClient, location: str) -> dict[str, object]:
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(location)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        sleep(0.02)
    raise AssertionError("experiment did not reach a terminal state")


def _decision_content() -> dict[str, object]:
    return {
        "title": "Raise contracted pricing 3%",
        "owner": "cfo",
        "context": "Margin recovery under stable demand.",
        "objectives": ["grow ebitda"],
        "company_model_version": "0.2.0",
        "recommendation": "Adopt the +3% contracted price policy.",
        "chosen_alternative": "price-plus-3",
        "justification": "Paired experiment shows a material EBITDA gain.",
        "evidence": {"experiment_ids": [1]},
    }


def test_tenant_sensitive_repositories_cannot_be_constructed_without_tenant(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'constructors.db'}",
        artifact_directory=tmp_path / "constructor-artifacts",
    )
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        for repository in (ScenarioRepository, ExperimentRepository):
            try:
                repository(session)  # type: ignore[call-arg]
            except TypeError:
                pass
            else:
                raise AssertionError(f"{repository.__name__} accepted no tenant")
    for repository in (
        SqlAlchemyDecisionEvidenceRepository,
        SqlDatasetRepository,
        SqlCalibrationRepository,
        SqlOptimizationRepository,
        SqlMonitoringRepository,
        SqlDecisionLedgerRepository,
    ):
        try:
            repository(session_factory)  # type: ignore[call-arg]
        except TypeError:
            pass
        else:
            raise AssertionError(f"{repository.__name__} accepted no tenant")
    engine.dispose()


def test_scenarios_experiments_and_idempotency_are_tenant_scoped(
    tmp_path: Path,
) -> None:
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={"scenario_id": "shared-scenario"}
    )
    request = {"iterations": 1, "seed": 731}
    headers = {"Idempotency-Key": "shared-submission"}

    with _tenant_client(tmp_path, "tenant-a") as tenant_a:
        assert tenant_a.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        ).status_code == 201
        submitted_a = tenant_a.post(
            "/api/v1/scenarios/shared-scenario/experiments",
            json=request,
            headers=headers,
        )
        assert submitted_a.status_code == 202
        experiment_a = _wait_for_terminal(
            tenant_a,
            submitted_a.headers["location"],
        )
        assert experiment_a["status"] == "completed"

    with _tenant_client(tmp_path, "tenant-b") as tenant_b:
        assert tenant_b.get("/api/v1/scenarios/shared-scenario").status_code == 404
        assert tenant_b.get(
            f"/api/v1/experiments/{experiment_a['id']}"
        ).status_code == 404
        assert tenant_b.get(
            f"/api/v1/experiments/{experiment_a['id']}/report"
        ).status_code == 404
        assert tenant_b.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        ).status_code == 201
        submitted_b = tenant_b.post(
            "/api/v1/scenarios/shared-scenario/experiments",
            json=request,
            headers=headers,
        )
        assert submitted_b.status_code == 202
        assert submitted_b.json()["id"] != experiment_a["id"]


def test_datasets_calibrations_decisions_and_monitoring_are_tenant_scoped(
    tmp_path: Path,
) -> None:
    decision_request = {
        "decision_id": "shared-decision",
        "content": _decision_content(),
    }
    outcome_request = {
        "predictions": [
            {
                "metric_name": "ebitda",
                "expected_mean": 1000.0,
                "lower": 900.0,
                "upper": 1100.0,
                "improvement_direction": "higher",
            }
        ],
        "outcomes": [
            {
                "metric_name": "ebitda",
                "as_of": "2026-02-01",
                "realized_value": 500.0,
            }
        ],
    }

    with _tenant_client(tmp_path, "tenant-a") as tenant_a:
        assert tenant_a.post(
            "/api/v1/datasets/synthetic",
            json={"dataset_id": "shared-history", "days": 60},
        ).status_code == 201
        assert tenant_a.post(
            "/api/v1/calibrations",
            json={
                "calibration_id": "shared-calibration",
                "dataset_id": "shared-history",
            },
        ).status_code == 201
        assert tenant_a.post(
            "/api/v1/ledger/decisions",
            json=decision_request,
        ).status_code == 201
        assert tenant_a.post(
            "/api/v1/ledger/decisions/shared-decision/outcomes",
            json=outcome_request,
        ).status_code == 201

    with _tenant_client(tmp_path, "tenant-b") as tenant_b:
        assert tenant_b.get(
            "/api/v1/datasets/shared-history/export.csv"
        ).status_code == 404
        assert tenant_b.get(
            "/api/v1/ledger/decisions/shared-decision"
        ).status_code == 404
        assert tenant_b.get(
            "/api/v1/ledger/decisions/shared-decision/monitoring"
        ).status_code == 404

        assert tenant_b.post(
            "/api/v1/datasets/synthetic",
            json={"dataset_id": "shared-history", "days": 60},
        ).status_code == 201
        assert tenant_b.post(
            "/api/v1/calibrations",
            json={
                "calibration_id": "shared-calibration",
                "dataset_id": "shared-history",
            },
        ).status_code == 201
        assert tenant_b.post(
            "/api/v1/ledger/decisions",
            json=decision_request,
        ).status_code == 201
