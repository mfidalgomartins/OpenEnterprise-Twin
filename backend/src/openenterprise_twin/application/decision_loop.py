"""Application services orchestrating the governed decision loop.

These services compose the pure analytics layer (calibration, backtesting,
credibility, optimization, monitoring) with persistence reached only through
ports. They own the transactional narrative of the loop -- ingest, calibrate,
optimize, monitor -- while keeping HTTP and SQLAlchemy out of the analytics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from openenterprise_twin.analytics.backtesting import (
    BacktestResult,
    backtest_calibration,
)
from openenterprise_twin.analytics.calibration import CalibrationResult, calibrate_twin
from openenterprise_twin.analytics.credibility import (
    CredibilityScore,
    score_credibility,
)
from openenterprise_twin.analytics.history import HistoricalDataset
from openenterprise_twin.analytics.monitoring import (
    MetricPrediction,
    MonitoringReport,
    OutcomeRecord,
    monitor_outcomes,
)
from openenterprise_twin.analytics.optimization import (
    OptimizationConfig,
    OptimizationResult,
    build_simulation_evaluator,
    optimize_policies,
)
from openenterprise_twin.analytics.quality import DataQualityReport, assess_data_quality
from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.scenario import Scenario


class DatasetTooLargeError(Exception):
    """Raised when a dataset exceeds the deployment ingestion limit."""

    def __init__(self, *, observation_count: int, limit: int) -> None:
        super().__init__(
            f"{observation_count} observations exceed the limit of {limit}"
        )
        self.observation_count = observation_count
        self.limit = limit


@dataclass(frozen=True, slots=True)
class StoredDataset:
    dataset_id: str
    company_id: str
    data_digest: str
    observation_count: int
    quality: DataQualityReport
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredCalibration:
    calibration_id: str
    dataset_id: str
    calibration: CalibrationResult
    credibility: CredibilityScore
    backtests: tuple[BacktestResult, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredOptimization:
    optimization_id: int
    digest: str
    evaluations: int
    result: OptimizationResult
    created_at: datetime


class DatasetRepository(Protocol):
    def get(self, dataset_id: str) -> HistoricalDataset | None: ...

    def get_quality(self, dataset_id: str) -> DataQualityReport | None: ...

    def save(
        self, dataset: HistoricalDataset, quality: DataQualityReport
    ) -> StoredDataset: ...

    def list(
        self, *, limit: int, after_id: str | None
    ) -> tuple[StoredDataset, ...]: ...


class CalibrationRepository(Protocol):
    def get(self, calibration_id: str) -> StoredCalibration | None: ...

    def save(self, stored: StoredCalibration) -> StoredCalibration: ...

    def list(
        self, *, limit: int, after_id: str | None
    ) -> tuple[StoredCalibration, ...]: ...


class OptimizationRepository(Protocol):
    def get(self, optimization_id: int) -> StoredOptimization | None: ...

    def save(
        self,
        *,
        company_model_version: str,
        config: OptimizationConfig,
        result: OptimizationResult,
    ) -> StoredOptimization: ...

    def list(
        self, *, limit: int, before_id: int | None
    ) -> tuple[StoredOptimization, ...]: ...


class MonitoringRepository(Protocol):
    def save(self, report: MonitoringReport) -> None: ...

    def latest(self, decision_id: str) -> MonitoringReport | None: ...


class CalibrationStudioService:
    """Ingest history, calibrate the twin and score its credibility."""

    def __init__(
        self,
        *,
        datasets: DatasetRepository,
        calibrations: CalibrationRepository,
        max_observations: int,
    ) -> None:
        self._datasets = datasets
        self._calibrations = calibrations
        self._max_observations = max_observations

    def ingest_dataset(self, dataset: HistoricalDataset) -> StoredDataset:
        # Enforced here so every ingestion path -- direct upload or synthetic
        # generation -- is bounded, not only the JSON route handler.
        if len(dataset.observations) > self._max_observations:
            raise DatasetTooLargeError(
                observation_count=len(dataset.observations),
                limit=self._max_observations,
            )
        if self._datasets.get(dataset.dataset_id) is not None:
            raise DomainValidationError(
                f"dataset '{dataset.dataset_id}' already exists"
            )
        quality = assess_data_quality(dataset)
        return self._datasets.save(dataset, quality)

    def calibrate(
        self,
        *,
        calibration_id: str,
        dataset_id: str,
        company: CompanyModel,
        backtest_cutoff: date | None,
    ) -> StoredCalibration:
        dataset = self._datasets.get(dataset_id)
        quality = self._datasets.get_quality(dataset_id)
        if dataset is None or quality is None:
            raise DomainValidationError(f"dataset '{dataset_id}' does not exist")
        if self._calibrations.get(calibration_id) is not None:
            raise DomainValidationError(
                f"calibration '{calibration_id}' already exists"
            )
        calibration = calibrate_twin(
            calibration_id=calibration_id,
            dataset=dataset,
            company=company,
        )
        backtests = (
            (backtest_calibration(
                dataset=dataset, company=company, cutoff=backtest_cutoff
            ),)
            if backtest_cutoff is not None
            else ()
        )
        credibility = score_credibility(
            calibration=calibration,
            quality=quality,
            backtests=backtests,
        )
        stored = StoredCalibration(
            calibration_id=calibration_id,
            dataset_id=dataset_id,
            calibration=calibration,
            credibility=credibility,
            backtests=backtests,
            created_at=calibration.created_at,
        )
        return self._calibrations.save(stored)


class OptimizationLabService:
    """Run bounded, deterministic policy optimizations over the engine."""

    def __init__(
        self,
        *,
        optimizations: OptimizationRepository,
        max_evaluations: int,
        max_periods: int,
    ) -> None:
        self._optimizations = optimizations
        self._max_evaluations = max_evaluations
        self._max_periods = max_periods

    def optimize(
        self,
        *,
        company: CompanyModel,
        base_scenario: Scenario,
        config: OptimizationConfig,
        replications: int,
        master_seed: int,
    ) -> StoredOptimization:
        if config.max_evaluations > self._max_evaluations:
            raise DomainValidationError(
                f"max_evaluations {config.max_evaluations} exceeds the deployment "
                f"limit {self._max_evaluations}"
            )
        estimated_periods = (
            config.max_evaluations * replications * base_scenario.horizon_days
        )
        if estimated_periods > self._max_periods:
            raise DomainValidationError(
                f"this search needs up to {estimated_periods:,} simulated periods; "
                f"the deployment limit is {self._max_periods:,}. Reduce the "
                "evaluation budget, replications or horizon."
            )
        evaluator = build_simulation_evaluator(
            company=company,
            base_scenario=base_scenario,
            master_seed=master_seed,
            replications=replications,
        )
        result = optimize_policies(
            config=config,
            evaluator=evaluator,
            company=company,
            base_scenario=base_scenario,
        )
        return self._optimizations.save(
            company_model_version=company.model_version,
            config=config,
            result=result,
        )


class MonitoringService:
    """Reconcile realised outcomes against a decision's prediction."""

    def __init__(self, *, reports: MonitoringRepository) -> None:
        self._reports = reports

    def record_outcomes(
        self,
        *,
        decision_id: str,
        predictions: tuple[MetricPrediction, ...],
        outcomes: tuple[OutcomeRecord, ...],
        now: datetime,
        parameter_change: float = 0.0,
        data_quality_delta: float = 0.0,
    ) -> MonitoringReport:
        report = monitor_outcomes(
            decision_id=decision_id,
            predictions=predictions,
            outcomes=outcomes,
            now=now,
            parameter_change=parameter_change,
            data_quality_delta=data_quality_delta,
        )
        self._reports.save(report)
        return report

    def latest(self, decision_id: str) -> MonitoringReport | None:
        return self._reports.latest(decision_id)
