import { defineConfig, devices } from '@playwright/test';
import os from 'node:os';

/**
 * Playwright E2E test configuration for OpenConstructionERP frontend.
 *
 * Scope: this config drives the NEW QA infrastructure under `tests/e2e/`
 * (smoke/, fixtures/, helpers/). The legacy spec folder `./e2e/` is still
 * served by its dedicated configs (e.g. `playwright.boq-tour.config.ts`,
 * `playwright.match.config.ts`) and is intentionally excluded here so the
 * harness can be opted-into per-batch without sweeping in older flows.
 *
 * Run examples:
 *   npm run test:e2e:smoke           # all smoke specs, all browsers
 *   npm run test:e2e:headed          # headed (debug)
 *   npx playwright test smoke/health.spec.ts --project=chromium
 *   ./tests/e2e/runner/parallel-runner.sh batch-01-auth
 *
 * Environment variables:
 *   OE_TEST_BASE_URL    — defaults to http://localhost:5173
 *   OE_TEST_API_URL     — backend, defaults to http://localhost:8000
 *   OE_TEST_LOCALE      — en|de|ru|ar|es|fr|pt|it|pl|ja|ko|zh (default en)
 *   OE_TEST_DEMO_EMAIL  — demo account, defaults to demo@openconstructionerp.com
 *   OE_TEST_DEMO_PASSWORD
 *   OE_TEST_WORKERS     — override worker count (cap is 4)
 *   CI                  — when set, retries=2 and forbidOnly=true
 */

const BASE_URL = process.env.OE_TEST_BASE_URL ?? 'http://localhost:5173';
const LOCALE = process.env.OE_TEST_LOCALE ?? 'en';

// Workers: auto-detect cores, cap at 4 (avoids hammering the demo backend
// which rate-limits /auth/login/ at ~5 req/min per IP).
const detectedCores = Math.max(1, os.cpus()?.length ?? 1);
const defaultWorkers = Math.min(4, Math.max(1, Math.floor(detectedCores / 2)));
const workers = process.env.OE_TEST_WORKERS
  ? Number(process.env.OE_TEST_WORKERS)
  : defaultWorkers;

// Per-project URL append helper: keeps the locale query alive across navigations.
const localeUrl = (locale: string): string => {
  const u = new URL(BASE_URL);
  if (locale && locale !== 'en') u.searchParams.set('locale', locale);
  return u.toString();
};

// Specs that only make sense at a phone viewport. `mobile-chromium` selects
// them; every desktop project has to deselect them with the SAME expression,
// or a spec called "fits in an iPhone SE viewport" also runs at 1280x720 and
// measures nothing there. One constant so the two halves cannot drift.
// @responsive was in this alternation and carried by no spec anywhere, so the
// mobile project promised a tag it never selected - the same shape as the RTL
// project selecting a test that measured no direction. Dropping it is a no-op
// against the tree (nothing matched it) and stops the promise being made.
const MOBILE_ONLY = /@mobile/;

export default defineConfig({
  testDir: './tests/e2e',
  // Only run specs under named module folders (smoke/, boq/, etc.).
  // Root-level legacy specs in tests/e2e/*.spec.ts have their own
  // dedicated configs (playwright.boq-tour.config.ts, ...) and are
  // intentionally ignored by this harness.
  testMatch: ['**/*.spec.ts'],
  testIgnore: [
    '**/fixtures/**',
    '**/helpers/**',
    '**/runner/**',
    '**/reporters/**',
    '**/node_modules/**',
    // Legacy specs sitting directly under tests/e2e/ (one-level deep)
    // are invoked via their dedicated configs at the repo root.
    'tests/e2e/*.spec.ts',
  ],

  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers,

  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },

  reporter: [
    ['html', { outputFolder: 'qa-report', open: 'never' }],
    ['list'],
    ['json', { outputFile: 'qa-results.json' }],
    // Fails the run if a project declared above contributed no tests, or needs
    // a browser that is not installed. Playwright is loud about the second and
    // completely silent about the first, and a project that quietly selected
    // nothing leaves a green summary that reads as coverage we do not have.
    ['./tests/e2e/reporters/project-coverage.ts'],
  ],

  outputDir: 'test-results',

  use: {
    baseURL: localeUrl(LOCALE),
    headless: true,
    actionTimeout: 5_000,
    navigationTimeout: 30_000,
    screenshot: 'on',
    video: 'retain-on-failure',
    trace: 'on-first-retry',
    ignoreHTTPSErrors: true,
    locale: LOCALE === 'ar' ? 'ar-SA' : LOCALE,
    extraHTTPHeaders: {
      'X-DDC-Client': 'OE-QA/1.0',
    },
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      grepInvert: MOBILE_ONLY,
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
      grepInvert: MOBILE_ONLY,
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      grepInvert: MOBILE_ONLY,
    },
    {
      // Mobile-responsive checks: iPhone SE viewport (375x667).
      name: 'mobile-chromium',
      use: {
        ...devices['iPhone SE'],
        // `devices['iPhone SE']` carries `defaultBrowserType: 'webkit'`, so
        // this project launched WebKit despite its name and died with
        // "Executable doesn't exist at ...\webkit-2311" on any machine that
        // installed chromium only. It never mattered while the @mobile specs
        // were also being picked up by the desktop chromium project; now that
        // they are correctly filtered out of it, this project is the only
        // place they run, and an unrunnable project would mean they run
        // nowhere. The phone metrics below are what the specs actually assert
        // against, and Chromium honours all of them.
        browserName: 'chromium',
        viewport: { width: 375, height: 667 },
      },
      grep: MOBILE_ONLY,
    },
    {
      // RTL / Arabic locale project — verifies direction handling. Desktop
      // Chrome, so it deselects the phone-only specs too: a spec tagged
      // @i18n @mobile would otherwise land here at 1280x720.
      name: 'rtl-arabic',
      use: {
        ...devices['Desktop Chrome'],
        baseURL: localeUrl('ar'),
        locale: 'ar-SA',
      },
      grep: /@rtl|@i18n/,
      grepInvert: MOBILE_ONLY,
    },
  ],

  // We deliberately DO NOT auto-start the dev server here so test runs
  // exit cleanly with a typed error if the app is not reachable. The
  // health smoke spec surfaces a friendly "start dev server first" hint.
});
