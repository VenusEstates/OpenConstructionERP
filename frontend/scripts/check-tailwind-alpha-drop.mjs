/**
 * Find colour classes that are in the source and absent from the stylesheet.
 *
 * Two shapes, one silence. Both leave the class sitting in the file, reading to
 * every reviewer as though it paints something, while no rule for it is ever
 * emitted and no build, type check, test or lint has anything to say.
 *
 *   1. A real token carrying a modifier it cannot take, `bg-oe-blue-subtle/60`.
 *   2. A name that resolves to nothing at all, `bg-surface-muted`. The theme
 *      defines surface as primary, secondary, tertiary and elevated, and there
 *      is no muted. This one needs no cleverness to write, only a plausible
 *      word, so it is the one a newcomer adds. See `deadNames`.
 *
 * Tailwind resolves `<utility>-<token>/<alpha>` by handing the token's value to
 * `withAlphaValue`. A function-valued token is called with the alpha. A string
 * token is run through `parseColor`, and when the string is a bare `var(--x)`
 * reference there is nothing to parse, so THE WHOLE DECLARATION IS DROPPED.
 * Not warned about, not approximated, dropped. The element renders with no
 * fill, no border colour, no gradient stop, while the source reads as though it
 * has one and every gate stays green, because the class is present in the file
 * and is simply absent from the stylesheet.
 *
 * The drop-set is DERIVED BY COMPILING, never by reading the config. It is
 * tempting to read `tailwind.config.js` and call every plain string suspect,
 * and that is wrong in both directions: every stock Tailwind colour is a plain
 * hex string and takes alpha perfectly well, while a project token written as
 * `'var(--oe-border)'` is the same shape and does not. The property that
 * decides is whether the string parses as a colour, and the only thing that
 * knows is Tailwind. So this compiles the real config over a synthetic probe of
 * every resolved colour token against every colour utility, reads back which
 * alpha classes came out, and takes the absentees as the drop-set. Deriving it
 * fresh every run is the point: a token added later is covered without editing
 * this file, a token that gets fixed leaves the set on its own, and there is no
 * hard-coded list to go stale and no exact-set ratchet to break the first time
 * somebody adds a colour.
 *
 * A legitimate alpha use therefore cannot be reported. `bg-oe-blue/10` emits
 * CSS, so `oe-blue` is not in the drop-set, so nothing that names it is looked
 * at. The false positive is not filtered out, it is unconstructable.
 *
 * IT REPORTS AND DOES NOT BLOCK ON FINDINGS. Measured at 79ea78bc7: 536
 * alpha-modifier occurrences in 201 files across 13 tokens, and 579 classes in
 * 124 files naming a token the theme never defined. Of the alpha half, 267
 * render nothing in either theme and 198 more are borders falling back to the
 * preflight `#e5e7eb`, a near-white rule on every dark surface. Making that
 * fail today would paint every lane red over a gap that predates the
 * instrument, and a lane that is red for a week is a lane somebody switches
 * off. The count is printed first and closed second. `--strict` turns findings
 * into a non-zero exit and belongs here the day the count reaches zero.
 *
 * Every count in this file names the commit it was taken at, because they move
 * under you. The dead-name half read 769 in 156 files at acd3bad9c and 579 in
 * 124 today; the difference is 190, which is the 138 classes repaired in
 * 8a0aa6b99 plus the 52 in 79ea78bc7. A figure with no commit against it is one
 * a reader cannot tell from a wrong one.
 *
 * IT DOES BLOCK ON BEING BROKEN OR STALE, so do not read the paragraph above as
 * "this cannot fail". A probe that will not compile, a config that resolves to
 * no colours, a run where no utility emits anything, a run where every token
 * looks broken, or a sweep that collects fewer files than the tree has, are all
 * the instrument blind rather than the tree clean, and each exits non-zero. The
 * last one is the one worth having: point this at the wrong directory, or let
 * the content globs drift, and it would otherwise report zero findings in a
 * confident voice.
 *
 * Usage:
 *   node scripts/check-tailwind-alpha-drop.mjs            # report, exit 0
 *   node scripts/check-tailwind-alpha-drop.mjs --strict   # findings fail
 *   node scripts/check-tailwind-alpha-drop.mjs --selftest # prove it can fail
 *   node scripts/check-tailwind-alpha-drop.mjs --list     # every occurrence
 */

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, '..');

