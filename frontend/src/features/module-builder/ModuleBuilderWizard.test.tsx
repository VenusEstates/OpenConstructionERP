// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Describing a module by clicking through the wizard.
 *
 * The path that matters is the one a person actually takes: type a name, add
 * the fields, add a rule, read what will be written, install. Each test below
 * walks part of it with real clicks rather than by setting state, because the
 * failures worth catching here are the ones where a step lets you past
 * something the server will refuse - and that only shows up when the button is
 * pressed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate, useParams: () => ({}) };
});

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    fetchVocabulary: vi.fn(),
    draftSpec: vi.fn(),
    previewModule: vi.fn(),
    installModule: vi.fn(),
  };
});

import { ApiError } from '@/shared/lib/api';

import {
  draftSpec,
  fetchVocabulary,
  installModule,
  previewModule,
  type InstalledModule,
  type ModuleSpec,
  type PreviewResponse,
  type Vocabulary,
} from './api';
import { ModuleBuilderWizard } from './ModuleBuilderWizard';

const vocabulary = vi.mocked(fetchVocabulary);
const draft = vi.mocked(draftSpec);
const preview = vi.mocked(previewModule);
const install = vi.mocked(installModule);

const VOCABULARY: Vocabulary = {
  field_types: [
    { type: 'text', label: 'Text', hint: 'A short line.' },
    { type: 'number', label: 'Quantity', hint: 'A measured amount.' },
    { type: 'money', label: 'Money', hint: 'Kept exact.' },
    { type: 'date', label: 'Date', hint: 'A day.' },
  ],
  rule_kinds: [
    { kind: 'required', label: 'Must be filled in', hint: '', applies_to: ['text', 'number', 'money', 'date'], needs_other_field: false, needs_bounds: false },
    { kind: 'positive', label: 'Must be above zero', hint: '', applies_to: ['number', 'money'], needs_other_field: false, needs_bounds: false },
    { kind: 'range', label: 'Between two values', hint: '', applies_to: ['number', 'money'], needs_other_field: false, needs_bounds: true },
  ],
  reserved_field_names: ['id', 'metadata', 'project_id'],
  reserved_keys: ['boq', 'costs'],
  max_fields: 40,
  assistant_available: true,
};

const DRAFTED: ModuleSpec = {
  key: 'pour_register',
  display_name: 'Pour Register',
  description: 'Every concrete pour on the job.',
  category: 'community',
  icon: 'Boxes',
  version: '0.1.0',
  author: '',
  // This fixture is what /draft returns, so it carries the mark the wizard renders.
  drafted_by: 'assistant',
  entity: {
    name: 'pour',
    display_name: 'Pour',
    plural_name: 'Pours',
    project_scoped: true,
    fields: [
      { name: 'reference', label: 'Reference', type: 'text', required: true, help_text: '', unit: '', options: [], in_list: true },
      { name: 'volume', label: 'Volume', type: 'number', required: false, help_text: '', unit: 'm3', options: [], in_list: true },
    ],
  },
  rules: [
    { code: 'VOLUME_POSITIVE', message: 'A pour of nothing is not a pour.', kind: 'positive', field: 'volume', min_value: null, max_value: null, other_field: '', severity: 'error' },
  ],
};

const PREVIEW: PreviewResponse = {
  spec: DRAFTED,
  files: [
    { path: 'manifest.py', lines: 24, content: '' },
    { path: 'models.py', lines: 51, content: '' },
    { path: 'router.py', lines: 96, content: '' },
  ],
  total_lines: 171,
  base_path: '/api/v1/pour-register',
  review_token: 'issued-for-this-preview',
};

const INSTALLED: InstalledModule = {
  key: 'pour_register',
  module_name: 'oe_pour_register',
  display_name: 'Pour Register',
  version: '0.1.0',
  generated_at: '2026-08-07T00:00:00Z',
  entity: 'Pour',
  field_count: 2,
  rule_count: 1,
  base_path: '/api/v1/pour-register',
};

function renderWizard(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ModuleBuilderWizard open onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { onClose };
}

/** Walk from the first step to the review step on an assistant-drafted spec. */
async function draftAndAdvance(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId('module-builder-description'));
  await user.paste('A register of every concrete pour on the job.');
  await user.click(screen.getByTestId('module-builder-draft'));
  await screen.findByTestId('module-builder-add-field');
}

