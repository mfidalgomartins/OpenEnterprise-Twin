"""Temporal (out-of-sample) backtesting of a calibrated twin.

Calibration is fit on an in-sample window and validated on a strictly later
window -- never a random split, so leakage across time is impossible. For each
calibrated observable we compare the calibration's forecast (mean adjusted by
its seasonal index) against realised history, reporting both point error and
probabilistic interval coverage.
"""

from __future__ import annotations

from datetime import date
from math import sqrt
from typing import Annotated

from pydantic import Field

from openenterprise_twin.analytics._digest import canonical_digest
from openenterprise_twin.analytics.calibration import (
    Z_95,
    EstimatedParameter,
    SeasonalityEstimate,
    calibrate_twin,
)
from openenterprise_twin.analytics.history import HistoricalDataset, SeriesName
from openenterprise_twin.domain.company import CompanyModel, DomainModel, Identifier
from openenterprise_twin.domain.errors import DomainValidationError

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]


class KpiBacktest(DomainModel):
    """Out-of-sample error and interval coverage for one calibrated observable."""

    series: SeriesName
    entity_id: Identifier | None
    sample_size: Annotated[int, Field(gt=0)]
    mean_absolute_error: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    root_mean_squared_error: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    weighted_mape: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    bias: FiniteFloat
    interval_coverage: UnitInterval
    nominal_coverage: UnitInterval


class BacktestResult(DomainModel):
    """A reproducible temporal backtest of one calibration."""

    calibration_id: Identifier
    calibration_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    in_sample_start: date
    in_sample_end: date
    validation_start: date
    validation_end: date
    kpis: tuple[KpiBacktest, ...]
    overall_weighted_mape: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    overall_interval_coverage: UnitInterval
    nominal_coverage: UnitInterval
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @property
    def evaluated_kpis(self) -> int:
        return len(self.kpis)


def backtest_calibration(
    *,
    dataset: HistoricalDataset,
    company: CompanyModel,
    cutoff: date,
    calibration_id: str = "backtest",
    nominal_coverage: float = 0.95,
) -> BacktestResult:
    """Calibrate on history up to ``cutoff`` and validate strictly after it."""

    start, end = dataset.window
    if not start <= cutoff < end:
        raise DomainValidationError(
            "backtest cutoff must fall inside the dataset window"
        )
    calibration = calibrate_twin(
        calibration_id=calibration_id,
        dataset=dataset,
        company=company,
        window=(start, cutoff),
    )
    validation_start = _next_day(cutoff)
    seasonality_by_key = {
        (item.series, item.entity_id): item for item in calibration.seasonality
    }
    kpis: list[KpiBacktest] = []
    for parameter in calibration.parameters:
        if parameter.provenance == "assumed":
            continue
        # Structural (seasonal index) parameters are not per-period forecasts of
        # their series, so they are excluded from out-of-sample error checks.
        if parameter.method.startswith("bucketed_index"):
            continue
        kpi = _backtest_parameter(
            parameter=parameter,
            dataset=dataset,
            window=(validation_start, end),
            seasonality=seasonality_by_key.get(
                (parameter.series, parameter.entity_id)
            ),
            nominal_coverage=nominal_coverage,
        )
        if kpi is not None:
            kpis.append(kpi)

    ordered = tuple(sorted(kpis, key=lambda k: (k.series, k.entity_id or "")))
    overall_wmape = _pooled_weighted_mape(ordered)
    overall_coverage = (
        round(sum(k.interval_coverage for k in ordered) / len(ordered), 6)
        if ordered
        else 0.0
    )
    digest = _backtest_digest(
        calibration_digest=calibration.digest,
        cutoff=cutoff,
        kpis=ordered,
    )
    return BacktestResult(
        calibration_id=calibration.calibration_id,
        calibration_digest=calibration.digest,
        in_sample_start=start,
        in_sample_end=cutoff,
        validation_start=validation_start,
        validation_end=end,
        kpis=ordered,
        overall_weighted_mape=overall_wmape,
        overall_interval_coverage=overall_coverage,
        nominal_coverage=nominal_coverage,
        digest=digest,
    )


