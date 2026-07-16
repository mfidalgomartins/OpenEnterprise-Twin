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
from openenterprise_twin.domain.results import PeriodResult, SimulationTrace
from openenterprise_twin.domain.scenario import (
    Scenario,
    validate_scenario_against_company,
)
from openenterprise_twin.simulation.demand import expected_daily_units
from openenterprise_twin.simulation.finance import apply_financing
from openenterprise_twin.simulation.invariants import validate_period
from openenterprise_twin.simulation.operations import plan_production
from openenterprise_twin.simulation.shocks import ShockTape, demand_key

START_DATE = date(2025, 1, 1)


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


def simulate_trace(
    company: CompanyModel, scenario: Scenario, shock_tape: ShockTape
) -> SimulationTrace:
    """Simulate one auditable trace; randomness may enter only through the tape."""

    validate_scenario_against_company(scenario, company)
    _validate_tape(scenario, shock_tape)

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
    receivables: dict[int, int] = {}
    payables: dict[int, int] = {}
    cash_cents = company.financial_policy.opening_cash_cents
    debt_cents = 0
    next_order_id = 1
    periods: list[PeriodResult] = []

    for day_index, shock in enumerate(shock_tape.days):
        period_date = START_DATE + timedelta(days=day_index)
        is_operating_day = period_date.weekday() < 5
        opening_finished_goods = finished_goods.copy()
        opening_material_inventory = material_inventory.copy()
        opening_backlog = _backlog_by_product(orders, products)
        opening_cash = cash_cents
        opening_debt = debt_cents

        good_production = {product_id: 0 for product_id in products}
        material_receipts = {material_id: 0 for material_id in materials}
        material_consumption = {material_id: 0 for material_id in materials}
        shipments = {product_id: 0 for product_id in products}
        new_orders = {product_id: 0 for product_id in products}
        cancellations = {product_id: 0 for product_id in products}

        work_orders, completed = _complete_work_orders(
            work_orders, day_index, shock.yield_rate_by_product
        )
        for product_id, units in completed.items():
            finished_goods[product_id] += units
            good_production[product_id] += units

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
            payables[due_day] = payables.get(due_day, 0) + invoice

        collections_cents = receivables.pop(day_index, 0)
        supplier_payments_cents = payables.pop(day_index, 0)

        if is_operating_day:
            created_orders, next_order_id = _create_orders(
                company=company,
                scenario=scenario,
                shock_multipliers=shock.demand_multiplier_by_key,
                day_index=day_index,
                next_order_id=next_order_id,
            )
            orders.extend(created_orders)
            for order in created_orders:
                new_orders[order.product_id] += order.original_units

        revenue_cents, cogs_cents = _ship_orders(
            orders=orders,
            finished_goods=finished_goods,
            shipments=shipments,
            products=products,
            segments=segments,
            scenario=scenario,
            day_index=day_index,
            receivables=receivables,
        )
        _cancel_overdue_orders(orders, cancellations, day_index)
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
            + collections_cents
            - supplier_payments_cents
            - conversion_cost_cents
            - fixed_cost_cents
            - interest_paid_cents
            - capital_investment_cents
        )
        financing = apply_financing(
            cash_before_financing_cents=cash_before_financing,
            opening_debt_cents=debt_cents,
            policy=company.financial_policy,
        )
        cash_cents = financing.closing_cash_cents
        debt_cents = financing.closing_debt_cents

        period = PeriodResult(
            period_index=day_index,
            period_date=period_date,
            is_operating_day=is_operating_day,
            opening_finished_goods_units=opening_finished_goods,
            good_production_units=good_production,
            shipments_units=shipments,
            closing_finished_goods_units=finished_goods.copy(),
            opening_backlog_units=opening_backlog,
            new_orders_units=new_orders,
            cancellations_units=cancellations,
            closing_backlog_units=_backlog_by_product(orders, products),
            opening_material_inventory_units=opening_material_inventory,
            material_receipts_units=material_receipts,
            material_consumption_units=material_consumption,
            closing_material_inventory_units=material_inventory.copy(),
            capacity_available_minutes=production_plan.capacity_available,
            capacity_used_minutes=production_plan.capacity_used,
            opening_cash_cents=opening_cash,
            collections_cents=collections_cents,
            supplier_payments_cents=supplier_payments_cents,
            conversion_cost_cents=conversion_cost_cents,
            fixed_cost_cents=fixed_cost_cents,
            interest_paid_cents=interest_paid_cents,
            capital_investment_cents=capital_investment_cents,
            revolver_draw_cents=financing.draw_cents,
            revolver_repayment_cents=financing.repayment_cents,
            closing_cash_cents=cash_cents,
            opening_revolver_debt_cents=opening_debt,
            closing_revolver_debt_cents=debt_cents,
            revenue_cents=revenue_cents,
            cogs_cents=cogs_cents,
        )
        validate_period(period)
        periods.append(period)

    digest = _trace_digest(periods)
    return SimulationTrace(
        company_model_version=company.model_version,
        scenario_id=scenario.scenario_id,
        seed=shock_tape.seed,
        replication_id=shock_tape.replication_id,
        rng_algorithm=shock_tape.rng_algorithm,
        periods=tuple(periods),
        digest=digest,
    )


