from datetime import timedelta
from decimal import Decimal
from typing import cast

import pytest

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.scenario import (
    PolicyLevers,
    SegmentProductPriceChange,
)
from openenterprise_twin.reporting.brief import (
    brief_content_digest,
    build_executive_brief,
    validate_executive_brief,
)
from openenterprise_twin.scenarios.comparison import (
    ScenarioComparison,
    compare_experiments,
)
from openenterprise_twin.simulation.experiment import (
    ExperimentRequest,
    run_experiment,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)


def _comparison(*, liquidity_stress: bool = False) -> ScenarioComparison:
    company = build_northstar_company()
    baseline_scenario = build_baseline_scenario(horizon_days=30)
    if liquidity_stress:
        levers = PolicyLevers(one_off_capital_investment_cents=100_000_000)
        scenario_id = "liquidity-stress"
        scenario_name = "Liquidity stress"
    else:
        levers = PolicyLevers(
            price_changes=(
                SegmentProductPriceChange(
                    segment_id="contracted",
                    product_id="standard-valve",
                    price_change=Decimal("0.02"),
                ),
            )
        )
        scenario_id = "balanced-pricing"
        scenario_name = "Balanced pricing"
    candidate_scenario = baseline_scenario.model_copy(
        update={
            "scenario_id": scenario_id,
            "name": scenario_name,
            "baseline_scenario_id": baseline_scenario.scenario_id,
            "policy_levers": levers,
        }
    )
    baseline = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=baseline_scenario,
            master_seed=20260716,
            replications=8,
            max_workers=1,
        )
    )
    candidate = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=candidate_scenario,
            master_seed=20260716,
            replications=8,
            max_workers=1,
        )
    )
    return compare_experiments(baseline, candidate)


def _rescue_comparison(
    *,
    baseline_investment_cents: int,
    candidate_investment_cents: int,
) -> ScenarioComparison:
    company = build_northstar_company()
    company = company.model_copy(
        update={
            "financial_policy": company.financial_policy.model_copy(
                update={"opening_cash_cents": 5_000_000, "revolver_limit_cents": 0}
            )
        }
    )
    baseline_scenario = build_baseline_scenario(horizon_days=5)
    baseline_scenario = baseline_scenario.model_copy(
        update={
            "policy_levers": PolicyLevers(
                one_off_capital_investment_cents=baseline_investment_cents
            )
        }
    )
    candidate_scenario = baseline_scenario.model_copy(
        update={
            "scenario_id": "candidate-liquidity-stress",
            "name": "Candidate liquidity stress",
            "baseline_scenario_id": baseline_scenario.scenario_id,
            "policy_levers": PolicyLevers(
                one_off_capital_investment_cents=candidate_investment_cents
            ),
        }
    )
    baseline = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=baseline_scenario,
            master_seed=7,
            replications=3,
        )
    )
    candidate = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=candidate_scenario,
            master_seed=7,
            replications=3,
        )
    )
    return compare_experiments(baseline, candidate)


def test_recommendation_cites_only_computed_metric_evidence() -> None:
    comparison = _comparison()

    brief = build_executive_brief(comparison)

    assert brief.recommendation.evidence_metric_ids
    assert set(brief.recommendation.evidence_metric_ids) <= set(comparison.metrics)
    assert all(
        outcome.metric_name in comparison.metrics for outcome in brief.outcome_deltas
    )
    assert brief.provenance.comparison_digest == comparison.digest
    assert brief.provenance.baseline_plugin_versions
    assert brief.provenance.candidate_plugin_versions
    assert len(brief.provenance.baseline_resolved_assumptions_hash) == 64
    assert len(brief.provenance.candidate_resolved_assumptions_hash) == 64
    assert brief.provenance.created_at.tzinfo is not None
    assert brief.provenance.duration_seconds >= 0
    assert brief.digest == brief_content_digest(brief)


def test_brief_freezes_decision_governance_and_evidence_linked_actions() -> None:
    comparison = _comparison(liquidity_stress=True)

    brief = build_executive_brief(comparison)

    assert brief.governance.decision_owner == "Managing Director"
    assert brief.governance.review_date == (
        comparison.candidate_experiment_created_at + timedelta(days=30)
    ).date()
    assert "comparison digest" in brief.governance.decision_record_action.lower()
    assert brief.actions
    assert brief.actions[0].action_id == "record-decision"
    assert all(action.owner for action in brief.actions)
    assert all(
        action.due_date <= brief.governance.review_date for action in brief.actions
    )
    assert all(
        set(action.evidence_metric_ids) <= set(comparison.metrics)
        for action in brief.actions
    )
    constrained_metrics = {
        constraint.metric_name for constraint in brief.constraints
    }
    action_metrics = {
        metric_name
        for action in brief.actions
        for metric_name in action.evidence_metric_ids
    }
    assert constrained_metrics <= action_metrics


