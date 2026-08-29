// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Typed client for the module builder
 * (``backend/app/modules/module_builder``, mounted at ``/api/v1/module-builder``)
 * and for the modules it produces.
 *
 * Two clients live here because they are two halves of one feature. The first
 * half describes and installs a module; the second half is how anyone then uses
 * it, and a module installed at runtime cannot ship a compiled screen, so the
 * platform renders one from the specification the module serves at
 * ``{base_path}/ui-spec``.
 *
 * Three rules this file exists to keep:
 *
 * 1. **``base_path`` always comes from the server.** The loader mounts a module
 *    at the hyphenated form of its key, and that rule lives in ``url_prefix_for``
 *    in ``spec.py``. Rebuilding it here as ``key.replace('_', '-')`` would be a
 *    second copy of a rule that belongs to the loader, and the two would drift
 *    the first time the mount changes. Every path below is derived from the
 *    ``base_path`` the API handed us.
 * 2. **Money and quantities are strings, end to end.** The generated schemas
 *    type them as ``Decimal``, which Pydantic serialises as a JSON string, and
 *    that is the point: a float cannot hold 0.1 and a contract sum is not
 *    allowed to be approximately right. They are never parsed into a JS number
 *    on the way through - see ``fields.ts``.
 * 3. **Shapes mirror ``schemas.py`` and ``spec.py`` field for field**, including
 *    the fields with defaults, because ``/ui-spec`` returns a full
 *    ``model_dump`` and the renderer reads every one of them.
 *
 * Paths carry no trailing slash: every route in ``module_builder/router.py`` and
 * in a generated ``router.py`` is declared without one, and the other spelling
 * only works by way of a 307 redirect that a POST body should not depend on.
 */
import { apiGet, apiPost, apiPatch, apiDelete } from '@/shared/lib/api';

// ---------------------------------------------------------------------------
// The specification (mirrors backend/app/modules/module_builder/spec.py)
// ---------------------------------------------------------------------------

/** ``FieldType`` in spec.py. */
export type ModuleFieldType =
  | 'text'
  | 'long_text'
  | 'integer'
  | 'number'
  | 'money'
  | 'date'
  | 'datetime'
  | 'boolean'
  | 'select';

/** ``RuleKind`` in spec.py. */
export type ModuleRuleKind = 'required' | 'positive' | 'not_future' | 'range' | 'one_of' | 'order';

export type ModuleRuleSeverity = 'error' | 'warning';

export type ModuleCategory = 'community' | 'integration' | 'regional';

/** One column, one form input, one table cell (``FieldSpec``). */
export interface ModuleFieldSpec {
  name: string;
  label: string;
  type: ModuleFieldType;
  required: boolean;
  help_text: string;
  unit: string;
  /** `select` only, and then at least two. */
  options: string[];
  /** Whether the list view shows this column. */
  in_list: boolean;
}

/** A validation rule (``RuleSpec``). */
export interface ModuleRuleSpec {
  code: string;
  message: string;
  kind: ModuleRuleKind;
  field: string;
  /** `range` only. */
  min_value: number | null;
  /** `range` only. */
  max_value: number | null;
  /** `order` only: `field` must not be later than this one. */
  other_field: string;
  severity: ModuleRuleSeverity;
}

/** The one record type a generated module manages (``EntitySpec``). */
export interface ModuleEntitySpec {
  name: string;
  display_name: string;
  plural_name: string;
  fields: ModuleFieldSpec[];
  /** When true the rows belong to a project and the list can be filtered by one. */
  project_scoped: boolean;
}

/** Everything the generator needs (``ModuleSpec``). */
export interface ModuleSpec {
  key: string;
  display_name: string;
  description: string;
  category: ModuleCategory;
  icon: string;
  version: string;
  author: string;
  entity: ModuleEntitySpec;
  rules: ModuleRuleSpec[];
  /**
   * Who wrote the specification: `assistant` when a model drafted it from a
   * sentence, `wizard` when a person filled the form in. Set by the server,
   * which is what called the model, and kept through editing, since a draft a
   * person then corrected was still drafted by a model.
   */
  drafted_by: 'assistant' | 'wizard';
}

/**
 * What ``{base_path}/ui-spec`` returns: the spec as written, plus the two
 * stamps ``_spec_json`` adds when the module is generated.
 */
