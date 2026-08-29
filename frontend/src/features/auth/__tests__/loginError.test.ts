import { describe, it, expect } from 'vitest';
import { loginFailureKind } from '../loginError';

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
