# Stewardship Family denials

This guide records a correction found by the
[pre-launch gate](../plans/stewardship/v1-launch.md#v1-process-changes)
review: every Family sign-in failure showed the same outage-like message. It
is part of the [v1 launch scope](../plans/stewardship/v1-launch.md) and needs
no schema change.

## The defect

Family sign-in failures rendered one page, "Access unavailable. Sign-in is
unavailable. Please try again.", for a mistyped or otherwise rejected code
(403), a rate-limited source (429), a limiter, Valkey or configuration outage
(503) and a rejected secure link (403). A Family that mistyped its code on
launch day was told sign-in was unavailable, which reads as an outage.

The [parishioner portal specification](../specs/stewardship/parishioner-portal/spec.md#availability-and-entry)
requires a fixed "This Family code cannot be found or used" result with a
retry link for every rejected code, and a fixed "This secure Family link
cannot be found or used" page with a link to manual code entry for every
rejected link. The
[architecture specification](../specs/stewardship/architecture/spec.md#identity-and-session-security)
gives rate limiting and limiter outages a separate generic
temporary-unavailability response.

## The correction

`login_denial()` takes a closed denial kind that selects only the message
category, never a reason within it. The kinds and their callers:

| Kind | Text | Callers |
| --- | --- | --- |
| `code` | This Family code cannot be found or used. | Manual code entry, 403 |
| `link` | This secure Family link cannot be found or used. | `/access/<token>`, 403 |
| `unavailable` | Sign-in is temporarily unavailable. Please try again later. | Any 429 or 503, Family or Admin |
| default | Sign-in is unavailable. Please try again. | Other Admin refusals; Family fetch 400/403 |

- Family `denied()` maps any 429 or 503 to `unavailable`. That covers the
  manual entry limiter checks, including the failure that crosses the limit,
  and the outage handlers of code entry, link exchange, the portal shell,
  keepalive and presence.
- The `/access/` rate-limit and outage gate in the authentication middleware,
  and the access-gate outage path on Family routes, use `unavailable`.
- Every Admin refusal with a 429 or 503 status uses `unavailable` too, since
  the architecture specification gives Admin OAuth and Family code entry the
  same generic temporary-unavailability response. That reaches the sign-in
  routes, the access gate and signed-in pages such as the report views,
  which call the same helper for restore review, database and limiter
  outages (their old text also read "Sign-in is unavailable"). Other Admin
  refusals keep the original generic text, and every Admin page keeps the
  Admin retry route.
- The keepalive and presence endpoints are called by page scripts that never
  display the response body, so their 400 and ended-session 403 keep the
  default text.

Unknown, malformed, wrong-mode, inactive, ineligible and closed-campaign
codes still render byte-identical pages, as do all rejected links. Status
codes, `Retry-After`, `Cache-Control: no-store`, limiter accounting and the
optional access-denied help are unchanged.

## Focused validation

- PostgreSQL, Family authentication: unknown, malformed, Production-in-Testing,
  inactive and closed-campaign codes each return 403 with one identical code
  page and no session; an unknown link and a link invalidated with its
  rehearsal return 403 with one identical link page; the pair-limit 429, a
  repeat 429 and a limiter outage 503 render one identical
  temporary-unavailability page with no private diagnostic.
- PostgreSQL, access gates: an invalid setup marker gives `/admin/`, `/`,
  `/family/` and `/access/` the temporary-unavailability text, with
  `Retry-After: 5` on the Family routes.
- PostgreSQL, public help: configured access-denied help stays identity-free
  on both the code and link pages, and rejected codes stay byte-identical.
- Pure: each kind renders only its own text with `no-store` and a fixed retry
  link, Admin's default text and route are unchanged, Admin 429 and 503
  refusals render the temporary text while a 403 keeps the default, and an
  unknown kind is rejected.
- Browser: the three new denial pages pass the accessibility and responsive
  component checks in Chromium, Firefox and WebKit.

## Checkpoint

Implementation and focused validation are complete. The three
[review rounds](stewardship-family-denials-reviews.md), CI and protected
delivery remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
