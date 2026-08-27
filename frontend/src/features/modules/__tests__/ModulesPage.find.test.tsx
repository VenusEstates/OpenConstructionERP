// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// The founder's path through the registry page, walked end to end.
//
// `moduleSearch.test.ts` proves the filter reaches the module. That is a
// statement about a function. This is the statement about the page, and the two
// fail differently: a correct filter wired behind a tab the reader never opens
// reproduces the original report exactly, because the page lands on Company
// Profiles and the modules are three tabs away.
//
// Rendered in German throughout, with no English fallback behind it. The word
// under test, "Regionalpaket", exists in no `display_name` and in no `oe_` id,
// so it can only arrive through the translated name the card prints.
//
// Run:  npx vitest run src/features/modules/__tests__/ModulesPage.find.test.tsx

import { describe, it, expect, beforeAll, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import i18next, { type i18n as I18n } from 'i18next';

// This suite exercises the real translation path, so the harness-wide stub of
// react-i18next (src/test/setup.ts) must not stand in for it here.
vi.unmock('react-i18next');

vi.mock('@/shared/lib/api', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(async () => ({})),
  apiDelete: vi.fn(async () => ({})),
}));

import { I18nextProvider } from 'react-i18next';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import de from '../../../app/locales/de';
import { getModuleTranslations } from '../../../modules/_registry';
import { apiGet } from '@/shared/lib/api';
import { ModulesPage } from '../ModulesPage';

/** Three of the 189 the server loads, enough to tell a hit from a miss. */
const SYSTEM_MODULES = [
  {
    name: 'oe_china_pack',
    version: '1.0.0',
    display_name: 'Regional Pack - China',
    category: 'regional',
    depends: [],
    has_router: true,
    loaded: true,
    enabled: true,
    is_core: false,
  },
  {
    name: 'oe_bim_hub',
    version: '1.0.0',
    display_name: 'BIM Hub',
    category: 'core',
    depends: [],
    has_router: true,
    loaded: true,
    enabled: true,
    is_core: true,
  },
  {
    name: 'oe_tendering',
    version: '1.0.0',
    display_name: 'Tendering',
    // A category the page has no label entry for. The chip row is built from
    // the data, so this module still has to be reachable.
    category: 'business',
    depends: [],
    has_router: true,
    loaded: true,
    enabled: true,
    is_core: false,
  },
];

let i18n: I18n;

beforeAll(async () => {
  i18n = i18next.createInstance();
  await i18n.init({
    lng: 'de',
    fallbackLng: false,
    resources: { de: { translation: {} } },
    keySeparator: false,
    nsSeparator: false,
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  // Production's merge order: the manifest bundles first, the locale file on
  // top, so a key both define is won by the locale file.
  for (const [lng, keys] of Object.entries(getModuleTranslations())) {
    i18n.addResourceBundle(lng, 'translation', keys, true, true);
  }
  i18n.addResourceBundle('de', 'translation', de.translation, false, true);
});

beforeEach(() => {
  vi.mocked(apiGet).mockReset();
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path.startsWith('/v1/modules/')) return SYSTEM_MODULES;
    if (path.includes('onboarding-presets')) return [];
    if (path.includes('partner-pack')) return { installed: [], active: null };
    if (path === '/marketplace') return [];
    return {};
  });
});

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <I18nextProvider i18n={i18n}>
          <ModulesPage />
        </I18nextProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** The id of the tab currently selected, e.g. `modules-tab-profiles`. */
function selectedTabId(): string | null | undefined {
  return screen.getByRole('tab', { selected: true }).getAttribute('id');
}

function findField(): HTMLElement {
  // Queried by role rather than by its label so the test does not have to know
  // which language the label is currently in.
  return screen.getByRole('searchbox');
}

/**
 * The name a card prints to a German reader, read out of the locale that ships
 * rather than assumed. Asserting on the English `display_name` would pass on a
 * page that never translated anything, which is the state this replaces.
 */
