// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
/**
 * A breakdown keyed by an id has to read as names - issue #441.
 *
 * The reporter's KPI groups by `boq_id`, which is the only thing the
 * database can group by, and the drill-down drawer showed the result as a
 * column of uuids. The backend now names each group, and this is the half
 * of that which the screen owes: printing the name rather than the record
 * that carries it, printing something readable where there is no name at
 * all, and offering a picker that agrees with what the server stores.
 *
 * Run:
 *     cd frontend
 *     npx vitest run src/features/bi-dashboards/__tests__/breakdownLabel.test.ts
 */

import { describe, it, expect } from 'vitest';
import { drillFieldText, defaultLabelField, NULL_GROUP_KEY } from '../breakdownLabel';
import type { KpiSpecEntity } from '../api';

const boqPosition: KpiSpecEntity = {
  name: 'boq_position',
  source_module: 'oe_boq',
  description: 'One priced line of a Bill of Quantities.',
  fields: [
    { name: 'amount', kind: 'numeric' },
    { name: 'boq_id', kind: 'uuid' },
    { name: 'boq_name', kind: 'text' },
    { name: 'unit', kind: 'text' },
  ],
  numeric_fields: ['amount'],
  groupable_fields: ['boq_id', 'boq_name', 'unit'],
  display_name_for: { boq_id: 'boq_name' },
  json_path_fields: [],
  // A priced line carries the bill it is part of, so it is one of the
  // entities a KPI can be read one estimate at a time (issue #447).
  narrows_to_estimate: true,
};

describe('drillFieldText', () => {
  it('prints the estimate name, not the record that carries it', () => {
    const group = { label: 'Warehouse extension', value: '18400.00' };
    expect(drillFieldText(group, '(not set)')).toBe('Warehouse extension: 18400.00');
  });

  it('maps the reserved key to words a reader recognises', () => {
    // The backend cannot translate it: it is a dict key, and a word in
    // that slot cannot be told apart from a real group value.
    expect(drillFieldText(NULL_GROUP_KEY, '(not set)')).toBe('(not set)');
  });

  it('maps the reserved key when it arrives as a group label', () => {
    // An estimate nobody named. The label is the token, the value is a
    // real amount, and printing "__null__: 900.00" is the plumbing
    // showing through.
    const group = { label: NULL_GROUP_KEY, value: '900.00' };
    expect(drillFieldText(group, '(not set)')).toBe('(not set): 900.00');
  });

  it('leaves a group value that merely looks unset alone', () => {
    // Only the reserved key is reserved. A bid actually called "(not set)"
    // is a name somebody typed and reads back as itself.
    expect(drillFieldText('(not set)', 'Not named')).toBe('(not set)');
  });

  it('still spells out a plain value', () => {
    expect(drillFieldText('18400.00', '(not set)')).toBe('18400.00');
  });
});

describe('defaultLabelField', () => {
  it('names the field that reads an id, so the form matches the server', () => {
    expect(defaultLabelField(boqPosition, 'boq_id')).toBe('boq_name');
  });

  it('offers nothing for a group that is already its own name', () => {
    expect(defaultLabelField(boqPosition, 'unit')).toBe('');
  });

  it('offers nothing when there is no breakdown at all', () => {
    expect(defaultLabelField(boqPosition, '')).toBe('');
  });

  it('survives a catalog served before the map existed', () => {
    // An older backend answers /kpis/spec-catalog without the field, and
    // a picker that throws on it is worse than one that offers no default.
    const older = { ...boqPosition } as Partial<KpiSpecEntity>;
    delete older.display_name_for;
    expect(defaultLabelField(older as KpiSpecEntity, 'boq_id')).toBe('');
  });
});
