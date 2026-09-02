// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * Tests for the price-analysis drawer, the first screen in the product that
 * asks the backend how a unit rate is built up.
 *
 * They are written against the transport rather than against `boqApi`: what
 * can actually be wrong here is the URL (a missing trailing slash, a preset
 * that never travels, a download that fetches the international sheet while
 * the reader is looking at the EFB one), and mocking `boqApi` would assert
 * only that this file calls its own wrapper. `@/shared/lib/api` is spread
 * from the original because `features/boq/api.ts` imports five other helpers
 * from it at module level.
 *
 * The fixtures carry money as Decimal-rendered strings, which is what
 * `PriceBreakdown.to_dict()` puts on the wire, and they carry all six resource
 * kinds in `kind_totals`, zeros included, which is what the backend always
 * does. Both of those are the shapes that break a reader written against a
 * friendlier imaginary payload.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { apiGet, downloadWithAuth } from '@/shared/lib/api';
import { PriceAnalysisPanel } from './PriceAnalysisPanel';
import type { PriceAnalysisPresetInfo, PriceAnalysisResponse } from './api';

vi.mock('@/shared/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/shared/lib/api')>();
  return {
    ...actual,
    apiGet: vi.fn(),
    downloadWithAuth: vi.fn(),
  };
});

const mockApiGet = vi.mocked(apiGet);
const mockDownload = vi.mocked(downloadWithAuth);

/* ── Preset fixtures ─────────────────────────────────────── */

/**
 * Presets as `Preset.to_dict()` puts them on the wire.
 *
 * Trimmed to three of the backend's six, which is enough to show that the
 * drawer offers whatever the server lists rather than a fixed pair. The
 * Hungarian one is here because it is the case the derivation exists for, and
 * because it is the one preset whose kind order differs from every other: it
 * opens with material rather than labour.
 */
const INTERNATIONAL_PRESET: PriceAnalysisPresetInfo = {
  name: 'international',
  label: 'Unit price analysis',
  label_i18n_key: 'price_breakdown.preset.international',
  region: 'international',
  kinds: [
    { kind: 'labor', label: 'Labour', i18n_key: 'price_breakdown.kind.labor' },
    { kind: 'material', label: 'Material', i18n_key: 'price_breakdown.kind.material' },
    { kind: 'machinery', label: 'Machinery', i18n_key: 'price_breakdown.kind.machinery' },
    { kind: 'equipment', label: 'Equipment', i18n_key: 'price_breakdown.kind.equipment' },
    {
      kind: 'subcontractor',
      label: 'Subcontract',
      i18n_key: 'price_breakdown.kind.subcontractor',
    },
    { kind: 'other', label: 'Other', i18n_key: 'price_breakdown.kind.other' },
  ],
  line_i18n_keys: {},
};

const EFB_PRESET: PriceAnalysisPresetInfo = {
  ...INTERNATIONAL_PRESET,
  name: 'efb',
  label: 'EFB price sheets (221/222/223)',
  label_i18n_key: 'price_breakdown.preset.efb',
  region: 'DE',
};

const HU_PRESET: PriceAnalysisPresetInfo = {
  name: 'hu_anyag_dij',
  label: 'Anyag és díj bontás (material and fee split)',
  label_i18n_key: 'price_breakdown.preset.hu_anyag_dij',
  region: 'HU',
  kinds: [
    { kind: 'material', label: 'Anyag', i18n_key: 'price_breakdown.kind.material' },
    { kind: 'labor', label: 'Díj - munkadíj', i18n_key: 'price_breakdown.kind.labor' },
    {
      kind: 'machinery',
      label: 'Díj - gépköltség',
      i18n_key: 'price_breakdown.kind.machinery',
    },
    {
      kind: 'equipment',
      label: 'Díj - eszközköltség',
      i18n_key: 'price_breakdown.kind.equipment',
    },
    {
      kind: 'subcontractor',
      label: 'Díj - alvállalkozói teljesítés',
      i18n_key: 'price_breakdown.kind.subcontractor',
    },
    {
      kind: 'other',
      label: 'Díj - egyéb költség',
      i18n_key: 'price_breakdown.kind.other',
    },
  ],
  line_i18n_keys: {},
};

/** What the presets endpoint answers with. */
const PRESET_TABLE = { presets: [INTERNATIONAL_PRESET, EFB_PRESET, HU_PRESET] };

/* ── Fixtures ───────────────────────────────────────────────────────── */