// Every Tailwind 3 utility that takes a colour and therefore accepts an alpha
// modifier. Longest first, so `border-t` is tried before `border` and the token
// of `border-t-border-light/60` does not come out as `t-border-light`.
//
// A name that leaves Tailwind does not silently shrink the sweep: a prefix
// counts only once the probe proves it emits, the ones that do not are printed,
// and USABLE_PREFIX_FLOOR below refuses the run if too few survive.
const CANDIDATE_PREFIXES = [
  'ring-offset',
  'placeholder',
  'decoration',
  'border-t',
  'border-r',
  'border-b',
  'border-l',
  'border-x',
  'border-y',
  'border-s',
  'border-e',
  'outline',
  'divide',
  'shadow',
  'stroke',
  'accent',
  'border',
  'caret',
  'fill',
  'from',
  'ring',
  'text',
  'via',
  'bg',
  'to',
];

// Floors, all set well under what the tree measures so ordinary growth never
// trips them and a collapse always does.
const COLOUR_TOKEN_FLOOR = 100; // resolved colour tokens; the tree has ~280
const USABLE_PREFIX_FLOOR = 10; // prefixes that emit; the tree has 25
const CONTENT_FILE_FLOOR = 1000; // files the content globs collect; ~2900 today

const ALPHA = String.raw`\[[^\]\s"'\x60]*\]|\d+(?:\.\d+)?%?`;

function fail(message, detail) {
  console.error(`\n${message}`);
  if (detail) console.error(detail);
  process.exit(1);
}

