import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * A bill of quantities is not called a BOQ outside the English speaking trade.
 * German construction calls it a Leistungsverzeichnis, LV for short; French
 * tendering writes DQE; Italian says computo metrico; Chinese, Japanese and
 * Korean each have their own settled word. Several of these locales had been
 * translated in more than one pass and ended up holding both the loan word and
 * the trade term, so the same object was named two ways on neighbouring
 * screens, and in French three ways.
 *
 * This gate holds each converted locale to one name. It checks four things per
 * locale. The loan word is gone except where it is genuinely right, and so is
 * the loan word spelled out: a locale that swapped BOQ for its own term and
 * left "Bill of Quantities" standing in the next sentence has moved the English
 * from three letters to three words rather than converted, and the abbreviation
 * check cannot see that at all. The rival
 * names the locale used to carry are gone too, because replacing only the loan
 * word would leave the file naming the object several ways in the reader's own
 * language. And the grammar the swap disturbs stays correct: gender and article
 * in German, particles in Korean, spacing in Chinese and Japanese, agreement in
 * Italian and French.
 *
 * Three uses of the abbreviation are correct and stay. The country exchange
 * modules name a format that really is called a BOQ in that market. A gloss
 * naming the local term with the English one in brackets helps a reader who
 * arrived from an English tender. And an i18next variable, an in-app route or a
 * backend source path is a name rather than prose, which is why those are
 * recognised by their shape and not by being lowercase: "das boq" in a German
 * sentence is still a defect.
 */

const LOCALES_DIR = ['src/app/locales', 'frontend/src/app/locales']
  .map((p) => resolve(process.cwd(), p))
  .find(existsSync);

/** `  "key": "value",` - the shape every line in a locale file has. */
const PAIR = /^\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$/;

/** Every spelling of the loan word, including the BoQ the sweeps kept missing. */
const BARE = /\b[Bb][Oo][Qq]s?\b/g;

/**
 * The same loan word spelled out. Kept separate from BARE rather than folded
 * into it, because one of the checks below reads BARE as "how often does this
 * file still say the abbreviation" and a ratio that counts two different
 * defects answers neither question.
 */
const ENGLISH_NAME = /\bbills?\s+of\s+quantit(?:y|ies)\b/gi;

/** The abbreviation is the artifact's own name in these markets. */
const EXCHANGE_MODULE = /^nav\.[a-z]+_boq_exchange$/;

type Entry = { key: string; value: string };

type ScopedRival = {
  /** The keys this rival is wrong in. Elsewhere the same word can be right. */
  keys: RegExp;
  pattern: RegExp;
  why: string;
};

type Locale = {
  code: string;
  /** Every spelling of what the locale calls the bill, abbreviation included. */
  names: RegExp;
  /** What the locale calls the bill now. */
  term: string;
  /** Spellings of that name which may stand in front of a bracketed gloss. */
  glossNames: string[];
  /** Names for the same object the locale is no longer allowed to use. */
  rivals: RegExp[];
  /**
   * How this locale writes the name of the country exchange format in prose.
   * The nav entries for it are recognised by their key, but a sentence that
   * mentions the same modules is recognised by this phrase or not at all.
   */
  exchangePhrase?: RegExp;
  /** Grammar the swap could disturb, one regexp per way it goes wrong. */
  grammar: RegExp[];
  /**
   * A word that is right elsewhere in the locale and wrong on these keys. The
   * German takeoff module called the bill a Kostengruppe, which is the DIN 276
   * cost group, and the DIN 276 module needs that word to keep saying it.
   */
  scopedRivals?: ScopedRival[];
};

/**
 * Keys a user meets on the way in. If the bill is named anywhere it is named
 * here, so these carry the whole rewrite in miniature and catch a locale that
 * names the object with some unrelated word rather than with the loan word.
 */
const HIGH_TRAFFIC = [
  'nav.boq',
  'modules.catalog.boq',
  'dashboard.open_boq',
  'takeoff.link_to_boq',
  'takeoff.step_add',
  'takeoff_viewer.link_to_boq_title',
];

