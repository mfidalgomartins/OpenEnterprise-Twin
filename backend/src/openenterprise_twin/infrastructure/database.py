"""Synchronous SQLAlchemy engine and session construction."""

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from openenterprise_twin.infrastructure.settings import Settings

SessionFactory = sessionmaker[Session]


def create_database_engine(settings: Settings) -> Engine:
    """Build a sync engine with bounded production pooling and SQLite support."""

    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        sqlite_options: dict[str, Any] = {
            "connect_args": {"check_same_thread": False},
            "pool_pre_ping": True,
        }
        if url.database in {None, "", ":memory:"}:
            sqlite_options["poolclass"] = StaticPool
        engine = create_engine(settings.database_url, **sqlite_options)
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
        return engine

    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
    )


def create_session_factory(engine: Engine) -> SessionFactory:
    """Create an explicit synchronous session factory bound to ``engine``."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
