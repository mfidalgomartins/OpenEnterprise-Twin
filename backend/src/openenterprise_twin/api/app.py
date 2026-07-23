"""FastAPI application factory with explicit lifecycle ownership."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import make_url
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from openenterprise_twin.api.decision_loop_routes import decision_loop_router
from openenterprise_twin.api.dependencies import AppServices
from openenterprise_twin.api.errors import install_error_handlers
from openenterprise_twin.api.middleware import RequestBodyLimitMiddleware
from openenterprise_twin.api.routes import public_router, router
from openenterprise_twin.application.decision_loop import (
    CalibrationStudioService,
    MonitoringService,
    OptimizationLabService,
)
from openenterprise_twin.application.ledger import DecisionLedgerService
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.repositories import (
    SqlAlchemyDecisionEvidenceRepository,
    SqlCalibrationRepository,
    SqlDatasetRepository,
    SqlDecisionLedgerRepository,
    SqlMonitoringRepository,
    SqlOptimizationRepository,
)
from openenterprise_twin.infrastructure.runner import BoundedExperimentRunner
from openenterprise_twin.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an isolated application instance for production or tests."""

    resolved_settings = settings or Settings()
    engine = create_database_engine(resolved_settings)
    if make_url(resolved_settings.database_url).get_backend_name() == "sqlite":
        Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    artifact_store = FileArtifactStore(resolved_settings.artifact_directory)
    runner = BoundedExperimentRunner(
        session_factory=session_factory,
        artifact_store=artifact_store,
        max_workers=resolved_settings.experiment_workers,
        max_replication_workers=(
            resolved_settings.replication_workers_per_experiment
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            runner.recover_pending()
            yield
        finally:
            runner.shutdown(
                resolved_settings.experiment_shutdown_timeout_seconds
            )
            engine.dispose()

    expose_docs = resolved_settings.deployment_environment != "production"
    app = FastAPI(
        title="OpenEnterprise Twin API",
        version="0.4.0",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=resolved_settings.max_request_body_bytes,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(resolved_settings.trusted_hosts),
    )
    if resolved_settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                str(origin).rstrip("/")
                for origin in resolved_settings.cors_allowed_origins
            ],
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Content-Type",
                "Idempotency-Key",
                "X-API-Key",
            ],
            expose_headers=["Location", "X-Trace-ID"],
        )
    dataset_repository = SqlDatasetRepository(session_factory)
    calibration_repository = SqlCalibrationRepository(session_factory)
    app.state.services = AppServices(
        session_factory=session_factory,
        artifact_store=artifact_store,
        decision_repository=SqlAlchemyDecisionEvidenceRepository(
            session_factory
        ),
        experiment_runner=runner,
        calibration_studio=CalibrationStudioService(
            datasets=dataset_repository,
            calibrations=calibration_repository,
            max_observations=resolved_settings.max_dataset_observations,
        ),
        optimization_lab=OptimizationLabService(
            optimizations=SqlOptimizationRepository(session_factory),
            max_evaluations=resolved_settings.max_optimization_evaluations,
            max_periods=resolved_settings.max_optimization_periods,
        ),
        monitoring=MonitoringService(
            reports=SqlMonitoringRepository(session_factory)
        ),
        decision_ledger=DecisionLedgerService(
            SqlDecisionLedgerRepository(session_factory)
        ),
        max_experiment_periods=resolved_settings.max_experiment_periods,
        max_adaptive_periods=resolved_settings.max_adaptive_periods,
    )
    app.state.settings = resolved_settings
    install_error_handlers(app)
    app.include_router(public_router)
    app.include_router(router)
    app.include_router(decision_loop_router)

    return app
