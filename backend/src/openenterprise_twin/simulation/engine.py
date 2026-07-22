"""Daily enterprise state transition conditioned on an immutable shock tape."""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256

from openenterprise_twin.domain.company import (
    CompanyModel,
    CustomerSegment,
    MaterialPolicy,
    Product,
)
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.results import (
    PeriodResult,
    Phase,
    SimulationTrace,
    trace_content_digest,
)
from openenterprise_twin.domain.scenario import (
    Scenario,
    validate_scenario_against_company,
)
from openenterprise_twin.simulation.demand import (
    binomial_quantile,
    collection_delay_days,
    expected_daily_units,
    negative_binomial_quantile,
    seasonality_multiplier,
)
from openenterprise_twin.simulation.finance import apply_financing
from openenterprise_twin.simulation.invariants import validate_period, validate_trace
from openenterprise_twin.simulation.operations import (
    material_inventory_levels,
    plan_production,
    validate_production_plan,
)
from openenterprise_twin.simulation.shocks import (
    RNG_ALGORITHM,
    TAPE_VERSION,
    DailyShock,
    ShockTape,
    demand_key,
)

START_DATE = date(2025, 1, 1)
ENGINE_VERSION = "0.2.0"


@dataclass(slots=True)
class _Order:
    order_id: int
    product_id: str
    segment_id: str
    order_day: int
    due_day: int
    grace_days: int
    original_units: int
    open_units: int
    unit_price_cents: int
    order_count: int
    unit_size: int
    origin_phase: Phase
    shipped_units: int = 0
    fulfilled_order_count: int = 0
    cancelled_order_count: int = 0
    all_shipments_on_time: bool = True


@dataclass(frozen=True, slots=True)
class _WorkOrder:
    product_id: str
    due_day: int
    started_units: int


@dataclass(frozen=True, slots=True)
class _PurchaseOrder:
    material_id: str
    due_day: int
    quantity: int
    unit_cost_milli_cents: int
    origin_phase: Phase


