# Stewardship runbook corrections reviews

This ledger records the independent review/fix rounds of the operator
procedure corrections that the pre-launch gate's second integration round
found:

- PL-I5 (High): an upgrade never refreshed the static files, so a release
  that changed or added a script shipped with the previous release's
  scripts; the deployment runbook's upgrade gains a static refresh step, and
  the rollback and the restore refresh them too
  ([deployment runbook](stewardship-deployment-runbook.md#upgrade),
  [backup runbook](stewardship-backup-runbook.md#restore-for-real),
  [runtime guide](stewardship-runtime.md)).
- PL-I5 (Medium): an application-only rollback after a release that added a
  runtime grant left the previous release's services refusing to start;
  such a rollback is now limited to releases that changed neither the schema
  nor a grant and added no deployment field (a Low finding), and a release
  that narrows a grant is documented as a reinstall before the schema
  freeze (a second Medium).
- PL-I5 (Low): a restore left plaintext copies of every Family's data and
  every credential; the restore now ends by deleting them.
- PL-I4 (Medium): a failed report preparation never finishes by itself, yet
  resume and the closed-campaign resolution told the operator to wait; the
  runbook now points to **Retry report work** on the failed task's page.
- PL-I4 (Medium): resume and a closed-campaign release need a Google sign-in
  from the last five minutes, which returns to the home page, and a sender
  test that is valid for five minutes; the steps now order both clocks, as
  the activation procedure already does.
- PL-I2 (Low): on a paused active campaign, a failed report to a current
  Administrator is folded into the next combined report at resume rather
  than offered for retry; the launch runbook and the
  [unsent resolution guide](stewardship-unsent-resolution.md) now say so.

It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds for a documentation increment, with a correction check after any
round that validates a finding. The Codex reviewer has been out of quota
since September 20, 2026; under the human's exemption, extended through
October 30, 2026, a completed Claude-only pass counts as a round, and each
round records which sources answered.

## Round 1

Claude only (Codex produced no structured output). Six raw findings, none
validated. All six low-severity notes were taken because an operator would
meet them: every closed-campaign resolution, not only a release, needs the
fresh sign-in; the home page lists only the latest five failures of the
past day, so the Background work page is where to look; the sender test is
sent from the campaign mail page and the operator returns to the delivery
control page; a release without schema or grant changes still pulls;
`database-grants` refuses a narrowing release outright; and the kept static
tree is named after the release being replaced, which a rollback may put
back. A further round follows.

## Round 2

Claude only (Codex produced no structured output). Six raw findings, one
validated and corrected:

- Medium: `migration` runs and commits before `database-grants` refuses a
  grant-narrowing release, and the runbook did not say how to recover. It
  now says to check the release notes for a narrowed grant before stopping
  the services, and to recover from a refusal after a successful migration
  with the database-restore rollback (or, before the freeze, by
  reinstalling), never by starting either image.

All five findings below the validation cutoff were taken: the resume
preview is a third five-minute clock; an authentication error on confirming
has the same recovery as a missing button; the deployment runbook links this
ledger; the restore's static step warns against moving onto an existing name
or offers the kept tree; and two long lines are rewrapped. A correction
check follows.
