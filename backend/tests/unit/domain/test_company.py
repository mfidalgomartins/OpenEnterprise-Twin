from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from openenterprise_twin.domain.company import CompanyModel


@pytest.fixture
def northstar_company() -> CompanyModel:
    return CompanyModel.model_validate(
        {
            "company_id": "northstar-components",
            "name": "Northstar Components",
            "model_version": "0.1.0",
            "products": [
                {
                    "product_id": "standard-valve",
                    "name": "Standard valve",
                    "standard_price": "120.00",
                    "standard_unit_cost": "72.00",
                    "yield_rate": "0.99",
                    "resource_requirements": [
                        {"resource_id": "assembly", "minutes_per_unit": "21"},
                        {"resource_id": "test", "minutes_per_unit": "5"},
                    ],
                    "material_requirements": [
                        {"material_id": "steel", "base_units_per_unit": 2000}
                    ],
                    "demand_profiles": [
                        {
                            "segment_id": "contracted",
                            "daily_baseline_units": 24,
                            "price_elasticity": "-0.55",
                            "seasonality_amplitude": "0.10",
                            "commercial_investment_sensitivity": "0.15",
                        },
                        {
                            "segment_id": "spot",
                            "daily_baseline_units": 12,
                            "price_elasticity": "-1.40",
                            "seasonality_amplitude": "0.18",
                            "commercial_investment_sensitivity": "0.24",
                        },
                    ],
                }
            ],
            "customer_segments": [
                {
                    "segment_id": "contracted",
                    "name": "Contracted accounts",
                    "discount_rate": "0.08",
                    "churn_probability": "0.02",
                    "cancellation_probability": "0.03",
                    "order_dispersion": "4.5",
                    "payment_terms_days": 45,
                    "service_reputation_sensitivity": "0.12",
                },
                {
                    "segment_id": "spot",
                    "name": "Spot buyers",
                    "discount_rate": "0",
                    "churn_probability": "0.10",
                    "cancellation_probability": "0.12",
                    "order_dispersion": "2.2",
                    "payment_terms_days": 30,
                    "service_reputation_sensitivity": "0.35",
                },
            ],
            "plant": {
                "plant_id": "northstar-main",
                "name": "Northstar Assembly and Test",
                "resources": [
                    {
                        "resource_id": "assembly",
                        "daily_capacity_minutes": 3380,
                        "max_overtime_minutes": 480,
                        "overtime_cost_per_minute": "0.71",
                    },
                    {
                        "resource_id": "test",
                        "daily_capacity_minutes": 1050,
                        "max_overtime_minutes": 240,
                        "overtime_cost_per_minute": "0.85",
                    },
                ],
                "materials": [
                    {
                        "material_id": "steel",
                        "name": "Steel",
                        "opening_inventory_base_units": 650000,
                        "reorder_point_base_units": 180000,
                        "unit_cost_per_base_unit": "0.006",
                        "supplier_lead_time_days": 8,
                        "supplier_payment_terms_days": 45,
                    }
                ],
            },
            "financial_policy": {
                "opening_cash": "650000.00",
                "liquidity_floor": "150000.00",
                "monthly_fixed_cost": "170000.00",
                "receivable_terms_days": 45,
                "payable_terms_days": 30,
                "annual_interest_rate": "0.075",
                "revolver_limit": "300000.00",
                "cash_target": "200000.00",
            },
        }
    )


def test_company_accepts_daily_northstar_model(northstar_company: CompanyModel) -> None:
    assert northstar_company.products[0].standard_price == Decimal("120.00")
    assert northstar_company.plant.resources[1].daily_capacity_minutes == 1050


def test_company_rejects_non_positive_capacity(northstar_company: CompanyModel) -> None:
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


def test_company_models_are_frozen_and_forbid_unknown_fields(
    northstar_company: CompanyModel,
) -> None:
    with pytest.raises(ValidationError):
        northstar_company.name = "Other company"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        CompanyModel.model_validate({**northstar_company.model_dump(), "unknown": True})


def test_company_rejects_product_requirement_for_unknown_resource(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["products"][0]["resource_requirements"][0]["resource_id"] = "paint"

    with pytest.raises(ValidationError, match="unknown resource"):
        CompanyModel.model_validate(payload)


def test_financial_policy_requires_cash_target_above_floor(
    northstar_company: CompanyModel,
) -> None:
    payload = deepcopy(northstar_company.model_dump())
    payload["financial_policy"]["cash_target"] = Decimal("100000")

    with pytest.raises(ValidationError, match="cash_target"):
        CompanyModel.model_validate(payload)
