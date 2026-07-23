"""Safe CSV ingestion and export for canonical historical observations.

Ingestion accepts a long-format CSV (one observation per row) and validates
every field strictly against the canonical model -- unknown series, malformed
dates or non-numeric values are rejected with a precise, line-numbered error.
Export neutralises spreadsheet formulas (CSV injection) so a downloaded dataset
is safe to open in Excel, Numbers or Google Sheets.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from openenterprise_twin.analytics.history import (
    SERIES_REGISTRY,
    HistoricalDataset,
    HistoricalObservation,
    SeriesName,
)
from openenterprise_twin.domain.errors import DomainValidationError

#: The exact, ordered columns of the long-format observation CSV.
CSV_COLUMNS: tuple[str, ...] = ("period_date", "series", "entity_id", "value", "unit")

#: Leading characters a spreadsheet may evaluate as a formula (OWASP guidance).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_VALID_SERIES = frozenset(SERIES_REGISTRY)


def observations_from_csv(content: str) -> tuple[HistoricalObservation, ...]:
    """Parse a long-format CSV into validated canonical observations.

    The CSV must have a header row of exactly ``period_date, series, entity_id,
    value, unit``. ``entity_id`` may be blank for company-level series. Rows are
    returned in file order; duplicates and gaps are preserved for the data
    quality report to surface rather than being silently repaired.
    """

    reader = csv.reader(io.StringIO(content))
    try:
        header = next(reader)
    except StopIteration as error:
        raise DomainValidationError("CSV is empty") from error
    normalised = tuple(column.strip() for column in header)
    if normalised != CSV_COLUMNS:
        raise DomainValidationError(
            f"CSV header must be {', '.join(CSV_COLUMNS)}"
        )

    observations: list[HistoricalObservation] = []
    for line_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        observations.append(_row_to_observation(row, line_number))
    if not observations:
        raise DomainValidationError("CSV contains no observation rows")
    return tuple(observations)


def dataset_to_csv(dataset: HistoricalDataset) -> str:
    """Serialise a dataset to formula-neutralised long-format CSV."""

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for observation in sorted(
        dataset.observations,
        key=lambda item: (
            item.period_date,
            item.series,
            item.entity_id or "",
        ),
    ):
        writer.writerow(
            (
                observation.period_date.isoformat(),
                _neutralize(observation.series),
                _neutralize(observation.entity_id or ""),
                repr(observation.value),
                _neutralize(observation.unit),
            )
        )
    return buffer.getvalue()


def _row_to_observation(row: list[str], line_number: int) -> HistoricalObservation:
    if len(row) != len(CSV_COLUMNS):
        raise DomainValidationError(
            f"line {line_number}: expected {len(CSV_COLUMNS)} columns, "
            f"got {len(row)}"
        )
    raw_date, raw_series, raw_entity, raw_value, raw_unit = (
        cell.strip() for cell in row
    )
    series = _parse_series(raw_series, line_number)
    return HistoricalObservation(
        period_date=_parse_date(raw_date, line_number),
        series=series,
        entity_id=raw_entity or None,
        value=_parse_value(raw_value, line_number),
        unit=raw_unit,
    )


def _parse_date(value: str, line_number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise DomainValidationError(
            f"line {line_number}: '{value}' is not an ISO date"
        ) from error


def _parse_series(value: str, line_number: int) -> SeriesName:
    if value not in _VALID_SERIES:
        raise DomainValidationError(
            f"line {line_number}: unknown series '{value}'"
        )
    return value


def _parse_value(value: str, line_number: int) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise DomainValidationError(
            f"line {line_number}: '{value}' is not a number"
        ) from error
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        raise DomainValidationError(
            f"line {line_number}: value must be finite"
        )
    return parsed


def _neutralize(cell: str) -> str:
    """Prefix a leading formula character so spreadsheets treat it as text."""

    if cell.startswith(_FORMULA_PREFIXES):
        return "'" + cell
    return cell
