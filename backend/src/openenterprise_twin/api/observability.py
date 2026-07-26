"""Bounded in-process operational metrics."""

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import TypedDict


class HttpRequestSnapshot(TypedDict):
    method: str
    route: str
    status_family: str
    count: int
    duration_seconds_total: float


class OperationalMetricsSnapshotData(TypedDict):
    uptime_seconds: float
    http_requests: list[HttpRequestSnapshot]


class OperationalMetrics:
    """Collect aggregate HTTP metrics with bounded caller-supplied labels."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._lock = Lock()
        self._http_requests: dict[tuple[str, str, str], tuple[int, float]] = {}

    def record_http(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """Record one completed HTTP response."""

        status_family = f"{status_code // 100}xx"
        key = (method, route, status_family)
        with self._lock:
            count, duration_total = self._http_requests.get(key, (0, 0.0))
            self._http_requests[key] = (
                count + 1,
                duration_total + duration_seconds,
            )

    def snapshot(self) -> OperationalMetricsSnapshotData:
        """Return a deterministic copy of metrics collected so far."""

        with self._lock:
            http_requests = [
                HttpRequestSnapshot(
                    method=method,
                    route=route,
                    status_family=status_family,
                    count=count,
                    duration_seconds_total=duration_total,
                )
                for (method, route, status_family), (
                    count,
                    duration_total,
                ) in sorted(self._http_requests.items())
            ]
            uptime_seconds = max(0.0, self._clock() - self._started_at)
        return OperationalMetricsSnapshotData(
            uptime_seconds=uptime_seconds,
            http_requests=http_requests,
        )
