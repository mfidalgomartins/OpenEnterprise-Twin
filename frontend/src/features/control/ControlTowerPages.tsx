import { Link } from "react-router-dom";

import { formatDate, formatPercent } from "../../lib/format";
import {
  formatDecisionStatus,
  formatMetricValue,
} from "../scenarios/formatScenario";
import type {
  DecisionStatus,
  DecisionSummary,
  EvidenceGrade,
  MetricName,
} from "../scenarios/types";
import { useControlTower } from "./useControlTower";
import "./control-tower.css";

function StatePanel({
  error,
  isPending,
  retry,
}: {
  error: unknown;
  isPending: boolean;
  retry: () => void;
}) {
  if (isPending) {
    return (
      <section aria-live="polite" className="control-state" role="status">
        <h1>Preparing the control tower</h1>
        <p>Loading the company model and decision evidence.</p>
      </section>
    );
  }
  if (error) {
    return (
      <section className="control-state">
        <h1>Control tower unavailable</h1>
        <p role="alert">The current evidence could not be loaded.</p>
        <button className="control-button" onClick={retry} type="button">
          Retry
        </button>
      </section>
    );
  }
  return null;
}

function PageHeader({
  eyebrow,
  title,
  summary,
}: {
  eyebrow: string;
  title: string;
  summary: string;
}) {
  return (
    <header className="control-header">
      <p className="control-header__eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p>{summary}</p>
    </header>
  );
}

function metricFor(decision: DecisionSummary, metricName: MetricName) {
  return decision.metrics.find((metric) => metric.metric_name === metricName);
}

function DecisionStatusLabel({
  evidenceGrade,
  status,
}: {
  evidenceGrade: EvidenceGrade;
  status: DecisionStatus;
}) {
  return (
    <span className={`decision-state decision-state--${status}`}>
      {formatDecisionStatus(status, evidenceGrade)}
    </span>
  );
}

function DecisionCard({ decision }: { decision: DecisionSummary }) {
  const ebitda = metricFor(decision, "ebitda");
  const cash = metricFor(decision, "closing_cash");
  return (
    <article className="decision-card">
      <div className="decision-card__topline">
        <DecisionStatusLabel
          evidenceGrade={decision.evidence_grade}
          status={decision.decision_status}
        />
        <span>
          {decision.evidence_grade === "decision_grade"
            ? `${decision.replication_count} paired runs`
            : `Exploratory · ${decision.replication_count} runs`}
        </span>
      </div>
      <h3>{decision.scenario_name}</h3>
      <p>{decision.headline}</p>
      <dl className="decision-card__metrics">
        <div>
          <dt>EBITDA</dt>
          <dd>
            {ebitda
              ? formatMetricValue("ebitda", ebitda.mean_difference, {
                  compact: true,
                  difference: true,
                })
              : "—"}
          </dd>
        </div>
        <div>
          <dt>Candidate cash</dt>
          <dd>
            {cash
              ? formatMetricValue("closing_cash", cash.candidate_mean, {
                  compact: true,
                })
              : "—"}
          </dd>
        </div>
      </dl>
      <Link
        className="control-link"
        to={`/scenarios/${encodeURIComponent(decision.scenario_id)}/compare?experiment=${decision.experiment_id}`}
      >
        Open decision room
      </Link>
    </article>
  );
}

