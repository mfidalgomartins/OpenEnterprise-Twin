"""Transparent finite-capacity production planning for the reference twin."""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.simulation.shocks import DailyShock


@dataclass(frozen=True, slots=True)
class ProductionPlan:
    starts_by_product: dict[str, int]
    material_consumption: dict[str, int]
    capacity_available: dict[str, int]
    capacity_used: dict[str, int]


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

    capacity_available = _available_capacity(
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
            target_units = 4 * sum(
                profile.daily_baseline_units for profile in product.demand_profiles
            )
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
    return ProductionPlan(
        starts_by_product=starts,
        material_consumption=material_consumption,
        capacity_available=capacity_available,
        capacity_used=capacity_used,
    )


def _available_capacity(
    company: CompanyModel,
    scenario: Scenario,
    shock: DailyShock,
    is_operating_day: bool,
) -> dict[str, int]:
    if not is_operating_day:
        return {resource.resource_id: 0 for resource in company.plant.resources}

    changes = {
        change.resource_id: change for change in scenario.policy_levers.resource_changes
    }
    result: dict[str, int] = {}
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
        result[resource.resource_id] = max(0, regular_minutes) + overtime
    return result
