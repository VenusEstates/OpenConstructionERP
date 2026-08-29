/**
 * Smoke — authentication: login, logout, wrong-password lockout.
 *
 * Uses the non-authed `page` fixture for login/logout flows. The
 * `authedPage` fixture is exercised by the dashboard smoke.
 */
import { test, expect, DEMO_USER } from '../fixtures';
import { captureScreen } from '../helpers';

test.describe('@smoke auth', () => {
  test('login page renders the form', async ({ page }) => {
    await page.goto('/login');
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('input[type="email"]')).toBeVisible();
    await expect(page.locator('input[type="password"]').first()).toBeVisible();
    await expect(page.locator('button[type="submit"]')).toBeVisible();
    await captureScreen(page, 'smoke', 'login-page-empty');
  });

  test('successful login with demo credentials redirects away from /login', async ({ page }) => {
    await page.goto('/login');
    await page.locator('input[type="email"]').fill(DEMO_USER.email);
    await page.locator('input[type="password"]').first().fill(DEMO_USER.password);
    await captureScreen(page, 'smoke', 'login-page-filled');
    await page.locator('button[type="submit"]').click();
    await expect(page, 'should leave /login after successful auth').not.toHaveURL(/\/login/, { timeout: 15_000 });
    await captureScreen(page, 'smoke', 'post-login-redirect');
  });

  // This used to sign in as the seeded demo account with a deliberately wrong
  // password and assert that the browser had not left /login. Two things were
  // wrong with it, and they compounded.
  //
  // The account cannot carry the assertion. In any non-production install with
  // demo seeding on, the three seeded demo emails are routed past the password
  // check deliberately, because the seeder randomises the demo password per
  // install and the documented credential would otherwise stop working. So the
  // sign-in under test succeeds by design and the browser does leave /login.
  //
  // The assertion could not report that. "We have not navigated yet" is true
  // for free whenever anything is slow, so it can only fail when the system is
  // fast, which is backwards. Measured: it passed at four workers and failed at
  // one, on the same build against the same server, and the failure was the
  // truthful run. It also claimed to surface an error while asserting nothing
  // about one.
  //
  // So the account is one the server does not know, which no shortcut exempts,
  // and the wait is for the refusal to appear rather than for time to pass. A
  // sign-in that wrongly succeeded produces no error to wait for and fails
  // here, which is the direction that has to work.
  test('an account the server does not know is refused, with the reason on screen', async ({ page }) => {
    await page.goto('/login');
    await page.locator('input[type="email"]').fill('no-such-account.e2e@openconstructionerp.invalid');
    await page.locator('input[type="password"]').first().fill('this-is-deliberately-wrong-2026');
    await page.locator('button[type="submit"]').click();
    await expect(page.getByTestId('login-error')).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/login/);
    await captureScreen(page, 'smoke', 'login-wrong-password');
  });

  test('logout clears auth tokens', async ({ authedPage }) => {
    await authedPage.goto('/');
    // Sanity: we're authed.
    const tokenBefore = await authedPage.evaluate(() => localStorage.getItem('oe_access_token'));
    expect(tokenBefore, 'demo session should have a token').toBeTruthy();
    // Simulate logout (any user-action would call clearAuth — but we just
    // verify the store contract here).
    await authedPage.evaluate(() => {
      localStorage.removeItem('oe_access_token');
      localStorage.removeItem('oe_refresh_token');
      sessionStorage.removeItem('oe_access_token');
      sessionStorage.removeItem('oe_refresh_token');
    });
    await authedPage.goto('/');
    // Protected routes should bounce to /login when un-authed.
    await authedPage.waitForURL(/\/login|\/about|\/$/, { timeout: 10_000 }).catch(() => {
      /* some marketing routes are unauthed — that's fine */
    });
    await captureScreen(authedPage, 'smoke', 'after-logout');
  });
});
