"""Deterministic, constrained multi-objective policy optimization (NSGA-II).

The optimizer searches the bounded policy-lever space over the existing Monte
Carlo engine to find the Pareto frontier of efficient policies under explicit
hard and soft constraints. It is fully reproducible: the same configuration and
seed always yield the same frontier, recommendation and digest. NSGA-II is used
for its transparent constraint-domination handling and its ability to expose
genuine trade-offs rather than collapsing them into a single "optimal" policy.

Evaluation is injected through the :class:`CandidateEvaluator` protocol so the
search is decoupled from the simulator: production wraps the deterministic
experiment engine, while tests can drive a fast closed-form evaluator.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal, Protocol

import numpy as np
from pydantic import Field, model_validator

from openenterprise_twin.domain.company import CompanyModel, DomainModel, Identifier
from openenterprise_twin.domain.errors import DomainValidationError
from openenterprise_twin.domain.scenario import (
    PolicyLevers,
    ResourcePolicyChange,
    Scenario,
    SegmentPaymentTermChange,
    SegmentProductPriceChange,
)
from openenterprise_twin.simulation.experiment import METRIC_NAMES, MetricName

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
UnitInterval = Annotated[float, Field(ge=0.0, le=1.0)]
Direction = Literal["maximize", "minimize"]
ConstraintOperator = Literal["gte", "lte"]
LeverKind = Literal[
    "commercial_investment", "price", "overtime", "payment_terms"
]


class ObjectiveSpec(DomainModel):
    """One optimization objective over a simulated metric."""

    metric_name: MetricName
    direction: Direction
    weight: Annotated[float, Field(gt=0.0)] = 1.0


class ConstraintSpec(DomainModel):
    """A hard or soft constraint on a simulated metric's mean."""

    metric_name: MetricName
    operator: ConstraintOperator
    bound: FiniteFloat
    kind: Literal["hard", "soft"] = "hard"
    penalty_weight: Annotated[float, Field(ge=0.0)] = 1.0

    def violation(self, value: float) -> float:
        if self.operator == "gte":
            return max(0.0, self.bound - value)
        return max(0.0, value - self.bound)


class LeverSpec(DomainModel):
    """A bounded decision variable mapped to a concrete policy lever."""

    lever_id: Identifier
    kind: LeverKind
    target_id: Identifier | None = None
    lower: FiniteFloat
    upper: FiniteFloat

    @model_validator(mode="after")
    def validate_bounds(self) -> LeverSpec:
        if self.upper <= self.lower:
            raise DomainValidationError("lever upper bound must exceed the lower")
        if self.kind in {"price", "overtime", "payment_terms"} and (
            self.target_id is None
        ):
            raise DomainValidationError(f"lever kind '{self.kind}' requires target_id")
        return self


class OptimizationConfig(DomainModel):
    """A validated, reproducible optimization request."""

    objectives: Annotated[tuple[ObjectiveSpec, ...], Field(min_length=1, max_length=6)]
    levers: Annotated[tuple[LeverSpec, ...], Field(min_length=1, max_length=12)]
    constraints: tuple[ConstraintSpec, ...] = ()
    population_size: Annotated[int, Field(ge=4, le=200)] = 16
    max_generations: Annotated[int, Field(ge=1, le=200)] = 10
    max_evaluations: Annotated[int, Field(ge=8, le=20_000)] = 240
    seed: Annotated[int, Field(ge=0)] = 20240115
    convergence_patience: Annotated[int, Field(ge=1, le=50)] = 4
    convergence_tolerance: Annotated[float, Field(ge=0.0)] = 1e-4

    @model_validator(mode="after")
    def validate_config(self) -> OptimizationConfig:
        lever_ids = [lever.lever_id for lever in self.levers]
        if len(lever_ids) != len(set(lever_ids)):
            raise DomainValidationError("lever identifiers must be unique")
        objective_metrics = [obj.metric_name for obj in self.objectives]
        if len(objective_metrics) != len(set(objective_metrics)):
            raise DomainValidationError("objectives must target unique metrics")
        if self.population_size % 2 != 0:
            raise DomainValidationError("population_size must be even")
        return self


