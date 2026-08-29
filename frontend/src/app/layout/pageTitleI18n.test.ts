// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// The page heading and the browser tab both come from the English literal a
// route passes as `<P title="...">`, translated through TITLE_I18N_MAP. Two
// things go wrong there and neither is visible from the type system.
//
// A route with no entry in the map keeps its English title in every language,
// because the lookup falls back to `defaultValue`. Half the routes were in
// that state, including the opening screen of most modules, so a German
// session read "Field Time" over a page whose every other word was German.
//
// An entry pointing at a key no locale answers looks fixed and behaves the
// same way, since the same `defaultValue` catches it. Two entries were in
// that state for as long as the map has existed.
//
// Run:  npx vitest run src/app/layout/pageTitleI18n.test.ts

import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import de from '../locales/de';
import en from '../locales/en';
import { sliceBetween } from '@/test/sourceSlice';

/**
 * `TITLE_I18N_MAP` is parsed out of `Header.tsx`, not imported from it.
 *
 * Importing `./Header` pulls the whole app graph, stores, router, react-query
 * and the icon set, into a worker that only ever needed a lookup table, and the
 * worker never finishes starting. This file did import it, from the day it was
 * written, and the runner's answer was `Test Files no tests` beside exit 1. The
 * assertions below had never run. A file that cannot start its worker also
 * takes its batch mates down with it, so running this one alongside
 * `Header.titleKeys.test.ts` silenced that file's four passing tests as well.
 *
 * `Header.titleKeys.test.ts` reaches the same table the same way, and says so at
 * its own head. Parsing keeps both tests leaves. The locale bundles below are
 * imported for real: measured, they cost about fourteen seconds and start fine,
 * and seven other test files import them the same way.
 *
 * The parse is contained rather than trusted, because a floor cannot catch the
 * direction that matters here. A slice that over-runs its end anchor yields a
 * BIGGER map, and a bigger map clears every floor you can put under it. Floors
 * only ever catch the emptying direction, so the containment guard belongs at
 * the slice and the floor belongs under the population. `sliceBetween` refuses a
 * missing anchor, a crossed pair, and a block holding a second copy of the
 * declaration it names.
 */
const find = (rel: string): string => {
  const candidates = [resolve(process.cwd(), rel), resolve(process.cwd(), 'frontend', rel)];
  const hit = candidates.find(existsSync);
  if (!hit) throw new Error(`cannot find ${rel}, looked in ${candidates.join(' and ')}`);
  return hit;
};

const APP_SOURCE = readFileSync(find('src/app/App.tsx'), 'utf8');
const HEADER_SOURCE = readFileSync(find('src/app/layout/Header.tsx'), 'utf8');

const TITLE_I18N_MAP: Record<string, string> = {};
for (const entry of sliceBetween(
  HEADER_SOURCE,
  'export const TITLE_I18N_MAP',
  'export function resolvePageTitleKey',
  { minSourceLength: 20000, label: 'src/app/layout/Header.tsx' },
).matchAll(/^\s*'((?:[^'\\]|\\.)*)':\s*'([^']+)',/gm)) {
  TITLE_I18N_MAP[entry[1]!] = entry[2]!;
}

const routeTitles = new Set<string>();
for (const match of APP_SOURCE.matchAll(/<P\s+title=(["'])(.*?)\1/gs)) {
  if (match[2]) routeTitles.add(match[2]);
}

/**
 * Routes whose title has no key anywhere yet.
 *
 * Every one of them is an operator-of-the-operator surface: developer tooling,
 * a chat trace viewer, an admin register. None appears in a workflow a site
 * uses, so they are named in English on purpose until somebody translates
 * them, rather than pointing at a neighbouring key that means something else.
 * A new route belongs in the map, not in this list.
 */
const ENGLISH_ON_PURPOSE = new Set([
  'CPM',
  'Chat Observability',
  'Compare Revisions',
  'EAC Block Primitives',
  'Geo Hub Admin',
  'Module Developer Guide',
  'Property Development Dashboard',
  'Search across projects',
  'Styles Lab',
  'Webhook Targets',
]);

describe('page titles and the locale bundle', () => {
  it('read a real route list and a real map', () => {
    // Both populations are collected by a regex over somebody else's file, and
    // every assertion below reports the empty set as success. A census that
    // silently stops matching therefore passes all three while measuring
    // nothing, so the floors are stated before the assertions that rest on them.
    // App.tsx mounts 192 titles and Header.tsx declares 186 entries today.
    expect(routeTitles.size, 'no <P title="..."> routes parsed out of App.tsx').toBeGreaterThan(150);
    expect(
      Object.keys(TITLE_I18N_MAP).length,
      'TITLE_I18N_MAP parsed as empty or nearly so',
    ).toBeGreaterThan(150);
    // A known pair, so that a regex matching the wrong shape is caught as well
    // as one matching nothing.
    expect(TITLE_I18N_MAP['Bill of Quantities'], 'a known map entry parsed wrong').toBe('boq.title');
  });

  it('translates every route title the app mounts', () => {
    const untranslated = [...routeTitles]
      .filter((title) => !(title in TITLE_I18N_MAP))
      .filter((title) => !ENGLISH_ON_PURPOSE.has(title))
      .sort();
    expect(untranslated, 'route titles with no i18n key: heading and browser tab stay English').toEqual([]);
  });

  it('points every entry at a key the locales answer', () => {
    // English is where a key is born; German is the language the map exists
    // for, and a key present in en and missing in de renders English anyway.
    const bundles: Array<[string, Record<string, string>]> = [
      ['en', en.translation],
      ['de', de.translation],
    ];
    const dangling: string[] = [];
    for (const [title, key] of Object.entries(TITLE_I18N_MAP)) {
      for (const [locale, bundle] of bundles) {
        if (!(key in bundle)) dangling.push(`${locale}: ${title} -> ${key}`);
      }
    }
    expect(dangling.sort(), 'map entries pointing at a key no locale carries').toEqual([]);
  });

  it('keeps the exception list honest', () => {
    // A title translated later must leave this list, or the list starts
    // certifying work that is already done.
    const stale = [...ENGLISH_ON_PURPOSE].filter((title) => title in TITLE_I18N_MAP).sort();
    expect(stale, 'listed as untranslatable but the map translates it').toEqual([]);
    const gone = [...ENGLISH_ON_PURPOSE].filter((title) => !routeTitles.has(title)).sort();
    expect(gone, 'listed but no route carries this title any more').toEqual([]);
  });
});