def simulate_trace(
    company: CompanyModel,
    scenario: Scenario,
    shock_tape: ShockTape,
    *,
    allow_rescue_funding: bool = False,
) -> SimulationTrace:
    """Simulate one auditable trace; randomness may enter only through the tape."""

    validate_scenario_against_company(scenario, company)
    _validate_tape(company, scenario, shock_tape)

    products = {product.product_id: product for product in company.products}
    segments = {
        segment.segment_id: segment for segment in company.customer_segments
    }
    materials = {
        material.material_id: material for material in company.plant.materials
    }
    finished_goods = {
        product.product_id: product.opening_finished_goods_units
        for product in company.products
    }
    material_inventory = {
        material.material_id: material.opening_inventory_base_units
        for material in company.plant.materials
    }
    orders: list[_Order] = []
    work_orders: list[_WorkOrder] = []
    purchase_orders: list[_PurchaseOrder] = []
    receivables: dict[int, dict[Phase, int]] = {}
    payables: dict[int, dict[Phase, int]] = {}
    cash_cents = company.financial_policy.opening_cash_cents
    debt_cents = 0
    next_order_id = 1
    retention_factors = {
        segment.segment_id: 1.0 for segment in company.customer_segments
    }
    periods: list[PeriodResult] = []

    for day_index, shock in enumerate(shock_tape.days):
        period_date = START_DATE + timedelta(days=day_index)
        phase = scenario.phase_for_day(day_index)
        is_operating_day = period_date.weekday() < 5
        opening_finished_goods = finished_goods.copy()
        opening_material_inventory = material_inventory.copy()
        opening_backlog = _backlog_by_product(orders, products)
        opening_wip = _work_in_progress_by_product(work_orders, products)
        opening_cash = cash_cents
        opening_debt = debt_cents

        good_production = {product_id: 0 for product_id in products}
        material_receipts = {material_id: 0 for material_id in materials}
        material_consumption = {material_id: 0 for material_id in materials}
        shipments = {product_id: 0 for product_id in products}
        new_orders = {product_id: 0 for product_id in products}
        lost_demand = {product_id: 0 for product_id in products}
        cancellations = {product_id: 0 for product_id in products}
        new_orders_count = {product_id: 0 for product_id in products}
        cancelled_orders_count = {product_id: 0 for product_id in products}
        fulfilled_orders_count = {product_id: 0 for product_id in products}
        otif_orders_count = {product_id: 0 for product_id in products}
        fulfilled_evaluation_orders_count = {
            product_id: 0 for product_id in products
        }
        otif_evaluation_orders_count = {product_id: 0 for product_id in products}
        cancelled_evaluation_orders_count = {
            product_id: 0 for product_id in products
        }
        on_time_shipment_units = {product_id: 0 for product_id in products}

        # Supplier receipts precede production completions by contract.
        purchase_orders, received = _receive_purchase_orders(
            purchase_orders, day_index
        )
        for purchase_order in received:
            material_inventory[purchase_order.material_id] += purchase_order.quantity
            material_receipts[purchase_order.material_id] += purchase_order.quantity
            material = materials[purchase_order.material_id]
            due_day = day_index + material.supplier_payment_terms_days
            invoice = (
                purchase_order.quantity * purchase_order.unit_cost_milli_cents
            ) // 1000
            _schedule_cash_flow(
                payables,
                due_day=due_day,
                origin_phase=purchase_order.origin_phase,
                amount_cents=invoice,
            )

        work_orders, completed_starts, completed_good = _complete_work_orders(
            work_orders, day_index, shock.yield_rate_by_product
        )
        production_scrap = {product_id: 0 for product_id in products}
        for product_id, units in completed_good.items():
            finished_goods[product_id] += units
            good_production[product_id] += units
            production_scrap[product_id] = completed_starts[product_id] - units

        (
            collections_cents,
            evaluation_origin_collections_cents,
        ) = _pop_scheduled_cash_flow(receivables, day_index)
        (
            supplier_payments_cents,
            evaluation_origin_supplier_payments_cents,
        ) = _pop_scheduled_cash_flow(payables, day_index)
        obligation_financing = apply_financing(
            cash_before_financing_cents=(
                cash_cents + collections_cents - supplier_payments_cents
            ),
            opening_debt_cents=debt_cents,
            policy=company.financial_policy,
            allow_repayment=False,
            allow_rescue_funding=allow_rescue_funding,
        )
        cash_cents = obligation_financing.closing_cash_cents
        debt_cents = obligation_financing.closing_debt_cents

        if is_operating_day and phase != "runoff":
            created_orders, next_order_id, lost_demand = _create_orders(
                company=company,
                scenario=scenario,
                shock=shock,
                day_index=day_index,
                next_order_id=next_order_id,
                retention_factors=retention_factors,
            )
            orders.extend(created_orders)
            for order in created_orders:
                new_orders[order.product_id] += order.original_units
                new_orders_count[order.product_id] += order.order_count

        revenue_cents, cogs_cents = _ship_orders(
            orders=orders,
            finished_goods=finished_goods,
            shipments=shipments,
            products=products,
            materials=materials,
            segments=segments,
            scenario=scenario,
            day_index=day_index,
            receivables=receivables,
            shock=shock,
            fulfilled_orders_count=fulfilled_orders_count,
            otif_orders_count=otif_orders_count,
            fulfilled_evaluation_orders_count=(
                fulfilled_evaluation_orders_count
            ),
            otif_evaluation_orders_count=otif_evaluation_orders_count,
            on_time_shipment_units=on_time_shipment_units,
        )
        _cancel_overdue_orders(
            orders=orders,
            cancellations=cancellations,
            cancelled_orders_count=cancelled_orders_count,
            cancelled_evaluation_orders_count=(
                cancelled_evaluation_orders_count
            ),
            segments=segments,
            shock=shock,
            day_index=day_index,
        )
        orders = [order for order in orders if order.open_units > 0]

        backlog = _backlog_by_product(orders, products)
        work_in_progress = _work_in_progress_by_product(work_orders, products)
        production_plan = plan_production(
            company=company,
            scenario=scenario,
            shock=shock,
            is_operating_day=is_operating_day,
            finished_goods=finished_goods,
            backlog=backlog,
            work_in_progress=work_in_progress,
            material_inventory=material_inventory,
        )
        validate_production_plan(
            company=company,
            plan=production_plan,
            material_inventory=material_inventory,
        )
        conversion_cost_cents = 0
        for product_id, starts in production_plan.starts_by_product.items():
            if starts == 0:
                continue
            product = products[product_id]
            due_day = _next_operating_day(
                day_index, product.production_lead_time_days
            )
            work_orders.append(
                _WorkOrder(
                    product_id=product_id,
                    due_day=due_day,
                    started_units=starts,
                )
            )
            conversion_cost_cents += starts * _conversion_cost_per_start(
                product, materials
            )
        for material_id, consumed in production_plan.material_consumption.items():
            material_inventory[material_id] -= consumed
            material_consumption[material_id] += consumed

        resources = {
            resource.resource_id: resource for resource in company.plant.resources
        }
        overtime_cost_cents = sum(
            minutes * resources[resource_id].overtime_cost_cents_per_minute
            for resource_id, minutes in production_plan.overtime_used.items()
        )
        production_scrap_cost_cents = sum(
            units
            * _effective_unit_cost_cents(
                products[product_id], materials, scenario
            )
            for product_id, units in production_scrap.items()
        )

        purchase_orders.extend(
            _place_purchase_orders(
                company=company,
                scenario=scenario,
                shock_delays=shock.supplier_delay_days_by_material,
                day_index=day_index,
                material_inventory=material_inventory,
                open_purchase_orders=purchase_orders,
            )
        )

        fixed_cost_cents = company.financial_policy.monthly_fixed_cost_cents * 12 // 365
        commercial_investment_change_cents = _commercial_investment_change_cents(
            company,
            scenario,
            is_operating_day=is_operating_day,
            phase=phase,
        )
        capacity_commitment_change_cents = _capacity_commitment_change_cents(
            company,
            scenario,
            is_operating_day=is_operating_day,
        )
        interest_paid_cents = _daily_interest_cents(
            debt_cents, company.financial_policy.annual_interest_rate
        )
        capital_investment_cents = (
            scenario.policy_levers.one_off_capital_investment_cents
            if day_index == 0
            else 0
        )
        cash_before_financing = (
            cash_cents
            - conversion_cost_cents
            - overtime_cost_cents
            - commercial_investment_change_cents
            - capacity_commitment_change_cents
            - fixed_cost_cents
            - interest_paid_cents
            - capital_investment_cents
        )
        financing = apply_financing(
            cash_before_financing_cents=cash_before_financing,
            opening_debt_cents=debt_cents,
            policy=company.financial_policy,
            allow_rescue_funding=allow_rescue_funding,
        )
        cash_cents = financing.closing_cash_cents
        debt_cents = financing.closing_debt_cents
        revolver_draw_cents = obligation_financing.draw_cents + financing.draw_cents
        revolver_repayment_cents = (
            obligation_financing.repayment_cents + financing.repayment_cents
        )
        rescue_funding_cents = (
            obligation_financing.rescue_funding_cents
            + financing.rescue_funding_cents
        )

        retention_factors = _update_retention_factors(
            company=company,
            orders=orders,
            day_index=day_index,
            current=retention_factors,
        )

        closing_wip = _work_in_progress_by_product(work_orders, products)

        period = PeriodResult(
            period_index=day_index,
            period_date=period_date,
            phase=phase,
            is_operating_day=is_operating_day,
            opening_finished_goods_units=opening_finished_goods,
            good_production_units=good_production,
            shipments_units=shipments,
            closing_finished_goods_units=finished_goods.copy(),
            opening_wip_units=opening_wip,
            production_start_units=production_plan.starts_by_product,
            completed_production_units=completed_starts,
            production_scrap_units=production_scrap,
            closing_wip_units=closing_wip,
            opening_backlog_units=opening_backlog,
            new_orders_units=new_orders,
            lost_demand_units=lost_demand,
            cancellations_units=cancellations,
            closing_backlog_units=_backlog_by_product(orders, products),
            new_orders_count=new_orders_count,
            cancelled_orders_count=cancelled_orders_count,
            fulfilled_orders_count=fulfilled_orders_count,
            otif_orders_count=otif_orders_count,
            fulfilled_evaluation_orders_count=(
                fulfilled_evaluation_orders_count
            ),
            otif_evaluation_orders_count=otif_evaluation_orders_count,
            cancelled_evaluation_orders_count=(
                cancelled_evaluation_orders_count
            ),
            closing_evaluation_backlog_orders_count=(
                _evaluation_backlog_orders_by_product(orders, products)
            ),
            on_time_shipment_units=on_time_shipment_units,
            retention_factor_by_segment=retention_factors.copy(),
            opening_material_inventory_units=opening_material_inventory,
            material_receipts_units=material_receipts,
            material_consumption_units=material_consumption,
            closing_material_inventory_units=material_inventory.copy(),
            capacity_available_minutes=production_plan.capacity_available,
            capacity_used_minutes=production_plan.capacity_used,
            overtime_used_minutes=production_plan.overtime_used,
            opening_cash_cents=opening_cash,
            collections_cents=collections_cents,
            evaluation_origin_collections_cents=(
                evaluation_origin_collections_cents
            ),
            supplier_payments_cents=supplier_payments_cents,
            evaluation_origin_supplier_payments_cents=(
                evaluation_origin_supplier_payments_cents
            ),
            closing_evaluation_receivables_cents=(
                _scheduled_phase_balance(receivables, "evaluation")
            ),
            closing_evaluation_payables_cents=(
                _scheduled_phase_balance(payables, "evaluation")
            ),
            conversion_cost_cents=conversion_cost_cents,
            overtime_cost_cents=overtime_cost_cents,
            commercial_investment_change_cents=(
                commercial_investment_change_cents
            ),
            capacity_commitment_change_cents=(
                capacity_commitment_change_cents
            ),
            production_scrap_cost_cents=production_scrap_cost_cents,
            fixed_cost_cents=fixed_cost_cents,
            interest_paid_cents=interest_paid_cents,
            capital_investment_cents=capital_investment_cents,
            rescue_funding_cents=rescue_funding_cents,
            revolver_draw_cents=revolver_draw_cents,
            revolver_repayment_cents=revolver_repayment_cents,
            closing_cash_cents=cash_cents,
            opening_revolver_debt_cents=opening_debt,
            closing_revolver_debt_cents=debt_cents,
            revenue_cents=revenue_cents,
            cogs_cents=cogs_cents,
        )
        validate_period(period)
        periods.append(period)

    resolved_assumptions_hash = _canonical_digest(
        {
            "company": company.model_dump(mode="json"),
            "scenario": scenario.model_dump(mode="json"),
        }
    )
    shock_tape_digest = _canonical_digest(shock_tape.model_dump(mode="json"))
    trace = SimulationTrace(
        company_model_version=company.model_version,
        scenario_schema_version=scenario.schema_version,
        engine_version=ENGINE_VERSION,
        shock_tape_version=shock_tape.tape_version,
        rescue_funding_enabled=allow_rescue_funding,
        scenario_id=scenario.scenario_id,
        seed=shock_tape.seed,
        replication_id=shock_tape.replication_id,
        rng_algorithm=shock_tape.rng_algorithm,
        resolved_assumptions_hash=resolved_assumptions_hash,
        shock_tape_digest=shock_tape_digest,
        periods=tuple(periods),
        digest="0" * 64,
    )
    trace = trace.model_copy(update={"digest": trace_content_digest(trace)})
    validate_trace(trace)
    return trace


