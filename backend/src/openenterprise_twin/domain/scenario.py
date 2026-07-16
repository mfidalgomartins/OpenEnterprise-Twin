"""Typed, scoped scenario metadata and policy changes."""

from collections.abc import Collection
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openenterprise_twin.domain.company import (
    CompanyModel,
    DisplayName,
    DomainModel,
    Identifier,
    Minutes,
    MoneyCents,
    Probability,
    VersionString,
)
from openenterprise_twin.domain.errors import DomainValidationError

PolicyRate = Annotated[Decimal, Field(ge=Decimal("-1"), le=Decimal("10"))]
PaymentTermChangeDays = Annotated[int, Field(ge=-365, le=365)]
CoverageDays = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("365"))]


class SegmentProductPriceChange(DomainModel):
    """A price change scoped to one product and customer segment."""

    segment_id: Identifier
    product_id: Identifier
    price_change: PolicyRate


class ResourcePolicyChange(DomainModel):
    """Capacity policy scoped to one production resource."""

    resource_id: Identifier
    regular_capacity_change: PolicyRate = Decimal("0")
    overtime_capacity_minutes: Minutes = 0


class MaterialPolicyChange(DomainModel):
    """Inventory and supplier policy scoped to one material."""

    material_id: Identifier
    safety_stock_coverage_days: CoverageDays = Decimal("0")
    supplier_lead_time_improvement: Probability = Decimal("0")
    supplier_unit_cost_change: PolicyRate = Decimal("0")


class SegmentPaymentTermChange(DomainModel):
    """Receivable-term change scoped to one customer segment."""

    segment_id: Identifier
    change_days: PaymentTermChangeDays


class PolicyLevers(DomainModel):
    """Bounded, addressable policy inputs relative to a baseline."""

    price_changes: tuple[SegmentProductPriceChange, ...] = ()
    commercial_investment_change: PolicyRate = Decimal("0")
    resource_changes: tuple[ResourcePolicyChange, ...] = ()
    material_changes: tuple[MaterialPolicyChange, ...] = ()
    payment_term_changes: tuple[SegmentPaymentTermChange, ...] = ()
    one_off_capital_investment_cents: MoneyCents = 0

    @model_validator(mode="after")
    def validate_unique_targets(self) -> Self:
        _require_unique_targets(
            [
                (change.segment_id, change.product_id)
                for change in self.price_changes
            ],
            "price changes must be unique for each segment and product",
        )
        _require_unique_targets(
            [change.resource_id for change in self.resource_changes],
            "resource changes must be unique",
        )
        _require_unique_targets(
            [change.material_id for change in self.material_changes],
            "material changes must be unique",
        )
        _require_unique_targets(
            [change.segment_id for change in self.payment_term_changes],
            "payment term changes must be unique",
        )
        return self


class Scenario(DomainModel):
    """A reproducible scenario relative to an optional baseline scenario."""

    scenario_id: Identifier
    name: DisplayName
    company_model_version: VersionString
    schema_version: VersionString
    horizon_days: Annotated[int, Field(gt=0, le=3650)]
    warmup_days: Annotated[int, Field(ge=0, le=3650)] = 0
    evaluation_days: Annotated[int, Field(ge=0, le=3650)] = 0
    runoff_days: Annotated[int, Field(ge=0, le=3650)] = 0
    baseline_scenario_id: Identifier | None = None
    policy_levers: PolicyLevers = Field(default_factory=PolicyLevers)

    @model_validator(mode="after")
    def validate_baseline_reference(self) -> Self:
        if self.baseline_scenario_id == self.scenario_id:
            raise DomainValidationError(
                "a scenario cannot reference itself as a baseline"
            )
        configured_lifecycle = (
            self.warmup_days + self.evaluation_days + self.runoff_days
        )
        if configured_lifecycle and configured_lifecycle != self.horizon_days:
            raise DomainValidationError(
                "scenario lifecycle phases must sum to horizon_days"
            )
        if configured_lifecycle and self.evaluation_days == 0:
            raise DomainValidationError(
                "scenario lifecycle phases require a positive evaluation period"
            )
        return self

    def phase_for_day(
        self, day_index: int
    ) -> Literal["warmup", "evaluation", "runoff"]:
        """Return the lifecycle phase; legacy unphased scenarios are evaluation-only."""

        if not 0 <= day_index < self.horizon_days:
            raise DomainValidationError("day index is outside the scenario horizon")
        if self.warmup_days + self.evaluation_days + self.runoff_days == 0:
            return "evaluation"
        if day_index < self.warmup_days:
            return "warmup"
        if day_index < self.warmup_days + self.evaluation_days:
            return "evaluation"
        return "runoff"


def validate_scenario_against_company(
    scenario: Scenario, company: CompanyModel
) -> None:
    """Validate all scoped policy targets against a company model."""

    if scenario.company_model_version != company.model_version:
        raise DomainValidationError(
            "scenario company_model_version does not match company model"
        )

    segments = {
        segment.segment_id: segment for segment in company.customer_segments
    }
    products = {product.product_id: product for product in company.products}
    resources = {
        resource.resource_id: resource for resource in company.plant.resources
    }
    materials = {
        material.material_id: material for material in company.plant.materials
    }

    for price_change in scenario.policy_levers.price_changes:
        _assert_known(price_change.segment_id, segments, "unknown customer segment")
        _assert_known(price_change.product_id, products, "unknown product")
        product = products[price_change.product_id]
        profile_segments = {
            profile.segment_id for profile in product.demand_profiles
        }
        if price_change.segment_id not in profile_segments:
            raise DomainValidationError(
                f"product '{price_change.product_id}' has no demand profile for "
                f"segment '{price_change.segment_id}'"
            )
        if Decimal(product.standard_price_cents) * (
            Decimal("1") + price_change.price_change
        ) <= 0:
            raise DomainValidationError("price change must preserve a positive price")
    for resource_change in scenario.policy_levers.resource_changes:
        _assert_known(resource_change.resource_id, resources, "unknown resource")
        resource = resources[resource_change.resource_id]
        if resource_change.overtime_capacity_minutes > resource.max_overtime_minutes:
            raise DomainValidationError(
                f"overtime cap for resource '{resource_change.resource_id}' is "
                f"{resource.max_overtime_minutes} minutes"
            )
    for material_change in scenario.policy_levers.material_changes:
        _assert_known(material_change.material_id, materials, "unknown material")
        material = materials[material_change.material_id]
        if Decimal(material.unit_cost_milli_cents) * (
            Decimal("1") + material_change.supplier_unit_cost_change
        ) <= 0:
            raise DomainValidationError(
                "material change must preserve a positive supplier cost"
            )
    for payment_change in scenario.policy_levers.payment_term_changes:
        _assert_known(payment_change.segment_id, segments, "unknown customer segment")
        segment = segments[payment_change.segment_id]
        effective_terms = segment.payment_terms_days + payment_change.change_days
        if not 0 <= effective_terms <= 365:
            raise DomainValidationError(
                "effective customer payment terms must remain between 0 and 365 days"
            )


def _require_unique_targets(values: list[object], message: str) -> None:
    if len(values) != len(set(values)):
        raise DomainValidationError(message)


def _assert_known(value: str, known: Collection[str], label: str) -> None:
    if value not in known:
        raise DomainValidationError(f"{label} '{value}'")
