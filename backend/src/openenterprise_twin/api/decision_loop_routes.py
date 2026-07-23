"""HTTP resources for the governed decision loop.

These endpoints expose calibration, optimization, adaptive-policy comparison,
the decision ledger and outcome monitoring. Long-running work is bounded by
deployment limits so requests stay responsive without a separate job runner.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, Security, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from openenterprise_twin.analytics.adaptive import (
    AdaptiveComparison,
    AdaptivePolicy,
    compare_adaptive_vs_static,
)
from openenterprise_twin.analytics.backtesting import BacktestResult
from openenterprise_twin.analytics.calibration import CalibrationResult
from openenterprise_twin.analytics.credibility import CredibilityScore
from openenterprise_twin.analytics.history import (
    DatasetProvenance,
    HistoricalDataset,
    HistoricalObservation,
    SourceKind,
    build_dataset,
)
from openenterprise_twin.analytics.ingestion import (
    dataset_to_csv,
    observations_from_csv,
)
from openenterprise_twin.analytics.monitoring import (
    MetricPrediction,
    MonitoringReport,
    OutcomeRecord,
)
from openenterprise_twin.analytics.optimization import (
    OptimizationConfig,
    OptimizationResult,
)
from openenterprise_twin.analytics.quality import DataQualityReport
from openenterprise_twin.analytics.synthetic import generate_northstar_history
from openenterprise_twin.api.dependencies import (
    AppServices,
    get_services,
    require_principal,
)
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.application.decision_loop import (
    DatasetTooLargeError,
    StoredDataset,
)
from openenterprise_twin.application.ledger import (
    DecisionListItem,
    DecisionSnapshot,
)
from openenterprise_twin.domain.ledger import (
    ApprovalRecord,
    DecisionContent,
    DecisionPacket,
    DecisionState,
    DecisionTransition,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)

decision_loop_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Security(require_principal)],
)
ServicesDependency = Annotated[AppServices, Depends(get_services)]


class LoopModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Calibration Studio --------------------------------------------------------


class DatasetIngestRequest(LoopModel):
    dataset_id: str = Field(min_length=1, max_length=128)
    company_id: str = Field(min_length=1, max_length=128)
    source_kind: SourceKind
    source_reference: str = Field(min_length=1, max_length=256)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    observations: tuple[HistoricalObservation, ...] = Field(min_length=1)


class SyntheticDatasetRequest(LoopModel):
    dataset_id: str = Field(default="northstar-history", min_length=1, max_length=128)
    seed: int = Field(default=20240115, ge=0)
    days: int = Field(default=540, ge=60, le=2000)


class DatasetSummary(LoopModel):
    dataset_id: str
    company_id: str
    data_digest: str
    observation_count: int
    created_at: datetime


class DatasetIngestResponse(LoopModel):
    dataset: DatasetSummary
    quality: DataQualityReport


class CalibrationRequest(LoopModel):
    calibration_id: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    backtest_cutoff: date | None = None


class CalibrationResponse(LoopModel):
    calibration_id: str
    dataset_id: str
    created_at: datetime
    calibration: CalibrationResult
    credibility: CredibilityScore
    backtests: tuple[BacktestResult, ...]


@decision_loop_router.post(
    "/datasets",
    response_model=DatasetIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_dataset(
    request: DatasetIngestRequest,
    response: Response,
    services: ServicesDependency,
) -> DatasetIngestResponse:
    dataset = _build_ingest_dataset(request)
    stored = _ingest(services, dataset)
    response.headers["Location"] = f"/api/v1/datasets/{stored.dataset_id}"
    return DatasetIngestResponse(
        dataset=_dataset_summary(stored),
        quality=stored.quality,
    )


@decision_loop_router.post(
    "/datasets/synthetic",
    response_model=DatasetIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_synthetic_dataset(
    request: SyntheticDatasetRequest,
    response: Response,
    services: ServicesDependency,
) -> DatasetIngestResponse:
    company = build_northstar_company()
    dataset = generate_northstar_history(
        company,
        seed=request.seed,
        days=request.days,
        dataset_id=request.dataset_id,
    )
    stored = _ingest(services, dataset)
    response.headers["Location"] = f"/api/v1/datasets/{stored.dataset_id}"
    return DatasetIngestResponse(
        dataset=_dataset_summary(stored),
        quality=stored.quality,
    )


@decision_loop_router.post(
    "/datasets/csv",
    response_model=DatasetIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_dataset_csv(
    request: Request,
    response: Response,
    services: ServicesDependency,
    dataset_id: Annotated[str, Query(min_length=1, max_length=128)],
    company_id: Annotated[str, Query(min_length=1, max_length=128)],
    source_reference: Annotated[
        str, Query(min_length=1, max_length=256)
    ] = "csv-upload",
) -> DatasetIngestResponse:
    """Ingest a long-format CSV body (``text/csv``) of canonical observations."""

    raw = await request.body()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ApiProblemError(
            status=422,
            code="invalid_encoding",
            title="CSV must be UTF-8 encoded",
            detail="The uploaded CSV could not be decoded as UTF-8.",
        ) from error
    observations = observations_from_csv(content)
    dataset = build_dataset(
        dataset_id=dataset_id,
        company_id=company_id,
        observations=observations,
        source_kind="csv",
        source_reference=source_reference,
    )
    stored = _ingest(services, dataset)
    response.headers["Location"] = f"/api/v1/datasets/{stored.dataset_id}"
    return DatasetIngestResponse(
        dataset=_dataset_summary(stored),
        quality=stored.quality,
    )


@decision_loop_router.get(
    "/datasets/{dataset_id}/export.csv",
    response_class=PlainTextResponse,
)
def export_dataset_csv(
    dataset_id: str,
    services: ServicesDependency,
) -> PlainTextResponse:
    """Export a dataset as formula-neutralised CSV, safe to open in a sheet."""

    dataset = services.calibration_studio.get_dataset(dataset_id)
    if dataset is None:
        raise ApiProblemError(
            status=404,
            code="dataset_not_found",
            title="Dataset not found",
            detail=f"Dataset '{dataset_id}' does not exist.",
        )
    return PlainTextResponse(
        content=dataset_to_csv(dataset),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{dataset_id}.csv"'
            )
        },
    )


def _ingest(services: AppServices, dataset: HistoricalDataset) -> StoredDataset:
    try:
        return services.calibration_studio.ingest_dataset(dataset)
    except DatasetTooLargeError as error:
        raise ApiProblemError(
            status=413,
            code="dataset_too_large",
            title="Dataset exceeds the ingestion limit",
            detail=(
                f"{error.observation_count:,} observations exceed the limit of "
                f"{error.limit:,}."
            ),
        ) from error


@decision_loop_router.post(
    "/calibrations",
    response_model=CalibrationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_calibration(
    request: CalibrationRequest,
    response: Response,
    services: ServicesDependency,
) -> CalibrationResponse:
    stored = services.calibration_studio.calibrate(
        calibration_id=request.calibration_id,
        dataset_id=request.dataset_id,
        company=build_northstar_company(),
        backtest_cutoff=request.backtest_cutoff,
    )
    response.headers["Location"] = f"/api/v1/calibrations/{stored.calibration_id}"
    return CalibrationResponse(
        calibration_id=stored.calibration_id,
        dataset_id=stored.dataset_id,
        created_at=stored.created_at,
        calibration=stored.calibration,
        credibility=stored.credibility,
        backtests=stored.backtests,
    )


# --- Optimization Lab ----------------------------------------------------------


class OptimizationRequest(LoopModel):
    config: OptimizationConfig
    horizon_days: int = Field(default=120, ge=30, le=730)
    replications: int = Field(default=8, ge=1, le=200)
    master_seed: int = Field(default=20240115, ge=0)


class OptimizationResponse(LoopModel):
    optimization_id: int
    digest: str
    evaluations: int
    created_at: datetime
    result: OptimizationResult


@decision_loop_router.post(
    "/optimizations",
    response_model=OptimizationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_optimization(
    request: OptimizationRequest,
    response: Response,
    services: ServicesDependency,
) -> OptimizationResponse:
    stored = services.optimization_lab.optimize(
        company=build_northstar_company(),
        base_scenario=build_baseline_scenario(horizon_days=request.horizon_days),
        config=request.config,
        replications=request.replications,
        master_seed=request.master_seed,
    )
    response.headers["Location"] = (
        f"/api/v1/optimizations/{stored.optimization_id}"
    )
    return OptimizationResponse(
        optimization_id=stored.optimization_id,
        digest=stored.digest,
        evaluations=stored.evaluations,
        created_at=stored.created_at,
        result=stored.result,
    )


# --- Adaptive Policy Builder ---------------------------------------------------


class AdaptiveCompareRequest(LoopModel):
    policy: AdaptivePolicy
    horizon_days: int = Field(default=120, ge=30, le=730)
    replications: int = Field(default=8, ge=1, le=200)
    master_seed: int = Field(default=20240115, ge=0)


@decision_loop_router.post("/adaptive-policies/validate")
def validate_adaptive_policy(policy: AdaptivePolicy) -> dict[str, str]:
    # Contradiction and schema validation happen while parsing the body.
    return {"policy_id": policy.policy_id, "status": "valid"}


@decision_loop_router.post(
    "/adaptive-policies/compare",
    response_model=AdaptiveComparison,
)
def compare_adaptive_policy(
    request: AdaptiveCompareRequest,
    services: ServicesDependency,
) -> AdaptiveComparison:
    estimated_periods = 2 * request.replications * request.horizon_days
    if estimated_periods > services.max_adaptive_periods:
        raise ApiProblemError(
            status=422,
            code="adaptive_budget_exceeded",
            title="Adaptive comparison compute budget exceeded",
            detail=(
                f"This comparison needs {estimated_periods:,} simulated periods; "
                f"the deployment limit is {services.max_adaptive_periods:,}."
            ),
        )
    return compare_adaptive_vs_static(
        company=build_northstar_company(),
        static_scenario=build_baseline_scenario(horizon_days=request.horizon_days),
        policy=request.policy,
        master_seed=request.master_seed,
        replications=request.replications,
    )


# --- Decision Ledger -----------------------------------------------------------


class DecisionCreateRequest(LoopModel):
    decision_id: str = Field(min_length=1, max_length=128)
    content: DecisionContent


class DecisionTransitionRequest(LoopModel):
    expected_version: int = Field(ge=1)
    target: DecisionState
    actor: str = Field(min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=280)
    approval: ApprovalRecord | None = None


class DecisionSnapshotResponse(LoopModel):
    decision_id: str
    state: DecisionState
    version: int
    owner: str
    content: DecisionContent
    transitions: tuple[DecisionTransition, ...]
    approvals: tuple[ApprovalRecord, ...]
    created_at: datetime
    updated_at: datetime


@decision_loop_router.post(
    "/ledger/decisions",
    response_model=DecisionSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_decision(
    request: DecisionCreateRequest,
    response: Response,
    services: ServicesDependency,
) -> DecisionSnapshotResponse:
    snapshot = services.decision_ledger.create_decision(
        decision_id=request.decision_id,
        content=request.content,
        actor=request.content.owner,
        occurred_at=datetime.now(UTC),
    )
    response.headers["Location"] = f"/api/v1/ledger/decisions/{snapshot.decision_id}"
    return _snapshot_response(snapshot)


@decision_loop_router.get(
    "/ledger/decisions",
    response_model=tuple[DecisionListItem, ...],
)
def list_decisions(
    services: ServicesDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> tuple[DecisionListItem, ...]:
    return services.decision_ledger.list_decisions(limit=limit, after_id=after_id)


@decision_loop_router.get(
    "/ledger/decisions/{decision_id}",
    response_model=DecisionSnapshotResponse,
)
def get_decision(
    decision_id: str,
    services: ServicesDependency,
) -> DecisionSnapshotResponse:
    return _snapshot_response(services.decision_ledger.get(decision_id))


@decision_loop_router.post(
    "/ledger/decisions/{decision_id}/transitions",
    response_model=DecisionSnapshotResponse,
)
def transition_decision(
    decision_id: str,
    request: DecisionTransitionRequest,
    services: ServicesDependency,
) -> DecisionSnapshotResponse:
    snapshot = services.decision_ledger.transition(
        decision_id=decision_id,
        expected_version=request.expected_version,
        target=request.target,
        actor=request.actor,
        occurred_at=datetime.now(UTC),
        note=request.note,
        approval=request.approval,
    )
    return _snapshot_response(snapshot)


@decision_loop_router.get(
    "/ledger/decisions/{decision_id}/packet",
    response_model=DecisionPacket,
)
def export_decision_packet(
    decision_id: str,
    services: ServicesDependency,
) -> DecisionPacket:
    return services.decision_ledger.export_packet(
        decision_id=decision_id,
        exported_at=datetime.now(UTC),
    )


# --- Monitoring Center ---------------------------------------------------------


class OutcomeRequest(LoopModel):
    predictions: tuple[MetricPrediction, ...] = Field(min_length=1)
    outcomes: tuple[OutcomeRecord, ...] = Field(min_length=1)
    parameter_change: float = 0.0
    data_quality_delta: float = 0.0


@decision_loop_router.post(
    "/ledger/decisions/{decision_id}/outcomes",
    response_model=MonitoringReport,
    status_code=status.HTTP_201_CREATED,
)
def record_outcomes(
    decision_id: str,
    request: OutcomeRequest,
    services: ServicesDependency,
) -> MonitoringReport:
    return services.monitoring.record_outcomes(
        decision_id=decision_id,
        predictions=request.predictions,
        outcomes=request.outcomes,
        now=datetime.now(UTC),
        parameter_change=request.parameter_change,
        data_quality_delta=request.data_quality_delta,
    )


@decision_loop_router.get(
    "/ledger/decisions/{decision_id}/monitoring",
    response_model=MonitoringReport,
)
def get_monitoring(
    decision_id: str,
    services: ServicesDependency,
) -> MonitoringReport:
    report = services.monitoring.latest(decision_id)
    if report is None:
        raise ApiProblemError(
            status=404,
            code="monitoring_not_found",
            title="No monitoring report",
            detail=f"Decision '{decision_id}' has no recorded outcomes yet.",
        )
    return report


# --- helpers -------------------------------------------------------------------


def _build_ingest_dataset(request: DatasetIngestRequest) -> HistoricalDataset:
    provenance = DatasetProvenance(
        source_kind=request.source_kind,
        source_reference=request.source_reference,
        ingested_at=datetime.now(UTC),
        timezone=request.timezone,
    )
    return build_dataset(
        dataset_id=request.dataset_id,
        company_id=request.company_id,
        observations=request.observations,
        source_kind=provenance.source_kind,
        source_reference=provenance.source_reference,
        timezone=provenance.timezone,
        ingested_at=provenance.ingested_at,
    )


def _dataset_summary(stored: StoredDataset) -> DatasetSummary:
    return DatasetSummary(
        dataset_id=stored.dataset_id,
        company_id=stored.company_id,
        data_digest=stored.data_digest,
        observation_count=stored.observation_count,
        created_at=stored.created_at,
    )


def _snapshot_response(snapshot: DecisionSnapshot) -> DecisionSnapshotResponse:
    return DecisionSnapshotResponse(
        decision_id=snapshot.decision_id,
        state=snapshot.state,
        version=snapshot.version,
        owner=snapshot.owner,
        content=snapshot.content,
        transitions=snapshot.transitions,
        approvals=snapshot.approvals,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
    )
