// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Editing a module specification, and knowing in advance what the server will
 * refuse.
 *
 * The wizard collects a `ModuleSpec` and the server validates it. Sending an
 * unbuildable one and rendering the 422 would work, but it means a person
 * discovers on the last step that a field they named `id` was never going to be
 * allowed. So the checks the spec makes are read here too, before the user
 * moves on.
 *
 * This is a second reader of one rule set rather than a second rule set: every
 * check below exists because `spec.py` refuses that exact thing, and the two
 * agree by both being written from it. The server is still the authority - an
 * install that gets past these can still be refused, and that refusal is shown
 * as it arrives. What this buys is that the ordinary mistakes are answered
 * where they were made.
 *
 * Nothing here mutates: each edit returns a new spec, so the wizard's undo and
 * React's change detection both work without a deep clone at every keystroke.
 */
import type {
  ModuleEntitySpec,
  ModuleFieldSpec,
  ModuleFieldType,
  ModuleRuleKind,
  ModuleRuleSpec,
  ModuleSpec,
  Vocabulary,
} from './api';

/** `IDENTIFIER_RE` in spec.py: snake_case, no trailing or doubled underscores. */
export const IDENTIFIER_RE = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$/;

/** `_code_shape` in spec.py. */
export const RULE_CODE_RE = /^[A-Z][A-Z0-9_]{2,48}$/;

/** `_version_shape` in spec.py. */
export const VERSION_RE = /^\d+\.\d+\.\d+$/;

/**
 * Python's own keywords, which an identifier may not be.
 *
 * Mirrors `keyword.kwlist` and `keyword.softkwlist`, minus the capitalised ones
 * that a lowercase identifier cannot collide with anyway. A name that gets past
 * this and is added to a later Python is still caught by the server.
 */
export const PYTHON_KEYWORDS = new Set([
  'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def', 'del',
  'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if', 'import', 'in',
  'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
  'with', 'yield', 'case', 'match', 'type', '_',
]);

const NUMERIC_TYPES: ModuleFieldType[] = ['integer', 'number', 'money'];
const TEMPORAL_TYPES: ModuleFieldType[] = ['date', 'datetime'];

/** One thing the server would refuse, said where the user can fix it. */
export interface SpecProblem {
  /** `module`, `entity`, `field:<index>` or `rule:<index>` - which step owns it. */
  where: string;
  message: string;
}

/** A spec with nothing in it yet: one text field and no rules. */
export function emptySpec(): ModuleSpec {
  return {
    key: '',
    display_name: '',
    description: '',
    category: 'community',
    icon: 'Boxes',
    version: '0.1.0',
    author: '',
    entity: {
      name: '',
      display_name: '',
      plural_name: '',
      fields: [newField('text')],
      project_scoped: true,
    },
    rules: [],
    drafted_by: 'wizard',
  };
}

/** A blank field of the given type, with the shape the API expects. */
export function newField(type: ModuleFieldType = 'text'): ModuleFieldSpec {
  return {
    name: '',
    label: '',
    type,
    required: false,
    help_text: '',
    unit: '',
    options: type === 'select' ? ['', ''] : [],
    in_list: true,
  };
}

/**
 * A snake_case identifier from something a person typed.
 *
 * Best effort by design: it is a starting point in an editable box, not a
 * decision. Anything it cannot turn into a legal identifier comes back empty,
 * and the user names it themselves.
 */
export function suggestIdentifier(text: string): string {
  const slug = (text || '')
    .toLowerCase()
    // Keep letters and digits; everything else becomes a separator. Accented
    // letters are stripped rather than transliterated: guessing that "ü" means
    // "ue" is right in German and wrong in Turkish.
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_{2,}/g, '_');
  if (!slug || /^[0-9]/.test(slug)) return '';
  return slug;
}

/** The plural the entity falls back to, matching `_fields_are_distinct` in spec.py. */
export function defaultPlural(displayName: string): string {
  return displayName ? `${displayName}s` : '';
}

