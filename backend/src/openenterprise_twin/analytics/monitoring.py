"""Outcome monitoring, drift detection and governed alerting for decisions.

Once a decision is implemented, realised KPI outcomes are compared against the
prediction that justified it. Every judgement is rule-based and explainable: a
KPI is *within expectation* when it lands inside its predicted interval, and it
escalates through documented thresholds to a review trigger. Drift is measured
across data, parameters and results, and alerts are severity-ranked, deduplicated
and cooldown-aware so the loop stays quiet until it genuinely needs attention.
"""

from __future__ import annotations

from datetime import date, datetime
from statistics import mean
from typing import Annotated, Literal

from pydantic import Field, model_validator

from openenterprise_twin.analytics._digest import canonical_digest
from openenterprise_twin.domain.company import DomainModel, Identifier
from openenterprise_twin.domain.errors import DomainValidationError

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
ImprovementDirection = Literal["higher", "lower"]

AlertLevel = Literal[
    "within_expectation",
    "early_warning",
    "material_deviation",
    "recalibration_required",
    "decision_review_required",
]
AlertSeverity = Literal["info", "warning", "critical"]

#: Documented severity of each alert level.
ALERT_SEVERITY: dict[AlertLevel, AlertSeverity] = {
    "within_expectation": "info",
    "early_warning": "warning",
    "material_deviation": "warning",
    "recalibration_required": "warning",
    "decision_review_required": "critical",
}

#: Adverse standardised-deviation band (in predicted half-widths) that separates
#: an early warning from a material deviation, and a review trigger.
EARLY_WARNING_Z = 1.5
DECISION_REVIEW_Z = 3.0
#: Drift severity at or above which recalibration is recommended.
RECALIBRATION_THRESHOLD = 0.5


class MetricPrediction(DomainModel):
    """The predicted distribution and governance rule for one KPI."""

    metric_name: Annotated[str, Field(min_length=1, max_length=64)]
    expected_mean: FiniteFloat
    lower: FiniteFloat
    upper: FiniteFloat
    improvement_direction: ImprovementDirection
    materiality_threshold: Annotated[float, Field(ge=0.0)] = 0.0
    is_hard_constraint: bool = False
    constraint_bound: FiniteFloat | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> MetricPrediction:
        if self.upper < self.lower:
            raise DomainValidationError("prediction upper < lower")
        if self.is_hard_constraint and self.constraint_bound is None:
            raise DomainValidationError(
                "a hard-constraint KPI requires a constraint_bound"
            )
        return self

    @property
    def half_width(self) -> float:
        return (self.upper - self.lower) / 2.0


class OutcomeRecord(DomainModel):
    """One realised observation of a KPI after implementation."""

    metric_name: Annotated[str, Field(min_length=1, max_length=64)]
    as_of: date
    realized_value: FiniteFloat


class KpiOutcome(DomainModel):
    """Expected-vs-realised reconciliation for one monitored KPI."""

    metric_name: str
    expected_mean: FiniteFloat
    realized_value: FiniteFloat
    lower: FiniteFloat
    upper: FiniteFloat
    within_interval: bool
    deviation: FiniteFloat
    adverse_deviation: FiniteFloat
    standardized_adverse_deviation: FiniteFloat
    cumulative_adverse_deviation: FiniteFloat
    hard_constraint_ok: bool
    level: AlertLevel
    observation_count: Annotated[int, Field(gt=0)]


class MonitoringAlert(DomainModel):
    """A governed, deduplicable alert with an explicit reason."""

    metric_name: str | None
    level: AlertLevel
    severity: AlertSeverity
    message: Annotated[str, Field(min_length=1, max_length=280)]
    created_at: datetime
    acknowledged: bool = False

    @property
    def dedup_key(self) -> str:
        return f"{self.metric_name or '*'}::{self.level}"

    @model_validator(mode="after")
    def validate_created_at(self) -> MonitoringAlert:
        if self.created_at.tzinfo is None:
            raise DomainValidationError("alert timestamps must be aware")
        return self


class DriftAssessment(DomainModel):
    """Decomposed drift across data, parameters and realised results."""

    data_drift: UnitInterval
    parameter_drift: UnitInterval
    result_drift: UnitInterval
    overall_severity: UnitInterval
    recalibration_required: bool
    detail: Annotated[str, Field(min_length=1, max_length=280)]


