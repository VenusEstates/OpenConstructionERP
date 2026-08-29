/**
 * Smoke — settings opens and every tab loads.
 *
 * Both tests previously died on a strict-mode violation: `.or()` resolved to
 * the sr-only <h1> AND [data-testid="settings-tabs"]. `.first()` would not
 * have fixed it, because the tab strip carrying that testid is the MOBILE
 * one (SettingsPage.tsx:1517, `lg:hidden`) and is display:none at the desktop
 * viewport these tests run at.
 */
import { test, expect } from '../fixtures';
import { gotoModule, captureScreen } from '../helpers';

/**
 * The one element that is unique to Settings and present at every viewport:
 * the tab panel at SettingsPage.tsx:1600. The two tab strips are
 * viewport-exclusive (mobile pills `lg:hidden` at :1517, desktop nav
 * `hidden lg:flex` at :1552), and the <h1> is `sr-only`
 * (shared/ui/PageHeader.tsx:60), i.e. a 1x1 clipped box — technically
 * "visible" to Playwright, which is exactly why it is a weak signal.
 */
const SETTINGS_PANEL = '#settings-content[role="tabpanel"]';

/**
 * <ErrorBoundary>'s heading, `error.something_wrong` (en.ts:4546,
 * de.ts:10423, ru.ts:8849), rendered at shared/ui/ErrorBoundary.tsx:89.
 */
const BOUNDARY_TITLE = /something went wrong|etwas ist schiefgegangen|что-то пошло не так/i;

test.describe('@smoke settings', () => {
  test('settings page mounts', async ({ authedPage }) => {
    await gotoModule(authedPage, 'settings');

    const panel = authedPage.locator(SETTINGS_PANEL);
    // Count first: if the page ever grows a second tab panel this fails
    // loudly instead of quietly picking one.
    await expect(panel, 'settings tab panel is not unique on the page').toHaveCount(1);
    await expect(panel).toBeVisible({ timeout: 10_000 });
    // ...and it is Settings' panel, not some other module's: it is labelled
    // by one of the settings tab buttons (SettingsPage.tsx:1602). Locale
    // independent, unlike matching the word "Settings".
    await expect(panel).toHaveAttribute('aria-labelledby', /^settings-tab-/);

    await captureScreen(authedPage, 'smoke', 'settings-loaded');
  });

  test('every settings tab opens and keeps the panel mounted', async ({ authedPage }) => {
    // 13 tabs, each clicked and screenshotted; the default 60s budget is not
    // enough for a full-page capture per tab.
    test.slow();
    await gotoModule(authedPage, 'settings');

    // The DESKTOP tab strip only (SettingsPage.tsx:1565). The mobile pills at
    // :1530 carry the same ids without the `-desktop` suffix, so the old
    // `[role="tab"], [data-testid^="settings-tab-"]` matched both strips and
    // spent a swallowed 5s action timeout on every display:none pill.
    const tabs = authedPage.locator('[role="tab"][data-testid$="-desktop"]');
    const count = await tabs.count();
    // TABS at SettingsPage.tsx:1268 holds 13 entries; `audit` and `einvoice`
    // are role-gated at :1415, so the floor for any role is 11. The old test
    // called `test.skip()` when it found none, which is how a locator that
    // matches nothing reads as a green run forever.
    expect(count, 'settings rendered no desktop tabs').toBeGreaterThanOrEqual(11);

    const panel = authedPage.locator(SETTINGS_PANEL);
    for (let i = 0; i < count; i += 1) {
      const tab = tabs.nth(i);
      // The testid doubles as the button's DOM id, which is what the panel
      // points at with aria-labelledby (SettingsPage.tsx:1564 and :1602).
      const id = (await tab.getAttribute('data-testid')) ?? `tab-${i}`;
      const name = id.replace(/^settings-tab-|-desktop$/g, '');
      await tab.click();
      // The click landed and the strip agrees which tab is open.
      await expect(tab, `${id}: click did not select the tab`).toHaveAttribute(
        'aria-selected',
        'true',
      );
      // The panel is still mounted and now belongs to the tab just clicked.
      // If the section crashed, the route boundary replaces the whole page
      // and this element is gone.
      await expect(panel, `${id}: tab panel disappeared`).toBeVisible();
      await expect(panel, `${id}: panel is not labelled by the open tab`).toHaveAttribute(
        'aria-labelledby',
        id,
      );
      await expect(
        authedPage.getByRole('heading', { name: BOUNDARY_TITLE }),
        `${id}: triggered the error boundary`,
      ).toHaveCount(0);
      await captureScreen(authedPage, 'smoke', `settings-tab-${name}`);
    }
  });
});
