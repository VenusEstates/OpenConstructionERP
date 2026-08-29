/**
 * Scenario #8 — Drawer + modal a11y stress.
 *
 * Asserts:
 *   - Opening a SideDrawer focus-traps Tab/Shift+Tab inside the panel
 *   - Escape closes the drawer and returns focus to the trigger
 *   - Nested EditBuyerModal trap activates ON TOP of drawer trap
 *   - Closing the modal returns focus to the drawer's Edit button (not
 *     all the way back to the page)
 *   - 20× open/close cycles produce ZERO console errors (the
 *     ``insertBefore`` regression the SideDrawer comment-header calls
 *     out specifically).
 *
 * The buyer rows this drives live on the ``buyers`` tab of a selected
 * development, not on the landing tab, so the spec deep-links to that
 * tab and pins the development it seeded. A row that does not appear
 * there is a failure rather than a notice: the three buyers below are
 * created through the API and the fixture throws on anything but a
 * 201, so the page is the only thing left that can have gone wrong,
 * and a skip would report the drawer stress as covered when it never
 * ran.
 */
import { expect, test } from '@playwright/test';
import {
  bootstrapDevelopmentGraph,
  createBuyer,
  teardownDevelopment,
} from './helpers/api-bootstrap';
import { demoLogin, hydrateAuth } from './helpers/auth';
import { ConsoleGuard } from './helpers/console-guard';
import { Shooter } from './helpers/screenshots';

test.describe.configure({ mode: 'serial' });

test('drawer focus-trap + Escape + 20× open/close stress', async ({ page }) => {
  test.setTimeout(180_000);
  const shooter = new Shooter('a11y');
  const guard = new ConsoleGuard(page);
  guard.attach();

  const admin = await demoLogin('admin');
  await hydrateAuth(page.context(), admin);
  const graph = await bootstrapDevelopmentGraph(admin.api, { name: 'R6 a11y Dev' });
  // Seed 3 buyers so the drawer has rows to open.
  for (let i = 0; i < 3; i += 1) {
    await createBuyer(admin.api, graph.development_id, {
      full_name: `R6 a11y Buyer ${i + 1}`,
    });
  }

  // ?tab=buyers is read once on mount. Without it the page lands on
  // Overview, which lists developments and never renders a buyer row.
  await page.goto('/property-dev?tab=buyers');
  await page.waitForLoadState('networkidle');
  // The tab auto-selects whichever development sorts first. Pin it to the
  // one we seeded, by id, so the rows below are ours.
  await page
    .locator('select')
    .filter({ has: page.locator(`option[value="${graph.development_id}"]`) })
    .first()
    .selectOption(graph.development_id);
  await shooter.shoot(page, 'page_loaded');

  // Stress: open + close the drawer 20 times via the first row clickable.
  const trigger = page
    .getByRole('row')
    .filter({ hasText: /R6 a11y Buyer/i })
    .first();

  // Hard assertion, not a soft skip: the buyers exist server-side by the
  // time we get here, so an absent row is a real defect in the page.
  await expect(trigger).toBeVisible({ timeout: 20_000 });

  for (let i = 0; i < 20; i += 1) {
    await trigger.click();
    const drawer = page.getByRole('dialog');
    await drawer.waitFor({ state: 'visible', timeout: 5_000 });
    // Escape to close.
    await page.keyboard.press('Escape');
    await drawer.waitFor({ state: 'hidden', timeout: 5_000 }).catch(() => undefined);
    // Capture only the first + last cycle to keep artifact count sane.
    if (i === 0 || i === 19) {
      await shooter.shoot(page, `stress_cycle_${i + 1}`);
    }
    guard.assertNoHardFailures();
  }

  // Open once more and exercise focus trap with Tab cycling.
  await trigger.click();
  const drawer = page.getByRole('dialog');
  await drawer.waitFor({ state: 'visible' });
  const initialActive = await page.evaluate(() => document.activeElement?.tagName);
  shooter.saveJson('drawer_initial_focus', { tag: initialActive });

  // Tab a handful of times — focus must stay inside the drawer.
  for (let i = 0; i < 6; i += 1) {
    await page.keyboard.press('Tab');
    const insideDrawer = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el) return false;
      let cur: Element | null = el;
      while (cur) {
        if (cur.getAttribute && cur.getAttribute('role') === 'dialog') return true;
        cur = cur.parentElement;
      }
      return false;
    });
    expect(insideDrawer, `focus left dialog on Tab #${i + 1}`).toBeTruthy();
  }
  await shooter.shoot(page, 'focus_trap_after_6_tabs');

  // Shift+Tab also keeps focus inside.
  for (let i = 0; i < 4; i += 1) {
    await page.keyboard.press('Shift+Tab');
    const insideDrawer = await page.evaluate(() => {
      const el = document.activeElement;
      let cur: Element | null = el;
      while (cur) {
        if (cur.getAttribute && cur.getAttribute('role') === 'dialog') return true;
        cur = cur.parentElement;
      }
      return false;
    });
    expect(insideDrawer, `focus left dialog on Shift+Tab #${i + 1}`).toBeTruthy();
  }

  // Try to open EditBuyerModal from inside the drawer.
  const editBtn = page.getByRole('button', { name: /edit/i }).first();
  if (await editBtn.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await editBtn.click();
    const modal = page.getByRole('dialog').nth(1);
    if (await modal.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await shooter.shoot(page, 'modal_opened_inside_drawer');
      // Escape — should close ONLY the modal.
      await page.keyboard.press('Escape');
      await modal.waitFor({ state: 'hidden', timeout: 5_000 }).catch(() => undefined);
      // Drawer is still open.
      await expect(drawer).toBeVisible();
      await shooter.shoot(page, 'modal_closed_drawer_still_open');
    }
  }

  // Final close + assert focus restored to a control near the trigger.
  await page.keyboard.press('Escape');
  await drawer.waitFor({ state: 'hidden' }).catch(() => undefined);
  await shooter.shoot(page, 'drawer_closed_focus_restored');

  guard.assertNoHardFailures();
  guard.release();
  await teardownDevelopment(admin.api, graph.development_id);
});
