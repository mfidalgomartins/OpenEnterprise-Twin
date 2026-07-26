import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { AppRoutes } from "../src/app/routes";
import {
  AuthContext,
  type AuthContextValue,
} from "../src/features/auth/authContext";
import { JobStatus } from "../src/features/jobs/JobStatus";
import { JobsPage } from "../src/features/jobs/JobsPage";
import { pollIntervalForJob } from "../src/features/jobs/useJob";
import type {
  Job,
  JobStatus as JobState,
} from "../src/features/jobs/types";

const now = "2026-07-26T08:00:00Z";

function job(
  status: JobState,
  overrides: Partial<Job> = {},
): Job {
  return {
    job_id: `job-${status}`,
    kind: "optimization",
    status,
    created_by: "finance-lead",
    attempt_count: status === "queued" ? 0 : 1,
    max_attempts: 3,
    progress:
      status === "succeeded" || status === "failed" || status === "cancelled"
        ? 100
        : status === "running"
          ? 42
          : 0,
    stage: status === "running" ? "evaluating_candidates" : status,
    cancellation_requested_at: null,
    next_attempt_at: null,
    result_resource_type: status === "succeeded" ? "optimization" : null,
    result_resource_id: status === "succeeded" ? "17" : null,
    result_digest: status === "succeeded" ? "a".repeat(64) : null,
    result_location:
      status === "succeeded" ? `/api/v1/jobs/job-${status}/result` : null,
    problem:
      status === "failed"
        ? {
            code: "optimization_failed",
            detail: "The bounded search could not complete.",
            occurred_at: now,
          }
        : null,
    created_at: now,
    started_at: status === "queued" ? null : now,
    finished_at:
      status === "succeeded" || status === "failed" || status === "cancelled"
        ? now
        : null,
    updated_at: now,
    ...overrides,
  };
}

function authValue(
  roles: NonNullable<AuthContextValue["session"]>["roles"] = ["analyst"],
): AuthContextValue {
  return {
    status: "authenticated",
    session: {
      subject: "finance-lead",
      tenant_id: "northstar",
      roles,
      authentication_method: "oidc",
    },
    error: null,
    mode: "oidc",
    login: async () => undefined,
    logout: async () => undefined,
    completeSignin: async () => undefined,
    can: (...required) => required.some((role) => roles.includes(role)),
  };
}

function TestContext({
  children,
  auth = authValue(),
}: PropsWithChildren<{ auth?: AuthContextValue }>) {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  const location = memoryLocation();
  return (
    <AuthContext.Provider value={auth}>
      <QueryClientProvider client={queryClient}>
        <Router hook={location.hook}>{children}</Router>
      </QueryClientProvider>
    </AuthContext.Provider>
  );
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    headers: { "Content-Type": "application/json" },
    status,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("job lifecycle polling", () => {
  it.each([
    ["queued", 1_000],
    ["running", 750],
    ["succeeded", false],
    ["failed", false],
    ["cancelled", false],
  ] satisfies Array<[JobState, number | false]>)(
    "uses a bounded interval for %s",
    (status, expected) => {
      expect(pollIntervalForJob(job(status))).toBe(expected);
    },
  );
});

describe("JobStatus", () => {
  it.each([
    ["queued", "Queued"],
    ["succeeded", "Succeeded"],
    ["cancelled", "Cancelled"],
  ] satisfies Array<[JobState, string]>)(
    "presents the %s state",
    (status, label) => {
      render(
        <TestContext>
          <JobStatus job={job(status)} />
        </TestContext>,
      );
      expect(screen.getByText(label)).toBeVisible();
    },
  );

  it("announces running progress, stage and attempt", () => {
    render(
      <TestContext>
        <JobStatus job={job("running")} />
      </TestContext>,
    );

    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "42",
    );
    expect(screen.getByText("Evaluating candidates")).toBeVisible();
    expect(screen.getByText("Attempt 1 of 3")).toBeVisible();
  });

  it("shows a safe terminal problem and traceable code", () => {
    render(
      <TestContext>
        <JobStatus job={job("failed")} />
      </TestContext>,
    );

    expect(
      screen.getByText("The bounded search could not complete."),
    ).toBeVisible();
    expect(screen.getByText("optimization_failed")).toBeVisible();
  });

  it("offers result and cancellation actions only when valid", async () => {
    const onCancel = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    const { rerender } = render(
      <TestContext>
        <JobStatus job={job("running")} canCancel onCancel={onCancel} />
      </TestContext>,
    );

    await user.click(screen.getByRole("button", { name: "Cancel job" }));
    expect(onCancel).toHaveBeenCalledOnce();

    rerender(
      <TestContext>
        <JobStatus job={job("succeeded")} canCancel onCancel={onCancel} />
      </TestContext>,
    );
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
    expect(screen.getByRole("link", { name: "Open result" })).toHaveAttribute(
      "href",
      "/api/v1/jobs/job-succeeded/result",
    );
  });

});

