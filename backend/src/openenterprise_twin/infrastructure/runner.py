"""Recoverable in-process adapter for durable Monte Carlo execution."""

import logging
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import BoundedSemaphore, Lock

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.application.experiments import ExperimentQueueFullError
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.models import ExperimentRecord
from openenterprise_twin.infrastructure.repositories import (
    ExperimentRepository,
    ScenarioRepository,
)
from openenterprise_twin.simulation.experiment import (
    ExperimentRequest,
    run_experiment_with_traces,
)
from openenterprise_twin.simulation.reference import build_northstar_company

logger = logging.getLogger(__name__)


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
