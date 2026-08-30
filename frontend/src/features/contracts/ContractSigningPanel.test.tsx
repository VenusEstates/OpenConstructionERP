// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Component tests for <ContractSigningPanel>.
//
// The backend suite already proves the bridge: a session is filed under the
// contract, a second one is refused, a non-draft contract cannot be put up for
// signature, and an edit makes the signatures collected so far stale. What no
// backend test can see is whether the screen puts those facts in front of the
// user in the right order, so that is what this file asserts.
//
// Two of the cases here cover states that are easy to get wrong and impossible
// to notice by looking at a healthy screen:
//
//   * before the compliance gate has answered, the contract's status is not
//     known, and the action must not be live. Defaulting the unknown to
//     "draft" would offer a button whose whole purpose is to be refused.
//   * on a declined or expired session nobody is awaiting anything. Saying
//     "Awaiting signature" there names a pending action no one can take.
//
// The contracts API module is stubbed, so no network is hit.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('./api', () => ({
  listContractSigningSessions: vi.fn(),
  openContractSigningSession: vi.fn(),
  syncContractFromSigning: vi.fn(),
}));

vi.mock('@/stores/useToastStore', () => ({
  useToastStore: (sel: (s: { addToast: () => void }) => unknown) =>
    sel({ addToast: vi.fn() }),
}));

import { ContractSigningPanel } from './ContractSigningPanel';
import * as api from './api';
import type { ContractSigningSession } from './api';

const listMock = vi.mocked(api.listContractSigningSessions);
const openMock = vi.mocked(api.openContractSigningSession);

const EMPLOYER = 'Northlake Estates';
const CONTRACTOR = 'Bramwell Civil Works';

/** A live session with two parties and nobody signed, unless overridden. */
function session(over: Partial<ContractSigningSession> = {}): ContractSigningSession {
  return {
    id: 'sess-1',
    document_ref: 'contract:c-1',
    document_content_hash: 'ab12cd34ef56ab12cd34ef56',
    provider_capability: 'advanced_electronic',
    delivered_capability: 'simple_electronic',
    status: 'awaiting_signatures',
    signatory_map: [
      { name: EMPLOYER, role: 'employer', required: true },
      { name: CONTRACTOR, role: 'contractor', required: true },
    ],
    expires_at: null,
    created_at: '2026-08-01T09:00:00Z',
    content_hash_current: true,
    stale_signatories: [],
    signed_roles: [],
    ...over,
  };
}

function renderPanel(props: {
  contractStatus: string | undefined;
  blocked?: boolean;
}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ContractSigningPanel
        contractId="c-1"
        contractStatus={props.contractStatus}
        blocked={props.blocked ?? false}
        onContractChanged={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

function openButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: /Put up for signature/i });
}

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue([]);
});

describe('<ContractSigningPanel>', () => {
  it('keeps the action disabled until the compliance gate has answered', async () => {
    renderPanel({ contractStatus: undefined });

    // Undefined is "not known yet", not "draft". The button exists so the user
    // can see what is coming, but pressing it now would only earn a refusal.
    await waitFor(() => expect(openButton()).toBeDisabled());
  });

  it('disables the action while the gate is blocking', async () => {
    renderPanel({ contractStatus: 'draft', blocked: true });

    await waitFor(() => expect(openButton()).toBeDisabled());
  });

  it('disables the action on a contract that has left draft', async () => {
    renderPanel({ contractStatus: 'active' });

    await waitFor(() => expect(openButton()).toBeDisabled());
    expect(openButton()).toHaveAttribute(
      'title',
      expect.stringMatching(/draft/i),
    );
  });

  it('opens a session from a clear draft', async () => {
    openMock.mockResolvedValue(session());
    renderPanel({ contractStatus: 'draft' });

    await waitFor(() => expect(openButton()).toBeEnabled());
    fireEvent.click(openButton());

    await waitFor(() => expect(openMock).toHaveBeenCalledTimes(1));
    expect(openMock).toHaveBeenCalledWith('c-1');
  });

  it('shows what was delivered beside what was required, and does not conflate them', async () => {
    // The panel used to render the required capability alone, which reads as a
    // statement about the signature that was collected. Core delivers a simple
    // electronic signature whatever is required of it, so both belong on screen.
    listMock.mockResolvedValue([session()]);
    renderPanel({ contractStatus: 'active' });

    expect(await screen.findByText('Required capability')).toBeInTheDocument();
    expect(screen.getByText('Delivered capability')).toBeInTheDocument();
    expect(screen.getByText('Advanced electronic')).toBeInTheDocument();
    expect(screen.getByText('Simple electronic')).toBeInTheDocument();
  });

  it('reports an unrecorded delivered capability as unrecorded, not as the requirement', async () => {
    // Every session created before the platform recorded this has null here.
    // Borrowing the requirement to fill the gap is the exact claim we removed.
    listMock.mockResolvedValue([session({ delivered_capability: null })]);
    renderPanel({ contractStatus: 'active' });

    expect(await screen.findByText('Not recorded')).toBeInTheDocument();
    expect(screen.queryAllByText('Advanced electronic')).toHaveLength(1);
  });

  it('says nobody has been asked yet when there is no session', async () => {
    renderPanel({ contractStatus: 'draft' });

    expect(await screen.findByText(/Nobody has been asked to sign/i)).toBeInTheDocument();
  });

  it('counts signatures by role, not by party position', async () => {
    // The employer has signed, the contractor has not. The count has to follow
    // the role, because that is the only thing the signing module records.
    listMock.mockResolvedValue([session({ signed_roles: ['employer'] })]);
    renderPanel({ contractStatus: 'awaiting_signatures' });

    expect(await screen.findByText('1 of 2 signed')).toBeInTheDocument();
    // Exact, not a regex: the status badge reads "Awaiting signatures" and a
    // loose match would be satisfied by the badge alone, so the assertion would
    // pass even if the per-party row said nothing at all.
    expect(screen.getByText('Awaiting signature')).toBeInTheDocument();
  });

  it('names who signed the version that has since changed', async () => {
    listMock.mockResolvedValue([
      session({
        signed_roles: ['employer'],
        content_hash_current: false,
        stale_signatories: [EMPLOYER],
      }),
    ]);
    renderPanel({ contractStatus: 'awaiting_signatures' });

    expect(
      await screen.findByText(/The contract changed after these signatures/i),
    ).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`${EMPLOYER} signed an earlier version`))).toBeInTheDocument();
    expect(screen.getByText('Stale')).toBeInTheDocument();
  });

  it('does not claim anybody is awaiting a signature on a declined session', async () => {
    listMock.mockResolvedValue([
      session({ status: 'declined', signed_roles: ['employer'] }),
    ]);
    renderPanel({ contractStatus: 'draft' });

    expect(await screen.findByText('Declined')).toBeInTheDocument();
    expect(screen.getByText('Signed')).toBeInTheDocument();
    expect(screen.queryByText('Awaiting signature')).not.toBeInTheDocument();
    // A closed attempt does not stop the contract being put up again.
    expect(openButton()).toBeEnabled();
  });

  it('shows the newest live session and counts the closed ones behind it', async () => {
    listMock.mockResolvedValue([
      session({ id: 'sess-3', status: 'awaiting_signatures' }),
      session({ id: 'sess-2', status: 'declined' }),
      session({ id: 'sess-1', status: 'expired' }),
    ]);
    renderPanel({ contractStatus: 'awaiting_signatures' });

    expect(await screen.findByText('Awaiting signatures')).toBeInTheDocument();
    expect(screen.getByText('Earlier attempts: 2')).toBeInTheDocument();
  });
});
