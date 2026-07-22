"""Pure aggregation of Monte Carlo metric distributions.

Percentiles use NumPy's deterministic ``method="linear"`` estimator. This is
the default linear interpolation estimator (Hyndman-Fan type 7); for a single
observation every requested percentile is that observation.
"""

import math
from collections.abc import Iterable
from typing import Annotated, Literal

import numpy as np
from pydantic import ConfigDict, Field

from openenterprise_twin.domain.company import DomainModel

_NORMAL_95_CRITICAL_VALUE = 1.959963984540054


class MetricDistribution(DomainModel):
    """Immutable summary statistics for one simulated metric."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
    )

    mean: float
    median: float
    p5: float
    p10: float
    p90: float
    p95: float
    standard_deviation: Annotated[float, Field(ge=0.0)]
    breach_probability: Annotated[float, Field(ge=0.0, le=1.0)]
    breach_probability_ci95_lower: Annotated[float, Field(ge=0.0, le=1.0)]
    breach_probability_ci95_upper: Annotated[float, Field(ge=0.0, le=1.0)]
    cvar95: float


def summarize_distribution(
    values: Iterable[float],
    guardrail: float,
    breach_when: Literal["below", "above"],
    downside_tail: Literal["lower", "upper"],
) -> MetricDistribution:
    """Summarize a finite sample with strict empirical breach semantics.

    CVaR95 integrates exactly 5% of the empirical probability mass. When the
    boundary crosses an observation, that observation receives fractional
    weight instead of expanding the tail beyond 5%.
    """

    if isinstance(guardrail, bool):
        raise ValueError("guardrail must be a finite number")
    try:
        guardrail_is_finite = math.isfinite(guardrail)
    except TypeError as exc:
        raise ValueError("guardrail must be a finite number") from exc
    if not guardrail_is_finite:
        raise ValueError("guardrail must be a finite number")
    if breach_when not in ("below", "above"):
        raise ValueError("breach_when must be 'below' or 'above'")
    if downside_tail not in ("lower", "upper"):
        raise ValueError("downside_tail must be 'lower' or 'upper'")

    try:
        sample = np.asarray(tuple(values), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("values must be a one-dimensional finite sample") from exc
    if sample.ndim != 1 or sample.size == 0 or not np.isfinite(sample).all():
        raise ValueError("values must be a non-empty finite sample")

    p5, p10, median, p90, p95 = np.quantile(
        sample,
        [0.05, 0.10, 0.50, 0.90, 0.95],
        method="linear",
    )
    breaches = sample < guardrail if breach_when == "below" else sample > guardrail
    breach_count = int(np.count_nonzero(breaches))
    breach_probability = breach_count / sample.size
    breach_ci_lower, breach_ci_upper = _wilson_interval(
        successes=breach_count,
        observations=int(sample.size),
    )
    ordered = np.sort(sample)
    tail_mass = 0.05 * sample.size
    full_count = math.floor(tail_mass)
    fractional_weight = tail_mass - full_count
    tail_ordered = ordered if downside_tail == "lower" else ordered[::-1]
    weighted_sum = float(np.sum(tail_ordered[:full_count]))
    if fractional_weight > 0.0:
        weighted_sum += fractional_weight * float(tail_ordered[full_count])
    if full_count == 0 and fractional_weight == 0.0:
        raise AssertionError("a non-empty sample must have positive tail mass")
    return MetricDistribution(
        mean=float(np.mean(sample)),
        median=float(median),
        p5=float(p5),
        p10=float(p10),
        p90=float(p90),
        p95=float(p95),
        standard_deviation=float(np.std(sample, ddof=0)),
        breach_probability=float(breach_probability),
        breach_probability_ci95_lower=breach_ci_lower,
        breach_probability_ci95_upper=breach_ci_upper,
        cvar95=weighted_sum / tail_mass,
    )


def _wilson_interval(*, successes: int, observations: int) -> tuple[float, float]:
    """Return a bounded 95% Wilson score interval for a binomial proportion."""

    probability = successes / observations
    z_squared = _NORMAL_95_CRITICAL_VALUE**2
    denominator = 1.0 + z_squared / observations
    centre = (probability + z_squared / (2.0 * observations)) / denominator
    half_width = (
        _NORMAL_95_CRITICAL_VALUE
        * math.sqrt(
            probability * (1.0 - probability) / observations
            + z_squared / (4.0 * observations**2)
        )
        / denominator
    )
    return max(0.0, centre - half_width), min(1.0, centre + half_width)
