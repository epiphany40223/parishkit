# Stewardship smoke tools reviews

This ledger records the independent review/fix rounds of the
[smoke tools increment](stewardship-smoke-tools.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, since the increment handles provider credentials. The Codex
reviewer has been out of quota since September 20, 2026; under the human's
exemption, extended through October 30, 2026, a completed Claude-only pass
counts as a round, and each round records which sources answered.

## Round 1

Claude only (Codex produced no output). Eleven raw findings, two validated,
both corrected:

- Medium: the installer's three-way classification lives in its helper's
  exception ladder, not in the check functions, so the smoke command would
  have exited with the generic line for a rejected ParishSoft key, a
  malformed credential file or an outage instead of printing `invalid` or
  `unavailable`. The ladder is now one shared function that both the
  installer's helper and the smoke command call, so the words cannot drift.
- Medium: the fakes signalled outcomes only by return value, which is not
  how the real checks fail, and the send refusals were untested. Cases now
  raise the real failure types for every target and assert both callers
  classify them alike, and cover a refused mailbox authentication, Slack
  refusing the post and a send-time failure reaching the console.

The nine findings the validation step did not confirm were not carried
forward.
