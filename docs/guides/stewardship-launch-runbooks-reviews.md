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

## Round 2

Claude only (Codex produced no output). Fourteen raw findings, six
validated:

- High: the credential replacement step implied acknowledgement happens by
  itself; the operator must run `acknowledge-credential` inside each
  recreated service with the request UUID from the status page, using `exec`
  and never `compose run`, with exactly one Compose file. The step now says
  so. Corrected.
- High: while delivery is paused the resend is not offered for a live
  message (round 3 widened this from Family mail to receipts and Admin
  reports too), yet resume is refused while any unknown delivery remains, so
  a message the provider shows was not sent cannot be resolved during a
  pause. This is a product defect, not a documentation one; the
  runbook now states the gap, forbids a false delivery confirmation to
  unblock a resume, and gives the interim course, and the code correction is
  the next increment, before the pre-launch gate.
- Medium: the resume preconditions omitted already-submitting messages,
  blocked Family groups and a running activation catch-up; they are listed.
  Corrected.
- Medium: the resend step presented the button as always available; its
  preconditions and what to do when it is absent are stated. Corrected.
- Medium: the closed-campaign path omitted the clear decision and the
  cancel refusals; both are described. Corrected.
- Medium: a failed or expired replacement after consumers were recreated
  needs them recreated again, not restarted; a failure branch says so.
  Corrected.

The eight findings the validation step did not confirm were not carried
forward. Since the second round validated findings, a correction check
follows.

## Round 3

Claude only (Codex produced no output). Correction check: five raw
findings, two validated, both corrected:

- Medium: the stated pause gap also covers submission receipts and Admin
  reports, whose resend admission refuses during a pause as well; the gap
  now names every live message type, and the code correction covers them.
- Medium: the resend conditions listed were those of invitations and
  reminders only; the step now gives the receipt and Admin-report
  conditions separately and links the delivery resolution guide.

The three findings the validation step did not confirm were not carried
forward. A further correction check follows.
