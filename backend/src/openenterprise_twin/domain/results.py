"""Immutable simulation ledger and trace contracts."""

from datetime import date
from typing import Annotated

from pydantic import Field

from openenterprise_twin.domain.company import DomainModel, Identifier, VersionString

NonNegativeInt = Annotated[int, Field(ge=0)]


class PeriodResult(DomainModel):
    """Auditable daily transition with physical and cash reconciliation fields."""

    period_index: NonNegativeInt
    period_date: date
    is_operating_day: bool
    opening_finished_goods_units: dict[str, NonNegativeInt]
    good_production_units: dict[str, NonNegativeInt]
    shipments_units: dict[str, NonNegativeInt]
    closing_finished_goods_units: dict[str, NonNegativeInt]
    opening_backlog_units: dict[str, NonNegativeInt]
    new_orders_units: dict[str, NonNegativeInt]
    cancellations_units: dict[str, NonNegativeInt]
    closing_backlog_units: dict[str, NonNegativeInt]
    opening_material_inventory_units: dict[str, NonNegativeInt]
    material_receipts_units: dict[str, NonNegativeInt]
    material_consumption_units: dict[str, NonNegativeInt]
    closing_material_inventory_units: dict[str, NonNegativeInt]
    capacity_available_minutes: dict[str, NonNegativeInt]
    capacity_used_minutes: dict[str, NonNegativeInt]
    opening_cash_cents: NonNegativeInt
    collections_cents: NonNegativeInt
    supplier_payments_cents: NonNegativeInt
    conversion_cost_cents: NonNegativeInt
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
    scenario_id: Identifier
    seed: NonNegativeInt
    replication_id: NonNegativeInt
    rng_algorithm: str
    periods: tuple[PeriodResult, ...]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
