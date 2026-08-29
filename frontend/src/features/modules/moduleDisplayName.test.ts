import { describe, expect, it } from 'vitest';

import de from '../../app/locales/de';
import en from '../../app/locales/en';
import ru from '../../app/locales/ru';
import {
  moduleDisplayNameKey,
  resolveModuleDisplayName,
  type TranslatableModule,
} from './moduleDisplayName';

/**
 * A stand-in for i18next's `t`: it knows a fixed set of keys and, like the real
 * one, returns `defaultValue` for anything it does not know. That last part is
 * the whole reason this resolver cannot just call `t` and stop, so the fake has
 * to reproduce it faithfully.
 */
function translator(known: Record<string, string>) {
  return (key: string, options: { defaultValue: string }) => known[key] ?? options.defaultValue;
}

const usPack: TranslatableModule = {
  name: 'oe_us_pack',
  display_name: 'Regional Pack - United States',
  display_name_i18n: {
    de: 'Regionalpaket - Vereinigte Staaten',
    ru: 'Региональный пакет - США',
  },
};

const boq: TranslatableModule = {
  name: 'oe_boq',
  display_name: 'Bill of Quantities',
};

describe('moduleDisplayNameKey', () => {
  it('drops the oe_ prefix that means nothing to a reader', () => {
    expect(moduleDisplayNameKey('oe_dwg_takeoff')).toBe('modules.catalog.dwg_takeoff');
  });

  it('leaves a name that does not carry the prefix alone', () => {
    expect(moduleDisplayNameKey('custom_register')).toBe('modules.catalog.custom_register');
  });

  it('strips only a leading prefix, not one in the middle', () => {
    expect(moduleDisplayNameKey('oe_cost_oe_match')).toBe('modules.catalog.cost_oe_match');
  });
});

describe('resolveModuleDisplayName', () => {
  it('prefers the locale file over the manifest, because it is the curated source', () => {
    const t = translator({ 'modules.catalog.us_pack': 'US-Regionalpaket' });
    expect(resolveModuleDisplayName(usPack, t, 'de')).toBe('US-Regionalpaket');
  });

  it('uses the manifest when the locale has no key for it', () => {
    // This is the regional-pack case: German exists in the manifest and the
    // locale files have not caught up. Without this branch the reader gets
    // English while a real translation sits unread in the manifest.
    const t = translator({});
    expect(resolveModuleDisplayName(usPack, t, 'de')).toBe('Regionalpaket - Vereinigte Staaten');
  });

  it('does not let an English fallback shadow a manifest translation', () => {
    // i18next answers a missing German key with the English value, so a naive
    // "did t() return something" check would stop here and never reach the
    // manifest. The resolver has to notice that what came back IS the English.
    const t = translator({ 'modules.catalog.us_pack': 'Regional Pack - United States' });
    expect(resolveModuleDisplayName(usPack, t, 'ru')).toBe('Региональный пакет - США');
  });

  it('accepts a regional tag against a bare manifest entry', () => {
    const t = translator({});
    expect(resolveModuleDisplayName(usPack, t, 'de-AT')).toBe('Regionalpaket - Vereinigte Staaten');
  });

  it('does not widen a bare tag into itself twice', () => {
    const t = translator({});
    expect(resolveModuleDisplayName(usPack, t, 'fr')).toBe('Regional Pack - United States');
  });

  it('falls back to English when nothing translates the module', () => {
    const t = translator({});
    expect(resolveModuleDisplayName(boq, t, 'ja')).toBe('Bill of Quantities');
  });

  it('translates a module that has no manifest dict at all', () => {
    const t = translator({ 'modules.catalog.boq': 'Ведомость объёмов работ' });
    expect(resolveModuleDisplayName(boq, t, 'ru')).toBe('Ведомость объёмов работ');
  });

  it('ignores a blank manifest entry rather than rendering an empty name', () => {
    const blank: TranslatableModule = {
      name: 'oe_x',
      display_name: 'Something',
      display_name_i18n: { de: '   ' },
    };
    expect(resolveModuleDisplayName(blank, translator({}), 'de')).toBe('Something');
  });

  it('returns English for an English reader', () => {
    const t = translator({ 'modules.catalog.boq': 'Bill of Quantities' });
    expect(resolveModuleDisplayName(boq, t, 'en')).toBe('Bill of Quantities');
  });
});

