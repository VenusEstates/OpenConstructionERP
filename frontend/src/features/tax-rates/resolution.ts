// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Turning a resolver answer into the one thing the screen has to draw.
 *
 * Pure, and deliberately separate from the panel: this is the decision the
 * whole feature rests on, and it should be testable without rendering
 * anything.
 *
 * Several of the server's statuses mean "no rate", and they are not several
 * ways of saying the same thing - each one names a different thing that is
 * missing and a different person who can supply it. One of them is not even a
 * single thing: `subdivision_unknown` is returned for three different
 * situations that a reader has to act on differently. They are told apart by
 * the shape of the response rather than by reading the server's English
 * `reason`, because matching on prose breaks the first time somebody rewords
 * a sentence.
 */

import type { TaxRateComponent, TaxResolution } from './api';

/**
 * What the screen should show. One of these is a number; the rest are not,
 * and none of the rest can be turned into one.
 */
export type Classification =
  /**
   * A rate a quantity surveyor can put in a tender. The rate is carried on
   * the variant rather than read back off the response, so the rendering code
   * cannot reach a number except through this branch, and cannot reach this
   * branch without one.
   */
  | { kind: 'answered'; combinedRatePct: string; components: TaxRateComponent[] }
  /** The country charges by region and no region was named. A question. */
  | { kind: 'needs_subdivision' }
  /** A region was named that we hold no rate for, and do not enumerate. */
  | { kind: 'subdivision_not_carried' }
  /** Regional rates are on file but unlabelled, so absence proves nothing. */
  | { kind: 'rates_unlabelled' }
  /** Nothing at all on file for this country on this date. */
  | { kind: 'no_country_data' }
  /** Rows are in force that do not resolve to a single standard rate. */
  | { kind: 'rates_conflict' }
  /**
   * The country's standard rate begins after the date asked about, and what
   * is in force instead is a reduced tier. Its own kind rather than a share
   * of `rates_conflict`, because the two need opposite things done: there,
   * somebody flags one of the rows already on file; here, the row that would
   * answer does not exist yet and has to be added.
   */
  | { kind: 'standard_rate_not_started' };

export type ResolutionKind = Classification['kind'];


/** Every kind except the one that carries a number. */
export const UNANSWERED_KINDS: readonly ResolutionKind[] = [
  'needs_subdivision',
  'subdivision_not_carried',
  'rates_unlabelled',
  'no_country_data',
  'rates_conflict',
  'standard_rate_not_started',
];

export function classifyResolution(r: TaxResolution): Classification {
  if (r.status === 'no_configuration') return { kind: 'no_country_data' };
  if (r.status === 'default_rate_ambiguous') return { kind: 'rates_conflict' };
  if (r.status === 'default_rate_not_in_force') return { kind: 'standard_rate_not_started' };

  if (r.status === 'subdivision_unknown') {
    // Three causes, three different people who can fix them, told apart by
    // which fields came back populated.
    //
    //   no code at all      the caller never named a region, and the person
    //                       reading the screen can answer it themselves
    //   code, but no name   the region is not in our registry and has no row,
    //                       so whether it charges anything is genuinely
    //                       unknown; somebody has to add the rate
    //   code and name       we know the region, and the reason we still
    //                       cannot answer is that our own rows are not
    //                       labelled yet; an administrator runs the repair
    if (!r.subdivision_code) return { kind: 'needs_subdivision' };
    if (!r.subdivision_name) return { kind: 'subdivision_not_carried' };
    return { kind: 'rates_unlabelled' };
  }

  if (r.resolved && r.combined_rate_pct !== null) {
    return { kind: 'answered', combinedRatePct: r.combined_rate_pct, components: r.components };
  }

  // Unreachable against the server as it stands: the five remaining statuses
  // all carry a rate. If it ever happens, the rows in force did not yield one
  // figure, which is what the conflict copy already says, and the honest
  // reading of a resolved status with no number is that the configuration is
  // wrong rather than that the question was.
  return { kind: 'rates_conflict' };
}

export interface SubdivisionOption {
  code: string;
  /** Registry name where we have one, otherwise the code itself. */
  label: string;
  /** False when the option exists only because a rate row mentions it. */
  inRegistry: boolean;
}

/**
 * What the region picker should offer.
 *
 * Not the registry alone. The registry covers Canada and stops there, while
 * the resolver decides a country charges by region if *either* the registry
 * knows it or the data carries regional rows for it. The United States is
 * exactly in the gap: one Californian rate is on file, no register of states
 * exists, so a picker built from `/subdivisions/US` offers nothing at all
 * while the resolver goes on asking which state it is. Offering the regions
 * our own rows mention closes that, and a country we have no rows for still
 * ends up with an empty picker, which is the truthful answer there.
 */
export function offerableSubdivisions(
  registry: readonly { code: string; name: string }[],
  rows: readonly { subdivision_code: string | null }[],
): SubdivisionOption[] {
  const byCode = new Map<string, SubdivisionOption>();

  for (const entry of registry) {
    byCode.set(entry.code, { code: entry.code, label: entry.name, inRegistry: true });
  }
  for (const row of rows) {
    const code = row.subdivision_code;
    if (!code || byCode.has(code)) continue;
    byCode.set(code, { code, label: code, inRegistry: false });
  }

  return [...byCode.values()].sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * Whether this country needs a region before the question can be answered.
 *
 * Mirrors the `axis` test in `tax_rules.resolve`: the registry knows it, or
 * the rows do. Kept here so the picker can be shown before a resolve call is
 * made rather than only after the server has refused one.
 */
export function needsSubdivision(options: readonly SubdivisionOption[]): boolean {
  return options.length > 0;
}
