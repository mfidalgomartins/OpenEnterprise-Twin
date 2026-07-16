"""Versioned, scenario-independent stochastic tapes for paired comparisons."""

from hashlib import sha256
from math import exp, sqrt
from types import MappingProxyType
from typing import Annotated, Self, cast

import numpy as np
from pydantic import Field, model_validator

from openenterprise_twin.domain.company import CompanyModel, DomainModel, Product
from openenterprise_twin.domain.scenario import Scenario

TAPE_VERSION = "1.0.0"
RNG_ALGORITHM = "numpy.philox.counter-keyed"
SUPPLIER_DELAY_MAX_DAYS = 7

NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
UnitFloat = Annotated[float, Field(ge=0, lt=1)]
ProbabilityFloat = Annotated[float, Field(ge=0, le=1)]

FloatEntry = tuple[str, float]
IntEntry = tuple[str, int]
class DailyShock(DomainModel):
    """Canonical exogenous draws consumed by one daily transition."""

    day_index: NonNegativeInt
    demand_multiplier_entries: tuple[tuple[str, PositiveFloat], ...]
    arrival_inverse_cdf_uniform_entries: tuple[tuple[str, UnitFloat], ...]
    cancellation_binomial_uniform_entries: tuple[tuple[str, UnitFloat], ...]
    collection_delay_uniform_entries: tuple[tuple[str, UnitFloat], ...]
    capacity_factor_entries: tuple[tuple[str, PositiveFloat], ...]
    yield_rate_entries: tuple[tuple[str, ProbabilityFloat], ...]
    supplier_delay_days_entries: tuple[tuple[str, NonNegativeInt], ...]

    @model_validator(mode="after")
    def validate_canonical_entries(self) -> Self:
        collections = (
            self.demand_multiplier_entries,
            self.arrival_inverse_cdf_uniform_entries,
            self.cancellation_binomial_uniform_entries,
            self.collection_delay_uniform_entries,
            self.capacity_factor_entries,
            self.yield_rate_entries,
            self.supplier_delay_days_entries,
        )
        for entries in collections:
            keys = tuple(key for key, _ in entries)
            if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
                raise ValueError("stochastic draw entries must have unique sorted keys")
        return self

    def demand_multiplier(self, product_id: str, segment_id: str) -> float:
        return _lookup(
            self.demand_multiplier_entries, demand_key(product_id, segment_id)
        )

    def arrival_inverse_cdf_uniform(
        self, product_id: str, segment_id: str
    ) -> float:
        return _lookup(
            self.arrival_inverse_cdf_uniform_entries,
            demand_key(product_id, segment_id),
        )

    def cancellation_binomial_uniform(
        self, product_id: str, segment_id: str
    ) -> float:
        return _lookup(
            self.cancellation_binomial_uniform_entries,
            demand_key(product_id, segment_id),
        )

    def collection_delay_uniform(self, segment_id: str) -> float:
        return _lookup(self.collection_delay_uniform_entries, segment_id)

    def capacity_factor(self, resource_id: str) -> float:
        return _lookup(self.capacity_factor_entries, resource_id)

    def yield_rate(self, product_id: str) -> float:
        return _lookup(self.yield_rate_entries, product_id)

    def supplier_delay_days(self, material_id: str) -> int:
        return _lookup(self.supplier_delay_days_entries, material_id)

    @property
    def demand_multiplier_by_key(self) -> dict[str, float]:
        """Read-only compatibility view for the current transition engine."""

        return _immutable_mapping(self.demand_multiplier_entries)

    @property
    def capacity_factor_by_resource(self) -> dict[str, float]:
        """Read-only compatibility view for the current operations planner."""

        return _immutable_mapping(self.capacity_factor_entries)

    @property
    def yield_rate_by_product(self) -> dict[str, float]:
        """Read-only compatibility view for the current transition engine."""

        return _immutable_mapping(self.yield_rate_entries)

    @property
    def supplier_delay_days_by_material(self) -> dict[str, int]:
        """Read-only compatibility view for the current procurement transition."""

        return _immutable_mapping(self.supplier_delay_days_entries)


