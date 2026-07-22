"""Explicit SQLAlchemy repositories for scenarios and experiment lifecycle."""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from openenterprise_twin.application.ports import (
    CompletedCandidateRecord,
    ExperimentDecisionRecord,
)
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.infrastructure.models import (
    ExperimentRecord,
    ScenarioRecord,
)


class ScenarioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, scenario_id: str) -> ScenarioRecord | None:
        return self._session.get(ScenarioRecord, scenario_id)

    def list(
        self,
        *,
        limit: int = 50,
        after_id: str | None = None,
    ) -> tuple[ScenarioRecord, ...]:
        statement: Select[tuple[ScenarioRecord]] = select(ScenarioRecord)
        if after_id is not None:
            statement = statement.where(ScenarioRecord.scenario_id > after_id)
        statement = statement.order_by(ScenarioRecord.scenario_id).limit(limit)
        return tuple(self._session.scalars(statement))

    def create(self, scenario: Scenario) -> ScenarioRecord:
        record = ScenarioRecord(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            company_model_version=scenario.company_model_version,
            scenario_schema_version=scenario.schema_version,
            payload=scenario.model_dump(mode="json"),
        )
        self._session.add(record)
        self._session.flush()
        return record


class ExperimentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, experiment_id: int) -> ExperimentRecord | None:
        return self._session.get(ExperimentRecord, experiment_id)

    def get_by_idempotency_key(self, key: str) -> ExperimentRecord | None:
        return self._session.scalar(
            select(ExperimentRecord).where(
                ExperimentRecord.idempotency_key == key
            )
        )

    def latest_completed_for_scenario(
        self,
        *,
        scenario_id: str,
        master_seed: int,
        replication_count: int,
    ) -> ExperimentRecord | None:
        statement: Select[tuple[ExperimentRecord]] = (
            select(ExperimentRecord)
            .where(
                ExperimentRecord.scenario_id == scenario_id,
                ExperimentRecord.master_seed == master_seed,
                ExperimentRecord.replication_count == replication_count,
                ExperimentRecord.status == "completed",
            )
            .order_by(ExperimentRecord.id.desc())
            .limit(1)
        )
        return self._session.scalar(statement)

    def create(
        self,
        *,
        scenario_id: str,
        baseline_experiment_id: int | None,
        master_seed: int,
        replication_count: int,
        idempotency_key: str | None,
        request_payload: Mapping[str, object],
    ) -> ExperimentRecord:
        record = ExperimentRecord(
            scenario_id=scenario_id,
            baseline_experiment_id=baseline_experiment_id,
            status="queued",
            master_seed=master_seed,
            replication_count=replication_count,
            idempotency_key=idempotency_key,
            request_payload=dict(request_payload),
        )
        self._session.add(record)
        self._session.flush()
        return record

    def delete_queued(self, record: ExperimentRecord) -> None:
        if record.status != "queued":
            raise RuntimeError("only queued experiments can be deleted")
        self._session.delete(record)
        self._session.flush()

    def recover_interrupted(self) -> int:
        records = tuple(
            self._session.scalars(
                select(ExperimentRecord).where(
                    ExperimentRecord.status == "running"
                )
            )
        )
        now = datetime.now(UTC)
        for record in records:
            record.status = "queued"
            record.started_at = None
            record.updated_at = now
        self._session.flush()
        return len(records)

    def pending_ids(self) -> tuple[int, ...]:
        statement = (
            select(ExperimentRecord.id)
            .where(ExperimentRecord.status == "queued")
            .order_by(ExperimentRecord.created_at, ExperimentRecord.id)
        )
        return tuple(self._session.scalars(statement))

    def claim_queued(self, experiment_id: int) -> ExperimentRecord | None:
        statement = (
            select(ExperimentRecord)
            .where(
                ExperimentRecord.id == experiment_id,
                ExperimentRecord.status == "queued",
            )
            .with_for_update(skip_locked=True)
        )
        record = self._session.scalar(statement)
        if record is None:
            return None
        now = datetime.now(UTC)
        record.status = "running"
        record.started_at = now
        record.updated_at = now
        self._session.flush()
        return record

    def mark_completed(
        self,
        record: ExperimentRecord,
        *,
        artifact_digest: str,
        result_payload: Mapping[str, object],
    ) -> None:
        if record.status != "running":
            raise RuntimeError("only running experiments can complete")
        now = datetime.now(UTC)
        record.status = "completed"
        record.artifact_digest = artifact_digest
        record.result_payload = dict(result_payload)
        record.error_code = None
        record.error_detail = None
        record.completed_at = now
        record.updated_at = now
        self._session.flush()

    def mark_failed(
        self,
        record: ExperimentRecord,
        *,
        error_code: str,
        error_detail: str,
    ) -> None:
        if record.status not in {"queued", "running"}:
            raise RuntimeError("only active experiments can fail")
        now = datetime.now(UTC)
        record.status = "failed"
        record.error_code = error_code
        record.error_detail = error_detail
        record.completed_at = now
        record.updated_at = now
        self._session.flush()

    def store_comparison(
        self,
        record: ExperimentRecord,
        payload: Mapping[str, object],
    ) -> None:
        record.comparison_payload = dict(payload)
        record.updated_at = datetime.now(UTC)
        self._session.flush()

    def store_brief(
        self,
        record: ExperimentRecord,
        payload: Mapping[str, object],
    ) -> None:
        record.brief_payload = dict(payload)
        record.updated_at = datetime.now(UTC)
        self._session.flush()


