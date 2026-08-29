/**
 * Smoke — mobile responsive: sidebar collapses, no horizontal scroll.
 *
 * Tagged @mobile so it runs under the mobile-chromium project. The desktop
 * projects carry the matching `grepInvert` (playwright.config.ts), without
 * which this file also ran at 1280x720 and its name was a lie there.
 */
import { test, expect } from '../fixtures';
import { gotoModule, captureScreen } from '../helpers';

/**
 * The sliding drawer that wraps the sidebar (AppLayout.tsx:134-142). The
 * `.oe-sidebar` class alone is not enough: the inner panel carries it too
 * (Sidebar.tsx:830), so the bare class matches two nested elements. The
 * drawer is the one with the transform on it, hence `.fixed`.
 */
const SIDEBAR_DRAWER = '.oe-sidebar.fixed';

/**
 * The burger, by accessible name `common.open_menu` (en.ts:9930 "Open menu",
 * de.ts:11295, ru.ts:11410) — Header.tsx:363-371. It is rendered `lg:hidden`,
 * so it exists only at this viewport. NB: the old locator led with
 * [data-testid="sidebar-toggle"], which the app shell does not carry — the
 * only element with that testid is in the PDF takeoff viewer
 * (modules/pdf-takeoff/TakeoffViewerModule.tsx:7617), a different screen.
 */
const BURGER_NAME = /^(open menu|menü öffnen|открыть меню)$/i;

test.describe('@smoke @mobile mobile-responsive', () => {
  test('dashboard fits in iPhone SE viewport without horizontal scroll', async ({ authedPage }) => {
    await gotoModule(authedPage, 'dashboard');
    const { docWidth, winWidth, widest } = await authedPage.evaluate(() => {
      const win = window.innerWidth;
      // Name the worst offender so a failure says WHAT overflows, not just
      // by how much.
      let widest = { tag: '(none)', cls: '', right: 0 };
      for (const el of Array.from(document.body.querySelectorAll<HTMLElement>('*'))) {
        const right = el.getBoundingClientRect().right;
        if (right > widest.right) {
          widest = {
            tag: el.tagName.toLowerCase(),
            cls: String(el.className ?? '').slice(0, 120),
            right,
          };
        }
      }
      return { docWidth: document.documentElement.scrollWidth, winWidth: win, widest };
    });
    // Allow 2px tolerance for sub-pixel rounding.
    expect(
      docWidth,
      `page is ${docWidth - winWidth}px wider than the ${winWidth}px viewport; ` +
        `widest element <${widest.tag} class="${widest.cls}"> ends at ${Math.round(widest.right)}px`,
    ).toBeLessThanOrEqual(winWidth + 2);
    await captureScreen(authedPage, 'smoke', 'mobile-dashboard');
  });

  test('sidebar is off-screen by default on mobile and the burger opens it', async ({
    authedPage,
  }) => {
    await gotoModule(authedPage, 'dashboard');

    const drawer = authedPage.locator(SIDEBAR_DRAWER);
    await expect(drawer, 'sidebar drawer is not a single element').toHaveCount(1);

    // "Collapsed" here means translated off the left edge
    // (`-translate-x-full`, AppLayout.tsx:140). It keeps a full-size box, so
    // toBeHidden() would never fire — geometry is the only honest check.
    const closed = await drawer.boundingBox();
    expect(closed, 'sidebar drawer has no box').not.toBeNull();
    expect(
      Math.round(closed!.x + closed!.width),
      'sidebar is on screen before anyone opened it',
    ).toBeLessThanOrEqual(0);

    const burger = authedPage.getByRole('button', { name: BURGER_NAME });
    await expect(burger, 'mobile viewport exposes no sidebar toggle').toBeVisible();

    // ...and it works. Without this the test would still pass if the button
    // were wired to nothing.
    await burger.click();
    await expect
      .poll(async () => Math.round((await drawer.boundingBox())?.x ?? -1), {
        message: 'burger did not slide the sidebar into view',
        timeout: 5_000,
      })
      .toBeGreaterThanOrEqual(0);

    await captureScreen(authedPage, 'smoke', 'mobile-sidebar-open');
  });
});
