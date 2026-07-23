"""Application services exposed to FastAPI request dependencies."""

from collections.abc import Iterator
from dataclasses import dataclass
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.application.decision_loop import (
    CalibrationStudioService,
    MonitoringService,
    OptimizationLabService,
)
from openenterprise_twin.application.experiments import ExperimentRunner
from openenterprise_twin.application.ledger import DecisionLedgerService
from openenterprise_twin.application.ports import (
    ArtifactReader,
    DecisionEvidenceRepository,
)
from openenterprise_twin.infrastructure.settings import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True, slots=True)
class AppServices:
    session_factory: sessionmaker[Session]
    artifact_store: ArtifactReader
    decision_repository: DecisionEvidenceRepository
    experiment_runner: ExperimentRunner
    calibration_studio: CalibrationStudioService
    optimization_lab: OptimizationLabService
    monitoring: MonitoringService
    decision_ledger: DecisionLedgerService
    max_experiment_periods: int
    max_adaptive_periods: int


@dataclass(frozen=True, slots=True)
class Principal:
    """Minimal authenticated service identity for the single-tenant release."""

    subject: str
    authentication_method: str


def get_settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("application settings are not initialized")
    return settings


def require_principal(
    request: Request,
    supplied_api_key: Annotated[str | None, Security(api_key_header)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    """Require the configured deployment key without exposing it to the browser."""

    configured_api_key = settings.api_key
    if configured_api_key is None:
        principal = Principal(
            subject="local-operator", authentication_method="local"
        )
        request.state.principal = principal
        return principal
    expected = configured_api_key.get_secret_value()
    if supplied_api_key is None or not compare_digest(supplied_api_key, expected):
        raise ApiProblemError(
            status=401,
            code="authentication_required",
            title="Authentication required",
            detail="Supply a valid X-API-Key header.",
        )
    principal = Principal(
        subject="enterprise-operator", authentication_method="api_key"
    )
    request.state.principal = principal
    return principal


def get_services(request: Request) -> AppServices:
    services = request.app.state.services
    if not isinstance(services, AppServices):
        raise RuntimeError("application services are not initialized")
    return services


def get_session(
    services: Annotated[AppServices, Depends(get_services)],
) -> Iterator[Session]:
    with services.session_factory() as session:
        yield session
