"""Decision portfolio and multi-objective policy frontier application services."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from openenterprise_twin.application.decisions import (
    get_or_build_brief,
    get_or_build_comparison,
)
from openenterprise_twin.application.ports import (
    ArtifactReader,
    CompletedCandidateRecord,
    DecisionEvidenceRepository,
)
from openenterprise_twin.domain.company import DomainModel
from openenterprise_twin.reporting.brief import DecisionStatus
from openenterprise_twin.simulation.experiment import MetricName

EvidenceGrade = Literal["exploratory", "decision_grade"]


class PortfolioMetric(DomainModel):
    metric_name: MetricName
    baseline_mean: float
    candidate_mean: float
    mean_difference: float
    candidate_breach_probability: Annotated[float, Field(ge=0.0, le=1.0)]


class DecisionSummary(DomainModel):
    experiment_id: Annotated[int, Field(gt=0)]
    scenario_id: str
    scenario_name: str
    completed_at: datetime
    replication_count: Annotated[int, Field(gt=0)]
    decision_status: DecisionStatus
    evidence_grade: EvidenceGrade
    headline: str
    hard_constraint_count: Annotated[int, Field(ge=0)]
    metrics: tuple[PortfolioMetric, ...]
    comparison_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    brief_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class DecisionPortfolio(DomainModel):
    items: tuple[DecisionSummary, ...]
    next_before_id: int | None


class FrontierPoint(DomainModel):
    experiment_id: int
    scenario_id: str
    scenario_name: str
    decision_status: DecisionStatus
    ebitda_delta: float
    free_cash_flow_delta: float
    otif_delta: float
    comparison_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class PolicyFrontier(DomainModel):
    points: tuple[FrontierPoint, ...]
    eligible_count: Annotated[int, Field(ge=0)]
    dominated_count: Annotated[int, Field(ge=0)]
    excluded_count: Annotated[int, Field(ge=0)]
    method: Literal["pareto_maximize_ebitda_fcf_otif"] = (
        "pareto_maximize_ebitda_fcf_otif"
    )


def list_decision_portfolio(
    *,
    repository: DecisionEvidenceRepository,
    artifact_store: ArtifactReader,
    limit: int,
    before_id: int | None = None,
) -> DecisionPortfolio:
    records = repository.list_completed_candidates(
        limit=limit,
        before_id=before_id,
    )
    items = tuple(
        _build_decision_summary(
            record,
            repository=repository,
            artifact_store=artifact_store,
        )
        for record in records
    )
    return DecisionPortfolio(
        items=items,
        next_before_id=items[-1].experiment_id if len(items) == limit else None,
    )


def build_policy_frontier(
    summaries: tuple[DecisionSummary, ...],
) -> PolicyFrontier:
    eligible = tuple(
        summary
        for summary in summaries
        if summary.evidence_grade == "decision_grade"
        and summary.decision_status != "do_not_adopt"
        and summary.hard_constraint_count == 0
    )
    frontier = tuple(
        summary
        for summary in eligible
        if not any(
            _dominates(other, summary)
            for other in eligible
            if other.experiment_id != summary.experiment_id
        )
    )
    ordered = tuple(
        sorted(
            frontier,
            key=lambda item: (-_metric_delta(item, "ebitda"), item.experiment_id),
        )
    )
    return PolicyFrontier(
        points=tuple(
            FrontierPoint(
                experiment_id=item.experiment_id,
                scenario_id=item.scenario_id,
                scenario_name=item.scenario_name,
                decision_status=item.decision_status,
                ebitda_delta=_metric_delta(item, "ebitda"),
                free_cash_flow_delta=_metric_delta(item, "free_cash_flow"),
                otif_delta=_metric_delta(item, "otif"),
                comparison_digest=item.comparison_digest,
            )
            for item in ordered
        ),
        eligible_count=len(eligible),
        dominated_count=len(eligible) - len(frontier),
        excluded_count=len(summaries) - len(eligible),
    )


def _build_decision_summary(
    record: CompletedCandidateRecord,
    *,
    repository: DecisionEvidenceRepository,
    artifact_store: ArtifactReader,
) -> DecisionSummary:
    comparison = get_or_build_comparison(
        record.id,
        repository=repository,
        artifact_store=artifact_store,
    )
    brief = get_or_build_brief(
        record.id,
        repository=repository,
        artifact_store=artifact_store,
    )
    metric_names: tuple[MetricName, ...] = (
        "ebitda",
        "free_cash_flow",
        "closing_cash",
        "otif",
    )
    return DecisionSummary(
        experiment_id=record.id,
        scenario_id=record.scenario_id,
        scenario_name=record.scenario_name,
        completed_at=record.completed_at,
        replication_count=record.replication_count,
        decision_status=brief.decision_status,
        evidence_grade=brief.evidence_quality.grade,
        headline=brief.recommendation.headline,
        hard_constraint_count=sum(
            constraint.severity == "breach" for constraint in brief.constraints
        ),
        metrics=tuple(
            PortfolioMetric(
                metric_name=metric_name,
                baseline_mean=comparison.metrics[metric_name].baseline_mean,
                candidate_mean=comparison.metrics[metric_name].candidate_mean,
                mean_difference=comparison.metrics[metric_name].mean_difference,
                candidate_breach_probability=(
                    comparison.metrics[metric_name].candidate_breach_probability
                ),
            )
            for metric_name in metric_names
        ),
        comparison_digest=comparison.digest,
        brief_digest=brief.digest,
    )


def _metric_delta(summary: DecisionSummary, metric_name: MetricName) -> float:
    for metric in summary.metrics:
        if metric.metric_name == metric_name:
            return metric.mean_difference
    raise ValueError(f"portfolio summary is missing '{metric_name}'")


def _dominates(candidate: DecisionSummary, other: DecisionSummary) -> bool:
    frontier_metrics: tuple[MetricName, ...] = (
        "ebitda",
        "free_cash_flow",
        "otif",
    )
    candidate_values = tuple(
        _metric_delta(candidate, metric_name)
        for metric_name in frontier_metrics
    )
    other_values = tuple(
        _metric_delta(other, metric_name)
        for metric_name in frontier_metrics
    )
    return all(
        candidate_value >= other_value
        for candidate_value, other_value in zip(
            candidate_values, other_values, strict=True
        )
    ) and any(
        candidate_value > other_value
        for candidate_value, other_value in zip(
            candidate_values, other_values, strict=True
        )
    )
