"""Infrastructure-neutral execution contracts for analytical job handlers."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from openenterprise_twin.analytics.adaptive import AdaptivePolicy
from openenterprise_twin.analytics.optimization import OptimizationConfig
from openenterprise_twin.application.jobs import (
    JOB_KINDS,
    Job,
    JobKind,
    JobProblem,
    validate_stage,
)

_RESOURCE_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RESOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@|/-]{0,127}")
_DIGEST_PATTERN = re.compile(r"[a-f0-9]{64}")


class JobRequestModel(BaseModel):
    """Strict persisted request envelope shared by HTTP and workers."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentJobRequest(JobRequestModel):
    experiment_id: int = Field(gt=0)


class CalibrationJobRequest(JobRequestModel):
    calibration_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    backtest_cutoff: date | None = None


class OptimizationJobRequest(JobRequestModel):
    config: OptimizationConfig
    horizon_days: int = Field(default=120, ge=30, le=730)
    replications: int = Field(default=8, ge=1, le=200)
    master_seed: int = Field(default=20240115, ge=0)


class AdaptiveComparisonJobRequest(JobRequestModel):
    policy: AdaptivePolicy
    horizon_days: int = Field(default=120, ge=30, le=730)
    replications: int = Field(default=8, ge=1, le=200)
    master_seed: int = Field(default=20240115, ge=0)


class JobCancelledError(Exception):
    """Cooperative stop raised at a deterministic handler checkpoint."""


class JobExecutionError(Exception):
    """Safe classified handler failure consumed by the worker runtime."""

    retryable: bool

    def __init__(self, *, code: str, detail: str) -> None:
        JobProblem(code=code, detail=detail)
        super().__init__(detail)
        self.code = code
        self.detail = detail


class RetryableJobExecutionError(JobExecutionError):
    """Operational failure eligible for a bounded retry."""

    retryable = True


class NonRetryableJobExecutionError(JobExecutionError):
    """Deterministic request or domain failure that must become terminal."""

    retryable = False


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    """Canonical terminal reference returned by a successful handler."""

    resource_type: str
    resource_id: str
    digest: str

    def __post_init__(self) -> None:
        if _RESOURCE_TYPE_PATTERN.fullmatch(self.resource_type) is None:
            raise ValueError("resource_type is not a safe bounded identifier")
        if _RESOURCE_ID_PATTERN.fullmatch(self.resource_id) is None:
            raise ValueError("resource_id is not a safe bounded identifier")
        if _DIGEST_PATTERN.fullmatch(self.digest) is None:
            raise ValueError("digest must be a lowercase SHA-256 value")


ProgressReporter = Callable[[int, str], bool]
CancellationProbe = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class JobExecutionContext:
    """Report progress and observe cancellation only at safe checkpoints."""

    job: Job
    report: ProgressReporter
    cancellation_requested: CancellationProbe

    def checkpoint(self, *, progress: int, stage: str) -> None:
        if not 0 <= progress <= 99:
            raise ValueError("handler progress must be between 0 and 99")
        validate_stage(stage)
        if self.cancellation_requested():
            raise JobCancelledError("job cancellation was requested")
        if self.report(progress, stage):
            raise JobCancelledError("job cancellation was requested")

    def raise_if_cancelled(self) -> None:
        if self.cancellation_requested():
            raise JobCancelledError("job cancellation was requested")


class JobHandler(Protocol):
    """Execute one already-claimed job without owning lease mechanics."""

    def __call__(
        self,
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult: ...


class JobHandlerRegistry:
    """Explicit kind-to-handler registry with startup completeness checks."""

    def __init__(self) -> None:
        self._handlers: dict[JobKind, JobHandler] = {}

    @property
    def supported_kinds(self) -> frozenset[JobKind]:
        return frozenset(self._handlers)

    def register(self, kind: JobKind, handler: JobHandler) -> None:
        if kind not in JOB_KINDS:
            raise ValueError("kind must be a supported analytical job kind")
        if kind in self._handlers:
            raise ValueError(f"handler for '{kind}' is already registered")
        self._handlers[kind] = handler

    def resolve(self, kind: JobKind) -> JobHandler:
        handler = self._handlers.get(kind)
        if handler is None:
            raise NonRetryableJobExecutionError(
                code="job_handler_missing",
                detail="No handler is registered for this job kind.",
            )
        return handler

    def require_complete(self) -> None:
        missing = JOB_KINDS.difference(self._handlers)
        if missing:
            raise ValueError(
                "job handlers are missing for: " + ", ".join(sorted(missing))
            )
