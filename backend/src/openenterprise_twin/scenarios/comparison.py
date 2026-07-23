"""Deterministic paired comparison of compatible simulation experiments."""

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from statistics import fmean, stdev
from time import perf_counter
from types import MappingProxyType
from typing import Annotated, Literal, Self

import numpy as np
from pydantic import Field, model_validator

from openenterprise_twin.domain.company import (
    DecisionMetricRule,
    DisplayName,
    DomainModel,
    Identifier,
    VersionString,
)
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.simulation.experiment import (
    METRIC_NAMES,
    ExperimentResult,
    MetricGuardrail,
    MetricName,
    PluginVersion,
    ReplicationMetrics,
    summarize_replication_metric,
    validate_experiment_result,
)

ComparisonDirection = Literal["higher", "lower"]
JointProbabilityName = Literal[
    "ebitda_improves_without_otif_declining",
    "ebitda_and_closing_cash_improve",
]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
NonNegativeFloat = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

_STUDENT_T_95_CRITICAL_VALUES = (
    12.706204736,
    4.30265273,
    3.182446305284263,
    2.776445105,
    2.570581836,
    2.446911851,
    2.364624252,
    2.306004135,
    2.262157163,
    2.228138852,
    2.20098516,
    2.17881283,
    2.160368656,
    2.144786688,
    2.131449546,
    2.119905299,
    2.109815578,
    2.10092204,
    2.093024054,
    2.085963447,
    2.079613845,
    2.073873068,
    2.06865761,
    2.063898562,
    2.059538553,
    2.055529439,
    2.051830516,
    2.048407142,
    2.045229642,
    2.042272456,
)
_HIGHER_IS_BETTER: frozenset[MetricName] = frozenset(
    {"revenue", "ebitda", "free_cash_flow", "closing_cash", "otif"}
)
_JOINT_PROBABILITY_NAMES: tuple[JointProbabilityName, ...] = (
    "ebitda_improves_without_otif_declining",
    "ebitda_and_closing_cash_improve",
)


class MaterialityThreshold(DomainModel):
    """Absolute threshold and improvement semantics for one metric."""

    metric_name: MetricName
    threshold: NonNegativeFloat
    direction: ComparisonDirection | None = None


class ComparisonPolicy(DomainModel):
    """Typed metric-level policy; omitted metrics inherit defaults."""

    materiality_thresholds: tuple[MaterialityThreshold, ...] = ()

    @model_validator(mode="after")
    def validate_unique_metrics(self) -> Self:
        names = [item.metric_name for item in self.materiality_thresholds]
        if len(names) != len(set(names)):
            raise ValueError("comparison materiality thresholds must be unique")
        return self


DEFAULT_COMPARISON_POLICY = ComparisonPolicy(
    materiality_thresholds=tuple(
        MaterialityThreshold(
            metric_name=metric_name,
            threshold=(
                0.000001
                if metric_name in {"otif", "cancellation_rate", "capacity_utilization"}
                else 1.0
            ),
            direction=("higher" if metric_name in _HIGHER_IS_BETTER else "lower"),
        )
        for metric_name in METRIC_NAMES
    )
)


class PairedDifference(DomainModel):
    """Candidate-minus-baseline outcomes for one aligned replication."""

    replication_id: Annotated[int, Field(ge=0)]
    baseline_metric_entries: tuple[tuple[MetricName, FiniteFloat], ...]
    candidate_metric_entries: tuple[tuple[MetricName, FiniteFloat], ...]
    metric_entries: tuple[tuple[MetricName, FiniteFloat], ...]

    @property
    def baseline_values(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.baseline_metric_entries))

    @property
    def candidate_values(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.candidate_metric_entries))

    @property
    def values(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.metric_entries))


class MetricComparison(DomainModel):
    """Paired effect summary and source experiment statistics for one metric."""

    metric_name: MetricName
    direction: ComparisonDirection
    baseline_mean: FiniteFloat
    candidate_mean: FiniteFloat
    baseline_breach_probability: Probability
    candidate_breach_probability: Probability
    baseline_breach_probability_ci95_lower: Probability
    baseline_breach_probability_ci95_upper: Probability
    candidate_breach_probability_ci95_lower: Probability
    candidate_breach_probability_ci95_upper: Probability
    mean_difference: FiniteFloat
    ci95_lower: FiniteFloat | None
    ci95_upper: FiniteFloat | None
    p5_difference: FiniteFloat
    p50_difference: FiniteFloat
    p95_difference: FiniteFloat
    probability_of_improvement: Probability
    materiality_threshold: NonNegativeFloat
    is_material: bool


