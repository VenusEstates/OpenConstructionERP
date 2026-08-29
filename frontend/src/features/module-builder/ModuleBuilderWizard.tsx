// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Describing a module, and installing the result.
 *
 * Four steps, and the order is the argument: describe it, say what a record
 * holds, say what the module checks, then read what will be written before any
 * of it is. The assistant, when one is connected, only ever produces a
 * specification - it never writes Python - so the worst a bad draft can do is
 * describe a module that fails validation. Everything after the first step is
 * the same whether a person or an assistant filled it in.
 *
 * The review step is where the confirmation lives. Installing writes files onto
 * the server and loads them into the running process, so the step before it
 * lists every file, its length, and the URL the module will answer on. Nothing
 * about the module is a surprise by the time the button is pressed.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  ArrowDown,
  Check,
  FileCode,
  Plus,
  Sparkles,
  Table2,
  Trash2,
  Wand2,
} from 'lucide-react';
import clsx from 'clsx';

import { Button, Input, WideModal } from '@/shared/ui';
import { getErrorMessage } from '@/shared/lib/api';
import { useToastStore } from '@/stores/useToastStore';

import {
  draftSpec,
  fetchVocabulary,
  installModule,
  previewModule,
  type InstalledModule,
  type ModuleFieldType,
  type ModuleRuleKind,
  type ModuleSpec,
  type PreviewResponse,
  type Vocabulary,
} from './api';
import {
  addField,
  addOption,
  addRule,
  defaultPlural,
  emptySpec,
  kindsForType,
  moveField,
  normaliseSpec,
  removeField,
  removeOption,
  removeRule,
  setOption,
  specProblems,
  suggestIdentifier,
  updateField,
  updateRule,
  type SpecProblem,
} from './draft';

type Step = 'describe' | 'record' | 'rules' | 'review' | 'done';

const STEP_ORDER: Step[] = ['describe', 'record', 'rules', 'review'];

export interface ModuleBuilderWizardProps {
  open: boolean;
  onClose: () => void;
  /** Called once the module is installed and serving. */
  onInstalled?: (module: InstalledModule) => void;
}