function germanName(moduleName: string): string {
  const key = `modules.catalog.${moduleName.replace(/^oe_/, '')}`;
  const value = (de.translation as Record<string, string>)[key];
  if (!value) {
    throw new Error(`Fixture module ${moduleName} has no German name, so the assertion proves nothing`);
  }
  return value;
}

describe('finding a module without knowing which tab it is on', () => {
  it('offers the search field on the tab the page opens on', () => {
    renderPage();
    expect(selectedTabId()).toBe('modules-tab-profiles');
    expect(findField()).toBeInTheDocument();
  });

  it('reaches the China pack from the German words on its card', async () => {
    renderPage();
    expect(selectedTabId()).toBe('modules-tab-profiles');

    fireEvent.change(findField(), { target: { value: 'Regionalpaket' } });

    // Typing opened the panel that holds the answer, without the reader
    // having to know it was there.
    await waitFor(() => expect(selectedTabId()).toBe('modules-tab-system'));
    expect(await screen.findByText('Regionalpaket - China')).toBeInTheDocument();
    // and it is a filter, not just a jump
    expect(screen.queryByText(germanName('oe_bim_hub'))).not.toBeInTheDocument();
  });

  it('reaches it from the English word a support thread would quote', async () => {
    // The reported case, verbatim: standing on the tab the page opens on,
    // typing the one word the reader knows.
    renderPage();
    expect(selectedTabId()).toBe('modules-tab-profiles');
    fireEvent.change(findField(), { target: { value: 'china' } });
    await waitFor(() => expect(selectedTabId()).toBe('modules-tab-system'));
    expect(await screen.findByText('Regionalpaket - China')).toBeInTheDocument();
  });

  it('keeps a module whose category the page has no label for reachable', async () => {
    renderPage();
    fireEvent.change(findField(), { target: { value: 'tendering' } });
    await waitFor(() => expect(selectedTabId()).toBe('modules-tab-system'));
    expect(await screen.findByText(germanName('oe_tendering'))).toBeInTheDocument();
  });

  it('says where else to look when nothing on this tab matches', async () => {
    renderPage();
    fireEvent.change(findField(), { target: { value: 'nothing matches this' } });
    await waitFor(() => expect(selectedTabId()).toBe('modules-tab-system'));

    const emptyState = de.translation['modules.no_system_matches_hint'] as string | undefined;
    // The key is English-only for now, so the reader gets the defaultValue.
    const hint =
      emptyState ??
      'Try a shorter search or pick All above. Company profiles, packs and data packages are separate lists, on the other tabs of this page.';
    expect(await screen.findByText(hint)).toBeInTheDocument();
    expect(screen.queryByText('Regionalpaket - China')).not.toBeInTheDocument();
  });

  it('clearing the field brings the whole list back', async () => {
    renderPage();
    fireEvent.change(findField(), { target: { value: 'china' } });
    expect(await screen.findByText('Regionalpaket - China')).toBeInTheDocument();

    fireEvent.change(findField(), { target: { value: '' } });
    expect(await screen.findByText(germanName('oe_bim_hub'))).toBeInTheDocument();
    expect(screen.getByText('Regionalpaket - China')).toBeInTheDocument();
  });
});

describe('the category chips', () => {
  it('offers every category the server sent, including ones with no label entry', async () => {
    renderPage();
    fireEvent.change(findField(), { target: { value: 'a' } });
    await waitFor(() => expect(selectedTabId()).toBe('modules-tab-system'));

    // `business` has no entry in the page's label map and falls back to the
    // raw backend value rather than dropping out of the filter.
    expect(await screen.findByRole('button', { name: /business/i })).toBeInTheDocument();
  });

  it('narrows the list to one category and can be cleared again', async () => {
    renderPage();
    fireEvent.change(findField(), { target: { value: 'a' } });
    await waitFor(() => expect(selectedTabId()).toBe('modules-tab-system'));

    fireEvent.click(await screen.findByRole('button', { name: /business/i }));
    await waitFor(() => expect(screen.queryByText('Regionalpaket - China')).not.toBeInTheDocument());
    expect(screen.getByText(germanName('oe_tendering'))).toBeInTheDocument();
  });
});
