"""Contract tests for deterministic paired scenario comparison."""

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from statistics import stdev
from typing import cast

import pytest

from openenterprise_twin.domain.company import DecisionMetricRule
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.scenarios.comparison import (
    DEFAULT_COMPARISON_POLICY,
    ComparisonPolicy,
    MaterialityThreshold,
    compare_experiments,
    comparison_content_digest,
    validate_scenario_comparison,
)
from openenterprise_twin.simulation.experiment import (
    METRIC_NAMES,
    ExperimentResult,
    MetricGuardrail,
    MetricName,
    MetricResult,
    PluginVersion,
    ReplicationMetrics,
    experiment_content_digest,
)
from openenterprise_twin.simulation.metrics import summarize_distribution

BASELINE_VALUES: dict[MetricName, tuple[float, ...]] = {
    "revenue": (100.0, 110.0, 90.0, 100.0),
    "ebitda": (40.0, 50.0, 45.0, 55.0),
    "free_cash_flow": (30.0, 35.0, 25.0, 40.0),
    "closing_cash": (50.0, 50.0, 50.0, 50.0),
    "otif": (0.95, 0.96, 0.94, 0.95),
    "cancellation_rate": (0.04, 0.05, 0.03, 0.04),
    "backlog_units": (10.0, 12.0, 8.0, 10.0),
    "capacity_utilization": (0.80, 0.82, 0.78, 0.80),
    "peak_revolver": (20.0, 30.0, 10.0, 20.0),
    "rescue_funding": (0.0, 0.0, 0.0, 0.0),
}
CANDIDATE_VALUES: dict[MetricName, tuple[float, ...]] = {
    "revenue": (110.0, 105.0, 95.0, 120.0),
    "ebitda": (50.0, 60.0, 40.0, 60.0),
    "free_cash_flow": (35.0, 32.0, 30.0, 50.0),
    "closing_cash": (55.0, 45.0, 60.0, 55.0),
    "otif": (0.96, 0.95, 0.96, 0.95),
    "cancellation_rate": (0.03, 0.06, 0.02, 0.04),
    "backlog_units": (8.0, 13.0, 7.0, 10.0),
    "capacity_utilization": (0.82, 0.81, 0.80, 0.79),
    "peak_revolver": (15.0, 35.0, 10.0, 18.0),
    "rescue_funding": (0.0, 0.0, 0.0, 0.0),
}
LOWER_IS_BETTER = {
    "cancellation_rate",
    "backlog_units",
    "capacity_utilization",
    "peak_revolver",
    "rescue_funding",
}


def _build_experiment(
    *,
    scenario_id: str,
    scenario_name: str,
    values: dict[MetricName, tuple[float, ...]],
    baseline_scenario_id: str | None,
    master_seed: int = 731,
) -> ExperimentResult:
    replication_count = len(values["revenue"])
    assert all(
        len(metric_values) == replication_count for metric_values in values.values()
    )
    replications = tuple(
        ReplicationMetrics(
            replication_id=replication_id,
            trace_digest=sha256(f"{scenario_id}:{replication_id}".encode()).hexdigest(),
            metric_entries=tuple(
                (metric_name, values[metric_name][replication_id])
                for metric_name in METRIC_NAMES
            ),
        )
        for replication_id in range(replication_count)
    )
    guardrails = tuple(
        MetricGuardrail(
            metric_name=metric_name,
            threshold=0.0,
            breach_when="above" if metric_name in LOWER_IS_BETTER else "below",
            downside_tail="upper" if metric_name in LOWER_IS_BETTER else "lower",
        )
        for metric_name in METRIC_NAMES
    )
    metric_results = tuple(
        MetricResult(
            metric_name=metric_name,
            distribution=summarize_distribution(
                values[metric_name],
                guardrail=0.0,
                breach_when="above" if metric_name in LOWER_IS_BETTER else "below",
                downside_tail=("upper" if metric_name in LOWER_IS_BETTER else "lower"),
            ),
        )
        for metric_name in METRIC_NAMES
    )
    result = ExperimentResult(
        scenario_id=scenario_id,
        scenario_name=scenario_name,
        baseline_scenario_id=baseline_scenario_id,
        policy_levers=PolicyLevers(
            commercial_investment_change=(
                Decimal("0.10") if baseline_scenario_id else Decimal("0")
            )
        ),
        company_model_version="0.1.0",
        scenario_schema_version="0.1.0",
        engine_version="0.1.0",
        shock_tape_version="0.1.0",
        company_model_hash=sha256(b"northstar-company").hexdigest(),
        resolved_assumptions_hash=sha256(scenario_id.encode()).hexdigest(),
        plugin_versions=(PluginVersion(plugin_id="core.simulation", version="0.1.0"),),
        master_seed=master_seed,
        replication_count=replication_count,
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
        duration_seconds=1.0,
        horizon_days=30,
        warmup_days=0,
        evaluation_days=30,
        runoff_days=0,
        guardrails=guardrails,
        replications=replications,
        metric_results=metric_results,
        digest="0" * 64,
    )
    return result.model_copy(update={"digest": experiment_content_digest(result)})


