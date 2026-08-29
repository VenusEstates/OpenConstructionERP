// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
/**
 * The delete guard is the part of the site-inventory delete flow that decides
 * what a reader is told, so these tests are about the sentence as much as the
 * arithmetic. Two things can go wrong quietly here and both are covered.
 *
 * A holder counted at zero must not survive into the list. The refusal reads
 * "X is referenced by ...", so a zero holder would state a reason that is not
 * true, and an all-zero list would refuse a delete that the server allows -
 * the page decides between the confirmation and the refusal on this list being
 * empty or not.
 *
 * The wording must follow the count. The backend words its own refusal in the
 * singular when there is one holder, and this list is rendered instead of that
 * sentence, so it has to make the same distinction rather than always say
 * "1 recorded movements".
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { holderList, holderPhrase, probeItemHolders, probeLocationHolders } from '../deleteGuard';
import type { StockItem } from '../api';

vi.mock('../api', () => ({
  fetchMovementsFor: vi.fn(),
  fetchStockOnHandAt: vi.fn(),
}));

const api = await import('../api');
const fetchMovementsFor = vi.mocked(api.fetchMovementsFor);
const fetchStockOnHandAt = vi.mocked(api.fetchStockOnHandAt);

/** A translator that renders the English defaults the call site carries, so a
 *  test failure points at the guard's wording and not at the locale file. */
const t = ((_key: string, opts?: Record<string, unknown>) => {
  const count = typeof opts?.count === 'number' ? opts.count : undefined;
  const template =
    count === 1
      ? ((opts?.defaultValue_one ?? opts?.defaultValue) as string)
      : ((opts?.defaultValue_other ?? opts?.defaultValue) as string);
  return template.replace('{{count}}', String(count));
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
}) as any;

/** Movement rows: the guard counts them and reads nothing else off them. */
const movements = (n: number) => Array.from({ length: n }, () => ({}) as never);

const onHand = (balances: string[]) => ({
  project_id: 'p',
  location_id: 'l',
  item_count: balances.length,
  rows: balances.map((on_hand, i) => ({ item_id: `i${i}`, name: '', unit: '', on_hand })),
});

const item = (id: string, defaultLocation: string | null) =>
  ({ id, default_location_id: defaultLocation }) as StockItem;

beforeEach(() => {
  vi.resetAllMocks();
});

describe('probeItemHolders', () => {
  it('reports nothing holding an item that has never moved', async () => {
    fetchMovementsFor.mockResolvedValue(movements(0));
    await expect(probeItemHolders('p', 'i1')).resolves.toEqual([]);
  });

  it('counts the movements booked against the item', async () => {
    fetchMovementsFor.mockResolvedValue(movements(3));
    await expect(probeItemHolders('p', 'i1')).resolves.toEqual([
      { kind: 'movements', count: 3 },
    ]);
  });

  it('asks the server for the movements of that item, not of the project', async () => {
    // The count has to be the server's own filtered count. Asking for the
    // project's movements and filtering in the browser would silently
    // undercount past the page's own fetch limit and call a held item free.
    fetchMovementsFor.mockResolvedValue(movements(0));
    await probeItemHolders('proj-1', 'item-7');
    expect(fetchMovementsFor).toHaveBeenCalledWith('proj-1', { itemId: 'item-7' });
  });
});

describe('probeLocationHolders', () => {
  it('reports nothing holding a location nothing was ever booked to', async () => {
    fetchMovementsFor.mockResolvedValue(movements(0));
    fetchStockOnHandAt.mockResolvedValue(onHand([]));
    await expect(probeLocationHolders('p', 'l1', [item('i1', null)])).resolves.toEqual([]);
  });

  it('names all three kinds, in the order the refusal reads them', async () => {
    fetchMovementsFor.mockResolvedValue(movements(9));
    fetchStockOnHandAt.mockResolvedValue(onHand(['4.0000', '2.5000']));
    const held = await probeLocationHolders('p', 'l1', [
      item('i1', 'l1'),
      item('i2', 'l2'),
      item('i3', 'l1'),
    ]);
    expect(held).toEqual([
      { kind: 'stocked', count: 2 },
      { kind: 'movements', count: 9 },
      { kind: 'defaulting', count: 2 },
    ]);
  });

  it('does not count an item that passed through and left as stock standing there', async () => {
    // A zero balance is the whole point of the ledger reaching zero. Counting
    // those rows would hold every location that ever saw material, which is
    // every location, and the guard would never allow a delete at all.
    fetchMovementsFor.mockResolvedValue(movements(0));
    fetchStockOnHandAt.mockResolvedValue(onHand(['0.0000', '0', '']));
    await expect(probeLocationHolders('p', 'l1', [])).resolves.toEqual([]);
  });

  it('counts a negative balance as stock to look at before the location goes', async () => {
    fetchMovementsFor.mockResolvedValue(movements(0));
    fetchStockOnHandAt.mockResolvedValue(onHand(['-3.0000']));
    await expect(probeLocationHolders('p', 'l1', [])).resolves.toEqual([
      { kind: 'stocked', count: 1 },
    ]);
  });

  it('counts only the items defaulting to this location', async () => {
    fetchMovementsFor.mockResolvedValue(movements(0));
    fetchStockOnHandAt.mockResolvedValue(onHand([]));
    const held = await probeLocationHolders('p', 'l1', [
      item('i1', 'l2'),
      item('i2', null),
      item('i3', 'l1'),
    ]);
    expect(held).toEqual([{ kind: 'defaulting', count: 1 }]);
  });
});

describe('holderPhrase', () => {
  it('words a single holder in the singular, as the server does', () => {
    expect(holderPhrase({ kind: 'movements', count: 1 }, t)).toBe('1 recorded movement');
    expect(holderPhrase({ kind: 'stocked', count: 1 }, t)).toBe(
      '1 item with stock still on hand there',
    );
    expect(holderPhrase({ kind: 'defaulting', count: 1 }, t)).toBe('1 item that defaults to it');
  });

  it('words more than one in the plural', () => {
    expect(holderPhrase({ kind: 'movements', count: 4 }, t)).toBe('4 recorded movements');
    expect(holderPhrase({ kind: 'defaulting', count: 2 }, t)).toBe('2 items that default to it');
  });
});

describe('holderList', () => {
  it('reads as one phrase, with the reader language between the last two', () => {
    // The separator comes from CLDR, not from a literal comma in the source:
    // the word before the last holder is a word, and it is not "and" in every
    // language this ships in.
    const list = holderList(
      [
        { kind: 'stocked', count: 2 },
        { kind: 'movements', count: 9 },
        { kind: 'defaulting', count: 1 },
      ],
      t,
    );
    // Asserted by containment and order rather than against one exact string:
    // the separator is CLDR's answer for whichever language is active, so
    // pinning the English one here would make this test a check on the test
    // environment's locale instead of on the guard.
    expect(list).toContain('2 items with stock still on hand there');
    expect(list).toContain('9 recorded movements');
    expect(list).toContain('1 item that defaults to it');
    expect(list.indexOf('2 items')).toBeLessThan(list.indexOf('9 recorded'));
    expect(list.indexOf('9 recorded')).toBeLessThan(list.indexOf('1 item that defaults'));
  });

  it('is just the phrase when one kind holds the row', () => {
    expect(holderList([{ kind: 'movements', count: 1 }], t)).toBe('1 recorded movement');
  });
});
