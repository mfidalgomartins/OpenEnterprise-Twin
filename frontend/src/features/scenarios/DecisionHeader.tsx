import { formatDate } from "../../lib/format";
import type { ExecutiveBrief, ScenarioComparison } from "./types";

interface DecisionHeaderProps {
  comparison: ScenarioComparison;
  report: ExecutiveBrief;
}

function formatHorizon(days: number) {
  const approximateMonths = Math.round(days / 30.4375);

  if (days >= 60) {
    return approximateMonths + " months";
  }
  return days + " " + (days === 1 ? "day" : "days");
}

export function DecisionHeader({ comparison, report }: DecisionHeaderProps) {
  return (
    <header className="decision-header">
      <div className="decision-header__title-row">
        <h1>{comparison.candidate_scenario_name}</h1>
        <p className="decision-header__state">
          {report.evidence_quality.grade === "decision_grade"
            ? `Decision-grade · ${report.evidence_quality.actual_replications} paired runs`
            : `Exploratory · ${report.evidence_quality.actual_replications} of ${report.evidence_quality.minimum_replications} required`}
        </p>
      </div>
      <p className="decision-header__metadata">
        <span>Baseline: {comparison.baseline_scenario_name}</span>
        <span>Horizon: {formatHorizon(comparison.horizon_days)}</span>
        <span>Compared: {formatDate(comparison.created_at)}</span>
      </p>
      <div className="decision-header__conclusion">
        <h2>{report.recommendation.headline}</h2>
        <p>{report.recommendation.rationale[0]}</p>
      </div>
    </header>
  );
}
