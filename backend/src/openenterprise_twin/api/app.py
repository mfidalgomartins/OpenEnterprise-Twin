"""FastAPI application factory with explicit lifecycle ownership."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.engine import make_url
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from openenterprise_twin import __version__
from openenterprise_twin.api.decision_loop_routes import decision_loop_router
from openenterprise_twin.api.dependencies import AppInfrastructure
from openenterprise_twin.api.errors import install_error_handlers
from openenterprise_twin.api.jobs import jobs_router
from openenterprise_twin.api.middleware import (
    OperationalMetricsMiddleware,
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
)
from openenterprise_twin.api.observability import (
    OperationalMetrics,
    RegisteredRouteResolver,
)
from openenterprise_twin.api.routes import public_router, router
from openenterprise_twin.api.system import public_system_router, system_router
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.identity import build_identity_provider
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.runner import (
    DurableJobWorker,
    EmbeddedJobWorkerPool,
    backfill_active_experiment_jobs,
    build_analytical_job_handlers,
    build_worker_id,
)
from openenterprise_twin.infrastructure.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build an isolated application instance for production or tests."""

    resolved_settings = settings or Settings()
    engine = create_database_engine(resolved_settings)
    if make_url(resolved_settings.database_url).get_backend_name() == "sqlite":
        Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    artifact_store = FileArtifactStore(resolved_settings.artifact_directory)
    handlers = build_analytical_job_handlers(
        session_factory=session_factory,
        artifact_store=artifact_store,
        max_replication_workers=(
            resolved_settings.replication_workers_per_experiment
        ),
        max_dataset_observations=resolved_settings.max_dataset_observations,
        max_optimization_evaluations=(
            resolved_settings.max_optimization_evaluations
        ),
        max_optimization_periods=resolved_settings.max_optimization_periods,
        max_adaptive_periods=resolved_settings.max_adaptive_periods,
    )
    worker_pool = (
        EmbeddedJobWorkerPool(
            workers=tuple(
                DurableJobWorker(
                    session_factory=session_factory,
                    handlers=handlers,
                    worker_id=build_worker_id("api-worker"),
                    lease_duration=timedelta(
                        seconds=resolved_settings.job_lease_seconds
                    ),
                    heartbeat_interval=timedelta(
                        seconds=resolved_settings.job_heartbeat_seconds
                    ),
                    retry_delay=timedelta(
                        seconds=resolved_settings.job_retry_delay_seconds
                    ),
                )
                for _ in range(resolved_settings.job_workers)
            ),
            poll_interval=timedelta(
                seconds=resolved_settings.job_poll_interval_seconds
            ),
        )
        if resolved_settings.job_worker_mode == "embedded"
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            backfill_active_experiment_jobs(session_factory)
            if worker_pool is not None:
                worker_pool.start()
            yield
        finally:
            if worker_pool is not None:
                worker_pool.shutdown(
                    resolved_settings.job_shutdown_timeout_seconds
                )
            engine.dispose()

    expose_docs = resolved_settings.deployment_environment != "production"
    app = FastAPI(
        title="OpenEnterprise Twin API",
        version=__version__,
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
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-API-Key",
            ],
            expose_headers=["Location", "X-Trace-ID"],
        )
    app.state.services = AppInfrastructure(
        session_factory=session_factory,
        artifact_store=artifact_store,
        max_experiment_periods=resolved_settings.max_experiment_periods,
        max_adaptive_periods=resolved_settings.max_adaptive_periods,
    )
    app.state.settings = resolved_settings
    app.state.job_handlers = handlers
    app.state.job_worker_pool = worker_pool
    app.state.identity_provider = build_identity_provider(resolved_settings)
    install_error_handlers(app)
    app.include_router(public_router)
    app.include_router(public_system_router)
    app.include_router(router)
    app.include_router(system_router)
    app.include_router(jobs_router)
    app.include_router(decision_loop_router)
    route_resolver = RegisteredRouteResolver.from_routes(app.routes)
    metrics = OperationalMetrics(
        registered_route_templates=route_resolver.registered_route_templates
    )
    app.state.metrics = metrics
    app.add_middleware(
        OperationalMetricsMiddleware,
        metrics=metrics,
        route_resolver=route_resolver,
    )
    app.add_middleware(SecurityHeadersMiddleware)

    return app