class ShockTape(DomainModel):
    """Immutable random inputs and complete tape reproducibility metadata."""

    tape_version: str
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
    """Build a stable-keyed Philox tape independent of scenario policy levers."""

    if seed < 0 or replication_id < 0:
        raise ValueError("seed and replication_id must be non-negative")

    product_states = {product.product_id: 0.0 for product in company.products}
    common_state = 0.0
    days: list[DailyShock] = []

    for day_index in range(scenario.horizon_days):
        common_state = _next_ar1_state(
            current_state=common_state,
            coefficient=0.65,
            innovation=_normal_draw(
                seed=seed,
                replication_id=replication_id,
                process="demand-market-ar1",
                day_index=day_index,
                entity="market",
                draw_id="innovation",
            ),
        )

        demand_multipliers: list[FloatEntry] = []
        arrival_uniforms: list[FloatEntry] = []
        cancellation_uniforms: list[FloatEntry] = []
        yield_rates: list[FloatEntry] = []
        for product in sorted(company.products, key=lambda item: item.product_id):
            product_state = _next_ar1_state(
                current_state=product_states[product.product_id],
                coefficient=0.35,
                innovation=_normal_draw(
                    seed=seed,
                    replication_id=replication_id,
                    process="demand-product-ar1",
                    day_index=day_index,
                    entity=product.product_id,
                    draw_id="innovation",
                ),
            )
            product_states[product.product_id] = product_state
            multiplier = exp(
                0.08 * common_state + 0.12 * product_state - 0.5 * 0.0208
            )
            for profile in sorted(
                product.demand_profiles, key=lambda item: item.segment_id
            ):
                entity = demand_key(product.product_id, profile.segment_id)
                demand_multipliers.append((entity, round(multiplier, 10)))
                arrival_uniforms.append(
                    (
                        entity,
                        _uniform_draw(
                            seed=seed,
                            replication_id=replication_id,
                            process="demand-arrival-negative-binomial",
                            day_index=day_index,
                            entity=entity,
                            draw_id="inverse-cdf-uniform",
                        ),
                    )
                )
                cancellation_uniforms.append(
                    (
                        entity,
                        _uniform_draw(
                            seed=seed,
                            replication_id=replication_id,
                            process="order-cancellation-binomial",
                            day_index=day_index,
                            entity=entity,
                            draw_id="conditional-uniform",
                        ),
                    )
                )
            yield_rates.append(
                (
                    product.product_id,
                    _yield_draw(
                        product=product,
                        seed=seed,
                        replication_id=replication_id,
                        day_index=day_index,
                    ),
                )
            )

        collection_uniforms = tuple(
            sorted(
                (
                    segment.segment_id,
                    _uniform_draw(
                        seed=seed,
                        replication_id=replication_id,
                        process="collection-delay-discrete",
                        day_index=day_index,
                        entity=segment.segment_id,
                        draw_id="inverse-cdf-uniform",
                    ),
                )
                for segment in company.customer_segments
            )
        )
        capacity_factors = tuple(
            sorted(
                (
                    resource.resource_id,
                    _capacity_draw(
                        seed=seed,
                        replication_id=replication_id,
                        day_index=day_index,
                        resource_id=resource.resource_id,
                    ),
                )
                for resource in company.plant.resources
            )
        )
        supplier_delays = tuple(
            sorted(
                (
                    material.material_id,
                    _supplier_delay_draw(
                        seed=seed,
                        replication_id=replication_id,
                        day_index=day_index,
                        material_id=material.material_id,
                    ),
                )
                for material in company.plant.materials
            )
        )
        days.append(
            DailyShock(
                day_index=day_index,
                demand_multiplier_entries=tuple(sorted(demand_multipliers)),
                arrival_inverse_cdf_uniform_entries=tuple(
                    sorted(arrival_uniforms)
                ),
                cancellation_binomial_uniform_entries=tuple(
                    sorted(cancellation_uniforms)
                ),
                collection_delay_uniform_entries=collection_uniforms,
                capacity_factor_entries=capacity_factors,
                yield_rate_entries=tuple(sorted(yield_rates)),
                supplier_delay_days_entries=supplier_delays,
            )
        )

    return ShockTape(
        tape_version=TAPE_VERSION,
        seed=seed,
        replication_id=replication_id,
        rng_algorithm=RNG_ALGORITHM,
        horizon_days=scenario.horizon_days,
        days=tuple(days),
    )


