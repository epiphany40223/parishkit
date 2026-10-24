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

Unknown, malformed, wrong-mode, inactive and ineligible codes still render
byte-identical pages, as do all rejected links. Since #1793, once the
campaign has ended every rejected code or link instead renders the
campaign-ended page that `/` shows, still byte-identical across credentials
(see
[after the campaign ends](../specs/stewardship/parishioner-portal/spec.md#after-the-campaign-ends)). Status
codes, `Retry-After`, `Cache-Control: no-store` and limiter accounting are
unchanged, and so is the optional access-denied help, except that after
close the ended page's "After the campaign" content takes its place.

Since #2116, Admin sign-in refusals have their own kinds (`no_access`,
`unfinished`, and `rate_limited` for every Admin `429`) instead of the
default and temporary texts above; Family denials are unchanged. The
[login and denial behavior](../specs/stewardship/admin-portal/spec.md#login-and-denial-behavior)
specification has their text and rationale.

## Focused validation

- PostgreSQL, after close (#1793): in Testing and after a Production close,
  valid, unknown and malformed links and codes return 403 with one
  byte-identical ended page equal to `/`, no cookies and no session; a 429
  and the maintenance 503 still win; before start and while being prepared
  stay generic; a failed campaign-state read falls back to the generic
  denial.
- Browser, after close (#1793): the ended page is accessible at phone and
  desktop widths and shows no denial text.
- PostgreSQL, Family authentication: unknown, malformed, Production-in-Testing
  and inactive codes each return 403 with one identical code
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

Implementation, focused validation and the three
[review rounds](stewardship-family-denials-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.

## Protected delivery

PR #101 delivered candidate `b1a174db`, two logical commits plus the receipt
of PR #99, whose tree `41aafe6c` is the retained commit-by-commit review
history on `pr/stewardship-family-denials-reviewed` (`f8393986`) plus exactly
that receipt, and is the landed tree. The three
[review rounds](stewardship-family-denials-reviews.md) were single-source
under the exemption and validated nothing; their low-severity notes were
taken where cheap. The pull request was marked ready before the candidate
was pushed. Exact-head ready-candidate CI `35759619635` and DCO passed all
25 checks, from 17:16:39 to 17:36:32 UTC on September 22, 2026 (19 minutes
53 seconds). Earlier runs on superseded draft heads stopped at the
draft-mode gates, and one was cancelled by the candidate push; none is
counted as acceptance. `origin/main` had no intervening commits since the
candidate's base `ed46c7ae`. Protected auto-merge landed as `aabcff6e` at
17:36:36 UTC and was verified on freshly fetched `origin/main`, whose second
parent's tree is the candidate's, before the next increment was committed.
This used the standing delivery authority, without deployment or release; no
real provider was contacted.