/** `{a,b}` `**` `*` `?` are all Tailwind ever puts in a content glob. */
function globToRegExp(glob) {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === '*') {
      if (glob[i + 1] === '*') {
        i++;
        if (glob[i + 1] === '/') i++;
        out += '(?:.*/)?';
      } else {
        out += '[^/]*';
      }
    } else if (c === '?') out += '[^/]';
    else if (c === '{') {
      const end = glob.indexOf('}', i);
      if (end === -1) out += '\\{';
      else {
        out += `(?:${glob
          .slice(i + 1, end)
          .split(',')
          .map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
          .join('|')})`;
        i = end;
      }
    } else out += c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${out}$`);
}

function walk(dir, acc) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return acc;
  }
  for (const e of entries) {
    if (e.name === 'node_modules' || e.name === '.git' || e.name === 'dist') continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, acc);
    else if (e.isFile()) acc.push(full);
  }
  return acc;
}

/**
 * The content globs are read off the config rather than written here, so that
 * pointing Tailwind at a new directory brings that directory into this sweep in
 * the same commit rather than in whichever later one notices.
 */
function contentFiles(config) {
  const raw = Array.isArray(config.content) ? config.content : (config.content?.files ?? []);
  const globs = raw.filter((g) => typeof g === 'string');
  if (globs.length === 0) fail('The config declares no string content globs, so there is nothing to sweep.');
  const matchers = globs.map((g) => globToRegExp(g.replace(/^\.\//, '')));
  const all = walk(FRONTEND, []);
  return all.filter((f) => {
    const rel = path.relative(FRONTEND, f).replace(/\\/g, '/');
    return matchers.some((m) => m.test(rel));
  });
}

/** Every class name in a compiled selector, unescaped. Stops at `:` so that
 *  `.placeholder-x\/50::placeholder` yields `placeholder-x/50`, not the two
 *  glued together — reading the pseudo-element as part of the name is how an
 *  earlier version of this decided a working utility was dead. */
function classNamesIn(selector) {
  const out = [];
  for (const m of selector.matchAll(/\.((?:[^\s.,:>+~()[\]{}\\]|\\.)+)/g)) {
    out.push(m[1].replace(/\\(.)/g, '$1'));
  }
  return out;
}

function flattenColours(colors, prefix = '', out = {}) {
  for (const [key, value] of Object.entries(colors ?? {})) {
    const name = key === 'DEFAULT' ? prefix.replace(/-$/, '') : `${prefix}${key}`;
    if (!name) continue;
    if (value && typeof value === 'object' && typeof value !== 'function') flattenColours(value, `${name}-`, out);
    else out[name] = value;
  }
  return out;
}

/** Compile a list of class names through the real config and return the ones
 *  that produced a rule. The single source of truth for "does this class exist",
 *  used both to derive the drop-set and to confirm a finding before printing it. */
async function emittedFor(classes, config) {
  const [{ default: postcss }, { default: tailwindcss }] = await Promise.all([import('postcss'), import('tailwindcss')]);
  let css;
  try {
    const result = await postcss([
      tailwindcss({
        ...config,
        // The sentinel is not decoration. The verification pass is fed a list of
        // classes that are all expected to emit nothing, and on a batch where
        // none of them does, Tailwind prints "No utility classes were detected
        // in your source files ... double-check the `content` option". In a
        // gate's output that reads as the instrument being misconfigured, which
        // is the one thing this must never say when it is not true. One class
        // that always emits keeps the warning off. Nothing reads it back.
        content: [{ raw: `${classes.join(' ')} sr-only`, extension: 'html' }],
        corePlugins: { preflight: false },
      }),
    ]).process('@tailwind utilities;', { from: undefined });
    css = result.css;
  } catch (error) {
    fail('The probe stylesheet did not compile, so nothing below was measured.', String(error?.stack ?? error));
  }
  const emitted = new Set();
  for (const m of css.matchAll(/(^|})([^{}]+)\{/g)) for (const c of classNamesIn(m[2])) emitted.add(c);
  return emitted;
}

async function deriveDropSet() {
  const { default: resolveConfig } = await import('tailwindcss/resolveConfig.js');
  const { default: config } = await import(`file://${path.join(FRONTEND, 'tailwind.config.js')}`);

  const tokens = Object.keys(flattenColours(resolveConfig(config).theme.colors)).sort();
  if (tokens.length < COLOUR_TOKEN_FLOOR) {
    fail(
      `The config resolved to ${tokens.length} colour tokens, under the floor of ${COLOUR_TOKEN_FLOOR}.`,
      'A theme this small means the config did not load the way this expects. Every answer below\n' +
        'is drawn from that list, so a short list is a blind instrument, not a small project.',
    );
  }

  // The probe. Both forms of every token against every candidate utility: the
  // plain one to prove the token works at all, the `/50` one to ask the actual
  // question. Class names only, no markup — Tailwind's extractor takes them
  // straight out of the raw string.
  const probe = [];
  for (const p of CANDIDATE_PREFIXES) for (const t of tokens) probe.push(`${p}-${t}`, `${p}-${t}/50`);

  const emitted = await emittedFor(probe, config);
  if (emitted.size === 0) {
    fail(
      'The probe compiled but emitted no classes at all.',
      'That is this script failing to feed Tailwind its own content, not a clean tree.',
    );
  }

  // A prefix earns its place by emitting for SOME token, rather than by being
  // checked against one token named here. Naming one would make a rename of
  // that token look like a rename of the utility.
  const usable = CANDIDATE_PREFIXES.filter((p) => tokens.some((t) => emitted.has(`${p}-${t}/50`)));
  const dead = CANDIDATE_PREFIXES.filter((p) => !usable.includes(p));
  if (usable.length < USABLE_PREFIX_FLOOR) {
    fail(
      `Only ${usable.length} of ${CANDIDATE_PREFIXES.length} utilities emitted an alpha class, under the floor of ${USABLE_PREFIX_FLOOR}.`,
      `Not usable: ${dead.join(', ') || '(none)'}\n` +
        'Every token is judged by whether it emits through these. If the utilities themselves are\n' +
        'not emitting, every token looks broken and the drop-set below is meaningless.',
    );
  }

  const dropping = [];
  const capable = [];
  for (const t of tokens) {
    const plain = usable.some((p) => emitted.has(`${p}-${t}`));
    const alpha = usable.some((p) => emitted.has(`${p}-${t}/50`));
    if (!plain) continue; // not a real colour token; says nothing either way
    (alpha ? capable : dropping).push(t);
  }
  if (capable.length === 0) {
    fail(
      'Not one colour token accepted an alpha modifier.',
      'A config where nothing works is this probe being built wrong. Without a token that is known\n' +
        'good there is no evidence the negative results mean anything.',
    );
  }
  return { tokens: new Set(tokens), dropping, capable, dead, config };
}

