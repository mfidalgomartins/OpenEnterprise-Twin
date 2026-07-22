import { expect, test } from "@playwright/test";

test("keeps the model context legible at the mobile breakpoint", async ({
  page,
}) => {
  await page.setViewportSize({ height: 844, width: 390 });
  await page.goto("/");

  const contextItems = page.locator(".model-context__item");
  await expect(contextItems).toHaveCount(3);

  const itemWidths = await contextItems.evaluateAll((items) =>
    items.map((item) => item.getBoundingClientRect().width),
  );
  const labelLineCounts = await contextItems
    .locator("dt")
    .evaluateAll((labels) =>
      labels.map((label) => {
        const lineHeight = Number.parseFloat(getComputedStyle(label).lineHeight);
        return label.getBoundingClientRect().height / lineHeight;
      }),
    );

  expect(Math.min(...itemWidths)).toBeGreaterThanOrEqual(170);
  expect(Math.max(...labelLineCounts)).toBeLessThanOrEqual(2);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth === window.innerWidth,
    ),
  ).toBe(true);
});

test("runs a policy experiment through the live API and publishes its brief", async ({
  page,
}) => {
  test.skip(
    process.env.LIVE_E2E !== "1",
    "Set LIVE_E2E=1 when the backend release stack is available.",
  );

  await page.goto("/scenarios");
  await expect(
    page.getByRole("heading", { level: 1, name: "Policy studio" }),
  ).toBeVisible();

  await page.getByRole("textbox", { name: "Scenario name" }).fill(
    "Release gate resilience",
  );
  await page
    .getByRole("spinbutton", {
      name: "Spot intelligent valve price change",
    })
    .fill("1.5");
  await page
    .getByRole("spinbutton", { name: "Paired iterations" })
    .fill("2");
  await page.getByRole("button", { name: "Run comparison" }).click();

  await expect(page.getByRole("status")).toContainText(
    "Comparison evidence is ready.",
    { timeout: 30_000 },
  );
  await page.getByRole("link", { name: "Open latest decision room" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Release gate resilience" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Execution" })).toBeVisible();

  await page
    .getByRole("link", { name: "Open published executive brief" })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Executive decision brief" }),
  ).toBeVisible();
  await expect(page.locator("[data-report-chapter]")).toHaveCount(8);
});
