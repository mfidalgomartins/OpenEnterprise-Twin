"""Evidence-bound narrative clauses derived only from typed policy levers."""

from typing import Literal

from openenterprise_twin.domain.company import DomainModel
from openenterprise_twin.domain.scenario import PolicyLevers

MechanismId = Literal[
    "pricing",
    "commercial-investment",
    "capacity",
    "inventory-sourcing",
    "payment-terms",
    "capital-investment",
]


class MechanismNarrative(DomainModel):
    """A deterministic explanation backed by one or more scenario levers."""

    mechanism_id: MechanismId
    title: str
    detail: str


_METRIC_LABELS = {
    "revenue": "Revenue",
    "ebitda": "EBITDA",
    "free_cash_flow": "Free cash flow",
    "closing_cash": "Closing cash",
    "otif": "OTIF",
    "cancellation_rate": "Cancellation rate",
    "backlog_units": "Backlog units",
    "capacity_utilization": "Capacity utilization",
    "peak_revolver": "Peak revolver",
    "rescue_funding": "Rescue funding",
}


def build_mechanism_narratives(
    policy_levers: PolicyLevers,
) -> tuple[MechanismNarrative, ...]:
    """Translate configured levers into factual clauses in a stable order."""

    narratives: list[MechanismNarrative] = []
    if policy_levers.price_changes:
        rates = [float(change.price_change) for change in policy_levers.price_changes]
        narratives.append(
            MechanismNarrative(
                mechanism_id="pricing",
                title="Pricing",
                detail=(
                    f"{len(rates)} segment-product price change(s), ranging from "
                    f"{min(rates):.2%} to {max(rates):.2%}."
                ),
            )
        )
    if policy_levers.commercial_investment_change:
        narratives.append(
            MechanismNarrative(
                mechanism_id="commercial-investment",
                title="Commercial investment",
                detail=(
                    "Commercial investment changes by "
                    f"{float(policy_levers.commercial_investment_change):.2%}."
                ),
            )
        )
    if policy_levers.resource_changes:
        overtime = sum(
            change.overtime_capacity_minutes
            for change in policy_levers.resource_changes
        )
        narratives.append(
            MechanismNarrative(
                mechanism_id="capacity",
                title="Capacity",
                detail=(
                    f"{len(policy_levers.resource_changes)} resource policy change(s) "
                    f"with {overtime} configured overtime minute(s)."
                ),
            )
        )
    if policy_levers.material_changes:
        narratives.append(
            MechanismNarrative(
                mechanism_id="inventory-sourcing",
                title="Inventory and sourcing",
                detail=(
                    f"{len(policy_levers.material_changes)} material policy "
                    "change(s) covering stock, lead time or supplier cost."
                ),
            )
        )
    if policy_levers.payment_term_changes:
        narratives.append(
            MechanismNarrative(
                mechanism_id="payment-terms",
                title="Payment terms",
                detail=(
                    f"{len(policy_levers.payment_term_changes)} customer payment-term "
                    "change(s)."
                ),
            )
        )
    if policy_levers.one_off_capital_investment_cents:
        euros = policy_levers.one_off_capital_investment_cents / 100
        narratives.append(
            MechanismNarrative(
                mechanism_id="capital-investment",
                title="Capital investment",
                detail=f"One-off capital investment of €{euros:,.2f}.",
            )
        )
    return tuple(narratives)


def format_metric_value(metric_name: str, value: float) -> str:
    """Format metric evidence without changing its value or interpretation."""

    if metric_name in {"otif", "cancellation_rate", "capacity_utilization"}:
        return f"{value:.1%}"
    if metric_name in {
        "revenue",
        "ebitda",
        "free_cash_flow",
        "closing_cash",
        "peak_revolver",
        "rescue_funding",
    }:
        euros = value / 100
        sign = "-" if euros < 0 else ""
        return f"{sign}€{abs(euros):,.0f}"
    return f"{value:,.1f}"


def format_metric_label(metric_name: str) -> str:
    """Return the executive display label for a stable metric identifier."""

    return _METRIC_LABELS.get(metric_name, metric_name.replace("_", " ").capitalize())