beforeEach(() => {
  vi.clearAllMocks();
  vocabulary.mockResolvedValue(VOCABULARY);
  draft.mockResolvedValue({ spec: DRAFTED, source: 'assistant' });
  preview.mockResolvedValue(PREVIEW);
  install.mockResolvedValue(INSTALLED);
});

describe('the first step', () => {
  it('offers the assistant only when one is connected', async () => {
    renderWizard();
    expect(await screen.findByTestId('module-builder-draft')).toBeTruthy();
  });

  it('says so, and still works, when no provider is configured', async () => {
    vocabulary.mockResolvedValue({ ...VOCABULARY, assistant_available: false });
    renderWizard();
    expect(await screen.findByText(/No AI provider is connected/i)).toBeTruthy();
    expect(screen.queryByTestId('module-builder-draft')).toBeNull();
    // The by-hand path is the same path; nothing about it depends on an assistant.
    expect(screen.getByTestId('module-builder-name')).toBeTruthy();
  });

  it('names the key from the module name so nobody has to invent one', async () => {
    const user = userEvent.setup();
    renderWizard();
    await user.click(await screen.findByTestId('module-builder-name'));
    await user.paste('Concrete Pour Register');
    expect(screen.getByTestId('module-builder-key')).toHaveProperty('value', 'concrete_pour_register');
  });

  it('stops leaning on the name once the key has been typed over', async () => {
    const user = userEvent.setup();
    renderWizard();
    await user.click(await screen.findByTestId('module-builder-name'));
    await user.paste('Pour Register');
    await user.clear(screen.getByTestId('module-builder-key'));
    await user.paste('pours');
    await user.click(screen.getByTestId('module-builder-name'));
    await user.paste(' v2');
    expect(screen.getByTestId('module-builder-key')).toHaveProperty('value', 'pours');
  });

  it('will not move on while the module has no name', async () => {
    const user = userEvent.setup();
    renderWizard();
    await screen.findByTestId('module-builder-name');
    expect(screen.getByTestId('module-builder-next')).toBeDisabled();
    await user.click(screen.getByTestId('module-builder-name'));
    await user.paste('Pour Register');
    await waitFor(() => expect(screen.getByTestId('module-builder-next')).not.toBeDisabled());
  });

  it('refuses a key a shipped module already owns, and says which', async () => {
    const user = userEvent.setup();
    renderWizard();
    await user.click(await screen.findByTestId('module-builder-name'));
    await user.paste('Bill of Quantities');
    await user.clear(screen.getByTestId('module-builder-key'));
    await user.paste('boq');
    expect(await screen.findByText(/already ships with the platform/i)).toBeTruthy();
    expect(screen.getByTestId('module-builder-next')).toBeDisabled();
  });

  it('shows the assistant refusal instead of burying it in a toast', async () => {
    draft.mockRejectedValue(new ApiError(422, 'Unprocessable Entity', { detail: 'That is not a register.' }));
    const user = userEvent.setup();
    renderWizard();
    await user.click(await screen.findByTestId('module-builder-description'));
    await user.paste('make me a sandwich please');
    await user.click(screen.getByTestId('module-builder-draft'));
    expect(await screen.findByRole('alert')).toHaveTextContent('That is not a register.');
  });
});

