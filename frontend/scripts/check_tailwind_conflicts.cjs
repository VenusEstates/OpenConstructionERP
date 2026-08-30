#!/usr/bin/env node
/**
 * Fail the build when one element carries two Tailwind utilities that write the
 * same CSS property.
 *
 * Two mechanisms, and they are not the same defect:
 *
 *   A. same origin. Both utilities are plain declarations, so the one later in
 *      the GENERATED stylesheet wins, whatever the order in the class
 *      attribute. The loser is a dead class.
 *
 *   B. animation. A keyframe declaration outranks an author-normal one for the
 *      whole cycle, so the static utility does not merely lose, it never
 *      applies. This is the one that bites. `animate-spin -translate-y-1/2`
 *      resolves to a pure translation with zero rotation at every offset: the
 *      spinner does not spin. `animate-bounce -rotate-45` resolves to a pure
 *      translate and the arrow points the wrong way. Both classes are present
 *      and spelled correctly, so review, eslint, tsc and the build all pass.
 *
 * The utility-to-property table is built from the stylesheet OUR config
 * generates, never from a table written out by hand. That matters: the theme
 * carries fourteen custom keyframes that write `transform`, and a remembered
 * table would go stale the moment someone adds a fifteenth, silently, in the
 * direction of a false green.
 *
 * Where the stylesheet comes from:
 *   --css <path>   use an existing sheet (a build already produces one)
 *   default        generate one here from the class tokens found in source
 *
 * Generating costs a Tailwind run. On a loaded workstation that measured about
 * 75s; on an idle CI runner it is far less, but it is not free, so this belongs
 * in a lane that already builds rather than in a fast pre-commit hook.
 *
 * The gate refuses to pass on an incomplete sheet. If a class it must reason
 * about produced no rule, that is reported as an error rather than skipped,
 * because a missing rule reads exactly like an absent conflict.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const FRONTEND = path.resolve(__dirname, '..');
const SRC = path.join(FRONTEND, 'src');
const ts = require(path.join(FRONTEND, 'node_modules/typescript'));

/**
 * Pairs of (animation, property) where the static utility is deliberately the
 * animation's starting value rather than a value meant to survive the cycle.
 *
 * This list is a human judgement and cannot be derived from the keyframes.
 * `ping` and `spin` look identical to a machine - neither declares its property
 * at offset 0 - yet `animate-ping opacity-75` is the documented dot idiom and
 * `animate-spin -translate-y-1/2` is a spinner that does not rotate. Adding an
 * entry here is a claim that a person checked the rendered result, so it wants
 * a reason next to it.
 */
const INTENTIONAL = [
  {
    animation: 'ping',
    property: 'opacity',
    // ping declares opacity only from 75%, so a static opacity utility supplies
    // the value the fade starts from. That is the intended shape of the idiom.
    reason: 'static opacity is the start value of the ping fade',
  },
];

// ---------------------------------------------------------------- stylesheet

function unescapeClass(s) {
  return s.replace(/\\(.)/g, '$1');
}

// Split on top-level `;` only, so `transform: translate(var(--a), var(--b))`
// stays one value. Index-based rather than character-accumulating: the built
// stylesheet is a single minified line of a couple of megabytes, and building
// strings a character at a time turns this into a quadratic walk.
function parseDecls(body) {
  const out = {};
  let depth = 0;
  let start = 0;
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
    else if (ch === ';' && depth === 0) {
      take(start, i);
      start = i + 1;
    }
  }
  take(start, body.length);
  return out;
}

// `from` / `to` / `0%` / `50%, 100%` -> [0], [100], [0], [50, 100]
function parseOffsets(prelude) {
  return prelude
    .split(',')
    .map((s) => s.trim().toLowerCase())
    .map((s) => {
      if (s === 'from') return 0;
      if (s === 'to') return 100;
      const m = /^(-?[\d.]+)%$/.exec(s);
      return m ? Number(m[1]) : null;
    })
    .filter((n) => n !== null);
}

/**
 * Walk the sheet tracking at-rule nesting.
 *   utilities: key `class||context` -> { cls, context, decls, order }
 *   keyframes: name -> { property -> Set(offsets) }
 * `context` is everything about the selector that is not the utility class
 * itself, plus any media query. Two utilities can only fight when it matches,
 * which is what keeps `hover:` and base apart without special-casing variants.
 */
