// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// GENERATED FILE - do not edit by hand.
// Regenerate with: python scripts/gen_case_country_portraits.py
//
// The country portraits that exist under frontend/public/assets/people, as
// bare filenames. `caseFaces.ts` consults this before it asks for one, so a
// market nobody has been photographed for costs nothing instead of costing a
// 404 per tile.
//
// Adding art is a folder operation: drop `prf-<country>-<stem>.webp` in beside
// the pooled portraits and run the script above. No TypeScript is written by
// hand here, and `caseFaces.test.ts` fails when this list and the folder
// disagree in either direction, so the step cannot be skipped quietly.

/** Filenames only, no path: the folder is `PEOPLE_ASSETS_BASE`. Sorted, so a
 *  regeneration shows only the webp that arrived. */
export const COUNTRY_PORTRAITS: ReadonlySet<string> = new Set<string>([
]);
