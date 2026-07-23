"""A transparent, decomposable credibility score for a calibrated twin.

The score is a documented weighted mean of seven normalised components, each in
``[0, 1]``. The final score is reported on a ``0..100`` scale with an explicit
interpretation band. No component is a black box: every contribution carries its
raw input, its normalisation and its weight, so a reviewer can reconstruct the
number by hand.

Formula
-------
``score = 100 * sum(component.normalized * component.weight)``

Weights (sum to 1.0):
    data_quality        0.20   overall data-quality score
    temporal_coverage   0.15   history span / target window (capped at 1.0)
    backtest_error      0.20   1 - min(1, wMAPE / error_tolerance)
    interval_coverage   0.15   1 - min(1, |coverage - nominal| / nominal)
    parameter_stability 0.10   1 - mean relative CI half-width (capped)
    assumed_ratio       0.10   1 - assumed_parameters / total_parameters
    drift               0.10   1 - recent_drift_severity

Interpretation bands:
    >= 80  decision_grade   |  60-79 supporting  |  40-59 provisional  |
    < 40   insufficient
"""

from __future__ import annotations

import json
from hashlib import sha256
from statistics import mean
from typing import Annotated, Literal

from pydantic import Field

from openenterprise_twin.analytics.backtesting import BacktestResult
from openenterprise_twin.analytics.calibration import CalibrationResult
from openenterprise_twin.analytics.quality import DataQualityReport
from openenterprise_twin.domain.company import DomainModel, Identifier
from openenterprise_twin.domain.errors import DomainValidationError

UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
CredibilityBand = Literal[
    "decision_grade", "supporting", "provisional", "insufficient"
]
ComponentName = Literal[
    "data_quality",
    "temporal_coverage",
    "backtest_error",
    "interval_coverage",
    "parameter_stability",
    "assumed_ratio",
    "drift",
]

#: Documented component weights (sum to 1.0).
CREDIBILITY_WEIGHTS: dict[ComponentName, float] = {
    "data_quality": 0.20,
    "temporal_coverage": 0.15,
    "backtest_error": 0.20,
    "interval_coverage": 0.15,
    "parameter_stability": 0.10,
    "assumed_ratio": 0.10,
    "drift": 0.10,
}

#: wMAPE at or above which the backtest-error component collapses to zero.
DEFAULT_ERROR_TOLERANCE = 0.30
#: Target history length used to normalise temporal coverage.
DEFAULT_TARGET_WINDOW_DAYS = 365


class CredibilityComponent(DomainModel):
    """One transparent contributor to the overall credibility score."""

    name: ComponentName
    raw_value: Annotated[float, Field(allow_inf_nan=False)]
    normalized: UnitInterval
    weight: UnitInterval
    detail: Annotated[str, Field(min_length=1, max_length=200)]

    @property
    def contribution(self) -> float:
        return self.normalized * self.weight


class CredibilityScore(DomainModel):
    """A reproducible, decomposable credibility assessment of a calibration."""

    calibration_id: Identifier
    calibration_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    score: Annotated[float, Field(ge=0.0, le=100.0)]
    band: CredibilityBand
    components: Annotated[tuple[CredibilityComponent, ...], Field(min_length=1)]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    def component(self, name: ComponentName) -> CredibilityComponent | None:
        for component in self.components:
            if component.name == name:
                return component
        return None


def score_credibility(
    *,
    calibration: CalibrationResult,
    quality: DataQualityReport,
    backtests: tuple[BacktestResult, ...],
    target_window_days: int = DEFAULT_TARGET_WINDOW_DAYS,
    error_tolerance: float = DEFAULT_ERROR_TOLERANCE,
    recent_drift_severity: float = 0.0,
) -> CredibilityScore:
    """Combine data quality, coverage, backtest and drift into one score."""

    if quality.data_digest != calibration.data_digest:
        raise DomainValidationError(
            "quality report and calibration describe different datasets"
        )
    if not 0.0 <= recent_drift_severity <= 1.0:
        raise DomainValidationError("recent_drift_severity must be within [0, 1]")
    if error_tolerance <= 0:
        raise DomainValidationError("error_tolerance must be positive")

    components = (
        _data_quality_component(quality),
        _temporal_coverage_component(calibration, target_window_days),
        _backtest_error_component(backtests, error_tolerance),
        _interval_coverage_component(backtests),
        _parameter_stability_component(calibration),
        _assumed_ratio_component(calibration),
        _drift_component(recent_drift_severity),
    )
    score = round(
        100.0 * sum(component.contribution for component in components), 4
    )
    band = _band_for(score)
    digest = _credibility_digest(
        calibration_digest=calibration.digest,
        components=components,
        score=score,
    )
    return CredibilityScore(
        calibration_id=calibration.calibration_id,
        calibration_digest=calibration.digest,
        score=score,
        band=band,
        components=components,
        digest=digest,
    )


def _data_quality_component(quality: DataQualityReport) -> CredibilityComponent:
    return CredibilityComponent(
        name="data_quality",
        raw_value=quality.quality_score,
        normalized=_clamp_unit(quality.quality_score),
        weight=CREDIBILITY_WEIGHTS["data_quality"],
        detail=f"data quality score {quality.quality_score:.3f}",
    )


