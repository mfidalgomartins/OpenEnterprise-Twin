from datetime import UTC, date, datetime, timedelta

import pytest

from openenterprise_twin.analytics.monitoring import (
    MetricPrediction,
    MonitoringAlert,
    OutcomeRecord,
    monitor_outcomes,
    reconcile_alerts,
)
from openenterprise_twin.domain.errors import DomainValidationError


def _prediction(**overrides: object) -> MetricPrediction:
    base: dict[str, object] = {
        "metric_name": "ebitda",
        "expected_mean": 1000.0,
        "lower": 900.0,
        "upper": 1100.0,
        "improvement_direction": "higher",
    }
    base.update(overrides)
    return MetricPrediction.model_validate(base)


def _outcome(value: float, day: int = 1, metric: str = "ebitda") -> OutcomeRecord:
    return OutcomeRecord(
        metric_name=metric,
        as_of=date(2026, 1, day),
        realized_value=value,
    )


def _now() -> datetime:
    return datetime(2026, 3, 1, tzinfo=UTC)


def test_within_interval_is_within_expectation() -> None:
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(_prediction(),),
        outcomes=(_outcome(1000.0),),
        now=_now(),
    )
    assert report.kpis[0].level == "within_expectation"
    assert report.alerts == ()
    assert report.recommended_level == "within_expectation"


def test_mild_adverse_deviation_is_early_warning() -> None:
    # 150 below expected, half-width 100 -> 1.5 adverse half-widths (boundary).
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(_prediction(),),
        outcomes=(_outcome(870.0),),
        now=_now(),
    )
    assert report.kpis[0].level in {"early_warning", "material_deviation"}
    assert report.alerts


def test_large_adverse_deviation_triggers_review() -> None:
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(_prediction(),),
        outcomes=(_outcome(500.0),),  # 5 half-widths below expected
        now=_now(),
    )
    assert report.kpis[0].level == "decision_review_required"
    assert report.recommended_level == "decision_review_required"
    assert report.alerts[0].severity == "critical"


def test_better_than_expected_is_early_warning_not_alarm() -> None:
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(_prediction(),),
        outcomes=(_outcome(1400.0),),  # far above interval, but favourable
        now=_now(),
    )
    kpi = report.kpis[0]
    assert kpi.level == "early_warning"
    assert kpi.adverse_deviation < 0


def test_hard_constraint_breach_forces_review() -> None:
    prediction = _prediction(
        metric_name="otif",
        expected_mean=0.96,
        lower=0.95,
        upper=0.99,
        is_hard_constraint=True,
        constraint_bound=0.95,
    )
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(prediction,),
        outcomes=(_outcome(0.90, metric="otif"),),
        now=_now(),
    )
    assert report.kpis[0].hard_constraint_ok is False
    assert report.kpis[0].level == "decision_review_required"


def test_cumulative_deviation_accumulates_over_time() -> None:
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(_prediction(),),
        outcomes=(
            _outcome(950.0, day=1),
            _outcome(940.0, day=2),
            _outcome(930.0, day=3),
        ),
        now=_now(),
    )
    kpi = report.kpis[0]
    # latest realised is day 3 = 930; cumulative adverse = (50+60+70) = 180.
    assert kpi.realized_value == 930.0
    assert kpi.cumulative_adverse_deviation == pytest.approx(180.0)


def test_parameter_drift_triggers_recalibration() -> None:
    report = monitor_outcomes(
        decision_id="dec-1",
        predictions=(_prediction(),),
        outcomes=(_outcome(1000.0),),
        now=_now(),
        parameter_change=0.6,  # 0.6 / 0.5 -> clamped to 1.0
    )
    assert report.drift.parameter_drift == 1.0
    assert report.drift.recalibration_required
    assert any(a.level == "recalibration_required" for a in report.alerts)


def test_unmatched_outcome_is_rejected() -> None:
    with pytest.raises(DomainValidationError):
        monitor_outcomes(
            decision_id="dec-1",
            predictions=(_prediction(),),
            outcomes=(_outcome(1.0, metric="revenue"),),
            now=_now(),
        )


def test_report_is_reproducible() -> None:
    args = {
        "decision_id": "dec-1",
        "predictions": (_prediction(),),
        "outcomes": (_outcome(880.0),),
        "now": _now(),
    }
    assert monitor_outcomes(**args).digest == monitor_outcomes(**args).digest


def test_reconcile_suppresses_within_cooldown() -> None:
    base = MonitoringAlert(
        metric_name="ebitda",
        level="material_deviation",
        severity="warning",
        message="x",
        created_at=_now(),
    )
    active = (base,)
    incoming = (base.model_copy(update={"created_at": _now() + timedelta(days=3)}),)
    assert reconcile_alerts(active=active, incoming=incoming, cooldown_days=7) == ()
    later = (base.model_copy(update={"created_at": _now() + timedelta(days=10)}),)
    assert len(reconcile_alerts(active=active, incoming=later, cooldown_days=7)) == 1


def test_reconcile_emits_new_dedup_key() -> None:
    active = (
        MonitoringAlert(
            metric_name="ebitda",
            level="early_warning",
            severity="warning",
            message="x",
            created_at=_now(),
        ),
    )
    incoming = (
        MonitoringAlert(
            metric_name="otif",
            level="decision_review_required",
            severity="critical",
            message="y",
            created_at=_now(),
        ),
    )
    assert len(reconcile_alerts(active=active, incoming=incoming, cooldown_days=7)) == 1
