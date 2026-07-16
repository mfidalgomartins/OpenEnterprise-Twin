"""Typed request and response contracts for the public API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ExperimentStatus = Literal["queued", "running", "completed", "failed"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentCreate(ApiModel):
    replications: Annotated[int, Field(gt=0, le=10_000)]
    master_seed: Annotated[int, Field(ge=0)]
    max_workers: Annotated[int, Field(gt=0, le=16)] = 1


class ExperimentRead(ApiModel):
    id: int
    scenario_id: str
    baseline_experiment_id: int | None
    status: ExperimentStatus
    master_seed: int
    replication_count: int
    artifact_digest: str | None
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class FieldViolation(ApiModel):
    field: str
    message: str


class ProblemDetail(ApiModel):
    type: str = "about:blank"
    title: str
    status: int
    code: str
    detail: str
    trace_id: str
    violations: tuple[FieldViolation, ...] = ()
