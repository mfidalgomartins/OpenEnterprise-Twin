import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AdaptivePolicyPage,
  CalibrationStudioPage,
  DecisionLedgerPage,
  MonitoringCenterPage,
  OptimizationLabPage,
} from "../src/features/autopilot/AutopilotPages";

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

function renderWithClient(element: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("calibration studio", () => {
  it("imports history and reports a decision-grade credibility score", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) => {
        const path = String(input);
        if (path.endsWith("/api/v1/datasets/synthetic")) {
          return Promise.resolve(
            jsonResponse(
              {
                dataset: {
                  dataset_id: "northstar-history",
                  company_id: "northstar-components",
                  data_digest: "a".repeat(64),
                  observation_count: 6600,
                  created_at: "2026-07-23T00:00:00Z",
                },
                quality: {
                  dataset_id: "northstar-history",
                  data_digest: "a".repeat(64),
                  total_observations: 6600,
                  distinct_series: 13,
                  quality_score: 1,
                  components: [
                    {
                      name: "completeness",
                      value: 1,
                      weight: 0.35,
                      detail: "no gaps",
                    },
                  ],
                  issues: [],
                },
              },
              201,
            ),
          );
        }
        if (path.endsWith("/api/v1/calibrations")) {
          return Promise.resolve(
            jsonResponse(
              {
                calibration_id: "northstar-cal",
                dataset_id: "northstar-history",
                created_at: "2026-07-23T00:00:00Z",
                calibration: {
                  calibration_id: "northstar-cal",
                  company_model_version: "0.2.0",
                  window_start: "2024-01-01",
                  window_end: "2025-06-30",
                  parameters: [
                    {
                      name: "demand_baseline:standard-valve",
                      provenance: "observed",
                      point_estimate: 50,
                      unit: "units/day",
                      sample_size: 400,
                    },
                  ],
                  warnings: [],
                },
                credibility: {
                  calibration_id: "northstar-cal",
                  score: 91.8,
                  band: "decision_grade",
                  components: [
                    {
                      name: "data_quality",
                      raw_value: 1,
                      normalized: 1,
                      weight: 0.2,
                      detail: "quality 1.0",
                    },
                  ],
                },
                backtests: [
                  {
                    overall_weighted_mape: 0.11,
                    overall_interval_coverage: 0.9,
                    nominal_coverage: 0.95,
                    kpis: [],
                  },
                ],
              },
              201,
            ),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      }),
    );

    const user = userEvent.setup();
    renderWithClient(<CalibrationStudioPage />);

    await user.click(screen.getByRole("button", { name: /import history/i }));
    expect(
      await screen.findByText("6,600"),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /calibrate & backtest/i }),
    );
    expect(await screen.findByText("Decision Grade")).toBeVisible();
    expect(screen.getByText("91.8")).toBeVisible();
  });
});

describe("optimization lab", () => {
  it("shows an empty state before a run", () => {
    renderWithClient(<OptimizationLabPage />);
    expect(screen.getByText(/no frontier yet/i)).toBeVisible();
  });
});

describe("adaptive policy builder", () => {
  it("renders the declarative rule preview", () => {
    renderWithClient(<AdaptivePolicyPage />);
    expect(screen.getByText(/backlog_days/i)).toBeVisible();
  });
});

describe("decision ledger", () => {
  it("shows an empty pipeline when no decisions exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse([]))),
    );
    renderWithClient(<DecisionLedgerPage />);
    expect(
      await screen.findByText(/no governed decisions yet/i),
    ).toBeVisible();
  });
});

describe("monitoring center", () => {
  it("prompts for a decision id", () => {
    renderWithClient(<MonitoringCenterPage />);
    expect(screen.getByText(/enter a decision/i)).toBeVisible();
  });
});
