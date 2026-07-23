from datetime import date

import pytest
from pydantic import ValidationError
from tests.factories import build_northstar_company

from openenterprise_twin.analytics.adaptive import (
    AdaptiveAction,
    AdaptivePolicy,
    AdaptiveRule,
    PeriodObservation,
    derive_adaptive_scenario,
    evaluate_adaptive_policy,
    observations_from_trace,
)
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.simulation.engine import simulate_trace
from openenterprise_twin.simulation.reference import build_baseline_scenario
from openenterprise_twin.simulation.shocks import build_shock_tape


def _obs(index: int, backlog: float) -> PeriodObservation:
    return PeriodObservation(
        period_index=index,
        period_date=date.fromordinal(date(2025, 1, 1).toordinal() + index),
        values={
            "backlog_days": backlog,
            "otif": 0.97,
            "demand_change": 0.0,
            "revolver_debt_cents": 0.0,
            "capacity_utilization": 0.5,
            "closing_cash_cents": 1_000_000.0,
        },
    )


def _rule(**overrides: object) -> AdaptiveRule:
    base: dict[str, object] = {
        "rule_id": "backlog",
        "metric": "backlog_days",
        "operator": "gt",
        "threshold": 12.0,
        "action": AdaptiveAction(
            type="add_overtime_capacity", target_id="assembly", magnitude="0.1"
        ),
    }
    base.update(overrides)
    return AdaptiveRule.model_validate(base)


def _policy(rule: AdaptiveRule) -> AdaptivePolicy:
    return AdaptivePolicy(policy_id="p", rules=(rule,))


# --- DSL security & validation -------------------------------------------------


def test_unknown_metric_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _rule(metric="delete_from_users")


def test_unknown_operator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _rule(operator="__import__")


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AdaptiveAction(type="rm_rf", magnitude="0.1")  # type: ignore[arg-type]


def test_overtime_action_requires_target() -> None:
    with pytest.raises(ValidationError):
        AdaptiveAction(type="add_overtime_capacity", magnitude="0.1")


def test_magnitude_is_bounded() -> None:
    with pytest.raises(ValidationError):
        AdaptiveAction(
            type="increase_commercial_investment", magnitude="99"
        )


def test_duplicate_rule_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AdaptivePolicy(policy_id="p", rules=(_rule(), _rule()))


def test_contradictory_rules_are_rejected() -> None:
    increase = AdaptiveRule(
        rule_id="up",
        metric="demand_change",
        operator="gt",
        threshold=-0.2,
        action=AdaptiveAction(
            type="increase_commercial_investment", magnitude="0.1"
        ),
    )
    reduce = AdaptiveRule(
        rule_id="down",
        metric="demand_change",
        operator="lt",
        threshold=-0.05,
        action=AdaptiveAction(
            type="reduce_commercial_investment", magnitude="0.1"
        ),
    )
    with pytest.raises(ValidationError):
        AdaptivePolicy(policy_id="p", rules=(increase, reduce))


def test_non_overlapping_opposing_rules_are_allowed() -> None:
    # Opposing actions whose trigger regions cannot both be satisfied are fine.
    raise_high = AdaptiveRule(
        rule_id="raise",
        metric="capacity_utilization",
        operator="gt",
        threshold=0.9,
        action=AdaptiveAction(type="raise_prices", magnitude="0.05"),
    )
    cut_low = AdaptiveRule(
        rule_id="cut",
        metric="capacity_utilization",
        operator="lt",
        threshold=0.3,
        action=AdaptiveAction(type="cut_prices", magnitude="0.05"),
    )
    policy = AdaptivePolicy(policy_id="p", rules=(raise_high, cut_low))
    assert len(policy.rules) == 2


# --- Controller semantics ------------------------------------------------------


def test_threshold_boundary_uses_strict_operator() -> None:
    rule = _rule(operator="gt", threshold=12.0)
    at_threshold = tuple(_obs(i, 12.0) for i in range(3))
    assert evaluate_adaptive_policy(_policy(rule), at_threshold).activations == ()
    above = tuple(_obs(i, 12.5) for i in range(3))
    assert evaluate_adaptive_policy(_policy(rule), above).activations


def test_persistence_requires_consecutive_periods() -> None:
    rule = _rule(persistence_periods=3, max_activations=9)
    # Condition true only every other period -> never three in a row.
    flapping = tuple(
        _obs(i, 20.0 if i % 2 == 0 else 0.0) for i in range(10)
    )
    assert evaluate_adaptive_policy(_policy(rule), flapping).activations == ()


def test_cooldown_blocks_immediate_refire() -> None:
    rule = _rule(cooldown_periods=5, max_activations=9)
    always_high = tuple(_obs(i, 20.0) for i in range(12))
    activations = evaluate_adaptive_policy(_policy(rule), always_high).activations
    fired_positions = [a.period_index for a in activations]
    # First fire at 0, then respecting a 5-period cooldown: 6, then 12...
    assert fired_positions == [0, 6]


def test_activation_limit_is_respected() -> None:
    rule = _rule(cooldown_periods=0, max_activations=2)
    always_high = tuple(_obs(i, 20.0) for i in range(10))
    activations = evaluate_adaptive_policy(_policy(rule), always_high).activations
    assert len(activations) == 2


