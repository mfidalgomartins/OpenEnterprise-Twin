"""Make every persisted business resource tenant-owned.

Revision ID: 0004_identity_tenancy
Revises: 0003_decision_loop
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_identity_tenancy"
down_revision: str | None = "0003_decision_loop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "scenarios",
    "experiments",
    "decisions",
    "decision_events",
    "historical_datasets",
    "calibrations",
    "optimizations",
    "monitoring_reports",
)

_TENANT_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "experiments",
        "ix_experiments_scenario_id",
        ("tenant_id", "scenario_id"),
    ),
    (
        "experiments",
        "ix_experiments_baseline_experiment_id",
        ("tenant_id", "baseline_experiment_id"),
    ),
    (
        "experiments",
        "ix_experiments_status",
        ("tenant_id", "status"),
    ),
    (
        "experiments",
        "ix_experiments_baseline_lookup",
        (
            "tenant_id",
            "scenario_id",
            "status",
            "seed",
            "replication_count",
            "id",
        ),
    ),
    (
        "decisions",
        "ix_decisions_state",
        ("tenant_id", "state"),
    ),
    (
        "decisions",
        "ix_decisions_updated_at",
        ("tenant_id", "updated_at", "decision_id"),
    ),
    (
        "decision_events",
        "ix_decision_events_decision_id",
        ("tenant_id", "decision_id", "sequence"),
    ),
    (
        "historical_datasets",
        "ix_historical_datasets_company_id",
        ("tenant_id", "company_id"),
    ),
    (
        "calibrations",
        "ix_calibrations_dataset_id",
        ("tenant_id", "dataset_id"),
    ),
    (
        "calibrations",
        "ix_calibrations_created_at",
        ("tenant_id", "created_at", "calibration_id"),
    ),
    (
        "optimizations",
        "ix_optimizations_created_at",
        ("tenant_id", "created_at", "id"),
    ),
    (
        "monitoring_reports",
        "ix_monitoring_reports_decision_id",
        ("tenant_id", "decision_id", "id"),
    ),
)

_LEGACY_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("experiments", "ix_experiments_scenario_id", ("scenario_id",)),
    (
        "experiments",
        "ix_experiments_baseline_experiment_id",
        ("baseline_experiment_id",),
    ),
    ("experiments", "ix_experiments_status", ("status",)),
    (
        "experiments",
        "ix_experiments_baseline_lookup",
        ("scenario_id", "status", "seed", "replication_count", "id"),
    ),
    ("decisions", "ix_decisions_state", ("state",)),
    (
        "decisions",
        "ix_decisions_updated_at",
        ("updated_at", "decision_id"),
    ),
    (
        "decision_events",
        "ix_decision_events_decision_id",
        ("decision_id", "sequence"),
    ),
    (
        "historical_datasets",
        "ix_historical_datasets_company_id",
        ("company_id",),
    ),
    ("calibrations", "ix_calibrations_dataset_id", ("dataset_id",)),
    (
        "calibrations",
        "ix_calibrations_created_at",
        ("created_at", "calibration_id"),
    ),
    (
        "optimizations",
        "ix_optimizations_created_at",
        ("created_at", "id"),
    ),
    (
        "monitoring_reports",
        "ix_monitoring_reports_decision_id",
        ("decision_id", "id"),
    ),
)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "tenant_id",
                sa.Text(),
                nullable=True,
                server_default=sa.text("'default'"),
            ),
        )

    _drop_legacy_foreign_keys()
    op.drop_constraint(
        "uq_experiments_idempotency_key",
        "experiments",
        type_="unique",
    )
    op.drop_constraint(
        "uq_decision_events_decision_id",
        "decision_events",
        type_="unique",
    )

    for table, name, _columns in _LEGACY_INDEXES:
        op.drop_index(name, table_name=table)
    op.drop_index(
        "ix_experiments_queued_created_at",
        table_name="experiments",
    )

    for table in (
        "scenarios",
        "decisions",
        "historical_datasets",
        "calibrations",
    ):
        op.drop_constraint(f"pk_{table}", table, type_="primary")

    op.create_primary_key(
        "pk_scenarios",
        "scenarios",
        ["tenant_id", "scenario_id"],
    )
    op.create_primary_key(
        "pk_decisions",
        "decisions",
        ["tenant_id", "decision_id"],
    )
    op.create_primary_key(
        "pk_historical_datasets",
        "historical_datasets",
        ["tenant_id", "dataset_id"],
    )
    op.create_primary_key(
        "pk_calibrations",
        "calibrations",
        ["tenant_id", "calibration_id"],
    )

    op.create_unique_constraint(
        "uq_experiments_tenant_id_id",
        "experiments",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_experiments_tenant_id_idempotency_key",
        "experiments",
        ["tenant_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_decision_events_tenant_decision_sequence",
        "decision_events",
        ["tenant_id", "decision_id", "sequence"],
    )
    op.create_unique_constraint(
        "uq_optimizations_tenant_id_id",
        "optimizations",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_monitoring_reports_tenant_id_id",
        "monitoring_reports",
        ["tenant_id", "id"],
    )

    _create_tenant_foreign_keys()
    for table, name, columns in _TENANT_INDEXES:
        op.create_index(name, table, list(columns), unique=False)
    op.create_index(
        "ix_experiments_queued_created_at",
        "experiments",
        ["tenant_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )

    for table in _TABLES:
        op.alter_column(
            table,
            "tenant_id",
            existing_type=sa.Text(),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    _assert_legacy_identifiers_are_unique()
    _drop_tenant_foreign_keys()

    for table, name, _columns in _TENANT_INDEXES:
        op.drop_index(name, table_name=table)
    op.drop_index(
        "ix_experiments_queued_created_at",
        table_name="experiments",
    )

    op.drop_constraint(
        "uq_monitoring_reports_tenant_id_id",
        "monitoring_reports",
        type_="unique",
    )
    op.drop_constraint(
        "uq_optimizations_tenant_id_id",
        "optimizations",
        type_="unique",
    )
    op.drop_constraint(
        "uq_decision_events_tenant_decision_sequence",
        "decision_events",
        type_="unique",
    )
    op.drop_constraint(
        "uq_experiments_tenant_id_idempotency_key",
        "experiments",
        type_="unique",
    )
    op.drop_constraint(
        "uq_experiments_tenant_id_id",
        "experiments",
        type_="unique",
    )

    for table in (
        "scenarios",
        "decisions",
        "historical_datasets",
        "calibrations",
    ):
        op.drop_constraint(f"pk_{table}", table, type_="primary")

    op.create_primary_key("pk_scenarios", "scenarios", ["scenario_id"])
    op.create_primary_key("pk_decisions", "decisions", ["decision_id"])
    op.create_primary_key(
        "pk_historical_datasets",
        "historical_datasets",
        ["dataset_id"],
    )
    op.create_primary_key(
        "pk_calibrations",
        "calibrations",
        ["calibration_id"],
    )

    op.create_unique_constraint(
        "uq_experiments_idempotency_key",
        "experiments",
        ["idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_decision_events_decision_id",
        "decision_events",
        ["decision_id", "sequence"],
    )
    _create_legacy_foreign_keys()

    for table, name, columns in _LEGACY_INDEXES:
        op.create_index(name, table, list(columns), unique=False)
    op.create_index(
        "ix_experiments_queued_created_at",
        "experiments",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )

    for table in reversed(_TABLES):
        op.drop_column(table, "tenant_id")


def _drop_legacy_foreign_keys() -> None:
    constraints = (
        (
            "experiments",
            "fk_experiments_scenario_id_scenarios",
        ),
        (
            "experiments",
            "fk_experiments_baseline_experiment_id_experiments",
        ),
        (
            "decision_events",
            "fk_decision_events_decision_id_decisions",
        ),
        (
            "calibrations",
            "fk_calibrations_dataset_id_historical_datasets",
        ),
        (
            "monitoring_reports",
            "fk_monitoring_reports_decision_id_decisions",
        ),
    )
    for table, name in constraints:
        op.drop_constraint(name, table, type_="foreignkey")


def _create_tenant_foreign_keys() -> None:
    op.create_foreign_key(
        "fk_experiments_tenant_scenario",
        "experiments",
        "scenarios",
        ["tenant_id", "scenario_id"],
        ["tenant_id", "scenario_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_experiments_tenant_baseline",
        "experiments",
        "experiments",
        ["tenant_id", "baseline_experiment_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_decision_events_tenant_decision",
        "decision_events",
        "decisions",
        ["tenant_id", "decision_id"],
        ["tenant_id", "decision_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_calibrations_tenant_dataset",
        "calibrations",
        "historical_datasets",
        ["tenant_id", "dataset_id"],
        ["tenant_id", "dataset_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_monitoring_reports_tenant_decision",
        "monitoring_reports",
        "decisions",
        ["tenant_id", "decision_id"],
        ["tenant_id", "decision_id"],
        ondelete="RESTRICT",
    )


def _drop_tenant_foreign_keys() -> None:
    constraints = (
        ("experiments", "fk_experiments_tenant_scenario"),
        ("experiments", "fk_experiments_tenant_baseline"),
        ("decision_events", "fk_decision_events_tenant_decision"),
        ("calibrations", "fk_calibrations_tenant_dataset"),
        ("monitoring_reports", "fk_monitoring_reports_tenant_decision"),
    )
    for table, name in constraints:
        op.drop_constraint(name, table, type_="foreignkey")


def _create_legacy_foreign_keys() -> None:
    op.create_foreign_key(
        "fk_experiments_scenario_id_scenarios",
        "experiments",
        "scenarios",
        ["scenario_id"],
        ["scenario_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_experiments_baseline_experiment_id_experiments",
        "experiments",
        "experiments",
        ["baseline_experiment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_decision_events_decision_id_decisions",
        "decision_events",
        "decisions",
        ["decision_id"],
        ["decision_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_calibrations_dataset_id_historical_datasets",
        "calibrations",
        "historical_datasets",
        ["dataset_id"],
        ["dataset_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_monitoring_reports_decision_id_decisions",
        "monitoring_reports",
        "decisions",
        ["decision_id"],
        ["decision_id"],
        ondelete="RESTRICT",
    )


def _assert_legacy_identifiers_are_unique() -> None:
    checks = (
        ("scenarios", "scenario_id"),
        ("decisions", "decision_id"),
        ("historical_datasets", "dataset_id"),
        ("calibrations", "calibration_id"),
    )
    connection = op.get_bind()
    for table, identifier in checks:
        duplicate = connection.execute(
            sa.text(
                f"SELECT 1 FROM {table} GROUP BY {identifier} "
                "HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise RuntimeError(
                f"cannot downgrade tenancy: duplicate {table}.{identifier}"
            )
    duplicate_idempotency = connection.execute(
        sa.text(
            "SELECT 1 FROM experiments WHERE idempotency_key IS NOT NULL "
            "GROUP BY idempotency_key HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).scalar_one_or_none()
    if duplicate_idempotency is not None:
        raise RuntimeError(
            "cannot downgrade tenancy: duplicate experiment idempotency keys"
        )
