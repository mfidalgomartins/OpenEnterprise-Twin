import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AdaptivePolicyPage,
  CalibrationStudioPage,
  DecisionLedgerPage,
  MonitoringCenterPage,
  OptimizationLabPage,
} from "../src/features/autopilot/AutopilotPages";
import {
  clearApiAccessToken,
  setApiAccessToken,
} from "../src/lib/api";
import { AuthenticatedTestSession } from "./testAuth";

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

function succeededJob(
  kind: "optimization" | "adaptive_comparison",
  jobId: string,
) {
  return {
    job_id: jobId,
    kind,
    status: "succeeded",
    created_by: "test-admin",
    attempt_count: 1,
    max_attempts: 3,
    progress: 100,
    stage: "completed",
    cancellation_requested_at: null,
    next_attempt_at: null,
    result_resource_type: kind,
    result_resource_id: "1",
    result_digest: "a".repeat(64),
    result_location: `/api/v1/jobs/${jobId}/result`,
    problem: null,
    created_at: "2026-07-23T00:00:00Z",
    started_at: "2026-07-23T00:00:00Z",
    finished_at: "2026-07-23T00:00:01Z",
    updated_at: "2026-07-23T00:00:01Z",
  };
}

function renderWithClient(element: ReactElement) {
  const location = memoryLocation();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <AuthenticatedTestSession>
      <QueryClientProvider client={queryClient}>
        <Router hook={location.hook}>{element}</Router>
      </QueryClientProvider>
    </AuthenticatedTestSession>,
  );
}

afterEach(() => {
  clearApiAccessToken();
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
                job_id: "calibration-job-1",
                kind: "calibration",
                status: "succeeded",
                created_by: "test-admin",
                attempt_count: 1,
                max_attempts: 3,
                progress: 100,
                stage: "completed",
                cancellation_requested_at: null,
                next_attempt_at: null,
                result_resource_type: "calibration",
                result_resource_id: "northstar-history-cal",
                result_digest: "a".repeat(64),
                result_location: "/api/v1/jobs/calibration-job-1/result",
                problem: null,
                created_at: "2026-07-23T00:00:00Z",
                started_at: "2026-07-23T00:00:00Z",
                finished_at: "2026-07-23T00:00:01Z",
                updated_at: "2026-07-23T00:00:01Z",
              },
              202,
            ),
          );
        }
        if (path.endsWith("/api/v1/jobs/calibration-job-1/result")) {
          return Promise.resolve(
            jsonResponse({
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
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      }),
    );

    const user = userEvent.setup();
    renderWithClient(<CalibrationStudioPage />);

    await user.click(screen.getByRole("button", { name: /import synthetic/i }));
    expect(
      await screen.findByText("6,600"),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /calibrate & backtest/i }),
    );
    expect(await screen.findByText("Decision Grade")).toBeVisible();
    expect(screen.getByText("91.8")).toBeVisible();
  });

  it("imports a CSV file and profiles its quality", async () => {
    setApiAccessToken("browser-access-token");
    const fetchMock = vi.fn<typeof fetch>((input) => {
        const path = String(input);
        if (path.includes("/api/v1/datasets/csv")) {
          return Promise.resolve(
            jsonResponse(
              {
                dataset: {
                  dataset_id: "my-history",
                  company_id: "northstar-components",
                  data_digest: "b".repeat(64),
                  observation_count: 42,
                  created_at: "2026-07-23T00:00:00Z",
                },
                quality: {
                  dataset_id: "my-history",
                  data_digest: "b".repeat(64),
                  total_observations: 42,
                  distinct_series: 3,
                  quality_score: 1,
                  components: [],
                  issues: [],
                },
              },
              201,
            ),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      });
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient(<CalibrationStudioPage />);
    const file = new File(
      ["period_date,series,entity_id,value,unit\n2025-01-01,otif,,0.96,ratio\n"],
      "my-history.csv",
      { type: "text/csv" },
    );
    const input = screen.getByLabelText(/import csv history/i);
    await userEvent.upload(input, file);
    expect(await screen.findByText("42")).toBeVisible();
    expect(
      screen.getByRole("button", { name: /export csv/i }),
    ).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/datasets/csv"),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: "Bearer browser-access-token",
        }),
      }),
    );
  });

  it("surfaces a problem detail when a CSV import is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() =>
        Promise.resolve(
          jsonResponse(
            {
              type: "about:blank",
              title: "Rejected",
              status: 422,
              code: "domain_validation",
              detail: "line 2: unknown series 'nope'",
              trace_id: "t1",
              violations: [],
            },
            422,
          ),
        ),
      ),
    );
    renderWithClient(<CalibrationStudioPage />);
    const file = new File(["bad"], "bad.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText(/import csv history/i), file);
    expect(await screen.findByText(/unknown series/i)).toBeVisible();
  });
});