/** A rule code from a field name and a kind, e.g. `CREW_SIZE_POSITIVE`. */
export function suggestRuleCode(fieldName: string, kind: ModuleRuleKind): string {
  const base = `${fieldName}_${kind}`.toUpperCase().replace(/[^A-Z0-9_]/g, '_');
  const trimmed = base.replace(/^_+|_+$/g, '').slice(0, 49);
  return RULE_CODE_RE.test(trimmed) ? trimmed : `RULE_${trimmed}`.slice(0, 49);
}

function withEntity(spec: ModuleSpec, entity: Partial<ModuleEntitySpec>): ModuleSpec {
  return { ...spec, entity: { ...spec.entity, ...entity } };
}

export function addField(spec: ModuleSpec, type: ModuleFieldType = 'text'): ModuleSpec {
  return withEntity(spec, { fields: [...spec.entity.fields, newField(type)] });
}

export function updateField(
  spec: ModuleSpec,
  index: number,
  patch: Partial<ModuleFieldSpec>,
): ModuleSpec {
  const fields = spec.entity.fields.map((field, i) => {
    if (i !== index) return field;
    const next = { ...field, ...patch };
    // Changing the type has to bring the options with it, or a field that
    // stopped being a select carries choices the spec then refuses. A patch
    // that says what the options should be wins: it is the caller being
    // explicit, and overriding it would silently discard what they asked for.
    if (patch.type !== undefined && patch.type !== field.type && patch.options === undefined) {
      next.options = patch.type === 'select' ? (field.options.length >= 2 ? field.options : ['', '']) : [];
    }
    return next;
  });
  const renamed = patch.name !== undefined && spec.entity.fields[index]?.name !== patch.name;
  if (!renamed) return withEntity(spec, { fields });

  // A rule points at a field by name. Renaming the field without following the
  // rules would leave the spec referring to something that no longer exists.
  const before = spec.entity.fields[index]?.name ?? '';
  const after = patch.name ?? '';
  const rules = spec.rules.map((rule) => ({
    ...rule,
    field: rule.field === before ? after : rule.field,
    other_field: rule.other_field === before ? after : rule.other_field,
  }));
  return { ...withEntity(spec, { fields }), rules };
}

/** Remove a field, and with it every rule that could only have been about it. */
export function removeField(spec: ModuleSpec, index: number): ModuleSpec {
  const removed = spec.entity.fields[index];
  if (!removed) return spec;
  const fields = spec.entity.fields.filter((_, i) => i !== index);
  const rules = spec.rules.filter(
    (rule) => rule.field !== removed.name && rule.other_field !== removed.name,
  );
  return { ...withEntity(spec, { fields }), rules };
}

/** Move a field one place up or down. Out-of-range moves are no-ops. */
export function moveField(spec: ModuleSpec, index: number, delta: number): ModuleSpec {
  const target = index + delta;
  const fields = [...spec.entity.fields];
  if (index < 0 || index >= fields.length || target < 0 || target >= fields.length) return spec;
  const moved = fields[index];
  const displaced = fields[target];
  if (!moved || !displaced) return spec;
  fields[index] = displaced;
  fields[target] = moved;
  return withEntity(spec, { fields });
}

export function setOption(spec: ModuleSpec, fieldIndex: number, optionIndex: number, value: string): ModuleSpec {
  const field = spec.entity.fields[fieldIndex];
  if (!field) return spec;
  const options = field.options.map((o, i) => (i === optionIndex ? value : o));
  return updateField(spec, fieldIndex, { options });
}

export function addOption(spec: ModuleSpec, fieldIndex: number): ModuleSpec {
  const field = spec.entity.fields[fieldIndex];
  if (!field) return spec;
  return updateField(spec, fieldIndex, { options: [...field.options, ''] });
}

export function removeOption(spec: ModuleSpec, fieldIndex: number, optionIndex: number): ModuleSpec {
  const field = spec.entity.fields[fieldIndex];
  if (!field) return spec;
  return updateField(spec, fieldIndex, { options: field.options.filter((_, i) => i !== optionIndex) });
}

