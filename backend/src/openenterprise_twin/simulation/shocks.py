"""Versioned, scenario-independent stochastic tapes for paired comparisons."""

from math import exp, sqrt
from typing import Annotated

import numpy as np
from pydantic import Field

from openenterprise_twin.domain.company import CompanyModel, DomainModel
from openenterprise_twin.domain.scenario import Scenario

NonNegativeInt = Annotated[int, Field(ge=0)]


class DailyShock(DomainModel):
    """All exogenous draws consumed by one daily transition."""

    day_index: NonNegativeInt
    demand_multiplier_by_key: dict[str, float]
    capacity_factor_by_resource: dict[str, float]
    yield_rate_by_product: dict[str, float]
    supplier_delay_days_by_material: dict[str, NonNegativeInt]


class ShockTape(DomainModel):
    """Immutable random inputs and reproducibility metadata."""

    seed: NonNegativeInt
    replication_id: NonNegativeInt
    rng_algorithm: str
    horizon_days: Annotated[int, Field(gt=0)]
    days: tuple[DailyShock, ...]


def build_shock_tape(
    company: CompanyModel,
    scenario: Scenario,
    *,
    seed: int,
    replication_id: int,
) -> ShockTape:
    """Build a Philox tape whose draws remain stable across policy scenarios."""

    generator = np.random.Generator(np.random.Philox([seed, replication_id]))
    product_states = {product.product_id: 0.0 for product in company.products}
    common_state = 0.0
    days: list[DailyShock] = []

    for day_index in range(scenario.horizon_days):
        common_state = 0.65 * common_state + sqrt(1 - 0.65**2) * float(
            generator.normal()
        )
        product_multipliers: dict[str, float] = {}
        for product in sorted(company.products, key=lambda item: item.product_id):
            state = product_states[product.product_id]
            state = 0.35 * state + sqrt(1 - 0.35**2) * float(generator.normal())
            product_states[product.product_id] = state
            multiplier = exp(0.08 * common_state + 0.12 * state - 0.5 * 0.0208)
            for profile in sorted(
                product.demand_profiles, key=lambda item: item.segment_id
            ):
                product_multipliers[
                    demand_key(product.product_id, profile.segment_id)
                ] = round(multiplier, 10)

        capacity_factors = {
            resource.resource_id: round(
                exp(0.03 * float(generator.normal()) - 0.5 * 0.03**2), 10
            )
            for resource in sorted(
                company.plant.resources, key=lambda item: item.resource_id
            )
        }
        yield_rates = {
            product.product_id: round(
                min(
                    1.0,
                    max(
                        0.0,
                        float(product.yield_rate) + 0.004 * float(generator.normal()),
                    ),
                ),
                10,
            )
            for product in sorted(company.products, key=lambda item: item.product_id)
        }
        supplier_delays = {
            material.material_id: int(
                generator.poisson(1 if material.material_id == "steel" else 2)
            )
            for material in sorted(
                company.plant.materials, key=lambda item: item.material_id
            )
        }
        days.append(
            DailyShock(
                day_index=day_index,
                demand_multiplier_by_key=product_multipliers,
                capacity_factor_by_resource=capacity_factors,
                yield_rate_by_product=yield_rates,
                supplier_delay_days_by_material=supplier_delays,
            )
        )

    return ShockTape(
        seed=seed,
        replication_id=replication_id,
        rng_algorithm="numpy.philox",
        horizon_days=scenario.horizon_days,
        days=tuple(days),
    )


def demand_key(product_id: str, segment_id: str) -> str:
    return f"{product_id}|{segment_id}"
