import { expect, test } from "@playwright/test";

import { authenticate } from "./support/auth";

const now = "2026-07-26T08:00:00Z";

function job(
  jobId: string,
  status: "queued" | "running" | "succeeded" | "cancelled",
  progress: number,
) {
  return {
    job_id: jobId,
    kind: "optimization",
    status,
    created_by: "e2e-analyst",
    attempt_count: status === "queued" ? 0 : 1,
    max_attempts: 3,
    progress,
    stage: status === "running" ? "evaluating_candidates" : status,
    cancellation_requested_at: status === "cancelled" ? now : null,
    next_attempt_at: null,
    result_resource_type: status === "succeeded" ? "optimization" : null,
    result_resource_id: status === "succeeded" ? "17" : null,
    result_digest: status === "succeeded" ? "a".repeat(64) : null,
    result_location:
      status === "succeeded" ? `/api/v1/jobs/${jobId}/result` : null,
    problem: null,
    created_at: now,
    started_at: status === "queued" ? null : now,
    finished_at:
      status === "succeeded" || status === "cancelled" ? now : null,
    updated_at: now,
  };
}

test("authenticates with authorization code + PKCE and signs out", async ({
  page,
}) => {
  test.skip(
    process.env.OIDC_E2E !== "1",
    "Set OIDC_E2E=1 with the local identity fixture.",
  );

  await authenticate(page);
  await expect(page.getByText("northstar", { exact: true })).toBeVisible();
  await expect(page.getByText("analyst · viewer")).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Enter the decision cockpit" }),
  ).toBeVisible();
});

test("presents progress, cancellation and successful durable results", async ({
  page,
}) => {
  test.skip(
    process.env.OIDC_E2E !== "1",
    "Set OIDC_E2E=1 with the local identity fixture.",
  );

  let listCount = 0;
  let cancelled = false;
  await page.route("**/api/v1/jobs**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (
      request.method() === "POST" &&
      url.pathname === "/api/v1/jobs/job-cancel/cancellation"
    ) {
      cancelled = true;
      await route.fulfill({
        json: job("job-cancel", "cancelled", 100),
        status: 202,
      });
      return;
    }
    if (request.method() === "GET" && url.pathname === "/api/v1/jobs") {
      listCount += 1;
      const lifecycle =
        listCount === 1
          ? job("job-success", "queued", 0)
          : listCount === 2
            ? job("job-success", "running", 45)
            : job("job-success", "succeeded", 100);
      await route.fulfill({
        json: [
          lifecycle,
          cancelled
            ? job("job-cancel", "cancelled", 100)
            : job("job-cancel", "running", 30),
        ],
      });
      return;
    }
    await route.fallback();
  });

  await authenticate(page, "/jobs");
  await expect(
    page.getByRole("heading", { level: 1, name: "Analytical jobs" }),
  ).toBeVisible();
  const cancellable = page.locator("tr", { hasText: "job-cancel" });
  await cancellable.getByRole("button", { name: "Cancel job" }).click();
  await expect(cancellable.getByText("Cancelled")).toBeVisible();

  const successful = page.locator("tr", { hasText: "job-success" });
  await expect(successful.getByText("Evaluating candidates")).toBeVisible({
    timeout: 5_000,
  });
  await expect(
    successful.getByRole("link", { name: "Open result" }),
  ).toBeVisible({ timeout: 5_000 });
});

test("an analyst bearer is denied an approval transition", async ({ page }) => {
  test.skip(
    process.env.OIDC_E2E !== "1",
    "Set OIDC_E2E=1 with the local identity fixture.",
  );

  await authenticate(page);
  const response = await page.evaluate(async (apiBaseUrl) => {
    const key = Object.keys(window.sessionStorage).find((candidate) =>
      candidate.startsWith("oidc.user:"),
    );
    if (!key) {
      throw new Error("OIDC session was not stored");
    }
    const stored = JSON.parse(window.sessionStorage.getItem(key) ?? "{}") as {
      access_token?: string;
    };
    const result = await fetch(
      `${apiBaseUrl}/api/v1/ledger/decisions/missing/transitions`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${stored.access_token ?? ""}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ expected_version: 1, target: "approved" }),
      },
    );
    return { status: result.status, body: await result.json() };
  }, process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000");

  expect(response.status).toBe(403);
  expect(response.body.code).toBe("authorization_denied");
});