/** A rate with a stored split: 2.5 h of formwork crew plus 1.05 m3 of concrete. */
const SPLIT_ANALYSIS: PriceAnalysisResponse = {
  position_ref: '01.02.003',
  description: 'Reinforced concrete wall C30/37',
  unit: 'm3',
  currency: 'EUR',
  position_quantity: '120.0000',
  components: [
    {
      kind: 'labor',
      kind_i18n_key: 'price_breakdown.kind.labor',
      description: 'Formwork crew',
      unit: 'h',
      quantity: '2.5000',
      unit_cost: '48.00',
      amount: '120.00',
    },
    {
      kind: 'material',
      kind_i18n_key: 'price_breakdown.kind.material',
      description: 'Concrete C30/37',
      unit: 'm3',
      quantity: '1.0500',
      unit_cost: '96.00',
      amount: '100.80',
    },
  ],
  // Always all six, whatever the position carries.
  kind_totals: {
    labor: '120.00',
    material: '100.80',
    machinery: '0.00',
    equipment: '0.00',
    subcontractor: '0.00',
    other: '0.00',
  },
  kind_i18n_keys: {
    labor: 'price_breakdown.kind.labor',
    material: 'price_breakdown.kind.material',
    machinery: 'price_breakdown.kind.machinery',
    equipment: 'price_breakdown.kind.equipment',
    subcontractor: 'price_breakdown.kind.subcontractor',
    other: 'price_breakdown.kind.other',
  },
  i18n_keys: {
    direct: 'price_breakdown.line.direct',
    overhead: 'price_breakdown.line.overhead',
    risk: 'price_breakdown.line.risk',
    profit: 'price_breakdown.line.profit',
    unit_rate: 'price_breakdown.line.unit_rate',
    position_total: 'price_breakdown.line.position_total',
  },
  direct_unit_cost: '220.80',
  overhead_pct: '12.00',
  overhead_amount: '26.50',
  risk_pct: '0.00',
  risk_amount: '0.00',
  profit_pct: '5.00',
  profit_amount: '12.37',
  unit_rate: '259.67',
  position_total: '31160.40',
  preset: INTERNATIONAL_PRESET,
};

/** The same position asked for as an EFB sheet: the German form wording. */
const EFB_ANALYSIS: PriceAnalysisResponse = {
  ...SPLIT_ANALYSIS,
  preset: EFB_PRESET,
  efb: {
    position_ref: '01.02.003',
    unit: 'm3',
    currency: 'EUR',
    rows: [
      { kind: 'labor', label: 'Lohnkosten (221)', amount: '120.00' },
      { kind: 'material', label: 'Stoffkosten (223)', amount: '100.80' },
      { kind: 'machinery', label: 'Geraetekosten', amount: '0.00' },
      { kind: 'equipment', label: 'Vorhaltekosten', amount: '0.00' },
      { kind: 'subcontractor', label: 'Nachunternehmerleistungen (222)', amount: '0.00' },
      { kind: 'other', label: 'Sonstige Kosten', amount: '0.00' },
    ],
    direct_unit_cost: '220.80',
    overhead_amount: '26.50',
    risk_amount: '0.00',
    profit_amount: '12.37',
    unit_rate: '259.67',
  },
};

/**
 * The same position on a Hungarian project, with no preset asked for.
 *
 * The numbers are identical; what differs is the preset the server resolved
 * from the project's own country, and with it the wording and the order of
 * the categories. That is the whole of the difference a preset makes to a JSON
 * reader, which is why the response has to name it.
 */
const HU_ANALYSIS: PriceAnalysisResponse = {
  ...SPLIT_ANALYSIS,
  currency: 'HUF',
  preset: HU_PRESET,
};

/**
 * What the backend answers for a position nobody has broken down: one
 * synthesised "other" line carrying the whole rate, so the sheet renders.
 * The response alone cannot be told apart from a real one-resource split,
 * which is why the panel is told by its caller instead.
 */
const UNSPLIT_ANALYSIS: PriceAnalysisResponse = {
  ...SPLIT_ANALYSIS,
  components: [
    {
      kind: 'other',
      kind_i18n_key: 'price_breakdown.kind.other',
      description: 'Reinforced concrete wall C30/37',
      unit: 'm3',
      quantity: '1.0000',
      unit_cost: '259.67',
      amount: '259.67',
    },
  ],
  kind_totals: {
    labor: '0.00',
    material: '0.00',
    machinery: '0.00',
    equipment: '0.00',
    subcontractor: '0.00',
    other: '259.67',
  },
  direct_unit_cost: '259.67',
  overhead_pct: '0.00',
  overhead_amount: '0.00',
  profit_pct: '0.00',
  profit_amount: '0.00',
};

/* ── Harness ────────────────────────────────────────────────────────── */

function renderPanel(hasResourceSplit = true) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PriceAnalysisPanel
        positionId="pos-1"
        positionOrdinal="01.02.003"
        positionDescription="Reinforced concrete wall C30/37"
        hasResourceSplit={hasResourceSplit}
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
}

