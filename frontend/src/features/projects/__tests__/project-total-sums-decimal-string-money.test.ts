// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Money crosses the wire as a decimal string, because a Decimal rendered
// verbatim survives a 126-million total that a float would round. The project
// screen's TypeScript declared those fields `number`, so the compiler enforced
// the lie rather than catching it: `+=` type-checked and concatenated, and
// `=== 0` type-checked and never matched. One lie, two very different faults.
//
// Asserted on behaviour, not on spelling. The report that brought this in came
// with the reason: the reporter had been patching their own shipped bundle by
// matching the minified text, a later build renamed the variables, the patch
// reported "pattern not found" and carried on, and the bug was quietly back
// while their tooling said it was fixed. So nothing here looks at how the sum
// is written; it feeds in what the API actually sends and reads what comes out.
//
// Both tests fail in both directions. The sum is checked to be a finite number
// AND to equal the right total, because `"0126150861.5085944498.86"` is a
// perfectly good string and only the second assertion knows it is wrong. The
// unpriced test pins the priced case too, so a helper that simply answered
// "true" every time would not pass.

import { describe, it, expect } from 'vitest';
import { sumBoqGrandTotals, isPositionUnpriced } from '../ProjectDetailPage';

describe('a project total over several estimates', () => {
  // The exact shape the API sends: PositionResponse and BOQResponse both carry
  // a field serialiser that renders these as plain decimal strings.
  const twoEstimates = [
    { grand_total: '126150861.50' },
    { grand_total: '85944498.86' },
  ];

  it('adds decimal strings instead of joining them', () => {
    const total = sumBoqGrandTotals(twoEstimates);
    expect(Number.isFinite(total)).toBe(true);
    expect(total).toBeCloseTo(212095360.36, 2);
  });

  it('still adds up when the wire sends numbers', () => {
    expect(sumBoqGrandTotals([{ grand_total: 10 }, { grand_total: 2.5 }])).toBe(12.5);
  });

  it('reads a single estimate the same way', () => {
    // The case that hid this for four releases: `0 + "2264760.32"` coerces and
    // looks correct, so a one-estimate project never showed the fault.
    expect(sumBoqGrandTotals([{ grand_total: '2264760.32' }])).toBeCloseTo(2264760.32, 2);
  });

  it('treats an empty project as zero rather than as an empty string', () => {
    const total = sumBoqGrandTotals([]);
    expect(total).toBe(0);
    expect(typeof total).toBe('number');
  });

  it('does not let one unparseable total destroy the rest of the sum', () => {
    expect(sumBoqGrandTotals([{ grand_total: '100' }, { grand_total: '' }])).toBe(100);
  });
});

describe('whether a position counts as unpriced', () => {
  it('counts the string zero the column defaults to', () => {
    // `!"0"` is false and `"0" === 0` is false, so the obvious spelling of this
    // test reported that nothing was unpriced however much was, and the "every
    // position is priced" health check went green with nothing behind it.
    expect(isPositionUnpriced('0')).toBe(true);
  });

  it('counts the padded and numeric spellings of the same zero', () => {
    expect(isPositionUnpriced('0.00')).toBe(true);
    expect(isPositionUnpriced('0.0000')).toBe(true);
    expect(isPositionUnpriced(0)).toBe(true);
  });

  it('counts a missing rate', () => {
    expect(isPositionUnpriced(null)).toBe(true);
    expect(isPositionUnpriced(undefined)).toBe(true);
    expect(isPositionUnpriced('')).toBe(true);
  });

  it('leaves a priced position alone', () => {
    expect(isPositionUnpriced('125.50')).toBe(false);
    expect(isPositionUnpriced(125.5)).toBe(false);
    expect(isPositionUnpriced('0.01')).toBe(false);
  });
});
