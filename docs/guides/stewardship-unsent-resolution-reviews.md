# Stewardship unsent resolution reviews

This ledger records the independent review/fix rounds of the
[unsent resolution correction](stewardship-unsent-resolution.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, because it changes mail-dispatch resolution and the schema,
with a correction check after any round that validates a finding. The Codex
reviewer has been out of quota since September 20, 2026; under the human's
exemption, extended through October 30, 2026, a completed Claude-only pass
counts as a round, and each round records which sources answered.

## Round 1

Claude only (Codex produced no output). Seven raw findings, two validated
(both Medium), both corrected:

- Medium: a report confirmed unsent ended as `permanent_failure`, but the
  daily and weekly completion proofs and the closed-pause digest coverage
  count only delivered messages and `recipient_revoked` cancellations. For
  the motivating case, a recipient who is no longer an Administrator (so no
  retry is admitted), the report occurrence stayed pending forever with no
  fulfillment, the weekly watermark never advanced, and a campaign closed
  while paused could not record its skip. The guard now freezes that
  recipient test, the one the digest dispatchers apply before cancelling,
  in the distinct reason `admin_unsent_recipient_revoked`, which the
  completion proofs and closed coverage count as settled; the metadata
  finalizer completes the occurrence. A still-admitted recipient's report
  keeps `admin_confirmed_unsent` and holds the cohort open for
  **Retry failed delivery**. The outbox has no `delivery_unknown` to
  `cancelled` edge, and a frozen reason keeps the proofs monotonic. New
  daily and weekly cases assert the occurrence and fulfillment for both
  recipients, and a closed-while-paused weekly case asserts the skip; the
  removed-recipient and closed cases fail without the change.
- Medium: the tests' `control(campaign, "resume")` writes a different
  ledger that never runs the SQL resume guard, so they did not prove that
  resume could proceed. A new Family case drives the real resume preview
  and confirmation with the sender check: refused while the invitation is
  unknown, then, after `confirm_unsent`, the recovery plan defers the failed
  occurrence and resume succeeds. The remaining `control` calls are
  commented as lifting only the pause flag, and the guide's validation list
  states what each case proves.

Of the five findings below the validation cutoff, three were taken: the
attempt history labels the Admin record by its reason rather than as a
provider refusal, the direct Web-insert refusals assert the trigger's
current-attempt error, and the guard's duplicated purpose test is folded
into one branch. A negative test of the occurrence edge predicate was not
taken: the Web role cannot evaluate it outside the definer guard (it lacks
the outbox event grant) and any other role fails its session check, so the
guard's own refusals remain its coverage. The last note, that the guide
claimed resumes the tests did not prove, is covered by the second Medium's
correction. A correction check follows.

## Round 2

Claude only (Codex produced no output); the correction check of round 1.
Six raw findings, two validated (both Medium), both corrected:

- Medium: the frozen `admin_unsent_recipient_revoked` reason covered only a
  removal before the evidence. A report confirmed unsent while its recipient
  was still an Administrator, who was removed afterwards, could never
  settle: no outbox edge leaves `permanent_failure` for `cancelled`, the
  retry gate requires an Administrator recipient, and the proofs did not
  count it. A provider permanent failure followed by removal had the same
  gap. The daily and weekly completion proofs and the closed-pause digest
  coverage now count any report in `permanent_failure` whose recipient is
  not currently an Administrator of the active configuration, the
  dispatchers' `recipient_revoked` test, as settled, whatever ended it. The
  frozen reason became redundant and was removed, leaving one rule; the
  guide documents that the live read can reopen a pending occurrence if the
  address becomes an Administrator again, while a completed one never
  changes. New cases cover confirmation then removal and a provider failure
  then removal.
- Medium: the metadata finalizer path was untested, because every case
  finished with the other Admin's delivery, whose worker settles the cohort
  itself. Each kind now runs the real finalizer producer and task whenever
  the last settlement has no worker: the other copy delivered first with
  `confirm_unsent` last, the two removal-after cases, and a removed only
  recipient, which completes with an empty fulfillment. Each asserts the
  occurrence, its fulfillment and, weekly, the watermark.

All four findings below the validation cutoff were taken: the runbook and
guide describe the closed-while-paused case of a still-admitted
recipient's failed report and the operator's path (removing a recipient who
should no longer be an Administrator settles it); the history label is one
template filter backed by a reason mapping, with unit tests; the daily
removed-recipient case asserts the service refuses the resend, like the
weekly one; and the guide's long lines are rewrapped. A further round
follows.

## Round 3

Claude only (Codex produced no output). Six raw findings, one validated
(Medium), corrected:

- Medium: the background-processing specification's Administrator digests
  paragraph, the authoritative completion rule, still said a cohort
  completes only when every recipient is accepted or withdrawn as
  `recipient_revoked`; the new settled outcome appeared only as an aside in
  the delivery-resolution paragraph. The rule now lives in the
  Administrator digests paragraph: a `permanent_failure` to a recipient who
  is not currently an Administrator settles that obligation, a cohort with
  no accepted recipient yields the empty disposition, a re-added
  Administrator reopens a still pending cohort while completed cohorts never
  reopen, and a failure to a current Administrator holds the cohort open.
  The aside is replaced by a link to that paragraph and to the unsent
  resolution guide.

All four findings below the validation cutoff were taken: a daily
closed-while-paused case joins the weekly one, and both kinds now have the
negative case (a still-Administrator recipient's failed report: the closed
proof is false, no skip is recorded, the occurrence stays pending); a
reopen test removes and re-adds the Administrator before completion and
asserts the occurrence stays pending, the retry is admitted again and the
finalizer allocated in the removed window refuses its claim, which the
guide documents as expected noise rather than changing claim admission;
the closed receipt case now runs the closed resolution, clearing the fully
resolved pause, after `confirm_unsent`; and the ragged runbook, Family-mail
resolution guide and specification paragraphs are reflowed. The Administrator
test is kept inline in each of the five SQL predicates rather than moved to a
shared helper: a plain SQL function called from a view reads with the
caller's privileges, and a role reading these views may lack the address-rule
grant, while a view reads its tables with its owner's rights. A correction
check follows.

## Round 4

Claude only (Codex produced no output). Correction check: nine raw findings,
one validated and corrected:

- Medium: the documented recovery on a campaign closed while paused only
  works before the closed held-message resolution runs. That resolution
  records an occurrence's skip only for the messages its own command
  cancels, so a removal afterwards can no longer produce one and the report
  stays pending. The guide and the runbook now give the ordering.

The eight findings the validation step did not confirm were not carried
forward. A further correction check follows.
