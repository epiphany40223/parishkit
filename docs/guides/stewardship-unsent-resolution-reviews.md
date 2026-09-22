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
