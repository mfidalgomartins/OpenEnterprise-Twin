"""SQLAlchemy records for scenarios and durable experiment lifecycle state."""

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, synonym
from sqlalchemy.types import TypeDecorator

ExperimentStatus = Literal["queued", "running", "completed", "failed"]
JsonObject = dict[str, Any]

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    """Return an aware UTC timestamp for ORM-side defaults and updates."""

    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist timezone-aware values and restore UTC awareness on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base with deterministic lowercase constraint names."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _json_type() -> JSON:
    return JSON().with_variant(JSONB(), "postgresql")


def _identity_type() -> BigInteger:
    return BigInteger().with_variant(Integer(), "sqlite")


class ScenarioRecord(Base):
    """Versioned scenario definition persisted as a portable JSON document."""

    __tablename__ = "scenarios"

    scenario_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    schema: Mapped[str] = mapped_column(Text, nullable=False)
    company_model_version: Mapped[str] = synonym("version")
    scenario_schema_version: Mapped[str] = synonym("schema")
    payload: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )


class ExperimentRecord(Base):
    """Durable experiment request, lifecycle, outputs and failure details."""

    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="status",
        ),
        CheckConstraint("seed >= 0", name="seed_non_negative"),
        CheckConstraint(
            "replication_count > 0",
            name="replication_count_positive",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_experiments_idempotency_key",
        ),
        Index("ix_experiments_scenario_id", "scenario_id"),
        Index(
            "ix_experiments_baseline_experiment_id",
            "baseline_experiment_id",
        ),
        Index("ix_experiments_status", "status"),
        Index(
            "ix_experiments_baseline_lookup",
            "scenario_id",
            "status",
            "seed",
            "replication_count",
            "id",
        ),
        Index(
            "ix_experiments_queued_created_at",
            "created_at",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    id: Mapped[int] = mapped_column(
        _identity_type(),
        Identity(always=True),
        primary_key=True,
    )
    scenario_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("scenarios.scenario_id", ondelete="RESTRICT"),
        nullable=False,
    )
    baseline_experiment_id: Mapped[int | None] = mapped_column(
        _identity_type(),
        ForeignKey("experiments.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ExperimentStatus] = mapped_column(
        Text,
        nullable=False,
        default="queued",
        server_default=text("'queued'"),
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    master_seed: Mapped[int] = synonym("seed")
    replication_count: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload: Mapped[JsonObject] = mapped_column(
        _json_type(),
        nullable=False,
    )
    result_payload: Mapped[JsonObject | None] = mapped_column(
        _json_type(),
        nullable=True,
    )
    comparison_payload: Mapped[JsonObject | None] = mapped_column(
        _json_type(),
        nullable=True,
    )
    brief_payload: Mapped[JsonObject | None] = mapped_column(
        _json_type(),
        nullable=True,
    )
    artifact_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