class MonitoringReport(DomainModel):
    """A reproducible outcome + drift assessment for one decision."""

    decision_id: Identifier
    kpis: tuple[KpiOutcome, ...]
    drift: DriftAssessment
    alerts: tuple[MonitoringAlert, ...]
    recommended_level: AlertLevel
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def monitor_outcomes(
    *,
    decision_id: str,
    predictions: tuple[MetricPrediction, ...],
    outcomes: tuple[OutcomeRecord, ...],
    now: datetime,
    parameter_change: float = 0.0,
    data_quality_delta: float = 0.0,
) -> MonitoringReport:
    """Reconcile realised outcomes with predictions and assess drift."""

    if not predictions:
        raise DomainValidationError("monitoring requires at least one prediction")
    prediction_by_name = {item.metric_name: item for item in predictions}
    outcomes_by_name: dict[str, list[OutcomeRecord]] = {}
    for record in outcomes:
        if record.metric_name not in prediction_by_name:
            raise DomainValidationError(
                f"outcome '{record.metric_name}' has no matching prediction"
            )
        outcomes_by_name.setdefault(record.metric_name, []).append(record)

    kpis: list[KpiOutcome] = []
    alerts: list[MonitoringAlert] = []
    for name in sorted(outcomes_by_name):
        kpi = _evaluate_kpi(
            prediction_by_name[name], outcomes_by_name[name]
        )
        kpis.append(kpi)
        if kpi.level != "within_expectation":
            alerts.append(_alert_for(kpi, now))

    drift = _assess_drift(
        kpis=tuple(kpis),
        parameter_change=parameter_change,
        data_quality_delta=data_quality_delta,
    )
    if drift.recalibration_required:
        alerts.append(
            MonitoringAlert(
                metric_name=None,
                level="recalibration_required",
                severity=ALERT_SEVERITY["recalibration_required"],
                message=(
                    f"drift severity {drift.overall_severity:.2f} exceeds the "
                    f"recalibration threshold {RECALIBRATION_THRESHOLD:.2f}"
                ),
                created_at=now,
            )
        )
    ordered_alerts = tuple(
        sorted(
            alerts,
            key=lambda alert: (
                -_severity_rank(alert.severity),
                alert.metric_name or "",
                alert.level,
            ),
        )
    )
    recommended = _recommended_level(kpis, drift)
    digest = _report_digest(
        decision_id=decision_id, kpis=tuple(kpis), drift=drift, level=recommended
    )
    return MonitoringReport(
        decision_id=decision_id,
        kpis=tuple(kpis),
        drift=drift,
        alerts=ordered_alerts,
        recommended_level=recommended,
        digest=digest,
    )


def reconcile_alerts(
    *,
    active: tuple[MonitoringAlert, ...],
    incoming: tuple[MonitoringAlert, ...],
    cooldown_days: int,
) -> tuple[MonitoringAlert, ...]:
    """Suppress duplicate alerts still inside their cooldown window.

    An incoming alert is emitted only when no active alert shares its dedup key
    within the cooldown horizon. Acknowledged active alerts continue to suppress
    re-emission until the cooldown lapses, keeping the loop quiet.
    """

    latest_by_key: dict[str, MonitoringAlert] = {}
    for alert in active:
        current = latest_by_key.get(alert.dedup_key)
        if current is None or alert.created_at > current.created_at:
            latest_by_key[alert.dedup_key] = alert
    emitted: list[MonitoringAlert] = []
    for alert in incoming:
        previous = latest_by_key.get(alert.dedup_key)
        if previous is not None:
            age_days = (alert.created_at - previous.created_at).days
            if age_days <= cooldown_days:
                continue
        emitted.append(alert)
    return tuple(emitted)


def _evaluate_kpi(
    prediction: MetricPrediction, records: list[OutcomeRecord]
) -> KpiOutcome:
    ordered = sorted(records, key=lambda record: record.as_of)
    realized = ordered[-1].realized_value
    within = prediction.lower <= realized <= prediction.upper
    deviation = realized - prediction.expected_mean
    adverse = _adverse(prediction.improvement_direction, deviation)
    scale = prediction.half_width or (abs(prediction.expected_mean) * 0.1) or 1.0
    standardized = adverse / scale
    cumulative = sum(
        _adverse(
            prediction.improvement_direction,
            record.realized_value - prediction.expected_mean,
        )
        for record in ordered
    )
    hard_ok = _hard_constraint_ok(prediction, realized)
    level = _classify(prediction, within, standardized, hard_ok)
    return KpiOutcome(
        metric_name=prediction.metric_name,
        expected_mean=prediction.expected_mean,
        realized_value=realized,
        lower=prediction.lower,
        upper=prediction.upper,
        within_interval=within,
        deviation=round(deviation, 6),
        adverse_deviation=round(adverse, 6),
        standardized_adverse_deviation=round(standardized, 6),
        cumulative_adverse_deviation=round(cumulative, 6),
        hard_constraint_ok=hard_ok,
        level=level,
        observation_count=len(ordered),
    )


