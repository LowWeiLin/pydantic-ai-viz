import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PVIZ_E2E_PORT ?? 4319);

/**
 * The specs drive the BUILT viewer (`pnpm build` first) through `e2e/server.mjs`.
 *
 * - `viewer`: behaviour and accessibility over the committed fixtures.
 * - `screenshots`: the docs images. Reads recordings from PVIZ_RECORDINGS_DIR (the real demo
 *   recordings) and falls back to the fixtures; see `e2e/screenshots.spec.ts` for where it writes.
 */
export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "viewer",
      testIgnore: /screenshots\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
    {
      name: "screenshots",
      testMatch: /screenshots\.spec\.ts/,
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 2,
      },
    },
  ],
  webServer: {
    command: "node e2e/server.mjs",
    url: `http://127.0.0.1:${port}/bare/`,
    reuseExistingServer: false,
    stdout: "ignore",
    stderr: "pipe",
  },
});
