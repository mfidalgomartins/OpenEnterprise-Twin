"""Bounded in-process operational metrics."""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from re import Pattern
from threading import Lock
from time import monotonic
from typing import Self, TypedDict

from fastapi.routing import RouteContext, iter_route_contexts
from starlette.routing import BaseRoute

_SUPPORTED_HTTP_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)


class HttpRequestSnapshot(TypedDict):
    method: str
    route: str
    status_family: str
    count: int
    duration_seconds_total: float


class OperationalMetricsSnapshotData(TypedDict):
    uptime_seconds: float
    http_requests: list[HttpRequestSnapshot]


@dataclass(frozen=True, slots=True)
class _RegisteredRoute:
    template: str
    methods: frozenset[str]
    path_regex: Pattern[str]


@dataclass(frozen=True, slots=True)
class RegisteredRouteResolver:
    """Resolve raw request paths to an immutable set of known templates."""

    _routes: tuple[_RegisteredRoute, ...]
    registered_route_templates: frozenset[str]

    @classmethod
    def from_routes(
        cls,
        routes: Sequence[BaseRoute | RouteContext],
    ) -> Self:
        registered_routes: list[_RegisteredRoute] = []
        for route in iter_route_contexts(routes):
            path = route.path
            methods = route.methods
            path_regex = getattr(route, "path_regex", None)
            if (
                path is None
                or methods is None
                or not isinstance(path_regex, Pattern)
            ):
                continue
            registered_routes.append(
                _RegisteredRoute(
                    template=path,
                    methods=frozenset(methods),
                    path_regex=path_regex,
                )
            )
        immutable_routes = tuple(registered_routes)
        return cls(
            _routes=immutable_routes,
            registered_route_templates=frozenset(
                route.template for route in immutable_routes
            ),
        )

    def resolve(self, *, method: str, path: str) -> str:
        """Return a registered template, never the supplied raw path."""

        normalized_method = method.upper()
        path_match: str | None = None
        for route in self._routes:
            if route.path_regex.match(path) is None:
                continue
            if path_match is None:
                path_match = route.template
            if normalized_method in route.methods:
                return route.template
        return path_match or "unmatched"


class OperationalMetrics:
    """Collect aggregate HTTP metrics with bounded labels."""

    def __init__(
        self,
        *,
        registered_route_templates: Iterable[str],
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._clock = clock
        self._started_at = clock()
        self._registered_route_templates = frozenset(
            registered_route_templates
        )
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

        if not 100 <= status_code <= 599:
            return
        normalized_method = method.upper()
        if normalized_method not in _SUPPORTED_HTTP_METHODS:
            normalized_method = "OTHER"
        normalized_route = (
            route
            if route in self._registered_route_templates
            else "unmatched"
        )
        status_family = f"{status_code // 100}xx"
        key = (normalized_method, normalized_route, status_family)
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
