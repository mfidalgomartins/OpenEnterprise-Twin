"""Explicit SQLAlchemy repositories for scenarios and experiment lifecycle."""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

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

    def mark_running(self, record: ExperimentRecord) -> None:
        now = datetime.now(UTC)
        record.status = "running"
        record.started_at = now
        record.updated_at = now
        self._session.flush()

    def mark_completed(
        self,
        record: ExperimentRecord,
        *,
        artifact_digest: str,
        result_payload: Mapping[str, object],
    ) -> None:
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
