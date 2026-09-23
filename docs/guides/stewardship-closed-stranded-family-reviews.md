# Stewardship closed-campaign stranded Family mail reviews

This ledger records the independent review/fix rounds of the correction of
the pre-launch gate's one uncorrected round 3 finding:

- PL-I2 (Medium): if a campaign was paused while an invitation or reminder
  whose preparation had failed waited for **Retry unsent**, and the
  campaign then closed while still paused, nothing could resolve that
  message. No worker claims its ended task to apply the close policy, an
  unsent retry is refused after close, and the closed resolution accepted
  only receipts and reports, so the durable pause could never be cleared.

The correction, recorded in the
[Admin portal specification](../specs/stewardship/admin-portal/spec.md#live-delivery-pause)
and the [launch runbooks](stewardship-launch-runbooks.md#pausing-and-resuming-delivery):

- A new owner-only view, `stewardship_delivery_stranded`, names exactly the
  held production invitations and reminders on a closed paused campaign
  whose task root has no queued, running, retrying or abandoned run and
  whose provider outcome is not uncertain. Uncertainty is one owner-only
  definition, `stewardship_delivery_uncertain`, shared with the inventory's
  unknown count.
- The delivery-control inventory counts them as `stranded`, and both the SQL
  preview guard and the Python preview count them toward clearing the
  pause, so a clear with no types is admitted when they are all that is
  held.
- Every closed resolution cancels them as `campaign_closed`, as the mail
  worker applies the close policy, under a command ID derived (by
  SHA-256) from the resolution and the message. Unlike the worker, which
  skips only a pending occurrence, it also skips one left running by an
  ended attempt (with the same transaction-local proof the resume's
  recovery uses); a failed occurrence keeps its truthful failure.
- The delivery page shows the count and says any resolution cancels them.

This changes the fresh-install schema (two new views, a changed inventory
view and a changed resolution function); the committed
[schema baseline](../../tests/stewardship/database/schema-baseline.json) is
updated, and the human reinstalls the validation deployment, which had not
started. It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds for mail dispatch and schema, with a correction check after
any round that validates a finding, each recorded by which sources
answered.

## Round 1

Claude only (Codex was out of credits). Eight raw findings, one validated
and corrected:

- Medium: the first stranded definition admitted a held message whose
  latest decisive event is an idempotent retry, which the inventory counts
  as unknown and the outbox guard forbids cancelling, so one such row would
  have made every closed resolution fail. Uncertainty is now one owner-only
  view shared by the inventory and the stranded view, so an uncertain row
  is never stranded and still blocks the clear (the round 2 regression test
  covers it).

The seven findings below the validation cutoff were taken: the cancellation
rechecks the target row's state, hold and version; its command ID is
derived from the resolution and the message; the comment and documents
state that a running occurrence is also skipped, unlike the worker; the
test fails the task while paused, before the close, shows nothing is
stranded until the close, and covers a digest cancel as well as an empty
clear; the page and documents say "no remaining delivery task" rather than
"preparation failed"; and the runbook links this ledger. A correction
check follows.

## Round 2

Claude only (Codex was out of credits). Four raw findings, one validated
and corrected:

- Medium: nothing tested the round 1 correction, because the in-flight
  uncertainty test holds only reports and the stranded view covers only
  invitations and reminders. A new regression test drives a held
  invitation to an uncertain idempotent retry with its task ended on a
  closed paused campaign: it counts as unknown, not stranded, clearing is
  refused, and a report cancellation succeeds without touching it or
  clearing the pause. Removing the exclusion makes the test fail.

The three findings below the validation cutoff were taken: the ledger no
longer overstates the in-flight test; the browser layout fixture carries a
stranded count, so both new page paragraphs render in the layout and
accessibility checks; and the derived command ID uses SHA-256, like every
other schema digest, since PostgreSQL's `md5()` fails on a FIPS host. A
correction check follows.

## Round 3

Claude only (Codex was out of credits). Correction check: one raw finding,
none validated; the review rounds are closed. The Low (a paragraph left
unwrapped after the round 2 edit) was taken, and the schema summary above
now names both new views.

## Protected delivery

PR #109 delivered candidate `50e2431c`, two logical commits plus the
receipt of PR #108, whose content is the retained review history on
`pr/stewardship-closed-held-family-reviewed` (`e187f030`), squashed with
identical content, plus exactly that receipt; the closed-resolution and
schema-baseline suites passed on that tree, and the candidate tree
`f71e7292` is the landed tree. The three rounds above were single-source
under the exemption; rounds 1 and 2 each validated one Medium, both
corrected, and round 3 validated none. The pull request was marked ready
before the candidate was pushed. Exact-head ready-candidate CI
`35810068360` and DCO passed all 25 checks, from 02:22:09 to 02:42:32 UTC
on September 23, 2026 (20 minutes 23 seconds). Earlier runs on superseded
draft heads are not counted as acceptance. `origin/main` had no
intervening commits since the candidate's base `1fce35dc`. Protected
auto-merge landed as `f79376f7` at 02:42:38 UTC and was verified on freshly
fetched `origin/main`, whose second parent's tree is the candidate's,
before the next increment was committed. This used the standing delivery
authority, without deployment or release; no real provider was contacted.
