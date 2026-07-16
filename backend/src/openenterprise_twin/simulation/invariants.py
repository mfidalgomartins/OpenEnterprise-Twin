"""Physical, order-flow and accounting invariants for every transition."""

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.results import PeriodResult, SimulationTrace


def validate_period(period: PeriodResult) -> None:
    """Raise a stable invariant code when a daily ledger does not reconcile."""

    product_ids = set(period.opening_finished_goods_units)
    _require_same_keys(
        product_ids,
        period.good_production_units,
        period.shipments_units,
        period.closing_finished_goods_units,
        period.opening_backlog_units,
        period.new_orders_units,
        period.cancellations_units,
        period.closing_backlog_units,
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
        set(period.capacity_available_minutes), period.capacity_used_minutes
    )
    for resource_id, used in period.capacity_used_minutes.items():
        if used > period.capacity_available_minutes[resource_id]:
            _fail(
                "capacity_limit",
                f"capacity use exceeds availability for '{resource_id}'",
            )

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


def _require_same_keys(expected: set[str], *mappings: dict[str, int]) -> None:
    if any(set(mapping) != expected for mapping in mappings):
        _fail("dimension_mismatch", "ledger dimensions do not match")


def _fail(code: str, detail: str) -> None:
    raise InvariantViolation(code, detail)