class ScenarioComparison(DomainModel):
    """Immutable, auditable comparison of two common-random-number experiments."""

    baseline_scenario_id: Identifier
    baseline_scenario_name: DisplayName
    candidate_scenario_id: Identifier
    candidate_scenario_name: DisplayName
    candidate_policy_levers: PolicyLevers
    baseline_experiment_digest: Digest
    candidate_experiment_digest: Digest
    company_model_version: VersionString
    company_model_hash: Digest
    scenario_schema_version: VersionString
    engine_version: VersionString
    shock_tape_version: VersionString
    baseline_plugin_versions: tuple[PluginVersion, ...]
    candidate_plugin_versions: tuple[PluginVersion, ...]
    baseline_resolved_assumptions_hash: Digest
    candidate_resolved_assumptions_hash: Digest
    baseline_experiment_created_at: datetime
    candidate_experiment_created_at: datetime
    baseline_experiment_duration_seconds: NonNegativeFloat
    candidate_experiment_duration_seconds: NonNegativeFloat
    created_at: datetime
    duration_seconds: NonNegativeFloat
    master_seed: Annotated[int, Field(ge=0)]
    replication_count: Annotated[int, Field(gt=0)]
    horizon_days: Annotated[int, Field(gt=0)]
    warmup_days: Annotated[int, Field(ge=0)]
    evaluation_days: Annotated[int, Field(gt=0)]
    runoff_days: Annotated[int, Field(ge=0)]
    baseline_guardrails: tuple[MetricGuardrail, ...]
    candidate_guardrails: tuple[MetricGuardrail, ...]
    policy: ComparisonPolicy
    paired_differences: tuple[PairedDifference, ...]
    metric_results: tuple[MetricComparison, ...]
    joint_probability_entries: tuple[tuple[JointProbabilityName, Probability], ...]
    digest: Digest

    @property
    def metrics(self) -> Mapping[str, MetricComparison]:
        return MappingProxyType(
            {item.metric_name: item for item in self.metric_results}
        )

    @property
    def joint_probabilities(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.joint_probability_entries))


