import math
from decimal import Decimal

import pytest

from openenterprise_twin.simulation import demand


def test_expected_daily_units_preserves_constant_elasticity_behavior() -> None:
    result = demand.expected_daily_units(
        baseline_units=Decimal("100"),
        price_change=Decimal("0.25"),
        elasticity=Decimal("-1"),
        demand_multiplier=Decimal("1.10"),
    )

    assert result == Decimal("88.0")


def test_seasonality_has_deterministic_phase_and_annual_period() -> None:
    amplitude = 0.2

    assert demand.seasonality_multiplier(0, amplitude) == pytest.approx(1.0)
    assert demand.seasonality_multiplier(365, amplitude) == pytest.approx(1.0)
    assert demand.seasonality_multiplier(91, amplitude) == pytest.approx(
        1.2, abs=2e-5
    )
    assert demand.seasonality_multiplier(274, amplitude) == pytest.approx(
        0.8, abs=2e-5
    )


def test_seasonality_has_annual_mean_one_and_remains_positive() -> None:
    values = [demand.seasonality_multiplier(day, 0.75) for day in range(365)]

    assert math.fsum(values) / len(values) == pytest.approx(1.0, abs=1e-12)
    assert min(values) > 0.0


@pytest.mark.parametrize("day_index", [-1, True])
def test_seasonality_rejects_invalid_day_index(day_index: int) -> None:
    with pytest.raises(ValueError, match="day_index"):
        demand.seasonality_multiplier(day_index, 0.2)


@pytest.mark.parametrize("amplitude", [-0.01, 1.0, math.inf, math.nan])
def test_seasonality_rejects_invalid_amplitude(amplitude: float) -> None:
    with pytest.raises(ValueError, match="amplitude"):
        demand.seasonality_multiplier(0, amplitude)


def test_negative_binomial_quantile_obeys_exact_cdf_boundaries() -> None:
    just_above_quarter = math.nextafter(0.25, 1.0)
    just_above_half = math.nextafter(0.5, 1.0)

    assert demand.negative_binomial_quantile(2.0, 2.0, 0.0) == 0
    assert demand.negative_binomial_quantile(2.0, 2.0, 0.25) == 0
    assert demand.negative_binomial_quantile(2.0, 2.0, just_above_quarter) == 1
    assert demand.negative_binomial_quantile(2.0, 2.0, 0.5) == 1
    assert demand.negative_binomial_quantile(2.0, 2.0, just_above_half) == 2


def test_negative_binomial_quantile_is_monotonic() -> None:
    uniforms = [index / 1_000 for index in range(1_000)]
    draws = [
        demand.negative_binomial_quantile(8.0, 3.5, uniform)
        for uniform in uniforms
    ]

    assert draws == sorted(draws)


def test_negative_binomial_quantile_matches_nb2_mean_on_uniform_grid() -> None:
    uniforms = [(index + 0.5) / 10_000 for index in range(10_000)]
    draws = [
        demand.negative_binomial_quantile(7.5, 3.2, uniform)
        for uniform in uniforms
    ]

    assert math.fsum(draws) / len(draws) == pytest.approx(7.5, abs=0.03)


@pytest.mark.parametrize("uniform", [0.0, 0.4, math.nextafter(1.0, 0.0)])
def test_negative_binomial_quantile_returns_zero_for_zero_mean(
    uniform: float,
) -> None:
    assert demand.negative_binomial_quantile(0.0, 2.0, uniform) == 0


@pytest.mark.parametrize("mean", [-0.01, math.inf, math.nan])
def test_negative_binomial_quantile_rejects_invalid_mean(mean: float) -> None:
    with pytest.raises(ValueError, match="mean"):
        demand.negative_binomial_quantile(mean, 2.0, 0.5)


@pytest.mark.parametrize("dispersion", [-1.0, 0.0, math.inf, math.nan])
def test_negative_binomial_quantile_rejects_invalid_dispersion(
    dispersion: float,
) -> None:
    with pytest.raises(ValueError, match="dispersion"):
        demand.negative_binomial_quantile(2.0, dispersion, 0.5)


