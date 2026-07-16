"""Focused contracts for persistence configuration and ORM records."""

from datetime import UTC

import pytest


def test_settings_load_bounded_database_pool_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OPENENTERPRISE_TWIN_DATABASE_URL",
        "sqlite+pysqlite:///:memory:",
    )
    monkeypatch.setenv("OPENENTERPRISE_TWIN_DATABASE_POOL_SIZE", "3")
    monkeypatch.setenv("OPENENTERPRISE_TWIN_DATABASE_MAX_OVERFLOW", "4")
    monkeypatch.setenv("OPENENTERPRISE_TWIN_DATABASE_POOL_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("OPENENTERPRISE_TWIN_DATABASE_POOL_RECYCLE_SECONDS", "600")

    from openenterprise_twin.infrastructure.settings import Settings

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.database_pool_size == 3
    assert settings.database_max_overflow == 4
    assert settings.database_pool_timeout_seconds == 12
    assert settings.database_pool_recycle_seconds == 600


def test_settings_default_to_postgresql_psycopg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "DATABASE_URL",
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT_SECONDS",
        "DATABASE_POOL_RECYCLE_SECONDS",
    ):
        monkeypatch.delenv(f"OPENENTERPRISE_TWIN_{name}", raising=False)

    from openenterprise_twin.infrastructure.settings import Settings

    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.database_pool_size > 0
    assert settings.database_max_overflow >= 0
    assert settings.database_pool_timeout_seconds > 0
    assert settings.database_pool_recycle_seconds > 0


def test_metadata_uses_lowercase_identifiers_and_portable_json() -> None:
    from sqlalchemy import BigInteger, Identity
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.dialects.postgresql import JSONB

    from openenterprise_twin.infrastructure.models import (
        Base,
        ExperimentRecord,
        ScenarioRecord,
    )

    assert set(Base.metadata.tables) == {"experiments", "scenarios"}
    assert all(
        identifier == identifier.lower()
        for table in Base.metadata.tables.values()
        for identifier in (table.name, *(column.name for column in table.columns))
    )

    for column in (
        ScenarioRecord.__table__.c.payload,
        ExperimentRecord.__table__.c.request_payload,
        ExperimentRecord.__table__.c.result_payload,
        ExperimentRecord.__table__.c.comparison_payload,
        ExperimentRecord.__table__.c.brief_payload,
    ):
        assert isinstance(
            column.type.dialect_impl(postgresql.dialect()),
            JSONB,
        )
        assert column.type.compile(dialect=sqlite.dialect()) == "JSON"

    identifier = ExperimentRecord.__table__.c.id
    assert isinstance(identifier.type, BigInteger)
    assert isinstance(identifier.identity, Identity)
    assert identifier.identity.always is True
    assert identifier.primary_key


