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
 * That leaves four ways for it to rot quietly, and this file is here for all
 * four: a language gets added to the picker and nobody adds a splash table for
 * it, a language is answered in a different language than the one asked for, a
 * translation loses the ``{n}`` slot the code interpolates into, or the script
 * asks for a key that no table has and the screen renders a blank.
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
 * A commented-out entry is not an entry, so the leading ``{`` has to be the
 * first thing on the line. That rule was written while ``uz`` sat behind a
 * ``//``. Uzbek is offered now, and the rule still stands for the next language
 * that waits behind one.
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

/**
 * The offered languages that deliberately read another language's table.
 *
 * Read from the file rather than restated here, so that this test asks the
 * shipped document what it decided instead of asking a copy of the decision.
 */
function readInherit(): Record<string, string> {
  const m = /var SPLASH_INHERIT = \{([^}]*)\}/.exec(html);
  expect(m, 'SPLASH_INHERIT not found in splash.html').not.toBeNull();
  const out: Record<string, string> = {};
  for (const e of m![1]!.matchAll(/'([^']+)':\s*'([^']+)'/g)) out[e[1]!] = e[2]!;
  return out;
}

/**
 * Every interpolation slot in a string, sorted, duplicates kept.
 *
 * Sorted because word order is exactly what a translation is allowed to
 * change; duplicates kept because a slot repeated once too often is as wrong
 * as one that went missing, and a plain set would call the two the same.
 */
function placeholders(value: string): string[] {
  return [...value.matchAll(/\{[^}]*\}/g)].map((m) => m[0]).sort();
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
const inherit = readInherit();
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

  it('answers every offered language, by a table or by a stated decision', () => {
    // This used to accept a base-language table as cover for a regional one,
    // and that is exactly how pt-BR came to be answered in European
    // Portuguese: the picker offered it, "pt" had a table, the test passed,
    // and Brazilian users read "ficheiro" on the first screen of the product
    // while every other screen said "arquivo". Falling back to the base
    // language is the right behaviour for a machine set to a language nobody
    // offers. It is not an answer for a language the product does offer, so
    // each of those has to be either translated here or written down as
    // deliberately inherited.
    const unanswered = offered
      .map((l) => l.code)
      .filter((code) => !table[code] && !inherit[code]);
    expect(unanswered, 'offered languages that no table and no decision covers').toEqual([]);
  });

  it('inherits only from tables that exist, and never from itself', () => {
    for (const [code, from] of Object.entries(inherit)) {
      expect(table[from], `${code} inherits from ${from}, which has no table`).toBeDefined();
      expect(table[code], `${code} both inherits and has its own table`).toBeUndefined();
      expect(from, `${code} inherits from itself`).not.toBe(code);
    }
  });

  it.each(Object.keys(table).filter((c) => c.includes('-')))(
    '%s is a real translation rather than a copy of its base',
    (code) => {
      const base = code.split('-')[0]!;
      const baseTable = table[base];
      if (!baseTable) return;
      // A regional table that matches its base word for word is either a
      // copy-paste that meant to be edited, or a decision that belongs in
      // SPLASH_INHERIT where a reader can see it. Two sets that come back
      // equal are the finding, not a coincidence.
      const same = Object.keys(table[code]!).filter((k) => table[code]![k] === baseTable[k]);
      expect(same.length, `${code} is identical to ${base} on every key`).toBeLessThan(
        Object.keys(baseTable).length,
      );
    },
  );

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
    '%s carries exactly the placeholders its English source carries',
    (code) => {
      const t = table[code]!;
      // Placeholders are code wearing the clothes of prose. A pass over these
      // strings can rename what is inside the braces, and the result is still
      // a well formed sentence, so review, lint, the build and a test that
      // only counts slots all stay quiet while the number stops appearing.
      // Compare the whole set, not the count and not just the one token we
      // happen to use today: a translation that invents {count} beside {n} is
      // as broken as one that drops {n}, and only the set catches both.
      for (const k of Object.keys(english)) {
        expect(placeholders(t[k]!), `${code}.${k} placeholders`).toEqual(
          placeholders(english[k]!),
        );
      }
    },
  );

  it.each(['ar', 'fa', 'he', 'ur'])(
    '%s keeps the log path reading left to right',
    (code) => {
      // A file path is a Latin run inside a right-to-left sentence, and the
      // punctuation around it is directionally neutral, so the leading dot of
      // ".openestimate" and the leading slash of a POSIX path take the
      // paragraph's direction and render on the wrong side of the name they
      // belong to. A left-to-right mark before the run settles it. These are
      // invisible, so a later pass over this table could drop them and nothing
      // on screen would say why the path had come apart.
      const t = table[code]!;
      expect(t.logFile, `${code}.logFile lost its mark`).toContain('\u200e{n}');
      expect(t.errLog, `${code}.errLog lost its mark`).toContain('\u200e.openestimate');
    },
  );
});