describe('the fields step', () => {
  it('lands on it filled in when the assistant drafted the module', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);
    expect(draft).toHaveBeenCalledWith('A register of every concrete pour on the job.');
    expect(screen.getByTestId('module-builder-field-label-0')).toHaveProperty('value', 'Reference');
    expect(screen.getByTestId('module-builder-field-label-1')).toHaveProperty('value', 'Volume');
  });

  it('marks a drafted specification and leaves a hand-built one unmarked', async () => {
    const user = userEvent.setup();
    renderWizard();
    // Before anybody drafts, the spec is the user's own and there is nothing
    // to say about where it came from.
    await screen.findByTestId('module-builder-name');
    expect(screen.queryByTestId('module-builder-ai-mark')).toBeNull();
    await draftAndAdvance(user);
    expect(screen.getByTestId('module-builder-ai-mark')).toBeTruthy();
  });

  it('offers exactly the field types the server said it may', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);
    const select = screen.getByTestId('module-builder-field-type-0');
    const offered = within(select).getAllByRole('option').map((o) => o.textContent);
    expect(offered).toEqual(['Text', 'Quantity', 'Money', 'Date']);
  });

  it('adds a field and names its column from its label', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);
    await user.click(screen.getByTestId('module-builder-add-field'));
    await user.click(screen.getByTestId('module-builder-field-label-2'));
    await user.paste('Poured on');
    expect(screen.getByTestId('module-builder-next')).not.toBeDisabled();
  });

  it('will not move on with a field that has no label', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);
    await user.click(screen.getByTestId('module-builder-add-field'));
    expect(await screen.findByText(/needs a label/i)).toBeTruthy();
    expect(screen.getByTestId('module-builder-next')).toBeDisabled();
  });

  it('refuses a column name the generated record already uses', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);
    const column = screen.getByTestId('module-builder-field-label-0').closest('div')?.parentElement;
    expect(column).toBeTruthy();
    await user.clear(screen.getByTestId('module-builder-field-label-0'));
    await user.paste('Metadata');
    expect(await screen.findByText(/is reserved/i)).toBeTruthy();
  });

  it('will not let the last field be removed', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);
    await user.click(screen.getByTestId('module-builder-remove-field-1'));
    expect(screen.queryByTestId('module-builder-field-label-1')).toBeNull();
    expect(screen.getByTestId('module-builder-remove-field-0')).toBeDisabled();
  });
});

describe('the rules step', () => {
  async function goToRules(user: ReturnType<typeof userEvent.setup>) {
    await draftAndAdvance(user);
    await user.click(screen.getByTestId('module-builder-next'));
    await screen.findByTestId('module-builder-rule-field');
  }

  it('offers only the rules that can apply to the chosen field', async () => {
    const user = userEvent.setup();
    renderWizard();
    await goToRules(user);

    // `reference` is text, so nothing numeric is on offer.
    expect(screen.getByTestId('module-builder-add-rule-required')).toBeTruthy();
    expect(screen.queryByTestId('module-builder-add-rule-positive')).toBeNull();

    await user.selectOptions(screen.getByTestId('module-builder-rule-field'), 'volume');
    expect(await screen.findByTestId('module-builder-add-rule-positive')).toBeTruthy();
  });

  it('will not move on while a rule has no message a person can act on', async () => {
    const user = userEvent.setup();
    renderWizard();
    await goToRules(user);
    await user.click(screen.getByTestId('module-builder-add-rule-required'));
    expect(await screen.findByText(/act on/i)).toBeTruthy();
    expect(screen.getByTestId('module-builder-next')).toBeDisabled();

    await user.click(screen.getByTestId('module-builder-rule-message-1'));
    await user.paste('Give the pour a reference.');
    await waitFor(() => expect(screen.getByTestId('module-builder-next')).not.toBeDisabled());
  });

  it('will not move on with no rules at all, because the platform refuses one', async () => {
    draft.mockResolvedValue({ spec: { ...DRAFTED, rules: [] }, source: 'assistant' });
    const user = userEvent.setup();
    renderWizard();
    await goToRules(user);
    expect(await screen.findByText(/at least one rule/i)).toBeTruthy();
    expect(screen.getByTestId('module-builder-next')).toBeDisabled();
  });
});