def demand_key(product_id: str, segment_id: str) -> str:
    return f"{product_id}|{segment_id}"


def product_quality_floor(product: Product) -> float:
    """Return a product-specific floor below its baseline expected yield."""

    baseline = float(product.yield_rate)
    return round(max(0.0, baseline - min(0.08, baseline / 2.0)), 10)


def _next_ar1_state(
    *, current_state: float, coefficient: float, innovation: float
) -> float:
    return coefficient * current_state + sqrt(1.0 - coefficient**2) * innovation


def _capacity_draw(
    *, seed: int, replication_id: int, day_index: int, resource_id: str
) -> float:
    sigma = 0.03
    innovation = _normal_draw(
        seed=seed,
        replication_id=replication_id,
        process="capacity-availability",
        day_index=day_index,
        entity=resource_id,
        draw_id="lognormal-innovation",
    )
    return max(1e-10, round(exp(sigma * innovation - 0.5 * sigma**2), 10))


def _yield_draw(
    *, product: Product, seed: int, replication_id: int, day_index: int
) -> float:
    quality_floor = product_quality_floor(product)
    baseline = float(product.yield_rate)
    scaled_mean = (baseline - quality_floor) / (1.0 - quality_floor)
    concentration = 160.0
    alpha = max(1e-6, scaled_mean * concentration)
    beta = max(1e-6, (1.0 - scaled_mean) * concentration)
    generator = _draw_generator(
        seed=seed,
        replication_id=replication_id,
        process="production-yield-beta",
        day_index=day_index,
        entity=product.product_id,
        draw_id="beta-realization",
    )
    beta_draw = float(generator.beta(alpha, beta))
    bounded = quality_floor + (1.0 - quality_floor) * beta_draw
    return min(1.0, max(quality_floor, round(bounded, 10)))


def _supplier_delay_draw(
    *, seed: int, replication_id: int, day_index: int, material_id: str
) -> int:
    generator = _draw_generator(
        seed=seed,
        replication_id=replication_id,
        process="supplier-delay-bounded",
        day_index=day_index,
        entity=material_id,
        draw_id="binomial-realization",
    )
    return int(generator.binomial(SUPPLIER_DELAY_MAX_DAYS, 0.25))


def _uniform_draw(
    *,
    seed: int,
    replication_id: int,
    process: str,
    day_index: int,
    entity: str,
    draw_id: str,
) -> float:
    generator = _draw_generator(
        seed=seed,
        replication_id=replication_id,
        process=process,
        day_index=day_index,
        entity=entity,
        draw_id=draw_id,
    )
    return float(generator.random())


def _normal_draw(
    *,
    seed: int,
    replication_id: int,
    process: str,
    day_index: int,
    entity: str,
    draw_id: str,
) -> float:
    generator = _draw_generator(
        seed=seed,
        replication_id=replication_id,
        process=process,
        day_index=day_index,
        entity=entity,
        draw_id=draw_id,
    )
    return float(generator.normal())


def _draw_generator(
    *,
    seed: int,
    replication_id: int,
    process: str,
    day_index: int,
    entity: str,
    draw_id: str,
) -> np.random.Generator:
    draw_key = "\x1f".join(
        (
            TAPE_VERSION,
            str(seed),
            str(replication_id),
            process,
            str(day_index),
            entity,
            draw_id,
        )
    ).encode("utf-8")
    counter_digest = sha256(b"counter\x00" + draw_key).digest()
    key_digest = sha256(b"key\x00" + draw_key).digest()
    counter = np.frombuffer(counter_digest, dtype="<u8")
    key = np.frombuffer(key_digest[:16], dtype="<u8")
    return np.random.Generator(np.random.Philox(counter=counter, key=key))


def _lookup[EntryValue: (int, float)](
    entries: tuple[tuple[str, EntryValue], ...], key: str
) -> EntryValue:
    for candidate, value in entries:
        if candidate == key:
            return value
    raise KeyError(f"unknown stochastic draw key '{key}'")


def _immutable_mapping[EntryValue: (int, float)](
    entries: tuple[tuple[str, EntryValue], ...],
) -> dict[str, EntryValue]:
    proxy = MappingProxyType(dict(entries))
    return cast(dict[str, EntryValue], proxy)
