# Stewardship operator diagnostics reviews

This ledger records the independent review/fix rounds of the corrections the
pre-launch gate's fourth round (a correction check of PL-I2 and PL-I5)
found:

- PL-I5 (Medium, Medium): the backup and upgrade runbooks promised process
  log diagnostics (`pg_dump`'s message for a failed dump, and a sentence
  naming a missing backup for a refused upgrade) that the production log
  formatter drops, since it keeps only reviewed events. Two reviewed events,
  `backup_dump_failed` and `upgrade_backup_required`, now carry them;
  `pg_dump`'s own text is no longer read or logged, since it can name hosts,
  roles and paths; tests check the formatted output; and the
  [backup runbook](stewardship-backup-runbook.md), the
  [deployment runbook](stewardship-deployment-runbook.md) and the
  [backup guide](stewardship-backup.md) name the events.
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
