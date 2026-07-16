"""Production-quality synthetic Northstar Components reference model."""

from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.scenario import PolicyLevers, Scenario


def build_northstar_company() -> CompanyModel:
    """Return the versioned reference company used by demos and tests."""

    return CompanyModel.model_validate(
        {
            "company_id": "northstar-components",
            "name": "Northstar Components",
            "model_version": "0.1.0",
            "products": [
                _product(
                    product_id="standard-valve",
                    name="Standard valve",
                    price_cents=12000,
                    cost_cents=4700,
                    opening_units=220,
                    yield_rate="0.990",
                    assembly_minutes=21,
                    test_minutes=5,
                    steel_units=2000,
                    electronics_units=0,
                    contracted_demand=36,
                    spot_demand=19,
                    contracted_elasticity="-0.55",
                    spot_elasticity="-1.40",
                ),
                _product(
                    product_id="intelligent-valve",
                    name="Intelligent valve",
                    price_cents=24000,
                    cost_cents=10825,
                    opening_units=110,
                    yield_rate="0.970",
                    assembly_minutes=39,
                    test_minutes=21,
                    steel_units=2500,
                    electronics_units=1,
                    contracted_demand=16,
                    spot_demand=8,
                    contracted_elasticity="-0.45",
                    spot_elasticity="-1.20",
                ),
                _product(
                    product_id="repair-cartridge",
                    name="Repair cartridge",
                    price_cents=6500,
                    cost_cents=1990,
                    opening_units=340,
                    yield_rate="0.995",
                    assembly_minutes=11,
                    test_minutes=2,
                    steel_units=800,
                    electronics_units=0,
                    contracted_demand=55,
                    spot_demand=30,
                    contracted_elasticity="-0.35",
                    spot_elasticity="-1.60",
                ),
            ],
            "customer_segments": [
                {
                    "segment_id": "contracted",
                    "name": "Contracted accounts",
                    "discount_rate": "0.08",
                    "churn_probability": "0.02",
                    "cancellation_probability": "0.03",
                    "order_dispersion": "25",
                    "mean_order_size": 8,
                    "promised_lead_time_days": 3,
                    "cancellation_grace_days": 5,
                    "payment_terms_days": 45,
                    "service_reputation_sensitivity": "0.12",
                },
                {
                    "segment_id": "spot",
                    "name": "Spot buyers",
                    "discount_rate": "0",
                    "churn_probability": "0.10",
                    "cancellation_probability": "0.12",
                    "order_dispersion": "10",
                    "mean_order_size": 3,
                    "promised_lead_time_days": 1,
                    "cancellation_grace_days": 2,
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
                        "opening_inventory_base_units": 4_000_000,
                        "reorder_point_base_units": 2_200_000,
                        "unit_cost_milli_cents": 600,
                        "supplier_lead_time_days": 8,
                        "supplier_payment_terms_days": 45,
                    },
                    {
                        "material_id": "electronics",
                        "name": "Electronics module",
                        "opening_inventory_base_units": 500,
                        "reorder_point_base_units": 150,
                        "unit_cost_milli_cents": 3_500_000,
                        "supplier_lead_time_days": 12,
                        "supplier_payment_terms_days": 30,
                    },
                ],
            },
            "financial_policy": {
                "opening_cash_cents": 35_000_000,
                "liquidity_floor_cents": 5_000_000,
                "cash_target_cents": 10_000_000,
                "monthly_fixed_cost_cents": 17_000_000,
                "annual_interest_rate": "0.08",
                "revolver_limit_cents": 50_000_000,
            },
        }
    )


def build_baseline_scenario(*, horizon_days: int = 515) -> Scenario:
    if horizon_days == 515:
        warmup_days, evaluation_days, runoff_days = 91, 364, 60
    else:
        warmup_days, evaluation_days, runoff_days = 0, horizon_days, 0
    return Scenario(
        scenario_id="current-plan",
        name="Current plan",
        company_model_version="0.1.0",
        schema_version="0.1.0",
        horizon_days=horizon_days,
        warmup_days=warmup_days,
        evaluation_days=evaluation_days,
        runoff_days=runoff_days,
        policy_levers=PolicyLevers(),
    )


def _product(
    *,
    product_id: str,
    name: str,
    price_cents: int,
    cost_cents: int,
    opening_units: int,
    yield_rate: str,
    assembly_minutes: int,
    test_minutes: int,
    steel_units: int,
    electronics_units: int,
    contracted_demand: int,
    spot_demand: int,
    contracted_elasticity: str,
    spot_elasticity: str,
) -> dict[str, object]:
    materials: list[dict[str, object]] = [
        {"material_id": "steel", "base_units_per_unit": steel_units}
    ]
    if electronics_units:
        materials.append(
            {
                "material_id": "electronics",
                "base_units_per_unit": electronics_units,
            }
        )
    return {
        "product_id": product_id,
        "name": name,
        "standard_price_cents": price_cents,
        "standard_unit_cost_cents": cost_cents,
        "opening_finished_goods_units": opening_units,
        "production_lead_time_days": 1,
        "yield_rate": yield_rate,
        "resource_requirements": [
            {"resource_id": "assembly", "minutes_per_unit": assembly_minutes},
            {"resource_id": "test", "minutes_per_unit": test_minutes},
        ],
        "material_requirements": materials,
        "demand_profiles": [
            {
                "segment_id": "contracted",
                "daily_baseline_units": contracted_demand,
                "price_elasticity": contracted_elasticity,
                "seasonality_amplitude": "0.10",
                "commercial_investment_sensitivity": "0.15",
            },
            {
                "segment_id": "spot",
                "daily_baseline_units": spot_demand,
                "price_elasticity": spot_elasticity,
                "seasonality_amplitude": "0.18",
                "commercial_investment_sensitivity": "0.24",
            },
        ],
    }
