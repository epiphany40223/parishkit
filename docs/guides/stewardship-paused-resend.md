# Stewardship paused resend

This guide records a launch-blocking correction found while writing the
[launch runbooks](stewardship-launch-runbooks.md): a paused campaign could not
resolve a message in `delivery_unknown` that the provider showed was not
sent. It is part of the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
and follows the
[pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).

## The defect

Resume refuses while any delivery is still submitting or unknown, so the
operator must resolve every unknown delivery first. An unknown delivery has
two resolutions: confirm it with external evidence, or authorize a
potentially duplicate resend. The resend admission refused during a pause, for
every live message kind: invitations and reminders, submission receipts and
daily and weekly Admin reports. A message the provider showed was not sent
could therefore not be resolved truthfully, and the pause could not be
resumed without recording a delivery that did not happen.

The [Admin portal specification](../specs/stewardship/admin-portal/spec.md#live-delivery-pause)
already required `delivery_unknown` messages to keep their reconciliation
workflow during a pause; the implementation did not.

## The correction

A pause now admits the resend of an unknown delivery, and only that retry;
retries of failed or unsent mail still wait for resume.

- The resolution admission, `stewardship_delivery_retry_admitted_v1`, treats
  the pause as satisfied when the message is in `delivery_unknown`. Every
  other condition is unchanged: campaign dates, Family eligibility and
  submission, slot fulfillment, activation catch-up and restore holds for
  Family mail; the unresolved-sibling check for receipts; report completion
  and a current Administrator recipient for digests.
- The receipt, daily and weekly send gates gain a `paused_ok` argument that
  defaults to false. Only resolution admission and the Web resolution
  preparation pass it; every worker and dispatch call uses the one-argument
  form, so no provider attempt crosses a pause.
- The Web preparation checks apply the same rule: the Family-mail preparation
  check, the receipt disposition (which gains a matching `paused_ok` keyword)
  and the digest check.

The resend returns the message to `pending`. The existing pause-hold trigger
attaches the current pause hold as it does for any message entering
`pending` while paused; the send gate refuses it; resume releases it with the
rest of the held mail, under the same overdue coalescing plan. The unknown
count falls when the resend is authorized, so the resume can proceed.

A campaign closed while paused cannot resume. There a resent receipt or
report is held at the current pause version like any other and is released
or cancelled by the closed held-message resolution; one that resolution
already released carries no new hold and is sent at once, as a released
message always is.

The delivery detail page therefore offers **Authorize potentially duplicate
resend** during a pause whenever it would be offered otherwise, and still
hides the failed and unsent retries. The launch runbook's temporary warning is
removed, and the
[Family-mail resolution guide](stewardship-family-mail-resolution.md) and the
Admin portal specification state the rule.

## Schema

The three send-gate functions change signature from `(message uuid)` to
`(message uuid, paused_ok boolean DEFAULT false)`, and the resolution
admission's body changes. No table, index, trigger, grant or policy changes.
The fresh-install baseline fingerprint is updated under the pre-production
policy.

The change is unavoidable (the admission is enforced in SQL) and lands before
the [schema freeze](../plans/stewardship/v1-launch.md#production-readiness-activation-and-schema-freeze).
It changes no stored data, but a validation deployment installed from an
earlier baseline does not have it. As the launch scope requires, the human
decides whether to reinstall that deployment, which the backup release
already requires, or to add a forward migration.

## Focused validation

- PostgreSQL, Family mail: with the campaign paused, confirming an unknown
  delivery still completes it; the unknown count the resume guard refuses
  over is one before the resend and zero after it; the resend leaves the
  message pending under the pause hold, is refused by the send gate, and is
  sent after resume; a retry of a failed or unsent message is still refused
  with no resolution or task recorded.
- PostgreSQL, receipt, daily report and weekly report: the same unknown count
  before and after; resent while paused, the message is held, refused by the
  send gate and sent after resume; a retry of a failed one is still refused
  with no resolution or task recorded.
- PostgreSQL, closed while paused: an unknown receipt resent there is
  admitted by SQL, held at the current pause and refused by the send gate,
  then sent once the closed resolution releases receipts while a held weekly
  report keeps the pause; after that attempt ends unknown again, the resend
  of the released receipt carries no new hold and is sent at once.
- Every paused receipt, daily and weekly case also asserts the SQL admission
  directly, since Web preparation refuses first and would hide it.
- PostgreSQL, delivery page: a paused campaign offers the resend and the
  acceptance for an unknown delivery and hides the retry of a failed one.
- The existing resolution, weekly, delivery view, delivery control, closed
  resolution and schema baseline suites pass; the schema audit shows exactly
  the three changed function signatures.

## Checkpoint

Implementation, focused validation and the three
[review rounds](stewardship-paused-resend-reviews.md) are complete; full
exact-head CI, DCO and protected delivery remain open. No deployment, release, live-provider
write or database deletion is authorized by this increment.

## Protected delivery

PR #97 delivered candidate `9de8c9e2`, two logical commits plus the PR #96
receipt, whose tree `a0d24844` is identical to the retained commit-by-commit
review history on `pr/stewardship-paused-resend-reviewed` (`1f22354b`) and to
the landed tree. The three [review rounds](stewardship-paused-resend-reviews.md)
were single-source under the exemption; rounds 1 and 2 validated five
findings, all corrected, and round 3 validated nothing. The pull request was
marked ready before the candidate was pushed. Exact-head ready-candidate CI
`35742384020` and DCO passed all 25 checks, from 14:44:10 to 15:04:46 UTC on
September 22, 2026 (20 minutes 36 seconds). Earlier runs on superseded draft
heads stopped at the draft-mode gates, and one was cancelled by the candidate
push; none is counted as acceptance. `origin/main` had no intervening commits
since the candidate's base `aa29a162`. Protected auto-merge landed as
`4b36435d` at 15:05:26 UTC and was verified on freshly fetched `origin/main`,
whose second parent's tree is the candidate's, before the next increment was
committed. This used the standing delivery authority, without deployment or
release; no real provider was contacted. The schema change's
reinstall-or-migrate decision for the validation deployment remains the
human's.

The paused resend correction is delivered. The launch scope continues with
the pre-launch gate.
