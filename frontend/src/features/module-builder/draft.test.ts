// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * What the wizard refuses before the server has to.
 *
 * Each case here corresponds to a check `spec.py` makes. If one of these ever
 * disagrees with the server the symptom is a person filling in four steps and
 * being told no on the last one, which is exactly what this file exists to
 * prevent - so a failure here is a real defect even though nothing crashes.
 */
import { describe, it, expect } from 'vitest';

import type { ModuleFieldSpec, ModuleSpec, ModuleRuleSpec, Vocabulary } from './api';
import {
  IDENTIFIER_RE,
  RULE_CODE_RE,
  addField,
  addRule,
  defaultPlural,
  emptySpec,
  kindsForType,
  moveField,
  normaliseSpec,
  removeField,
  removeOption,
  setOption,
  specProblems,
  suggestIdentifier,
  suggestRuleCode,
  updateField,
  updateRule,
} from './draft';

const VOCABULARY: Vocabulary = {
  field_types: [
    { type: 'text', label: 'Text', hint: '' },
    { type: 'money', label: 'Money', hint: '' },
    { type: 'date', label: 'Date', hint: '' },
  ],
  rule_kinds: [
    { kind: 'required', label: 'Must be filled in', hint: '', applies_to: ['text', 'money', 'date'], needs_other_field: false, needs_bounds: false },
    { kind: 'positive', label: 'Above zero', hint: '', applies_to: ['money'], needs_other_field: false, needs_bounds: false },
    { kind: 'order', label: 'Order', hint: '', applies_to: ['date'], needs_other_field: true, needs_bounds: false },
  ],
  reserved_field_names: ['id', 'metadata', 'project_id'],
  reserved_keys: ['boq', 'costs', 'core'],
  max_fields: 40,
  assistant_available: true,
};

function field(over: Partial<ModuleFieldSpec> & { name: string }): ModuleFieldSpec {
  return {
    label: over.name,
    type: 'text',
    required: false,
    help_text: '',
    unit: '',
    options: [],
    in_list: true,
    ...over,
  };
}

function rule(over: Partial<ModuleRuleSpec> & { code: string; kind: ModuleRuleSpec['kind']; field: string }): ModuleRuleSpec {
  return {
    message: 'Say something useful',
    min_value: null,
    max_value: null,
    other_field: '',
    severity: 'error',
    ...over,
  };
}

/** A spec the server would accept, so each test can break exactly one thing. */
function goodSpec(): ModuleSpec {
  return {
    key: 'pour_register',
    display_name: 'Pour Register',
    description: '',
    category: 'community',
    icon: 'Boxes',
    version: '0.1.0',
    author: '',
    drafted_by: 'wizard',
    entity: {
      name: 'pour',
      display_name: 'Pour',
      plural_name: 'Pours',
      project_scoped: true,
      fields: [
        field({ name: 'reference', label: 'Reference' }),
        field({ name: 'volume', label: 'Volume', type: 'money' }),
        field({ name: 'poured_on', label: 'Poured on', type: 'date' }),
        field({ name: 'checked_on', label: 'Checked on', type: 'date' }),
      ],
    },
    rules: [rule({ code: 'REFERENCE_REQUIRED', kind: 'required', field: 'reference' })],
  };
}

const messages = (spec: ModuleSpec) => specProblems(spec, VOCABULARY).map((p) => p.message);

describe('suggestIdentifier', () => {
  it('turns a name a person typed into a legal identifier', () => {
    expect(suggestIdentifier('Concrete Pour Register')).toBe('concrete_pour_register');
    expect(suggestIdentifier('  Site   diary  ')).toBe('site_diary');
    expect(suggestIdentifier('RFI-log')).toBe('rfi_log');
  });

  it('produces something the identifier rule actually accepts', () => {
    for (const input of ['Concrete Pour Register', 'Betonier Übersicht', 'ölçüm kaydı', 'A/B test']) {
      const out = suggestIdentifier(input);
      if (out) expect(IDENTIFIER_RE.test(out)).toBe(true);
    }
  });

  it('gives up rather than guessing when nothing is left', () => {
    expect(suggestIdentifier('журнал')).toBe('');
    expect(suggestIdentifier('123')).toBe('');
    expect(suggestIdentifier('...')).toBe('');
  });
});

describe('suggestRuleCode', () => {
  it('produces a code the shape rule accepts', () => {
    expect(RULE_CODE_RE.test(suggestRuleCode('volume', 'positive'))).toBe(true);
    expect(suggestRuleCode('volume', 'positive')).toBe('VOLUME_POSITIVE');
  });

  it('still produces a legal code from an unhelpful field name', () => {
    expect(RULE_CODE_RE.test(suggestRuleCode('a', 'required'))).toBe(true);
  });
});

