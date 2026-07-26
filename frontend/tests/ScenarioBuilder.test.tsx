import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { ScenarioBuilder } from "../src/features/scenarios/ScenarioBuilder";
import {
  buildCandidateScenario,
  defaultScenarioDraft,
} from "../src/features/scenarios/scenarioDraft";

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
      segment_id: "spot",
      name: "Spot buyers",
      payment_terms_days: 15,
    },
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

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

function problemResponse(code: string, detail: string, status: number) {
  return jsonResponse(
    {
      type: "about:blank",
      title: "Request failed",
      status,
      code,
      detail,
      trace_id: "trace-builder",
      violations: [],
    },
    status,
  );
}

function referenceFetch() {
  return vi.fn<typeof fetch>((input) => {
    const path = String(input);
    if (path.endsWith("/api/v1/baseline")) {
      return Promise.resolve(jsonResponse(baseline));
    }
    if (path.endsWith("/api/v1/company")) {
      return Promise.resolve(jsonResponse(company));
    }
    return Promise.reject(new Error(`Unexpected API request: ${path}`));
  });
}

function successfulExperimentFetch() {
  let experimentPosts = 0;

  return vi.fn<typeof fetch>((input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    if (method === "GET" && path.endsWith("/api/v1/baseline")) {
      return Promise.resolve(jsonResponse(baseline));
    }
    if (method === "GET" && path.endsWith("/api/v1/company")) {
      return Promise.resolve(jsonResponse(company));
    }
    if (method === "GET" && path.endsWith("/api/v1/scenarios/current-plan")) {
      return Promise.resolve(jsonResponse(baseline));
    }
    if (method === "GET" && path.includes("/api/v1/scenarios/")) {
      return Promise.resolve(
        problemResponse("scenario_not_found", "Scenario not found.", 404),
      );
    }
    if (method === "POST" && path.endsWith("/api/v1/scenarios")) {
      return Promise.resolve(jsonResponse({ id: "candidate" }, 201));
    }
    if (method === "POST" && path.includes("/experiments")) {
      experimentPosts += 1;
      return Promise.resolve(
        jsonResponse(
          {
            job_id: `experiment-job-${experimentPosts}`,
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
            result_resource_id: String(experimentPosts),
            result_digest: "a".repeat(64),
            result_location: `/api/v1/jobs/experiment-job-${experimentPosts}/result`,
            problem: null,
            created_at: "2026-07-18T10:00:00Z",
            started_at: "2026-07-18T10:00:00Z",
            finished_at: "2026-07-18T10:00:01Z",
            updated_at: "2026-07-18T10:00:01Z",
          },
          202,
        ),
      );
    }
    return Promise.reject(
      new Error(`Unexpected API request: ${method} ${path}`),
    );
  });
}

