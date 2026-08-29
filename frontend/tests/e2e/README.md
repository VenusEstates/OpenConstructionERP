# Playwright E2E Infrastructure

Total-coverage UI testing for OpenConstructionERP. Designed for **hundreds
of parallel tests with screenshots on every step** — the QA-test-plan
agent authors specs that plug into this harness.

## Layout

```
tests/e2e/
├── fixtures/                 # Shared Playwright fixtures (composed via merge)
│   ├── auth.fixture.ts       # JWT once per worker, cached to playwright/.auth/demo.json
│   ├── api.fixture.ts        # Typed API client for setup/teardown
│   ├── tenant.fixture.ts     # Per-test isolated project (auto-cleanup)
│   ├── screenshot.fixture.ts # captureScreen(name) — auto-numbered, module-routed
│   ├── seed.fixture.ts       # Worker-scope demo seed via POST /api/v1/admin/seed-demo
│   └── index.ts              # Single re-export — `import { test, expect } from '../fixtures'`
├── helpers/                  # Stateless utilities
│   ├── nav.ts                # gotoModule, openSidebar, openCommandPalette, ...
│   ├── assert.ts             # expectDecimalMoneyString, expectIDORReturns404, expectA11yClean
│   ├── forms.ts              # fillForm — typed multiselect/date/file pattern
│   ├── screenshots.ts        # Standalone captureScreen (no TestInfo needed)
│   ├── wait.ts               # waitForToast, waitForGridRowCount, waitForModal
│   └── index.ts              # Bulk re-export
├── smoke/                    # 10 reference specs (templates for the QA agent)
│   ├── auth.spec.ts
│   ├── dashboard.spec.ts
│   ├── sidebar.spec.ts
│   ├── settings.spec.ts
│   ├── health.spec.ts
│   ├── language-switch.spec.ts
│   ├── mobile-responsive.spec.ts
│   ├── keyboard-shortcuts.spec.ts
│   ├── error-boundary.spec.ts
│   └── screenshot-baseline.spec.ts
├── runner/
│   └── parallel-runner.sh    # Orchestrator entry point
└── README.md                 # ← you are here
```

The **legacy** `frontend/e2e/` directory and its dedicated configs
(`playwright.boq-tour.config.ts`, `playwright.match.config.ts`, etc.)
are unchanged and still invoked explicitly — this new harness is
opt-in via `tests/e2e/` and the updated root `playwright.config.ts`.

## Adding a new spec

1. Decide the module folder — `tests/e2e/<module>/` (auto-creates the
   screenshot folder `qa-screenshots/<module>/`).
2. Create `<module>/<feature>.spec.ts`:

```ts
import { test, expect } from '../fixtures';
import { gotoModule, waitForToast } from '../helpers';

test.describe('@boq positions', () => {
  test('adds a position', async ({ authedPage, project, captureScreen }) => {
    await gotoModule(authedPage, 'boq');
    await captureScreen('boq-empty');
    // ... interact ...
    await waitForToast(authedPage, /saved|gespeichert/i);
    await captureScreen('boq-after-save');
  });
});
```

3. Tag the test for selective runs:
   - `@smoke`   — included in `npm run test:e2e:smoke`
   - `@mobile`  — runs under the `mobile-chromium` project
   - `@rtl`     — runs under the `rtl-arabic` project
   - `@i18n`    — locale-sensitive (also runs under `rtl-arabic`)
   - `@flaky`   — excluded from default runs (debug only)

## Running locally

```bash
# Install once (browser binaries)
npm run test:e2e:install

# All smoke specs across all browsers
npm run test:e2e:smoke

# Single spec, single browser, with the UI inspector
npx playwright test smoke/health.spec.ts --project=chromium --ui

# Headed mode (watch the browser)
npm run test:e2e:headed -- smoke/auth.spec.ts

# Specific batch via the orchestrator wrapper
./tests/e2e/runner/parallel-runner.sh smoke

# Open the most recent HTML report
npm run test:e2e:report
```

### Required services

The harness expects:
- `http://localhost:5173` — Vite dev server (`npm run dev`)
- `http://localhost:8000` — FastAPI backend (`uvicorn app.main:app --reload`)

If either is down, the **health smoke** fails fast with a friendly hint
that names the missing service — no stack traces.

## Reading the screenshot output

```
qa-screenshots/
├── smoke/
│   ├── smoke-login-page-empty-01-login-page-empty.png
│   ├── smoke-login-page-filled-02-login-page-filled.png
│   └── ...
├── baseline/
│   ├── baseline-dashboard-01-dashboard.png
│   └── ...
└── <your-module>/
    └── ...
```

Each filename = `<module>-<test-slug>-<step-NN>-<name>.png`. Screenshots
are also attached to the Playwright HTML report — open
`qa-report/index.html` and drill into any test to see them inline.

## Debugging a failing trace

```bash
# When a test fails on retry, Playwright writes a trace.zip.
# Find it under test-results/<test-name>/trace.zip:
ls test-results/

# Open the trace viewer
npx playwright show-trace test-results/<your-test>/trace.zip
```

The viewer scrubs through every action, network request, console log,
and snapshot — usually faster than re-running with `--headed`.

## Environment variables

| Var                     | Default                          | Notes |
|-------------------------|----------------------------------|-------|
| `OE_TEST_BASE_URL`      | `http://localhost:5173`          | Frontend |
| `OE_TEST_API_URL`       | `http://localhost:8000`          | Backend |
| `OE_TEST_LOCALE`        | `en`                             | en/de/ru/ar/es/fr/pt/it/pl/ja/ko/zh |
| `OE_TEST_DEMO_EMAIL`    | `demo@openconstructionerp.com`          | Demo user (note the "r") |
| `OE_TEST_DEMO_PASSWORD` | `OpenEstimate2024!`              |       |
| `OE_TEST_WORKERS`       | min(4, cores/2)                  | Cap is 4 — backend rate-limits login |
| `CI`                    | unset                            | When set: retries=2, forbidOnly=true |

## How auth works (1 login for 1000 tests)

`auth.fixture.ts` obtains a JWT once per worker via the backend's
`/api/v1/users/auth/demo-login/` (fallback: classic
`/api/v1/users/auth/login/`), then **caches it on disk** at
`playwright/.auth/demo.json`. Subsequent workers in the same run read
the file instead of hitting the backend again. The token is considered
fresh for 55 minutes (under the typical 60-min JWT exp).

Per-test `authedPage` opens a fresh `Page` and injects the cached token
into `localStorage` + `sessionStorage` via `addInitScript`, so the SPA
boots authenticated without a login round-trip.

## Troubleshooting

- **`auth.fixture: cannot log in`** — backend not running, or rate-limit
  hit. Wait 1 minute and re-run, or set `OE_TEST_DEMO_*` to a non-demo
  account.
- **All tests time out at `expectAppShell`** — usually a frontend build
  error. Run `npm run typecheck` to surface it.
- **Screenshots empty / black** — viewport may be 0×0. Don't set
  `viewport: null` on a custom project unless you know what you're doing.
- **Windows: `parallel-runner.sh: command not found`** — run from Git
  Bash, WSL, or invoke directly via `bash tests/e2e/runner/parallel-runner.sh`.

## See also

- `frontend/playwright.config.ts` — root config (3 browsers + mobile + RTL)
- `internal QA note INSTALL_PLAYWRIGHT` — setup runbook