@pytest.mark.parametrize("uniform", [-0.01, 1.0, math.inf, math.nan])
def test_negative_binomial_quantile_rejects_invalid_uniform(
    uniform: float,
) -> None:
    with pytest.raises(ValueError, match="uniform"):
        demand.negative_binomial_quantile(2.0, 2.0, uniform)


def test_binomial_quantile_obeys_exact_cdf_boundaries() -> None:
    just_above_quarter = math.nextafter(0.25, 1.0)
    just_above_three_quarters = math.nextafter(0.75, 1.0)

    assert demand.binomial_quantile(2, 0.5, 0.0) == 0
    assert demand.binomial_quantile(2, 0.5, 0.25) == 0
    assert demand.binomial_quantile(2, 0.5, just_above_quarter) == 1
    assert demand.binomial_quantile(2, 0.5, 0.75) == 1
    assert demand.binomial_quantile(2, 0.5, just_above_three_quarters) == 2


def test_binomial_quantile_handles_degenerate_distributions() -> None:
    largest_uniform = math.nextafter(1.0, 0.0)

    assert demand.binomial_quantile(0, 0.5, largest_uniform) == 0
    assert demand.binomial_quantile(12, 0.0, largest_uniform) == 0
    assert demand.binomial_quantile(12, 1.0, 0.0) == 12


def test_binomial_quantile_is_monotonic() -> None:
    uniforms = [index / 1_000 for index in range(1_000)]
    draws = [demand.binomial_quantile(25, 0.35, uniform) for uniform in uniforms]

    assert draws == sorted(draws)


def test_binomial_quantile_matches_mean_on_uniform_grid() -> None:
    uniforms = [(index + 0.5) / 10_000 for index in range(10_000)]
    draws = [demand.binomial_quantile(20, 0.3, uniform) for uniform in uniforms]

    assert math.fsum(draws) / len(draws) == pytest.approx(6.0, abs=0.01)


@pytest.mark.parametrize("trials", [-1, True, 2.5])
def test_binomial_quantile_rejects_invalid_trials(trials: int) -> None:
    with pytest.raises(ValueError, match="trials"):
        demand.binomial_quantile(trials, 0.5, 0.5)


@pytest.mark.parametrize("probability", [-0.01, 1.01, math.inf, math.nan])
def test_binomial_quantile_rejects_invalid_probability(probability: float) -> None:
    with pytest.raises(ValueError, match="probability"):
        demand.binomial_quantile(10, probability, 0.5)


@pytest.mark.parametrize("uniform", [-0.01, 1.0, math.inf, math.nan])
def test_binomial_quantile_rejects_invalid_uniform(uniform: float) -> None:
    with pytest.raises(ValueError, match="uniform"):
        demand.binomial_quantile(10, 0.5, uniform)


@pytest.mark.parametrize(
    ("uniform", "expected_delay"),
    [
        (0.0, -3),
        (math.nextafter(0.10, 0.0), -3),
        (0.10, 0),
        (math.nextafter(0.65, 0.0), 0),
        (0.65, 2),
        (math.nextafter(0.85, 0.0), 2),
        (0.85, 7),
        (math.nextafter(0.95, 0.0), 7),
        (0.95, 14),
        (math.nextafter(1.0, 0.0), 14),
    ],
)
def test_collection_delay_days_obeys_explicit_boundaries(
    uniform: float, expected_delay: int
) -> None:
    assert demand.collection_delay_days(uniform) == expected_delay


def test_collection_delay_days_is_monotonic_with_expected_mean() -> None:
    uniforms = [(index + 0.5) / 10_000 for index in range(10_000)]
    delays = [demand.collection_delay_days(uniform) for uniform in uniforms]

    assert delays == sorted(delays)
    assert math.fsum(delays) / len(delays) == pytest.approx(1.5, abs=1e-12)


@pytest.mark.parametrize("uniform", [-0.01, 1.0, math.inf, math.nan])
def test_collection_delay_days_rejects_invalid_uniform(uniform: float) -> None:
    with pytest.raises(ValueError, match="uniform"):
        demand.collection_delay_days(uniform)
