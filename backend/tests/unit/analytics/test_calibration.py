from datetime import date

import pytest
from tests.factories import build_northstar_company

from openenterprise_twin.analytics.calibration import (
    calibrate_twin,
    compare_calibrations,
)
from openenterprise_twin.analytics.synthetic import generate_northstar_history
from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import DomainValidationError


@pytest.fixture
def company() -> CompanyModel:
    return build_northstar_company()


def test_calibration_is_deterministic(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    first = calibrate_twin(calibration_id="c", dataset=dataset, company=company)
    second = calibrate_twin(calibration_id="c", dataset=dataset, company=company)
    assert first.digest == second.digest


def test_observed_parameters_carry_confidence_intervals(
    company: CompanyModel,
) -> None:
    dataset = generate_northstar_history(company)
    calibration = calibrate_twin(
        calibration_id="c", dataset=dataset, company=company
    )
    demand = calibration.parameter("demand_baseline:standard-valve")
    assert demand is not None
    assert demand.provenance == "observed"
    assert demand.confidence_interval is not None
    assert demand.confidence_interval.lower < demand.point_estimate
    assert demand.point_estimate < demand.confidence_interval.upper


def test_missing_series_falls_back_to_assumed(company: CompanyModel) -> None:
    # A dataset that only contains demand leaves prices/costs uncalibrated,
    # so those parameters must be flagged as assumed -- never silently invented.
    dataset = generate_northstar_history(company)
    demand_only = tuple(
        item for item in dataset.observations if item.series == "demand_units"
    )
    from openenterprise_twin.analytics.history import build_dataset

    trimmed = build_dataset(
        dataset_id="trimmed",
        company_id=company.company_id,
        observations=demand_only,
        source_kind="inline",
        source_reference="unit-test",
    )
    calibration = calibrate_twin(
        calibration_id="c", dataset=trimmed, company=company
    )
    price = calibration.parameter("unit_price:standard-valve")
    assert price is not None
    assert price.provenance == "assumed"
    assert price.point_estimate == float(company.products[0].standard_price_cents)
    assert calibration.provenance_mix["assumed"] > 0


def test_dominant_weekly_seasonality_is_detected(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    calibration = calibrate_twin(
        calibration_id="c", dataset=dataset, company=company
    )
    demand_seasonality = [
        item for item in calibration.seasonality if item.series == "demand_units"
    ]
    assert demand_seasonality
    assert all(item.period == "weekly" for item in demand_seasonality)
    assert all(item.amplitude > 0.1 for item in demand_seasonality)


def test_compare_calibrations_reports_deltas(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company, seed=1)
    other = generate_northstar_history(company, seed=2)
    base = calibrate_twin(calibration_id="base", dataset=dataset, company=company)
    cand = calibrate_twin(calibration_id="cand", dataset=other, company=company)
    comparison = compare_calibrations(base, cand)
    assert comparison.deltas
    assert comparison.max_relative_change >= 0.0
    named = {delta.name: delta for delta in comparison.deltas}
    demand = named["demand_baseline:standard-valve"]
    assert demand.baseline_estimate is not None
    assert demand.candidate_estimate is not None


def test_calibration_rejects_mismatched_company(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    other = company.model_copy(update={"company_id": "other-co"})
    with pytest.raises(DomainValidationError):
        calibrate_twin(calibration_id="c", dataset=dataset, company=other)


def test_calibration_window_is_respected(company: CompanyModel) -> None:
    dataset = generate_northstar_history(company)
    start, end = dataset.window
    mid = date.fromordinal((start.toordinal() + end.toordinal()) // 2)
    calibration = calibrate_twin(
        calibration_id="c",
        dataset=dataset,
        company=company,
        window=(start, mid),
    )
    assert calibration.window_start == start
    assert calibration.window_end == mid
