from decimal import Decimal

import pytest
from pydantic import ValidationError

from openenterprise_twin.domain.scenario import PolicyLevers, Scenario


def test_policy_levers_reject_impossible_rates() -> None:
    with pytest.raises(ValidationError):
        PolicyLevers(price_change=Decimal("-1.01"))


def test_policy_levers_reject_impossible_probabilities() -> None:
    with pytest.raises(ValidationError):
        PolicyLevers(supplier_lead_time_improvement=Decimal("1.01"))


def test_policy_levers_bound_commercial_investment_change() -> None:
    with pytest.raises(ValidationError):
        PolicyLevers(commercial_investment_change=Decimal("10.01"))


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


def test_scenario_is_immutable_and_uses_typed_policy_levers() -> None:
    scenario = Scenario(
        scenario_id="pricing-test",
        name="Pricing test",
        company_model_version="0.1.0",
        schema_version="0.1.0",
        horizon_days=515,
        policy_levers=PolicyLevers(price_change=Decimal("0.05")),
    )

    assert scenario.policy_levers.price_change == Decimal("0.05")

    with pytest.raises(ValidationError):
        scenario.horizon_days = 364  # type: ignore[misc]