/** Every path this panel asks for, in call order. */
function requestedPaths(): string[] {
  return mockApiGet.mock.calls.map((call) => String(call[0]));
}

/**
 * The price-analysis requests only.
 *
 * The drawer also fetches the preset table, and the order the two queries fire
 * in is React Query's business rather than this component's, so an assertion
 * on "the first call" would be asserting the wrong thing and would break on a
 * reordering that changes nothing.
 */
function analysisPaths(): string[] {
  return requestedPaths().filter((path) => !path.includes('/price-analysis/presets/'));
}

/** Answer the presets endpoint; hand everything else to `answer`. */
function serve(answer: (path: string) => PriceAnalysisResponse) {
  mockApiGet.mockImplementation(async (path: string) =>
    String(path).includes('/price-analysis/presets/') ? PRESET_TABLE : answer(String(path)),
  );
}

beforeEach(() => {
  mockApiGet.mockReset();
  mockDownload.mockReset();
  mockDownload.mockResolvedValue(undefined);
  // Answer whichever preset the panel asks for, the way the endpoint does:
  // the `efb` key exists only when the request carried `preset=efb`, and a
  // request naming no preset is answered with whatever the project resolves
  // to, which for this default fixture is the international sheet.
  serve((path) => (path.includes('preset=efb') ? EFB_ANALYSIS : SPLIT_ANALYSIS));
});

/* ── Tests ──────────────────────────────────────────────────────────── */

