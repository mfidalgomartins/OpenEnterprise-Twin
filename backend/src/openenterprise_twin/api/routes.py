"""Versioned HTTP resources for scenarios, experiments and decisions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, Security, status
from sqlalchemy.orm import Session

from openenterprise_twin.api.dependencies import (
    AppServices,
    get_services,
    get_session,
    require_principal,
)
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.api.schemas import (
    ExperimentCreate,
    ExperimentRead,
    ScenarioRead,
)
from openenterprise_twin.application.decisions import (
    DecisionEvidenceError,
    get_or_build_brief,
    get_or_build_comparison,
)
from openenterprise_twin.application.experiments import ExperimentQueueFullError
from openenterprise_twin.application.portfolio import (
    DecisionPortfolio,
    PolicyFrontier,
    build_policy_frontier,
    list_decision_portfolio,
)
from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.scenario import (
    Scenario,
    validate_scenario_against_company,
)
from openenterprise_twin.infrastructure.models import ExperimentRecord
from openenterprise_twin.infrastructure.repositories import (
    ExperimentRepository,
    ScenarioRepository,
)
from openenterprise_twin.reporting.brief import ExecutiveBrief
from openenterprise_twin.scenarios.comparison import ScenarioComparison
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)

public_router = APIRouter()
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Security(require_principal)],
)
SessionDependency = Annotated[Session, Depends(get_session)]
ServicesDependency = Annotated[AppServices, Depends(get_services)]
IdempotencyKey = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


@router.post(
    "/scenarios",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scenario(
    scenario: Scenario,
    response: Response,
    session: SessionDependency,
) -> ScenarioRead:
    try:
        validate_scenario_against_company(scenario, build_northstar_company())
    except DomainValidationError as error:
        raise ApiProblemError(
            status=422,
            code="scenario_incompatible",
            title="Scenario is incompatible with the company model",
            detail=str(error),
        ) from error
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
    return _scenario_read(scenario)


@public_router.get("/health", include_in_schema=False)
@public_router.get("/api/v1/health", include_in_schema=False)
def get_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/company", response_model=CompanyModel)
def get_company() -> CompanyModel:
    return build_northstar_company()


@router.get("/baseline", response_model=ScenarioRead)
def get_baseline() -> ScenarioRead:
    return _scenario_read(build_baseline_scenario())


@router.get("/scenarios", response_model=tuple[ScenarioRead, ...])
def list_scenarios(
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
) -> tuple[ScenarioRead, ...]:
    repository = ScenarioRepository(session)
    return tuple(
        _scenario_read(Scenario.model_validate(record.payload))
        for record in repository.list(limit=limit, after_id=after_id)
    )


@router.get("/scenarios/{scenario_id}", response_model=ScenarioRead)
def get_scenario(scenario_id: str, session: SessionDependency) -> ScenarioRead:
    record = ScenarioRepository(session).get(scenario_id)
    if record is None:
        raise _scenario_not_found(scenario_id)
    return _scenario_read(Scenario.model_validate(record.payload))


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
            requested_periods = request.replications * scenario.horizon_days
            if requested_periods > services.max_experiment_periods:
                raise ApiProblemError(
                    status=422,
                    code="experiment_budget_exceeded",
                    title="Experiment compute budget exceeded",
                    detail=(
                        f"This request needs {requested_periods:,} simulated "
                        f"periods; the deployment limit is "
                        f"{services.max_experiment_periods:,}. Reduce the "
                        "replication count or scenario horizon."
                    ),
                )
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
                queued_record = experiments.get(record.id)
                if queued_record is not None:
                    experiments.delete_queued(queued_record)
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
    services: ServicesDependency,
) -> ScenarioComparison:
    try:
        return get_or_build_comparison(
            experiment_id,
            repository=services.decision_repository,
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
    services: ServicesDependency,
) -> ExecutiveBrief:
    try:
        return get_or_build_brief(
            experiment_id,
            repository=services.decision_repository,
            artifact_store=services.artifact_store,
        )
    except DecisionEvidenceError as error:
        raise _decision_problem(error) from error


@router.get("/decisions", response_model=DecisionPortfolio)
def list_decisions(
    services: ServicesDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    before_id: Annotated[int | None, Query(gt=0)] = None,
) -> DecisionPortfolio:
    return list_decision_portfolio(
        repository=services.decision_repository,
        artifact_store=services.artifact_store,
        limit=limit,
        before_id=before_id,
    )


@router.get("/frontier", response_model=PolicyFrontier)
def get_frontier(
    services: ServicesDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> PolicyFrontier:
    portfolio = list_decision_portfolio(
        repository=services.decision_repository,
        artifact_store=services.artifact_store,
        limit=limit,
    )
    return build_policy_frontier(portfolio.items)


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
    result_payload = baseline.result_payload or {}
    expected_metadata = {
        "company_model_version": scenario.company_model_version,
        "scenario_schema_version": scenario.schema_version,
        "horizon_days": scenario.horizon_days,
        "warmup_days": scenario.warmup_days,
        "evaluation_days": scenario.evaluation_days,
        "runoff_days": scenario.runoff_days,
    }
    if any(
        result_payload.get(name) != value
        for name, value in expected_metadata.items()
    ):
        raise ApiProblemError(
            status=409,
            code="baseline_experiment_incompatible",
            title="Completed baseline experiment is incompatible",
            detail=(
                "Run the baseline with the candidate model version, schema, "
                "and lifecycle calendar before creating this experiment."
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
        seed=record.master_seed,
        iterations=record.replication_count,
        artifact_digest=record.artifact_digest,
        error_code=record.error_code,
        error_detail=record.error_detail,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
    )


def _scenario_read(scenario: Scenario) -> ScenarioRead:
    return ScenarioRead(id=scenario.scenario_id, **scenario.model_dump())


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
        status=404 if error.code == "experiment_not_found" else 409,
        code=error.code,
        title="Decision evidence unavailable",
        detail=error.detail,
    )
