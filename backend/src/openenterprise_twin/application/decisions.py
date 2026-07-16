"""Application service for persisted comparisons and decision briefs."""

from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.models import ExperimentRecord
from openenterprise_twin.infrastructure.repositories import ExperimentRepository
from openenterprise_twin.reporting.brief import (
    ExecutiveBrief,
    build_executive_brief,
)
from openenterprise_twin.scenarios.comparison import (
    ScenarioComparison,
    compare_experiments,
)
from openenterprise_twin.simulation.experiment import ExperimentResult


class DecisionEvidenceError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def get_or_build_comparison(
    record: ExperimentRecord,
    *,
    repository: ExperimentRepository,
    artifact_store: FileArtifactStore,
) -> ScenarioComparison:
    if record.comparison_payload is not None:
        return ScenarioComparison.model_validate(record.comparison_payload)
    if record.status != "completed" or record.artifact_digest is None:
        raise DecisionEvidenceError(
            "experiment_not_completed",
            "The candidate experiment must be completed before comparison.",
        )
    if record.baseline_experiment_id is None:
        raise DecisionEvidenceError(
            "baseline_experiment_missing",
            "The candidate experiment has no compatible baseline experiment.",
        )
    baseline_record = repository.get(record.baseline_experiment_id)
    if (
        baseline_record is None
        or baseline_record.status != "completed"
        or baseline_record.artifact_digest is None
    ):
        raise DecisionEvidenceError(
            "baseline_experiment_missing",
            "The compatible baseline experiment is unavailable or incomplete.",
        )

    baseline = ExperimentResult.model_validate(
        artifact_store.get_json(baseline_record.artifact_digest)
    )
    candidate = ExperimentResult.model_validate(
        artifact_store.get_json(record.artifact_digest)
    )
    comparison = compare_experiments(baseline, candidate)
    repository.store_comparison(
        record,
        comparison.model_dump(mode="json"),
    )
    return comparison


def get_or_build_brief(
    record: ExperimentRecord,
    *,
    repository: ExperimentRepository,
    artifact_store: FileArtifactStore,
) -> ExecutiveBrief:
    if record.brief_payload is not None:
        return ExecutiveBrief.model_validate(record.brief_payload)
    comparison = get_or_build_comparison(
        record,
        repository=repository,
        artifact_store=artifact_store,
    )
    brief = build_executive_brief(comparison)
    repository.store_brief(record, brief.model_dump(mode="json"))
    return brief
