/**
 * Smoke — switching to Arabic flips the document direction to RTL.
 *
 * Why this exists: the `rtl-arabic` project in playwright.config.ts selected
 * exactly one test, `language-switch.spec.ts`, which walks EN to DE to RU and
 * asserts only `<html lang>`. None of those three is right-to-left, so a
 * project named for RTL rendered no RTL language and asserted nothing about
 * direction, while a reader scanning the config concluded RTL was covered.
 * The two specs that do assert direction (`e2e/propdev/07-i18n-rtl.spec.ts`
 * and `e2e/property-dev-i18n.spec.ts`) live under `frontend/e2e/`, which this
 * config's testDir does not reach.
 *
 * Tagged `@rtl` so the `rtl-arabic` project selects it. It deliberately does
 * NOT lean on that project's `baseURL`/`locale`: the three desktop projects
 * only deselect the phone specs, so this runs under them at `locale: en` too,
 * and a spec that needed an Arabic baseURL would fail in four projects out of
 * five. Switching the language inside the test is also the stronger check -
 * a direction bug is a bug in the switch, not in a startup flag.
 */
import { test, expect } from '../fixtures';
import { gotoModule, switchLanguage, captureScreen } from '../helpers';

test.describe('@smoke @rtl direction', () => {
  test('switching to Arabic flips <html dir> to rtl', async ({ authedPage }) => {
    await gotoModule(authedPage, 'dashboard');

    // Read the direction BEFORE the switch. Asserting only the end state
    // would pass just as well on an app that hardcoded dir="rtl" for
    // everyone, which is why the pair is what gets asserted below.
    const dirBefore = await authedPage.locator('html').getAttribute('dir');

    await switchLanguage(authedPage, 'ar');
    await authedPage.waitForTimeout(500); // allow i18next to swap chunks

    const dirAfter = await authedPage.locator('html').getAttribute('dir');
    const langAfter = await authedPage.locator('html').getAttribute('lang');

    expect(dirAfter).toBe('rtl');
    expect(langAfter?.toLowerCase()).toMatch(/^ar/);
    expect(dirAfter).not.toBe(dirBefore);

    await captureScreen(authedPage, 'smoke', 'rtl-arabic-dashboard');
  });
});
