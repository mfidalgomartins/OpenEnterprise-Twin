"""Transparent finite-capacity production planning for the reference twin."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.simulation.shocks import DailyShock


@dataclass(frozen=True, slots=True)
class ProductionPlan:
    starts_by_product: dict[str, int]
    material_consumption: dict[str, int]
    regular_capacity_available: dict[str, int]
    capacity_available: dict[str, int]
    capacity_used: dict[str, int]
    overtime_used: dict[str, int]


def plan_production(
    *,
    company: CompanyModel,
    scenario: Scenario,
    shock: DailyShock,
    is_operating_day: bool,
    finished_goods: dict[str, int],
    backlog: dict[str, int],
    work_in_progress: dict[str, int],
    material_inventory: dict[str, int],
) -> ProductionPlan:
    """Allocate resources and material using an auditable backlog-first rule."""

    regular_capacity, capacity_available = _available_capacity(
        company, scenario, shock, is_operating_day
    )
    remaining_capacity = capacity_available.copy()
    remaining_material = material_inventory.copy()
    starts = {product.product_id: 0 for product in company.products}
    material_consumption = {
        material.material_id: 0 for material in company.plant.materials
    }

    if is_operating_day:
        for product in sorted(
            company.products,
            key=lambda item: (-backlog[item.product_id], item.product_id),
        ):
            target_units = _finished_goods_target_units(company, product.product_id)
            need = max(
                0,
                target_units
                + backlog[product.product_id]
                - finished_goods[product.product_id]
                - work_in_progress[product.product_id],
            )
            feasible = need
            for resource_requirement in product.resource_requirements:
                feasible = min(
                    feasible,
                    remaining_capacity[resource_requirement.resource_id]
                    // resource_requirement.minutes_per_unit,
                )
            for material_requirement in product.material_requirements:
                feasible = min(
                    feasible,
                    remaining_material[material_requirement.material_id]
                    // material_requirement.base_units_per_unit,
                )
            starts[product.product_id] = feasible
            for resource_requirement in product.resource_requirements:
                used = feasible * resource_requirement.minutes_per_unit
                remaining_capacity[resource_requirement.resource_id] -= used
            for material_requirement in product.material_requirements:
                consumed = feasible * material_requirement.base_units_per_unit
                remaining_material[material_requirement.material_id] -= consumed
                material_consumption[material_requirement.material_id] += consumed

    capacity_used = {
        resource_id: capacity_available[resource_id] - remaining
        for resource_id, remaining in remaining_capacity.items()
    }
    overtime_used = {
        resource_id: max(0, used - regular_capacity[resource_id])
        for resource_id, used in capacity_used.items()
    }
    return ProductionPlan(
        starts_by_product=starts,
        material_consumption=material_consumption,
        regular_capacity_available=regular_capacity,
        capacity_available=capacity_available,
        capacity_used=capacity_used,
        overtime_used=overtime_used,
    )


def _available_capacity(
    company: CompanyModel,
    scenario: Scenario,
    shock: DailyShock,
    is_operating_day: bool,
) -> tuple[dict[str, int], dict[str, int]]:
    if not is_operating_day:
        zero = {resource.resource_id: 0 for resource in company.plant.resources}
        return zero, zero.copy()

    changes = {
        change.resource_id: change for change in scenario.policy_levers.resource_changes
    }
    regular_result: dict[str, int] = {}
    total_result: dict[str, int] = {}
    for resource in company.plant.resources:
        change = changes.get(resource.resource_id)
        regular_change = change.regular_capacity_change if change else Decimal("0")
        overtime = change.overtime_capacity_minutes if change else 0
        stochastic_capacity = (
            Decimal(resource.daily_capacity_minutes)
            * (Decimal("1") + regular_change)
            * Decimal(str(shock.capacity_factor_by_resource[resource.resource_id]))
        )
        regular_minutes = int(
            stochastic_capacity.to_integral_value(rounding=ROUND_FLOOR)
        )
        regular_result[resource.resource_id] = max(0, regular_minutes)
        total_result[resource.resource_id] = max(0, regular_minutes) + overtime
    return regular_result, total_result


def _finished_goods_target_units(company: CompanyModel, product_id: str) -> int:
    """Cover the physical replenishment cycle plus the longest customer promise."""

    products = {product.product_id: product for product in company.products}
    segments = {
        segment.segment_id: segment for segment in company.customer_segments
    }
    product = products[product_id]
    maximum_promise = max(
        segments[profile.segment_id].promised_lead_time_days
        for profile in product.demand_profiles
    )
    coverage_days = product.production_lead_time_days + maximum_promise
    return coverage_days * sum(
        profile.daily_baseline_units for profile in product.demand_profiles
    )


def material_inventory_levels(
    company: CompanyModel, scenario: Scenario
) -> dict[str, tuple[int, int]]:
    """Return effective reorder and order-up-to levels including safety stock."""

    changes = {
        change.material_id: change for change in scenario.policy_levers.material_changes
    }
    daily_usage = {material.material_id: 0 for material in company.plant.materials}
    for product in company.products:
        demand_units = sum(
            profile.daily_baseline_units for profile in product.demand_profiles
        )
        for requirement in product.material_requirements:
            daily_usage[requirement.material_id] += (
                demand_units * requirement.base_units_per_unit
            )

    levels: dict[str, tuple[int, int]] = {}
    for material in company.plant.materials:
        change = changes.get(material.material_id)
        coverage = change.safety_stock_coverage_days if change else Decimal("0")
        safety_units = int(
            (coverage * daily_usage[material.material_id]).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        levels[material.material_id] = (
            material.reorder_point_base_units + safety_units,
            material.opening_inventory_base_units + safety_units,
        )
    return levels


def validate_production_plan(
    *,
    company: CompanyModel,
    plan: ProductionPlan,
    material_inventory: dict[str, int],
) -> None:
    """Prove that starts reconcile to declared material and capacity consumption."""

    expected_capacity = {
        resource.resource_id: 0 for resource in company.plant.resources
    }
    expected_material = {
        material.material_id: 0 for material in company.plant.materials
    }
    expected_products = {product.product_id for product in company.products}
    if set(plan.starts_by_product) != expected_products:
        raise InvariantViolation(
            "production_dimension_mismatch",
            "production starts do not match the company product dimension",
        )

    for product in company.products:
        starts = plan.starts_by_product[product.product_id]
        for resource_requirement in product.resource_requirements:
            expected_capacity[resource_requirement.resource_id] += (
                starts * resource_requirement.minutes_per_unit
            )
        for material_requirement in product.material_requirements:
            expected_material[material_requirement.material_id] += (
                starts * material_requirement.base_units_per_unit
            )

    if expected_capacity != plan.capacity_used:
        raise InvariantViolation(
            "production_capacity_reconciliation",
            "declared capacity use does not reconcile to production starts",
        )
    if expected_material != plan.material_consumption:
        raise InvariantViolation(
            "production_material_reconciliation",
            "declared material use does not reconcile to production starts",
        )
    for resource_id, used in expected_capacity.items():
        if used > plan.capacity_available[resource_id]:
            raise InvariantViolation(
                "production_capacity_limit",
                f"production exceeds effective capacity for '{resource_id}'",
            )
    for material_id, consumed in expected_material.items():
        if consumed > material_inventory[material_id]:
            raise InvariantViolation(
                "production_material_limit",
                f"production exceeds material availability for '{material_id}'",
            )
