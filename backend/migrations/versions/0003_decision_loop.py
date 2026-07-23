"""Create calibration, optimization and monitoring persistence tables.

Revision ID: 0003_decision_loop
Revises: 0002_decision_ledger
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_decision_loop"
down_revision: str | None = "0002_decision_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_LEVELS = (
    "within_expectation",
    "early_warning",
    "material_deviation",
    "recalibration_required",
    "decision_review_required",
)
_ALERT_LEVEL_SQL = ", ".join(f"'{level}'" for level in _ALERT_LEVELS)


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _identity_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "historical_datasets",
        sa.Column("dataset_id", sa.Text(), nullable=False),
        sa.Column("company_id", sa.Text(), nullable=False),
        sa.Column("data_digest", sa.String(length=64), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("quality", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("dataset_id", name="pk_historical_datasets"),
    )
    op.create_index(
        "ix_historical_datasets_company_id",
        "historical_datasets",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "calibrations",
        sa.Column("calibration_id", sa.Text(), nullable=False),
        sa.Column("dataset_id", sa.Text(), nullable=False),
        sa.Column("company_model_version", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("calibration", _json_type(), nullable=False),
        sa.Column("credibility", _json_type(), nullable=False),
        sa.Column("backtests", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["historical_datasets.dataset_id"],
            name="fk_calibrations_dataset_id_historical_datasets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("calibration_id", name="pk_calibrations"),
    )
    op.create_index(
        "ix_calibrations_dataset_id", "calibrations", ["dataset_id"], unique=False
    )
    op.create_index(
        "ix_calibrations_created_at",
        "calibrations",
        ["created_at", "calibration_id"],
        unique=False,
    )

    op.create_table(
        "optimizations",
        sa.Column(
            "id", _identity_type(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("company_model_version", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("evaluations", sa.Integer(), nullable=False),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("result", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_optimizations"),
    )
    op.create_index(
        "ix_optimizations_created_at",
        "optimizations",
        ["created_at", "id"],
        unique=False,
    )

    op.create_table(
        "monitoring_reports",
        sa.Column(
            "id", _identity_type(), sa.Identity(always=True), nullable=False
        ),
        sa.Column("decision_id", sa.Text(), nullable=False),
        sa.Column("recommended_level", sa.Text(), nullable=False),
        sa.Column("report", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"recommended_level IN ({_ALERT_LEVEL_SQL})",
            name=op.f("ck_monitoring_reports_recommended_level"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decisions.decision_id"],
            name="fk_monitoring_reports_decision_id_decisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_monitoring_reports"),
    )
    op.create_index(
        "ix_monitoring_reports_decision_id",
        "monitoring_reports",
        ["decision_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_monitoring_reports_decision_id", table_name="monitoring_reports"
    )
    op.drop_table("monitoring_reports")
    op.drop_index("ix_optimizations_created_at", table_name="optimizations")
    op.drop_table("optimizations")
    op.drop_index("ix_calibrations_created_at", table_name="calibrations")
    op.drop_index("ix_calibrations_dataset_id", table_name="calibrations")
    op.drop_table("calibrations")
    op.drop_index(
        "ix_historical_datasets_company_id", table_name="historical_datasets"
    )
    op.drop_table("historical_datasets")
