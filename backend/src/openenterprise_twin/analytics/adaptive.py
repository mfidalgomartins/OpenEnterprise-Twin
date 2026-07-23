"""A safe declarative DSL for state-dependent (adaptive) policies.

Adaptive policies let a decision respond to how the business actually behaves:
"if backlog exceeds 12 days, add capacity" or "if OTIF drops below 94%, activate
alternative sourcing". The language is intentionally tiny and *closed*: metrics,
operators and actions are all allow-listed, there is no expression evaluation and
no ``eval`` anywhere, and every rule is schema-validated. The controller is
deterministic -- the same observation stream always yields the same activations,
in a fixed priority order -- and produces a complete audit trail.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from openenterprise_twin.domain.company import (
    CompanyModel,
    DomainModel,
    Identifier,
    ResourceCapacity,
)
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.results import PeriodResult, SimulationTrace
from openenterprise_twin.domain.scenario import (
    PolicyLevers,
    ResourcePolicyChange,
    Scenario,
    SegmentProductPriceChange,
)

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]

#: Metrics an adaptive rule may observe. Nothing else can be referenced.
AdaptiveMetric = Literal[
    "backlog_days",
    "otif",
    "demand_change",
    "revolver_debt_cents",
    "capacity_utilization",
    "closing_cash_cents",
]

#: The only comparison operators permitted in the DSL.
AdaptiveOperator = Literal["gt", "gte", "lt", "lte"]

#: Actions an adaptive rule may take. Each maps to a bounded lever change.
AdaptiveActionType = Literal[
    "add_overtime_capacity",
    "increase_commercial_investment",
    "reduce_commercial_investment",
    "activate_alternative_sourcing",
    "raise_prices",
    "cut_prices",
]

#: Pairs of actions that directly oppose one another for contradiction checks.
_OPPOSING_ACTIONS: frozenset[frozenset[AdaptiveActionType]] = frozenset(
    {
        frozenset({"increase_commercial_investment", "reduce_commercial_investment"}),
        frozenset({"raise_prices", "cut_prices"}),
    }
)

_INCREASING_OPERATORS: frozenset[AdaptiveOperator] = frozenset({"gt", "gte"})


class AdaptiveAction(DomainModel):
    """A bounded, allow-listed action taken when a rule activates."""

    type: AdaptiveActionType
    #: Optional target entity (e.g. a resource for overtime capacity).
    target_id: Identifier | None = None
    #: A bounded magnitude; interpretation depends on the action type.
    magnitude: Annotated[Decimal, Field(ge=Decimal("0"), le=Decimal("2"))]

    @model_validator(mode="after")
    def validate_target(self) -> AdaptiveAction:
        if self.type == "add_overtime_capacity" and self.target_id is None:
            raise DomainValidationError(
                "add_overtime_capacity requires a resource target_id"
            )
        return self


class AdaptiveRule(DomainModel):
    """One condition-action rule with temporal and rate-limiting semantics."""

    rule_id: Identifier
    metric: AdaptiveMetric
    operator: AdaptiveOperator
    threshold: FiniteFloat
    #: Trailing window (in observed periods) the condition is averaged over.
    window_periods: Annotated[int, Field(ge=1, le=180)] = 1
    #: Consecutive windows the condition must hold before the rule may fire.
    persistence_periods: Annotated[int, Field(ge=1, le=180)] = 1
    #: Minimum periods between two activations of the same rule.
    cooldown_periods: Annotated[int, Field(ge=0, le=365)] = 0
    max_activations: Annotated[int, Field(ge=1, le=365)] = 1
    #: Higher priority rules are recorded first when several fire together.
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    action: AdaptiveAction
    action_cost_cents: Annotated[int, Field(ge=0)] = 0

    def region_overlaps(self, other: AdaptiveRule) -> bool:
        """Return whether two same-metric rules can fire for a shared value."""

        if self.metric != other.metric:
            return False
        return _intervals_overlap(
            self.operator, self.threshold, other.operator, other.threshold
        )


class AdaptivePolicy(DomainModel):
    """A validated set of adaptive rules with contradiction detection."""

    policy_id: Identifier
    rules: Annotated[tuple[AdaptiveRule, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def validate_policy(self) -> AdaptivePolicy:
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise DomainValidationError("adaptive rule identifiers must be unique")
        for index, rule in enumerate(self.rules):
            for other in self.rules[index + 1 :]:
                if _actions_oppose(rule.action.type, other.action.type) and (
                    rule.region_overlaps(other)
                ):
                    raise DomainValidationError(
                        f"rules '{rule.rule_id}' and '{other.rule_id}' are "
                        "contradictory: opposing actions with overlapping triggers"
                    )
        return self


class PeriodObservation(DomainModel):
    """The adaptive metric vector observed for one simulated period."""

    period_index: Annotated[int, Field(ge=0)]
    period_date: date
    values: dict[AdaptiveMetric, FiniteFloat]


class Activation(DomainModel):
    """One audited firing of an adaptive rule."""

    rule_id: Identifier
    period_index: Annotated[int, Field(ge=0)]
    period_date: date
    metric: AdaptiveMetric
    observed_value: FiniteFloat
    threshold: FiniteFloat
    action_type: AdaptiveActionType
    action_cost_cents: Annotated[int, Field(ge=0)]


class RuleActivationSummary(DomainModel):
    """Frequency and cost roll-up for one rule."""

    rule_id: Identifier
    activation_count: Annotated[int, Field(ge=0)]
    total_cost_cents: Annotated[int, Field(ge=0)]


class AdaptiveEvaluation(DomainModel):
    """The deterministic result of evaluating a policy over an observation stream."""

    policy_id: Identifier
    observed_periods: Annotated[int, Field(ge=0)]
    activations: tuple[Activation, ...]
    rule_summaries: tuple[RuleActivationSummary, ...]
    total_action_cost_cents: Annotated[int, Field(ge=0)]


def evaluate_adaptive_policy(
    policy: AdaptivePolicy,
    observations: tuple[PeriodObservation, ...],
) -> AdaptiveEvaluation:
    """Evaluate a policy over an ordered observation stream, deterministically."""

    ordered_obs = tuple(sorted(observations, key=lambda o: o.period_index))
    consecutive: dict[str, int] = {rule.rule_id: 0 for rule in policy.rules}
    last_fired: dict[str, int | None] = {rule.rule_id: None for rule in policy.rules}
    counts: dict[str, int] = {rule.rule_id: 0 for rule in policy.rules}
    costs: dict[str, int] = {rule.rule_id: 0 for rule in policy.rules}
    activations: list[Activation] = []

    for position, observation in enumerate(ordered_obs):
        firing: list[Activation] = []
        for rule in policy.rules:
            value = _window_mean(policy, ordered_obs, position, rule)
            if value is None:
                consecutive[rule.rule_id] = 0
                continue
            if _condition_holds(rule.operator, value, rule.threshold):
                consecutive[rule.rule_id] += 1
            else:
                consecutive[rule.rule_id] = 0
                continue
            if not _may_fire(rule, consecutive, last_fired, counts, position):
                continue
            firing.append(
                Activation(
                    rule_id=rule.rule_id,
                    period_index=observation.period_index,
                    period_date=observation.period_date,
                    metric=rule.metric,
                    observed_value=round(value, 6),
                    threshold=rule.threshold,
                    action_type=rule.action.type,
                    action_cost_cents=rule.action_cost_cents,
                )
            )
        for activation in sorted(
            firing,
            key=lambda item: (-_priority(policy, item.rule_id), item.rule_id),
        ):
            activations.append(activation)
            counts[activation.rule_id] += 1
            costs[activation.rule_id] += activation.action_cost_cents
            last_fired[activation.rule_id] = position

    summaries = tuple(
        RuleActivationSummary(
            rule_id=rule.rule_id,
            activation_count=counts[rule.rule_id],
            total_cost_cents=costs[rule.rule_id],
        )
        for rule in policy.rules
    )
    return AdaptiveEvaluation(
        policy_id=policy.policy_id,
        observed_periods=len(ordered_obs),
        activations=tuple(activations),
        rule_summaries=summaries,
        total_action_cost_cents=sum(costs.values()),
    )


class AdaptiveComparison(DomainModel):
    """Paired comparison of an adaptive policy against the static plan."""

    policy_id: Identifier
    static_scenario_id: Identifier
    adaptive_scenario_id: Identifier
    replications: Annotated[int, Field(gt=0)]
    master_seed: Annotated[int, Field(ge=0)]
    metric_deltas: dict[str, FiniteFloat]
    activation_count: Annotated[int, Field(ge=0)]
    total_action_cost_cents: Annotated[int, Field(ge=0)]


def compare_adaptive_vs_static(
    *,
    company: CompanyModel,
    static_scenario: Scenario,
    policy: AdaptivePolicy,
    master_seed: int,
    replications: int,
    adaptive_scenario_id: str = "adaptive-candidate",
    adaptive_scenario_name: str = "Adaptive policy",
) -> AdaptiveComparison:
    """Run static and adaptive plans over identical shock tapes and compare.

    Activations are detected on the static plan's first replication trace; the
    resulting adaptive scenario is then evaluated across every paired
    replication with the same master seed, so both plans face identical demand,
    yield and lead-time shocks.
    """

    from openenterprise_twin.simulation.experiment import (
        ExperimentRequest,
        run_experiment,
        run_experiment_with_traces,
    )

    static_artifact = run_experiment_with_traces(
        ExperimentRequest(
            company=company,
            scenario=static_scenario,
            master_seed=master_seed,
            replications=replications,
        )
    )
    observations = observations_from_trace(static_artifact.traces[0])
    evaluation = evaluate_adaptive_policy(policy, observations)
    adaptive_scenario = derive_adaptive_scenario(
        base_scenario=static_scenario,
        company=company,
        evaluation=evaluation,
        policy=policy,
        adaptive_scenario_id=adaptive_scenario_id,
        adaptive_scenario_name=adaptive_scenario_name,
    )
    adaptive_result = run_experiment(
        ExperimentRequest(
            company=company,
            scenario=adaptive_scenario,
            master_seed=master_seed,
            replications=replications,
        )
    )
    static_metrics = static_artifact.result.metrics
    adaptive_metrics = adaptive_result.metrics
    deltas = {
        name: round(
            adaptive_metrics[name].mean - static_metrics[name].mean, 6
        )
        for name in static_metrics
    }
    return AdaptiveComparison(
        policy_id=policy.policy_id,
        static_scenario_id=static_scenario.scenario_id,
        adaptive_scenario_id=adaptive_scenario_id,
        replications=replications,
        master_seed=master_seed,
        metric_deltas=deltas,
        activation_count=len(evaluation.activations),
        total_action_cost_cents=evaluation.total_action_cost_cents,
    )


def observations_from_trace(
    trace: SimulationTrace,
    *,
    demand_reference_periods: int = 30,
) -> tuple[PeriodObservation, ...]:
    """Project a simulation trace into the adaptive metric space, per period."""

    periods = trace.periods
    reference = _reference_daily_demand(periods, demand_reference_periods)
    observations: list[PeriodObservation] = []
    for period in periods:
        new_orders = sum(period.new_orders_units.values())
        shipments = max(1, sum(period.shipments_units.values()))
        backlog_units = sum(period.closing_backlog_units.values())
        available = sum(period.capacity_available_minutes.values())
        used = sum(period.capacity_used_minutes.values())
        otif_orders = sum(period.otif_orders_count.values())
        fulfilled = sum(period.fulfilled_orders_count.values())
        values: dict[AdaptiveMetric, float] = {
            "backlog_days": backlog_units / shipments,
            "otif": otif_orders / fulfilled if fulfilled else 1.0,
            "demand_change": (
                (new_orders - reference) / reference if reference else 0.0
            ),
            "revolver_debt_cents": float(period.closing_revolver_debt_cents),
            "capacity_utilization": used / available if available else 0.0,
            "closing_cash_cents": float(period.closing_cash_cents),
        }
        observations.append(
            PeriodObservation(
                period_index=period.period_index,
                period_date=period.period_date,
                values=values,
            )
        )
    return tuple(observations)


def derive_adaptive_scenario(
    *,
    base_scenario: Scenario,
    company: CompanyModel,
    evaluation: AdaptiveEvaluation,
    policy: AdaptivePolicy,
    adaptive_scenario_id: str,
    adaptive_scenario_name: str,
) -> Scenario:
    """Fold fired adaptive actions into a concrete, comparable scenario.

    The engine applies levers over the whole horizon, so an adaptive action that
    fired during the run is committed as a policy adjustment for the paired
    comparison. This is a documented first-order model: activations are detected
    on the observed trajectory, then their aggregate effect is evaluated end to
    end against the same shock tape as the static plan.
    """

    action_by_rule = {rule.rule_id: rule.action for rule in policy.rules}
    fired_rule_ids = {activation.rule_id for activation in evaluation.activations}
    fired_actions = tuple(
        action_by_rule[rule_id] for rule_id in sorted(fired_rule_ids)
    )
    levers = _merge_levers(base_scenario.policy_levers, fired_actions, company)
    return base_scenario.model_copy(
        update={
            "scenario_id": adaptive_scenario_id,
            "name": adaptive_scenario_name,
            "policy_levers": levers,
        }
    )


def _merge_levers(
    base: PolicyLevers,
    actions: Iterable[AdaptiveAction],
    company: CompanyModel,
) -> PolicyLevers:
    commercial = base.commercial_investment_change
    resource_changes = {change.resource_id: change for change in base.resource_changes}
    price_delta = Decimal("0")
    for action in actions:
        if action.type == "increase_commercial_investment":
            commercial += action.magnitude
        elif action.type == "reduce_commercial_investment":
            commercial -= action.magnitude
        elif action.type == "raise_prices":
            price_delta += action.magnitude
        elif action.type == "cut_prices":
            price_delta -= action.magnitude
        elif action.type == "add_overtime_capacity" and action.target_id is not None:
            resource = _resource(company, action.target_id)
            minutes = min(
                resource.max_overtime_minutes,
                int(action.magnitude * resource.daily_capacity_minutes),
            )
            resource_changes[action.target_id] = ResourcePolicyChange(
                resource_id=action.target_id,
                overtime_capacity_minutes=minutes,
            )
        # ``activate_alternative_sourcing`` improves lead time only inside the
        # engine's sourcing model, which is out of scope for the global-lever
        # comparison and is captured in the activation audit instead.
    commercial = max(Decimal("-1"), min(Decimal("10"), commercial))
    price_changes = _apply_price_delta(base, company, price_delta)
    return base.model_copy(
        update={
            "commercial_investment_change": commercial,
            "resource_changes": tuple(resource_changes.values()),
            "price_changes": price_changes,
        }
    )


def _apply_price_delta(
    base: PolicyLevers, company: CompanyModel, price_delta: Decimal
) -> tuple[SegmentProductPriceChange, ...]:
    if price_delta == 0:
        return base.price_changes
    existing = {
        (change.segment_id, change.product_id): change
        for change in base.price_changes
    }
    for product in company.products:
        for profile in product.demand_profiles:
            key = (profile.segment_id, product.product_id)
            current = existing.get(key)
            base_rate = current.price_change if current else Decimal("0")
            new_rate = max(Decimal("-0.9"), min(Decimal("2"), base_rate + price_delta))
            existing[key] = SegmentProductPriceChange(
                segment_id=profile.segment_id,
                product_id=product.product_id,
                price_change=new_rate,
            )
    return tuple(existing.values())


def _resource(company: CompanyModel, resource_id: str) -> ResourceCapacity:
    for resource in company.plant.resources:
        if resource.resource_id == resource_id:
            return resource
    raise DomainValidationError(f"unknown resource '{resource_id}'")


def _reference_daily_demand(
    periods: tuple[PeriodResult, ...], count: int
) -> float:
    values = [
        sum(period.new_orders_units.values())
        for period in periods
        if period.phase != "runoff"
    ]
    window = values[:count]
    return sum(window) / len(window) if window else 0.0


def _window_mean(
    policy: AdaptivePolicy,
    observations: tuple[PeriodObservation, ...],
    position: int,
    rule: AdaptiveRule,
) -> float | None:
    start = position - rule.window_periods + 1
    if start < 0:
        return None
    window = observations[start : position + 1]
    values = [obs.values[rule.metric] for obs in window]
    return sum(values) / len(values)


def _condition_holds(
    operator: AdaptiveOperator, value: float, threshold: float
) -> bool:
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    return value <= threshold


def _may_fire(
    rule: AdaptiveRule,
    consecutive: dict[str, int],
    last_fired: dict[str, int | None],
    counts: dict[str, int],
    position: int,
) -> bool:
    if consecutive[rule.rule_id] < rule.persistence_periods:
        return False
    if counts[rule.rule_id] >= rule.max_activations:
        return False
    previous = last_fired[rule.rule_id]
    return previous is None or position - previous > rule.cooldown_periods


def _priority(policy: AdaptivePolicy, rule_id: str) -> int:
    for rule in policy.rules:
        if rule.rule_id == rule_id:
            return rule.priority
    return 0


def _actions_oppose(first: AdaptiveActionType, second: AdaptiveActionType) -> bool:
    return frozenset({first, second}) in _OPPOSING_ACTIONS


def _intervals_overlap(
    first_op: AdaptiveOperator,
    first_threshold: float,
    second_op: AdaptiveOperator,
    second_threshold: float,
) -> bool:
    first_increasing = first_op in _INCREASING_OPERATORS
    second_increasing = second_op in _INCREASING_OPERATORS
    if first_increasing == second_increasing:
        # Same direction: the looser threshold's region contains the tighter one.
        return True
    lower_threshold = first_threshold if first_increasing else second_threshold
    upper_threshold = second_threshold if first_increasing else first_threshold
    # Increasing region [lower, inf) overlaps decreasing region (-inf, upper]
    # when lower <= upper.
    return lower_threshold <= upper_threshold
