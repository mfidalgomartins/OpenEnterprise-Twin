from datetime import date

import pytest
from tests.factories import build_northstar_company

from openenterprise_twin.analytics.backtesting import (
    backtest_calibration,
    backtest_rolling,
)
from openenterprise_twin.analytics.synthetic import generate_northstar_history
from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import DomainValidationError


@pytest.fixture
def company() -> CompanyModel:
    return build_northstar_company()


def _cutoff(dataset_window: tuple[date, date], fraction: float) -> date:
    start, end = dataset_window
    span = end.toordinal() - start.toordinal()
    return date.fromordinal(start.toordinal() + int(span * fraction))


def test_backtest_validates_strictly_after_cutoff(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    cutoff = _cutoff(dataset.window, 0.66)
    result = backtest_calibration(dataset=dataset, company=company, cutoff=cutoff)
    assert result.in_sample_end == cutoff
    assert result.validation_start > cutoff
    assert result.validation_end == dataset.window[1]


def test_backtest_cutoff_must_be_inside_window(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    start, end = dataset.window
    with pytest.raises(DomainValidationError):
        backtest_calibration(dataset=dataset, company=company, cutoff=end)
    with pytest.raises(DomainValidationError):
        backtest_calibration(
            dataset=dataset,
            company=company,
            cutoff=date.fromordinal(start.toordinal() - 1),
        )


def test_backtest_produces_bounded_metrics(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    cutoff = _cutoff(dataset.window, 0.66)
    result = backtest_calibration(dataset=dataset, company=company, cutoff=cutoff)
    assert result.evaluated_kpis > 0
    assert 0.0 <= result.overall_interval_coverage <= 1.0
    assert result.overall_weighted_mape >= 0.0
    for kpi in result.kpis:
        assert kpi.sample_size > 0
        assert kpi.mean_absolute_error >= 0.0
        assert 0.0 <= kpi.interval_coverage <= 1.0


def test_backtest_is_deterministic(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    cutoff = _cutoff(dataset.window, 0.66)
    first = backtest_calibration(dataset=dataset, company=company, cutoff=cutoff)
    second = backtest_calibration(dataset=dataset, company=company, cutoff=cutoff)
    assert first.digest == second.digest


def test_seasonality_index_parameters_are_not_backtested(
    company: CompanyModel,
) -> None:
    dataset = generate_northstar_history(company)
    cutoff = _cutoff(dataset.window, 0.66)
    result = backtest_calibration(dataset=dataset, company=company, cutoff=cutoff)
    # A well-fit demand forecast should keep wMAPE well below a naive 1.0, which
    # would be the signature of an index parameter leaking into the backtest.
    demand = [kpi for kpi in result.kpis if kpi.series == "demand_units"]
    assert demand
    assert all(kpi.weighted_mape < 0.5 for kpi in demand)


def test_rolling_backtest_runs_each_cutoff(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    cutoffs = (
        _cutoff(dataset.window, 0.5),
        _cutoff(dataset.window, 0.65),
        _cutoff(dataset.window, 0.8),
    )
    results = backtest_rolling(dataset=dataset, company=company, cutoffs=cutoffs)
    assert len(results) == 3
    assert [r.in_sample_end for r in results] == sorted(cutoffs)
