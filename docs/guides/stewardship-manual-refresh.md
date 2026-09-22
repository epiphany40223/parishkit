# Stewardship manual ParishSoft refresh

This guide records the first slice of
[ADM-08](../tasks/stewardship/admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs)
under the [v1 launch scope](../plans/stewardship/v1-launch.md): the coalesced
manual refresh controls the
[admin portal specification](../specs/stewardship/admin-portal/spec.md#manual-parishsoft-refresh)
requires, so staff can pull source changes during the campaign. It follows
the [pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

An Administrator requests an immediate full ParishSoft refresh from a
confirmation page that shows the latest successful refresh and whether a
refresh is running or waiting. The confirmation records one durable refresh
command bound to a key the page chose and takes the Administrator to the
run's own progress page. If a refresh is running, no second one starts
beside it and the requested one follows it; if one is already waiting, the
request is coalesced into that run; a repeated submission returns the same
run. The home page links to the confirmation beside the latest refresh.

The manual-census queue portion of .02, the log export of .04 and the tests
of .05 remain later work, as the launch scope orders it.

## Design

### The domain owner decides; the page only confirms

The refresh request primitive already existed for the nightly and fallback
producers: it records a command under the caller's key, coalesces a full
load behind any refresh waiting for the same source window, replays a known
key without a second root, and admits the caller under the global work lock.
This slice gives it its first browser caller and nothing else: the page
chooses a fresh key per render, the confirmation passes it with the manual
cause and the Administrator's identity, and the domain owner's rules decide
whether a new run is created. The page's own reading of running or waiting
work is wording for the Administrator, never the coalescing decision.

### Authority and cost

The page and the confirmation require the current session, the configure
capability and CSRF; the authorization callback the primitive runs under its
lock rechecks the same Administrator, so a revoked session cannot record a
command. A source that is not the configured organization is an outage to
the page, never a refresh of another organization. No ParishSoft call and no
lock wait happens in the web process; the progress page reports the phases
the task records. The web role gains insert on the refresh request and
command tables; the lease owner it needs to coalesce is already readable
through the existing column grant, which the grant registry records.

## Schema

No schema change.

## Focused validation

- Two PostgreSQL cases under the real web role with a promoted source: the
  confirmation page offering the request, one run created for the first
  key, the same key and a new key both leading to that run while it waits
  with one task and two commands recorded, the page saying so, and a
  further request waiting behind the run once it executes; a session
  revoked after admission and before the domain's own check refused on a
  new key and a replay alike with nothing recorded; and Staff, an
  anonymous caller, a malformed key and a stray field refused with nothing
  recorded.
- Two browser cases in every engine against the component page: both
  wordings of the confirmation passing the accessibility scan at phone and
  desktop widths and saying what a request does, and the form posting the
  page's key and CSRF token natively to the refresh route.
- The grant registry and build contract database-free suites pass.
- Ruff, formatting, Markdown lint and the migration drift check pass.

## Checkpoint

Implementation and focused validation are complete and
[round 1](stewardship-manual-refresh-reviews.md) is answered; the remaining
review/fix rounds, full exact-head CI, DCO and protected delivery remain
open. No deployment,
release, live-provider write or database deletion is authorized by this
increment.
