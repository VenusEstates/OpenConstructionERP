// @ts-nocheck
// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// A readiness indicator must read the store that the feature it stands for
// actually searches.
//
// This card offers two install routes that fill two different collections.
// "Generate All Regions" (`POST /vector/index/`) fills `cost_items`, which is
// what rate suggestion, classification and anomaly checks search. Restoring a
// published snapshot fills `cwicr_<lang>_v3`, which is what element matching
// searches. Neither route populates the other collection.
//
// The card used to describe both with one number taken from `/vector/status/`,
// which can only see `cost_items`. So it showed 0% over a working matching
// install, and a green 100% over an install where matching returned nothing.
// A single boolean over two independent stores is how that happened.
//
// The assertion that would have caught it is not "the number renders". It is
// that the two indicators are INDEPENDENT: one store's payload must not be
// able to move the other store's tile. A test that drove both stores the same
// way would have passed before this fix as happily as after it, so every case
// below drives them in opposite directions.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('@/shared/lib/api', async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn() };
});

import { apiGet } from '@/shared/lib/api';
import { VectorDatabaseSection } from '../ImportDatabasePage';

// ── Fixtures ────────────────────────────────────────────────────────────────
//
// Both shapes are the real ones. `/vector/status/` reports `cost_collection`
// and nothing about the v3 catalogues; `/catalogues-v3/` reports a per-row
// `install_status` plus whether the server answered at all. Neither payload
// contains a field describing the other store, which is the point.

/** `cost_items` full: the AI features would work, matching is unaffected. */
const AI_STORE_FULL = {
  connected: true,
  backend: 'qdrant',
  cost_collection: { vectors_count: 500_000, points_count: 500_000, status: 'green' },
  can_restore_snapshots: true,
};

/** `cost_items` empty: the AI features would not work, matching is unaffected. */
const AI_STORE_EMPTY = {
  connected: true,
  backend: 'qdrant',
  cost_collection: { vectors_count: 0, points_count: 0, status: 'green' },
  can_restore_snapshots: true,
};

/** Three v3 catalogues installed: matching would work. */
const CATALOGUES_LOADED = {
  catalogues: [
    { region: 'DE_BERLIN', country_iso: 'DE', language: 'de', install_status: 'loaded', size_mb: 410 },
    { region: 'USA_USD', country_iso: 'US', language: 'en', install_status: 'loaded', size_mb: 520 },
    { region: 'FR_PARIS', country_iso: 'FR', language: 'fr', install_status: 'loaded', size_mb: 380 },
    { region: 'JP_TOKYO', country_iso: 'JP', language: 'ja', install_status: 'available', size_mb: 300 },
  ],
  server: { url: 'http://qdrant:6333', reachable: true, total_collections: 3 },
};

/** Server answered, nothing installed: matching would return nothing. */
const CATALOGUES_NONE = {
  catalogues: [
    { region: 'DE_BERLIN', country_iso: 'DE', language: 'de', install_status: 'available', size_mb: 410 },
    { region: 'USA_USD', country_iso: 'US', language: 'en', install_status: 'available', size_mb: 520 },
  ],
  server: { url: 'http://qdrant:6333', reachable: true, total_collections: 0 },
};

/**
 * Server did NOT answer. Every row falls back to `available` in the handler,
 * so the rows look identical to CATALOGUES_NONE - only `server.reachable`
 * separates "nothing is installed" from "we could not ask".
 */
const CATALOGUES_UNREACHABLE = {
  catalogues: [
    { region: 'DE_BERLIN', country_iso: 'DE', language: 'de', install_status: 'available', size_mb: 410 },
    { region: 'USA_USD', country_iso: 'US', language: 'en', install_status: 'available', size_mb: 520 },
  ],
  server: { url: null, reachable: false, total_collections: 0 },
};

// ── Harness ─────────────────────────────────────────────────────────────────

function mount({ vectorStatus, catalogues }) {
  apiGet.mockImplementation((url: string) => {
    if (url.includes('/vector/status/')) return Promise.resolve(vectorStatus);
    if (url.includes('/catalogues-v3/')) return Promise.resolve(catalogues);
    if (url.includes('/vector/regions/')) return Promise.resolve([]);
    if (url.includes('/regions/stats/')) {
      // Denominator for the coverage tile. Held constant across every case so
      // that a moving coverage number can only come from the vector count.
      return Promise.resolve([{ region: 'DE_BERLIN', count: 500_000 }]);
    }
    return Promise.resolve(null);
  });

  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <VectorDatabaseSection />
    </QueryClientProvider>,
  );
}

