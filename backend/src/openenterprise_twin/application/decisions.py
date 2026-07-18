"""Application service for persisted comparisons and decision briefs."""

from openenterprise_twin.application.ports import (
    ArtifactReader,
    DecisionEvidenceRepository,
    ExperimentDecisionRecord,
)
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.reporting.brief import (
    ExecutiveBrief,
    build_executive_brief,
)
from openenterprise_twin.scenarios.comparison import (
    ScenarioComparison,
    compare_experiments,
)
from openenterprise_twin.simulation.experiment import (
    ExperimentArtifact,
    ExperimentResult,
)


class DecisionEvidenceError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def get_or_build_comparison(
    experiment_id: int,
    *,
    repository: DecisionEvidenceRepository,
    artifact_store: ArtifactReader,
) -> ScenarioComparison:
    record = _required_record(repository, experiment_id)
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

    baseline = _load_experiment_result(
        artifact_store.get_json(baseline_record.artifact_digest)
    )
    candidate = _load_experiment_result(
        artifact_store.get_json(record.artifact_digest)
    )
    try:
        comparison = compare_experiments(baseline, candidate)
    except InvariantViolation as error:
        raise DecisionEvidenceError(
            "experiment_incompatible",
            "The baseline and candidate experiments are not comparable.",
        ) from error
    repository.store_comparison(record.id, comparison.model_dump(mode="json"))
    return comparison


def get_or_build_brief(
    experiment_id: int,
    *,
    repository: DecisionEvidenceRepository,
    artifact_store: ArtifactReader,
) -> ExecutiveBrief:
    record = _required_record(repository, experiment_id)
    if record.brief_payload is not None:
        return ExecutiveBrief.model_validate(record.brief_payload)
    comparison = get_or_build_comparison(
        experiment_id,
        repository=repository,
        artifact_store=artifact_store,
    )
    brief = build_executive_brief(comparison)
    repository.store_brief(record.id, brief.model_dump(mode="json"))
    return brief


def _load_experiment_result(payload: object) -> ExperimentResult:
    if isinstance(payload, dict) and "result" in payload:
        return ExperimentArtifact.model_validate(payload).result
    return ExperimentResult.model_validate(payload)


def _required_record(
    repository: DecisionEvidenceRepository,
    experiment_id: int,
) -> ExperimentDecisionRecord:
    record = repository.get(experiment_id)
    if record is None:
        raise DecisionEvidenceError(
            "experiment_not_found",
            f"Experiment '{experiment_id}' does not exist.",
        )
    return record
