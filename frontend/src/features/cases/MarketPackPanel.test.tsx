// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko
//
// The panel has to separate applied, available and absent, and the deployment
// it was written on can only produce two of those by itself: eighteen packs on
// disk and `active_slug` null, so everything is "available". A suite that
// checked each state alone would pass on a build where applied and available
// render identically, which is the mistake worth guarding here. Every state
// test below is written against another state's rendering rather than against
// a fixed string.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const hookMock = vi.hoisted(() => ({
  usePartnerPack: vi.fn(),
  useInstalledPacks: vi.fn(),
  partnerLogoUrl: vi.fn(() => '/api/v1/partner-pack/logo'),
}));
vi.mock('@/shared/hooks/usePartnerPack', () => hookMock);

const authMock = vi.hoisted(() => ({ role: 'admin' as string }));
vi.mock('@/stores/useAuthStore', () => ({
  useAuthStore: (sel: (s: { userRole: string }) => unknown) => sel({ userRole: authMock.role }),
}));

// The dialog is the modules feature's own component and drags the pack apply
// API in with it. What this file is about is which pack the panel points the
// button at, so the dialog is reduced to a marker that prints the slug it was
// handed.
vi.mock('@/features/modules/PartnerPackApplyDialog', () => ({
  PartnerPackApplyDialog: ({ open, slug }: { open: boolean; slug: string }) =>
    open ? <div data-testid="apply-dialog" data-slug={slug} /> : null,
}));

import { MarketPackPanel } from './MarketPackPanel';

function packOf(slug: string, country: string, name = slug) {
  return {
    slug,
    partner_name: name,
    type: 'country',
    description: `Pre-configured for ${country}: standards, tax and currency`,
    default_locale: 'en-US',
    default_currency: 'EUR',
    default_tax_template: 'de_vat_19',
    pack_version: '0.2.0',
    validation_rule_packs: ['din276'],
    metadata: { country },
    branding: { primary_color: '#123456', accent_color: null },
  };
}

const INSTALLED = [
  packOf('bimhessen-de', 'DE'),
  packOf('uk-jct', 'GB'),
  packOf('us-california', 'US'),
  packOf('us-texas', 'US'),
];

function mount(region: string | null | undefined, activeSlug: string | null = null) {
  hookMock.useInstalledPacks.mockReturnValue({
    isLoading: false,
    data: { active_slug: activeSlug, installed: INSTALLED },
  });
  return render(
    <MemoryRouter>
      <MarketPackPanel region={region} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  cleanup();
  hookMock.useInstalledPacks.mockReset();
  authMock.role = 'admin';
});

describe('<MarketPackPanel />', () => {
  it('names the market pack and offers the action that switches it on', () => {
    // The founder's report was that the case said a regional pack was needed
    // and gave the reader nothing to press. The button is the assertion.
    mount('DE');
    const panel = screen.getByTestId('market-pack-panel');
    expect(panel.getAttribute('data-pack-state')).toBe('available');
    expect(panel.getAttribute('data-pack-slug')).toBe('bimhessen-de');
    expect(screen.getByRole('button', { name: /activate/i })).toBeEnabled();
  });

  it('renders the applied market differently from the same market unapplied', () => {
    const availableHtml = mount('GB').container.innerHTML;
    cleanup();
    mount('GB', 'uk-jct');

    const panel = screen.getByTestId('market-pack-panel');
    expect(panel.getAttribute('data-pack-state')).toBe('applied');
    // Not just a different attribute: the applied state must not still be
    // offering to activate what is already active.
    expect(screen.queryByRole('button', { name: /activate/i })).toBeNull();
    expect(panel.outerHTML).not.toBe(availableHtml);
  });

  it('says nothing for a market with no pack rather than the nearest one', () => {
    // Ten shipped cases carry ES and no Spanish pack exists on disk. A panel
    // that fell back to a plausible neighbour would put German standards and
    // a German VAT template under a Spanish case.
    const { container } = mount('ES');
    expect(container.firstChild).toBeNull();
  });

  it('points the button at the applied pack when several serve one market', () => {
    // us-california and us-texas both declare US. Unapplied the panel leads
    // with the first; applied it must lead with the one in force, or a Texan
    // workspace reading a US case is offered California.
    const unapplied = mount('US');
    expect(unapplied.getByTestId('market-pack-panel').getAttribute('data-pack-slug')).toBe(
      'us-california',
    );
    cleanup();

    mount('US', 'us-texas');
    expect(screen.getByTestId('market-pack-panel').getAttribute('data-pack-slug')).toBe('us-texas');
  });

  it('matches the case spelling of a market against the pack spelling', () => {
    // Cases write DE, packs write de. Both are right in their own file, and a
    // case-sensitive comparison would blank the panel on every case.
    const upper = mount('DE').container.innerHTML;
    cleanup();
    const lower = mount('de').container.innerHTML;
    expect(lower).toBe(upper);
    expect(lower).not.toBe('');
  });

  it('lets a non-admin see the pack but not apply it', () => {
    // Applying is admin-only server side. Hiding the panel from everyone else
    // would hide the ANSWER too, and a reader who cannot apply still needs to
    // know which pack the numbers on this page assume.
    const adminHtml = mount('DE').container.innerHTML;
    cleanup();

    authMock.role = 'viewer';
    mount('DE');
    expect(screen.getByTestId('market-pack-panel')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /activate/i })).toBeDisabled();
    expect(screen.getByTestId('market-pack-panel').outerHTML).not.toBe(adminHtml);
  });

  it('keeps the registry reachable in both states', () => {
    // The other packs for a market, and what an applied pack configures, live
    // one click away. The deep link has to carry the slug or the reader lands
    // on a list of eighteen and matches by eye.
    mount('DE');
    const link = screen.getByRole('link');
    expect(link.getAttribute('href')).toBe('/modules?tab=packs&pack=bimhessen-de');
    cleanup();

    mount('DE', 'bimhessen-de');
    expect(screen.getByRole('link').getAttribute('href')).toBe(
      '/modules?tab=packs&pack=bimhessen-de',
    );
  });
});
