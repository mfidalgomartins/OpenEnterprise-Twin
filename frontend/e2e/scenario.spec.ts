import { expect, test } from "@playwright/test";

test("keeps the model context legible at the mobile breakpoint", async ({
  page,
}) => {
  await page.setViewportSize({ height: 844, width: 390 });
  await page.goto("/");

  const contextItems = page.locator(".model-context__item");
  await expect(contextItems).toHaveCount(4);

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
