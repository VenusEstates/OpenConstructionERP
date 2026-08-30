#!/usr/bin/env node
/**
 * Fail the build when a pair of physical side utilities on one element does not
 * mirror under `dir="rtl"`.
 *
 * src/index.css mirrors the common physical side utilities by hand, and every
 * one of those rules writes BOTH sides:
 *
 *   [dir='rtl'] .pl-10 { padding-left: 0; padding-right: 2.5rem }
 *   [dir='rtl'] .pr-3  { padding-right: 0; padding-left: 0.75rem }
 *
 * Put both on one element - a search field with an icon at one end and a clear
 * button at the other - and they do not swap, they fight over both properties.
 * The later rule wins outright and the other side is not mirrored but erased:
 * 37.5 / 33.75 becomes 0 / 37.5 rather than 33.75 / 37.5. An element stretched
 * by `left-0 right-0` collapses the same way and, with no width of its own,
 * measures zero and vanishes.
 *
 * Two things make this invisible to review and to every other gate. Both
 * classes are present and spelled correctly, so eslint, tsc and the build are
 * happy. And the mirror rule's specificity is (0,2,0), so it also outranks the
 * plain (0,1,0) utility of the other side: `pl-8 pr-2.5` collapses although
 * only one of the two has a mirror rule at all. That second shape is why this
 * gate exists next to check_tailwind_conflicts.cjs rather than inside it - that
 * gate compares utilities within one selector context, and here the two rules
 * are in different contexts while still colliding.
 *
 * The fix is never to add another mirror rule. Tailwind 3.3+ ships the logical
 * utilities natively (ps-/pe-, ms-/me-, start-/end-, border-s/-e, rounded-s/-e,
 * text-start/-end); they need no plugin and no config, and they carry the
 * direction themselves. Both halves of a pair have to move together, along with
 * any absolutely positioned overlay of the same control.
 *
 * What is measured is not "do two classes collide" but "is RTL the mirror image
 * of LTR". Both cascades are simulated from the real stylesheet and compared,
 * so the verdict is a value, not a name.
 *
 * The mirrored-class table is parsed out of index.css and the utility values
 * out of a generated stylesheet. Neither is written by hand: a remembered table
 * goes stale silently, and always in the direction of a false green.
 *
 * Sites where NO mirror rule is in play are a different defect - the side
 * simply never flips - and are counted separately rather than folded in. There
 * are a great many of them and they are not what this gate blocks on; a single
 * total would let a partial fix look like progress on the wrong defect.
 *
 * TWO BLIND SPOTS, BOTH LOAD-BEARING. Green here does not mean the tree has no
 * collapsing pairs.
 *
 *   Only classes present on EVERY render are considered. A pair that lives
 *   inside a conditional - `isReply && 'ml-8 pl-3 border-l-2'` - is invisible to
 *   this gate by construction, because a token that may not be on the element
 *   cannot be shown to collide with one that is. When this gate was written six
 *   such sites existed, and two of them (CommentThread.tsx, CostsPage.tsx) had
 *   just been repaired by hand; reverting either would leave this lane green.
 *   Widening to conditional tokens means proving the branches can co-occur,
 *   which the AST does not know, so it would trade silence for false alarms.
 *
 *   Only className attributes are read. A class list assembled in a variable, a
 *   constant or a helper that returns a string never reaches the JSX attribute
 *   this walks.
 *
 *   --css <path>   use an existing sheet (a build already produces one)
 *   default        generate one here from the class tokens found in source
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const FRONTEND = path.resolve(__dirname, '..');
const SRC = path.join(FRONTEND, 'src');
const INDEX_CSS = path.join(SRC, 'index.css');
const ts = require(path.join(FRONTEND, 'node_modules/typescript'));

// Physical pairs we can simulate exactly, as [start-side, end-side].
const PAIRS = [
  ['padding-left', 'padding-right'],
  ['margin-left', 'margin-right'],
  ['left', 'right'],
  ['border-left-width', 'border-right-width'],
];
const TRACKED = new Set(PAIRS.flat());

// An absent declaration is the property's initial value, not a mismatch:
// `padding-left: 0` and no padding-left render identically. Preflight sets
// `border-width: 0` on every element, so an absent border width is 0, not
// `medium`.
const INITIAL = {
  'padding-left': '0', 'padding-right': '0',
  'margin-left': '0', 'margin-right': '0',
  left: 'auto', right: 'auto',
  'border-left-width': '0', 'border-right-width': '0',
};

// ---------------------------------------------------------------- stylesheet

/**
 * Remove CSS comments, keeping a space so two tokens cannot fuse. The parser
 * accumulates a selector prelude up to `{`, so a comment in front of a rule
 * would otherwise be glued onto it and the rule filed under a selector that
 * matches nothing. index.css puts a comment above most of its sections.
 */