export function BriefingPage() {
  const control = useControlTower();
  const state = (
    <StatePanel
      error={control.error}
      isPending={control.isPending}
      retry={control.retry}
    />
  );
  if (control.isPending || control.error) {
    return state;
  }
  const decisions = control.decisions?.items ?? [];
  const frontier = control.frontier;
  return (
    <div className="control-page">
      <PageHeader
        eyebrow="Enterprise control tower"
        title="Decision briefing"
        summary="A governed view of current policy choices, evidence quality, and the operating model behind them."
      />
      <section aria-label="Decision posture" className="control-kpis">
        <div>
          <span>Model</span>
          <strong>v{control.company?.model_version}</strong>
          <small>Synthetic reference</small>
        </div>
        <div>
          <span>Recent choices</span>
          <strong>{decisions.length}</strong>
          <small>Latest completed candidates</small>
        </div>
        <div>
          <span>Pareto frontier</span>
          <strong>{frontier?.points.length ?? 0}</strong>
          <small>Non-dominated feasible policies</small>
        </div>
        <div>
          <span>Decision gate</span>
          <strong>30</strong>
          <small>Minimum paired replications</small>
        </div>
      </section>

      <div className="control-grid">
        <section aria-labelledby="queue-title" className="control-section">
          <div className="control-section__heading">
            <div>
              <p>Review queue</p>
              <h2 id="queue-title">Latest decisions</h2>
            </div>
            <Link className="control-link" to="/decisions">
              View portfolio
            </Link>
          </div>
          {decisions.length ? (
            <div className="decision-card-list">
              {decisions.slice(0, 3).map((decision) => (
                <DecisionCard decision={decision} key={decision.experiment_id} />
              ))}
            </div>
          ) : (
            <div className="control-empty">
              <h3>No candidate evidence yet</h3>
              <p>Run a paired scenario to create the first governed decision.</p>
              <Link className="control-link" to="/scenarios">
                Create scenario
              </Link>
            </div>
          )}
        </section>

        <aside aria-labelledby="frontier-title" className="control-section">
          <div className="control-section__heading">
            <div>
              <p>Multi-objective choice</p>
              <h2 id="frontier-title">Pareto-efficient policy set</h2>
            </div>
          </div>
          {frontier?.points.length ? (
            <ol className="frontier-list">
              {frontier.points.map((point) => (
                <li key={point.experiment_id}>
                  <strong>{point.scenario_name}</strong>
                  <span>
                    EBITDA {formatMetricValue("ebitda", point.ebitda_delta, {
                      compact: true,
                      difference: true,
                    })}
                  </span>
                  <span>
                    FCF {formatMetricValue("free_cash_flow", point.free_cash_flow_delta, {
                      compact: true,
                      difference: true,
                    })}
                  </span>
                  <span>
                    OTIF {formatPercent(point.otif_delta, { signDisplay: "always" })}
                  </span>
                </li>
              ))}
            </ol>
          ) : (
            <div className="control-empty control-empty--compact">
              <h3>No decision-grade frontier</h3>
              <p>Complete at least 30 paired replications per feasible policy.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

export function TwinPage() {
  const control = useControlTower();
  if (control.isPending || control.error) {
    return (
      <StatePanel
        error={control.error}
        isPending={control.isPending}
        retry={control.retry}
      />
    );
  }
  const company = control.company;
  const baseline = control.baseline;
  return (
    <div className="control-page">
      <PageHeader
        eyebrow="Versioned operating model"
        title="Company twin"
        summary="The products, customer contracts, finite resources, materials, and lifecycle that move together in every experiment."
      />
      <section className="twin-overview" aria-label="Twin scope">
        <div>
          <span>Company</span>
          <strong>{company?.name}</strong>
        </div>
        <div>
          <span>Products</span>
          <strong>{company?.products.length}</strong>
        </div>
        <div>
          <span>Evaluation</span>
          <strong>{baseline?.evaluation_days} days</strong>
        </div>
        <div>
          <span>Runoff</span>
          <strong>{baseline?.runoff_days} days</strong>
        </div>
      </section>
      <div className="twin-grid">
        <section className="control-section" aria-labelledby="products-title">
          <h2 id="products-title">Product economics</h2>
          <ul className="model-list">
            {company?.products.map((product) => (
              <li key={product.product_id}>
                <strong>{product.name}</strong>
                <span>
                  Standard price {formatMetricValue("revenue", product.standard_price_cents)}
                </span>
              </li>
            ))}
          </ul>
        </section>
        <section className="control-section" aria-labelledby="resources-title">
          <h2 id="resources-title">Finite capacity</h2>
          <ul className="model-list">
            {company?.plant.resources.map((resource) => (
              <li key={resource.resource_id}>
                <strong>{resource.resource_id}</strong>
                <span>{resource.daily_capacity_minutes.toLocaleString()} min/day</span>
                <span>{resource.max_overtime_minutes.toLocaleString()} min overtime cap</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="control-section" aria-labelledby="materials-title">
          <h2 id="materials-title">Supply commitments</h2>
          <ul className="model-list">
            {company?.plant.materials.map((material) => (
              <li key={material.material_id}>
                <strong>{material.name}</strong>
                <span>{material.supplier_lead_time_days} day supplier lead time</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <Link className="control-link control-link--primary" to="/scenarios">
        Stress the operating model
      </Link>
    </div>
  );
}

export function DecisionsPage() {
  const control = useControlTower();
  if (control.isPending || control.error) {
    return (
      <StatePanel
        error={control.error}
        isPending={control.isPending}
        retry={control.retry}
      />
    );
  }
  const decisions = control.decisions?.items ?? [];
  return (
    <div className="control-page">
      <PageHeader
        eyebrow="Governed policy choices"
        title="Decision portfolio"
        summary="Every recommendation is paired, constraint-aware, and traceable to its evidence digest."
      />
      {control.frontier?.points.length ? (
        <section className="control-section" aria-labelledby="frontier-table-title">
          <div className="control-section__heading">
            <div>
              <p>Feasible non-dominated choices</p>
              <h2 id="frontier-table-title">Policy frontier</h2>
            </div>
          </div>
          <div className="control-table-wrap">
            <table className="control-table">
              <thead>
                <tr>
                  <th scope="col">Policy</th>
                  <th scope="col">EBITDA delta</th>
                  <th scope="col">FCF delta</th>
                  <th scope="col">OTIF delta</th>
                </tr>
              </thead>
              <tbody>
                {control.frontier.points.map((point) => (
                  <tr key={point.experiment_id}>
                    <th scope="row">{point.scenario_name}</th>
                    <td>{formatMetricValue("ebitda", point.ebitda_delta, { compact: true, difference: true })}</td>
                    <td>{formatMetricValue("free_cash_flow", point.free_cash_flow_delta, { compact: true, difference: true })}</td>
                    <td>{formatPercent(point.otif_delta, { signDisplay: "always" })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
      <section className="control-section" aria-labelledby="portfolio-title">
        <div className="control-section__heading">
          <div>
            <p>{decisions.length} recent completed candidates</p>
            <h2 id="portfolio-title">Recommendation register</h2>
          </div>
          <Link className="control-link" to="/scenarios">Create variant</Link>
        </div>
        {decisions.length ? (
          <div className="decision-card-list decision-card-list--three">
            {decisions.map((decision) => (
              <DecisionCard decision={decision} key={decision.experiment_id} />
            ))}
          </div>
        ) : (
          <div className="control-empty">
            <h3>No recommendations recorded</h3>
            <p>Run a paired policy experiment to populate the register.</p>
            <Link className="control-link" to="/scenarios">Create scenario</Link>
          </div>
        )}
      </section>
    </div>
  );
}

export function ReportsPage() {
  const control = useControlTower();
  if (control.isPending || control.error) {
    return (
      <StatePanel
        error={control.error}
        isPending={control.isPending}
        retry={control.retry}
      />
    );
  }
  const decisions = control.decisions?.items ?? [];
  return (
    <div className="control-page">
      <PageHeader
        eyebrow="Board-ready evidence"
        title="Decision briefs"
        summary="Published recommendations with named owners, review dates, assumptions, and reproducibility records."
      />
      <section className="control-section" aria-label="Decision brief register">
        {decisions.length ? (
          <div className="report-register">
            {decisions.map((decision) => (
            <article key={decision.experiment_id}>
              <div>
                <DecisionStatusLabel
                  evidenceGrade={decision.evidence_grade}
                  status={decision.decision_status}
                />
                <p>{formatDate(decision.completed_at)}</p>
              </div>
              <h2>{decision.scenario_name}</h2>
              <p>{decision.headline}</p>
              <dl>
                <div>
                  <dt>Evidence</dt>
                  <dd>{decision.replication_count} paired runs</dd>
                </div>
                <div>
                  <dt>Digest</dt>
                  <dd><code>{decision.brief_digest.slice(0, 12)}</code></dd>
                </div>
              </dl>
              <Link className="control-link" to={`/reports/${decision.experiment_id}`}>
                Open brief
              </Link>
            </article>
            ))}
          </div>
        ) : (
          <div className="control-empty">
            <h2>No published decision briefs</h2>
            <p>Decision briefs appear after a candidate experiment completes.</p>
            <Link className="control-link" to="/scenarios">Run first comparison</Link>
          </div>
        )}
      </section>
    </div>
  );
}

export function NotFoundPage() {
  return (
    <section className="control-state">
      <p className="control-header__eyebrow">404</p>
      <h1>Workspace not found</h1>
      <p>The requested route is not part of this decision workspace.</p>
      <Link className="control-link" to="/">
        Return to briefing
      </Link>
    </section>
  );
}