/** Digits only, so the assertion does not depend on the thousands separator
 *  of whichever locale the number formatter resolves in this environment. */
const digits = (el: HTMLElement) => el.textContent.replace(/\D/g, '');

async function readTiles() {
  const matching = await screen.findByTestId('tile-matching-catalogues');
  return {
    aiVectors: digits(screen.getByTestId('tile-ai-vectors')),
    aiCoverage: screen.getByTestId('tile-ai-coverage').textContent.trim(),
    matchingText: matching.textContent.trim(),
    matchingDigits: digits(matching),
  };
}

describe('vector readiness reads the store it stands for', () => {
  beforeEach(() => {
    apiGet.mockReset();
  });

  it('reports matching as not ready even when the AI store is completely indexed', async () => {
    // The direction that shipped the false green: press "Generate All
    // Regions", watch it succeed, then match nothing.
    mount({ vectorStatus: AI_STORE_FULL, catalogues: CATALOGUES_NONE });
    const tiles = await readTiles();

    expect(tiles.aiVectors).toBe('500000');
    expect(tiles.aiCoverage).toBe('100%');
    // The whole defect in one assertion: a full cost-item index must not be
    // allowed to speak for the catalogue store.
    expect(tiles.matchingDigits).toBe('0');
  });

  it('reports matching as ready even when the AI store is completely empty', async () => {
    // The opposite direction: restore a published snapshot and matching works
    // while `cost_items` is still empty. The card used to show 0% here and
    // say nothing at all about the catalogues that had just been installed.
    mount({ vectorStatus: AI_STORE_EMPTY, catalogues: CATALOGUES_LOADED });
    const tiles = await readTiles();

    expect(tiles.aiVectors).toBe('0');
    expect(tiles.aiCoverage).toBe('0%');
    expect(tiles.matchingDigits).toBe('3');
  });

  it('does not let either store move the other store\'s tile', async () => {
    // The independence property stated directly. Hold `/vector/status/`
    // identical across both renders and change only the catalogue payload:
    // the AI tiles must not move, and the matching tile must. Any future
    // change that folds these back onto one source fails here, including one
    // that keeps both numbers looking plausible.
    const first = mount({ vectorStatus: AI_STORE_FULL, catalogues: CATALOGUES_NONE });
    const before = await readTiles();

    // Unmount before the second render: `readTiles` reads by test id from the
    // whole screen, and two live copies would make getByTestId throw on a
    // duplicate rather than report the number under test.
    first.unmount();
    apiGet.mockReset();
    mount({ vectorStatus: AI_STORE_FULL, catalogues: CATALOGUES_LOADED });
    await waitFor(async () => {
      expect((await readTiles()).matchingDigits).toBe('3');
    });
    const after = await readTiles();

    // Same cost-item payload both times, so these must be unchanged.
    expect(after.aiVectors).toBe(before.aiVectors);
    expect(after.aiCoverage).toBe(before.aiCoverage);
    // Different catalogue payload, so this must have changed.
    expect(after.matchingDigits).not.toBe(before.matchingDigits);
  });

  it('shows an unreachable catalogue server as unknown rather than as zero installed', async () => {
    // `list_v3_catalogues` derives every row's status from one probe of the
    // Qdrant server, and on a failed probe every row reads `available` - byte
    // for byte what "nothing is installed" looks like. Printing 0 here would
    // be a new false statement in the opposite direction from the old one.
    mount({ vectorStatus: AI_STORE_FULL, catalogues: CATALOGUES_UNREACHABLE });
    const tiles = await readTiles();

    expect(tiles.matchingText).toBe('—');
    expect(tiles.matchingDigits).toBe('');
  });

  it('asks the catalogue endpoint at all', async () => {
    // Cheap, but it is the regression that started this: the endpoint that
    // knows about the matching store existed and had no caller on this page,
    // so the tile had nothing to be wrong about.
    mount({ vectorStatus: AI_STORE_FULL, catalogues: CATALOGUES_LOADED });
    await screen.findByTestId('tile-matching-catalogues');

    const asked = apiGet.mock.calls.map(([url]) => url);
    expect(asked.some((u: string) => u.includes('/catalogues-v3/'))).toBe(true);
    expect(asked.some((u: string) => u.includes('/vector/status/'))).toBe(true);
  });
});
