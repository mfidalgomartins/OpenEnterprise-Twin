"""Worker runtime contracts for lease renewal and failure handling."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import sleep

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.application.job_handlers import (
    JobExecutionContext,
    JobExecutionResult,
    JobHandlerRegistry,
    NonRetryableJobExecutionError,
    RetryableJobExecutionError,
)
from openenterprise_twin.application.jobs import Job, SubmitJob
from openenterprise_twin.infrastructure.jobs import SqlJobRepository
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.runner import (
    DurableJobWorker,
    job_queue_snapshot,
)


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _submit(
    session_factory: sessionmaker[Session],
    *,
    max_attempts: int = 3,
) -> Job:
    return SqlJobRepository(session_factory, "tenant-a").submit(
        SubmitJob(
            kind="experiment",
            created_by="analyst",
            request_payload={"experiment_id": 42},
            max_attempts=max_attempts,
        )
    ).job


def _worker(
    session_factory: sessionmaker[Session],
    handler,
    *,
    lease_duration: timedelta = timedelta(seconds=2),
    heartbeat_interval: timedelta = timedelta(milliseconds=250),
    retry_delay: timedelta = timedelta(seconds=1),
) -> DurableJobWorker:
    registry = JobHandlerRegistry()
    registry.register("experiment", handler)
    return DurableJobWorker(
        session_factory=session_factory,
        handlers=registry,
        worker_id="worker-a",
        lease_duration=lease_duration,
        heartbeat_interval=heartbeat_interval,
        retry_delay=retry_delay,
    )


def test_worker_completes_a_claimed_job(
    session_factory: sessionmaker[Session],
) -> None:
    submitted = _submit(session_factory)
    checkpoints: list[tuple[int, str]] = []

    def handler(
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        assert job.job_id == submitted.job_id
        context.checkpoint(progress=50, stage="simulating")
        checkpoints.append((job.attempt_count, job.kind))
        return JobExecutionResult(
            resource_type="experiment",
            resource_id="42",
            digest="a" * 64,
        )

    assert _worker(session_factory, handler).run_once() is True

    terminal = SqlJobRepository(session_factory, "tenant-a").get(
        submitted.job_id
    )
    assert terminal is not None
    assert terminal.status == "succeeded"
    assert terminal.attempt_count == 1
    assert checkpoints == [(1, "experiment")]


def test_worker_retries_safe_operational_failure_then_succeeds(
    session_factory: sessionmaker[Session],
) -> None:
    submitted = _submit(session_factory, max_attempts=2)
    attempts = 0

    def handler(
        _job: Job,
        _context: JobExecutionContext,
    ) -> JobExecutionResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RetryableJobExecutionError(
                code="dependency_timeout",
                detail="A required dependency timed out.",
            )
        return JobExecutionResult(
            resource_type="experiment",
            resource_id="42",
            digest="b" * 64,
        )

    worker = _worker(session_factory, handler)
    first = datetime(2026, 7, 26, 10, tzinfo=UTC)
    assert worker.run_once(now=first) is True
    queued = SqlJobRepository(session_factory, "tenant-a").get(submitted.job_id)
    assert queued is not None
    assert queued.status == "queued"
    assert queued.next_attempt_at == first + timedelta(seconds=1)
    assert worker.run_once(now=first + timedelta(milliseconds=999)) is False
    assert worker.run_once(now=first + timedelta(seconds=1)) is True
    assert (
        SqlJobRepository(session_factory, "tenant-a").get(submitted.job_id).status
        == "succeeded"  # type: ignore[union-attr]
    )


def test_worker_stores_non_retryable_failure_without_traceback(
    session_factory: sessionmaker[Session],
) -> None:
    submitted = _submit(session_factory)

    def handler(
        _job: Job,
        _context: JobExecutionContext,
    ) -> JobExecutionResult:
        raise NonRetryableJobExecutionError(
            code="invalid_job_request",
            detail="The stored job request is invalid.",
        )

    assert _worker(session_factory, handler).run_once() is True
    terminal = SqlJobRepository(session_factory, "tenant-a").get(
        submitted.job_id
    )
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.attempt_count == 1
    assert terminal.problem is not None
    assert terminal.problem["code"] == "invalid_job_request"
    assert "traceback" not in str(terminal.problem).lower()


def test_worker_honours_cooperative_cancellation(
    session_factory: sessionmaker[Session],
) -> None:
    submitted = _submit(session_factory)

    def handler(
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        SqlJobRepository(
            session_factory,
            job.tenant_id,
        ).request_cancellation(job.job_id)
        context.checkpoint(progress=50, stage="simulating")
        raise AssertionError("cancelled checkpoint must stop the handler")

    assert _worker(session_factory, handler).run_once() is True
    terminal = SqlJobRepository(session_factory, "tenant-a").get(
        submitted.job_id
    )
    assert terminal is not None
    assert terminal.status == "cancelled"


def test_heartbeat_keeps_lease_alive_during_long_handler(
    session_factory: sessionmaker[Session],
) -> None:
    submitted = _submit(session_factory)

    def handler(
        _job: Job,
        _context: JobExecutionContext,
    ) -> JobExecutionResult:
        sleep(0.9)
        return JobExecutionResult(
            resource_type="experiment",
            resource_id="42",
            digest="c" * 64,
        )

    # The property under test is that a handler outliving its lease still
    # completes because the heartbeat renews the lease. The absolute timings
    # are deliberately generous: a shared CI runner can starve the heartbeat
    # thread for tens of milliseconds, and tighter margins made this test flaky.
    # Keep the work well above the lease and many heartbeats inside it.
    worker = _worker(
        session_factory,
        handler,
        lease_duration=timedelta(milliseconds=500),
        heartbeat_interval=timedelta(milliseconds=50),
    )
    assert worker.run_once() is True
    terminal = SqlJobRepository(session_factory, "tenant-a").get(
        submitted.job_id
    )
    assert terminal is not None
    assert terminal.status == "succeeded"


def test_worker_loop_stops_without_waiting_for_poll_timeout(
    session_factory: sessionmaker[Session],
) -> None:
    registry = JobHandlerRegistry()
    worker = DurableJobWorker(
        session_factory=session_factory,
        handlers=registry,
        worker_id="worker-a",
        lease_duration=timedelta(seconds=2),
        heartbeat_interval=timedelta(milliseconds=250),
        retry_delay=timedelta(seconds=1),
    )
    stop = Event()
    stop.set()

    worker.run_forever(stop, poll_interval=timedelta(seconds=30))


def test_queue_health_is_aggregate_and_reports_stale_leases(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    _submit(session_factory)
    _submit(session_factory)
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.claim_next(
        worker_id="dead-worker",
        lease_duration=timedelta(seconds=10),
        now=started,
    )

    snapshot = job_queue_snapshot(
        session_factory,
        now=started + timedelta(seconds=11),
    )

    assert snapshot.queued == 1
    assert snapshot.running == 1
    assert snapshot.stale_leases == 1
    assert snapshot.oldest_queued_age_seconds is not None
