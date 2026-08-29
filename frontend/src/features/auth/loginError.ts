/** Deciding what a failed sign-in is allowed to tell the person signing in.
 *
 * A backend that is down does not produce a network error when anything sits
 * in front of it. A reverse proxy answers 502, 503 or 504, and that is an
 * ordinary HTTP response: `fetch` resolves, `res.ok` is false, and the
 * `catch` branch that exists for "unable to connect" never runs. So the one
 * place a server outage actually lands is the same branch as a typo in a
 * password.
 *
 * That is not hypothetical. On 2026-08-29 our own demo application was down
 * for a full day while Caddy answered 502 for every request to it, and the
 * sign-in screen in front of it had exactly one sentence for the situation:
 * "Invalid email or password". Telling someone their password is wrong is the
 * single claim we can be sure is unfounded, because nothing ever read it.
 */

/** What kind of failure a sign-in response describes. */
export type LoginFailureKind = 'credentials' | 'unavailable';

/**
 * Classify a failed sign-in response by its status code.
 *
 * @param status - The HTTP status of the response that was not ok.
 * @returns `'unavailable'` when the server never got as far as checking the
 *   credentials, `'credentials'` when it did and refused them.
 *
 * 5xx covers both halves of the same story: our own server failing while
 * answering for itself, and a proxy in front of it reporting that it could
 * not reach the server at all. Neither of them looked at the password.
 * Everything else - 401, 403, 422 - is the server having read the request and
 * declined it, which is what the credentials wording is for.
 */
export function loginFailureKind(status: number): LoginFailureKind {
  return status >= 500 ? 'unavailable' : 'credentials';
}
