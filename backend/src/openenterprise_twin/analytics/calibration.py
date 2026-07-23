"""Estimate twin parameters and seasonality from canonical historical data.

Calibration is deterministic and fully traceable. Every parameter is tagged as
``observed`` (estimated from a sufficient sample), ``estimated`` (from a small
sample, with wider uncertainty) or ``assumed`` (no data -- carried over from the
authored company model). Confidence intervals use a normal approximation of the
sampling error of the mean; seasonality is a bucketed multiplicative index.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from math import sqrt
from statistics import mean, stdev
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openenterprise_twin.analytics._digest import canonical_digest
from openenterprise_twin.analytics.history import (
    SERIES_REGISTRY,
    HistoricalDataset,
    HistoricalObservation,
    SeriesName,
)
from openenterprise_twin.domain.company import CompanyModel, DomainModel, Identifier
from openenterprise_twin.domain.errors import DomainValidationError

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
Provenance = Literal["observed", "estimated", "assumed"]

#: Sample size at or above which an estimate is treated as directly observed.
OBSERVED_SAMPLE_THRESHOLD = 30
#: z-score for a two-sided 95% normal confidence interval of the mean.
Z_95 = 1.959963984540054
#: Minimum span (days) before seasonality is estimated on yearly buckets.
YEARLY_SEASONALITY_MIN_SPAN = 400


class ConfidenceInterval(DomainModel):
    """A symmetric confidence interval for a point estimate."""

    lower: FiniteFloat
    upper: FiniteFloat
    level: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.95

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.upper < self.lower:
            raise DomainValidationError("confidence interval upper < lower")
        return self

    @property
    def half_width(self) -> float:
        return (self.upper - self.lower) / 2.0


class EstimatedParameter(DomainModel):
    """One calibrated parameter with provenance and quantified uncertainty."""

    name: Annotated[str, Field(min_length=1, max_length=96)]
    series: SeriesName
    entity_id: Identifier | None
    provenance: Provenance
    point_estimate: FiniteFloat
    dispersion: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    sample_size: Annotated[int, Field(ge=0)]
    confidence_interval: ConfidenceInterval | None
    method: Annotated[str, Field(min_length=1, max_length=64)]
    warnings: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = ()

    @property
    def relative_uncertainty(self) -> float:
        if self.confidence_interval is None or self.point_estimate == 0:
            return 0.0
        return abs(self.confidence_interval.half_width / self.point_estimate)


class SeasonalFactor(DomainModel):
    """One multiplicative seasonal index for a calendar bucket."""

    bucket: Annotated[int, Field(ge=0, le=366)]
    factor: Annotated[float, Field(gt=0.0, allow_inf_nan=False)]


class SeasonalityEstimate(DomainModel):
    """A bucketed multiplicative seasonality profile for one series/entity."""

    series: SeriesName
    entity_id: Identifier | None
    period: Literal["weekly", "yearly"]
    amplitude: Annotated[float, Field(ge=0.0, le=1.0)]
    factors: Annotated[tuple[SeasonalFactor, ...], Field(min_length=2)]
    sample_size: Annotated[int, Field(gt=0)]


class CalibrationResult(DomainModel):
    """A versioned, content-addressed calibration of one twin from history."""

    calibration_id: Identifier
    company_id: Identifier
    company_model_version: Annotated[
        str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    ]
    dataset_id: Identifier
    data_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    window_start: date
    window_end: date
    created_at: datetime
    parameters: tuple[EstimatedParameter, ...]
    seasonality: tuple[SeasonalityEstimate, ...]
    warnings: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def validate_created_at(self) -> Self:
        if self.created_at.tzinfo is None:
            raise DomainValidationError("created_at must be timezone-aware")
        return self

    @property
    def provenance_mix(self) -> dict[Provenance, int]:
        counts: dict[Provenance, int] = {
            "observed": 0,
            "estimated": 0,
            "assumed": 0,
        }
        for parameter in self.parameters:
            counts[parameter.provenance] += 1
        return counts

    def parameter(self, name: str) -> EstimatedParameter | None:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        return None


def calibrate_twin(
    *,
    calibration_id: str,
    dataset: HistoricalDataset,
    company: CompanyModel,
    window: tuple[date, date] | None = None,
    created_at: datetime | None = None,
) -> CalibrationResult:
    """Estimate twin parameters from a dataset within an optional time window."""

    if dataset.company_id != company.company_id:
        raise DomainValidationError(
            "dataset company_id does not match the company model"
        )
    start, end = window if window is not None else dataset.window
    if end < start:
        raise DomainValidationError("calibration window end precedes its start")

    parameters: list[EstimatedParameter] = []
    seasonality: list[SeasonalityEstimate] = []
    warnings: list[str] = []

    for product in company.products:
        _calibrate_product(
            product_id=product.product_id,
            assumed_price=float(product.standard_price_cents),
            assumed_cost=float(product.standard_unit_cost_cents),
            assumed_demand=float(
                sum(
                    profile.daily_baseline_units
                    for profile in product.demand_profiles
                )
            ),
            assumed_seasonality=float(
                mean(
                    float(profile.seasonality_amplitude)
                    for profile in product.demand_profiles
                )
            ),
            dataset=dataset,
            window=(start, end),
            parameters=parameters,
            seasonality=seasonality,
        )

    for material in company.plant.materials:
        _calibrate_scalar(
            series="supplier_lead_time_days",
            entity_id=material.material_id,
            assumed=float(material.supplier_lead_time_days),
            dataset=dataset,
            window=(start, end),
            parameters=parameters,
            name=f"lead_time:{material.material_id}",
        )

    for segment in company.customer_segments:
        _calibrate_scalar(
            series="payment_terms_days",
            entity_id=segment.segment_id,
            assumed=float(segment.payment_terms_days),
            dataset=dataset,
            window=(start, end),
            parameters=parameters,
            name=f"payment_terms:{segment.segment_id}",
        )

    for resource in company.plant.resources:
        _calibrate_optional(
            series="capacity_utilization",
            entity_id=resource.resource_id,
            dataset=dataset,
            window=(start, end),
            parameters=parameters,
            name=f"capacity_utilization:{resource.resource_id}",
        )

    for series, name in (("otif", "otif"), ("backlog_units", "backlog")):
        _calibrate_optional(
            series=series,  # type: ignore[arg-type]
            entity_id=None,
            dataset=dataset,
            window=(start, end),
            parameters=parameters,
            name=name,
        )

    assumed = [p for p in parameters if p.provenance == "assumed"]
    if assumed:
        warnings.append(
            f"{len(assumed)} parameter(s) are assumed from the authored model "
            "because history was unavailable"
        )
    low_confidence = [p for p in parameters if p.relative_uncertainty > 0.25]
    if low_confidence:
        warnings.append(
            f"{len(low_confidence)} parameter(s) have wide (>25%) confidence "
            "intervals"
        )

    ordered_parameters = tuple(
        sorted(parameters, key=lambda p: (p.series, p.entity_id or "", p.name))
    )
    ordered_seasonality = tuple(
        sorted(seasonality, key=lambda s: (s.series, s.entity_id or ""))
    )
    digest = _calibration_digest(
        calibration_id=calibration_id,
        data_digest=dataset.data_digest,
        window=(start, end),
        parameters=ordered_parameters,
        seasonality=ordered_seasonality,
    )
    return CalibrationResult(
        calibration_id=calibration_id,
        company_id=company.company_id,
        company_model_version=company.model_version,
        dataset_id=dataset.dataset_id,
        data_digest=dataset.data_digest,
        window_start=start,
        window_end=end,
        created_at=created_at or datetime.now(UTC),
        parameters=ordered_parameters,
        seasonality=ordered_seasonality,
        warnings=tuple(warnings),
        digest=digest,
    )


class ParameterDelta(DomainModel):
    """A parameter-level difference between two calibrations."""

    name: str
    baseline_estimate: FiniteFloat | None
    candidate_estimate: FiniteFloat | None
    absolute_change: FiniteFloat | None
    relative_change: FiniteFloat | None
    baseline_provenance: Provenance | None
    candidate_provenance: Provenance | None


class CalibrationComparison(DomainModel):
    """A deterministic comparison of two calibrations of the same twin."""

    baseline_calibration_id: Identifier
    candidate_calibration_id: Identifier
    deltas: tuple[ParameterDelta, ...]
    max_relative_change: Annotated[float, Field(ge=0.0)]


def compare_calibrations(
    baseline: CalibrationResult, candidate: CalibrationResult
) -> CalibrationComparison:
    """Compare two calibrations parameter by parameter."""

    if baseline.company_id != candidate.company_id:
        raise DomainValidationError("calibrations describe different companies")
    baseline_by_name = {p.name: p for p in baseline.parameters}
    candidate_by_name = {p.name: p for p in candidate.parameters}
    names = sorted(set(baseline_by_name) | set(candidate_by_name))
    deltas: list[ParameterDelta] = []
    max_relative = 0.0
    for name in names:
        base = baseline_by_name.get(name)
        cand = candidate_by_name.get(name)
        base_value = base.point_estimate if base else None
        cand_value = cand.point_estimate if cand else None
        absolute: float | None = None
        relative: float | None = None
        if base_value is not None and cand_value is not None:
            absolute = cand_value - base_value
            if base_value != 0.0:
                relative = absolute / base_value
        if relative is not None:
            max_relative = max(max_relative, abs(relative))
        deltas.append(
            ParameterDelta(
                name=name,
                baseline_estimate=base_value,
                candidate_estimate=cand_value,
                absolute_change=absolute,
                relative_change=relative,
                baseline_provenance=base.provenance if base else None,
                candidate_provenance=cand.provenance if cand else None,
            )
        )
    return CalibrationComparison(
        baseline_calibration_id=baseline.calibration_id,
        candidate_calibration_id=candidate.calibration_id,
        deltas=tuple(deltas),
        max_relative_change=round(max_relative, 6),
    )


def _calibrate_product(
    *,
    product_id: str,
    assumed_price: float,
    assumed_cost: float,
    assumed_demand: float,
    assumed_seasonality: float,
    dataset: HistoricalDataset,
    window: tuple[date, date],
    parameters: list[EstimatedParameter],
    seasonality: list[SeasonalityEstimate],
) -> None:
    parameters.append(
        _estimate_or_assume(
            series="demand_units",
            entity_id=product_id,
            assumed=assumed_demand,
            dataset=dataset,
            window=window,
            name=f"demand_baseline:{product_id}",
        )
    )
    parameters.append(
        _estimate_or_assume(
            series="unit_price_cents",
            entity_id=product_id,
            assumed=assumed_price,
            dataset=dataset,
            window=window,
            name=f"unit_price:{product_id}",
        )
    )
    parameters.append(
        _estimate_or_assume(
            series="variable_unit_cost_cents",
            entity_id=product_id,
            assumed=assumed_cost,
            dataset=dataset,
            window=window,
            name=f"variable_cost:{product_id}",
        )
    )
    demand = _windowed_observations(dataset, "demand_units", product_id, window)
    estimate = _estimate_seasonality("demand_units", product_id, demand)
    if estimate is not None:
        seasonality.append(estimate)
        parameters.append(
            EstimatedParameter(
                name=f"seasonality_amplitude:{product_id}",
                series="demand_units",
                entity_id=product_id,
                provenance="observed"
                if estimate.sample_size >= OBSERVED_SAMPLE_THRESHOLD
                else "estimated",
                point_estimate=estimate.amplitude,
                dispersion=0.0,
                unit="ratio",
                sample_size=estimate.sample_size,
                confidence_interval=None,
                method=f"bucketed_index:{estimate.period}",
            )
        )
    else:
        parameters.append(
            _assumed_parameter(
                series="demand_units",
                entity_id=product_id,
                assumed=assumed_seasonality,
                unit="ratio",
                name=f"seasonality_amplitude:{product_id}",
            )
        )


def _calibrate_scalar(
    *,
    series: SeriesName,
    entity_id: str | None,
    assumed: float,
    dataset: HistoricalDataset,
    window: tuple[date, date],
    parameters: list[EstimatedParameter],
    name: str,
) -> None:
    parameters.append(
        _estimate_or_assume(
            series=series,
            entity_id=entity_id,
            assumed=assumed,
            dataset=dataset,
            window=window,
            name=name,
        )
    )


def _calibrate_optional(
    *,
    series: SeriesName,
    entity_id: str | None,
    dataset: HistoricalDataset,
    window: tuple[date, date],
    parameters: list[EstimatedParameter],
    name: str,
) -> None:
    values = _windowed(dataset, series, entity_id, window)
    if not values:
        return
    parameters.append(
        _estimate_parameter(series, entity_id, values, name)
    )


def _estimate_or_assume(
    *,
    series: SeriesName,
    entity_id: str | None,
    assumed: float,
    dataset: HistoricalDataset,
    window: tuple[date, date],
    name: str,
) -> EstimatedParameter:
    values = _windowed(dataset, series, entity_id, window)
    if not values:
        return _assumed_parameter(
            series=series,
            entity_id=entity_id,
            assumed=assumed,
            unit=SERIES_REGISTRY[series].unit,
            name=name,
        )
    return _estimate_parameter(series, entity_id, values, name)


def _estimate_parameter(
    series: SeriesName,
    entity_id: str | None,
    values: list[float],
    name: str,
) -> EstimatedParameter:
    spec = SERIES_REGISTRY[series]
    point = mean(values)
    sample_size = len(values)
    dispersion = stdev(values) if sample_size >= 2 else 0.0
    interval: ConfidenceInterval | None = None
    warnings: list[str] = []
    if sample_size >= 2:
        standard_error = dispersion / sqrt(sample_size)
        half = Z_95 * standard_error
        interval = ConfidenceInterval(lower=point - half, upper=point + half)
    provenance: Provenance = (
        "observed" if sample_size >= OBSERVED_SAMPLE_THRESHOLD else "estimated"
    )
    if provenance == "estimated":
        warnings.append(
            f"only {sample_size} observation(s); estimate is provisional"
        )
    return EstimatedParameter(
        name=name,
        series=series,
        entity_id=entity_id,
        provenance=provenance,
        point_estimate=round(point, 6),
        dispersion=round(dispersion, 6),
        unit=spec.unit,
        sample_size=sample_size,
        confidence_interval=interval,
        method="sample_mean_normal_ci",
        warnings=tuple(warnings),
    )


def _assumed_parameter(
    *,
    series: SeriesName,
    entity_id: str | None,
    assumed: float,
    unit: str,
    name: str,
) -> EstimatedParameter:
    return EstimatedParameter(
        name=name,
        series=series,
        entity_id=entity_id,
        provenance="assumed",
        point_estimate=round(assumed, 6),
        dispersion=0.0,
        unit=unit,
        sample_size=0,
        confidence_interval=None,
        method="authored_model_default",
        warnings=("no history available; carried over from the company model",),
    )


def _estimate_seasonality(
    series: SeriesName,
    entity_id: str | None,
    observations: list[HistoricalObservation],
) -> SeasonalityEstimate | None:
    """Detect the dominant multiplicative seasonality (weekly vs yearly).

    Both a weekly (weekday) and, when enough history exists, a yearly (monthly)
    index are computed; the profile with the larger amplitude is kept as the
    single dominant pattern used for forecasting and backtesting.
    """

    if len(observations) < 2 * 7:
        return None
    span = (
        max(o.period_date for o in observations)
        - min(o.period_date for o in observations)
    ).days
    candidates = [_seasonality_for(series, entity_id, observations, "weekly", 3)]
    if span >= YEARLY_SEASONALITY_MIN_SPAN:
        candidates.append(
            _seasonality_for(series, entity_id, observations, "yearly", 6)
        )
    viable = [candidate for candidate in candidates if candidate is not None]
    if not viable:
        return None
    return max(viable, key=lambda estimate: estimate.amplitude)


def _seasonality_for(
    series: SeriesName,
    entity_id: str | None,
    observations: list[HistoricalObservation],
    period: Literal["weekly", "yearly"],
    min_buckets: int,
) -> SeasonalityEstimate | None:
    grouped: dict[int, list[float]] = {}
    for observation in observations:
        bucket = (
            observation.period_date.month
            if period == "yearly"
            else observation.period_date.weekday()
        )
        grouped.setdefault(bucket, []).append(observation.value)
    overall = mean(o.value for o in observations)
    if overall <= 0 or len(grouped) < min_buckets:
        return None
    factors = tuple(
        SeasonalFactor(bucket=bucket, factor=max(1e-6, mean(vals) / overall))
        for bucket, vals in sorted(grouped.items())
    )
    factor_values = [factor.factor for factor in factors]
    amplitude = min(1.0, (max(factor_values) - min(factor_values)) / 2.0)
    return SeasonalityEstimate(
        series=series,
        entity_id=entity_id,
        period=period,
        amplitude=round(amplitude, 6),
        factors=factors,
        sample_size=len(observations),
    )


def _windowed(
    dataset: HistoricalDataset,
    series: SeriesName,
    entity_id: str | None,
    window: tuple[date, date],
) -> list[float]:
    start, end = window
    return [
        observation.value
        for observation in dataset.observations_for(series, entity_id)
        if start <= observation.period_date <= end
    ]


def _windowed_observations(
    dataset: HistoricalDataset,
    series: SeriesName,
    entity_id: str | None,
    window: tuple[date, date],
) -> list[HistoricalObservation]:
    start, end = window
    return [
        observation
        for observation in dataset.observations_for(series, entity_id)
        if start <= observation.period_date <= end
    ]


def _calibration_digest(
    *,
    calibration_id: str,
    data_digest: str,
    window: tuple[date, date],
    parameters: tuple[EstimatedParameter, ...],
    seasonality: tuple[SeasonalityEstimate, ...],
) -> str:
    body = {
        "calibration_id": calibration_id,
        "data_digest": data_digest,
        "window": [window[0].isoformat(), window[1].isoformat()],
        "parameters": [p.model_dump(mode="json") for p in parameters],
        "seasonality": [s.model_dump(mode="json") for s in seasonality],
    }
    return canonical_digest(body)
