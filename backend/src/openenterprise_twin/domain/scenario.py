"""Typed scenario metadata and policy changes."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field, model_validator

from openenterprise_twin.domain.company import (
    DisplayName,
    DomainModel,
    Identifier,
    NonNegativeMoney,
    NonNegativeQuantity,
    Probability,
    VersionString,
)
from openenterprise_twin.domain.errors import DomainValidationError

PolicyRate = Annotated[Decimal, Field(ge=Decimal("-1"), le=Decimal("10"))]
PaymentTermChangeDays = Annotated[int, Field(ge=-365, le=365)]
CoverageDays = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("365"))]


class SegmentProductPriceChange(DomainModel):
    """A price change that applies to one product in one customer segment."""

    segment_id: Identifier
    product_id: Identifier
    price_change: PolicyRate


class PolicyLevers(DomainModel):
    """The bounded policy inputs that may differ from a baseline scenario."""

    price_change: PolicyRate = Decimal("0")
    price_changes: tuple[SegmentProductPriceChange, ...] = ()
    commercial_investment_change: PolicyRate = Decimal("0")
    regular_capacity_change: PolicyRate = Decimal("0")
    overtime_capacity_minutes: NonNegativeQuantity | None = None
    safety_stock_coverage_days: CoverageDays = Decimal("0")
    supplier_lead_time_improvement: Probability = Decimal("0")
    supplier_unit_cost_change: PolicyRate = Decimal("0")
    customer_payment_term_change_days: PaymentTermChangeDays = 0
    one_off_capital_investment: NonNegativeMoney = Decimal("0")

    @model_validator(mode="after")
    def validate_unique_price_changes(self) -> "PolicyLevers":
        price_change_keys = [
            (price_change.segment_id, price_change.product_id)
            for price_change in self.price_changes
        ]
        if len(price_change_keys) != len(set(price_change_keys)):
            raise DomainValidationError(
                "price changes must be unique for each segment and product"
            )
        return self


class Scenario(DomainModel):
    """A reproducible scenario relative to an optional baseline scenario."""

    scenario_id: Identifier
    name: DisplayName
    company_model_version: VersionString
    schema_version: VersionString
    horizon_days: Annotated[int, Field(gt=0, le=3650)]
    baseline_scenario_id: Identifier | None = None
    policy_levers: PolicyLevers = PolicyLevers()

    @model_validator(mode="after")
    def validate_baseline_reference(self) -> "Scenario":
        if self.baseline_scenario_id == self.scenario_id:
            raise DomainValidationError(
                "a scenario cannot reference itself as a baseline"
            )
        return self
