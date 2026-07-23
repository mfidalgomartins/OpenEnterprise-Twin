from datetime import UTC, date, datetime

from openenterprise_twin.analytics.history import (
    HistoricalObservation,
    build_dataset,
)
from openenterprise_twin.analytics.quality import (
    QUALITY_COMPONENT_WEIGHTS,
    assess_data_quality,
)


def _clean_observations(count: int = 60) -> tuple[HistoricalObservation, ...]:
    start = date(2025, 1, 1)
    return tuple(
        HistoricalObservation(
            period_date=date.fromordinal(start.toordinal() + offset),
            series="demand_units",
            entity_id="standard-valve",
            value=50.0 + (offset % 5),
            unit="units/day",
        )
        for offset in range(count)
    )


def _dataset(observations: tuple[HistoricalObservation, ...]):
    return build_dataset(
        dataset_id="d1",
        company_id="northstar-components",
        observations=observations,
        source_kind="inline",
        source_reference="unit-test",
        ingested_at=datetime(2025, 7, 1, tzinfo=UTC),
    )


def test_quality_component_weights_sum_to_one() -> None:
    assert abs(sum(QUALITY_COMPONENT_WEIGHTS.values()) - 1.0) < 1e-9


def test_clean_dataset_has_no_errors_and_high_score() -> None:
    report = assess_data_quality(_dataset(_clean_observations()))
    assert not report.has_errors
    assert report.quality_score == 1.0
    assert report.total_observations == 60


def test_duplicate_observation_is_flagged_and_lowers_uniqueness() -> None:
    observations = _clean_observations()
    duplicate = observations[0]
    report = assess_data_quality(_dataset((*observations, duplicate)))
    codes = {issue.code for issue in report.issues}
    assert "duplicate_observation" in codes
    assert report.has_errors
    uniqueness = next(c for c in report.components if c.name == "uniqueness")
    assert uniqueness.value < 1.0


def test_incompatible_unit_is_flagged() -> None:
    observations = list(_clean_observations())
    observations[0] = observations[0].model_copy(update={"unit": "units/week"})
    report = assess_data_quality(_dataset(tuple(observations)))
    codes = {issue.code for issue in report.issues}
    assert "unit_incompatible" in codes
    assert report.has_errors


def test_negative_value_in_non_negative_series_is_error() -> None:
    observations = list(_clean_observations())
    observations[0] = observations[0].model_copy(update={"value": -3.0})
    report = assess_data_quality(_dataset(tuple(observations)))
    codes = {issue.code for issue in report.issues}
    assert "negative_not_allowed" in codes


def test_missing_periods_are_detected() -> None:
    start = date(2025, 1, 1)
    days = [0, 1, 2, 3, 10, 11, 12, 13, 14, 15]  # a gap between day 3 and day 10
    observations = tuple(
        HistoricalObservation(
            period_date=date.fromordinal(start.toordinal() + offset),
            series="demand_units",
            entity_id="standard-valve",
            value=50.0,
            unit="units/day",
        )
        for offset in days
    )
    report = assess_data_quality(_dataset(observations))
    codes = {issue.code for issue in report.issues}
    assert "missing_period" in codes


def test_report_is_reproducible() -> None:
    dataset = _dataset(_clean_observations())
    assert assess_data_quality(dataset).report_digest == (
        assess_data_quality(dataset).report_digest
    )
