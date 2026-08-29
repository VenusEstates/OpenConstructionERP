// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
// A search hit must never reach the screen as a bare identifier.
//
// The modal used to render `{hit.title || hit.id}`. That guard only sees an
// empty title, and it is not the only way a hit arrives with nothing to say:
// `VectorHit.title` on the backend falls back to the row id when a payload
// carries neither a title nor any text, so the title is a truthy UUID and the
// `||` never fires. Both shapes put a raw identifier in front of the reader.
//
// Every assertion here checks the content of the label rather than its
// presence. A UUID and a whitespace string are both truthy and would satisfy
// a test that only asked for something non-empty, which is exactly the output
// being fixed.
import { describe, expect, it } from 'vitest';
import type { TFunction } from 'i18next';
import { collectionLabel, hitLabel, type UnifiedSearchHit } from '../api';

const ID = '4015cdf0-9c2a-4f7e-9a1b-2f8e7d6c5b4a';

/** i18next with English active: an unanswered key renders its default. */
const tEnglish = ((key: string, opts?: { defaultValue?: string }) =>
  opts?.defaultValue ?? key) as unknown as TFunction;

/** i18next that echoes the key back, so a test can see which key was read.
 *  A hardcoded string returns itself here and no key at all, which is the
 *  difference this file exists to hold. */
const tKey = ((key: string) => key) as unknown as TFunction;

/** What the modal passes: the collection name in the reader's language. */
const kind = (collection: string) => collectionLabel(tEnglish, collection);

/** The interpolation i18next performs on `global_search.unnamed_hit`. */
const unnamed = (kindName: string, ref: string) => `${kindName} ${ref}`;

function hit(overrides: Partial<UnifiedSearchHit> = {}): UnifiedSearchHit {
  return {
    id: ID,
    score: 0.5,
    title: '',
    snippet: '',
    text: '',
    module: 'boq',
    project_id: '',
    tenant_id: '',
    payload: {},
    collection: 'oe_boq_positions',
    ...overrides,
  };
}

describe('hitLabel', () => {
  it('names a hit that has no title by its type and a short reference', () => {
    const label = hitLabel(hit({ title: '' }), kind, unnamed);

    expect(label).toBe('BOQ 4015cdf0');
    expect(label).not.toBe(ID);
  });

  it('does not hand over a title that is only the row id', () => {
    // The vector track produces this: truthy, so `title || id` returned it
    // unchanged and the reader got a UUID.
    const row = hit({ title: ID });

    expect(row.title || row.id).toBe(ID); // what the old expression yielded
    expect(hitLabel(row, kind, unnamed)).toBe('BOQ 4015cdf0');
  });

  it('treats a whitespace title as no title', () => {
    const row = hit({ title: '   ' });

    expect(row.title || row.id).toBe('   '); // truthy, so the old guard passed it
    expect(hitLabel(row, kind, unnamed).trim()).toBe('BOQ 4015cdf0');
  });

  it('shortens the reference instead of printing the whole identifier', () => {
    const label = hitLabel(hit(), kind, unnamed);

    expect(label).not.toContain(ID);
    expect(label).toContain(ID.slice(0, 8));
    expect(label.length).toBeLessThan(ID.length);
  });

  it('uses the collection name so two unnamed hits are told apart', () => {
    const boq = hitLabel(hit({ collection: 'oe_boq_positions' }), kind, unnamed);
    const risk = hitLabel(hit({ collection: 'oe_risks' }), kind, unnamed);

    expect(boq).not.toBe(risk);
    expect(boq).toContain('BOQ');
    expect(risk).toContain('Risks');
  });

  // --- Negative controls: a real title must survive untouched ---

  it('keeps a real title exactly as the backend sent it', () => {
    const label = hitLabel(
      hit({ title: '01.10.030 - Blinding to foundations' }),
      kind,
      unnamed,
    );

    expect(label).toBe('01.10.030 - Blinding to foundations');
  });

  it('keeps a title that merely contains the id', () => {
    // Only an exact match is the fallback shape; a title that quotes the id
    // is still a title someone wrote.
    const label = hitLabel(hit({ title: `Ref ${ID}` }), kind, unnamed);

    expect(label).toBe(`Ref ${ID}`);
  });
});

describe('collectionLabel', () => {
  // Every one of these named itself in English to all 42 offered languages
  // until this switch started reading the locale files. There is no i18n
  // call to inspect in a hardcoded return, so no gate could see it; `tKey`
  // is what sees it here.
  //
  // The list is ALL_COLLECTIONS from backend/app/core/vector_index.py, which
  // is what a search with no type filter fans out to - not the shorter list
  // the switch used to know. The last four reached the screen as their raw
  // collection key.
  const COLLECTIONS = [
    'oe_boq_positions',
    'oe_documents',
    'oe_tasks',
    'oe_risks',
    'oe_bim_elements',
    'oe_requirements',
    'oe_rfi_rfis',
    'oe_submittals_submittals',
    'oe_correspondence_correspondence',
    'oe_validation',
    'oe_chat',
    'oe_change_orders',
    'oe_variations',
    'oe_moc',
    'oe_cost_items',
  ];

  it('reads a locale key for every collection the search can return', () => {
    const keys = COLLECTIONS.map((c) => collectionLabel(tKey, c));

    for (const key of keys) {
      expect(key).toMatch(/^global_search\.collection\.[a-z_]+$/);
    }
    expect(new Set(keys).size).toBe(COLLECTIONS.length);
  });

  it('keeps the English wording as the default of each key', () => {
    expect(collectionLabel(tEnglish, 'oe_boq_positions')).toBe('BOQ');
    expect(collectionLabel(tEnglish, 'oe_risks')).toBe('Risks');
    expect(collectionLabel(tEnglish, 'oe_correspondence_correspondence')).toBe(
      'Correspondence',
    );
  });

  it('offers the acronyms for translation like any other term', () => {
    // A locale that answers BOQ with BOQ has decided that. These three used
    // to be unable to make that decision at all.
    expect(collectionLabel(tKey, 'oe_boq_positions')).toBe(
      'global_search.collection.boq',
    );
    expect(collectionLabel(tKey, 'oe_bim_elements')).toBe(
      'global_search.collection.bim',
    );
    expect(collectionLabel(tKey, 'oe_rfi_rfis')).toBe(
      'global_search.collection.rfi',
    );
  });

  it('does not invent a key for a collection it has never heard of', () => {
    // A key no locale file can answer would only make the orphan gate red.
    // The bare name is the honest answer for a collection added backend-side.
    expect(collectionLabel(tKey, 'oe_future_module')).toBe('future_module');
    expect(collectionLabel(tKey, 'unprefixed')).toBe('unprefixed');
  });

  it('names every collection a search with no type filter can return', () => {
    // The four change-management and cost collections used to land here as
    // "change_orders", "variations", "moc" and "cost_items".
    expect(COLLECTIONS).toHaveLength(15);
    for (const collection of COLLECTIONS) {
      expect(collectionLabel(tKey, collection)).not.toBe(
        collection.replace(/^oe_/, ''),
      );
    }
  });
});