class CandidateEvaluation(DomainModel):
    """Simulator output for one candidate policy."""

    means: dict[MetricName, FiniteFloat]
    breach_probability: dict[MetricName, UnitInterval]

    @model_validator(mode="after")
    def validate_metrics(self) -> CandidateEvaluation:
        missing = set(METRIC_NAMES) - set(self.means)
        if missing:
            raise DomainValidationError(
                f"evaluation is missing metrics: {sorted(missing)}"
            )
        return self


class CandidateEvaluator(Protocol):
    """Deterministic evaluation of a policy into simulated metrics."""

    def __call__(self, levers: PolicyLevers) -> CandidateEvaluation: ...


class PolicyCandidate(DomainModel):
    """One evaluated policy with its objectives, constraints and robustness."""

    candidate_id: Annotated[int, Field(ge=0)]
    levers: PolicyLevers
    objective_values: dict[str, FiniteFloat]
    constraint_values: dict[str, FiniteFloat]
    hard_violation: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    soft_penalty: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    feasible: bool
    robustness: UnitInterval
    weighted_score: FiniteFloat
    rank: Annotated[int, Field(ge=0)]
    exclusion_reason: str | None = None


class LeverSensitivity(DomainModel):
    """Local sensitivity of the weighted score to one lever, around the optimum."""

    lever_id: Identifier
    downward_score_delta: FiniteFloat
    upward_score_delta: FiniteFloat
    influence: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class ConvergencePoint(DomainModel):
    """Best feasible weighted score and frontier size at one generation."""

    generation: Annotated[int, Field(ge=0)]
    best_weighted_score: FiniteFloat
    frontier_size: Annotated[int, Field(ge=0)]


class OptimizationResult(DomainModel):
    """The reproducible outcome of one optimization run."""

    objectives: tuple[ObjectiveSpec, ...]
    frontier: tuple[PolicyCandidate, ...]
    recommended: PolicyCandidate | None
    dominated: tuple[PolicyCandidate, ...]
    infeasible: tuple[PolicyCandidate, ...]
    sensitivity: tuple[LeverSensitivity, ...]
    convergence: tuple[ConvergencePoint, ...]
    evaluations: Annotated[int, Field(ge=0)]
    converged: bool
    seed: Annotated[int, Field(ge=0)]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


@dataclass(slots=True)
class _Individual:
    genome: tuple[float, ...]
    evaluation: CandidateEvaluation
    objectives: tuple[float, ...]
    hard_violation: float
    soft_penalty: float
    weighted_score: float
    rank: int = 0
    crowding: float = 0.0
    candidate_id: int = 0


@dataclass(slots=True)
class _SearchState:
    evaluator: CandidateEvaluator
    config: OptimizationConfig
    company: CompanyModel
    base_scenario: Scenario
    cache: dict[tuple[float, ...], CandidateEvaluation] = field(default_factory=dict)
    evaluations: int = 0


def optimize_policies(
    *,
    config: OptimizationConfig,
    evaluator: CandidateEvaluator,
    company: CompanyModel,
    base_scenario: Scenario,
) -> OptimizationResult:
    """Run a deterministic constrained NSGA-II search and summarise the frontier."""

    rng = np.random.default_rng(config.seed)
    state = _SearchState(
        evaluator=evaluator,
        config=config,
        company=company,
        base_scenario=base_scenario,
    )
    dimension = len(config.levers)
    population = [
        _make_individual(state, tuple(rng.random(dimension)))
        for _ in range(config.population_size)
    ]
    _assign_fronts(population)
    convergence: list[ConvergencePoint] = []
    best_history: list[float] = []
    converged = False

    for generation in range(config.max_generations):
        offspring = _reproduce(state, population, rng)
        combined = population + offspring
        _assign_fronts(combined)
        population = _select_next_generation(combined, config.population_size)

        frontier = [ind for ind in population if ind.rank == 0 and _feasible(ind)]
        best = max((ind.weighted_score for ind in frontier), default=float("-inf"))
        convergence.append(
            ConvergencePoint(
                generation=generation,
                best_weighted_score=_finite(best),
                frontier_size=len(frontier),
            )
        )
        best_history.append(best)
        if _has_converged(best_history, config):
            converged = True
            break
        if state.evaluations >= config.max_evaluations:
            break

    return _build_result(state, population, convergence, converged, rng)


