"""Typed request and response contracts for the public API."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from openenterprise_twin.domain.scenario import Scenario

ExperimentStatus = Literal["queued", "running", "completed", "failed"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExperimentCreate(ApiModel):
    replications: Annotated[
        int,
        Field(
            gt=0,
            le=1_000,
            validation_alias=AliasChoices("iterations", "replications"),
        ),
    ]
    master_seed: Annotated[
        int,
        Field(
            ge=0,
            le=2**63 - 1,
            validation_alias=AliasChoices("seed", "master_seed"),
        ),
    ]
    max_workers: Annotated[int, Field(gt=0, le=16)] = 1


class ScenarioRead(Scenario):
    """Public scenario representation with the canonical resource identifier."""

    id: str


class ExperimentRead(ApiModel):
    id: int
    scenario_id: str
    baseline_experiment_id: int | None
    status: ExperimentStatus
    master_seed: int
    replication_count: int
    seed: int
    iterations: int
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
