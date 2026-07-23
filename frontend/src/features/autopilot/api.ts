import { apiRequest } from "../../lib/api";
import type {
  AdaptiveComparison,
  CalibrationResponse,
  DatasetIngestResponse,
  DecisionListItem,
  DecisionSnapshot,
  MonitoringReport,
  OptimizationResponse,
} from "./types";

export function ingestSyntheticDataset(datasetId: string, days: number) {
  return apiRequest<DatasetIngestResponse>("/api/v1/datasets/synthetic", {
    method: "POST",
    body: { dataset_id: datasetId, days },
  });
}

export function runCalibration(
  calibrationId: string,
  datasetId: string,
  backtestCutoff: string,
) {
  return apiRequest<CalibrationResponse>("/api/v1/calibrations", {
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
  return apiRequest<OptimizationResponse>("/api/v1/optimizations", {
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
  return apiRequest<AdaptiveComparison>("/api/v1/adaptive-policies/compare", {
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
