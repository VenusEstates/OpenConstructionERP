// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// The registry search, asserted against the locale files that actually ship.
//
// The defect this replaces was not a crash: 189 modules rendered as one flat
// unsearchable grid, and a reader looking for "Regional Pack - China" concluded
// the module did not exist. So the assertions below are all of the shape "the
// word a reader would type reaches the module", and the load-bearing ones use a
// word that exists only in the translation - if the filter ever falls back to
// matching the English `display_name` and the `oe_` id, those go red while the
// English cases stay green.
//
// Run:  npx vitest run src/features/modules/moduleSearch.test.ts

import { describe, expect, it } from 'vitest';

import de from '../../app/locales/de';
import en from '../../app/locales/en';
import ru from '../../app/locales/ru';
import {
  ALL_CATEGORIES,
  filterModules,
  matchesModuleSearch,
  moduleSearchText,
  tallyModuleCategories,
  type ModuleSearchContext,
  type SearchableModule,
} from './moduleSearch';

/** `t` backed by a real shipped locale, falling back the way i18next does. */
function localeTranslator(bundle: Record<string, string>) {
  return (key: string, options: { defaultValue: string }) => bundle[key] ?? options.defaultValue;
}

function context(bundle: Record<string, string>, language: string): ModuleSearchContext {
  return { t: localeTranslator(bundle), language };
}

/**
 * The regional cohort as the server reports it, verbatim from
 * `GET /api/v1/modules/`. Names and categories are the real ones; a fixture
 * that invented them would not be evidence about the shipped page.
 */
const REGIONAL_PACKS: SearchableModule[] = [
  { name: 'oe_asia_pac_pack', display_name: 'Regional Pack - Asia-Pacific', category: 'regional' },
  { name: 'oe_china_pack', display_name: 'Regional Pack - China', category: 'regional' },
  { name: 'oe_dach_pack', display_name: 'Regional Pack - DACH (DE/AT/CH)', category: 'regional' },
  { name: 'oe_india_pack', display_name: 'Regional Pack - India', category: 'regional' },
  { name: 'oe_latam_pack', display_name: 'Regional Pack - Latin America', category: 'regional' },
  { name: 'oe_mexico_pack', display_name: 'Regional Pack - Mexico', category: 'regional' },
  { name: 'oe_middle_east_pack', display_name: 'Regional Pack - Middle East & GCC', category: 'regional' },
  { name: 'oe_payment_clock', display_name: 'Payment Clock', category: 'regional' },
  { name: 'oe_russia_pack', display_name: 'Regional Pack - Russia & CIS', category: 'regional' },
  { name: 'oe_sa_pack', display_name: 'Regional Pack - South Africa', category: 'regional' },
  { name: 'oe_uk_pack', display_name: 'Regional Pack - United Kingdom', category: 'regional' },
  { name: 'oe_us_ca_pack', display_name: 'Regional Pack - California', category: 'regional' },
  { name: 'oe_us_pack', display_name: 'Regional Pack - United States', category: 'regional' },
  { name: 'oe_us_tx_pack', display_name: 'Regional Pack - Texas', category: 'regional' },
];

const CHINA_PACK = REGIONAL_PACKS.find((m) => m.name === 'oe_china_pack')!;

const OTHER_MODULES: SearchableModule[] = [
  { name: 'oe_boq', display_name: 'Bill of Quantities', category: 'core' },
  { name: 'oe_bim_hub', display_name: 'BIM Hub', category: 'core' },
  { name: 'oe_tendering', display_name: 'Tendering', category: 'business' },
];

const ALL_MODULES = [...REGIONAL_PACKS, ...OTHER_MODULES];

function names(modules: SearchableModule[]): string[] {
  return modules.map((m) => m.name);
}

