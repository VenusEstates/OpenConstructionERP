// DDC-CWICR-OE: DataDrivenConstruction - OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Tests for the classifier the tax rate screen is built on.
//
// The thing under test is not "does it return a kind". It is that five
// different reasons for having no rate stay five different things all the way
// to the screen, and that the one kind carrying a number is the only one that
// can carry a number. The last part is enforced by the type - the `answered`
// variant holds `combinedRatePct`, the others have no field to put one in -
// so these tests pin the routing rather than the shape.

import { describe, it, expect } from 'vitest';

import type { TaxResolution, TaxResolutionStatus } from './api';
import {
  classifyResolution,
  needsSubdivision,
  offerableSubdivisions,
  UNANSWERED_KINDS,
} from './resolution';

function resolution(over: Partial<TaxResolution>): TaxResolution {
  return {
    country_code: 'CA',
    subdivision_code: null,
    subdivision_name: null,
    status: 'federal_only',
    resolved: true,
    combined_rate_pct: '5',
    federal_rate_pct: '5',
    as_of: '2026-08-26',
    components: [],
    reason: null,
    ...over,
  };
}

describe('classifyResolution', () => {
  it('hands back the rate on the variant that carries one', () => {
    const answer = classifyResolution(
      resolution({ status: 'harmonised', subdivision_code: 'CA-ON', combined_rate_pct: '13' }),
    );
    expect(answer.kind).toBe('answered');
    // Read through the variant rather than off the response: this is the only
    // route to a number the panel has.
    expect(answer.kind === 'answered' && answer.combinedRatePct).toBe('13');
  });

  it('keeps Alberta and nobody-chose-a-province apart', () => {
    // The whole design in one assertion. Both are Canada, both come back with
    // a federal rate of five on the payload, and they are not the same answer.
    const alberta = classifyResolution(
      resolution({
        status: 'federal_only',
        subdivision_code: 'CA-AB',
        subdivision_name: 'Alberta',
        combined_rate_pct: '5',
      }),
    );
    const unasked = classifyResolution(
      resolution({
        status: 'subdivision_unknown',
        resolved: false,
        combined_rate_pct: null,
        federal_rate_pct: '5',
      }),
    );

    expect(alberta.kind).toBe('answered');
    expect(unasked.kind).toBe('needs_subdivision');
    expect(alberta.kind).not.toBe(unasked.kind);
  });

  it('tells the three causes of an unknown subdivision apart', () => {
    // One status, three situations, three different people who can act. The
    // discriminator is which fields came back populated, never the prose in
    // `reason`, so each fixture carries a reason that would mislead a reader
    // matching on text.
    const never_asked = classifyResolution(
      resolution({
        status: 'subdivision_unknown',
        resolved: false,
        combined_rate_pct: null,
        subdivision_code: null,
        subdivision_name: null,
        reason: 'Country CA charges tax by subdivision.',
      }),
    );
    const not_carried = classifyResolution(
      resolution({
        status: 'subdivision_unknown',
        resolved: false,
        combined_rate_pct: null,
        country_code: 'US',
        subdivision_code: 'US-TX',
        subdivision_name: null,
        reason: 'Subdivision US-TX is not one this platform carries rates for.',
      }),
    );
    const unlabelled = classifyResolution(
      resolution({
        status: 'subdivision_unknown',
        resolved: false,
        combined_rate_pct: null,
        subdivision_code: 'CA-ON',
        subdivision_name: 'Ontario',
        reason: 'Run the tax_subdivision_backfill repair.',
      }),
    );

    expect(never_asked.kind).toBe('needs_subdivision');
    expect(not_carried.kind).toBe('subdivision_not_carried');
    expect(unlabelled.kind).toBe('rates_unlabelled');

    // Three, not one wearing three names. Equal sets here would be the
    // finding, so assert the disjointness rather than each member.
    const kinds = new Set([never_asked.kind, not_carried.kind, unlabelled.kind]);
    expect(kinds.size).toBe(3);
  });

  it('routes the two whole-country refusals to their own kinds', () => {
    expect(
      classifyResolution(
        resolution({ status: 'no_configuration', resolved: false, combined_rate_pct: null }),
      ).kind,
    ).toBe('no_country_data');
    expect(
      classifyResolution(
        resolution({ status: 'default_rate_ambiguous', resolved: false, combined_rate_pct: null }),
      ).kind,
    ).toBe('rates_conflict');
  });

  it('sends a standard rate that had not started to its own kind, carrying no number', () => {
    // The server answered this with the reduced tier that happened to be in
    // force - Germany at 7 % for 1990 - until the resolver learned to refuse.
    // Two things are asserted rather than one: that it does not land on
    // `answered`, which is the failure that put a wrong number in front of
    // somebody, and that it does not land on `rates_conflict` either, whose
    // copy tells the reader to flag one of the rows already on file. Doing
    // that here would flag a reduced tier as the standard rate and make the
    // wrong number permanent.
    const classified = classifyResolution(
      resolution({
        status: 'default_rate_not_in_force',
        resolved: false,
        combined_rate_pct: null,
      }),
    );

    expect(classified.kind).toBe('standard_rate_not_started');
    expect(UNANSWERED_KINDS).toContain(classified.kind);
    expect(classified).not.toHaveProperty('combinedRatePct');
  });

  it('degrades a status invented after this client shipped, rather than throwing', () => {
    // The runtime half of the exhaustiveness guard. The compile-time half is
    // in the classifier and cannot be asserted from here: a test that failed
    // to compile would not run at all. This one pins the other direction,
    // that a client older than its server shows no rate rather than crashing
    // the panel over a deploy-order skew.
    const fromANewerServer = classifyResolution(
      resolution({
        status: 'a_status_this_client_has_never_heard_of' as TaxResolutionStatus,
        resolved: false,
        combined_rate_pct: null,
      }),
    );

    expect(UNANSWERED_KINDS).toContain(fromANewerServer.kind);
    expect(fromANewerServer).not.toHaveProperty('combinedRatePct');
  });

  it('refuses to answer when a resolved status arrives with no number', () => {
    // Should not happen against the server as it stands. If it ever does, the
    // screen must not fall through to a blank rate slot.
    const broken = classifyResolution(
      resolution({ status: 'national', resolved: true, combined_rate_pct: null }),
    );
    expect(broken.kind).not.toBe('answered');
  });

  it('carries every component through, not just the first', () => {
    // A stacked province is two rows and the panel prints both. A fixture with
    // one component cannot tell "passes the components along" from "passes the
    // first one along", so this one carries two.
    const quebec = classifyResolution(
      resolution({
        status: 'stacked',
        subdivision_code: 'CA-QC',
        subdivision_name: 'Quebec',
        combined_rate_pct: '14.975',
        components: [
          {
            tax_code: 'GST',
            tax_name: 'GST',
            rate_pct: '5',
            combination: 'federal',
            base: 'consideration',
            effective_rate_pct: '5',
          },
          {
            tax_code: 'QST_QC',
            tax_name: 'QST',
            rate_pct: '9.975',
            combination: 'stacks_on_federal',
            base: 'consideration',
            effective_rate_pct: '9.975',
          },
        ],
      }),
    );
    expect(quebec.kind === 'answered' && quebec.components).toHaveLength(2);
  });
});

