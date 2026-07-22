"""Environment-backed application infrastructure settings."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PoolSize = Annotated[int, Field(ge=1, le=100)]
PoolOverflow = Annotated[int, Field(ge=0, le=100)]
PoolTimeout = Annotated[float, Field(gt=0, le=300)]
PoolRecycle = Annotated[int, Field(gt=0, le=86_400)]
WorkerCount = Annotated[int, Field(ge=1, le=32)]
ReplicationWorkerCount = Annotated[int, Field(ge=1, le=16)]
ShutdownTimeout = Annotated[float, Field(gt=0, le=300)]
RequestBodyBytes = Annotated[int, Field(ge=1, le=100_000_000)]
ExperimentPeriods = Annotated[int, Field(ge=1, le=100_000_000)]


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
    deployment_environment: Literal["development", "test", "production"] = (
        "development"
    )
    api_key: SecretStr | None = None
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    max_request_body_bytes: RequestBodyBytes = 1_048_576
    max_experiment_periods: ExperimentPeriods = 50_000

    @model_validator(mode="after")
    def require_production_api_key(self) -> "Settings":
        if self.deployment_environment == "production" and (
            self.api_key is None
            or len(self.api_key.get_secret_value()) < 32
        ):
            raise ValueError(
                "api_key with at least 32 characters is required in production"
            )
        return self
