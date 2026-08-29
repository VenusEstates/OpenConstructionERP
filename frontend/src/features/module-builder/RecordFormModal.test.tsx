// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * The form on its own, for the cases the page-level tests cannot reach cleanly.
 *
 * Three of these are about *not* doing something: not writing when nothing
 * changed, not refusing on a warning, not shouting at a form the moment it
 * opens. Those are the behaviours a screenshot cannot tell apart from the
 * wrong ones.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return { ...actual, createModuleRecord: vi.fn(), updateModuleRecord: vi.fn() };
});

import {
  createModuleRecord,
  updateModuleRecord,
  type GeneratedRecord,
  type ModuleUiSpec,
} from './api';
import { RecordFormModal } from './RecordFormModal';

const create = vi.mocked(createModuleRecord);
const update = vi.mocked(updateModuleRecord);

const BASE_PATH = '/api/v1/site-diary';

function spec(over: Partial<ModuleUiSpec> = {}): ModuleUiSpec {
  return {
    key: 'site_diary',
    display_name: 'Site Diary',
    description: '',
    category: 'community',
    icon: 'Boxes',
    version: '0.1.0',
    author: '',
    drafted_by: 'wizard',
    entity: {
      name: 'entry',
      display_name: 'Entry',
      plural_name: 'Entries',
      project_scoped: true,
      fields: [
        { name: 'reference', label: 'Reference', type: 'text', required: true, help_text: '', unit: '', options: [], in_list: true },
        { name: 'crew', label: 'Crew size', type: 'integer', required: false, help_text: 'People on site', unit: '', options: [], in_list: true },
        { name: 'started_at', label: 'Started at', type: 'datetime', required: false, help_text: '', unit: '', options: [], in_list: true },
      ],
    },
    rules: [
      { code: 'CREW_POSITIVE', message: 'A day with nobody on site is not a day worked.', kind: 'positive', field: 'crew', min_value: null, max_value: null, other_field: '', severity: 'error' },
    ],
    ...over,
  };
}

const RECORD: GeneratedRecord = {
  id: 'r1',
  project_id: 'p1',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
  reference: 'SD-001',
  crew: 8,
  started_at: '2026-08-01T07:00:00+00:00',
};

function renderForm(over: Partial<Parameters<typeof RecordFormModal>[0]> = {}) {
  const props = {
    open: true,
    spec: spec(),
    basePath: BASE_PATH,
    projectId: 'p1' as string | null,
    record: null as GeneratedRecord | null,
    onClose: vi.fn(),
    onSaved: vi.fn(),
    ...over,
  };
  render(<RecordFormModal {...props} />);
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  create.mockResolvedValue(RECORD);
  update.mockResolvedValue(RECORD);
});

describe('opening the form', () => {
  it('does not mark anything wrong before the user has done anything', async () => {
    renderForm();
    await screen.findByLabelText(/Reference/);
    // `reference` is required and empty. Saying so on open is telling someone
    // off for not having filled in a form they have just been handed.
    expect(screen.queryByText(/cannot be left empty/i)).toBeNull();
  });

  it('shows the field help the module author wrote, as written', async () => {
    renderForm();
    expect(await screen.findByText('People on site')).toBeTruthy();
  });

  it('warns when the records need a project and none is chosen', async () => {
    renderForm({ projectId: null });
    expect(await screen.findByText(/belong to a project/i)).toBeTruthy();
  });
});

describe('saving', () => {
  it('lists everything wrong at once when save is pressed', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(await screen.findByLabelText(/Crew size/), '0');
    await user.click(screen.getByTestId('runtime-module-save'));

    expect(await screen.findByText(/cannot be left empty/i)).toBeTruthy();
    expect(screen.getByText('A day with nobody on site is not a day worked.')).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });

  it('clears the marks again as soon as the user starts fixing them', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.click(screen.getByTestId('runtime-module-save'));
    expect(await screen.findByText(/cannot be left empty/i)).toBeTruthy();

    await user.type(screen.getByLabelText(/Reference/), 'SD-002');
    await waitFor(() => expect(screen.queryByText(/cannot be left empty/i)).toBeNull());
  });

  it('does not write when nothing changed', async () => {
    const user = userEvent.setup();
    const props = renderForm({ record: RECORD });
    await screen.findByLabelText(/Reference/);
    await user.click(screen.getByTestId('runtime-module-save'));

    await waitFor(() => expect(props.onClose).toHaveBeenCalled());
    // A save that rewrote every column would bump updated_at and put a change
    // nobody made into the record's history.
    expect(update).not.toHaveBeenCalled();
  });

  it('lets a warning through', async () => {
    const user = userEvent.setup();
    const warned = spec();
    warned.rules = [{ ...warned.rules[0]!, severity: 'warning' }];
    renderForm({ spec: warned });

    await user.type(await screen.findByLabelText(/Reference/), 'SD-003');
    await user.type(screen.getByLabelText(/Crew size/), '0');
    await user.click(screen.getByTestId('runtime-module-save'));

    await waitFor(() => expect(create).toHaveBeenCalled());
  });

  it('sends a datetime as an instant', async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(await screen.findByLabelText(/Reference/), 'SD-004');
    await user.type(screen.getByLabelText(/Started at/), '2026-08-07T09:30');
    await user.click(screen.getByTestId('runtime-module-save'));

    await waitFor(() => expect(create).toHaveBeenCalled());
    // The test process is pinned to UTC, so the instant reads back the same;
    // what is asserted is that an offset was attached at all.
    expect(create.mock.calls[0]?.[1]?.started_at).toBe('2026-08-07T09:30:00.000Z');
  });
});
