"""Typed, infrastructure-free contracts implemented by plugin capabilities."""

import json
from hashlib import sha256
from typing import Annotated, Protocol, Self, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openenterprise_twin.domain.company import CompanyModel, FinancialPolicy
from openenterprise_twin.domain.results import SimulationTrace, trace_content_digest
from openenterprise_twin.domain.scenario import Scenario
from openenterprise_twin.reporting.brief import ExecutiveBrief
from openenterprise_twin.simulation.shocks import ShockTape


class PluginContractModel(BaseModel):
    """Strict immutable base for values crossing a plugin boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class QuantityEntry(PluginContractModel):
    """An immutable integer quantity keyed by a domain entity."""

    entity_id: Annotated[str, Field(min_length=1, max_length=128)]
    value: Annotated[int, Field(ge=0)]


class DemandModelInput(PluginContractModel):
    """Complete deterministic context available to a demand capability."""

    company: CompanyModel
    scenario: Scenario
    shock_tape: ShockTape


class DemandModelOutput(PluginContractModel):
    """Demand quantities returned in a stable immutable order."""

    demand_units: tuple[QuantityEntry, ...]


class OperationsModelInput(PluginContractModel):
    """Physical state and demand supplied to an operations capability."""

    company: CompanyModel
    scenario: Scenario
    shock_tape: ShockTape
    demand: DemandModelOutput
    finished_goods_units: tuple[QuantityEntry, ...] = ()
    backlog_units: tuple[QuantityEntry, ...] = ()
    material_inventory_units: tuple[QuantityEntry, ...] = ()


class OperationsModelOutput(PluginContractModel):
    """Auditable physical decisions produced by an operations capability."""

    production_start_units: tuple[QuantityEntry, ...]
    shipment_units: tuple[QuantityEntry, ...]
    material_consumption_units: tuple[QuantityEntry, ...]
    capacity_used_minutes: tuple[QuantityEntry, ...]


class FinanceModelInput(PluginContractModel):
    """Typed cash and policy state supplied to a finance capability."""

    policy: FinancialPolicy
    operations: OperationsModelOutput
    cash_before_financing_cents: int
    opening_debt_cents: Annotated[int, Field(ge=0)]


class FinanceModelOutput(PluginContractModel):
    """Reconciled financing decision returned by a finance capability."""

    closing_cash_cents: Annotated[int, Field(ge=0)]
    closing_debt_cents: Annotated[int, Field(ge=0)]
    draw_cents: Annotated[int, Field(ge=0)]
    repayment_cents: Annotated[int, Field(ge=0)]
    rescue_funding_cents: Annotated[int, Field(ge=0)]


class RiskMetricInput(PluginContractModel):
    """Immutable simulation evidence supplied to a risk metric."""

    trace: "TraceEvidence"


class TraceEvidence(PluginContractModel):
    """Canonical immutable trace whose digest is validated at the boundary."""

    canonical_json: str
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]

    @classmethod
    def from_trace(cls, trace: SimulationTrace) -> "TraceEvidence":
        if trace_content_digest(trace) != trace.digest:
            raise ValueError("simulation trace does not match its digest")
        canonical_json = json.dumps(
            trace.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(canonical_json=canonical_json, digest=trace.digest)

    @model_validator(mode="after")
    def validate_canonical_evidence(self) -> Self:
        try:
            decoded = json.loads(self.canonical_json)
        except json.JSONDecodeError:
            raise ValueError("canonical_json must contain valid JSON") from None
        if not isinstance(decoded, dict):
            raise ValueError("canonical_json must contain a trace object")
        payload = cast(dict[str, object], decoded)
        if payload.get("digest") != self.digest:
            raise ValueError("trace evidence digest field does not match")
        content = dict(payload)
        content.pop("digest", None)
        canonical_content = json.dumps(
            content,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if sha256(canonical_content).hexdigest() != self.digest:
            raise ValueError("trace evidence content does not match its digest")
        if json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ) != self.canonical_json:
            raise ValueError("trace evidence JSON must use canonical encoding")
        return self


class RiskMetricOutput(PluginContractModel):
    """One finite scalar risk result."""

    metric_id: Annotated[str, Field(min_length=1, max_length=128)]
    value: Annotated[float, Field(allow_inf_nan=False)]


class OptimizationStrategyInput(PluginContractModel):
    """Bounded search context supplied to an optimization strategy."""

    company: CompanyModel
    baseline_scenario: Scenario
    objective_metric_ids: tuple[str, ...]
    max_evaluations: Annotated[int, Field(gt=0)]


class OptimizationStrategyOutput(PluginContractModel):
    """Ordered candidate scenarios returned by an optimization strategy."""

    candidate_scenarios: tuple[Scenario, ...]


class ReportSectionInput(PluginContractModel):
    """Decision evidence supplied to a report-section capability."""

    brief: ExecutiveBrief


class ReportSectionOutput(PluginContractModel):
    """A deterministic report section with no rendering-framework types."""

    section_id: Annotated[str, Field(min_length=1, max_length=128)]
    title: Annotated[str, Field(min_length=1, max_length=160)]
    body_markdown: str


@runtime_checkable
class DemandModel(Protocol):
    """Forecast demand from immutable company, scenario and shock inputs."""

    @property
    def capability_id(self) -> str: ...

    def forecast(self, inputs: DemandModelInput, /) -> DemandModelOutput: ...


@runtime_checkable
class OperationsModel(Protocol):
    """Plan physical operations from typed immutable state."""

    @property
    def capability_id(self) -> str: ...

    def plan(self, inputs: OperationsModelInput, /) -> OperationsModelOutput: ...


@runtime_checkable
class FinanceModel(Protocol):
    """Project financing transitions from typed operating decisions."""

    @property
    def capability_id(self) -> str: ...

    def project(self, inputs: FinanceModelInput, /) -> FinanceModelOutput: ...


@runtime_checkable
class RiskMetric(Protocol):
    """Calculate one risk metric from immutable simulation evidence."""

    @property
    def capability_id(self) -> str: ...

    def calculate(self, inputs: RiskMetricInput, /) -> RiskMetricOutput: ...


@runtime_checkable
class OptimizationStrategy(Protocol):
    """Generate bounded candidate scenarios for a declared objective."""

    @property
    def capability_id(self) -> str: ...

    def optimize(
        self,
        inputs: OptimizationStrategyInput,
        /,
    ) -> OptimizationStrategyOutput: ...


@runtime_checkable
class ReportSection(Protocol):
    """Render one deterministic section from typed decision evidence."""

    @property
    def capability_id(self) -> str: ...

    def render(self, inputs: ReportSectionInput, /) -> ReportSectionOutput: ...
