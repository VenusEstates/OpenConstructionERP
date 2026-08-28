// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Two ways a semantic colour class can look right in the source and paint
// something else on screen. Both were live in the tree, and neither is visible
// to a build: Tailwind drops an unknown utility silently and exits zero.
//
// 1. A token that does not exist. The semantic palette defines a base hue, a
//    `-bg` wash and a `-vivid` variant, and nothing called `-subtle`, so
//    `bg-semantic-warning-subtle` emitted no rule. On a background that reads
//    as no background. On a ring it is worse: `ring-8` still applies
//    Tailwind's own default ring colour when the ring-colour class is
//    dropped, and that default is blue, so a green success mark wore a wide
//    blue halo.
//
// 2. An alpha modifier that puts the colour under the contrast floor. An
//    alpha modifier does not fade a colour, it composites it onto whatever is
//    behind: the painted pixel is alpha*fg + (1-alpha)*bg. It can therefore
//    only ever LOWER contrast, and these hues start close to the floor -
//    5.45:1 for error and 4.95:1 for success against white. This mattered the
//    moment the palette started emitting alpha rules at all: before that fix
//    the modified classes painted nothing and the text simply inherited its
//    parent colour, so the debt is a regression waiting on a deploy rather
//    than something a reader has already suffered.
//
// The floor asserted below is the 3:1 one that WCAG asks of a graphical
// object such as an icon glyph, not the 4.5:1 it asks of text, and it is
// computed from index.css rather than typed in, so it follows the palette.
//
// WHAT THIS GUARD DELIBERATELY DOES NOT ASSERT. It cannot tell an icon from a
// text node, and text needs 4.5:1, which NO alpha value reaches on these
// hues. Asserting the text floor here would fail the five icon-only sites
// that are correct at /70, and a guard that cannot be satisfied gets
// weakened. Whether a given site is text is a judgement made when the site is
// written; what this file rules out is the value that is wrong either way.
//
// Run: npx vitest run src/tests/semanticAlphaContrast.test.ts

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { describe, it, expect } from 'vitest';

const SRC = resolve(__dirname, '..');
const CSS = readFileSync(resolve(SRC, 'index.css'), 'utf-8');

const HUES = ['success', 'warning', 'error', 'info'] as const;
const SURFACES = ['bg', 'bg-secondary', 'bg-tertiary', 'bg-elevated'];
const GRAPHICAL_FLOOR = 3;

/** Every .tsx and .ts under src, minus the test files themselves. */
function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

// Read once. Each assertion below scans the whole tree, and re-reading a few
// thousand files per test is what pushed this file past the default timeout
// while a typecheck was running beside it.
const SOURCES: ReadonlyArray<readonly [string, string]> = sources(SRC).map(
  (file) => [file.slice(SRC.length + 1), readFileSync(file, 'utf-8')] as const,
);

/**
 * Read a custom property out of one theme block.
 *
 * The light values sit under `:root` and the dark ones under the `.dark`
 * selector further down the same file, so the file is split at the dark
 * selector and the first declaration in each half wins.
 */
function token(name: string, theme: 'light' | 'dark'): string {
  const darkAt = CSS.indexOf('.dark');
  const chunk = theme === 'light' ? CSS.slice(0, darkAt) : CSS.slice(darkAt);
  const found = new RegExp(`--oe-${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`).exec(chunk);
  if (!found) throw new Error(`--oe-${name} has no hex value in the ${theme} block`);
  return found[1]!;
}

function rgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance([r, g, b]: [number, number, number]): number {
  const channel = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: [number, number, number], b: [number, number, number]): number {
  const one = luminance(a);
  const other = luminance(b);
  return (Math.max(one, other) + 0.05) / (Math.min(one, other) + 0.05);
}

function composite(
  fg: [number, number, number],
  bg: [number, number, number],
  alpha: number,
): [number, number, number] {
  return fg.map((f, i) => Math.round(alpha * f + (1 - alpha) * bg[i]!)) as [number, number, number];
}

/** Lowest alpha step at which this hue still clears the graphical floor everywhere. */
function lowestUsableAlpha(hue: string): number {
  for (let step = 5; step <= 100; step += 5) {
    const clears = (['light', 'dark'] as const).every((theme) =>
      SURFACES.every((surface) => {
        const bg = rgb(token(surface, theme));
        return contrast(composite(rgb(token(hue, theme)), bg, step / 100), bg) >= GRAPHICAL_FLOOR;
      }),
    );
    if (clears) return step;
  }
  return 100;
}

describe('semantic colour classes paint what they say', () => {
  it('never names a token the palette does not define', () => {
    const offenders: string[] = [];
    for (const [name, text] of SOURCES) {
      for (const match of text.matchAll(/[a-z-]+-semantic-[a-z]+-subtle/g)) {
        offenders.push(`${name}: ${match[0]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never fades a semantic foreground below the graphical contrast floor', () => {
    const floors = Object.fromEntries(HUES.map((hue) => [hue, lowestUsableAlpha(hue)]));
    const offenders: string[] = [];
    for (const [name, text] of SOURCES) {
      for (const match of text.matchAll(/\btext-semantic-(success|warning|error|info)\/(\d+)\b/g)) {
        const hue = match[1] as (typeof HUES)[number];
        if (Number(match[2]) < floors[hue]!) {
          offenders.push(`${name}: ${match[0]} needs at least /${floors[hue]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never asks for an opacity step Tailwind does not generate', () => {
    // The slash syntax reads the opacity scale, which runs in fives. A colour
    // utility asking for /12 is not a slightly different tint, it is no rule
    // at all, exactly like a token that does not exist: verified on a fresh
    // build, where /10, /15, /35 and /45 are all present and the only /12 in
    // four hundred kilobytes of stylesheet is the width fraction w-10/12.
    // Anything between the steps has to use the bracket form, bg-black/[0.12],
    // which this pattern deliberately does not match.
    const COLOUR = /\b(?:bg|text|border|ring|fill|stroke|from|via|to|divide|outline|accent|caret|decoration|placeholder|shadow)-[a-z0-9-]+\/(\d+)\b/g;
    const offenders: string[] = [];
    for (const [name, text] of SOURCES) {
      for (const match of text.matchAll(COLOUR)) {
        if (Number(match[1]) % 5 !== 0) {
          offenders.push(`${name}: ${match[0]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('measured a real tree against the palette this repo actually ships', () => {
    // This file has two inputs and either one can go quietly empty. Both are
    // guarded here, in one place, because they are one idea rather than two
    // defensive lines: every assertion above is only worth reading if neither
    // input was empty, and a guard that reads as decoration gets deleted.
    //
    // The tree. The three assertions above each collect offenders out of
    // SOURCES and assert the collection is empty, so a walk that returns
    // nothing satisfies all three while opening no file at all. The walk is a
    // recursive readdir from a resolved path with an extension filter, and it
    // empties without erroring if any of those move.
    expect(SOURCES.length, 'the walk over src collected no files').toBeGreaterThan(1500);

    // The palette. A guard computed from a file it cannot read would pass on
    // an empty palette, so the floors are pinned here as well. If a hue moves,
    // this line is the one that says so out loud rather than silently relaxing.
    expect(lowestUsableAlpha('error')).toBe(65);
    expect(lowestUsableAlpha('success')).toBe(75);
    expect(lowestUsableAlpha('warning')).toBe(75);
    expect(lowestUsableAlpha('info')).toBe(70);
  });
});
