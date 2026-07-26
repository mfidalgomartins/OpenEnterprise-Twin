"""Infrastructure-neutral contracts for durable analytical jobs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, Protocol, cast

JobKind = Literal[
    "experiment",
    "calibration",
    "optimization",
    "adaptive_comparison",
]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
JsonObject = dict[str, object]

JOB_KINDS: frozenset[str] = frozenset(
    {"experiment", "calibration", "optimization", "adaptive_comparison"}
)
JOB_STATUSES: frozenset[str] = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled"}
)
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@|/-]{0,127}")
_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@|/-]{0,199}")
_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_STAGE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SENSITIVE_DETAIL = re.compile(
    r"(?i)(traceback|authorization:|bearer\s+|api[_ -]?key|"
    r"password\s*[=:]|secret\s*[=:]|token\s*[=:])"
)


class JobError(Exception):
    """Base class for stable job lifecycle failures."""


class JobConflictError(JobError):
    """An idempotency key was reused for a different canonical request."""


class JobNotFoundError(JobError):
    """The tenant-scoped job does not exist."""


class JobLeaseError(JobError):
    """A worker attempted a write without owning a valid lease."""


class InvalidJobTransitionError(JobError):
    """The requested transition is not valid from the current state."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _json_snapshot(payload: Mapping[str, object]) -> JsonObject:
    try:
        encoded = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("request_payload must contain finite JSON values") from error
    if not isinstance(decoded, dict):
        raise ValueError("request_payload must be a JSON object")
    return cast(JsonObject, decoded)


def canonical_request_digest(
    kind: JobKind,
    request_payload: Mapping[str, object],
) -> str:
    """Bind one canonical JSON request to its analytical job kind."""

    if kind not in JOB_KINDS:
        raise ValueError("kind must be a supported analytical job kind")
    canonical = json.dumps(
        {"kind": kind, "request_payload": _json_snapshot(request_payload)},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def validate_worker_id(worker_id: str) -> None:
    """Reject unsafe or unbounded worker identities."""

    if _IDENTIFIER_PATTERN.fullmatch(worker_id) is None:
        raise ValueError("worker_id is not a safe bounded identifier")


def validate_stage(stage: str) -> None:
    """Reject stages unsuitable for durable storage and metrics."""

    if _STAGE_PATTERN.fullmatch(stage) is None:
        raise ValueError("stage must be a lowercase bounded identifier")


@dataclass(frozen=True, slots=True)
class SubmitJob:
    """Immutable command for one tenant-bound analytical workload."""

    kind: JobKind
    created_by: str
    request_payload: Mapping[str, object]
    idempotency_key: str | None = None
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.kind not in JOB_KINDS:
            raise ValueError("kind must be a supported analytical job kind")
        if _IDENTIFIER_PATTERN.fullmatch(self.created_by) is None:
            raise ValueError("created_by is not a safe bounded identifier")
        if (
            self.idempotency_key is not None
            and _KEY_PATTERN.fullmatch(self.idempotency_key) is None
        ):
            raise ValueError("idempotency_key is not a safe bounded value")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        object.__setattr__(
            self,
            "request_payload",
            _json_snapshot(self.request_payload),
        )

    @property
    def request_digest(self) -> str:
        return canonical_request_digest(self.kind, self.request_payload)


@dataclass(frozen=True, slots=True)
class JobProblem:
    """Safe terminal failure information suitable for public APIs."""

    code: str
    detail: str
    occurred_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if _CODE_PATTERN.fullmatch(self.code) is None:
            raise ValueError("problem code must be a lowercase bounded identifier")
        if not 1 <= len(self.detail) <= 500:
            raise ValueError("problem detail must contain between 1 and 500 characters")
        if _SENSITIVE_DETAIL.search(self.detail) is not None:
            raise ValueError("problem detail contains unsafe diagnostic information")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

    def as_json(self) -> JsonObject:
        occurred_at = self.occurred_at.astimezone(UTC).isoformat().replace(
            "+00:00",
            "Z",
        )
        return {
            "code": self.code,
            "detail": self.detail,
            "occurred_at": occurred_at,
        }


@dataclass(frozen=True, slots=True)
class Job:
    """Immutable read model of the durable job state machine."""

    job_id: str
    tenant_id: str
    kind: JobKind
    status: JobStatus
    created_by: str
    request_payload: Mapping[str, object]
    request_digest: str
    idempotency_key: str | None
    attempt_count: int
    max_attempts: int
    progress: int
    stage: str
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    cancellation_requested_at: datetime | None
    next_attempt_at: datetime | None
    result_resource_type: str | None
    result_resource_id: str | None
    result_digest: str | None
    problem: Mapping[str, object] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class JobSubmission:
    """Submission result distinguishing creation from idempotent replay."""

    job: Job
    created: bool


class JobRepository(Protocol):
    """Port implemented by durable tenant-scoped job stores."""

    def submit(self, command: SubmitJob) -> JobSubmission: ...

    def get(self, job_id: str) -> Job | None: ...

    def list(
        self,
        *,
        statuses: frozenset[JobStatus] | None = None,
        kinds: frozenset[JobKind] | None = None,
        limit: int = 50,
        before_created_at: datetime | None = None,
        before_job_id: str | None = None,
    ) -> tuple[Job, ...]: ...

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job | None: ...

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job: ...

    def report_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        progress: int,
        stage: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job: ...

    def request_cancellation(
        self,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> Job: ...

    def succeed(
        self,
        job_id: str,
        *,
        worker_id: str,
        result_resource_type: str,
        result_resource_id: str,
        result_digest: str,
        now: datetime | None = None,
    ) -> Job: ...

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        problem: JobProblem,
        retryable: bool,
        retry_delay: timedelta,
        now: datetime | None = None,
    ) -> Job: ...

    def cancel(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> Job: ...

    def recover_expired_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> int: ...
