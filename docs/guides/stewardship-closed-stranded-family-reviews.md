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
  whose task root has no queued, running, retrying or abandoned run.
- The delivery-control inventory counts them as `stranded`, and both the SQL
  preview guard and the Python preview count them toward clearing the
  pause, so a clear with no types is admitted when they are all that is
  held.
- Every closed resolution cancels them as `campaign_closed`, exactly as the
  mail worker applies the close policy, and skips their occurrence when it
  is still pending or running (with the same transaction-local proof the
  resume's recovery uses); a failed occurrence keeps its truthful failure.
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