function parseSheet(cssPath) {
  const css = fs.readFileSync(cssPath, 'utf8');
  const utilities = new Map();
  const keyframes = new Map();
  let order = 0;
  let i = 0;
  let bufStart = 0;
  let kfName = null;
  const stack = [];

  while (i < css.length) {
    const ch = css[i];
    if (ch === '{') {
      const prelude = css.slice(bufStart, i).trim();
      if (prelude.startsWith('@')) {
        const m = /^@keyframes\s+([A-Za-z0-9_-]+)/.exec(prelude);
        if (m) {
          kfName = m[1];
          if (!keyframes.has(kfName)) keyframes.set(kfName, new Map());
        }
        stack.push(prelude);
        i++;
        bufStart = i;
        continue;
      }
      let depth = 1;
      let j = i + 1;
      while (j < css.length && depth > 0) {
        if (css[j] === '{') depth++;
        else if (css[j] === '}') {
          depth--;
          if (depth === 0) break;
        }
        j++;
      }
      const decls = parseDecls(css.slice(i + 1, j));
      if (kfName) {
        const offsets = parseOffsets(prelude);
        const slot = keyframes.get(kfName);
        for (const prop of Object.keys(decls)) {
          if (!slot.has(prop)) slot.set(prop, new Set());
          for (const o of offsets) slot.get(prop).add(o);
        }
      } else {
        const media = stack.filter((s) => s.startsWith('@media') || s.startsWith('@supports')).join(' && ');
        for (const sel of prelude.split(',')) {
          const s = sel.trim();
          // Tailwind puts the utility last: `.dark .dark\:bg-x`, `.group:hover .group-hover\:flex`.
          // The escape alternative has to come FIRST and the negated class has
          // to exclude the backslash. Otherwise `[^...]` happily eats the `\`
          // of `.animate-\[spin_4s_linear_infinite\]`, the `[` then matches
          // nothing, and the class silently truncates to `animate-` - a whole
          // family of arbitrary-value utilities disappearing without an error.
          const classes = s.match(/\.((?:\\.|[^\s.:[\]()+>~,\\])+)/g);
          if (!classes || !classes.length) continue;
          const last = classes[classes.length - 1];
          const cls = unescapeClass(last.slice(1));
          const idx = s.lastIndexOf(last);
          const context = ((media ? media + ' ' : '') + (s.slice(0, idx) + s.slice(idx + last.length)).trim()).trim();
          utilities.set(cls + '||' + context, { cls, context, decls, order: order++ });
        }
      }
      i = j + 1;
      bufStart = i;
      continue;
    }
    if (ch === '}') {
      if (stack.length && stack.pop().startsWith('@keyframes')) kfName = null;
      i++;
      bufStart = i;
      continue;
    }
    i++;
  }
  return { utilities, keyframes };
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

/** Classes that are present on every render of this element. */
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
      if (n.operatorToken.kind === ts.SyntaxKind.PlusToken) {
        rec(n.left, certain);
        rec(n.right, certain);
      }
      return;
    }
    if (ts.isCallExpression(n)) {
      const name = ts.isIdentifier(n.expression) ? n.expression.text : '';
      // A helper's plain string arguments are unconditional; everything else
      // inside it (ternaries, `cond && 'x'`, object keys) is not.
      if (/^(clsx|cn|classNames|cx|twMerge|twJoin)$/.test(name)) {
        for (const arg of n.arguments) rec(arg, certain);
      }
      return;
    }
    if (ts.isArrayLiteralExpression(n)) {
      for (const el of n.elements) rec(el, certain);
    }
  };
  rec(node, true);
  return always;
}

function collectSites() {
  const sites = [];
  let total = 0;
  for (const file of walk(SRC, [])) {
    const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const visit = (n) => {
      if (ts.isJsxAttribute(n)) {
        const nm = n.name.getText(sf);
        if (nm === 'className' || nm === 'class') {
          total++;
          const classes = [...new Set(alwaysPresent(n.initializer))];
          if (classes.length > 1) {
            sites.push({
              file: path.relative(FRONTEND, file).replace(/\\/g, '/'),
              line: sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1,
              classes,
            });
          }
        }
      }
      ts.forEachChild(n, visit);
    };
    visit(sf);
  }
  return { sites, total };
}

// ------------------------------------------------------------------ generate