def _validate_tape(
    company: CompanyModel, scenario: Scenario, tape: ShockTape
) -> None:
    if (
        tape.horizon_days != scenario.horizon_days
        or len(tape.days) != scenario.horizon_days
    ):
        raise DomainValidationError("shock tape horizon does not match scenario")
    if tape.tape_version != TAPE_VERSION or tape.rng_algorithm != RNG_ALGORITHM:
        raise DomainValidationError(
            "shock tape version or RNG algorithm is unsupported"
        )

    expected_demand_keys = {
        demand_key(product.product_id, profile.segment_id)
        for product in company.products
        for profile in product.demand_profiles
    }
    expected_products = {product.product_id for product in company.products}
    expected_segments = {
        segment.segment_id for segment in company.customer_segments
    }
    expected_resources = {
        resource.resource_id for resource in company.plant.resources
    }
    expected_materials = {
        material.material_id for material in company.plant.materials
    }
    for day_index, shock in enumerate(tape.days):
        dimensions = (
            {key for key, _ in shock.demand_multiplier_entries},
            {key for key, _ in shock.arrival_inverse_cdf_uniform_entries},
            {key for key, _ in shock.cancellation_binomial_uniform_entries},
        )
        if shock.day_index != day_index or any(
            keys != expected_demand_keys for keys in dimensions
        ):
            raise DomainValidationError("shock tape demand dimensions are invalid")
        actual_segments = {
            key for key, _ in shock.collection_delay_uniform_entries
        }
        if actual_segments != expected_segments:
            raise DomainValidationError("shock tape segment dimensions are invalid")
        if {key for key, _ in shock.capacity_factor_entries} != expected_resources:
            raise DomainValidationError("shock tape resource dimensions are invalid")
        if {key for key, _ in shock.yield_rate_entries} != expected_products:
            raise DomainValidationError("shock tape product dimensions are invalid")
        if {key for key, _ in shock.supplier_delay_days_entries} != expected_materials:
            raise DomainValidationError("shock tape material dimensions are invalid")


