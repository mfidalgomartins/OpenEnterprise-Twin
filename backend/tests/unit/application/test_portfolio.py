from datetime import UTC, datetime

from openenterprise_twin.application.portfolio import (
    DecisionSummary,
    PortfolioMetric,
    build_policy_frontier,
)


def _summary(
    experiment_id: int,
    *,
    ebitda: float,
    free_cash_flow: float,
    otif: float,
    decision_status: str = "conditional",
    evidence_grade: str = "decision_grade",
) -> DecisionSummary:
    return DecisionSummary(
        experiment_id=experiment_id,
        scenario_id=f"scenario-{experiment_id}",
        scenario_name=f"Scenario {experiment_id}",
        completed_at=datetime(2026, 7, 18, tzinfo=UTC),
        replication_count=30,
        decision_status=decision_status,
        evidence_grade=evidence_grade,
        headline=f"Decision {experiment_id}",
        hard_constraint_count=0,
        metrics=(
            PortfolioMetric(
                metric_name="ebitda",
                baseline_mean=0.0,
                candidate_mean=ebitda,
                mean_difference=ebitda,
                candidate_breach_probability=0.0,
            ),
            PortfolioMetric(
                metric_name="free_cash_flow",
                baseline_mean=0.0,
                candidate_mean=free_cash_flow,
                mean_difference=free_cash_flow,
                candidate_breach_probability=0.0,
            ),
            PortfolioMetric(
                metric_name="otif",
                baseline_mean=0.95,
                candidate_mean=0.95 + otif,
                mean_difference=otif,
                candidate_breach_probability=0.0,
            ),
        ),
        comparison_digest="a" * 64,
        brief_digest="b" * 64,
    )


def test_policy_frontier_is_feasible_and_pareto_efficient() -> None:
    dominant = _summary(1, ebitda=10.0, free_cash_flow=5.0, otif=0.01)
    dominated = _summary(2, ebitda=8.0, free_cash_flow=4.0, otif=0.0)
    tradeoff = _summary(3, ebitda=7.0, free_cash_flow=8.0, otif=0.02)
    infeasible = _summary(
        4,
        ebitda=100.0,
        free_cash_flow=100.0,
        otif=0.10,
        decision_status="do_not_adopt",
    )
    exploratory = _summary(
        5,
        ebitda=100.0,
        free_cash_flow=100.0,
        otif=0.10,
        evidence_grade="exploratory",
    )

    frontier = build_policy_frontier(
        (dominant, dominated, tradeoff, infeasible, exploratory)
    )

    assert [point.experiment_id for point in frontier.points] == [1, 3]
    assert frontier.eligible_count == 3
    assert frontier.dominated_count == 1
    assert frontier.excluded_count == 2
    assert all(point.decision_status != "do_not_adopt" for point in frontier.points)


def test_policy_frontier_is_deterministic_regardless_of_input_order() -> None:
    items = (
        _summary(7, ebitda=4.0, free_cash_flow=9.0, otif=0.01),
        _summary(6, ebitda=9.0, free_cash_flow=4.0, otif=0.02),
    )

    assert build_policy_frontier(items) == build_policy_frontier(tuple(reversed(items)))
