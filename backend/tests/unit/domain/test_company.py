from copy import deepcopy

import pytest
from pydantic import ValidationError
from tests.factories import build_northstar_company

from openenterprise_twin.domain.company import CompanyModel


@pytest.fixture
def northstar_company() -> CompanyModel:
    return build_northstar_company()


def test_company_accepts_integer_operating_units(
    northstar_company: CompanyModel,
) -> None:
    assert northstar_company.products[0].standard_price_cents == 12000
    assert northstar_company.products[0].opening_finished_goods_units == 220
    assert northstar_company.plant.resources[1].daily_capacity_minutes == 1050
    assert len(northstar_company.plant.materials) == 2
    assert northstar_company.customer_segments[0].promised_lead_time_days == 3


def test_company_rejects_fractional_money_cents(
    northstar_company: CompanyModel,
) -> None:
    payload = northstar_company.model_dump()
    payload["products"][0]["standard_price_cents"] = 12000.5

    with pytest.raises(ValidationError):
        CompanyModel.model_validate(payload)


def test_company_rejects_non_positive_capacity(
    northstar_company: CompanyModel,
) -> None:
    payload = northstar_company.model_dump()
    payload["plant"]["resources"][0]["daily_capacity_minutes"] = 0

    with pytest.raises(ValidationError):
        CompanyModel.model_validate(payload)


def test_company_rejects_demand_profile_for_unknown_segment(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["products"][0]["demand_profiles"][0]["segment_id"] = "unknown"

    with pytest.raises(ValidationError, match="unknown customer segment"):
        CompanyModel.model_validate(payload)


def test_company_rejects_product_requirement_for_unknown_resource(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["products"][0]["resource_requirements"][0]["resource_id"] = "paint"

    with pytest.raises(ValidationError, match="unknown resource"):
        CompanyModel.model_validate(payload)


def test_company_rejects_product_requirement_for_unknown_material(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["products"][0]["material_requirements"][0]["material_id"] = "copper"

    with pytest.raises(ValidationError, match="unknown material"):
        CompanyModel.model_validate(payload)


def test_company_rejects_duplicate_resource_identifiers(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["plant"]["resources"][1]["resource_id"] = "assembly"

    with pytest.raises(ValidationError, match="unique identifiers"):
        CompanyModel.model_validate(payload)


def test_financial_policy_requires_cash_target_above_floor(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["financial_policy"]["cash_target_cents"] = 4_000_000

    with pytest.raises(ValidationError, match="cash_target"):
        CompanyModel.model_validate(payload)


def test_company_models_are_frozen_and_forbid_unknown_fields(
    northstar_company: CompanyModel,
) -> None:
    with pytest.raises(ValidationError):
        northstar_company.name = "Other company"

    with pytest.raises(ValidationError):
        CompanyModel.model_validate({**northstar_company.model_dump(), "unknown": True})


def test_reference_company_defines_auditable_decision_materiality_rules(
    northstar_company: CompanyModel,
) -> None:
    rules = northstar_company.decision_policy.metric_rules

    assert {rule.metric_name for rule in rules} == {
        "revenue",
        "ebitda",
        "free_cash_flow",
        "closing_cash",
        "otif",
        "cancellation_rate",
        "backlog_units",
        "capacity_utilization",
        "peak_revolver",
        "rescue_funding",
    }
    assert all(rule.materiality_threshold >= 0 for rule in rules)
