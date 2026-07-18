import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AppRoutes } from "../src/app/routes";

const digest = "a".repeat(64);

const metrics = {
  revenue: {
    baseline: [100_000_000, 102_000_000, 98_000_000, 101_000_000],
    candidate: [103_000_000, 106_000_000, 101_000_000, 106_000_000],
    direction: "higher",
  },
  ebitda: {
    baseline: [9_000_000, 10_000_000, 11_000_000, 10_000_000],
    candidate: [25_000_000, 27_000_000, 30_000_000, 30_000_000],
    direction: "higher",
  },
  free_cash_flow: {
    baseline: [5_000_000, 6_000_000, 7_000_000, 6_000_000],
    candidate: [15_000_000, 18_000_000, 22_000_000, 21_000_000],
    direction: "higher",
  },
  closing_cash: {
    baseline: [20_000_000, 21_000_000, 22_000_000, 21_000_000],
    candidate: [25_000_000, 27_000_000, 30_000_000, 30_000_000],
    direction: "higher",
  },
  otif: {
    baseline: [0.955, 0.958, 0.96, 0.959],
    candidate: [0.968, 0.97, 0.971, 0.971],
    direction: "higher",
  },
  cancellation_rate: {
    baseline: [0.042, 0.04, 0.041, 0.039],
    candidate: [0.036, 0.035, 0.034, 0.035],
    direction: "lower",
  },
  backlog_units: {
    baseline: [410, 400, 405, 405],
    candidate: [370, 365, 360, 365],
    direction: "lower",
  },
  capacity_utilization: {
    baseline: [0.84, 0.85, 0.86, 0.85],
    candidate: [0.88, 0.89, 0.9, 0.89],
    direction: "lower",
  },
  peak_revolver: {
    baseline: [32_000_000, 30_000_000, 31_000_000, 31_000_000],
    candidate: [28_000_000, 29_000_000, 27_000_000, 28_000_000],
    direction: "lower",
  },
  rescue_funding: {
    baseline: [0, 0, 0, 0],
    candidate: [0, 0, 0, 0],
    direction: "lower",
  },
} as const;

type FixtureMetricName = keyof typeof metrics;

