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
    cvar95: float


def summarize_distribution(
    values: Iterable[float],
    guardrail: float,
    breach_when: Literal["below", "above"],
    downside_tail: Literal["lower", "upper"],
) -> MetricDistribution:
    """Summarize a finite sample with strict empirical breach semantics.

    CVaR95 uses the mean of the ``max(1, ceil(0.05 * n))`` worst ordered
    observations, making the tail definition non-empty for every sample size.
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
    ordered = np.sort(sample)
    tail_count = max(1, math.ceil(0.05 * sample.size))
    tail = ordered[:tail_count] if downside_tail == "lower" else ordered[-tail_count:]
    return MetricDistribution(
        mean=float(np.mean(sample)),
        median=float(median),
        p5=float(p5),
        p10=float(p10),
        p90=float(p90),
        p95=float(p95),
        standard_deviation=float(np.std(sample, ddof=0)),
        breach_probability=float(np.count_nonzero(breaches) / sample.size),
        cvar95=float(np.mean(tail)),
    )