function sweep(files, dropSet, knownTokens, usablePrefixes) {
  const byLen = (a, b) => b.length - a.length || a.localeCompare(b);
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const P = usablePrefixes.slice().sort(byLen).map(esc).join('|');
  const known = new RegExp(
    String.raw`(?<![\w-])(${P})-(${[...dropSet].sort(byLen).map(esc).join('|')})/(${ALPHA})`,
    'g',
  );
  // The stricter half's alpha-carrying slice. `deadNames` below covers the rest.
  //
  // The match here is a CANDIDATE, never a finding. `text-sm/6` is a real
  // utility — a font size with a line height — and `sm` is not a colour, so a
  // name-shaped test alone would report a working class. Every candidate is
  // compiled in `verifyUnknown` before anything is printed, and only the ones
  // that genuinely emit no rule survive. Nothing about which names are colours
  // is written down here; the compiler is asked.
  const any = new RegExp(String.raw`(?<![\w-])(${P})-([a-z][a-z0-9]*(?:-[a-z0-9]+)*)/(${ALPHA})`, 'g');

  const drops = [];
  const unknown = [];
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    if (!text.includes('/')) continue;
    const rel = path.relative(FRONTEND, file).replace(/\\/g, '/');
    const lineOf = (idx) => text.slice(0, idx).split('\n').length;
    for (const m of text.matchAll(known)) {
      drops.push({ file: rel, line: lineOf(m.index), cls: m[0], token: m[2] });
    }
    for (const m of text.matchAll(any)) {
      const token = m[2];
      if (knownTokens.has(token) || dropSet.has(token)) continue;
      unknown.push({ file: rel, line: lineOf(m.index), cls: m[0], token });
    }
  }
  return { drops, unknown };
}

/**
 * The other shape of the same silence: a class whose NAME resolves to nothing.
 * `bg-surface-muted` is not a token carrying a modifier it cannot take, it is a
 * word nobody ever defined. It needs no cleverness to write, only a plausible
 * word, so it is the one a newcomer introduces.
 *
 * Dropping the alpha requirement opens a false-positive source the alpha form
 * structurally could not have, and every case has the same cause: a string that
 * is not a class list. `stroke-width` and `border-color` are attribute names.
 * `to-do` and `to-ako` are English and Croatian prose in the locale bundles,
 * caught because `to-` is the gradient utility and also a word.
 *
 * Two filters, neither of them a list of words to ignore:
 *
 *   1. Shape. A class list is lowercase and is not a sentence. Prose, UI copy
 *      and identifiers break that; `rounded bg-surface-muted px-1.5` does not.
 *      Measured at acd3bad9c, against the two populations that were largest
 *      then: it kept 42 of 42 `surface-muted` and 102 of 102 `border-subtle`
 *      while removing 80% of the noise, so it was not buying precision with
 *      recall. Both of those are repaired now and the check is not repeatable
 *      against them; re-derive it against whatever the largest populations are
 *      on the day, and expect a crude grep to disagree with this script in both
 *      directions, because the grep is the cruder instrument.
 *   2. The compiler. Every survivor is compiled and only the ones that produce
 *      no rule at all are reported, which is what makes a legitimate class
 *      unreportable rather than merely unreported.
 *
 * A residual remains, around 1%, where a lowercase attribute name sits in a
 * string of its own. That is why this reports rather than blocks.
 */
const CLASS_STRINGS = /'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g;
const looksLikeProse = (s) =>
  /[A-Z]/.test(s) || /[?!;,]/.test(s) || s.includes('{{') || /\b(a|the|to|of|is|and|or)\b \b/i.test(s);

async function deadNames(files, usablePrefixes, config, themeTokens) {
  const P = usablePrefixes.slice().sort((a, b) => b.length - a.length || a.localeCompare(b)).join('|');
  // Not followed by `:` — an attribute name always is, a class never is, since
  // Tailwind variants put the colon BEFORE the utility.
  const RE = new RegExp(
    String.raw`(?<![\w-])((?:[a-z0-9-]+:)*)(${P})-([a-z][a-z0-9]*(?:-[a-z0-9]+)*)(/(?:\[[^\]\s]*\]|\d+(?:\.\d+)?%?))?(?![\w:-])`,
    'g',
  );
  const occurrences = [];
  const vocabulary = new Set();
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    const rel = path.relative(FRONTEND, file).replace(/\\/g, '/');
    for (const sm of text.matchAll(CLASS_STRINGS)) {
      const body = sm[0].slice(1, -1);
      if (!/[a-z]-[a-z]/.test(body) || looksLikeProse(body)) continue;
      const found = [...body.matchAll(RE)];
      if (!found.length) continue;
      for (const w of body.split(/[\s\\]+/)) if (w) vocabulary.add(w.replace(/^(?:[a-z0-9-]+:)*/, ''));
      for (const m of found) {
        // The MATCH's own line, not the string literal's first line. A template
        // literal spanning ten lines would otherwise be numbered from its start
        // here and from the match there, and the two halves of this guard would
        // fail to recognise the same occurrence and report it twice.
        const line = text.slice(0, sm.index + 1 + m.index).split('\n').length;
        occurrences.push({ file: rel, line, cls: m[0], bare: `${m[2]}-${m[3]}${m[4] ?? ''}`, token: m[3] });
      }
    }
  }
  if (occurrences.length === 0) return [];

  const probe = [...new Set([...vocabulary, ...occurrences.map((o) => o.bare)])].filter(
    (s) => s.length < 60 && /^[\w[\]/.,#%()-]+$/.test(s),
  );
  const emitted = await emittedFor(probe, config);
  // A class written by hand in the stylesheet is not dead either.
  const hand = new Set();
  for (const m of fs.readFileSync(path.join(FRONTEND, 'src/index.css'), 'utf8').matchAll(/\.((?:[\w-]|\\.)+)/g))
    hand.add(m[1].replace(/\\(.)/g, '$1'));

  // Only names the theme has never heard of. A class on a REAL token that emits
  // nothing is the alpha-drop defect and belongs to the other half; reporting it
  // here would both double-count it and put it under a heading that is false
  // about it. Deduplicating on file and line cannot do this job, because the two
  // halves number a multi-line template literal from different points.
  return occurrences.filter((o) => !themeTokens.has(o.token) && !emitted.has(o.bare) && !hand.has(o.bare));
}

