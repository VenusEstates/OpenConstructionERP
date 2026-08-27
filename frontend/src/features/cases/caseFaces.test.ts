// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Gate for the case-photography plumbing. The one that matters most is the
// closed-set check at the bottom: every path the module can EVER return must
// name a file that exists under frontend/public/assets/people, so a renamed
// or deleted webp fails here instead of 404ing in production.

import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  BESPOKE_CASE_PHOTOS,
  PEOPLE_ASSETS_BASE,
  ROLE_CAST,
  type CaseRole,
  caseFaceFor,
  companySceneFor,
  companyThumbFor,
  dealCaseFaces,
} from './caseFaces';
import { COMPANY_TYPE_META } from './companyTypes';
import { PLAYBOOKS } from './playbooks';

const HERE = dirname(fileURLToPath(import.meta.url));
const PEOPLE_DIR = resolve(HERE, '../../../public/assets/people');
const PRESETS_PY = resolve(HERE, '../../../../backend/app/core/onboarding_presets.py');

/** The closed set of files actually on disk. */
const filesOnDisk = new Set(readdirSync(PEOPLE_DIR));

/** Assert a public path returned by the module names a real file. */
function expectOnDisk(publicPath: string | null): void {
  expect(publicPath).not.toBeNull();
  expect(publicPath!.startsWith(`${PEOPLE_ASSETS_BASE}/`)).toBe(true);
  const file = publicPath!.slice(`${PEOPLE_ASSETS_BASE}/`.length);
  expect(filesOnDisk, `${file} is not in public/assets/people`).toContain(file);
}

/** The backend's company preset keys, read from the source of truth
 *  (COMPANY_PRESETS only - the SIZE_PRESETS dict below it is a different
 *  dimension and has no photos). */
function backendPresetKeys(): string[] {
  const text = readFileSync(PRESETS_PY, 'utf-8');
  const start = text.indexOf('COMPANY_PRESETS');
  const end = text.indexOf('SIZE_PRESETS');
  const section = text.slice(start, end === -1 ? undefined : end);
  const keys = [...section.matchAll(/key="([a-z0-9_]+)"/g)].map((m) => m[1]!);
  // The wizard shows at least the nine headline profiles; the catalogue has
  // grown past that. If this drops below nine the slice above went stale.
  expect(keys.length).toBeGreaterThanOrEqual(9);
  return keys;
}

describe('companyThumbFor', () => {
  it('resolves every COMPANY_TYPE_META id (hyphenated scheme) to a file on disk', () => {
    for (const meta of COMPANY_TYPE_META) {
      expectOnDisk(companyThumbFor(meta.id));
    }
  });

  it('resolves every backend onboarding preset key (underscored scheme) to a file on disk', () => {
    for (const key of backendPresetKeys()) {
      expectOnDisk(companyThumbFor(key));
    }
  });

  it('returns null for an unknown id instead of minting a 404 path', () => {
    expect(companyThumbFor('interior-decorator')).toBeNull();
    expect(companyThumbFor('')).toBeNull();
  });
});

describe('companySceneFor', () => {
  it('resolves every COMPANY_TYPE_META id (hyphenated scheme) to a file on disk', () => {
    for (const meta of COMPANY_TYPE_META) {
      expectOnDisk(companySceneFor(meta.id));
    }
  });

  it('resolves every backend onboarding preset key (underscored scheme) to a file on disk', () => {
    for (const key of backendPresetKeys()) {
      expectOnDisk(companySceneFor(key));
    }
  });

  it('returns null for an unknown id instead of minting a 404 path', () => {
    expect(companySceneFor('interior-decorator')).toBeNull();
    expect(companySceneFor('')).toBeNull();
  });

  it('names the same stem as the thumb it is cropped from', () => {
    for (const meta of COMPANY_TYPE_META) {
      const thumb = companyThumbFor(meta.id)!;
      expect(companySceneFor(meta.id)).toBe(thumb.replace('/cmt-', '/cmp-'));
    }
  });
});

/** A value that is NOT in the company vocabulary.
 *
 *  `caseFaceFor` and `CaseFaceInput` take `CompanyType` rather than `string`,
 *  so a caller can no longer wander into the unknown-id branch by accident -
 *  which is the point of the types. The branch still has to hold at runtime,
 *  because both id schemes arrive as plain strings from the server and from
 *  localStorage, so the only way left to test it is to force one through. The
 *  double assertion is deliberate and is the marker for "this is a negative
 *  control", not a shortcut around a type that was inconvenient. */
