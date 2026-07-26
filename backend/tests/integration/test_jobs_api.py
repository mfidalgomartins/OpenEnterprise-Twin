"""HTTP contracts for tenant-scoped asynchronous analytical jobs."""

from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient

from openenterprise_twin.api.app import create_app
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.reference import build_baseline_scenario


def _settings(
    tmp_path: Path,
    *,
    worker_mode: str = "external",
) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'jobs-api.db'}",
        artifact_directory=tmp_path / "artifacts",
        experiment_workers=1,
        replication_workers_per_experiment=1,
        job_worker_mode=worker_mode,
        job_workers=1,
        job_poll_interval_seconds=0.01,
        job_lease_seconds=2,
        job_heartbeat_seconds=0.25,
        job_retry_delay_seconds=0,
        _env_file=None,
    )


def _create_scenario(client: TestClient, scenario_id: str = "async") -> None:
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={"scenario_id": scenario_id, "name": "Async contract"}
    )
    response = client.post(
        "/api/v1/scenarios",
        json=scenario.model_dump(mode="json"),
    )
    assert response.status_code == 201


def test_submit_get_list_and_cancel_job_contract(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_scenario(client)
        submitted = client.post(
            "/api/v1/scenarios/async/experiments",
            json={"iterations": 1, "seed": 731},
            headers={"Idempotency-Key": "async-contract"},
        )
        assert submitted.status_code == 202
        body = submitted.json()
        assert submitted.headers["location"] == (
            f"/api/v1/jobs/{body['job_id']}"
        )
        assert body["kind"] == "experiment"
        assert body["status"] == "queued"
        assert body["progress"] == 0
        assert body["attempt_count"] == 0
        assert body["result_location"] is None
        assert "request_payload" not in body

        fetched = client.get(submitted.headers["location"])
        assert fetched.status_code == 200
        assert fetched.json() == body

        listed = client.get("/api/v1/jobs?status=queued&kind=experiment")
        assert listed.status_code == 200
        assert [item["job_id"] for item in listed.json()] == [body["job_id"]]

        cancellation = client.post(
            f"/api/v1/jobs/{body['job_id']}/cancellation"
        )
        assert cancellation.status_code == 202
        assert cancellation.json()["status"] == "cancelled"
        assert cancellation.json()["finished_at"] is not None


def test_submission_idempotency_replays_and_conflicts(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        _create_scenario(client)
        url = "/api/v1/scenarios/async/experiments"
        headers = {"Idempotency-Key": "same-request"}
        first = client.post(
            url,
            json={"iterations": 1, "seed": 731},
            headers=headers,
        )
        replay = client.post(
            url,
            json={"seed": 731, "iterations": 1},
            headers=headers,
        )
        conflict = client.post(
            url,
            json={"iterations": 1, "seed": 732},
            headers=headers,
        )

        assert first.status_code == replay.status_code == 202
        assert first.json()["job_id"] == replay.json()["job_id"]
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"


def test_job_errors_are_stable_and_tenant_safe(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        missing = client.get(
            "/api/v1/jobs/00000000-0000-4000-8000-000000000000"
        )
        missing_result = client.get(
            "/api/v1/jobs/00000000-0000-4000-8000-000000000000/result"
        )

        assert missing.status_code == 404
        assert missing.json()["code"] == "job_not_found"
        assert missing_result.status_code == 404
        assert missing_result.json()["code"] == "job_not_found"


def test_all_analytical_submissions_share_one_job_contract_and_kind_namespace(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.post(
            "/api/v1/datasets/synthetic",
            json={"dataset_id": "history", "days": 60},
        ).status_code == 201
        key = {"Idempotency-Key": "shared-across-kinds"}
        calibration = client.post(
            "/api/v1/calibrations",
            json={"calibration_id": "cal-1", "dataset_id": "history"},
            headers=key,
        )
        optimization = client.post(
            "/api/v1/optimizations",
            json={
                "config": {
                    "objectives": [
                        {"metric_name": "ebitda", "direction": "maximize"}
                    ],
                    "levers": [
                        {
                            "lever_id": "ci",
                            "kind": "commercial_investment",
                            "lower": -0.1,
                            "upper": 0.2,
                        }
                    ],
                    "population_size": 4,
                    "max_generations": 1,
                    "max_evaluations": 8,
                    "seed": 5,
                },
                "horizon_days": 30,
                "replications": 1,
                "master_seed": 5,
            },
            headers=key,
        )
        adaptive = client.post(
            "/api/v1/adaptive-policies/compare",
            json={
                "policy": {
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
                },
                "horizon_days": 30,
                "replications": 1,
                "master_seed": 5,
            },
            headers=key,
        )
        replay = client.post(
            "/api/v1/calibrations",
            json={"calibration_id": "cal-1", "dataset_id": "history"},
            headers=key,
        )

        assert {
            calibration.status_code,
            optimization.status_code,
            adaptive.status_code,
        } == {202}
        assert {
            calibration.json()["kind"],
            optimization.json()["kind"],
            adaptive.json()["kind"],
        } == {"calibration", "optimization", "adaptive_comparison"}
        assert len(
            {
                calibration.json()["job_id"],
                optimization.json()["job_id"],
                adaptive.json()["job_id"],
            }
        ) == 3
        assert replay.json()["job_id"] == calibration.json()["job_id"]


def test_embedded_worker_publishes_result_resource(tmp_path: Path) -> None:
    with TestClient(
        create_app(_settings(tmp_path, worker_mode="embedded"))
    ) as client:
        _create_scenario(client)
        submitted = client.post(
            "/api/v1/scenarios/async/experiments",
            json={"iterations": 1, "seed": 731},
        )
        assert submitted.status_code == 202
        location = submitted.headers["location"]
        deadline = monotonic() + 10
        while monotonic() < deadline:
            current = client.get(location)
            assert current.status_code == 200
            if current.json()["status"] in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                break
            sleep(0.02)
        else:
            raise AssertionError("job did not terminate")

        body = current.json()
        assert body["status"] == "succeeded"
        assert body["progress"] == 100
        assert body["result_resource_type"] == "experiment"
        assert body["result_location"] == f"{location}/result"
        result = client.get(body["result_location"])
        assert result.status_code == 200
        assert result.json()["result"]["scenario_id"] == "async"
