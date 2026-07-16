"""Reproducible Monte Carlo experiments with bounded parallel execution."""

import json
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from types import MappingProxyType
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from openenterprise_twin.domain.company import (
    CompanyModel,
    DisplayName,
    DomainModel,
    Identifier,
    VersionString,
)
from openenterprise_twin.domain.errors import InvariantViolation
from openenterprise_twin.domain.results import SimulationTrace
from openenterprise_twin.domain.scenario import (
    PolicyLevers,
    Scenario,
    validate_scenario_against_company,
)
from openenterprise_twin.simulation.engine import ENGINE_VERSION, simulate_trace
from openenterprise_twin.simulation.metrics import (
    MetricDistribution,
    summarize_distribution,
)
from openenterprise_twin.simulation.shocks import TAPE_VERSION, build_shock_tape

MetricName = Literal[
    "revenue",
    "ebitda",
    "free_cash_flow",
    "closing_cash",
    "otif",
    "cancellation_rate",
    "backlog_units",
    "capacity_utilization",
    "peak_revolver",
    "rescue_funding",
]
BreachDirection = Literal["below", "above"]
DownsideTail = Literal["lower", "upper"]
NonNegativeInt = Annotated[int, Field(ge=0)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
PluginIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9.-]*$"),
]

METRIC_NAMES: tuple[MetricName, ...] = (
    "revenue",
    "ebitda",
    "free_cash_flow",
    "closing_cash",
    "otif",
    "cancellation_rate",
    "backlog_units",
    "capacity_utilization",
    "peak_revolver",
    "rescue_funding",
)


class MetricGuardrail(DomainModel):
    """Empirical breach and downside semantics for one experiment metric."""

    metric_name: MetricName
    threshold: FiniteFloat
    breach_when: BreachDirection
    downside_tail: DownsideTail


class PluginVersion(DomainModel):
    """Plugin identity retained in experiment provenance."""

    plugin_id: PluginIdentifier
    version: VersionString


class ExperimentRequest(DomainModel):
    """Validated inputs for one reproducible scenario experiment."""

    company: CompanyModel
    scenario: Scenario
    master_seed: NonNegativeInt
    replications: Annotated[int, Field(gt=0, le=10_000)]
    max_workers: Annotated[int, Field(gt=0, le=32)] = 1
    guardrails: tuple[MetricGuardrail, ...] = ()
    plugin_versions: tuple[PluginVersion, ...] = ()

    @model_validator(mode="after")
    def validate_guardrails(self) -> Self:
        names = [guardrail.metric_name for guardrail in self.guardrails]
        if len(names) != len(set(names)):
            raise ValueError("experiment guardrails must target unique metrics")
        plugin_ids = [plugin.plugin_id for plugin in self.plugin_versions]
        if len(plugin_ids) != len(set(plugin_ids)):
            raise ValueError("experiment plugin versions must have unique identifiers")
        validate_scenario_against_company(self.scenario, self.company)
        return self


class ReplicationMetrics(DomainModel):
    """Trace-linked scalar outcomes retained for paired comparisons."""

    replication_id: NonNegativeInt
    trace_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    metric_entries: tuple[tuple[MetricName, FiniteFloat], ...]

    @property
    def metric_values(self) -> Mapping[str, float]:
        return MappingProxyType(dict(self.metric_entries))


class MetricResult(DomainModel):
    metric_name: MetricName
    distribution: MetricDistribution


class ExperimentResult(DomainModel):
    """Auditable distributions and replication-level values for one scenario."""

    scenario_id: Identifier
    scenario_name: DisplayName
    baseline_scenario_id: Identifier | None
    policy_levers: PolicyLevers
    company_model_version: VersionString
    scenario_schema_version: VersionString
    engine_version: VersionString
    shock_tape_version: VersionString
    resolved_assumptions_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    plugin_versions: tuple[PluginVersion, ...]
    master_seed: NonNegativeInt
    replication_count: Annotated[int, Field(gt=0)]
    created_at: datetime
    duration_seconds: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    guardrails: tuple[MetricGuardrail, ...]
    replications: tuple[ReplicationMetrics, ...]
    metric_results: tuple[MetricResult, ...]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @property
    def metrics(self) -> Mapping[str, MetricDistribution]:
        return MappingProxyType(
            {item.metric_name: item.distribution for item in self.metric_results}
        )


