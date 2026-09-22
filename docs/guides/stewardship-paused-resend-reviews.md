# Stewardship paused resend reviews

This ledger records the independent review/fix rounds of the
[paused resend correction](stewardship-paused-resend.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, because it changes mail-dispatch admission and the schema, with
a correction check after any round that validates a finding. The Codex
reviewer has been out of quota since September 20, 2026; under the human's
exemption, extended through October 30, 2026, a completed Claude-only pass
counts as a round, and each round records which sources answered.

## Round 1

Claude only (Codex produced no output). Eight raw findings, two validated,
both corrected:

- Medium: the receipt and digest tests covered only the admitted resend, so
  a regression that granted the pause exception unconditionally, or keyed it
  on the action rather than the unknown state, would have passed. The
  receipt, daily and (new) weekly tests are parametrized over the admitted
  resend and a still-refused retry of a failed message, and assert the
  resume guard's unknown count before and after the resend.
- Medium: the runbook said a resent message goes only when delivery
  resumes, which is wrong for a campaign closed while paused (no resume; the
  closed held-message resolution releases or cancels it) and for a message
  that resolution already released (no new hold; sent at once). The runbook
  and the guide now state both cases.

Of the six findings below the validation cutoff, three were taken as
drive-by corrections in the same fix: the Family branch of the admission now
uses the same `resolving.unknown` value as the receipt and digest gates, the
guide no longer says that only the admission passes `paused_ok`, and the
weekly digest has its own test. A further correction check follows.

## Round 2

Claude only (Codex produced no output; a first attempt ended on a provider
rate limit and was relaunched). Eight raw findings, three validated, all
corrected:

- Medium: the Admin portal specification still said a resent message is
  always sent only after resume, the claim round 1 corrected in the runbook
  and guide. It now names the closed-while-paused resolution and the
  already-released case.
- Medium: the receipt and digest refusal cases were refused by Web
  preparation first, so the authoritative SQL admission was never exercised
  for those kinds. Each paused receipt, daily and weekly case now also
  asserts `stewardship_delivery_retry_admitted_v1` directly.
- Medium: the closed-while-paused and already-released resend paths were
  documented but untested. A new case resends an unknown receipt on a
  campaign closed while paused (held, refused, then sent once receipts are
  released) and, after that attempt ends unknown, resends the released
  receipt, which carries no new hold and is sent at once.

Of the five findings below the validation cutoff, four were taken: a
comment at the guard's render call records that admission must run while the
message is still unknown, the runbook links the paused resend guide for
every kind, edited paragraphs are rewrapped, and the weekly test's import
moved to module level. The delivery-page parametrization over receipts and
digests was not taken, because the new SQL assertions cover the admission
it reads. A further round follows.