export function ModuleBuilderWizard({ open, onClose, onInstalled }: ModuleBuilderWizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const addToast = useToastStore((s) => s.addToast);

  const [step, setStep] = useState<Step>('describe');
  const [spec, setSpec] = useState<ModuleSpec>(() => emptySpec());
  const [sentence, setSentence] = useState('');
  const [drafting, setDrafting] = useState(false);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [installing, setInstalling] = useState(false);
  const [installed, setInstalled] = useState<InstalledModule | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const vocabularyQuery = useQuery({
    queryKey: ['module-builder', 'vocabulary'],
    queryFn: fetchVocabulary,
    enabled: open,
    staleTime: 30 * 60_000,
  });
  const vocabulary = vocabularyQuery.data;

  // A fresh wizard every time it opens. Reusing the last run's spec would be a
  // surprise, and reusing the last run's preview would be a lie.
  useEffect(() => {
    if (!open) return;
    setStep('describe');
    setSpec(emptySpec());
    setSentence('');
    setPreview(null);
    setInstalled(null);
    setRefusal(null);
  }, [open]);

  const problems = useMemo(() => specProblems(spec, vocabulary), [spec, vocabulary]);
  const problemsIn = (prefix: string) =>
    problems.filter((p) => p.where === prefix || p.where.startsWith(`${prefix}:`));

  const stepProblems: Record<Step, SpecProblem[]> = {
    describe: problemsIn('module'),
    record: [...problemsIn('entity'), ...problemsIn('field')],
    rules: problemsIn('rules').concat(problemsIn('rule')),
    review: problems,
    done: [],
  };

  const handleDraft = async () => {
    setDrafting(true);
    setRefusal(null);
    try {
      const result = await draftSpec(sentence);
      setSpec(result.spec);
      setStep('record');
    } catch (err) {
      // A 422 here is the assistant declining to describe the module, which is
      // a real answer and not an error to bury in a toast.
      setRefusal(getErrorMessage(err));
    } finally {
      setDrafting(false);
    }
  };

  const goToReview = async () => {
    setRefusal(null);
    try {
      const result = await previewModule(normaliseSpec(spec));
      setPreview(result);
      setStep('review');
    } catch (err) {
      setRefusal(getErrorMessage(err));
    }
  };

  const handleInstall = async () => {
    // Install what was reviewed, not what the form holds now: the token is
    // bound to the previewed spec, so sending anything else is refused by the
    // server rather than quietly writing code nobody read. Install is only
    // reachable from the review step, so a missing preview is a bug, not a
    // state a user can reach.
    if (!preview) return;
    setInstalling(true);
    setRefusal(null);
    try {
      const result = await installModule(preview.spec, preview.review_token);
      setInstalled(result);
      setStep('done');
      addToast({
        type: 'success',
        title: t('module_builder.installed_toast', {
          name: result.display_name,
          defaultValue: '{{name}} is installed and serving',
        }),
      });
      // The installed list is what every screen resolves a module's URL from.
      void qc.invalidateQueries({ queryKey: ['module-builder', 'installed'] });
      onInstalled?.(result);
    } catch (err) {
      setRefusal(getErrorMessage(err));
    } finally {
      setInstalling(false);
    }
  };

  const openInstalled = () => {
    if (!installed) return;
    onClose();
    navigate(`/modules/${installed.key}`);
  };

  const currentIndex = STEP_ORDER.indexOf(step);
  const blocked = stepProblems[step].length > 0;

  return (
    <WideModal
      open={open}
      onClose={onClose}
      size="xl"
      title={t('module_builder.title', { defaultValue: 'Module builder' })}
      subtitle={t('module_builder.subtitle', {
        defaultValue: 'Describe what you need and the platform builds it.',
      })}
      busy={drafting || installing}
      footer={
        <WizardFooter
          step={step}
          blocked={blocked}
          drafting={drafting}
          installing={installing}
          onBack={() => setStep(STEP_ORDER[Math.max(currentIndex - 1, 0)] ?? 'describe')}
          onNext={() => {
            if (step === 'describe') setStep('record');
            else if (step === 'record') setStep('rules');
            else if (step === 'rules') void goToReview();
          }}
          onInstall={() => void handleInstall()}
          onOpen={openInstalled}
          onClose={onClose}
        />
      }
    >
      <div className="space-y-5" data-testid="module-builder-wizard">
        {step !== 'done' && <StepBar current={step} onJump={setStep} />}

        {/* Marks the specification itself rather than the wizard, so it sits
            beside StepBar and covers record, rules and review in one place.
            A spec the user typed by hand carries drafted_by 'wizard' and shows
            nothing, which is why there is no "not AI" counterpart here: on this
            screen the by-hand path is the unremarkable one. Editing a drafted
            spec does not clear it, because a model still wrote the first
            version. */}
        {step !== 'done' && spec.drafted_by === 'assistant' && (
          <p className="flex flex-wrap items-center gap-1.5" data-testid="module-builder-ai-mark">
            <span className="inline-flex items-center gap-1 rounded-full bg-oe-blue/10 px-2 py-0.5 text-xs font-medium text-oe-blue-text">
              <Sparkles className="h-3 w-3" />
              {t('module_builder.spec_ai_drafted')}
            </span>
            <span className="text-xs text-content-secondary">
              {t('module_builder.spec_ai_drafted_hint')}
            </span>
          </p>
        )}

        {refusal && (
          <p
            role="alert"
            className="flex items-start gap-2 rounded-lg bg-semantic-error-bg px-3 py-2 text-sm text-semantic-error"
          >
            <AlertTriangle size={15} className="mt-px shrink-0" />
            {refusal}
          </p>
        )}

        {step === 'describe' && (
          <DescribeStep
            spec={spec}
            setSpec={setSpec}
            sentence={sentence}
            setSentence={setSentence}
            drafting={drafting}
            assistantAvailable={vocabulary?.assistant_available ?? false}
            onDraft={() => void handleDraft()}
            onClose={onClose}
            problems={stepProblems.describe}
          />
        )}

        {step === 'record' && (
          <RecordStep spec={spec} setSpec={setSpec} vocabulary={vocabulary} problems={stepProblems.record} />
        )}

        {step === 'rules' && (
          <RulesStep spec={spec} setSpec={setSpec} vocabulary={vocabulary} problems={stepProblems.rules} />
        )}

        {step === 'review' && preview && <ReviewStep preview={preview} />}

        {step === 'done' && installed && <DoneStep installed={installed} />}
      </div>
    </WideModal>
  );
}

/* ── Step chrome ─────────────────────────────────────────────────────────── */

/**
 * The rail across the top of the wizard. It answers two questions a form in
 * steps has to answer at a glance: where am I, and how much is left.
 *
 * A finished step is a button back to itself. Going back used to mean pressing
 * Back once per step, which is the same journey made longer, and the numbered
 * nodes already looked pressable. Steps ahead are not links: the wizard refuses
 * to advance past a step with problems on it, so offering to skip forward would
 * promise something the footer then takes away.
 */
