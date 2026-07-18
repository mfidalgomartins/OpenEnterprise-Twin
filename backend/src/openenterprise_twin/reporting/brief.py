"""Deterministic executive brief assembled from paired scenario evidence."""

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from time import perf_counter
from typing import Annotated, Literal

from pydantic import Field

from openenterprise_twin.domain.company import DomainModel, VersionString
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.reporting.narrative import (
    MechanismNarrative,
    build_mechanism_narratives,
    format_metric_label,
    format_metric_value,
)
from openenterprise_twin.scenarios.comparison import (
    MetricComparison,
    ScenarioComparison,
    validate_scenario_comparison,
)
from openenterprise_twin.simulation.experiment import (
    METRIC_NAMES,
    MetricName,
    PluginVersion,
)

DecisionStatus = Literal["adopt", "conditional", "do_not_adopt"]
_MAX_ADOPT_BREACH_PROBABILITY = 0.0
BRIEF_SCHEMA_VERSION = "0.2.1"


class Recommendation(DomainModel):
    status: DecisionStatus
    headline: str
    rationale: tuple[str, ...]
    evidence_metric_ids: tuple[MetricName, ...]


class OutcomeDelta(DomainModel):
    metric_name: MetricName
    baseline_mean: float
    candidate_mean: float
    mean_difference: float
    probability_of_improvement: Annotated[float, Field(ge=0.0, le=1.0)]
    is_material: bool


class DecisionConstraint(DomainModel):
    metric_name: MetricName
    severity: Literal["watch", "breach"]
    detail: str


class DownsideTrigger(DomainModel):
    metric_name: MetricName
    breach_probability: Annotated[float, Field(ge=0.0, le=1.0)]
    detail: str


class DecisionGovernance(DomainModel):
    decision_owner: str
    decision_record_action: str
    review_date: date


class ExecutionAction(DomainModel):
    action_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    title: str
    owner: str
    due_date: date
    evidence_metric_ids: tuple[MetricName, ...]
    completion_evidence: str


