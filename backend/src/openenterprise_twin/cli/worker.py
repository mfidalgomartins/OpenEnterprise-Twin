"""Standalone durable analytical worker process."""

from __future__ import annotations

import logging
import signal
from datetime import timedelta
from threading import Event
from types import FrameType

from openenterprise_twin.infrastructure.artifacts import FileArtifactStore
from openenterprise_twin.infrastructure.database import (
    create_database_engine,
    create_session_factory,
)
from openenterprise_twin.infrastructure.runner import (
    DurableJobWorker,
    EmbeddedJobWorkerPool,
    build_analytical_job_handlers,
    build_worker_id,
)
from openenterprise_twin.infrastructure.settings import Settings

logger = logging.getLogger(__name__)


def main() -> None:
    """Run bounded worker threads until SIGINT or SIGTERM."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    artifact_store = FileArtifactStore(settings.artifact_directory)
    handlers = build_analytical_job_handlers(
        session_factory=session_factory,
        artifact_store=artifact_store,
        max_replication_workers=settings.replication_workers_per_experiment,
        max_dataset_observations=settings.max_dataset_observations,
        max_optimization_evaluations=settings.max_optimization_evaluations,
        max_optimization_periods=settings.max_optimization_periods,
        max_adaptive_periods=settings.max_adaptive_periods,
    )
    process_id = build_worker_id("worker")
    pool = EmbeddedJobWorkerPool(
        workers=tuple(
            DurableJobWorker(
                session_factory=session_factory,
                handlers=handlers,
                worker_id=f"{process_id}-{index}",
                lease_duration=timedelta(seconds=settings.job_lease_seconds),
                heartbeat_interval=timedelta(
                    seconds=settings.job_heartbeat_seconds
                ),
                retry_delay=timedelta(
                    seconds=settings.job_retry_delay_seconds
                ),
            )
            for index in range(settings.job_workers)
        ),
        poll_interval=timedelta(seconds=settings.job_poll_interval_seconds),
    )
    stop = Event()

    def request_stop(
        signum: int,
        _frame: FrameType | None,
    ) -> None:
        logger.info("worker shutdown requested", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        pool.start()
        logger.info("durable analytical worker started")
        stop.wait()
    finally:
        pool.shutdown(settings.job_shutdown_timeout_seconds)
        engine.dispose()
        logger.info("durable analytical worker stopped")


if __name__ == "__main__":
    main()
