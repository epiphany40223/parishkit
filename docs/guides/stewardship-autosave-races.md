# Stewardship user-rule race and exact-once tests

This guide records the tenth and closing slice of
[ADM-07](../tasks/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
the tests of ADM-07.05 that the
[work package](../plans/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments)
requires, over the behavior the
[login-rule autosave queue](stewardship-rule-autosave.md) and the earlier
increments delivered. It changes no behavior and follows the
[pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## Scope

The work package asks for tests of hosted-domain behavior, precedence,
concurrent digest changes, activation-time revocation, Administrator-grant
notification failure, retry and acknowledgement, Ministry row-scope updates,
rapid edits across rows and tables, repeated toggles of an in-flight
checkbox, slow installers, lost acceptance and activation responses, failed
requests, other tabs and Administrators, removed targets, session expiry and
revocation, page teardown, and exact-once request, audit and notification
behavior. This slice adds the cases the delivered suites did not yet hold
and cites where the rest already live, so ADM-07's tasks can be checked.

## Coverage

### Already held by delivered suites

- Hosted-domain behavior and address-over-domain precedence: the
  [portal users review](stewardship-portal-users.md) and
  [login rule edit](stewardship-user-rule-edits.md) suites.
- Activation-time revocation of the confirming Administrator: the login rule
  edit suite's `actor_unauthorized` cases.
- Administrator-grant notification, its failure, retry and acknowledgement,
  and exactly one mail per security event however often the fanout runs:
  the [security event](stewardship-policy-security-events.md) and
  [security event mail](stewardship-security-event-mail.md) suites, the
  latter binding each event's cohort once.
- Ministry row-scope updates through the suspension overlay and the manual
  assignments: the [Chairperson review](stewardship-chair-review.md) and
  [assignment editor](stewardship-assignment-editor.md) suites.
- Slow installers, lost acceptance responses, failed requests, another
  Administrator's activation found at intake or activation, a removed target
  refused at the route, lost access, page teardown and repeated toggles of
  an in-flight checkbox in one row: the
  [autosave queue](stewardship-rule-autosave.md) suites.

### Added here

- PostgreSQL, under the real web and restricted installer roles: an
  autosaved Administrator grant recorded once for its key however often it
  is resent, its intake audit written once and no audit row added by a
  resubmission, activated with its audit rows and exactly one security
  event written once, and no further checkpoint, audit row or event on a
  later resubmission; two Administrators' intents against one
  base, the second failing at activation with `stale_base`, reported so by
  its status route, and its retry as a new key against the new digest
  applied; and a session revoked, or expired, mid-queue denying the apply
  and status routes with nothing recorded.
- Browser, in every engine against the component page with the routes
  mocked: rapid edits across two address rows and the domain table applied
  one at a time in tick order, each against the digest the previous one
  applied; a target another Administrator deleted listed as such in the
  conflict view, not retried, and its controls disabled; and a stale digest
  on a later intent leaving the applied one alone.

## Schema

No schema change.

## Checkpoint

The tests are complete and pass locally and the
[review rounds](stewardship-autosave-races-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. With them ADM-07.01 to .05 are
checked in the task map. M5 and Gate 3 remain open. No deployment, release,
live-provider write or database deletion is authorized by this increment.

## Protected delivery

PR #89 delivered candidate `7f6232c9`, two logical commits plus the PR #88
receipt, whose tree `a1c5f47f` is identical to the retained commit-by-commit
review history on `pr/stewardship-autosave-races-reviewed` (`21f5ca5b`) and
to the landed tree. The six
[review/fix rounds](stewardship-autosave-races-reviews.md) were
single-source under the exemption, with every accepted finding fixed and the
sixth, a correction check, validating nothing. The pull request was marked
ready before the candidate was pushed, and the candidate was pushed once the
ready-for-review run for the previous head was in progress, so that run was
cancelled by the candidate's own. Exact-head ready-candidate CI
`35671418869` and DCO passed all 25 checks, from 00:20:03 to 00:37:34 UTC on
September 22, 2026 (17 minutes 31 seconds). `origin/main` had no intervening
commits since the candidate's base `44f53cb3`. Protected auto-merge landed as
`189c4d0d` at 00:37:41 UTC and was verified on freshly fetched `origin/main`,
whose second parent's tree is the candidate's, before the next increment
started. This used the standing delivery authority, without deployment or
release, and supersedes the checkpoint above. The cancelled and
retained-history runs are not counted as acceptance.

The user-rule race and exact-once tests increment is delivered, and with it
ADM-07 is complete: its five tasks are checked in the task map. M5 and the
pre-launch gate remain open.