def _create_orders(
    *,
    company: CompanyModel,
    scenario: Scenario,
    shock: DailyShock,
    day_index: int,
    next_order_id: int,
    retention_factors: dict[str, float],
) -> tuple[list[_Order], int, dict[str, int]]:
    segments = {
        segment.segment_id: segment for segment in company.customer_segments
    }
    price_changes = {
        (change.product_id, change.segment_id): change.price_change
        for change in scenario.policy_levers.price_changes
    }
    created: list[_Order] = []
    lost_demand = {product.product_id: 0 for product in company.products}
    for product in sorted(company.products, key=lambda item: item.product_id):
        for profile in sorted(
            product.demand_profiles, key=lambda item: item.segment_id
        ):
            segment = segments[profile.segment_id]
            price_change = price_changes.get(
                (product.product_id, segment.segment_id), Decimal("0")
            )
            commercial_factor = Decimal("1") + (
                scenario.policy_levers.commercial_investment_change
                * profile.commercial_investment_sensitivity
            )
            demand_without_retention = expected_daily_units(
                baseline_units=Decimal(profile.daily_baseline_units),
                price_change=price_change,
                elasticity=profile.price_elasticity,
                demand_multiplier=Decimal(
                    str(
                        shock.demand_multiplier(
                            product.product_id, segment.segment_id
                        )
                        * seasonality_multiplier(
                            day_index, float(profile.seasonality_amplitude)
                        )
                    )
                )
                * commercial_factor,
            )
            expected = demand_without_retention * Decimal(
                str(retention_factors[segment.segment_id])
            )
            lost_demand[product.product_id] += max(
                0,
                int(
                    (demand_without_retention - expected).to_integral_value(
                        rounding=ROUND_HALF_UP
                    )
                ),
            )
            order_count = negative_binomial_quantile(
                mean=float(expected / Decimal(segment.mean_order_size)),
                dispersion=float(segment.order_dispersion),
                uniform=shock.arrival_inverse_cdf_uniform(
                    product.product_id, segment.segment_id
                ),
            )
            if order_count == 0:
                continue
            units = order_count * segment.mean_order_size
            price = (
                Decimal(product.standard_price_cents)
                * (Decimal("1") - segment.discount_rate)
                * (Decimal("1") + price_change)
            )
            unit_price_cents = int(price.to_integral_value(rounding=ROUND_HALF_UP))
            created.append(
                _Order(
                    order_id=next_order_id,
                    product_id=product.product_id,
                    segment_id=segment.segment_id,
                    order_day=day_index,
                    due_day=day_index + segment.promised_lead_time_days,
                    grace_days=segment.cancellation_grace_days,
                    original_units=units,
                    open_units=units,
                    unit_price_cents=unit_price_cents,
                    order_count=order_count,
                    unit_size=segment.mean_order_size,
                    origin_phase=scenario.phase_for_day(day_index),
                )
            )
            next_order_id += 1
    return created, next_order_id, lost_demand