/**
 * The adjectives the German locale actually puts in front of the bill, in the feminine
 * form a sweep would leave behind. German declines the adjective after whatever stands in
 * front of it, so "das bepreiste LV" is right and only a bare one, with no determiner, has
 * to move to the strong ending.
 */
const ADJECTIVE =
  'bepreiste|verknüpfte|vollständige|andere|erste|vorhandene|neue|leere|eigene|gesperrte|' +
  'importierte|aktuelle|gepreiste|ausgepreiste|strukturierte|echte|betroffene|gesamte|unbenannte';
/** A feminine determiner, or a bare feminine adjective, in front of the neuter LV. */
const GERMAN_FEMININE = new RegExp(
  `\\b(?:die|eine|einer|der|dieser|diese|ihrer|zur|keine)(?:\\s+[a-zäöüß]+(?:e|es|en|er))?\\s+LV(?![\\w-])` +
    `|(?:^|[;:,(\\-·]\\s*)(?:${ADJECTIVE})\\s+LV(?![\\w-])`,
  'i',
);

/**
 * Italian has no noun-noun apposition. A head noun reaches its complement through
 * a preposition, so "posizione del computo metrico" is the phrase and a bare
 * "posizione computo metrico" is an English compound swapped in word for word.
 */
const ITALIAN_BARE_APPOSITION = new RegExp(
  '\\b(?:posizion[ei]|editor|vo(?:ce|ci)|righ[ae]|dati|esportazione|importazione|griglia|' +
    'quantità|element[oi]|modello|struttura|tabella|totale|riepilogo)\\s+comput[oi]\\s+metric[io]',
  'i',
);

/**
 * Korean particles pick their form from the last syllable of the word in front.
 * 내역서 ends in an open syllable, so it takes 는, 가, 를, 와 and 로. The
 * consonant final forms after it mean the sweep moved a word without moving the
 * particle that agrees with it.
 */
const KOREAN_WRONG_PARTICLE = /내역서(?:을|이|은|과|으로)(?![가-힣])/;

/** Han, kana and full width punctuation. An ASCII space next to one is a leftover. */
const CJK = '[\\u3001-\\u30FF\\u3400-\\u4DBF\\u4E00-\\u9FFF\\uF900-\\uFAFF\\uFF01-\\uFF60]';
const spacedTerm = (term: string) => new RegExp(`${CJK} +${term}|${term} +${CJK}`);

