# Stewardship launch runbooks reviews

This ledger records the independent review/fix rounds of the
[launch runbooks](stewardship-launch-runbooks.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds for a documentation increment, with a correction check after any
round that validates a finding. The Codex reviewer has been out of quota
since September 20, 2026; under the human's exemption, extended through
October 30, 2026, a completed Claude-only pass counts as a round, and each
round records which sources answered.

## Round 1

Claude only (Codex produced no output). Fourteen raw findings, five
validated, all corrected:

- High: the resume procedure did not match the delivery controls. The
  held-message resolution by type exists only for a campaign that closed
  while paused; an active campaign's resume coalesces overdue Family mail,
  turns overdue reports into report obligations and releases every receipt;
  and it requires resolved unknown deliveries, a fresh accepted sender test
  and a recent Google sign-in. The procedure now follows the page's own
  steps and labels, and describes the closed-campaign resolution separately.
- Medium: pausing holds all unsent live mail, not Family mail plus selected
  reports, and the controls exist only for the current Production campaign;
  the introduction now says so.
- Medium: credential replacement is on the Integrations **Replace
  credential** page, not the setup wizard, and needs the consumers recreated
  within an hour and the acknowledged fingerprint selected. A new section
  gives the real four steps; the outage procedures point to it.
- Medium: operational email uses the same mailbox, so a mail outage's alert
  may not arrive, and a quiet day proves nothing. The alert section now
  recommends Slack, names the critical-events banner and failed-task list,
  and suggests the daily Admin report as the heartbeat.
- Medium: `invalid` from the mailbox check also covers revoked delegation, a
  suspended mailbox and a mistyped address, which a new key does not fix. The
  step now says so and replaces the key only after the admin console is
  clean.

The nine findings the validation step did not confirm were not carried
forward.