function generateSheet(tokens, outDir) {
  const cfgPath = path.join(outDir, 'tw-conflicts.config.cjs');
  const outPath = path.join(outDir, 'tw-conflicts.css');
  // Spread the real config and replace only `content`. Using it as a *preset*
  // silently drops `theme.extend.animation`, which loses the custom keyframes -
  // the exact blind spot this gate exists to close.
  const base = 'const base = require(' + JSON.stringify(path.join(FRONTEND, 'tailwind.config.js')) + ');\n';
  const body = 'module.exports = Object.assign({}, base, { content: [{ raw: ' + JSON.stringify(tokens.join(' ')) + ' }] });\n';
  fs.writeFileSync(cfgPath, base + body);
  // The input is the app's real entry sheet, not a bare set of @tailwind
  // directives. src/index.css carries the directives AND two dozen hand-written
  // @keyframes plus the .animate-* classes that use them, none of which live in
  // tailwind.config.js. Generating from the directives alone produces a sheet
  // that resolves every config animation and none of those, which is a silent
  // false negative for exactly the mechanism this gate checks.
  execFileSync(
    process.execPath,
    [
      path.join(FRONTEND, 'node_modules/tailwindcss/lib/cli.js'),
      '-c', cfgPath,
      '-i', path.join(SRC, 'index.css'),
      '-o', outPath,
    ],
    { stdio: 'pipe' },
  );
  return outPath;
}

// --------------------------------------------------------------------- check

