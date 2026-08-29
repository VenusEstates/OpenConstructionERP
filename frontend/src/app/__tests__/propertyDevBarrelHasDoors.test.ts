// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// A screen that nothing can open.
//
// `features/property-dev/CompliancePage.tsx` shipped finished: a dashboard,
// four regulator reports, every string through i18n, 29 keys in en.ts, three
// backend endpoints answering 401 rather than 404, and an entry in the feature
// barrel. It had no route and no consumer, so no URL reached it and no click
// reached it. Nothing was red. TypeScript is happy with an export nobody
// imports, eslint is happy, the build is happy, and the e2e spec that wanted
// the page skipped itself with a comment explaining why, which reads as a
// decision rather than a defect.
//
// The instance was one missing `<Route>`. The class is a barrel export with no
// door, so this gate is written over the whole barrel rather than over
// compliance: a test naming one page would pass just as happily the next time
// somebody forgets a different one.
//
// Three ways a component can be reachable, and all three count:
//   - routed:   App.tsx lazy-loads it and mounts it inside a <Route>.
//   - embedded: another screen renders it (a tab, a panel). The host is named
//               here and the claim is checked, not taken on trust.
//   - neither:  a defect. The set of these must equal KNOWN_ORPHANS exactly,
//               so a new one fails and a fixed one also fails until its entry
//               goes, which stops the list turning into an allowlist.
//
// App.tsx is read as text: importing it drags the whole application graph into
// the worker. Comments are stripped before anything is matched, because the
// file already carries "ValidationRulesSettingsPage" inside a comment at the
// lazy-import block, and a bare name scan reads that as wiring. That also
// stops the red proof for this gate from lying: commenting a route out leaves
// its text in the file.
//
// Run:  npx vitest run src/app/__tests__/propertyDevBarrelHasDoors.test.ts
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

/** Drop `/* *\/` blocks and `//` tails so a name in prose is not read as code. */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

/**
 * Components rendered by another screen instead of by a route, and where.
 *
 * Every entry is a hole in this instrument, so the bar is: something genuinely
 * renders the component and you can name the file. The host is a claim the
 * test verifies rather than takes on trust, and an entry naming a file that
 * stopped rendering it fails here instead of quietly excusing the export.
 *
 * `ValidationRulesSettingsPage` belongs here and not in the routed branch.
 * App.tsx:1367 does carry `/property-dev/settings/validation-rules`, but that
 * route mounts `<Navigate to="/governance?tab=validation" replace />` and never
 * names the component; the thing that renders it is GovernancePage.tsx:194. A
 * redirect route is not a mount, and counting one as a mount would let a page
 * be certified by a route that cannot show it.
 */
const EMBEDDED: Record<string, { host: string; why: string }> = {
  ValidationRulesSettingsPage: {
    host: 'features/governance/GovernancePage.tsx',
    why: 'rendered as the validation tab; the old route redirects to that screen',
  },
};

/**
 * Exports that reach no user at all today.
 *
 * Not an allowlist and not a justification. Each line is a screen or panel
 * that ships, compiles and is exported, and that nothing opens or renders —
 * the same defect this file was written for, recorded so the gate can be green
 * about what is already known while still failing on anything new. Give one a
 * door and this test goes red until its line is deleted.
 */
const KNOWN_ORPHANS: Record<string, string> = {
  TaxQuotePanel:
    'jurisdiction-aware tax breakdown. Exported and unit-tested, rendered by no screen: ' +
    'the only import outside the barrel is its own test. Where it belongs (the quote ' +
    'screen? the unit detail drawer?) is a product call, so it is recorded rather than ' +
    'guessed at.',
};

/** `export { A, B } from './X';` → the exported names. */
function readBarrelExports(rel: string): string[] {
  const names: string[] = [];
  for (const m of stripComments(read(rel)).matchAll(/export\s*\{([^}]+)\}\s*from\s*'[^']+';/g)) {
    for (const raw of m[1]!.split(',')) {
      // `X as Y` is re-exported under Y, which is the name App.tsx would use.
      const name = raw.trim().split(/\s+as\s+/).pop()?.trim();
      if (name) names.push(name);
    }
  }
  return names;
}

const APP = stripComments(read('app/App.tsx'));

/**
 * The local name App.tsx gives each lazily imported property-dev export.
 *
 * Anchored on the exact module specifier: `@/features/property-dev/dashboards`
 * is a different barrel, and `CompliancePage` exists in `features/compliance-docs`
 * too, so matching a bare `m.CompliancePage` could bind the wrong feature.
 */
