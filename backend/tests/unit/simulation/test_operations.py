from dataclasses import replace
from decimal import Decimal

import pytest

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.scenario import (
    MaterialPolicyChange,
    PolicyLevers,
    ResourcePolicyChange,
)
from openenterprise_twin.simulation.operations import (
    material_inventory_levels,
    plan_production,
    validate_production_plan,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)
from openenterprise_twin.simulation.shocks import build_shock_tape


def test_safety_stock_coverage_increases_reorder_and_order_up_to_levels() -> None:
    company = build_northstar_company()
    baseline = build_baseline_scenario(horizon_days=1)
    policy = baseline.model_copy(
        update={
            "policy_levers": PolicyLevers(
                material_changes=(
                    MaterialPolicyChange(
                        material_id="steel",
                        safety_stock_coverage_days=Decimal("5"),
                    ),
                )
            )
        }
    )

    baseline_levels = material_inventory_levels(company, baseline)
    policy_levels = material_inventory_levels(company, policy)

    assert policy_levels["steel"][0] > baseline_levels["steel"][0]
    assert policy_levels["steel"][1] > baseline_levels["steel"][1]
    assert policy_levels["electronics"] == baseline_levels["electronics"]


def test_overtime_is_used_only_beyond_regular_capacity() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=1).model_copy(
        update={
            "policy_levers": PolicyLevers(
                resource_changes=(
                    ResourcePolicyChange(
                        resource_id="assembly",
                        overtime_capacity_minutes=480,
                    ),
                    ResourcePolicyChange(
                        resource_id="test",
                        overtime_capacity_minutes=240,
                    ),
                )
            )
        }
    )
    shock = build_shock_tape(
        company, scenario, seed=20260716, replication_id=41
    ).days[0]
    products = {product.product_id for product in company.products}

    plan = plan_production(
        company=company,
        scenario=scenario,
        shock=shock,
        is_operating_day=True,
        finished_goods={product_id: 0 for product_id in products},
        backlog={product_id: 10_000 for product_id in products},
        work_in_progress={product_id: 0 for product_id in products},
        material_inventory={
            material.material_id: material.opening_inventory_base_units
            for material in company.plant.materials
        },
    )

    assert sum(plan.overtime_used.values()) > 0
    for resource_id, overtime in plan.overtime_used.items():
        assert overtime == max(
            0,
            plan.capacity_used[resource_id]
            - plan.regular_capacity_available[resource_id],
        )


def test_production_plan_validation_rejects_unexplained_capacity_use() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=1)
    shock = build_shock_tape(
        company, scenario, seed=20260716, replication_id=42
    ).days[0]
    material_inventory = {
        material.material_id: material.opening_inventory_base_units
        for material in company.plant.materials
    }
    products = {product.product_id for product in company.products}
    plan = plan_production(
        company=company,
        scenario=scenario,
        shock=shock,
        is_operating_day=True,
        finished_goods={product_id: 0 for product_id in products},
        backlog={product_id: 100 for product_id in products},
        work_in_progress={product_id: 0 for product_id in products},
        material_inventory=material_inventory,
    )
    broken_capacity = plan.capacity_used.copy()
    broken_capacity["assembly"] += 1

    with pytest.raises(InvariantViolation, match="production_capacity_reconciliation"):
        validate_production_plan(
            company=company,
            plan=replace(plan, capacity_used=broken_capacity),
            material_inventory=material_inventory,
        )
