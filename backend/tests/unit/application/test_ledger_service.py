"""Decision-ledger service and persistence behaviour on SQLite."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.application.ledger import (
    DecisionLedgerService,
    LedgerConflictError,
    LedgerNotFoundError,
)
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.ledger import (
    ApprovalRecord,
    DecisionContent,
    DecisionEvidence,
)
from openenterprise_twin.infrastructure.models import Base
from openenterprise_twin.infrastructure.repositories import (
    SqlDecisionLedgerRepository,
)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def service(
    session_factory: sessionmaker[Session],
) -> DecisionLedgerService:
    return DecisionLedgerService(SqlDecisionLedgerRepository(session_factory))


def _content(owner: str = "cfo") -> DecisionContent:
    return DecisionContent(
        title="Raise contracted pricing 3%",
        owner=owner,
        context="Margin recovery under stable demand.",
        objectives=("grow ebitda",),
        company_model_version="0.2.0",
        recommendation="Adopt the +3% contracted price policy.",
        chosen_alternative="price-plus-3",
        justification="Paired experiment shows a material EBITDA gain.",
        evidence=DecisionEvidence(experiment_ids=(1, 2)),
    )


def _now() -> datetime:
    return datetime(2026, 7, 23, 12, tzinfo=UTC)


def test_full_lifecycle_to_monitoring(service: DecisionLedgerService) -> None:
    content = _content()
    now = _now()
    snap = service.create_decision(
        decision_id="dec-1", content=content, actor="cfo", occurred_at=now
    )
    assert snap.state == "draft"
    assert snap.version == 1

    for target, version in (
        ("evidence_ready", 1),
        ("under_review", 2),
    ):
        snap = service.transition(
            decision_id="dec-1",
            expected_version=version,
            target=target,  # type: ignore[arg-type]
            actor="cfo",
            occurred_at=now,
        )
    approval = ApprovalRecord(
        approver="ceo",
        decision="approve",
        occurred_at=now,
        approved_content_digest=content.content_digest(),
    )
    snap = service.transition(
        decision_id="dec-1",
        expected_version=3,
        target="approved",
        actor="ceo",
        occurred_at=now,
        approval=approval,
    )
    assert snap.state == "approved"
    assert len(snap.approvals) == 1

    snap = service.transition(
        decision_id="dec-1",
        expected_version=4,
        target="implemented",
        actor="coo",
        occurred_at=now,
    )
    snap = service.transition(
        decision_id="dec-1",
        expected_version=5,
        target="monitoring",
        actor="coo",
        occurred_at=now,
    )
    assert snap.state == "monitoring"
    # The audit trail retains every transition in order.
    assert [t.to_state for t in snap.transitions] == [
        "draft",
        "evidence_ready",
        "under_review",
        "approved",
        "implemented",
        "monitoring",
    ]


def test_optimistic_locking_rejects_stale_version(
    service: DecisionLedgerService,
) -> None:
    service.create_decision(
        decision_id="dec-1", content=_content(), actor="cfo", occurred_at=_now()
    )
    with pytest.raises(LedgerConflictError):
        service.transition(
            decision_id="dec-1",
            expected_version=99,
            target="evidence_ready",
            actor="cfo",
            occurred_at=_now(),
        )


def test_illegal_transition_is_rejected(service: DecisionLedgerService) -> None:
    service.create_decision(
        decision_id="dec-1", content=_content(), actor="cfo", occurred_at=_now()
    )
    with pytest.raises(DomainValidationError):
        service.transition(
            decision_id="dec-1",
            expected_version=1,
            target="approved",
            actor="cfo",
            occurred_at=_now(),
        )


def test_self_approval_is_rejected(service: DecisionLedgerService) -> None:
    content = _content(owner="cfo")
    now = _now()
    service.create_decision(
        decision_id="dec-1", content=content, actor="cfo", occurred_at=now
    )
    service.transition(
        decision_id="dec-1",
        expected_version=1,
        target="evidence_ready",
        actor="cfo",
        occurred_at=now,
    )
    service.transition(
        decision_id="dec-1",
        expected_version=2,
        target="under_review",
        actor="cfo",
        occurred_at=now,
    )
    self_approval = ApprovalRecord(
        approver="cfo",
        decision="approve",
        occurred_at=now,
        approved_content_digest=content.content_digest(),
    )
    with pytest.raises(DomainValidationError):
        service.transition(
            decision_id="dec-1",
            expected_version=3,
            target="approved",
            actor="cfo",
            occurred_at=now,
            approval=self_approval,
        )


def test_approval_must_match_current_evidence(
    service: DecisionLedgerService,
) -> None:
    content = _content()
    now = _now()
    service.create_decision(
        decision_id="dec-1", content=content, actor="cfo", occurred_at=now
    )
    service.transition(
        decision_id="dec-1",
        expected_version=1,
        target="evidence_ready",
        actor="cfo",
        occurred_at=now,
    )
    service.transition(
        decision_id="dec-1",
        expected_version=2,
        target="under_review",
        actor="cfo",
        occurred_at=now,
    )
    stale_approval = ApprovalRecord(
        approver="ceo",
        decision="approve",
        occurred_at=now,
        approved_content_digest="0" * 64,
    )
    with pytest.raises(DomainValidationError):
        service.transition(
            decision_id="dec-1",
            expected_version=3,
            target="approved",
            actor="ceo",
            occurred_at=now,
            approval=stale_approval,
        )


def test_content_frozen_after_review(service: DecisionLedgerService) -> None:
    now = _now()
    service.create_decision(
        decision_id="dec-1", content=_content(), actor="cfo", occurred_at=now
    )
    service.transition(
        decision_id="dec-1",
        expected_version=1,
        target="evidence_ready",
        actor="cfo",
        occurred_at=now,
    )
    service.transition(
        decision_id="dec-1",
        expected_version=2,
        target="under_review",
        actor="cfo",
        occurred_at=now,
    )
    with pytest.raises(DomainValidationError):
        service.update_content(
            decision_id="dec-1",
            expected_version=3,
            content=_content().model_copy(update={"title": "Changed after review"}),
            actor="cfo",
            occurred_at=now,
        )


def test_missing_decision_raises(service: DecisionLedgerService) -> None:
    with pytest.raises(LedgerNotFoundError):
        service.transition(
            decision_id="nope",
            expected_version=1,
            target="evidence_ready",
            actor="cfo",
            occurred_at=_now(),
        )


def test_export_packet_is_reproducible(service: DecisionLedgerService) -> None:
    now = _now()
    service.create_decision(
        decision_id="dec-1", content=_content(), actor="cfo", occurred_at=now
    )
    exported_at = now + timedelta(hours=1)
    first = service.export_packet(decision_id="dec-1", exported_at=exported_at)
    second = service.export_packet(decision_id="dec-1", exported_at=exported_at)
    assert first.packet_digest == second.packet_digest


def test_duplicate_creation_conflicts(service: DecisionLedgerService) -> None:
    service.create_decision(
        decision_id="dec-1", content=_content(), actor="cfo", occurred_at=_now()
    )
    with pytest.raises(LedgerConflictError):
        service.create_decision(
            decision_id="dec-1",
            content=_content(),
            actor="cfo",
            occurred_at=_now(),
        )