export function addRule(spec: ModuleSpec, kind: ModuleRuleKind, fieldName: string): ModuleSpec {
  const rule: ModuleRuleSpec = {
    code: suggestRuleCode(fieldName || 'rule', kind),
    message: '',
    kind,
    field: fieldName,
    min_value: null,
    max_value: null,
    other_field: '',
    severity: 'error',
  };
  return { ...spec, rules: [...spec.rules, rule] };
}

export function updateRule(spec: ModuleSpec, index: number, patch: Partial<ModuleRuleSpec>): ModuleSpec {
  return { ...spec, rules: spec.rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)) };
}

export function removeRule(spec: ModuleSpec, index: number): ModuleSpec {
  return { ...spec, rules: spec.rules.filter((_, i) => i !== index) };
}

/** The rule kinds that can be applied to a field of this type. */
export function kindsForType(vocabulary: Vocabulary | undefined, type: ModuleFieldType) {
  if (!vocabulary) return [];
  return vocabulary.rule_kinds.filter((kind) => kind.applies_to.includes(type));
}

function identifierProblem(value: string, what: string): string | null {
  const trimmed = (value || '').trim();
  if (!trimmed) return `${what} is needed.`;
  if (!IDENTIFIER_RE.test(trimmed)) {
    return `${what} must be snake_case: a letter, then letters, digits and single underscores.`;
  }
  if (PYTHON_KEYWORDS.has(trimmed)) return `${what} is a Python keyword.`;
  return null;
}

/**
 * Everything the server would refuse about this spec.
 *
 * An empty list does not promise the install will succeed - the key may have
 * been taken a second ago by someone else, and only the server knows that - but
 * a non-empty one is certain: each entry is a check `spec.py` makes.
 */
