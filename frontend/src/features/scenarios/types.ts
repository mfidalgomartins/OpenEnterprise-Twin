export type MetricName =
  | "revenue"
  | "ebitda"
  | "free_cash_flow"
  | "closing_cash"
  | "otif"
  | "cancellation_rate"
  | "backlog_units"
  | "capacity_utilization"
  | "peak_revolver"
  | "rescue_funding";

export type DecisionStatus = "adopt" | "conditional" | "do_not_adopt";
export type EvidenceGrade = "exploratory" | "decision_grade";
export type ComparisonDirection = "higher" | "lower";
export type MechanismId =
  | "pricing"
  | "commercial-investment"
  | "capacity"
  | "inventory-sourcing"
  | "payment-terms"
  | "capital-investment";

export interface SegmentProductPriceChange {
  segment_id: string;
  product_id: string;
  price_change: string;
}

export interface ResourcePolicyChange {
  resource_id: string;
  regular_capacity_change: string;
  overtime_capacity_minutes: number;
}

export interface MaterialPolicyChange {
  material_id: string;
  safety_stock_coverage_days: string;
  supplier_lead_time_improvement: string;
  supplier_unit_cost_change: string;
}

export interface SegmentPaymentTermChange {
  segment_id: string;
  change_days: number;
}

export interface PolicyLevers {
  price_changes: SegmentProductPriceChange[];
  commercial_investment_change: string;
  resource_changes: ResourcePolicyChange[];
  material_changes: MaterialPolicyChange[];
  payment_term_changes: SegmentPaymentTermChange[];
  one_off_capital_investment_cents: number;
}

export interface PluginVersion {
  plugin_id: string;
  version: string;
}

export interface MetricGuardrail {
  metric_name: MetricName;
  threshold: number;
  breach_when: "below" | "above";
  downside_tail: "lower" | "upper";
}

export interface MaterialityThreshold {
  metric_name: MetricName;
  threshold: number;
  direction: ComparisonDirection;
}

export interface ComparisonPolicy {
  materiality_thresholds: MaterialityThreshold[];
}

export type MetricEntry = [MetricName, number];

export interface PairedDifference {
  replication_id: number;
  baseline_metric_entries: MetricEntry[];
  candidate_metric_entries: MetricEntry[];
  metric_entries: MetricEntry[];
}

export interface MetricComparison {
  metric_name: MetricName;
  direction: ComparisonDirection;
  baseline_mean: number;
  candidate_mean: number;
  baseline_breach_probability: number;
  candidate_breach_probability: number;
  baseline_breach_probability_ci95_lower: number;
  baseline_breach_probability_ci95_upper: number;
  candidate_breach_probability_ci95_lower: number;
  candidate_breach_probability_ci95_upper: number;
  mean_difference: number;
  ci95_lower: number | null;
  ci95_upper: number | null;
  p5_difference: number;
  p50_difference: number;
  p95_difference: number;
  probability_of_improvement: number;
  materiality_threshold: number;
  is_material: boolean;
}

export interface ScenarioComparison {
  baseline_scenario_id: string;
  baseline_scenario_name: string;
  candidate_scenario_id: string;
  candidate_scenario_name: string;
  candidate_policy_levers: PolicyLevers;
  baseline_experiment_digest: string;
  candidate_experiment_digest: string;
  company_model_version: string;
  company_model_hash: string;
  scenario_schema_version: string;
  engine_version: string;
  shock_tape_version: string;
  baseline_plugin_versions: PluginVersion[];
  candidate_plugin_versions: PluginVersion[];
  baseline_resolved_assumptions_hash: string;
  candidate_resolved_assumptions_hash: string;
  baseline_experiment_created_at: string;
  candidate_experiment_created_at: string;
  baseline_experiment_duration_seconds: number;
  candidate_experiment_duration_seconds: number;
  created_at: string;
  duration_seconds: number;
  master_seed: number;
  replication_count: number;
  horizon_days: number;
  warmup_days: number;
  evaluation_days: number;
  runoff_days: number;
  baseline_guardrails: MetricGuardrail[];
  candidate_guardrails: MetricGuardrail[];
  policy: ComparisonPolicy;
  paired_differences: PairedDifference[];
  metric_results: MetricComparison[];
  joint_probability_entries: [string, number][];
  digest: string;
}

export interface Recommendation {
  status: DecisionStatus;
  headline: string;
  rationale: string[];
  evidence_metric_ids: MetricName[];
}

export interface OutcomeDelta {
  metric_name: MetricName;
  baseline_mean: number;
  candidate_mean: number;
  mean_difference: number;
  probability_of_improvement: number;
  is_material: boolean;
}

