import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { useScenarioExperiment } from "../src/features/scenarios/useScenarioExperiment";
import type {
  ScenarioPayload,
  ScenarioResource,
} from "../src/features/scenarios/types";

const baseline: ScenarioResource = {
  id: "current-plan",
  scenario_id: "current-plan",
  name: "Current plan",
  company_model_version: "0.2.0",
  schema_version: "0.1.0",
  horizon_days: 5,
  warmup_days: 0,
  evaluation_days: 5,
  runoff_days: 0,
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

const candidate: ScenarioPayload = {
  scenario_id: "numeric-identifier",
  name: "Numeric identifier",
  company_model_version: baseline.company_model_version,
  schema_version: baseline.schema_version,
  horizon_days: baseline.horizon_days,
  warmup_days: baseline.warmup_days,
  evaluation_days: baseline.evaluation_days,
  runoff_days: baseline.runoff_days,
  baseline_scenario_id: baseline.scenario_id,
  policy_levers: {
    ...baseline.policy_levers,
    price_changes: [
      {
        segment_id: "spot",
        product_id: "001",
        price_change: "0.07",
      },
    ],
  },
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

test("does not normalize numeric-looking policy identifiers", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>((input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (method === "GET" && path.endsWith("/scenarios/current-plan")) {
        return Promise.resolve(jsonResponse(baseline));
      }
      if (method === "GET" && path.endsWith("/scenarios/numeric-identifier")) {
        return Promise.resolve(
          jsonResponse({
            id: candidate.scenario_id,
            ...candidate,
            policy_levers: {
              ...candidate.policy_levers,
              price_changes: [
                {
                  ...candidate.policy_levers.price_changes[0],
                  product_id: "1",
                  price_change: "0.070",
                },
              ],
            },
          }),
        );
      }
      if (method === "POST" && path.includes("/experiments")) {
        return Promise.resolve(
          jsonResponse(
            {
              job_id: "baseline-job",
              kind: "experiment",
              status: "succeeded",
              created_by: "test-admin",
              attempt_count: 1,
              max_attempts: 3,
              progress: 100,
              stage: "completed",
              cancellation_requested_at: null,
              next_attempt_at: null,
              result_resource_type: "experiment",
              result_resource_id: "1",
              result_digest: "a".repeat(64),
              result_location: "/api/v1/jobs/baseline-job/result",
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
      return Promise.reject(new Error(`Unexpected request: ${method} ${path}`));
    }),
  );
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    );
  }
  const { result } = renderHook(() => useScenarioExperiment(), { wrapper });

  await act(async () => {
    await result.current.runScenario({
      baseline,
      candidate,
      iterations: 1,
      seed: 731,
    });
  });

  await waitFor(() => expect(result.current.phase).toBe("failed"));
  expect(result.current.issue?.code).toBe("scenario_conflict");
});
