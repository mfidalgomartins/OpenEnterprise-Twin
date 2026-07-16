"""Validated model factories shared by backend tests."""

from openenterprise_twin.domain.company import CompanyModel


def build_northstar_company() -> CompanyModel:
    return CompanyModel.model_validate(
        {
            "company_id": "northstar-components",
            "name": "Northstar Components",
            "model_version": "0.1.0",
            "products": [
                {
                    "product_id": "standard-valve",
                    "name": "Standard valve",
                    "standard_price_cents": 12000,
                    "standard_unit_cost_cents": 7200,
                    "yield_rate": "0.99",
                    "resource_requirements": [
                        {"resource_id": "assembly", "minutes_per_unit": 21},
                        {"resource_id": "test", "minutes_per_unit": 5},
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
                    "payment_terms_days": 15,
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
                        "overtime_cost_cents_per_minute": 71,
                    },
                    {
                        "resource_id": "test",
                        "daily_capacity_minutes": 1050,
                        "max_overtime_minutes": 240,
                        "overtime_cost_cents_per_minute": 85,
                    },
                ],
                "materials": [
                    {
                        "material_id": "steel",
                        "name": "Steel",
                        "opening_inventory_base_units": 650000,
                        "reorder_point_base_units": 180000,
                        "unit_cost_milli_cents": 600,
                        "supplier_lead_time_days": 8,
                        "supplier_payment_terms_days": 45,
                    },
                    {
                        "material_id": "electronics",
                        "name": "Electronics module",
                        "opening_inventory_base_units": 900,
                        "reorder_point_base_units": 250,
                        "unit_cost_milli_cents": 35000,
                        "supplier_lead_time_days": 12,
                        "supplier_payment_terms_days": 30,
                    },
                ],
            },
            "financial_policy": {
                "opening_cash_cents": 65_000_000,
                "liquidity_floor_cents": 15_000_000,
                "cash_target_cents": 20_000_000,
                "monthly_fixed_cost_cents": 17_000_000,
                "annual_interest_rate": "0.075",
                "revolver_limit_cents": 30_000_000,
            },
        }
    )
