"""Create the append-only decision ledger tables.

Revision ID: 0002_decision_ledger
Revises: 0001_initial
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_decision_ledger"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _identity_type() -> sa.BigInteger:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("decision_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Text(),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("content", _json_type(), nullable=False),
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
        sa.CheckConstraint(
            f"state IN ({_DECISION_STATE_SQL})",
            name=op.f("ck_decisions_state"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_decisions_version_positive"),
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_decisions"),
    )
    op.create_index(
        "ix_decisions_state", "decisions", ["state"], unique=False
    )
    op.create_index(
        "ix_decisions_updated_at",
        "decisions",
        ["updated_at", "decision_id"],
        unique=False,
    )

    op.create_table(
        "decision_events",
        sa.Column(
            "id",
            _identity_type(),
            sa.Identity(always=True),
            nullable=False,
        ),
        sa.Column("decision_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("approval", _json_type(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"to_state IN ({_DECISION_STATE_SQL})",
            name=op.f("ck_decision_events_to_state"),
        ),
        sa.CheckConstraint(
            "sequence >= 1",
            name=op.f("ck_decision_events_sequence_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["decisions.decision_id"],
            name="fk_decision_events_decision_id_decisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decision_events"),
        sa.UniqueConstraint(
            "decision_id",
            "sequence",
            name="uq_decision_events_decision_id",
        ),
    )
    op.create_index(
        "ix_decision_events_decision_id",
        "decision_events",
        ["decision_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decision_events_decision_id", table_name="decision_events"
    )
    op.drop_table("decision_events")
    op.drop_index("ix_decisions_updated_at", table_name="decisions")
    op.drop_index("ix_decisions_state", table_name="decisions")
    op.drop_table("decisions")