def _temporal_coverage_component(
    calibration: CalibrationResult, target_window_days: int
) -> CredibilityComponent:
    span = (calibration.window_end - calibration.window_start).days + 1
    coverage = _clamp_unit(span / target_window_days) if target_window_days else 0.0
    return CredibilityComponent(
        name="temporal_coverage",
        raw_value=float(span),
        normalized=coverage,
        weight=CREDIBILITY_WEIGHTS["temporal_coverage"],
        detail=f"{span} day(s) of history vs {target_window_days} target",
    )


def _backtest_error_component(
    backtests: tuple[BacktestResult, ...], error_tolerance: float
) -> CredibilityComponent:
    if not backtests:
        return CredibilityComponent(
            name="backtest_error",
            raw_value=0.0,
            normalized=0.0,
            weight=CREDIBILITY_WEIGHTS["backtest_error"],
            detail="no backtest was performed",
        )
    wmape = mean(result.overall_weighted_mape for result in backtests)
    normalized = _clamp_unit(1.0 - min(1.0, wmape / error_tolerance))
    return CredibilityComponent(
        name="backtest_error",
        raw_value=round(wmape, 6),
        normalized=round(normalized, 6),
        weight=CREDIBILITY_WEIGHTS["backtest_error"],
        detail=f"mean out-of-sample wMAPE {wmape:.3f} vs tol {error_tolerance:.2f}",
    )


def _interval_coverage_component(
    backtests: tuple[BacktestResult, ...],
) -> CredibilityComponent:
    if not backtests:
        return CredibilityComponent(
            name="interval_coverage",
            raw_value=0.0,
            normalized=0.0,
            weight=CREDIBILITY_WEIGHTS["interval_coverage"],
            detail="no backtest was performed",
        )
    coverage = mean(result.overall_interval_coverage for result in backtests)
    nominal = mean(result.nominal_coverage for result in backtests)
    miss = abs(coverage - nominal) / nominal if nominal else 1.0
    normalized = _clamp_unit(1.0 - min(1.0, miss))
    return CredibilityComponent(
        name="interval_coverage",
        raw_value=round(coverage, 6),
        normalized=round(normalized, 6),
        weight=CREDIBILITY_WEIGHTS["interval_coverage"],
        detail=f"empirical coverage {coverage:.3f} vs nominal {nominal:.2f}",
    )


def _parameter_stability_component(
    calibration: CalibrationResult,
) -> CredibilityComponent:
    estimated = [
        parameter
        for parameter in calibration.parameters
        if parameter.provenance != "assumed"
        and parameter.confidence_interval is not None
    ]
    if not estimated:
        return CredibilityComponent(
            name="parameter_stability",
            raw_value=0.0,
            normalized=0.0,
            weight=CREDIBILITY_WEIGHTS["parameter_stability"],
            detail="no parameters were estimated from data",
        )
    mean_uncertainty = mean(
        min(1.0, parameter.relative_uncertainty) for parameter in estimated
    )
    normalized = _clamp_unit(1.0 - mean_uncertainty)
    return CredibilityComponent(
        name="parameter_stability",
        raw_value=round(mean_uncertainty, 6),
        normalized=round(normalized, 6),
        weight=CREDIBILITY_WEIGHTS["parameter_stability"],
        detail=f"mean relative CI half-width {mean_uncertainty:.3f}",
    )


def _assumed_ratio_component(
    calibration: CalibrationResult,
) -> CredibilityComponent:
    mix = calibration.provenance_mix
    total = sum(mix.values())
    assumed_ratio = mix["assumed"] / total if total else 1.0
    normalized = _clamp_unit(1.0 - assumed_ratio)
    return CredibilityComponent(
        name="assumed_ratio",
        raw_value=round(assumed_ratio, 6),
        normalized=round(normalized, 6),
        weight=CREDIBILITY_WEIGHTS["assumed_ratio"],
        detail=f"{mix['assumed']} assumed of {total} parameters",
    )


def _drift_component(recent_drift_severity: float) -> CredibilityComponent:
    normalized = _clamp_unit(1.0 - recent_drift_severity)
    return CredibilityComponent(
        name="drift",
        raw_value=round(recent_drift_severity, 6),
        normalized=round(normalized, 6),
        weight=CREDIBILITY_WEIGHTS["drift"],
        detail=f"recent drift severity {recent_drift_severity:.3f}",
    )


def _band_for(score: float) -> CredibilityBand:
    if score >= 80.0:
        return "decision_grade"
    if score >= 60.0:
        return "supporting"
    if score >= 40.0:
        return "provisional"
    return "insufficient"


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _credibility_digest(
    *,
    calibration_digest: str,
    components: tuple[CredibilityComponent, ...],
    score: float,
) -> str:
    body = {
        "calibration_digest": calibration_digest,
        "score": score,
        "components": [component.model_dump(mode="json") for component in components],
    }
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
