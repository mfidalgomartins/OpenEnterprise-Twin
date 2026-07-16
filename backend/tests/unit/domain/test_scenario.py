from decimal import Decimal

import pytest
from pydantic import ValidationError
from tests.factories import build_northstar_company

from openenterprise_twin.domain.company import CompanyModel
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.scenario import (
    MaterialPolicyChange,
    PolicyLevers,
    ResourcePolicyChange,
    Scenario,
    SegmentPaymentTermChange,
    SegmentProductPriceChange,
    validate_scenario_against_company,
)


def test_policy_levers_reject_impossible_rates() -> None:
    with pytest.raises(ValidationError):
        SegmentProductPriceChange(
            segment_id="contracted",
            product_id="standard-valve",
            price_change=Decimal("-1.01"),
        )


def test_policy_levers_bound_commercial_investment_change() -> None:
    with pytest.raises(ValidationError):
        PolicyLevers(commercial_investment_change=Decimal("10.01"))


def test_policy_levers_reject_duplicate_resource_changes() -> None:
    change = ResourcePolicyChange(
        resource_id="test",
        regular_capacity_change=Decimal("0"),
        overtime_capacity_minutes=240,
    )
    with pytest.raises(ValidationError, match="resource changes must be unique"):
        PolicyLevers(resource_changes=(change, change))


def test_scenario_validation_rejects_unknown_policy_target() -> None:
    scenario = build_scenario(
        PolicyLevers(
            price_changes=(
                SegmentProductPriceChange(
                    segment_id="contracted",
                    product_id="unknown-product",
                    price_change=Decimal("0.05"),
                ),
            )
        )
    )

    with pytest.raises(DomainValidationError, match="unknown product"):
        validate_scenario_against_company(scenario, build_northstar_company())


def test_scenario_accepts_scoped_enterprise_policy() -> None:
    policy = PolicyLevers(
        price_changes=(
            SegmentProductPriceChange(
                segment_id="contracted",
                product_id="standard-valve",
                price_change=Decimal("0.025"),
            ),
        ),
        resource_changes=(
            ResourcePolicyChange(
                resource_id="test",
                regular_capacity_change=Decimal("0"),
                overtime_capacity_minutes=240,
            ),
        ),
        material_changes=(
            MaterialPolicyChange(
                material_id="electronics",
                safety_stock_coverage_days=Decimal("5"),
                supplier_lead_time_improvement=Decimal("0.20"),
                supplier_unit_cost_change=Decimal("0.10"),
            ),
        ),
        payment_term_changes=(
            SegmentPaymentTermChange(segment_id="contracted", change_days=-15),
        ),
        one_off_capital_investment_cents=12_000_000,
    )
    scenario = build_scenario(policy)

    validate_scenario_against_company(scenario, build_northstar_company())
    assert scenario.policy_levers.resource_changes[0].resource_id == "test"


def test_scenario_rejects_overtime_above_resource_cap() -> None:
    scenario = build_scenario(
        PolicyLevers(
            resource_changes=(
                ResourcePolicyChange(
                    resource_id="test",
                    overtime_capacity_minutes=241,
                ),
            )
        )
    )

    with pytest.raises(DomainValidationError, match="overtime cap"):
        validate_scenario_against_company(scenario, build_northstar_company())


def test_scenario_rejects_effective_payment_terms_outside_bounds() -> None:
    scenario = build_scenario(
        PolicyLevers(
            payment_term_changes=(
                SegmentPaymentTermChange(segment_id="contracted", change_days=-46),
            )
        )
    )

    with pytest.raises(DomainValidationError, match="payment terms"):
        validate_scenario_against_company(scenario, build_northstar_company())


def test_scenario_rejects_price_change_without_demand_profile() -> None:
    company_payload = build_northstar_company().model_dump()
    company_payload["products"][0]["demand_profiles"] = company_payload["products"][0][
        "demand_profiles"
    ][:1]
    company = CompanyModel.model_validate(company_payload)
    scenario = build_scenario(
        PolicyLevers(
            price_changes=(
                SegmentProductPriceChange(
                    segment_id="spot",
                    product_id="standard-valve",
                    price_change=Decimal("0.05"),
                ),
            )
        )
    )

    with pytest.raises(DomainValidationError, match="no demand profile"):
        validate_scenario_against_company(scenario, company)


def test_scenario_rejects_nonpositive_effective_price() -> None:
    scenario = build_scenario(
        PolicyLevers(
            price_changes=(
                SegmentProductPriceChange(
                    segment_id="contracted",
                    product_id="standard-valve",
                    price_change=Decimal("-1"),
                ),
            )
        )
    )

    with pytest.raises(DomainValidationError, match="positive price"):
        validate_scenario_against_company(scenario, build_northstar_company())


def test_scenario_rejects_nonpositive_effective_supplier_cost() -> None:
    scenario = build_scenario(
        PolicyLevers(
            material_changes=(
                MaterialPolicyChange(
                    material_id="steel",
                    supplier_unit_cost_change=Decimal("-1"),
                ),
            )
        )
    )

    with pytest.raises(DomainValidationError, match="positive supplier cost"):
        validate_scenario_against_company(scenario, build_northstar_company())


def test_scenario_rejects_itself_as_baseline() -> None:
    with pytest.raises(ValidationError, match="cannot reference itself"):
        Scenario(
            scenario_id="pricing-test",
            name="Pricing test",
            company_model_version="0.1.0",
            schema_version="0.1.0",
            horizon_days=515,
            baseline_scenario_id="pricing-test",
        )


def test_scenario_is_immutable() -> None:
    scenario = build_scenario(PolicyLevers())

    with pytest.raises(ValidationError):
        scenario.horizon_days = 364  # type: ignore[misc]


def build_scenario(policy_levers: PolicyLevers) -> Scenario:
    return Scenario(
        scenario_id="balanced-policy",
        name="Balanced policy",
        company_model_version="0.1.0",
        schema_version="0.1.0",
        horizon_days=515,
        baseline_scenario_id="current-plan",
        policy_levers=policy_levers,
    )
