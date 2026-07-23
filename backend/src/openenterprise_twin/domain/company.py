"""Typed assumptions for the daily Northstar operating model."""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openenterprise_twin.domain.errors import DomainValidationError

Identifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
]
DisplayName = Annotated[str, Field(min_length=1, max_length=160)]
VersionString = Annotated[
    str, Field(min_length=5, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
]
PositiveDecimal = Annotated[Decimal, Field(gt=Decimal("0"))]
Probability = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("1"))]
NonNegativeRate = Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("10"))]
Elasticity = Annotated[Decimal, Field(ge=Decimal("-10"), lt=Decimal("0"))]
MoneyCents = Annotated[int, Field(ge=0)]
PositiveMoneyCents = Annotated[int, Field(gt=0)]
Minutes = Annotated[int, Field(ge=0)]
PositiveMinutes = Annotated[int, Field(gt=0)]
MetricName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]


class DomainModel(BaseModel):
    """Base model that prevents mutation and unrecognised assumptions."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class DemandProfile(DomainModel):
    """Daily demand assumptions for one product and customer segment."""

    segment_id: Identifier
    daily_baseline_units: Annotated[int, Field(gt=0)]
    price_elasticity: Elasticity
    seasonality_amplitude: Probability
    commercial_investment_sensitivity: NonNegativeRate


class ResourceRequirement(DomainModel):
    """Capacity consumed by one production start."""

    resource_id: Identifier
    minutes_per_unit: PositiveMinutes


class MaterialRequirement(DomainModel):
    """Material consumed by one production start in the material base unit."""

    material_id: Identifier
    base_units_per_unit: Annotated[int, Field(gt=0)]


class Product(DomainModel):
    """A manufactured component and its commercial and operating assumptions."""

    product_id: Identifier
    name: DisplayName
    standard_price_cents: PositiveMoneyCents
    standard_unit_cost_cents: MoneyCents
    opening_finished_goods_units: Annotated[int, Field(ge=0)]
    production_lead_time_days: Annotated[int, Field(gt=0, le=365)]
    yield_rate: Annotated[Decimal, Field(gt=Decimal("0"), le=Decimal("1"))]
    resource_requirements: Annotated[
        tuple[ResourceRequirement, ...], Field(min_length=1)
    ]
    material_requirements: Annotated[
        tuple[MaterialRequirement, ...], Field(min_length=1)
    ]
    demand_profiles: Annotated[tuple[DemandProfile, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_product(self) -> Self:
        if self.standard_price_cents <= self.standard_unit_cost_cents:
            raise DomainValidationError(
                "standard_price_cents must exceed standard_unit_cost_cents"
            )
        _require_unique(
            [requirement.resource_id for requirement in self.resource_requirements],
            "resource requirements",
        )
        _require_unique(
            [requirement.material_id for requirement in self.material_requirements],
            "material requirements",
        )
        _require_unique(
            [profile.segment_id for profile in self.demand_profiles],
            "demand profiles",
        )
        return self


class CustomerSegment(DomainModel):
    """Commercial behaviour and working-capital terms for a buyer segment."""

    segment_id: Identifier
    name: DisplayName
    discount_rate: Probability
    churn_probability: Probability
    cancellation_probability: Probability
    order_dispersion: PositiveDecimal
    mean_order_size: Annotated[int, Field(gt=0)]
    promised_lead_time_days: Annotated[int, Field(ge=0, le=365)]
    cancellation_grace_days: Annotated[int, Field(ge=0, le=365)]
    payment_terms_days: Annotated[int, Field(ge=0, le=365)]
    service_reputation_sensitivity: Probability


class ResourceCapacity(DomainModel):
    """A finite production resource measured in integer minutes."""

    resource_id: Identifier
    daily_capacity_minutes: PositiveMinutes
    max_overtime_minutes: Minutes
    overtime_cost_cents_per_minute: MoneyCents
    capacity_cost_cents_per_minute: MoneyCents = 0


class MaterialPolicy(DomainModel):
    """Opening stock, procurement economics and supplier terms."""

    material_id: Identifier
    name: DisplayName
    opening_inventory_base_units: Annotated[int, Field(gt=0)]
    reorder_point_base_units: Annotated[int, Field(ge=0)]
    unit_cost_milli_cents: PositiveMoneyCents
    supplier_lead_time_days: Annotated[int, Field(gt=0, le=365)]
    supplier_payment_terms_days: Annotated[int, Field(ge=0, le=365)]

    @model_validator(mode="after")
    def validate_inventory_policy(self) -> Self:
        if self.reorder_point_base_units > self.opening_inventory_base_units:
            raise DomainValidationError(
                "reorder_point_base_units cannot exceed opening inventory"
            )
        return self


class Plant(DomainModel):
    """The production network included in the first enterprise twin."""

    plant_id: Identifier
    name: DisplayName
    resources: Annotated[tuple[ResourceCapacity, ...], Field(min_length=1)]
    materials: Annotated[tuple[MaterialPolicy, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_unique_assets(self) -> Self:
        _require_unique(
            [resource.resource_id for resource in self.resources], "plant resources"
        )
        _require_unique(
            [material.material_id for material in self.materials], "plant materials"
        )
        return self


class FinancialPolicy(DomainModel):
    """Cash, financing and working-capital assumptions for the company."""

    opening_cash_cents: MoneyCents
    liquidity_floor_cents: MoneyCents
    cash_target_cents: MoneyCents
    monthly_fixed_cost_cents: MoneyCents
    annual_interest_rate: NonNegativeRate
    revolver_limit_cents: MoneyCents
    daily_commercial_investment_cents: MoneyCents = 0

    @model_validator(mode="after")
    def validate_liquidity_policy(self) -> Self:
        if self.cash_target_cents < self.liquidity_floor_cents:
            raise DomainValidationError(
                "cash_target_cents must be greater than or equal to "
                "liquidity_floor_cents"
            )
        return self


class DecisionMetricRule(DomainModel):
    """Company-owned materiality and improvement semantics for one outcome."""

    metric_name: MetricName
    materiality_threshold: Annotated[Decimal, Field(ge=Decimal("0"))]
    improvement_direction: Literal["higher", "lower"]


class DecisionPolicy(DomainModel):
    """Auditable decision rules used by paired scenario comparisons."""

    metric_rules: tuple[DecisionMetricRule, ...] = ()

    @model_validator(mode="after")
    def validate_unique_metric_rules(self) -> Self:
        _require_unique(
            [rule.metric_name for rule in self.metric_rules],
            "decision metric rules",
        )
        return self


class CompanyModel(DomainModel):
    """Versioned, self-consistent assumptions for one simulated company."""

    company_id: Identifier
    name: DisplayName
    model_version: VersionString
    products: Annotated[tuple[Product, ...], Field(min_length=1)]
    customer_segments: Annotated[tuple[CustomerSegment, ...], Field(min_length=1)]
    plant: Plant
    financial_policy: FinancialPolicy
    decision_policy: DecisionPolicy = Field(default_factory=DecisionPolicy)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        segment_ids = [segment.segment_id for segment in self.customer_segments]
        product_ids = [product.product_id for product in self.products]
        _require_unique(segment_ids, "customer segments")
        _require_unique(product_ids, "products")

        known_segments = set(segment_ids)
        known_resources = {resource.resource_id for resource in self.plant.resources}
        known_materials = {material.material_id for material in self.plant.materials}

        for product in self.products:
            self._validate_product_references(
                product, known_segments, known_resources, known_materials
            )
        return self

    @staticmethod
    def _validate_product_references(
        product: Product,
        known_segments: set[str],
        known_resources: set[str],
        known_materials: set[str],
    ) -> None:
        _require_known(
            {profile.segment_id for profile in product.demand_profiles},
            known_segments,
            f"product '{product.product_id}' references unknown customer segment",
        )
        _require_known(
            {
                requirement.resource_id
                for requirement in product.resource_requirements
            },
            known_resources,
            f"product '{product.product_id}' references unknown resource",
        )
        _require_known(
            {
                requirement.material_id
                for requirement in product.material_requirements
            },
            known_materials,
            f"product '{product.product_id}' references unknown material",
        )


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise DomainValidationError(f"{label} must have unique identifiers")


def _require_known(actual: set[str], known: set[str], message: str) -> None:
    unknown = actual - known
    if unknown:
        raise DomainValidationError(f"{message} '{sorted(unknown)[0]}'")
