import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AppRoutes } from "../src/app/routes";

const metricResults = [
  {
    metric_name: "ebitda",
    direction: "higher",
    baseline_mean: 10_000_000,
    candidate_mean: 28_000_000,
    baseline_breach_probability: 0,
    candidate_breach_probability: 0,
    baseline_breach_probability_ci95_lower: 0,
    baseline_breach_probability_ci95_upper: 0.49,
    candidate_breach_probability_ci95_lower: 0,
    candidate_breach_probability_ci95_upper: 0.49,
    mean_difference: 18_000_000,
    ci95_lower: 16_000_000,
    ci95_upper: 20_000_000,
    p5_difference: 15_000_000,
    p50_difference: 18_000_000,
    p95_difference: 21_000_000,
    probability_of_improvement: 1,
    materiality_threshold: 1_000_000,
    is_material: true,
  },
  {
    metric_name: "free_cash_flow",
    direction: "higher",
    baseline_mean: 8_000_000,
    candidate_mean: 17_000_000,
    baseline_breach_probability: 0,
    candidate_breach_probability: 0,
    baseline_breach_probability_ci95_lower: 0,
    baseline_breach_probability_ci95_upper: 0.49,
    candidate_breach_probability_ci95_lower: 0,
    candidate_breach_probability_ci95_upper: 0.49,
    mean_difference: 9_000_000,
    ci95_lower: 7_000_000,
    ci95_upper: 11_000_000,
    p5_difference: 6_000_000,
    p50_difference: 9_000_000,
    p95_difference: 12_000_000,
    probability_of_improvement: 0.95,
    materiality_threshold: 1_000_000,
    is_material: true,
  },
  {
    metric_name: "closing_cash",
    direction: "higher",
    baseline_mean: 21_000_000,
    candidate_mean: 28_000_000,
    baseline_breach_probability: 0,
    candidate_breach_probability: 0.25,
    baseline_breach_probability_ci95_lower: 0,
    baseline_breach_probability_ci95_upper: 0.49,
    candidate_breach_probability_ci95_lower: 0.05,
    candidate_breach_probability_ci95_upper: 0.7,
    mean_difference: 7_000_000,
    ci95_lower: -2_000_000,
    ci95_upper: 16_000_000,
    p5_difference: -4_000_000,
    p50_difference: 7_000_000,
    p95_difference: 18_000_000,
    probability_of_improvement: 0.75,
    materiality_threshold: 1_000_000,
    is_material: true,
  },
  {
    metric_name: "otif",
    direction: "higher",
    baseline_mean: 0.9,
    candidate_mean: 0.92,
    baseline_breach_probability: 0.25,
    candidate_breach_probability: 0,
    baseline_breach_probability_ci95_lower: 0.05,
    baseline_breach_probability_ci95_upper: 0.7,
    candidate_breach_probability_ci95_lower: 0,
    candidate_breach_probability_ci95_upper: 0.49,
    mean_difference: 0.02,
    ci95_lower: 0.01,
    ci95_upper: 0.03,
    p5_difference: 0,
    p50_difference: 0.02,
    p95_difference: 0.04,
    probability_of_improvement: 0.9,
    materiality_threshold: 0.01,
    is_material: true,
  },
];

const comparisonFixture = {
  baseline_scenario_id: "current-plan",
  baseline_scenario_name: "Current plan",
  candidate_scenario_id: "resilient-margin",
  candidate_scenario_name: "Resilient margin",
  candidate_policy_levers: {
    price_changes: [
      {
        segment_id: "spot",
        product_id: "intelligent-valve",
        price_change: "0.025",
      },
    ],
    commercial_investment_change: "0",
    resource_changes: [],
    material_changes: [],
    payment_term_changes: [],
    one_off_capital_investment_cents: 0,
  },
  metric_results: metricResults,
  joint_probability_entries: [
    ["ebitda_improves_without_otif_declining", 0.9],
  ],
  created_at: "2026-07-16T08:03:00Z",
  digest: "f".repeat(64),
};