def compare_experiments(
    baseline: ExperimentResult,
    candidate: ExperimentResult,
    policy: ComparisonPolicy | None = None,
) -> ScenarioComparison:
    """Compare aligned outcomes using candidate-minus-baseline differences."""

    created_at = datetime.now(UTC)
    started_at = perf_counter()
    _validate_experiment_compatibility(baseline, candidate)
    validate_experiment_result(baseline)
    validate_experiment_result(candidate)

    resolved_policy = _resolve_policy(policy, baseline.decision_metric_rules)
    thresholds = {
        item.metric_name: item.threshold
        for item in resolved_policy.materiality_thresholds
    }
    directions = {
        item.metric_name: item.direction
        for item in resolved_policy.materiality_thresholds
    }
    paired_differences = tuple(
        PairedDifference(
            replication_id=baseline_replication.replication_id,
            baseline_metric_entries=baseline_replication.metric_entries,
            candidate_metric_entries=candidate_replication.metric_entries,
            metric_entries=tuple(
                (
                    metric_name,
                    candidate_replication.metric_values[metric_name]
                    - baseline_replication.metric_values[metric_name],
                )
                for metric_name in METRIC_NAMES
            ),
        )
        for baseline_replication, candidate_replication in zip(
            baseline.replications, candidate.replications, strict=True
        )
    )
    metric_results = tuple(
        _summarize_metric_comparison(
            metric_name=metric_name,
            differences=tuple(
                paired.values[metric_name] for paired in paired_differences
            ),
            baseline_mean=baseline.metrics[metric_name].mean,
            candidate_mean=candidate.metrics[metric_name].mean,
            baseline_breach_probability=(
                baseline.metrics[metric_name].breach_probability
            ),
            candidate_breach_probability=(
                candidate.metrics[metric_name].breach_probability
            ),
            baseline_breach_probability_ci95_lower=(
                baseline.metrics[metric_name].breach_probability_ci95_lower
            ),
            baseline_breach_probability_ci95_upper=(
                baseline.metrics[metric_name].breach_probability_ci95_upper
            ),
            candidate_breach_probability_ci95_lower=(
                candidate.metrics[metric_name].breach_probability_ci95_lower
            ),
            candidate_breach_probability_ci95_upper=(
                candidate.metrics[metric_name].breach_probability_ci95_upper
            ),
            materiality_threshold=thresholds[metric_name],
            direction=_required_direction(directions[metric_name]),
        )
        for metric_name in METRIC_NAMES
    )
    result = ScenarioComparison(
        baseline_scenario_id=baseline.scenario_id,
        baseline_scenario_name=baseline.scenario_name,
        candidate_scenario_id=candidate.scenario_id,
        candidate_scenario_name=candidate.scenario_name,
        candidate_policy_levers=candidate.policy_levers,
        baseline_experiment_digest=baseline.digest,
        candidate_experiment_digest=candidate.digest,
        company_model_version=baseline.company_model_version,
        company_model_hash=baseline.company_model_hash,
        scenario_schema_version=baseline.scenario_schema_version,
        engine_version=baseline.engine_version,
        shock_tape_version=baseline.shock_tape_version,
        baseline_plugin_versions=baseline.plugin_versions,
        candidate_plugin_versions=candidate.plugin_versions,
        baseline_resolved_assumptions_hash=baseline.resolved_assumptions_hash,
        candidate_resolved_assumptions_hash=candidate.resolved_assumptions_hash,
        baseline_experiment_created_at=baseline.created_at,
        candidate_experiment_created_at=candidate.created_at,
        baseline_experiment_duration_seconds=baseline.duration_seconds,
        candidate_experiment_duration_seconds=candidate.duration_seconds,
        created_at=created_at,
        duration_seconds=perf_counter() - started_at,
        master_seed=baseline.master_seed,
        replication_count=baseline.replication_count,
        horizon_days=baseline.horizon_days,
        warmup_days=baseline.warmup_days,
        evaluation_days=baseline.evaluation_days,
        runoff_days=baseline.runoff_days,
        baseline_guardrails=baseline.guardrails,
        candidate_guardrails=candidate.guardrails,
        policy=resolved_policy,
        paired_differences=paired_differences,
        metric_results=metric_results,
        joint_probability_entries=_joint_probabilities(
            paired_differences,
            directions=directions,
        ),
        digest="0" * 64,
    )
    result = result.model_copy(update={"digest": comparison_content_digest(result)})
    validate_scenario_comparison(result)
    return result


