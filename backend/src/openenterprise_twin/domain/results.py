"""Immutable simulation ledger and trace contracts."""

import json
from datetime import date
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import Field

from openenterprise_twin.domain.company import DomainModel, Identifier, VersionString

NonNegativeInt = Annotated[int, Field(ge=0)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
Phase = Literal["warmup", "evaluation", "runoff"]


class PeriodResult(DomainModel):
    """Auditable daily transition with physical and cash reconciliation fields."""

    period_index: NonNegativeInt
    period_date: date
    phase: Phase
    is_operating_day: bool
    opening_finished_goods_units: dict[str, NonNegativeInt]
    good_production_units: dict[str, NonNegativeInt]
    shipments_units: dict[str, NonNegativeInt]
    closing_finished_goods_units: dict[str, NonNegativeInt]
    opening_wip_units: dict[str, NonNegativeInt]
    production_start_units: dict[str, NonNegativeInt]
    completed_production_units: dict[str, NonNegativeInt]
    production_scrap_units: dict[str, NonNegativeInt]
    closing_wip_units: dict[str, NonNegativeInt]
    opening_backlog_units: dict[str, NonNegativeInt]
    new_orders_units: dict[str, NonNegativeInt]
    lost_demand_units: dict[str, NonNegativeInt]
    cancellations_units: dict[str, NonNegativeInt]
    closing_backlog_units: dict[str, NonNegativeInt]
    new_orders_count: dict[str, NonNegativeInt]
    fulfilled_orders_count: dict[str, NonNegativeInt]
    otif_orders_count: dict[str, NonNegativeInt]
    on_time_shipment_units: dict[str, NonNegativeInt]
    retention_factor_by_segment: dict[str, UnitInterval]
    opening_material_inventory_units: dict[str, NonNegativeInt]
    material_receipts_units: dict[str, NonNegativeInt]
    material_consumption_units: dict[str, NonNegativeInt]
    closing_material_inventory_units: dict[str, NonNegativeInt]
    capacity_available_minutes: dict[str, NonNegativeInt]
    capacity_used_minutes: dict[str, NonNegativeInt]
    overtime_used_minutes: dict[str, NonNegativeInt]
    opening_cash_cents: NonNegativeInt
    collections_cents: NonNegativeInt
    supplier_payments_cents: NonNegativeInt
    conversion_cost_cents: NonNegativeInt
    overtime_cost_cents: NonNegativeInt
    fixed_cost_cents: NonNegativeInt
    interest_paid_cents: NonNegativeInt
    capital_investment_cents: NonNegativeInt
    revolver_draw_cents: NonNegativeInt
    revolver_repayment_cents: NonNegativeInt
    closing_cash_cents: NonNegativeInt
    opening_revolver_debt_cents: NonNegativeInt
    closing_revolver_debt_cents: NonNegativeInt
    revenue_cents: NonNegativeInt
    cogs_cents: NonNegativeInt


class SimulationTrace(DomainModel):
    """Complete reproducible result for one company, scenario and shock tape."""

    company_model_version: VersionString
    scenario_schema_version: VersionString
    engine_version: VersionString
    shock_tape_version: VersionString
    scenario_id: Identifier
    seed: NonNegativeInt
    replication_id: NonNegativeInt
    rng_algorithm: str
    resolved_assumptions_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    shock_tape_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    periods: tuple[PeriodResult, ...]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def trace_content_digest(trace: SimulationTrace) -> str:
    """Hash every trace field except the digest itself using canonical JSON."""

    canonical = json.dumps(
        trace.model_dump(mode="json", exclude={"digest"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()
