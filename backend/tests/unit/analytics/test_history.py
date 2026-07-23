from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from openenterprise_twin.analytics.history import (
    DatasetProvenance,
    HistoricalDataset,
    HistoricalObservation,
    build_dataset,
    compute_data_digest,
)
from openenterprise_twin.domain.errors import DomainValidationError


def _obs(day: int, value: float, series: str = "demand_units") -> HistoricalObservation:
    return HistoricalObservation(
        period_date=date(2025, 1, day),
        series=series,  # type: ignore[arg-type]
        entity_id="standard-valve",
        value=value,
        unit="units/day",
    )


def test_data_digest_is_order_independent() -> None:
    forward = (_obs(1, 10.0), _obs(2, 12.0), _obs(3, 11.0))
    reversed_order = tuple(reversed(forward))
    assert compute_data_digest(forward) == compute_data_digest(reversed_order)


def test_data_digest_changes_with_value() -> None:
    baseline = (_obs(1, 10.0), _obs(2, 12.0))
    perturbed = (_obs(1, 10.0), _obs(2, 12.5))
    assert compute_data_digest(baseline) != compute_data_digest(perturbed)


def test_build_dataset_is_content_addressed() -> None:
    observations = (_obs(1, 10.0), _obs(2, 12.0))
    dataset = build_dataset(
        dataset_id="d1",
        company_id="northstar-components",
        observations=observations,
        source_kind="inline",
        source_reference="unit-test",
        ingested_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    assert dataset.data_digest == compute_data_digest(observations)
    assert dataset.window == (date(2025, 1, 1), date(2025, 1, 2))
    assert dataset.series_keys() == (("demand_units", "standard-valve"),)


def test_dataset_rejects_tampered_digest() -> None:
    observations = (_obs(1, 10.0), _obs(2, 12.0))
    provenance = DatasetProvenance(
        source_kind="inline",
        source_reference="unit-test",
        ingested_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        HistoricalDataset(
            dataset_id="d1",
            company_id="northstar-components",
            observations=observations,
            provenance=provenance,
            data_digest="0" * 64,
        )


def test_build_dataset_requires_observations() -> None:
    with pytest.raises(DomainValidationError):
        build_dataset(
            dataset_id="d1",
            company_id="northstar-components",
            observations=(),
            source_kind="inline",
            source_reference="unit-test",
        )


def test_provenance_requires_aware_timestamp() -> None:
    with pytest.raises(ValidationError):
        build_dataset(
            dataset_id="d1",
            company_id="northstar-components",
            observations=(_obs(1, 10.0),),
            source_kind="inline",
            source_reference="unit-test",
            ingested_at=datetime(2025, 7, 1),
        )


def test_observations_for_returns_sorted_slice() -> None:
    observations = (_obs(3, 11.0), _obs(1, 10.0), _obs(2, 12.0))
    dataset = build_dataset(
        dataset_id="d1",
        company_id="northstar-components",
        observations=observations,
        source_kind="inline",
        source_reference="unit-test",
        ingested_at=datetime(2025, 7, 1, tzinfo=UTC),
    )
    slice_ = dataset.observations_for("demand_units", "standard-valve")
    assert [item.period_date.day for item in slice_] == [1, 2, 3]
