"""Demand transformations kept independent from random-number generation."""

import math
from decimal import Decimal, localcontext

_DAYS_PER_YEAR = 365
_QUANTILE_RECURRENCE_LIMIT = 100_000
_BETA_MAX_ITERATIONS = 200
_BETA_EPSILON = 3e-14
_BETA_MIN_DENOMINATOR = 1e-300
_COLLECTION_DELAY_CDF: tuple[tuple[float, int], ...] = (
    (0.10, -3),
    (0.65, 0),
    (0.85, 2),
    (0.95, 7),
    (1.00, 14),
)


def expected_daily_units(
    *,
    baseline_units: Decimal,
    price_change: Decimal,
    elasticity: Decimal,
    demand_multiplier: Decimal,
) -> Decimal:
    """Return conditional expected units under a constant-elasticity curve."""

    if baseline_units < 0:
        raise ValueError("baseline_units must be non-negative")
    if price_change <= Decimal("-1"):
        raise ValueError("price_change must preserve a positive price")
    if demand_multiplier < 0:
        raise ValueError("demand_multiplier must be non-negative")

    with localcontext() as context:
        context.prec = 28
        price_factor = (Decimal("1") + price_change) ** elasticity
        return baseline_units * price_factor * demand_multiplier


def seasonality_multiplier(day_index: int, amplitude: float) -> float:
    """Return an annual multiplier starting at the mean and rising to its peak."""

    if isinstance(day_index, bool) or day_index < 0:
        raise ValueError("day_index must be a non-negative integer")
    if not math.isfinite(amplitude) or not 0.0 <= amplitude < 1.0:
        raise ValueError("amplitude must be finite and in [0, 1)")

    phase = 2.0 * math.pi * (day_index % _DAYS_PER_YEAR) / _DAYS_PER_YEAR
    return 1.0 + amplitude * math.sin(phase)


def _validate_uniform(uniform: float) -> None:
    if not math.isfinite(uniform) or not 0.0 <= uniform < 1.0:
        raise ValueError("uniform must be finite and in [0, 1)")


def _beta_continued_fraction(
    first_shape: float, second_shape: float, x: float
) -> float:
    total_shape = first_shape + second_shape
    first_adjusted = first_shape + 1.0
    first_reduced = first_shape - 1.0
    c_value = 1.0
    d_value = 1.0 - total_shape * x / first_adjusted
    if abs(d_value) < _BETA_MIN_DENOMINATOR:
        d_value = _BETA_MIN_DENOMINATOR
    d_value = 1.0 / d_value
    result = d_value

    for iteration in range(1, _BETA_MAX_ITERATIONS + 1):
        doubled = 2 * iteration
        even_term = (
            iteration
            * (second_shape - iteration)
            * x
            / ((first_reduced + doubled) * (first_shape + doubled))
        )
        d_value = 1.0 + even_term * d_value
        if abs(d_value) < _BETA_MIN_DENOMINATOR:
            d_value = _BETA_MIN_DENOMINATOR
        c_value = 1.0 + even_term / c_value
        if abs(c_value) < _BETA_MIN_DENOMINATOR:
            c_value = _BETA_MIN_DENOMINATOR
        d_value = 1.0 / d_value
        result *= d_value * c_value

        odd_term = -(
            (first_shape + iteration)
            * (total_shape + iteration)
            * x
            / ((first_shape + doubled) * (first_adjusted + doubled))
        )
        d_value = 1.0 + odd_term * d_value
        if abs(d_value) < _BETA_MIN_DENOMINATOR:
            d_value = _BETA_MIN_DENOMINATOR
        c_value = 1.0 + odd_term / c_value
        if abs(c_value) < _BETA_MIN_DENOMINATOR:
            c_value = _BETA_MIN_DENOMINATOR
        d_value = 1.0 / d_value
        delta = d_value * c_value
        result *= delta
        if abs(delta - 1.0) <= _BETA_EPSILON:
            return result

    raise ArithmeticError("regularized beta continued fraction did not converge")