const LOCALES: Locale[] = [
  {
    code: 'de',
    names: /\bLV\b|LV-|Leistungsverzeichnis/,
    term: 'LV',
    glossNames: ['Leistungsverzeichnis', 'LV'],
    rivals: [],
    grammar: [GERMAN_FEMININE],
    scopedRivals: [
      {
        keys: /^takeoff/,
        pattern: /Kostengruppe/,
        why: 'the takeoff links measurements to the bill, never to a DIN 276 cost group',
      },
      { keys: /^bim\.boq_export/, pattern: /Kostenschätzung/, why: 'the export is the bill, not a cost estimate' },
      {
        keys: /^schedule\.(quickstart|feature)_boq_desc$/,
        pattern: /Mengenaufstellung/,
        why: 'the schedule is built from the bill, and the bill has one name',
      },
    ],
  },
  {
    code: 'fr',
    names: /\bDQE\b|devis quantitatifs?/i,
    term: 'DQE',
    glossNames: ['devis quantitatifs?', 'DQE'],
    // No rival to name here. métré quantitatif once stood for the bill in this
    // locale and now stands only for the takeoff, which is what it means in the
    // trade, so the phrase itself cannot be banned. What is banned is any word
    // other than the ones above standing in front of a bracketed BOQ, and the
    // unexplained BOQ check is what catches that.
    rivals: [],
    // DQE is masculine and invariable, so a feminine article or an English
    // plural on it is the swap having moved the noun and not its agreement.
    grammar: [/\b(?:la|une|cette|ma|sa|votre nouvelle|de la) DQE\b/i, /\bDQEs\b/],
  },
  {
    code: 'it',
    names: /comput[oi] metric[io]/i,
    term: 'computo metrico',
    glossNames: ['computo metrico', 'computi metrici'],
    rivals: [],
    exchangePhrase: /scambio BOQ/i,
    // The noun is masculine and the adjective has to follow its number.
    grammar: [
      /\b(?:la|una|della|alla|nella|questa) computo metrico\b/i,
      /\bcomputo metrici\b/i,
      /\bcomputi metrico\b/i,
      ITALIAN_BARE_APPOSITION,
    ],
  },
  {
    code: 'zh',
    names: /工程量清单/,
    term: '工程量清单',
    glossNames: ['工程量清单'],
    rivals: [/工程清单/],
    grammar: [spacedTerm('工程量清单')],
  },
  {
    code: 'ja',
    names: /内訳書/,
    term: '内訳書',
    glossNames: ['内訳書'],
    // 見積 on its own is the estimate and the file needs it in a hundred
    // places; only 見積表, which had been naming the bill on the upload label,
    // is the rival.
    rivals: [/数量明細書/, /内訳明細書/, /数量内訳書/, /数量内訳(?!書)/, /積算書/, /見積表/],
    grammar: [spacedTerm('内訳書')],
  },
  {
    code: 'ko',
    names: /내역서/,
    term: '내역서',
    glossNames: ['내역서'],
    // 명세서 is a statement of anything and the locale needs it for the
    // payslip, the bill of materials and the progress claim. 수량 명세서 is one
    // syllable from the 물량 명세서 already listed, and it was the spelling the
    // file actually carried.
    rivals: [/물량\s*내역서/, /수량\s*내역서/, /물량\s*명세서/, /수량\s*명세서/],
    grammar: [KOREAN_WRONG_PARTICLE],
  },
];

function readLocale(code: string): Entry[] {
  const text = readFileSync(resolve(LOCALES_DIR as string, `${code}.ts`), 'utf8');
  const entries: Entry[] = [];
  for (const line of text.split('\n')) {
    const [, key, value] = PAIR.exec(line) ?? [];
    if (key !== undefined && value !== undefined) entries.push({ key, value });
  }
  return entries;
}

/** The locale's own name for the bill, standing at the end of the text given. */
function glossPattern(names: string[]): RegExp {
  return new RegExp(`(?:${names.join('|')})\\s*$`, 'i');
}

/**
 * Is this one occurrence of the loan word explained? Judged per occurrence and
 * by shape, so a value that carries a legitimate `{{boq}}` alongside a bare BOQ
 * in its prose still fails.
 */
function explained(value: string, at: number, length: number, gloss: RegExp, exchange?: RegExp): boolean {
  const before = value.slice(0, at);
  const after = value.slice(at + length);
  if (/\{\{\s*$/.test(before) || /^\s*\}\}/.test(after)) return true; // an i18next variable
  if (/\/$/.test(before) && value.slice(at, at + length) === 'boq') return true; // a route or a source path
  if (exchange && exchange.test(value.slice(Math.max(0, at - 20), at + length + 20))) return true;
  // A gloss is the local name, then a bracket holding nothing but the loan word.
  // The name has to touch its own bracket: reading back over whatever prose sits
  // nearby would let any sentence that glosses once carry bare BOQs afterwards.
  const open = /[(（]\s*$/.exec(before);
  if (!open || !/^\s*[)）]/.test(after)) return false;
  return gloss.test(before.slice(0, open.index));
}

function untranslated(
  entries: Entry[],
  locale: Pick<Locale, 'glossNames' | 'exchangePhrase'>,
  pattern: RegExp = BARE,
): Entry[] {
  const gloss = glossPattern(locale.glossNames);
  return entries.filter((entry) => {
    if (EXCHANGE_MODULE.test(entry.key)) return false;
    pattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(entry.value))) {
      if (!explained(entry.value, match.index, match[0].length, gloss, locale.exchangePhrase)) return true;
    }
    return false;
  });
}