def _experiments() -> tuple[ExperimentResult, ExperimentResult]:
    baseline = _build_experiment(
        scenario_id="current-plan",
        scenario_name="Current plan",
        values=BASELINE_VALUES,
        baseline_scenario_id=None,
    )
    candidate = _build_experiment(
        scenario_id="balanced-growth",
        scenario_name="Balanced growth",
        values=CANDIDATE_VALUES,
        baseline_scenario_id=baseline.scenario_id,
    )
    return baseline, candidate


def _rehash(result: ExperimentResult, **updates: object) -> ExperimentResult:
    changed = result.model_copy(update={**updates, "digest": "0" * 64})
    return changed.model_copy(update={"digest": experiment_content_digest(changed)})


def test_comparison_retains_exact_paired_values_and_provenance() -> None:
    baseline, candidate = _experiments()

    comparison = compare_experiments(baseline, candidate)

    assert comparison.baseline_scenario_id == baseline.scenario_id
    assert comparison.baseline_scenario_name == baseline.scenario_name
    assert comparison.candidate_scenario_id == candidate.scenario_id
    assert comparison.candidate_scenario_name == candidate.scenario_name
    assert comparison.candidate_policy_levers == candidate.policy_levers
    assert comparison.baseline_experiment_digest == baseline.digest
    assert comparison.candidate_experiment_digest == candidate.digest
    assert comparison.company_model_version == baseline.company_model_version
    assert comparison.scenario_schema_version == baseline.scenario_schema_version
    assert comparison.engine_version == baseline.engine_version
    assert comparison.shock_tape_version == baseline.shock_tape_version
    assert comparison.baseline_plugin_versions == baseline.plugin_versions
    assert comparison.candidate_plugin_versions == candidate.plugin_versions
    assert comparison.master_seed == baseline.master_seed
    assert comparison.replication_count == baseline.replication_count
    assert len(comparison.digest) == 64
    assert tuple(comparison.metrics) == METRIC_NAMES
    for paired in comparison.paired_differences:
        for metric_name in METRIC_NAMES:
            expected = (
                CANDIDATE_VALUES[metric_name][paired.replication_id]
                - BASELINE_VALUES[metric_name][paired.replication_id]
            )
            assert paired.values[metric_name] == pytest.approx(expected)


def test_paired_statistics_use_normal_ci_and_linear_percentiles() -> None:
    baseline, candidate = _experiments()

    revenue = compare_experiments(baseline, candidate).metrics["revenue"]

    differences = (-5.0, 5.0, 10.0, 20.0)
    margin = 1.959963984540054 * stdev(differences) / len(differences) ** 0.5
    assert revenue.baseline_mean == pytest.approx(100.0)
    assert revenue.candidate_mean == pytest.approx(107.5)
    assert revenue.baseline_breach_probability == 0.0
    assert revenue.candidate_breach_probability == 0.0
    assert revenue.mean_difference == pytest.approx(7.5)
    assert revenue.ci95_lower == pytest.approx(7.5 - margin)
    assert revenue.ci95_upper == pytest.approx(7.5 + margin)
    assert revenue.p5_difference == pytest.approx(-3.5)
    assert revenue.p50_difference == pytest.approx(7.5)
    assert revenue.p95_difference == pytest.approx(18.5)
    assert revenue.probability_of_improvement == pytest.approx(0.75)
    assert revenue.direction == "higher"


def test_singleton_paired_ci_collapses_to_observed_difference() -> None:
    baseline = _build_experiment(
        scenario_id="current-plan",
        scenario_name="Current plan",
        values={name: (values[0],) for name, values in BASELINE_VALUES.items()},
        baseline_scenario_id=None,
    )
    candidate = _build_experiment(
        scenario_id="balanced-growth",
        scenario_name="Balanced growth",
        values={name: (values[0],) for name, values in CANDIDATE_VALUES.items()},
        baseline_scenario_id=baseline.scenario_id,
    )

    revenue = compare_experiments(baseline, candidate).metrics["revenue"]

    assert revenue.mean_difference == 10.0
    assert revenue.ci95_lower is None
    assert revenue.ci95_upper is None
    assert revenue.p5_difference == 10.0
    assert revenue.p50_difference == 10.0
    assert revenue.p95_difference == 10.0


