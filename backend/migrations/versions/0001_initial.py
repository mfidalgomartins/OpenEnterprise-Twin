"""Create scenario and experiment persistence tables.

Revision ID: 0001_initial
Revises: None
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _identity_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "scenarios",
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("schema", sa.Text(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("scenario_id", name="pk_scenarios"),
    )

    op.create_table(
        "experiments",
        sa.Column(
            "id",
            _identity_type(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("scenario_id", sa.Text(), nullable=False),
        sa.Column("baseline_experiment_id", _identity_type(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("replication_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("request_payload", _json_type(), nullable=False),
        sa.Column("result_payload", _json_type(), nullable=True),
        sa.Column("comparison_payload", _json_type(), nullable=True),
        sa.Column("brief_payload", _json_type(), nullable=True),
        sa.Column("artifact_digest", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name=op.f("ck_experiments_status"),
        ),
        sa.CheckConstraint(
            "seed >= 0",
            name=op.f("ck_experiments_seed_non_negative"),
        ),
        sa.CheckConstraint(
            "replication_count > 0",
            name=op.f("ck_experiments_replication_count_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["baseline_experiment_id"],
            ["experiments.id"],
            name="fk_experiments_baseline_experiment_id_experiments",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.scenario_id"],
            name="fk_experiments_scenario_id_scenarios",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experiments"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_experiments_idempotency_key",
        ),
    )
    op.create_index(
        "ix_experiments_scenario_id",
        "experiments",
        ["scenario_id"],
        unique=False,
    )
    op.create_index(
        "ix_experiments_baseline_experiment_id",
        "experiments",
        ["baseline_experiment_id"],
        unique=False,
    )
    op.create_index(
        "ix_experiments_status",
        "experiments",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_experiments_baseline_lookup",
        "experiments",
        ["scenario_id", "status", "seed", "replication_count", "id"],
        unique=False,
    )
    op.create_index(
        "ix_experiments_queued_created_at",
        "experiments",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )


def downgrade() -> None:
    op.drop_index("ix_experiments_queued_created_at", table_name="experiments")
    op.drop_index("ix_experiments_baseline_lookup", table_name="experiments")
    op.drop_index("ix_experiments_status", table_name="experiments")
    op.drop_index(
        "ix_experiments_baseline_experiment_id",
        table_name="experiments",
    )
    op.drop_index("ix_experiments_scenario_id", table_name="experiments")
    op.drop_table("experiments")
    op.drop_table("scenarios")
