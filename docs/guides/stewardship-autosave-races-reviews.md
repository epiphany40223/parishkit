# Stewardship user-rule race and exact-once tests reviews

This ledger records the independent review/fix rounds of the
[user-rule race and exact-once tests increment](stewardship-autosave-races.md),
under the delivery cycle's minimum of three rounds per PR. The Codex
reviewer has been out of quota since September 20, 2026; under the human's
second single-source exemption, recorded in
[the overall plan](../plans/stewardship/overall.md), a completed Claude-only
pass counts as a round through September 25, 2026, and each round records
which sources answered.

## Round 1

Claude only (Codex out of quota). Eleven raw findings, two validated, both
corrected:

- Medium: the exact-once case counted requests by primary key, which can
  only ever be zero or one and so could not detect a second request under
  another id. It now compares the request count before and after and counts
  the requests for the key.
- Medium: the rapid-edits case claimed each intent was formed against the
  digest the previous one applied, but the mock answered every applied
  receipt with one constant. The mock now derives each request's applied
  digest from its id, and the case asserts the per-request chain; the other
  cases assert the specific digest they adopt.

The nine findings the validation step did not confirm were not carried
forward.

## Round 2

Claude only (Codex out of quota). Ten raw findings, one validated and
corrected:

- Medium: the exact-once case proved the request, checkpoints and security
  event but said nothing of the audit rows or the notification, which the
  work package names too. It now asserts that intake's audit is written
  once and resubmissions add no row, that activation's audit rows are
  written once and later resubmissions add none, and the guide cites the
  security event mail suite for exactly one mail per event.

The nine findings the validation step did not confirm were not carried
forward.
