"""SQL-backed tenant-scoped persistence for durable analytical jobs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from openenterprise_twin.application.jobs import (
    InvalidJobTransitionError,
    Job,
    JobConflictError,
    JobKind,
    JobLeaseError,
    JobNotFoundError,
    JobProblem,
    JobStatus,
    JobSubmission,
    SubmitJob,
    validate_stage,
    validate_worker_id,
)
from openenterprise_twin.infrastructure.models import JobRecord

_TENANT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_RESOURCE_TYPE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
_RESOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@|/-]{0,127}")
_DIGEST_PATTERN = re.compile(r"[a-f0-9]{64}")
_MAX_LEASE_DURATION = timedelta(hours=1)
_MAX_RETRY_DELAY = timedelta(days=1)


class SqlJobRepository:
    """Execute short transactions around one tenant's durable job queue."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        tenant_id: str,
    ) -> None:
        if _TENANT_PATTERN.fullmatch(tenant_id) is None:
            raise ValueError("tenant_id is not a safe bounded identifier")
        self._session_factory = session_factory
        self._tenant_id = tenant_id

    def submit(self, command: SubmitJob) -> JobSubmission:
        """Create a job or replay an equivalent idempotent submission."""

        if command.idempotency_key is not None:
            existing = self._get_by_idempotency(
                command.kind,
                command.idempotency_key,
            )
            if existing is not None:
                return self._replay_or_conflict(existing, command)

        record = self._new_record(command)
        try:
            with self._session_factory.begin() as session:
                session.add(record)
                session.flush()
                job = _to_job(record)
        except IntegrityError:
            if command.idempotency_key is None:
                raise
            existing = self._get_by_idempotency(
                command.kind,
                command.idempotency_key,
            )
            if existing is None:
                raise
            return self._replay_or_conflict(existing, command)
        return JobSubmission(job=job, created=True)

    def submit_in_session(
        self,
        session: Session,
        command: SubmitJob,
    ) -> JobSubmission:
        """Submit inside a caller-owned transaction with related resources."""

        if command.idempotency_key is not None:
            existing = session.scalar(
                select(JobRecord).where(
                    JobRecord.tenant_id == self._tenant_id,
                    JobRecord.kind == command.kind,
                    JobRecord.idempotency_key == command.idempotency_key,
                )
            )
            if existing is not None:
                return self._replay_or_conflict(existing, command)
        record = self._new_record(command)
        session.add(record)
        session.flush()
        return JobSubmission(job=_to_job(record), created=True)

    def get(self, job_id: str) -> Job | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(JobRecord).where(
                    JobRecord.tenant_id == self._tenant_id,
                    JobRecord.job_id == job_id,
                )
            )
            return None if record is None else _to_job(record)

    def list(
        self,
        *,
        statuses: frozenset[JobStatus] | None = None,
        kinds: frozenset[JobKind] | None = None,
        limit: int = 50,
        before_created_at: datetime | None = None,
        before_job_id: str | None = None,
    ) -> tuple[Job, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if (before_created_at is None) != (before_job_id is None):
            raise ValueError("both job cursor fields must be supplied together")
        statement: Select[tuple[JobRecord]] = select(JobRecord).where(
            JobRecord.tenant_id == self._tenant_id
        )
        if statuses:
            statement = statement.where(JobRecord.status.in_(statuses))
        if kinds:
            statement = statement.where(JobRecord.kind.in_(kinds))
        if before_created_at is not None:
            boundary = _aware_utc(before_created_at, name="before_created_at")
            assert before_job_id is not None
            statement = statement.where(
                or_(
                    JobRecord.created_at < boundary,
                    and_(
                        JobRecord.created_at == boundary,
                        JobRecord.job_id < before_job_id,
                    ),
                )
            )
        statement = statement.order_by(
            JobRecord.created_at.desc(),
            JobRecord.job_id.desc(),
        ).limit(limit)
        with self._session_factory() as session:
            return tuple(_to_job(record) for record in session.scalars(statement))

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job | None:
        """Atomically claim the oldest eligible job for this tenant."""

        validate_worker_id(worker_id)
        lease_duration = _bounded_duration(
            lease_duration,
            maximum=_MAX_LEASE_DURATION,
            name="lease_duration",
        )
        current = _now(now)
        lease_expires_at = current + lease_duration
        with self._session_factory.begin() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                record = session.scalar(
                    self._eligible_statement(current).with_for_update(
                        skip_locked=True
                    )
                )
                if record is None:
                    return None
                _claim_record(
                    record,
                    worker_id=worker_id,
                    current=current,
                    lease_expires_at=lease_expires_at,
                )
                session.flush()
                return _to_job(record)

            candidate = (
                self._eligible_statement(current)
                .with_only_columns(JobRecord.job_id)
                .scalar_subquery()
            )
            claimed_id = session.scalar(
                update(JobRecord)
                .where(
                    JobRecord.job_id == candidate,
                    JobRecord.tenant_id == self._tenant_id,
                    JobRecord.status == "queued",
                    JobRecord.cancellation_requested_at.is_(None),
                    or_(
                        JobRecord.next_attempt_at.is_(None),
                        JobRecord.next_attempt_at <= current,
                    ),
                )
                .values(
                    status="running",
                    attempt_count=JobRecord.attempt_count + 1,
                    progress=0,
                    stage="starting",
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    heartbeat_at=current,
                    next_attempt_at=None,
                    started_at=func.coalesce(JobRecord.started_at, current),
                    updated_at=current,
                )
                .returning(JobRecord.job_id)
            )
            if claimed_id is None:
                return None
            record = session.get(JobRecord, claimed_id)
            if record is None:
                return None
            return _to_job(record)

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job:
        validate_worker_id(worker_id)
        lease_duration = _bounded_duration(
            lease_duration,
            maximum=_MAX_LEASE_DURATION,
            name="lease_duration",
        )
        current = _now(now)
        return self._leased_update(
            job_id,
            worker_id=worker_id,
            current=current,
            values={
                "heartbeat_at": current,
                "lease_expires_at": current + lease_duration,
                "updated_at": current,
            },
        )

    def report_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        progress: int,
        stage: str,
        lease_duration: timedelta,
        now: datetime | None = None,
    ) -> Job:
        if not 0 <= progress <= 99:
            raise ValueError("running progress must be between 0 and 99")
        validate_stage(stage)
        validate_worker_id(worker_id)
        lease_duration = _bounded_duration(
            lease_duration,
            maximum=_MAX_LEASE_DURATION,
            name="lease_duration",
        )
        current = _now(now)
        return self._leased_update(
            job_id,
            worker_id=worker_id,
            current=current,
            values={
                "progress": progress,
                "stage": stage,
                "heartbeat_at": current,
                "lease_expires_at": current + lease_duration,
                "updated_at": current,
            },
            extra_condition=JobRecord.progress <= progress,
            transition_error="job progress cannot move backwards",
        )

    def request_cancellation(
        self,
        job_id: str,
        *,
        now: datetime | None = None,
    ) -> Job:
        current = _now(now)
        with self._session_factory.begin() as session:
            record = self._locked_record(session, job_id)
            if record is None:
                raise JobNotFoundError("job not found")
            if record.status in {"succeeded", "failed", "cancelled"}:
                return _to_job(record)
            if record.cancellation_requested_at is None:
                record.cancellation_requested_at = current
            record.updated_at = current
            if record.status == "queued":
                record.status = "cancelled"
                record.stage = "cancelled"
                record.finished_at = current
                record.next_attempt_at = None
            session.flush()
            return _to_job(record)

    def succeed(
        self,
        job_id: str,
        *,
        worker_id: str,
        result_resource_type: str,
        result_resource_id: str,
        result_digest: str,
        now: datetime | None = None,
    ) -> Job:
        validate_worker_id(worker_id)
        if _RESOURCE_TYPE_PATTERN.fullmatch(result_resource_type) is None:
            raise ValueError("result_resource_type is not a safe identifier")
        if _RESOURCE_ID_PATTERN.fullmatch(result_resource_id) is None:
            raise ValueError("result_resource_id is not a safe identifier")
        if _DIGEST_PATTERN.fullmatch(result_digest) is None:
            raise ValueError("result_digest must be a lowercase SHA-256 digest")
        current = _now(now)
        return self._leased_update(
            job_id,
            worker_id=worker_id,
            current=current,
            values={
                "status": "succeeded",
                "progress": 100,
                "stage": "succeeded",
                "lease_owner": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "result_resource_type": result_resource_type,
                "result_resource_id": result_resource_id,
                "result_digest": result_digest,
                "finished_at": current,
                "updated_at": current,
            },
        )

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        problem: JobProblem,
        retryable: bool,
        retry_delay: timedelta,
        now: datetime | None = None,
    ) -> Job:
        validate_worker_id(worker_id)
        retry_delay = _bounded_duration(
            retry_delay,
            maximum=_MAX_RETRY_DELAY,
            name="retry_delay",
            allow_zero=True,
        )
        current = _now(now)
        with self._session_factory.begin() as session:
            record = self._locked_record(session, job_id)
            self._require_valid_lease(
                record,
                worker_id=worker_id,
                current=current,
            )
            assert record is not None
            if record.cancellation_requested_at is not None:
                record.status = "cancelled"
                record.stage = "cancelled"
                record.finished_at = current
                record.next_attempt_at = None
                record.problem = None
            elif retryable and record.attempt_count < record.max_attempts:
                record.status = "queued"
                record.stage = "queued"
                record.progress = 0
                record.next_attempt_at = current + retry_delay
                record.problem = None
            else:
                record.status = "failed"
                record.stage = "failed"
                record.finished_at = current
                record.next_attempt_at = None
                record.problem = problem.as_json()
            _clear_lease(record)
            record.updated_at = current
            session.flush()
            return _to_job(record)

    def cancel(
        self,
        job_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> Job:
        validate_worker_id(worker_id)
        current = _now(now)
        with self._session_factory.begin() as session:
            record = self._locked_record(session, job_id)
            self._require_valid_lease(
                record,
                worker_id=worker_id,
                current=current,
            )
            assert record is not None
            if record.cancellation_requested_at is None:
                raise InvalidJobTransitionError(
                    "job cancellation has not been requested"
                )
            record.status = "cancelled"
            record.stage = "cancelled"
            record.finished_at = current
            record.next_attempt_at = None
            _clear_lease(record)
            record.updated_at = current
            session.flush()
            return _to_job(record)

    def recover_expired_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        current = _now(now)
        with self._session_factory.begin() as session:
            statement = (
                select(JobRecord)
                .where(
                    JobRecord.tenant_id == self._tenant_id,
                    JobRecord.status == "running",
                    JobRecord.lease_expires_at <= current,
                )
                .order_by(JobRecord.lease_expires_at, JobRecord.job_id)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            records = tuple(session.scalars(statement))
            for record in records:
                if record.cancellation_requested_at is not None:
                    record.status = "cancelled"
                    record.stage = "cancelled"
                    record.finished_at = current
                elif record.attempt_count < record.max_attempts:
                    record.status = "queued"
                    record.stage = "queued"
                    record.progress = 0
                    record.next_attempt_at = current
                else:
                    record.status = "failed"
                    record.stage = "failed"
                    record.finished_at = current
                    record.problem = JobProblem(
                        code="lease_expired",
                        detail="The worker lease expired before the job completed.",
                        occurred_at=current,
                    ).as_json()
                _clear_lease(record)
                record.updated_at = current
            session.flush()
            return len(records)

    def _get_by_idempotency(
        self,
        kind: JobKind,
        key: str,
    ) -> JobRecord | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(JobRecord).where(
                    JobRecord.tenant_id == self._tenant_id,
                    JobRecord.kind == kind,
                    JobRecord.idempotency_key == key,
                )
            )
            if record is not None:
                session.expunge(record)
            return record

    def _new_record(self, command: SubmitJob) -> JobRecord:
        return JobRecord(
            job_id=str(uuid4()),
            tenant_id=self._tenant_id,
            kind=command.kind,
            status="queued",
            created_by=command.created_by,
            request_payload=dict(command.request_payload),
            request_digest=command.request_digest,
            idempotency_key=command.idempotency_key,
            attempt_count=0,
            max_attempts=command.max_attempts,
            progress=0,
            stage="queued",
        )

    @staticmethod
    def _replay_or_conflict(
        record: JobRecord,
        command: SubmitJob,
    ) -> JobSubmission:
        if record.request_digest != command.request_digest:
            raise JobConflictError(
                "The idempotency key was used for a different request."
            )
        return JobSubmission(job=_to_job(record), created=False)

    def _eligible_statement(self, current: datetime) -> Select[tuple[JobRecord]]:
        return (
            select(JobRecord)
            .where(
                JobRecord.tenant_id == self._tenant_id,
                JobRecord.status == "queued",
                JobRecord.cancellation_requested_at.is_(None),
                or_(
                    JobRecord.next_attempt_at.is_(None),
                    JobRecord.next_attempt_at <= current,
                ),
            )
            .order_by(JobRecord.created_at, JobRecord.job_id)
            .limit(1)
        )

    def _locked_record(
        self,
        session: Session,
        job_id: str,
    ) -> JobRecord | None:
        statement = select(JobRecord).where(
            JobRecord.tenant_id == self._tenant_id,
            JobRecord.job_id == job_id,
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _leased_update(
        self,
        job_id: str,
        *,
        worker_id: str,
        current: datetime,
        values: Mapping[str, object],
        extra_condition: ColumnElement[bool] | None = None,
        transition_error: str | None = None,
    ) -> Job:
        conditions = [
            JobRecord.tenant_id == self._tenant_id,
            JobRecord.job_id == job_id,
            JobRecord.status == "running",
            JobRecord.lease_owner == worker_id,
            JobRecord.lease_expires_at > current,
        ]
        if extra_condition is not None:
            conditions.append(extra_condition)
        with self._session_factory.begin() as session:
            updated_id = session.scalar(
                update(JobRecord)
                .where(*conditions)
                .values(**values)
                .returning(JobRecord.job_id)
            )
            if updated_id is None:
                record = self._locked_record(session, job_id)
                if transition_error is not None and _owns_valid_lease(
                    record,
                    worker_id=worker_id,
                    current=current,
                ):
                    raise InvalidJobTransitionError(transition_error)
                raise JobLeaseError("worker does not own a valid job lease")
            record = session.get(JobRecord, updated_id)
            if record is None:
                raise JobNotFoundError("job not found")
            session.refresh(record)
            return _to_job(record)

    @staticmethod
    def _require_valid_lease(
        record: JobRecord | None,
        *,
        worker_id: str,
        current: datetime,
    ) -> None:
        if not _owns_valid_lease(
            record,
            worker_id=worker_id,
            current=current,
        ):
            raise JobLeaseError("worker does not own a valid job lease")


def _claim_record(
    record: JobRecord,
    *,
    worker_id: str,
    current: datetime,
    lease_expires_at: datetime,
) -> None:
    record.status = "running"
    record.attempt_count += 1
    record.progress = 0
    record.stage = "starting"
    record.lease_owner = worker_id
    record.lease_expires_at = lease_expires_at
    record.heartbeat_at = current
    record.next_attempt_at = None
    record.started_at = record.started_at or current
    record.updated_at = current


def _clear_lease(record: JobRecord) -> None:
    record.lease_owner = None
    record.lease_expires_at = None
    record.heartbeat_at = None


def _owns_valid_lease(
    record: JobRecord | None,
    *,
    worker_id: str,
    current: datetime,
) -> bool:
    return bool(
        record is not None
        and record.status == "running"
        and record.lease_owner == worker_id
        and record.lease_expires_at is not None
        and record.lease_expires_at > current
    )


def _to_job(record: JobRecord) -> Job:
    return Job(
        job_id=record.job_id,
        tenant_id=record.tenant_id,
        kind=record.kind,
        status=record.status,
        created_by=record.created_by,
        request_payload=dict(record.request_payload),
        request_digest=record.request_digest,
        idempotency_key=record.idempotency_key,
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        progress=record.progress,
        stage=record.stage,
        lease_owner=record.lease_owner,
        lease_expires_at=record.lease_expires_at,
        heartbeat_at=record.heartbeat_at,
        cancellation_requested_at=record.cancellation_requested_at,
        next_attempt_at=record.next_attempt_at,
        result_resource_type=record.result_resource_type,
        result_resource_id=record.result_resource_id,
        result_digest=record.result_digest,
        problem=None if record.problem is None else dict(record.problem),
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        updated_at=record.updated_at,
    )


def _now(value: datetime | None) -> datetime:
    return datetime.now(UTC) if value is None else _aware_utc(value, name="now")


def _aware_utc(value: datetime, *, name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_duration(
    value: timedelta,
    *,
    maximum: timedelta,
    name: str,
    allow_zero: bool = False,
) -> timedelta:
    minimum = timedelta(0)
    if value < minimum or (value == minimum and not allow_zero) or value > maximum:
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier} and bounded")
    return value
