"""Application services exposed to FastAPI request dependencies."""

from collections.abc import Callable, Iterator
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
from openenterprise_twin.application.identity import (
    AuthenticationError,
    IdentityProvider,
    Principal,
    Role,
)
from openenterprise_twin.application.ledger import DecisionLedgerService
from openenterprise_twin.application.ports import (
    ArtifactReader,
    DecisionEvidenceRepository,
)
from openenterprise_twin.infrastructure.repositories import (
    SqlAlchemyDecisionEvidenceRepository,
    SqlCalibrationRepository,
    SqlDatasetRepository,
    SqlDecisionLedgerRepository,
    SqlMonitoringRepository,
    SqlOptimizationRepository,
)
from openenterprise_twin.infrastructure.settings import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AppInfrastructure:
    session_factory: sessionmaker[Session]
    artifact_store: ArtifactReader
    max_experiment_periods: int
    max_adaptive_periods: int


@dataclass(frozen=True, slots=True)
class AppServices:
    tenant_id: str
    session_factory: sessionmaker[Session]
    artifact_store: ArtifactReader
    decision_repository: DecisionEvidenceRepository
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


PrincipalDependency = Annotated[Principal, Security(require_principal)]


def authorize_principal(principal: Principal, *required_roles: Role) -> Principal:
    """Enforce one explicit role set for an authenticated principal."""

    if not required_roles or not principal.has_any_role(*required_roles):
        raise ApiProblemError(
            status=403,
            code="authorization_denied",
            title="Operation is not permitted",
            detail="Your current role does not permit this operation.",
        )
    return principal


def require_any_role(
    *required_roles: Role,
) -> Callable[[Principal], Principal]:
    """Build a FastAPI dependency for an explicit any-role policy."""

    if not required_roles:
        raise ValueError("at least one required role is needed")

    def dependency(principal: PrincipalDependency) -> Principal:
        return authorize_principal(principal, *required_roles)

    dependency.__name__ = "require_" + "_or_".join(required_roles)
    return dependency


reader_guard = require_any_role("viewer", "analyst", "approver", "admin")
analyst_guard = require_any_role("analyst", "admin")
admin_guard = require_any_role("admin")


def get_infrastructure(request: Request) -> AppInfrastructure:
    infrastructure = request.app.state.services
    if not isinstance(infrastructure, AppInfrastructure):
        raise RuntimeError("application infrastructure is not initialized")
    return infrastructure


def get_services(
    principal: PrincipalDependency,
    infrastructure: Annotated[
        AppInfrastructure,
        Depends(get_infrastructure),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AppServices:
    dataset_repository = SqlDatasetRepository(
        infrastructure.session_factory,
        principal.tenant_id,
    )
    calibration_repository = SqlCalibrationRepository(
        infrastructure.session_factory,
        principal.tenant_id,
    )
    return AppServices(
        tenant_id=principal.tenant_id,
        session_factory=infrastructure.session_factory,
        artifact_store=infrastructure.artifact_store,
        decision_repository=SqlAlchemyDecisionEvidenceRepository(
            infrastructure.session_factory,
            principal.tenant_id,
        ),
        calibration_studio=CalibrationStudioService(
            datasets=dataset_repository,
            calibrations=calibration_repository,
            max_observations=settings.max_dataset_observations,
        ),
        optimization_lab=OptimizationLabService(
            optimizations=SqlOptimizationRepository(
                infrastructure.session_factory,
                principal.tenant_id,
            ),
            max_evaluations=settings.max_optimization_evaluations,
            max_periods=settings.max_optimization_periods,
        ),
        monitoring=MonitoringService(
            reports=SqlMonitoringRepository(
                infrastructure.session_factory,
                principal.tenant_id,
            )
        ),
        decision_ledger=DecisionLedgerService(
            SqlDecisionLedgerRepository(
                infrastructure.session_factory,
                principal.tenant_id,
            )
        ),
        max_experiment_periods=infrastructure.max_experiment_periods,
        max_adaptive_periods=infrastructure.max_adaptive_periods,
    )


def get_session(
    infrastructure: Annotated[
        AppInfrastructure,
        Depends(get_infrastructure),
    ],
) -> Iterator[Session]:
    with infrastructure.session_factory() as session:
        yield session


SettingsDependency = Annotated[Settings, Depends(get_settings)]
ServicesDependency = Annotated[AppServices, Depends(get_services)]
InfrastructureDependency = Annotated[
    AppInfrastructure,
    Depends(get_infrastructure),
]
