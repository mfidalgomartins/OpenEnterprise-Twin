"""Application services exposed to FastAPI request dependencies."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.application.decision_loop import (
    CalibrationStudioService,
    MonitoringService,
    OptimizationLabService,
)
from openenterprise_twin.application.experiments import ExperimentRunner
from openenterprise_twin.application.identity import (
    AuthenticationError,
    IdentityProvider,
    Principal,
)
from openenterprise_twin.application.ledger import DecisionLedgerService
from openenterprise_twin.application.ports import (
    ArtifactReader,
    DecisionEvidenceRepository,
)
from openenterprise_twin.infrastructure.settings import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


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


def get_settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise RuntimeError("application settings are not initialized")
    return settings


def require_principal(
    request: Request,
    supplied_api_key: Annotated[str | None, Security(api_key_header)],
    supplied_bearer: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ],
) -> Principal:
    """Authenticate the configured deployment mode without exposing credentials."""

    provider = request.app.state.identity_provider
    if not isinstance(provider, IdentityProvider):
        raise RuntimeError("identity provider is not initialized")
    try:
        principal = provider.authenticate(
            api_key=supplied_api_key,
            bearer_token=(
                supplied_bearer.credentials
                if supplied_bearer is not None
                and supplied_bearer.scheme.lower() == "bearer"
                else None
            ),
        )
    except AuthenticationError as error:
        raise ApiProblemError(
            status=401,
            code=error.code,
            title="Authentication required",
            detail=error.detail,
        ) from error
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


SettingsDependency = Annotated[Settings, Depends(get_settings)]
ServicesDependency = Annotated[AppServices, Depends(get_services)]
