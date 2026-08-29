/**
 * Standalone Playwright config for the BOQ Editor tour spec.
 *
 * Why a separate file: the default `playwright.config.ts` declares a
 * `webServer` that wants port 5173 and runs a `globalSetup` script that
 * isn't present in this worktree. The BOQ tour spec assumes a Vite
 * dev server is already running on port 5180 and a backend on 9090 —
 * we point this config at the live processes rather than start fresh
 * ones.
 *
 * Run:
 *   npx playwright test --config=playwright.boq-tour.config.ts \
 *     tests/e2e/boq-tour.spec.ts
 */
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  // Every other dedicated config in this directory names its own spec, and this
  // one did not. Without it `testDir` sweeps the whole harness, so listing this
  // config collected the vitest test under reporters/ and two specs that use
  // `__dirname` in ES module scope, and it reported three collection errors and
  // zero tests. It only ever appeared to work because the documented invocation
  // above passes the spec path on the command line, which narrows the sweep by
  // hand; anything that ran the config on its own got nothing.
  testMatch: 'boq-tour.spec.ts',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? 'http://localhost:5180',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    ignoreHTTPSErrors: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
