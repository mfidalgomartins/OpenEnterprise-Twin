import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { formatDate, formatPercent } from "../../lib/format";
import {
  getScenarioComparison,
  getScenarioReport,
} from "../scenarios/api";
import {
  formatInteger,
  formatMetricValue,
  metricLabels,
} from "../scenarios/formatScenario";
import type {
  ExecutiveBrief,
  MetricComparison,
  MetricName,
  OutcomeDelta,
  ScenarioComparison,
} from "../scenarios/types";
import "./report.css";

const decisionLabels = {
  adopt: "Adopt",
  conditional: "Adopt with guardrails",
  do_not_adopt: "Do not adopt",
} as const;

const valueMetrics = new Set<MetricName>([
  "revenue",
  "ebitda",
  "free_cash_flow",
  "closing_cash",
]);

const operationalMetrics = new Set<MetricName>([
  "otif",
  "cancellation_rate",
  "backlog_units",
  "capacity_utilization",
]);

interface ReportChapterProps {
  children: React.ReactNode;
  id: string;
  introduction: string;
  number: string;
  title: string;
}

function ReportChapter({
  children,
  id,
  introduction,
  number,
  title,
}: ReportChapterProps) {
  return (
    <section
      aria-labelledby={`${id}-title`}
      className="executive-report__chapter"
      data-report-chapter={number}
    >
      <header className="executive-report__chapter-heading">
        <p>{number}</p>
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          <p>{introduction}</p>
        </div>
      </header>
      {children}
    </section>
  );
}

