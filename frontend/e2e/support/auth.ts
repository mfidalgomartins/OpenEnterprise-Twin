import { expect, type Page } from "@playwright/test";

export async function authenticate(
  page: Page,
  destination = "/",
): Promise<void> {
  await page.goto("/");
  const signIn = page.getByRole("button", { name: "Sign in securely" });
  if (await signIn.isVisible()) {
    await signIn.click();
    await expect(
      page.getByRole("heading", { level: 1, name: "Decision briefing" }),
    ).toBeVisible({ timeout: 15_000 });
  }
  if (destination !== "/") {
    await page.goto(destination);
  }
}
