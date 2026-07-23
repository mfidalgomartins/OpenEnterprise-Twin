from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from openenterprise_twin.application.portfolio import (
    DecisionSummary,
    PortfolioMetric,
    build_policy_frontier,
    list_decision_portfolio,
)
from openenterprise_twin.application.ports import (
    ArtifactReader,
    CompletedCandidateRecord,
    DecisionEvidenceRepository,
)
from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.reporting.brief import build_executive_brief
from openenterprise_twin.scenarios.comparison import compare_experiments
from openenterprise_twin.simulation.experiment import ExperimentRequest, run_experiment
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
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


def test_portfolio_uses_bulk_loaded_persisted_evidence() -> None:
    company = build_northstar_company()
    baseline_scenario = build_baseline_scenario(horizon_days=3)
    candidate_scenario = baseline_scenario.model_copy(
        update={
            "scenario_id": "cached-decision",
            "name": "Cached decision",
            "baseline_scenario_id": baseline_scenario.scenario_id,
            "policy_levers": PolicyLevers(
                commercial_investment_change=Decimal("0.05")
            ),
        }
    )
    baseline = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=baseline_scenario,
            master_seed=731,
            replications=1,
            max_workers=1,
        )
    )
    candidate = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=candidate_scenario,
            master_seed=731,
            replications=1,
            max_workers=1,
        )
    )
    comparison = compare_experiments(baseline, candidate)
    brief = build_executive_brief(comparison)
    record = cast(
        CompletedCandidateRecord,
        SimpleNamespace(
            id=7,
            scenario_id=candidate_scenario.scenario_id,
            scenario_name=candidate_scenario.name,
            completed_at=datetime(2026, 7, 23, tzinfo=UTC),
            replication_count=1,
            comparison_payload=comparison.model_dump(mode="json"),
            brief_payload=brief.model_dump(mode="json"),
        ),
    )

    class CachedRepository:
        def list_completed_candidates(self, **_kwargs: object):
            return (record,)

        def get(self, _experiment_id: int):
            raise AssertionError("cached portfolio evidence must not be re-read")

    class UnusedArtifactStore:
        def get_json(self, _digest: str):
            raise AssertionError("cached portfolio evidence must not load artifacts")

    portfolio = list_decision_portfolio(
        repository=cast(DecisionEvidenceRepository, CachedRepository()),
        artifact_store=cast(ArtifactReader, UnusedArtifactStore()),
        limit=20,
    )

    assert [item.experiment_id for item in portfolio.items] == [7]
    assert portfolio.items[0].comparison_digest == comparison.digest
    assert portfolio.items[0].brief_digest == brief.digest