def _validate_tape(scenario: Scenario, tape: ShockTape) -> None:
    if (
        tape.horizon_days != scenario.horizon_days
        or len(tape.days) != scenario.horizon_days
    ):
        raise DomainValidationError("shock tape horizon does not match scenario")


def _create_orders(
    *,
    company: CompanyModel,
    scenario: Scenario,
    shock_multipliers: dict[str, float],
    day_index: int,
    next_order_id: int,
) -> tuple[list[_Order], int]:
    segments = {
        segment.segment_id: segment for segment in company.customer_segments
    }
    price_changes = {
        (change.product_id, change.segment_id): change.price_change
        for change in scenario.policy_levers.price_changes
    }
    created: list[_Order] = []
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
            expected = expected_daily_units(
                baseline_units=Decimal(profile.daily_baseline_units),
                price_change=price_change,
                elasticity=profile.price_elasticity,
                demand_multiplier=Decimal(
                    str(
                        shock_multipliers[
                            demand_key(product.product_id, segment.segment_id)
                        ]
                    )
                )
                * commercial_factor,
            )
            units = int(expected.to_integral_value(rounding=ROUND_HALF_UP))
            if units == 0:
                continue
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
                )
            )
            next_order_id += 1
    return created, next_order_id


def _ship_orders(
    *,
    orders: list[_Order],
    finished_goods: dict[str, int],
    shipments: dict[str, int],
    products: dict[str, Product],
    segments: dict[str, CustomerSegment],
    scenario: Scenario,
    day_index: int,
    receivables: dict[int, int],
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
        finished_goods[order.product_id] -= shipped
        shipments[order.product_id] += shipped
        invoice = shipped * order.unit_price_cents
        revenue += invoice
        cogs += shipped * products[order.product_id].standard_unit_cost_cents
        segment = segments[order.segment_id]
        due_day = (
            day_index
            + segment.payment_terms_days
            + payment_changes.get(order.segment_id, 0)
        )
        receivables[due_day] = receivables.get(due_day, 0) + invoice
    return revenue, cogs


def _cancel_overdue_orders(
    orders: list[_Order], cancellations: dict[str, int], day_index: int
) -> None:
    for order in orders:
        if order.open_units and day_index > order.due_day + order.grace_days:
            cancellations[order.product_id] += order.open_units
            order.open_units = 0


def _complete_work_orders(
    work_orders: list[_WorkOrder],
    day_index: int,
    yield_rates: dict[str, float],
) -> tuple[list[_WorkOrder], dict[str, int]]:
    remaining: list[_WorkOrder] = []
    completed: dict[str, int] = {}
    for work_order in work_orders:
        if work_order.due_day > day_index:
            remaining.append(work_order)
            continue
        good_units = int(
            Decimal(work_order.started_units * yield_rates[work_order.product_id])
            .to_integral_value(rounding=ROUND_HALF_UP)
        )
        completed[work_order.product_id] = (
            completed.get(work_order.product_id, 0) + good_units
        )
    return remaining, completed


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
    pending = {material.material_id: 0 for material in company.plant.materials}
    for purchase_order in open_purchase_orders:
        pending[purchase_order.material_id] += purchase_order.quantity
    created: list[_PurchaseOrder] = []
    for material in company.plant.materials:
        inventory_position = material_inventory[material.material_id] + pending[
            material.material_id
        ]
        if inventory_position > material.reorder_point_base_units:
            continue
        quantity = material.opening_inventory_base_units - inventory_position
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


def _backlog_by_product(
    orders: list[_Order], products: dict[str, Product]
) -> dict[str, int]:
    result = {product_id: 0 for product_id in products}
    for order in orders:
        result[order.product_id] += order.open_units
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


def _trace_digest(periods: list[PeriodResult]) -> str:
    canonical = json.dumps(
        [period.model_dump(mode="json") for period in periods],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()
