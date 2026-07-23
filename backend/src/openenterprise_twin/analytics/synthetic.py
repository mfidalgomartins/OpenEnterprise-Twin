"""Deterministic synthetic operating history for the Northstar reference twin.

The generator is fully seeded: identical inputs produce an identical dataset and
therefore an identical ``data_digest``. Series are anchored on the authored
company parameters so a well-behaved calibration recovers them and earns a high
credibility score -- while realistic noise and seasonality keep the backtest
honest. No real enterprise data is ever used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np

from openenterprise_twin.analytics.history import (
    SERIES_REGISTRY,
    HistoricalDataset,
    HistoricalObservation,
    SeriesName,
    build_dataset,
)
from openenterprise_twin.domain.company import CompanyModel

#: Multiplicative demand factor by weekday (Mon..Sun); trade tapers at weekends.
_WEEKDAY_FACTORS = (1.08, 1.05, 1.02, 1.0, 0.96, 0.62, 0.55)
#: Multiplicative demand factor by calendar month (Jan..Dec); mild seasonality.
_MONTH_FACTORS = (
    0.92, 0.95, 1.02, 1.05, 1.08, 1.10,
    1.06, 1.00, 1.04, 1.03, 0.97, 0.88,
)


def generate_northstar_history(
    company: CompanyModel,
    *,
    seed: int = 20240115,
    days: int = 540,
    end_date: date = date(2025, 6, 30),
    dataset_id: str = "northstar-history",
) -> HistoricalDataset:
    """Return a reproducible synthetic dataset for the given company."""

    rng = np.random.default_rng(seed)
    start_date = end_date - timedelta(days=days - 1)
    calendar = [start_date + timedelta(days=offset) for offset in range(days)]
    observations: list[HistoricalObservation] = []

    for product in company.products:
        base_demand = float(
            sum(profile.daily_baseline_units for profile in product.demand_profiles)
        )
        for day in calendar:
            factor = (
                _WEEKDAY_FACTORS[day.weekday()] * _MONTH_FACTORS[day.month - 1]
            )
            noise = float(rng.lognormal(mean=0.0, sigma=0.08))
            demand = max(0.0, round(base_demand * factor * noise))
            sales = max(0.0, round(demand * float(rng.uniform(0.94, 0.99))))
            observations.append(
                _obs(day, "demand_units", product.product_id, demand)
            )
            observations.append(
                _obs(day, "sales_units", product.product_id, sales)
            )
        _emit_weekly(
            observations,
            calendar,
            "unit_price_cents",
            product.product_id,
            base=float(product.standard_price_cents),
            rng=rng,
            sigma=0.015,
        )
        _emit_weekly(
            observations,
            calendar,
            "variable_unit_cost_cents",
            product.product_id,
            base=float(product.standard_unit_cost_cents),
            rng=rng,
            sigma=0.02,
        )

    for resource in company.plant.resources:
        for day in calendar:
            factor = _WEEKDAY_FACTORS[day.weekday()]
            raw = 0.82 * factor + rng.normal(0.0, 0.05)
            utilization = float(np.clip(raw, 0.0, 1.5))
            observations.append(
                _obs(
                    day,
                    "capacity_utilization",
                    resource.resource_id,
                    utilization,
                    4,
                )
            )

    for material in company.plant.materials:
        _emit_weekly(
            observations,
            calendar,
            "supplier_lead_time_days",
            material.material_id,
            base=float(material.supplier_lead_time_days),
            rng=rng,
            sigma=0.10,
            round_digits=1,
        )

    for segment in company.customer_segments:
        _emit_monthly(
            observations,
            calendar,
            "payment_terms_days",
            segment.segment_id,
            base=float(segment.payment_terms_days),
            rng=rng,
        )

    for day in calendar:
        otif = float(np.clip(0.962 + rng.normal(0.0, 0.012), 0.0, 1.0))
        backlog = max(0.0, round(float(rng.normal(140.0, 35.0))))
        cash_flow = round(float(rng.normal(180_000.0, 900_000.0)))
        observations.append(_obs(day, "otif", None, otif, 4))
        observations.append(_obs(day, "backlog_units", None, backlog))
        observations.append(_obs(day, "cash_flow_cents", None, cash_flow))

    return build_dataset(
        dataset_id=dataset_id,
        company_id=company.company_id,
        observations=tuple(observations),
        source_kind="inline",
        source_reference=f"synthetic:northstar:seed={seed}:days={days}",
        timezone="UTC",
        ingested_at=datetime(2025, 7, 1, tzinfo=UTC),
    )


def _emit_weekly(
    observations: list[HistoricalObservation],
    calendar: list[date],
    series: SeriesName,
    entity_id: str | None,
    *,
    base: float,
    rng: np.random.Generator,
    sigma: float,
    round_digits: int = 0,
) -> None:
    for day in calendar[::7]:
        value = max(0.0, base * float(rng.lognormal(mean=0.0, sigma=sigma)))
        observations.append(_obs(day, series, entity_id, value, round_digits))


def _emit_monthly(
    observations: list[HistoricalObservation],
    calendar: list[date],
    series: SeriesName,
    entity_id: str | None,
    *,
    base: float,
    rng: np.random.Generator,
) -> None:
    seen_months: set[tuple[int, int]] = set()
    for day in calendar:
        key = (day.year, day.month)
        if key in seen_months:
            continue
        seen_months.add(key)
        value = max(0.0, round(base + float(rng.normal(0.0, 1.5))))
        observations.append(_obs(day, series, entity_id, value))


def _obs(
    day: date,
    series: SeriesName,
    entity_id: str | None,
    value: float,
    round_digits: int = 0,
) -> HistoricalObservation:
    rounded = round(value, round_digits) if round_digits else float(round(value))
    return HistoricalObservation(
        period_date=day,
        series=series,
        entity_id=entity_id,
        value=rounded,
        unit=SERIES_REGISTRY[series].unit,
    )