/**
 * The tests above prove the resolver's logic against a fake dictionary, which
 * cannot fail if the key it derives is not the key the locale files actually
 * carry: the fake answers whatever key it is handed. So these run the same
 * resolver against the real shipped locale data and ask what a reader would
 * see, which is the question that matters and the one nobody was asking when
 * 185 names rendered English for years.
 */
describe('resolveModuleDisplayName against the shipped locale files', () => {
  const real = (dict: Record<string, string>) => (key: string, options: { defaultValue: string }) =>
    dict[key] ?? options.defaultValue;

  it('derives keys that exist in en.ts, for every module en.ts knows about', () => {
    const keys = Object.keys(en.translation).filter((k) => k.startsWith('modules.catalog.'));
    // The Python gate asserts the other direction, that every manifest has a
    // key. This asserts the derivation itself round-trips, so the two sides
    // cannot agree on a key shape the page never asks for.
    expect(keys.length).toBeGreaterThan(150);
    for (const key of keys) {
      expect(moduleDisplayNameKey(`oe_${key.slice('modules.catalog.'.length)}`)).toBe(key);
    }
  });

  it('shows a German reader German, not English', () => {
    const mod: TranslatableModule = { name: 'oe_boq', display_name: en.translation['modules.catalog.boq']! };
    const shown = resolveModuleDisplayName(mod, real(de.translation), 'de');
    expect(shown).toBe(de.translation['modules.catalog.boq']);
    expect(shown).not.toBe(mod.display_name);
  });

  it('shows a Russian reader Cyrillic for a mined name', () => {
    const mod: TranslatableModule = { name: 'oe_boq', display_name: en.translation['modules.catalog.boq']! };
    expect(resolveModuleDisplayName(mod, real(ru.translation), 'ru')).toMatch(/[Ѐ-ӿ]/);
  });

  it('reaches the manifest German for a regional pack the locales do not cover', () => {
    // us_pack is one of the eleven manifests carrying a hand-written de/ru
    // name. Whether the locale files have caught up or not, a German reader
    // must not end up with the English string while that German exists.
    const mod: TranslatableModule = {
      name: 'oe_us_pack',
      display_name: en.translation['modules.catalog.us_pack']!,
      display_name_i18n: { de: 'Regionalpaket - Vereinigte Staaten' },
    };
    expect(resolveModuleDisplayName(mod, real(de.translation), 'de')).not.toBe(mod.display_name);
  });

  it('answers the module that used to stand for the untranslated cohort', () => {
    // This pinned the opposite until the cohort it named stopped existing.
    // client_errors was one of the 62 modules with no attested carrier, left
    // untranslated on purpose so that filling them later would be a visible
    // change rather than a silent one. They were filled: every locale now
    // answers all 190 modules.catalog keys en.ts carries, none missing in any
    // of them, so there is no longer a module for this case to point at. The
    // resolver's English-fallback branch is not what changed and is still
    // covered by the fake-dictionary case above, which is where it belongs,
    // because it is a property of the resolver and not of the shipped data.
    const key = 'modules.catalog.client_errors';
    const mod: TranslatableModule = { name: 'oe_client_errors', display_name: en.translation[key]! };
    expect(en.translation[key]).toBeDefined();
    expect(de.translation[key]).toBeDefined();
    expect(resolveModuleDisplayName(mod, real(de.translation), 'de')).not.toBe(mod.display_name);
  });
});
