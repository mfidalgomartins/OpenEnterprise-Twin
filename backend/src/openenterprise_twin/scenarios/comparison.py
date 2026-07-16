"""Deterministic paired comparison of compatible simulation experiments."""

import json
import math
from collections.abc import Mapping, Sequence
from hashlib import sha256
from statistics import fmean, stdev
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
    MetricName,
    PluginVersion,
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

_NORMAL_95_CRITICAL_VALUE = 1.959963984540054
_HIGHER_IS_BETTER: frozenset[MetricName] = frozenset(
    {"revenue", "ebitda", "free_cash_flow", "closing_cash", "otif"}
)
_JOINT_PROBABILITY_NAMES: tuple[JointProbabilityName, ...] = (
    "ebitda_improves_without_otif_declining",
    "ebitda_and_closing_cash_improve",
)


class MaterialityThreshold(DomainModel):
    """Absolute mean-difference threshold for one metric."""

    metric_name: MetricName
    threshold: NonNegativeFloat


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
    mean_difference: FiniteFloat
    ci95_lower: FiniteFloat
    ci95_upper: FiniteFloat
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
    scenario_schema_version: VersionString
    engine_version: VersionString
    shock_tape_version: VersionString
    baseline_plugin_versions: tuple[PluginVersion, ...]
    candidate_plugin_versions: tuple[PluginVersion, ...]
    master_seed: Annotated[int, Field(ge=0)]
    replication_count: Annotated[int, Field(gt=0)]
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

    _validate_experiment_compatibility(baseline, candidate)
    validate_experiment_result(baseline)
    validate_experiment_result(candidate)

    resolved_policy = _resolve_policy(policy, baseline.decision_metric_rules)
    thresholds = {
        item.metric_name: item.threshold
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
            materiality_threshold=thresholds[metric_name],
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
        scenario_schema_version=baseline.scenario_schema_version,
        engine_version=baseline.engine_version,
        shock_tape_version=baseline.shock_tape_version,
        baseline_plugin_versions=baseline.plugin_versions,
        candidate_plugin_versions=candidate.plugin_versions,
        master_seed=baseline.master_seed,
        replication_count=baseline.replication_count,
        policy=resolved_policy,
        paired_differences=paired_differences,
        metric_results=metric_results,
        joint_probability_entries=_joint_probabilities(paired_differences),
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
    ):
        raise InvariantViolation(
            "scenario_comparison_policy_dimension",
            "comparison policy does not resolve every required metric",
        )

    thresholds = {
        item.metric_name: item.threshold
        for item in comparison.policy.materiality_thresholds
    }
    for actual in comparison.metric_results:
        expected = _summarize_metric_comparison(
            metric_name=actual.metric_name,
            differences=tuple(
                paired.values[actual.metric_name]
                for paired in comparison.paired_differences
            ),
            baseline_mean=actual.baseline_mean,
            candidate_mean=actual.candidate_mean,
            baseline_breach_probability=actual.baseline_breach_probability,
            candidate_breach_probability=actual.candidate_breach_probability,
            materiality_threshold=thresholds[actual.metric_name],
        )
        means_reconcile = math.isclose(
            actual.candidate_mean - actual.baseline_mean,
            actual.mean_difference,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        source_means_reconcile = math.isclose(
            actual.baseline_mean,
            fmean(
                paired.baseline_values[actual.metric_name]
                for paired in comparison.paired_differences
            ),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ) and math.isclose(
            actual.candidate_mean,
            fmean(
                paired.candidate_values[actual.metric_name]
                for paired in comparison.paired_differences
            ),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        if expected != actual or not means_reconcile or not source_means_reconcile:
            raise InvariantViolation(
                "scenario_comparison_summary_reconciliation",
                f"comparison summary does not reconcile for '{actual.metric_name}'",
            )

    expected_joint = _joint_probabilities(comparison.paired_differences)
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
            baseline.plugin_versions == candidate.plugin_versions,
            "scenario_comparison_plugin_versions",
            "experiments must use identical plugin versions",
        ),
        (
            baseline.decision_metric_rules == candidate.decision_metric_rules,
            "scenario_comparison_decision_policy",
            "experiments must use identical company decision rules",
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
    overrides = {
        item.metric_name: item
        for item in (policy or ComparisonPolicy()).materiality_thresholds
    }
    defaults = {
        item.metric_name: item
        for item in DEFAULT_COMPARISON_POLICY.materiality_thresholds
    }
    defaults.update(
        {
            item.metric_name: MaterialityThreshold(
                metric_name=item.metric_name,
                threshold=float(item.materiality_threshold),
            )
            for item in company_rules
            if item.metric_name in METRIC_NAMES
        }
    )
    defaults.update(overrides)
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
    materiality_threshold: float,
) -> MetricComparison:
    sample = np.asarray(differences, dtype=np.float64)
    if sample.ndim != 1 or sample.size == 0 or not np.isfinite(sample).all():
        raise ValueError("paired differences must be a non-empty finite sample")

    mean_difference = fmean(differences)
    if len(differences) == 1:
        margin = 0.0
    else:
        margin = (
            _NORMAL_95_CRITICAL_VALUE * stdev(differences) / math.sqrt(len(differences))
        )
    p5, p50, p95 = np.quantile(
        sample,
        [0.05, 0.50, 0.95],
        method="linear",
    )
    direction: ComparisonDirection = (
        "higher" if metric_name in _HIGHER_IS_BETTER else "lower"
    )
    improvements = sample > 0.0 if direction == "higher" else sample < 0.0
    return MetricComparison(
        metric_name=metric_name,
        direction=direction,
        baseline_mean=baseline_mean,
        candidate_mean=candidate_mean,
        baseline_breach_probability=baseline_breach_probability,
        candidate_breach_probability=candidate_breach_probability,
        mean_difference=mean_difference,
        ci95_lower=mean_difference - margin,
        ci95_upper=mean_difference + margin,
        p5_difference=float(p5),
        p50_difference=float(p50),
        p95_difference=float(p95),
        probability_of_improvement=float(np.count_nonzero(improvements) / sample.size),
        materiality_threshold=materiality_threshold,
        is_material=abs(mean_difference) >= materiality_threshold,
    )


def _joint_probabilities(
    paired_differences: tuple[PairedDifference, ...],
) -> tuple[tuple[JointProbabilityName, float], ...]:
    count = len(paired_differences)
    ebitda_without_otif_loss = sum(
        paired.values["ebitda"] > 0.0 and paired.values["otif"] >= 0.0
        for paired in paired_differences
    )
    ebitda_and_cash = sum(
        paired.values["ebitda"] > 0.0 and paired.values["closing_cash"] > 0.0
        for paired in paired_differences
    )
    return (
        (
            "ebitda_improves_without_otif_declining",
            ebitda_without_otif_loss / count,
        ),
        ("ebitda_and_closing_cash_improve", ebitda_and_cash / count),
    )
