import { render, screen } from "@testing-library/react";

import { App } from "../src/app/App";

describe("App", () => {
  it("renders the routed report workspace inside the persistent shell", async () => {
    window.history.pushState({}, "", "/reports");

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) => {
        const path = String(input);
        const payload = path.endsWith("/api/v1/company")
          ? {
              company_id: "northstar-components",
              name: "Northstar Components",
              model_version: "0.2.0",
              products: [],
              customer_segments: [],
              plant: { resources: [], materials: [] },
            }
          : path.endsWith("/api/v1/baseline")
            ? {
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
              }
            : path.endsWith("/api/v1/decisions")
              ? { items: [], next_before_id: null }
              : { points: [], eligible_count: 0, dominated_count: 0, excluded_count: 0, method: "pareto_maximize_ebitda_fcf_otif" };
        return Promise.resolve(
          new Response(JSON.stringify(payload), {
            headers: { "Content-Type": "application/json" },
          }),
        );
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Decision briefs" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("navigation", { name: /primary/i })).toBeVisible();

    window.history.pushState({}, "", "/");
    vi.unstubAllGlobals();
  });
});
