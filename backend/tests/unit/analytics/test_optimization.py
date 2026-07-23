from collections.abc import Callable

import pytest
from pydantic import ValidationError
from tests.factories import build_northstar_company

from openenterprise_twin.analytics.optimization import (
    CandidateEvaluation,
    ConstraintSpec,
    LeverSpec,
    ObjectiveSpec,
    OptimizationConfig,
    decode_levers,
    optimize_policies,
)
from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.simulation.experiment import METRIC_NAMES
from openenterprise_twin.simulation.reference import build_baseline_scenario

Evaluator = Callable[[PolicyLevers], CandidateEvaluation]


@pytest.fixture
def company() -> CompanyModel:
    return build_northstar_company()


def _tradeoff_evaluator() -> Evaluator:
    # EBITDA falls and OTIF rises with commercial investment -> genuine trade-off.
    def _evaluate(levers: PolicyLevers) -> CandidateEvaluation:
        x = float(levers.commercial_investment_change)
        means = {name: 0.0 for name in METRIC_NAMES}
        means["ebitda"] = 1e7 * (1.0 - 0.6 * x)
        means["otif"] = min(0.99, 0.90 + 0.15 * x)
        means["closing_cash"] = 1e7
        return CandidateEvaluation(
            means=means,
            breach_probability={name: 0.0 for name in METRIC_NAMES},
        )

    return _evaluate


def _config(**overrides: object) -> OptimizationConfig:
    base: dict[str, object] = {
        "objectives": (
            ObjectiveSpec(metric_name="ebitda", direction="maximize"),
            ObjectiveSpec(metric_name="otif", direction="maximize"),
        ),
        "levers": (
            LeverSpec(
                lever_id="ci",
                kind="commercial_investment",
                lower=-0.2,
                upper=0.5,
            ),
        ),
        "population_size": 16,
        "max_generations": 10,
        "max_evaluations": 200,
        "seed": 3,
    }
    base.update(overrides)
    return OptimizationConfig.model_validate(base)


def _run(company: CompanyModel, config: OptimizationConfig, evaluator: Evaluator):
    return optimize_policies(
        config=config,
        evaluator=evaluator,
        company=company,
        base_scenario=build_baseline_scenario(horizon_days=120),
    )


def test_config_rejects_duplicate_levers() -> None:
    with pytest.raises(ValidationError):
        OptimizationConfig(
            objectives=(ObjectiveSpec(metric_name="ebitda", direction="maximize"),),
            levers=(
                LeverSpec(
                    lever_id="a", kind="commercial_investment", lower=0.0, upper=1.0
                ),
                LeverSpec(
                    lever_id="a", kind="commercial_investment", lower=0.0, upper=1.0
                ),
            ),
        )


def test_config_requires_even_population() -> None:
    with pytest.raises(ValidationError):
        _config(population_size=15)


def test_finds_multi_point_pareto_frontier(company: CompanyModel) -> None:
    result = _run(company, _config(), _tradeoff_evaluator())
    assert len(result.frontier) >= 3
    xs = sorted(
        float(c.levers.commercial_investment_change) for c in result.frontier
    )
    # The frontier should span the lever range for a real trade-off.
    assert xs[0] < 0.0 < xs[-1]


def test_frontier_is_non_dominated(company: CompanyModel) -> None:
    result = _run(company, _config(), _tradeoff_evaluator())
    points = [
        (c.objective_values["ebitda"], c.objective_values["otif"])
        for c in result.frontier
    ]
    for i, (e_i, o_i) in enumerate(points):
        for j, (e_j, o_j) in enumerate(points):
            if i == j:
                continue
            dominates = e_j >= e_i and o_j >= o_i and (e_j > e_i or o_j > o_i)
            assert not dominates, "frontier contains a dominated point"


def test_is_deterministic(company: CompanyModel) -> None:
    first = _run(company, _config(), _tradeoff_evaluator())
    second = _run(company, _config(), _tradeoff_evaluator())
    assert first.digest == second.digest


def test_respects_evaluation_budget(company: CompanyModel) -> None:
    config = _config(max_evaluations=40, max_generations=50)
    result = _run(company, config, _tradeoff_evaluator())
    # Sensitivity adds a bounded number of extra evaluations after the search.
    assert result.evaluations <= 40 + 2 * len(config.levers) + 2


def test_hard_constraint_excludes_infeasible(company: CompanyModel) -> None:
    # Require OTIF >= 0.98, only reachable near the top of the lever range.
    config = _config(
        constraints=(
            ConstraintSpec(
                metric_name="otif", operator="gte", bound=0.98, kind="hard"
            ),
        ),
    )
    result = _run(company, config, _tradeoff_evaluator())
    for candidate in result.frontier:
        assert candidate.feasible
        assert candidate.objective_values["otif"] >= 0.98
    for candidate in result.infeasible:
        assert candidate.exclusion_reason is not None
        assert "otif" in candidate.exclusion_reason


def test_reports_convergence_and_sensitivity(company: CompanyModel) -> None:
    result = _run(company, _config(), _tradeoff_evaluator())
    assert result.convergence
    assert result.recommended is not None
    assert result.sensitivity
    assert result.sensitivity[0].lever_id == "ci"


def test_decode_levers_maps_all_kinds(company: CompanyModel) -> None:
    scenario = build_baseline_scenario(horizon_days=60)
    config = OptimizationConfig(
        objectives=(ObjectiveSpec(metric_name="ebitda", direction="maximize"),),
        levers=(
            LeverSpec(
                lever_id="ci", kind="commercial_investment", lower=-0.1, upper=0.3
            ),
            LeverSpec(
                lever_id="price",
                kind="price",
                target_id="standard-valve",
                lower=-0.05,
                upper=0.1,
            ),
            LeverSpec(
                lever_id="ot",
                kind="overtime",
                target_id="assembly",
                lower=0.0,
                upper=400.0,
            ),
            LeverSpec(
                lever_id="terms",
                kind="payment_terms",
                target_id="spot",
                lower=-5.0,
                upper=5.0,
            ),
        ),
    )
    levers = decode_levers(config, company, scenario, (1.0, 1.0, 1.0, 0.5))
    assert float(levers.commercial_investment_change) == pytest.approx(0.3)
    assert levers.price_changes
    assert levers.resource_changes[0].overtime_capacity_minutes == 400
    assert levers.payment_term_changes[0].change_days == 0
