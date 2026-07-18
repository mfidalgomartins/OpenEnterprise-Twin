"""FastAPI application factory with explicit lifecycle ownership."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import make_url
from starlette.middleware.cors import CORSMiddleware

from openenterprise_twin.api.dependencies import AppServices
from openenterprise_twin.api.errors import install_error_handlers
from openenterprise_twin.api.routes import router
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.repositories import (
    SqlAlchemyDecisionEvidenceRepository,
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

    app = FastAPI(
        title="OpenEnterprise Twin API",
        version="0.1.0",
        lifespan=lifespan,
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
            allow_headers=["Accept", "Content-Type", "Idempotency-Key"],
            expose_headers=["Location", "X-Trace-ID"],
        )
    app.state.services = AppServices(
        session_factory=session_factory,
        artifact_store=artifact_store,
        decision_repository=SqlAlchemyDecisionEvidenceRepository(
            session_factory
        ),
        experiment_runner=runner,
    )
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
