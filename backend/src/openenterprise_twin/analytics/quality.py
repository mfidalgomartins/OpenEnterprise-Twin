"""Explicit, decomposable data quality assessment for historical datasets.

Every issue is *reported*, never silently repaired. The overall quality score is
a documented weighted mean of four normalised components so downstream
credibility scoring can trace exactly why a dataset is trusted or distrusted.
"""

from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from itertools import pairwise
from statistics import median
from typing import Annotated, Literal

from pydantic import Field

from openenterprise_twin.analytics.history import (
    SERIES_REGISTRY,
    HistoricalDataset,
    HistoricalObservation,
    SeriesName,
    SeriesWindow,
)
from openenterprise_twin.domain.company import DomainModel, Identifier

IssueSeverity = Literal["error", "warning", "info"]
IssueCode = Literal[
    "unit_incompatible",
    "duplicate_observation",
    "missing_period",
    "value_out_of_range",
    "negative_not_allowed",
    "short_history",
]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]

#: Minimum recommended distinct sampling points before a series is calibratable.
MIN_SERIES_POINTS = 30

#: Documented weights of the quality-score components (sum to 1.0).
QUALITY_COMPONENT_WEIGHTS: dict[str, float] = {
    "completeness": 0.35,
    "validity": 0.30,
    "uniqueness": 0.20,
    "consistency": 0.15,
}


class DataQualityIssue(DomainModel):
    """A single, aggregated data quality finding for one series or entity."""

    code: IssueCode
    severity: IssueSeverity
    series: SeriesName | None
    entity_id: Identifier | None
    count: Annotated[int, Field(gt=0)]
    message: Annotated[str, Field(min_length=1, max_length=280)]


class QualityComponent(DomainModel):
    """One transparent, weighted contributor to the overall quality score."""

    name: Literal["completeness", "validity", "uniqueness", "consistency"]
    value: UnitInterval
    weight: UnitInterval
    detail: Annotated[str, Field(min_length=1, max_length=200)]

    @property
    def contribution(self) -> float:
        return self.value * self.weight


class DataQualityReport(DomainModel):
    """A reproducible, decomposable quality assessment of one dataset."""

    dataset_id: Identifier
    data_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    total_observations: Annotated[int, Field(gt=0)]
    distinct_series: Annotated[int, Field(gt=0)]
    series_windows: tuple[SeriesWindow, ...]
    issues: tuple[DataQualityIssue, ...]
    components: tuple[QualityComponent, ...]
    quality_score: UnitInterval
    report_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


def assess_data_quality(dataset: HistoricalDataset) -> DataQualityReport:
    """Profile a dataset and return an auditable quality report."""

    issues: list[DataQualityIssue] = []
    windows: list[SeriesWindow] = []

    total = len(dataset.observations)
    duplicate_count = 0
    missing_count = 0
    invalid_count = 0
    unit_incompatible_series = 0

    for series, entity_id in dataset.series_keys():
        group = dataset.observations_for(series, entity_id)
        spec = SERIES_REGISTRY[series]

        unit_issue = _check_units(series, entity_id, group)
        if unit_issue is not None:
            issues.append(unit_issue)
            unit_incompatible_series += 1

        dup = _count_duplicates(group)
        if dup:
            duplicate_count += dup
            issues.append(
                DataQualityIssue(
                    code="duplicate_observation",
                    severity="error",
                    series=series,
                    entity_id=entity_id,
                    count=dup,
                    message=(
                        f"{dup} duplicate observation(s) share a date for "
                        f"series '{series}'"
                    ),
                )
            )

        missing = _count_missing_periods(group)
        if missing:
            missing_count += missing
            issues.append(
                DataQualityIssue(
                    code="missing_period",
                    severity="warning",
                    series=series,
                    entity_id=entity_id,
                    count=missing,
                    message=(
                        f"{missing} sampling gap(s) detected in series '{series}'"
                    ),
                )
            )

        range_issues, invalid = _check_values(series, entity_id, group)
        issues.extend(range_issues)
        invalid_count += invalid

        distinct_dates = {item.period_date for item in group}
        if len(distinct_dates) < MIN_SERIES_POINTS:
            issues.append(
                DataQualityIssue(
                    code="short_history",
                    severity="warning",
                    series=series,
                    entity_id=entity_id,
                    count=len(distinct_dates),
                    message=(
                        f"series '{series}' has only {len(distinct_dates)} distinct "
                        f"periods (recommended >= {MIN_SERIES_POINTS})"
                    ),
                )
            )
        del spec
        windows.append(_series_window(series, entity_id, group))

    components = _quality_components(
        total=total,
        duplicate_count=duplicate_count,
        missing_count=missing_count,
        invalid_count=invalid_count,
        unit_incompatible_series=unit_incompatible_series,
        distinct_series=len(dataset.series_keys()),
    )
    quality_score = round(
        sum(component.contribution for component in components), 6
    )
    ordered_issues = tuple(
        sorted(
            issues,
            key=lambda issue: (
                {"error": 0, "warning": 1, "info": 2}[issue.severity],
                issue.series or "",
                issue.entity_id or "",
                issue.code,
            ),
        )
    )
    body = {
        "dataset_id": dataset.dataset_id,
        "data_digest": dataset.data_digest,
        "quality_score": quality_score,
        "components": [component.model_dump(mode="json") for component in components],
        "issues": [issue.model_dump(mode="json") for issue in ordered_issues],
    }
    report_digest = sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return DataQualityReport(
        dataset_id=dataset.dataset_id,
        data_digest=dataset.data_digest,
        total_observations=total,
        distinct_series=len(dataset.series_keys()),
        series_windows=tuple(
            sorted(windows, key=lambda w: (w.series, w.entity_id or ""))
        ),
        issues=ordered_issues,
        components=components,
        quality_score=quality_score,
        report_digest=report_digest,
    )


