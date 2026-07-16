"""Bounded background execution for durable Monte Carlo experiments."""

from concurrent.futures import Future, ThreadPoolExecutor
from threading import BoundedSemaphore
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

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
    run_experiment,
)
from openenterprise_twin.simulation.reference import build_northstar_company


class ExperimentQueueFullError(RuntimeError):
    """Raised when both execution and bounded queue capacity are exhausted."""


class ExperimentRunner(Protocol):
    def submit(self, experiment_id: int) -> None: ...

    def shutdown(self) -> None: ...


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

    def submit(self, experiment_id: int) -> None:
        if not self._slots.acquire(blocking=False):
            raise ExperimentQueueFullError("experiment execution queue is full")
        try:
            future = self._executor.submit(self._execute, experiment_id)
        except Exception:
            self._slots.release()
            raise
        future.add_done_callback(self._release_slot)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _release_slot(self, future: Future[None]) -> None:
        self._slots.release()

    def _execute(self, experiment_id: int) -> None:
        try:
            scenario_payload, master_seed, replication_count, max_workers = (
                self._start_job(experiment_id)
            )
            scenario = Scenario.model_validate(scenario_payload)
            result = run_experiment(
                ExperimentRequest(
                    company=build_northstar_company(),
                    scenario=scenario,
                    master_seed=master_seed,
                    replications=replication_count,
                    max_workers=max_workers,
                )
            )
            full_payload = result.model_dump(mode="json")
            digest = self._artifact_store.put_json(full_payload)
            summary = result.model_dump(
                mode="json",
                exclude={"replications"},
            )
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
    ) -> tuple[object, int, int, int]:
        with self._session_factory() as session, session.begin():
            experiments = ExperimentRepository(session)
            record = experiments.get(experiment_id)
            if record is None:
                raise LookupError(f"experiment '{experiment_id}' is not present")
            scenario_record = ScenarioRepository(session).get(record.scenario_id)
            if scenario_record is None:
                raise LookupError(
                    f"scenario '{record.scenario_id}' is not present"
                )
            experiments.mark_running(record)
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
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
            record = repository.get(experiment_id)
            if record is None:
                return
            code = (
                error.code
                if isinstance(error, InvariantViolation)
                else "experiment_execution"
            )
            detail = str(error).strip() or "Experiment execution failed."
            repository.mark_failed(
                record,
                error_code=code,
                error_detail=detail[:1000],
            )


def _required_experiment(
    repository: ExperimentRepository,
    experiment_id: int,
) -> ExperimentRecord:
    record = repository.get(experiment_id)
    if record is None:
        raise LookupError(f"experiment '{experiment_id}' is not present")
    return record
