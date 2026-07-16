import pytest

from openenterprise_twin.domain.errors import InvariantViolation
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
            + period.revolver_draw_cents
            - period.supplier_payments_cents
            - period.conversion_cost_cents
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
