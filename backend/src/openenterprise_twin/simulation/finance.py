"""Cash and revolver policy with exact integer-cent reconciliation."""

from dataclasses import dataclass

from openenterprise_twin.domain.company import FinancialPolicy
from openenterprise_twin.domain.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class FinancingResult:
    closing_cash_cents: int
    closing_debt_cents: int
    draw_cents: int
    repayment_cents: int


def apply_financing(
    *,
    cash_before_financing_cents: int,
    opening_debt_cents: int,
    policy: FinancialPolicy,
) -> FinancingResult:
    """Maintain the liquidity floor, then repay excess cash above target."""

    available_facility = policy.revolver_limit_cents - opening_debt_cents
    required_draw = max(
        0, policy.liquidity_floor_cents - cash_before_financing_cents
    )
    draw = min(required_draw, available_facility)
    cash_after_draw = cash_before_financing_cents + draw
    debt_after_draw = opening_debt_cents + draw
    if cash_after_draw < 0:
        raise InvariantViolation(
            "liquidity_exhausted",
            "cash remains negative after the revolving facility is exhausted",
        )

    repayment = min(
        max(0, cash_after_draw - policy.cash_target_cents), debt_after_draw
    )
    return FinancingResult(
        closing_cash_cents=cash_after_draw - repayment,
        closing_debt_cents=debt_after_draw - repayment,
        draw_cents=draw,
        repayment_cents=repayment,
    )
