"""SQLAlchemy records for scenarios and durable experiment lifecycle state."""

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
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
JobKind = Literal[
    "experiment",
    "calibration",
    "optimization",
    "adaptive_comparison",
]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
JsonObject = dict[str, Any]
DEFAULT_TENANT_ID = "default"

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

    tenant_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=DEFAULT_TENANT_ID,
    )
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
            "seed <= 9223372036854775807",
            name="seed_bigint",
        ),
        CheckConstraint(
            "replication_count > 0",
            name="replication_count_positive",
        ),
        CheckConstraint(
            "("
            "status = 'queued' AND started_at IS NULL AND completed_at IS NULL "
            "AND artifact_digest IS NULL AND result_payload IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL"
            ") OR ("
            "status = 'running' AND started_at IS NOT NULL "
            "AND completed_at IS NULL AND artifact_digest IS NULL "
            "AND result_payload IS NULL AND error_code IS NULL "
            "AND error_detail IS NULL"
            ") OR ("
            "status = 'completed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND artifact_digest IS NOT NULL "
            "AND result_payload IS NOT NULL AND error_code IS NULL "
            "AND error_detail IS NULL"
            ") OR ("
            "status = 'failed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND artifact_digest IS NULL "
            "AND result_payload IS NULL AND error_code IS NOT NULL "
            "AND error_detail IS NOT NULL"
            ")",
            name="lifecycle_consistency",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_experiments_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_experiments_tenant_id_idempotency_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_job_id",
            name="uq_experiments_tenant_source_job_id",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "scenario_id"),
            ("scenarios.tenant_id", "scenarios.scenario_id"),
            name="fk_experiments_tenant_scenario",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "baseline_experiment_id"),
            ("experiments.tenant_id", "experiments.id"),
            name="fk_experiments_tenant_baseline",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "source_job_id"),
            ("jobs.tenant_id", "jobs.job_id"),
            name="fk_experiments_tenant_source_job",
            ondelete="RESTRICT",
        ),
        Index("ix_experiments_scenario_id", "tenant_id", "scenario_id"),
        Index(
            "ix_experiments_baseline_experiment_id",
            "tenant_id",
            "baseline_experiment_id",
        ),
        Index("ix_experiments_status", "tenant_id", "status"),
        Index(
            "ix_experiments_source_job_id",
            "tenant_id",
            "source_job_id",
        ),
        Index(
            "ix_experiments_baseline_lookup",
            "tenant_id",
            "scenario_id",
            "status",
            "seed",
            "replication_count",
            "id",
        ),
        Index(
            "ix_experiments_queued_created_at",
            "tenant_id",
            "created_at",
            "id",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DEFAULT_TENANT_ID,
    )
    id: Mapped[int] = mapped_column(
        _identity_type(),
        Identity(always=True),
        primary_key=True,
    )
    scenario_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    baseline_experiment_id: Mapped[int | None] = mapped_column(
        _identity_type(),
        nullable=True,
    )
    source_job_id: Mapped[str | None] = mapped_column(
        String(36),
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


class JobRecord(Base):
    """Tenant-scoped analytical job with lease-based worker ownership."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ("
            "'experiment', 'calibration', 'optimization', "
            "'adaptive_comparison'"
            ")",
            name="kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name="attempts",
        ),
        CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 10",
            name="max_attempts",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress"),
        CheckConstraint(
            "("
            "status = 'queued' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL "
            "AND finished_at IS NULL AND result_resource_type IS NULL "
            "AND result_resource_id IS NULL AND result_digest IS NULL "
            "AND problem IS NULL"
            ") OR ("
            "status = 'running' AND started_at IS NOT NULL "
            "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND finished_at IS NULL "
            "AND result_resource_type IS NULL AND result_resource_id IS NULL "
            "AND result_digest IS NULL AND problem IS NULL"
            ") OR ("
            "status = 'succeeded' AND started_at IS NOT NULL "
            "AND finished_at IS NOT NULL AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL "
            "AND progress = 100 AND result_resource_type IS NOT NULL "
            "AND result_resource_id IS NOT NULL AND result_digest IS NOT NULL "
            "AND problem IS NULL"
            ") OR ("
            "status = 'failed' AND started_at IS NOT NULL "
            "AND finished_at IS NOT NULL AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL "
            "AND result_resource_type IS NULL AND result_resource_id IS NULL "
            "AND result_digest IS NULL AND problem IS NOT NULL"
            ") OR ("
            "status = 'cancelled' AND finished_at IS NOT NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL "
            "AND heartbeat_at IS NULL AND result_resource_type IS NULL "
            "AND result_resource_id IS NULL AND result_digest IS NULL "
            "AND problem IS NULL"
            ")",
            name="lifecycle_consistency",
        ),
        UniqueConstraint(
            "tenant_id",
            "job_id",
            name="uq_jobs_tenant_id_job_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "kind",
            "idempotency_key",
            name="uq_jobs_tenant_kind_idempotency_key",
        ),
        Index(
            "ix_jobs_queue",
            "tenant_id",
            "status",
            "next_attempt_at",
            "created_at",
            "job_id",
        ),
        Index(
            "ix_jobs_lease_expiry",
            "tenant_id",
            "status",
            "lease_expires_at",
        ),
        Index(
            "ix_jobs_created_at",
            "tenant_id",
            "created_at",
            "job_id",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[JobKind] = mapped_column(Text, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Text,
        nullable=False,
        default="queued",
        server_default=text("'queued'"),
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    progress: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    stage: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="queued",
        server_default=text("'queued'"),
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(),
        nullable=True,
    )
    result_resource_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    result_resource_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    problem: Mapped[JsonObject | None] = mapped_column(_json_type(), nullable=True)
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
    finished_at: Mapped[datetime | None] = mapped_column(
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


_DECISION_STATES = (
    "draft",
    "evidence_ready",
    "under_review",
    "approved",
    "implemented",
    "monitoring",
    "successful",
    "underperformed",
    "superseded",
    "abandoned",
)
_DECISION_STATE_SQL = ", ".join(f"'{state}'" for state in _DECISION_STATES)


class DecisionLedgerRecord(Base):
    """Current, version-controlled snapshot of one governed decision."""

    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({_DECISION_STATE_SQL})",
            name="state",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_decisions_state", "tenant_id", "state"),
        Index(
            "ix_decisions_updated_at",
            "tenant_id",
            "updated_at",
            "decision_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=DEFAULT_TENANT_ID,
    )
    decision_id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="draft",
        server_default=text("'draft'"),
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    content: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
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


class DecisionEventRecord(Base):
    """Append-only audit event for a decision transition, revision or approval."""

    __tablename__ = "decision_events"
    __table_args__ = (
        CheckConstraint(
            f"to_state IN ({_DECISION_STATE_SQL})",
            name="to_state",
        ),
        CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_DECISION_STATE_SQL})",
            name="from_state",
        ),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        UniqueConstraint(
            "tenant_id",
            "decision_id",
            "sequence",
            name="uq_decision_events_tenant_decision_sequence",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "decision_id"),
            ("decisions.tenant_id", "decisions.decision_id"),
            name="fk_decision_events_tenant_decision",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_decision_events_decision_id",
            "tenant_id",
            "decision_id",
            "sequence",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DEFAULT_TENANT_ID,
    )
    id: Mapped[int] = mapped_column(
        _identity_type(),
        Identity(always=True),
        primary_key=True,
    )
    decision_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_state: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approval: Mapped[JsonObject | None] = mapped_column(_json_type(), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class HistoricalDatasetRecord(Base):
    """An ingested historical dataset with its data-quality profile."""

    __tablename__ = "historical_datasets"
    __table_args__ = (
        Index(
            "ix_historical_datasets_company_id",
            "tenant_id",
            "company_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=DEFAULT_TENANT_ID,
    )
    dataset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    company_id: Mapped[str] = mapped_column(Text, nullable=False)
    data_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    quality: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class CalibrationRecord(Base):
    """A calibration of a twin with its credibility score and backtests."""

    __tablename__ = "calibrations"
    __table_args__ = (
        Index("ix_calibrations_dataset_id", "tenant_id", "dataset_id"),
        Index(
            "ix_calibrations_created_at",
            "tenant_id",
            "created_at",
            "calibration_id",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "dataset_id"),
            ("historical_datasets.tenant_id", "historical_datasets.dataset_id"),
            name="fk_calibrations_tenant_dataset",
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=DEFAULT_TENANT_ID,
    )
    calibration_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    company_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    calibration: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    credibility: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    backtests: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class OptimizationRecord(Base):
    """A completed policy-optimization run and its Pareto result."""

    __tablename__ = "optimizations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_optimizations_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_job_id",
            name="uq_optimizations_tenant_source_job_id",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "source_job_id"),
            ("jobs.tenant_id", "jobs.job_id"),
            name="fk_optimizations_tenant_source_job",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_optimizations_created_at",
            "tenant_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_optimizations_source_job_id",
            "tenant_id",
            "source_job_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DEFAULT_TENANT_ID,
    )
    id: Mapped[int] = mapped_column(
        _identity_type(),
        Identity(always=True),
        primary_key=True,
    )
    source_job_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    company_model_version: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluations: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    result: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )


class MonitoringReportRecord(Base):
    """A monitoring report reconciling realised outcomes for a decision."""

    __tablename__ = "monitoring_reports"
    __table_args__ = (
        CheckConstraint(
            "recommended_level IN ("
            "'within_expectation', 'early_warning', 'material_deviation', "
            "'recalibration_required', 'decision_review_required')",
            name="recommended_level",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_monitoring_reports_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "decision_id"),
            ("decisions.tenant_id", "decisions.decision_id"),
            name="fk_monitoring_reports_tenant_decision",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_monitoring_reports_decision_id",
            "tenant_id",
            "decision_id",
            "id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DEFAULT_TENANT_ID,
    )
    id: Mapped[int] = mapped_column(
        _identity_type(),
        Identity(always=True),
        primary_key=True,
    )
    decision_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    recommended_level: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[JsonObject] = mapped_column(_json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
