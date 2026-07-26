"""Recoverable in-process adapter for durable Monte Carlo execution."""

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore, Event, Lock, Thread
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.analytics.adaptive import compare_adaptive_vs_static
from openenterprise_twin.application.decision_loop import (
    CalibrationStudioService,
    OptimizationLabService,
)
from openenterprise_twin.application.experiments import ExperimentQueueFullError
from openenterprise_twin.application.job_handlers import (
    AdaptiveComparisonJobRequest,
    CalibrationJobRequest,
    ExperimentJobRequest,
    JobCancelledError,
    JobExecutionContext,
    JobExecutionError,
    JobExecutionResult,
    JobHandlerRegistry,
    NonRetryableJobExecutionError,
    OptimizationJobRequest,
    RetryableJobExecutionError,
)
from openenterprise_twin.application.jobs import (
    Job,
    JobConflictError,
    JobLeaseError,
    JobProblem,
    SubmitJob,
    validate_worker_id,
)
from openenterprise_twin.domain.errors import (
    DomainValidationError,
    InvariantViolation,
)
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.jobs import SqlJobRepository
from openenterprise_twin.infrastructure.models import ExperimentRecord, JobRecord
from openenterprise_twin.infrastructure.repositories import (
    ExperimentRepository,
    ScenarioRepository,
    SqlCalibrationRepository,
    SqlDatasetRepository,
    SqlOptimizationRepository,
)
from openenterprise_twin.simulation.experiment import (
    ExperimentRequest,
    run_experiment_with_traces,
)
from openenterprise_twin.simulation.reference import (
    build_baseline_scenario,
    build_northstar_company,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobQueueSnapshot:
    """Aggregate queue evidence without tenant or job identifier labels."""

    queued: int
    running: int
    stale_leases: int
    oldest_queued_age_seconds: float | None


class DurableJobWorker:
    """Claim and execute one SQL-backed job at a time with lease renewal."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        handlers: JobHandlerRegistry,
        worker_id: str,
        lease_duration: timedelta,
        heartbeat_interval: timedelta,
        retry_delay: timedelta,
    ) -> None:
        if lease_duration <= timedelta(0) or lease_duration > timedelta(hours=1):
            raise ValueError("lease_duration must be positive and at most one hour")
        if (
            heartbeat_interval <= timedelta(0)
            or heartbeat_interval >= lease_duration
        ):
            raise ValueError(
                "heartbeat_interval must be positive and shorter than the lease"
            )
        if retry_delay < timedelta(0) or retry_delay > timedelta(days=1):
            raise ValueError(
                "retry_delay must be non-negative and at most one day"
            )
        validate_worker_id(worker_id)
        self._session_factory = session_factory
        self._handlers = handlers
        self._worker_id = worker_id
        self._lease_duration = lease_duration
        self._heartbeat_interval = heartbeat_interval
        self._retry_delay = retry_delay
        self._tenant_offset = 0

    @property
    def worker_id(self) -> str:
        return self._worker_id

    def run_once(self, *, now: datetime | None = None) -> bool:
        """Recover expired leases, claim one eligible job and execute it."""

        current = _worker_now(now)
        tenants = self._active_tenants()
        if not tenants:
            return False
        ordered = _rotate(tenants, self._tenant_offset)
        self._tenant_offset = (self._tenant_offset + 1) % len(tenants)
        for tenant_id in ordered:
            SqlJobRepository(
                self._session_factory,
                tenant_id,
            ).recover_expired_leases(now=current)
        for tenant_id in ordered:
            claimed = SqlJobRepository(
                self._session_factory,
                tenant_id,
            ).claim_next(
                worker_id=self._worker_id,
                lease_duration=self._lease_duration,
                now=current,
            )
            if claimed is not None:
                operation_clock = (
                    (lambda: current)
                    if now is not None
                    else lambda: datetime.now(UTC)
                )
                self._execute_claimed(claimed, operation_clock)
                return True
        return False

    def run_forever(
        self,
        stop: Event,
        *,
        poll_interval: timedelta,
    ) -> None:
        if poll_interval <= timedelta(0) or poll_interval > timedelta(minutes=1):
            raise ValueError(
                "poll_interval must be positive and at most one minute"
            )
        while not stop.is_set():
            processed = self.run_once()
            if not processed:
                stop.wait(poll_interval.total_seconds())

    def _active_tenants(self) -> tuple[str, ...]:
        with self._session_factory() as session:
            return tuple(
                session.scalars(
                    select(JobRecord.tenant_id)
                    .where(JobRecord.status.in_(("queued", "running")))
                    .distinct()
                    .order_by(JobRecord.tenant_id)
                )
            )

    def _execute_claimed(
        self,
        job: Job,
        operation_clock: Callable[[], datetime],
    ) -> None:
        repository = SqlJobRepository(self._session_factory, job.tenant_id)
        stop_heartbeat = Event()
        lease_lost = Event()

        def heartbeat_loop() -> None:
            while not stop_heartbeat.wait(
                self._heartbeat_interval.total_seconds()
            ):
                try:
                    repository.heartbeat(
                        job.job_id,
                        worker_id=self._worker_id,
                        lease_duration=self._lease_duration,
                        now=operation_clock(),
                    )
                except JobLeaseError:
                    lease_lost.set()
                    return
                except Exception:
                    logger.exception(
                        "job heartbeat failed",
                        extra={"job_kind": job.kind},
                    )
                    lease_lost.set()
                    return

        heartbeat = Thread(
            target=heartbeat_loop,
            name=f"job-heartbeat-{self._worker_id}",
            daemon=True,
        )
        heartbeat.start()

        def cancellation_requested() -> bool:
            current = repository.get(job.job_id)
            return bool(
                current is None
                or current.cancellation_requested_at is not None
                or lease_lost.is_set()
            )

        def report(progress: int, stage: str) -> bool:
            current = repository.report_progress(
                job.job_id,
                worker_id=self._worker_id,
                progress=progress,
                stage=stage,
                lease_duration=self._lease_duration,
                now=operation_clock(),
            )
            return current.cancellation_requested_at is not None

        context = JobExecutionContext(
            job=job,
            report=report,
            cancellation_requested=cancellation_requested,
        )
        try:
            handler = self._handlers.resolve(job.kind)
            result = handler(job, context)
        except JobCancelledError:
            self._stop_heartbeat(stop_heartbeat, heartbeat)
            if not lease_lost.is_set():
                self._cancel(repository, job, operation_clock())
            return
        except JobExecutionError as error:
            self._stop_heartbeat(stop_heartbeat, heartbeat)
            if not lease_lost.is_set():
                self._fail(
                    repository,
                    job,
                    code=error.code,
                    detail=error.detail,
                    retryable=error.retryable,
                    now=operation_clock(),
                )
            return
        except Exception:
            logger.exception(
                "job execution failed",
                extra={"job_kind": job.kind, "attempt": job.attempt_count},
            )
            self._stop_heartbeat(stop_heartbeat, heartbeat)
            if not lease_lost.is_set():
                self._fail(
                    repository,
                    job,
                    code="job_execution_failed",
                    detail="The analytical job could not be completed.",
                    retryable=True,
                    now=operation_clock(),
                )
            return
        self._stop_heartbeat(stop_heartbeat, heartbeat)
        if lease_lost.is_set():
            return
        self._succeed(repository, job, result, operation_clock())

    @staticmethod
    def _stop_heartbeat(stop: Event, heartbeat: Thread) -> None:
        stop.set()
        heartbeat.join()

    def _succeed(
        self,
        repository: SqlJobRepository,
        job: Job,
        result: JobExecutionResult,
        now: datetime,
    ) -> None:
        try:
            latest = repository.get(job.job_id)
            if latest is not None and latest.cancellation_requested_at is not None:
                repository.cancel(
                    job.job_id,
                    worker_id=self._worker_id,
                    now=now,
                )
                return
            repository.succeed(
                job.job_id,
                worker_id=self._worker_id,
                result_resource_type=result.resource_type,
                result_resource_id=result.resource_id,
                result_digest=result.digest,
                now=now,
            )
        except JobLeaseError:
            logger.warning(
                "job success discarded after lease loss",
                extra={"job_kind": job.kind},
            )

    def _fail(
        self,
        repository: SqlJobRepository,
        job: Job,
        *,
        code: str,
        detail: str,
        retryable: bool,
        now: datetime,
    ) -> None:
        try:
            repository.fail(
                job.job_id,
                worker_id=self._worker_id,
                problem=JobProblem(code=code, detail=detail, occurred_at=now),
                retryable=retryable,
                retry_delay=self._retry_delay,
                now=now,
            )
        except JobLeaseError:
            logger.warning(
                "job failure discarded after lease loss",
                extra={"job_kind": job.kind},
            )

    def _cancel(
        self,
        repository: SqlJobRepository,
        job: Job,
        now: datetime,
    ) -> None:
        try:
            repository.cancel(
                job.job_id,
                worker_id=self._worker_id,
                now=now,
            )
        except JobLeaseError:
            logger.warning(
                "job cancellation discarded after lease loss",
                extra={"job_kind": job.kind},
            )


class EmbeddedJobWorkerPool:
    """Run the production worker loop in bounded local/test threads."""

    def __init__(
        self,
        *,
        workers: tuple[DurableJobWorker, ...],
        poll_interval: timedelta,
    ) -> None:
        if not workers:
            raise ValueError("at least one worker is required")
        self._workers = workers
        self._poll_interval = poll_interval
        self._stop = Event()
        self._threads: tuple[Thread, ...] = ()

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("worker pool is already started")
        self._threads = tuple(
            Thread(
                target=worker.run_forever,
                kwargs={
                    "stop": self._stop,
                    "poll_interval": self._poll_interval,
                },
                name=f"durable-{worker.worker_id}",
                daemon=True,
            )
            for worker in self._workers
        )
        for thread in self._threads:
            thread.start()

    def shutdown(self, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")
        self._stop.set()
        deadline = datetime.now(UTC).timestamp() + timeout_seconds
        for thread in self._threads:
            remaining = max(0.0, deadline - datetime.now(UTC).timestamp())
            thread.join(remaining)
        self._threads = ()


def build_worker_id(prefix: str = "worker") -> str:
    """Return a process-unique identity without host or credential material."""

    return f"{prefix}-{uuid4().hex}"


def job_queue_snapshot(
    session_factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
) -> JobQueueSnapshot:
    """Collect bounded aggregate queue health evidence."""

    current = _worker_now(now)
    with session_factory() as session:
        queued = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(JobRecord.status == "queued")
        )
        running = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(JobRecord.status == "running")
        )
        stale = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.status == "running",
                JobRecord.lease_expires_at <= current,
            )
        )
        oldest = session.scalar(
            select(func.min(JobRecord.created_at)).where(
                JobRecord.status == "queued"
            )
        )
    age = (
        None
        if oldest is None
        else max(0.0, (current - oldest).total_seconds())
    )
    return JobQueueSnapshot(
        queued=int(queued or 0),
        running=int(running or 0),
        stale_leases=int(stale or 0),
        oldest_queued_age_seconds=age,
    )


def backfill_active_experiment_jobs(
    session_factory: sessionmaker[Session],
) -> int:
    """Attach pre-v0.6 active experiments to the recoverable SQL job queue."""

    created = 0
    with session_factory.begin() as session:
        statement = (
            select(ExperimentRecord)
            .where(
                ExperimentRecord.status.in_(("queued", "running")),
                ExperimentRecord.source_job_id.is_(None),
            )
            .order_by(
                ExperimentRecord.tenant_id,
                ExperimentRecord.created_at,
                ExperimentRecord.id,
            )
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        active = tuple(session.scalars(statement))
        for experiment in active:
            repository = SqlJobRepository(
                session_factory,
                experiment.tenant_id,
            )
            try:
                submission = repository.submit_in_session(
                    session,
                    SubmitJob(
                        kind="experiment",
                        created_by="system-recovery",
                        request_payload={"experiment_id": experiment.id},
                        idempotency_key=experiment.idempotency_key,
                    ),
                )
            except JobConflictError:
                continue
            experiment.source_job_id = submission.job.job_id
            session.flush()
            created += 1
    return created


def _worker_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)


def _rotate(values: tuple[str, ...], offset: int) -> tuple[str, ...]:
    if not values:
        return ()
    normalized = offset % len(values)
    return values[normalized:] + values[:normalized]


def build_analytical_job_handlers(
    *,
    session_factory: sessionmaker[Session],
    artifact_store: FileArtifactStore,
    max_replication_workers: int,
    max_dataset_observations: int,
    max_optimization_evaluations: int,
    max_optimization_periods: int,
    max_adaptive_periods: int,
) -> JobHandlerRegistry:
    """Register all production analytical handlers with bounded dependencies."""

    handlers = _AnalyticalJobHandlers(
        session_factory=session_factory,
        artifact_store=artifact_store,
        max_replication_workers=max_replication_workers,
        max_dataset_observations=max_dataset_observations,
        max_optimization_evaluations=max_optimization_evaluations,
        max_optimization_periods=max_optimization_periods,
        max_adaptive_periods=max_adaptive_periods,
    )
    registry = JobHandlerRegistry()
    registry.register("experiment", handlers.experiment)
    registry.register("calibration", handlers.calibration)
    registry.register("optimization", handlers.optimization)
    registry.register("adaptive_comparison", handlers.adaptive_comparison)
    registry.require_complete()
    return registry


@dataclass(frozen=True, slots=True)
class _AnalyticalJobHandlers:
    session_factory: sessionmaker[Session]
    artifact_store: FileArtifactStore
    max_replication_workers: int
    max_dataset_observations: int
    max_optimization_evaluations: int
    max_optimization_periods: int
    max_adaptive_periods: int

    def experiment(
        self,
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        try:
            request = ExperimentJobRequest.model_validate(job.request_payload)
            context.checkpoint(progress=5, stage="loading")
            loaded = self._load_experiment(job.tenant_id, request.experiment_id)
            if isinstance(loaded, JobExecutionResult):
                return loaded
            scenario_payload, master_seed, replications, max_workers = loaded
            context.checkpoint(progress=15, stage="simulating")
            artifact = run_experiment_with_traces(
                ExperimentRequest(
                    company=build_northstar_company(),
                    scenario=Scenario.model_validate(scenario_payload),
                    master_seed=master_seed,
                    replications=replications,
                    max_workers=max_workers,
                )
            )
            context.checkpoint(progress=85, stage="persisting")
            digest = self.artifact_store.put_json(
                artifact.model_dump(mode="json")
            )
            summary = artifact.result.model_dump(
                mode="json",
                exclude={"replications"},
            )
            self._complete_experiment(
                job.tenant_id,
                request.experiment_id,
                artifact_digest=digest,
                result_payload=summary,
            )
            return JobExecutionResult(
                resource_type="experiment",
                resource_id=str(request.experiment_id),
                digest=digest,
            )
        except JobCancelledError:
            self._terminal_experiment_failure(
                job,
                code="experiment_cancelled",
                detail="Experiment execution was cancelled.",
            )
            raise
        except (DomainValidationError, LookupError, ValidationError) as error:
            self._terminal_experiment_failure(
                job,
                code="invalid_experiment_job",
                detail="The stored experiment job request is invalid.",
            )
            raise NonRetryableJobExecutionError(
                code="invalid_experiment_job",
                detail="The stored experiment job request is invalid.",
            ) from error
        except Exception as error:
            logger.exception(
                "analytical experiment handler failed",
                extra={"attempt": job.attempt_count},
            )
            if job.attempt_count >= job.max_attempts:
                self._terminal_experiment_failure(
                    job,
                    code="experiment_execution_failed",
                    detail="Experiment execution failed.",
                )
            else:
                self._reset_experiment(job)
            raise RetryableJobExecutionError(
                code="experiment_execution_failed",
                detail="Experiment execution could not be completed.",
            ) from error

    def calibration(
        self,
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        try:
            request = CalibrationJobRequest.model_validate(job.request_payload)
            context.checkpoint(progress=10, stage="loading")
            repository = SqlCalibrationRepository(
                self.session_factory,
                job.tenant_id,
            )
            stored = repository.get(request.calibration_id)
            if (
                stored is not None
                and stored.dataset_id != request.dataset_id
            ):
                raise DomainValidationError(
                    "calibration identifier belongs to another dataset"
                )
            if stored is None:
                service = CalibrationStudioService(
                    datasets=SqlDatasetRepository(
                        self.session_factory,
                        job.tenant_id,
                    ),
                    calibrations=repository,
                    max_observations=self.max_dataset_observations,
                )
                context.checkpoint(progress=25, stage="calibrating")
                stored = service.calibrate(
                    calibration_id=request.calibration_id,
                    dataset_id=request.dataset_id,
                    company=build_northstar_company(),
                    backtest_cutoff=request.backtest_cutoff,
                )
            payload = {
                "calibration_id": stored.calibration_id,
                "dataset_id": stored.dataset_id,
                "created_at": stored.created_at.isoformat(),
                "calibration": stored.calibration.model_dump(mode="json"),
                "credibility": stored.credibility.model_dump(mode="json"),
                "backtests": [
                    item.model_dump(mode="json") for item in stored.backtests
                ],
            }
            digest = self.artifact_store.put_json(payload)
            return JobExecutionResult(
                resource_type="calibration",
                resource_id=stored.calibration_id,
                digest=digest,
            )
        except (DomainValidationError, ValidationError) as error:
            raise NonRetryableJobExecutionError(
                code="invalid_calibration_job",
                detail="The stored calibration job request is invalid.",
            ) from error
        except JobCancelledError:
            raise
        except Exception as error:
            logger.exception(
                "analytical calibration handler failed",
                extra={"attempt": job.attempt_count},
            )
            raise RetryableJobExecutionError(
                code="calibration_execution_failed",
                detail="Calibration execution could not be completed.",
            ) from error

    def optimization(
        self,
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        try:
            request = OptimizationJobRequest.model_validate(job.request_payload)
            context.checkpoint(progress=10, stage="loading")
            repository = SqlOptimizationRepository(
                self.session_factory,
                job.tenant_id,
            )
            stored = repository.get_by_source_job_id(job.job_id)
            if stored is None:
                context.checkpoint(progress=20, stage="optimizing")
                stored = OptimizationLabService(
                    optimizations=repository,
                    max_evaluations=self.max_optimization_evaluations,
                    max_periods=self.max_optimization_periods,
                ).optimize(
                    company=build_northstar_company(),
                    base_scenario=build_baseline_scenario(
                        horizon_days=request.horizon_days
                    ),
                    config=request.config,
                    replications=request.replications,
                    master_seed=request.master_seed,
                    source_job_id=job.job_id,
                )
            payload = {
                "optimization_id": stored.optimization_id,
                "digest": stored.digest,
                "evaluations": stored.evaluations,
                "created_at": stored.created_at.isoformat(),
                "result": stored.result.model_dump(mode="json"),
            }
            artifact_digest = self.artifact_store.put_json(payload)
            return JobExecutionResult(
                resource_type="optimization",
                resource_id=str(stored.optimization_id),
                digest=artifact_digest,
            )
        except (DomainValidationError, ValidationError) as error:
            raise NonRetryableJobExecutionError(
                code="invalid_optimization_job",
                detail="The stored optimization job request is invalid.",
            ) from error
        except JobCancelledError:
            raise
        except Exception as error:
            logger.exception(
                "analytical optimization handler failed",
                extra={"attempt": job.attempt_count},
            )
            raise RetryableJobExecutionError(
                code="optimization_execution_failed",
                detail="Optimization execution could not be completed.",
            ) from error

    def adaptive_comparison(
        self,
        job: Job,
        context: JobExecutionContext,
    ) -> JobExecutionResult:
        try:
            request = AdaptiveComparisonJobRequest.model_validate(
                job.request_payload
            )
            estimated_periods = (
                2 * request.replications * request.horizon_days
            )
            if estimated_periods > self.max_adaptive_periods:
                raise DomainValidationError(
                    "adaptive comparison exceeds the deployment compute budget"
                )
            context.checkpoint(progress=15, stage="simulating")
            comparison = compare_adaptive_vs_static(
                company=build_northstar_company(),
                static_scenario=build_baseline_scenario(
                    horizon_days=request.horizon_days
                ),
                policy=request.policy,
                master_seed=request.master_seed,
                replications=request.replications,
            )
            context.checkpoint(progress=90, stage="persisting")
            digest = self.artifact_store.put_json(
                comparison.model_dump(mode="json")
            )
            return JobExecutionResult(
                resource_type="adaptive_comparison",
                resource_id=request.policy.policy_id,
                digest=digest,
            )
        except (DomainValidationError, ValidationError) as error:
            raise NonRetryableJobExecutionError(
                code="invalid_adaptive_job",
                detail="The stored adaptive comparison job request is invalid.",
            ) from error
        except JobCancelledError:
            raise
        except Exception as error:
            logger.exception(
                "analytical adaptive comparison handler failed",
                extra={"attempt": job.attempt_count},
            )
            raise RetryableJobExecutionError(
                code="adaptive_execution_failed",
                detail="Adaptive comparison execution could not be completed.",
            ) from error

    def _load_experiment(
        self,
        tenant_id: str,
        experiment_id: int,
    ) -> tuple[object, int, int, int] | JobExecutionResult:
        with self.session_factory() as session, session.begin():
            repository = ExperimentRepository(session, tenant_id)
            record = repository.get(experiment_id)
            if record is None:
                raise LookupError("experiment is not present")
            if record.status == "completed":
                assert record.artifact_digest is not None
                return JobExecutionResult(
                    resource_type="experiment",
                    resource_id=str(experiment_id),
                    digest=record.artifact_digest,
                )
            if record.status == "failed":
                raise DomainValidationError("experiment is already failed")
            if record.status == "running":
                record.status = "queued"
                record.started_at = None
                record.updated_at = datetime.now(UTC)
                session.flush()
            claimed = repository.claim_queued(experiment_id)
            if claimed is None:
                raise LookupError("experiment could not be claimed")
            scenario_record = ScenarioRepository(session, tenant_id).get(
                claimed.scenario_id
            )
            if scenario_record is None:
                raise LookupError("scenario is not present")
            return (
                scenario_record.payload,
                claimed.master_seed,
                claimed.replication_count,
                min(
                    int(claimed.request_payload.get("max_workers", 1)),
                    self.max_replication_workers,
                ),
            )

    def _complete_experiment(
        self,
        tenant_id: str,
        experiment_id: int,
        *,
        artifact_digest: str,
        result_payload: dict[str, object],
    ) -> None:
        with self.session_factory() as session, session.begin():
            repository = ExperimentRepository(session, tenant_id)
            record = _required_experiment(repository, experiment_id)
            repository.mark_completed(
                record,
                artifact_digest=artifact_digest,
                result_payload=result_payload,
            )

    def _terminal_experiment_failure(
        self,
        job: Job,
        *,
        code: str,
        detail: str,
    ) -> None:
        try:
            request = ExperimentJobRequest.model_validate(job.request_payload)
        except ValidationError:
            return
        with self.session_factory() as session, session.begin():
            repository = ExperimentRepository(session, job.tenant_id)
            record = repository.get(request.experiment_id)
            if record is not None and record.status in {"queued", "running"}:
                repository.mark_failed(
                    record,
                    error_code=code,
                    error_detail=detail,
                )

    def _reset_experiment(self, job: Job) -> None:
        try:
            request = ExperimentJobRequest.model_validate(job.request_payload)
        except ValidationError:
            return
        with self.session_factory() as session, session.begin():
            record = ExperimentRepository(
                session,
                job.tenant_id,
            ).get(request.experiment_id)
            if record is not None and record.status == "running":
                record.status = "queued"
                record.started_at = None
                record.updated_at = datetime.now(UTC)
                session.flush()


class BoundedExperimentRunner:
    """Execute simulations outside transactions with bounded concurrency."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        artifact_store: FileArtifactStore,
        max_workers: int,
        max_replication_workers: int,
        max_queue_size: int | None = None,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        if max_replication_workers <= 0:
            raise ValueError("max_replication_workers must be positive")
        queue_size = max_queue_size if max_queue_size is not None else max_workers * 2
        if queue_size < 0:
            raise ValueError("max_queue_size cannot be negative")
        self._session_factory = session_factory
        self._artifact_store = artifact_store
        self._max_replication_workers = max_replication_workers
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="enterprise-twin",
        )
        self._slots = BoundedSemaphore(max_workers + queue_size)
        self._state_lock = Lock()
        self._scheduled_ids: set[tuple[str, int]] = set()
        self._futures: set[Future[None]] = set()
        self._shutting_down = False

    def submit(self, experiment_id: int, tenant_id: str) -> None:
        execution_key = (tenant_id, experiment_id)
        if not self._slots.acquire(blocking=False):
            raise ExperimentQueueFullError("experiment execution queue is full")
        with self._state_lock:
            if self._shutting_down:
                self._slots.release()
                raise RuntimeError("experiment runner is shutting down")
            if execution_key in self._scheduled_ids:
                self._slots.release()
                return
            self._scheduled_ids.add(execution_key)
        try:
            future = self._executor.submit(
                self._execute,
                experiment_id,
                tenant_id,
            )
        except Exception:
            with self._state_lock:
                self._scheduled_ids.discard(execution_key)
            self._slots.release()
            raise
        with self._state_lock:
            self._futures.add(future)

        def release_slot(completed: Future[None]) -> None:
            self._release_slot(completed, execution_key)

        future.add_done_callback(release_slot)

    def recover_pending(self) -> None:
        with self._session_factory() as session, session.begin():
            tenant_ids = tuple(
                session.scalars(
                    select(ExperimentRecord.tenant_id)
                    .where(
                        ExperimentRecord.status.in_(("queued", "running"))
                    )
                    .distinct()
                )
            )
            pending: list[tuple[str, int]] = []
            for tenant_id in tenant_ids:
                repository = ExperimentRepository(session, tenant_id)
                repository.recover_interrupted()
                pending.extend(
                    (tenant_id, experiment_id)
                    for experiment_id in repository.pending_ids()
                )
        for tenant_id, experiment_id in pending:
            try:
                self.submit(experiment_id, tenant_id)
            except ExperimentQueueFullError:
                break

    def shutdown(self, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            raise ValueError("shutdown timeout must be positive")
        with self._state_lock:
            self._shutting_down = True
            futures = tuple(self._futures)
        _, unfinished = wait(futures, timeout=timeout_seconds)
        for future in unfinished:
            future.cancel()
        self._executor.shutdown(wait=not unfinished, cancel_futures=True)

    def _release_slot(
        self,
        future: Future[None],
        execution_key: tuple[str, int],
    ) -> None:
        with self._state_lock:
            self._futures.discard(future)
            self._scheduled_ids.discard(execution_key)
            should_schedule = not self._shutting_down
        self._slots.release()
        if should_schedule:
            self._schedule_next_pending()

    def _schedule_next_pending(self) -> None:
        with self._session_factory() as session:
            pending = tuple(
                (tenant_id, experiment_id)
                for tenant_id, experiment_id in session.execute(
                    select(
                        ExperimentRecord.tenant_id,
                        ExperimentRecord.id,
                    )
                    .where(ExperimentRecord.status == "queued")
                    .order_by(
                        ExperimentRecord.created_at,
                        ExperimentRecord.id,
                    )
                )
            )
        with self._state_lock:
            scheduled_ids = frozenset(self._scheduled_ids)
        next_key = next(
            (item for item in pending if item not in scheduled_ids),
            None,
        )
        if next_key is None:
            return
        try:
            self.submit(next_key[1], next_key[0])
        except (ExperimentQueueFullError, RuntimeError):
            return

    def _execute(self, experiment_id: int, tenant_id: str) -> None:
        try:
            job = self._start_job(experiment_id, tenant_id)
            if job is None:
                return
            scenario_payload, master_seed, replication_count, max_workers = job
            scenario = Scenario.model_validate(scenario_payload)
            artifact = run_experiment_with_traces(
                ExperimentRequest(
                    company=build_northstar_company(),
                    scenario=scenario,
                    master_seed=master_seed,
                    replications=replication_count,
                    max_workers=max_workers,
                )
            )
            result = artifact.result
            digest = self._artifact_store.put_json(
                artifact.model_dump(mode="json")
            )
            summary = result.model_dump(mode="json", exclude={"replications"})
            self._complete_job(
                experiment_id,
                tenant_id,
                artifact_digest=digest,
                result_payload=summary,
            )
        except Exception as error:
            self._fail_job(experiment_id, tenant_id, error)

    def _start_job(
        self,
        experiment_id: int,
        tenant_id: str,
    ) -> tuple[object, int, int, int] | None:
        with self._session_factory() as session, session.begin():
            experiments = ExperimentRepository(session, tenant_id)
            record = experiments.claim_queued(experiment_id)
            if record is None:
                return None
            scenario_record = ScenarioRepository(session, tenant_id).get(
                record.scenario_id
            )
            if scenario_record is None:
                raise LookupError(
                    f"scenario '{record.scenario_id}' is not present"
                )
            return (
                scenario_record.payload,
                record.master_seed,
                record.replication_count,
                min(
                    int(record.request_payload.get("max_workers", 1)),
                    self._max_replication_workers,
                ),
            )

    def _complete_job(
        self,
        experiment_id: int,
        tenant_id: str,
        *,
        artifact_digest: str,
        result_payload: dict[str, object],
    ) -> None:
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session, tenant_id)
            record = _required_experiment(repository, experiment_id)
            repository.mark_completed(
                record,
                artifact_digest=artifact_digest,
                result_payload=result_payload,
            )

    def _fail_job(
        self,
        experiment_id: int,
        tenant_id: str,
        error: Exception,
    ) -> None:
        logger.exception(
            "experiment execution failed",
            exc_info=error,
            extra={"experiment_id": experiment_id, "tenant_id": tenant_id},
        )
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session, tenant_id)
            record = repository.get(experiment_id)
            if record is None or record.status not in {"queued", "running"}:
                return
            code = (
                error.code
                if isinstance(error, InvariantViolation)
                else "experiment_execution"
            )
            repository.mark_failed(
                record,
                error_code=code,
                error_detail="Experiment execution failed.",
            )


def _required_experiment(
    repository: ExperimentRepository,
    experiment_id: int,
) -> ExperimentRecord:
    record = repository.get(experiment_id)
    if record is None:
        raise LookupError(f"experiment '{experiment_id}' is not present")
    return record