function mean(values: readonly number[]) {
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function metricResult(metricName: FixtureMetricName) {
  const metric = metrics[metricName];
  const baselineMean = mean(metric.baseline);
  const candidateMean = mean(metric.candidate);
  const differences = metric.candidate.map(
    (value, index) => value - metric.baseline[index],
  );

  return {
    metric_name: metricName,
    direction: metric.direction,
    baseline_mean: baselineMean,
    candidate_mean: candidateMean,
    baseline_breach_probability: 0,
    candidate_breach_probability: metricName === "closing_cash" ? 0.25 : 0,
    mean_difference: candidateMean - baselineMean,
    ci95_lower: Math.min(...differences),
    ci95_upper: Math.max(...differences),
    p5_difference: Math.min(...differences),
    p50_difference: mean(differences),
    p95_difference: Math.max(...differences),
    probability_of_improvement: metricName === "closing_cash" ? 0.75 : 1,
    materiality_threshold: 1,
    is_material: metricName !== "rescue_funding",
  };
}

const comparisonFixture = {
  baseline_scenario_id: "current-plan",
  baseline_scenario_name: "Current plan",
  candidate_scenario_id: "resilient-margin",
  candidate_scenario_name: "Resilient margin",
  candidate_policy_levers: {
    price_changes: [
      {
        segment_id: "contracted",
        product_id: "standard-valve",
        price_change: "0.025",
      },
    ],
    commercial_investment_change: "0",
    resource_changes: [
      {
        resource_id: "test-cell",
        regular_capacity_change: "0.2",
        overtime_capacity_minutes: 0,
      },
    ],
    material_changes: [],
    payment_term_changes: [],
    one_off_capital_investment_cents: 12_000_000,
  },
  baseline_experiment_digest: digest,
  candidate_experiment_digest: "b".repeat(64),
  company_model_version: "0.1.0",
  company_model_hash: "c".repeat(64),
  scenario_schema_version: "0.1.0",
  engine_version: "0.1.0",
  shock_tape_version: "0.1.0",
  baseline_plugin_versions: [
    { plugin_id: "demand.forecast", version: "1.3.0" },
  ],
  candidate_plugin_versions: [
    { plugin_id: "demand.forecast", version: "1.3.0" },
  ],
  baseline_resolved_assumptions_hash: "d".repeat(64),
  candidate_resolved_assumptions_hash: "e".repeat(64),
  baseline_experiment_created_at: "2026-07-16T08:00:00Z",
  candidate_experiment_created_at: "2026-07-16T08:02:00Z",
  baseline_experiment_duration_seconds: 11.2,
  candidate_experiment_duration_seconds: 12.4,
  created_at: "2026-07-16T08:03:00Z",
  duration_seconds: 0.08,
  master_seed: 48_271,
  replication_count: 4,
  horizon_days: 365,
  warmup_days: 30,
  evaluation_days: 330,
  runoff_days: 5,
  baseline_guardrails: [],
  candidate_guardrails: [],
  policy: { materiality_thresholds: [] },
  paired_differences: Array.from({ length: 4 }, (_, replicationId) => ({
    replication_id: replicationId,
    baseline_metric_entries: Object.entries(metrics).map(([name, metric]) => [
      name,
      metric.baseline[replicationId],
    ]),
    candidate_metric_entries: Object.entries(metrics).map(([name, metric]) => [
      name,
      metric.candidate[replicationId],
    ]),
    metric_entries: Object.entries(metrics).map(([name, metric]) => [
      name,
      metric.candidate[replicationId] - metric.baseline[replicationId],
    ]),
  })),
  metric_results: Object.keys(metrics).map((name) =>
    metricResult(name as FixtureMetricName),
  ),
  joint_probability_entries: [
    ["ebitda_improves_without_otif_declining", 1],
    ["ebitda_and_closing_cash_improve", 0.75],
  ],
  digest: "f".repeat(64),
};

const reportFixture = {
  brief_schema_version: "0.2.0",
  decision_status: "conditional",
  recommendation: {
    status: "conditional",
    headline: "Adopt Resilient margin with guardrails",
    rationale: [
      "ebitda: €100,000 to €280,000 (paired delta €180,000, 100.0% probability of improvement).",
      "closing_cash: €210,000 to €280,000 (paired delta €70,000, 75.0% probability of improvement).",
    ],
    evidence_metric_ids: ["ebitda", "closing_cash"],
  },
  outcome_deltas: Object.keys(metrics).map((name) => {
    const result = metricResult(name as FixtureMetricName);
    return {
      metric_name: name,
      baseline_mean: result.baseline_mean,
      candidate_mean: result.candidate_mean,
      mean_difference: result.mean_difference,
      probability_of_improvement: result.probability_of_improvement,
      is_material: result.is_material,
    };
  }),
  mechanisms: [
    {
      mechanism_id: "pricing",
      title: "Pricing",
      detail: "1 segment-product price change(s), ranging from 2.50% to 2.50%.",
    },
    {
      mechanism_id: "capacity",
      title: "Capacity",
      detail: "1 resource policy change(s) with 0 configured overtime minute(s).",
    },
    {
      mechanism_id: "capital-investment",
      title: "Capital investment",
      detail: "One-off capital investment is €120,000.",
    },
  ],
  constraints: [
    {
      metric_name: "closing_cash",
      severity: "watch",
      detail: "closing_cash: breach probability rises from 0.0% to 25.0%.",
    },
  ],
  downside_triggers: [
    {
      metric_name: "closing_cash",
      breach_probability: 0.25,
      detail: "Reassess if the closing_cash guardrail risk persists above the simulated level.",
    },
  ],
  governance: {
    decision_owner: "Managing Director",
    decision_record_action:
      "Record the 'adopt with guardrails' recommendation and comparison digest in the decision register before implementation.",
    review_date: "2026-08-15",
  },
  actions: [
    {
      action_id: "record-decision",
      title: "Record conditional adoption decision",
      owner: "Managing Director",
      due_date: "2026-07-23",
      evidence_metric_ids: ["ebitda", "closing_cash"],
      completion_evidence:
        "Decision-register entry containing the recommendation, comparison digest and named guardrails.",
    },
    {
      action_id: "review-closing-cash",
      title: "Review closing cash guardrail",
      owner: "Finance Director",
      due_date: "2026-08-15",
      evidence_metric_ids: ["closing_cash"],
      completion_evidence:
        "Actual closing cash result compared with the simulated guardrail risk and documented in the decision register.",
    },
  ],
  assumptions: [
    "4 paired replications use common random numbers.",
    "Confidence intervals use the paired normal approximation.",
    "Narrative clauses are selected deterministically from computed states.",
  ],
  provenance: {
    comparison_digest: comparisonFixture.digest,
    baseline_experiment_digest: comparisonFixture.baseline_experiment_digest,
    candidate_experiment_digest: comparisonFixture.candidate_experiment_digest,
    company_model_version: comparisonFixture.company_model_version,
    company_model_hash: comparisonFixture.company_model_hash,
    scenario_schema_version: comparisonFixture.scenario_schema_version,
    engine_version: comparisonFixture.engine_version,
    shock_tape_version: comparisonFixture.shock_tape_version,
    master_seed: comparisonFixture.master_seed,
    replication_count: comparisonFixture.replication_count,
    baseline_plugin_versions: comparisonFixture.baseline_plugin_versions,
    candidate_plugin_versions: comparisonFixture.candidate_plugin_versions,
    baseline_resolved_assumptions_hash:
      comparisonFixture.baseline_resolved_assumptions_hash,
    candidate_resolved_assumptions_hash:
      comparisonFixture.candidate_resolved_assumptions_hash,
    baseline_experiment_created_at:
      comparisonFixture.baseline_experiment_created_at,
    candidate_experiment_created_at:
      comparisonFixture.candidate_experiment_created_at,
    baseline_experiment_duration_seconds:
      comparisonFixture.baseline_experiment_duration_seconds,
    candidate_experiment_duration_seconds:
      comparisonFixture.candidate_experiment_duration_seconds,
    comparison_created_at: comparisonFixture.created_at,
    comparison_duration_seconds: comparisonFixture.duration_seconds,
    created_at: "2026-07-16T08:03:01Z",
    duration_seconds: 0.02,
  },
  digest: "1".repeat(64),
};

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });
}

