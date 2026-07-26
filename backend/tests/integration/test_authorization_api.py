"""Runtime role-matrix and safe authenticated-session contracts."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openenterprise_twin.api.app import create_app
from openenterprise_twin.application.identity import Role
from openenterprise_twin.infrastructure.settings import Settings
from openenterprise_twin.simulation.reference import build_baseline_scenario


@contextmanager
def _client(
    tmp_path: Path,
    *,
    roles: tuple[Role, ...],
) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / ('-'.join(roles) + '.db')}",
        artifact_directory=tmp_path / ("-".join(roles) + "-artifacts"),
        deployment_environment="test",
        authentication_mode="local",
        local_subject="user-1",
        local_tenant_id="northstar",
        local_roles=roles,
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize(
    "roles",
    (
        ("viewer",),
        ("analyst",),
        ("approver",),
        ("admin",),
    ),
)
def test_every_supported_role_can_read_tenant_resources(
    tmp_path: Path,
    roles: tuple[Role, ...],
) -> None:
    with _client(tmp_path, roles=roles) as client:
        response = client.get("/api/v1/company")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("roles", "expected_status"),
    (
        (("viewer",), 403),
        (("approver",), 403),
        (("analyst",), 201),
        (("admin",), 201),
    ),
)
def test_scenario_creation_enforces_analyst_role(
    tmp_path: Path,
    roles: tuple[Role, ...],
    expected_status: int,
) -> None:
    scenario = build_baseline_scenario(horizon_days=2).model_copy(
        update={"scenario_id": f"scenario-{roles[0]}"}
    )
    with _client(tmp_path, roles=roles) as client:
        response = client.post(
            "/api/v1/scenarios",
            json=scenario.model_dump(mode="json"),
        )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json()["code"] == "authorization_denied"


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/scenarios/baseline-northstar/experiments",
        "/api/v1/datasets",
        "/api/v1/datasets/synthetic",
        "/api/v1/datasets/csv?dataset_id=history&company_id=northstar",
        "/api/v1/calibrations",
        "/api/v1/optimizations",
        "/api/v1/adaptive-policies/validate",
        "/api/v1/adaptive-policies/compare",
        "/api/v1/ledger/decisions",
        "/api/v1/ledger/decisions/missing/outcomes",
    ),
)
def test_viewer_is_denied_on_every_static_mutation_route(
    tmp_path: Path,
    path: str,
) -> None:
    with _client(tmp_path, roles=("viewer",)) as client:
        response = client.post(path, json={})

    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/session",
        "/api/v1/company",
        "/api/v1/baseline",
        "/api/v1/scenarios",
        "/api/v1/scenarios/missing",
        "/api/v1/experiments/1",
        "/api/v1/experiments/1/comparison",
        "/api/v1/experiments/1/report",
        "/api/v1/decisions",
        "/api/v1/frontier",
        "/api/v1/datasets/missing/export.csv",
        "/api/v1/ledger/decisions",
        "/api/v1/ledger/decisions/missing",
        "/api/v1/ledger/decisions/missing/packet",
        "/api/v1/ledger/decisions/missing/monitoring",
    ),
)
def test_viewer_reaches_every_tenant_read_route(
    tmp_path: Path,
    path: str,
) -> None:
    with _client(tmp_path, roles=("viewer",)) as client:
        response = client.get(path)

    assert response.status_code != 401
    assert response.status_code != 403


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/system/info",
        "/api/v1/system/metrics",
    ),
)
def test_operational_endpoints_are_admin_only(
    tmp_path: Path,
    path: str,
) -> None:
    with _client(tmp_path, roles=("viewer",)) as viewer:
        denied = viewer.get(path)
    with _client(tmp_path, roles=("admin",)) as admin:
        allowed = admin.get(path)

    assert denied.status_code == 403
    assert denied.json()["code"] == "authorization_denied"
    assert allowed.status_code == 200


def test_session_endpoint_exposes_only_safe_effective_identity(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, roles=("analyst", "viewer")) as client:
        response = client.get("/api/v1/session")

    assert response.status_code == 200
    assert response.json() == {
        "subject": "user-1",
        "tenant_id": "northstar",
        "roles": ["analyst", "viewer"],
        "authentication_method": "local",
    }
    assert "token" not in response.text
    assert "api_key" not in response.text


def test_api_key_session_requires_transport_credentials(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'service.db'}",
        artifact_directory=tmp_path / "service-artifacts",
        deployment_environment="test",
        authentication_mode="api_key",
        api_key=SecretStr("service-secret"),
        service_account_subject="planning-worker",
        service_account_tenant_id="northstar",
        service_account_roles=("analyst",),
        experiment_workers=1,
        database_pool_size=2,
        database_max_overflow=0,
    )

    with TestClient(create_app(settings)) as client:
        denied = client.get("/api/v1/session")
        allowed = client.get(
            "/api/v1/session",
            headers={"X-API-Key": "service-secret"},
        )

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["subject"] == "planning-worker"
    assert allowed.json()["authentication_method"] == "api_key"


def test_openapi_declares_api_key_or_bearer_security(tmp_path: Path) -> None:
    with _client(tmp_path, roles=("admin",)) as client:
        schema = client.get("/openapi.json").json()

    assert schema["components"]["securitySchemes"] == {
        "APIKeyHeader": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
        },
        "HTTPBearer": {"type": "http", "scheme": "bearer"},
    }
    for path in (
        "/api/v1/session",
        "/api/v1/scenarios",
        "/api/v1/system/info",
    ):
        for operation in schema["paths"][path].values():
            assert operation["security"] == [
                {"APIKeyHeader": []},
                {"HTTPBearer": []},
            ]


@pytest.mark.parametrize(
    ("roles", "target", "expected_status"),
    (
        (("viewer",), "evidence_ready", 403),
        (("approver",), "evidence_ready", 403),
        (("analyst",), "evidence_ready", 404),
        (("admin",), "evidence_ready", 404),
        (("analyst",), "approved", 403),
        (("approver",), "approved", 404),
        (("admin",), "approved", 404),
    ),
)
def test_decision_transition_authorization_depends_on_target(
    tmp_path: Path,
    roles: tuple[Role, ...],
    target: str,
    expected_status: int,
) -> None:
    with _client(tmp_path, roles=roles) as client:
        response = client.post(
            "/api/v1/ledger/decisions/missing/transitions",
            json={
                "expected_version": 1,
                "target": target,
                "actor": "forged-body-actor",
            },
        )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json()["code"] == "authorization_denied"
