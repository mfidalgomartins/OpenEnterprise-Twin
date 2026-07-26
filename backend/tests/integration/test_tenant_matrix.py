"""Focused PostgreSQL proof for the cross-tenant resource matrix."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from openenterprise_twin.api.app import create_app
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.reference import build_baseline_scenario


@pytest.fixture
def isolated_postgres_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    configured_url = os.getenv("OPENENTERPRISE_TWIN_TEST_POSTGRES_URL")
    if configured_url is None:
        pytest.skip("requires the PostgreSQL 16 CI service")
    base_url = make_url(configured_url)
    database_name = f"oet_tenant_{uuid4().hex}"
    admin_engine = create_engine(
        base_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    database_url = base_url.set(database=database_name)
    rendered_url = database_url.render_as_string(hide_password=False)
    monkeypatch.setenv("OPENENTERPRISE_TWIN_DATABASE_URL", rendered_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        yield rendered_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


@contextmanager
def _client(
    database_url: str,
    tmp_path: Path,
    tenant_id: str,
) -> Iterator[TestClient]:
    settings = Settings(
        database_url=database_url,
        artifact_directory=tmp_path / "artifacts",
        deployment_environment="test",
        authentication_mode="local",
        local_subject=f"admin:{tenant_id}",
        local_tenant_id=tenant_id,
        local_roles=("admin",),
        job_worker_mode="external",
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_postgres_hides_scenarios_datasets_decisions_and_jobs_across_tenants(
    isolated_postgres_url: str,
    tmp_path: Path,
) -> None:
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={"scenario_id": "shared-scenario"}
    )
    decision = {
        "decision_id": "shared-decision",
        "content": {
            "title": "Protect margin",
            "owner": "cfo",
            "context": "PostgreSQL tenant matrix.",
            "objectives": ["grow ebitda"],
            "company_model_version": "0.2.0",
            "recommendation": "Pilot the candidate policy.",
            "chosen_alternative": "candidate",
            "justification": "Bounded decision evidence.",
            "evidence": {"experiment_ids": [1]},
        },
    }

    with _client(isolated_postgres_url, tmp_path, "tenant-a") as tenant_a:
        created_scenario = tenant_a.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )
        created_dataset = tenant_a.post(
            "/api/v1/datasets/synthetic",
            json={"dataset_id": "shared-history", "days": 60},
        )
        created_decision = tenant_a.post(
            "/api/v1/ledger/decisions",
            json=decision,
        )
        submitted_job = tenant_a.post(
            "/api/v1/calibrations",
            json={
                "calibration_id": "shared-calibration",
                "dataset_id": "shared-history",
            },
        )

    assert created_scenario.status_code == 201
    assert created_dataset.status_code == 201
    assert created_decision.status_code == 201
    assert submitted_job.status_code == 202
    job_id = submitted_job.json()["job_id"]

    with _client(isolated_postgres_url, tmp_path, "tenant-b") as tenant_b:
        hidden_statuses = (
            tenant_b.get("/api/v1/scenarios/shared-scenario").status_code,
            tenant_b.get(
                "/api/v1/datasets/shared-history/export.csv"
            ).status_code,
            tenant_b.get(
                "/api/v1/ledger/decisions/shared-decision"
            ).status_code,
            tenant_b.get(f"/api/v1/jobs/{job_id}").status_code,
        )
        same_public_id = tenant_b.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )

    assert hidden_statuses == (404, 404, 404, 404)
    assert same_public_id.status_code == 201
