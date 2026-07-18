import { expect, test } from "@playwright/test";

function metric(
  metricName: "ebitda" | "free_cash_flow" | "closing_cash" | "otif",
  baseline: number,
  candidate: number,
) {
  const difference = candidate - baseline;
  return {
    metric_name: metricName,
    direction: "higher",
    baseline_mean: baseline,
    candidate_mean: candidate,
    baseline_breach_probability: 0,
    candidate_breach_probability: metricName === "closing_cash" ? 0.25 : 0,
    mean_difference: difference,
    ci95_lower: difference * 0.8,
    ci95_upper: difference * 1.2,
    p5_difference: difference * 0.7,
    p50_difference: difference,
    p95_difference: difference * 1.3,
    probability_of_improvement: metricName === "closing_cash" ? 0.75 : 0.95,
    materiality_threshold: metricName === "otif" ? 0.01 : 1_000_000,
    is_material: true,
  };
}

const metricResults = [
  metric("ebitda", 10_000_000, 28_000_000),
  metric("free_cash_flow", 8_000_000, 17_000_000),
  metric("closing_cash", 21_000_000, 28_000_000),
  metric("otif", 0.9, 0.92),
];

const comparison = {
  baseline_scenario_id: "current-plan",
  baseline_scenario_name: "Current plan",
  candidate_scenario_id: "resilient-margin",
  candidate_scenario_name: "Resilient margin",
  candidate_policy_levers: {
    price_changes: [],
    commercial_investment_change: "0",
    resource_changes: [],
    material_changes: [],
    payment_term_changes: [],
    one_off_capital_investment_cents: 0,
  },
  metric_results: metricResults,
  joint_probability_entries: [],
  created_at: "2026-07-16T08:03:00Z",
  digest: "f".repeat(64),
};

const report = {
  brief_schema_version: "0.2.1",
  decision_status: "conditional",
  recommendation: {
    status: "conditional",
    headline: "Adopt Resilient margin with guardrails",
    rationale: [
      "EBITDA improves materially under paired uncertainty.",
      "Closing cash retains a downside guardrail.",
    ],
    evidence_metric_ids: ["ebitda", "closing_cash"],
  },
  outcome_deltas: metricResults.map((item) => ({
    metric_name: item.metric_name,
    baseline_mean: item.baseline_mean,
    candidate_mean: item.candidate_mean,
    mean_difference: item.mean_difference,
    probability_of_improvement: item.probability_of_improvement,
    is_material: item.is_material,
  })),
  mechanisms: [
    {
      mechanism_id: "pricing",
      title: "Pricing",
      detail: "Spot price changes alter revenue and demand through elasticity.",
    },
  ],
  constraints: [
    {
      metric_name: "closing_cash",
      severity: "watch",
      detail: "Closing cash retains a 25% simulated breach probability.",
    },
  ],
  downside_triggers: [
    {
      metric_name: "closing_cash",
      breach_probability: 0.25,
      detail: "Reassess if the closing cash guardrail risk persists.",
    },
  ],
  governance: {
    decision_owner: "Managing Director",
    decision_record_action:
      "Record the recommendation and comparison digest in the decision register.",
    review_date: "2026-08-15",
  },
  actions: [
    {
      action_id: "record-decision",
      title: "Record conditional adoption decision",
      owner: "Managing Director",
      due_date: "2026-07-23",
      evidence_metric_ids: ["ebitda", "closing_cash"],
      completion_evidence: "Decision-register entry with the comparison digest.",
    },
    {
      action_id: "review-closing-cash",
      title: "Review closing cash guardrail",
      owner: "Finance Director",
      due_date: "2026-08-15",
      evidence_metric_ids: ["closing_cash"],
      completion_evidence: "Actual closing cash reconciled to simulated risk.",
    },
  ],
  assumptions: [
    "100 paired replications use common random numbers.",
    "Confidence intervals use the paired normal approximation.",
  ],
  provenance: {
    comparison_digest: comparison.digest,
    baseline_experiment_digest: "a".repeat(64),
    candidate_experiment_digest: "b".repeat(64),
    company_model_version: "0.1.0",
    company_model_hash: "c".repeat(64),
    scenario_schema_version: "0.1.0",
    engine_version: "0.1.0",
    shock_tape_version: "0.1.0",
    master_seed: 731,
    replication_count: 100,
    baseline_plugin_versions: [],
    candidate_plugin_versions: [],
    baseline_resolved_assumptions_hash: "d".repeat(64),
    candidate_resolved_assumptions_hash: "e".repeat(64),
    baseline_experiment_created_at: "2026-07-16T08:00:00Z",
    candidate_experiment_created_at: "2026-07-16T08:02:00Z",
    baseline_experiment_duration_seconds: 3,
    candidate_experiment_duration_seconds: 4,
    comparison_created_at: comparison.created_at,
    comparison_duration_seconds: 0.1,
    created_at: "2026-07-16T08:03:01Z",
    duration_seconds: 0.02,
  },
  digest: "1".repeat(64),
};

test("publishes eight print-safe chapters from one immutable experiment", async ({
  page,
}) => {
  const requestedPaths: string[] = [];
  await page.route("**/api/v1/experiments/42/comparison", async (route) => {
    requestedPaths.push(new URL(route.request().url()).pathname);
    await route.fulfill({ json: comparison });
  });
  await page.route("**/api/v1/experiments/42/report", async (route) => {
    requestedPaths.push(new URL(route.request().url()).pathname);
    await route.fulfill({ json: report });
  });

  await page.goto("/reports/42");
  await expect(
    page.getByRole("heading", { level: 1, name: "Executive decision brief" }),
  ).toBeVisible();
  await expect(page.locator("[data-report-chapter]")).toHaveCount(8);
  expect(requestedPaths.sort()).toEqual([
    "/api/v1/experiments/42/comparison",
    "/api/v1/experiments/42/report",
  ]);

  await page.emulateMedia({ media: "print" });
  await expect(page.locator(".app-header")).toBeHidden();
  expect(
    await page.locator(".executive-report__chapter").first().evaluate(
      (chapter) => getComputedStyle(chapter).breakBefore,
    ),
  ).toBe("page");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth === window.innerWidth,
    ),
  ).toBe(true);

  const pdf = await page.pdf({
    format: "A4",
    landscape: true,
    printBackground: true,
  });
  expect(pdf.subarray(0, 4).toString()).toBe("%PDF");
  expect(pdf.toString("latin1").match(/\/Type\s*\/Page\b/g)?.length ?? 0).toBeGreaterThanOrEqual(9);
});
