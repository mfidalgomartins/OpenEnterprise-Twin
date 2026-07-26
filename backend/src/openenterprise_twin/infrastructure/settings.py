"""Environment-backed application infrastructure settings."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from openenterprise_twin.application.identity import Role

PoolSize = Annotated[int, Field(ge=1, le=100)]
PoolOverflow = Annotated[int, Field(ge=0, le=100)]
PoolTimeout = Annotated[float, Field(gt=0, le=300)]
PoolRecycle = Annotated[int, Field(gt=0, le=86_400)]
WorkerCount = Annotated[int, Field(ge=1, le=32)]
JobWorkerCount = Annotated[int, Field(ge=1, le=16)]
JobPollInterval = Annotated[float, Field(gt=0, le=60)]
JobLeaseSeconds = Annotated[float, Field(ge=1, le=3_600)]
JobHeartbeatSeconds = Annotated[float, Field(gt=0, le=1_800)]
JobRetryDelaySeconds = Annotated[float, Field(ge=0, le=86_400)]
ReplicationWorkerCount = Annotated[int, Field(ge=1, le=16)]
ShutdownTimeout = Annotated[float, Field(gt=0, le=300)]
RequestBodyBytes = Annotated[int, Field(ge=1, le=100_000_000)]
ExperimentPeriods = Annotated[int, Field(ge=1, le=100_000_000)]
DatasetObservations = Annotated[int, Field(ge=1, le=1_000_000)]
OptimizationEvaluations = Annotated[int, Field(ge=8, le=20_000)]
BuildCommit = Annotated[
    str,
    Field(min_length=7, max_length=64, pattern=r"^[0-9a-f]+$"),
]
IdentityIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@|/-]*$"),
]
TenantIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$"),
]
ClaimName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$"),
]
OidcAlgorithm = Literal["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
_DEVELOPMENT_TRUSTED_HOSTS = ("localhost", "127.0.0.1", "testserver")


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
    job_worker_mode: Literal["embedded", "external"] = "external"
    job_workers: JobWorkerCount = 2
    job_poll_interval_seconds: JobPollInterval = 0.25
    job_lease_seconds: JobLeaseSeconds = 30.0
    job_heartbeat_seconds: JobHeartbeatSeconds = 10.0
    job_retry_delay_seconds: JobRetryDelaySeconds = 2.0
    job_shutdown_timeout_seconds: ShutdownTimeout = 10.0
    cors_allowed_origins: tuple[AnyHttpUrl, ...] = ()
    deployment_environment: Literal["development", "test", "production"] = (
        "development"
    )
    build_commit: BuildCommit | None = None
    authentication_mode: Literal["local", "api_key", "oidc"] = "local"
    api_key: SecretStr | None = None
    local_subject: IdentityIdentifier = "local-operator"
    local_tenant_id: TenantIdentifier = "default"
    local_roles: tuple[Role, ...] = ("admin",)
    service_account_subject: IdentityIdentifier = "enterprise-service"
    service_account_tenant_id: TenantIdentifier = "default"
    service_account_roles: tuple[Role, ...] = ("admin",)
    oidc_issuer: AnyHttpUrl | None = None
    oidc_audience: IdentityIdentifier | None = None
    oidc_jwks_url: AnyHttpUrl | None = None
    oidc_algorithms: tuple[OidcAlgorithm, ...] = ("RS256",)
    oidc_tenant_claim: ClaimName = "tenant_id"
    oidc_roles_claim: ClaimName = "roles"
    oidc_clock_skew_seconds: Annotated[int, Field(ge=0, le=300)] = 30
    oidc_jwks_cache_ttl_seconds: Annotated[int, Field(ge=30, le=86_400)] = 300
    oidc_http_timeout_seconds: Annotated[float, Field(gt=0, le=30)] = 3.0
    oidc_jwks_max_response_bytes: Annotated[
        int, Field(ge=1_024, le=1_048_576)
    ] = 65_536
    trusted_hosts: tuple[str, ...] = _DEVELOPMENT_TRUSTED_HOSTS
    max_request_body_bytes: RequestBodyBytes = 4_194_304
    max_experiment_periods: ExperimentPeriods = 50_000
    # Direct JSON uploads are gated first by max_request_body_bytes; this count
    # cap is a reachable secondary guard within that body budget. Bulk history
    # belongs on the file/database connectors, not a single JSON request.
    max_dataset_observations: DatasetObservations = 30_000
    max_optimization_evaluations: OptimizationEvaluations = 400
    max_optimization_periods: ExperimentPeriods = 1_000_000
    max_adaptive_periods: ExperimentPeriods = 150_000

    @field_validator("local_roles", "service_account_roles")
    @classmethod
    def require_non_empty_roles(
        cls,
        roles: tuple[Role, ...],
    ) -> tuple[Role, ...]:
        if not roles:
            raise ValueError("at least one role is required")
        return tuple(dict.fromkeys(roles))

    @field_validator("oidc_algorithms")
    @classmethod
    def require_oidc_algorithms(
        cls,
        algorithms: tuple[OidcAlgorithm, ...],
    ) -> tuple[OidcAlgorithm, ...]:
        if not algorithms:
            raise ValueError("oidc_algorithms must not be empty")
        return tuple(dict.fromkeys(algorithms))

    @model_validator(mode="after")
    def require_safe_deployment_configuration(self) -> "Settings":
        if self.job_heartbeat_seconds >= self.job_lease_seconds:
            raise ValueError(
                "job_heartbeat_seconds must be shorter than job_lease_seconds"
            )
        if self.authentication_mode == "api_key":
            if self.api_key is None:
                raise ValueError(
                    "authentication mode api_key requires api_key"
                )
        elif self.authentication_mode == "oidc" and (
            self.oidc_issuer is None
            or self.oidc_audience is None
            or self.oidc_jwks_url is None
        ):
            raise ValueError(
                "authentication mode oidc requires issuer, audience and "
                "JWKS URL"
            )

        if self.deployment_environment == "production":
            if self.authentication_mode == "local":
                raise ValueError(
                    "authentication mode local is not allowed in production"
                )
            if (
                self.authentication_mode == "api_key"
                and self.api_key is not None
                and len(self.api_key.get_secret_value()) < 32
            ):
                raise ValueError(
                    "authentication mode api_key requires api_key with at least "
                    "32 characters in production"
                )
            if self.authentication_mode == "oidc":
                assert self.oidc_issuer is not None
                assert self.oidc_jwks_url is not None
                if (
                    self.oidc_issuer.scheme != "https"
                    or self.oidc_jwks_url.scheme != "https"
                ):
                    raise ValueError(
                        "authentication mode oidc requires HTTPS issuer and "
                        "JWKS URL in production"
                    )
            if (
                not self.trusted_hosts
                or "*" in self.trusted_hosts
                or self.trusted_hosts == _DEVELOPMENT_TRUSTED_HOSTS
            ):
                raise ValueError(
                    "trusted_hosts must be explicit and restrictive in production"
                )
        return self
