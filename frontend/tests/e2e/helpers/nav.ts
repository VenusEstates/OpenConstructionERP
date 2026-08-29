/**
 * nav.ts — central navigation helpers.
 *
 * Use these instead of literal `page.goto('/boq')` so route renames or
 * sidebar restructures only need a single update.
 *
 * Every helper prefers `data-testid` selectors when the component
 * exposes one, and falls back to role/text only when no testid exists.
 */
import { type Page, expect } from '@playwright/test';

/** Canonical module slugs → primary route paths. */
export const MODULE_ROUTES = {
  dashboard: '/',
  projects: '/projects',
  boq: '/boq',
  takeoff: '/takeoff',
  costs: '/costs',
  bim: '/bim-hub',
  validation: '/validation',
  tendering: '/tendering',
  reporting: '/reporting',
  settings: '/settings',
  accommodation: '/accommodation',
  geoHub: '/geo-hub',
  contacts: '/contacts',
  schedule: '/schedule',
  propDev: '/property-development',
} as const;

export type ModuleKey = keyof typeof MODULE_ROUTES;

/** Navigate to a module by canonical key and wait for the body to settle. */
export async function gotoModule(page: Page, key: ModuleKey): Promise<void> {
  const path = MODULE_ROUTES[key];
  await page.goto(path);
  await page.waitForLoadState('domcontentloaded');
  // Brief settle: the React app may stream chunks after DOMContentLoaded.
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => {
    /* a busy app may never reach networkidle; that's OK */
  });
}

/** Open the sidebar (mobile) — no-op on desktop where it's always visible. */
export async function openSidebar(page: Page): Promise<void> {
  const burger = page.locator('[data-testid="sidebar-toggle"], button[aria-label*="menu" i]').first();
  if (await burger.isVisible({ timeout: 1_000 }).catch(() => false)) {
    await burger.click();
  }
}

/** Close the sidebar (mobile) if it's currently open. */
export async function closeSidebar(page: Page): Promise<void> {
  const close = page.locator('[data-testid="sidebar-close"]').first();
  if (await close.isVisible({ timeout: 500 }).catch(() => false)) {
    await close.click();
  }
}

/** Open the global command palette (Cmd/Ctrl+K). */
export async function openCommandPalette(page: Page): Promise<void> {
  const isMac = process.platform === 'darwin';
  await page.keyboard.press(isMac ? 'Meta+K' : 'Control+K');
}

/** Open the keyboard-shortcuts help (?). */
export async function openShortcutsHelp(page: Page): Promise<void> {
  await page.keyboard.press('Shift+Slash');
}

/**
 * Confirm the navbar/header is mounted and the app shell rendered — a
 * cheap sanity check after any goto so we know we're not on a white
 * screen or an error boundary.
 */
export async function expectAppShell(page: Page): Promise<void> {
  // Either the explicit testid OR the role-based fallback should be present.
  const shell = page.locator(
    '[data-testid="app-shell"], [data-testid="app-header"], header, [role="banner"]',
  ).first();
  await expect(shell).toBeVisible({ timeout: 15_000 });
}

/**
 * Display name each dropdown entry renders (Header.tsx:1102-1116,
 * `SUPPORTED_LANGUAGES` in app/i18n.ts) — this is the entry's accessible
 * name, since the entry has no `data-testid` and is not `role="option"`.
 */
const LANGUAGE_NAMES: Record<
  'en' | 'de' | 'ru' | 'ar' | 'es' | 'fr' | 'pt' | 'it' | 'pl' | 'ja' | 'ko' | 'zh',
  string
> = {
  en: 'English',
  de: 'Deutsch',
  ru: 'Русский',
  ar: 'العربية',
  es: 'Español',
  fr: 'Français',
  pt: 'Português',
  it: 'Italiano',
  pl: 'Polski',
  ja: '日本語',
  ko: '한국어',
  zh: '简体中文',
};

/**
 * Switch UI language via the language switcher dropdown.
 *
 * The trigger button carries `aria-label="Language: <name>"`
 * (Header.tsx:1086), and each entry in the opened menu is a
 * `role="menuitem"` button whose accessible name is the language's own
 * display name (Header.tsx:1099-1116) — neither element carries a
 * `data-testid`, and the entries are NOT `role="option"`.
 *
 * NB: the old locator looked for `[data-testid="lang-${lang}"]` /
 * `[role="option"][data-value="${lang}"]`, which the app renders neither
 * of, so it always fell through to a "last resort" that navigated to
 * `?locale=${lang}`. The app only ever reads `?lang=`
 * (`resolveInitialLanguage`, app/i18n.ts:300) — never `?locale=` — so that
 * fallback silently did nothing and every caller of this helper was
 * testing English regardless of the language it asked for.
 */
export async function switchLanguage(page: Page, lang: 'en' | 'de' | 'ru' | 'ar' | 'es' | 'fr' | 'pt' | 'it' | 'pl' | 'ja' | 'ko' | 'zh'): Promise<void> {
  const trigger = page.locator('button[aria-label*="language" i]').first();
  await trigger.click();
  await page.getByRole('menuitem', { name: LANGUAGE_NAMES[lang], exact: true }).click();
}
