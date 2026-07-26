"""System liveness, readiness and safe build metadata contracts."""

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal, NoReturn, cast

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin import __version__
from openenterprise_twin.api import system as system_module
from openenterprise_twin.api.app import create_app
from openenterprise_twin.api.dependencies import get_services
from openenterprise_twin.api.errors import ApiProblemError
from openenterprise_twin.infrastructure.settings import Settings

_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "permissions-policy": (
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    ),
    "cache-control": "no-store",
}


def _settings(
    tmp_path: Path,
    *,
    deployment_environment: Literal["development", "test", "production"] = (
        "development"
    ),
    api_key: SecretStr | None = None,
    trusted_hosts: tuple[str, ...] = (
        "localhost",
        "127.0.0.1",
        "testserver",
    ),
    build_commit: str | None = None,
) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'system.db'}",
        artifact_directory=tmp_path / "artifacts",
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
        deployment_environment=deployment_environment,
        authentication_mode="api_key" if api_key is not None else "local",
        api_key=api_key,
        trusted_hosts=trusted_hosts,
        build_commit=build_commit,
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


@pytest.fixture
def production_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = _settings(
        tmp_path,
        deployment_environment="production",
        api_key=SecretStr("x" * 32),
        trusted_hosts=("testserver",),
        build_commit="a1b2c3d4",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_artifact_probe_fsync_failure_removes_probe_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()

    def fail_fsync(_file_descriptor: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(ApiProblemError) as raised:
        system_module._check_artifact_directory(artifact_directory)

    assert raised.value.status == 503
    assert tuple(artifact_directory.iterdir()) == ()


def test_artifact_probe_mismatched_read_returns_safe_problem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "artifact-read-secret-" + "x" * 32
    settings = _settings(tmp_path, api_key=SecretStr(api_key))
    app = create_app(settings)
    original_fsync = os.fsync

    def corrupt_probe(file_descriptor: int) -> None:
        os.lseek(file_descriptor, 0, os.SEEK_SET)
        os.write(file_descriptor, b"0")
        original_fsync(file_descriptor)

    with TestClient(app) as test_client:
        monkeypatch.setattr(os, "fsync", corrupt_probe)
        response = test_client.get("/ready")

    _assert_safe_not_ready(response, settings)
    assert tuple(settings.artifact_directory.glob(".readiness-*.tmp")) == ()


def test_artifact_probe_cleanup_oserror_maps_to_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_directory = tmp_path / "artifacts"
    artifact_directory.mkdir()
    original_unlink = Path.unlink

    def unlink_then_fail(path: Path, *, missing_ok: bool = False) -> None:
        original_unlink(path, missing_ok=missing_ok)
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(Path, "unlink", unlink_then_fail)

    with pytest.raises(ApiProblemError) as raised:
        system_module._check_artifact_directory(artifact_directory)

    assert raised.value.status == 503
    assert tuple(artifact_directory.iterdir()) == ()


def test_artifact_probe_failure_returns_safe_problem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key = "artifact-probe-secret-" + "x" * 32
    settings = _settings(tmp_path, api_key=SecretStr(api_key))
    app = create_app(settings)

    def fail_probe_open(*_args: object, **_kwargs: object) -> NoReturn:
        raise OSError("simulated artifact probe failure")

    with TestClient(app) as test_client:
        monkeypatch.setattr(Path, "open", fail_probe_open)
        response = test_client.get("/ready")

    _assert_safe_not_ready(response, settings)
    assert tuple(settings.artifact_directory.glob(".readiness-*.tmp")) == ()


def test_database_probe_failure_returns_safe_problem(tmp_path: Path) -> None:
    api_key = "database-probe-secret-" + "x" * 32
    settings = _settings(tmp_path, api_key=SecretStr(api_key))
    app = create_app(settings)

    def fail_session_factory() -> NoReturn:
        raise SQLAlchemyError("simulated database probe failure")

    services = replace(
        app.state.services,
        session_factory=cast(sessionmaker[Session], fail_session_factory),
    )
    app.dependency_overrides[get_services] = lambda: services

    with TestClient(app) as test_client:
        response = test_client.get("/ready")

    _assert_safe_not_ready(response, settings)


def test_liveness_and_readiness_have_distinct_contracts(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {"artifacts": "ready", "database": "ready"},
    }


def test_system_info_is_protected_and_safe(
    production_client: TestClient,
) -> None:
    assert production_client.get("/api/v1/system/info").status_code == 401
    response = production_client.get(
        "/api/v1/system/info", headers={"X-API-Key": "x" * 32}
    )
    assert response.status_code == 200
    assert response.json() == {
        "name": "OpenEnterprise Twin",
        "version": __version__,
        "environment": "production",
        "build_commit": "a1b2c3d4",
        "capabilities": [
            "adaptive_policies",
            "calibration",
            "decision_ledger",
            "monitoring",
            "optimization",
            "paired_simulation",
            "secure_csv",
        ],
    }
    assert "database_url" not in response.text
    assert "api_key" not in response.text


def test_metrics_are_protected_sorted_and_exclude_request_identifiers(
    production_client: TestClient,
) -> None:
    api_key = {"X-API-Key": "x" * 32}

    assert production_client.get("/api/v1/system/metrics").status_code == 401
    assert production_client.get("/unmatched-attacker-path").status_code == 404
    assert production_client.get(
        "/api/v1/scenarios/scenario-secret-123",
        headers=api_key,
    ).status_code == 404

    response = production_client.get("/api/v1/system/metrics", headers=api_key)

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"uptime_seconds", "http_requests"}
    assert isinstance(payload["uptime_seconds"], float)
    assert payload["uptime_seconds"] >= 0
    assert payload["http_requests"] == sorted(
        payload["http_requests"],
        key=lambda item: (
            item["method"],
            item["route"],
            item["status_family"],
        ),
    )
    assert {
        "method": "GET",
        "route": "unmatched",
        "status_family": "4xx",
        "count": 1,
        "duration_seconds_total": pytest.approx(
            next(
                item["duration_seconds_total"]
                for item in payload["http_requests"]
                if item["route"] == "unmatched"
            )
        ),
    } in payload["http_requests"]
    assert {
        "method": "GET",
        "route": "/api/v1/scenarios/{scenario_id}",
        "status_family": "4xx",
        "count": 1,
        "duration_seconds_total": pytest.approx(
            next(
                item["duration_seconds_total"]
                for item in payload["http_requests"]
                if item["route"] == "/api/v1/scenarios/{scenario_id}"
            )
        ),
    } in payload["http_requests"]
    assert "scenario-secret-123" not in response.text
    assert "enterprise-operator" not in response.text
    assert "trace_id" not in response.text


def test_security_headers_cover_api_response_classes(tmp_path: Path) -> None:
    api_key = "x" * 32
    settings = _settings(tmp_path, api_key=SecretStr(api_key)).model_copy(
        update={"max_request_body_bytes": 8}
    )

    with TestClient(create_app(settings)) as test_client:
        responses = (
            test_client.get("/api/v1/system/info", headers={"X-API-Key": api_key}),
            test_client.get("/api/v1/system/info"),
            test_client.get("/unmatched-attacker-path"),
            test_client.post(
                "/api/v1/scenarios",
                content=b"x" * 9,
                headers={"Content-Type": "application/json"},
            ),
            test_client.get("/ready"),
        )

    assert [response.status_code for response in responses] == [
        200,
        401,
        404,
        413,
        200,
    ]
    for response in responses:
        assert {
            name: response.headers[name] for name in _SECURITY_HEADERS
        } == _SECURITY_HEADERS


def test_oversized_request_records_registered_route_template(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path).model_copy(
        update={"max_request_body_bytes": 8}
    )

    with TestClient(create_app(settings)) as test_client:
        oversized = test_client.post(
            "/api/v1/scenarios",
            content=b"x" * 9,
            headers={"Content-Type": "application/json"},
        )
        metrics_response = test_client.get("/api/v1/system/metrics")

    assert oversized.status_code == 413
    assert metrics_response.status_code == 200
    http_requests = metrics_response.json()["http_requests"]
    recorded = next(
        item
        for item in http_requests
        if item["method"] == "POST"
        and item["route"] == "/api/v1/scenarios"
        and item["status_family"] == "4xx"
    )
    assert recorded["count"] == 1
    assert recorded["duration_seconds_total"] >= 0
    assert not any(
        item["method"] == "POST" and item["route"] == "unmatched"
        for item in http_requests
    )


def _assert_safe_not_ready(response: Response, settings: Settings) -> None:
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"

    payload = response.json()
    trace_id = payload["trace_id"]

    assert isinstance(trace_id, str)
    assert trace_id
    assert response.headers["x-trace-id"] == trace_id
    assert payload == {
        "type": "about:blank",
        "title": "Service is not ready",
        "status": 503,
        "code": "service_not_ready",
        "detail": "One or more required dependencies are unavailable.",
        "trace_id": trace_id,
        "violations": [],
    }
    assert settings.database_url not in response.text
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() not in response.text
