import pytest

from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.simulation.finance import apply_financing
from openenterprise_twin.simulation.reference import build_northstar_company


def test_financing_rejects_unfunded_liquidity_floor_breach() -> None:
    policy = build_northstar_company().financial_policy.model_copy(
        update={
            "liquidity_floor_cents": 5_000_000,
            "revolver_limit_cents": 1_000_000,
        }
    )

    with pytest.raises(InvariantViolation, match="liquidity_floor_breach"):
        apply_financing(
            cash_before_financing_cents=2_000_000,
            opening_debt_cents=0,
            policy=policy,
        )


def test_financing_draws_exactly_to_the_floor() -> None:
    policy = build_northstar_company().financial_policy

    result = apply_financing(
        cash_before_financing_cents=4_250_000,
        opening_debt_cents=0,
        policy=policy,
    )

    assert result.draw_cents == 750_000
    assert result.closing_cash_cents == policy.liquidity_floor_cents


def test_financing_can_record_exact_external_rescue_funding() -> None:
    policy = build_northstar_company().financial_policy.model_copy(
        update={"revolver_limit_cents": 1_000_000}
    )

    result = apply_financing(
        cash_before_financing_cents=2_000_000,
        opening_debt_cents=0,
        policy=policy,
        allow_rescue_funding=True,
    )

    assert result.draw_cents == 1_000_000
    assert result.rescue_funding_cents == 2_000_000
    assert result.closing_cash_cents == policy.liquidity_floor_cents