const reportFixture = {
  brief_schema_version: "0.3.0",
  decision_status: "conditional",
  evidence_quality: {
    grade: "exploratory",
    actual_replications: 4,
    minimum_replications: 30,
    detail: "Exploratory evidence: 4 paired replications; 30 required for decision-grade evidence.",
  },
  recommendation: {
    status: "conditional",
    headline: "Hold Resilient margin: 4 of 30 paired replications complete",
    rationale: [
      "ebitda: €100,000 to €280,000 (paired delta €180,000, 100.0% probability of improvement).",
      "closing_cash: €210,000 to €280,000 (paired delta €70,000, 75.0% probability of improvement).",
    ],
    evidence_metric_ids: ["ebitda", "closing_cash"],
  },
  outcome_deltas: metricResults.map((metric) => ({
    metric_name: metric.metric_name,
    baseline_mean: metric.baseline_mean,
    candidate_mean: metric.candidate_mean,
    mean_difference: metric.mean_difference,
    probability_of_improvement: metric.probability_of_improvement,
    is_material: metric.is_material,
  })),
  mechanisms: [
    {
      mechanism_id: "pricing",
      title: "Pricing",
      detail: "1 segment-product price change, ranging from 2.50% to 2.50%.",
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
    "Mean-effect intervals use paired Student-t; breach risks use Wilson intervals.",
  ],
  provenance: {
    comparison_digest: comparisonFixture.digest,
    baseline_experiment_digest: "a".repeat(64),
    candidate_experiment_digest: "b".repeat(64),
    company_model_version: "0.1.0",
    company_model_hash: "c".repeat(64),
    scenario_schema_version: "0.1.0",
    engine_version: "0.1.0",
    shock_tape_version: "0.1.0",
    master_seed: 48_271,
    replication_count: 4,
    baseline_plugin_versions: [{ plugin_id: "demand.forecast", version: "1.3.0" }],
    candidate_plugin_versions: [{ plugin_id: "demand.forecast", version: "1.3.0" }],
    baseline_resolved_assumptions_hash: "d".repeat(64),
    candidate_resolved_assumptions_hash: "e".repeat(64),
    baseline_experiment_created_at: "2026-07-16T08:00:00Z",
    candidate_experiment_created_at: "2026-07-16T08:02:00Z",
    baseline_experiment_duration_seconds: 3,
    candidate_experiment_duration_seconds: 4,
    comparison_created_at: comparisonFixture.created_at,
    comparison_duration_seconds: 0.1,
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

function renderReport() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/reports/42"]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function installReportApi() {
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const path = String(input);
    if (path.endsWith("/api/v1/experiments/42/comparison")) {
      return Promise.resolve(jsonResponse(comparisonFixture));
    }
    if (path.endsWith("/api/v1/experiments/42/report")) {
      return Promise.resolve(jsonResponse(reportFixture));
    }
    return Promise.reject(new Error(`Unexpected API request: ${path}`));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ExecutiveReportPage", () => {
  it("renders eight complete chapters from one frozen experiment", async () => {
    const fetchMock = installReportApi();
    const { container } = renderReport();

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Executive decision brief",
      }),
    ).toBeVisible();
    expect(container.querySelectorAll("[data-report-chapter]")).toHaveLength(8);
    for (const chapter of [
      "Recommendation",
      "Scenario comparison",
      "Value bridge",
      "Operational feasibility",
      "Sensitivities and guardrails",
      "Execution plan",
      "Assumptions",
      "Provenance",
    ]) {
      expect(screen.getByRole("heading", { level: 2, name: chapter })).toBeVisible();
    }

    expect(
      screen.getByRole("heading", {
        level: 3,
        name: "Hold Resilient margin: 4 of 30 paired replications complete",
      }),
    ).toBeVisible();
    expect(screen.getByText("Hold", { selector: "p" })).toBeVisible();
    const scenarios = screen.getByRole("region", {
      name: "Scenario comparison",
    });
    expect(
      within(scenarios).getByRole("heading", { name: "Current plan" }),
    ).toBeVisible();
    expect(
      within(scenarios).getByRole("heading", { name: "Resilient margin" }),
    ).toBeVisible();
    expect(screen.getByRole("cell", { name: "+€180,000" })).toBeVisible();
    expect(screen.getByRole("cell", { name: "+2 pp" })).toBeVisible();

    const execution = screen.getByRole("region", { name: "Execution plan" });
    expect(within(execution).getAllByText("Managing Director")[0]).toBeVisible();
    expect(within(execution).getByText("Finance Director")).toBeVisible();
    expect(within(execution).getAllByText("15 Aug 2026")[0]).toBeVisible();

    expect(screen.getByText("Model 0.1.0")).toBeVisible();
    expect(screen.getAllByText("Experiment 42")[0]).toBeVisible();
    expect(screen.getAllByText("16 Jul 2026")[0]).toBeVisible();
    expect(screen.getByText("4 paired replications use common random numbers.")).toBeVisible();
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual(
      expect.arrayContaining([
        "/api/v1/experiments/42/comparison",
        "/api/v1/experiments/42/report",
      ]),
    );
  });

  it("offers the browser print flow for the frozen brief", async () => {
    installReportApi();
    const printMock = vi.spyOn(window, "print").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderReport();

    await user.click(
      await screen.findByRole("button", { name: "Print or save PDF" }),
    );

    expect(printMock).toHaveBeenCalledOnce();
  });
});
