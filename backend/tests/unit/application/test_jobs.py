"""Application-level contracts for durable analytical jobs."""

from datetime import UTC, datetime

import pytest

from openenterprise_twin.application.jobs import (
    JobKind,
    JobProblem,
    SubmitJob,
    canonical_request_digest,
)


@pytest.mark.parametrize(
    "kind",
    (
        "experiment",
        "calibration",
        "optimization",
        "adaptive_comparison",
    ),
)
def test_submit_job_accepts_every_supported_kind(kind: JobKind) -> None:
    command = SubmitJob(
        kind=kind,
        created_by="oidc:user@example.com",
        request_payload={"z": 1, "nested": {"b": 2, "a": 1}},
        idempotency_key="quarterly-plan",
        max_attempts=4,
    )

    assert command.kind == kind
    assert command.max_attempts == 4


def test_canonical_request_digest_is_order_independent_and_kind_bound() -> None:
    first = canonical_request_digest(
        "experiment",
        {"scenario_id": "growth", "config": {"seed": 7, "runs": 100}},
    )
    reordered = canonical_request_digest(
        "experiment",
        {"config": {"runs": 100, "seed": 7}, "scenario_id": "growth"},
    )
    other_kind = canonical_request_digest(
        "optimization",
        {"scenario_id": "growth", "config": {"seed": 7, "runs": 100}},
    )

    assert first == reordered
    assert first != other_kind
    assert len(first) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("created_by", ""),
        ("created_by", "contains spaces"),
        ("idempotency_key", ""),
        ("idempotency_key", "x" * 201),
        ("max_attempts", 0),
        ("max_attempts", 11),
    ),
)
def test_submit_job_rejects_unbounded_or_unsafe_values(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "kind": "experiment",
        "created_by": "analyst",
        "request_payload": {"scenario_id": "base"},
        "idempotency_key": "key-1",
        "max_attempts": 3,
    }
    values[field] = value

    with pytest.raises(ValueError):
        SubmitJob(**values)  # type: ignore[arg-type]


def test_submit_job_snapshots_json_payload() -> None:
    payload = {"scenario_id": "base", "values": [1, 2]}
    command = SubmitJob(
        kind="experiment",
        created_by="analyst",
        request_payload=payload,
    )

    payload["scenario_id"] = "tampered"
    payload["values"].append(3)

    assert command.request_payload == {
        "scenario_id": "base",
        "values": [1, 2],
    }


def test_problem_is_safe_bounded_and_serializable() -> None:
    problem = JobProblem(
        code="upstream_timeout",
        detail="The upstream solver did not respond.",
        occurred_at=datetime(2026, 7, 26, tzinfo=UTC),
    )

    assert problem.as_json() == {
        "code": "upstream_timeout",
        "detail": "The upstream solver did not respond.",
        "occurred_at": "2026-07-26T00:00:00Z",
    }

    with pytest.raises(ValueError):
        JobProblem(code="unsafe code", detail="No")
    with pytest.raises(ValueError):
        JobProblem(code="failure", detail="Traceback (most recent call last)")
    with pytest.raises(ValueError):
        JobProblem(code="failure", detail="token=super-secret")