def _ship_orders(
    *,
    orders: list[_Order],
    finished_goods: dict[str, int],
    shipments: dict[str, int],
    products: dict[str, Product],
    materials: dict[str, MaterialPolicy],
    segments: dict[str, CustomerSegment],
    scenario: Scenario,
    day_index: int,
    receivables: dict[int, dict[Phase, int]],
    shock: DailyShock,
    fulfilled_orders_count: dict[str, int],
    otif_orders_count: dict[str, int],
    fulfilled_evaluation_orders_count: dict[str, int],
    otif_evaluation_orders_count: dict[str, int],
    on_time_shipment_units: dict[str, int],
) -> tuple[int, int]:
    payment_changes = {
        change.segment_id: change.change_days
        for change in scenario.policy_levers.payment_term_changes
    }
    revenue = 0
    cogs = 0
    for order in sorted(orders, key=lambda item: (item.due_day, item.order_id)):
        shipped = min(order.open_units, finished_goods[order.product_id])
        if shipped == 0:
            continue
        order.open_units -= shipped
        fulfilled_before = order.fulfilled_order_count
        order.shipped_units += shipped
        if day_index > order.due_day:
            order.all_shipments_on_time = False
        else:
            on_time_shipment_units[order.product_id] += shipped
        finished_goods[order.product_id] -= shipped
        shipments[order.product_id] += shipped
        invoice = shipped * order.unit_price_cents
        revenue += invoice
        cogs += shipped * _effective_unit_cost_cents(
            products[order.product_id], materials, scenario
        )
        segment = segments[order.segment_id]
        due_day = (
            day_index
            + segment.payment_terms_days
            + payment_changes.get(order.segment_id, 0)
            + collection_delay_days(
                shock.collection_delay_uniform(order.segment_id)
            )
        )
        # Same-day collections have already run in the fixed chronology.
        due_day = max(day_index + 1, due_day)
        _schedule_cash_flow(
            receivables,
            due_day=due_day,
            origin_phase=order.origin_phase,
            amount_cents=invoice,
        )
        eligible_orders = order.order_count - order.cancelled_order_count
        order.fulfilled_order_count = min(
            eligible_orders, order.shipped_units // order.unit_size
        )
        newly_fulfilled = order.fulfilled_order_count - fulfilled_before
        fulfilled_orders_count[order.product_id] += newly_fulfilled
        if order.origin_phase == "evaluation":
            fulfilled_evaluation_orders_count[order.product_id] += newly_fulfilled
        if order.all_shipments_on_time:
            otif_orders_count[order.product_id] += newly_fulfilled
            if order.origin_phase == "evaluation":
                otif_evaluation_orders_count[order.product_id] += newly_fulfilled
    return revenue, cogs


