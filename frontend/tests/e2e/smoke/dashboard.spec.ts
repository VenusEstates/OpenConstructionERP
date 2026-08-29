/**
 * Smoke — dashboard loads with widgets.
 *
 * The widget test used to claim three selectors and rest on one:
 * `[data-testid^="widget-"]` and `[data-widget]` and
 * `[data-testid="dashboard-customize"]` match nothing anywhere in
 * `frontend/src`, so the whole assertion hung on a "Customize" button found
 * by English or German text — with `.first()` choosing between it and
 * "Customise branding". Every selector below is anchored to a source line.
 */
import { test, expect } from '../fixtures';
import { gotoModule, expectAppShell, captureScreen, collectConsoleErrors, expectNoConsoleErrors } from '../helpers';

test.describe('@smoke dashboard', () => {
  test('dashboard mounts and the app shell is visible', async ({ authedPage }) => {
    const errors = collectConsoleErrors(authedPage);
    await gotoModule(authedPage, 'dashboard');
    await expectAppShell(authedPage);
    await captureScreen(authedPage, 'smoke', 'dashboard-loaded');
    // Allow benign noise (3rd-party SDK warnings, etc.) — block real exceptions only.
    expectNoConsoleErrors(errors, [/sourcemap/i, /favicon/i, /react devtools/i, /\bws:\/\//]);
  });

  test('the dashboard grid renders widgets and Customize lists them', async ({ authedPage }) => {
    await gotoModule(authedPage, 'dashboard');

    // The KPI ribbon is the `kpi` registry widget's own container
    // (DashboardPage.tsx:1030, registered at widgetRegistry.ts:110). It is
    // emitted by the grid loop at DashboardPage.tsx:2834, so seeing it proves
    // a widget mounted rather than just page chrome.
    await expect(
      authedPage.getByTestId('dashboard-tour-kpi-ribbon'),
      'dashboard grid rendered no KPI widget',
    ).toBeVisible({ timeout: 15_000 });

    // The Customize control, by testid rather than by its label
    // (DashboardPage.tsx:2710). The label is translated and the tree holds a
    // second, unrelated "Customise branding" control.
    const customize = authedPage.getByTestId('dashboard-tour-customize-button');
    await expect(customize).toBeVisible();
    await customize.click();

    // Customize mounts <DashboardLayoutManager>, which loops the reconciled
    // registry at DashboardLayoutManager.tsx:254 and stamps each row with
    // data-testid={`dash-widget-row-${id}`} at :98. Assert two ids the grid
    // above also renders, so panel and page cannot drift apart silently.
    await expect(
      authedPage.getByTestId('dash-widget-row-kpi'),
      'Customize lists no row for the KPI widget',
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      authedPage.getByTestId('dash-widget-row-projects'),
      'Customize lists no row for the projects widget',
    ).toBeVisible();

    // The panel is the whole registry, not a stub: DASHBOARD_WIDGETS
    // (widgetRegistry.ts:59) holds 26 entries today, and reconcileOrder
    // (useDashboardLayoutStore.ts:153) reinserts every id a saved layout is
    // missing, so all of them get a row whatever the user has customised.
    const rows = authedPage.locator('[data-testid^="dash-widget-row-"]');
    expect(await rows.count(), 'Customize listed almost nothing').toBeGreaterThanOrEqual(20);

    await captureScreen(authedPage, 'smoke', 'dashboard-widgets');
  });
});
