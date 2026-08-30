/**
 * Shared E2E test helpers and fixtures.
 *
 * Usage:
 *   import { login, logout, TEST_USER } from './helpers';
 */
import { type Page, expect } from '@playwright/test';

// ── Default test credentials ─────────────────────────────────────────────────
// The demo account the seeder actually creates, and the password the CLI banner
// prints when it starts a local demo. The previous defaults here,
// admin@openestimate.local and OpenEstimate2024!, carried the product's name
// from before the rename and no seeder has ever created that account. It did
// not merely fail to authenticate: POST /auth/login answers 422 for it, because
// the .local domain does not survive email validation, so the request never
// reached a password check. Every spec that logs in through this helper
// therefore sat on /login until the redirect assertion timed out, and read as a
// broken spec rather than a broken helper.
//
// Operators whose install generated its own demo password (the platform mints
// one per install unless OE_DEMO_PASSWORD is set) override both values here.
export const TEST_USER = {
  email: process.env.E2E_USER_EMAIL ?? 'demo@openconstructionerp.com',
  password: process.env.E2E_USER_PASSWORD ?? 'DemoPass1234!',
};

// ── Auth helpers ─────────────────────────────────────────────────────────────

/**
 * Log in through the UI login form and wait for the dashboard to load.
 * Injects tokens directly into sessionStorage to avoid repeating the login
 * form for every test — but falls back to the full form flow when needed.
 */
export async function login(page: Page): Promise<void> {
  await page.goto('/login');
  await expect(page.locator('form')).toBeVisible();

  await page.locator('input[type="email"]').fill(TEST_USER.email);
  await page.locator('#login-password').fill(TEST_USER.password);
  await page.locator('button[type="submit"]').click();

  // Wait for redirect away from /login (either dashboard or onboarding)
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
}

/**
 * Log out by clearing auth tokens from storage and reloading to /login.
 */
export async function logout(page: Page): Promise<void> {
  await page.evaluate(() => {
    localStorage.removeItem('oe_access_token');
    localStorage.removeItem('oe_refresh_token');
    localStorage.removeItem('oe_remember');
    sessionStorage.removeItem('oe_access_token');
    sessionStorage.removeItem('oe_refresh_token');
  });
  await page.goto('/login');
  await expect(page).toHaveURL(/\/login/);
}

/**
 * Inject a fake auth token directly into sessionStorage so that the
 * React app considers the user authenticated without a real backend call.
 * Use this only for tests that don't need real API data.
 */
export async function injectFakeAuth(page: Page): Promise<void> {
  await page.goto('/');
  await page.evaluate(() => {
    sessionStorage.setItem('oe_access_token', 'fake-e2e-token');
    sessionStorage.setItem('oe_refresh_token', 'fake-e2e-refresh');
  });
  await page.reload();
}
