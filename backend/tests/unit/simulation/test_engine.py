from decimal import Decimal

from openenterprise_twin.simulation.demand import expected_daily_units
from openenterprise_twin.simulation.engine import simulate_trace
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)
from openenterprise_twin.simulation.shocks import build_shock_tape


def test_reference_company_represents_cross_functional_system() -> None:
    company = build_northstar_company()

    assert len(company.products) == 3
    assert {resource.resource_id for resource in company.plant.resources} == {
        "assembly",
        "test",
    }
    assert {material.material_id for material in company.plant.materials} == {
        "electronics",
        "steel",
    }


def test_price_elasticity_changes_expected_demand() -> None:
    baseline = expected_daily_units(
        baseline_units=Decimal("100"),
        price_change=Decimal("0"),
        elasticity=Decimal("-1.4"),
        demand_multiplier=Decimal("1"),
    )
    increased_price = expected_daily_units(
        baseline_units=Decimal("100"),
        price_change=Decimal("0.01"),
        elasticity=Decimal("-1.4"),
        demand_multiplier=Decimal("1"),
    )

    assert baseline == Decimal("100")
    assert Decimal("98.5") < increased_price < Decimal("98.7")


def test_same_seed_produces_identical_trace() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=30)
    first_tape = build_shock_tape(
        company, scenario, seed=20260716, replication_id=731
    )
    second_tape = build_shock_tape(
        company, scenario, seed=20260716, replication_id=731
    )

    first_trace = simulate_trace(company, scenario, first_tape)
    second_trace = simulate_trace(company, scenario, second_tape)

    assert first_tape == second_tape
    assert first_trace == second_trace
    assert first_trace.digest == second_trace.digest
    assert len(first_trace.periods) == 30


def test_shipment_flow_is_conserved() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=20)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=4)
    trace = simulate_trace(company, scenario, tape)

    for period in trace.periods:
        for product_id in period.closing_finished_goods_units:
            assert (
                period.opening_finished_goods_units[product_id]
                + period.good_production_units[product_id]
                == period.closing_finished_goods_units[product_id]
                + period.shipments_units[product_id]
            )
            assert (
                period.opening_backlog_units[product_id]
                + period.new_orders_units[product_id]
                == period.closing_backlog_units[product_id]
                + period.shipments_units[product_id]
                + period.cancellations_units[product_id]
            )


def test_full_reference_horizon_remains_financially_and_operationally_viable() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario()
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=0)

    trace = simulate_trace(company, scenario, tape)

    assert len(trace.periods) == 515
    assert all(
        period.closing_material_inventory_units["steel"] > 0
        for period in trace.periods
    )
    assert (
        trace.periods[-1].closing_revolver_debt_cents
        <= company.financial_policy.revolver_limit_cents
    )
