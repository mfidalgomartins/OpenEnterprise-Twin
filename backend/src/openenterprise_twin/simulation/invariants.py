"""Physical, order-flow and accounting invariants for every transition."""

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.results import (
    PeriodResult,
    SimulationTrace,
    trace_content_digest,
)


def validate_period(period: PeriodResult) -> None:
    """Raise a stable invariant code when a daily ledger does not reconcile."""

    product_ids = set(period.opening_finished_goods_units)
    _require_same_keys(
        product_ids,
        period.good_production_units,
        period.shipments_units,
        period.closing_finished_goods_units,
        period.opening_wip_units,
        period.production_start_units,
        period.completed_production_units,
        period.production_scrap_units,
        period.closing_wip_units,
        period.opening_backlog_units,
        period.new_orders_units,
        period.lost_demand_units,
        period.cancellations_units,
        period.closing_backlog_units,
        period.new_orders_count,
        period.cancelled_orders_count,
        period.fulfilled_orders_count,
        period.otif_orders_count,
        period.fulfilled_evaluation_orders_count,
        period.otif_evaluation_orders_count,
        period.cancelled_evaluation_orders_count,
        period.closing_evaluation_backlog_orders_count,
        period.on_time_shipment_units,
    )
    for product_id in product_ids:
        if (
            period.opening_finished_goods_units[product_id]
            + period.good_production_units[product_id]
            != period.shipments_units[product_id]
            + period.closing_finished_goods_units[product_id]
        ):
            _fail(
                "finished_goods_conservation",
                f"finished goods do not reconcile for '{product_id}'",
            )
        if (
            period.opening_wip_units[product_id]
            + period.production_start_units[product_id]
            != period.completed_production_units[product_id]
            + period.closing_wip_units[product_id]
        ):
            _fail(
                "wip_conservation",
                f"work in progress does not reconcile for '{product_id}'",
            )
        if (
            period.good_production_units[product_id]
            + period.production_scrap_units[product_id]
            != period.completed_production_units[product_id]
        ):
            _fail(
                "production_yield_conservation",
                f"production yield does not reconcile for '{product_id}'",
            )
        if (
            period.otif_orders_count[product_id]
            > period.fulfilled_orders_count[product_id]
        ):
            _fail(
                "otif_bound",
                f"OTIF orders exceed fulfilled orders for '{product_id}'",
            )
        if (
            period.fulfilled_evaluation_orders_count[product_id]
            > period.fulfilled_orders_count[product_id]
            or period.otif_evaluation_orders_count[product_id]
            > period.otif_orders_count[product_id]
            or period.cancelled_evaluation_orders_count[product_id]
            > period.cancelled_orders_count[product_id]
        ):
            _fail(
                "evaluation_order_bound",
                f"evaluation order outcomes exceed total outcomes for '{product_id}'",
            )
        if (
            period.on_time_shipment_units[product_id]
            > period.shipments_units[product_id]
        ):
            _fail(
                "on_time_shipment_bound",
                f"on-time shipments exceed shipments for '{product_id}'",
            )
        if (
            period.opening_backlog_units[product_id]
            + period.new_orders_units[product_id]
            != period.shipments_units[product_id]
            + period.cancellations_units[product_id]
            + period.closing_backlog_units[product_id]
        ):
            _fail(
                "backlog_conservation",
                f"order flow does not reconcile for '{product_id}'",
            )

    material_ids = set(period.opening_material_inventory_units)
    _require_same_keys(
        material_ids,
        period.material_receipts_units,
        period.material_consumption_units,
        period.closing_material_inventory_units,
    )
    for material_id in material_ids:
        if (
            period.opening_material_inventory_units[material_id]
            + period.material_receipts_units[material_id]
            != period.material_consumption_units[material_id]
            + period.closing_material_inventory_units[material_id]
        ):
            _fail(
                "material_conservation",
                f"material flow does not reconcile for '{material_id}'",
            )

    _require_same_keys(
        set(period.capacity_available_minutes),
        period.capacity_used_minutes,
        period.overtime_used_minutes,
    )
    for resource_id, used in period.capacity_used_minutes.items():
        if used > period.capacity_available_minutes[resource_id]:
            _fail(
                "capacity_limit",
                f"capacity use exceeds availability for '{resource_id}'",
            )
        if period.overtime_used_minutes[resource_id] > used:
            _fail(
                "overtime_bound",
                f"overtime use exceeds total capacity use for '{resource_id}'",
            )

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
    if expected_cash != period.closing_cash_cents:
        _fail("cash_reconciliation", "cash ledger does not reconcile to the cent")

    expected_debt = (
        period.opening_revolver_debt_cents
        + period.revolver_draw_cents
        - period.revolver_repayment_cents
    )
    if expected_debt != period.closing_revolver_debt_cents:
        _fail("debt_reconciliation", "revolver ledger does not reconcile")


def validate_trace(trace: SimulationTrace) -> None:
    if trace_content_digest(trace) != trace.digest:
        _fail("trace_digest", "trace content does not match its provenance digest")
    if not trace.periods:
        _fail("empty_trace", "a simulation trace must contain at least one period")
    for index, period in enumerate(trace.periods):
        validate_period(period)
        if period.period_index != index:
            _fail("period_sequence", "period indexes must be contiguous")
        if index == 0:
            continue
        previous = trace.periods[index - 1]
        if previous.closing_finished_goods_units != period.opening_finished_goods_units:
            _fail("state_continuity", "finished goods state is discontinuous")
        if (
            previous.closing_material_inventory_units
            != period.opening_material_inventory_units
        ):
            _fail("state_continuity", "material state is discontinuous")
        if previous.closing_backlog_units != period.opening_backlog_units:
            _fail("state_continuity", "backlog state is discontinuous")
        if previous.closing_cash_cents != period.opening_cash_cents:
            _fail("state_continuity", "cash state is discontinuous")
        if (
            previous.closing_revolver_debt_cents
            != period.opening_revolver_debt_cents
        ):
            _fail("state_continuity", "debt state is discontinuous")
        if previous.closing_wip_units != period.opening_wip_units:
            _fail("state_continuity", "work-in-progress state is discontinuous")

    evaluation_created = sum(
        sum(period.new_orders_count.values())
        for period in trace.periods
        if period.phase == "evaluation"
    )
    evaluation_resolved = sum(
        sum(period.fulfilled_evaluation_orders_count.values())
        + sum(period.cancelled_evaluation_orders_count.values())
        for period in trace.periods
    )
    evaluation_open = sum(
        trace.periods[-1].closing_evaluation_backlog_orders_count.values()
    )
    if evaluation_created != evaluation_resolved + evaluation_open:
        _fail(
            "evaluation_order_conservation",
            "evaluation orders do not reconcile through the runoff period",
        )


def _require_same_keys(expected: set[str], *mappings: dict[str, int]) -> None:
    if any(set(mapping) != expected for mapping in mappings):
        _fail("dimension_mismatch", "ledger dimensions do not match")


def _fail(code: str, detail: str) -> None:
    raise InvariantViolation(code, detail)