def _cancel_overdue_orders(
    *,
    orders: list[_Order],
    cancellations: dict[str, int],
    cancelled_orders_count: dict[str, int],
    cancelled_evaluation_orders_count: dict[str, int],
    segments: dict[str, CustomerSegment],
    shock: DailyShock,
    day_index: int,
) -> None:
    grouped: dict[tuple[str, str], list[_Order]] = {}
    for order in orders:
        if order.open_units and day_index > order.due_day + order.grace_days:
            grouped.setdefault((order.product_id, order.segment_id), []).append(order)

    for (product_id, segment_id), overdue_orders in grouped.items():
        overdue_order_count = sum(
            _open_order_count(order) for order in overdue_orders
        )
        cancelled_order_count = binomial_quantile(
            trials=overdue_order_count,
            probability=float(segments[segment_id].cancellation_probability),
            uniform=shock.cancellation_binomial_uniform(product_id, segment_id),
        )
        cancelled_orders_count[product_id] += cancelled_order_count
        remaining = cancelled_order_count
        for order in sorted(overdue_orders, key=lambda item: item.order_id):
            order_count = min(_open_order_count(order), remaining)
            if order_count == 0:
                continue
            partial_units = order.shipped_units % order.unit_size
            untouched_orders = _open_order_count(order) - (1 if partial_units else 0)
            untouched_cancelled = min(order_count, untouched_orders)
            cancelled_units = untouched_cancelled * order.unit_size
            if order_count > untouched_cancelled:
                cancelled_units += order.unit_size - partial_units
            cancelled_units = min(cancelled_units, order.open_units)
            order.open_units -= cancelled_units
            order.cancelled_order_count += order_count
            cancellations[product_id] += cancelled_units
            if order.origin_phase == "evaluation":
                cancelled_evaluation_orders_count[product_id] += order_count
            remaining -= order_count
            if remaining == 0:
                break


def _complete_work_orders(
    work_orders: list[_WorkOrder],
    day_index: int,
    yield_rates: dict[str, float],
) -> tuple[list[_WorkOrder], dict[str, int], dict[str, int]]:
    remaining: list[_WorkOrder] = []
    completed_starts = {product_id: 0 for product_id in yield_rates}
    completed_good = {product_id: 0 for product_id in yield_rates}
    for work_order in work_orders:
        if work_order.due_day > day_index:
            remaining.append(work_order)
            continue
        good_units = int(
            Decimal(work_order.started_units * yield_rates[work_order.product_id])
            .to_integral_value(rounding=ROUND_HALF_UP)
        )
        completed_starts[work_order.product_id] += work_order.started_units
        completed_good[work_order.product_id] += good_units
    return remaining, completed_starts, completed_good