describe('offerableSubdivisions', () => {
  it('offers every region in the registry', () => {
    const options = offerableSubdivisions(
      [
        { code: 'CA-ON', name: 'Ontario' },
        { code: 'CA-AB', name: 'Alberta' },
        { code: 'CA-BC', name: 'British Columbia' },
      ],
      [],
    );
    expect(options.map((o) => o.code)).toEqual(['CA-AB', 'CA-BC', 'CA-ON']);
    expect(options.every((o) => o.inRegistry)).toBe(true);
  });

  it('offers a region that only the rate rows know about', () => {
    // The United States. One Californian rate is on file, the registry is
    // empty, and the resolver still demands a state - so a picker built from
    // the registry alone offers nothing and the screen is unanswerable.
    const options = offerableSubdivisions([], [{ subdivision_code: 'US-CA' }]);
    expect(options).toEqual([{ code: 'US-CA', label: 'US-CA', inRegistry: false }]);
  });

  it('does not offer the same region twice when both sources have it', () => {
    const options = offerableSubdivisions(
      [{ code: 'CA-ON', name: 'Ontario' }],
      [{ subdivision_code: 'CA-ON' }, { subdivision_code: null }],
    );
    // The registry name wins over the bare code, so the picker reads
    // "Ontario" rather than "CA-ON". Asserted on the whole array: a length
    // check plus an indexed read says the same thing in two statements and
    // leaves the second one reaching into a value the first only implies.
    expect(options).toEqual([{ code: 'CA-ON', label: 'Ontario', inRegistry: true }]);
  });

  it('offers nothing for a country that charges no regional tax', () => {
    const options = offerableSubdivisions([], [{ subdivision_code: null }]);
    expect(options).toEqual([]);
    expect(needsSubdivision(options)).toBe(false);
  });
});