const NOT_A_COMPANY_TYPE = 'interior-decorator' as unknown as CaseRole;

describe('caseFaceFor', () => {
  const roles = Object.keys(ROLE_CAST) as CaseRole[];

  it('is deterministic - same inputs, same face', () => {
    for (const role of roles) {
      for (let i = 0; i < 5; i++) {
        expect(caseFaceFor('some-case', [role], i)).toEqual(caseFaceFor('some-case', [role], i));
      }
    }
  });

  it('never repeats a face on adjacent indices within a role whose cast has more than one member', () => {
    for (const role of roles) {
      const cast = ROLE_CAST[role];
      if (cast.length < 2) continue;
      for (let i = 0; i < cast.length * 2; i++) {
        const a = caseFaceFor('case-a', [role], i)?.src;
        const b = caseFaceFor('case-b', [role], i + 1)?.src;
        expect(a, `role ${role}, indices ${i}/${i + 1}`).not.toBe(b);
      }
    }
  });

  it('lets the first castable company type win, like the site keys on the first data-companies token', () => {
    expect(caseFaceFor('some-case', ['cost-consultant', 'general-contractor'], 0)?.src).toBe(
      `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`,
    );
    expect(caseFaceFor('some-case', [NOT_A_COMPANY_TYPE, 'designer'], 0)?.src).toBe(
      `${PEOPLE_ASSETS_BASE}/prf-architecture-engineering.webp`,
    );
  });

  it('lets a bespoke pbk photo win over the pooled company cast', () => {
    for (const [slug, photo] of Object.entries(BESPOKE_CASE_PHOTOS)) {
      expect(caseFaceFor(slug, ['general-contractor'], 3)).toEqual({ src: photo, pooled: photo });
    }
  });

  it('returns null when no company type has a cast', () => {
    expect(caseFaceFor('some-case', [NOT_A_COMPANY_TYPE], 0)).toBeNull();
    expect(caseFaceFor('some-case', [], 0)).toBeNull();
  });
});

/**
 * The country axis. What can be proved here is which FILE the code asks for;
 * whether that file exists is decided by the browser at load time and is
 * proved in caseFacePhoto.test.tsx, because there is no build-time list of
 * country art and there must not be one.
 */
describe('caseFaceFor - country variants', () => {
  it('asks for the country portrait when the case names a market', () => {
    const face = caseFaceFor('some-case', ['cost-consultant'], 0, 'DE');
    expect(face?.src).toBe(`${PEOPLE_ASSETS_BASE}/prf-de-estimator.webp`);
  });

  it('lowercases the market, because the asset folder is lowercase and Linux is not forgiving', () => {
    const upper = caseFaceFor('some-case', ['cost-consultant'], 0, 'CN');
    const lower = caseFaceFor('some-case', ['cost-consultant'], 0, 'cn');
    expect(upper?.src).toBe(`${PEOPLE_ASSETS_BASE}/prf-cn-estimator.webp`);
    expect(lower?.src).toBe(upper?.src);
  });

  it('keeps the pooled portrait beside the country one, so a market with no art has somewhere to land', () => {
    // The fallback for a market nobody has shot yet is not "some other
    // market's photo" and not "nothing" - it is the picture this case wore
    // before the country axis existed.
    const face = caseFaceFor('some-case', ['cost-consultant'], 0, 'ZZ');
    expect(face?.pooled).toBe(`${PEOPLE_ASSETS_BASE}/prf-estimator.webp`);
    expect(caseFaceFor('some-case', ['cost-consultant'], 0)?.pooled).toBe(face?.pooled);
  });

  it('leaves a universal case exactly where it was', () => {
    const before = `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`;
    expect(caseFaceFor('some-case', ['cost-consultant'], 0)).toEqual({
      src: before,
      pooled: before,
    });
  });

  it('does not let the country overrule the company type', () => {
    // Country is a SECOND axis, not a replacement for the first. One market
    // asking two company types for a portrait must still get two different
    // people, or the German cases all end up wearing one face.
    const consultant = caseFaceFor('a', ['cost-consultant'], 0, 'DE')?.src;
    const designer = caseFaceFor('b', ['designer'], 0, 'DE')?.src;
    expect(consultant).toBe(`${PEOPLE_ASSETS_BASE}/prf-de-estimator.webp`);
    expect(designer).toBe(`${PEOPLE_ASSETS_BASE}/prf-de-architecture-engineering.webp`);
    expect(consultant).not.toBe(designer);
  });

  it('keeps the round-robin, so one market does not collapse onto one face', () => {
    // The thing a per-country cast would have destroyed. Thirteen German
    // general-contractor cases have to reach eight different Germans.
    const cast = ROLE_CAST['general-contractor'];
    const asked = new Set(
      cast.map((_, i) => caseFaceFor(`case-${i}`, ['general-contractor'], i, 'DE')?.src),
    );
    expect(asked.size).toBe(cast.length);
  });

  it('leaves a bespoke photo country-blind, since a bespoke photo is already for one case', () => {
    const photo = BESPOKE_CASE_PHOTOS['takeoff-quantities-from-a-pdf-plan']!;
    // The one shipped case that is both bespoke and market-specific.
    expect(caseFaceFor('takeoff-quantities-from-a-pdf-plan', ['designer'], 0, 'DE')).toEqual({
      src: photo,
      pooled: photo,
    });
  });

  it('ignores a region that is not an ISO 3166-1 alpha-2 code rather than minting a nonsense name', () => {
    const pooled = `${PEOPLE_ASSETS_BASE}/prf-estimator.webp`;
    for (const bad of ['', 'DEU', 'd', 'de-DE', '42']) {
      expect(caseFaceFor('some-case', ['cost-consultant'], 0, bad)?.src, bad).toBe(pooled);
    }
  });

  it('names a country file the pooled stem can be read straight out of', () => {
    // The convention is an INSERTION, not a rename: prf-<country>- then the
    // stem, unchanged. That is what lets the manifest be generated and what
    // keeps a stem whose own first segment is short from being ambiguous.
    for (const companyType of Object.keys(ROLE_CAST) as CaseRole[]) {
      const cast = ROLE_CAST[companyType];
      for (let i = 0; i < cast.length; i++) {
        const face = caseFaceFor('no-bespoke-case', [companyType], i, 'GB')!;
        const stem = face.pooled.slice(`${PEOPLE_ASSETS_BASE}/prf-`.length);
        expect(face.src).toBe(`${PEOPLE_ASSETS_BASE}/prf-gb-${stem}`);
      }
    }
  });
});

