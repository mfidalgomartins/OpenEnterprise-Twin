import { act, renderHook, waitFor } from "@testing-library/react";

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
              id: path.includes("current-plan") ? 1 : 2,
              scenario_id: path.includes("current-plan")
                ? "current-plan"
                : "numeric-identifier",
              baseline_experiment_id: path.includes("current-plan") ? null : 1,
              status: "completed",
              seed: 731,
              iterations: 1,
              master_seed: 731,
              replication_count: 1,
              artifact_digest: "a".repeat(64),
              error_code: null,
              error_detail: null,
              created_at: "2026-07-23T00:00:00Z",
              started_at: "2026-07-23T00:00:00Z",
              completed_at: "2026-07-23T00:00:01Z",
            },
            202,
          ),
        );
      }
      return Promise.reject(new Error(`Unexpected request: ${method} ${path}`));
    }),
  );
  const { result } = renderHook(() => useScenarioExperiment(0));

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
