"""Unit contracts for bounded operational HTTP metrics."""

from fastapi import APIRouter, FastAPI

from openenterprise_twin.api.observability import (
    OperationalMetrics,
    RegisteredRouteResolver,
)

_REGISTERED_ROUTE_TEMPLATES = (
    "/api/v1/scenarios",
    "/api/v1/scenarios/{scenario_id}",
    "/api/v1/system/info",
)


def _metrics() -> OperationalMetrics:
    return OperationalMetrics(
        registered_route_templates=_REGISTERED_ROUTE_TEMPLATES
    )


def test_http_metrics_aggregate_only_bounded_dimensions() -> None:
    metrics = _metrics()

    metrics.record_http(
        method="GET",
        route="/api/v1/scenarios/{scenario_id}",
        status_code=200,
        duration_seconds=0.125,
    )

    snapshot = metrics.snapshot()

    assert snapshot["http_requests"][0] == {
        "method": "GET",
        "route": "/api/v1/scenarios/{scenario_id}",
        "status_family": "2xx",
        "count": 1,
        "duration_seconds_total": 0.125,
    }


def test_http_metrics_accumulate_and_sort_buckets_deterministically() -> None:
    metrics = _metrics()
    metrics.record_http(
        method="POST",
        route="/api/v1/scenarios",
        status_code=201,
        duration_seconds=0.2,
    )
    metrics.record_http(
        method="GET",
        route="/api/v1/system/info",
        status_code=401,
        duration_seconds=0.4,
    )
    metrics.record_http(
        method="POST",
        route="/api/v1/scenarios",
        status_code=201,
        duration_seconds=0.3,
    )

    snapshot = metrics.snapshot()

    assert snapshot["http_requests"] == [
        {
            "method": "GET",
            "route": "/api/v1/system/info",
            "status_family": "4xx",
            "count": 1,
            "duration_seconds_total": 0.4,
        },
        {
            "method": "POST",
            "route": "/api/v1/scenarios",
            "status_family": "2xx",
            "count": 2,
            "duration_seconds_total": 0.5,
        },
    ]


def test_http_metrics_uses_monotonic_uptime() -> None:
    clock_values = iter((10.0, 12.5))
    metrics = OperationalMetrics(
        registered_route_templates=_REGISTERED_ROUTE_TEMPLATES,
        clock=lambda: next(clock_values),
    )

    snapshot = metrics.snapshot()

    assert snapshot["uptime_seconds"] == 2.5


def test_http_metrics_replace_arbitrary_routes_with_unmatched() -> None:
    metrics = _metrics()

    metrics.record_http(
        method="GET",
        route="/api/v1/scenarios/raw-secret-id",
        status_code=404,
        duration_seconds=0.1,
    )

    assert metrics.snapshot()["http_requests"] == [
        {
            "method": "GET",
            "route": "unmatched",
            "status_family": "4xx",
            "count": 1,
            "duration_seconds_total": 0.1,
        }
    ]


def test_http_metrics_normalize_unsupported_methods_to_other() -> None:
    metrics = _metrics()

    metrics.record_http(
        method="ATTACKER-CONTROLLED",
        route="/api/v1/scenarios",
        status_code=405,
        duration_seconds=0.1,
    )

    assert metrics.snapshot()["http_requests"][0]["method"] == "OTHER"


def test_http_metrics_normalize_lowercase_supported_methods() -> None:
    metrics = _metrics()

    metrics.record_http(
        method="get",
        route="/api/v1/scenarios",
        status_code=200,
        duration_seconds=0.1,
    )

    assert metrics.snapshot()["http_requests"][0]["method"] == "GET"


def test_http_metrics_ignore_out_of_range_statuses() -> None:
    metrics = _metrics()

    for status_code in (0, 99, 600, 999):
        metrics.record_http(
            method="GET",
            route="/api/v1/scenarios",
            status_code=status_code,
            duration_seconds=0.1,
        )

    assert metrics.snapshot()["http_requests"] == []


def test_registered_route_resolver_returns_only_known_templates() -> None:
    router = APIRouter(prefix="/api/v1")

    @router.post("/scenarios/{scenario_id}")
    def create_scenario(scenario_id: str) -> dict[str, str]:
        return {"scenario_id": scenario_id}

    app = FastAPI(openapi_url=None)
    app.include_router(router)

    resolver = RegisteredRouteResolver.from_routes(app.routes)

    assert resolver.registered_route_templates == frozenset(
        {"/api/v1/scenarios/{scenario_id}"}
    )
    assert (
        resolver.resolve(
            method="POST",
            path="/api/v1/scenarios/sensitive-id",
        )
        == "/api/v1/scenarios/{scenario_id}"
    )
    assert (
        resolver.resolve(
            method="POST",
            path="/api/v1/unregistered/sensitive-id",
        )
        == "unmatched"
    )
