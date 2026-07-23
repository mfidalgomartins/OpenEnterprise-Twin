"""Calibration, backtesting and credibility analytics for the decision twin.

This package is a pure analytics layer: like ``domain`` and ``simulation`` it
never imports delivery infrastructure. It turns real operating history into a
calibrated, credibility-scored twin that the optimizer and monitoring loop
build on.
"""

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
from openenterprise_twin.analytics.quality import (
    DataQualityIssue,
    DataQualityReport,
    assess_data_quality,
)
from openenterprise_twin.analytics.synthetic import generate_northstar_history

__all__ = [
    "SERIES_REGISTRY",
    "BacktestResult",
    "CalibrationComparison",
    "CalibrationResult",
    "ConfidenceInterval",
    "CredibilityComponent",
    "CredibilityScore",
    "DataQualityIssue",
    "DataQualityReport",
    "DatasetProvenance",
    "EstimatedParameter",
    "HistoricalDataset",
    "HistoricalObservation",
    "KpiBacktest",
    "SeasonalityEstimate",
    "SeriesName",
    "assess_data_quality",
    "backtest_calibration",
    "backtest_rolling",
    "build_dataset",
    "calibrate_twin",
    "compare_calibrations",
    "compute_data_digest",
    "generate_northstar_history",
    "score_credibility",
]
