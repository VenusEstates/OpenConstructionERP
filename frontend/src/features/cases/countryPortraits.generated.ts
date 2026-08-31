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
  'prf-ca-commercial-manager.webp',
  'prf-ca-construction-manager.webp',
  'prf-ca-general-contractor.webp',
  'prf-ca-hse-manager.webp',
  'prf-ca-procurement-manager.webp',
  'prf-ca-quality-manager.webp',
  'prf-ca-scheduler-planner.webp',
  'prf-ca-site-supervisor.webp',
  'prf-cn-commercial-manager.webp',
  'prf-cn-construction-manager.webp',
  'prf-cn-estimator.webp',
  'prf-cn-general-contractor.webp',
  'prf-cn-hse-manager.webp',
  'prf-cn-owner-client.webp',
  'prf-cn-quality-manager.webp',
  'prf-cn-scheduler-planner.webp',
  'prf-cn-site-supervisor.webp',
  'prf-de-commercial-manager.webp',
  'prf-de-construction-manager.webp',
  'prf-de-general-contractor.webp',
  'prf-de-hse-manager.webp',
  'prf-de-procurement-manager.webp',
  'prf-de-quality-manager.webp',
  'prf-de-scheduler-planner.webp',
  'prf-de-site-supervisor.webp',
  'prf-de-subcontractor.webp',
  'prf-es-commercial-manager.webp',
  'prf-es-construction-manager.webp',
  'prf-es-general-contractor.webp',
  'prf-es-hse-manager.webp',
  'prf-es-mep-contractor.webp',
  'prf-es-procurement-manager.webp',
  'prf-es-quality-manager.webp',
  'prf-es-site-supervisor.webp',
  'prf-gb-commercial-manager.webp',
  'prf-gb-construction-manager.webp',
  'prf-gb-estimator.webp',
  'prf-gb-general-contractor.webp',
  'prf-gb-homebuilder.webp',
  'prf-gb-procurement-manager.webp',
  'prf-gb-scheduler-planner.webp',
  'prf-gb-site-supervisor.webp',
  'prf-us-commercial-manager.webp',
  'prf-us-construction-manager.webp',
  'prf-us-general-contractor.webp',
  'prf-us-hse-manager.webp',
  'prf-us-procurement-manager.webp',
  'prf-us-quality-manager.webp',
  'prf-us-scheduler-planner.webp',
  'prf-us-site-supervisor.webp',
]);
