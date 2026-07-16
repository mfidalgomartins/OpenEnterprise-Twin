from decimal import Decimal

from openenterprise_twin.domain.scenario import PolicyLevers, ResourcePolicyChange
from openenterprise_twin.simulation.demand import (
    binomial_quantile,
    expected_daily_units,
    negative_binomial_quantile,
    seasonality_multiplier,
)
from openenterprise_twin.simulation.engine import (
    _cancel_overdue_orders,
    _Order,
    _ship_orders,
    simulate_trace,
)
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


def test_engine_consumes_negative_binomial_arrival_draws() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=2)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=732)
    shock = tape.days[0]

    trace = simulate_trace(company, scenario, tape)

    for product in company.products:
        expected_units = 0
        for profile in product.demand_profiles:
            segment = next(
                item
                for item in company.customer_segments
                if item.segment_id == profile.segment_id
            )
            conditional_units = expected_daily_units(
                baseline_units=Decimal(profile.daily_baseline_units),
                price_change=Decimal("0"),
                elasticity=profile.price_elasticity,
                demand_multiplier=Decimal(
                    str(
                        shock.demand_multiplier(product.product_id, segment.segment_id)
                        * seasonality_multiplier(
                            0, float(profile.seasonality_amplitude)
                        )
                    )
                ),
            )
            order_count = negative_binomial_quantile(
                mean=float(conditional_units / Decimal(segment.mean_order_size)),
                dispersion=float(segment.order_dispersion),
                uniform=shock.arrival_inverse_cdf_uniform(
                    product.product_id, segment.segment_id
                ),
            )
            expected_units += order_count * segment.mean_order_size
        assert trace.periods[0].new_orders_units[product.product_id] == expected_units


def test_overdue_cancellation_uses_conditional_binomial_draw() -> None:
    company = build_northstar_company()
    segment = company.customer_segments[0]
    scenario = build_baseline_scenario(horizon_days=2)
    shock = build_shock_tape(
        company, scenario, seed=20260716, replication_id=733
    ).days[1]
    order = _Order(
        order_id=1,
        product_id="standard-valve",
        segment_id=segment.segment_id,
        order_day=0,
        due_day=0,
        grace_days=0,
        original_units=100,
        open_units=100,
        unit_price_cents=10_000,
        order_count=10,
        unit_size=10,
        origin_phase="evaluation",
    )
    cancellations = {product.product_id: 0 for product in company.products}
    cancelled_orders = {product.product_id: 0 for product in company.products}
    cancelled_evaluation_orders = {
        product.product_id: 0 for product in company.products
    }

    _cancel_overdue_orders(
        orders=[order],
        cancellations=cancellations,
        cancelled_orders_count=cancelled_orders,
        cancelled_evaluation_orders_count=cancelled_evaluation_orders,
        segments={segment.segment_id: segment},
        shock=shock,
        day_index=1,
    )

    expected = binomial_quantile(
        10,
        float(segment.cancellation_probability),
        shock.cancellation_binomial_uniform("standard-valve", segment.segment_id),
    )
    assert cancellations["standard-valve"] == expected * order.unit_size
    assert cancelled_orders["standard-valve"] == expected
    assert cancelled_evaluation_orders["standard-valve"] == expected
    assert order.open_units == 100 - expected * order.unit_size


