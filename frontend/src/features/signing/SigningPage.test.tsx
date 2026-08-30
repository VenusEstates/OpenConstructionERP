// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Component tests for <SigningPage>, covering one thing only: that a session
// row never reports the capability it REQUIRED as the capability it GOT.
//
// A signing session carries two separate facts. `provider_capability` is what
// the caller requires of the signature - qualified, advanced or simple
// electronic, or a digital certificate. `delivered_capability` is what the
// resolved provider actually delivers. Core ships one provider, which performs
// no cryptography and delivers `simple_electronic`, and the registry falls back
// to it for every capability no adapter has claimed. There are no adapters in
// this tree, so a session requiring a qualified electronic signature resolves
// to it and gets the weakest tier there is.
//
// The backend records both and the API returns both. What no backend test can
// see is whether this screen puts the difference in front of the reader, and
// this page rendered the requirement alone, as a bare badge, with nothing
// beside it to say the requirement had not been met.
//
// The assertions below are on the two values being rendered as DIFFERENT
// strings, not merely on the delivered one being present. A future
// `delivered ?? required` fallback anywhere between the API and the badge would
// make the two agree, which reads as correct everywhere except here.
//
// The signing API module is stubbed, so no network is hit.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

vi.mock('./api', () => ({
  fetchSigningSessions: vi.fn(),
  fetchSigningManifest: vi.fn(),
  fetchExpiringCerts: vi.fn(),
  createSigningSession: vi.fn(),
  updateSigningSession: vi.fn(),
  deleteSigningSession: vi.fn(),
  attestSigningSession: vi.fn(),
  declineSigningSession: vi.fn(),
  downloadSigningManifest: vi.fn(),
}));

vi.mock('@/shared/lib/api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  apiGet: vi.fn(async () => []),
}));

vi.mock('@/shared/hooks/useActiveProjectId', () => ({
  useActiveProjectId: () => 'proj-1',
}));

vi.mock('@/stores/useToastStore', () => ({
  useToastStore: (sel: (s: { addToast: () => void }) => unknown) => sel({ addToast: vi.fn() }),
}));

import { SigningPage } from './SigningPage';
import * as api from './api';
import type { SigningSession } from './api';

const sessionsMock = vi.mocked(api.fetchSigningSessions);
const certsMock = vi.mocked(api.fetchExpiringCerts);

/** A session requiring the highest legal tier, with nothing signed yet. */
function session(over: Partial<SigningSession> = {}): SigningSession {
  return {
    id: 'sess-1',
    project_id: 'proj-1',
    document_ref: 'contract/northlake-phase-2',
    document_content_hash: 'ab12cd34ef56ab12cd34ef56',
    provider_capability: 'qualified_electronic',
    delivered_capability: 'simple_electronic',
    signatory_map: [{ name: 'Northlake Estates', role: 'employer', required: true }],
    status: 'awaiting_signatures',
    expires_at: null,
    metadata: {},
    created_by: null,
    created_at: '2026-08-01T09:00:00Z',
    updated_at: '2026-08-01T09:00:00Z',
    required_count: 1,
    signed_count: 0,
    declined_count: 0,
    signatures: [],
    ...over,
  };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <SigningPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/**
 * The session row, scoped.
 *
 * Every query below runs inside it rather than over the document, because the
 * capability filter in the toolbar renders every tier as an `<option>` with
 * exactly the same label. A document-wide `getAllByText('Simple electronic')`
 * counts that option too, so "the row shows one capability, not two" would be
 * measured against the wrong population and the assertion would be meaningless.
 */
async function row(): Promise<HTMLElement> {
  const link = await screen.findByRole('link', { name: /northlake-phase-2/i });
  const root = link.closest('.border-b');
  if (!(root instanceof HTMLElement)) throw new Error('session row root not found');
  return root;
}

/** The collapsed row is a button; expanding it reveals the labelled fields. */
function expandRow(root: HTMLElement): void {
  const header = root.querySelector('[role="button"][aria-expanded="false"]');
  if (!(header instanceof HTMLElement)) throw new Error('collapsed row header not found');
  fireEvent.click(header);
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionsMock.mockResolvedValue([]);
  certsMock.mockResolvedValue([]);
});

describe('<SigningPage> capability reporting', () => {
  it('shows what was delivered beside what was required, as two different values', async () => {
    sessionsMock.mockResolvedValue([session()]);
    renderPage();
    const r = within(await row());

    // Both tiers are on the row, once each. The requirement appearing twice
    // would mean the delivered slot borrowed it; the delivered tier missing
    // would mean the row is back to reporting the requirement alone.
    expect(r.getAllByText('Qualified electronic')).toHaveLength(1);
    expect(r.getAllByText('Simple electronic')).toHaveLength(1);
  });

  it('labels the pair so two capability names side by side are not ambiguous', async () => {
    sessionsMock.mockResolvedValue([session()]);
    renderPage();
    const root = await row();

    const labelled = root.querySelector('[title*="Delivered capability"]');
    expect(labelled).not.toBeNull();
    expect(labelled?.getAttribute('title')).toContain('Required capability: Qualified electronic');
    expect(labelled?.getAttribute('title')).toContain('Delivered capability: Simple electronic');
  });

  it('states both capabilities as labelled fields when the row is expanded', async () => {
    sessionsMock.mockResolvedValue([session()]);
    renderPage();
    const root = await row();
    expandRow(root);

    // Stated always, whether or not they agree - a field, not just a warning.
    const r = within(root);
    expect(r.getByText('Required capability')).toBeInTheDocument();
    expect(r.getByText('Delivered capability')).toBeInTheDocument();
  });

  it('reports an unrecorded delivered capability as unrecorded, not as the requirement', async () => {
    // Every session written before the platform recorded this has null here.
    // Filling the gap from the requirement is the exact claim being removed.
    sessionsMock.mockResolvedValue([session({ delivered_capability: null })]);
    renderPage();
    const r = within(await row());

    expect(r.getByText('Not recorded')).toBeInTheDocument();
    expect(r.getAllByText('Qualified electronic')).toHaveLength(1);
  });

  it('does not flag a shortfall when the delivered capability is the required one', async () => {
    sessionsMock.mockResolvedValue([
      session({ provider_capability: 'simple_electronic', delivered_capability: 'simple_electronic' }),
    ]);
    renderPage();
    const r = within(await row());

    // One badge, not two: there is no discrepancy to flag.
    expect(r.getAllByText('Simple electronic')).toHaveLength(1);
    expect(r.queryByText('Not recorded')).toBeNull();
  });
});