def validate_scenario_comparison(comparison: ScenarioComparison) -> None:
    """Reconcile retained pairs and summaries, then verify canonical provenance."""

    if comparison_content_digest(comparison) != comparison.digest:
        raise InvariantViolation(
            "scenario_comparison_digest",
            "scenario comparison content does not match its provenance digest",
        )
    if len(comparison.paired_differences) != comparison.replication_count:
        raise InvariantViolation(
            "scenario_comparison_replication_count",
            "comparison replication count does not match retained pairs",
        )
    if any(
        timestamp.tzinfo is None
        for timestamp in (
            comparison.baseline_experiment_created_at,
            comparison.candidate_experiment_created_at,
            comparison.created_at,
        )
    ):
        raise InvariantViolation(
            "scenario_comparison_creation_time",
            "comparison provenance times must be timezone-aware",
        )
    if (
        comparison.warmup_days
        + comparison.evaluation_days
        + comparison.runoff_days
        != comparison.horizon_days
    ):
        raise InvariantViolation(
            "scenario_comparison_lifecycle",
            "comparison lifecycle phases must sum to the horizon",
        )
    if tuple(
        paired.replication_id for paired in comparison.paired_differences
    ) != tuple(range(comparison.replication_count)):
        raise InvariantViolation(
            "scenario_comparison_replication_alignment",
            "comparison replication identifiers must be contiguous and aligned",
        )
    if tuple(item.metric_name for item in comparison.metric_results) != METRIC_NAMES:
        raise InvariantViolation(
            "scenario_comparison_metric_dimension",
            "comparison summaries do not match the required metric dimension",
        )
    for paired in comparison.paired_differences:
        dimensions = (
            tuple(name for name, _ in paired.baseline_metric_entries),
            tuple(name for name, _ in paired.candidate_metric_entries),
            tuple(name for name, _ in paired.metric_entries),
        )
        if any(dimension != METRIC_NAMES for dimension in dimensions):
            raise InvariantViolation(
                "scenario_comparison_metric_dimension",
                "paired differences do not match the required metric dimension",
            )
        for metric_name in METRIC_NAMES:
            expected_difference = (
                paired.candidate_values[metric_name]
                - paired.baseline_values[metric_name]
            )
            if paired.values[metric_name] != expected_difference:
                raise InvariantViolation(
                    "scenario_comparison_summary_reconciliation",
                    f"paired values do not reconcile for '{metric_name}'",
                )
    if (
        tuple(item.metric_name for item in comparison.policy.materiality_thresholds)
        != METRIC_NAMES
        or any(
            item.direction is None
            for item in comparison.policy.materiality_thresholds
        )
    ):
        raise InvariantViolation(
            "scenario_comparison_policy_dimension",
            "comparison policy does not resolve every required metric",
        )
    if tuple(
        item.metric_name for item in comparison.baseline_guardrails
    ) != METRIC_NAMES or tuple(
        item.metric_name for item in comparison.candidate_guardrails
    ) != METRIC_NAMES:
        raise InvariantViolation(
            "scenario_comparison_guardrail_dimension",
            "comparison guardrails do not match the required metric dimension",
        )

    thresholds = {
        item.metric_name: item.threshold
        for item in comparison.policy.materiality_thresholds
    }
    directions = {
        item.metric_name: item.direction
        for item in comparison.policy.materiality_thresholds
    }
    baseline_replications = _source_replications(comparison, source="baseline")
    candidate_replications = _source_replications(comparison, source="candidate")
    baseline_guardrails = {
        item.metric_name: item for item in comparison.baseline_guardrails
    }
    candidate_guardrails = {
        item.metric_name: item for item in comparison.candidate_guardrails
    }
    for actual in comparison.metric_results:
        baseline_distribution = summarize_replication_metric(
            metric_name=actual.metric_name,
            replications=baseline_replications,
            guardrail=baseline_guardrails[actual.metric_name],
        )
        candidate_distribution = summarize_replication_metric(
            metric_name=actual.metric_name,
            replications=candidate_replications,
            guardrail=candidate_guardrails[actual.metric_name],
        )
        expected = _summarize_metric_comparison(
            metric_name=actual.metric_name,
            differences=tuple(
                paired.values[actual.metric_name]
                for paired in comparison.paired_differences
            ),
            baseline_mean=baseline_distribution.mean,
            candidate_mean=candidate_distribution.mean,
            baseline_breach_probability=baseline_distribution.breach_probability,
            candidate_breach_probability=candidate_distribution.breach_probability,
            baseline_breach_probability_ci95_lower=(
                baseline_distribution.breach_probability_ci95_lower
            ),
            baseline_breach_probability_ci95_upper=(
                baseline_distribution.breach_probability_ci95_upper
            ),
            candidate_breach_probability_ci95_lower=(
                candidate_distribution.breach_probability_ci95_lower
            ),
            candidate_breach_probability_ci95_upper=(
                candidate_distribution.breach_probability_ci95_upper
            ),
            materiality_threshold=thresholds[actual.metric_name],
            direction=_required_direction(directions[actual.metric_name]),
        )
        means_reconcile = math.isclose(
            actual.candidate_mean - actual.baseline_mean,
            actual.mean_difference,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        if expected != actual or not means_reconcile:
            raise InvariantViolation(
                "scenario_comparison_summary_reconciliation",
                f"comparison summary does not reconcile for '{actual.metric_name}'",
            )

    expected_joint = _joint_probabilities(
        comparison.paired_differences,
        directions=directions,
    )
    if (
        tuple(name for name, _ in comparison.joint_probability_entries)
        != _JOINT_PROBABILITY_NAMES
        or comparison.joint_probability_entries != expected_joint
    ):
        raise InvariantViolation(
            "scenario_comparison_joint_probability_reconciliation",
            "joint probabilities do not reconcile with retained pairs",
        )


def comparison_content_digest(comparison: ScenarioComparison) -> str:
    """Return a canonical SHA-256 digest of comparison content."""

    canonical = json.dumps(
        comparison.model_dump(mode="json", exclude={"digest"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _validate_experiment_compatibility(
    baseline: ExperimentResult,
    candidate: ExperimentResult,
) -> None:
    checks = (
        (
            baseline.company_model_version == candidate.company_model_version,
            "scenario_comparison_company_model_version",
            "experiments must use the same company model version",
        ),
        (
            baseline.company_model_hash == candidate.company_model_hash,
            "scenario_comparison_company_model_hash",
            "experiments must use the same resolved company model",
        ),
        (
            baseline.scenario_schema_version == candidate.scenario_schema_version,
            "scenario_comparison_scenario_schema_version",
            "experiments must use the same scenario schema version",
        ),
        (
            baseline.engine_version == candidate.engine_version,
            "scenario_comparison_engine_version",
            "experiments must use the same simulation engine version",
        ),
        (
            baseline.shock_tape_version == candidate.shock_tape_version,
            "scenario_comparison_shock_tape_version",
            "experiments must use the same shock tape version",
        ),
        (
            baseline.master_seed == candidate.master_seed,
            "scenario_comparison_master_seed",
            "experiments must use the same master seed",
        ),
        (
            _plugin_signature(baseline.plugin_versions)
            == _plugin_signature(candidate.plugin_versions),
            "scenario_comparison_plugin_versions",
            "experiments must use identical plugin versions",
        ),
        (
            _decision_rule_signature(baseline.decision_metric_rules)
            == _decision_rule_signature(candidate.decision_metric_rules),
            "scenario_comparison_decision_policy",
            "experiments must use identical company decision rules",
        ),
        (
            (
                baseline.horizon_days,
                baseline.warmup_days,
                baseline.evaluation_days,
                baseline.runoff_days,
            )
            == (
                candidate.horizon_days,
                candidate.warmup_days,
                candidate.evaluation_days,
                candidate.runoff_days,
            ),
            "scenario_comparison_lifecycle",
            "experiments must use the same simulation lifecycle",
        ),
        (
            baseline.guardrails == candidate.guardrails,
            "scenario_comparison_guardrails",
            "experiments must use identical metric guardrails",
        ),
        (
            baseline.replication_count == candidate.replication_count,
            "scenario_comparison_replication_count",
            "experiments must have the same replication count",
        ),
        (
            tuple(item.replication_id for item in baseline.replications)
            == tuple(item.replication_id for item in candidate.replications),
            "scenario_comparison_replication_alignment",
            "experiment replication identifiers must be aligned",
        ),
        (
            tuple(item.shock_tape_digest for item in baseline.replications)
            == tuple(item.shock_tape_digest for item in candidate.replications),
            "scenario_comparison_shock_tape_alignment",
            "paired replications must use identical shock tapes",
        ),
        (
            candidate.baseline_scenario_id == baseline.scenario_id,
            "scenario_comparison_baseline_scenario_id",
            "candidate must reference the compared baseline scenario",
        ),
    )
    for compatible, code, detail in checks:
        if not compatible:
            raise InvariantViolation(code, detail)


def _resolve_policy(
    policy: ComparisonPolicy | None,
    company_rules: tuple[DecisionMetricRule, ...],
) -> ComparisonPolicy:
    defaults = {
        item.metric_name: item
        for item in DEFAULT_COMPARISON_POLICY.materiality_thresholds
    }
    defaults.update(
        {
            item.metric_name: MaterialityThreshold(
                metric_name=item.metric_name,
                threshold=float(item.materiality_threshold),
                direction=item.improvement_direction,
            )
            for item in company_rules
            if item.metric_name in METRIC_NAMES
        }
    )
    for override in (policy or ComparisonPolicy()).materiality_thresholds:
        inherited = defaults[override.metric_name]
        defaults[override.metric_name] = MaterialityThreshold(
            metric_name=override.metric_name,
            threshold=override.threshold,
            direction=override.direction or inherited.direction,
        )
    return ComparisonPolicy(
        materiality_thresholds=tuple(defaults[name] for name in METRIC_NAMES)
    )


def _summarize_metric_comparison(
    *,
    metric_name: MetricName,
    differences: Sequence[float],
    baseline_mean: float,
    candidate_mean: float,
    baseline_breach_probability: float,
    candidate_breach_probability: float,
    baseline_breach_probability_ci95_lower: float,
    baseline_breach_probability_ci95_upper: float,
    candidate_breach_probability_ci95_lower: float,
    candidate_breach_probability_ci95_upper: float,
    materiality_threshold: float,
    direction: ComparisonDirection,
) -> MetricComparison:
    sample = np.asarray(differences, dtype=np.float64)
    if sample.ndim != 1 or sample.size == 0 or not np.isfinite(sample).all():
        raise ValueError("paired differences must be a non-empty finite sample")

    mean_difference = fmean(differences)
    if len(differences) == 1:
        ci95_lower = None
        ci95_upper = None
    else:
        margin = (
            _student_t_critical_value(len(differences) - 1)
            * stdev(differences)
            / math.sqrt(len(differences))
        )
        ci95_lower = mean_difference - margin
        ci95_upper = mean_difference + margin
    p5, p50, p95 = np.quantile(
        sample,
        [0.05, 0.50, 0.95],
        method="linear",
    )
    improvements = sample > 0.0 if direction == "higher" else sample < 0.0
    return MetricComparison(
        metric_name=metric_name,
        direction=direction,
        baseline_mean=baseline_mean,
        candidate_mean=candidate_mean,
        baseline_breach_probability=baseline_breach_probability,
        candidate_breach_probability=candidate_breach_probability,
        baseline_breach_probability_ci95_lower=(
            baseline_breach_probability_ci95_lower
        ),
        baseline_breach_probability_ci95_upper=(
            baseline_breach_probability_ci95_upper
        ),
        candidate_breach_probability_ci95_lower=(
            candidate_breach_probability_ci95_lower
        ),
        candidate_breach_probability_ci95_upper=(
            candidate_breach_probability_ci95_upper
        ),
        mean_difference=mean_difference,
        ci95_lower=ci95_lower,
        ci95_upper=ci95_upper,
        p5_difference=float(p5),
        p50_difference=float(p50),
        p95_difference=float(p95),
        probability_of_improvement=float(np.count_nonzero(improvements) / sample.size),
        materiality_threshold=materiality_threshold,
        is_material=abs(mean_difference) > materiality_threshold,
    )


def _student_t_critical_value(degrees_of_freedom: int) -> float:
    """Return a conservative two-sided 95% Student-t critical value."""

    if degrees_of_freedom <= len(_STUDENT_T_95_CRITICAL_VALUES):
        return _STUDENT_T_95_CRITICAL_VALUES[degrees_of_freedom - 1]
    if degrees_of_freedom <= 40:
        return _STUDENT_T_95_CRITICAL_VALUES[-1]
    if degrees_of_freedom <= 60:
        return 2.02107539
    if degrees_of_freedom <= 120:
        return 2.000297822
    return 1.979930406


def _source_replications(
    comparison: ScenarioComparison,
    *,
    source: Literal["baseline", "candidate"],
) -> tuple[ReplicationMetrics, ...]:
    return tuple(
        ReplicationMetrics(
            replication_id=paired.replication_id,
            trace_digest="0" * 64,
            shock_tape_digest="0" * 64,
            metric_entries=(
                paired.baseline_metric_entries
                if source == "baseline"
                else paired.candidate_metric_entries
            ),
        )
        for paired in comparison.paired_differences
    )


def _plugin_signature(
    plugins: tuple[PluginVersion, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((plugin.plugin_id, plugin.version) for plugin in plugins))


def _decision_rule_signature(
    rules: tuple[DecisionMetricRule, ...],
) -> tuple[DecisionMetricRule, ...]:
    return tuple(sorted(rules, key=lambda rule: rule.metric_name))


def _required_direction(
    direction: ComparisonDirection | None,
) -> ComparisonDirection:
    if direction is None:
        raise ValueError("resolved comparison policy must define a direction")
    return direction


def _joint_probabilities(
    paired_differences: tuple[PairedDifference, ...],
    *,
    directions: Mapping[MetricName, ComparisonDirection | None],
) -> tuple[tuple[JointProbabilityName, float], ...]:
    count = len(paired_differences)
    ebitda_without_otif_loss = sum(
        _is_directional_improvement(
            paired.values["ebitda"],
            _required_direction(directions["ebitda"]),
        )
        and not _is_directional_decline(
            paired.values["otif"],
            _required_direction(directions["otif"]),
        )
        for paired in paired_differences
    )
    ebitda_and_cash = sum(
        _is_directional_improvement(
            paired.values["ebitda"],
            _required_direction(directions["ebitda"]),
        )
        and _is_directional_improvement(
            paired.values["closing_cash"],
            _required_direction(directions["closing_cash"]),
        )
        for paired in paired_differences
    )
    return (
        (
            "ebitda_improves_without_otif_declining",
            ebitda_without_otif_loss / count,
        ),
        ("ebitda_and_closing_cash_improve", ebitda_and_cash / count),
    )


def _is_directional_improvement(
    difference: float,
    direction: ComparisonDirection,
) -> bool:
    return difference > 0.0 if direction == "higher" else difference < 0.0


def _is_directional_decline(
    difference: float,
    direction: ComparisonDirection,
) -> bool:
    return difference < 0.0 if direction == "higher" else difference > 0.0