def test_brief_explains_only_policy_levers_present_in_candidate() -> None:
    comparison = _comparison()
    brief = build_executive_brief(comparison)

    assert [mechanism.mechanism_id for mechanism in brief.mechanisms] == ["pricing"]
    assert "2.00%" in brief.mechanisms[0].detail
    assert comparison.metrics["revenue"].materiality_threshold == 1_000_000


def test_liquidity_breach_prevents_unqualified_recommendation() -> None:
    comparison = _comparison(liquidity_stress=True)

    brief = build_executive_brief(comparison)

    assert brief.decision_status == "conditional"
    assert "closing_cash" in brief.recommendation.evidence_metric_ids
    assert "rescue_funding" in brief.recommendation.evidence_metric_ids
    assert any(
        constraint.metric_name == "closing_cash" for constraint in brief.constraints
    )


def test_worse_rescue_mean_is_visible_when_breach_probabilities_are_saturated() -> None:
    comparison = _rescue_comparison(
        baseline_investment_cents=50_000_000,
        candidate_investment_cents=100_000_000,
    )

    brief = build_executive_brief(comparison)
    rescue = comparison.metrics["rescue_funding"]

    assert rescue.baseline_breach_probability == 1.0
    assert rescue.candidate_breach_probability == 1.0
    assert rescue.mean_difference > rescue.materiality_threshold
    assert brief.decision_status == "conditional"
    assert any(
        constraint.metric_name == "rescue_funding"
        for constraint in brief.constraints
    )


def test_persistent_absolute_breach_remains_an_explicit_constraint() -> None:
    comparison = _rescue_comparison(
        baseline_investment_cents=50_000_000,
        candidate_investment_cents=50_000_000,
    )

    brief = build_executive_brief(comparison)
    constrained_metrics = {
        constraint.metric_name for constraint in brief.constraints
    }

    assert comparison.metrics["closing_cash"].baseline_breach_probability == 1.0
    assert comparison.metrics["closing_cash"].candidate_breach_probability == 1.0
    assert brief.decision_status == "conditional"
    assert {"closing_cash", "rescue_funding"} <= constrained_metrics


def test_brief_is_deterministic_and_collections_are_immutable() -> None:
    comparison = _comparison()

    first = build_executive_brief(comparison)
    second = build_executive_brief(comparison)

    assert first.decision_status == second.decision_status
    assert first.recommendation == second.recommendation
    assert first.outcome_deltas == second.outcome_deltas
    assert first.mechanisms == second.mechanisms
    assert first.constraints == second.constraints
    assert first.governance == second.governance
    assert first.actions == second.actions
    assert isinstance(first.outcome_deltas, tuple)
    assert isinstance(first.mechanisms, tuple)
    immutable_outcomes = cast(list[object], first.outcome_deltas)
    with pytest.raises(TypeError):
        immutable_outcomes[0] = object()


def test_brief_validation_rejects_unsupported_evidence_after_rehash() -> None:
    comparison = _comparison()
    brief = build_executive_brief(comparison)
    recommendation = brief.recommendation.model_copy(
        update={"evidence_metric_ids": ("not_a_metric",)}
    )
    tampered = brief.model_copy(
        update={"recommendation": recommendation, "digest": "0" * 64}
    )
    tampered = tampered.model_copy(
        update={"digest": brief_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation, match="brief_evidence"):
        validate_executive_brief(tampered, comparison)


def test_brief_validation_rejects_unsupported_narrative_after_rehash() -> None:
    comparison = _comparison()
    brief = build_executive_brief(comparison)
    recommendation = brief.recommendation.model_copy(
        update={"rationale": ("Unsupported claim.",)}
    )
    tampered = brief.model_copy(
        update={"recommendation": recommendation, "digest": "0" * 64}
    )
    tampered = tampered.model_copy(
        update={"digest": brief_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation, match="brief_evidence"):
        validate_executive_brief(tampered, comparison)


def test_brief_validation_rejects_tampered_execution_action_after_rehash() -> None:
    comparison = _comparison(liquidity_stress=True)
    brief = build_executive_brief(comparison)
    action = brief.actions[0].model_copy(update={"owner": "Unverified owner"})
    tampered = brief.model_copy(
        update={"actions": (action, *brief.actions[1:]), "digest": "0" * 64}
    )
    tampered = tampered.model_copy(
        update={"digest": brief_content_digest(tampered)}
    )

    with pytest.raises(InvariantViolation, match="brief_actions"):
        validate_executive_brief(tampered, comparison)
