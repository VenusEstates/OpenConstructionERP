// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// The three states this notice has to separate are applied, available and
// absent, and the deployment it was written on can only produce two of them by
// itself: eighteen packs on disk, active_slug null, so everything is
// "available". A suite that checked each state on its own would therefore pass
// on a build where applied and available render identically, which is the one
// mistake worth guarding against here. Every state test below compares its
// rendering against another state's.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const hookMock = vi.hoisted(() => ({
  usePartnerPack: vi.fn(),
  useInstalledPacks: vi.fn(),
  partnerLogoUrl: vi.fn(() => '/api/v1/partner-pack/logo'),
}));
vi.mock('@/shared/hooks/usePartnerPack', () => hookMock);

import { MarketPackNotice } from './MarketPackNotice';

function packOf(slug: string, country: string) {
  return {
    slug,
    partner_name: slug,
    type: 'country',
    default_locale: 'en-US',
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
      <MarketPackNotice region={region} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  cleanup();
  hookMock.useInstalledPacks.mockReset();
});

describe('<MarketPackNotice />', () => {
  it('offers a way in when the market has a pack that is switched off', () => {
    // The founder's report: the case named a required pack and gave the reader
    // nowhere to go. The href is the assertion, not the presence of a chip.
    mount('DE');
    const notice = screen.getByTestId('market-pack-notice');
    expect(notice.getAttribute('data-pack-state')).toBe('available');
    expect(notice.getAttribute('href')).toBe('/modules?tab=packs&pack=bimhessen-de');
  });

  it('renders the applied market differently from the same market unapplied', () => {
    const availableHtml = mount('GB').container.innerHTML;
    cleanup();
    mount('GB', 'uk-jct');

    const notice = screen.getByTestId('market-pack-notice');
    expect(notice.getAttribute('data-pack-state')).toBe('applied');
    // Both states reach the same pack; the destination is what knows there is
    // nothing to apply. What must differ is the rendering itself, and that is
    // asserted against the other state rather than against a fixed string.
    expect(notice.getAttribute('href')).toBe('/modules?tab=packs&pack=uk-jct');
    expect(notice.outerHTML).not.toBe(availableHtml);
  });

  it('says nothing for a market with no pack rather than the nearest one', () => {
    // Ten shipped cases carry ES and no Spanish pack exists. A chip that
    // shrugged on all ten would teach the reader to stop reading the row, and
    // one that fell back to a plausible neighbour would put German standards
    // under a Spanish case.
    const { container } = mount('ES');
    expect(container.firstChild).toBeNull();
  });

  it('points at the applied pack when several serve one market', () => {
    // us-california and us-texas both declare US. Unapplied, the notice leads
    // with the first; applied, it must lead with the one actually in force,
    // otherwise a Texan workspace reading a US case is told to set up
    // California.
    const unapplied = mount('US');
    expect(unapplied.getByTestId('market-pack-notice').getAttribute('data-pack-slug')).toBe(
      'us-california',
    );
    cleanup();

    mount('US', 'us-texas');
    const notice = screen.getByTestId('market-pack-notice');
    expect(notice.getAttribute('data-pack-slug')).toBe('us-texas');
    expect(notice.getAttribute('data-pack-state')).toBe('applied');
  });

  it('matches the case spelling of a market against the pack spelling', () => {
    // Cases write DE, packs write de. Both are correct in their own file, and
    // a case-sensitive comparison would silence the notice on every case.
    const upper = mount('DE').container.innerHTML;
    cleanup();
    const lower = mount('de').container.innerHTML;
    expect(lower).toBe(upper);
    expect(lower).not.toBe('');
  });

  it('draws the market flag rather than a fallback mark', () => {
    // The emblem is what makes the chip readable before the name is; a
    // monogram here would mean the pack's country never reached it.
    mount('GB');
    expect(
      screen
        .getByTestId('market-pack-notice')
        .querySelector('[data-pack-emblem]')
        ?.getAttribute('data-country'),
    ).toBe('gb');
  });
});