describe('dealCaseFaces', () => {
  it('carries the case region through to the file it asks for', () => {
    const faces = dealCaseFaces([
      { id: 'universal', companyTypes: ['designer'] },
      { id: 'german', companyTypes: ['designer'], region: 'DE' },
    ]);
    // Two cases, same company type, consecutive positions in the cast: the
    // country decorates whichever stem the round-robin reached, so the market
    // never re-casts the case.
    const cast = ROLE_CAST['designer'];
    expect(faces.get('universal')?.src).toBe(`${PEOPLE_ASSETS_BASE}/${cast[0]}.webp`);
    expect(faces.get('german')?.src).toBe(
      `${PEOPLE_ASSETS_BASE}/${cast[1]!.replace('prf-', 'prf-de-')}.webp`,
    );
    expect(faces.get('german')?.pooled).toBe(`${PEOPLE_ASSETS_BASE}/${cast[1]}.webp`);
  });


  it('deals each role round its cast by position, like the site', () => {
    const cast = ROLE_CAST['general-contractor'];
    const faces = dealCaseFaces(
      cast.map((_, i) => ({ id: `case-${i}`, companyTypes: ['general-contractor'] })),
    );
    cast.forEach((stem, i) => {
      expect(faces.get(`case-${i}`)?.src).toBe(`${PEOPLE_ASSETS_BASE}/${stem}.webp`);
    });
  });

  it('lets a bespoke case take its turn, so it does not re-cast the ones after it', () => {
    const cast = ROLE_CAST['general-contractor'];
    const faces = dealCaseFaces([
      { id: 'answer-an-rfi', companyTypes: ['general-contractor'] },
      { id: 'plain-case', companyTypes: ['general-contractor'] },
    ]);
    expect(faces.get('answer-an-rfi')?.src).toBe(BESPOKE_CASE_PHOTOS['answer-an-rfi']);
    expect(faces.get('plain-case')?.src).toBe(`${PEOPLE_ASSETS_BASE}/${cast[1]}.webp`);
  });

  it('counts each role on its own, so one role does not move another along', () => {
    const faces = dealCaseFaces([
      { id: 'a', companyTypes: ['general-contractor'] },
      { id: 'b', companyTypes: ['designer'] },
      { id: 'c', companyTypes: ['designer'] },
    ]);
    expect(faces.get('a')?.src).toBe(`${PEOPLE_ASSETS_BASE}/${ROLE_CAST['general-contractor'][0]}.webp`);
    expect(faces.get('b')?.src).toBe(`${PEOPLE_ASSETS_BASE}/${ROLE_CAST['designer'][0]}.webp`);
    expect(faces.get('c')?.src).toBe(`${PEOPLE_ASSETS_BASE}/${ROLE_CAST['designer'][1]}.webp`);
  });

  it('leaves out a case whose company types have no cast rather than guessing', () => {
    const faces = dealCaseFaces([
      { id: 'no-types', companyTypes: [] },
      { id: 'unknown-type', companyTypes: [NOT_A_COMPANY_TYPE] },
    ]);
    expect(faces.size).toBe(0);
  });
});