/** One occurrence reported once, however many passes found it. */
function mergeFindings(...lists) {
  const seen = new Set();
  const out = [];
  for (const list of lists)
    for (const r of list) {
      const key = `${r.file}:${r.line}:${r.cls}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(r);
    }
  return out;
}

/** Keep only the candidates that really emit nothing. See the note on `any`. */
async function verifyUnknown(candidates, config) {
  if (candidates.length === 0) return [];
  const emitted = await emittedFor([...new Set(candidates.map((r) => r.cls))], config);
  return candidates.filter((r) => !emitted.has(r.cls));
}

function tally(rows, key) {
  const out = new Map();
  for (const r of rows) out.set(r[key], (out.get(r[key]) ?? 0) + 1);
  return [...out].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

async function selftest() {
  // A negative control has to bite somewhere only the defect could reach. The
  // question is not "does it report something", it is "does it separate the two
  // kinds of class", so this asks for both answers at once and demands they
  // differ: an alpha on a token the real config drops, and an alpha on one the
  // real config supports, in the same file.
  const { tokens, dropping, capable, dead, config } = await deriveDropSet();
  if (dropping.length === 0) {
    console.log('selftest: no token in this config drops its alpha, so the defect this guards cannot be staged.');
    return 0;
  }
  const bad = dropping[0];
  // A control that is only "some token that works" would pass while testing
  // nothing, since most of the 255 are stock Tailwind hexes. Prefer one of this
  // project's own function-form tokens, which is the shape the fix would use.
  const good = capable.find((t) => t.startsWith('oe-') || t.startsWith('surface-')) ?? capable[0];
  const dir = fs.mkdtempSync(path.join(FRONTEND, '.alpha-drop-selftest-'));
  try {
    // Six classes that a name-shaped test cannot tell apart, and six different
    // right answers. "It reported something" is not evidence for a guard whose
    // whole job is to separate cases that look identical in source.
    const file = path.join(dir, 'probe.tsx');
    fs.writeFileSync(
      file,
      `export const A = () => <i className="bg-${bad}/40 bg-${good}/40 bg-nonesuch-token/40 text-sm/6" />;\n` +
        `export const B = () => <i className="rounded bg-nonesuch-plain px-2" />;\n` +
        `export const C = () => <i title="Add a to-do item to the list" />;\n`,
    );
    const usable = CANDIDATE_PREFIXES.filter((p) => !dead.includes(p));
    const swept = sweep([file], new Set(dropping), tokens, usable);
    const drops = swept.drops;
    const withAlpha = await verifyUnknown(swept.unknown, config);
    const names = mergeFindings(withAlpha, await deadNames([file], usable, config, tokens));

    const checks = [
      [`bg-${bad}/40`, 'a real token that cannot take an alpha', drops.filter((r) => r.token === bad).length, 1],
      [`bg-${good}/40`, 'a real token that CAN take an alpha', drops.filter((r) => r.token === good).length, 0],
      ['bg-nonesuch-token/40', 'a name that does not exist, with alpha', names.filter((r) => r.token === 'nonesuch-token').length, 1],
      ['bg-nonesuch-plain', 'a name that does not exist, NO alpha', names.filter((r) => r.token === 'nonesuch-plain').length, 1],
      ['text-sm/6', 'a real utility whose token is not a colour', names.filter((r) => r.cls === 'text-sm/6').length, 0],
      ['to-do (in prose)', 'an English word that looks like a utility', names.filter((r) => r.token === 'do').length, 0],
    ];
    let bad_ = 0;
    for (const [cls, why, got, want] of checks) {
      if (got !== want) bad_++;
      console.log(`  ${got === want ? 'ok  ' : 'FAIL'}  ${cls.padEnd(24)} -> ${got} finding(s), want ${want}   ${why}`);
    }
    if (bad_) {
      console.error(`\n${bad_} check(s) failed. The guard does not separate these cases, so its silence means nothing.`);
      return 1;
    }
    console.log(
      '\nselftest passed: six classes that look alike in source, three findings and three not.',
    );
    return 0;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

async function main(argv) {
  if (argv.includes('--selftest')) return selftest();
  const strict = argv.includes('--strict');
  const list = argv.includes('--list');

  const { tokens, dropping, capable, dead, config } = await deriveDropSet();
  const usable = CANDIDATE_PREFIXES.filter((p) => !dead.includes(p));

  const files = contentFiles(config);
  if (files.length < CONTENT_FILE_FLOOR) {
    fail(
      `The content globs collected ${files.length} files, under the floor of ${CONTENT_FILE_FLOOR}.`,
      'Zero findings over a handful of files is not a clean tree, it is a sweep that missed it. Either\n' +
        'this is pointed at the wrong directory or the globs in tailwind.config.js no longer describe\n' +
        'the source, and both are worth more than the report that would have followed.',
    );
  }

  const swept = sweep(files, new Set(dropping), tokens, usable);
  const drops = swept.drops;
  const withAlpha = await verifyUnknown(swept.unknown, config);
  // Both halves can see the same occurrence: `bg-surface-muted/40` is a dead
  // name AND carries an alpha. Merge on file, line and class so it is one
  // finding rather than two. The selftest fails on exactly this.
  const unknown = mergeFindings(withAlpha, await deadNames(files, usable, config, tokens));

  console.log(
    `Tailwind alpha-modifier drop: ${tokens.size} colour tokens resolved, ` +
      `${capable.length} take an alpha modifier, ${dropping.length} do not.`,
  );
  console.log(`Swept ${files.length} files through ${usable.length} colour utilities.`);
  if (dead.length) console.log(`Utilities that emitted nothing and were skipped: ${dead.join(', ')}`);

  console.log(`\n${dropping.length} token(s) drop the whole declaration when given an alpha modifier:`);
  const used = new Map(tally(drops, 'token'));
  for (const t of dropping) {
    const n = used.get(t) ?? 0;
    console.log(`  ${t}${n ? `  -- ${n} occurrence(s) in the tree` : ''}`);
  }

  if (drops.length) {
    const files_ = new Set(drops.map((r) => r.file));
    console.log(`\n${drops.length} occurrence(s) in ${files_.size} file(s) write an alpha on one of them:`);
    for (const [token, n] of tally(drops, 'token')) console.log(`  ${String(n).padStart(4)}  ${token}`);
    console.log('\nEach of these emits NO CSS at all. Not a weaker colour, none: no fill, no border colour,');
    console.log('no gradient stop. The class is in the file and absent from the stylesheet.');
    if (list) for (const r of drops) console.log(`  ${r.file}:${r.line}  ${r.cls}`);
    else console.log('Pass --list for every occurrence.');
  }

  if (unknown.length) {
    const files_ = new Set(unknown.map((r) => r.file));
    console.log(`\n${unknown.length} class(es) in ${files_.size} file(s) name a colour token this theme never defined:`);
    for (const [token, n] of tally(unknown, 'token')) {
      const f = new Set(unknown.filter((r) => r.token === token).map((r) => r.file)).size;
      console.log(`  ${String(n).padStart(4)}  ${token}  (${f} files)`);
    }
    console.log('\nThese emit nothing for the more basic reason: the name resolves to nothing at all.');
    console.log('It needs no cleverness to write, only a plausible word, so it is the easier one to add.');
    if (list) for (const r of unknown) console.log(`  ${r.file}:${r.line}  ${r.cls}`);
    else console.log('Pass --list for every occurrence.');
  }

  const findings = drops.length + unknown.length;
  if (findings && strict) return 1;
  if (findings) {
    console.log(`\n${findings} finding(s). Reporting only; pass --strict to make them fail.`);
  } else {
    console.log('\nNo class writes an alpha modifier on a token that cannot take one.');
  }
  return 0;
}

main(process.argv.slice(2)).then((code) => process.exit(code));
