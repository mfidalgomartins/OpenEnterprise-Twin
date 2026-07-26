"""PostgreSQL migration contracts for populated tenant backfills."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pytest import MonkeyPatch
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url

POSTGRES_TEST_URL = os.getenv("OPENENTERPRISE_TWIN_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    POSTGRES_TEST_URL is None,
    reason="requires the PostgreSQL 16 CI service",
)


@pytest.fixture
def isolated_database(monkeypatch: MonkeyPatch) -> Iterator[Engine]:
    assert POSTGRES_TEST_URL is not None
    base_url = make_url(POSTGRES_TEST_URL)
    database_name = f"oet_migration_{uuid4().hex}"
    admin_url = base_url.set(database="postgres")
    database_url = base_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    engine = create_engine(database_url)
    monkeypatch.setenv(
        "OPENENTERPRISE_TWIN_DATABASE_URL",
        database_url.render_as_string(hide_password=False),
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


def test_populated_v05_schema_backfills_and_reverses_tenant_ownership(
    isolated_database: Engine,
) -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "0003_decision_loop")
    with isolated_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO scenarios "
                "(scenario_id, name, version, schema, payload) "
                "VALUES ('baseline', 'Baseline', '0.5.0', '0.5.0', '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO experiments "
                "(scenario_id, status, seed, replication_count, "
                "idempotency_key, request_payload) "
                "VALUES ('baseline', 'queued', 731, 1, 'legacy-key', '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO decisions "
                "(decision_id, title, owner, state, version, content) "
                "VALUES ('decision-1', 'Decision', 'analyst', 'draft', 1, '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO decision_events "
                "(decision_id, sequence, from_state, to_state, actor, "
                "content_digest, occurred_at) "
                "VALUES ('decision-1', 1, NULL, 'draft', 'analyst', "
                "repeat('a', 64), now())"
            )
        )
        connection.execute(
            text(
                "INSERT INTO historical_datasets "
                "(dataset_id, company_id, data_digest, observation_count, "
                "payload, quality) "
                "VALUES ('history-1', 'northstar', repeat('b', 64), 1, '{}', '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO calibrations "
                "(calibration_id, dataset_id, company_model_version, digest, "
                "calibration, credibility, backtests) "
                "VALUES ('cal-1', 'history-1', '0.5.0', repeat('c', 64), "
                "'{}', '{}', '[]')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO optimizations "
                "(company_model_version, digest, evaluations, config, result) "
                "VALUES ('0.5.0', repeat('d', 64), 1, '{}', '{}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO monitoring_reports "
                "(decision_id, recommended_level, report) "
                "VALUES ('decision-1', 'within_expectation', '{}')"
            )
        )

    command.upgrade(config, "head")
    table_names = (
        "scenarios",
        "experiments",
        "decisions",
        "decision_events",
        "historical_datasets",
        "calibrations",
        "optimizations",
        "monitoring_reports",
    )
    with isolated_database.connect() as connection:
        for table_name in table_names:
            tenant_id = connection.scalar(
                text(f"SELECT tenant_id FROM {table_name} LIMIT 1")
            )
            assert tenant_id == "default"

    command.downgrade(config, "0003_decision_loop")
    columns = {
        column["name"]
        for column in inspect(isolated_database).get_columns("scenarios")
    }
    assert "tenant_id" not in columns

    command.upgrade(config, "head")
    columns = {
        column["name"]
        for column in inspect(isolated_database).get_columns("scenarios")
    }
    assert "tenant_id" in columns
