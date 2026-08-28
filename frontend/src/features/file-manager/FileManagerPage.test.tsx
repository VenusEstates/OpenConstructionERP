// DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
// Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
//
// Can a person actually see a tag on a file?
//
// Tags could be created and tags could be filtered, and they never appeared on
// a file. TagPill was written, covered by its own test and spliced into
// nothing; the epic's other two pieces, TagFilterFacet and BulkTagDrawer, were
// wired and live. Its integration document even names the two files and the
// exact JSX, and shipped code in this feature already referred to "the per-row
// TagPill renderer" as a thing that existed. The plan was written and never
// executed, and no test could notice, because a test that mounts TagPill
// itself cannot fail when nothing in the application mounts TagPill.
//
// So this starts where a person starts. It lands on the file manager, presses
// the category in the tree the way a user would, and requires a tag to be
// visible on the file that carries it, in both the grid and the list. The
// assertions read the tag's own display name through the pill's test id, which
// only TagPill renders.
//
// The last test is the negative control and it is what makes the others mean
// anything: the same click, the same file, the same rendered rows, with the
// file carrying no tags, and no pill. Without it an assertion that merely
// tracked "the file list rendered" would pass just as well.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { FileManagerPage } from './FileManagerPage';
import { useProjectContextStore } from '@/stores/useProjectContextStore';

// Only the request verbs are replaced. This page and its children import far
// more than these names from the module, and a factory returning only the mocks
// would leave the rest undefined at module scope.
vi.mock('@/shared/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/shared/lib/api')>('@/shared/lib/api');
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() };
});

// The real router hooks, restored for this file only.
//
// The shared test setup replaces `useSearchParams` with a fresh empty instance
// on every render and a setter that does nothing. This page keeps the chosen
// category in the URL and re-hydrates its state from it, so under that mock the
// hydrate effect sees "no kind in the URL" on the very next render and undoes
// the click. That is an artefact of the harness and not of the page: with a
// real MemoryRouter the round trip works exactly as it does in a browser.
vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));

import { apiGet } from '@/shared/lib/api';

const mockGet = vi.mocked(apiGet);

const PROJECT_ID = 'p1';
const CATEGORY = 'Documents';
const FILE_NAME = 'Foundation plan.pdf';
const TAG_NAME = 'Structural';

function fileRow() {
  return {
    id: 'f1',
    kind: 'document',
    name: FILE_NAME,
    project_id: PROJECT_ID,
    size_bytes: 2048,
    mime_type: 'application/pdf',
    extension: '.pdf',
    modified_at: '2026-08-20T10:00:00Z',
    physical_path: '/files/f1.pdf',
    relative_path: 'f1.pdf',
    storage_backend: 'local',
    download_url: null,
    preview_url: null,
    thumbnail_url: null,
    discipline: null,
    category: null,
    extra: {},
  };
}

function tagRecord() {
  return {
    id: 't1',
    project_id: PROJECT_ID,
    name: 'structural',
    display_name: TAG_NAME,
    color: '#3b82f6',
    category: 'discipline',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    created_by_id: null,
    assignment_count: 1,
  };
}

/**
 * Answer everything the page reaches for on the way in.
 *
 * `tags` is the only axis the tests vary, because it is the axis the splice is
 * about. Order matters here: the tree and locations paths both sit under
 * `/files/`, so they are matched before the list.
 */
function serveApi(tags: ReturnType<typeof tagRecord>[]) {
  mockGet.mockImplementation((path: string) => {
    if (path.includes('/files/tree/')) {
      return Promise.resolve([
        { id: 'document', label: CATEGORY, kind: 'category', file_count: 1, total_bytes: 2048 },
      ]);
    }
    if (path.includes('/files/locations/')) return Promise.resolve({ locations: [] });
    if (path.includes('/v1/file-tags/by-file/')) return Promise.resolve(tags);
    if (path.includes('/v1/file-tags')) return Promise.resolve([]);
    if (path.includes('/v1/file-favorites')) return Promise.resolve([]);
    if (path.includes('/files/')) return Promise.resolve({ items: [fileRow()], total: 1 });
    return Promise.resolve({ items: [], total: 0 });
  });
}

function renderFileManager() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FileManagerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Walk in from the landing state the way a person does: pick a category.
 *
 * The landing screen offers the same category twice, once in the tree at the
 * side and once as a folder card in the middle, so this takes the first rather
 * than asserting there is only one. Either is a real way in, and requiring a
 * single match would make the test fail the day somebody adds a third.
 */
async function openCategory(user: ReturnType<typeof userEvent.setup>) {
  const ways = await screen.findAllByRole('button', { name: new RegExp(CATEGORY) });
  await user.click(ways[0] as HTMLElement);
  return screen.findByTitle(FILE_NAME);
}

/** Every tag chip on screen, by the display name each one prints. */
function pillNames(): string[] {
  return screen.queryAllByTestId('tag-pill').map((el) => el.textContent?.trim() ?? '');
}

beforeEach(() => {
  mockGet.mockReset();
  // `useParams` is mocked globally to `{}`, so the active project can only come
  // from the switcher store, which is where the header's project picker puts it.
  useProjectContextStore.setState({ activeProjectId: PROJECT_ID, activeProjectName: 'Riverside' });
  localStorage.clear();
});

describe('seeing a tag on a file in the grid', () => {
  it('shows no tag before the category is opened, so getting there takes the click', async () => {
    serveApi([tagRecord()]);
    renderFileManager();

    // Wait for the tree, otherwise "absent" would only mean "still loading".
    expect((await screen.findAllByRole('button', { name: new RegExp(CATEGORY) })).length)
      .toBeGreaterThan(0);
    expect(pillNames()).toEqual([]);
  });

  it('puts the tag on the file a person opened the category to look at', async () => {
    const user = userEvent.setup();
    serveApi([tagRecord()]);
    renderFileManager();

    await openCategory(user);

    await waitFor(() => expect(pillNames()).toContain(TAG_NAME));
  });
});

describe('seeing a tag on a file in the list', () => {
  it('puts the tag under the file name in the table view too', async () => {
    // The view is remembered per reader, and half of them are in the table.
    localStorage.setItem('file-manager:view-mode', 'list');
    const user = userEvent.setup();
    serveApi([tagRecord()]);
    renderFileManager();

    await openCategory(user);

    await waitFor(() => expect(pillNames()).toContain(TAG_NAME));
  });
});

describe('the control that makes the above mean something', () => {
  it('renders the same file with no pill when the file carries no tags', async () => {
    const user = userEvent.setup();
    serveApi([]);
    renderFileManager();

    // The file is on screen, so the absence below is the tag row declining to
    // render rather than the category never having opened.
    expect(await openCategory(user)).toBeInTheDocument();
    expect(pillNames()).toEqual([]);
  });
});
