# Stewardship CSRF namespaces

This guide records a correction found by the
[pre-launch gate](../plans/stewardship/v1-launch.md#v1-process-changes)
review (round 2, medium severity): Family and Admin sessions used separate
cookies, but shared one CSRF cookie. It is part of the
[v1 launch scope](../plans/stewardship/v1-launch.md) and needs no schema
change.

## The defect

Session cookies are namespaced: `pk_family` on `/` and `pk_admin` on
`/admin/`. Django's CSRF secret was one site-wide `csrftoken` cookie, and
Family login, Admin login and Admin privilege rotation each rotate it. Every
rotation invalidated the tokens already rendered in the other namespace's
open pages.

A Family with a partly completed form open, in a browser where a staff
helper then signed in to Admin, had its next submit rejected with a CSRF
403. The Family client treats a 403 as an ended session, so it cleared the
answers and showed "Your session has ended. Unsubmitted changes have not
been saved.", although the Family session was still valid. An Admin editing
page failed the same way after a Family sign-in in the same browser. The
[architecture specification](../specs/stewardship/architecture/spec.md#identity-and-session-security)
requires Family and administration sessions to be separate namespaces.

## The correction

- CSRF state follows the session namespace. `NamespacedCsrfMiddleware`, a
  small `CsrfViewMiddleware` subclass in `accounts/sessions.py`, replaces
  Django's middleware and overrides only the cookie read and write hooks.
  Family routes use `pk_family_csrf` on `/`; `/admin/` routes use
  `pk_admin_csrf` on `/admin/`. One `cookie_namespace()` path rule selects
  both the session and the CSRF cookie. Token masking, comparison,
  origin and referer checks, `HttpOnly`, `SameSite=Lax`, `Secure` in
  production and the cookie age are Django's and the existing settings'.
- Login and privilege rotation still call `rotate_token()`, so a pre-login
  token cannot be replayed after sign-in; the new secret now replaces only
  its own namespace's cookie. A malformed cookie value is replaced, never
  trusted.
- `CSRF_USE_SESSIONS` was not chosen: the namespaced sessions are Django
  sessions, but storing the secret there would write a database session for
  every anonymous sign-in page view.
- A Family JSON request that fails CSRF now gets a distinguishable 403,
  `{"error": "csrf_failed", "csrf_token": ...}`, with a fresh token for the
  browser's own Family CSRF cookie. Only same-origin script can read it,
  like the token already rendered into Family pages, and the rejected
  request did nothing. Every other CSRF failure, Admin included, keeps the
  plain uniform 403.
- The Family client (`family-v1.js`) reads its token from the page, never
  from the cookie. On `csrf_failed` it writes the fresh token into the
  page's token inputs, which the sign-out form and the keepalive and
  presence calls share, keeps every answer and announces through the
  existing status region that the page's security check was refreshed and
  the answers are still here. It does not retry automatically. Any other
  403 still ends the tab's session as before.

Existing browsers carry an unused `csrftoken` cookie; it is ignored. The
database tests read the namespace cookie their route uses.

## Focused validation

- PostgreSQL, CSRF namespaces: a Family page's rendered token keeps working
  for keepalive after an Admin Google sign-in in the same client, whose
  Family CSRF cookie is unchanged; an Admin page's token still signs out
  after a Family sign-in; each login rotates its own namespace's secret and
  the pre-login token is then refused; a stale Family JSON request gets
  `csrf_failed` with a working fresh token and `no-store`, while non-JSON
  and Admin failures keep the plain 403; a signed-out Family with a valid
  token still gets `session_ended`; a malformed cookie is replaced.
- PostgreSQL, related suites: every suite whose requests read the CSRF
  cookie, plus access gates, Google recovery, response submission, session
  review rounds and runtime grants (535 tests).
- Browser: in Chromium, Firefox and WebKit, a submit rejected with
  `csrf_failed` keeps the edited answer, the sign-out control and the
  current step, refreshes the page token, and the next submit sends the
  fresh token and is accepted.

## Checkpoint

Implementation and focused validation are complete. The three
[review rounds](stewardship-csrf-namespaces-reviews.md), CI and protected
delivery remain open. No deployment, release, live-provider write or
database deletion is authorized by this increment.
