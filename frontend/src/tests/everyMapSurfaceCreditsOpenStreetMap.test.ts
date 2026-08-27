// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// ODbL attribution gate for every surface that paints OpenStreetMap tiles.
//
// WHY THIS EXISTS. The dashboard map shipped with `attributionControl={false}`
// and nothing mounted in its place, so it drew OSM-derived tiles with zero
// credit on screen. OSM data is ODbL; the tiles rendered from it are a Produced
// Work, and a Produced Work owes attribution. (The share-alike obligation is on
// the database, not on the rendered image, so this gate asserts credit and
// nothing more.) The other three surfaces were already correct, which is the
// point: the defect was invisible precisely because it was one surface out of
// four, and no gate compared them.
//
// WHAT THIS ASSERTS, AND WHY IN THIS FORM.
//   1. Every named surface carries the OSM copyright URL. That literal is the
//      one invariant that holds across all four, including CesiumViewer, whose
//      credit is a hand-rolled HTML overlay rather than a react-map-gl control.
//   2. A surface that turns the built-in control OFF must mount one back. This
//      is the exact shape of the original defect.
//   3. Where a control is mounted, the credit must live on its
//      `customAttribution` prop, not merely somewhere in the file. Without this
//      an attribution moved into a dead comment would still pass rule 1.
//
// Rule 2 deliberately matches `<Attribution` with an optional suffix rather
// than the literal `<AttributionControl`. DashboardProjectsMap resolves the
// control off its dynamically-imported module (`mapLib?.AttributionControl`)
// and renders it as `<Attribution>`, so a gate written against the import name
// would be permanently red on correct code.
//
// The surfaces are enumerated by path, not globbed. A glob that matches
// nothing passes green, which is the same family of silent pass as
// `vitest run <path-that-does-not-exist>`; `readFileSync` on a named path
// throws instead. The first test below is the explicit guard against a
// vacuous pass. The list is a floor, not a census: a legitimate fifth map
// surface should be added here, but its absence must not be asserted, or
// this gate turns into a ratchet that breaks on new work.
//
// Run: npx vitest run src/tests/everyMapSurfaceCreditsOpenStreetMap.test.ts

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '..');

/** Every component that renders OpenStreetMap-derived tiles. */
const MAP_SURFACES = [
  'shared/ui/ProjectMap/ProjectMap.tsx',
  'features/dashboard/components/DashboardProjectsMap.tsx',
  'features/geo-hub/MapLibreViewer.tsx',
  'features/geo-hub/CesiumViewer.tsx',
] as const;

/** The credit link itself. Present in all four, in three different shapes. */
const OSM_CREDIT = 'openstreetmap.org/copyright';

/** Turning the library's own control off. */
const DISABLES_BUILTIN = 'attributionControl={false}';

/** Mounting a control back: `<AttributionControl ...` or `<Attribution ...`. */
const MOUNTS_CONTROL = /<Attribution\w*[\s/>]/;

const sources = MAP_SURFACES.map((rel) => ({
  rel,
  text: readFileSync(resolve(SRC, rel), 'utf-8'),
}));

describe('every map surface credits OpenStreetMap', () => {
  it('actually read the files it claims to check', () => {
    // Without this, a rename that emptied the list would leave every
    // `it.each` below with nothing to iterate and the suite green.
    expect(sources).toHaveLength(MAP_SURFACES.length);
    expect(sources.length).toBeGreaterThanOrEqual(4);
    for (const { rel, text } of sources) {
      expect(text.length, `${rel} is empty`).toBeGreaterThan(1000);
    }
    const names = sources.map((s) => s.rel);
    expect(names).toContain('shared/ui/ProjectMap/ProjectMap.tsx');
    expect(names).toContain('features/dashboard/components/DashboardProjectsMap.tsx');
  });

  it.each(sources)('$rel shows the OpenStreetMap credit', ({ rel, text }) => {
    expect(
      text.includes(OSM_CREDIT),
      `${rel} renders OSM-derived tiles but carries no ${OSM_CREDIT} link. ` +
        'OSM tiles are a Produced Work under ODbL and owe attribution.',
    ).toBe(true);
  });

  it.each(sources)('$rel replaces any control it switches off', ({ rel, text }) => {
    if (!text.includes(DISABLES_BUILTIN)) return;
    expect(
      MOUNTS_CONTROL.test(text),
      `${rel} sets ${DISABLES_BUILTIN} and never mounts an attribution ` +
        'control in its place, so the map draws with no credit at all.',
    ).toBe(true);
  });

  it.each(sources)('$rel puts the credit on the control, not in a comment', ({ rel, text }) => {
    const attributionProps = text
      .split('\n')
      .filter((line) => line.includes('customAttribution'));
    if (attributionProps.length === 0) return;
    for (const line of attributionProps) {
      expect(
        line.includes(OSM_CREDIT),
        `${rel} passes a customAttribution that does not credit OpenStreetMap: ${line.trim()}`,
      ).toBe(true);
    }
  });
});