def _receive_purchase_orders(
    purchase_orders: list[_PurchaseOrder], day_index: int
) -> tuple[list[_PurchaseOrder], list[_PurchaseOrder]]:
    remaining = [order for order in purchase_orders if order.due_day > day_index]
    received = [order for order in purchase_orders if order.due_day <= day_index]
    return remaining, received


def _place_purchase_orders(
    *,
    company: CompanyModel,
    scenario: Scenario,
    shock_delays: dict[str, int],
    day_index: int,
    material_inventory: dict[str, int],
    open_purchase_orders: list[_PurchaseOrder],
) -> list[_PurchaseOrder]:
    changes = {
        change.material_id: change for change in scenario.policy_levers.material_changes
    }
    inventory_levels = material_inventory_levels(company, scenario)
    pending = {material.material_id: 0 for material in company.plant.materials}
    for purchase_order in open_purchase_orders:
        pending[purchase_order.material_id] += purchase_order.quantity
    created: list[_PurchaseOrder] = []
    for material in company.plant.materials:
        reorder_point, order_up_to = inventory_levels[material.material_id]
        inventory_position = material_inventory[material.material_id] + pending[
            material.material_id
        ]
        if inventory_position > reorder_point:
            continue
        quantity = order_up_to - inventory_position
        change = changes.get(material.material_id)
        lead_time_factor = (
            Decimal("1") - change.supplier_lead_time_improvement
            if change
            else Decimal("1")
        )
        lead_time = max(
            1,
            int(
                (Decimal(material.supplier_lead_time_days) * lead_time_factor)
                .to_integral_value(rounding=ROUND_HALF_UP)
            ),
        )
        cost_factor = (
            Decimal("1") + change.supplier_unit_cost_change
            if change
            else Decimal("1")
        )
        unit_cost = int(
            (Decimal(material.unit_cost_milli_cents) * cost_factor)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )
        created.append(
            _PurchaseOrder(
                material_id=material.material_id,
                due_day=day_index
                + lead_time
                + shock_delays[material.material_id],
                quantity=quantity,
                unit_cost_milli_cents=unit_cost,
                origin_phase=scenario.phase_for_day(day_index),
            )
        )
    return created


def _conversion_cost_per_start(
    product: Product, materials: dict[str, MaterialPolicy]
) -> int:
    material_cost = 0
    for requirement in product.material_requirements:
        material = materials[requirement.material_id]
        material_cost += (
            requirement.base_units_per_unit * material.unit_cost_milli_cents // 1000
        )
    return max(0, product.standard_unit_cost_cents - material_cost)


def _schedule_cash_flow(
    schedule: dict[int, dict[Phase, int]],
    *,
    due_day: int,
    origin_phase: Phase,
    amount_cents: int,
) -> None:
    by_phase = schedule.setdefault(due_day, {})
    by_phase[origin_phase] = by_phase.get(origin_phase, 0) + amount_cents


def _pop_scheduled_cash_flow(
    schedule: dict[int, dict[Phase, int]],
    due_day: int,
) -> tuple[int, int]:
    by_phase = schedule.pop(due_day, {})
    return sum(by_phase.values()), by_phase.get("evaluation", 0)


def _scheduled_phase_balance(
    schedule: dict[int, dict[Phase, int]], phase: Phase
) -> int:
    return sum(by_phase.get(phase, 0) for by_phase in schedule.values())


