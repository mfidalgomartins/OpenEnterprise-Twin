"""Operational readiness and protected build metadata endpoints."""

import os
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, Request, Security
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from openenterprise_twin import __version__
from openenterprise_twin.api.dependencies import (
    AppInfrastructure,
    InfrastructureDependency,
    SettingsDependency,
    admin_guard,
)
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.api.observability import OperationalMetrics
from openenterprise_twin.api.schemas import (
    OperationalMetricsSnapshot,
    ReadinessChecks,
    ReadinessStatus,
    SystemInfo,
)
from openenterprise_twin.infrastructure.runner import job_queue_snapshot

public_system_router = APIRouter()
system_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Security(admin_guard)],
)

_CAPABILITIES = (
    "adaptive_policies",
    "calibration",
    "decision_ledger",
    "durable_jobs",
    "identity_rbac",
    "monitoring",
    "optimization",
    "paired_simulation",
    "secure_csv",
    "tenant_isolation",
)


@public_system_router.get("/ready", response_model=ReadinessStatus)
def get_readiness(
    infrastructure: InfrastructureDependency,
    settings: SettingsDependency,
) -> ReadinessStatus:
    _check_artifact_directory(settings.artifact_directory)
    _check_database(infrastructure)
    return ReadinessStatus(
        status="ready",
        checks=ReadinessChecks(artifacts="ready", database="ready"),
    )


@system_router.get("/system/info", response_model=SystemInfo)
def get_system_info(settings: SettingsDependency) -> SystemInfo:
    return SystemInfo(
        name="OpenEnterprise Twin",
        version=__version__,
        environment=settings.deployment_environment,
        build_commit=settings.build_commit,
        capabilities=_CAPABILITIES,
    )


@system_router.get(
    "/system/metrics",
    response_model=OperationalMetricsSnapshot,
)
def get_operational_metrics(
    request: Request,
    infrastructure: InfrastructureDependency,
) -> OperationalMetricsSnapshot:
    metrics = request.app.state.metrics
    if not isinstance(metrics, OperationalMetrics):
        raise RuntimeError("operational metrics are not initialized")
    queue = job_queue_snapshot(infrastructure.session_factory)
    return OperationalMetricsSnapshot.model_validate(
        {
            **metrics.snapshot(),
            "job_queue": {
                "queued": queue.queued,
                "running": queue.running,
                "stale_leases": queue.stale_leases,
                "oldest_queued_age_seconds": (
                    queue.oldest_queued_age_seconds
                ),
            },
        }
    )


def _check_artifact_directory(artifact_directory: os.PathLike[str]) -> None:
    probe_content = b"1"
    probe_path = Path(artifact_directory) / f".readiness-{uuid4().hex}.tmp"
    probe_created = False
    probe_failed = False
    try:
        with probe_path.open("x+b") as probe:
            probe_created = True
            probe.write(probe_content)
            probe.flush()
            os.fsync(probe.fileno())
            probe.seek(0)
            if probe.read() != probe_content:
                probe_failed = True
    except OSError:
        probe_failed = True
    finally:
        if probe_created:
            try:
                probe_path.unlink()
            except OSError:
                probe_failed = True
    if probe_failed:
        _raise_not_ready()


def _check_database(infrastructure: AppInfrastructure) -> None:
    try:
        with infrastructure.session_factory() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        _raise_not_ready()


def _raise_not_ready() -> NoReturn:
    raise ApiProblemError(
        status=503,
        code="service_not_ready",
        title="Service is not ready",
        detail="One or more required dependencies are unavailable.",
    )
