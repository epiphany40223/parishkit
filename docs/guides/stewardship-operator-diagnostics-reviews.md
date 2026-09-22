# Stewardship operator diagnostics reviews

This ledger records the independent review/fix rounds of the corrections the
pre-launch gate's fourth round (a correction check of PL-I2 and PL-I5)
found:

- PL-I5 (Medium, Medium): the backup and upgrade runbooks promised process
  log diagnostics (`pg_dump`'s message for a failed dump, and a sentence
  naming a missing backup for a refused upgrade) that the production log
  formatter drops, since it keeps only reviewed events. Two reviewed failure
  categories, `backup_dump_failed` and `upgrade_backup_required`, now carry
  them on the existing `startup_rejected` event;
  `pg_dump`'s own text is no longer read or logged, since it can name hosts,
  roles and paths; tests check the formatted output; and the
  [backup runbook](stewardship-backup-runbook.md), the
  [deployment runbook](stewardship-deployment-runbook.md) and the
  [backup guide](stewardship-backup.md) name the failure categories.
- PL-I2 (Low): if the operator paused during a mail outage, resume refuses
  while any delivery is unknown, so the
  [outage recovery](stewardship-launch-runbooks.md#mail-provider-outage) now
  settles unknown deliveries before resuming and retrying.

It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds, with a correction check after any round that validates a
finding. The Codex reviewer has been out of quota since September 20, 2026;
under the human's exemption, extended through October 30, 2026, a completed
Claude-only pass counts as a round, and each round records which sources
answered.

## Round 1

Claude only (Codex produced no structured output). Nine raw findings, one
validated and corrected:

- High: `Event` is a closed contract mirrored by the SQL constraint on the
  persisted operational events, and a database test inserts every value, so
  the two new events would have failed it and needed a schema change. They
  are now `FailureKind` categories on the existing `startup_rejected`
  event, which the formatter already keeps and SQL does not mirror; the
  contract test passes and the documents name the categories.

All eight findings below the validation cutoff were taken: the
`dump_database` docstring no longer says `pg_dump`'s message is logged,
stderr is drained in bounded chunks, the dump test asserts that the canary
text reaches neither the raw records nor the formatted output, the backup
runbook notes that a dump running past an hour is logged as an unexpected
failure, the outage step points to the other resume conditions, and the
runbooks link this ledger. The operator test still covers the shared
refusal branch rather than each command's own raise site. A correction
check follows.

## Round 2

Claude only (Codex produced no structured output). Seven raw findings, one
validated and corrected:

- Medium: the backup runbook said a dump running past an hour is stopped,
  but the hour is counted only after `pg_dump` closes its output, so a dump
  that hangs is never bounded. The runbook no longer promises a limit and
  names the overdue alert as what reports a dump that never finishes.

The six findings below the validation cutoff were weighed: a failed dump
logs `backup_dump_failed` and then the command's general configuration
refusal, which the runbook now says; the dump test parses the formatted
lines and checks the event, the category and the absent canary there; the
ledger names failure categories; and two long or stray source lines were
rewrapped. The operator test still covers the shared refusal branch rather
than each command's own raise site. A correction check follows.

## Round 3

Claude only (Codex produced no structured output). Four raw findings, one
validated and corrected:

- Medium: the backup checklist tied the database and the password file
  only to `backup_dump_failed`, but the command connects to the database
  and checks its login and schema before the dump starts, so a stopped
  database or a mismatched password file is logged as
  `database_unavailable`. The checklist now names both categories.

The three findings below the validation cutoff were taken: the dump no
longer passes a one-hour timeout it could never enforce, with a comment
naming the overdue alert as the bound; the backup commands' docstring says
the process log records only a reviewed category; and the operator test
parses the formatted line and checks the event and the category. A
correction check follows.
