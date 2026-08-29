// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Every refusal `sliceBetween` makes, exercised.
//
// A guard nobody runs is decoration, and decoration is what gets deleted by the
// next reader who cannot tell it apart from a defensive line that never fires.
// Each case below is a shape that has actually shipped in this tree or is one
// edit away from it, so the reason the guard exists is readable from the test
// name rather than from the helper's header.
//
// Run: npx vitest run src/test/sourceSlice.test.ts

import { describe, it, expect } from 'vitest';

import { sliceBetween } from './sourceSlice';

/** A stand-in for a source file: two exports, the second one after the first. */
const FILE = [
  "export const CREDIT = 'OpenStreetMap contributors';",
  '',
  "export const RELIEF_CREDIT = 'OpenStreetMap and SRTM';",
  '',
].join('\n');

const opts = { minSourceLength: 10, label: 'fixture.ts' };

describe('sliceBetween', () => {
  it('returns just the region between the anchors', () => {
    const block = sliceBetween(FILE, 'export const CREDIT', 'export const RELIEF_CREDIT', opts);
    expect(block).toContain('OpenStreetMap contributors');
    expect(block).not.toContain('SRTM');
  });

  it('refuses a missing end anchor instead of slicing to the end of the file', () => {
    // The defect this helper exists for. `slice(start, -1)` runs to EOF and the
    // assertion then reports on whichever declaration sits last in the file.
    expect(() => sliceBetween(FILE, 'export const CREDIT', 'export const GONE', opts)).toThrow(
      /end anchor .* is not in the source after the start anchor/,
    );
  });

  it('refuses a missing start anchor', () => {
    expect(() => sliceBetween(FILE, 'export const GONE', 'export const RELIEF_CREDIT', opts)).toThrow(
      /start anchor .* is not in the source/,
    );
  });

  it('refuses an end anchor that only occurs before the start anchor', () => {
    // A bare `indexOf(end)` searching from zero finds this one in front of the
    // start and hands back an empty block, which reads as "the block is empty"
    // rather than "the anchors crossed".
    const crossed = ['const FIRST = 1;', 'const SECOND = 2;'].join('\n');
    expect(() => sliceBetween(crossed, 'const SECOND', 'const FIRST', opts)).toThrow(
      /end anchor .* is not in the source after the start anchor/,
    );
  });

  it('refuses a block that swallowed a second copy of the declaration it names', () => {
    const twice = [
      'export const CREDIT = 1;',
      'export const CREDIT = 2;',
      'export const AFTER = 3;',
    ].join('\n');
    expect(() => sliceBetween(twice, 'export const CREDIT', 'export const AFTER', opts)).toThrow(
      /contains a second .* so it holds more than the one declaration it names/,
    );
  });

  it('refuses a source shorter than the stated minimum, naming it', () => {
    // An empty read is the shape that makes a whole file of assertions pass for
    // free, so it fails here, at the read, rather than three assertions later.
    expect(() => sliceBetween('', 'a', 'b', { minSourceLength: 10, label: 'empty.ts' })).toThrow(
      /empty\.ts: read 0 characters, expected at least 10/,
    );
  });

  it('refuses an anchor that spans a line break, because it matches only one checkout', () => {
    // `';\n'` matches an LF copy of a file and misses the CRLF copy of the same
    // bytes, silently. This tree holds both, so the anchor is rejected outright.
    expect(() => sliceBetween(FILE, "';\n", 'export const RELIEF_CREDIT', opts)).toThrow(
      /spans a line break/,
    );
    expect(() => sliceBetween(FILE, 'export const CREDIT', "';\r\n", opts)).toThrow(/spans a line break/);
  });

  it('refuses an empty anchor, which would match at position 0 and mean nothing', () => {
    expect(() => sliceBetween(FILE, '', 'export const RELIEF_CREDIT', opts)).toThrow(/anchor is empty/);
  });

  it('finds the same region whether the target is checked out LF or CRLF', () => {
    // The property the header claims. Same bytes, two checkouts, one answer.
    const lf = FILE;
    const crlf = FILE.replace(/\n/g, '\r\n');
    const pick = (text: string) =>
      sliceBetween(text, 'export const CREDIT', 'export const RELIEF_CREDIT', opts)
        .replace(/\r/g, '')
        .trim();
    expect(pick(crlf)).toBe(pick(lf));
  });
});
