"""FastAPI application factory with explicit lifecycle ownership."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import make_url

from openenterprise_twin.api.dependencies import AppServices
from openenterprise_twin.api.errors import install_error_handlers
from openenterprise_twin.api.routes import router
from openenterprise_twin.application.experiments import BoundedExperimentRunner
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.models import Base
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
            yield
        finally:
            runner.shutdown()
            engine.dispose()

    app = FastAPI(
        title="OpenEnterprise Twin API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.services = AppServices(
        session_factory=session_factory,
        artifact_store=artifact_store,
        experiment_runner=runner,
    )
    install_error_handlers(app)
    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