export function specProblems(spec: ModuleSpec, vocabulary?: Vocabulary): SpecProblem[] {
  const problems: SpecProblem[] = [];
  const say = (where: string, message: string) => problems.push({ where, message });

  const keyProblem = identifierProblem(spec.key, 'The module key');
  if (keyProblem) say('module', keyProblem);
  else if (spec.key.trim().length < 3) say('module', 'The module key is too short to be recognisable.');
  else if (vocabulary?.reserved_keys.includes(spec.key.trim())) {
    say('module', `A module called ${spec.key} already ships with the platform, and it would win.`);
  }

  if (!spec.display_name.trim()) say('module', 'A module needs a name a person can read.');
  if (!VERSION_RE.test(spec.version.trim())) say('module', 'The version must be MAJOR.MINOR.PATCH.');

  const entityProblem = identifierProblem(spec.entity.name, 'The record name');
  if (entityProblem) say('entity', entityProblem);
  if (!spec.entity.display_name.trim()) say('entity', 'The record needs a name a person can read.');

  const fields = spec.entity.fields;
  if (fields.length === 0) say('entity', 'A module needs at least one field.');
  if (vocabulary && fields.length > vocabulary.max_fields) {
    say('entity', `A module may have at most ${vocabulary.max_fields} fields.`);
  }

  const counts = new Map<string, number>();
  for (const field of fields) {
    const name = field.name.trim();
    if (name) counts.set(name, (counts.get(name) ?? 0) + 1);
  }

  fields.forEach((field, index) => {
    const where = `field:${index}`;
    const name = field.name.trim();
    const problem = identifierProblem(name, 'The field name');
    if (problem) say(where, problem);
    else if (vocabulary?.reserved_field_names.includes(name)) {
      say(where, `${name} is reserved: every generated record already has one.`);
    } else if ((counts.get(name) ?? 0) > 1) {
      say(where, `Two fields are called ${name}.`);
    }
    if (!field.label.trim()) say(where, 'Every field needs a label - it is what the user reads.');
    if (field.type === 'select') {
      const options = field.options.map((o) => o.trim()).filter(Boolean);
      if (options.length < 2) say(where, 'A choice of one is not a choice: add a second option.');
      if (new Set(options).size !== options.length) say(where, 'The same option is listed twice.');
    }
  });

  if (spec.rules.length === 0) {
    // Rule 4 of the platform: validation is part of the workflow, not an option.
    say('rules', 'A module needs at least one rule. Validation is part of the workflow here.');
  }

  const byName = new Map(fields.map((f) => [f.name.trim(), f]));
  const ruleCodes = new Map<string, number>();
  for (const rule of spec.rules) {
    const code = rule.code.trim().toUpperCase();
    if (code) ruleCodes.set(code, (ruleCodes.get(code) ?? 0) + 1);
  }

  spec.rules.forEach((rule, index) => {
    const where = `rule:${index}`;
    const code = rule.code.trim().toUpperCase();
    if (!RULE_CODE_RE.test(code)) say(where, 'A rule code is UPPER_SNAKE, 3 to 49 characters.');
    else if ((ruleCodes.get(code) ?? 0) > 1) say(where, `Two rules are called ${code}.`);
    if (rule.message.trim().length < 4) say(where, 'A rule needs a message a person can act on.');

    const field = byName.get(rule.field.trim());
    if (!field) {
      say(where, `This rule names ${rule.field || 'no field'}, which does not exist.`);
      return;
    }
    if (rule.kind === 'range') {
      if (rule.min_value === null && rule.max_value === null) say(where, 'A range needs at least one bound.');
      if (rule.min_value !== null && rule.max_value !== null && rule.min_value > rule.max_value) {
        say(where, 'The lower bound is above the upper one.');
      }
    }
    if ((rule.kind === 'positive' || rule.kind === 'range') && !NUMERIC_TYPES.includes(field.type)) {
      say(where, `${field.label || field.name} is not a number, so this rule cannot apply to it.`);
    }
    if (rule.kind === 'one_of' && field.type !== 'select') {
      say(where, `${field.label || field.name} has no list of choices to check against.`);
    }
    if (rule.kind === 'not_future' && !TEMPORAL_TYPES.includes(field.type)) {
      say(where, `${field.label || field.name} is not a date.`);
    }
    if (rule.kind === 'order') {
      const other = byName.get(rule.other_field.trim());
      if (!other) say(where, 'This rule compares an order but names only one field.');
      else if (rule.other_field.trim() === rule.field.trim()) say(where, 'A field cannot come after itself.');
      else if (!TEMPORAL_TYPES.includes(field.type) || !TEMPORAL_TYPES.includes(other.type)) {
        say(where, 'Only dates can be put in order.');
      }
    }
  });

  return problems;
}

/**
 * The spec as the API wants it: trimmed, with the plural filled in and the
 * empty select options dropped.
 *
 * The wizard keeps blank options around while a person is typing them; the API
 * refuses a select whose options include an empty string, and it should.
 */
export function normaliseSpec(spec: ModuleSpec): ModuleSpec {
  const displayName = spec.entity.display_name.trim();
  return {
    ...spec,
    key: spec.key.trim(),
    display_name: spec.display_name.trim(),
    description: spec.description.trim(),
    version: spec.version.trim(),
    author: spec.author.trim(),
    entity: {
      ...spec.entity,
      name: spec.entity.name.trim(),
      display_name: displayName,
      plural_name: spec.entity.plural_name.trim() || defaultPlural(displayName),
      fields: spec.entity.fields.map((field) => ({
        ...field,
        name: field.name.trim(),
        label: field.label.trim(),
        help_text: field.help_text.trim(),
        unit: field.unit.trim(),
        options: field.type === 'select' ? field.options.map((o) => o.trim()).filter(Boolean) : [],
      })),
    },
    rules: spec.rules.map((rule) => ({
      ...rule,
      code: rule.code.trim().toUpperCase(),
      message: rule.message.trim(),
      field: rule.field.trim(),
      other_field: rule.kind === 'order' ? rule.other_field.trim() : '',
      min_value: rule.kind === 'range' ? rule.min_value : null,
      max_value: rule.kind === 'range' ? rule.max_value : null,
    })),
  };
}
