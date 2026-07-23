export interface DatasetSummary {
  dataset_id: string;
  company_id: string;
  data_digest: string;
  observation_count: number;
  created_at: string;
}

export interface QualityComponent {
  name: string;
  value: number;
  weight: number;
  detail: string;
}

export interface DataQualityIssue {
  code: string;
  severity: "error" | "warning" | "info";
  series: string | null;
  entity_id: string | null;
  count: number;
  message: string;
}

export interface DataQualityReport {
  dataset_id: string;
  data_digest: string;
  total_observations: number;
  distinct_series: number;
  quality_score: number;
  components: QualityComponent[];
  issues: DataQualityIssue[];
}

export interface DatasetIngestResponse {
  dataset: DatasetSummary;
  quality: DataQualityReport;
}

export interface CredibilityComponent {
  name: string;
  raw_value: number;
  normalized: number;
  weight: number;
  detail: string;
}

export interface CredibilityScore {
  calibration_id: string;
  score: number;
  band: "decision_grade" | "supporting" | "provisional" | "insufficient";
  components: CredibilityComponent[];
}

export interface KpiBacktest {
  series: string;
  entity_id: string | null;
  sample_size: number;
  weighted_mape: number;
  interval_coverage: number;
  nominal_coverage: number;
}

export interface BacktestResult {
  overall_weighted_mape: number;
  overall_interval_coverage: number;
  nominal_coverage: number;
  kpis: KpiBacktest[];
}

export interface EstimatedParameter {
  name: string;
  provenance: "observed" | "estimated" | "assumed";
  point_estimate: number;
  unit: string;
  sample_size: number;
}

export interface CalibrationResult {
  calibration_id: string;
  company_model_version: string;
  window_start: string;
  window_end: string;
  parameters: EstimatedParameter[];
  warnings: string[];
}

export interface CalibrationResponse {
  calibration_id: string;
  dataset_id: string;
  created_at: string;
  calibration: CalibrationResult;
  credibility: CredibilityScore;
  backtests: BacktestResult[];
}

export interface PolicyCandidate {
  candidate_id: number;
  objective_values: Record<string, number>;
  constraint_values: Record<string, number>;
  feasible: boolean;
  robustness: number;
  weighted_score: number;
  rank: number;
  exclusion_reason: string | null;
}

export interface LeverSensitivity {
  lever_id: string;
  influence: number;
}

export interface ConvergencePoint {
  generation: number;
  best_weighted_score: number;
  frontier_size: number;
}

export interface OptimizationResult {
  frontier: PolicyCandidate[];
  recommended: PolicyCandidate | null;
  dominated: PolicyCandidate[];
  infeasible: PolicyCandidate[];
  sensitivity: LeverSensitivity[];
  convergence: ConvergencePoint[];
  evaluations: number;
  converged: boolean;
  seed: number;
}

export interface OptimizationResponse {
  optimization_id: number;
  digest: string;
  evaluations: number;
  created_at: string;
  result: OptimizationResult;
}

export interface AdaptiveComparison {
  policy_id: string;
  static_scenario_id: string;
  adaptive_scenario_id: string;
  replications: number;
  master_seed: number;
  metric_deltas: Record<string, number>;
  activation_count: number;
  total_action_cost_cents: number;
}

export type DecisionState =
  | "draft"
  | "evidence_ready"
  | "under_review"
  | "approved"
  | "implemented"
  | "monitoring"
  | "successful"
  | "underperformed"
  | "superseded"
  | "abandoned";

export interface DecisionListItem {
  decision_id: string;
  title: string;
  owner: string;
  state: DecisionState;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface DecisionTransitionRecord {
  from_state: DecisionState | null;
  to_state: DecisionState;
  actor: string;
  occurred_at: string;
  note: string | null;
}

export interface DecisionSnapshot {
  decision_id: string;
  state: DecisionState;
  version: number;
  owner: string;
  content: Record<string, unknown>;
  transitions: DecisionTransitionRecord[];
  approvals: unknown[];
  created_at: string;
  updated_at: string;
}

export interface KpiOutcome {
  metric_name: string;
  expected_mean: number;
  realized_value: number;
  lower: number;
  upper: number;
  within_interval: boolean;
  standardized_adverse_deviation: number;
  hard_constraint_ok: boolean;
  level: string;
}

export interface DriftAssessment {
  data_drift: number;
  parameter_drift: number;
  result_drift: number;
  overall_severity: number;
  recalibration_required: boolean;
  detail: string;
}

export interface MonitoringAlert {
  metric_name: string | null;
  level: string;
  severity: "info" | "warning" | "critical";
  message: string;
  created_at: string;
}

export interface MonitoringReport {
  decision_id: string;
  kpis: KpiOutcome[];
  drift: DriftAssessment;
  alerts: MonitoringAlert[];
  recommended_level: string;
}
