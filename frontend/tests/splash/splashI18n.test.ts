/**
 * The desktop splash screen carries its own translations, and nothing else
 * checks them.
 *
 * ``frontend/public/splash.html`` is the first thing every desktop user sees.
 * It runs on the tauri:// origin before the application bundle exists, so it
 * cannot use i18next, cannot read the language the user picked inside the app,
 * and is not covered by any of the locale gates that guard
 * ``src/app/locales/``. Its strings live in a JSON table inside the file.
 *
 * That leaves three ways for it to rot quietly, and this file is here for all
 * three: a language gets added to the picker and nobody adds a splash table for
 * it, a translation loses the ``{n}`` slot the code interpolates into, or the
 * script asks for a key that no table has and the screen renders a blank.
 *
 * The table is parsed out of the HTML rather than imported, because the file
 * has to be a single self-contained document the Tauri window can load.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const SPLASH = resolve(__dirname, '..', '..', 'public', 'splash.html');
const I18N_TS = resolve(__dirname, '..', '..', 'src', 'app', 'i18n.ts');

const html = readFileSync(SPLASH, 'utf-8');
const i18nSource = readFileSync(I18N_TS, 'utf-8');

type Table = Record<string, string>;

/** Pull the strict-JSON translation table out of the splash document. */
function readTable(): Record<string, Table> {
  const start = html.indexOf('var SPLASH_I18N = ');
  const end = html.indexOf('// (i18n-table-end)');
  expect(start, 'SPLASH_I18N declaration not found in splash.html').toBeGreaterThan(-1);
  expect(end, 'i18n-table-end sentinel not found in splash.html').toBeGreaterThan(start);
  const raw = html.slice(start + 'var SPLASH_I18N = '.length, end).trim().replace(/;$/, '');
  return JSON.parse(raw) as Record<string, Table>;
}

/**
 * The language codes the picker actually offers, read from the source rather
 * than imported: importing ``i18n.ts`` executes i18next and pulls in the whole
 * English locale, and this test is about two files agreeing on a list.
 *
 * A commented-out entry is not an entry — ``uz`` sits in that file behind a
 * ``//`` and is deliberately not offered — so the leading ``{`` has to be the
 * first thing on the line.
 */
function offeredLanguages(): { code: string; rtl: boolean }[] {
  const block = i18nSource.slice(
    i18nSource.indexOf('export const SUPPORTED_LANGUAGES = ['),
    i18nSource.indexOf('\n];'),
  );
  const out: { code: string; rtl: boolean }[] = [];
  for (const line of block.split('\n')) {
    const m = /^\s*\{ code: '([^']+)'/.exec(line);
    if (m) out.push({ code: m[1]!, rtl: line.includes("dir: 'rtl'") });
  }
  return out;
}

/** Every translation key the splash script actually asks for. */
function keysUsed(): Set<string> {
  const used = new Set<string>();
  for (const m of html.matchAll(/\bt\('([A-Za-z]+)'\)/g)) used.add(m[1]!);
  for (const m of html.matchAll(/\bkey:\s*'([A-Za-z]+)'/g)) used.add(m[1]!);
  for (const m of html.matchAll(/data-i18n(?:-aria)?="([A-Za-z]+)"/g)) used.add(m[1]!);
  const mapStart = html.indexOf('var LAUNCHER_PHRASES = {');
  const mapEnd = html.indexOf('};', mapStart);
  expect(mapStart, 'LAUNCHER_PHRASES not found').toBeGreaterThan(-1);
  for (const m of html.slice(mapStart, mapEnd).matchAll(/:\s*'([A-Za-z]+)'/g)) used.add(m[1]!);
  return used;
}

const table = readTable();
const offered = offeredLanguages();
const english = table.en!;

describe('desktop splash translations', () => {
  it('parses as strict JSON and has English as its source', () => {
    expect(english, 'no "en" table').toBeDefined();
    expect(Object.keys(english).length).toBeGreaterThan(20);
    // Guard the guard: a regex that matched nothing would make every check
    // below pass vacuously.
    expect(offered.length).toBeGreaterThan(30);
  });

  it('gives every language the picker offers something to render', () => {
    // Resolution is exact code, then base language, then English. A language
    // whose base has no table would silently show English on the first screen
    // of the product, so name the ones that would.
    const withoutTable = offered
      .map((l) => l.code)
      .filter((code) => {
        const base = code.split('-')[0]!;
        // English and its regional variants are answered by the source table.
        if (base === 'en') return false;
        return !table[code] && !table[base];
      });
    expect(withoutTable, 'offered languages with no splash table').toEqual([]);
  });

  it('lays out the right-to-left languages right to left', () => {
    const declared = /var SPLASH_RTL = \[([^\]]*)\]/.exec(html);
    expect(declared, 'SPLASH_RTL not found').not.toBeNull();
    const inSplash = [...declared![1]!.matchAll(/'([a-z-]+)'/g)].map((m) => m[1]!).sort();
    const inApp = offered.filter((l) => l.rtl).map((l) => l.code.split('-')[0]!);
    expect(inSplash).toEqual([...new Set(inApp)].sort());
  });

  it('answers every key the script asks for', () => {
    const missing = [...keysUsed()].filter((k) => typeof english[k] !== 'string').sort();
    expect(missing, 'keys used by splash.html with no English string').toEqual([]);
  });

  it.each(Object.keys(table).filter((code) => code !== 'en'))(
    '%s carries exactly the English key set, with no blanks',
    (code) => {
      const t = table[code]!;
      expect(Object.keys(t).sort()).toEqual(Object.keys(english).sort());
      const blank = Object.keys(t).filter((k) => !t[k]!.trim());
      expect(blank, `empty strings in ${code}`).toEqual([]);
    },
  );

  it.each(Object.keys(table).filter((code) => code !== 'en'))(
    '%s keeps the {n} slot exactly where English has one',
    (code) => {
      const t = table[code]!;
      // A mechanical pass over these files can rename or drop an
      // interpolation token without any other gate noticing, and the result is
      // a literal "{n}" on screen or a number that never appears at all.
      const expected = Object.keys(english)
        .filter((k) => english[k]!.includes('{n}'))
        .sort();
      const actual = Object.keys(t)
        .filter((k) => t[k]!.includes('{n}'))
        .sort();
      expect(actual).toEqual(expected);
      for (const k of expected) {
        expect(t[k]!.split('{n}').length - 1, `${code}.${k} repeats {n}`).toBe(1);
      }
    },
  );
});
