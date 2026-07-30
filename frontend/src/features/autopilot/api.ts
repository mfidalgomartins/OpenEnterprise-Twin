import {
  ApiError,
  type ApiProblem,
  apiFetch,
  apiRequest,
} from "../../lib/api";
import type { Job } from "../jobs/types";
import type {
  DatasetIngestResponse,
  DecisionListItem,
  DecisionSnapshot,
  DecisionState,
  MonitoringReport,
} from "./types";

function problemFrom(response: Response, payload: unknown, fallback: string): ApiProblem {
  if (payload && typeof payload === "object" && "code" in payload) {
    return payload as ApiProblem;
  }
  return {
    type: "about:blank",
    title: response.statusText || "Request failed",
    status: response.status,
    code: `http_${response.status}`,
    detail: fallback,
    trace_id: response.headers.get("X-Trace-ID") ?? "",
    violations: [],
  };
}

export function ingestSyntheticDataset(datasetId: string, days: number) {
  return apiRequest<DatasetIngestResponse>("/api/v1/datasets/synthetic", {
    method: "POST",
    body: { dataset_id: datasetId, days },
  });
}

export async function ingestCsvDataset(
  datasetId: string,
  companyId: string,
  csvText: string,
): Promise<DatasetIngestResponse> {
  const query = new URLSearchParams({
    dataset_id: datasetId,
    company_id: companyId,
  });
  const response = await apiFetch(
    `/api/v1/datasets/csv?${query.toString()}`,
    {
      method: "POST",
      headers: { "Content-Type": "text/csv", Accept: "application/json" },
      body: csvText,
    },
  );
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(problemFrom(response, payload, "CSV ingestion failed."));
  }
  return payload as DatasetIngestResponse;
}

