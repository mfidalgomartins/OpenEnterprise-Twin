"""Operational readiness and protected build metadata endpoints."""

import os
from typing import NoReturn

from fastapi import APIRouter, Security
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from openenterprise_twin import __version__
from openenterprise_twin.api.dependencies import (
    ServicesDependency,
    SettingsDependency,
    require_principal,
)
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.api.schemas import (
    ReadinessChecks,
    ReadinessStatus,
    SystemInfo,
)

public_system_router = APIRouter()
system_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Security(require_principal)],
)

_CAPABILITIES = (
    "adaptive_policies",
    "calibration",
    "decision_ledger",
    "monitoring",
    "optimization",
    "paired_simulation",
    "secure_csv",
)


@public_system_router.get("/ready", response_model=ReadinessStatus)
def get_readiness(
    services: ServicesDependency,
    settings: SettingsDependency,
) -> ReadinessStatus:
    _check_artifact_directory(settings.artifact_directory)
    _check_database(services)
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


def _check_artifact_directory(artifact_directory: os.PathLike[str]) -> None:
    try:
        is_usable = (
            os.path.isdir(artifact_directory)
            and os.access(artifact_directory, os.R_OK | os.W_OK)
        )
    except OSError:
        is_usable = False
    if not is_usable:
        _raise_not_ready()


def _check_database(services: ServicesDependency) -> None:
    try:
        with services.session_factory() as session:
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