function StepBar({ current, onJump }: { current: Step; onJump: (step: Step) => void }) {
  const { t } = useTranslation();
  const labels: Record<Step, string> = {
    describe: t('module_builder.step_describe', { defaultValue: 'Describe' }),
    record: t('module_builder.step_record', { defaultValue: 'What a record holds' }),
    rules: t('module_builder.step_rules', { defaultValue: 'What it checks' }),
    review: t('module_builder.step_review', { defaultValue: 'Review' }),
    done: '',
  };
  const index = STEP_ORDER.indexOf(current);

  return (
    <nav aria-label={t('module_builder.subtitle', { defaultValue: 'Describe what you need and the platform builds it.' })}>
      <ol className="flex items-start">
        {STEP_ORDER.map((step, i) => {
          const done = i < index;
          const here = i === index;
          const node = (
            <span
              className={clsx(
                'relative z-10 flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-semibold transition-colors',
                done && 'bg-oe-blue text-white',
                here && 'bg-oe-blue-subtle text-oe-blue-text ring-2 ring-oe-blue',
                !done && !here && 'bg-surface-secondary text-content-quaternary ring-1 ring-border-light',
              )}
            >
              {done ? <Check size={13} strokeWidth={3} /> : i + 1}
            </span>
          );
          // The label sits inside the button rather than beside it, so the
          // accessible name is already "2 What a record holds" and needs no
          // separate aria-label, and so the whole column is the target instead
          // of a 28px circle.
          const body = (
            <>
              {node}
              <span
                className={clsx(
                  'text-center text-[11px] leading-tight',
                  here ? 'font-medium text-content-primary' : 'text-content-tertiary',
                )}
              >
                {labels[step]}
              </span>
            </>
          );
          return (
            <li key={step} className="relative flex flex-1 flex-col items-center px-1">
              {/* Drawn from the previous node's centre to this one, so the rail
                  fills as the reader advances instead of sitting there whole. */}
              {i > 0 && (
                <span
                  aria-hidden
                  className={clsx(
                    'absolute right-1/2 top-3.5 h-0.5 w-full -translate-y-1/2 transition-colors',
                    i <= index ? 'bg-oe-blue' : 'bg-border-light',
                  )}
                />
              )}
              {done ? (
                <button
                  type="button"
                  onClick={() => onJump(step)}
                  className="flex w-full flex-col items-center gap-1.5 rounded-lg py-0.5 transition-colors hover:text-content-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-oe-blue/40"
                  data-testid={`module-builder-step-${step}`}
                >
                  {body}
                </button>
              ) : (
                <div className="flex w-full flex-col items-center gap-1.5 py-0.5">{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function ProblemList({ problems }: { problems: SpecProblem[] }) {
  if (problems.length === 0) return null;
  return (
    <ul className="space-y-1 rounded-lg bg-semantic-warning-bg px-3 py-2 text-xs text-semantic-warning">
      {problems.map((problem, i) => (
        <li key={`${problem.where}-${i}`} className="flex items-start gap-1.5">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          {problem.message}
        </li>
      ))}
    </ul>
  );
}

interface FooterProps {
  step: Step;
  blocked: boolean;
  drafting: boolean;
  installing: boolean;
  onBack: () => void;
  onNext: () => void;
  onInstall: () => void;
  onOpen: () => void;
  onClose: () => void;
}

function WizardFooter({ step, blocked, drafting, installing, onBack, onNext, onInstall, onOpen, onClose }: FooterProps) {
  const { t } = useTranslation();

  if (step === 'done') {
    return (
      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          {t('common.close', { defaultValue: 'Close' })}
        </Button>
        <Button variant="primary" onClick={onOpen} data-testid="module-builder-open">
          {t('module_builder.open_module', { defaultValue: 'Open the module' })}
        </Button>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-2">
      <Button
        variant="ghost"
        icon={<ArrowLeft size={14} />}
        onClick={step === 'describe' ? onClose : onBack}
        disabled={drafting || installing}
      >
        {step === 'describe'
          ? t('common.cancel', { defaultValue: 'Cancel' })
          : t('common.back', { defaultValue: 'Back' })}
      </Button>
      {step === 'review' ? (
        <Button
          variant="primary"
          icon={<Check size={14} />}
          loading={installing}
          disabled={blocked}
          onClick={onInstall}
          data-testid="module-builder-install"
        >
          {t('module_builder.install', { defaultValue: 'Install it' })}
        </Button>
      ) : (
        <Button
          variant="primary"
          icon={<ArrowRight size={14} />}
          iconPosition="right"
          disabled={blocked || drafting}
          onClick={onNext}
          data-testid="module-builder-next"
        >
          {t('common.next', { defaultValue: 'Next' })}
        </Button>
      )}
    </div>
  );
}

/* ── Step 1: describe ────────────────────────────────────────────────────── */

interface DescribeStepProps {
  spec: ModuleSpec;
  setSpec: (spec: ModuleSpec) => void;
  sentence: string;
  setSentence: (text: string) => void;
  drafting: boolean;
  assistantAvailable: boolean;
  onDraft: () => void;
  /** Dismisses the wizard when the reader leaves to connect a provider. */
  onClose: () => void;
  problems: SpecProblem[];
}

function DescribeStep({
  spec,
  setSpec,
  sentence,
  setSentence,
  drafting,
  assistantAvailable,
  onDraft,
  onClose,
  problems,
}: DescribeStepProps) {
  const { t } = useTranslation();

  /** Naming the module names its key and its record, until the user says otherwise. */
  const setDisplayName = (value: string) => {
    const suggested = suggestIdentifier(value);
    const keyWasSuggested = spec.key === '' || spec.key === suggestIdentifier(spec.display_name);
    setSpec({
      ...spec,
      display_name: value,
      key: keyWasSuggested ? suggested : spec.key,
    });
  };

  return (
    <div className="space-y-4">
      {assistantAvailable ? (
        <div className="rounded-xl border border-border-light bg-surface-secondary/50 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-content-primary">
            <Sparkles size={14} className="text-oe-blue-text" />
            {t('module_builder.describe_heading', { defaultValue: 'Say what you need' })}
          </p>
          <textarea
            value={sentence}
            rows={3}
            onChange={(e) => setSentence(e.target.value)}
            placeholder={t('module_builder.describe_placeholder', {
              defaultValue:
                'A register of concrete pours: pour reference, date, volume in cubic metres, the mix, and who signed it off.',
            })}
            className="w-full rounded-lg border border-border-light bg-surface-primary px-3 py-2 text-sm text-content-primary focus:outline-none focus:ring-2 focus:ring-oe-blue/40"
            data-testid="module-builder-description"
          />
          <div className="mt-2 flex items-center justify-between gap-2">
            <p className="text-xs text-content-tertiary">
              {t('module_builder.describe_note', {
                defaultValue:
                  'The assistant writes a description of the module, never its code. You read it on the next step and can change every part of it.',
              })}
            </p>
            <Button
              variant="secondary"
              size="sm"
              icon={<Wand2 size={13} />}
              loading={drafting}
              disabled={sentence.trim().length < 10}
              onClick={onDraft}
              data-testid="module-builder-draft"
            >
              {t('module_builder.draft', { defaultValue: 'Draft it' })}
            </Button>
          </div>
        </div>
      ) : (
        // The by-hand path is not a degraded one, so this says what is missing
        // and where to fix it rather than sitting on a disabled control the
        // reader has to guess about. Leaving closes the wizard: the header
        // mounts it above every page, so a modal left open would follow the
        // reader onto the settings screen it just sent them to.
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-surface-secondary/60 px-3 py-2 text-xs text-content-tertiary">
          <span>
            {t('module_builder.no_assistant', {
              defaultValue:
                'No AI provider is connected, so the module is described by hand. Everything below works the same way either way.',
            })}
          </span>
          <Link
            to="/settings?tab=ai"
            onClick={onClose}
            className="inline-flex items-center gap-1 font-medium text-oe-blue-text hover:text-oe-blue-hover"
            data-testid="module-builder-connect-ai"
          >
            {t('module_builder.connect_ai', { defaultValue: 'Connect an AI provider' })}
            <ArrowRight size={12} className="shrink-0" />
          </Link>
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          label={t('module_builder.field_display_name', { defaultValue: 'Module name' })}
          value={spec.display_name}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Concrete Pour Register"
          data-testid="module-builder-name"
        />
        <Input
          label={t('module_builder.field_key', { defaultValue: 'Key' })}
          hint={t('module_builder.field_key_hint', {
            defaultValue: 'Used for the folder, the table and the URL. Lower case, underscores.',
          })}
          value={spec.key}
          onChange={(e) => setSpec({ ...spec, key: e.target.value })}
          placeholder="concrete_pours"
          data-testid="module-builder-key"
        />
      </div>

      <div>
        <label
          htmlFor="module-builder-purpose"
          className="mb-1 block text-sm font-medium text-content-secondary"
        >
          {t('common.description', { defaultValue: 'Description' })}
        </label>
        <textarea
          id="module-builder-purpose"
          value={spec.description}
          rows={2}
          onChange={(e) => setSpec({ ...spec, description: e.target.value })}
          className="w-full rounded-lg border border-border-light bg-surface-primary px-3 py-2 text-sm text-content-primary focus:outline-none focus:ring-2 focus:ring-oe-blue/40"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Input
          label={t('module_builder.field_version', { defaultValue: 'Version' })}
          value={spec.version}
          onChange={(e) => setSpec({ ...spec, version: e.target.value })}
        />
        <Input
          label={t('module_builder.field_author', { defaultValue: 'Author' })}
          value={spec.author}
          onChange={(e) => setSpec({ ...spec, author: e.target.value })}
        />
      </div>

      <ProblemList problems={problems} />
    </div>
  );
}

/* ── Step 2: the record and its fields ───────────────────────────────────── */

interface EditStepProps {
  spec: ModuleSpec;
  setSpec: (spec: ModuleSpec) => void;
  vocabulary: Vocabulary | undefined;
  problems: SpecProblem[];
}

/** Roughly how wide a value of this type tends to be, for the placeholder bar. */
const PREVIEW_WIDTH: Record<string, string> = {
  bool: 'w-6',
  number: 'w-10',
  integer: 'w-10',
  currency: 'w-14',
  date: 'w-16',
  datetime: 'w-20',
  select: 'w-14',
  text: 'w-24',
};

/**
 * The table the module will actually serve, drawn from the fields as they are
 * typed. This step asks the reader to describe a register in the abstract, and
 * a register is a thing people recognise by looking at it, so the abstraction
 * was doing all the work.
 *
 * The cells are bars, not sample values. Inventing plausible figures here would
 * be the same mistake the insights panel refuses to make: a made-up row reads
 * as if it were a real one, and this is the screen where someone decides
 * whether the module is right. A bar says a value goes here and claims nothing
 * about what it is.
 */
function TablePreview({ spec }: { spec: ModuleSpec }) {
  const { t } = useTranslation();
  const columns = spec.entity.fields.filter((f) => f.in_list && f.label.trim() !== '');
  const caption = spec.entity.plural_name.trim() || spec.display_name.trim();

  return (
    <div
      className="overflow-hidden rounded-xl border border-border-light bg-surface-secondary/30"
      data-testid="module-builder-preview"
    >
      <div className="flex items-center gap-2 border-b border-border-light bg-surface-primary/60 px-3 py-2">
        <Table2 size={13} className="shrink-0 text-content-tertiary" />
        <p className="min-w-0 truncate text-xs font-medium text-content-secondary">
          {t('module_builder.preview_table', { defaultValue: 'The table this builds' })}
          {caption && <span className="ml-1.5 font-normal text-content-tertiary">{caption}</span>}
        </p>
      </div>
      {columns.length === 0 ? (
        <p
          className="px-3 py-5 text-center text-xs text-content-tertiary"
          data-testid="module-builder-preview-empty"
        >
          {t('module_builder.preview_empty', {
            defaultValue: 'No field is shown in the table yet, so the register would open on empty columns.',
          })}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border-light">
                {columns.map((field, i) => (
                  <th
                    key={i}
                    className="whitespace-nowrap px-3 py-1.5 text-xs font-medium text-content-secondary"
                  >
                    {field.label}
                    {field.unit.trim() !== '' && (
                      <span className="ml-1 font-normal text-content-quaternary">{field.unit}</span>
                    )}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[0, 1].map((row) => (
                <tr key={row} className="border-b border-border-light/50 last:border-0">
                  {columns.map((field, i) => (
                    <td key={i} className="px-3 py-2">
                      <span
                        aria-hidden
                        className={clsx(
                          'block h-2 rounded-full bg-surface-tertiary',
                          PREVIEW_WIDTH[field.type] ?? 'w-20',
                        )}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RecordStep({ spec, setSpec, vocabulary, problems }: EditStepProps) {
  const { t } = useTranslation();
  const types = vocabulary?.field_types ?? [];
  const atLimit = vocabulary !== undefined && spec.entity.fields.length >= vocabulary.max_fields;

  const setEntityDisplayName = (value: string) => {
    const nameWasSuggested =
      spec.entity.name === '' || spec.entity.name === suggestIdentifier(spec.entity.display_name);
    const pluralWasSuggested =
      spec.entity.plural_name === '' || spec.entity.plural_name === defaultPlural(spec.entity.display_name);
    setSpec({
      ...spec,
      entity: {
        ...spec.entity,
        display_name: value,
        name: nameWasSuggested ? suggestIdentifier(value) : spec.entity.name,
        plural_name: pluralWasSuggested ? defaultPlural(value) : spec.entity.plural_name,
      },
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Input
          label={t('module_builder.field_record_name', { defaultValue: 'One record is called' })}
          value={spec.entity.display_name}
          onChange={(e) => setEntityDisplayName(e.target.value)}
          placeholder="Pour"
          data-testid="module-builder-entity-name"
        />
        <Input
          label={t('module_builder.field_record_plural', { defaultValue: 'Several are called' })}
          value={spec.entity.plural_name}
          onChange={(e) => setSpec({ ...spec, entity: { ...spec.entity, plural_name: e.target.value } })}
          placeholder="Pours"
        />
        <Input
          label={t('module_builder.field_record_key', { defaultValue: 'Table name' })}
          value={spec.entity.name}
          onChange={(e) => setSpec({ ...spec, entity: { ...spec.entity, name: e.target.value } })}
          placeholder="pour"
        />
      </div>

      <label className="flex items-start gap-2.5 text-sm">
        <input
          type="checkbox"
          checked={spec.entity.project_scoped}
          onChange={(e) => setSpec({ ...spec, entity: { ...spec.entity, project_scoped: e.target.checked } })}
          className="mt-0.5 h-4 w-4 rounded border-border-light text-oe-blue focus:ring-oe-blue/40"
        />
        <span>
          <span className="block text-content-primary">
            {t('module_builder.project_scoped', { defaultValue: 'These records belong to a project' })}
          </span>
          <span className="block text-xs text-content-tertiary">
            {t('module_builder.project_scoped_hint', {
              defaultValue: 'Almost everything on a construction project does. Leave it on unless it does not.',
            })}
          </span>
        </span>
      </label>

      <TablePreview spec={spec} />

      <div className="space-y-3">
        {spec.entity.fields.map((field, index) => (
          <div
            key={index}
            className="rounded-xl border border-border-light p-3 transition-colors focus-within:border-oe-blue/50"
          >
            <div className="grid gap-2 sm:grid-cols-12">
              <div className="sm:col-span-4">
                <Input
                  label={t('module_builder.field_label', { defaultValue: 'Label' })}
                  value={field.label}
                  onChange={(e) => {
                    const nameWasSuggested = field.name === '' || field.name === suggestIdentifier(field.label);
                    setSpec(
                      updateField(spec, index, {
                        label: e.target.value,
                        ...(nameWasSuggested ? { name: suggestIdentifier(e.target.value) } : {}),
                      }),
                    );
                  }}
                  data-testid={`module-builder-field-label-${index}`}
                />
              </div>
              <div className="sm:col-span-3">
                <Input
                  label={t('module_builder.field_name', { defaultValue: 'Column' })}
                  value={field.name}
                  onChange={(e) => setSpec(updateField(spec, index, { name: e.target.value }))}
                />
              </div>
              <div className="sm:col-span-3">
                <label className="mb-1 block text-sm font-medium text-content-secondary">
                  {t('common.type', { defaultValue: 'Type' })}
                </label>
                <select
                  value={field.type}
                  onChange={(e) =>
                    setSpec(updateField(spec, index, { type: e.target.value as ModuleFieldType }))
                  }
                  className="w-full rounded-lg border border-border-light bg-surface-primary px-3 py-2 text-sm text-content-primary focus:outline-none focus:ring-2 focus:ring-oe-blue/40"
                  data-testid={`module-builder-field-type-${index}`}
                >
                  {types.map((type) => (
                    <option key={type.type} value={type.type}>
                      {type.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="sm:col-span-2">
                <Input
                  label={t('common.unit', { defaultValue: 'Unit' })}
                  value={field.unit}
                  onChange={(e) => setSpec(updateField(spec, index, { unit: e.target.value }))}
                  placeholder="m3"
                />
              </div>
            </div>

            {field.type === 'select' && (
              <div className="mt-2 space-y-1.5">
                <p className="text-xs font-medium text-content-secondary">
                  {t('module_builder.field_options', { defaultValue: 'The choices' })}
                </p>
                {field.options.map((option, optionIndex) => (
                  <div key={optionIndex} className="flex items-center gap-2">
                    <input
                      value={option}
                      onChange={(e) => setSpec(setOption(spec, index, optionIndex, e.target.value))}
                      className="flex-1 rounded-lg border border-border-light bg-surface-primary px-2 py-1 text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => setSpec(removeOption(spec, index, optionIndex))}
                      aria-label={t('common.remove', { defaultValue: 'Remove' })}
                      className="rounded-md p-1 text-content-tertiary hover:text-semantic-error"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() => setSpec(addOption(spec, index))}
                  className="text-xs font-medium text-oe-blue-text hover:text-oe-blue-hover"
                >
                  {t('module_builder.add_option', { defaultValue: 'Add a choice' })}
                </button>
              </div>
            )}

            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-4 text-xs">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={field.required}
                    onChange={(e) => setSpec(updateField(spec, index, { required: e.target.checked }))}
                    className="h-3.5 w-3.5 rounded border-border-light text-oe-blue"
                  />
                  {t('module_builder.field_required', { defaultValue: 'Must be filled in' })}
                </label>
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={field.in_list}
                    onChange={(e) => setSpec(updateField(spec, index, { in_list: e.target.checked }))}
                    className="h-3.5 w-3.5 rounded border-border-light text-oe-blue"
                    data-testid={`module-builder-field-in-list-${index}`}
                  />
                  {t('module_builder.field_in_list', { defaultValue: 'Show in the table' })}
                </label>
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setSpec(moveField(spec, index, -1))}
                  aria-label={t('common.move_up', { defaultValue: 'Move up' })}
                  disabled={index === 0}
                  className="rounded-md p-1 text-content-tertiary hover:text-content-primary disabled:opacity-30"
                >
                  <ArrowUp size={13} />
                </button>
                <button
                  type="button"
                  onClick={() => setSpec(moveField(spec, index, 1))}
                  aria-label={t('common.move_down', { defaultValue: 'Move down' })}
                  disabled={index === spec.entity.fields.length - 1}
                  className="rounded-md p-1 text-content-tertiary hover:text-content-primary disabled:opacity-30"
                >
                  <ArrowDown size={13} />
                </button>
                <button
                  type="button"
                  onClick={() => setSpec(removeField(spec, index))}
                  aria-label={t('module_builder.remove_field', { defaultValue: 'Remove this field' })}
                  disabled={spec.entity.fields.length === 1}
                  className="rounded-md p-1 text-content-tertiary hover:text-semantic-error disabled:opacity-30"
                  data-testid={`module-builder-remove-field-${index}`}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-3">
        <Button
          variant="secondary"
          size="sm"
          icon={<Plus size={13} />}
          disabled={atLimit}
          onClick={() => setSpec(addField(spec))}
          data-testid="module-builder-add-field"
        >
          {t('module_builder.add_field', { defaultValue: 'Add a field' })}
        </Button>
        {/* There is a cap, and until now it announced itself only by greying
            the button out at the moment it was reached. Two numbers and a
            slash need no translating, and they turn a dead control into a
            limit the reader saw coming. */}
        {vocabulary !== undefined && (
          <span
            className={clsx(
              'text-xs tabular-nums',
              atLimit ? 'font-medium text-semantic-warning' : 'text-content-quaternary',
            )}
            data-testid="module-builder-field-count"
          >
            {spec.entity.fields.length} / {vocabulary.max_fields}
          </span>
        )}
      </div>

      <ProblemList problems={problems} />
    </div>
  );
}

/* ── Step 3: the rules ───────────────────────────────────────────────────── */

function RulesStep({ spec, setSpec, vocabulary, problems }: EditStepProps) {
  const { t } = useTranslation();
  const [pendingField, setPendingField] = useState('');
  const named = spec.entity.fields.filter((f) => f.name.trim() !== '');
  const chosen = named.find((f) => f.name === pendingField) ?? named[0];
  const available = chosen ? kindsForType(vocabulary, chosen.type) : [];
  const dateFields = spec.entity.fields.filter((f) => f.type === 'date' || f.type === 'datetime');

  return (
    <div className="space-y-4">
      <p className="text-xs text-content-tertiary">
        {t('module_builder.rules_intro', {
          defaultValue:
            'Every module here ships rules; a module with none is refused. They run on every write and their findings travel with the answer.',
        })}
      </p>

      <div className="flex flex-wrap items-end gap-2 rounded-xl border border-border-light p-3">
        <div className="min-w-[10rem] flex-1">
          <label className="mb-1 block text-xs font-medium text-content-secondary">
            {t('module_builder.rule_on_field', { defaultValue: 'About which field' })}
          </label>
          <select
            value={chosen?.name ?? ''}
            onChange={(e) => setPendingField(e.target.value)}
            className="w-full rounded-lg border border-border-light bg-surface-primary px-3 py-2 text-sm"
            data-testid="module-builder-rule-field"
          >
            {named.map((field) => (
              <option key={field.name} value={field.name}>
                {field.label || field.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {available.map((kind) => (
            <Button
              key={kind.kind}
              variant="secondary"
              size="sm"
              icon={<Plus size={12} />}
              title={kind.hint}
              onClick={() => setSpec(addRule(spec, kind.kind as ModuleRuleKind, chosen?.name ?? ''))}
              data-testid={`module-builder-add-rule-${kind.kind}`}
            >
              {kind.label}
            </Button>
          ))}
          {named.length === 0 && (
            <p className="text-xs text-content-tertiary">
              {t('module_builder.rules_need_fields', {
                defaultValue: 'Name a field on the previous step first.',
              })}
            </p>
          )}
        </div>
      </div>

      <div className="space-y-3">
        {spec.rules.map((rule, index) => (
          <div key={index} className="rounded-xl border border-border-light p-3">
            <div className="grid gap-2 sm:grid-cols-12">
              <div className="sm:col-span-4">
                <Input
                  label={t('module_builder.rule_code', { defaultValue: 'Code' })}
                  value={rule.code}
                  onChange={(e) => setSpec(updateRule(spec, index, { code: e.target.value.toUpperCase() }))}
                />
              </div>
              <div className="sm:col-span-8">
                <Input
                  label={t('module_builder.rule_message', { defaultValue: 'What the user is told' })}
                  value={rule.message}
                  onChange={(e) => setSpec(updateRule(spec, index, { message: e.target.value }))}
                  placeholder="A pour cannot be recorded before it happened."
                  data-testid={`module-builder-rule-message-${index}`}
                />
              </div>
            </div>

            {rule.kind === 'range' && (
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <Input
                  label={t('module_builder.rule_min', { defaultValue: 'Not below' })}
                  value={rule.min_value === null ? '' : String(rule.min_value)}
                  inputMode="decimal"
                  onChange={(e) =>
                    setSpec(
                      updateRule(spec, index, {
                        min_value: e.target.value.trim() === '' ? null : Number(e.target.value),
                      }),
                    )
                  }
                />
                <Input
                  label={t('module_builder.rule_max', { defaultValue: 'Not above' })}
                  value={rule.max_value === null ? '' : String(rule.max_value)}
                  inputMode="decimal"
                  onChange={(e) =>
                    setSpec(
                      updateRule(spec, index, {
                        max_value: e.target.value.trim() === '' ? null : Number(e.target.value),
                      }),
                    )
                  }
                />
              </div>
            )}

            {rule.kind === 'order' && (
              <div className="mt-2">
                <label className="mb-1 block text-xs font-medium text-content-secondary">
                  {t('module_builder.rule_other_field', { defaultValue: 'Must not come after' })}
                </label>
                <select
                  value={rule.other_field}
                  onChange={(e) => setSpec(updateRule(spec, index, { other_field: e.target.value }))}
                  className="w-full rounded-lg border border-border-light bg-surface-primary px-3 py-2 text-sm"
                >
                  <option value="">{t('common.select', { defaultValue: 'Select…' })}</option>
                  {dateFields
                    .filter((f) => f.name !== rule.field)
                    .map((f) => (
                      <option key={f.name} value={f.name}>
                        {f.label || f.name}
                      </option>
                    ))}
                </select>
              </div>
            )}

            <div className="mt-2 flex items-center justify-between gap-2 text-xs text-content-tertiary">
              <span>
                {rule.kind} · {rule.field}
              </span>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={rule.severity === 'warning'}
                    onChange={(e) =>
                      setSpec(updateRule(spec, index, { severity: e.target.checked ? 'warning' : 'error' }))
                    }
                    className="h-3.5 w-3.5 rounded border-border-light text-oe-blue"
                  />
                  {t('module_builder.rule_warning_only', { defaultValue: 'Warn, do not refuse' })}
                </label>
                <button
                  type="button"
                  onClick={() => setSpec(removeRule(spec, index))}
                  aria-label={t('module_builder.remove_rule', { defaultValue: 'Remove this rule' })}
                  className="rounded-md p-1 hover:text-semantic-error"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <ProblemList problems={problems} />
    </div>
  );
}

/* ── Step 4: review ──────────────────────────────────────────────────────── */

function ReviewStep({ preview }: { preview: PreviewResponse }) {
  const { t } = useTranslation();
  // The longest file sets the scale, so the bars compare the files with each
  // other rather than against a number nobody has in their head. A module of
  // sixteen even files and one of two large ones read differently at a glance,
  // and that difference is worth seeing before pressing install.
  const longest = preview.files.reduce((max, file) => Math.max(max, file.lines), 0);

  return (
    <div className="space-y-3" data-testid="module-builder-review">
      <div className="rounded-xl border border-border-light bg-surface-secondary/40 p-3 text-sm">
        <p className="text-content-primary">
          {t('module_builder.review_summary', {
            files: preview.files.length,
            lines: preview.total_lines,
            defaultValue: '{{files}} files, {{lines}} lines. Nothing has been written yet.',
          })}
        </p>
        <p className="mt-1 text-xs text-content-tertiary">
          {t('module_builder.review_url', {
            path: preview.base_path,
            defaultValue: 'It will answer on {{path}} as soon as it is installed.',
          })}
        </p>
      </div>

      <ul className="divide-y divide-border-light rounded-xl border border-border-light">
        {preview.files.map((file) => (
          <li key={file.path} className="flex items-center justify-between gap-3 px-3 py-1.5 text-xs">
            <span className="flex min-w-0 items-center gap-2 text-content-secondary">
              <FileCode size={13} className="shrink-0 text-content-quaternary" />
              <span className="truncate font-mono">{file.path}</span>
            </span>
            <span className="flex shrink-0 items-center gap-2 text-content-tertiary">
              <span aria-hidden className="hidden h-1 w-16 rounded-full bg-surface-tertiary sm:block">
                <span
                  className="block h-full rounded-full bg-oe-blue/50"
                  style={{ width: longest > 0 ? `${Math.max(4, (file.lines / longest) * 100)}%` : '0%' }}
                />
              </span>
              {/* Deliberately not a counted key: `count` would make i18next
                  demand a plural form per language for a string whose natural
                  rendering in several of them is "lines: 96". */}
              {t('module_builder.review_lines', { lines: file.lines, defaultValue: '{{lines}} lines' })}
            </span>
          </li>
        ))}
      </ul>

      <p className="text-xs text-content-tertiary">
        {t('module_builder.review_note', {
          defaultValue:
            'Installing writes these files into this instance and loads them straight away. A platform upgrade will not overwrite them, and removing the module later leaves its records alone unless you ask for them to go too.',
        })}
      </p>
    </div>
  );
}

/* ── Step 5: done ────────────────────────────────────────────────────────── */

function DoneStep({ installed }: { installed: InstalledModule }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-2 py-4 text-center" data-testid="module-builder-done">
      {/* The one moment in the wizard that is purely good news, so it is given
          the room to read as one. The ring is a halo around the mark rather
          than a second border on it.

          Both colours here used to name `semantic-success-subtle`, which is
          not a token: the semantic palette defines the base hue, a `-bg` wash
          and a `-vivid` variant, and nothing called `-subtle`. The fill
          therefore painted nothing, and the ring was worse than nothing,
          because `ring-8` still applies Tailwind's own default ring colour
          when the ring-colour class is dropped. That default is blue, so the
          success mark wore a wide blue halo on the one screen that exists to
          say the install worked. The ring takes its alpha from the base hue
          rather than from `-bg`, because the dark-mode `-bg` value is already
          an rgba() wash and a modifier on it would read stronger than the
          plain class. */}
      <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-semantic-success-bg text-semantic-success ring-8 ring-semantic-success/20">
        <Check size={28} strokeWidth={2.5} />
      </span>
      <p className="pt-1 text-base font-semibold text-content-primary">{installed.display_name}</p>
      <p className="text-xs text-content-tertiary">
        {t('module_builder.done_counts', {
          fields: installed.field_count,
          rules: installed.rule_count,
          defaultValue: '{{fields}} fields, {{rules}} rules.',
        })}
      </p>
      {/* The path stays inside done_note rather than also getting a monospace
          chip of its own. A chip would look better and would print the same
          path twice on a screen four lines long, which reads as a mistake. */}
      <p className="mx-auto max-w-sm break-words text-xs text-content-tertiary">
        {t('module_builder.done_note', {
          path: installed.base_path,
          defaultValue: 'Installed and serving on {{path}}. No restart is needed.',
        })}
      </p>
    </div>
  );
}

export default ModuleBuilderWizard;
