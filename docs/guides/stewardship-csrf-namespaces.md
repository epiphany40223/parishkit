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
  CSRF cookie; the access gate uses the same module. Token masking,
  comparison, origin and referer checks are Django's; `HttpOnly`,
  `SameSite=Lax`, `Secure` in production, the cookie age and
  `CSRF_COOKIE_DOMAIN` come from the existing settings. The middleware
  refuses to load if `CSRF_USE_SESSIONS`, `CSRF_COOKIE_NAME` or
  `CSRF_COOKIE_PATH` is set, since it would otherwise ignore them.
- Login and privilege rotation still call `rotate_token()`, so a pre-login
  token cannot be replayed after sign-in; the new secret now replaces only
  its own namespace's cookie. A malformed cookie value is replaced, never
  trusted.
- `CSRF_USE_SESSIONS` was not chosen: the namespaced sessions are Django
  sessions, but storing the secret there would write a database session for
  every anonymous sign-in page view.
- The CSRF failure view, the Family page scripts and their handling of a
  403 are unchanged: a Family 403 still ends the tab's session with the
  existing message.

No token recovery is offered. With separate cookies, the Family CSRF secret
rotates only on a Family sign-in, and that sign-in revokes every earlier
Family session in the browser and replaces `pk_family`. A stale Family page
token with a live Family session therefore means the tab's own session was
replaced, possibly by a different Family. Handing that tab a fresh token
would silently rebind it to the newer session: its keepalive would renew
the new session, and its submit would then fail anyway. Ending the old tab
is the correct result. An early revision offered such a recovery; review
round 2 removed it.

Existing browsers carry an unused `csrftoken` cookie; it is ignored. The
database tests read the namespace cookie their route uses.

## Focused validation

- PostgreSQL, CSRF namespaces: a Family page's rendered token keeps working
  for keepalive after an Admin Google sign-in in the same client, whose
  Family CSRF cookie is unchanged; an Admin page's token still signs out
  after a Family sign-in; each login rotates its own namespace's secret and
  the pre-login token is then refused; after a second Family sign-in in the
  same client the first tab's keepalive with its old token is refused with
  the plain 403, the first session is revoked, and the new session's
  activity is not renewed; a signed-out Family with a valid token still
  gets `session_ended`; a malformed cookie is replaced. Each namespace's
  `Set-Cookie` has its own path (`/` or `/admin/`), `HttpOnly`,
  `SameSite=Lax`, and `Secure` and the domain when those settings are set,
  with no `csrftoken` cookie; `CSRF_USE_SESSIONS` or a custom CSRF cookie
  name or path refuses to load (11 tests).
- PostgreSQL, related suites: every suite whose requests read the CSRF
  cookie, plus Family authentication, presence, response HTTP and
  submission, access gates, Google and Google recovery, session review
  rounds and runtime grants (540 tests).
- Browser: the Family response, Family census and component suites pass
  unchanged in Chromium, Firefox and WebKit (597 tests).

## Checkpoint

Implementation, focused validation and the three
[review rounds](stewardship-csrf-namespaces-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. No deployment, release, live-provider write or
database deletion is authorized by this increment.
