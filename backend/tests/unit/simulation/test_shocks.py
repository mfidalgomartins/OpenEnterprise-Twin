from decimal import Decimal

import numpy as np
import pytest

from openenterprise_twin.domain.scenario import PolicyLevers
from openenterprise_twin.simulation import shocks
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)


def test_tape_is_versioned_and_reproducible_for_same_seed() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=14)

    first = shocks.build_shock_tape(
        company, scenario, seed=20260716, replication_id=3
    )
    second = shocks.build_shock_tape(
        company, scenario, seed=20260716, replication_id=3
    )

    assert first.tape_version == shocks.TAPE_VERSION
    assert first == second


def test_different_replications_produce_different_tapes() -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=7)

    first = shocks.build_shock_tape(
        company, scenario, seed=20260716, replication_id=11
    )
    second = shocks.build_shock_tape(
        company, scenario, seed=20260716, replication_id=12
    )

    assert first != second
    assert first.days[0].arrival_inverse_cdf_uniform(
        "standard-valve", "contracted"
    ) != second.days[0].arrival_inverse_cdf_uniform(
        "standard-valve", "contracted"
    )


def test_draw_collections_are_canonical_and_immutable() -> None:
    tape = shocks.build_shock_tape(
        build_northstar_company(),
        build_baseline_scenario(horizon_days=1),
        seed=17,
        replication_id=2,
    )
    shock = tape.days[0]

    assert isinstance(shock.demand_multiplier_entries, tuple)
    assert shock.demand_multiplier_entries == tuple(
        sorted(shock.demand_multiplier_entries)
    )
    with pytest.raises(TypeError):
        shock.demand_multiplier_by_key["standard-valve|contracted"] = 2.0
    with pytest.raises(TypeError):
        shock.capacity_factor_by_resource["assembly"] = 2.0


def test_existing_entity_streams_do_not_shift_when_entities_are_reordered_or_added(
) -> None:
    company = build_northstar_company()
    scenario = build_baseline_scenario(horizon_days=10)
    reordered = company.model_copy(
        update={
            "products": tuple(reversed(company.products)),
            "customer_segments": tuple(reversed(company.customer_segments)),
            "plant": company.plant.model_copy(
                update={
                    "resources": tuple(reversed(company.plant.resources)),
                    "materials": tuple(reversed(company.plant.materials)),
                }
            ),
        }
    )
    added_product = company.products[0].model_copy(
        update={"product_id": "auxiliary-valve", "name": "Auxiliary valve"}
    )
    added_resource = company.plant.resources[0].model_copy(
        update={"resource_id": "packaging"}
    )
    added_material = company.plant.materials[0].model_copy(
        update={"material_id": "resin", "name": "Resin"}
    )
    augmented = company.model_validate(
        reordered.model_copy(
            update={
                "products": (*reordered.products, added_product),
                "plant": reordered.plant.model_copy(
                    update={
                        "resources": (*reordered.plant.resources, added_resource),
                        "materials": (*reordered.plant.materials, added_material),
                    }
                ),
            }
        ).model_dump()
    )

    baseline_tape = shocks.build_shock_tape(
        company, scenario, seed=20260716, replication_id=5
    )
    augmented_tape = shocks.build_shock_tape(
        augmented, scenario, seed=20260716, replication_id=5
    )

    for baseline_day, augmented_day in zip(
        baseline_tape.days, augmented_tape.days, strict=True
    ):
        for product in company.products:
            assert baseline_day.yield_rate(product.product_id) == (
                augmented_day.yield_rate(product.product_id)
            )
            for profile in product.demand_profiles:
                key = (product.product_id, profile.segment_id)
                assert baseline_day.demand_multiplier(*key) == (
                    augmented_day.demand_multiplier(*key)
                )
                assert baseline_day.arrival_inverse_cdf_uniform(*key) == (
                    augmented_day.arrival_inverse_cdf_uniform(*key)
                )
                assert baseline_day.cancellation_binomial_uniform(*key) == (
                    augmented_day.cancellation_binomial_uniform(*key)
                )
        for segment in company.customer_segments:
            assert baseline_day.collection_delay_uniform(segment.segment_id) == (
                augmented_day.collection_delay_uniform(segment.segment_id)
            )
        for resource in company.plant.resources:
            assert baseline_day.capacity_factor(resource.resource_id) == (
                augmented_day.capacity_factor(resource.resource_id)
            )
        for material in company.plant.materials:
            assert baseline_day.supplier_delay_days(material.material_id) == (
                augmented_day.supplier_delay_days(material.material_id)
            )


def test_tape_is_independent_of_policy_scenario() -> None:
    company = build_northstar_company()
    baseline = build_baseline_scenario(horizon_days=21)
    policy = baseline.model_copy(
        update={
            "scenario_id": "growth-policy",
            "policy_levers": PolicyLevers(
                commercial_investment_change=Decimal("0.35"),
                one_off_capital_investment_cents=500_000,
            ),
        }
    )

    baseline_tape = shocks.build_shock_tape(
        company, baseline, seed=99, replication_id=8
    )
    policy_tape = shocks.build_shock_tape(
        company, policy, seed=99, replication_id=8
    )

    assert baseline_tape == policy_tape


def test_uniforms_are_independent_and_valid_for_inverse_cdf_consumers() -> None:
    tape = shocks.build_shock_tape(
        build_northstar_company(),
        build_baseline_scenario(horizon_days=8),
        seed=20260716,
        replication_id=23,
    )

    observed: list[tuple[float, float, float]] = []
    for shock in tape.days:
        arrival = shock.arrival_inverse_cdf_uniform(
            "standard-valve", "contracted"
        )
        cancellation = shock.cancellation_binomial_uniform(
            "standard-valve", "contracted"
        )
        collection = shock.collection_delay_uniform("contracted")
        assert 0.0 <= arrival < 1.0
        assert 0.0 <= cancellation < 1.0
        assert 0.0 <= collection < 1.0
        observed.append((arrival, cancellation, collection))

    assert all(len(set(values)) == 3 for values in observed)


def test_supplier_delays_yields_and_capacity_are_bounded() -> None:
    company = build_northstar_company()
    tape = shocks.build_shock_tape(
        company,
        build_baseline_scenario(horizon_days=300),
        seed=412,
        replication_id=9,
    )

    for shock in tape.days:
        for material in company.plant.materials:
            delay = shock.supplier_delay_days(material.material_id)
            assert 0 <= delay <= shocks.SUPPLIER_DELAY_MAX_DAYS
        for product in company.products:
            yield_rate = shock.yield_rate(product.product_id)
            assert shocks.product_quality_floor(product) <= yield_rate <= 1.0
        for resource in company.plant.resources:
            assert shock.capacity_factor(resource.resource_id) > 0.0


def test_demand_process_retains_market_correlation_and_normalized_scale() -> None:
    tape = shocks.build_shock_tape(
        build_northstar_company(),
        build_baseline_scenario(horizon_days=900),
        seed=20260716,
        replication_id=31,
    )
    standard = np.array(
        [
            shock.demand_multiplier("standard-valve", "contracted")
            for shock in tape.days
        ]
    )
    intelligent = np.array(
        [
            shock.demand_multiplier("intelligent-valve", "contracted")
            for shock in tape.days
        ]
    )

    cross_product_correlation = float(np.corrcoef(standard, intelligent)[0, 1])
    lag_one_correlation = float(np.corrcoef(standard[:-1], standard[1:])[0, 1])

    assert 0.95 < float(np.mean(standard)) < 1.05
    assert cross_product_correlation > 0.10
    assert lag_one_correlation > 0.10
