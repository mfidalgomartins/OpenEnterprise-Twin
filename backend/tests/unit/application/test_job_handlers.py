"""Contracts for infrastructure-neutral job execution handlers."""

from datetime import UTC, datetime

import pytest

from openenterprise_twin.application.job_handlers import (
    JobCancelledError,
    JobExecutionContext,
    JobExecutionResult,
    JobHandlerRegistry,
    NonRetryableJobExecutionError,
    RetryableJobExecutionError,
)
from openenterprise_twin.application.jobs import Job


def _job(kind: str = "experiment") -> Job:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    return Job(
        job_id="00000000-0000-4000-8000-000000000001",
        tenant_id="tenant-a",
        kind=kind,  # type: ignore[arg-type]
        status="running",
        created_by="analyst",
        request_payload={"resource_id": "resource-1"},
        request_digest="a" * 64,
        idempotency_key=None,
        attempt_count=1,
        max_attempts=3,
        progress=0,
        stage="starting",
        lease_owner="worker-a",
        lease_expires_at=now,
        heartbeat_at=now,
        cancellation_requested_at=None,
        next_attempt_at=None,
        result_resource_type=None,
        result_resource_id=None,
        result_digest=None,
        problem=None,
        created_at=now,
        started_at=now,
        finished_at=None,
        updated_at=now,
    )


def test_context_reports_deterministic_safe_checkpoints() -> None:
    checkpoints: list[tuple[int, str]] = []
    context = JobExecutionContext(
        job=_job(),
        report=lambda progress, stage: (
            checkpoints.append((progress, stage)) or False
        ),
        cancellation_requested=lambda: False,
    )

    context.checkpoint(progress=10, stage="loading")
    context.checkpoint(progress=70, stage="simulating")

    assert checkpoints == [(10, "loading"), (70, "simulating")]


def test_context_raises_before_or_during_cancelled_checkpoint() -> None:
    with pytest.raises(JobCancelledError):
        JobExecutionContext(
            job=_job(),
            report=lambda _progress, _stage: False,
            cancellation_requested=lambda: True,
        ).checkpoint(progress=10, stage="loading")

    with pytest.raises(JobCancelledError):
        JobExecutionContext(
            job=_job(),
            report=lambda _progress, _stage: True,
            cancellation_requested=lambda: False,
        ).checkpoint(progress=10, stage="loading")


def test_registry_is_complete_and_rejects_duplicate_handlers() -> None:
    def handler(job: Job, context: JobExecutionContext) -> JobExecutionResult:
        del job, context
        return JobExecutionResult(
            resource_type="experiment",
            resource_id="42",
            digest="a" * 64,
        )

    registry = JobHandlerRegistry()
    for kind in (
        "experiment",
        "calibration",
        "optimization",
        "adaptive_comparison",
    ):
        registry.register(kind, handler)

    assert registry.supported_kinds == frozenset(
        {
            "experiment",
            "calibration",
            "optimization",
            "adaptive_comparison",
        }
    )
    registry.require_complete()
    with pytest.raises(ValueError, match="already registered"):
        registry.register("experiment", handler)


def test_registry_reports_missing_handler_as_non_retryable() -> None:
    registry = JobHandlerRegistry()

    with pytest.raises(NonRetryableJobExecutionError) as raised:
        registry.resolve("experiment")

    assert raised.value.code == "job_handler_missing"
    assert raised.value.retryable is False


def test_execution_errors_expose_only_safe_failure_contracts() -> None:
    retryable = RetryableJobExecutionError(
        code="dependency_timeout",
        detail="A required dependency timed out.",
    )
    terminal = NonRetryableJobExecutionError(
        code="invalid_job_request",
        detail="The stored job request is invalid.",
    )

    assert retryable.retryable is True
    assert terminal.retryable is False
    with pytest.raises(ValueError):
        RetryableJobExecutionError(
            code="unsafe",
            detail="Traceback: secret token=abc",
        )

