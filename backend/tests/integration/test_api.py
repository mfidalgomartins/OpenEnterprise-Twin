"""End-to-end contracts for durable scenario experiments."""

from pathlib import Path
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from openenterprise_twin.api.app import create_app
from openenterprise_twin.application import experiments as experiment_service
from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.infrastructure.settings import Settings
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


def test_execution_failure_reaches_a_stable_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_experiment(request: object) -> None:
        raise RuntimeError("synthetic execution failure")

    monkeypatch.setattr(experiment_service, "run_experiment", fail_experiment)
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
    assert result["error_detail"] == "synthetic execution failure"


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