export interface ModuleUiSpec extends ModuleSpec {
  generated_at?: string;
  generator?: string;
}

// ---------------------------------------------------------------------------
// Builder request and response shapes (mirrors module_builder/schemas.py)
// ---------------------------------------------------------------------------

/** One choice in the wizard's field-type list. */
export interface FieldTypeInfo {
  type: ModuleFieldType;
  label: string;
  hint: string;
}

export interface RuleKindInfo {
  kind: ModuleRuleKind;
  label: string;
  hint: string;
  applies_to: ModuleFieldType[];
  needs_other_field: boolean;
  needs_bounds: boolean;
}

/**
 * What the wizard is allowed to offer.
 *
 * Fetched rather than hardcoded: the field types and rule kinds are read out of
 * the spec on the server, so adding one there adds it to the wizard without a
 * frontend change and the two lists cannot disagree.
 */
export interface Vocabulary {
  field_types: FieldTypeInfo[];
  rule_kinds: RuleKindInfo[];
  reserved_field_names: string[];
  reserved_keys: string[];
  max_fields: number;
  /** False when no AI provider is configured; the wizard then offers only the by-hand path. */
  assistant_available: boolean;
}

export interface DraftResponse {
  spec: ModuleSpec;
  /** `assistant` when drafted from a sentence, `wizard` when filled in by hand. */
  source: string;
}

export interface PreviewFile {
  path: string;
  lines: number;
  content: string;
}

/** Everything that would land on disk, before any of it does. */
export interface PreviewResponse {
  spec: ModuleSpec;
  files: PreviewFile[];
  total_lines: number;
  /** Known before the install rather than discovered after it. */
  base_path: string;
  /**
   * Proof that these files were rendered for this person to read. Install
   * refuses a spec that arrives without it, which is what makes the review step
   * a rule the server enforces rather than a screen the wizard happens to show.
   */
  review_token: string;
}

export interface InstalledModule {
  key: string;
  module_name: string;
  display_name: string;
  version: string;
  generated_at: string;
  entity: string;
  field_count: number;
  rule_count: number;
  /** Where this module answers. The screen fetches everything under it. */
  base_path: string;
}

export interface InstalledList {
  items: InstalledModule[];
  total: number;
  /** The directory on the server these modules live in. */
  runtime_root: string;
}

export interface UninstallResponse {
  key: string;
  module_name: string;
  removed: boolean;
  data_dropped: boolean;
}

const BASE = '/v1/module-builder';

/** The field types and rule kinds the wizard may offer, read from the server's spec. */
export async function fetchVocabulary(): Promise<Vocabulary> {
  return apiGet<Vocabulary>(`${BASE}/vocabulary`);
}

/**
 * Turn a description into a specification. Writes nothing.
 *
 * `longRunning` because this one call goes out to an AI provider, and the
 * default 45s budget is a client timeout on a request that is still working.
 */
export async function draftSpec(description: string): Promise<DraftResponse> {
  return apiPost<DraftResponse, { description: string }>(`${BASE}/draft`, { description }, {
    longRunning: true,
  });
}

/** Render every file the module would consist of, without writing any. */
export async function previewModule(spec: ModuleSpec): Promise<PreviewResponse> {
  return apiPost<PreviewResponse, { spec: ModuleSpec }>(`${BASE}/preview`, { spec });
}

/**
 * Write the module and load it into the running server.
 *
 * Either it is installed and serving when this resolves, or nothing was left
 * behind: there is no half-installed state for the caller to clean up.
 */
export async function installModule(
  spec: ModuleSpec,
  reviewToken: string,
): Promise<InstalledModule> {
  return apiPost<InstalledModule, { spec: ModuleSpec; review_token: string }>(
    BASE,
    { spec, review_token: reviewToken },
    { longRunning: true },
  );
}

/** Every module built on this instance. Readable by any signed-in user. */
export async function fetchInstalledModules(): Promise<InstalledList> {
  return apiGet<InstalledList>(BASE);
}

/**
 * Take a module away again.
 *
 * The records stay unless `dropData` is asked for, so removing a module you
 * regret installing does not also lose what you recorded with it.
 */
export async function uninstallModule(key: string, dropData = false): Promise<UninstallResponse> {
  const qs = dropData ? '?drop_data=true' : '';
  return apiDelete<UninstallResponse>(`${BASE}/${encodeURIComponent(key)}${qs}`);
}

