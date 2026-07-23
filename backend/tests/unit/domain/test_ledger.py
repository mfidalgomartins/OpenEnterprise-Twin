from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.ledger import (
    ALLOWED_TRANSITIONS,
    DECISION_STATES,
    DecisionContent,
    DecisionEvidence,
    DecisionTransition,
    build_decision_packet,
    can_transition,
    ensure_separation_of_duties,
    is_terminal,
    validate_transition,
)


def _content() -> DecisionContent:
    return DecisionContent(
        title="Raise contracted pricing 3%",
        owner="cfo",
        context="Margin recovery under stable demand.",
        objectives=("grow ebitda", "hold otif >= 0.95"),
        company_model_version="0.2.0",
        recommendation="Adopt the +3% contracted price policy.",
        chosen_alternative="price-plus-3",
        rejected_alternatives=("status-quo",),
        justification="Paired experiment shows a material EBITDA gain.",
        hard_constraints=("otif >= 0.95",),
        evidence=DecisionEvidence(experiment_ids=(1, 2)),
    )


def test_every_state_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(DECISION_STATES)


def test_terminal_states_have_no_transitions() -> None:
    for state in DECISION_STATES:
        if is_terminal(state):
            assert ALLOWED_TRANSITIONS[state] == frozenset()


def test_valid_forward_transition() -> None:
    validate_transition("draft", "evidence_ready")
    assert can_transition("under_review", "approved")


def test_illegal_transition_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        validate_transition("draft", "approved")


def test_same_state_transition_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        validate_transition("approved", "approved")


def test_first_event_must_create_a_draft() -> None:
    with pytest.raises(ValidationError):
        DecisionTransition(
            from_state=None,
            to_state="approved",
            actor="cfo",
            occurred_at=datetime.now(UTC),
        )


def test_same_state_event_allowed_only_before_review() -> None:
    revision = DecisionTransition(
        from_state="draft",
        to_state="draft",
        actor="cfo",
        occurred_at=datetime.now(UTC),
        note="content revised",
    )
    assert revision.to_state == "draft"
    with pytest.raises(ValidationError):
        DecisionTransition(
            from_state="approved",
            to_state="approved",
            actor="cfo",
            occurred_at=datetime.now(UTC),
        )


def test_separation_of_duties() -> None:
    ensure_separation_of_duties(owner="cfo", approver="ceo", required=True)
    with pytest.raises(DomainValidationError):
        ensure_separation_of_duties(owner="cfo", approver="cfo", required=True)
    # Allowed when separation is not required.
    ensure_separation_of_duties(owner="cfo", approver="cfo", required=False)


def test_content_and_evidence_digests_are_stable() -> None:
    content = _content()
    assert content.content_digest() == _content().content_digest()
    assert content.evidence.digest() == _content().evidence.digest()


def test_packet_reconciles_and_is_tamper_evident() -> None:
    content = _content()
    transition = DecisionTransition(
        from_state=None,
        to_state="draft",
        actor="cfo",
        occurred_at=datetime.now(UTC),
    )
    packet = build_decision_packet(
        decision_id="dec-1",
        state="draft",
        version=1,
        content=content,
        transitions=(transition,),
        approvals=(),
        exported_at=datetime.now(UTC),
    )
    assert packet.content_digest == content.content_digest()
    assert packet.evidence_digest == content.evidence.digest()
    from openenterprise_twin.domain.ledger import DecisionPacket

    tampered = packet.model_dump(mode="json")
    tampered["content_digest"] = "0" * 64
    with pytest.raises(ValidationError):
        DecisionPacket.model_validate(tampered)