def test_experiment_schema_has_required_constraints_and_indexes() -> None:
    from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

    from openenterprise_twin.infrastructure.models import ExperimentRecord

    table = ExperimentRecord.__table__
    check_constraints = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    foreign_keys = {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    indexes = {index.name: index for index in table.indexes}

    assert "ck_experiments_status" in check_constraints
    assert "ck_experiments_seed_non_negative" in check_constraints
    assert "ck_experiments_replication_count_positive" in check_constraints
    assert all(
        status in check_constraints["ck_experiments_status"]
        for status in ("queued", "running", "completed", "failed")
    )
    assert ("scenarios.scenario_id",) in foreign_keys
    assert ("experiments.id",) in foreign_keys
    assert ("idempotency_key",) in unique_columns
    assert {
        "ix_experiments_scenario_id",
        "ix_experiments_baseline_experiment_id",
        "ix_experiments_status",
        "ix_experiments_baseline_lookup",
        "ix_experiments_queued_created_at",
    } <= indexes.keys()
    queued_index = indexes["ix_experiments_queued_created_at"]
    assert tuple(column.name for column in queued_index.columns) == (
        "created_at",
        "id",
    )
    assert str(queued_index.dialect_options["postgresql"]["where"]) == (
        "status = 'queued'"
    )


def test_domain_facing_names_alias_canonical_storage_attributes() -> None:
    from openenterprise_twin.infrastructure.models import (
        ExperimentRecord,
        ScenarioRecord,
    )

    scenario = ScenarioRecord(
        scenario_id="baseline",
        name="Baseline",
        company_model_version="0.1.0",
        scenario_schema_version="0.1.0",
        payload={},
    )
    experiment = ExperimentRecord(
        scenario_id="baseline",
        master_seed=731,
        replication_count=1,
        request_payload={},
    )

    assert scenario.version == scenario.company_model_version == "0.1.0"
    assert scenario.schema == scenario.scenario_schema_version == "0.1.0"
    assert experiment.seed == experiment.master_seed == 731


def test_sqlite_session_round_trips_records_with_aware_timestamps() -> None:
    from openenterprise_twin.infrastructure.database import (
        create_database_engine,
        create_session_factory,
    )
    from openenterprise_twin.infrastructure.models import (
        Base,
        ExperimentRecord,
        ScenarioRecord,
    )
    from openenterprise_twin.infrastructure.settings import Settings

    engine = create_database_engine(
        Settings(database_url="sqlite+pysqlite:///:memory:", _env_file=None)
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    with session_factory.begin() as session:
        scenario = ScenarioRecord(
            scenario_id="baseline",
            name="Baseline",
            version="0.1.0",
            schema="0.1.0",
            payload={"scenario_id": "baseline"},
        )
        session.add(scenario)

    with session_factory.begin() as session:
        experiment = ExperimentRecord(
            scenario_id="baseline",
            seed=731,
            replication_count=100,
            idempotency_key="baseline-731",
            request_payload={"replications": 100, "master_seed": 731},
        )
        session.add(experiment)

    with session_factory() as session:
        stored_scenario = session.get(ScenarioRecord, "baseline")
        stored_experiment = session.get(ExperimentRecord, experiment.id)

    assert stored_scenario is not None
    assert stored_scenario.payload == {"scenario_id": "baseline"}
    assert stored_scenario.created_at.tzinfo is not None
    assert stored_scenario.created_at.utcoffset() == UTC.utcoffset(None)
    assert stored_experiment is not None
    assert stored_experiment.status == "queued"
    assert stored_experiment.request_payload["master_seed"] == 731
    assert stored_experiment.result_payload is None
    assert stored_experiment.comparison_payload is None
    assert stored_experiment.brief_payload is None
    assert stored_experiment.error_code is None
    assert stored_experiment.error_detail is None
    assert stored_experiment.created_at.tzinfo is not None
    assert stored_experiment.updated_at.tzinfo is not None

    engine.dispose()


def test_sqlite_enforces_lifecycle_constraint() -> None:
    from sqlalchemy.exc import IntegrityError

    from openenterprise_twin.infrastructure.database import (
        create_database_engine,
        create_session_factory,
    )
    from openenterprise_twin.infrastructure.models import (
        Base,
        ExperimentRecord,
        ScenarioRecord,
    )
    from openenterprise_twin.infrastructure.settings import Settings

    engine = create_database_engine(
        Settings(database_url="sqlite+pysqlite:///:memory:", _env_file=None)
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    with session_factory.begin() as session:
        session.add(
            ScenarioRecord(
                scenario_id="baseline",
                name="Baseline",
                version="0.1.0",
                schema="0.1.0",
                payload={},
            )
        )

    with session_factory() as session:
        session.add(
            ExperimentRecord(
                scenario_id="baseline",
                status="cancelled",
                seed=1,
                replication_count=1,
                request_payload={},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()


def test_sqlite_enforces_scenario_foreign_key() -> None:
    from sqlalchemy.exc import IntegrityError

    from openenterprise_twin.infrastructure.database import (
        create_database_engine,
        create_session_factory,
    )
    from openenterprise_twin.infrastructure.models import Base, ExperimentRecord
    from openenterprise_twin.infrastructure.settings import Settings

    engine = create_database_engine(
        Settings(database_url="sqlite+pysqlite:///:memory:", _env_file=None)
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        session.add(
            ExperimentRecord(
                scenario_id="missing",
                seed=1,
                replication_count=1,
                request_payload={},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()
