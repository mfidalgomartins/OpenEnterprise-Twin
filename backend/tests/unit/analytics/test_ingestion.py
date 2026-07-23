import pytest

from openenterprise_twin.analytics.history import HistoricalObservation, build_dataset
from openenterprise_twin.analytics.ingestion import (
    CSV_COLUMNS,
    dataset_to_csv,
    observations_from_csv,
)
from openenterprise_twin.domain.errors import DomainValidationError

_HEADER = ",".join(CSV_COLUMNS)


def test_parses_valid_long_format_csv() -> None:
    content = (
        f"{_HEADER}\n"
        "2025-01-01,demand_units,standard-valve,55,units/day\n"
        "2025-01-02,otif,,0.96,ratio\n"
    )
    observations = observations_from_csv(content)
    assert len(observations) == 2
    assert observations[0].series == "demand_units"
    assert observations[0].value == 55.0
    assert observations[1].entity_id is None


def test_blank_lines_are_skipped() -> None:
    content = (
        f"{_HEADER}\n"
        "2025-01-01,otif,,0.96,ratio\n"
        "\n"
        "2025-01-02,otif,,0.95,ratio\n"
    )
    assert len(observations_from_csv(content)) == 2


def test_rejects_wrong_header() -> None:
    with pytest.raises(DomainValidationError):
        observations_from_csv("date,series,value\n2025-01-01,otif,0.9\n")


def test_rejects_unknown_series_with_line_number() -> None:
    content = f"{_HEADER}\n2025-01-01,not_a_series,,1,unit\n"
    with pytest.raises(DomainValidationError, match="line 2: unknown series"):
        observations_from_csv(content)


def test_rejects_non_iso_date() -> None:
    content = f"{_HEADER}\n01/01/2025,otif,,0.9,ratio\n"
    with pytest.raises(DomainValidationError, match="not an ISO date"):
        observations_from_csv(content)


def test_rejects_non_numeric_value() -> None:
    content = f"{_HEADER}\n2025-01-01,otif,,high,ratio\n"
    with pytest.raises(DomainValidationError, match="not a number"):
        observations_from_csv(content)


def test_rejects_non_finite_value() -> None:
    content = f"{_HEADER}\n2025-01-01,otif,,inf,ratio\n"
    with pytest.raises(DomainValidationError, match="finite"):
        observations_from_csv(content)


def test_empty_csv_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        observations_from_csv("")
    with pytest.raises(DomainValidationError):
        observations_from_csv(f"{_HEADER}\n")


def test_export_round_trips() -> None:
    content = (
        f"{_HEADER}\n"
        "2025-01-01,demand_units,standard-valve,55.0,units/day\n"
        "2025-01-02,otif,,0.96,ratio\n"
    )
    dataset = build_dataset(
        dataset_id="d",
        company_id="northstar-components",
        observations=observations_from_csv(content),
        source_kind="csv",
        source_reference="unit-test",
    )
    exported = dataset_to_csv(dataset)
    assert exported.splitlines()[0] == _HEADER
    assert len(observations_from_csv(exported)) == 2


def test_export_neutralizes_formula_injection() -> None:
    malicious = HistoricalObservation(
        period_date="2025-01-01",  # type: ignore[arg-type]
        series="otif",
        entity_id=None,
        value=0.9,
        unit="=SUM(A1:A9)",
    )
    dataset = build_dataset(
        dataset_id="m",
        company_id="c",
        observations=(malicious,),
        source_kind="csv",
        source_reference="unit-test",
    )
    exported = dataset_to_csv(dataset)
    # The dangerous cell is prefixed with a single quote so a spreadsheet keeps
    # it as text rather than evaluating it.
    assert "'=SUM(A1:A9)" in exported
    assert ",=SUM" not in exported