function main() {
  const argv = process.argv.slice(2);
  const cssArg = argv.indexOf('--css');
  const quiet = argv.includes('--quiet');

  const { sites, total } = collectSites();
  const used = new Set();
  for (const s of sites) for (const c of s.classes) used.add(c);

  let cssPath;
  let generated = false;
  if (cssArg >= 0 && argv[cssArg + 1]) {
    cssPath = path.resolve(argv[cssArg + 1]);
    if (!fs.existsSync(cssPath)) {
      console.error('stylesheet not found: ' + cssPath);
      process.exit(2);
    }
  } else {
    const outDir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'tw-conflicts-'));
    cssPath = generateSheet([...used], outDir);
    generated = true;
  }

  const { utilities, keyframes } = parseSheet(cssPath);

  const byClass = new Map();
  for (const e of utilities.values()) {
    if (!byClass.has(e.cls)) byClass.set(e.cls, []);
    byClass.get(e.cls).push(e);
  }

  // animate-* -> { name, props: Map(prop -> Set(offsets)) }
  const animations = new Map();
  for (const [cls, entries] of byClass) {
    for (const e of entries) {
      const shorthand = e.decls['animation'] || e.decls['animation-name'];
      if (!shorthand) continue;
      const name = shorthand.split(/\s+/).find((t) => keyframes.has(t));
      if (name) animations.set(cls, { name, props: keyframes.get(name) });
    }
  }

  // An obviously wrong sheet is worth refusing outright, because every check
  // below would come back clean and mean nothing.
  if (utilities.size < 100) {
    console.error('Tailwind conflict gate: the stylesheet parsed to only ' + utilities.size + ' rules.');
    console.error('That is not a real sheet, and a green result from it would be meaningless.');
    console.error(cssPath);
    process.exit(2);
  }

  // An animate-* class that resolves to no keyframes is a defect in its own
  // right: the element carries an animation that never runs. It reads exactly
  // like a class with no conflict, so it is reported rather than skipped.
  // Two causes, both real here: the class does not exist at all (the
  // tailwindcss-animate vocabulary - animate-in, fade-in, slide-in-from-*,
  // zoom-in-* - is used in several places although the plugin is not
  // installed and the config declares no plugins), or an arbitrary value names
  // a keyframe nobody defined. A stale sheet would also land here, which is
  // why the message names both possibilities instead of guessing.
  const unresolved = new Map();
  for (const site of sites) {
    for (const cls of site.classes) {
      if (!cls.startsWith('animate-') || animations.has(cls)) continue;
      if (!unresolved.has(cls)) unresolved.set(cls, []);
      unresolved.get(cls).push(site.file + ':' + site.line);
    }
  }

  const intentional = (animName, prop) => INTENTIONAL.some((x) => x.animation === animName && x.property === prop);

  const problems = [];
  for (const site of sites) {
    // ---- mechanism B: an animation's keyframes against a static declaration.
    for (const cls of site.classes) {
      const anim = animations.get(cls);
      if (!anim) continue;
      for (const ae of byClass.get(cls) || []) {
        for (const other of site.classes) {
          if (other === cls) continue;
          for (const oe of (byClass.get(other) || []).filter((x) => x.context === ae.context)) {
            for (const prop of anim.props.keys()) {
              if (prop.startsWith('--')) continue;
              if (!(prop in oe.decls)) continue;
              if (intentional(anim.name, prop)) continue;
              problems.push({
                kind: 'animation',
                file: site.file,
                line: site.line,
                prop,
                detail: cls + ' animates ' + prop + ' (@keyframes ' + anim.name + '), but ' + other + ' also sets it to ' + oe.decls[prop],
              });
            }
          }
        }
      }
    }

    // ---- mechanism A: two plain utilities, same property, different value.
    const slots = new Map();
    for (const cls of site.classes) {
      for (const e of byClass.get(cls) || []) {
        for (const [prop, val] of Object.entries(e.decls)) {
          const k = e.context + '||' + prop;
          if (!slots.has(k)) slots.set(k, []);
          slots.get(k).push({ cls, val, order: e.order, context: e.context, prop, size: Object.keys(e.decls).length, shape: Object.keys(e.decls).sort().join(',') });
        }
      }
    }
    for (const arr of slots.values()) {
      if (arr.length < 2) continue;
      if (new Set(arr.map((x) => x.val)).size === 1) continue; // same value twice
      // Tailwind's design is that a broad utility sets a default and a targeted
      // one replaces part of it: `text-sm` carries a line-height and
      // `leading-tight` replaces just that. Generated order guarantees the
      // targeted utility wins, so that is the mechanism working. A real
      // collision is two utilities owning the SAME property set and
      // disagreeing - two ways to say one thing, one of them dead.
      if (new Set(arr.map((x) => x.shape)).size !== 1) continue;
      const winner = arr.reduce((a, b) => (b.order > a.order ? b : a));
      const ctx = arr[0].context;
      const names = [...new Set(arr.map((x) => x.cls))];
      problems.push({
        kind: 'collision',
        file: site.file,
        line: site.line,
        // Key on the classes rather than the property: a pair that writes two
        // properties (pl-* and pr-* each write both sides under [dir=rtl]) is
        // one problem, not one per property.
        key: names.slice().sort().join('+') + '||' + ctx,
        prop: arr[0].prop,
        detail: names.join(' + ') + ' both write ' + arr[0].prop
          + (ctx ? ' under `' + ctx + '`' : '')
          + '; ' + winner.cls + ' wins'
          + (ctx ? ' there and the other has no effect in that context' : ' and the rest are dead'),
      });
    }
  }

  const seen = new Set();
  const unique = problems.filter((p) => {
    const k = p.kind + '|' + p.file + '|' + p.line + '|' + (p.key || p.prop);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  if (!quiet) {
    console.log('className sites scanned      : ' + total);
    console.log('sites with 2+ certain classes: ' + sites.length);
    console.log('utility rules in stylesheet  : ' + utilities.size);
    console.log('animations resolved          : ' + animations.size);
    console.log('stylesheet                   : ' + cssPath + (generated ? ' (generated here)' : ' (provided)'));
    console.log('');
  }

  if (!unique.length && !unresolved.size) {
    console.log('Tailwind conflict gate: clean.');
    return 0;
  }

  const anim = unique.filter((p) => p.kind === 'animation');
  const coll = unique.filter((p) => p.kind === 'collision');
  console.error('Tailwind conflict gate: ' + (unique.length + unresolved.size) + ' problem(s).');
  if (unresolved.size) {
    console.error('');
    console.error('These animate-* classes resolve to no keyframes, so the animation never');
    console.error('runs. Either the class does not exist (check that the vocabulary you are');
    console.error('using comes from a plugin this project actually installs), or it names a');
    console.error('keyframe nobody defined. If instead the stylesheet predates the source,');
    console.error('rebuild it and run again.');
    for (const [cls, at] of unresolved) {
      console.error('  ' + cls + '  (' + at.length + ' site' + (at.length === 1 ? '' : 's') + ': ' + at.slice(0, 3).join(', ') + (at.length > 3 ? ', ...' : '') + ')');
    }
  }
  if (anim.length) {
    console.error('');
    console.error('An animated property replaces a static one for the whole cycle, so the');
    console.error('static utility never applies. Put the animation and the static value on');
    console.error('different nodes - see src/shared/auth/RequiresProject.tsx.');
    for (const p of anim) console.error('  ' + p.file + ':' + p.line + '  ' + p.detail);
  }
  if (coll.length) {
    console.error('');
    console.error('Two utilities write the same property; the loser is a dead class.');
    for (const p of coll) console.error('  ' + p.file + ':' + p.line + '  ' + p.detail);
  }
  return 1;
}

process.exit(main());