export async function downloadDatasetCsv(datasetId: string): Promise<void> {
  const path = `/api/v1/datasets/${encodeURIComponent(datasetId)}/export.csv`;
  const response = await apiFetch(path, {
    headers: { Accept: "text/csv" },
  });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw new ApiError(problemFrom(response, payload, "CSV export failed."));
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `${datasetId}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

export function runCalibration(
  calibrationId: string,
  datasetId: string,
  backtestCutoff: string | null,
) {
  return apiRequest<Job>("/api/v1/calibrations", {
    method: "POST",
    body: {
      calibration_id: calibrationId,
      dataset_id: datasetId,
      backtest_cutoff: backtestCutoff,
    },
  });
}

export interface OptimizationInput {
  commercialLower: number;
  commercialUpper: number;
  overtimeUpper: number;
  requireNoRescue: boolean;
  populationSize: number;
  maxGenerations: number;
  maxEvaluations: number;
  seed: number;
  horizonDays: number;
  replications: number;
}

export function runOptimization(input: OptimizationInput) {
  return apiRequest<Job>("/api/v1/optimizations", {
    method: "POST",
    body: {
      config: {
        objectives: [
          { metric_name: "ebitda", direction: "maximize" },
          { metric_name: "otif", direction: "maximize" },
        ],
        levers: [
          {
            lever_id: "commercial",
            kind: "commercial_investment",
            lower: input.commercialLower,
            upper: input.commercialUpper,
          },
          {
            lever_id: "overtime",
            kind: "overtime",
            target_id: "assembly",
            lower: 0,
            upper: input.overtimeUpper,
          },
        ],
        constraints: input.requireNoRescue
          ? [
              {
                metric_name: "rescue_funding",
                operator: "lte",
                bound: 0,
                kind: "hard",
              },
            ]
          : [],
        population_size: input.populationSize,
        max_generations: input.maxGenerations,
        max_evaluations: input.maxEvaluations,
        seed: input.seed,
      },
      horizon_days: input.horizonDays,
      replications: input.replications,
      master_seed: input.seed,
    },
  });
}

export interface AdaptiveInput {
  metric: string;
  operator: string;
  threshold: number;
  windowPeriods: number;
  persistencePeriods: number;
  cooldownPeriods: number;
  maxActivations: number;
  horizonDays: number;
  replications: number;
  seed: number;
}

export function compareAdaptivePolicy(input: AdaptiveInput) {
  return apiRequest<Job>("/api/v1/adaptive-policies/compare", {
    method: "POST",
    body: {
      policy: {
        policy_id: "adaptive-capacity",
        rules: [
          {
            rule_id: "capacity",
            metric: input.metric,
            operator: input.operator,
            threshold: input.threshold,
            window_periods: input.windowPeriods,
            persistence_periods: input.persistencePeriods,
            cooldown_periods: input.cooldownPeriods,
            max_activations: input.maxActivations,
            action: {
              type: "add_overtime_capacity",
              target_id: "assembly",
              magnitude: "0.1",
            },
            action_cost_cents: 400000,
          },
        ],
      },
      horizon_days: input.horizonDays,
      replications: input.replications,
      master_seed: input.seed,
    },
  });
}

export function listLedgerDecisions() {
  return apiRequest<DecisionListItem[]>("/api/v1/ledger/decisions");
}

export interface DecisionDraft {
  decisionId: string;
  title: string;
  owner: string;
  context: string;
  objective: string;
  recommendation: string;
  chosenAlternative: string;
  justification: string;
}

export function createLedgerDecision(draft: DecisionDraft) {
  return apiRequest<DecisionSnapshot>("/api/v1/ledger/decisions", {
    method: "POST",
    body: {
      decision_id: draft.decisionId,
      content: {
        title: draft.title,
        owner: draft.owner,
        context: draft.context,
        objectives: [draft.objective],
        company_model_version: "0.2.0",
        recommendation: draft.recommendation,
        chosen_alternative: draft.chosenAlternative,
        justification: draft.justification,
      },
    },
  });
}

export interface TransitionInput {
  expectedVersion: number;
  target: DecisionState;
  note?: string;
  /** Required only for the review → approved transition. */
  approvedContentDigest?: string;
}

export function transitionLedgerDecision(
  decisionId: string,
  input: TransitionInput,
) {
  // The approver identity is bound to the authenticated session server-side;
  // the client only signs the digest it was served.
  const approval = input.approvedContentDigest
    ? {
        decision: "approve",
        occurred_at: new Date().toISOString(),
        approved_content_digest: input.approvedContentDigest,
      }
    : undefined;
  return apiRequest<DecisionSnapshot>(
    `/api/v1/ledger/decisions/${encodeURIComponent(decisionId)}/transitions`,
    {
      method: "POST",
      body: {
        expected_version: input.expectedVersion,
        target: input.target,
        note: input.note ?? null,
        approval,
      },
    },
  );
}

export interface OutcomeInput {
  metricName: string;
  expectedMean: number;
  lower: number;
  upper: number;
  realizedValue: number;
  asOf: string;
}

export function recordOutcomes(decisionId: string, input: OutcomeInput) {
  return apiRequest<MonitoringReport>(
    `/api/v1/ledger/decisions/${encodeURIComponent(decisionId)}/outcomes`,
    {
      method: "POST",
      body: {
        predictions: [
          {
            metric_name: input.metricName,
            expected_mean: input.expectedMean,
            lower: input.lower,
            upper: input.upper,
            improvement_direction: "higher",
          },
        ],
        outcomes: [
          {
            metric_name: input.metricName,
            as_of: input.asOf,
            realized_value: input.realizedValue,
          },
        ],
      },
    },
  );
}

export function getLedgerDecision(decisionId: string) {
  return apiRequest<DecisionSnapshot>(
    `/api/v1/ledger/decisions/${encodeURIComponent(decisionId)}`,
  );
}

export function getMonitoring(decisionId: string) {
  return apiRequest<MonitoringReport>(
    `/api/v1/ledger/decisions/${encodeURIComponent(decisionId)}/monitoring`,
  );
}