export interface MechanismNarrative {
  mechanism_id: MechanismId;
  title: string;
  detail: string;
}

export interface DecisionConstraint {
  metric_name: MetricName;
  severity: "watch" | "breach";
  detail: string;
}

export interface DownsideTrigger {
  metric_name: MetricName;
  breach_probability: number;
  detail: string;
}

export interface DecisionGovernance {
  decision_owner: string;
  decision_record_action: string;
  review_date: string;
}

export interface ExecutionAction {
  action_id: string;
  title: string;
  owner: string;
  due_date: string;
  evidence_metric_ids: MetricName[];
  completion_evidence: string;
}

export interface BriefProvenance {
  comparison_digest: string;
  baseline_experiment_digest: string;
  candidate_experiment_digest: string;
  company_model_version: string;
  company_model_hash: string;
  scenario_schema_version: string;
  engine_version: string;
  shock_tape_version: string;
  master_seed: number;
  replication_count: number;
  baseline_plugin_versions: PluginVersion[];
  candidate_plugin_versions: PluginVersion[];
  baseline_resolved_assumptions_hash: string;
  candidate_resolved_assumptions_hash: string;
  baseline_experiment_created_at: string;
  candidate_experiment_created_at: string;
  baseline_experiment_duration_seconds: number;
  candidate_experiment_duration_seconds: number;
  comparison_created_at: string;
  comparison_duration_seconds: number;
  created_at: string;
  duration_seconds: number;
}

export interface ExecutiveBrief {
  brief_schema_version: string;
  decision_status: DecisionStatus;
  evidence_quality: {
    grade: EvidenceGrade;
    actual_replications: number;
    minimum_replications: number;
    detail: string;
  };
  recommendation: Recommendation;
  outcome_deltas: OutcomeDelta[];
  mechanisms: MechanismNarrative[];
  constraints: DecisionConstraint[];
  downside_triggers: DownsideTrigger[];
  governance: DecisionGovernance;
  actions: ExecutionAction[];
  assumptions: string[];
  provenance: BriefProvenance;
  digest: string;
}

export interface ScenarioResource {
  id: string;
  scenario_id: string;
  name: string;
  company_model_version: string;
  schema_version: string;
  horizon_days: number;
  warmup_days: number;
  evaluation_days: number;
  runoff_days: number;
  baseline_scenario_id: string | null;
  policy_levers: PolicyLevers;
}

export type ScenarioPayload = Omit<ScenarioResource, "id">;

export interface CompanyReference {
  company_id: string;
  name: string;
  model_version: string;
  products: Array<{
    product_id: string;
    name: string;
    standard_price_cents: number;
  }>;
  customer_segments: Array<{
    segment_id: string;
    name: string;
    payment_terms_days: number;
  }>;
  plant: {
    resources: Array<{
      resource_id: string;
      daily_capacity_minutes: number;
      max_overtime_minutes: number;
    }>;
    materials: Array<{
      material_id: string;
      name: string;
      supplier_lead_time_days: number;
    }>;
  };
}

export type ExperimentStatus = "queued" | "running" | "completed" | "failed";

export interface ExperimentResource {
  id: number;
  scenario_id: string;
  baseline_experiment_id: number | null;
  status: ExperimentStatus;
  seed: number;
  iterations: number;
  master_seed: number;
  replication_count: number;
  artifact_digest: string | null;
  error_code: string | null;
  error_detail: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface PortfolioMetric {
  metric_name: MetricName;
  baseline_mean: number;
  candidate_mean: number;
  mean_difference: number;
  candidate_breach_probability: number;
}

export interface DecisionSummary {
  experiment_id: number;
  scenario_id: string;
  scenario_name: string;
  completed_at: string;
  replication_count: number;
  decision_status: DecisionStatus;
  evidence_grade: EvidenceGrade;
  headline: string;
  hard_constraint_count: number;
  metrics: PortfolioMetric[];
  comparison_digest: string;
  brief_digest: string;
}

export interface DecisionPortfolio {
  items: DecisionSummary[];
  next_before_id: number | null;
}

export interface FrontierPoint {
  experiment_id: number;
  scenario_id: string;
  scenario_name: string;
  decision_status: DecisionStatus;
  ebitda_delta: number;
  free_cash_flow_delta: number;
  otif_delta: number;
  comparison_digest: string;
}

export interface PolicyFrontier {
  points: FrontierPoint[];
  eligible_count: number;
  dominated_count: number;
  excluded_count: number;
  method: "pareto_maximize_ebitda_fcf_otif";
}
