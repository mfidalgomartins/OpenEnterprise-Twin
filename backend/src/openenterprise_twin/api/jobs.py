"""Tenant-scoped HTTP resources for durable analytical jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, Security, status
from fastapi.responses import JSONResponse

from openenterprise_twin.api.dependencies import (
    ServicesDependency,
    analyst_guard,
    reader_guard,
)
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.api.schemas import ApiModel
from openenterprise_twin.application.jobs import (
    Job,
    JobConflictError,
    JobKind,
    JobStatus,
    JobSubmission,
    SubmitJob,
)
from openenterprise_twin.infrastructure.jobs import SqlJobRepository

jobs_router = APIRouter(
    prefix="/api/v1/jobs",
    dependencies=[Security(reader_guard)],
)


class JobRead(ApiModel):
    job_id: str
    kind: JobKind
    status: JobStatus
    created_by: str
    attempt_count: int
    max_attempts: int
    progress: int
    stage: str
    cancellation_requested_at: datetime | None
    next_attempt_at: datetime | None
    result_resource_type: str | None
    result_resource_id: str | None
    result_digest: str | None
    result_location: str | None
    problem: dict[str, object] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


@jobs_router.get("", response_model=tuple[JobRead, ...])
def list_jobs(
    services: ServicesDependency,
    status_filter: Annotated[
        list[JobStatus] | None,
        Query(alias="status"),
    ] = None,
    kind_filter: Annotated[
        list[JobKind] | None,
        Query(alias="kind"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before_created_at: datetime | None = None,
    before_job_id: Annotated[
        str | None,
        Query(min_length=1, max_length=36),
    ] = None,
) -> tuple[JobRead, ...]:
    repository = SqlJobRepository(
        services.session_factory,
        services.tenant_id,
    )
    try:
        jobs = repository.list(
            statuses=(
                None if status_filter is None else frozenset(status_filter)
            ),
            kinds=None if kind_filter is None else frozenset(kind_filter),
            limit=limit,
            before_created_at=before_created_at,
            before_job_id=before_job_id,
        )
    except ValueError as error:
        raise ApiProblemError(
            status=422,
            code="invalid_job_cursor",
            title="Invalid job cursor",
            detail="The supplied job pagination cursor is invalid.",
        ) from error
    return tuple(job_read(job) for job in jobs)


@jobs_router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, services: ServicesDependency) -> JobRead:
    job = _repository(services).get(job_id)
    if job is None:
        raise job_not_found()
    return job_read(job)


@jobs_router.get("/{job_id}/result")
def get_job_result(
    job_id: str,
    services: ServicesDependency,
) -> JSONResponse:
    job = _repository(services).get(job_id)
    if job is None:
        raise job_not_found()
    if job.status != "succeeded" or job.result_digest is None:
        raise ApiProblemError(
            status=409,
            code="job_result_not_ready",
            title="Job result is not ready",
            detail="The job has not produced a successful result.",
        )
    try:
        payload = services.artifact_store.get_json(job.result_digest)
    except (OSError, ValueError) as error:
        raise ApiProblemError(
            status=503,
            code="job_result_unavailable",
            title="Job result is unavailable",
            detail="The completed job result could not be loaded.",
        ) from error
    return JSONResponse(content=payload)


@jobs_router.post(
    "/{job_id}/cancellation",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Security(analyst_guard)],
)
def cancel_job(
    job_id: str,
    services: ServicesDependency,
) -> JobRead:
    repository = _repository(services)
    if repository.get(job_id) is None:
        raise job_not_found()
    return job_read(repository.request_cancellation(job_id))


def submit_job(
    repository: SqlJobRepository,
    command: SubmitJob,
) -> JobSubmission:
    """Map stable application conflicts to the public problem contract."""

    try:
        return repository.submit(command)
    except JobConflictError as error:
        raise idempotency_conflict() from error


def set_job_response(
    response: Response,
    job: Job,
) -> JobRead:
    response.headers["Location"] = f"/api/v1/jobs/{job.job_id}"
    return job_read(job)


def job_read(job: Job) -> JobRead:
    return JobRead(
        job_id=job.job_id,
        kind=job.kind,
        status=job.status,
        created_by=job.created_by,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        progress=job.progress,
        stage=job.stage,
        cancellation_requested_at=job.cancellation_requested_at,
        next_attempt_at=job.next_attempt_at,
        result_resource_type=job.result_resource_type,
        result_resource_id=job.result_resource_id,
        result_digest=job.result_digest,
        result_location=(
            f"/api/v1/jobs/{job.job_id}/result"
            if job.status == "succeeded" and job.result_digest is not None
            else None
        ),
        problem=None if job.problem is None else dict(job.problem),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
    )


def idempotency_conflict() -> ApiProblemError:
    return ApiProblemError(
        status=409,
        code="idempotency_conflict",
        title="Idempotency key conflict",
        detail="The idempotency key was used for a different request.",
    )


def job_not_found() -> ApiProblemError:
    return ApiProblemError(
        status=404,
        code="job_not_found",
        title="Job not found",
        detail="The requested job does not exist.",
    )


def _repository(services: ServicesDependency) -> SqlJobRepository:
    return SqlJobRepository(
        services.session_factory,
        services.tenant_id,
    )