def run_experiment(request: ExperimentRequest) -> ExperimentResult:
    """Execute ordered replications and aggregate deterministic distributions."""

    created_at = datetime.now(UTC)
    started_at = perf_counter()
    tasks = tuple(
        (request.company, request.scenario, request.master_seed, replication_id)
        for replication_id in range(request.replications)
    )
    if request.max_workers == 1:
        replications = tuple(_run_replication(task) for task in tasks)
    else:
        workers = min(request.max_workers, request.replications)
        chunk_size = max(1, request.replications // (workers * 4))
        with ProcessPoolExecutor(max_workers=workers) as executor:
            replications = tuple(
                executor.map(_run_replication, tasks, chunksize=chunk_size)
            )

    rules = _resolved_guardrails(request)
    metric_results = tuple(
        MetricResult(
            metric_name=metric_name,
            distribution=_summarize_metric(
                metric_name=metric_name,
                replications=replications,
                guardrail=rules[metric_name],
            ),
        )
        for metric_name in METRIC_NAMES
    )
    result = ExperimentResult(
        scenario_id=request.scenario.scenario_id,
        scenario_name=request.scenario.name,
        baseline_scenario_id=request.scenario.baseline_scenario_id,
        policy_levers=request.scenario.policy_levers,
        company_model_version=request.company.model_version,
        scenario_schema_version=request.scenario.schema_version,
        engine_version=ENGINE_VERSION,
        shock_tape_version=TAPE_VERSION,
        resolved_assumptions_hash=_assumptions_digest(request),
        plugin_versions=_resolved_plugin_versions(request),
        master_seed=request.master_seed,
        replication_count=request.replications,
        created_at=created_at,
        duration_seconds=perf_counter() - started_at,
        guardrails=tuple(rules[name] for name in METRIC_NAMES),
        replications=replications,
        metric_results=metric_results,
        digest="0" * 64,
    )
    result = result.model_copy(update={"digest": experiment_content_digest(result)})
    validate_experiment_result(result)
    return result


def validate_experiment_result(result: ExperimentResult) -> None:
    """Reject tampering, incomplete replications and incompatible metric sets."""

    if experiment_content_digest(result) != result.digest:
        raise InvariantViolation(
            "experiment_digest",
            "experiment content does not match its provenance digest",
        )
    if len(result.replications) != result.replication_count:
        raise InvariantViolation(
            "experiment_replication_count",
            "experiment replication count does not match retained outcomes",
        )
    if tuple(item.replication_id for item in result.replications) != tuple(
        range(result.replication_count)
    ):
        raise InvariantViolation(
            "experiment_replication_sequence",
            "experiment replication identifiers must be contiguous",
        )
    if tuple(item.metric_name for item in result.metric_results) != METRIC_NAMES:
        raise InvariantViolation(
            "experiment_metric_dimension",
            "experiment distributions do not match the required metric dimension",
        )
    for replication in result.replications:
        if tuple(name for name, _ in replication.metric_entries) != METRIC_NAMES:
            raise InvariantViolation(
                "experiment_metric_dimension",
                "replication values do not match the required metric dimension",
            )
    if tuple(item.metric_name for item in result.guardrails) != METRIC_NAMES:
        raise InvariantViolation(
            "experiment_guardrail_dimension",
            "experiment guardrails do not match the required metric dimension",
        )
    if result.created_at.tzinfo is None:
        raise InvariantViolation(
            "experiment_creation_time",
            "experiment creation time must be timezone-aware",
        )
    plugin_ids = [plugin.plugin_id for plugin in result.plugin_versions]
    if len(plugin_ids) != len(set(plugin_ids)):
        raise InvariantViolation(
            "experiment_plugin_versions",
            "experiment plugin versions must have unique identifiers",
        )

    guardrails = {
        guardrail.metric_name: guardrail for guardrail in result.guardrails
    }
    for metric_result in result.metric_results:
        expected = _summarize_metric(
            metric_name=metric_result.metric_name,
            replications=result.replications,
            guardrail=guardrails[metric_result.metric_name],
        )
        if expected != metric_result.distribution:
            raise InvariantViolation(
                "experiment_distribution_reconciliation",
                f"distribution does not reconcile for '{metric_result.metric_name}'",
            )


def _run_replication(
    task: tuple[CompanyModel, Scenario, int, int],
) -> ReplicationMetrics:
    company, scenario, master_seed, replication_id = task
    tape = build_shock_tape(
        company,
        scenario,
        seed=master_seed,
        replication_id=replication_id,
    )
    trace = simulate_trace(
        company,
        scenario,
        tape,
        allow_rescue_funding=True,
    )
    values = _trace_metric_values(trace)
    return ReplicationMetrics(
        replication_id=replication_id,
        trace_digest=trace.digest,
        metric_entries=tuple((name, values[name]) for name in METRIC_NAMES),
    )


def _trace_metric_values(trace: SimulationTrace) -> dict[MetricName, float]:
    evaluation = [period for period in trace.periods if period.phase == "evaluation"]
    if not evaluation:
        raise ValueError("trace must contain an evaluation period")

    revenue = sum(period.revenue_cents for period in evaluation)
    ebitda = sum(
        period.revenue_cents
        - period.cogs_cents
        - period.fixed_cost_cents
        - period.overtime_cost_cents
        for period in evaluation
    )
    free_cash_flow = sum(
        period.collections_cents
        - period.supplier_payments_cents
        - period.conversion_cost_cents
        - period.overtime_cost_cents
        - period.fixed_cost_cents
        - period.interest_paid_cents
        for period in evaluation
    ) - sum(period.capital_investment_cents for period in trace.periods)
    evaluation_orders = sum(
        sum(period.new_orders_count.values()) for period in evaluation
    )
    evaluation_otif = sum(
        sum(period.otif_evaluation_orders_count.values())
        for period in trace.periods
    )
    evaluation_cancellations = sum(
        sum(period.cancelled_evaluation_orders_count.values())
        for period in trace.periods
    )
    available_minutes = sum(
        sum(period.capacity_available_minutes.values()) for period in evaluation
    )
    used_minutes = sum(
        sum(period.capacity_used_minutes.values()) for period in evaluation
    )
    otif = evaluation_otif / evaluation_orders if evaluation_orders else 1.0
    cancellation_rate = (
        evaluation_cancellations / evaluation_orders if evaluation_orders else 0.0
    )
    return {
        "revenue": float(revenue),
        "ebitda": float(ebitda),
        "free_cash_flow": float(free_cash_flow),
        "closing_cash": float(trace.periods[-1].closing_cash_cents),
        "otif": otif,
        "cancellation_rate": cancellation_rate,
        "backlog_units": float(
            sum(trace.periods[-1].closing_backlog_units.values())
        ),
        "capacity_utilization": (
            used_minutes / available_minutes if available_minutes else 0.0
        ),
        "peak_revolver": float(
            max(period.closing_revolver_debt_cents for period in trace.periods)
        ),
        "rescue_funding": float(
            sum(period.rescue_funding_cents for period in trace.periods)
        ),
    }


def _resolved_guardrails(
    request: ExperimentRequest,
) -> dict[MetricName, MetricGuardrail]:
    defaults = (
        MetricGuardrail(
            metric_name="revenue",
            threshold=0.0,
            breach_when="below",
            downside_tail="lower",
        ),
        MetricGuardrail(
            metric_name="ebitda",
            threshold=0.0,
            breach_when="below",
            downside_tail="lower",
        ),
        MetricGuardrail(
            metric_name="free_cash_flow",
            threshold=0.0,
            breach_when="below",
            downside_tail="lower",
        ),
        MetricGuardrail(
            metric_name="closing_cash",
            threshold=float(request.company.financial_policy.liquidity_floor_cents),
            breach_when="below",
            downside_tail="lower",
        ),
        MetricGuardrail(
            metric_name="otif",
            threshold=0.95,
            breach_when="below",
            downside_tail="lower",
        ),
        MetricGuardrail(
            metric_name="cancellation_rate",
            threshold=0.05,
            breach_when="above",
            downside_tail="upper",
        ),
        MetricGuardrail(
            metric_name="backlog_units",
            threshold=0.0,
            breach_when="above",
            downside_tail="upper",
        ),
        MetricGuardrail(
            metric_name="capacity_utilization",
            threshold=0.95,
            breach_when="above",
            downside_tail="upper",
        ),
        MetricGuardrail(
            metric_name="peak_revolver",
            threshold=float(request.company.financial_policy.revolver_limit_cents),
            breach_when="above",
            downside_tail="upper",
        ),
        MetricGuardrail(
            metric_name="rescue_funding",
            threshold=0.0,
            breach_when="above",
            downside_tail="upper",
        ),
    )
    result = {guardrail.metric_name: guardrail for guardrail in defaults}
    result.update(
        {guardrail.metric_name: guardrail for guardrail in request.guardrails}
    )
    return result


def experiment_content_digest(result: ExperimentResult) -> str:
    canonical = json.dumps(
        result.model_dump(mode="json", exclude={"digest"}),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _assumptions_digest(request: ExperimentRequest) -> str:
    canonical = json.dumps(
        {
            "company": request.company.model_dump(mode="json"),
            "scenario": request.scenario.model_dump(mode="json"),
            "plugin_versions": [
                plugin.model_dump(mode="json")
                for plugin in _resolved_plugin_versions(request)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _resolved_plugin_versions(
    request: ExperimentRequest,
) -> tuple[PluginVersion, ...]:
    core = (
        PluginVersion(plugin_id="core.simulation", version=ENGINE_VERSION),
        PluginVersion(plugin_id="core.metrics", version=ENGINE_VERSION),
    )
    return (*core, *request.plugin_versions)


def _summarize_metric(
    *,
    metric_name: MetricName,
    replications: tuple[ReplicationMetrics, ...],
    guardrail: MetricGuardrail,
) -> MetricDistribution:
    values = [
        replication.metric_values[metric_name] for replication in replications
    ]
    distribution = summarize_distribution(
        values,
        guardrail=guardrail.threshold,
        breach_when=guardrail.breach_when,
        downside_tail=guardrail.downside_tail,
    )
    if metric_name != "closing_cash":
        return distribution

    breaches = sum(
        replication.metric_values["closing_cash"] < guardrail.threshold
        or replication.metric_values["rescue_funding"] > 0
        for replication in replications
    )
    return distribution.model_copy(
        update={"breach_probability": breaches / len(replications)}
    )