describe('every converted locale names the bill the way its trade names it', () => {
  it('reads the locale directory at all', () => {
    expect(LOCALES_DIR).toBeDefined();
    expect(LOCALES.length).toBeGreaterThan(0);
  });

  for (const locale of LOCALES) {
    describe(locale.code, () => {
      const entries = readLocale(locale.code);

      it('reads the whole locale, so an empty finding means something', () => {
        expect(entries.length).toBeGreaterThan(30000);
      });

      it('leaves no unexplained BOQ', () => {
        const left = untranslated(entries, locale).map((entry) => `${entry.key}: ${entry.value}`);
        expect(left).toEqual([]);
      });

      it('leaves no unexplained English name for the bill', () => {
        const left = untranslated(entries, locale, ENGLISH_NAME).map((entry) => `${entry.key}: ${entry.value}`);
        expect(left).toEqual([]);
      });

      it('keeps the uses of the abbreviation that are correct', () => {
        const kept = entries.filter((entry) => {
          BARE.lastIndex = 0;
          return BARE.test(entry.value);
        });
        expect(kept.length).toBeGreaterThan(0);
      });

      it('actually says the trade term, in more places than it says BOQ', () => {
        const saysTerm = entries.filter((entry) => locale.names.test(entry.value)).length;
        const saysBoq = entries.filter((entry) => {
          BARE.lastIndex = 0;
          return BARE.test(entry.value);
        }).length;
        expect(saysTerm).toBeGreaterThan(saysBoq);
      });

      it('names the bill on the keys a user meets first', () => {
        const byKey = new Map(entries.map((entry) => [entry.key, entry.value]));
        const silent = HIGH_TRAFFIC.filter((key) => {
          const value = byKey.get(key);
          return value === undefined || !locale.names.test(value);
        }).map((key) => `${key}: ${byKey.get(key) ?? '<missing>'}`);
        expect(silent).toEqual([]);
      });

      it('does not call the bill by a word that belongs to another object', () => {
        for (const rival of locale.scopedRivals ?? []) {
          const found = entries
            .filter((entry) => rival.keys.test(entry.key) && rival.pattern.test(entry.value))
            .map((entry) => `${rival.why} :: ${entry.key}: ${entry.value}`);
          expect(found).toEqual([]);
        }
      });

      it('carries no rival name for the same object', () => {
        for (const rival of locale.rivals) {
          const found = entries
            .filter((entry) => rival.test(entry.value))
            .map((entry) => `${rival.source} :: ${entry.key}: ${entry.value}`);
          expect(found).toEqual([]);
        }
      });

      it('keeps the grammar the swap could have disturbed', () => {
        for (const wrong of locale.grammar) {
          const found = entries
            .filter((entry) => wrong.test(entry.value))
            .map((entry) => `${wrong.source} :: ${entry.key}: ${entry.value}`);
          expect(found).toEqual([]);
        }
      });
    });
  }
});

