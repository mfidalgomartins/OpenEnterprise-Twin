"""End-to-end contracts for the governed decision-loop API."""

from pathlib import Path

from fastapi.testclient import TestClient

from openenterprise_twin.api.app import create_app
from openenterprise_twin.domain.ledger import DecisionContent
from openenterprise_twin.infrastructure.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'loop.db'}",
        artifact_directory=tmp_path / "artifacts",
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
        max_optimization_evaluations=60,
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_settings(tmp_path)))


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


def test_calibration_studio_flow(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        ingest = client.post(
            "/api/v1/datasets/synthetic",
            json={"dataset_id": "northstar-history", "days": 540},
        )
        assert ingest.status_code == 201
        body = ingest.json()
        assert body["quality"]["quality_score"] == 1.0
        assert body["dataset"]["observation_count"] > 1000

        calibrate = client.post(
            "/api/v1/calibrations",
            json={
                "calibration_id": "cal-1",
                "dataset_id": "northstar-history",
                "backtest_cutoff": "2024-12-31",
            },
        )
        assert calibrate.status_code == 201
        credibility = calibrate.json()["credibility"]
        assert credibility["band"] == "decision_grade"
        assert credibility["score"] >= 80.0
        assert calibrate.json()["backtests"]


def test_duplicate_dataset_conflicts(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        first = client.post(
            "/api/v1/datasets/synthetic", json={"dataset_id": "d", "days": 120}
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/datasets/synthetic", json={"dataset_id": "d", "days": 120}
        )
        assert second.status_code == 422
        assert second.json()["code"] == "domain_validation"


def test_optimization_flow_and_budget_cap(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        config = {
            "objectives": [
                {"metric_name": "ebitda", "direction": "maximize"},
                {"metric_name": "otif", "direction": "maximize"},
            ],
            "levers": [
                {
                    "lever_id": "ci",
                    "kind": "commercial_investment",
                    "lower": -0.1,
                    "upper": 0.3,
                }
            ],
            "population_size": 8,
            "max_generations": 3,
            "max_evaluations": 30,
            "seed": 5,
        }
        response = client.post(
            "/api/v1/optimizations",
            json={
                "config": config,
                "horizon_days": 60,
                "replications": 4,
                "master_seed": 5,
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["result"]["frontier"]
        assert payload["evaluations"] <= 40

        over_budget = dict(config)
        over_budget["max_evaluations"] = 5000
        rejected = client.post(
            "/api/v1/optimizations",
            json={"config": over_budget, "horizon_days": 60, "replications": 2},
        )
        assert rejected.status_code == 422


def test_adaptive_compute_budget_is_capped(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        policy = {
            "policy_id": "capacity",
            "rules": [
                {
                    "rule_id": "cap",
                    "metric": "backlog_days",
                    "operator": "gt",
                    "threshold": 8.0,
                    "action": {
                        "type": "add_overtime_capacity",
                        "target_id": "assembly",
                        "magnitude": "0.1",
                    },
                }
            ],
        }
        response = client.post(
            "/api/v1/adaptive-policies/compare",
            json={
                "policy": policy,
                "horizon_days": 730,
                "replications": 200,
                "master_seed": 5,
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "adaptive_budget_exceeded"


def test_adaptive_contradiction_is_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        policy = {
            "policy_id": "bad",
            "rules": [
                {
                    "rule_id": "up",
                    "metric": "demand_change",
                    "operator": "gt",
                    "threshold": -0.2,
                    "action": {
                        "type": "increase_commercial_investment",
                        "magnitude": "0.1",
                    },
                },
                {
                    "rule_id": "down",
                    "metric": "demand_change",
                    "operator": "lt",
                    "threshold": -0.05,
                    "action": {
                        "type": "reduce_commercial_investment",
                        "magnitude": "0.1",
                    },
                },
            ],
        }
        response = client.post("/api/v1/adaptive-policies/validate", json=policy)
        assert response.status_code == 422


def test_decision_lifecycle_and_optimistic_conflict(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/v1/ledger/decisions",
            json={"decision_id": "dec-1", "content": _decision_content()},
        )
        assert created.status_code == 201
        assert created.json()["state"] == "draft"

        assert (
            client.post(
                "/api/v1/ledger/decisions/dec-1/transitions",
                json={
                    "expected_version": 1,
                    "target": "evidence_ready",
                    "actor": "cfo",
                },
            ).status_code
            == 200
        )
        # Stale version is rejected by optimistic concurrency control.
        conflict = client.post(
            "/api/v1/ledger/decisions/dec-1/transitions",
            json={
                "expected_version": 1,
                "target": "under_review",
                "actor": "cfo",
            },
        )
        assert conflict.status_code == 409

        client.post(
            "/api/v1/ledger/decisions/dec-1/transitions",
            json={"expected_version": 2, "target": "under_review", "actor": "cfo"},
        )
        snapshot = client.get("/api/v1/ledger/decisions/dec-1").json()
        digest = DecisionContent.model_validate(snapshot["content"]).content_digest()
        approved = client.post(
            "/api/v1/ledger/decisions/dec-1/transitions",
            json={
                "expected_version": 3,
                "target": "approved",
                "actor": "ceo",
                "approval": {
                    "approver": "ceo",
                    "decision": "approve",
                    "occurred_at": "2026-07-23T12:00:00Z",
                    "approved_content_digest": digest,
                },
            },
        )
        assert approved.status_code == 200
        assert approved.json()["state"] == "approved"

        packet = client.get("/api/v1/ledger/decisions/dec-1/packet")
        assert packet.status_code == 200
        assert packet.json()["state"] == "approved"


def test_self_approval_is_rejected_over_http(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/ledger/decisions",
            json={"decision_id": "dec-1", "content": _decision_content()},
        )
        client.post(
            "/api/v1/ledger/decisions/dec-1/transitions",
            json={"expected_version": 1, "target": "evidence_ready", "actor": "cfo"},
        )
        client.post(
            "/api/v1/ledger/decisions/dec-1/transitions",
            json={"expected_version": 2, "target": "under_review", "actor": "cfo"},
        )
        snapshot = client.get("/api/v1/ledger/decisions/dec-1").json()
        digest = DecisionContent.model_validate(snapshot["content"]).content_digest()
        response = client.post(
            "/api/v1/ledger/decisions/dec-1/transitions",
            json={
                "expected_version": 3,
                "target": "approved",
                "actor": "cfo",
                "approval": {
                    "approver": "cfo",
                    "decision": "approve",
                    "occurred_at": "2026-07-23T12:00:00Z",
                    "approved_content_digest": digest,
                },
            },
        )
        assert response.status_code == 422


def test_monitoring_flow(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.post(
            "/api/v1/ledger/decisions",
            json={"decision_id": "dec-1", "content": _decision_content()},
        )
        outcomes = client.post(
            "/api/v1/ledger/decisions/dec-1/outcomes",
            json={
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
            },
        )
        assert outcomes.status_code == 201
        assert outcomes.json()["recommended_level"] == "decision_review_required"

        latest = client.get("/api/v1/ledger/decisions/dec-1/monitoring")
        assert latest.status_code == 200
        assert latest.json()["alerts"]


def test_missing_monitoring_is_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/ledger/decisions/ghost/monitoring")
        assert response.status_code == 404