function stripComments(css) {
  const parts = [];
  let i = 0, last = 0, quote = null;
  while (i < css.length) {
    const ch = css[i];
    if (quote) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; i++; continue; }
    if (ch === '/' && css[i + 1] === '*') {
      const end = css.indexOf('*/', i + 2);
      parts.push(css.slice(last, i), ' ');
      i = end < 0 ? css.length : end + 2;
      last = i;
      continue;
    }
    i++;
  }
  parts.push(css.slice(last));
  return parts.join('');
}

// Split on top-level `;` only, so `translate(var(--a), var(--b))` stays one
// value. Index-based rather than character-accumulating: a built sheet is a
// single minified line of a couple of megabytes.
function parseDecls(body) {
  const out = {};
  let depth = 0, start = 0;
  const take = (from, to) => {
    const seg = body.slice(from, to);
    const i = seg.indexOf(':');
    if (i < 0) return;
    const prop = seg.slice(0, i).trim();
    if (prop) out[prop] = seg.slice(i + 1).trim();
  };
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    else if (ch === ';' && depth === 0) { take(start, i); start = i + 1; }
  }
  take(start, body.length);
  return out;
}

// The mirror block writes `border-left: none`, which also zeroes the width.
// Expand the shorthands it uses so the simulation sees the property in dispute.
function expand(decls) {
  const out = {};
  for (const [p, v] of Object.entries(decls)) {
    if (p === 'border-left' || p === 'border-right') out[p + '-width'] = v === 'none' ? '0px' : v;
    else out[p] = v;
  }
  return out;
}

/** Every non-keyframe rule in a sheet, as { sel, decls, order, media }. */
function rules(cssPath) {
  const css = stripComments(fs.readFileSync(cssPath, 'utf8'));
  const out = [];
  let i = 0, bufStart = 0, order = 0, kf = 0;
  const stack = [];
  while (i < css.length) {
    const ch = css[i];
    if (ch === '{') {
      const prelude = css.slice(bufStart, i).trim();
      if (prelude.startsWith('@')) {
        if (/^@keyframes/.test(prelude)) kf++;
        stack.push(prelude);
        i++; bufStart = i;
        continue;
      }
      let depth = 1, j = i + 1;
      while (j < css.length && depth > 0) {
        if (css[j] === '{') depth++;
        else if (css[j] === '}') { depth--; if (depth === 0) break; }
        j++;
      }
      if (!kf) {
        const media = stack.filter((s) => s.startsWith('@media') || s.startsWith('@supports')).join(' && ');
        const decls = expand(parseDecls(css.slice(i + 1, j)));
        for (const sel of prelude.split(',')) out.push({ sel: sel.trim(), decls, order: order++, media });
      }
      i = j + 1; bufStart = i;
      continue;
    }
    if (ch === '}') {
      if (stack.length && stack.pop().startsWith('@keyframes')) kf--;
      i++; bufStart = i;
      continue;
    }
    i++;
  }
  return out;
}

const unesc = (s) => s.replace(/\\(.)/g, '$1');
const CLASS = '((?:\\\\.|[^\\s.:[\\]()+>~,\\\\])+)';

/** `.pl-3 { ... }` - one class, no media, no pseudo. */
function baseUtilities(cssPath) {
  const map = new Map();
  for (const r of rules(cssPath)) {
    if (r.media) continue;
    const m = new RegExp('^\\.' + CLASS + '$').exec(r.sel);
    if (!m) continue;
    if (!Object.keys(r.decls).some((p) => TRACKED.has(p))) continue;
    map.set(unesc(m[1]), { decls: r.decls, order: r.order });
  }
  return map;
}

/** `[dir='rtl'] .pl-3 { ... }` - the hand-written mirror block. */
function mirrorRules() {
  const map = new Map();
  for (const r of rules(INDEX_CSS)) {
    if (r.media) continue;
    const m = new RegExp('^\\[dir=[\'"]rtl[\'"]\\]\\s+\\.' + CLASS + '$').exec(r.sel);
    if (!m) continue;
    if (!Object.keys(r.decls).some((p) => TRACKED.has(p))) continue;
    map.set(unesc(m[1]), { decls: r.decls, order: r.order });
  }
  return map;
}

// ------------------------------------------------------------------- sources

function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === 'dist') continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.tsx')) out.push(p);
  }
  return out;
}

