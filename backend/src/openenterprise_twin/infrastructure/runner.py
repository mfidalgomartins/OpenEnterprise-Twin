"""Recoverable in-process adapter for durable Monte Carlo execution."""

import logging
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import BoundedSemaphore, Lock

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
        self._scheduled_ids: set[int] = set()
        self._futures: set[Future[None]] = set()
        self._shutting_down = False

    def submit(self, experiment_id: int) -> None:
        if not self._slots.acquire(blocking=False):
            raise ExperimentQueueFullError("experiment execution queue is full")
        with self._state_lock:
            if self._shutting_down:
                self._slots.release()
                raise RuntimeError("experiment runner is shutting down")
            if experiment_id in self._scheduled_ids:
                self._slots.release()
                return
            self._scheduled_ids.add(experiment_id)
        try:
            future = self._executor.submit(self._execute, experiment_id)
        except Exception:
            with self._state_lock:
                self._scheduled_ids.discard(experiment_id)
            self._slots.release()
            raise
        with self._state_lock:
            self._futures.add(future)

        def release_slot(completed: Future[None]) -> None:
            self._release_slot(completed, experiment_id)

        future.add_done_callback(release_slot)

    def recover_pending(self) -> None:
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
            repository.recover_interrupted()
            pending_ids = repository.pending_ids()
        for experiment_id in pending_ids:
            try:
                self.submit(experiment_id)
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
        experiment_id: int,
    ) -> None:
        with self._state_lock:
            self._futures.discard(future)
            self._scheduled_ids.discard(experiment_id)
            should_schedule = not self._shutting_down
        self._slots.release()
        if should_schedule:
            self._schedule_next_pending()

    def _schedule_next_pending(self) -> None:
        with self._session_factory() as session:
            pending_ids = ExperimentRepository(session).pending_ids()
        with self._state_lock:
            scheduled_ids = frozenset(self._scheduled_ids)
        next_id = next(
            (item for item in pending_ids if item not in scheduled_ids),
            None,
        )
        if next_id is None:
            return
        try:
            self.submit(next_id)
        except (ExperimentQueueFullError, RuntimeError):
            return

    def _execute(self, experiment_id: int) -> None:
        try:
            job = self._start_job(experiment_id)
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
                artifact_digest=digest,
                result_payload=summary,
            )
        except Exception as error:
            self._fail_job(experiment_id, error)

    def _start_job(
        self,
        experiment_id: int,
    ) -> tuple[object, int, int, int] | None:
        with self._session_factory() as session, session.begin():
            experiments = ExperimentRepository(session)
            record = experiments.claim_queued(experiment_id)
            if record is None:
                return None
            scenario_record = ScenarioRepository(session).get(record.scenario_id)
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
        *,
        artifact_digest: str,
        result_payload: dict[str, object],
    ) -> None:
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
            record = _required_experiment(repository, experiment_id)
            repository.mark_completed(
                record,
                artifact_digest=artifact_digest,
                result_payload=result_payload,
            )

    def _fail_job(self, experiment_id: int, error: Exception) -> None:
        logger.exception(
            "experiment execution failed",
            exc_info=error,
            extra={"experiment_id": experiment_id},
        )
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
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