describe('the gate recognises the defective shapes and leaves the correct ones alone', () => {
  const german = { glossNames: ['Leistungsverzeichnis', 'LV'] };

  it('flags a bare loan word whatever its case', () => {
    const fixture: Entry[] = [
      { key: 'boq.title', value: 'Bepreiste BOQ' },
      { key: 'boq.list', value: 'Noch keine BOQs in diesem Projekt' },
      { key: 'match_wizard.tab_excel', value: 'Excel BoQ' },
      { key: 'boq.prose', value: 'Das boq ist leer' },
      { key: 'boq.ok', value: 'Bepreistes LV mit seinen LV-Positionen' },
    ];
    expect(untranslated(fixture, german).map((entry) => entry.key)).toEqual([
      'boq.title',
      'boq.list',
      'match_wizard.tab_excel',
      'boq.prose',
    ]);
  });

  it('leaves the three shapes that are names rather than prose', () => {
    const fixture: Entry[] = [
      { key: 'nav.uae_boq_exchange', value: 'UAE BOQ Exchange' },
      { key: 'changeorders.impact_boq_add', value: 'Fügt dem {{boq}} 1 neuen Abschnitt hinzu.' },
      { key: 'cases.editor.step_screen_invalid', value: 'Ein Bildschirm in der App, zum Beispiel /boq.' },
      { key: 'modules.dev_backend_ref', value: 'Referenz: backend/app/modules/boq/ und projects/.' },
      { key: 'takeoff.export', value: 'Senden Sie Messungen in Ihr Leistungsverzeichnis (BOQ).' },
    ];
    expect(untranslated(fixture, german)).toEqual([]);
  });

  it('leaves the exchange format where prose names it, and only there', () => {
    const italian = { glossNames: ['computo metrico'], exchangePhrase: /scambio BOQ/i };
    const fixture: Entry[] = [
      { key: 'modules.ex', value: 'es. una UI regionale di scambio BOQ o un report specifico' },
      { key: 'boq.open', value: 'Apri il BOQ valorizzato' },
    ];
    expect(untranslated(fixture, italian).map((entry) => entry.key)).toEqual(['boq.open']);
    // Without the phrase the same locale has no excuse for either of them.
    expect(untranslated(fixture, { glossNames: ['computo metrico'] }).map((entry) => entry.key)).toEqual([
      'modules.ex',
      'boq.open',
    ]);
  });

  it('still flags prose that hides behind a legitimate variable in the same string', () => {
    const fixture: Entry[] = [{ key: 'boq.mixed', value: 'Fügt dem {{boq}} hinzu, das BOQ bleibt leer.' }];
    expect(untranslated(fixture, german).map((entry) => entry.key)).toEqual(['boq.mixed']);
  });

  it('accepts the gloss in each locale that carries one', () => {
    expect(untranslated([{ key: 'k', value: 'Un devis quantitatif (BOQ) est une liste.' }], { glossNames: ['devis quantitatifs?', 'DQE'] })).toEqual([]);
    expect(untranslated([{ key: 'k', value: '工程量清单（BOQ）是项目的全部工作。' }], { glossNames: ['工程量清单'] })).toEqual([]);
    expect(untranslated([{ key: 'k', value: '내역서 (BOQ)' }], { glossNames: ['내역서'] })).toEqual([]);
    // A gloss behind a different word is not this locale's gloss.
    expect(
      untranslated([{ key: 'k', value: 'Un métré quantitatif (BOQ) est une liste.' }], { glossNames: ['devis quantitatifs?', 'DQE'] }).map(
        (e) => e.key,
      ),
    ).toEqual(['k']);
  });

  it('still flags prose that hides behind a legitimate gloss in the same string', () => {
    // The gloss excuses its own bracket and nothing further along the sentence.
    const fixture: Entry[] = [
      { key: 'ja.mixed', value: '内訳書（BOQ）を開き、BOQを選択' },
      { key: 'ja.ok', value: '内訳書（BOQ）を開く' },
    ];
    expect(untranslated(fixture, { glossNames: ['内訳書'] }).map((entry) => entry.key)).toEqual(['ja.mixed']);
    // Nor does a bracket excuse a name that is not standing against it.
    expect(
      untranslated([{ key: 'k', value: 'Das Leistungsverzeichnis der Wohnung (BOQ 3)' }], german).map((e) => e.key),
    ).toEqual(['k']);
  });

  it('flags the Italian compound that lost its preposition', () => {
    expect(ITALIAN_BARE_APPOSITION.test('Posizione computo metrico')).toBe(true);
    expect(ITALIAN_BARE_APPOSITION.test('Esportazione computo metrico in Excel')).toBe(true);
    expect(ITALIAN_BARE_APPOSITION.test('Posizione del computo metrico')).toBe(false);
    expect(ITALIAN_BARE_APPOSITION.test('Voci di computo metrico')).toBe(false);
    expect(ITALIAN_BARE_APPOSITION.test('Nuovo computo metrico')).toBe(false);
  });

  it('scopes the German cost group to the module the word belongs to', () => {
    const german = LOCALES.find((l) => l.code === 'de') as Locale;
    const takeoff = german.scopedRivals?.[0] as ScopedRival;
    const wrong = { key: 'takeoff.link_to_boq', value: 'Mit Kostengruppe verknüpfen' };
    const right = { key: 'takeoff.link_to_boq', value: 'Mit LV verknüpfen' };
    // The same word on a DIN 276 key is the cost group and has to survive.
    const din = { key: 'costmodel.exposure_col_group', value: 'Kostengruppe' };
    expect(takeoff.keys.test(wrong.key) && takeoff.pattern.test(wrong.value)).toBe(true);
    expect(takeoff.keys.test(right.key) && takeoff.pattern.test(right.value)).toBe(false);
    expect(takeoff.keys.test(din.key)).toBe(false);
  });

  it('sees a locale that names the bill with some unrelated word on a high traffic key', () => {
    const french = LOCALES.find((l) => l.code === 'fr') as Locale;
    // métré is the takeoff, so a dashboard button carrying it names the wrong thing.
    expect(french.names.test('Ouvrir le métré')).toBe(false);
    expect(french.names.test('Ouvrir le DQE')).toBe(true);
    expect(french.names.test('Ouvrir le devis quantitatif')).toBe(true);
    const german = LOCALES.find((l) => l.code === 'de') as Locale;
    expect(german.names.test('Zur Kostengruppe hinzufügen')).toBe(false);
    expect(german.names.test('Zum LV hinzufügen')).toBe(true);
    expect(german.names.test('Mit LV-Position verknüpfen')).toBe(true);
    expect(german.names.test('Leistungsverzeichnis')).toBe(true);
  });

  it('sees a feminine article in front of the neuter German LV', () => {
    expect(GERMAN_FEMININE.test('Material aus der LV beschaffen')).toBe(true);
    expect(GERMAN_FEMININE.test('Eine vollständige LV erzeugen')).toBe(true);
    // The article survived a sweep that moved only the adjective, which is the
    // half-finished shape and has to be caught as well.
    expect(GERMAN_FEMININE.test('Eine vollständiges LV erzeugen')).toBe(true);
    expect(GERMAN_FEMININE.test('Zur LV hinzufügen')).toBe(true);
    expect(GERMAN_FEMININE.test('Vollständige LV im Export')).toBe(true);
    expect(GERMAN_FEMININE.test('Ein vollständiges LV erzeugen')).toBe(false);
    // Weak after a definite article, so this one is already right.
    expect(GERMAN_FEMININE.test('Sehen das bepreiste LV, nicht die Margen')).toBe(false);
    expect(GERMAN_FEMININE.test('eine Menge, die vom LV abweicht')).toBe(false);
    expect(GERMAN_FEMININE.test('Keine LVs in diesem Projekt')).toBe(false);
    expect(GERMAN_FEMININE.test('Nicht mit einer LV-Position verknüpft')).toBe(false);
  });

  it('sees a Korean particle that did not follow the noun', () => {
    expect(KOREAN_WRONG_PARTICLE.test('내역서을 여세요')).toBe(true);
    expect(KOREAN_WRONG_PARTICLE.test('내역서이 비어 있습니다')).toBe(true);
    expect(KOREAN_WRONG_PARTICLE.test('내역서으로 보내기')).toBe(true);
    expect(KOREAN_WRONG_PARTICLE.test('내역서를 여세요')).toBe(false);
    expect(KOREAN_WRONG_PARTICLE.test('내역서가 비어 있습니다')).toBe(false);
    expect(KOREAN_WRONG_PARTICLE.test('내역서로 보내기')).toBe(false);
    expect(KOREAN_WRONG_PARTICLE.test('내역서 항목에 추가')).toBe(false);
  });

  it('sees an ASCII space left between the term and the script around it', () => {
    const spaced = spacedTerm('工程量清单');
    expect(spaced.test('打开 工程量清单')).toBe(true);
    expect(spaced.test('工程量清单 编辑器')).toBe(true);
    expect(spaced.test('打开工程量清单编辑器')).toBe(false);
    // A separator or a Latin neighbour keeps its spaces.
    expect(spaced.test('{{n}} 个工程量清单 · 多货币')).toBe(false);
    expect(spaced.test('BIM → 工程量清单')).toBe(false);
  });

  it('sees Italian and French agreement that did not move with the noun', () => {
    const italian = LOCALES.find((l) => l.code === 'it') as Locale;
    const french = LOCALES.find((l) => l.code === 'fr') as Locale;
    const hits = (locale: Locale, value: string) => locale.grammar.some((rx) => rx.test(value));
    expect(hits(italian, 'Apri la computo metrico')).toBe(true);
    expect(hits(italian, 'Tutti i computo metrici')).toBe(true);
    expect(hits(italian, 'Tutti i computi metrico')).toBe(true);
    expect(hits(italian, 'Apri il computo metrico')).toBe(false);
    expect(hits(italian, 'Tutti i computi metrici')).toBe(false);
    expect(hits(italian, 'Posizione del computo metrico')).toBe(false);
    expect(hits(french, 'Ouvrir la DQE')).toBe(true);
    expect(hits(french, 'Charger les DQEs')).toBe(true);
    expect(hits(french, 'Ouvrir le DQE')).toBe(false);
    expect(hits(french, 'Tous les DQE de ce projet')).toBe(false);
    expect(hits(french, 'Position du DQE')).toBe(false);
  });

  it('sees the loan word spelled out, where the abbreviation check is blind', () => {
    const italian = { glossNames: ['computo metrico'] };
    const fixture: Entry[] = [
      { key: 'excel_label', value: 'Carica un Bill of Quantities .xlsx' },
      { key: 'one', value: 'Un bill of quantity per opzione' },
      { key: 'plural', value: 'Tutti i Bills of Quantities del progetto' },
      { key: 'ok', value: 'Carica un computo metrico .xlsx' },
      { key: 'gloss', value: 'Il computo metrico (Bill of Quantities) elenca le lavorazioni.' },
    ];
    expect(untranslated(fixture, italian, ENGLISH_NAME).map((entry) => entry.key)).toEqual(['excel_label', 'one', 'plural']);
    // None of them says BOQ, which is the whole reason this pattern exists.
    expect(untranslated(fixture, italian)).toEqual([]);
  });

  it('sees the second local name each of these two locales had kept', () => {
    const japanese = LOCALES.find((l) => l.code === 'ja') as Locale;
    const korean = LOCALES.find((l) => l.code === 'ko') as Locale;
    const hits = (locale: Locale, value: string) => locale.rivals.some((rx) => rx.test(value));
    expect(hits(japanese, '.xlsxの見積表をアップロード')).toBe(true);
    expect(hits(japanese, '.xlsxの内訳書をアップロード')).toBe(false);
    // The estimate keeps its own word.
    expect(hits(japanese, '見積もりを作成')).toBe(false);
    expect(hits(korean, '.xlsx 수량 명세서 업로드')).toBe(true);
    expect(hits(korean, '.xlsx 수량명세서 업로드')).toBe(true);
    expect(hits(korean, '.xlsx 내역서 업로드')).toBe(false);
    // 수량 alone is the quantity, and 명세서 belongs to the payslip and the
    // bill of materials. Neither may be caught by the pair.
    expect(hits(korean, '모델 수량 (면적, 부피, 길이, 무게)')).toBe(false);
    expect(hits(korean, '급여 명세서')).toBe(false);
    expect(hits(korean, '자재명세서')).toBe(false);
  });
});