class BriefProvenance(DomainModel):
    comparison_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    baseline_experiment_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    candidate_experiment_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    company_model_version: VersionString
    company_model_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    scenario_schema_version: VersionString
    engine_version: VersionString
    shock_tape_version: VersionString
    master_seed: Annotated[int, Field(ge=0)]
    replication_count: Annotated[int, Field(gt=0)]
    baseline_plugin_versions: tuple[PluginVersion, ...]
    candidate_plugin_versions: tuple[PluginVersion, ...]
    baseline_resolved_assumptions_hash: Annotated[
        str, Field(pattern=r"^[a-f0-9]{64}$")
    ]
    candidate_resolved_assumptions_hash: Annotated[
        str, Field(pattern=r"^[a-f0-9]{64}$")
    ]
    baseline_experiment_created_at: datetime
    candidate_experiment_created_at: datetime
    baseline_experiment_duration_seconds: Annotated[
        float, Field(ge=0.0, allow_inf_nan=False)
    ]
    candidate_experiment_duration_seconds: Annotated[
        float, Field(ge=0.0, allow_inf_nan=False)
    ]
    comparison_created_at: datetime
    comparison_duration_seconds: Annotated[
        float, Field(ge=0.0, allow_inf_nan=False)
    ]
    created_at: datetime
    duration_seconds: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class ExecutiveBrief(DomainModel):
    """Evidence-linked decision object consumed by the API, UI and exporters."""

    brief_schema_version: VersionString
    decision_status: DecisionStatus
    recommendation: Recommendation
    outcome_deltas: tuple[OutcomeDelta, ...]
    mechanisms: tuple[MechanismNarrative, ...]
    constraints: tuple[DecisionConstraint, ...]
    downside_triggers: tuple[DownsideTrigger, ...]
    governance: DecisionGovernance
    actions: tuple[ExecutionAction, ...]
    assumptions: tuple[str, ...]
    provenance: BriefProvenance
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def build_executive_brief(
    comparison: ScenarioComparison,
    *,
    created_at: datetime | None = None,
    duration_seconds: float | None = None,
) -> ExecutiveBrief:
    """Build a recommendation using only computed comparison states."""

    report_created_at = created_at or datetime.now(UTC)
    started_at = perf_counter()
    validate_scenario_comparison(comparison)
    metrics = comparison.metrics
    status = _decision_status(metrics)
    recommendation = _recommendation(comparison, status)
    outcomes = tuple(
        OutcomeDelta(
            metric_name=metric_name,
            baseline_mean=metrics[metric_name].baseline_mean,
            candidate_mean=metrics[metric_name].candidate_mean,
            mean_difference=metrics[metric_name].mean_difference,
            probability_of_improvement=(
                metrics[metric_name].probability_of_improvement
            ),
            is_material=metrics[metric_name].is_material,
        )
        for metric_name in METRIC_NAMES
    )
    constraints = _constraints(metrics)
    governance = _governance(comparison, status)
    report_duration_seconds = (
        duration_seconds
        if duration_seconds is not None
        else perf_counter() - started_at
    )
    brief = ExecutiveBrief(
        brief_schema_version=BRIEF_SCHEMA_VERSION,
        decision_status=status,
        recommendation=recommendation,
        outcome_deltas=outcomes,
        mechanisms=build_mechanism_narratives(
            comparison.candidate_policy_levers
        ),
        constraints=constraints,
        downside_triggers=_downside_triggers(constraints, metrics),
        governance=governance,
        actions=_execution_actions(
            comparison,
            status=status,
            constraints=constraints,
            recommendation=recommendation,
            governance=governance,
        ),
        assumptions=_assumptions(comparison),
        provenance=_provenance(
            comparison,
            created_at=report_created_at,
            duration_seconds=report_duration_seconds,
        ),
        digest="0" * 64,
    )
    brief = brief.model_copy(update={"digest": brief_content_digest(brief)})
    validate_executive_brief(brief, comparison)
    return brief


def validate_executive_brief(
    brief: ExecutiveBrief, comparison: ScenarioComparison
) -> None:
    """Reject unsupported evidence and stale or internally inconsistent briefs."""

    if brief_content_digest(brief) != brief.digest:
        raise InvariantViolation(
            "brief_digest", "brief content does not match its digest"
        )
    if brief.brief_schema_version != BRIEF_SCHEMA_VERSION:
        raise InvariantViolation(
            "brief_schema_version", "brief schema version is unsupported"
        )
    if brief.provenance.created_at.tzinfo is None:
        raise InvariantViolation(
            "brief_provenance", "brief creation time must be timezone-aware"
        )
    if brief.provenance != _provenance(
        comparison,
        created_at=brief.provenance.created_at,
        duration_seconds=brief.provenance.duration_seconds,
    ):
        raise InvariantViolation(
            "brief_provenance", "brief does not reference the supplied comparison"
        )
    metric_names = set(comparison.metrics)
    if not brief.recommendation.evidence_metric_ids or not set(
        brief.recommendation.evidence_metric_ids
    ) <= metric_names:
        raise InvariantViolation(
            "brief_evidence", "recommendation cites unsupported metric evidence"
        )
    if brief.decision_status != brief.recommendation.status:
        raise InvariantViolation(
            "brief_decision_status", "recommendation status is inconsistent"
        )
    expected_status = _decision_status(comparison.metrics)
    if brief.decision_status != expected_status:
        raise InvariantViolation(
            "brief_decision_status", "decision status is not supported by metrics"
        )
    expected_recommendation = _recommendation(comparison, expected_status)
    if brief.recommendation != expected_recommendation:
        raise InvariantViolation(
            "brief_evidence",
            "recommendation text or evidence is not supported by metrics",
        )
    expected_mechanisms = build_mechanism_narratives(
        comparison.candidate_policy_levers
    )
    if brief.mechanisms != expected_mechanisms:
        raise InvariantViolation(
            "brief_mechanisms", "brief mechanisms do not match scenario policy levers"
        )
    if tuple(outcome.metric_name for outcome in brief.outcome_deltas) != METRIC_NAMES:
        raise InvariantViolation(
            "brief_outcome_reconciliation",
            "brief outcomes do not match the required metric dimension",
        )
    for outcome in brief.outcome_deltas:
        metric = comparison.metrics[outcome.metric_name]
        if (
            outcome.baseline_mean != metric.baseline_mean
            or outcome.candidate_mean != metric.candidate_mean
            or outcome.mean_difference != metric.mean_difference
            or outcome.probability_of_improvement
            != metric.probability_of_improvement
            or outcome.is_material != metric.is_material
        ):
            raise InvariantViolation(
                "brief_outcome_reconciliation",
                f"outcome does not reconcile for '{outcome.metric_name}'",
            )
    expected_constraints = _constraints(comparison.metrics)
    if brief.constraints != expected_constraints:
        raise InvariantViolation(
            "brief_constraints", "brief constraints are not supported by metrics"
        )
    if brief.downside_triggers != _downside_triggers(
        expected_constraints, comparison.metrics
    ):
        raise InvariantViolation(
            "brief_downside_triggers",
            "brief downside triggers are not supported by metrics",
        )
    expected_governance = _governance(comparison, expected_status)
    if brief.governance != expected_governance:
        raise InvariantViolation(
            "brief_governance", "brief governance is stale or unsupported"
        )
    if brief.actions != _execution_actions(
        comparison,
        status=expected_status,
        constraints=expected_constraints,
        recommendation=expected_recommendation,
        governance=expected_governance,
    ):
        raise InvariantViolation(
            "brief_actions", "brief actions are stale or unsupported"
        )
    if brief.assumptions != _assumptions(comparison):
        raise InvariantViolation(
            "brief_assumptions", "brief assumptions are stale or unsupported"
        )