// ---------------------------------------------------------------------------
// The generated module's own API (mirrors generator.py's _router)
// ---------------------------------------------------------------------------

/**
 * One row as a generated module returns it.
 *
 * The user's own fields are open by construction - their names come from the
 * spec, not from anything compiled in - so they are typed as unknown and read
 * through the spec that describes them. `id`, `created_at` and `updated_at` are
 * on every generated row; `project_id` is there when the entity is
 * project-scoped.
 */
export interface GeneratedRecord {
  id: string;
  project_id?: string;
  created_at: string;
  updated_at: string;
  [field: string]: unknown;
}

export interface GeneratedRecordPage {
  items: GeneratedRecord[];
  total: number;
}

/**
 * A field-level finding from the generated validator, as it arrives on a 422.
 *
 * The generated router sends `detail` as a list of these rather than FastAPI's
 * own validation shape, so a refused write names the rule that refused it.
 */
export interface GeneratedFinding {
  code: string;
  message: string;
  field: string;
}

/**
 * Pull the module's own findings out of an `ApiError` body, if that is what it is.
 *
 * Returns an empty list for every other kind of 422 (FastAPI's own body shape,
 * a string detail), so a caller can fall back to the generic message.
 */
export function findingsFromError(body: unknown): GeneratedFinding[] {
  if (!body || typeof body !== 'object') return [];
  const detail = (body as { detail?: unknown }).detail;
  if (!Array.isArray(detail)) return [];
  const findings: GeneratedFinding[] = [];
  for (const entry of detail) {
    if (!entry || typeof entry !== 'object') continue;
    const e = entry as Record<string, unknown>;
    // A generated finding always carries all three. FastAPI's own 422 entries
    // carry `loc`/`msg`/`type` instead, and fall out here.
    if (typeof e.code === 'string' && typeof e.message === 'string' && typeof e.field === 'string') {
      findings.push({ code: e.code, message: e.message, field: e.field });
    }
  }
  return findings;
}

/**
 * The screen description a generated module serves.
 *
 * `basePath` is passed through untouched - it arrives as `/api/v1/<key>` and
 * the API helper strips its own `/api` prefix rather than doubling it, which is
 * exactly the guard in ``shared/lib/api.ts``.
 */
export async function fetchModuleUiSpec(basePath: string): Promise<ModuleUiSpec> {
  return apiGet<ModuleUiSpec>(`${basePath}/ui-spec`);
}

export interface ModuleRecordQuery {
  /** Only meaningful when the entity is project-scoped; the API ignores it otherwise. */
  projectId?: string | null;
  limit?: number;
  offset?: number;
}

export async function fetchModuleRecords(
  basePath: string,
  query: ModuleRecordQuery = {},
): Promise<GeneratedRecordPage> {
  const qs = new URLSearchParams();
  if (query.projectId) qs.set('project_id', query.projectId);
  if (query.limit !== undefined) qs.set('limit', String(query.limit));
  if (query.offset !== undefined) qs.set('offset', String(query.offset));
  const suffix = qs.toString();
  return apiGet<GeneratedRecordPage>(suffix ? `${basePath}?${suffix}` : basePath);
}

export async function fetchModuleRecord(basePath: string, id: string): Promise<GeneratedRecord> {
  return apiGet<GeneratedRecord>(`${basePath}/${encodeURIComponent(id)}`);
}

/**
 * Values are sent as the API types them: strings for money and quantities,
 * numbers for counts, booleans for checkboxes. See ``toPayload`` in fields.ts,
 * which is what builds this body.
 */
export async function createModuleRecord(
  basePath: string,
  payload: Record<string, unknown>,
): Promise<GeneratedRecord> {
  return apiPost<GeneratedRecord, Record<string, unknown>>(basePath, payload);
}

export async function updateModuleRecord(
  basePath: string,
  id: string,
  payload: Record<string, unknown>,
): Promise<GeneratedRecord> {
  return apiPatch<GeneratedRecord, Record<string, unknown>>(
    `${basePath}/${encodeURIComponent(id)}`,
    payload,
  );
}

export async function deleteModuleRecord(basePath: string, id: string): Promise<void> {
  await apiDelete(`${basePath}/${encodeURIComponent(id)}`);
}