def test_lower_is_better_and_joint_probabilities_are_explicit() -> None:
    baseline, candidate = _experiments()

    comparison = compare_experiments(baseline, candidate)

    cancellation = comparison.metrics["cancellation_rate"]
    assert cancellation.direction == "lower"
    assert cancellation.probability_of_improvement == pytest.approx(0.5)
    assert comparison.joint_probabilities[
        "ebitda_improves_without_otif_declining"
    ] == pytest.approx(0.5)
    assert comparison.joint_probabilities[
        "ebitda_and_closing_cash_improve"
    ] == pytest.approx(0.5)


def test_materiality_defaults_are_typed_auditable_and_overridable() -> None:
    baseline, candidate = _experiments()
    assert (
        tuple(
            threshold.metric_name
            for threshold in DEFAULT_COMPARISON_POLICY.materiality_thresholds
        )
        == METRIC_NAMES
    )

    immaterial = compare_experiments(
        baseline,
        candidate,
        ComparisonPolicy(
            materiality_thresholds=(
                MaterialityThreshold(metric_name="revenue", threshold=8.0),
            )
        ),
    )
    material = compare_experiments(
        baseline,
        candidate,
        ComparisonPolicy(
            materiality_thresholds=(
                MaterialityThreshold(metric_name="revenue", threshold=7.0),
            )
        ),
    )

    assert immaterial.metrics["revenue"].materiality_threshold == 8.0
    assert not immaterial.metrics["revenue"].is_material
    assert material.metrics["revenue"].materiality_threshold == 7.0
    assert material.metrics["revenue"].is_material
    assert len(material.policy.materiality_thresholds) == len(METRIC_NAMES)


def test_zero_difference_is_not_material_with_zero_threshold() -> None:
    baseline, candidate = _experiments()
    comparison = compare_experiments(
        baseline,
        candidate,
        ComparisonPolicy(
            materiality_thresholds=(
                MaterialityThreshold(
                    metric_name="rescue_funding",
                    threshold=0.0,
                ),
            )
        ),
    )

    assert comparison.metrics["rescue_funding"].mean_difference == 0.0
    assert comparison.metrics["rescue_funding"].is_material is False


def test_company_improvement_direction_is_applied() -> None:
    baseline, candidate = _experiments()
    rules = (
        DecisionMetricRule(
            metric_name="capacity_utilization",
            materiality_threshold=Decimal("0.001"),
            improvement_direction="higher",
        ),
    )
    baseline = _rehash(baseline, decision_metric_rules=rules)
    candidate = _rehash(candidate, decision_metric_rules=rules)

    comparison = compare_experiments(baseline, candidate)

    assert comparison.metrics["capacity_utilization"].direction == "higher"
    assert comparison.metrics[
        "capacity_utilization"
    ].probability_of_improvement == pytest.approx(0.5)


def test_joint_probabilities_follow_company_improvement_directions() -> None:
    baseline, candidate = _experiments()
    rules = tuple(
        DecisionMetricRule(
            metric_name=metric_name,
            materiality_threshold=Decimal("0"),
            improvement_direction="lower",
        )
        for metric_name in ("ebitda", "otif", "closing_cash")
    )
    baseline = _rehash(baseline, decision_metric_rules=rules)
    candidate = _rehash(candidate, decision_metric_rules=rules)

    comparison = compare_experiments(baseline, candidate)

    assert comparison.metrics["ebitda"].probability_of_improvement == 0.25
    assert (
        comparison.joint_probabilities[
            "ebitda_improves_without_otif_declining"
        ]
        == 0.0
    )
    assert (
        comparison.joint_probabilities["ebitda_and_closing_cash_improve"]
        == 0.0
    )


@pytest.mark.parametrize(
    ("field", "changed_value", "error_code"),
    [
        (
            "company_model_version",
            "0.2.0",
            "scenario_comparison_company_model_version",
        ),
        (
            "scenario_schema_version",
            "0.2.0",
            "scenario_comparison_scenario_schema_version",
        ),
        ("engine_version", "0.2.0", "scenario_comparison_engine_version"),
        (
            "shock_tape_version",
            "0.2.0",
            "scenario_comparison_shock_tape_version",
        ),
        ("master_seed", 999, "scenario_comparison_master_seed"),
    ],
)
def test_comparison_rejects_incompatible_common_random_inputs(
    field: str, changed_value: object, error_code: str
) -> None:
    baseline, candidate = _experiments()
    candidate = _rehash(candidate, **{field: changed_value})

    with pytest.raises(InvariantViolation) as error:
        compare_experiments(baseline, candidate)

    assert error.value.code == error_code


def test_comparison_rejects_different_replication_count() -> None:
    baseline, _ = _experiments()
    candidate = _build_experiment(
        scenario_id="balanced-growth",
        scenario_name="Balanced growth",
        values={name: values[:3] for name, values in CANDIDATE_VALUES.items()},
        baseline_scenario_id=baseline.scenario_id,
    )

    with pytest.raises(InvariantViolation) as error:
        compare_experiments(baseline, candidate)

    assert error.value.code == "scenario_comparison_replication_count"


