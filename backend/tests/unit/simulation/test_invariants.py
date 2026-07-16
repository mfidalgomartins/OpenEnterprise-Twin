import pytest

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.results import trace_content_digest
from openenterprise_twin.simulation.engine import simulate_trace
from openenterprise_twin.simulation.invariants import validate_period, validate_trace
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)
from openenterprise_twin.simulation.shocks import build_shock_tape


def test_reference_trace_passes_all_invariants() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=45)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=9)
    trace = simulate_trace(company, scenario, tape)

    validate_trace(trace)


def test_cash_reconciles_to_cent() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=15)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=2)
    trace = simulate_trace(company, scenario, tape)

    for period in trace.periods:
        expected_cash = (
            period.opening_cash_cents
            + period.collections_cents
            + period.rescue_funding_cents
            + period.revolver_draw_cents
            - period.supplier_payments_cents
            - period.conversion_cost_cents
            - period.overtime_cost_cents
            - period.fixed_cost_cents
            - period.interest_paid_cents
            - period.capital_investment_cents
            - period.revolver_repayment_cents
        )
        assert period.closing_cash_cents == expected_cash


def test_validate_period_rejects_broken_flow() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=2)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=1)
    period = simulate_trace(company, scenario, tape).periods[0]
    broken_period = period.model_copy(
        update={
            "closing_finished_goods_units": {
                **period.closing_finished_goods_units,
                "standard-valve": period.closing_finished_goods_units[
                    "standard-valve"
                ]
                + 1,
            }
        }
    )

    with pytest.raises(InvariantViolation, match="finished_goods_conservation"):
        validate_period(broken_period)


def test_validate_period_rejects_broken_wip_flow() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=5)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=22)
    period = simulate_trace(company, scenario, tape).periods[0]
    broken_period = period.model_copy(
        update={
            "closing_wip_units": {
                **period.closing_wip_units,
                "standard-valve": period.closing_wip_units["standard-valve"] + 1,
            }
        }
    )

    with pytest.raises(InvariantViolation, match="wip_conservation"):
        validate_period(broken_period)


def test_validate_trace_rejects_broken_debt_continuity() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=5)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=23)
    trace = simulate_trace(company, scenario, tape)
    periods = list(trace.periods)
    periods[1] = periods[1].model_copy(
        update={
            "opening_revolver_debt_cents": (
                periods[1].opening_revolver_debt_cents + 1
            ),
            "closing_revolver_debt_cents": (
                periods[1].closing_revolver_debt_cents + 1
            ),
        }
    )
    broken_trace = trace.model_copy(update={"periods": tuple(periods)})
    broken_trace = broken_trace.model_copy(
        update={"digest": trace_content_digest(broken_trace)}
    )

    with pytest.raises(InvariantViolation, match="debt state is discontinuous"):
        validate_trace(broken_trace)


def test_validate_trace_rejects_tampered_provenance() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=5)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=24)
    trace = simulate_trace(company, scenario, tape)
    tampered_trace = trace.model_copy(update={"engine_version": "0.1.1"})

    with pytest.raises(InvariantViolation, match="trace_digest"):
        validate_trace(tampered_trace)