describe('editing fields', () => {
  it('follows a rename through into the rules that point at the field', () => {
    // A rule names its field by name. Renaming without following would leave the
    // spec referring to a field that no longer exists, and the server would say
    // so at install time rather than here.
    let spec = goodSpec();
    spec = updateField(spec, 0, { name: 'pour_reference' });
    expect(spec.rules[0]?.field).toBe('pour_reference');
    expect(messages(spec)).toEqual([]);
  });

  it('follows a rename into the other half of an order rule', () => {
    let spec = goodSpec();
    spec = addRule(spec, 'order', 'poured_on');
    spec = updateRule(spec, 1, { other_field: 'checked_on', message: 'A pour is checked after it is poured.' });
    spec = updateField(spec, 3, { name: 'inspected_on' });
    expect(spec.rules[1]?.other_field).toBe('inspected_on');
  });

  it('takes a field s rules with it when the field goes', () => {
    let spec = goodSpec();
    spec = addRule(spec, 'positive', 'volume');
    expect(spec.rules).toHaveLength(2);
    spec = removeField(spec, 1);
    expect(spec.rules.map((r) => r.field)).toEqual(['reference']);
  });

  it('drops select options when a field stops being a select', () => {
    let spec = goodSpec();
    spec = updateField(spec, 0, { type: 'select', options: ['a', 'b'] });
    expect(spec.entity.fields[0]?.options).toEqual(['a', 'b']);
    spec = updateField(spec, 0, { type: 'text' });
    // A non-select carrying options is refused by the spec, so the change has
    // to bring the options with it.
    expect(spec.entity.fields[0]?.options).toEqual([]);
  });

  it('gives a new select two empty options to fill in', () => {
    const spec = updateField(goodSpec(), 0, { type: 'select' });
    expect(spec.entity.fields[0]?.options).toHaveLength(2);
  });

  it('moves a field without losing one', () => {
    const spec = moveField(goodSpec(), 0, 1);
    expect(spec.entity.fields.map((f) => f.name)).toEqual([
      'volume',
      'reference',
      'poured_on',
      'checked_on',
    ]);
  });

  it('does nothing when the move would fall off either end', () => {
    const spec = goodSpec();
    expect(moveField(spec, 0, -1)).toBe(spec);
    expect(moveField(spec, 3, 1)).toBe(spec);
  });

  it('does not mutate the spec it was given', () => {
    const spec = goodSpec();
    const snapshot = JSON.stringify(spec);
    addField(spec);
    removeField(spec, 0);
    updateField(spec, 0, { name: 'other' });
    moveField(spec, 0, 1);
    expect(JSON.stringify(spec)).toBe(snapshot);
  });
});

describe('specProblems', () => {
  it('says nothing about a spec the server would accept', () => {
    expect(specProblems(goodSpec(), VOCABULARY)).toEqual([]);
  });

  it('refuses a key that a shipped module already owns', () => {
    const spec = { ...goodSpec(), key: 'boq' };
    expect(messages(spec).join(' ')).toContain('already ships');
  });

  it('refuses a key that is not snake_case, or is a Python keyword', () => {
    expect(messages({ ...goodSpec(), key: 'Pour Register' }).join(' ')).toContain('snake_case');
    expect(messages({ ...goodSpec(), key: 'class' }).join(' ')).toContain('keyword');
  });

  it('refuses a key too short to recognise', () => {
    expect(messages({ ...goodSpec(), key: 'ab' }).join(' ')).toContain('too short');
  });

  it('refuses a field name the generated row already uses', () => {
    const spec = goodSpec();
    spec.entity.fields[0] = field({ name: 'metadata', label: 'Notes' });
    expect(messages(spec).join(' ')).toContain('reserved');
  });

  it('names a duplicated field once for each copy', () => {
    const spec = goodSpec();
    spec.entity.fields[1] = field({ name: 'reference', label: 'Second' });
    const said = specProblems(spec, VOCABULARY).filter((p) => p.message.includes('called reference'));
    expect(said).toHaveLength(2);
    expect(said.map((p) => p.where)).toEqual(['field:0', 'field:1']);
  });

  it('refuses a module with no rules at all', () => {
    const spec = { ...goodSpec(), rules: [] };
    expect(messages(spec).join(' ')).toContain('at least one rule');
  });

  it('refuses a numeric rule on a field that holds text', () => {
    let spec = goodSpec();
    spec = addRule(spec, 'positive', 'reference');
    spec = updateRule(spec, 1, { message: 'Must be above zero.' });
    expect(messages(spec).join(' ')).toContain('not a number');
  });

  it('refuses a date rule on a field that is not a date', () => {
    let spec = goodSpec();
    spec = addRule(spec, 'not_future', 'reference');
    spec = updateRule(spec, 1, { message: 'Cannot be in the future.' });
    expect(messages(spec).join(' ')).toContain('not a date');
  });

  it('refuses a range with no bound, and one whose bounds are inverted', () => {
    let spec = goodSpec();
    spec = addRule(spec, 'range', 'volume');
    spec = updateRule(spec, 1, { message: 'Between one and ten.' });
    expect(messages(spec).join(' ')).toContain('at least one bound');
    spec = updateRule(spec, 1, { min_value: 10, max_value: 1 });
    expect(messages(spec).join(' ')).toContain('above the upper');
  });

  it('refuses an order rule that names one field, or names itself', () => {
    let spec = goodSpec();
    spec = addRule(spec, 'order', 'poured_on');
    spec = updateRule(spec, 1, { message: 'Poured before checked.' });
    expect(messages(spec).join(' ')).toContain('only one field');
    spec = updateRule(spec, 1, { other_field: 'poured_on' });
    expect(messages(spec).join(' ')).toContain('after itself');
  });

  it('refuses a select with fewer than two real choices', () => {
    let spec = goodSpec();
    spec = updateField(spec, 0, { type: 'select', options: ['only'] });
    expect(messages(spec).join(' ')).toContain('choice of one');
  });

  it('refuses a select that lists the same choice twice', () => {
    let spec = goodSpec();
    spec = updateField(spec, 0, { type: 'select', options: ['dry', 'dry'] });
    expect(messages(spec).join(' ')).toContain('twice');
  });

  it('refuses two rules sharing a code', () => {
    const spec = goodSpec();
    spec.rules = [
      rule({ code: 'SAME', kind: 'required', field: 'reference' }),
      rule({ code: 'SAME', kind: 'required', field: 'volume' }),
    ];
    expect(messages(spec).join(' ')).toContain('Two rules are called SAME');
  });

  it('refuses a version that is not three numbers', () => {
    expect(messages({ ...goodSpec(), version: '1.0' }).join(' ')).toContain('MAJOR.MINOR.PATCH');
  });

  it('refuses a rule with no message a person could act on', () => {
    const spec = goodSpec();
    spec.rules = [rule({ code: 'REF', kind: 'required', field: 'reference', message: 'no' })];
    expect(messages(spec).join(' ')).toContain('act on');
  });

  it('files each problem where the step that owns it can find it', () => {
    const spec = { ...goodSpec(), key: '', version: 'x' };
    spec.entity.fields[0] = field({ name: '', label: '' });
    const wheres = new Set(specProblems(spec, VOCABULARY).map((p) => p.where));
    expect(wheres.has('module')).toBe(true);
    expect(wheres.has('field:0')).toBe(true);
  });

  it('works with no vocabulary, checking only what it can', () => {
    // The vocabulary is a network call. Its absence must not make the wizard
    // claim a spec is fine when its shape is plainly wrong.
    const spec = { ...goodSpec(), key: 'Not An Identifier' };
    expect(specProblems(spec).some((p) => p.message.includes('snake_case'))).toBe(true);
  });
});

