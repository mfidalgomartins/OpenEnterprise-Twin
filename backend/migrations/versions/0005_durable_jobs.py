"""Create the tenant-scoped durable analytical job queue.

Revision ID: 0005_durable_jobs
Revises: 0004_identity_tenancy
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_durable_jobs"
down_revision: str | None = "0004_identity_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("request_payload", _json_type(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "progress",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.String(length=64),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancellation_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result_resource_type",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "result_resource_id",
            sa.String(length=128),
            nullable=True,
        ),
        sa.Column("result_digest", sa.String(length=64), nullable=True),
        sa.Column("problem", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ("
            "'experiment', 'calibration', 'optimization', "
            "'adaptive_comparison'"
            ")",
            name=op.f("ck_jobs_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name=op.f("ck_jobs_status"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts",
            name=op.f("ck_jobs_attempts"),
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 10",
            name=op.f("ck_jobs_max_attempts"),
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name=op.f("ck_jobs_progress"),
        ),
        sa.CheckConstraint(
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
            name=op.f("ck_jobs_lifecycle_consistency"),
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_jobs")),
        sa.UniqueConstraint(
            "tenant_id",
            "job_id",
            name="uq_jobs_tenant_id_job_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "kind",
            "idempotency_key",
            name="uq_jobs_tenant_kind_idempotency_key",
        ),
    )
    op.create_index(
        "ix_jobs_queue",
        "jobs",
        ["tenant_id", "status", "next_attempt_at", "created_at", "job_id"],
        unique=False,
    )
    op.create_index(
        "ix_jobs_lease_expiry",
        "jobs",
        ["tenant_id", "status", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_jobs_created_at",
        "jobs",
        ["tenant_id", "created_at", "job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_lease_expiry", table_name="jobs")
    op.drop_index("ix_jobs_queue", table_name="jobs")
    op.drop_table("jobs")