def _classify(
    prediction: MetricPrediction,
    within: bool,
    standardized_adverse: float,
    hard_ok: bool,
) -> AlertLevel:
    if not hard_ok:
        return "decision_review_required"
    if within:
        return "within_expectation"
    if standardized_adverse <= 0:
        # Outside the interval but better than expected: the model under-
        # predicted, which is a mild calibration signal, not a business concern.
        return "early_warning"
    if standardized_adverse >= DECISION_REVIEW_Z:
        return "decision_review_required"
    if standardized_adverse >= EARLY_WARNING_Z:
        return "material_deviation"
    return "early_warning"


def _hard_constraint_ok(prediction: MetricPrediction, realized: float) -> bool:
    if not prediction.is_hard_constraint or prediction.constraint_bound is None:
        return True
    if prediction.improvement_direction == "higher":
        return realized >= prediction.constraint_bound
    return realized <= prediction.constraint_bound


def _adverse(direction: ImprovementDirection, deviation: float) -> float:
    return -deviation if direction == "higher" else deviation


def _assess_drift(
    *,
    kpis: tuple[KpiOutcome, ...],
    parameter_change: float,
    data_quality_delta: float,
) -> DriftAssessment:
    if kpis:
        result_drift = _clamp_unit(
            mean(max(0.0, kpi.standardized_adverse_deviation) for kpi in kpis)
            / DECISION_REVIEW_Z
        )
    else:
        result_drift = 0.0
    parameter_drift = _clamp_unit(abs(parameter_change) / 0.5)
    data_drift = _clamp_unit(max(0.0, -data_quality_delta) / 0.5)
    overall = max(result_drift, parameter_drift, data_drift)
    return DriftAssessment(
        data_drift=round(data_drift, 6),
        parameter_drift=round(parameter_drift, 6),
        result_drift=round(result_drift, 6),
        overall_severity=round(overall, 6),
        recalibration_required=overall >= RECALIBRATION_THRESHOLD,
        detail=(
            f"result={result_drift:.2f} parameter={parameter_drift:.2f} "
            f"data={data_drift:.2f}"
        ),
    )


def _alert_for(kpi: KpiOutcome, now: datetime) -> MonitoringAlert:
    return MonitoringAlert(
        metric_name=kpi.metric_name,
        level=kpi.level,
        severity=ALERT_SEVERITY[kpi.level],
        message=_alert_message(kpi),
        created_at=now,
    )


def _alert_message(kpi: KpiOutcome) -> str:
    if kpi.level == "decision_review_required" and not kpi.hard_constraint_ok:
        return (
            f"{kpi.metric_name}: hard constraint breached "
            f"(realised {kpi.realized_value:.2f})"
        )
    return (
        f"{kpi.metric_name}: realised {kpi.realized_value:.2f} vs expected "
        f"{kpi.expected_mean:.2f} "
        f"({kpi.standardized_adverse_deviation:+.2f} adverse half-widths)"
    )


def _recommended_level(
    kpis: list[KpiOutcome], drift: DriftAssessment
) -> AlertLevel:
    levels = [kpi.level for kpi in kpis]
    if drift.recalibration_required:
        levels.append("recalibration_required")
    order: list[AlertLevel] = [
        "decision_review_required",
        "recalibration_required",
        "material_deviation",
        "early_warning",
        "within_expectation",
    ]
    for level in order:
        if level in levels:
            return level
    return "within_expectation"


def _severity_rank(severity: AlertSeverity) -> int:
    return {"info": 0, "warning": 1, "critical": 2}[severity]


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _report_digest(
    *,
    decision_id: str,
    kpis: tuple[KpiOutcome, ...],
    drift: DriftAssessment,
    level: AlertLevel,
) -> str:
    body = {
        "decision_id": decision_id,
        "level": level,
        "kpis": [kpi.model_dump(mode="json") for kpi in kpis],
        "drift": drift.model_dump(mode="json"),
    }
    return canonical_digest(body)