def build_simulation_evaluator(
    *,
    company: CompanyModel,
    base_scenario: Scenario,
    master_seed: int,
    replications: int,
) -> CandidateEvaluator:
    """Wrap the deterministic experiment engine as a candidate evaluator."""

    from openenterprise_twin.simulation.experiment import (
        ExperimentRequest,
        run_experiment,
    )

    def _evaluate(levers: PolicyLevers) -> CandidateEvaluation:
        scenario = base_scenario.model_copy(
            update={
                "scenario_id": "optimization-candidate",
                "name": "Optimization candidate",
                "policy_levers": levers,
            }
        )
        result = run_experiment(
            ExperimentRequest(
                company=company,
                scenario=scenario,
                master_seed=master_seed,
                replications=replications,
            )
        )
        metrics = result.metrics
        return CandidateEvaluation(
            means={name: metrics[name].mean for name in METRIC_NAMES},
            breach_probability={
                name: metrics[name].breach_probability for name in METRIC_NAMES
            },
        )

    return _evaluate


def decode_levers(
    config: OptimizationConfig,
    company: CompanyModel,
    base_scenario: Scenario,
    genome: tuple[float, ...],
) -> PolicyLevers:
    """Map a genome in the unit hypercube to a validated ``PolicyLevers``."""

    commercial = base_scenario.policy_levers.commercial_investment_change
    price_changes = list(base_scenario.policy_levers.price_changes)
    resource_changes = {
        change.resource_id: change
        for change in base_scenario.policy_levers.resource_changes
    }
    payment_changes = {
        change.segment_id: change
        for change in base_scenario.policy_levers.payment_term_changes
    }
    for lever, gene in zip(config.levers, genome, strict=True):
        value = lever.lower + gene * (lever.upper - lever.lower)
        if lever.kind == "commercial_investment":
            commercial = _decimal(value)
        elif lever.kind == "price" and lever.target_id is not None:
            price_changes.extend(_price_changes(company, lever.target_id, value))
        elif lever.kind == "overtime" and lever.target_id is not None:
            resource_changes[lever.target_id] = ResourcePolicyChange(
                resource_id=lever.target_id,
                overtime_capacity_minutes=_overtime_minutes(company, lever, value),
            )
        elif lever.kind == "payment_terms" and lever.target_id is not None:
            payment_changes[lever.target_id] = SegmentPaymentTermChange(
                segment_id=lever.target_id,
                change_days=round(value),
            )
    return PolicyLevers(
        price_changes=tuple(price_changes),
        commercial_investment_change=commercial,
        resource_changes=tuple(resource_changes.values()),
        payment_term_changes=tuple(payment_changes.values()),
        one_off_capital_investment_cents=(
            base_scenario.policy_levers.one_off_capital_investment_cents
        ),
    )


# --- NSGA-II internals ---------------------------------------------------------


def _make_individual(
    state: _SearchState, genome: tuple[float, ...]
) -> _Individual:
    evaluation = _evaluate_genome(state, genome)
    objectives = tuple(
        _minimized(objective, evaluation.means[objective.metric_name])
        for objective in state.config.objectives
    )
    hard_violation = sum(
        constraint.violation(evaluation.means[constraint.metric_name])
        for constraint in state.config.constraints
        if constraint.kind == "hard"
    )
    soft_penalty = sum(
        constraint.penalty_weight
        * constraint.violation(evaluation.means[constraint.metric_name])
        for constraint in state.config.constraints
        if constraint.kind == "soft"
    )
    weighted = _weighted_score(state.config, evaluation) - soft_penalty
    return _Individual(
        genome=genome,
        evaluation=evaluation,
        objectives=objectives,
        hard_violation=hard_violation,
        soft_penalty=soft_penalty,
        weighted_score=weighted,
    )


def _evaluate_genome(
    state: _SearchState, genome: tuple[float, ...]
) -> CandidateEvaluation:
    key = tuple(round(gene, 6) for gene in genome)
    cached = state.cache.get(key)
    if cached is not None:
        return cached
    levers = decode_levers(
        state.config, state.company, state.base_scenario, genome
    )
    evaluation = state.evaluator(levers)
    state.cache[key] = evaluation
    state.evaluations += 1
    return evaluation


