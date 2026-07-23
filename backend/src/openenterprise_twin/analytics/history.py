"""Canonical, provenance-tracked historical observations for twin calibration.

The calibration studio ingests real operating history from heterogeneous
sources (CSV, spreadsheets, PostgreSQL, external APIs) and normalises it into a
single immutable, content-addressed dataset. The canonical model deliberately
keeps *raw* observations (including gaps and duplicates) so that the data
quality report can detect issues explicitly -- nothing is imputed silently.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openenterprise_twin.domain.company import DomainModel, Identifier
from openenterprise_twin.domain.errors import DomainValidationError

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

#: A canonical operating series. Each family maps to one measurement semantics.
SeriesName = Literal[
    "demand_units",
    "sales_units",
    "unit_price_cents",
    "variable_unit_cost_cents",
    "finished_goods_units",
    "production_units",
    "capacity_utilization",
    "supplier_lead_time_days",
    "otif",
    "backlog_units",
    "receivables_cents",
    "payment_terms_days",
    "cash_flow_cents",
]

#: Measurement kind drives validation, unit checking and calibration behaviour.
SeriesKind = Literal["flow", "level", "ratio", "money", "days"]

SourceKind = Literal["csv", "excel", "postgresql", "api", "inline"]


class SeriesSpec(DomainModel):
    """Canonical measurement semantics for one operating series."""

    kind: SeriesKind
    unit: str
    #: Dimension the series is scoped to, or ``None`` for a company-level series.
    entity_dimension: Literal["product", "segment", "resource", "material"] | None
    allow_negative: bool = False
    #: Inclusive expected range used only for range plausibility warnings.
    plausible_min: float | None = None
    plausible_max: float | None = None


#: The registry pins the semantics of every canonical series. Extending the
#: historical model is a deliberate, reviewable change here -- not an implicit
#: side effect of ingesting an unknown column.
SERIES_REGISTRY: Mapping[SeriesName, SeriesSpec] = {
    "demand_units": SeriesSpec(
        kind="flow", unit="units/day", entity_dimension="product", plausible_min=0.0
    ),
    "sales_units": SeriesSpec(
        kind="flow", unit="units/day", entity_dimension="product", plausible_min=0.0
    ),
    "unit_price_cents": SeriesSpec(
        kind="money", unit="cents", entity_dimension="product", plausible_min=0.0
    ),
    "variable_unit_cost_cents": SeriesSpec(
        kind="money", unit="cents", entity_dimension="product", plausible_min=0.0
    ),
    "finished_goods_units": SeriesSpec(
        kind="level", unit="units", entity_dimension="product", plausible_min=0.0
    ),
    "production_units": SeriesSpec(
        kind="flow", unit="units/day", entity_dimension="product", plausible_min=0.0
    ),
    "capacity_utilization": SeriesSpec(
        kind="ratio",
        unit="ratio",
        entity_dimension="resource",
        plausible_min=0.0,
        plausible_max=2.0,
    ),
    "supplier_lead_time_days": SeriesSpec(
        kind="days", unit="days", entity_dimension="material", plausible_min=0.0
    ),
    "otif": SeriesSpec(
        kind="ratio",
        unit="ratio",
        entity_dimension=None,
        plausible_min=0.0,
        plausible_max=1.0,
    ),
    "backlog_units": SeriesSpec(
        kind="level", unit="units", entity_dimension=None, plausible_min=0.0
    ),
    "receivables_cents": SeriesSpec(
        kind="level", unit="cents", entity_dimension="segment", plausible_min=0.0
    ),
    "payment_terms_days": SeriesSpec(
        kind="days", unit="days", entity_dimension="segment", plausible_min=0.0
    ),
    "cash_flow_cents": SeriesSpec(
        kind="money", unit="cents", entity_dimension=None, allow_negative=True
    ),
}


class HistoricalObservation(DomainModel):
    """One dated measurement of a canonical series for an optional entity."""

    period_date: date
    series: SeriesName
    entity_id: Identifier | None
    value: FiniteFloat
    unit: str = Field(min_length=1, max_length=32)


class DatasetProvenance(DomainModel):
    """Traceable origin of an ingested dataset (redaction-safe reference only)."""

    source_kind: SourceKind
    source_reference: Annotated[str, Field(min_length=1, max_length=256)]
    ingested_at: datetime
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "UTC"

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        if self.ingested_at.tzinfo is None:
            raise DomainValidationError("ingested_at must be timezone-aware")
        return self


class SeriesWindow(DomainModel):
    """Observed time window for one series, used for coverage reporting."""

    series: SeriesName
    entity_id: Identifier | None
    start_date: date
    end_date: date
    observation_count: Annotated[int, Field(gt=0)]

    @property
    def span_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class HistoricalDataset(DomainModel):
    """An immutable, content-addressed bundle of raw historical observations."""

    dataset_id: Identifier
    company_id: Identifier
    observations: Annotated[tuple[HistoricalObservation, ...], Field(min_length=1)]
    provenance: DatasetProvenance
    data_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        expected = compute_data_digest(self.observations)
        if self.data_digest != expected:
            raise DomainValidationError(
                "dataset data_digest does not match its observations"
            )
        return self

    @property
    def window(self) -> tuple[date, date]:
        dates = [item.period_date for item in self.observations]
        return (min(dates), max(dates))

    def series_keys(self) -> tuple[tuple[SeriesName, str | None], ...]:
        seen: dict[tuple[SeriesName, str | None], None] = {}
        for item in self.observations:
            seen.setdefault((item.series, item.entity_id), None)
        return tuple(sorted(seen, key=lambda key: (key[0], key[1] or "")))

    def observations_for(
        self, series: SeriesName, entity_id: str | None
    ) -> tuple[HistoricalObservation, ...]:
        matched = tuple(
            item
            for item in self.observations
            if item.series == series and item.entity_id == entity_id
        )
        return tuple(sorted(matched, key=lambda item: item.period_date))


def compute_data_digest(
    observations: tuple[HistoricalObservation, ...],
) -> str:
    """Return a reproducible digest of observation content (order-independent)."""

    rows = sorted(
        (
            item.period_date.isoformat(),
            item.series,
            item.entity_id or "",
            repr(item.value),
            item.unit,
        )
        for item in observations
    )
    canonical = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    return sha256(canonical).hexdigest()


def build_dataset(
    *,
    dataset_id: str,
    company_id: str,
    observations: tuple[HistoricalObservation, ...],
    source_kind: SourceKind,
    source_reference: str,
    timezone: str = "UTC",
    ingested_at: datetime | None = None,
) -> HistoricalDataset:
    """Assemble a content-addressed dataset from validated raw observations."""

    if not observations:
        raise DomainValidationError("a dataset requires at least one observation")
    provenance = DatasetProvenance(
        source_kind=source_kind,
        source_reference=source_reference,
        ingested_at=ingested_at or datetime.now(UTC),
        timezone=timezone,
    )
    return HistoricalDataset(
        dataset_id=dataset_id,
        company_id=company_id,
        observations=observations,
        provenance=provenance,
        data_digest=compute_data_digest(observations),
    )
