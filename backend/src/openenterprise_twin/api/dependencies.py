"""Application services exposed to FastAPI request dependencies."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.application.experiments import ExperimentRunner
from openenterprise_twin.application.ports import (
    ArtifactReader,
    DecisionEvidenceRepository,
)


@dataclass(frozen=True, slots=True)
class AppServices:
    session_factory: sessionmaker[Session]
    artifact_store: ArtifactReader
    decision_repository: DecisionEvidenceRepository
    experiment_runner: ExperimentRunner


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