class SqlAlchemyDecisionEvidenceRepository:
    """Short-transaction adapter for CPU and I/O-heavy decision services."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, experiment_id: int) -> ExperimentDecisionRecord | None:
        with self._session_factory() as session:
            record = ExperimentRepository(session).get(experiment_id)
            if record is None:
                return None
            return ExperimentDecisionRecord(
                id=record.id,
                status=record.status,
                baseline_experiment_id=record.baseline_experiment_id,
                artifact_digest=record.artifact_digest,
                comparison_payload=(
                    dict(record.comparison_payload)
                    if record.comparison_payload is not None
                    else None
                ),
                brief_payload=(
                    dict(record.brief_payload)
                    if record.brief_payload is not None
                    else None
                ),
            )

    def store_comparison(
        self,
        experiment_id: int,
        payload: Mapping[str, object],
    ) -> None:
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
            record = repository.get(experiment_id)
            if record is None:
                raise LookupError(f"experiment '{experiment_id}' is not present")
            repository.store_comparison(record, payload)

    def store_brief(
        self,
        experiment_id: int,
        payload: Mapping[str, object],
    ) -> None:
        with self._session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
            record = repository.get(experiment_id)
            if record is None:
                raise LookupError(f"experiment '{experiment_id}' is not present")
            repository.store_brief(record, payload)

    def list_completed_candidates(
        self,
        *,
        limit: int,
        before_id: int | None,
    ) -> tuple[CompletedCandidateRecord, ...]:
        with self._session_factory() as session:
            statement = (
                select(ExperimentRecord, ScenarioRecord.name)
                .join(
                    ScenarioRecord,
                    ScenarioRecord.scenario_id == ExperimentRecord.scenario_id,
                )
                .where(
                    ExperimentRecord.status == "completed",
                    ExperimentRecord.baseline_experiment_id.is_not(None),
                )
                .order_by(ExperimentRecord.id.desc())
                .limit(limit)
            )
            if before_id is not None:
                statement = statement.where(ExperimentRecord.id < before_id)
            rows = session.execute(statement).all()
            records: list[CompletedCandidateRecord] = []
            for record, scenario_name in rows:
                if record.completed_at is None:
                    raise RuntimeError("completed experiment is missing completed_at")
                records.append(
                    CompletedCandidateRecord(
                        id=record.id,
                        scenario_id=record.scenario_id,
                        scenario_name=scenario_name,
                        completed_at=record.completed_at,
                        replication_count=record.replication_count,
                    )
                )
            return tuple(records)
