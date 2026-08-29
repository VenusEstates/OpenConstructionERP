/**
 * Smoke — a backend that fails must leave a usable page behind, not a blank
 * one. Two different mechanisms do that work, and they are not the same
 * thing, so they get one test each:
 *
 *   1. A 5xx on a list query settles React Query into `isError` and the
 *      feature swaps the list for <RecoveryCard> (ProjectsPage.tsx:918 →
 *      shared/ui/RecoveryCard.tsx:94). No React error boundary is involved:
 *      nothing threw during render.
 *   2. A throw during render is caught by the route-level <ErrorBoundary>
 *      keyed on pathname (App.tsx:768 → shared/ui/ErrorBoundary.tsx:79),
 *      which replaces the page and leaves the shell standing.
 *
 * The previous single test claimed (2) in its name while injecting (1), and
 * could not run in any environment: its locator comma-joined plain CSS with
 * the `text=` and `:has-text()` engines inside one CSS string, which
 * Playwright rejects with `Unexpected token "=" while parsing css selector`.
 */
import { test, expect } from '../fixtures';
import { expectAppShell, captureScreen } from '../helpers';

/**
 * The projects LIST endpoint, with or without the `?status=` filter
 * (features/projects/api.ts:252 and :259). Deliberately anchored to the end
 * of the URL so `/v1/projects/{id}/...` keeps working — only the query under
 * test fails, and everything else on the page stays healthy.
 */