const lazyLocalFor = new Map<string, string>();
for (const m of APP.matchAll(
  /const\s+(\w+)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\('@\/features\/property-dev'\)\s*\.then\(\s*\(m\)\s*=>\s*\(\{\s*default:\s*m\.(\w+)/g,
)) {
  lazyLocalFor.set(m[2]!, m[1]!);
}

/** Every `<Route path=... element={...} />`, as path plus the element it mounts. */
const routeElements: Array<{ path: string; element: string }> = [];
for (const m of APP.matchAll(/path="([^"]+)"\s+element=\{([\s\S]{0,400}?)\}\s*\/>/g)) {
  routeElements.push({ path: m[1]!, element: m[2]! });
}

/**
 * The route that mounts a local component name, if any.
 *
 * The trailing character class matters: anchoring on `<Name />` alone would
 * report the next page mounted with a prop as unrouted, while the class still
 * refuses `<NameSomethingElse`.
 */
function routeMounting(local: string): string | null {
  const rendered = new RegExp(`<${local}[\\s/>]`);
  return routeElements.find((r) => rendered.test(r.element))?.path ?? null;
}

const barrelExports = readBarrelExports('features/property-dev/index.ts');

describe('the property-dev barrel and the doors into it', () => {
  // Every verdict below reports an empty list as success, so a parser that
  // stopped matching would certify the whole barrel while measuring nothing.
  // State the populations first. Measured 2026-08-29: 9 exports, 7 lazy
  // bindings and 293 routed elements. The floors sit well under those.
  it('read a real barrel, a real lazy-import list and a real route list', () => {
    expect(barrelExports.length, 'no exports parsed out of the property-dev barrel').toBeGreaterThan(5);
    expect(barrelExports, 'a known export missing from the parse').toContain('PropertyDevPage');
    expect(lazyLocalFor.size, 'no property-dev lazy imports parsed out of App.tsx').toBeGreaterThan(4);
    expect(routeElements.length, 'no routed elements parsed out of App.tsx').toBeGreaterThan(200);
    expect(
      lazyLocalFor.get('InventoryMapPage'),
      'a known lazy binding parsed wrong',
    ).toBe('PropertyDevInventoryMapPage');
  });

  it('strips comments, so prose naming a component is not read as wiring', () => {
    // App.tsx says "ValidationRulesSettingsPage now mounts inside GovernancePage"
    // in a comment. Without the strip that sentence answers for the component.
    expect(read('app/App.tsx')).toContain('// (ValidationRulesSettingsPage now mounts');
    expect(APP).not.toContain('ValidationRulesSettingsPage');
  });

  it('gives every exported component a route or a screen that renders it', () => {
    const orphans: string[] = [];

    for (const name of barrelExports) {
      if (EMBEDDED[name]) continue; // checked, on its own terms, below
      const local = lazyLocalFor.get(name);
      if (!local) {
        orphans.push(`${name}: no lazy import in App.tsx and no host screen listed`);
        continue;
      }
      const path = routeMounting(local);
      if (!path) {
        orphans.push(
          `${name}: App.tsx lazy-loads it as ${local} but mounts it in no <Route>, ` +
            `so the chunk is declared and the screen has no URL`,
        );
      }
    }

    const known = Object.keys(KNOWN_ORPHANS).sort();
    const found = orphans.map((line) => line.split(':')[0]!).sort();
    expect(
      found,
      `\n  - ${orphans.join('\n  - ')}\n` +
        `Route it in App.tsx, or render it from a screen and name that screen in EMBEDDED.\n`,
    ).toEqual(known);
  });

  it('checks each embedded component against the screen that claims it', () => {
    const broken: string[] = [];
    for (const [name, { host, why }] of Object.entries(EMBEDDED)) {
      if (!barrelExports.includes(name)) {
        broken.push(`${name}: listed as embedded but the barrel no longer exports it`);
        continue;
      }
      if (!existsSync(join(SRC, host))) {
        broken.push(`${name}: listed as embedded in ${host}, which does not exist`);
        continue;
      }
      if (!new RegExp(`<${name}[\\s/>]`).test(stripComments(read(host)))) {
        broken.push(`${name}: ${host} is named as the screen that renders it (${why}) but does not`);
      }
    }
    expect(broken, `\n  - ${broken.join('\n  - ')}\n`).toEqual([]);
  });

  it('keeps the orphan list honest', () => {
    const gone = Object.keys(KNOWN_ORPHANS).filter((n) => !barrelExports.includes(n)).sort();
    expect(gone, 'recorded as an orphan but the barrel no longer exports it').toEqual([]);
    const both = Object.keys(KNOWN_ORPHANS).filter((n) => n in EMBEDDED).sort();
    expect(both, 'recorded as an orphan and as embedded at the same time').toEqual([]);
  });

  it('routes the compliance dashboard under a development, where its data lives', () => {
    // The instance that prompted the gate, kept as its own line because the
    // path carries a decision: all three backend endpoints require dev_id and
    // the panel takes devId, so the screen is dev-scoped like pricing and the
    // inventory map, not top level.
    const local = lazyLocalFor.get('CompliancePageRoute');
    expect(local, 'CompliancePageRoute has no lazy import in App.tsx').toBeTruthy();
    expect(routeMounting(local!)).toBe('/property-dev/developments/:devId/compliance');
  });
});
