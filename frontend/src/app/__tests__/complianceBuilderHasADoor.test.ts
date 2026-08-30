// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// A screen with a route and no way to click it.
//
// The Compliance Rule Builder shipped whole: a 14 KB panel, a DSL preview, a
// pattern-hints pane, three backend endpoints behind `compliance.rule.*`
// permissions, a how-it-works card, a title in `TITLE_I18N_MAP` and a label
// key in every shipped locale. `App.tsx` mounts it at `/compliance/builder`.
// The menu never offered it. Measured 2026-08-30: `navCatalog.ts` held zero
// rows under `/compliance`, and a grep for the path across `src` returned the
// route, a `storageKey` and the help card - no link anywhere.
//
// So the previous gate could not see it. `navCatalog.test.ts` asks the one
// direction that has an obvious victim: "does every menu row have a route?" A
// row pointing at nothing is a visible dead click. The reverse - a route no
// row offers - has no victim to complain, because the people who would use the
// screen never learn it exists. Nothing was red: the route mounts, the panel
// renders, the tests pass, the build passes.
//
// The half-fix is what makes this worth a file. The how-it-works card knew:
// it carried `spotlightRoute: '/validation'` with a comment saying the
// sub-route has no sidebar link, so the tour highlighted a NEIGHBOURING row.
// That is a workaround recorded as a decision, and it reads as one. The check
// below therefore also asserts the card spotlights the screen its own button
// opens, which is the assertion that goes red the moment somebody reaches for
// that workaround again.
//
// Every assertion is paired with a control, because "not found" is how both a
// real defect and a broken parser look. The populations are floored first, and
// `/validation` - the row this one sits beside - is put through the same two
// parsers, so a regex that has stopped matching fails loudly instead of
// reporting the menu as empty and the screen as missing.
//
// Comments are stripped from both sources before anything is matched. This
// file's own subject appears inside a comment in `navCatalog.ts` explaining
// why the row is there, and in `Header.tsx` beside the route-component map, so
// a bare text scan would read prose as wiring and stay green through a
// deletion.
//
// Run:  npx vitest run src/app/__tests__/complianceBuilderHasADoor.test.ts
//
// The suite-wide jsdom environment is kept although nothing here renders:
// `src/test/setup.ts` installs a localStorage mock on `window`, so a file that
// opts into the node environment fails to load before its first assertion.

import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Resolve `frontend/src` whether vitest was started at `frontend/` or the repo root. */
function findSrcRoot(): string {
  const root = [resolve(process.cwd(), 'src'), resolve(process.cwd(), 'frontend/src')].find((p) =>
    existsSync(join(p, 'app/App.tsx')),
  );
  expect(root, 'could not locate frontend/src from the test working directory').toBeTruthy();
  return root!;
}

const SRC = findSrcRoot();
const read = (rel: string): string => readFileSync(join(SRC, rel), 'utf8');

/** Drop `/* *\/` blocks and `//` tails so a path in prose is not read as wiring. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

const BUILDER_ROUTE = '/compliance/builder';
const BUILDER_LABEL_KEY = 'nav.compliance_rule_builder';
/** The row the builder sits beside. Used as the positive control for both parsers. */
const CONTROL_ROUTE = '/validation';

/** Every `path` App.tsx mounts a `<Route>` on. */
const mountedRoutes: ReadonlySet<string> = new Set(
  [...stripComments(read('app/App.tsx')).matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1]!),
);

/** Every `to` the screen catalogue offers, i.e. every row the sidebar renders. */
const offeredRoutes: ReadonlySet<string> = new Set(
  [...stripComments(read('app/layout/navCatalog.ts')).matchAll(/\bto:\s*'([^']+)'/g)].map(
    (m) => m[1]!.split('?')[0]!,
  ),
);

describe('the menu and the routes the app mounts', () => {
  it('read a real route list and a real menu', () => {
    // Both sets are collected by a regex over somebody else's file, and every
    // assertion below reports "absent" as failure - which is also what an
    // empty set reports. State the floors before resting anything on them.
    expect(mountedRoutes.size, 'no <Route path="..."> parsed out of App.tsx').toBeGreaterThan(150);
    expect(offeredRoutes.size, 'no rows parsed out of navCatalog.ts').toBeGreaterThan(100);
    // A known pair, so a regex that matches the wrong shape is caught too.
    expect(mountedRoutes.has(CONTROL_ROUTE), `${CONTROL_ROUTE} is mounted; the route parser missed it`).toBe(true);
    expect(offeredRoutes.has(CONTROL_ROUTE), `${CONTROL_ROUTE} is in the menu; the menu parser missed it`).toBe(true);
  });

  it('offers the Compliance Rule Builder, which was routed but unreachable', () => {
    expect(mountedRoutes.has(BUILDER_ROUTE), `${BUILDER_ROUTE} lost its <Route> in App.tsx`).toBe(true);
    expect(
      offeredRoutes.has(BUILDER_ROUTE),
      `${BUILDER_ROUTE} mounts a finished screen that no menu row opens, so only a typed URL reaches it`,
    ).toBe(true);
  });
});

describe('the label on that row', () => {
  // `defaultValue` / `defaultLabel` rescue a missing key at render time, so a
  // rendering test cannot tell a translated row from an untranslated one - a
  // sidebar label once hid an absent key from every gate including English.
  // Read the locale bundles as text instead: the key is either a literal there
  // or it is not.
  it.each(['en', 'de', 'ru'])('is a real key in %s, not a default', (locale) => {
    const bundle = read(`app/locales/${locale}.ts`);
    expect(bundle.length, `${locale}.ts read as empty`).toBeGreaterThan(10_000);
    expect(
      bundle.includes(`"${BUILDER_LABEL_KEY}"`),
      `${BUILDER_LABEL_KEY} is missing from ${locale}.ts, so the row renders through a fallback`,
    ).toBe(true);
  });
});

describe('the how-it-works card for the builder', () => {
  const card = read('features/help/catalog/modules/compliance.ts');

  it('spotlights the screen its own button opens', () => {
    // The card names the route the "Open module" button navigates to, and
    // optionally a `spotlightRoute` the tour highlights in the sidebar instead.
    // While the builder had no row of its own the spotlight was aimed at
    // `/validation` - the tour pointed at a neighbour and called it the module.
    const code = stripComments(card);
    expect(code.includes(`route: '${BUILDER_ROUTE}'`), 'the card no longer opens the builder').toBe(true);
    const spotlight = code.match(/spotlightRoute:\s*'([^']+)'/)?.[1];
    expect(
      spotlight ?? BUILDER_ROUTE,
      'the card diverts its spotlight to another screen, which is the workaround for a missing menu row',
    ).toBe(BUILDER_ROUTE);
  });
});
