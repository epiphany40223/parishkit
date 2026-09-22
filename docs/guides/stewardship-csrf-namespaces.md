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
  `pk_admin_csrf` on `/admin/`. One path rule, `cookie_namespace()` in the
  dependency-free `web/namespaces.py`, selects both the session and the
  CSRF cookie; the CSRF failure view and the access gate use the same
  module. Token masking, comparison, origin and referer checks are
  Django's; `HttpOnly`, `SameSite=Lax`, `Secure` in production, the cookie
  age and `CSRF_COOKIE_DOMAIN` come from the existing settings. The
  middleware refuses to load if `CSRF_USE_SESSIONS`, `CSRF_COOKIE_NAME` or
  `CSRF_COOKIE_PATH` is set, since it would otherwise ignore them.
- Login and privilege rotation still call `rotate_token()`, so a pre-login
  token cannot be replayed after sign-in; the new secret now replaces only
  its own namespace's cookie. A malformed cookie value is replaced, never
  trusted.
- `CSRF_USE_SESSIONS` was not chosen: the namespaced sessions are Django
  sessions, but storing the secret there would write a database session for
  every anonymous sign-in page view.
- A Family JSON request that fails CSRF because its token or cookie is
  missing or stale now gets a distinguishable 403,
  `{"error": "csrf_failed", "csrf_token": ...}`, with a fresh token for the
  browser's own Family CSRF cookie. Only same-origin script can read it,
  like the token already rendered into Family pages, and the rejected
  request did nothing. Origin and Referer failures, which a fresh token
  cannot fix, and every Admin or non-JSON failure keep the plain uniform
  403.
- The Family page scripts read the token from the page, never from the
  cookie. A shared helper in `ui-v1.js` handles `csrf_failed` by writing
  the fresh token into every page token field, which the sign-out form,
  the form and submit calls, keepalive and presence all read.
- The Family form (`family-v1.js`) then keeps every answer and announces
  through the existing status region that the page's security check was
  refreshed and the answers are still here. It does not resubmit
  automatically. Any other 403 still ends the tab's session as before.
- The background keepalive and presence calls in `ui-v1.js` retry once
  with the fresh token, without ending the session; the rejected keepalive
  never reached the view, so its activity claim is still unused.

Existing browsers carry an unused `csrftoken` cookie; it is ignored. The
database tests read the namespace cookie their route uses.

## Focused validation

- PostgreSQL, CSRF namespaces: a Family page's rendered token keeps working
  for keepalive after an Admin Google sign-in in the same client, whose
  Family CSRF cookie is unchanged; an Admin page's token still signs out
  after a Family sign-in; each login rotates its own namespace's secret and
  the pre-login token is then refused; a stale Family JSON request gets
  `csrf_failed` with a working fresh token and `no-store`, as does a
  missing token, while a foreign Origin, non-JSON and Admin failures keep
  the plain 403; a signed-out Family with a valid token still gets
  `session_ended`; a malformed cookie is replaced. Each namespace's
  `Set-Cookie` has its own path (`/` or `/admin/`), `HttpOnly`,
  `SameSite=Lax`, and `Secure` and the domain when those settings are set,
  with no `csrftoken` cookie; `CSRF_USE_SESSIONS` or a custom CSRF cookie
  name or path refuses to load (12 tests).
- PostgreSQL, related suites: every suite whose requests read the CSRF
  cookie, plus access gates, Google recovery, response submission, session
  review rounds and runtime grants (541 tests).
- Browser: in Chromium, Firefox and WebKit, a submit rejected with
  `csrf_failed` keeps the edited answer, the sign-out control and the
  current step, refreshes the page token, and the next submit sends the
  fresh token and is accepted; a keepalive and a presence beat rejected
  with `csrf_failed` each retry once with their fresh token, and the
  session stays open.

## Checkpoint

Implementation, focused validation and review round 1 are complete. The
round 1 correction check, the remaining
[review rounds](stewardship-csrf-namespaces-reviews.md), CI and protected
delivery remain open. No deployment, release, live-provider write or
database deletion is authorized by this increment.