const toks = (s) => s.split(/\s+/).filter(Boolean);

/**
 * Only whitespace-delimited COMPLETE tokens of a template literal are certain.
 * `text-${size}` leaves a partial `text-` that is not a class.
 */
function staticTokensFromTemplate(node) {
  if (ts.isNoSubstitutionTemplateLiteral(node)) return toks(node.text);
  const out = [];
  const push = (text, headBounded, tailBounded) => {
    const parts = text.split(/\s+/);
    parts.forEach((part, k) => {
      if (!part) return;
      if (k === 0 && !headBounded && !/^\s/.test(text)) return;
      if (k === parts.length - 1 && !tailBounded && !/\s$/.test(text)) return;
      out.push(part);
    });
  };
  push(node.head.text, true, false);
  node.templateSpans.forEach((sp, k) => push(sp.literal.text, false, k === node.templateSpans.length - 1));
  return out;
}

/** Classes present on every render of this element. */
function alwaysPresent(node) {
  const always = [];
  const rec = (n, certain) => {
    if (!n) return;
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) {
      if (certain) always.push(...toks(n.text));
      return;
    }
    if (ts.isTemplateExpression(n)) {
      if (certain) always.push(...staticTokensFromTemplate(n));
      return;
    }
    if (ts.isJsxExpression(n) || ts.isParenthesizedExpression(n)) return rec(n.expression, certain);
    if (ts.isBinaryExpression(n)) {
      if (n.operatorToken.kind === ts.SyntaxKind.PlusToken) { rec(n.left, certain); rec(n.right, certain); }
      return;
    }
    if (ts.isCallExpression(n)) {
      const name = ts.isIdentifier(n.expression) ? n.expression.text : '';
      // A helper's plain string arguments are unconditional; ternaries and
      // `cond && 'x'` inside it are not.
      if (/^(clsx|cn|classNames|cx|twMerge|twJoin)$/.test(name)) for (const a of n.arguments) rec(a, certain);
      return;
    }
    if (ts.isArrayLiteralExpression(n)) for (const el of n.elements) rec(el, certain);
  };
  rec(node, true);
  return [...new Set(always)];
}

// ------------------------------------------------------------------ generate

function generateSheet(tokens, outDir) {
  const cfgPath = path.join(outDir, 'rtl-collapse.config.cjs');
  const outPath = path.join(outDir, 'rtl-collapse.css');
  // Spread the real config and replace only `content`. tailwind.config.js is
  // transpiled ESM, so `require` hands back { __esModule, default }; spreading
  // that wrapper yields a config with no theme, and Tailwind then emits a sheet
  // with no utilities at all - every class reads as unresolved and every
  // collapse as absent.
  const base = 'const _m = require(' + JSON.stringify(path.join(FRONTEND, 'tailwind.config.js')) + ');\n' +
    'const base = _m.default || _m;\n';
  fs.writeFileSync(cfgPath, base +
    'module.exports = Object.assign({}, base, { content: [{ raw: ' + JSON.stringify(tokens.join(' ')) + ' }] });\n');

  const written = require(cfgPath);
  if (!written.theme) {
    throw new Error('generated config carries no `theme`: the real config was not unwrapped, so the sheet ' +
      'would contain no utilities and every collapse would read as absent');
  }
  execFileSync(
    process.execPath,
    [path.join(FRONTEND, 'node_modules/tailwindcss/lib/cli.js'), '-c', cfgPath, '-i', INDEX_CSS, '-o', outPath],
    { stdio: 'pipe' },
  );
  return outPath;
}

// --------------------------------------------------------------------- check

