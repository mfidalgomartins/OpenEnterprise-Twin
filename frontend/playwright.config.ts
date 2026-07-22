import { defineConfig } from "@playwright/test";

export default defineConfig({
  fullyParallel: true,
  outputDir: "output/playwright",
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
  ],
  testDir: "e2e",
  use: {
    baseURL: "http://127.0.0.1:4173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    reuseExistingServer: process.env.LIVE_E2E !== "1",
    url: "http://127.0.0.1:4173",
  },
});