describe('normaliseSpec', () => {
  it('drops the empty options a half-typed select carries', () => {
    let spec = goodSpec();
    spec = updateField(spec, 0, { type: 'select', options: ['dry', '', 'rain', '  '] });
    expect(normaliseSpec(spec).entity.fields[0]?.options).toEqual(['dry', 'rain']);
  });

  it('fills in the plural the server would have filled in', () => {
    const spec = goodSpec();
    spec.entity.plural_name = '';
    expect(normaliseSpec(spec).entity.plural_name).toBe(defaultPlural('Pour'));
  });

  it('upper-cases a rule code and trims what the user typed', () => {
    const spec = goodSpec();
    spec.rules = [rule({ code: ' ref_required ', kind: 'required', field: ' reference ', message: '  Needed  ' })];
    const out = normaliseSpec(spec).rules[0];
    expect(out?.code).toBe('REF_REQUIRED');
    expect(out?.field).toBe('reference');
    expect(out?.message).toBe('Needed');
  });

  it('does not send bounds on a rule that is not a range', () => {
    const spec = goodSpec();
    spec.rules = [rule({ code: 'REF', kind: 'required', field: 'reference', min_value: 3, max_value: 9 })];
    const out = normaliseSpec(spec).rules[0];
    expect(out?.min_value).toBeNull();
    expect(out?.max_value).toBeNull();
  });

  it('does not send another field on a rule that is not an order', () => {
    const spec = goodSpec();
    spec.rules = [rule({ code: 'REF', kind: 'required', field: 'reference', other_field: 'volume' })];
    expect(normaliseSpec(spec).rules[0]?.other_field).toBe('');
  });
});

describe('kindsForType', () => {
  it('offers only the rules that can apply to the field', () => {
    expect(kindsForType(VOCABULARY, 'money').map((k) => k.kind)).toEqual(['required', 'positive']);
    expect(kindsForType(VOCABULARY, 'text').map((k) => k.kind)).toEqual(['required']);
  });

  it('offers nothing at all before the vocabulary has arrived', () => {
    expect(kindsForType(undefined, 'text')).toEqual([]);
  });
});

describe('option editing', () => {
  it('changes one option without touching the others', () => {
    let spec = updateField(goodSpec(), 0, { type: 'select', options: ['a', 'b', 'c'] });
    spec = setOption(spec, 0, 1, 'B');
    expect(spec.entity.fields[0]?.options).toEqual(['a', 'B', 'c']);
    spec = removeOption(spec, 0, 0);
    expect(spec.entity.fields[0]?.options).toEqual(['B', 'c']);
  });
});

describe('emptySpec', () => {
  it('starts with something to fill in and problems that say what', () => {
    const spec = emptySpec();
    expect(spec.entity.fields).toHaveLength(1);
    expect(specProblems(spec, VOCABULARY).length).toBeGreaterThan(0);
  });
});