def brief_content_digest(brief: ExecutiveBrief) -> str:
    canonical = json.dumps(
        brief.model_dump(mode="json", exclude={"digest"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _decision_status(
    metrics: Mapping[str, MetricComparison]
) -> DecisionStatus:
    ebitda = metrics["ebitda"]
    if (
        _is_material_downside(ebitda)
        and ebitda.probability_of_improvement < 0.40
    ):
        return "do_not_adopt"
    if _constraints(metrics):
        return "conditional"
    if (
        _is_material_improvement(ebitda)
        and _confidence_excludes_no_change(ebitda)
        and ebitda.probability_of_improvement >= 0.60
    ):
        return "adopt"
    return "conditional"


def _recommendation(
    comparison: ScenarioComparison, status: DecisionStatus
) -> Recommendation:
    metrics = comparison.metrics
    evidence_ids = _evidence_metric_ids(metrics, status)
    return Recommendation(
        status=status,
        headline=_headline(status, comparison.candidate_scenario_name),
        rationale=tuple(
            _metric_evidence_clause(metric_name, metrics[metric_name])
            for metric_name in evidence_ids
        ),
        evidence_metric_ids=evidence_ids,
    )


def _evidence_metric_ids(
    metrics: Mapping[str, MetricComparison], status: DecisionStatus
) -> tuple[MetricName, ...]:
    selected: list[MetricName] = ["ebitda"]
    if metrics["free_cash_flow"].is_material or status != "adopt":
        selected.append("free_cash_flow")
    selected.extend(constraint.metric_name for constraint in _constraints(metrics))
    return tuple(dict.fromkeys(selected))


def _constraints(
    metrics: Mapping[str, MetricComparison]
) -> tuple[DecisionConstraint, ...]:
    constraints: list[DecisionConstraint] = []
    for metric_name in METRIC_NAMES:
        metric = metrics[metric_name]
        breach_worsened = (
            metric.candidate_breach_probability
            > metric.baseline_breach_probability
        )
        absolute_breach_risk = (
            metric.candidate_breach_probability
            > _MAX_ADOPT_BREACH_PROBABILITY
        )
        material_downside = _is_material_downside(metric)
        if not absolute_breach_risk and not material_downside:
            continue
        details: list[str] = []
        if breach_worsened:
            details.append(
                f"breach probability rises from "
                f"{metric.baseline_breach_probability:.1%} to "
                f"{metric.candidate_breach_probability:.1%}"
            )
        elif absolute_breach_risk:
            details.append(
                "candidate guardrail breach probability remains at "
                f"{metric.candidate_breach_probability:.1%}"
            )
        if material_downside:
            details.append(
                "candidate mean moves adversely by "
                f"{format_metric_value(metric_name, metric.mean_difference)}"
            )
        constraints.append(
            DecisionConstraint(
                metric_name=metric_name,
                severity=(
                    "breach" if metric.candidate_breach_probability >= 0.50 else "watch"
                ),
                detail=f"{format_metric_label(metric_name)}: {'; '.join(details)}.",
            )
        )
    return tuple(constraints)


def _downside_triggers(
    constraints: tuple[DecisionConstraint, ...],
    metrics: Mapping[str, MetricComparison],
) -> tuple[DownsideTrigger, ...]:
    return tuple(
        DownsideTrigger(
            metric_name=constraint.metric_name,
            breach_probability=(
                metrics[constraint.metric_name].candidate_breach_probability
            ),
            detail=(
                f"Reassess if the {_sentence_metric_label(constraint.metric_name)} "
                "guardrail risk "
                "persists above the simulated level."
            ),
        )
        for constraint in constraints
    )


def _governance(
    comparison: ScenarioComparison, status: DecisionStatus
) -> DecisionGovernance:
    status_label = {
        "adopt": "adopt",
        "conditional": "adopt with guardrails",
        "do_not_adopt": "do not adopt",
    }[status]
    return DecisionGovernance(
        decision_owner="Managing Director",
        decision_record_action=(
            f"Record the '{status_label}' recommendation and comparison digest "
            "in the decision register before implementation."
        ),
        review_date=(
            comparison.candidate_experiment_created_at + timedelta(days=30)
        ).date(),
    )


def _execution_actions(
    comparison: ScenarioComparison,
    *,
    status: DecisionStatus,
    constraints: tuple[DecisionConstraint, ...],
    recommendation: Recommendation,
    governance: DecisionGovernance,
) -> tuple[ExecutionAction, ...]:
    reference_date = comparison.candidate_experiment_created_at.date()
    decision_label = {
        "adopt": "adoption",
        "conditional": "conditional adoption",
        "do_not_adopt": "non-adoption",
    }[status]
    actions = [
        ExecutionAction(
            action_id="record-decision",
            title=f"Record {decision_label} decision",
            owner=governance.decision_owner,
            due_date=reference_date + timedelta(days=7),
            evidence_metric_ids=recommendation.evidence_metric_ids,
            completion_evidence=(
                "Decision-register entry containing the recommendation, "
                "comparison digest and named guardrails."
            ),
        )
    ]
    for constraint in constraints:
        metric_label = constraint.metric_name.replace("_", " ")
        actions.append(
            ExecutionAction(
                action_id=f"review-{constraint.metric_name.replace('_', '-')}",
                title=f"Review {metric_label} guardrail",
                owner=_metric_owner(constraint.metric_name),
                due_date=governance.review_date,
                evidence_metric_ids=(constraint.metric_name,),
                completion_evidence=(
                    f"Actual {metric_label} result compared with the simulated "
                    "guardrail risk and documented in the decision register."
                ),
            )
        )
    if not constraints:
        actions.append(
            ExecutionAction(
                action_id="review-value-realisation",
                title="Review value realisation",
                owner="Finance Director",
                due_date=governance.review_date,
                evidence_metric_ids=("ebitda", "free_cash_flow"),
                completion_evidence=(
                    "Actual EBITDA and free cash flow reconciled to the paired "
                    "experiment means."
                ),
            )
        )
    return tuple(actions)


def _metric_owner(metric_name: MetricName) -> str:
    if metric_name in {
        "ebitda",
        "free_cash_flow",
        "closing_cash",
        "peak_revolver",
        "rescue_funding",
    }:
        return "Finance Director"
    if metric_name == "revenue":
        return "Commercial Director"
    return "Operations Director"


def _sentence_metric_label(metric_name: MetricName) -> str:
    label = format_metric_label(metric_name)
    return label if label.isupper() else f"{label[0].lower()}{label[1:]}"


def _directional_difference(metric: MetricComparison) -> float:
    return (
        metric.mean_difference
        if metric.direction == "higher"
        else -metric.mean_difference
    )


def _is_material_downside(metric: MetricComparison) -> bool:
    return metric.is_material and _directional_difference(metric) < 0.0


def _is_material_improvement(metric: MetricComparison) -> bool:
    return metric.is_material and _directional_difference(metric) > 0.0


def _confidence_excludes_no_change(metric: MetricComparison) -> bool:
    if metric.ci95_lower is None or metric.ci95_upper is None:
        return False
    if metric.direction == "higher":
        return metric.ci95_lower > 0.0
    return metric.ci95_upper < 0.0


def _assumptions(comparison: ScenarioComparison) -> tuple[str, ...]:
    return (
        (
            f"{comparison.replication_count} paired replications use common "
            "random numbers."
        ),
        "Confidence intervals use the paired normal approximation.",
        "Narrative clauses are selected deterministically from computed states.",
    )


def _provenance(
    comparison: ScenarioComparison,
    *,
    created_at: datetime,
    duration_seconds: float,
) -> BriefProvenance:
    return BriefProvenance(
        comparison_digest=comparison.digest,
        baseline_experiment_digest=comparison.baseline_experiment_digest,
        candidate_experiment_digest=comparison.candidate_experiment_digest,
        company_model_version=comparison.company_model_version,
        company_model_hash=comparison.company_model_hash,
        scenario_schema_version=comparison.scenario_schema_version,
        engine_version=comparison.engine_version,
        shock_tape_version=comparison.shock_tape_version,
        master_seed=comparison.master_seed,
        replication_count=comparison.replication_count,
        baseline_plugin_versions=comparison.baseline_plugin_versions,
        candidate_plugin_versions=comparison.candidate_plugin_versions,
        baseline_resolved_assumptions_hash=(
            comparison.baseline_resolved_assumptions_hash
        ),
        candidate_resolved_assumptions_hash=(
            comparison.candidate_resolved_assumptions_hash
        ),
        baseline_experiment_created_at=(
            comparison.baseline_experiment_created_at
        ),
        candidate_experiment_created_at=(
            comparison.candidate_experiment_created_at
        ),
        baseline_experiment_duration_seconds=(
            comparison.baseline_experiment_duration_seconds
        ),
        candidate_experiment_duration_seconds=(
            comparison.candidate_experiment_duration_seconds
        ),
        comparison_created_at=comparison.created_at,
        comparison_duration_seconds=comparison.duration_seconds,
        created_at=created_at,
        duration_seconds=duration_seconds,
    )


def _metric_evidence_clause(
    metric_name: MetricName, metric: MetricComparison
) -> str:
    return (
        f"{format_metric_label(metric_name)}: "
        f"{format_metric_value(metric_name, metric.baseline_mean)} to "
        f"{format_metric_value(metric_name, metric.candidate_mean)} "
        f"(paired delta {format_metric_value(metric_name, metric.mean_difference)}, "
        f"{metric.probability_of_improvement:.1%} probability of improvement)."
    )


def _headline(status: DecisionStatus, candidate_name: str) -> str:
    if status == "adopt":
        return f"Adopt {candidate_name}"
    if status == "conditional":
        return f"Adopt {candidate_name} with guardrails"
    return f"Do not adopt {candidate_name}"
