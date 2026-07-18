"""Application contracts for durable experiment execution."""

from typing import Protocol


class ExperimentQueueFullError(RuntimeError):
    """Raised when both execution and bounded queue capacity are exhausted."""


class ExperimentRunner(Protocol):
    def submit(self, experiment_id: int) -> None: ...

    def recover_pending(self) -> None: ...

    def shutdown(self, timeout_seconds: float) -> None: ...