def test_priority_orders_simultaneous_activations() -> None:
    high = _rule(rule_id="high", priority=90, cooldown_periods=0)
    low = AdaptiveRule(
        rule_id="low",
        metric="backlog_days",
        operator="gt",
        threshold=12.0,
        priority=10,
        action=AdaptiveAction(
            type="add_overtime_capacity", target_id="test", magnitude="0.1"
        ),
    )
    policy = AdaptivePolicy(policy_id="p", rules=(low, high))
    activations = evaluate_adaptive_policy(policy, (_obs(0, 20.0),)).activations
    assert [a.rule_id for a in activations] == ["high", "low"]


def test_evaluation_is_deterministic() -> None:
    rule = _rule(cooldown_periods=2, max_activations=5)
    stream = tuple(_obs(i, 20.0) for i in range(20))
    first = evaluate_adaptive_policy(_policy(rule), stream)
    second = evaluate_adaptive_policy(_policy(rule), stream)
    assert first == second


# --- Trace integration ---------------------------------------------------------


def test_observations_from_trace_are_bounded() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=200)
    tape = build_shock_tape(company, scenario, seed=7, replication_id=0)
    trace = simulate_trace(company, scenario, tape, allow_rescue_funding=True)
    observations = observations_from_trace(trace)
    assert len(observations) == 200
    for observation in observations:
        assert 0.0 <= observation.values["otif"] <= 1.0
        assert 0.0 <= observation.values["capacity_utilization"] <= 1.5


def test_derive_adaptive_scenario_applies_actions() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=200)
    tape = build_shock_tape(company, scenario, seed=7, replication_id=0)
    trace = simulate_trace(company, scenario, tape, allow_rescue_funding=True)
    observations = observations_from_trace(trace)
    policy = AdaptivePolicy(
        policy_id="p",
        rules=(
            AdaptiveRule(
                rule_id="capacity",
                metric="backlog_days",
                operator="gt",
                threshold=10.0,
                window_periods=5,
                persistence_periods=3,
                cooldown_periods=20,
                max_activations=5,
                action=AdaptiveAction(
                    type="add_overtime_capacity",
                    target_id="assembly",
                    magnitude="0.1",
                ),
                action_cost_cents=500_000,
            ),
        ),
    )
    evaluation = evaluate_adaptive_policy(policy, observations)
    assert evaluation.activations
    adaptive_scenario = derive_adaptive_scenario(
        base_scenario=scenario,
        company=company,
        evaluation=evaluation,
        policy=policy,
        adaptive_scenario_id="northstar-adaptive",
        adaptive_scenario_name="Adaptive capacity plan",
    )
    assert adaptive_scenario.scenario_id == "northstar-adaptive"
    assert adaptive_scenario.policy_levers.resource_changes
    # The derived scenario remains valid against the company model.
    from openenterprise_twin.domain.scenario import (
        validate_scenario_against_company,
    )

    validate_scenario_against_company(adaptive_scenario, company)


def test_paired_adaptive_vs_static_is_deterministic() -> None:
    from openenterprise_twin.analytics.adaptive import compare_adaptive_vs_static

    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=90)
    policy = AdaptivePolicy(
        policy_id="p",
        rules=(
            AdaptiveRule(
                rule_id="capacity",
                metric="backlog_days",
                operator="gt",
                threshold=8.0,
                window_periods=5,
                persistence_periods=3,
                cooldown_periods=15,
                max_activations=5,
                action=AdaptiveAction(
                    type="add_overtime_capacity",
                    target_id="assembly",
                    magnitude="0.1",
                ),
                action_cost_cents=400_000,
            ),
        ),
    )
    first = compare_adaptive_vs_static(
        company=company,
        static_scenario=scenario,
        policy=policy,
        master_seed=11,
        replications=3,
    )
    second = compare_adaptive_vs_static(
        company=company,
        static_scenario=scenario,
        policy=policy,
        master_seed=11,
        replications=3,
    )
    assert first.metric_deltas == second.metric_deltas
    assert "ebitda" in first.metric_deltas
    assert first.activation_count >= 0


def test_derive_scenario_rejects_unknown_resource() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=60)
    from openenterprise_twin.analytics.adaptive import (
        Activation,
        AdaptiveEvaluation,
        RuleActivationSummary,
    )

    policy = AdaptivePolicy(
        policy_id="p",
        rules=(
            AdaptiveRule(
                rule_id="bad",
                metric="backlog_days",
                operator="gt",
                threshold=1.0,
                action=AdaptiveAction(
                    type="add_overtime_capacity",
                    target_id="ghost-resource",
                    magnitude="0.1",
                ),
            ),
        ),
    )
    evaluation = AdaptiveEvaluation(
        policy_id="p",
        observed_periods=1,
        activations=(
            Activation(
                rule_id="bad",
                period_index=0,
                period_date=date(2025, 1, 1),
                metric="backlog_days",
                observed_value=2.0,
                threshold=1.0,
                action_type="add_overtime_capacity",
                action_cost_cents=0,
            ),
        ),
        rule_summaries=(
            RuleActivationSummary(
                rule_id="bad", activation_count=1, total_cost_cents=0
            ),
        ),
        total_action_cost_cents=0,
    )
    with pytest.raises(DomainValidationError):
        derive_adaptive_scenario(
            base_scenario=scenario,
            company=company,
            evaluation=evaluation,
            policy=policy,
            adaptive_scenario_id="x",
            adaptive_scenario_name="x",
        )
