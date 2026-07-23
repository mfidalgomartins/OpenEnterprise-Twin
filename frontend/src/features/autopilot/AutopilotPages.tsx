import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { ApiError } from "../../lib/api";
import {
  Badge,
  Meter,
  Panel,
  ScoreDial,
  Stat,
  StateBanner,
  type Tone,
} from "./components";
import {
  compareAdaptivePolicy,
  getLedgerDecision,
  getMonitoring,
  ingestSyntheticDataset,
  listLedgerDecisions,
  runCalibration,
  runOptimization,
} from "./api";
import type {
  AdaptiveComparison,
  CalibrationResponse,
  DatasetIngestResponse,
  DecisionState,
  MonitoringReport,
  OptimizationResponse,
} from "./types";
import {
  formatCents,
  formatNumber,
  formatPercent,
  formatSignedMoney,
  metricIsMoney,
  metricLabel,
  titleCase,
} from "./format";

function errorDetail(error: unknown): string {
  return error instanceof ApiError
    ? error.problem.detail
    : "The request could not be completed.";
}

function credibilityTone(band: string): "high" | "medium" | "low" {
  if (band === "decision_grade") return "high";
  if (band === "supporting" || band === "provisional") return "medium";
  return "low";
}

// --- Calibration Studio ------------------------------------------------------