def test_comparison_rejects_different_plugin_versions() -> None:
    baseline, candidate = _experiments()
    candidate = _rehash(
        candidate,
        plugin_versions=(
            PluginVersion(plugin_id="core.simulation", version="0.2.0"),
        ),
    )

    with pytest.raises(InvariantViolation) as error:
        compare_experiments(baseline, candidate)

    assert error.value.code == "scenario_comparison_plugin_versions"


def test_comparison_accepts_same_plugins_in_different_order() -> None:
    baseline, candidate = _experiments()
    plugins = (
        PluginVersion(plugin_id="alpha", version="0.1.0"),
        PluginVersion(plugin_id="beta", version="0.1.0"),
    )
    baseline = _rehash(baseline, plugin_versions=plugins)
    candidate = _rehash(candidate, plugin_versions=tuple(reversed(plugins)))

    comparison = compare_experiments(baseline, candidate)

    assert comparison.replication_count == baseline.replication_count


def test_comparison_rejects_different_lifecycle_contract() -> None:
    baseline, candidate = _experiments()
    candidate = _rehash(
        candidate,
        horizon_days=515,
        warmup_days=91,
        evaluation_days=364,
        runoff_days=60,
    )

    with pytest.raises(InvariantViolation) as error:
        compare_experiments(baseline, candidate)

    assert error.value.code == "scenario_comparison_lifecycle"


def test_comparison_rejects_non_aligned_replication_ids() -> None:
    baseline, candidate = _experiments()
    candidate = _rehash(
        candidate,
        replications=(
            candidate.replications[1],
            candidate.replications[0],
            *candidate.replications[2:],
        ),
    )

    with pytest.raises(InvariantViolation) as error:
        compare_experiments(baseline, candidate)

    assert error.value.code == "scenario_comparison_replication_alignment"


def test_comparison_is_deterministic_and_collections_are_immutable() -> None:
    baseline, candidate = _experiments()

    first = compare_experiments(baseline, candidate)
    second = compare_experiments(baseline, candidate)

    assert first.metric_results == second.metric_results
    assert first.paired_differences == second.paired_differences
    assert first.policy == second.policy
    assert first.joint_probability_entries == second.joint_probability_entries
    assert isinstance(first.metric_results, tuple)
    assert isinstance(first.paired_differences, tuple)
    with pytest.raises(TypeError):
        cast(dict[str, object], first.metrics)["revenue"] = object()
    with pytest.raises(TypeError):
        cast(dict[str, float], first.paired_differences[0].values)["revenue"] = 0.0


def test_validation_rejects_tampering_even_after_digest_is_recomputed() -> None:
    baseline, candidate = _experiments()
    comparison = compare_experiments(baseline, candidate)
    first = comparison.paired_differences[0]
    changed_entries = tuple(
        (name, value + 1.0 if name == "revenue" else value)
        for name, value in first.metric_entries
    )
    changed_first = first.model_copy(update={"metric_entries": changed_entries})
    tampered = comparison.model_copy(
        update={
            "paired_differences": (
                changed_first,
                *comparison.paired_differences[1:],
            ),
            "digest": "0" * 64,
        }
    )
    tampered = tampered.model_copy(
        update={"digest": comparison_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation) as error:
        validate_scenario_comparison(tampered)

    assert error.value.code == "scenario_comparison_summary_reconciliation"


def test_validation_rejects_tampered_source_means_after_rehash() -> None:
    baseline, candidate = _experiments()
    comparison = compare_experiments(baseline, candidate)
    revenue = comparison.metric_results[0].model_copy(
        update={
            "baseline_mean": comparison.metric_results[0].baseline_mean + 1.0,
            "candidate_mean": comparison.metric_results[0].candidate_mean + 1.0,
        }
    )
    tampered = comparison.model_copy(
        update={
            "metric_results": (revenue, *comparison.metric_results[1:]),
            "digest": "0" * 64,
        }
    )
    tampered = tampered.model_copy(
        update={"digest": comparison_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation) as error:
        validate_scenario_comparison(tampered)

    assert error.value.code == "scenario_comparison_summary_reconciliation"


def test_validation_rejects_tampered_breach_probability_after_rehash() -> None:
    baseline, candidate = _experiments()
    comparison = compare_experiments(baseline, candidate)
    revenue = comparison.metric_results[0].model_copy(
        update={"candidate_breach_probability": 0.75}
    )
    tampered = comparison.model_copy(
        update={
            "metric_results": (revenue, *comparison.metric_results[1:]),
            "digest": "0" * 64,
        }
    )
    tampered = tampered.model_copy(
        update={"digest": comparison_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation) as error:
        validate_scenario_comparison(tampered)

    assert error.value.code == "scenario_comparison_summary_reconciliation"
