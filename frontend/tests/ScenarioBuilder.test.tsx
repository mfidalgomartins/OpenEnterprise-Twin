import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { ScenarioBuilder } from "../src/features/scenarios/ScenarioBuilder";

const baseline = {
  id: "current-plan",
  scenario_id: "current-plan",
  name: "Current plan",
  company_model_version: "0.1.0",
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
  model_version: "0.1.0",
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
  let scenarioPosts = 0;
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
    if (method === "POST" && path.endsWith("/api/v1/scenarios")) {
      scenarioPosts += 1;
      return Promise.resolve(
        scenarioPosts === 1
          ? problemResponse(
              "scenario_conflict",
              "Current plan already exists.",
              409,
            )
          : jsonResponse({ id: "candidate" }, 201),
      );
    }
    if (method === "POST" && path.includes("/experiments")) {
      experimentPosts += 1;
      return Promise.resolve(
        jsonResponse(
          {
            id: experimentPosts,
            scenario_id:
              experimentPosts === 1 ? "current-plan" : "candidate",
            baseline_experiment_id: experimentPosts === 1 ? null : 1,
            status: "completed",
            seed: 731,
            iterations: 100,
            master_seed: 731,
            replication_count: 100,
            artifact_digest: "a".repeat(64),
            error_code: null,
            error_detail: null,
            created_at: "2026-07-18T10:00:00Z",
            started_at: "2026-07-18T10:00:00Z",
            completed_at: "2026-07-18T10:00:01Z",
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
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ScenarioBuilder />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("ScenarioBuilder", () => {
  it("enforces the price, capacity, and safety-stock boundaries", async () => {
    vi.stubGlobal("fetch", referenceFetch());
    const user = userEvent.setup();
    renderBuilder();

    const price = await screen.findByLabelText(
      "Spot intelligent valve price change",
    );
    const capacity = screen.getByLabelText("Test capacity change");
    const safetyStock = screen.getByLabelText("Electronics safety stock");

    await user.clear(price);
    await user.type(price, "-100");
    await user.clear(capacity);
    await user.type(capacity, "1001");
    await user.clear(safetyStock);
    await user.type(safetyStock, "366");

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
    expect(scenarioCalls).toHaveLength(2);
    const candidatePayload = JSON.parse(String(scenarioCalls[1]?.[1]?.body));
    expect(candidatePayload).toMatchObject({
      baseline_scenario_id: "current-plan",
      company_model_version: "0.1.0",
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
    expect(screen.getByRole("status")).toHaveTextContent(
      /Running candidate experiment/i,
    );
    expect(latest).toBeVisible();
  });

  it("preserves inputs and shows a stable API code with corrective action", async () => {
    const fetchMock = successfulExperimentFetch();
    let scenarioPosts = 0;
    fetchMock.mockImplementation((input, init) => {
      const path = String(input);
      if (init?.method === "POST" && path.endsWith("/api/v1/scenarios")) {
        scenarioPosts += 1;
        if (scenarioPosts === 2) {
          return Promise.resolve(
            problemResponse(
              "scenario_incompatible",
              "The selected lever is outside the company model.",
              422,
            ),
          );
        }
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
});
