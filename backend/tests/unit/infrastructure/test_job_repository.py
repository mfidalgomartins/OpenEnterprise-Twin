"""Persistence contracts for tenant-scoped lease-based jobs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.application.jobs import (
    JobConflictError,
    JobLeaseError,
    JobProblem,
    SubmitJob,
)
from openenterprise_twin.infrastructure.jobs import SqlJobRepository
from openenterprise_twin.infrastructure.models import Base


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _submit(
    repository: SqlJobRepository,
    *,
    key: str | None = "plan-2026",
    payload: dict[str, object] | None = None,
    max_attempts: int = 3,
):
    return repository.submit(
        SubmitJob(
            kind="optimization",
            created_by="analyst",
            request_payload=payload or {"budget": 1000, "objective": "margin"},
            idempotency_key=key,
            max_attempts=max_attempts,
        )
    )


def test_submission_is_idempotent_per_tenant_and_kind(
    session_factory: sessionmaker[Session],
) -> None:
    tenant_a = SqlJobRepository(session_factory, "tenant-a")
    tenant_b = SqlJobRepository(session_factory, "tenant-b")

    first = _submit(tenant_a)
    repeated = _submit(tenant_a)
    other_tenant = _submit(tenant_b)

    assert first.created is True
    assert repeated.created is False
    assert repeated.job.job_id == first.job.job_id
    assert other_tenant.created is True
    assert other_tenant.job.job_id != first.job.job_id
    assert tenant_b.get(first.job.job_id) is None


def test_reusing_idempotency_key_for_other_request_conflicts(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    _submit(repository)

    with pytest.raises(JobConflictError, match="different request"):
        _submit(repository, payload={"budget": 999, "objective": "margin"})


def test_idempotency_key_namespace_includes_job_kind(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    optimization = _submit(repository).job
    experiment = repository.submit(
        SubmitJob(
            kind="experiment",
            created_by="analyst",
            request_payload={"scenario_id": "base"},
            idempotency_key="plan-2026",
        )
    ).job

    assert experiment.job_id != optimization.job_id


def test_concurrent_equivalent_submissions_create_one_job(
    session_factory: sessionmaker[Session],
) -> None:
    def submit(_: int):
        return _submit(
            SqlJobRepository(session_factory, "tenant-a"),
            key="concurrent-key",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        submissions = tuple(executor.map(submit, range(8)))

    assert len({item.job.job_id for item in submissions}) == 1
    assert sum(item.created for item in submissions) == 1


def test_claim_heartbeat_progress_and_success_lifecycle(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    submitted = _submit(repository, key=None).job
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)

    claimed = repository.claim_next(
        worker_id="worker-a",
        lease_duration=timedelta(seconds=30),
        now=started,
    )
    assert claimed is not None
    assert claimed.job_id == submitted.job_id
    assert claimed.status == "running"
    assert claimed.attempt_count == 1
    assert claimed.lease_expires_at == started + timedelta(seconds=30)

    progressed = repository.report_progress(
        submitted.job_id,
        worker_id="worker-a",
        progress=65,
        stage="solving",
        lease_duration=timedelta(seconds=30),
        now=started + timedelta(seconds=10),
    )
    assert progressed.progress == 65
    assert progressed.stage == "solving"
    assert progressed.lease_expires_at == started + timedelta(seconds=40)

    completed = repository.succeed(
        submitted.job_id,
        worker_id="worker-a",
        result_resource_type="optimization",
        result_resource_id="opt-42",
        result_digest="a" * 64,
        now=started + timedelta(seconds=20),
    )
    assert completed.status == "succeeded"
    assert completed.progress == 100
    assert completed.lease_owner is None
    assert completed.result_resource_id == "opt-42"
    assert completed.finished_at == started + timedelta(seconds=20)


def test_stale_or_wrong_worker_cannot_write(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    job = _submit(repository, key=None).job
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.claim_next(
        worker_id="worker-a",
        lease_duration=timedelta(seconds=10),
        now=started,
    )

    with pytest.raises(JobLeaseError):
        repository.heartbeat(
            job.job_id,
            worker_id="worker-b",
            lease_duration=timedelta(seconds=10),
            now=started + timedelta(seconds=1),
        )
    with pytest.raises(JobLeaseError):
        repository.heartbeat(
            job.job_id,
            worker_id="worker-a",
            lease_duration=timedelta(seconds=10),
            now=started + timedelta(seconds=11),
        )


def test_failure_retries_then_becomes_terminal_with_safe_problem(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    job = _submit(repository, key=None, max_attempts=2).job
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.claim_next(
        worker_id="worker-a",
        lease_duration=timedelta(minutes=1),
        now=started,
    )
    problem = JobProblem(code="solver_timeout", detail="Solver timed out.")

    retried = repository.fail(
        job.job_id,
        worker_id="worker-a",
        problem=problem,
        retryable=True,
        retry_delay=timedelta(seconds=5),
        now=started + timedelta(seconds=1),
    )
    assert retried.status == "queued"
    assert retried.next_attempt_at == started + timedelta(seconds=6)
    assert retried.problem is None
    assert (
        repository.claim_next(
            worker_id="worker-b",
            lease_duration=timedelta(minutes=1),
            now=started + timedelta(seconds=5),
        )
        is None
    )

    repository.claim_next(
        worker_id="worker-b",
        lease_duration=timedelta(minutes=1),
        now=started + timedelta(seconds=6),
    )
    failed = repository.fail(
        job.job_id,
        worker_id="worker-b",
        problem=problem,
        retryable=True,
        retry_delay=timedelta(seconds=5),
        now=started + timedelta(seconds=7),
    )
    assert failed.status == "failed"
    assert failed.problem == problem.as_json()
    assert failed.finished_at == started + timedelta(seconds=7)


def test_cancellation_is_immediate_when_queued_and_cooperative_when_running(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    queued = _submit(repository, key="queued").job
    running = _submit(repository, key="running").job
    now = datetime(2026, 7, 26, 10, tzinfo=UTC)

    cancelled = repository.request_cancellation(queued.job_id, now=now)
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at == now

    claimed = repository.claim_next(
        worker_id="worker-a",
        lease_duration=timedelta(minutes=1),
        now=now,
    )
    assert claimed is not None
    assert claimed.job_id == running.job_id
    requested = repository.request_cancellation(
        running.job_id,
        now=now + timedelta(seconds=1),
    )
    assert requested.status == "running"
    assert requested.cancellation_requested_at == now + timedelta(seconds=1)

    terminal = repository.cancel(
        running.job_id,
        worker_id="worker-a",
        now=now + timedelta(seconds=2),
    )
    assert terminal.status == "cancelled"
    assert terminal.lease_owner is None


def test_failure_after_cancellation_request_finishes_as_cancelled(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    job = _submit(repository, key=None).job
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.claim_next(
        worker_id="worker-a",
        lease_duration=timedelta(minutes=1),
        now=started,
    )
    repository.request_cancellation(
        job.job_id,
        now=started + timedelta(seconds=1),
    )

    terminal = repository.fail(
        job.job_id,
        worker_id="worker-a",
        problem=JobProblem(code="interrupted", detail="Execution was interrupted."),
        retryable=True,
        retry_delay=timedelta(seconds=1),
        now=started + timedelta(seconds=2),
    )

    assert terminal.status == "cancelled"
    assert terminal.problem is None
    assert terminal.next_attempt_at is None
    assert terminal.finished_at == started + timedelta(seconds=2)


def test_expired_leases_are_recovered_or_exhausted(
    session_factory: sessionmaker[Session],
) -> None:
    repository = SqlJobRepository(session_factory, "tenant-a")
    retryable = _submit(repository, key="retry", max_attempts=2).job
    started = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.claim_next(
        worker_id="dead-worker",
        lease_duration=timedelta(seconds=10),
        now=started,
    )

    assert repository.recover_expired_leases(
        now=started + timedelta(seconds=11)
    ) == 1
    assert repository.get(retryable.job_id).status == "queued"  # type: ignore[union-attr]

    repository.claim_next(
        worker_id="dead-again",
        lease_duration=timedelta(seconds=10),
        now=started + timedelta(seconds=11),
    )
    assert repository.recover_expired_leases(
        now=started + timedelta(seconds=22)
    ) == 1
    exhausted = repository.get(retryable.job_id)
    assert exhausted is not None
    assert exhausted.status == "failed"
    assert exhausted.problem is not None
    assert exhausted.problem["code"] == "lease_expired"