function MetricTable({
  caption,
  outcomes,
}: {
  caption: string;
  outcomes: OutcomeDelta[];
}) {
  return (
    <div className="report-table-wrap">
      <table className="report-table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">Baseline</th>
            <th scope="col">Candidate</th>
            <th scope="col">Paired delta</th>
            <th scope="col">Probability of improvement</th>
          </tr>
        </thead>
        <tbody>
          {outcomes.map((outcome) => (
            <tr key={outcome.metric_name}>
              <th scope="row">{metricLabels[outcome.metric_name]}</th>
              <td>
                {formatMetricValue(
                  outcome.metric_name,
                  outcome.baseline_mean,
                )}
              </td>
              <td>
                {formatMetricValue(
                  outcome.metric_name,
                  outcome.candidate_mean,
                )}
              </td>
              <td>
                {formatMetricValue(
                  outcome.metric_name,
                  outcome.mean_difference,
                  { difference: true },
                )}
              </td>
              <td>
                {formatPercent(outcome.probability_of_improvement, {
                  maximumFractionDigits: 0,
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function interval(metric: MetricComparison) {
  if (metric.ci95_lower === null || metric.ci95_upper === null) {
    return "Not estimable";
  }
  return `${formatMetricValue(metric.metric_name, metric.ci95_lower, {
    difference: true,
  })} to ${formatMetricValue(metric.metric_name, metric.ci95_upper, {
    difference: true,
  })}`;
}

function RecommendationChapter({ report }: { report: ExecutiveBrief }) {
  return (
    <ReportChapter
      id="report-recommendation"
      introduction="The decision statement is generated from the paired evidence and frozen with this experiment."
      number="01"
      title="Recommendation"
    >
      <div className="report-recommendation">
        <div>
          <p
            className={`report-decision report-decision--${report.decision_status}`}
          >
            {decisionLabels[report.decision_status]}
          </p>
          <h3>{report.recommendation.headline}</h3>
        </div>
        <ul>
          {report.recommendation.rationale.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    </ReportChapter>
  );
}

function ScenarioChapter({
  comparison,
  report,
}: {
  comparison: ScenarioComparison;
  report: ExecutiveBrief;
}) {
  return (
    <ReportChapter
      id="report-scenarios"
      introduction="The candidate is evaluated only against its named baseline under the same random shock tape."
      number="02"
      title="Scenario comparison"
    >
      <div className="report-scenario-pair">
        <article>
          <p>Baseline</p>
          <h3>{comparison.baseline_scenario_name}</h3>
          <code>{comparison.baseline_scenario_id}</code>
        </article>
        <span aria-hidden="true">→</span>
        <article>
          <p>Candidate</p>
          <h3>{comparison.candidate_scenario_name}</h3>
          <code>{comparison.candidate_scenario_id}</code>
        </article>
      </div>
      <div className="report-callout">
        <h3>Changed mechanisms</h3>
        <ul>
          {report.mechanisms.map((mechanism) => (
            <li key={mechanism.mechanism_id}>
              <strong>{mechanism.title}</strong>
              <span>{mechanism.detail}</span>
            </li>
          ))}
        </ul>
      </div>
    </ReportChapter>
  );
}

function SensitivityChapter({
  comparison,
  report,
}: {
  comparison: ScenarioComparison;
  report: ExecutiveBrief;
}) {
  return (
    <ReportChapter
      id="report-sensitivities"
      introduction="Materiality, uncertainty and guardrail risk remain visible alongside average outcomes."
      number="05"
      title="Sensitivities and guardrails"
    >
      <div className="report-table-wrap">
        <table className="report-table report-table--dense">
          <caption>Paired uncertainty by metric</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col">95% interval</th>
              <th scope="col">P5 / P95</th>
              <th scope="col">Improve</th>
              <th scope="col">Candidate breach</th>
            </tr>
          </thead>
          <tbody>
            {comparison.metric_results.map((metric) => (
              <tr key={metric.metric_name}>
                <th scope="row">{metricLabels[metric.metric_name]}</th>
                <td>{interval(metric)}</td>
                <td>
                  {formatMetricValue(metric.metric_name, metric.p5_difference, {
                    difference: true,
                  })}{" "}
                  /{" "}
                  {formatMetricValue(metric.metric_name, metric.p95_difference, {
                    difference: true,
                  })}
                </td>
                <td>
                  {formatPercent(metric.probability_of_improvement, {
                    maximumFractionDigits: 0,
                  })}
                </td>
                <td>
                  {formatPercent(metric.candidate_breach_probability, {
                    maximumFractionDigits: 0,
                  })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="report-guardrails">
        <div>
          <h3>Binding constraints</h3>
          {report.constraints.length > 0 ? (
            <ul>
              {report.constraints.map((constraint) => (
                <li key={`${constraint.metric_name}-${constraint.severity}`}>
                  {constraint.detail}
                </li>
              ))}
            </ul>
          ) : (
            <p>No material constraint was identified.</p>
          )}
        </div>
        <div>
          <h3>Downside triggers</h3>
          {report.downside_triggers.length > 0 ? (
            <ul>
              {report.downside_triggers.map((trigger) => (
                <li key={trigger.metric_name}>{trigger.detail}</li>
              ))}
            </ul>
          ) : (
            <p>No downside trigger was produced.</p>
          )}
        </div>
      </div>
    </ReportChapter>
  );
}

function ExecutionChapter({ report }: { report: ExecutiveBrief }) {
  return (
    <ReportChapter
      id="report-execution"
      introduction="Named ownership and dated evidence turn the recommendation into a controlled decision cycle."
      number="06"
      title="Execution plan"
    >
      <dl className="report-governance">
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
      <div className="report-table-wrap">
        <table className="report-table report-actions">
          <caption>Evidence-linked actions</caption>
          <thead>
            <tr>
              <th scope="col">Action</th>
              <th scope="col">Owner</th>
              <th scope="col">Due</th>
              <th scope="col">Completion evidence</th>
            </tr>
          </thead>
          <tbody>
            {report.actions.map((action) => (
              <tr key={action.action_id}>
                <th scope="row">
                  {action.title}
                  <span>
                    {action.evidence_metric_ids
                      .map((metricName) => metricLabels[metricName])
                      .join(", ")}
                  </span>
                </th>
                <td>{action.owner}</td>
                <td>{formatDate(action.due_date)}</td>
                <td>{action.completion_evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </ReportChapter>
  );
}

function ProvenanceChapter({
  experimentId,
  report,
}: {
  experimentId: string;
  report: ExecutiveBrief;
}) {
  const provenance = report.provenance;
  return (
    <ReportChapter
      id="report-provenance"
      introduction="These identifiers reproduce the evidence and prove which model state supported the decision."
      number="08"
      title="Provenance"
    >
      <div className="report-provenance-summary">
        <p>Experiment {experimentId}</p>
        <p>Brief schema {report.brief_schema_version}</p>
        <p>Model {provenance.company_model_version}</p>
        <p>Engine {provenance.engine_version}</p>
        <p>Seed {formatInteger(provenance.master_seed)}</p>
        <p>{formatInteger(provenance.replication_count)} paired replications</p>
        <p>{formatDate(provenance.created_at)}</p>
      </div>
      <dl className="report-provenance-list">
        <div>
          <dt>Report digest</dt>
          <dd><code>{report.digest}</code></dd>
        </div>
        <div>
          <dt>Comparison digest</dt>
          <dd><code>{provenance.comparison_digest}</code></dd>
        </div>
        <div>
          <dt>Company model hash</dt>
          <dd><code>{provenance.company_model_hash}</code></dd>
        </div>
        <div>
          <dt>Baseline experiment digest</dt>
          <dd><code>{provenance.baseline_experiment_digest}</code></dd>
        </div>
        <div>
          <dt>Candidate experiment digest</dt>
          <dd><code>{provenance.candidate_experiment_digest}</code></dd>
        </div>
        <div>
          <dt>Shock tape</dt>
          <dd>{provenance.shock_tape_version}</dd>
        </div>
      </dl>
    </ReportChapter>
  );
}

export function ExecutiveReportPage() {
  const { experimentId = "" } = useParams();
  const immutableQueryOptions = {
    enabled: Boolean(experimentId),
    gcTime: Infinity,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  } as const;
  const comparisonQuery = useQuery({
    ...immutableQueryOptions,
    queryFn: () => getScenarioComparison(experimentId),
    queryKey: ["published-comparison", experimentId],
  });
  const reportQuery = useQuery({
    ...immutableQueryOptions,
    queryFn: () => getScenarioReport(experimentId),
    queryKey: ["published-report", experimentId],
  });

  if (!experimentId) {
    return (
      <section className="executive-report-state">
        <h1>Executive brief unavailable</h1>
        <p role="alert">An experiment identifier is required.</p>
      </section>
    );
  }

  if (comparisonQuery.isPending || reportQuery.isPending) {
    return (
      <section className="executive-report-state">
        <h1>Preparing published brief</h1>
        <p>Loading the immutable evidence snapshot for experiment {experimentId}.</p>
      </section>
    );
  }

  if (
    comparisonQuery.error ||
    reportQuery.error ||
    !comparisonQuery.data ||
    !reportQuery.data
  ) {
    return (
      <section className="executive-report-state">
        <h1>Executive brief unavailable</h1>
        <p role="alert">
          The published comparison could not be loaded. Check the experiment
          identifier and API state.
        </p>
      </section>
    );
  }

  const comparison = comparisonQuery.data;
  const report = reportQuery.data;
  const financialOutcomes = report.outcome_deltas.filter((outcome) =>
    valueMetrics.has(outcome.metric_name),
  );
  const operationsOutcomes = report.outcome_deltas.filter((outcome) =>
    operationalMetrics.has(outcome.metric_name),
  );

  return (
    <article className="executive-report">
      <div className="executive-report__toolbar">
        <div>
          <p>Published snapshot</p>
          <span>Experiment {experimentId}</span>
        </div>
        <button type="button" onClick={() => window.print()}>
          Print or save PDF
        </button>
      </div>

      <div className="executive-report__print-header" aria-hidden="true">
        <span>OpenEnterprise Twin</span>
        <span>Executive decision brief · Experiment {experimentId}</span>
      </div>
      <div className="executive-report__print-footer" aria-hidden="true">
        <span>Immutable evidence snapshot</span>
        <span>{report.digest.slice(0, 16)}</span>
      </div>

      <header className="executive-report__cover">
        <div>
          <p className="executive-report__eyebrow">Frozen decision record</p>
          <h1>Executive decision brief</h1>
          <h2>{comparison.candidate_scenario_name}</h2>
          <p>{report.recommendation.headline}</p>
        </div>
        <dl>
          <div>
            <dt>Experiment</dt>
            <dd>{experimentId}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{report.provenance.company_model_version}</dd>
          </div>
          <div>
            <dt>Published</dt>
            <dd>{formatDate(report.provenance.created_at)}</dd>
          </div>
          <div>
            <dt>Review</dt>
            <dd>{formatDate(report.governance.review_date)}</dd>
          </div>
        </dl>
      </header>

      <RecommendationChapter report={report} />
      <ScenarioChapter comparison={comparison} report={report} />
      <ReportChapter
        id="report-value"
        introduction="Exact baseline, candidate and paired-difference values show where enterprise value changes."
        number="03"
        title="Value bridge"
      >
        <MetricTable caption="Financial outcome bridge" outcomes={financialOutcomes} />
      </ReportChapter>
      <ReportChapter
        id="report-operations"
        introduction="Service and operating constraints are reported alongside the financial case."
        number="04"
        title="Operational feasibility"
      >
        <MetricTable
          caption="Operational outcome bridge"
          outcomes={operationsOutcomes}
        />
      </ReportChapter>
      <SensitivityChapter comparison={comparison} report={report} />
      <ExecutionChapter report={report} />
      <ReportChapter
        id="report-assumptions"
        introduction="The decision is valid only under these recorded modelling conventions and mechanisms."
        number="07"
        title="Assumptions"
      >
        <ol className="report-assumptions">
          {report.assumptions.map((assumption) => (
            <li key={assumption}>{assumption}</li>
          ))}
        </ol>
        <p className="report-assumptions__note">
          Scenario schema {report.provenance.scenario_schema_version}; shock tape{" "}
          {report.provenance.shock_tape_version}. Re-run a new experiment when
          assumptions or model versions change.
        </p>
      </ReportChapter>
      <ProvenanceChapter experimentId={experimentId} report={report} />
    </article>
  );
}
