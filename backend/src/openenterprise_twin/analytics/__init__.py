"""Calibration, backtesting and credibility analytics for the decision twin.

This package is a pure analytics layer: like ``domain`` and ``simulation`` it
never imports delivery infrastructure. It turns real operating history into a
calibrated, credibility-scored twin that the optimizer and monitoring loop
build on.
"""

from openenterprise_twin.analytics.adaptive import (
    Activation,
    AdaptiveComparison,
    AdaptiveEvaluation,
    AdaptivePolicy,
    AdaptiveRule,
    compare_adaptive_vs_static,
    derive_adaptive_scenario,
    evaluate_adaptive_policy,
    observations_from_trace,
)
from openenterprise_twin.analytics.backtesting import (
    BacktestResult,
    KpiBacktest,
    backtest_calibration,
    backtest_rolling,
)
from openenterprise_twin.analytics.calibration import (
    CalibrationComparison,
    CalibrationResult,
    ConfidenceInterval,
    EstimatedParameter,
    SeasonalityEstimate,
    calibrate_twin,
    compare_calibrations,
)
from openenterprise_twin.analytics.credibility import (
    CredibilityComponent,
    CredibilityScore,
    score_credibility,
)
from openenterprise_twin.analytics.history import (
    SERIES_REGISTRY,
    DatasetProvenance,
    HistoricalDataset,
    HistoricalObservation,
    SeriesName,
    build_dataset,
    compute_data_digest,
)
from openenterprise_twin.analytics.monitoring import (
    DriftAssessment,
    KpiOutcome,
    MetricPrediction,
    MonitoringAlert,
    MonitoringReport,
    OutcomeRecord,
    monitor_outcomes,
    reconcile_alerts,
)
from openenterprise_twin.analytics.optimization import (
    CandidateEvaluation,
    ConstraintSpec,
    LeverSpec,
    ObjectiveSpec,
    OptimizationConfig,
    OptimizationResult,
    PolicyCandidate,
    build_simulation_evaluator,
    decode_levers,
    optimize_policies,
)
from openenterprise_twin.analytics.quality import (
    DataQualityIssue,
    DataQualityReport,
    assess_data_quality,
)
from openenterprise_twin.analytics.synthetic import generate_northstar_history

__all__ = [
    "SERIES_REGISTRY",
    "Activation",
    "AdaptiveComparison",
    "AdaptiveEvaluation",
    "AdaptivePolicy",
    "AdaptiveRule",
    "BacktestResult",
    "CalibrationComparison",
    "CalibrationResult",
    "CandidateEvaluation",
    "ConfidenceInterval",
    "ConstraintSpec",
    "CredibilityComponent",
    "CredibilityScore",
    "DataQualityIssue",
    "DataQualityReport",
    "DatasetProvenance",
    "DriftAssessment",
    "EstimatedParameter",
    "HistoricalDataset",
    "HistoricalObservation",
    "KpiBacktest",
    "KpiOutcome",
    "LeverSpec",
    "MetricPrediction",
    "MonitoringAlert",
    "MonitoringReport",
    "ObjectiveSpec",
    "OptimizationConfig",
    "OptimizationResult",
    "OutcomeRecord",
    "PolicyCandidate",
    "SeasonalityEstimate",
    "SeriesName",
    "assess_data_quality",
    "backtest_calibration",
    "backtest_rolling",
    "build_dataset",
    "build_simulation_evaluator",
    "calibrate_twin",
    "compare_adaptive_vs_static",
    "compare_calibrations",
    "compute_data_digest",
    "decode_levers",
    "derive_adaptive_scenario",
    "evaluate_adaptive_policy",
    "generate_northstar_history",
    "monitor_outcomes",
    "observations_from_trace",
    "optimize_policies",
    "reconcile_alerts",
    "score_credibility",
]