export function CalibrationStudioPage() {
  const [dataset, setDataset] = useState<DatasetIngestResponse | null>(null);
  const [calibration, setCalibration] = useState<CalibrationResponse | null>(
    null,
  );

  const ingest = useMutation({
    mutationFn: () => ingestSyntheticDataset("northstar-history", 540),
    onSuccess: (data) => {
      setDataset(data);
      setCalibration(null);
    },
  });
  const calibrate = useMutation({
    mutationFn: () =>
      runCalibration("northstar-cal", "northstar-history", "2024-12-31"),
    onSuccess: setCalibration,
  });

  const provenance = calibration
    ? calibration.calibration.parameters.reduce<Record<string, number>>(
        (accumulator, parameter) => {
          accumulator[parameter.provenance] =
            (accumulator[parameter.provenance] ?? 0) + 1;
          return accumulator;
        },
        {},
      )
    : {};

  return (
    <div className="ap-layout">
      <Panel
        title="Calibration Studio"
        description="Fit the Northstar twin to reproducible operating history and score how far it can be trusted."
        actions={
          <div className="ap-actions">
            <button
              type="button"
              className="ap-button"
              onClick={() => ingest.mutate()}
              disabled={ingest.isPending}
            >
              {ingest.isPending ? "Importing…" : "Import history"}
            </button>
            <button
              type="button"
              className="ap-button ap-button--primary"
              onClick={() => calibrate.mutate()}
              disabled={!dataset || calibrate.isPending}
            >
              {calibrate.isPending ? "Calibrating…" : "Calibrate & backtest"}
            </button>
          </div>
        }
      >
        {ingest.isError ? (
          <StateBanner
            kind="error"
            title="Import failed"
            detail={errorDetail(ingest.error)}
          />
        ) : null}
        {!dataset ? (
          <StateBanner
            kind="empty"
            title="No history imported yet"
            detail="Import the synthetic Northstar history to profile its quality, then calibrate."
          />
        ) : (
          <div className="ap-quality">
            <dl className="ap-stat-row">
              <Stat
                label="Observations"
                value={formatNumber(dataset.dataset.observation_count, 0)}
              />
              <Stat
                label="Series"
                value={String(dataset.quality.distinct_series)}
              />
              <Stat
                label="Quality score"
                value={formatPercent(dataset.quality.quality_score)}
                tone={dataset.quality.quality_score >= 0.95 ? "positive" : "neutral"}
              />
              <Stat
                label="Blocking issues"
                value={String(
                  dataset.quality.issues.filter((i) => i.severity === "error")
                    .length,
                )}
                tone={
                  dataset.quality.issues.some((i) => i.severity === "error")
                    ? "negative"
                    : "positive"
                }
              />
            </dl>
            <div className="ap-meters">
              {dataset.quality.components.map((component) => (
                <Meter
                  key={component.name}
                  label={titleCase(component.name)}
                  value={component.value}
                  detail={component.detail}
                />
              ))}
            </div>
          </div>
        )}
      </Panel>

      {calibrate.isError ? (
        <Panel title="Credibility">
          <StateBanner
            kind="error"
            title="Calibration failed"
            detail={errorDetail(calibrate.error)}
          />
        </Panel>
      ) : null}

      {calibration ? (
        <Panel
          title="Credibility"
          description="A transparent, weighted score. Every component is traceable back to its inputs."
        >
          <div className="ap-credibility">
            <ScoreDial
              value={calibration.credibility.score}
              max={100}
              label={titleCase(calibration.credibility.band)}
              tone={credibilityTone(calibration.credibility.band)}
            />
            <div className="ap-credibility__components">
              {calibration.credibility.components.map((component) => (
                <div key={component.name} className="ap-contrib">
                  <div className="ap-contrib__head">
                    <span>{titleCase(component.name)}</span>
                    <span className="ap-contrib__weight">
                      w {component.weight.toFixed(2)}
                    </span>
                  </div>
                  <div className="ap-contrib__track">
                    <div
                      className="ap-contrib__fill"
                      style={{ width: `${(component.normalized * 100).toFixed(1)}%` }}
                    />
                  </div>
                  <p className="ap-contrib__detail">{component.detail}</p>
                </div>
              ))}
            </div>
          </div>
          <div className="ap-provenance">
            <h3 className="ap-subhead">Parameter provenance</h3>
            <div className="ap-chips">
              {(["observed", "estimated", "assumed"] as const).map((kind) => (
                <span key={kind} className={`ap-chip ap-chip--${kind}`}>
                  {titleCase(kind)}: {provenance[kind] ?? 0}
                </span>
              ))}
            </div>
          </div>
          {calibration.backtests[0] ? (
            <div className="ap-backtest">
              <h3 className="ap-subhead">Out-of-sample backtest</h3>
              <dl className="ap-stat-row">
                <Stat
                  label="Weighted MAPE"
                  value={formatPercent(
                    calibration.backtests[0].overall_weighted_mape,
                  )}
                  tone={
                    calibration.backtests[0].overall_weighted_mape < 0.15
                      ? "positive"
                      : "neutral"
                  }
                />
                <Stat
                  label="Interval coverage"
                  value={formatPercent(
                    calibration.backtests[0].overall_interval_coverage,
                  )}
                />
                <Stat
                  label="Nominal coverage"
                  value={formatPercent(
                    calibration.backtests[0].nominal_coverage,
                  )}
                />
              </dl>
            </div>
          ) : null}
          {calibration.calibration.warnings.length > 0 ? (
            <ul className="ap-warnings">
              {calibration.calibration.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}

// --- Optimization Lab --------------------------------------------------------

export function OptimizationLabPage() {
  const [seed, setSeed] = useState(731);
  const [requireNoRescue, setRequireNoRescue] = useState(true);
  const [result, setResult] = useState<OptimizationResponse | null>(null);

  const optimize = useMutation({
    mutationFn: () =>
      runOptimization({
        commercialLower: -0.1,
        commercialUpper: 0.3,
        overtimeUpper: 400,
        requireNoRescue,
        populationSize: 12,
        maxGenerations: 6,
        maxEvaluations: 120,
        seed,
        horizonDays: 120,
        replications: 6,
      }),
    onSuccess: setResult,
  });

  return (
    <div className="ap-layout">
      <Panel
        title="Optimization Lab"
        description="Search the policy space for the efficient frontier of EBITDA and service level under explicit constraints."
        actions={
          <div className="ap-actions">
            <label className="ap-field ap-field--inline">
              <span>Seed</span>
              <input
                className="ap-input"
                type="number"
                value={seed}
                min={0}
                onChange={(event) => setSeed(Number(event.target.value))}
              />
            </label>
            <label className="ap-check">
              <input
                type="checkbox"
                checked={requireNoRescue}
                onChange={(event) => setRequireNoRescue(event.target.checked)}
              />
              <span>No rescue funding</span>
            </label>
            <button
              type="button"
              className="ap-button ap-button--primary"
              onClick={() => optimize.mutate()}
              disabled={optimize.isPending}
            >
              {optimize.isPending ? "Searching…" : "Run NSGA-II"}
            </button>
          </div>
        }
      >
        {optimize.isPending ? (
          <StateBanner
            kind="loading"
            title="Evaluating candidate policies"
            detail="Running a bounded, deterministic multi-objective search over the engine."
          />
        ) : null}
        {optimize.isError ? (
          <StateBanner
            kind="error"
            title="Optimization rejected"
            detail={errorDetail(optimize.error)}
          />
        ) : null}
        {!result && !optimize.isPending ? (
          <StateBanner
            kind="empty"
            title="No frontier yet"
            detail="Run the optimizer to reveal efficient trade-offs between profit and service."
          />
        ) : null}
        {result ? (
          <OptimizationResultView result={result} />
        ) : null}
      </Panel>
    </div>
  );
}

function OptimizationResultView({ result }: { result: OptimizationResponse }) {
  const { frontier, recommended, dominated, infeasible, evaluations, converged } =
    result.result;
  return (
    <div className="ap-optimization">
      <dl className="ap-stat-row">
        <Stat label="Evaluations" value={String(evaluations)} />
        <Stat label="Frontier size" value={String(frontier.length)} />
        <Stat label="Dominated" value={String(dominated.length)} />
        <Stat
          label="Infeasible"
          value={String(infeasible.length)}
          tone={infeasible.length > 0 ? "negative" : "neutral"}
        />
        <Stat
          label="Converged"
          value={converged ? "Yes" : "Budget reached"}
          tone={converged ? "positive" : "neutral"}
        />
      </dl>
      <table className="ap-table">
        <caption className="ap-table__caption">
          Efficient policies (Pareto frontier), best EBITDA first
        </caption>
        <thead>
          <tr>
            <th scope="col">Policy</th>
            <th scope="col">EBITDA</th>
            <th scope="col">OTIF</th>
            <th scope="col">Robustness</th>
            <th scope="col" />
          </tr>
        </thead>
        <tbody>
          {frontier.map((candidate) => (
            <tr
              key={candidate.candidate_id}
              className={
                recommended?.candidate_id === candidate.candidate_id
                  ? "ap-table__row--recommended"
                  : undefined
              }
            >
              <th scope="row">#{candidate.candidate_id}</th>
              <td>{formatMoneyValue(candidate.objective_values.ebitda)}</td>
              <td>{formatPercent(candidate.objective_values.otif ?? 0)}</td>
              <td>{formatPercent(candidate.robustness)}</td>
              <td>
                {recommended?.candidate_id === candidate.candidate_id ? (
                  <Badge tone="high">Recommended</Badge>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {infeasible.length > 0 ? (
        <p className="ap-note">
          {infeasible.length} candidate(s) excluded — e.g. {infeasible[0]
            .exclusion_reason ?? "constraint violation"}.
        </p>
      ) : null}
    </div>
  );
}

function formatMoneyValue(value: number | undefined): string {
  if (value === undefined) return "—";
  return formatSignedMoney(value);
}

// --- Adaptive Policy Builder -------------------------------------------------

export function AdaptivePolicyPage() {
  const [threshold, setThreshold] = useState(8);
  const [result, setResult] = useState<AdaptiveComparison | null>(null);

  const compare = useMutation({
    mutationFn: () =>
      compareAdaptivePolicy({
        metric: "backlog_days",
        operator: "gt",
        threshold,
        windowPeriods: 5,
        persistencePeriods: 3,
        cooldownPeriods: 15,
        maxActivations: 5,
        horizonDays: 120,
        replications: 6,
        seed: 731,
      }),
    onSuccess: setResult,
  });

  return (
    <div className="ap-layout">
      <Panel
        title="Adaptive Policy Builder"
        description="A conditional rule that adds capacity when backlog builds — compared against the static plan on identical shocks."
        actions={
          <div className="ap-actions">
            <label className="ap-field ap-field--inline">
              <span>Backlog trigger (days)</span>
              <input
                className="ap-input"
                type="number"
                min={1}
                max={60}
                value={threshold}
                onChange={(event) => setThreshold(Number(event.target.value))}
              />
            </label>
            <button
              type="button"
              className="ap-button ap-button--primary"
              onClick={() => compare.mutate()}
              disabled={compare.isPending}
            >
              {compare.isPending ? "Comparing…" : "Compare vs static"}
            </button>
          </div>
        }
      >
        <p className="ap-rule">
          <Badge tone="observed">IF</Badge> backlog_days &gt; {threshold} for 3
          of 5 periods → <Badge tone="high">add overtime capacity</Badge> (cooldown
          15, max 5 activations)
        </p>
        {compare.isPending ? (
          <StateBanner
            kind="loading"
            title="Running paired replications"
            detail="Evaluating the adaptive and static plans over the same shock tapes."
          />
        ) : null}
        {compare.isError ? (
          <StateBanner
            kind="error"
            title="Comparison failed"
            detail={errorDetail(compare.error)}
          />
        ) : null}
        {!result && !compare.isPending ? (
          <StateBanner
            kind="empty"
            title="No comparison yet"
            detail="Run the comparison to see how the conditional policy changes outcomes."
          />
        ) : null}
        {result ? (
          <div className="ap-adaptive">
            <dl className="ap-stat-row">
              <Stat
                label="Activations"
                value={String(result.activation_count)}
              />
              <Stat
                label="Action cost"
                value={formatCents(result.total_action_cost_cents)}
              />
              <Stat
                label="EBITDA delta"
                value={formatSignedMoney(result.metric_deltas.ebitda ?? 0)}
                tone={(result.metric_deltas.ebitda ?? 0) >= 0 ? "positive" : "negative"}
              />
              <Stat
                label="OTIF delta"
                value={formatPercent(result.metric_deltas.otif ?? 0)}
                tone={(result.metric_deltas.otif ?? 0) >= 0 ? "positive" : "negative"}
              />
            </dl>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

// --- Decision Ledger ---------------------------------------------------------

const STATE_TONE: Record<DecisionState, Tone> = {
  draft: "neutral",
  evidence_ready: "observed",
  under_review: "warning",
  approved: "high",
  implemented: "high",
  monitoring: "observed",
  successful: "high",
  underperformed: "risk",
  superseded: "neutral",
  abandoned: "risk",
};

export function DecisionLedgerPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const decisions = useQuery({
    queryKey: ["ledger-decisions"],
    queryFn: listLedgerDecisions,
  });
  const detail = useQuery({
    queryKey: ["ledger-decision", selected],
    queryFn: () => getLedgerDecision(selected as string),
    enabled: selected !== null,
  });

  return (
    <div className="ap-layout ap-layout--split">
      <Panel
        title="Decision Ledger"
        description="Every governed decision, its state and its append-only audit trail."
      >
        {decisions.isPending ? (
          <StateBanner kind="loading" title="Loading decisions" />
        ) : decisions.isError ? (
          <StateBanner
            kind="error"
            title="Could not load the ledger"
            detail={errorDetail(decisions.error)}
          />
        ) : (decisions.data ?? []).length === 0 ? (
          <StateBanner
            kind="empty"
            title="No governed decisions yet"
            detail="Create a decision through the API to see it tracked here."
          />
        ) : (
          <ul className="ap-pipeline">
            {(decisions.data ?? []).map((item) => (
              <li key={item.decision_id}>
                <button
                  type="button"
                  className={`ap-pipeline__item${
                    selected === item.decision_id
                      ? " ap-pipeline__item--active"
                      : ""
                  }`}
                  onClick={() => setSelected(item.decision_id)}
                >
                  <span className="ap-pipeline__title">{item.title}</span>
                  <span className="ap-pipeline__meta">
                    <Badge tone={STATE_TONE[item.state]}>
                      {titleCase(item.state)}
                    </Badge>
                    <span className="ap-pipeline__owner">{item.owner}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Audit trail">
        {selected === null ? (
          <StateBanner
            kind="empty"
            title="Select a decision"
            detail="Its lifecycle transitions and approvals appear here."
          />
        ) : detail.isPending ? (
          <StateBanner kind="loading" title="Loading audit trail" />
        ) : detail.isError ? (
          <StateBanner
            kind="error"
            title="Could not load the decision"
            detail={errorDetail(detail.error)}
          />
        ) : detail.data ? (
          <div className="ap-audit">
            <dl className="ap-stat-row">
              <Stat label="State" value={titleCase(detail.data.state)} />
              <Stat label="Version" value={String(detail.data.version)} />
              <Stat label="Owner" value={detail.data.owner} />
              <Stat label="Approvals" value={String(detail.data.approvals.length)} />
            </dl>
            <ol className="ap-timeline">
              {detail.data.transitions.map((transition, index) => (
                <li key={index} className="ap-timeline__item">
                  <span className="ap-timeline__state">
                    {titleCase(transition.to_state)}
                  </span>
                  <span className="ap-timeline__meta">
                    {transition.actor}
                    {transition.note ? ` · ${transition.note}` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </Panel>
    </div>
  );
}

// --- Monitoring Center -------------------------------------------------------

const LEVEL_TONE: Record<string, Tone> = {
  within_expectation: "high",
  early_warning: "warning",
  material_deviation: "warning",
  recalibration_required: "warning",
  decision_review_required: "risk",
};

export function MonitoringCenterPage() {
  const [decisionId, setDecisionId] = useState("");
  const [report, setReport] = useState<MonitoringReport | null>(null);
  const [notFound, setNotFound] = useState(false);

  const load = useMutation({
    mutationFn: () => getMonitoring(decisionId.trim()),
    onSuccess: (data) => {
      setReport(data);
      setNotFound(false);
    },
    onError: (error) => {
      setReport(null);
      setNotFound(error instanceof ApiError && error.status === 404);
    },
  });

  return (
    <div className="ap-layout">
      <Panel
        title="Monitoring Center"
        description="Compare realised outcomes with the prediction that justified a decision, and surface drift."
        actions={
          <form
            className="ap-actions"
            onSubmit={(event) => {
              event.preventDefault();
              if (decisionId.trim()) load.mutate();
            }}
          >
            <label className="ap-field ap-field--inline">
              <span>Decision ID</span>
              <input
                className="ap-input"
                value={decisionId}
                placeholder="northstar-pricing"
                onChange={(event) => setDecisionId(event.target.value)}
              />
            </label>
            <button
              type="submit"
              className="ap-button ap-button--primary"
              disabled={load.isPending || !decisionId.trim()}
            >
              {load.isPending ? "Loading…" : "Load outcomes"}
            </button>
          </form>
        }
      >
        {notFound ? (
          <StateBanner
            kind="empty"
            title="No outcomes recorded"
            detail="This decision has no realised outcomes yet, or the ID is unknown."
          />
        ) : null}
        {load.isError && !notFound ? (
          <StateBanner
            kind="error"
            title="Could not load monitoring"
            detail={errorDetail(load.error)}
          />
        ) : null}
        {!report && !load.isPending && !notFound ? (
          <StateBanner
            kind="empty"
            title="Enter a decision"
            detail="Load a decision's monitoring report to see expected-vs-actual and drift."
          />
        ) : null}
        {report ? <MonitoringReportView report={report} /> : null}
      </Panel>
    </div>
  );
}

function MonitoringReportView({ report }: { report: MonitoringReport }) {
  return (
    <div className="ap-monitoring">
      <div className="ap-monitoring__head">
        <Badge tone={LEVEL_TONE[report.recommended_level] ?? "neutral"}>
          {titleCase(report.recommended_level)}
        </Badge>
        {report.drift.recalibration_required ? (
          <Badge tone="risk">Recalibration required</Badge>
        ) : null}
      </div>
      <div className="ap-meters">
        <Meter
          label="Result drift"
          value={report.drift.result_drift}
          tone={report.drift.result_drift > 0.5 ? "risk" : "warning"}
        />
        <Meter
          label="Parameter drift"
          value={report.drift.parameter_drift}
          tone="warning"
        />
        <Meter
          label="Data drift"
          value={report.drift.data_drift}
          tone="warning"
        />
      </div>
      <table className="ap-table">
        <caption className="ap-table__caption">Expected vs realised</caption>
        <thead>
          <tr>
            <th scope="col">KPI</th>
            <th scope="col">Expected</th>
            <th scope="col">Realised</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {report.kpis.map((kpi) => (
            <tr key={kpi.metric_name}>
              <th scope="row">{metricLabel(kpi.metric_name)}</th>
              <td>{formatKpi(kpi.metric_name, kpi.expected_mean)}</td>
              <td>{formatKpi(kpi.metric_name, kpi.realized_value)}</td>
              <td>
                <Badge tone={LEVEL_TONE[kpi.level] ?? "neutral"}>
                  {titleCase(kpi.level)}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {report.alerts.length > 0 ? (
        <ul className="ap-alerts">
          {report.alerts.map((alert, index) => (
            <li key={index} className={`ap-alert ap-alert--${alert.severity}`}>
              {alert.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function formatKpi(metric: string, value: number): string {
  if (metricIsMoney(metric)) return formatSignedMoney(value);
  if (metric === "otif" || metric === "capacity_utilization") {
    return formatPercent(value);
  }
  return formatNumber(value);
}