def _reproduce(
    state: _SearchState,
    population: list[_Individual],
    rng: np.random.Generator,
) -> list[_Individual]:
    offspring: list[_Individual] = []
    size = state.config.population_size
    while len(offspring) < size:
        parent_a = _tournament(population, rng)
        parent_b = _tournament(population, rng)
        child_a, child_b = _sbx_crossover(parent_a.genome, parent_b.genome, rng)
        for genome in (child_a, child_b):
            mutated = _polynomial_mutation(genome, rng)
            offspring.append(_make_individual(state, mutated))
            if len(offspring) >= size:
                break
        if state.evaluations >= state.config.max_evaluations:
            break
    return offspring


def _tournament(
    population: list[_Individual], rng: np.random.Generator
) -> _Individual:
    i, j = (int(index) for index in rng.integers(0, len(population), size=2))
    first, second = population[i], population[j]
    if (first.rank, -first.crowding) <= (second.rank, -second.crowding):
        return first
    return second


def _sbx_crossover(
    parent_a: tuple[float, ...],
    parent_b: tuple[float, ...],
    rng: np.random.Generator,
    eta: float = 15.0,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    child_a: list[float] = []
    child_b: list[float] = []
    for gene_a, gene_b in zip(parent_a, parent_b, strict=True):
        if rng.random() > 0.9 or abs(gene_a - gene_b) < 1e-12:
            child_a.append(gene_a)
            child_b.append(gene_b)
            continue
        u = rng.random()
        beta = (
            (2.0 * u) ** (1.0 / (eta + 1.0))
            if u <= 0.5
            else (1.0 / (2.0 * (1.0 - u))) ** (1.0 / (eta + 1.0))
        )
        low = 0.5 * ((gene_a + gene_b) - beta * abs(gene_b - gene_a))
        high = 0.5 * ((gene_a + gene_b) + beta * abs(gene_b - gene_a))
        child_a.append(_clip_unit(low))
        child_b.append(_clip_unit(high))
    return tuple(child_a), tuple(child_b)


def _polynomial_mutation(
    genome: tuple[float, ...],
    rng: np.random.Generator,
    eta: float = 20.0,
) -> tuple[float, ...]:
    probability = 1.0 / len(genome)
    mutated: list[float] = []
    for gene in genome:
        if rng.random() > probability:
            mutated.append(gene)
            continue
        u = rng.random()
        if u < 0.5:
            delta = (2.0 * u) ** (1.0 / (eta + 1.0)) - 1.0
        else:
            delta = 1.0 - (2.0 * (1.0 - u)) ** (1.0 / (eta + 1.0))
        mutated.append(_clip_unit(gene + delta))
    return tuple(mutated)


def _assign_fronts(population: list[_Individual]) -> None:
    fronts = _fast_non_dominated_sort(population)
    for rank, front in enumerate(fronts):
        for individual in front:
            individual.rank = rank
        _assign_crowding(front)


def _fast_non_dominated_sort(
    population: list[_Individual],
) -> list[list[_Individual]]:
    dominated_by: dict[int, list[int]] = {i: [] for i in range(len(population))}
    domination_count = [0] * len(population)
    fronts: list[list[int]] = [[]]
    for p in range(len(population)):
        for q in range(len(population)):
            if p == q:
                continue
            if _dominates(population[p], population[q]):
                dominated_by[p].append(q)
            elif _dominates(population[q], population[p]):
                domination_count[p] += 1
        if domination_count[p] == 0:
            fronts[0].append(p)
    index = 0
    while fronts[index]:
        nxt: list[int] = []
        for p in fronts[index]:
            for q in dominated_by[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    nxt.append(q)
        index += 1
        fronts.append(nxt)
    return [[population[i] for i in front] for front in fronts if front]


def _dominates(a: _Individual, b: _Individual) -> bool:
    a_feasible = a.hard_violation == 0.0
    b_feasible = b.hard_violation == 0.0
    if a_feasible != b_feasible:
        return a_feasible
    if not a_feasible:
        return a.hard_violation < b.hard_violation
    at_least_as_good = all(
        av <= bv for av, bv in zip(a.objectives, b.objectives, strict=True)
    )
    strictly_better = any(
        av < bv for av, bv in zip(a.objectives, b.objectives, strict=True)
    )
    return at_least_as_good and strictly_better


def _assign_crowding(front: list[_Individual]) -> None:
    if not front:
        return
    count = len(front)
    for individual in front:
        individual.crowding = 0.0
    objective_count = len(front[0].objectives)
    for m in range(objective_count):
        ordered = sorted(front, key=lambda ind: ind.objectives[m])
        ordered[0].crowding = float("inf")
        ordered[-1].crowding = float("inf")
        low = ordered[0].objectives[m]
        high = ordered[-1].objectives[m]
        span = high - low
        if span <= 0:
            continue
        for i in range(1, count - 1):
            ordered[i].crowding += (
                ordered[i + 1].objectives[m] - ordered[i - 1].objectives[m]
            ) / span


def _select_next_generation(
    combined: list[_Individual], size: int
) -> list[_Individual]:
    ordered = sorted(combined, key=lambda ind: (ind.rank, -ind.crowding))
    return ordered[:size]


def _has_converged(history: list[float], config: OptimizationConfig) -> bool:
    if len(history) <= config.convergence_patience:
        return False
    window = history[-(config.convergence_patience + 1) :]
    finite = [value for value in window if value != float("-inf")]
    if len(finite) <= 1:
        return False
    return max(finite) - min(finite) <= config.convergence_tolerance


def _build_result(
    state: _SearchState,
    population: list[_Individual],
    convergence: list[ConvergencePoint],
    converged: bool,
    rng: np.random.Generator,
) -> OptimizationResult:
    config = state.config
    unique: dict[tuple[float, ...], _Individual] = {}
    for individual in population:
        key = tuple(round(gene, 6) for gene in individual.genome)
        unique.setdefault(key, individual)
    ranked = sorted(unique.values(), key=lambda ind: (ind.rank, -ind.weighted_score))
    for candidate_id, individual in enumerate(ranked):
        individual.candidate_id = candidate_id

    frontier_individuals = [ind for ind in ranked if ind.rank == 0 and _feasible(ind)]
    candidates = [self_candidate(state, ind) for ind in ranked]
    frontier = tuple(c for c in candidates if c.rank == 0 and c.feasible)
    infeasible = tuple(c for c in candidates if not c.feasible)
    dominated = tuple(
        c for c in candidates if c.feasible and c.rank > 0
    )
    recommended = max(
        frontier, key=lambda c: c.weighted_score, default=None
    )
    sensitivity = (
        _sensitivity(state, _find_individual(frontier_individuals, recommended))
        if recommended is not None
        else ()
    )
    digest = _optimization_digest(
        config=config,
        frontier=frontier,
        recommended=recommended,
        evaluations=state.evaluations,
    )
    del rng
    return OptimizationResult(
        objectives=config.objectives,
        frontier=frontier,
        recommended=recommended,
        dominated=dominated,
        infeasible=infeasible,
        sensitivity=sensitivity,
        convergence=tuple(convergence),
        evaluations=state.evaluations,
        converged=converged,
        seed=config.seed,
        digest=digest,
    )


def self_candidate(state: _SearchState, individual: _Individual) -> PolicyCandidate:
    """Project an internal individual into the public candidate model."""

    config = state.config
    levers = decode_levers(
        config, state.company, state.base_scenario, individual.genome
    )
    objective_values: dict[str, float] = {
        str(objective.metric_name): individual.evaluation.means[
            objective.metric_name
        ]
        for objective in config.objectives
    }
    constraint_values: dict[str, float] = {
        str(constraint.metric_name): individual.evaluation.means[
            constraint.metric_name
        ]
        for constraint in config.constraints
    }
    feasible = individual.hard_violation == 0.0
    reason = None
    if not feasible:
        breached = [
            constraint.metric_name
            for constraint in config.constraints
            if constraint.kind == "hard"
            and constraint.violation(
                individual.evaluation.means[constraint.metric_name]
            )
            > 0.0
        ]
        reason = f"violates hard constraints: {', '.join(sorted(breached))}"
    return PolicyCandidate(
        candidate_id=individual.candidate_id,
        levers=levers,
        objective_values=objective_values,
        constraint_values=constraint_values,
        hard_violation=round(individual.hard_violation, 6),
        soft_penalty=round(individual.soft_penalty, 6),
        feasible=feasible,
        robustness=_robustness(state, individual),
        weighted_score=round(individual.weighted_score, 6),
        rank=individual.rank,
        exclusion_reason=reason,
    )


def _robustness(state: _SearchState, individual: _Individual) -> float:
    metrics = {obj.metric_name for obj in state.config.objectives}
    metrics.update(
        constraint.metric_name for constraint in state.config.constraints
    )
    if not metrics:
        return 1.0
    breach = [individual.evaluation.breach_probability[name] for name in metrics]
    return round(_clamp_unit(1.0 - sum(breach) / len(breach)), 6)


def _sensitivity(
    state: _SearchState, individual: _Individual | None
) -> tuple[LeverSensitivity, ...]:
    if individual is None:
        return ()
    base_score = individual.weighted_score
    step = 0.1
    results: list[LeverSensitivity] = []
    for index, lever in enumerate(state.config.levers):
        genome = list(individual.genome)
        down = list(genome)
        down[index] = _clip_unit(genome[index] - step)
        up = list(genome)
        up[index] = _clip_unit(genome[index] + step)
        down_score = _make_individual(state, tuple(down)).weighted_score
        up_score = _make_individual(state, tuple(up)).weighted_score
        results.append(
            LeverSensitivity(
                lever_id=lever.lever_id,
                downward_score_delta=round(down_score - base_score, 6),
                upward_score_delta=round(up_score - base_score, 6),
                influence=round(
                    max(abs(down_score - base_score), abs(up_score - base_score)),
                    6,
                ),
            )
        )
    return tuple(results)


def _find_individual(
    individuals: list[_Individual], candidate: PolicyCandidate | None
) -> _Individual | None:
    if candidate is None:
        return None
    for individual in individuals:
        if individual.candidate_id == candidate.candidate_id:
            return individual
    return individuals[0] if individuals else None


def _feasible(individual: _Individual) -> bool:
    return individual.hard_violation == 0.0


def _minimized(objective: ObjectiveSpec, value: float) -> float:
    return -value if objective.direction == "maximize" else value


def _weighted_score(
    config: OptimizationConfig, evaluation: CandidateEvaluation
) -> float:
    total = 0.0
    for objective in config.objectives:
        value = evaluation.means[objective.metric_name]
        signed = value if objective.direction == "maximize" else -value
        total += objective.weight * _normalize(objective.metric_name, signed)
    return total


def _normalize(metric_name: MetricName, value: float) -> float:
    scale = _METRIC_SCALE.get(metric_name, 1.0)
    return value / scale


def _price_changes(
    company: CompanyModel, product_id: str, value: float
) -> list[SegmentProductPriceChange]:
    product = next(
        (p for p in company.products if p.product_id == product_id), None
    )
    if product is None:
        raise DomainValidationError(f"unknown product '{product_id}'")
    return [
        SegmentProductPriceChange(
            segment_id=profile.segment_id,
            product_id=product_id,
            price_change=_decimal(value),
        )
        for profile in product.demand_profiles
    ]


def _overtime_minutes(
    company: CompanyModel, lever: LeverSpec, value: float
) -> int:
    resource = next(
        (
            r
            for r in company.plant.resources
            if r.resource_id == lever.target_id
        ),
        None,
    )
    if resource is None:
        raise DomainValidationError(f"unknown resource '{lever.target_id}'")
    return max(0, min(resource.max_overtime_minutes, round(value)))


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _clip_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _finite(value: float) -> float:
    if value == float("-inf"):
        return -1e18
    return value


#: Rough scales used only to make weighted objective sums comparable.
_METRIC_SCALE: dict[MetricName, float] = {
    "revenue": 1e8,
    "ebitda": 1e7,
    "free_cash_flow": 1e7,
    "closing_cash": 1e7,
    "otif": 1.0,
    "cancellation_rate": 1.0,
    "backlog_units": 1e3,
    "capacity_utilization": 1.0,
    "peak_revolver": 1e7,
    "rescue_funding": 1e7,
}


def _optimization_digest(
    *,
    config: OptimizationConfig,
    frontier: tuple[PolicyCandidate, ...],
    recommended: PolicyCandidate | None,
    evaluations: int,
) -> str:
    body = {
        "config": config.model_dump(mode="json"),
        "frontier": [candidate.model_dump(mode="json") for candidate in frontier],
        "recommended": (
            recommended.model_dump(mode="json") if recommended is not None else None
        ),
        "evaluations": evaluations,
    }
    return sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
