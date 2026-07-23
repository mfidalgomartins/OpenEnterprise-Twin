"""Application service governing the decision ledger lifecycle.

The service owns the transactional rules that the pure state machine cannot:
optimistic concurrency control, append-only auditing, separation of duties and
the immutability of evidence once a decision leaves drafting. Persistence is
reached only through the :class:`DecisionLedgerRepository` port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.ledger import (
    APPROVAL_TRANSITION,
    EDITABLE_STATES,
    ApprovalRecord,
    DecisionContent,
    DecisionPacket,
    DecisionState,
    DecisionTransition,
    build_decision_packet,
    ensure_separation_of_duties,
    validate_transition,
)


class LedgerError(Exception):
    """Base class for decision-ledger application failures."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class LedgerNotFoundError(LedgerError):
    def __init__(self, decision_id: str) -> None:
        super().__init__(
            "decision_not_found", f"decision '{decision_id}' does not exist"
        )


class LedgerConflictError(LedgerError):
    def __init__(self, detail: str) -> None:
        super().__init__("decision_version_conflict", detail)


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """The current persisted state of one decision plus its full audit trail."""

    decision_id: str
    state: DecisionState
    version: int
    owner: str
    content: DecisionContent
    transitions: tuple[DecisionTransition, ...]
    approvals: tuple[ApprovalRecord, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DecisionListItem:
    """A lightweight decision summary for portfolio listings."""

    decision_id: str
    title: str
    owner: str
    state: DecisionState
    version: int
    created_at: datetime
    updated_at: datetime


class DecisionLedgerRepository(Protocol):
    """Persistence port for the append-only, versioned decision ledger."""

    def get(self, decision_id: str) -> DecisionSnapshot | None: ...

    def create(
        self,
        *,
        decision_id: str,
        content: DecisionContent,
        transition: DecisionTransition,
        occurred_at: datetime,
    ) -> DecisionSnapshot: ...

    def append(
        self,
        *,
        decision_id: str,
        expected_version: int,
        new_state: DecisionState,
        content: DecisionContent,
        transition: DecisionTransition,
        approval: ApprovalRecord | None,
        occurred_at: datetime,
    ) -> DecisionSnapshot: ...

    def list(
        self,
        *,
        limit: int,
        after_id: str | None,
    ) -> tuple[DecisionListItem, ...]: ...


class DecisionLedgerService:
    """Coordinates governed decision transitions over a persistence port."""

    def __init__(
        self,
        repository: DecisionLedgerRepository,
        *,
        require_separation_of_duties: bool = True,
    ) -> None:
        self._repository = repository
        self._require_separation = require_separation_of_duties

    def create_decision(
        self,
        *,
        decision_id: str,
        content: DecisionContent,
        actor: str,
        occurred_at: datetime,
    ) -> DecisionSnapshot:
        if self._repository.get(decision_id) is not None:
            raise LedgerConflictError(
                f"decision '{decision_id}' already exists"
            )
        transition = DecisionTransition(
            from_state=None,
            to_state="draft",
            actor=actor,
            occurred_at=occurred_at,
        )
        return self._repository.create(
            decision_id=decision_id,
            content=content,
            transition=transition,
            occurred_at=occurred_at,
        )

    def update_content(
        self,
        *,
        decision_id: str,
        expected_version: int,
        content: DecisionContent,
        actor: str,
        occurred_at: datetime,
    ) -> DecisionSnapshot:
        snapshot = self._require(decision_id)
        self._check_version(snapshot, expected_version)
        if snapshot.state not in EDITABLE_STATES:
            raise DomainValidationError(
                "decision content is frozen once it enters review"
            )
        transition = DecisionTransition(
            from_state=snapshot.state,
            to_state=snapshot.state,
            actor=actor,
            occurred_at=occurred_at,
            note="content revised",
        )
        return self._repository.append(
            decision_id=decision_id,
            expected_version=expected_version,
            new_state=snapshot.state,
            content=content,
            transition=transition,
            approval=None,
            occurred_at=occurred_at,
        )

    def transition(
        self,
        *,
        decision_id: str,
        expected_version: int,
        target: DecisionState,
        actor: str,
        occurred_at: datetime,
        note: str | None = None,
        approval: ApprovalRecord | None = None,
    ) -> DecisionSnapshot:
        snapshot = self._require(decision_id)
        self._check_version(snapshot, expected_version)
        validate_transition(snapshot.state, target)

        if (snapshot.state, target) == APPROVAL_TRANSITION:
            approval = self._validated_approval(snapshot, approval)
        elif approval is not None:
            raise DomainValidationError(
                "approvals are only valid on the review-to-approved transition"
            )

        transition = DecisionTransition(
            from_state=snapshot.state,
            to_state=target,
            actor=actor,
            occurred_at=occurred_at,
            note=note,
        )
        return self._repository.append(
            decision_id=decision_id,
            expected_version=expected_version,
            new_state=target,
            content=snapshot.content,
            transition=transition,
            approval=approval,
            occurred_at=occurred_at,
        )

    def list_decisions(
        self, *, limit: int, after_id: str | None = None
    ) -> tuple[DecisionListItem, ...]:
        return self._repository.list(limit=limit, after_id=after_id)

    def get(self, decision_id: str) -> DecisionSnapshot:
        return self._require(decision_id)

    def export_packet(
        self, *, decision_id: str, exported_at: datetime
    ) -> DecisionPacket:
        snapshot = self._require(decision_id)
        return build_decision_packet(
            decision_id=snapshot.decision_id,
            state=snapshot.state,
            version=snapshot.version,
            content=snapshot.content,
            transitions=snapshot.transitions,
            approvals=snapshot.approvals,
            exported_at=exported_at,
        )

    def _validated_approval(
        self, snapshot: DecisionSnapshot, approval: ApprovalRecord | None
    ) -> ApprovalRecord:
        if approval is None:
            raise DomainValidationError(
                "approval to 'approved' requires an approval record"
            )
        if approval.decision != "approve":
            raise DomainValidationError(
                "a rejecting approval cannot move a decision to 'approved'"
            )
        expected_digest = snapshot.content.content_digest()
        if approval.approved_content_digest != expected_digest:
            raise DomainValidationError(
                "approval does not match the current decision evidence"
            )
        ensure_separation_of_duties(
            owner=snapshot.owner,
            approver=approval.approver,
            required=self._require_separation,
        )
        return approval

    def _require(self, decision_id: str) -> DecisionSnapshot:
        snapshot = self._repository.get(decision_id)
        if snapshot is None:
            raise LedgerNotFoundError(decision_id)
        return snapshot

    @staticmethod
    def _check_version(snapshot: DecisionSnapshot, expected_version: int) -> None:
        if snapshot.version != expected_version:
            raise LedgerConflictError(
                f"decision '{snapshot.decision_id}' is at version "
                f"{snapshot.version}, not {expected_version}"
            )
