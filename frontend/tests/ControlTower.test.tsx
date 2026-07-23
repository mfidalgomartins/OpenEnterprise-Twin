import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { AppRoutes } from "../src/app/routes";

const company = {
  company_id: "northstar-components",
  name: "Northstar Components",
  model_version: "0.2.0",
  products: [
    {
      product_id: "intelligent-valve",
      name: "Intelligent valve",
      standard_price_cents: 24_000,
    },
  ],
  customer_segments: [
    {
      segment_id: "contracted",
      name: "Contracted accounts",
      payment_terms_days: 45,
    },
  ],
  plant: {
    resources: [
      {
        resource_id: "test",
        daily_capacity_minutes: 1_050,
        max_overtime_minutes: 240,
      },
    ],
    materials: [
      {
        material_id: "electronics",
        name: "Electronics module",
        supplier_lead_time_days: 12,
      },
    ],
  },
};

const baseline = {
  id: "current-plan",
  scenario_id: "current-plan",
  name: "Current plan",
  company_model_version: "0.2.0",
  schema_version: "0.1.0",
  horizon_days: 515,
  warmup_days: 91,
  evaluation_days: 364,
  runoff_days: 60,
  baseline_scenario_id: null,
  policy_levers: {
    price_changes: [],
    commercial_investment_change: "0",
    resource_changes: [],
    material_changes: [],
    payment_term_changes: [],
    one_off_capital_investment_cents: 0,
  },
};

const decisions = {
  items: [
    {
      experiment_id: 17,
      scenario_id: "resilient-margin",
      scenario_name: "Resilient margin",
      completed_at: "2026-07-18T10:00:00Z",
      replication_count: 30,
      decision_status: "conditional",
      evidence_grade: "decision_grade",
      headline: "Pilot Resilient margin: EBITDA changes by +€180k with guardrails",
      hard_constraint_count: 0,
      metrics: [
        {
          metric_name: "ebitda",
          baseline_mean: 500_000,
          candidate_mean: 680_000,
          mean_difference: 180_000,
          candidate_breach_probability: 0,
        },
        {
          metric_name: "free_cash_flow",
          baseline_mean: 250_000,
          candidate_mean: 310_000,
          mean_difference: 60_000,
          candidate_breach_probability: 0,
        },
        {
          metric_name: "closing_cash",
          baseline_mean: 2_000_000,
          candidate_mean: 2_150_000,
          mean_difference: 150_000,
          candidate_breach_probability: 0,
        },
        {
          metric_name: "otif",
          baseline_mean: 0.95,
          candidate_mean: 0.97,
          mean_difference: 0.02,
          candidate_breach_probability: 0,
        },
      ],
      comparison_digest: "a".repeat(64),
      brief_digest: "b".repeat(64),
    },
  ],
  next_before_id: null,
};

const frontier = {
  points: [
    {
      experiment_id: 17,
      scenario_id: "resilient-margin",
      scenario_name: "Resilient margin",
      decision_status: "conditional",
      ebitda_delta: 180_000,
      free_cash_flow_delta: 60_000,
      otif_delta: 0.02,
      comparison_digest: "a".repeat(64),
    },
  ],
  eligible_count: 1,
  dominated_count: 0,
  excluded_count: 0,
  method: "pareto_maximize_ebitda_fcf_otif",
};

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status: 200,
  });
}

function renderControlTower({ frontierFails = false } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input) => {
      const path = String(input);
      if (path.endsWith("/api/v1/company")) {
        return Promise.resolve(jsonResponse(company));
      }
      if (path.endsWith("/api/v1/baseline")) {
        return Promise.resolve(jsonResponse(baseline));
      }
      if (path.endsWith("/api/v1/decisions")) {
        return Promise.resolve(jsonResponse(decisions));
      }
      if (path.endsWith("/api/v1/frontier")) {
        return Promise.resolve(
          frontierFails
            ? new Response(JSON.stringify({ detail: "Frontier unavailable" }), {
                headers: { "Content-Type": "application/problem+json" },
                status: 503,
              })
            : jsonResponse(frontier),
        );
      }
      return Promise.reject(new Error(`Unexpected API request: ${path}`));
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("enterprise control tower", () => {
  it("turns the briefing into an evidence-backed decision workspace", async () => {
    renderControlTower();

    expect(
      await screen.findByRole("heading", { name: "Decision briefing" }),
    ).toBeVisible();
    expect(screen.getAllByText("Resilient margin").length).toBeGreaterThan(0);
    expect(screen.getByText(/Pareto-efficient policy/i)).toBeVisible();
    expect(screen.getByRole("link", { name: /open decision room/i })).toHaveAttribute(
      "href",
      "/scenarios/resilient-margin/compare?experiment=17",
    );
  });

  it("makes Twin, Decisions and Reports operational destinations", async () => {
    const user = userEvent.setup();
    renderControlTower();
    await screen.findByRole("heading", { name: "Decision briefing" });

    await user.click(screen.getByRole("link", { name: "Twin" }));
    expect(
      await screen.findByRole("heading", { name: "Company twin" }),
    ).toBeVisible();
    expect(screen.getByText("Intelligent valve")).toBeVisible();

    await user.click(screen.getByRole("link", { name: "Decisions" }));
    expect(
      await screen.findByRole("heading", { name: "Decision portfolio" }),
    ).toBeVisible();
    expect(screen.getByText(/€1.8K|€180K|€180k/)).toBeVisible();

    await user.click(screen.getByRole("link", { name: "Reports" }));
    expect(
      await screen.findByRole("heading", { name: "Decision briefs" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: /open brief/i })).toHaveAttribute(
      "href",
      "/reports/17",
    );
  });

  it("keeps the core tower available when the optional frontier fails", async () => {
    renderControlTower({ frontierFails: true });

    expect(
      await screen.findByRole("heading", { name: "Decision briefing" }),
    ).toBeVisible();
    expect(screen.queryByText("Control tower unavailable")).not.toBeInTheDocument();
    expect(screen.getByText("No decision-grade frontier")).toBeVisible();
  });
});