def _check_units(
    series: SeriesName,
    entity_id: str | None,
    group: tuple[HistoricalObservation, ...],
) -> DataQualityIssue | None:
    expected = SERIES_REGISTRY[series].unit
    mismatched = sum(1 for item in group if item.unit != expected)
    if not mismatched:
        return None
    return DataQualityIssue(
        code="unit_incompatible",
        severity="error",
        series=series,
        entity_id=entity_id,
        count=mismatched,
        message=(
            f"{mismatched} observation(s) in series '{series}' do not use the "
            f"canonical unit '{expected}'"
        ),
    )


def _count_duplicates(group: tuple[HistoricalObservation, ...]) -> int:
    counts = Counter(item.period_date for item in group)
    return sum(count - 1 for count in counts.values() if count > 1)


def _count_missing_periods(group: tuple[HistoricalObservation, ...]) -> int:
    distinct = sorted({item.period_date for item in group})
    if len(distinct) < 3:
        return 0
    gaps = [(later - earlier).days for earlier, later in pairwise(distinct)]
    cadence = max(1, round(median(gaps)))
    missing = 0
    for gap in gaps:
        steps = round(gap / cadence)
        if steps > 1:
            missing += steps - 1
    return missing


def _check_values(
    series: SeriesName,
    entity_id: str | None,
    group: tuple[HistoricalObservation, ...],
) -> tuple[list[DataQualityIssue], int]:
    spec = SERIES_REGISTRY[series]
    issues: list[DataQualityIssue] = []
    negative = 0
    out_of_range = 0
    for item in group:
        if not spec.allow_negative and item.value < 0:
            negative += 1
            continue
        below = spec.plausible_min is not None and item.value < spec.plausible_min
        above = spec.plausible_max is not None and item.value > spec.plausible_max
        if below or above:
            out_of_range += 1
    if negative:
        issues.append(
            DataQualityIssue(
                code="negative_not_allowed",
                severity="error",
                series=series,
                entity_id=entity_id,
                count=negative,
                message=(
                    f"{negative} negative value(s) in non-negative series '{series}'"
                ),
            )
        )
    if out_of_range:
        issues.append(
            DataQualityIssue(
                code="value_out_of_range",
                severity="warning",
                series=series,
                entity_id=entity_id,
                count=out_of_range,
                message=(
                    f"{out_of_range} value(s) in series '{series}' fall outside the "
                    "plausible range"
                ),
            )
        )
    return issues, negative + out_of_range


def _series_window(
    series: SeriesName,
    entity_id: str | None,
    group: tuple[HistoricalObservation, ...],
) -> SeriesWindow:
    dates = [item.period_date for item in group]
    return SeriesWindow(
        series=series,
        entity_id=entity_id,
        start_date=min(dates),
        end_date=max(dates),
        observation_count=len(group),
    )


def _quality_components(
    *,
    total: int,
    duplicate_count: int,
    missing_count: int,
    invalid_count: int,
    unit_incompatible_series: int,
    distinct_series: int,
) -> tuple[QualityComponent, ...]:
    completeness = _ratio_complement(missing_count, total + missing_count)
    validity = _ratio_complement(invalid_count, total)
    uniqueness = _ratio_complement(duplicate_count, total)
    consistency = _ratio_complement(unit_incompatible_series, distinct_series)
    weights = QUALITY_COMPONENT_WEIGHTS
    return (
        QualityComponent(
            name="completeness",
            value=completeness,
            weight=weights["completeness"],
            detail=f"{missing_count} sampling gap(s) across {distinct_series} series",
        ),
        QualityComponent(
            name="validity",
            value=validity,
            weight=weights["validity"],
            detail=f"{invalid_count} implausible value(s) of {total} observations",
        ),
        QualityComponent(
            name="uniqueness",
            value=uniqueness,
            weight=weights["uniqueness"],
            detail=f"{duplicate_count} duplicate observation(s)",
        ),
        QualityComponent(
            name="consistency",
            value=consistency,
            weight=weights["consistency"],
            detail=(
                f"{unit_incompatible_series} series with incompatible units"
            ),
        ),
    )


def _ratio_complement(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(max(0.0, 1.0 - numerator / denominator), 6)
