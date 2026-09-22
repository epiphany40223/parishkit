# Stewardship unsent resolution

This guide records a launch-blocking correction found by the pre-launch gate
review: a message in `delivery_unknown` that the provider showed was not sent
had no truthful resolution once its resend was no longer admitted, so a
paused campaign could never resume. It is part of the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
and follows the
[pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## The defect

Resume refuses while any production delivery is unknown. An unknown delivery
had two resolutions: confirm delivery with external evidence, or authorize a
potentially duplicate resend. The resend is admitted only while every live
condition still holds (see the
[paused resend guide](stewardship-paused-resend.md#the-correction)); for
example, the Family has not submitted and is still active, eligible and
reachable, or a report's recipient is still an Administrator.

When the provider's own record showed the attempt was not sent but one of
those conditions had since failed, neither resolution was truthful: the
runbook forbids confirming delivery that did not happen, and the resend was
refused. The unknown count could never reach zero, so resume was refused
forever and every held campaign message stayed held. The same unresolved row
also blocked that Family's submission receipt.

## The correction

A new Admin resolution action, `confirm_unsent`, records with a required
evidence note that the provider confirms the attempt was not sent, and does
not resend. The delivery page offers it next to the acceptance for every
unknown message, as **Record that the provider did not send it (no resend)**.

- **Admission.** It is admitted only for `delivery_unknown`, for invitations,
  reminders, receipts and daily and weekly reports, under the same authority
  as the acceptance: a fresh exact Admin, export admission, a campaign that
  is not archived, a drained failed latest execution and the current
  unresolved attempt. Like the acceptance, it never consults the resend
  admission, so it is available during a pause, after the Family submitted
  or lost eligibility, after an Admin was removed, and on a campaign closed
  while paused. It prepares nothing, allocates no task and needs no
  duplicate acknowledgement.
- **Effect.** It reuses the terminal outcome the dispatcher records when the
  provider definitively refuses an attempt: the outbox edge
  `delivery_unknown` to `permanent_failure` through the existing
  `fail_unaccepted` action, and, for an invitation or reminder, the
  occurrence becomes `failed` with reason `recovery_fail`, as the
  [background-processing state table](../specs/stewardship/background-processing/spec.md)
  allows only after definitive non-acceptance. The event carries the Admin
  as actor, the evidence note and digest, and the distinct reason
  `admin_confirmed_unsent`. No schedule fulfillment is inserted, nothing
  counts as provider success, and no recipient is suppressed, because this
  is not a recipient refusal. Sealed substitutions and any pause binding are
  cleared as the acceptance clears them. The audit event is
  `delivery_resolution_confirm_unsent`.
- **Admin reports.** A report's occurrence completes only when every
  recipient's message is delivered or was cancelled because its recipient
  stopped being an Administrator (`recipient_revoked`). A report confirmed
  unsent to a recipient who is no longer an Administrator can never be
  retried, so the guard freezes that fact, using the same test the digest
  dispatchers apply before cancelling a revoked recipient, in the distinct
  reason `admin_unsent_recipient_revoked`. The daily and weekly completion
  proofs and the closed-pause digest coverage count that reason as settled,
  exactly like the `recipient_revoked` cancellation, so the report completes
  (and the weekly watermark advances) once the rest of its cohort is
  delivered, or a campaign closed while paused can record its skip. With no
  live worker behind the settlement, the existing metadata finalizer
  completes the occurrence, as it does after an Admin acceptance. A report
  whose recipient is still an Administrator keeps
  `admin_confirmed_unsent`: an ordinary failure that holds its cohort open
  until **Retry failed delivery** sends it.
- **Consequences.** The message leaves the delivery-control inventory's
  unknown count, so resume (or the closed-pause resolution) can proceed, and
  a receipt waiting behind it is no longer blocked. Schedule reconciliation
  and recovery treat it exactly as a provider permanent failure; for an
  invitation or reminder, the resume plan defers the failed occurrence as
  ordinary Family recovery. A later **Retry failed delivery** is an ordinary
  retry of a failed message, still governed by the resend admission. The
  attempt history labels the Admin record by its reason, not as a provider
  refusal.

Reusing `fail_unaccepted` rather than adding an outbox action keeps every
consumer of the outbox vocabulary, which already handles a permanent failure
from `delivery_unknown`, unchanged. The Admin origin is distinguishable by
reason, actor and the resolution journal row. The outbox has no edge from
`delivery_unknown` to `cancelled`, so a revoked recipient's report is not
relabelled as the dispatcher's cancellation; freezing the revocation in the
reason at the time of the evidence, rather than rereading the current roster
in the completion proofs, keeps those proofs monotonic if the address later
becomes an Administrator again.

## Schema

- The `delivery_resolution_action` check on
  `stewardship_delivery_resolution` accepts `confirm_unsent` (also in the
  initial Django migration's model state).
- `stewardship_delivery_resolution_guard_v1` handles the new action beside
  `accept` and chooses the report reason.
- `stewardship_daily_digest_completion_ready`,
  `stewardship_weekly_digest_completion_ready` and
  `stewardship_delivery_closed_digest_v1` count
  `admin_unsent_recipient_revoked` as settled.
- `stewardship_delivery_resolution_edge_v1` admits the occurrence edge from
  `delivery_unknown` to `failed` only when the same command recorded the
  `fail_unaccepted` event with reason `admin_confirmed_unsent`.

No table, index, trigger, grant or policy changes, and no stored data
changes. The fresh-install baseline's constraint, function and relation
(view) fingerprints are updated under the pre-production policy. The change lands before the
[schema freeze](../plans/stewardship/v1-launch.md#production-readiness-activation-and-schema-freeze);
a validation deployment installed from an earlier baseline lacks it, and, as
the launch scope requires, the human decides whether to reinstall that
deployment or add a forward migration.

## Focused validation

- PostgreSQL, Family mail, real resume: with an invitation unknown on a
  paused campaign and a current sender check, the Admin resume preview is
  refused; after `confirm_unsent` the occurrence is `failed` with no
  fulfillment, the Family recovery plan shows it deferred and nothing
  blocked, and the real resume preview and confirmation succeed.
- PostgreSQL, Family mail, refused resend: on a paused production campaign,
  an unknown invitation whose Family was refreshed to no deliverable address
  is refused a resend (the SQL admission is false); `confirm_unsent` leaves
  the message `permanent_failure` with the Admin evidence and reason, the
  occurrence `failed`, no fulfillment, no refusal and no new task; the
  unknown count goes from one to zero; a replay of the same command is
  idempotent; and, with only the pause flag lifted, a retry of the failure
  is still refused because the Family is ineligible.
- PostgreSQL, receipt: with the real Admin delivery controls, resume is
  refused while an unknown receipt remains; after `confirm_unsent` the
  resume preview and confirmation succeed. The same settlement works on a
  campaign closed while paused.
- PostgreSQL, daily and weekly reports with two Admins: confirmed unsent
  while paused, the unknown count goes from one to zero. When the recipient
  was removed first, the resend is refused and the other Admin's later
  delivery completes the occurrence with a delivered fulfillment (and
  advances the weekly watermark). When the recipient is still an
  Administrator, the occurrence stays pending with no fulfillment and a
  retry of the failure is admitted. Without the completion change, the
  removed-recipient cases stay pending.
- PostgreSQL, closed while paused: a weekly report confirmed unsent to a
  removed Admin no longer blocks the closed resolution, which cancels the
  other Admin's held report and records the occurrence's skip. Without the
  coverage change, no skip is recorded.
- Refusals: a failed, delivered or never-submitted message is refused by the
  service and, inserted directly under the Web role, by the trigger's
  current-attempt check; a blank note is refused by the service and by the
  trigger; a duplicate acknowledgement is refused; Web still cannot update
  the outbox.
- Delivery page: the action is offered beside the acceptance for an unknown
  delivery, also while paused, and not for a failed one; the actual form
  applies once with CSRF, its replay redirects, a stale command conflicts,
  and the history shows the Admin record, not a provider refusal.
- The resolution, closed resolution, delivery control, delivery view,
  receipt dispatch, daily and weekly resolution, digest completion and
  finalization, and schema baseline suites pass.

## Checkpoint

Implementation, focused validation and round 1 of the
[review rounds](stewardship-unsent-resolution-reviews.md) are complete; the
remaining rounds, exact-head CI, DCO and protected delivery remain open. No deployment, release, live-provider
write or database deletion is authorized by this increment.