def test_partial_cancellation_cannot_be_reported_as_fulfilled_or_otif() -> None:
    company = build_northstar_company()
    product = company.products[0]
    segment = company.customer_segments[0].model_copy(
        update={"payment_terms_days": 0}
    )
    scenario = build_baseline_scenario(horizon_days=2)
    base_shock = build_shock_tape(
        company, scenario, seed=20260716, replication_id=734
    ).days[0]
    collection_entries = tuple(
        (segment_id, 0.0 if segment_id == segment.segment_id else uniform)
        for segment_id, uniform in base_shock.collection_delay_uniform_entries
    )
    shock = base_shock.model_copy(
        update={"collection_delay_uniform_entries": collection_entries}
    )
    order = _Order(
        order_id=1,
        product_id=product.product_id,
        segment_id=segment.segment_id,
        order_day=0,
        due_day=0,
        grace_days=0,
        original_units=100,
        open_units=80,
        unit_price_cents=10_000,
        order_count=10,
        unit_size=10,
        origin_phase="evaluation",
        cancelled_order_count=2,
    )
    fulfilled = {product.product_id: 0}
    otif = {product.product_id: 0}
    fulfilled_evaluation = {product.product_id: 0}
    otif_evaluation = {product.product_id: 0}
    on_time_units = {product.product_id: 0}
    receivables: dict[int, int] = {}

    _ship_orders(
        orders=[order],
        finished_goods={product.product_id: 80},
        shipments={product.product_id: 0},
        products={product.product_id: product},
        segments={segment.segment_id: segment},
        scenario=scenario,
        day_index=0,
        receivables=receivables,
        shock=shock,
        fulfilled_orders_count=fulfilled,
        otif_orders_count=otif,
        fulfilled_evaluation_orders_count=fulfilled_evaluation,
        otif_evaluation_orders_count=otif_evaluation,
        on_time_shipment_units=on_time_units,
    )

    assert fulfilled[product.product_id] == 8
    assert otif[product.product_id] == 8
    assert fulfilled_evaluation[product.product_id] == 8
    assert otif_evaluation[product.product_id] == 8
    assert receivables == {1: 800_000}


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
    assert [scenario.warmup_days, scenario.evaluation_days, scenario.runoff_days] == [
        91,
        364,
        60,
    ]
    assert all(
        sum(period.new_orders_units.values()) == 0
        for period in trace.periods[-scenario.runoff_days :]
    )
    assert {period.phase for period in trace.periods[: scenario.warmup_days]} == {
        "warmup"
    }
    assert {
        period.phase
        for period in trace.periods[
            scenario.warmup_days : scenario.warmup_days + scenario.evaluation_days
        ]
    } == {"evaluation"}
    assert {period.phase for period in trace.periods[-scenario.runoff_days :]} == {
        "runoff"
    }
    created_evaluation_orders = sum(
        sum(period.new_orders_count.values())
        for period in trace.periods
        if period.phase == "evaluation"
    )
    resolved_evaluation_orders = sum(
        sum(period.fulfilled_evaluation_orders_count.values())
        + sum(period.cancelled_evaluation_orders_count.values())
        for period in trace.periods
    )
    open_evaluation_orders = sum(
        trace.periods[-1].closing_evaluation_backlog_orders_count.values()
    )
    assert created_evaluation_orders == (
        resolved_evaluation_orders + open_evaluation_orders
    )
    assert all(
        period.closing_material_inventory_units["steel"] > 0
        for period in trace.periods
    )
    assert (
        trace.periods[-1].closing_revolver_debt_cents
        <= company.financial_policy.revolver_limit_cents
    )
    assert all(
        period.closing_cash_cents >= company.financial_policy.liquidity_floor_cents
        for period in trace.periods
    )
    assert trace.engine_version == "0.1.0"
    assert trace.scenario_schema_version == scenario.schema_version
    assert trace.shock_tape_version == tape.tape_version
    assert len(trace.resolved_assumptions_hash) == 64
    assert len(trace.shock_tape_digest) == 64


def test_trace_records_stochastic_commercial_outcomes() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=90)
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=18)

    trace = simulate_trace(company, scenario, tape)

    order_totals = [sum(period.new_orders_units.values()) for period in trace.periods]
    assert len(set(order_totals)) > 1
    assert all(
        0 <= factor <= 1
        for period in trace.periods
        for factor in period.retention_factor_by_segment.values()
    )
    assert any(
        sum(period.lost_demand_units.values()) > 0 for period in trace.periods
    )


def test_overtime_cost_is_charged_to_the_cash_ledger() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=10).model_copy(
        update={
            "policy_levers": PolicyLevers(
                resource_changes=tuple(
                    ResourcePolicyChange(
                        resource_id=resource.resource_id,
                        regular_capacity_change=Decimal("-0.95"),
                        overtime_capacity_minutes=resource.max_overtime_minutes,
                    )
                    for resource in company.plant.resources
                )
            )
        }
    )
    tape = build_shock_tape(company, scenario, seed=20260716, replication_id=43)

    trace = simulate_trace(company, scenario, tape)

    resources = {
        resource.resource_id: resource for resource in company.plant.resources
    }
    assert any(
        sum(period.overtime_used_minutes.values()) > 0 for period in trace.periods
    )
    for period in trace.periods:
        assert period.overtime_cost_cents == sum(
            minutes * resources[resource_id].overtime_cost_cents_per_minute
            for resource_id, minutes in period.overtime_used_minutes.items()
        )