function renderDecisionRoom() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[
          "/scenarios/resilient-margin/compare?experiment=experiment-42",
        ]}
      >
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function installApiFixture() {
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const path = String(input);

    if (path.endsWith("/api/v1/experiments/experiment-42/comparison")) {
      return Promise.resolve(jsonResponse(comparisonFixture));
    }
    if (path.endsWith("/api/v1/experiments/experiment-42/report")) {
      return Promise.resolve(jsonResponse(reportFixture));
    }

    return Promise.reject(new Error(`Unexpected API request: ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);

  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ScenarioComparePage", () => {
  it("turns the comparison and report into a decision-first narrative", async () => {
    const fetchMock = installApiFixture();
    renderDecisionRoom();

    expect(
      await screen.findByRole("heading", { level: 1, name: "Resilient margin" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Adopt Resilient margin with guardrails",
      }),
    ).toBeVisible();

    const outcomes = screen.getByRole("list", { name: "Key outcomes" });
    expect(within(outcomes).getAllByRole("listitem")).toHaveLength(3);
    expect(within(outcomes).getByText("+€180k")).toBeVisible();
    expect(within(outcomes).getByText("+€70k")).toBeVisible();
    expect(within(outcomes).getByText("+1.2 pp")).toBeVisible();

    expect(
      screen.getByText(
        "closing_cash: breach probability rises from 0.0% to 25.0%.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Seed 48,271")).toBeVisible();
    expect(screen.getByText("Model 0.1.0")).toBeVisible();
    expect(screen.getByText("4 paired replications")).toBeVisible();
    expect(screen.getByText("demand.forecast 1.3.0")).toBeVisible();
    const execution = screen.getByRole("region", { name: "Execution" });
    expect(within(execution).getByText("Finance Director")).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Open published executive brief" }),
    ).toHaveAttribute("href", "/reports/experiment-42");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual(
      expect.arrayContaining([
        "/api/v1/experiments/experiment-42/comparison",
        "/api/v1/experiments/experiment-42/report",
      ]),
    );
  });

  it("pairs the visual trajectory with a concise summary and exact uncertainty values", async () => {
    installApiFixture();
    renderDecisionRoom();

    const chart = await screen.findByRole("img", {
      name: /Resilient margin improves the earnings-to-cash path/i,
    });
    expect(chart).toBeVisible();
    expect(
      screen.getByText(
        "Resilient margin improves all 3 earnings-to-cash outcomes on average; shaded bands show the P10–P90 range across paired replications.",
      ),
    ).toBeVisible();

    const table = screen.getByRole("table", {
      name: "Exact earnings-to-cash uncertainty values",
    });
    expect(table).toBeVisible();
    expect(
      within(table).getByRole("row", {
        name: "EBITDA Current plan €93k €100k €107k",
      }),
    ).toBeVisible();
    expect(
      within(table).getByRole("row", {
        name: "EBITDA Resilient margin €256k €280k €300k",
      }),
    ).toBeVisible();
  });

  it("announces recalculation politely while the two evidence requests are pending", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() => new Promise<Response>(() => undefined)),
    );
    renderDecisionRoom();

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent("Recalculating scenario evidence");
  });
});
