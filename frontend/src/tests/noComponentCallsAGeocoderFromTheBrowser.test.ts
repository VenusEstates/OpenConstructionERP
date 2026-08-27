// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Usage-policy gate: no component may call a public geocoder from the browser.
//
// WHY THIS EXISTS. ProjectMap and DashboardProjectsMap both fetched
// `https://nominatim.openstreetmap.org/search` straight from the user's
// browser. A browser cannot set a User-Agent, so those requests were
// unidentifiable and unthrottled and they fanned out over every user's IP.
// The Nominatim usage policy forbids exactly that. We already own the correct
// implementation server-side (`backend/app/modules/geo_hub/geocoder.py`):
// Photon first, Nominatim only as fallback, a process-global 1 req/s gate, a
// contact User-Agent, and `OE_GEOCODER_BASE_URL` so an operator can point at
// their own mirror. Both call sites now go through
// `GET /api/v1/geo-hub/geocode/suggest`.
//
// WHAT THIS ASSERTS.
//   1. No source file contains a geocoding API hostname except as an `href`
//      (the licence and provider credits in CesiumViewer are legitimate
//      links, not calls) or inside a comment.
//   2. The two components that geocode do it through one source only: they
//      import `geocodeSuggest` from the geo-hub API client, and they contain
//      no raw `fetch(` of their own.
//
// Rule 2 is the load-bearing one. Rule 1 is a hostname denylist, and a
// denylist cannot see a geocoder nobody has added yet, so it is a backstop
// rather than the guarantee. Asserting a single source of geocoding inside
// the files that geocode holds regardless of which host a future edit reaches
// for. If a third component starts geocoding, add it to GEOCODING_COMPONENTS.
//
// Rule 1 matches on full API hostnames on purpose. A bare `nominatim.org`
// would also match the documentation link in CesiumViewer's licence panel and
// go red on a correct line.
//
// Run: npx vitest run src/tests/noComponentCallsAGeocoderFromTheBrowser.test.ts

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '..');

/** This file names the hosts it bans, so it must not police itself. */
const SELF = resolve(__dirname, 'noComponentCallsAGeocoderFromTheBrowser.test.ts');

/**
 * Public geocoding API hosts. Locale files are excluded from the scan below,
 * so the human-readable provider names that appear in translated licence
 * copy ("OSM Nominatim") are out of scope by construction.
 */
const GEOCODER_HOSTS = [
  'nominatim.openstreetmap.org',
  'photon.komoot.io',
  'api.opencagedata.com',
  'geocode.maps.co',
  'api.mapbox.com/geocoding',
  'maps.googleapis.com/maps/api/geocode',
];

/** Directories with no executable call sites in them. */
const SKIP_DIRS = new Set(['locales', 'node_modules', '__snapshots__']);

/** A mention rather than a call: a credit link, or prose in a comment. */
function isMention(line: string): boolean {
  const trimmed = line.trim();
  return (
    trimmed.startsWith('//') ||
    trimmed.startsWith('*') ||
    trimmed.startsWith('/*') ||
    line.includes('href=')
  );
}

function collectSources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      collectSources(full, out);
      continue;
    }
    if (!/\.tsx?$/.test(entry.name)) continue;
    if (full === SELF) continue;
    out.push(full);
  }
  return out;
}

const FILES = collectSources(SRC);

/** The components that resolve an address to coordinates. */
const GEOCODING_COMPONENTS = [
  'shared/ui/ProjectMap/ProjectMap.tsx',
  'features/dashboard/components/DashboardProjectsMap.tsx',
] as const;

describe('no component calls a geocoder from the browser', () => {
  it('actually scanned the tree', () => {
    // A source scan that globs zero files passes green. Prove otherwise
    // before believing anything the assertions below report.
    expect(FILES.length).toBeGreaterThan(500);
    const rels = FILES.map((f) => relative(SRC, f).split(sep).join('/'));
    for (const comp of GEOCODING_COMPONENTS) {
      expect(rels, `${comp} was not part of the scan`).toContain(comp);
    }
    // And prove the scan can see file bodies, not just names.
    expect(statSync(FILES[0] as string).size).toBeGreaterThan(0);
  });

  it('reaches no geocoding API host outside a credit link or a comment', () => {
    const offenders: string[] = [];
    for (const file of FILES) {
      const text = readFileSync(file, 'utf-8');
      if (!GEOCODER_HOSTS.some((host) => text.includes(host))) continue;
      const rel = relative(SRC, file).split(sep).join('/');
      text.split('\n').forEach((line, i) => {
        if (!GEOCODER_HOSTS.some((host) => line.includes(host))) return;
        if (isMention(line)) return;
        offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(
      offenders,
      'These lines reach a public geocoder from the browser. A browser cannot ' +
        'set a User-Agent, so the request is unidentifiable and unthrottled and ' +
        'it fans out over every user IP. Route it through the backend geocoder ' +
        '(GET /api/v1/geo-hub/geocode/suggest) instead.',
    ).toEqual([]);
  });

  it.each(GEOCODING_COMPONENTS)('%s geocodes through the backend and nowhere else', (rel) => {
    const text = readFileSync(resolve(SRC, rel), 'utf-8');
    expect(text.includes("from '@/features/geo-hub/api'"), `${rel} lost its backend client import`).toBe(
      true,
    );
    expect(text.includes('geocodeSuggest('), `${rel} no longer calls geocodeSuggest`).toBe(true);
    const rawFetches = text
      .split('\n')
      .map((line, i) => ({ line, n: i + 1 }))
      .filter(({ line }) => /(?<![\w.])fetch\s*\(/.test(line) && !isMention(line));
    expect(
      rawFetches.map(({ line, n }) => `${rel}:${n}: ${line.trim()}`),
      `${rel} hand-rolls a fetch. Geocoding must have exactly one source here, ` +
        'the geo-hub API client, so a future edit cannot quietly reach a host directly.',
    ).toEqual([]);
  });
});