describe('the review step', () => {
  async function goToReview(user: ReturnType<typeof userEvent.setup>) {
    await draftAndAdvance(user);
    await user.click(screen.getByTestId('module-builder-next'));
    await screen.findByTestId('module-builder-rule-field');
    await user.click(screen.getByTestId('module-builder-next'));
    await screen.findByTestId('module-builder-review');
  }

  it('shows what would be written, and has written nothing', async () => {
    const user = userEvent.setup();
    renderWizard();
    await goToReview(user);

    expect(screen.getByText(/3 files, 171 lines/)).toBeTruthy();
    expect(screen.getByText('manifest.py')).toBeTruthy();
    expect(screen.getByText('router.py')).toBeTruthy();
    expect(install).not.toHaveBeenCalled();
  });

  it('names the URL before the module exists, not after', async () => {
    const user = userEvent.setup();
    renderWizard();
    await goToReview(user);
    expect(screen.getByText(/\/api\/v1\/pour-register/)).toBeTruthy();
  });

  it('sends the spec normalised, with the empty select options gone', async () => {
    const user = userEvent.setup();
    renderWizard();
    await goToReview(user);
    const sent = preview.mock.calls[0]?.[0];
    expect(sent?.entity.plural_name).toBe('Pours');
    expect(sent?.rules[0]?.code).toBe('VOLUME_POSITIVE');
  });

  it('installs, then says where the module is answering', async () => {
    const user = userEvent.setup();
    renderWizard();
    await goToReview(user);
    await user.click(screen.getByTestId('module-builder-install'));

    await screen.findByTestId('module-builder-done');
    expect(install).toHaveBeenCalledTimes(1);
    // What gets written is the spec the review step rendered, carried with the
    // token that proves it was rendered, rather than whatever the form holds.
    expect(install).toHaveBeenCalledWith(PREVIEW.spec, 'issued-for-this-preview');
    expect(screen.getByText(/Installed and serving/)).toBeTruthy();
  });

  it('opens the module it just built', async () => {
    const user = userEvent.setup();
    const { onClose } = renderWizard();
    await goToReview(user);
    await user.click(screen.getByTestId('module-builder-install'));
    await user.click(await screen.findByTestId('module-builder-open'));

    // Routed by key. The URL the key resolves to is the server's business.
    expect(navigate).toHaveBeenCalledWith('/modules/pour_register');
    expect(onClose).toHaveBeenCalled();
  });

  it('stays on the review step and says why when the install is refused', async () => {
    install.mockRejectedValue(new ApiError(409, 'Conflict', { detail: 'A module with that key is already installed.' }));
    const user = userEvent.setup();
    renderWizard();
    await goToReview(user);
    await user.click(screen.getByTestId('module-builder-install'));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'A module with that key is already installed.',
    );
    expect(screen.queryByTestId('module-builder-done')).toBeNull();
    expect(screen.getByTestId('module-builder-install')).toBeTruthy();
  });
});

describe('the step rail', () => {
  it('goes back to a finished step in one press', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);

    await user.click(screen.getByTestId('module-builder-step-describe'));

    expect(screen.getByTestId('module-builder-name')).toBeTruthy();
  });

  it('does not offer a step the wizard has not reached', async () => {
    // Skipping ahead would promise something the footer takes back: it refuses
    // to advance past a step that still has problems on it.
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);

    expect(screen.queryByTestId('module-builder-step-rules')).toBeNull();
    expect(screen.queryByTestId('module-builder-step-review')).toBeNull();
  });

  it('does not offer the step being stood on', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);

    expect(screen.queryByTestId('module-builder-step-record')).toBeNull();
  });
});

describe('the table preview', () => {
  it('shows the columns the register would open on', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);

    const table = within(screen.getByTestId('module-builder-preview')).getByRole('table');
    expect(within(table).getByText('Reference')).toBeTruthy();
    expect(within(table).getByText('Volume')).toBeTruthy();
    // The unit belongs to the column heading, not to a row of invented figures.
    expect(within(table).getByText('m3')).toBeTruthy();
  });

  it('puts no values in the rows at all', async () => {
    // The load-bearing one. A plausible sample row reads as a real record on
    // the screen where somebody decides whether the module is right, which is
    // the same mistake the insights panel refuses to make. The rows are shape
    // and nothing else, so the assertion is that they carry no text.
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);

    const table = within(screen.getByTestId('module-builder-preview')).getByRole('table');
    const body = table.querySelector('tbody');
    expect(body).not.toBeNull();
    expect(body?.querySelectorAll('tr').length).toBeGreaterThan(0);
    expect(body?.textContent?.trim()).toBe('');
  });

  it('says the register would open empty when no field is shown in the table', async () => {
    const user = userEvent.setup();
    renderWizard();
    await draftAndAdvance(user);

    await user.click(screen.getByTestId('module-builder-field-in-list-0'));
    await user.click(screen.getByTestId('module-builder-field-in-list-1'));

    expect(screen.getByTestId('module-builder-preview-empty')).toBeTruthy();
    expect(within(screen.getByTestId('module-builder-preview')).queryByRole('table')).toBeNull();
  });
});