def _effective_unit_cost_cents(
    product: Product,
    materials: dict[str, MaterialPolicy],
    scenario: Scenario,
) -> int:
    """Resolve purchase-price variance into product cost without hiding it in cash."""

    changes = {
        change.material_id: change
        for change in scenario.policy_levers.material_changes
    }
    baseline_material_cost = 0
    effective_material_cost = 0
    for requirement in product.material_requirements:
        material = materials[requirement.material_id]
        baseline_component = (
            requirement.base_units_per_unit * material.unit_cost_milli_cents // 1000
        )
        change = changes.get(requirement.material_id)
        cost_factor = (
            Decimal("1") + change.supplier_unit_cost_change
            if change is not None
            else Decimal("1")
        )
        effective_milli_cents = int(
            (Decimal(material.unit_cost_milli_cents) * cost_factor)
            .to_integral_value(rounding=ROUND_HALF_UP)
        )
        effective_component = (
            requirement.base_units_per_unit * effective_milli_cents // 1000
        )
        baseline_material_cost += baseline_component
        effective_material_cost += effective_component
    conversion_cost = max(
        0, product.standard_unit_cost_cents - baseline_material_cost
    )
    return conversion_cost + effective_material_cost


def _commercial_investment_change_cents(
    company: CompanyModel,
    scenario: Scenario,
    *,
    is_operating_day: bool,
    phase: Phase,
) -> int:
    if not is_operating_day or phase == "runoff":
        return 0
    change = scenario.policy_levers.commercial_investment_change
    return int(
        (
            Decimal(company.financial_policy.daily_commercial_investment_cents)
            * change
        ).to_integral_value(rounding=ROUND_HALF_UP)
    )


def _capacity_commitment_change_cents(
    company: CompanyModel,
    scenario: Scenario,
    *,
    is_operating_day: bool,
) -> int:
    if not is_operating_day:
        return 0
    changes = {
        change.resource_id: change
        for change in scenario.policy_levers.resource_changes
    }
    total = Decimal("0")
    for resource in company.plant.resources:
        change = changes.get(resource.resource_id)
        if change is None:
            continue
        total += (
            Decimal(resource.daily_capacity_minutes)
            * change.regular_capacity_change
            * Decimal(resource.capacity_cost_cents_per_minute)
        )
    return int(total.to_integral_value(rounding=ROUND_HALF_UP))


def _backlog_by_product(
    orders: list[_Order], products: dict[str, Product]
) -> dict[str, int]:
    result = {product_id: 0 for product_id in products}
    for order in orders:
        result[order.product_id] += order.open_units
    return result


def _open_order_count(order: _Order) -> int:
    return max(
        0,
        order.order_count
        - order.fulfilled_order_count
        - order.cancelled_order_count,
    )


def _evaluation_backlog_orders_by_product(
    orders: list[_Order], products: dict[str, Product]
) -> dict[str, int]:
    result = {product_id: 0 for product_id in products}
    for order in orders:
        if order.origin_phase == "evaluation":
            result[order.product_id] += _open_order_count(order)
    return result


def _work_in_progress_by_product(
    work_orders: list[_WorkOrder], products: dict[str, Product]
) -> dict[str, int]:
    result = {product_id: 0 for product_id in products}
    for work_order in work_orders:
        result[work_order.product_id] += work_order.started_units
    return result


def _next_operating_day(day_index: int, lead_time_days: int) -> int:
    candidate = day_index
    remaining = lead_time_days
    while remaining:
        candidate += 1
        if (START_DATE + timedelta(days=candidate)).weekday() < 5:
            remaining -= 1
    return candidate


def _daily_interest_cents(debt_cents: int, annual_rate: Decimal) -> int:
    interest = Decimal(debt_cents) * annual_rate / Decimal(365)
    return int(interest.to_integral_value(rounding=ROUND_HALF_UP))


def _update_retention_factors(
    *,
    company: CompanyModel,
    orders: list[_Order],
    day_index: int,
    current: dict[str, float],
) -> dict[str, float]:
    """Apply contractual churn and a gradual service-reputation penalty."""

    result: dict[str, float] = {}
    for segment in company.customer_segments:
        open_units = sum(
            order.open_units
            for order in orders
            if order.segment_id == segment.segment_id
        )
        late_units = sum(
            order.open_units
            for order in orders
            if order.segment_id == segment.segment_id and day_index > order.due_day
        )
        late_share = late_units / open_units if open_units else 0.0
        annual_retention = 1.0 - float(segment.churn_probability)
        daily_retention = annual_retention ** (1.0 / 365.0)
        service_penalty = (
            float(segment.service_reputation_sensitivity) * late_share / 30.0
        )
        result[segment.segment_id] = max(
            0.0,
            min(1.0, current[segment.segment_id] * daily_retention - service_penalty),
        )
    return result


def _canonical_digest(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()
