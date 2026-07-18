"""Infrastructure-neutral ports consumed by application services."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExperimentDecisionRecord:
    id: int
    status: str
    baseline_experiment_id: int | None
    artifact_digest: str | None
    comparison_payload: Mapping[str, object] | None
    brief_payload: Mapping[str, object] | None


class ArtifactReader(Protocol):
    def get_json(self, digest: str) -> object: ...


class DecisionEvidenceRepository(Protocol):
    def get(self, experiment_id: int) -> ExperimentDecisionRecord | None: ...

    def store_comparison(
        self,
        experiment_id: int,
        payload: Mapping[str, object],
    ) -> None: ...

    def store_brief(
        self,
        experiment_id: int,
        payload: Mapping[str, object],
    ) -> None: ...
