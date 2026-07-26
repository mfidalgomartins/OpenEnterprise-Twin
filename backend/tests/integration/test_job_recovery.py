"""Restart recovery contract for expired worker leases."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from openenterprise_twin.application.job_handlers import (
    JobExecutionContext,
    JobExecutionResult,
    JobHandlerRegistry,
)
from openenterprise_twin.application.jobs import Job, SubmitJob
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.jobs import SqlJobRepository
from openenterprise_twin.infrastructure.models import Base, OptimizationRecord
from openenterprise_twin.infrastructure.repositories import (
    ExperimentRepository,
    ScenarioRepository,
)
from openenterprise_twin.infrastructure.runner import (
    DurableJobWorker,
    build_analytical_job_handlers,
)
from openenterprise_twin.simulation.reference import build_baseline_scenario


def test_restarted_worker_recovers_expired_job_and_rejects_stale_owner(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'recovery.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlJobRepository(factory, "tenant-a")
    submitted = repository.submit(
        SubmitJob(
            kind="experiment",
            created_by="analyst",
            request_payload={"experiment_id": 42},
            max_attempts=2,
        )
    ).job
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.claim_next(
        worker_id="crashed-worker",
        lease_duration=timedelta(seconds=10),
        now=started,
    )

    registry = JobHandlerRegistry()

    def handler(
        _job: Job,
        _context: JobExecutionContext,
    ) -> JobExecutionResult:
        return JobExecutionResult(
            resource_type="experiment",
            resource_id="42",
            digest="d" * 64,
        )

    registry.register("experiment", handler)
    restarted = DurableJobWorker(
        session_factory=factory,
        handlers=registry,
        worker_id="replacement-worker",
        lease_duration=timedelta(seconds=10),
        heartbeat_interval=timedelta(seconds=2),
        retry_delay=timedelta(seconds=1),
    )

    assert restarted.run_once(now=started + timedelta(seconds=11)) is True
    terminal = repository.get(submitted.job_id)
    assert terminal is not None
    assert terminal.status == "succeeded"
    assert terminal.attempt_count == 2
    assert terminal.result_digest == "d" * 64


def test_real_experiment_handler_completes_legacy_and_job_resources(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'handler.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={"scenario_id": "job-scenario"}
    )
    with factory() as session, session.begin():
        ScenarioRepository(session, "tenant-a").create(scenario)
        experiment = ExperimentRepository(session, "tenant-a").create(
            scenario_id=scenario.scenario_id,
            baseline_experiment_id=None,
            master_seed=731,
            replication_count=1,
            idempotency_key=None,
            request_payload={"max_workers": 1},
        )
        experiment_id = experiment.id
    repository = SqlJobRepository(factory, "tenant-a")
    submitted = repository.submit(
        SubmitJob(
            kind="experiment",
            created_by="analyst",
            request_payload={"experiment_id": experiment_id},
        )
    ).job
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    worker = DurableJobWorker(
        session_factory=factory,
        handlers=build_analytical_job_handlers(
            session_factory=factory,
            artifact_store=artifacts,
            max_replication_workers=1,
            max_dataset_observations=1_000,
            max_optimization_evaluations=20,
            max_optimization_periods=100_000,
            max_adaptive_periods=100_000,
        ),
        worker_id="real-worker",
        lease_duration=timedelta(seconds=10),
        heartbeat_interval=timedelta(seconds=2),
        retry_delay=timedelta(seconds=1),
    )

    assert worker.run_once() is True

    terminal = repository.get(submitted.job_id)
    assert terminal is not None
    assert terminal.status == "succeeded"
    assert terminal.result_resource_id == str(experiment_id)
    assert terminal.result_digest is not None
    assert artifacts.get_json(terminal.result_digest)
    with factory() as session:
        legacy = ExperimentRepository(session, "tenant-a").get(experiment_id)
        assert legacy is not None
        assert legacy.status == "completed"
        assert legacy.artifact_digest == terminal.result_digest


def test_optimization_handler_is_exactly_once_per_source_job(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'optimization.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    job = SqlJobRepository(factory, "tenant-a").submit(
        SubmitJob(
            kind="optimization",
            created_by="analyst",
            request_payload={
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
        )
    ).job
    handler = build_analytical_job_handlers(
        session_factory=factory,
        artifact_store=FileArtifactStore(tmp_path / "optimization-artifacts"),
        max_replication_workers=1,
        max_dataset_observations=1_000,
        max_optimization_evaluations=20,
        max_optimization_periods=100_000,
        max_adaptive_periods=100_000,
    ).resolve("optimization")
    context = JobExecutionContext(
        job=job,
        report=lambda _progress, _stage: False,
        cancellation_requested=lambda: False,
    )

    first = handler(job, context)
    replay = handler(job, context)

    assert replay.resource_id == first.resource_id
    with factory() as session:
        assert session.query(OptimizationRecord).count() == 1
