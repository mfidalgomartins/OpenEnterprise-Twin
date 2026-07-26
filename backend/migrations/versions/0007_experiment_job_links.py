"""Link durable experiment resources to their source jobs.

Revision ID: 0007_experiment_job_links
Revises: 0006_job_result_links
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_experiment_job_links"
down_revision: str | None = "0006_job_result_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("source_job_id", sa.String(length=36), nullable=True),
    )
    op.create_unique_constraint(
        "uq_experiments_tenant_source_job_id",
        "experiments",
        ["tenant_id", "source_job_id"],
    )
    op.create_foreign_key(
        "fk_experiments_tenant_source_job",
        "experiments",
        "jobs",
        ["tenant_id", "source_job_id"],
        ["tenant_id", "job_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_experiments_source_job_id",
        "experiments",
        ["tenant_id", "source_job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiments_source_job_id",
        table_name="experiments",
    )
    op.drop_constraint(
        "fk_experiments_tenant_source_job",
        "experiments",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_experiments_tenant_source_job_id",
        "experiments",
        type_="unique",
    )
    op.drop_column("experiments", "source_job_id")
