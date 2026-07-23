"""Governed decision state machine, evidence binding and immutable packets.

This module is pure domain logic: it defines the legal decision lifecycle, the
content captured for each decision, and the deterministic digests that make an
approved decision tamper-evident. Persistence, optimistic locking and the
append-only event log live in the infrastructure and application layers.
"""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openenterprise_twin.domain.company import (
    DisplayName,
    DomainModel,
    Identifier,
    VersionString,
)
from openenterprise_twin.domain.errors import DomainValidationError

DecisionState = Literal[
    "draft",
    "evidence_ready",
    "under_review",
    "approved",
    "implemented",
    "monitoring",
    "successful",
    "underperformed",
    "superseded",
    "abandoned",
]

Line = Annotated[str, Field(min_length=1, max_length=280)]
Paragraph = Annotated[str, Field(min_length=1, max_length=4000)]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

DECISION_STATES: tuple[DecisionState, ...] = (
    "draft",
    "evidence_ready",
    "under_review",
    "approved",
    "implemented",
    "monitoring",
    "successful",
    "underperformed",
    "superseded",
    "abandoned",
)

#: Legal forward transitions. Anything not listed here is rejected.
ALLOWED_TRANSITIONS: dict[DecisionState, frozenset[DecisionState]] = {
    "draft": frozenset({"evidence_ready", "abandoned"}),
    "evidence_ready": frozenset({"under_review", "draft", "abandoned"}),
    "under_review": frozenset({"approved", "draft", "abandoned"}),
    "approved": frozenset({"implemented", "superseded", "abandoned"}),
    "implemented": frozenset({"monitoring", "superseded"}),
    "monitoring": frozenset({"successful", "underperformed", "superseded"}),
    "successful": frozenset({"superseded"}),
    "underperformed": frozenset({"superseded"}),
    "superseded": frozenset(),
    "abandoned": frozenset(),
}

#: States from which no further transition is possible.
TERMINAL_STATES: frozenset[DecisionState] = frozenset({"superseded", "abandoned"})

#: The single transition that requires an authenticated approval record.
APPROVAL_TRANSITION: tuple[DecisionState, DecisionState] = (
    "under_review",
    "approved",
)

#: States in which the decision rationale may still be revised.
_EDITABLE_STATES: frozenset[DecisionState] = frozenset({"draft", "evidence_ready"})


def is_terminal(state: DecisionState) -> bool:
    """Return whether a decision state admits no further transitions."""

    return state in TERMINAL_STATES


def can_transition(current: DecisionState, target: DecisionState) -> bool:
    """Return whether ``current -> target`` is a legal transition."""

    return target in ALLOWED_TRANSITIONS[current]


def validate_transition(current: DecisionState, target: DecisionState) -> None:
    """Raise ``DomainValidationError`` for an illegal decision transition."""

    if current == target:
        raise DomainValidationError(
            f"decision is already in state '{current}'"
        )
    if not can_transition(current, target):
        raise DomainValidationError(
            f"illegal decision transition '{current}' -> '{target}'"
        )


