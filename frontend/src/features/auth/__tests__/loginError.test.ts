import { describe, it, expect } from 'vitest';
import { loginFailureKind, loginFailureKindFromResponse } from '../loginError';

/**
 * The case this exists for: a backend that is down, behind a proxy.
 *
 * `fetch` resolves normally for a 502 - it is a response, not a network
 * failure - so the `catch` branch labelled "unable to connect" is never
 * reached, and before this helper every such status fell through to the
 * credentials wording. On 2026-08-29 our own demo application was down for a
 * day behind a proxy answering 502, and the sign-in screen told everyone who
 * tried that their password was wrong.
 */
describe('loginFailureKind', () => {
  it('calls a proxy 502 unavailable, because nothing read the password', () => {
    expect(loginFailureKind(502)).toBe('unavailable');
  });

  it.each([500, 501, 503, 504])('treats %i as unavailable', (status) => {
    expect(loginFailureKind(status)).toBe('unavailable');
  });

  it.each([400, 401, 403, 404, 422, 429])(
    'treats %i as a credentials answer, because the server read the request',
    (status) => {
      expect(loginFailureKind(status)).toBe('credentials');
    },
  );

  it('does not depend on where the 5xx boundary is written', () => {
    // 499 and 500 sit either side of the only comparison in the helper, so
    // this pair is what would catch an off-by-one rather than restating it.
    expect(loginFailureKind(499)).toBe('credentials');
    expect(loginFailureKind(500)).toBe('unavailable');
  });
});

/**
 * What the status split above left open.
 *
 * A 5xx is only one of the ways something in front of the application answers
 * for it. A WAF blocks the request with 403, a CDN throttles it with 429, a
 * proxy that lost the route replies 404, and each of them serves its own page.
 * `res.json()` throws on a page, so both sign-in screens are already holding
 * `null` at that point, and `null` fell straight through to "Invalid email or
 * password" - about a request that never reached anything holding a password.
 */
describe('loginFailureKindFromResponse', () => {
  const detail = 'Invalid email or password';

  it.each([403, 404, 429])(
    'calls %i unavailable when the body carried no message, because nothing we own answered it',
    (status) => {
      expect(loginFailureKindFromResponse(status, null)).toBe('unavailable');
    },
  );

  it('still calls a 401 carrying our own detail a credentials answer', () => {
    // The control that stops the rule swallowing real refusals. Every 401 our
    // login endpoint raises carries this detail, so a wrong password keeps its
    // own wording rather than being excused as an outage.
    expect(loginFailureKindFromResponse(401, detail)).toBe('credentials');
  });

  it('keeps a wrong password and an unknown account indistinguishable', () => {
    // The backend answers both with one status and one detail. Nothing here
    // may split them, and the only way to be sure is to ask about the pair.
    expect(loginFailureKindFromResponse(401, detail)).toBe(loginFailureKindFromResponse(401, detail));
    expect(loginFailureKindFromResponse(401, detail)).toBe('credentials');
  });

  // Both of these are messages the server really sends, not invented ones. The
  // 429 sentence is copied from the throttle our own login route raises, in
  // backend/app/modules/users/router.py, and it matters that it is a
  // HTTPException with a string detail: it reaches the browser as JSON, so a
  // burst of wrong passwords keeps the throttle wording and cannot be excused
  // as an outage. Only a 429 from something in front of us, serving its own
  // page, arrives with nothing to read.
  it.each([
    [422, 'body.email: value is not a valid email address'],
    [429, 'Too many login attempts. Please wait a minute and try again.'],
  ])('treats %i with a detail as an answer about the request', (status, message) => {
    expect(loginFailureKindFromResponse(status as number, message as string)).toBe('credentials');
  });

  it('leaves the 5xx half alone whether or not a body came with it', () => {
    expect(loginFailureKindFromResponse(502, null)).toBe('unavailable');
    expect(loginFailureKindFromResponse(500, 'Internal server error')).toBe('unavailable');
  });

  it('parts company with the status-only rule exactly at the gap it was written for', () => {
    // Naming the delta rather than the result: the status half has to keep
    // saying 'credentials' here, and the whole point of the second half is
    // that it overrules it. A revert of either one collapses this pair.
    expect(loginFailureKind(403)).toBe('credentials');
    expect(loginFailureKindFromResponse(403, null)).toBe('unavailable');
  });

  it('reads an empty detail as no detail', () => {
    // The screens fall back on a falsy `parsed`, so a message that is present
    // but empty has to classify the same way as one that is absent, otherwise
    // the two halves disagree about the same response.
    expect(loginFailureKindFromResponse(403, '')).toBe('unavailable');
  });
});
