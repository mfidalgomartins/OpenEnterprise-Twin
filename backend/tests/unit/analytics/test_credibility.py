from datetime import date

import pytest
from tests.factories import build_northstar_company

from openenterprise_twin.analytics.backtesting import backtest_calibration
from openenterprise_twin.analytics.calibration import calibrate_twin
from openenterprise_twin.analytics.credibility import (
    CREDIBILITY_WEIGHTS,
    score_credibility,
)
from openenterprise_twin.analytics.quality import assess_data_quality
from openenterprise_twin.analytics.synthetic import generate_northstar_history
from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import DomainValidationError


@pytest.fixture
def company() -> CompanyModel:
    return build_northstar_company()


def _pipeline(company: CompanyModel):
    dataset = generate_northstar_history(company)
    quality = assess_data_quality(dataset)
    calibration = calibrate_twin(
        calibration_id="c", dataset=dataset, company=company
    )
    start, end = dataset.window
    span = end.toordinal() - start.toordinal()
    cutoff = date.fromordinal(start.toordinal() + span * 2 // 3)
    backtest = backtest_calibration(dataset=dataset, company=company, cutoff=cutoff)
    return dataset, quality, calibration, backtest


def test_weights_sum_to_one() -> None:
    assert abs(sum(CREDIBILITY_WEIGHTS.values()) - 1.0) < 1e-9


def test_score_is_exact_sum_of_contributions(company: CompanyModel) -> None:
    _, quality, calibration, backtest = _pipeline(company)
    score = score_credibility(
        calibration=calibration, quality=quality, backtests=(backtest,)
    )
    # The score is the sum of contributions, rounded to display precision (4dp);
    # the decomposition is fully transparent to that precision.
    reconstructed = 100.0 * sum(c.contribution for c in score.components)
    assert abs(score.score - reconstructed) < 1e-3


def test_northstar_is_decision_grade(company: CompanyModel) -> None:
    _, quality, calibration, backtest = _pipeline(company)
    score = score_credibility(
        calibration=calibration, quality=quality, backtests=(backtest,)
    )
    assert score.band == "decision_grade"
    assert score.score >= 80.0


def test_drift_lowers_the_score(company: CompanyModel) -> None:
    _, quality, calibration, backtest = _pipeline(company)
    calm = score_credibility(
        calibration=calibration, quality=quality, backtests=(backtest,)
    )
    drifting = score_credibility(
        calibration=calibration,
        quality=quality,
        backtests=(backtest,),
        recent_drift_severity=1.0,
    )
    assert drifting.score < calm.score


def test_no_backtest_zeroes_error_components(company: CompanyModel) -> None:
    _, quality, calibration, _ = _pipeline(company)
    score = score_credibility(
        calibration=calibration, quality=quality, backtests=()
    )
    backtest_error = score.component("backtest_error")
    interval_coverage = score.component("interval_coverage")
    assert backtest_error is not None and backtest_error.normalized == 0.0
    assert interval_coverage is not None and interval_coverage.normalized == 0.0


def test_mismatched_dataset_is_rejected(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    quality = assess_data_quality(dataset)
    other = generate_northstar_history(company, seed=999)
    calibration = calibrate_twin(
        calibration_id="c", dataset=other, company=company
    )
    with pytest.raises(DomainValidationError):
        score_credibility(
            calibration=calibration, quality=quality, backtests=()
        )