describe('PriceAnalysisPanel', () => {
  it('opens without naming a preset, so the project decides which sheet this is', async () => {
    // This assertion used to be its own opposite: it required the drawer to
    // ask for `?preset=international`, and it passed, because that is what the
    // drawer did. The endpoint has six presets and reads the project's country
    // when the caller names none, so sending a preset from here overrode that
    // resolution from the one place in the system that knows least about the
    // project. A Hungarian estimator got the international single-rate sheet
    // on a workspace the platform already knew was Hungarian.
    renderPanel();

    await waitFor(() => expect(analysisPaths().length).toBeGreaterThan(0));
    expect(analysisPaths()[0]).toBe('/v1/boq/positions/pos-1/price-analysis/');
    expect(analysisPaths()[0]).not.toContain('preset=');
  });

  it('offers every preset the server lists, not a pair written down here', async () => {
    // The drawer used to hold its own two-entry copy of a six-entry table, so
    // four presets that the backend could render were unreachable from the
    // interface with no error and nothing saying they existed.
    renderPanel();

    await screen.findByText('Formwork crew');
    await waitFor(() =>
      expect(requestedPaths()).toContain('/v1/boq/price-analysis/presets/'),
    );
    for (const p of PRESET_TABLE.presets) {
      expect(screen.getByText(p.label)).toBeInTheDocument();
    }
  });

  it('shows the sheet the server chose, and shows it as the one selected', async () => {
    // Nobody clicks anything here. The project is Hungarian, the server says
    // so by naming the preset on the response, and the drawer has to follow:
    // the Hungarian button reads pressed even though the local choice is still
    // empty.
    serve(() => HU_ANALYSIS);
    renderPanel();

    await screen.findByText('Formwork crew');
    const hungarian = screen.getByText(HU_PRESET.label);
    expect(hungarian).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(INTERNATIONAL_PRESET.label)).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('labels and orders the categories from the preset on the response', async () => {
    // What this proves is the wiring: the category wording comes from the
    // sheet rather than from a table in the component, and the order is the
    // preset's own. The Hungarian preset opens with material where every other
    // preset opens with labour, so the order is the observable part.
    //
    // What it deliberately does not prove is what a Hungarian user sees. The
    // test harness mocks `t` to return `defaultValue` unconditionally, and in
    // the running product the generic `price_breakdown.kind.*` key wins
    // wherever a locale defines it, because those keys are preset-independent
    // on the backend while these labels are not. A test named for the
    // Hungarian wording would pass here and would start failing the day
    // somebody translates the generic key, which would look like a regression
    // in their translation work rather than the known limit that it is.
    serve(() => HU_ANALYSIS);
    renderPanel();

    await screen.findByText('Formwork crew');

    // Scoped to the category list on purpose. The preset picker's own button
    // reads "Anyag es dij bontas", so a search of the whole document finds
    // "Anyag" in the switch above rather than in a category row, and the
    // comparison below would hold whichever way the list was sorted. Asserting
    // over the whole body here would be a test that passes for a reason
    // unrelated to the thing it is named for.
    const heading = screen.getByText('By category');
    const section = heading.parentElement;
    expect(section).not.toBeNull();
    const text = section?.textContent ?? '';

    // Material before labour: the reverse of every other preset's order, and
    // the only observable effect of the ordering this component now applies.
    expect(text).toContain('Anyag');
    expect(text).toContain('Díj - munkadíj');
    expect(text.indexOf('Anyag')).toBeLessThan(text.indexOf('Díj - munkadíj'));
  });

  it('downloads the sheet the server chose when the reader has chosen nothing', async () => {
    // The download used to fall back to the international sheet whenever the
    // reader had not touched the switch, which after this change is the normal
    // case: it would have handed over a differently-shaped document from the
    // one on screen.
    serve(() => HU_ANALYSIS);
    renderPanel();
    await screen.findByText('Formwork crew');

    fireEvent.click(screen.getByText('Download sheet (Markdown)'));

    await waitFor(() => expect(mockDownload).toHaveBeenCalled());
    expect(String(mockDownload.mock.calls[0]![0])).toBe(
      '/api/v1/boq/positions/pos-1/price-analysis/?format=markdown&preset=hu_anyag_dij',
    );
  });

  it('renders the stored resource lines and leaves out the categories carrying no cost', async () => {
    renderPanel();

    expect(await screen.findByText('Formwork crew')).toBeInTheDocument();
    expect(screen.getByText('Concrete C30/37')).toBeInTheDocument();
    // The unit rate, at the currency's own precision, from a Decimal string.
    expect(screen.getByText('€259.67')).toBeInTheDocument();
    // 2.5000 on the wire reads as 2.5 on screen, not as four trailing zeros.
    expect(screen.getByText(/^2\.5$/)).toBeInTheDocument();

    // `kind_totals` carries all six kinds; the four empty ones are not rows.
    expect(screen.queryByText('Machinery')).not.toBeInTheDocument();
    expect(screen.queryByText('Equipment')).not.toBeInTheDocument();
    expect(screen.queryByText('Subcontract')).not.toBeInTheDocument();
  });

  it('switches to the EFB preset and shows the form wording the sheet is checked against', async () => {
    renderPanel();
    await screen.findByText('Formwork crew');

    fireEvent.click(screen.getByText('EFB price sheets (221/222/223)'));

    await waitFor(() =>
      expect(requestedPaths()).toContain('/v1/boq/positions/pos-1/price-analysis/?preset=efb'),
    );
    expect(await screen.findByText('Lohnkosten (221)')).toBeInTheDocument();
    expect(screen.getByText('Stoffkosten (223)')).toBeInTheDocument();
    // A Formblatt has fixed rows, so the empty ones stay and read zero.
    expect(screen.getByText('Nachunternehmerleistungen (222)')).toBeInTheDocument();
  });

  it('downloads the sheet for the preset on screen, not the default one', async () => {
    renderPanel();
    await screen.findByText('Formwork crew');

    fireEvent.click(screen.getByText('EFB price sheets (221/222/223)'));
    await screen.findByText('Lohnkosten (221)');
    fireEvent.click(screen.getByText('Download sheet (Markdown)'));

    await waitFor(() => expect(mockDownload).toHaveBeenCalled());
    expect(String(mockDownload.mock.calls[0]![0])).toBe(
      '/api/v1/boq/positions/pos-1/price-analysis/?format=markdown&preset=efb',
    );
    expect(mockDownload.mock.calls[0]![1]).toBe('price_analysis_01.02.003.md');
  });

  it('closes on Escape from anywhere in the drawer', async () => {
    const onClose = vi.fn();
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <PriceAnalysisPanel
          positionId="pos-1"
          positionOrdinal="01.02.003"
          positionDescription="Reinforced concrete wall C30/37"
          hasResourceSplit
          onClose={onClose}
        />
      </QueryClientProvider>,
    );
    await screen.findByText('Formwork crew');

    // Nothing inside the drawer holds focus, so the listener has to be on the
    // document; a handler bound to the panel would never see this key.
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onClose).toHaveBeenCalled();
  });

  it('says a rate has not been broken down instead of drawing a one-line table', async () => {
    serve(() => UNSPLIT_ANALYSIS);
    renderPanel(false);

    expect(
      await screen.findByText(/has not been broken down into resources yet/),
    ).toBeInTheDocument();
    // No component table: the synthesised line is not presented as a split.
    expect(screen.queryByText('Resource')).not.toBeInTheDocument();
    expect(screen.queryByText('Unit cost')).not.toBeInTheDocument();
    // The position's own numbers are still true and still shown.
    expect(screen.getByText('Direct cost per unit')).toBeInTheDocument();
    expect(screen.getByText('Unit rate')).toBeInTheDocument();
    expect(screen.getByText('€31,160.40')).toBeInTheDocument();
  });
});