def _regularized_beta(x: float, first_shape: float, second_shape: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    log_scale = (
        math.lgamma(first_shape + second_shape)
        - math.lgamma(first_shape)
        - math.lgamma(second_shape)
        + first_shape * math.log(x)
        + second_shape * math.log1p(-x)
    )
    scale = math.exp(log_scale)
    if x < (first_shape + 1.0) / (first_shape + second_shape + 2.0):
        result = (
            scale
            * _beta_continued_fraction(first_shape, second_shape, x)
            / first_shape
        )
    else:
        result = 1.0 - (
            scale
            * _beta_continued_fraction(second_shape, first_shape, 1.0 - x)
            / second_shape
        )
    return min(1.0, max(0.0, result))


def _negative_binomial_cdf(
    count: int, dispersion: float, success_probability: float
) -> float:
    return _regularized_beta(success_probability, dispersion, float(count + 1))


def _negative_binomial_quantile_by_search(
    mean: float,
    dispersion: float,
    success_probability: float,
    uniform: float,
) -> int:
    lower = 0
    upper = max(1, math.ceil(mean))
    while _negative_binomial_cdf(upper, dispersion, success_probability) < uniform:
        lower = upper + 1
        upper = upper * 2 + 1

    while lower < upper:
        midpoint = (lower + upper) // 2
        if (
            _negative_binomial_cdf(midpoint, dispersion, success_probability)
            >= uniform
        ):
            upper = midpoint
        else:
            lower = midpoint + 1
    return lower


def negative_binomial_quantile(
    mean: float, dispersion: float, uniform: float
) -> int:
    """Return an inverse-CDF NB2 count with variance ``mean + mean² / dispersion``."""

    if not math.isfinite(mean) or mean < 0.0:
        raise ValueError("mean must be finite and non-negative")
    if not math.isfinite(dispersion) or dispersion <= 0.0:
        raise ValueError("dispersion must be finite and positive")
    _validate_uniform(uniform)
    if mean == 0.0:
        return 0

    success_probability = dispersion / (dispersion + mean)
    failure_probability = 1.0 - success_probability
    probability = math.exp(dispersion * math.log(success_probability))
    cumulative_probability = probability
    count = 0

    while uniform > cumulative_probability and count < _QUANTILE_RECURRENCE_LIMIT:
        count += 1
        probability *= (
            (count - 1 + dispersion) / count * failure_probability
        )
        updated_cumulative = cumulative_probability + probability
        if updated_cumulative == cumulative_probability:
            break
        cumulative_probability = updated_cumulative

    if uniform <= cumulative_probability:
        return count
    return _negative_binomial_quantile_by_search(
        mean, dispersion, success_probability, uniform
    )


def _binomial_cdf(count: int, trials: int, probability: float) -> float:
    if count >= trials:
        return 1.0
    return _regularized_beta(
        1.0 - probability, float(trials - count), float(count + 1)
    )


def _binomial_quantile_by_search(
    trials: int, probability: float, uniform: float
) -> int:
    lower = 0
    upper = trials
    while lower < upper:
        midpoint = (lower + upper) // 2
        if _binomial_cdf(midpoint, trials, probability) >= uniform:
            upper = midpoint
        else:
            lower = midpoint + 1
    return lower


def binomial_quantile(trials: int, probability: float, uniform: float) -> int:
    """Return the inverse-CDF count for a binomial distribution."""

    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 0:
        raise ValueError("trials must be a non-negative integer")
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be finite and in [0, 1]")
    _validate_uniform(uniform)
    if trials == 0 or probability == 0.0:
        return 0
    if probability == 1.0:
        return trials

    failure_probability = 1.0 - probability
    point_probability = math.exp(trials * math.log(failure_probability))
    cumulative_probability = point_probability
    count = 0

    while uniform > cumulative_probability and count < trials:
        count += 1
        point_probability *= (
            (trials - count + 1) / count * probability / failure_probability
        )
        updated_cumulative = cumulative_probability + point_probability
        if updated_cumulative == cumulative_probability:
            break
        cumulative_probability = updated_cumulative

    if uniform <= cumulative_probability:
        return count
    return _binomial_quantile_by_search(trials, probability, uniform)


def collection_delay_days(uniform: float) -> int:
    """Map a uniform draw to a bounded delay relative to contractual terms."""

    _validate_uniform(uniform)
    for cumulative_probability, delay_days in _COLLECTION_DELAY_CDF:
        if uniform < cumulative_probability:
            return delay_days
    raise AssertionError("validated uniform must map to a collection delay")