describe('the founder case: reaching the China pack by the words on the card', () => {
  it('finds it from the English name', () => {
    const found = filterModules(ALL_MODULES, 'China', ALL_CATEGORIES, context(en.translation, 'en'));
    expect(names(found)).toEqual(['oe_china_pack']);
  });

  it('finds it from the words "Regional Pack"', () => {
    const found = filterModules(ALL_MODULES, 'regional pack', ALL_CATEGORIES, context(en.translation, 'en'));
    expect(found).toHaveLength(13);
    expect(names(found)).toContain('oe_china_pack');
  });

  it('finds it from the module id a support thread would quote', () => {
    const found = filterModules(ALL_MODULES, 'oe_china_pack', ALL_CATEGORIES, context(en.translation, 'en'));
    expect(names(found)).toEqual(['oe_china_pack']);
  });

  it('ignores case and surrounding whitespace, the way a paste does', () => {
    const found = filterModules(ALL_MODULES, '  CHINA  ', ALL_CATEGORIES, context(en.translation, 'en'));
    expect(names(found)).toEqual(['oe_china_pack']);
  });
});

/**
 * These are the assertions that discriminate. "China" is in the raw id, so it
 * matches even with the translation dropped from the haystack and proves
 * nothing about translation. "Regionalpaket" and "Китай" appear in no English
 * field and in no `oe_` id anywhere in the fixture, so they can only be reached
 * through `resolveModuleDisplayName`.
 */
describe('a reader searches in the language the page is rendered in', () => {
  it('reaches the China pack from the German word on the card', () => {
    expect(de.translation['modules.catalog.china_pack']).toBe('Regionalpaket - China');
    const found = filterModules(ALL_MODULES, 'Regionalpaket', ALL_CATEGORIES, context(de.translation, 'de'));
    expect(names(found)).toContain('oe_china_pack');
  });

  it('reaches it from the Russian word on the card', () => {
    const found = filterModules(ALL_MODULES, 'Китай', ALL_CATEGORIES, context(ru.translation, 'ru'));
    expect(names(found)).toEqual(['oe_china_pack']);
  });

  it('proves the German word is absent from every non-translated field', () => {
    // If this ever fails, the two assertions above have stopped discriminating
    // and would pass on a filter that never consults the translation at all.
    const untranslated = ALL_MODULES.flatMap((m) => [m.name, m.display_name, m.category ?? ''])
      .join('\n')
      .toLowerCase();
    expect(untranslated).not.toContain('regionalpaket');
    expect(untranslated).not.toContain('китай');
  });

  it('still reaches the module from the manifest translation when the locale has no key', () => {
    // Runtime-installed modules cannot add keys to a compiled locale bundle,
    // so `display_name_i18n` is their only translation path. The search has to
    // read the same fallback chain the card does.
    const installed: SearchableModule = {
      name: 'oe_custom_register',
      display_name: 'Custom Register',
      display_name_i18n: { de: 'Eigenes Verzeichnis' },
      category: 'extension',
    };
    expect(
      matchesModuleSearch(installed, 'Eigenes', { t: localeTranslator(de.translation), language: 'de' }),
    ).toBe(true);
  });

  it('does not leak one language into another', () => {
    // A German reader typing the Russian word finds nothing: the haystack
    // carries the current language's name, not every language's.
    const found = filterModules(ALL_MODULES, 'Китай', ALL_CATEGORIES, context(de.translation, 'de'));
    expect(found).toHaveLength(0);
  });
});

describe('moduleSearchText', () => {
  it('opens the id out so both "oe_china_pack" and "china pack" are typeable', () => {
    const text = moduleSearchText(CHINA_PACK, context(en.translation, 'en'));
    expect(text).toContain('oe_china_pack');
    expect(text).toContain('china pack');
  });

  it('carries the description and the category label', () => {
    const mod: SearchableModule = {
      name: 'oe_gaeb',
      display_name: 'GAEB Exchange',
      description: 'Reads and writes GAEB XML 3.3 tender files',
      category: 'regional',
    };
    const text = moduleSearchText(mod, {
      t: localeTranslator(en.translation),
      language: 'en',
      categoryLabel: () => 'Regional Standards',
    });
    expect(text).toContain('tender files');
    expect(text).toContain('regional standards');
  });

  it('matches a category by its translated label, not only its raw value', () => {
    const ctx: ModuleSearchContext = {
      t: localeTranslator(de.translation),
      language: 'de',
      categoryLabel: () => 'Regionale Standards',
    };
    expect(matchesModuleSearch(CHINA_PACK, 'Regionale Standards', ctx)).toBe(true);
  });
});

