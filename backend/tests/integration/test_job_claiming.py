"""Concurrency contract for durable job claiming."""

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from openenterprise_twin.application.jobs import SubmitJob
from openenterprise_twin.infrastructure.jobs import SqlJobRepository
from openenterprise_twin.infrastructure.models import Base


@pytest.fixture
def postgres_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[sessionmaker]:
    configured_url = os.getenv("OPENENTERPRISE_TWIN_TEST_POSTGRES_URL")
    if configured_url is None:
        pytest.skip("requires the PostgreSQL 16 CI service")
    base_url = make_url(configured_url)
    database_name = f"oet_jobs_{uuid4().hex}"
    admin_engine = create_engine(
        base_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
    database_url = base_url.set(database=database_name)
    engine = create_engine(database_url, pool_size=12, max_overflow=4)
    monkeypatch.setenv(
        "OPENENTERPRISE_TWIN_DATABASE_URL",
        database_url.render_as_string(hide_password=False),
    )
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    try:
        yield sessionmaker(engine, expire_on_commit=False)
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


def test_concurrent_workers_claim_each_job_at_most_once(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'claiming.db'}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    repository = SqlJobRepository(factory, "tenant-a")
    expected = {
        repository.submit(
            SubmitJob(
                kind="experiment",
                created_by="analyst",
                request_payload={"scenario_id": f"scenario-{index}"},
            )
        ).job.job_id
        for index in range(12)
    }
    now = datetime(2026, 7, 26, 10, tzinfo=UTC)

    def claim(worker_number: int) -> str | None:
        claimed = SqlJobRepository(factory, "tenant-a").claim_next(
            worker_id=f"worker-{worker_number}",
            lease_duration=timedelta(minutes=1),
            now=now,
        )
        return None if claimed is None else claimed.job_id

    with ThreadPoolExecutor(max_workers=12) as executor:
        claimed = tuple(executor.map(claim, range(24)))

    claimed_ids = tuple(job_id for job_id in claimed if job_id is not None)
    assert set(claimed_ids) == expected
    assert len(claimed_ids) == len(set(claimed_ids))


def test_postgres_skip_locked_distributes_jobs_across_workers(
    postgres_factory: sessionmaker,
) -> None:
    repository = SqlJobRepository(postgres_factory, "tenant-a")
    expected = {
        repository.submit(
            SubmitJob(
                kind="calibration",
                created_by="analyst",
                request_payload={"dataset_id": f"dataset-{index}"},
            )
        ).job.job_id
        for index in range(8)
    }
    now = datetime(2026, 7, 26, 10, tzinfo=UTC)

    def claim(worker_number: int) -> str | None:
        claimed = SqlJobRepository(postgres_factory, "tenant-a").claim_next(
            worker_id=f"postgres-worker-{worker_number}",
            lease_duration=timedelta(minutes=1),
            now=now,
        )
        return None if claimed is None else claimed.job_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        claimed = tuple(executor.map(claim, range(16)))

    claimed_ids = tuple(job_id for job_id in claimed if job_id is not None)
    assert set(claimed_ids) == expected
    assert len(claimed_ids) == len(set(claimed_ids))
