import { formatDate } from "../../lib/format";
import { metricLabels } from "./formatScenario";
import type { ExecutiveBrief } from "./types";

interface ExecutionSectionProps {
  report: ExecutiveBrief;
}

export function ExecutionSection({ report }: ExecutionSectionProps) {
  return (
    <section
      aria-labelledby="execution-title"
      className="decision-chapter execution-section"
    >
      <div className="decision-chapter__heading">
        <h2 id="execution-title">Execution</h2>
        <p>
          Named owners, review timing and completion evidence close the loop
          between simulation and action.
        </p>
      </div>

      <dl className="execution-section__governance">
        <div>
          <dt>Decision owner</dt>
          <dd>{report.governance.decision_owner}</dd>
        </div>
        <div>
          <dt>Review date</dt>
          <dd>{formatDate(report.governance.review_date)}</dd>
        </div>
        <div>
          <dt>Decision record</dt>
          <dd>{report.governance.decision_record_action}</dd>
        </div>
      </dl>

      <ol className="execution-section__actions">
        {report.actions.map((action) => (
          <li key={action.action_id}>
            <div>
              <p>{action.title}</p>
              <span>
                {action.evidence_metric_ids
                  .map((metricName) => metricLabels[metricName])
                  .join(", ")}
              </span>
            </div>
            <dl>
              <div>
                <dt>Owner</dt>
                <dd>{action.owner}</dd>
              </div>
              <div>
                <dt>Due</dt>
                <dd>{formatDate(action.due_date)}</dd>
              </div>
            </dl>
            <p>{action.completion_evidence}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