describe("optimization lab", () => {
  it("shows an empty state before a run", () => {
    renderWithClient(<OptimizationLabPage />);
    expect(screen.getByText(/no frontier yet/i)).toBeVisible();
  });

  it("loads the durable optimization result after job success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input, init) => {
        const path = String(input);
        if (
          path.endsWith("/api/v1/optimizations") &&
          init?.method === "POST"
        ) {
          return Promise.resolve(
            jsonResponse(succeededJob("optimization", "optimization-job"), 202),
          );
        }
        if (path.endsWith("/api/v1/jobs/optimization-job/result")) {
          const candidate = {
            candidate_id: 7,
            objective_values: { ebitda: 1_200_000, otif: 0.97 },
            constraint_values: {},
            feasible: true,
            robustness: 0.92,
            weighted_score: 1,
            rank: 0,
            exclusion_reason: null,
          };
          return Promise.resolve(
            jsonResponse({
              optimization_id: 1,
              digest: "a".repeat(64),
              evaluations: 12,
              created_at: "2026-07-23T00:00:01Z",
              result: {
                frontier: [candidate],
                recommended: candidate,
                dominated: [],
                infeasible: [],
                sensitivity: [],
                convergence: [],
                evaluations: 12,
                converged: true,
                seed: 731,
              },
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<OptimizationLabPage />);

    await user.click(screen.getByRole("button", { name: "Run NSGA-II" }));

    expect(await screen.findByText("Recommended")).toBeVisible();
    expect(screen.getByText("12")).toBeVisible();
  });
});

describe("adaptive policy builder", () => {
  it("renders the declarative rule preview", () => {
    renderWithClient(<AdaptivePolicyPage />);
    expect(screen.getByText(/backlog_days/i)).toBeVisible();
  });

  it("renders the durable adaptive comparison result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input, init) => {
        const path = String(input);
        if (
          path.endsWith("/api/v1/adaptive-policies/compare") &&
          init?.method === "POST"
        ) {
          return Promise.resolve(
            jsonResponse(
              succeededJob("adaptive_comparison", "adaptive-job"),
              202,
            ),
          );
        }
        if (path.endsWith("/api/v1/jobs/adaptive-job/result")) {
          return Promise.resolve(
            jsonResponse({
              policy_id: "adaptive-capacity",
              static_scenario_id: "static",
              adaptive_scenario_id: "adaptive",
              replications: 6,
              master_seed: 731,
              metric_deltas: { ebitda: 250_000, otif: 0.02 },
              activation_count: 3,
              total_action_cost_cents: 1_200_000,
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      }),
    );
    const user = userEvent.setup();
    renderWithClient(<AdaptivePolicyPage />);

    await user.click(
      screen.getByRole("button", { name: "Compare vs static" }),
    );

    expect(await screen.findByText("Activations")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
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

describe("decision ledger error state", () => {
  it("surfaces a problem detail when the ledger request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() =>
        Promise.resolve(
          jsonResponse(
            {
              type: "about:blank",
              title: "Service unavailable",
              status: 503,
              code: "ledger_unavailable",
              detail: "The ledger is temporarily unavailable.",
              trace_id: "t1",
              violations: [],
            },
            503,
          ),
        ),
      ),
    );
    renderWithClient(<DecisionLedgerPage />);
    expect(
      await screen.findByText(/could not load the ledger/i),
    ).toBeVisible();
    expect(
      screen.getByText(/the ledger is temporarily unavailable/i),
    ).toBeVisible();
  });
});

describe("monitoring center", () => {
  it("prompts for a decision id", () => {
    renderWithClient(<MonitoringCenterPage />);
    expect(screen.getByText(/enter a decision/i)).toBeVisible();
  });

  it("shows the empty state when a decision has no outcomes (404)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() =>
        Promise.resolve(
          jsonResponse(
            {
              type: "about:blank",
              title: "No monitoring report",
              status: 404,
              code: "monitoring_not_found",
              detail: "No outcomes yet.",
              trace_id: "t1",
              violations: [],
            },
            404,
          ),
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithClient(<MonitoringCenterPage />);
    await user.type(screen.getByPlaceholderText(/northstar-pricing/i), "ghost");
    await user.click(screen.getByRole("button", { name: /load outcomes/i }));
    expect(
      await screen.findByText(/no outcomes recorded/i),
    ).toBeVisible();
  });
});
