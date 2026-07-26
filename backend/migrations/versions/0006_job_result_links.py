"""Link generated optimization results to their durable source jobs.

Revision ID: 0006_job_result_links
Revises: 0005_durable_jobs
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_job_result_links"
down_revision: str | None = "0005_durable_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "optimizations",
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
    )
    op.create_unique_constraint(
        "uq_optimizations_tenant_source_job_id",
        "optimizations",
        ["tenant_id", "source_job_id"],
    )
    op.create_foreign_key(
        "fk_optimizations_tenant_source_job",
        "optimizations",
        "jobs",
        ["tenant_id", "source_job_id"],
        ["tenant_id", "job_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_optimizations_source_job_id",
        "optimizations",
        ["tenant_id", "source_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_optimizations_source_job_id",
        table_name="optimizations",
    )
    op.drop_constraint(
        "fk_optimizations_tenant_source_job",
        "optimizations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_optimizations_tenant_source_job_id",
        "optimizations",
        type_="unique",
    )
    op.drop_column("optimizations", "source_job_id")
