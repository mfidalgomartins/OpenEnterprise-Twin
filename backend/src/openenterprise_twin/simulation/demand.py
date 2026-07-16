"""Demand transformations kept independent from random-number generation."""

from decimal import Decimal, localcontext


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