describe('an empty query is not a filter', () => {
  it('keeps every module', () => {
    const found = filterModules(ALL_MODULES, '', ALL_CATEGORIES, context(en.translation, 'en'));
    expect(found).toHaveLength(ALL_MODULES.length);
  });

  it('keeps every module when the query is only spaces', () => {
    const found = filterModules(ALL_MODULES, '   ', ALL_CATEGORIES, context(en.translation, 'en'));
    expect(found).toHaveLength(ALL_MODULES.length);
  });
});

describe('the category filter', () => {
  it('narrows to the regional cohort a reader thinks in', () => {
    const found = filterModules(ALL_MODULES, '', 'regional', context(en.translation, 'en'));
    expect(found).toHaveLength(14);
  });

  it('combines with the search rather than replacing it', () => {
    const found = filterModules(ALL_MODULES, 'China', 'regional', context(en.translation, 'en'));
    expect(names(found)).toEqual(['oe_china_pack']);
    expect(filterModules(ALL_MODULES, 'China', 'core', context(en.translation, 'en'))).toHaveLength(0);
  });
});

/**
 * The chips are built from the modules, never from the page's label map. The
 * server ships eight categories the map has no entry for and they account for a
 * quarter of the list; a chip row derived from the map would drop them out of
 * the filter with nothing going red.
 */
describe('tallyModuleCategories', () => {
  /** The category histogram measured on the live server, 189 modules. */
  const LIVE_CATEGORIES: Record<string, number> = {
    core: 119,
    business: 34,
    regional: 14,
    extension: 7,
    controls: 6,
    enterprise: 3,
    developer_tools: 1,
    compliance: 1,
    infra: 1,
    estimation: 1,
    integration: 1,
    project_controls: 1,
  };

  const liveFixture: SearchableModule[] = Object.entries(LIVE_CATEGORIES).flatMap(
    ([category, count]) =>
      Array.from({ length: count }, (_, i) => ({
        name: `oe_${category}_${i}`,
        display_name: `${category} ${i}`,
        category,
      })),
  );

  it('gives every category the server actually ships a chip', () => {
    const tallies = tallyModuleCategories(liveFixture);
    expect(tallies.map((c) => c.category).sort()).toEqual(Object.keys(LIVE_CATEGORIES).sort());
  });

  it('accounts for every module, so no chip can silently drop a third of the list', () => {
    const tallies = tallyModuleCategories(liveFixture);
    expect(tallies.reduce((sum, c) => sum + c.count, 0)).toBe(liveFixture.length);
    expect(liveFixture).toHaveLength(189);
  });

  it('counts each category correctly', () => {
    const tallies = tallyModuleCategories(liveFixture);
    const byName = new Map(tallies.map((c) => [c.category, c.count]));
    expect(byName.get('regional')).toBe(14);
    expect(byName.get('core')).toBe(119);
  });

  it('honours the preferred order and puts anything unnamed after it', () => {
    const order = tallyModuleCategories(liveFixture, ['core', 'regional']).map((c) => c.category);
    expect(order.slice(0, 2)).toEqual(['core', 'regional']);
    // Everything the order does not name follows, alphabetically.
    const rest = order.slice(2);
    expect(rest).toEqual([...rest].sort());
    expect(rest).toContain('business');
  });

  it('ignores a module with no category rather than inventing one', () => {
    const tallies = tallyModuleCategories([
      { name: 'oe_a', display_name: 'A' },
      { name: 'oe_b', display_name: 'B', category: 'core' },
    ]);
    expect(tallies).toEqual([{ category: 'core', count: 1 }]);
  });
});
