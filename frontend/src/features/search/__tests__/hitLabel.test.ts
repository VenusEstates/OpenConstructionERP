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
import { hitLabel, type UnifiedSearchHit } from '../api';

const ID = '4015cdf0-9c2a-4f7e-9a1b-2f8e7d6c5b4a';

/** The interpolation i18next performs on `global_search.unnamed_hit`. */
const unnamed = (kind: string, ref: string) => `${kind} ${ref}`;

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
    const label = hitLabel(hit({ title: '' }), unnamed);

    expect(label).toBe('BOQ 4015cdf0');
    expect(label).not.toBe(ID);
  });

  it('does not hand over a title that is only the row id', () => {
    // The vector track produces this: truthy, so `title || id` returned it
    // unchanged and the reader got a UUID.
    const row = hit({ title: ID });

    expect(row.title || row.id).toBe(ID); // what the old expression yielded
    expect(hitLabel(row, unnamed)).toBe('BOQ 4015cdf0');
  });

  it('treats a whitespace title as no title', () => {
    const row = hit({ title: '   ' });

    expect(row.title || row.id).toBe('   '); // truthy, so the old guard passed it
    expect(hitLabel(row, unnamed).trim()).toBe('BOQ 4015cdf0');
  });

  it('shortens the reference instead of printing the whole identifier', () => {
    const label = hitLabel(hit(), unnamed);

    expect(label).not.toContain(ID);
    expect(label).toContain(ID.slice(0, 8));
    expect(label.length).toBeLessThan(ID.length);
  });

  it('uses the collection name so two unnamed hits are told apart', () => {
    const boq = hitLabel(hit({ collection: 'oe_boq_positions' }), unnamed);
    const risk = hitLabel(hit({ collection: 'oe_risks' }), unnamed);

    expect(boq).not.toBe(risk);
    expect(boq).toContain('BOQ');
    expect(risk).toContain('Risks');
  });

  // --- Negative controls: a real title must survive untouched ---

  it('keeps a real title exactly as the backend sent it', () => {
    const label = hitLabel(
      hit({ title: '01.10.030 - Blinding to foundations' }),
      unnamed,
    );

    expect(label).toBe('01.10.030 - Blinding to foundations');
  });

  it('keeps a title that merely contains the id', () => {
    // Only an exact match is the fallback shape; a title that quotes the id
    // is still a title someone wrote.
    const label = hitLabel(hit({ title: `Ref ${ID}` }), unnamed);

    expect(label).toBe(`Ref ${ID}`);
  });
});
