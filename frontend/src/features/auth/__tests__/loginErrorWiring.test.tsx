import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

/**
 * That the sign-in screen actually asks the classifier.
 *
 * `loginError.test.ts` pins the rule itself, and it passes on the pure
 * function alone: delete the `loginFailureKindFromResponse` branch out of
 * `LoginPageNext.handleSubmit` and all 22 of those cases stay green, because
 * nothing in them renders a page. The helper would keep giving the right
 * answer to a question the screen had stopped asking, and the outage wording
 * would silently go back to being "Invalid email or password".
 *
 * So these cases assert the wiring rather than the rule: what a person
 * actually ends up reading, for a response the classifier is supposed to split
 * away from a wrong password.
 *
 * `t` is mocked to echo the key, so an assertion names the branch that ran
 * rather than a sentence that a copy edit or a locale change could move.
 */

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}));

vi.mock('@/app/i18n', () => ({
  SUPPORTED_LANGUAGES: [{ code: 'en', name: 'English', flag: 'gb', country: 'gb' }],
}));

// The marketing background animates on a setInterval and has no bearing on
// which error the form reaches; stubbing it keeps the render deterministic.
vi.mock('../AuthBackground', () => ({
  AuthBackground: () => null,
}));

import { LoginPageNext } from '../LoginPageNext';

/**
 * Mount the page with `fetch` already scripted.
 *
 * The stub has to be in place before the render, not after: the component
 * probes `/auth/first-run` on mount, and a stub installed later would leave
 * that probe reaching for the real network and settling mid-test.
 */
async function renderWith(login: () => Promise<unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/auth/first-run')) {
        return Promise.resolve({ ok: true, json: async () => ({ demo_enabled: false }) });
      }
      return login();
    }),
  );
  render(
    <MemoryRouter>
      <LoginPageNext />
    </MemoryRouter>,
  );
  // Let the mount-time probe settle inside act, so the only state left moving
  // is the one the submit below is about.
  await act(async () => { await Promise.resolve(); });
}

/** Fill both fields and submit, the way a person reaches an error at all. */
function submitLogin() {
  fireEvent.change(document.querySelector('#login-email-next')!, {
    target: { value: 'someone@example.com' },
  });
  fireEvent.change(document.querySelector('#login-password-next')!, {
    target: { value: 'a-real-password' },
  });
  fireEvent.submit(document.querySelector('form')!);
}

/** A response whose body is a page rather than JSON, as a proxy or WAF serves. */
function unreadable(status: number) {
  return Promise.resolve({
    ok: false,
    status,
    json: async () => {
      throw new Error('not json');
    },
  });
}

describe('LoginPageNext error wiring', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('tells a person the server did not answer when a proxy returns 502', async () => {
    // The case the whole split exists for: the backend is down, a proxy
    // answers for it, and `fetch` resolves - so the "unable to connect" catch
    // never runs and this used to land on the credentials wording.
    await renderWith(() => unreadable(502));
    submitLogin();

    await waitFor(() => expect(screen.getByText('auth.server_unavailable')).toBeTruthy());
    expect(screen.queryByText('auth.invalid_credentials')).toBeNull();
  });

  it('says the same for a 4xx whose body carried no message we can read', async () => {
    // A WAF or CDN answering with its own page: sub-500, so the status alone
    // still reads as 'credentials', and only the empty body separates it.
    await renderWith(() => unreadable(403));
    submitLogin();

    await waitFor(() => expect(screen.getByText('auth.server_unavailable')).toBeTruthy());
  });

  it('still shows our own refusal when the API really answered about the credentials', async () => {
    // The control. Without this a suite would pass just as happily on a screen
    // that called every failure an outage, which is the same defect pointing
    // the other way.
    await renderWith(() =>
      Promise.resolve({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Invalid email or password' }),
      }),
    );
    submitLogin();

    await waitFor(() => expect(screen.getByText('Invalid email or password')).toBeTruthy());
    expect(screen.queryByText('auth.server_unavailable')).toBeNull();
  });

  it('keeps the connection wording when nothing answered at all', async () => {
    // ECONNREFUSED: `fetch` rejects, so this is the one path that never reaches
    // the classifier, and it has to stay distinct from both of the above.
    await renderWith(() => Promise.reject(new TypeError('Failed to fetch')));
    submitLogin();

    await waitFor(() => expect(screen.getByText('auth.connection_error')).toBeTruthy());
    expect(screen.queryByText('auth.invalid_credentials')).toBeNull();
  });
});
