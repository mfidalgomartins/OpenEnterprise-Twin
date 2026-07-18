"""End-to-end contracts for durable scenario experiments."""

from datetime import UTC, datetime
from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from openenterprise_twin.api import app as app_module
from openenterprise_twin.api.app import create_app
from openenterprise_twin.application.experiments import ExperimentQueueFullError
from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.infrastructure import runner as experiment_service
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.models import (
    Base,
    ExperimentRecord,
    ScenarioRecord,
)
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.experiment import ExperimentArtifact
from openenterprise_twin.simulation.reference import build_baseline_scenario


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'twin.db'}",
        artifact_directory=tmp_path / "artifacts",
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
    )


def _wait_for_experiment(
    client: TestClient,
    location: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = client.get(location)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        sleep(0.01)
    raise AssertionError("experiment did not reach a terminal state")


def test_create_run_compare_and_report(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    baseline = build_baseline_scenario(horizon_days=5)
    candidate = baseline.model_copy(
        update={
            "scenario_id": "pricing-candidate",
            "name": "Pricing candidate",
            "baseline_scenario_id": baseline.scenario_id,
            "policy_levers": PolicyLevers(commercial_investment_change="0.05"),
        }
    )

    with TestClient(app) as client:
        for scenario in (baseline, candidate):
            response = client.post(
                "/api/v1/scenarios",
                json=scenario.model_dump(mode="json"),
            )
            assert response.status_code == 201
            assert response.headers["location"].endswith(scenario.scenario_id)

        baseline_response = client.post(
            f"/api/v1/scenarios/{baseline.scenario_id}/experiments",
            json={"replications": 3, "master_seed": 731, "max_workers": 1},
            headers={"Idempotency-Key": "baseline-731"},
        )
        assert baseline_response.status_code == 202
        baseline_run = _wait_for_experiment(
            client, baseline_response.headers["location"]
        )
        assert baseline_run["status"] == "completed"
        assert baseline_run["artifact_digest"]

        candidate_response = client.post(
            f"/api/v1/scenarios/{candidate.scenario_id}/experiments",
            json={"replications": 3, "master_seed": 731, "max_workers": 1},
            headers={"Idempotency-Key": "candidate-731"},
        )
        assert candidate_response.status_code == 202
        candidate_run = _wait_for_experiment(
            client, candidate_response.headers["location"]
        )
        assert candidate_run["status"] == "completed"
        assert candidate_run["baseline_experiment_id"] == baseline_run["id"]

        comparison_response = client.get(
            f"/api/v1/experiments/{candidate_run['id']}/comparison"
        )
        assert comparison_response.status_code == 200
        comparison = comparison_response.json()
        assert comparison["baseline_experiment_digest"]
        assert comparison["candidate_experiment_digest"]
        assert len(comparison["metric_results"]) == 10

        report_response = client.get(
            f"/api/v1/experiments/{candidate_run['id']}/report"
        )
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["decision_status"] in {
            "adopt",
            "conditional",
            "do_not_adopt",
        }
        assert report["provenance"]["comparison_digest"] == comparison["digest"]

        original_created_at = report["provenance"]["created_at"]
        with app.state.services.session_factory() as session, session.begin():
            stored = session.get(ExperimentRecord, int(candidate_run["id"]))
            assert stored is not None
            assert stored.brief_payload is not None
            legacy_payload = dict(stored.brief_payload)
            legacy_payload.pop("governance")
            legacy_payload.pop("actions")
            legacy_payload.pop("brief_schema_version")
            stored.brief_payload = legacy_payload

        upgraded_response = client.get(
            f"/api/v1/experiments/{candidate_run['id']}/report"
        )
        assert upgraded_response.status_code == 200
        upgraded = upgraded_response.json()
        assert upgraded["brief_schema_version"] == "0.2.0"
        assert upgraded["governance"]["decision_owner"] == "Managing Director"
        assert upgraded["actions"]
        assert upgraded["provenance"]["created_at"] == original_created_at

        repeated_response = client.get(
            f"/api/v1/experiments/{candidate_run['id']}/report"
        )
        assert repeated_response.json()["digest"] == upgraded["digest"]


def test_api_exposes_reference_resources_and_scenario_collection(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=2)

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        company = client.get("/api/v1/company")
        baseline = client.get("/api/v1/baseline")
        created = client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        scenarios = client.get("/api/v1/scenarios")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert company.status_code == 200
    assert company.json()["model_version"] == scenario.company_model_version
    assert baseline.status_code == 200
    assert baseline.json()["id"] == scenario.scenario_id
    assert created.status_code == 201
    assert created.json()["id"] == scenario.scenario_id
    assert scenarios.status_code == 200
    assert [item["id"] for item in scenarios.json()] == [scenario.scenario_id]


def test_canonical_experiment_contract_accepts_iterations_and_seed(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=2)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        response = client.post(
            f"/api/v1/scenarios/{created.json()['id']}/experiments",
            json={"iterations": 1, "seed": 731},
        )

    assert response.status_code == 202
    assert response.json()["iterations"] == 1
    assert response.json()["seed"] == 731


def test_scenario_creation_rejects_unknown_company_model(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={"company_model_version": "999.0.0"}
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )

    assert response.status_code == 422
    assert response.json()["code"] == "scenario_incompatible"


def test_experiment_creation_rejects_seed_outside_bigint(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=2)

    with TestClient(app) as client:
        client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        response = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"iterations": 1, "seed": 2**63},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation"


def test_experiment_creation_is_idempotent(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=3)

    with TestClient(app) as client:
        client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        first = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"replications": 1, "master_seed": 9, "max_workers": 1},
            headers={"Idempotency-Key": "same-request"},
        )
        second = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"replications": 1, "master_seed": 9, "max_workers": 1},
            headers={"Idempotency-Key": "same-request"},
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["id"] == second.json()["id"]

        conflict = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"replications": 2, "master_seed": 9, "max_workers": 1},
            headers={"Idempotency-Key": "same-request"},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"