def backtest_rolling(
    *,
    dataset: HistoricalDataset,
    company: CompanyModel,
    cutoffs: tuple[date, ...],
    nominal_coverage: float = 0.95,
) -> tuple[BacktestResult, ...]:
    """Run multiple expanding-window backtests, one per cutoff date."""

    if not cutoffs:
        raise DomainValidationError("at least one cutoff is required")
    return tuple(
        backtest_calibration(
            dataset=dataset,
            company=company,
            cutoff=cutoff,
            calibration_id=f"backtest-{index}",
            nominal_coverage=nominal_coverage,
        )
        for index, cutoff in enumerate(sorted(set(cutoffs)))
    )


def _backtest_parameter(
    *,
    parameter: EstimatedParameter,
    dataset: HistoricalDataset,
    window: tuple[date, date],
    seasonality: SeasonalityEstimate | None,
    nominal_coverage: float,
) -> KpiBacktest | None:
    start, end = window
    observations = [
        observation
        for observation in dataset.observations_for(
            parameter.series, parameter.entity_id
        )
        if start <= observation.period_date <= end
    ]
    if not observations:
        return None

    half_width = Z_95 * parameter.dispersion
    absolute_errors: list[float] = []
    squared_errors: list[float] = []
    signed_errors: list[float] = []
    observed_magnitude = 0.0
    covered = 0
    for observation in observations:
        predicted = parameter.point_estimate * _seasonal_factor(
            seasonality, observation.period_date
        )
        error = predicted - observation.value
        absolute_errors.append(abs(error))
        squared_errors.append(error * error)
        signed_errors.append(error)
        observed_magnitude += abs(observation.value)
        lower = predicted - half_width
        upper = predicted + half_width
        if lower <= observation.value <= upper:
            covered += 1

    count = len(observations)
    mae = sum(absolute_errors) / count
    rmse = sqrt(sum(squared_errors) / count)
    wmape = sum(absolute_errors) / observed_magnitude if observed_magnitude else 0.0
    bias = sum(signed_errors) / count
    coverage = covered / count
    return KpiBacktest(
        series=parameter.series,
        entity_id=parameter.entity_id,
        sample_size=count,
        mean_absolute_error=round(mae, 6),
        root_mean_squared_error=round(rmse, 6),
        weighted_mape=round(wmape, 6),
        bias=round(bias, 6),
        interval_coverage=round(coverage, 6),
        nominal_coverage=nominal_coverage,
    )


def _seasonal_factor(
    seasonality: SeasonalityEstimate | None, period_date: date
) -> float:
    if seasonality is None:
        return 1.0
    bucket = (
        period_date.month
        if seasonality.period == "yearly"
        else period_date.weekday()
    )
    for factor in seasonality.factors:
        if factor.bucket == bucket:
            return factor.factor
    return 1.0


def _pooled_weighted_mape(kpis: tuple[KpiBacktest, ...]) -> float:
    if not kpis:
        return 0.0
    weighted = sum(kpi.weighted_mape * kpi.sample_size for kpi in kpis)
    total = sum(kpi.sample_size for kpi in kpis)
    return round(weighted / total, 6) if total else 0.0


def _next_day(day: date) -> date:
    return date.fromordinal(day.toordinal() + 1)


def _backtest_digest(
    *,
    calibration_digest: str,
    cutoff: date,
    kpis: tuple[KpiBacktest, ...],
) -> str:
    body = {
        "calibration_digest": calibration_digest,
        "cutoff": cutoff.isoformat(),
        "kpis": [kpi.model_dump(mode="json") for kpi in kpis],
    }
    return canonical_digest(body)


__all__ = [
    "BacktestResult",
    "KpiBacktest",
    "backtest_calibration",
    "backtest_rolling",
]
