"""Environment-backed application infrastructure settings."""

from pathlib import Path
from typing import Annotated

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PoolSize = Annotated[int, Field(ge=1, le=100)]
PoolOverflow = Annotated[int, Field(ge=0, le=100)]
PoolTimeout = Annotated[float, Field(gt=0, le=300)]
PoolRecycle = Annotated[int, Field(gt=0, le=86_400)]
WorkerCount = Annotated[int, Field(ge=1, le=32)]
ReplicationWorkerCount = Annotated[int, Field(ge=1, le=16)]
ShutdownTimeout = Annotated[float, Field(gt=0, le=300)]


class Settings(BaseSettings):
    """Runtime settings loaded from ``OPENENTERPRISE_TWIN_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="OPENENTERPRISE_TWIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://localhost/openenterprise_twin"
    database_pool_size: PoolSize = 5
    database_max_overflow: PoolOverflow = 10
    database_pool_timeout_seconds: PoolTimeout = 30.0
    database_pool_recycle_seconds: PoolRecycle = 1_800
    artifact_directory: Path = Path("artifacts")
    experiment_workers: WorkerCount = 2
    replication_workers_per_experiment: ReplicationWorkerCount = 4
    experiment_shutdown_timeout_seconds: ShutdownTimeout = 5.0
    cors_allowed_origins: tuple[AnyHttpUrl, ...] = ()