function main() {
  const argv = process.argv.slice(2);
  const cssArg = argv.indexOf('--css');

  const files = walk(SRC, []);
  const sites = [];
  let total = 0;
  for (const file of files) {
    const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const rel = path.relative(FRONTEND, file).replace(/\\/g, '/');
    const visit = (n) => {
      if (ts.isJsxAttribute(n) && n.name.getText(sf) === 'className') {
        total++;
        const classes = alwaysPresent(n.initializer);
        if (classes.length > 1) {
          sites.push({ file: rel, line: sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1, classes });
        }
      }
      ts.forEachChild(n, visit);
    };
    visit(sf);
  }

  const used = new Set();
  for (const s of sites) for (const c of s.classes) used.add(c);

  let cssPath;
  let generated = false;
  if (cssArg >= 0 && argv[cssArg + 1]) {
    cssPath = path.resolve(argv[cssArg + 1]);
    if (!fs.existsSync(cssPath)) {
      console.error('stylesheet not found: ' + cssPath);
      return 2;
    }
  } else {
    cssPath = generateSheet([...used], fs.mkdtempSync(path.join(require('os').tmpdir(), 'rtl-collapse-')));
    generated = true;
  }

  const base = baseUtilities(cssPath);
  const mirror = mirrorRules();

  // Refuse an obviously wrong sheet outright: every check below would come back
  // clean and mean nothing. Both failures below are silent and they point in
  // opposite directions, so the counts are printed next to the verdict too.
  if (!mirror.size) {
    console.error('RTL collapse gate: index.css declares no `[dir=\'rtl\'] .<class>` mirror rules.');
    console.error('Either the mirror block is gone - in which case delete this gate - or the parse failed.');
    return 2;
  }
  if (base.size < 20) {
    console.error('RTL collapse gate: the stylesheet yielded only ' + base.size + ' side utilities.');
    console.error('That is not a real sheet, and a green result from it would be meaningless.');
    console.error(cssPath);
    return 2;
  }

  /** Winning value of `prop` for a class list, under ltr or rtl. */
  const resolve = (classes, prop, rtl) => {
    let best = null;
    const offer = (spec, order, val) => {
      if (!best || spec > best.spec || (spec === best.spec && order > best.order)) best = { spec, order, val };
    };
    for (const cls of classes) {
      const b = base.get(cls);
      if (b && prop in b.decls) offer(1, b.order, b.decls[prop]);
      if (!rtl) continue;
      const m = mirror.get(cls);
      if (m && prop in m.decls) offer(2, 1e9 + m.order, m.decls[prop]);
    }
    return best ? best.val : null;
  };

  const norm = (v, prop) => {
    const s = String(v === null || v === 'unset' || v === 'initial' ? INITIAL[prop] : v).replace(/\s+/g, ' ').trim();
    return /^0(px|rem|em|%)?$/.test(s) ? '0' : s;
  };

  const collapse = [];
  let noMirror = 0;
  const noMirrorFiles = new Set();
  for (const site of sites) {
    for (const [a, b] of PAIRS) {
      const ltrA = resolve(site.classes, a, false);
      const ltrB = resolve(site.classes, b, false);
      if (ltrA === null && ltrB === null) continue;
      const rtlA = resolve(site.classes, a, true);
      const rtlB = resolve(site.classes, b, true);
      // RTL must be the mirror image of LTR: the two sides swap.
      if (norm(rtlA, a) === norm(ltrB, b) && norm(rtlB, b) === norm(ltrA, a)) continue;
      const mirrored = site.classes.filter((c) => {
        const m = mirror.get(c);
        return m && (a in m.decls || b in m.decls);
      });
      if (!mirrored.length) { noMirror++; noMirrorFiles.add(site.file); continue; }
      collapse.push({
        file: site.file,
        line: site.line,
        detail: '[' + a + '/' + b + '] ltr(' + norm(ltrA, a) + ' / ' + norm(ltrB, b) + ')'
          + ' becomes rtl(' + norm(rtlA, a) + ' / ' + norm(rtlB, b) + ')'
          + ' instead of (' + norm(ltrB, b) + ' / ' + norm(ltrA, a) + ')'
          + '; mirrored by ' + mirrored.join(' '),
      });
    }
  }

  console.log('className sites scanned      : ' + total);
  console.log('sites with 2+ certain classes: ' + sites.length);
  console.log('mirror rules in index.css    : ' + mirror.size);
  console.log('side utilities in stylesheet : ' + base.size);
  console.log('stylesheet                   : ' + cssPath + (generated ? ' (generated here)' : ' (provided)'));
  // Reported next to the verdict on purpose. This gate deliberately does not
  // block on the far larger "never flips at all" population, and a reader who
  // cannot see that number would mistake a green result for a mirrored UI.
  console.log('not blocked on, side never flips: ' + noMirror + ' site(s) in ' + noMirrorFiles.size + ' files');
  console.log('');

  if (!collapse.length) {
    console.log('RTL side collapse gate: clean.');
    return 0;
  }

  console.error('RTL side collapse gate: ' + collapse.length + ' collapsing pair(s).');
  console.error('');
  console.error('Under dir="rtl" these elements do not mirror, they lose a side. Move BOTH');
  console.error('halves of the pair to logical utilities in one edit - ps-/pe-, ms-/me-,');
  console.error('start-/end-, border-s/-e, rounded-s/-e, text-start/-end - together with any');
  console.error('absolutely positioned overlay of the same control. Do not add a mirror rule.');
  for (const p of collapse) console.error('  ' + p.file + ':' + p.line + '  ' + p.detail);
  return 1;
}

process.exit(main());