def test_queue_saturation_does_not_poison_idempotent_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlwaysFullRunner:
        def __init__(self, **_: object) -> None:
            pass

        def submit(self, experiment_id: int) -> None:
            del experiment_id
            raise ExperimentQueueFullError("experiment execution queue is full")

        def recover_pending(self) -> None:
            pass

        def shutdown(self, timeout_seconds: float) -> None:
            del timeout_seconds
            pass

    monkeypatch.setattr(app_module, "BoundedExperimentRunner", AlwaysFullRunner)
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=2)

    with TestClient(app) as client:
        client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        responses = [
            client.post(
                f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
                json={"iterations": 1, "seed": 9},
                headers={"Idempotency-Key": "retry-after-capacity"},
            )
            for _ in range(2)
        ]

    assert [response.status_code for response in responses] == [429, 429]
    assert all(
        response.json()["code"] == "experiment_queue_full"
        for response in responses
    )


def test_startup_recovers_persisted_running_experiment(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    scenario = build_baseline_scenario(horizon_days=2)
    engine = create_database_engine(settings)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        session.add(
            ScenarioRecord(
                scenario_id=scenario.scenario_id,
                name=scenario.name,
                company_model_version=scenario.company_model_version,
                scenario_schema_version=scenario.schema_version,
                payload=scenario.model_dump(mode="json"),
            )
        )
    with session_factory.begin() as session:
        session.add(
            ExperimentRecord(
                scenario_id=scenario.scenario_id,
                status="running",
                master_seed=91,
                replication_count=1,
                request_payload={
                    "replications": 1,
                    "master_seed": 91,
                    "max_workers": 1,
                },
                started_at=datetime.now(UTC),
            )
        )
    engine.dispose()

    app = create_app(settings)
    with TestClient(app) as client:
        result = _wait_for_experiment(
            client,
            "/api/v1/experiments/1",
            timeout_seconds=0.5,
        )

    assert result["status"] == "completed"
    assert result["artifact_digest"]


def test_completed_experiment_persists_full_trace_artifact(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=3)

    with TestClient(app) as client:
        client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        response = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"iterations": 1, "seed": 731},
        )
        result = _wait_for_experiment(client, response.headers["location"])
        payload = app.state.services.artifact_store.get_json(
            result["artifact_digest"]
        )

    artifact = ExperimentArtifact.model_validate(payload)
    assert artifact.result.replication_count == 1
    assert len(artifact.traces) == 1
    assert len(artifact.traces[0].periods) == 3


def test_candidate_rejects_incompatible_completed_baseline(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    baseline = build_baseline_scenario(horizon_days=1)
    candidate = build_baseline_scenario(horizon_days=2).model_copy(
        update={
            "scenario_id": "incompatible-candidate",
            "name": "Incompatible candidate",
            "baseline_scenario_id": baseline.scenario_id,
        }
    )

    with TestClient(app) as client:
        for scenario in (baseline, candidate):
            assert client.post(
                "/api/v1/scenarios",
                json=scenario.model_dump(mode="json"),
            ).status_code == 201
        baseline_response = client.post(
            f"/api/v1/scenarios/{baseline.scenario_id}/experiments",
            json={"iterations": 1, "seed": 731},
        )
        assert _wait_for_experiment(
            client,
            baseline_response.headers["location"],
        )["status"] == "completed"
        candidate_response = client.post(
            f"/api/v1/scenarios/{candidate.scenario_id}/experiments",
            json={"iterations": 1, "seed": 731},
        )

    assert candidate_response.status_code == 409
    assert candidate_response.json()["code"] == "baseline_experiment_incompatible"


def test_execution_failure_reaches_a_stable_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_experiment(request: object) -> None:
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr(
        experiment_service,
        "run_experiment_with_traces",
        fail_experiment,
    )
    app = create_app(_settings(tmp_path))
    scenario = build_baseline_scenario(horizon_days=3)

    with TestClient(app) as client:
        client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        response = client.post(
            f"/api/v1/scenarios/{scenario.scenario_id}/experiments",
            json={"replications": 1, "master_seed": 9, "max_workers": 1},
        )
        result = _wait_for_experiment(client, response.headers["location"])

    assert result["status"] == "failed"
    assert result["error_code"] == "experiment_execution"
    assert result["error_detail"] == "Experiment execution failed."


def test_api_returns_problem_details_for_missing_and_invalid_resources(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        missing = client.get("/api/v1/scenarios/missing-scenario")
        invalid = client.post(
            "/api/v1/scenarios",
            json={"scenario_id": "INVALID"},
        )

    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["code"] == "scenario_not_found"
    assert missing.json()["trace_id"]
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert invalid.json()["code"] == "request_validation"
    assert invalid.json()["violations"]


def test_cors_allows_only_explicitly_configured_development_origin(
    tmp_path: Path,
) -> None:
    configured = _settings(tmp_path).model_copy(
        update={"cors_allowed_origins": ("http://127.0.0.1:5173",)}
    )
    app = create_app(configured)

    with TestClient(app) as client:
        allowed = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == (
        "http://127.0.0.1:5173"
    )
    assert "access-control-allow-origin" not in denied.headers
