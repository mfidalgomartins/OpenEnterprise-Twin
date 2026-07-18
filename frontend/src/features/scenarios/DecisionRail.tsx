import { Link } from "react-router-dom";

import { formatDate, formatPercent } from "../../lib/format";
import { metricLabels } from "./formatScenario";
import type { ExecutiveBrief } from "./types";

const decisionLabels = {
  adopt: "Adopt",
  conditional: "Adopt with guardrails",
  do_not_adopt: "Do not adopt",
} as const;

interface DecisionRailProps {
  experimentId: string;
  report: ExecutiveBrief;
}

export function DecisionRail({ experimentId, report }: DecisionRailProps) {
  return (
    <aside aria-labelledby="decision-rail-title" className="decision-rail">
      <section className="decision-rail__section">
        <h2 id="decision-rail-title">Decision recommendation</h2>
        <p
          className={
            "decision-rail__recommendation decision-rail__recommendation--" +
            report.decision_status
          }
        >
          {decisionLabels[report.decision_status]}
        </p>
        <p>{report.recommendation.headline}</p>
      </section>

      <section className="decision-rail__section">
        <h3>Downside trigger</h3>
        {report.downside_triggers.length > 0 ? (
          <ul className="decision-rail__list">
            {report.downside_triggers.map((trigger) => (
              <li key={trigger.metric_name}>
                <strong>{metricLabels[trigger.metric_name]}</strong>
                <span>
                  {formatPercent(trigger.breach_probability, {
                    maximumFractionDigits: 0,
                  })}{" "}
                  simulated breach risk
                </span>
                <span>{trigger.detail}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No downside trigger was produced by this comparison.</p>
        )}
      </section>

      <section className="decision-rail__section">
        <h3>Binding constraints</h3>
        {report.constraints.length > 0 ? (
          <ul className="decision-rail__list">
            {report.constraints.map((constraint) => (
              <li key={constraint.metric_name + "-" + constraint.severity}>
                <strong>{metricLabels[constraint.metric_name]}</strong>
                <span>{constraint.detail}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p>No material constraint was identified.</p>
        )}
      </section>

      <section className="decision-rail__section">
        <h3>Decision governance</h3>
        <dl className="decision-rail__governance">
          <div>
            <dt>Owner</dt>
            <dd>{report.governance.decision_owner}</dd>
          </div>
          <div>
            <dt>Review</dt>
            <dd>{formatDate(report.governance.review_date)}</dd>
          </div>
        </dl>
        <Link
          className="decision-rail__action"
          to={`/reports/${encodeURIComponent(experimentId)}`}
        >
          Open published executive brief
        </Link>
      </section>

      <section className="decision-rail__section">
        <h3>Evidence basis</h3>
        <p>
          {report.recommendation.evidence_metric_ids
            .map((metricName) => metricLabels[metricName])
            .join(", ")}
        </p>
        <a className="decision-rail__action" href="#evidence">
          Review decision evidence
        </a>
      </section>
    </aside>
  );
}