const PROJECT_LIST_RE = /\/api\/v1\/projects\/(\?[^#]*)?$/;

/**
 * The same endpoint as fetched by the PAGE (api.ts:252 `/v1/projects/` and
 * :259 `?status=`), excluding the header project switcher's own copy of it
 * (`?limit=500`, Header.tsx:1477). Two components ask for this list, so
 * counting matches of PROJECT_LIST_RE would let the switcher's background
 * refetch stand in for the page's, and "Retry refetched" would be provable
 * without Retry doing anything.
 */
const PAGE_LIST_RE = /\/api\/v1\/projects\/(\?status=[^&#]*)?$/;

/**
 * <RecoveryCard>'s generic-failure title, `recovery.load_failed_title`
 * (en.ts:9649 "Couldn’t load this", de.ts:12524, ru.ts:12800). `.` covers
 * the typographic apostrophe in the English string.
 */
const RECOVERY_TITLE = /couldn.t load this|konnte nicht geladen werden|не удалось загрузить/i;

/**
 * <ErrorBoundary>'s heading, `error.something_wrong` (en.ts:4546,
 * de.ts:10423, ru.ts:8849) — rendered at ErrorBoundary.tsx:89.
 */
const BOUNDARY_TITLE = /something went wrong|etwas ist schiefgegangen|что-то пошло не так/i;

test.describe('@smoke error-boundary', () => {
  test('a 500 on the projects list swaps the list for a recovery card, not a white screen', async ({
    authedPage,
  }) => {
    // Requests prove Retry re-asks; responses prove the list actually
    // SETTLED. The baseline below needs the second one: a request that has
    // merely gone out leaves the page still empty, and "no recovery card
    // here" would then be true only because nothing had rendered yet.
    const listGets: string[] = [];
    const listSettled: number[] = [];
    authedPage.on('request', (req) => {
      if (req.method() === 'GET' && PAGE_LIST_RE.test(req.url())) listGets.push(req.url());
    });
    authedPage.on('response', (res) => {
      if (res.request().method() === 'GET' && PAGE_LIST_RE.test(res.url())) {
        listSettled.push(res.status());
      }
    });

    // Baseline. Without it, "the recovery card is present" would also pass on
    // a page that merely contains those words, which is how the old text
    // locator would have read as coverage forever.
    await authedPage.goto('/projects');
    await expectAppShell(authedPage);
    await expect
      .poll(() => listSettled.length, { message: 'the projects list never answered' })
      .toBeGreaterThan(0);
    // ...and the results branch has actually committed. The skeleton
    // (SkeletonLoader.tsx:146, rendered by ProjectsPage.tsx:913 while
    // isLoading) is the only thing standing between a settled query and one
    // of the result branches, so its disappearance is the positive event
    // that makes the next line mean something. Without it "no recovery card
    // here" is true of any page that has not finished rendering, which is a
    // claim a slow server satisfies for free.
    await expect(
      authedPage.getByTestId('skeleton-grid'),
      'the projects list never finished loading',
    ).toHaveCount(0, { timeout: 15_000 });
    await expect(
      authedPage.getByRole('heading', { name: RECOVERY_TITLE }),
      'recovery card is on screen before anything was broken',
    ).toHaveCount(0);

    // Now break the list query and reload into the failure.
    await authedPage.route(PROJECT_LIST_RE, (route) =>
      route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'forced test failure' }),
      }),
    );
    await authedPage.reload();

    // The shell survives: a failed list is not a white screen.
    await expectAppShell(authedPage);

    const title = authedPage.getByRole('heading', { name: RECOVERY_TITLE });
    await expect(title, 'a failed projects list rendered no recovery card').toBeVisible({
      timeout: 15_000,
    });

    // The retry control is the one belonging to THIS card: EmptyState renders
    // its action as the only button inside the same container as the title
    // (shared/ui/EmptyState.tsx:79 for the title, :82-93 for the action slot;
    // RecoveryCard passes exactly one Button into it at RecoveryCard.tsx:99).
    const card = title.locator('xpath=..');
    const retry = card.getByRole('button');
    await expect(retry, 'recovery card offered no action').toHaveCount(1);

    // Clicking it must actually re-ask the backend. That is what separates
    // RecoveryCard's Retry from any other button that happens to say "Retry".
    const before = listGets.length;
    await retry.click();
    await expect
      .poll(() => listGets.length, { message: 'Retry did not refetch the projects list' })
      .toBeGreaterThan(before);

    await captureScreen(authedPage, 'smoke', 'error-boundary-500-recovery-card');
  });

  test('a render-time crash is caught by the route error boundary', async ({ authedPage }) => {
    // A 200 whose body is not the array the callers are typed for
    // (features/projects/api.ts:252, Header.tsx:1477). Two components read it
    // as an array during render: ProjectsPage at :254 (`projects.map(...)`
    // inside a useMemo) and the header project switcher at Header.tsx:1485
    // (`(projects ?? []).filter(...)`). Either throws in the render pass,
    // which is the only kind of failure <ErrorBoundary> can catch.
    //
    // The switcher throws FIRST, and that is the point of this test rather
    // than an accident of it. <AppLayout> — sidebar and header — is mounted
    // OUTSIDE the boundary (App.tsx:765-769 wraps only the <Outlet/>), so a
    // throw from the chrome takes the whole application down: measured
    // against a running instance, #root ends up with zero children and no
    // fallback anywhere. This test therefore fails until the shell is inside
    // a boundary. That failure is the finding, not a flake.
    await authedPage.route(PROJECT_LIST_RE, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'not a list' }),
      }),
    );
    await authedPage.goto('/projects');

    // The fallback FIRST, the surviving shell second, and that order is the
    // whole point. Checking the shell before the crash has landed is a claim
    // about a page that has not crashed yet: `expectAppShell` passed here on
    // the run that ended with #root emptied, because it looked while the
    // header was still on screen and the throw arrived after. An assertion
    // that can be satisfied by being early measures the clock.
    const heading = authedPage.getByRole('heading', { name: BOUNDARY_TITLE });
    await expect(heading, 'a render crash produced no error boundary UI').toBeVisible({
      timeout: 15_000,
    });

    // Now that the fallback is on screen, the crash has definitely happened,
    // so this says something: the boundary sits inside AppLayout
    // (App.tsx:766-772) and a crashed page must not take the sidebar and
    // header down with it.
    await expectAppShell(authedPage);

    // Everything below is scoped to the fallback's own container, the parent
    // of that heading (ErrorBoundary.tsx:85-121), so nothing here can be
    // satisfied by an unrelated element elsewhere on the page.
    const fallback = heading.locator('xpath=..');

    // The boundary prints the thrown message in its details block
    // (ErrorBoundary.tsx:96-103). The runtime message is English whatever the
    // UI locale is, so this pins the assertion to the boundary itself rather
    // than to any panel that happens to share its wording. `toContainText`
    // does not require visibility, which matters: the <details> is collapsed.
    await expect(fallback.locator('details pre')).toContainText(/is not a function/i);

    // Both recovery affordances are offered: Try again and Go to Dashboard
    // (ErrorBoundary.tsx:106-119).
    await expect(fallback.locator('> div > button')).toHaveCount(2);

    await captureScreen(authedPage, 'smoke', 'error-boundary-render-crash');
  });
});
