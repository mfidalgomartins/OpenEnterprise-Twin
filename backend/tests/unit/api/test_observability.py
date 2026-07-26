"""Unit contracts for bounded operational HTTP metrics."""

from openenterprise_twin.api.observability import OperationalMetrics


def test_http_metrics_aggregate_only_bounded_dimensions() -> None:
    metrics = OperationalMetrics()

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
    metrics = OperationalMetrics()
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
    metrics = OperationalMetrics(clock=lambda: next(clock_values))

    snapshot = metrics.snapshot()

    assert snapshot["uptime_seconds"] == 2.5