describe("JobsPage", () => {
  it("defaults to jobs created by the current user within the active tenant", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() =>
        Promise.resolve(
          jsonResponse([
            job("running"),
            job("queued", {
              job_id: "job-colleague",
              created_by: "operations-lead",
              kind: "calibration",
            }),
          ]),
        ),
      ),
    );

    render(
      <TestContext>
        <JobsPage />
      </TestContext>,
    );

    expect(
      screen.getByRole("heading", { name: "Analytical jobs" }),
    ).toBeVisible();
    const table = await screen.findByRole("table");
    expect(within(table).getByText("finance-lead")).toBeVisible();
    expect(within(table).queryByText("operations-lead")).toBeNull();
    expect(screen.getByText("northstar")).toBeVisible();
  });

  it("lets an analyst cancel an active job and refreshes its row", async () => {
    let current = job("running");
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const path = String(input);
      if (
        path.endsWith("/api/v1/jobs/job-running/cancellation") &&
        init?.method === "POST"
      ) {
        current = job("cancelled", { job_id: "job-running" });
        return Promise.resolve(jsonResponse(current, 202));
      }
      if (path.endsWith("/api/v1/jobs?limit=50")) {
        return Promise.resolve(jsonResponse([current]));
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();

    render(
      <TestContext>
        <JobsPage />
      </TestContext>,
    );
    await user.click(
      await screen.findByRole("button", { name: "Cancel job" }),
    );

    await waitFor(() =>
      expect(
        within(screen.getByLabelText("Job job-running")).getByText("Cancelled"),
      ).toBeVisible(),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/jobs/job-running/cancellation",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("does not expose cancellation to a viewer", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(() => Promise.resolve(jsonResponse([job("running")]))),
    );

    render(
      <TestContext auth={authValue(["viewer"])}>
        <JobsPage />
      </TestContext>,
    );

    await screen.findByText("Running");
    expect(screen.queryByRole("button", { name: "Cancel job" })).toBeNull();
  });
});

describe("jobs route", () => {
  it("opens the tenant job cockpit inside the persistent shell", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>((input) => {
        const path = String(input);
        if (path.endsWith("/api/v1/jobs?limit=50")) {
          return Promise.resolve(jsonResponse([]));
        }
        if (path.endsWith("/api/v1/company")) {
          return Promise.resolve(
            jsonResponse({
              company_id: "northstar-components",
              name: "Northstar Components",
              model_version: "0.6.0",
              products: [],
              customer_segments: [],
              plant: { resources: [], materials: [] },
            }),
          );
        }
        return Promise.reject(new Error(`Unexpected request: ${path}`));
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const location = memoryLocation({ path: "/jobs" });

    render(
      <AuthContext.Provider value={authValue()}>
        <QueryClientProvider client={queryClient}>
          <Router hook={location.hook}>
            <AppRoutes />
          </Router>
        </QueryClientProvider>
      </AuthContext.Provider>,
    );

    expect(
      await screen.findByRole("heading", { name: "Analytical jobs" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Jobs" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
