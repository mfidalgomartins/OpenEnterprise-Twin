"""Versioned HTTP resources for scenarios, experiments and decisions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from openenterprise_twin.api.dependencies import (
    AppServices,
    get_services,
    get_session,
)
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.api.schemas import ExperimentCreate, ExperimentRead
from openenterprise_twin.application.decisions import (
    DecisionEvidenceError,
    get_or_build_brief,
    get_or_build_comparison,
)
from openenterprise_twin.application.experiments import ExperimentQueueFullError
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.infrastructure.models import ExperimentRecord
from openenterprise_twin.infrastructure.repositories import (
    ExperimentRepository,
    ScenarioRepository,
)
from openenterprise_twin.reporting.brief import ExecutiveBrief
from openenterprise_twin.scenarios.comparison import ScenarioComparison

router = APIRouter(prefix="/api/v1")
SessionDependency = Annotated[Session, Depends(get_session)]
ServicesDependency = Annotated[AppServices, Depends(get_services)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


@router.post(
    "/scenarios",
    response_model=Scenario,
    status_code=status.HTTP_201_CREATED,
)
def create_scenario(
    scenario: Scenario,
    response: Response,
    session: SessionDependency,
) -> Scenario:
    repository = ScenarioRepository(session)
    with session.begin():
        if repository.get(scenario.scenario_id) is not None:
            raise ApiProblemError(
                status=409,
                code="scenario_conflict",
                title="Scenario already exists",
                detail=f"Scenario '{scenario.scenario_id}' already exists.",
            )
        repository.create(scenario)
    response.headers["Location"] = f"/api/v1/scenarios/{scenario.scenario_id}"
    return scenario


@router.get("/scenarios/{scenario_id}", response_model=Scenario)
def get_scenario(scenario_id: str, session: SessionDependency) -> Scenario:
    record = ScenarioRepository(session).get(scenario_id)
    if record is None:
        raise _scenario_not_found(scenario_id)
    return Scenario.model_validate(record.payload)


@router.post(
    "/scenarios/{scenario_id}/experiments",
    response_model=ExperimentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_experiment(
    scenario_id: str,
    request: ExperimentCreate,
    response: Response,
    session: SessionDependency,
    services: ServicesDependency,
    idempotency_key: IdempotencyKey = None,
) -> ExperimentRead:
    scenarios = ScenarioRepository(session)
    experiments = ExperimentRepository(session)
    created = False
    with session.begin():
        scenario_record = scenarios.get(scenario_id)
        if scenario_record is None:
            raise _scenario_not_found(scenario_id)
        request_payload = request.model_dump(mode="json")
        existing = (
            experiments.get_by_idempotency_key(idempotency_key)
            if idempotency_key is not None
            else None
        )
        if existing is not None:
            if (
                existing.scenario_id != scenario_id
                or existing.request_payload != request_payload
            ):
                raise ApiProblemError(
                    status=409,
                    code="idempotency_conflict",
                    title="Idempotency key conflict",
                    detail="The idempotency key was used for a different request.",
                )
            record = existing
        else:
            scenario = Scenario.model_validate(scenario_record.payload)
            baseline_experiment_id = _resolve_baseline_experiment_id(
                scenario,
                experiments=experiments,
                request=request,
            )
            record = experiments.create(
                scenario_id=scenario_id,
                baseline_experiment_id=baseline_experiment_id,
                master_seed=request.master_seed,
                replication_count=request.replications,
                idempotency_key=idempotency_key,
                request_payload=request_payload,
            )
            created = True
        payload = _experiment_read(record)

    if created:
        try:
            services.experiment_runner.submit(record.id)
        except ExperimentQueueFullError as error:
            with session.begin():
                experiments.mark_failed(
                    record,
                    error_code="experiment_queue_full",
                    error_detail=str(error),
                )
            raise ApiProblemError(
                status=429,
                code="experiment_queue_full",
                title="Experiment queue is full",
                detail="Retry the request after an active experiment completes.",
            ) from error

    response.headers["Location"] = f"/api/v1/experiments/{record.id}"
    return payload


@router.get("/experiments/{experiment_id}", response_model=ExperimentRead)
def get_experiment(
    experiment_id: int,
    session: SessionDependency,
) -> ExperimentRead:
    record = ExperimentRepository(session).get(experiment_id)
    if record is None:
        raise _experiment_not_found(experiment_id)
    return _experiment_read(record)


@router.get(
    "/experiments/{experiment_id}/comparison",
    response_model=ScenarioComparison,
)
def get_comparison(
    experiment_id: int,
    session: SessionDependency,
    services: ServicesDependency,
) -> ScenarioComparison:
    repository = ExperimentRepository(session)
    with session.begin():
        record = repository.get(experiment_id)
        if record is None:
            raise _experiment_not_found(experiment_id)
        try:
            return get_or_build_comparison(
                record,
                repository=repository,
                artifact_store=services.artifact_store,
            )
        except DecisionEvidenceError as error:
            raise _decision_problem(error) from error


@router.get(
    "/experiments/{experiment_id}/report",
    response_model=ExecutiveBrief,
)
def get_report(
    experiment_id: int,
    session: SessionDependency,
    services: ServicesDependency,
) -> ExecutiveBrief:
    repository = ExperimentRepository(session)
    with session.begin():
        record = repository.get(experiment_id)
        if record is None:
            raise _experiment_not_found(experiment_id)
        try:
            return get_or_build_brief(
                record,
                repository=repository,
                artifact_store=services.artifact_store,
            )
        except DecisionEvidenceError as error:
            raise _decision_problem(error) from error


def _resolve_baseline_experiment_id(
    scenario: Scenario,
    *,
    experiments: ExperimentRepository,
    request: ExperimentCreate,
) -> int | None:
    if scenario.baseline_scenario_id is None:
        return None
    baseline = experiments.latest_completed_for_scenario(
        scenario_id=scenario.baseline_scenario_id,
        master_seed=request.master_seed,
        replication_count=request.replications,
    )
    if baseline is None:
        raise ApiProblemError(
            status=409,
            code="baseline_experiment_missing",
            title="Compatible baseline experiment missing",
            detail=(
                "Run the referenced baseline scenario with the same seed and "
                "replication count before this candidate."
            ),
        )
    return baseline.id


def _experiment_read(record: ExperimentRecord) -> ExperimentRead:
    return ExperimentRead(
        id=record.id,
        scenario_id=record.scenario_id,
        baseline_experiment_id=record.baseline_experiment_id,
        status=record.status,
        master_seed=record.master_seed,
        replication_count=record.replication_count,
        artifact_digest=record.artifact_digest,
        error_code=record.error_code,
        error_detail=record.error_detail,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


def _scenario_not_found(scenario_id: str) -> ApiProblemError:
    return ApiProblemError(
        status=404,
        code="scenario_not_found",
        title="Scenario not found",
        detail=f"Scenario '{scenario_id}' does not exist.",
    )


def _experiment_not_found(experiment_id: int) -> ApiProblemError:
    return ApiProblemError(
        status=404,
        code="experiment_not_found",
        title="Experiment not found",
        detail=f"Experiment '{experiment_id}' does not exist.",
    )


def _decision_problem(error: DecisionEvidenceError) -> ApiProblemError:
    return ApiProblemError(
        status=409,
        code=error.code,
        title="Decision evidence unavailable",
        detail=error.detail,
    )
