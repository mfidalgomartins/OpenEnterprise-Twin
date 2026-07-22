"""Operational contracts for the Northstar seed and flagship demo."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.api.app import create_app
from openenterprise_twin.cli.demo import (
    build_flagship_scenario,
    format_demo_result,
    run_demo,
    seed_from_settings,
    seed_northstar,
)
from openenterprise_twin.domain.scenario import validate_scenario_against_company
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.repositories import ScenarioRepository
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)


def test_flagship_scenario_is_a_valid_northstar_policy() -> None:
    scenario = build_flagship_scenario(horizon_days=5)

    assert scenario.scenario_id == "service-resilience-plan"
    assert scenario.baseline_scenario_id == "current-plan"
    assert scenario.horizon_days == 5
    assert scenario.policy_levers.resource_changes
    assert scenario.policy_levers.material_changes
    assert scenario.policy_levers.payment_term_changes
    validate_scenario_against_company(scenario, build_northstar_company())


def test_seed_northstar_is_idempotent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )

    assert seed_northstar(session_factory) is True
    assert seed_northstar(session_factory) is False

    with session_factory() as session:
        record = ScenarioRepository(session).get("current-plan")
        assert record is not None
        assert record.payload == build_baseline_scenario().model_dump(mode="json")


def test_seed_from_settings_bootstraps_isolated_sqlite(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'seed.db'}",
        artifact_directory=tmp_path / "artifacts",
    )

    assert seed_from_settings(settings) is True
    assert seed_from_settings(settings) is False


@pytest.mark.parametrize("horizon_days", [0, 3651])
def test_flagship_scenario_rejects_invalid_horizon(horizon_days: int) -> None:
    with pytest.raises(ValueError, match="horizon_days"):
        build_flagship_scenario(horizon_days=horizon_days)


def test_run_demo_creates_paired_experiments_and_reproducibility_output(
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'demo.db'}",
        artifact_directory=tmp_path / "artifacts",
        experiment_workers=1,
        replication_workers_per_experiment=1,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        result = run_demo(
            client,
            frontend_url="http://127.0.0.1:5173",
            master_seed=731,
            replications=2,
            horizon_days=5,
            timeout_seconds=10,
            poll_interval_seconds=0.01,
        )
        repeated = run_demo(
            client,
            frontend_url="http://127.0.0.1:5173",
            master_seed=731,
            replications=2,
            horizon_days=5,
            timeout_seconds=10,
            poll_interval_seconds=0.01,
        )

    assert result.route.endswith(
        "/scenarios/service-resilience-plan/compare?experiment="
        f"{result.candidate_experiment_id}"
    )
    assert result.baseline_experiment_id != result.candidate_experiment_id
    assert result.master_seed == 731
    assert result.replication_count == 2
    assert result.company_model_version == "0.2.0"
    assert result.engine_version == "0.2.0"
    assert len(result.comparison_digest) == 64
    assert len(result.brief_digest) == 64
    assert repeated == result

    output = format_demo_result(result)
    assert f"Decision Room: {result.route}" in output
    assert "Master seed: 731" in output
    assert "Replications: 2" in output
    assert f"Comparison digest: {result.comparison_digest}" in output
    assert f"Brief digest: {result.brief_digest}" in output
