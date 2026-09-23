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
  worker applies the close policy, under a command ID derived from the
  resolution and the message. Unlike the worker, which skips only a pending
  occurrence, it also skips one left running by an ended attempt (with the
  same transaction-local proof the resume's recovery uses); a failed
  occurrence keeps its truthful failure.
- The delivery page shows the count and says any resolution cancels them.

This changes the fresh-install schema (one view, a changed inventory view
and a changed resolution function); the committed
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
  is never stranded and still blocks the clear; the existing in-flight
  uncertainty test exercises the shared definition.

The seven findings below the validation cutoff were taken: the cancellation
rechecks the target row's state, hold and version; its command ID is
derived from the resolution and the message; the comment and documents
state that a running occurrence is also skipped, unlike the worker; the
test fails the task while paused, before the close, shows nothing is
stranded until the close, and covers a digest cancel as well as an empty
clear; the page and documents say "no remaining delivery task" rather than
"preparation failed"; and the runbook links this ledger. A correction
check follows.