describe('closed set - every path the module can ever return exists on disk', () => {
  it('covers every pooled portrait reachable through caseFaceFor', () => {
    for (const companyType of Object.keys(ROLE_CAST) as CaseRole[]) {
      const cast = ROLE_CAST[companyType];
      for (let i = 0; i < cast.length; i++) {
        expectOnDisk(caseFaceFor('no-bespoke-case', [companyType], i)?.pooled ?? null);
      }
    }
  });

  it('covers every bespoke photo', () => {
    for (const [slug, photo] of Object.entries(BESPOKE_CASE_PHOTOS)) {
      expect(photo).toBe(`${PEOPLE_ASSETS_BASE}/pbk-${slug}.webp`);
      expectOnDisk(photo);
    }
  });

  it('covers every company thumb and scene reachable from either id scheme', () => {
    const ids = [...COMPANY_TYPE_META.map((m) => m.id as string), ...backendPresetKeys()];
    for (const id of ids) {
      expectOnDisk(companyThumbFor(id));
      expectOnDisk(companySceneFor(id));
    }
  });

  // The tests above feed hand-written role arrays, which can only prove the
  // module is consistent with itself. This one runs the real catalogue through
  // the same call the Cases hub makes, so a case shipping a company type
  // nobody cast - or a webp leaving the folder - fails here.
  it('gives every shipped case a face that exists on disk', () => {
    const faces = dealCaseFaces(PLAYBOOKS);
    const missing = PLAYBOOKS.filter((pb) => !faces.has(pb.id)).map((pb) => pb.id);
    expect(missing, 'cases with no castable company type').toEqual([]);
    // `pooled` is the closed half of the pair and the one this suite guards:
    // whatever a market has or has not been shot for, every case still has a
    // real file to land on. `src` is deliberately open (see below).
    for (const face of faces.values()) expectOnDisk(face.pooled);
  });

  // The counterpart to the check above, and the reason it asserts on `pooled`
  // rather than on `src`. The country portraits are bought and dropped into
  // public/assets/people over time, so `src` names files that do not exist
  // yet and MUST be allowed to: a closed set here would be a list of known
  // filenames, which is exactly what point 2 of this feature forbids and what
  // `COMPANY_ART_IDS` in CompanyArt.tsx demonstrates the cost of. What is
  // asserted instead is that the open half is well FORMED - it differs from
  // the pooled path by exactly a lowercase country code and nothing else - so
  // the founder can shop from `docs/strategy/case_portrait_manifest.md` and be
  // certain the names match.
  it('asks for a well-formed country file for every case that names a market', () => {
    const faces = dealCaseFaces(PLAYBOOKS);
    let regioned = 0;
    for (const pb of PLAYBOOKS) {
      const face = faces.get(pb.id)!;
      if (!pb.region || face.pooled.includes('/pbk-')) {
        expect(face.src, `${pb.id} has no market, so it asks for the pooled photo`).toBe(
          face.pooled,
        );
        continue;
      }
      regioned += 1;
      const stem = face.pooled.slice(`${PEOPLE_ASSETS_BASE}/prf-`.length);
      expect(face.src).toBe(`${PEOPLE_ASSETS_BASE}/prf-${pb.region.toLowerCase()}-${stem}`);
      // Lowercase, always. The pooled folder is entirely lowercase and a Linux
      // server is not as forgiving about it as the developer's filesystem.
      expect(face.src).toBe(face.src.toLowerCase());
    }
    // A floor, so this cannot be satisfied by a catalogue that lost its
    // market-specific cases: sixty-two of them carried a region when the
    // country axis was added.
    expect(regioned, 'cases carrying a region').toBeGreaterThan(50);
  });
});
