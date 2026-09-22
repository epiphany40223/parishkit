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