function renderBuilder() {
  const location = memoryLocation();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <Router hook={location.hook}>
        <ScenarioBuilder />
      </Router>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

describe("ScenarioBuilder", () => {
  it("versions immutable scenario identity with the baseline model", () => {
    const versionedBaseline = {
      ...baseline,
      company_model_version: "0.3.0",
    };

    expect(
      buildCandidateScenario(defaultScenarioDraft, baseline).scenario_id,
    ).not.toBe(
      buildCandidateScenario(defaultScenarioDraft, versionedBaseline).scenario_id,
    );
  });

  it("enforces policy and compute-budget boundaries", async () => {
    vi.stubGlobal("fetch", referenceFetch());
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    const capacity = screen.getByLabelText("Test capacity change");
    const safetyStock = screen.getByLabelText("Electronics safety stock");
    const iterations = screen.getByLabelText("Paired iterations");

    await user.clear(price);
    await user.type(price, "-100");
    await user.clear(capacity);
    await user.type(capacity, "1001");
    await user.clear(safetyStock);
    await user.type(safetyStock, "366");
    await user.clear(iterations);
    await user.type(iterations, "1001");

    expect(
      screen.getByText(
        "Price change must be greater than -100% and no more than 1,000%.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Capacity change must be between -100% and 1,000%.",
      ),
    ).toBeVisible();
    expect(
      screen.getByText("Safety stock must be between 0 and 365 days."),
    ).toBeVisible();
    expect(
      screen.getByText("Iterations must be a whole number from 1 to 1,000."),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Run comparison" }),
    ).toBeDisabled();
  });

  it("counts changed drivers and submits a bounded scenario payload", async () => {
    const fetchMock = successfulExperimentFetch();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    await user.clear(price);
    await user.type(price, "4");
    await user.clear(screen.getByLabelText("Test capacity change"));
    await user.type(screen.getByLabelText("Test capacity change"), "8");
    await user.clear(screen.getByLabelText("Electronics safety stock"));
    await user.type(screen.getByLabelText("Electronics safety stock"), "8");

    expect(screen.getByText("3 changed drivers")).toBeVisible();
    const runButton = screen.getByRole("button", { name: "Run comparison" });
    expect(runButton).toBeEnabled();
    const form = (runButton as HTMLButtonElement).form;
    const invalidInputs = Array.from(form?.elements ?? [])
      .filter(
        (element): element is HTMLInputElement =>
          element instanceof HTMLInputElement && !element.checkValidity(),
      )
      .map((element) => ({
        id: element.id,
        maximum: element.max,
        minimum: element.min,
        step: element.step,
        value: element.value,
      }));
    expect(invalidInputs).toEqual([]);
    await user.click(runButton);

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Comparison evidence is ready.",
      ),
    );
    const resultLink = await screen.findByRole("link", {
      name: "Open latest decision room",
    });
    expect(resultLink).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/scenarios\/.+\/compare\?experiment=2$/),
    );

    const scenarioCalls = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith("/api/v1/scenarios") &&
        init?.method === "POST",
    );
    expect(scenarioCalls).toHaveLength(1);
    const candidatePayload = JSON.parse(String(scenarioCalls[0]?.[1]?.body));
    expect(candidatePayload.scenario_id).toMatch(/-[0-9a-z]{13}$/);
    expect(candidatePayload.scenario_id.length).toBeLessThanOrEqual(80);
    expect(candidatePayload).toMatchObject({
      baseline_scenario_id: "current-plan",
      company_model_version: "0.2.0",
      policy_levers: {
        price_changes: [
          {
            price_change: "0.04",
            product_id: "intelligent-valve",
            segment_id: "spot",
          },
        ],
        resource_changes: [
          {
            overtime_capacity_minutes: 0,
            regular_capacity_change: "0.08",
            resource_id: "test",
          },
        ],
        material_changes: [
          expect.objectContaining({
            material_id: "electronics",
            safety_stock_coverage_days: "8",
          }),
        ],
      },
    });

    const experimentCalls = fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).includes("/experiments") && init?.method === "POST",
    );
    expect(experimentCalls).toHaveLength(2);
    expect(JSON.parse(String(experimentCalls[0]?.[1]?.body))).toEqual({
      iterations: 100,
      seed: 731,
      max_workers: 1,
    });
  });

  it("announces progress and retains the last completed comparison", async () => {
    const firstRun = successfulExperimentFetch();
    let holdSecondCandidate = false;
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = String(input);
      if (
        holdSecondCandidate &&
        init?.method === "POST" &&
        path.includes("/experiments") &&
        !path.includes("current-plan")
      ) {
        return new Promise<Response>(() => undefined);
      }
      return firstRun(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    await user.clear(price);
    await user.type(price, "4");
    await user.click(screen.getByRole("button", { name: "Run comparison" }));
    const latest = await screen.findByRole("link", {
      name: "Open latest decision room",
    });

    holdSecondCandidate = true;
    await user.clear(screen.getByLabelText("Test capacity change"));
    await user.type(screen.getByLabelText("Test capacity change"), "5");
    await user.click(screen.getByRole("button", { name: "Run comparison" }));

    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        /Running candidate experiment/i,
      ),
    );
    expect(latest).toBeVisible();
  });

  it("surfaces durable worker progress for an active experiment", async () => {
    const fallback = successfulExperimentFetch();
    const queuedJob = {
      job_id: "baseline-job-live",
      kind: "experiment",
      status: "queued",
      created_by: "test-admin",
      attempt_count: 0,
      max_attempts: 3,
      progress: 0,
      stage: "queued",
      cancellation_requested_at: null,
      next_attempt_at: null,
      result_resource_type: null,
      result_resource_id: null,
      result_digest: null,
      result_location: null,
      problem: null,
      created_at: "2026-07-18T10:00:00Z",
      started_at: null,
      finished_at: null,
      updated_at: "2026-07-18T10:00:00Z",
    };
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (
        method === "POST" &&
        path.endsWith("/scenarios/current-plan/experiments")
      ) {
        return Promise.resolve(jsonResponse(queuedJob, 202));
      }
      if (method === "GET" && path.endsWith("/api/v1/jobs/baseline-job-live")) {
        return Promise.resolve(
          jsonResponse({
            ...queuedJob,
            status: "running",
            attempt_count: 1,
            progress: 42,
            stage: "simulating_replications",
            started_at: "2026-07-18T10:00:01Z",
            updated_at: "2026-07-18T10:00:02Z",
          }),
        );
      }
      return fallback(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    await user.clear(price);
    await user.type(price, "4");
    await user.click(screen.getByRole("button", { name: "Run comparison" }));

    expect(await screen.findByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "42",
    );
    expect(screen.getByText("Simulating replications")).toBeVisible();
    expect(screen.getByRole("link", { name: "Manage jobs" })).toHaveAttribute(
      "href",
      "/jobs",
    );
  });

  it("preserves inputs and shows a stable API code with corrective action", async () => {
    const fetchMock = successfulExperimentFetch();
    fetchMock.mockImplementation((input, init) => {
      const path = String(input);
      if (init?.method === "POST" && path.endsWith("/api/v1/scenarios")) {
        return Promise.resolve(
          problemResponse(
            "scenario_incompatible",
            "The selected lever is outside the company model.",
            422,
          ),
        );
      }
      return successfulExperimentFetch()(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    await user.clear(price);
    await user.type(price, "4");
    await user.click(screen.getByRole("button", { name: "Run comparison" }));

    expect(
      await screen.findByText("Error code: scenario_incompatible"),
    ).toBeVisible();
    expect(
      screen.getByText("Review the highlighted lever limits and try again."),
    ).toBeVisible();
    expect(price).toHaveValue(4);
  });

  it("refuses to run when an immutable scenario id resolves to other inputs", async () => {
    const fallbackFetch = successfulExperimentFetch();
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (
        method === "GET" &&
        path.includes("/api/v1/scenarios/") &&
        !path.endsWith("/api/v1/scenarios/current-plan")
      ) {
        const scenarioId = decodeURIComponent(path.split("/").at(-1) ?? "");
        return Promise.resolve(
          jsonResponse({
            ...baseline,
            id: scenarioId,
            scenario_id: scenarioId,
            name: "Conflicting stored scenario",
            baseline_scenario_id: "current-plan",
          }),
        );
      }
      return fallbackFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    await user.clear(price);
    await user.type(price, "4");
    await user.click(screen.getByRole("button", { name: "Run comparison" }));

    expect(
      await screen.findByText("Error code: scenario_conflict"),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Change a driver or scenario name to create a distinct immutable revision.",
      ),
    ).toBeVisible();
  });

  it("accepts a stored immutable scenario with equivalent decimal formatting", async () => {
    const expected = buildCandidateScenario(
      { ...defaultScenarioDraft, price_change_percent: "4" },
      baseline,
    );
    const fallbackFetch = successfulExperimentFetch();
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = String(input);
      const method = init?.method ?? "GET";
      if (
        method === "GET" &&
        path.includes("/api/v1/scenarios/") &&
        !path.endsWith("/api/v1/scenarios/current-plan")
      ) {
        return Promise.resolve(
          jsonResponse({
            id: expected.scenario_id,
            ...expected,
            policy_levers: {
              one_off_capital_investment_cents: 0,
              payment_term_changes: [],
              material_changes: [],
              resource_changes: [],
              commercial_investment_change: "0.000",
              price_changes: [
                {
                  price_change: "0.040",
                  product_id: "intelligent-valve",
                  segment_id: "spot",
                },
              ],
            },
          }),
        );
      }
      return fallbackFetch(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    await user.clear(price);
    await user.type(price, "4");
    await user.click(screen.getByRole("button", { name: "Run comparison" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Comparison evidence is ready.",
      ),
    );
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith("/api/v1/scenarios") &&
          init?.method === "POST",
      ),
    ).toHaveLength(0);
  });
});
