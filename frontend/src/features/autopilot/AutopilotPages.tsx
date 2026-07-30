import { type ChangeEvent, useRef, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { Link } from "wouter";

import { ApiError } from "../../lib/api";
import { useAuth } from "../auth/authContext";
import { JobStatus } from "../jobs/JobStatus";
import { useTrackedJob } from "../jobs/useJob";
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
  createLedgerDecision,
  downloadDatasetCsv,
  getLedgerDecision,
  getMonitoring,
  ingestCsvDataset,
  ingestSyntheticDataset,
  listLedgerDecisions,
  recordOutcomes,
  runCalibration,
  runOptimization,
  transitionLedgerDecision,
} from "./api";
import type {
  AdaptiveComparison,
  CalibrationResponse,
  DatasetIngestResponse,
  DecisionSnapshot,
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
  sanitizeDatasetId,
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

interface LoadedDataset {
  response: DatasetIngestResponse;
  datasetId: string;
  backtestCutoff: string | null;
}

export function CalibrationStudioPage() {
  const { can } = useAuth();
  const [dataset, setDataset] = useState<LoadedDataset | null>(null);
  const execution = useTrackedJob<CalibrationResponse>();
  const calibration = execution.resultQuery.data ?? null;
  const fileInput = useRef<HTMLInputElement>(null);

  const onLoaded = (loaded: LoadedDataset) => {
    setDataset(loaded);
  };

  const ingest = useMutation({
    mutationFn: () => ingestSyntheticDataset("northstar-history", 540),
    onSuccess: (response) =>
      onLoaded({
        response,
        datasetId: "northstar-history",
        backtestCutoff: "2024-12-31",
      }),
  });
  const ingestCsv = useMutation({
    mutationFn: async (file: File) => {
      const datasetId = sanitizeDatasetId(file.name);
      const response = await ingestCsvDataset(
        datasetId,
        "northstar-components",
        await file.text(),
      );
      return { response, datasetId, backtestCutoff: null };
    },
    onSuccess: onLoaded,
  });
  const calibrate = useMutation({
    mutationFn: () => {
      if (!dataset) throw new Error("no dataset loaded");
      return runCalibration(
        `${dataset.datasetId}-cal`,
        dataset.datasetId,
        dataset.backtestCutoff,
      );
    },
    onSuccess: execution.track,
  });
  const exportCsv = useMutation({
    mutationFn: () => downloadDatasetCsv(dataset?.datasetId ?? ""),
  });

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) ingestCsv.mutate(file);
    event.target.value = "";
  };

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
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              className="ap-visually-hidden"
              aria-label="Import CSV history"
              onChange={onFileChange}
            />
            <button
              type="button"
              className="ap-button"
              onClick={() => ingest.mutate()}
              disabled={ingest.isPending}
            >
              {ingest.isPending ? "Importing…" : "Import synthetic"}
            </button>
            <button
              type="button"
              className="ap-button"
              onClick={() => fileInput.current?.click()}
              disabled={ingestCsv.isPending}
            >
              {ingestCsv.isPending ? "Reading CSV…" : "Import CSV"}
            </button>
            <button
              type="button"
              className="ap-button ap-button--primary"
              onClick={() => calibrate.mutate()}
              disabled={
                !dataset || calibrate.isPending || execution.isActive
              }
            >
              {calibrate.isPending || execution.isActive
                ? "Calibrating…"
                : "Calibrate & backtest"}
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
        {ingestCsv.isError ? (
          <StateBanner
            kind="error"
            title="CSV import failed"
            detail={errorDetail(ingestCsv.error)}
          />
        ) : null}
        {execution.currentJob ? (
          <JobStatus
            canCancel={can("analyst", "admin")}
            isCancelling={execution.cancellation.isPending}
            job={execution.currentJob}
            onCancel={execution.cancel}
          />
        ) : null}
        {!dataset ? (
          <StateBanner
            kind="empty"
            title="No history imported yet"
            detail="Import the synthetic Northstar history — or upload a long-format CSV (period_date, series, entity_id, value, unit) — to profile its quality, then calibrate."
          />
        ) : (
          <div className="ap-quality">
            <dl className="ap-stat-row">
              <Stat
                label="Observations"
                value={formatNumber(dataset.response.dataset.observation_count, 0)}
              />
              <Stat
                label="Series"
                value={String(dataset.response.quality.distinct_series)}
              />
              <Stat
                label="Quality score"
                value={formatPercent(dataset.response.quality.quality_score)}
                tone={
                  dataset.response.quality.quality_score >= 0.95
                    ? "positive"
                    : "neutral"
                }
              />
              <Stat
                label="Blocking issues"
                value={String(
                  dataset.response.quality.issues.filter(
                    (i) => i.severity === "error",
                  ).length,
                )}
                tone={
                  dataset.response.quality.issues.some(
                    (i) => i.severity === "error",
                  )
                    ? "negative"
                    : "positive"
                }
              />
            </dl>
            <div className="ap-meters">
              {dataset.response.quality.components.map((component) => (
                <Meter
                  key={component.name}
                  label={titleCase(component.name)}
                  value={component.value}
                  detail={component.detail}
                />
              ))}
            </div>
            <div className="ap-actions">
              <button
                type="button"
                className="ap-button"
                onClick={() => exportCsv.mutate()}
                disabled={exportCsv.isPending}
              >
                {exportCsv.isPending ? "Preparing…" : "Export CSV"}
              </button>
              {exportCsv.isError ? (
                <span className="ap-note">{errorDetail(exportCsv.error)}</span>
              ) : null}
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
      {execution.jobQuery.isError || execution.resultQuery.isError ? (
        <Panel title="Credibility">
          <StateBanner
            kind="error"
            title="Calibration result unavailable"
            detail={errorDetail(
              execution.jobQuery.error ?? execution.resultQuery.error,
            )}
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
  const { can } = useAuth();
  const [seed, setSeed] = useState(731);
  const [requireNoRescue, setRequireNoRescue] = useState(true);
  const execution = useTrackedJob<OptimizationResponse>();
  const result = execution.resultQuery.data ?? null;

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
    onSuccess: execution.track,
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
              disabled={optimize.isPending || execution.isActive}
            >
              {optimize.isPending || execution.isActive
                ? "Searching…"
                : "Run NSGA-II"}
            </button>
          </div>
        }
      >
        {execution.currentJob ? (
          <JobStatus
            canCancel={can("analyst", "admin")}
            isCancelling={execution.cancellation.isPending}
            job={execution.currentJob}
            onCancel={execution.cancel}
          />
        ) : optimize.isPending ? (
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
        {!result && !optimize.isPending && !execution.currentJob ? (
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

const ADAPTIVE_RULE = {
  metric: "backlog_days" as const,
  operator: "gt" as const,
  windowPeriods: 5,
  persistencePeriods: 3,
  cooldownPeriods: 15,
  maxActivations: 5,
  horizonDays: 120,
  replications: 6,
  seed: 731,
};

export function AdaptivePolicyPage() {
  const { can } = useAuth();
  const [threshold, setThreshold] = useState(8);
  const execution = useTrackedJob<AdaptiveComparison>();
  const result = execution.resultQuery.data ?? null;

  const compare = useMutation({
    mutationFn: () => compareAdaptivePolicy({ ...ADAPTIVE_RULE, threshold }),
    onSuccess: execution.track,
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
              disabled={compare.isPending || execution.isActive}
            >
              {compare.isPending || execution.isActive
                ? "Comparing…"
                : "Compare vs static"}
            </button>
          </div>
        }
      >
        <p className="ap-rule">
          <Badge tone="observed">IF</Badge> {ADAPTIVE_RULE.metric} &gt;{" "}
          {threshold} for {ADAPTIVE_RULE.persistencePeriods} of{" "}
          {ADAPTIVE_RULE.windowPeriods} periods →{" "}
          <Badge tone="high">add overtime capacity</Badge> (cooldown{" "}
          {ADAPTIVE_RULE.cooldownPeriods}, max {ADAPTIVE_RULE.maxActivations}{" "}
          activations)
        </p>
        {execution.currentJob ? (
          <JobStatus
            canCancel={can("analyst", "admin")}
            isCancelling={execution.cancellation.isPending}
            job={execution.currentJob}
            onCancel={execution.cancel}
          />
        ) : compare.isPending ? (
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
        {!result && !compare.isPending && !execution.currentJob ? (
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
  const [drafting, setDrafting] = useState(false);
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["ledger-decisions"] });
    void queryClient.invalidateQueries({ queryKey: ["ledger-decision"] });
  };

  return (
    <div className="ap-layout ap-layout--split">
      <Panel
        title="Decision Ledger"
        description="Every governed decision, its state and its append-only audit trail."
        actions={
          <button
            type="button"
            className="ap-button ap-button--primary"
            onClick={() => setDrafting((open) => !open)}
          >
            {drafting ? "Close" : "New decision"}
          </button>
        }
      >
        {drafting ? (
          <NewDecisionForm
            onCreated={(decisionId) => {
              setDrafting(false);
              setSelected(decisionId);
              refresh();
            }}
          />
        ) : null}
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
            <GovernedActions snapshot={detail.data} onDone={refresh} />
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

/** The governed next step offered for each state, in lifecycle order. */
const NEXT_STEP: Partial<Record<DecisionState, { target: DecisionState; label: string }>> =
  {
    draft: { target: "evidence_ready", label: "Mark evidence ready" },
    evidence_ready: { target: "under_review", label: "Submit for review" },
    under_review: { target: "approved", label: "Approve" },
    approved: { target: "implemented", label: "Mark implemented" },
    implemented: { target: "monitoring", label: "Start monitoring" },
    monitoring: { target: "successful", label: "Mark successful" },
  };

function NewDecisionForm({
  onCreated,
}: {
  onCreated: (decisionId: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [owner, setOwner] = useState("cfo");

  const create = useMutation({
    mutationFn: () => {
      const decisionId = sanitizeDatasetId(title);
      return createLedgerDecision({
        decisionId,
        title,
        owner,
        context: "Captured from the executive Decision Ledger.",
        objective: "grow ebitda",
        recommendation: title,
        chosenAlternative: decisionId,
        justification:
          "Recorded for governance; attach optimizer and experiment evidence " +
          "before submitting for review.",
      }).then((snapshot) => {
        onCreated(snapshot.decision_id);
        return snapshot;
      });
    },
  });

  return (
    <form
      className="ap-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (title.trim()) create.mutate();
      }}
    >
      <label className="ap-field">
        <span>Decision title</span>
        <input
          className="ap-input"
          value={title}
          placeholder="Raise contracted pricing 3%"
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label className="ap-field">
        <span>Owner</span>
        <input
          className="ap-input"
          value={owner}
          onChange={(event) => setOwner(event.target.value)}
        />
      </label>
      <button
        type="submit"
        className="ap-button ap-button--primary"
        disabled={!title.trim() || create.isPending}
      >
        {create.isPending ? "Creating…" : "Create draft"}
      </button>
      {create.isError ? (
        <span className="ap-note">{errorDetail(create.error)}</span>
      ) : null}
    </form>
  );
}

function GovernedActions({
  snapshot,
  onDone,
}: {
  snapshot: DecisionSnapshot;
  onDone: () => void;
}) {
  const { can, session } = useAuth();
  const step: { target: DecisionState; label: string } | undefined =
    NEXT_STEP[snapshot.state];
  const needsApproval = snapshot.state === "under_review";
  // Approving is an approver/admin action; advancing the lifecycle is an
  // analyst/admin action. The server re-checks both.
  const permitted = needsApproval
    ? can("approver", "admin")
    : can("analyst", "admin");

  const advance = useMutation({
    mutationFn: () => {
      if (!step) throw new Error("no transition is available");
      return transitionLedgerDecision(snapshot.decision_id, {
        expectedVersion: snapshot.version,
        target: step.target,
        approvedContentDigest: needsApproval
          ? snapshot.content_digest
          : undefined,
      });
    },
    onSuccess: onDone,
  });

  if (!step) {
    return (
      <p className="ap-note">
        This decision has reached a terminal state; no further transition is
        possible.
      </p>
    );
  }

  if (!permitted) {
    return (
      <p className="ap-note">
        Your role cannot {needsApproval ? "approve" : "advance"} this decision.
      </p>
    );
  }

  return (
    <div className="ap-govern">
      <button
        type="button"
        className="ap-button ap-button--primary"
        onClick={() => advance.mutate()}
        disabled={advance.isPending}
      >
        {advance.isPending ? "Recording…" : step.label}
      </button>
      {needsApproval ? (
        <p className="ap-note">
          Approving as <strong>{session?.subject ?? "your identity"}</strong>{" "}
          signs content digest{" "}
          <code>{snapshot.content_digest.slice(0, 12)}…</code>. The server binds
          the approver to your session and rejects the owner as approver.
        </p>
      ) : null}
      {advance.isError ? (
        <p className="ap-note ap-note--risk">{errorDetail(advance.error)}</p>
      ) : null}
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

      {decisionId.trim() ? (
        <Panel
          title="Record a realised outcome"
          description="Compare what actually happened against the prediction that justified the decision."
        >
          <RecordOutcomeForm
            decisionId={decisionId.trim()}
            onRecorded={(recorded) => {
              setReport(recorded);
              setNotFound(false);
            }}
          />
        </Panel>
      ) : null}
    </div>
  );
}

function RecordOutcomeForm({
  decisionId,
  onRecorded,
}: {
  decisionId: string;
  onRecorded: (report: MonitoringReport) => void;
}) {
  const { can } = useAuth();
  const [metricName, setMetricName] = useState("ebitda");
  const [expectedMean, setExpectedMean] = useState("24000000");
  const [lower, setLower] = useState("20000000");
  const [upper, setUpper] = useState("28000000");
  const [realizedValue, setRealizedValue] = useState("21500000");

  const record = useMutation({
    mutationFn: () =>
      recordOutcomes(decisionId, {
        metricName,
        expectedMean: Number(expectedMean),
        lower: Number(lower),
        upper: Number(upper),
        realizedValue: Number(realizedValue),
        asOf: new Date().toISOString().slice(0, 10),
      }),
    onSuccess: onRecorded,
  });

  if (!can("analyst", "admin")) {
    return (
      <p className="ap-note">Your role cannot record outcomes.</p>
    );
  }

  return (
    <form
      className="ap-form ap-form--grid"
      onSubmit={(event) => {
        event.preventDefault();
        record.mutate();
      }}
    >
      <label className="ap-field">
        <span>KPI</span>
        <input
          className="ap-input"
          value={metricName}
          onChange={(event) => setMetricName(event.target.value)}
        />
      </label>
      <label className="ap-field">
        <span>Expected mean</span>
        <input
          className="ap-input"
          type="number"
          value={expectedMean}
          onChange={(event) => setExpectedMean(event.target.value)}
        />
      </label>
      <label className="ap-field">
        <span>Interval lower</span>
        <input
          className="ap-input"
          type="number"
          value={lower}
          onChange={(event) => setLower(event.target.value)}
        />
      </label>
      <label className="ap-field">
        <span>Interval upper</span>
        <input
          className="ap-input"
          type="number"
          value={upper}
          onChange={(event) => setUpper(event.target.value)}
        />
      </label>
      <label className="ap-field">
        <span>Realised value</span>
        <input
          className="ap-input"
          type="number"
          value={realizedValue}
          onChange={(event) => setRealizedValue(event.target.value)}
        />
      </label>
      <button
        type="submit"
        className="ap-button ap-button--primary"
        disabled={record.isPending || !metricName.trim()}
      >
        {record.isPending ? "Recording…" : "Record outcome"}
      </button>
      {record.isError ? (
        <p className="ap-note ap-note--risk">{errorDetail(record.error)}</p>
      ) : null}
    </form>
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
      {report.drift.recalibration_required ? (
        <div className="ap-cta">
          <span>
            Drift has passed the recalibration threshold — refit the twin on
            recent history before relying on this decision again.
          </span>
          <Link className="ap-button ap-button--primary" href="/calibration">
            Recalibrate
          </Link>
        </div>
      ) : null}
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