class DecisionEvidence(DomainModel):
    """Content-addressed references to the analysis backing a decision."""

    calibration_id: Identifier | None = None
    calibration_digest: Digest | None = None
    credibility_digest: Digest | None = None
    optimization_id: Identifier | None = None
    optimization_digest: Digest | None = None
    experiment_ids: tuple[Annotated[int, Field(gt=0)], ...] = ()
    comparison_digest: Digest | None = None
    brief_digest: Digest | None = None

    def digest(self) -> str:
        """A stable digest binding every evidence reference for this decision."""

        return sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class DecisionContent(DomainModel):
    """The decision rationale captured for governance and audit."""

    title: DisplayName
    owner: Identifier
    context: Paragraph
    objectives: Annotated[tuple[Line, ...], Field(min_length=1)]
    company_model_version: VersionString
    considered_scenario_ids: tuple[Identifier, ...] = ()
    recommendation: Line
    chosen_alternative: Line
    rejected_alternatives: tuple[Line, ...] = ()
    justification: Paragraph
    hard_constraints: tuple[Line, ...] = ()
    approval_conditions: tuple[Line, ...] = ()
    risks: tuple[Line, ...] = ()
    evidence: DecisionEvidence = Field(default_factory=DecisionEvidence)

    def content_digest(self) -> str:
        """A deterministic digest of the decision rationale and evidence."""

        return sha256(
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


class DecisionTransition(DomainModel):
    """One append-only lifecycle event for a decision."""

    from_state: DecisionState | None
    to_state: DecisionState
    actor: Identifier
    occurred_at: datetime
    note: Line | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.occurred_at.tzinfo is None:
            raise DomainValidationError("transition timestamps must be aware")
        if self.from_state is None:
            if self.to_state != "draft":
                raise DomainValidationError(
                    "the first decision event must create a draft"
                )
            return self
        if self.from_state == self.to_state:
            # A same-state event records a content revision or annotation; it is
            # only legal while the decision can still be edited.
            if self.from_state not in _EDITABLE_STATES:
                raise DomainValidationError(
                    "same-state events are only valid before review"
                )
            return self
        validate_transition(self.from_state, self.to_state)
        return self


class ApprovalRecord(DomainModel):
    """An authenticated approval bound to the exact evidence approved."""

    approver: Identifier
    decision: Literal["approve", "reject"]
    occurred_at: datetime
    approved_content_digest: Digest
    note: Line | None = None

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        if self.occurred_at.tzinfo is None:
            raise DomainValidationError("approval timestamps must be aware")
        return self


def ensure_separation_of_duties(
    *, owner: str, approver: str, required: bool
) -> None:
    """Reject self-approval when proposer/approver separation is required."""

    if required and owner == approver:
        raise DomainValidationError(
            "separation of duties requires a different approver than the owner"
        )


class DecisionPacket(DomainModel):
    """An immutable, content-addressed export of a governed decision."""

    decision_id: Identifier
    state: DecisionState
    version: Annotated[int, Field(ge=1)]
    content: DecisionContent
    content_digest: Digest
    evidence_digest: Digest
    transitions: Annotated[tuple[DecisionTransition, ...], Field(min_length=1)]
    approvals: tuple[ApprovalRecord, ...]
    exported_at: datetime
    packet_digest: Digest

    @model_validator(mode="after")
    def validate_packet(self) -> Self:
        if self.exported_at.tzinfo is None:
            raise DomainValidationError("packet export time must be aware")
        if self.content.content_digest() != self.content_digest:
            raise DomainValidationError("packet content_digest does not reconcile")
        if self.content.evidence.digest() != self.evidence_digest:
            raise DomainValidationError("packet evidence_digest does not reconcile")
        if self.packet_digest != _packet_digest(self):
            raise DomainValidationError("packet_digest does not reconcile")
        return self


def build_decision_packet(
    *,
    decision_id: str,
    state: DecisionState,
    version: int,
    content: DecisionContent,
    transitions: tuple[DecisionTransition, ...],
    approvals: tuple[ApprovalRecord, ...],
    exported_at: datetime,
) -> DecisionPacket:
    """Assemble a tamper-evident decision packet with reconciled digests."""

    draft = DecisionPacket.model_construct(
        decision_id=decision_id,
        state=state,
        version=version,
        content=content,
        content_digest=content.content_digest(),
        evidence_digest=content.evidence.digest(),
        transitions=transitions,
        approvals=approvals,
        exported_at=exported_at,
        packet_digest="0" * 64,
    )
    digest = _packet_digest(draft)
    return DecisionPacket(
        decision_id=decision_id,
        state=state,
        version=version,
        content=content,
        content_digest=content.content_digest(),
        evidence_digest=content.evidence.digest(),
        transitions=transitions,
        approvals=approvals,
        exported_at=exported_at,
        packet_digest=digest,
    )


def _packet_digest(packet: DecisionPacket) -> str:
    body = packet.model_dump(mode="json", exclude={"packet_digest"})
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
