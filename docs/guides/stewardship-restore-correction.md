# Stewardship restore correction

This guide records corrections to the [v1 backup](stewardship-backup.md) and
its [runbook](stewardship-backup-runbook.md) found by the
pre-launch gate's first integration round
(PL-I5, with one PL-I1 finding on the same procedure). They follow the
[pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy)
and change no schema.

## The defects

- **A restored database could not start.** The dump used `--no-owner
  --no-acl`. Every security-definer function's `REVOKE ... FROM PUBLIC` and
  every runtime grant live only in the privileges, so a restore from such a
  dump left all 55 definer functions executable by `PUBLIC` and the runtime
  logins without table access. The services' own database admission then
  refuses to start, and the backup login could not record a new run, so the
  upgrade admission could not recover either. The drill only counted rows, so
  it could not notice.
- **The restore runbook promised what v1 cannot do.** It told the operator
  to start the web service with Family access closed and implied that no
  Family message would be sent twice. v1 has no control that closes Family
  access and none that marks restored mail as sent.
- **Only missing files were restored**, although a configuration or
  credential changed after the backup no longer matches the restored database
  and stops startup; and the runbook gave no command for emptying the
  database, where dropping it would lose its provisioning marker.
- **Uploaded branding was not backed up**, although the operations
  specification's backup set includes it and the restored database refers to
  it.
- A deployment that moved the authority store outside the configuration tree
  would have backed up without it, silently.

## The correction

- `pg_dump` now keeps owners and privileges. The same role names exist
  wherever the deployment's roles were provisioned (the same cluster, or a
  replacement host after `database-roles`).
- The archive holds the `config`, `credentials` and `media` trees, and the
  backup profile mounts `media` read-only. The backup refuses to run when the
  authority store is outside the archived trees.
- The runbook's **Restore for real** gives exact steps: stop the online
  services; open and verify the set; restore all three trees from the same
  set, moving the current ones aside; on a replacement host run
  `database-roles` first; replace the database's contents with
  `pg_restore --clean --if-exists --single-transaction --exit-on-error` as
  the operator login inside the database container, keeping the database
  itself; retarget to the set's image; start web alone, pause delivery,
  compare with the provider's sent log, then start the background services
  and resume deliberately; and take a fresh backup at once.
- A new **Restore limitations in v1** section states what the gate must
  approve: Family access stays open during the review, work after the backup
  is lost, and mail the provider accepted after the backup can be sent again.
  It advises a backup by hand after each large send. The deployment runbook's
  rollback and the launch scope's manual restore now say the same.
- The drill rehearses the whole real restore, including starting web and
  checking health, on a deployment that is not Production.

## Focused validation

- Pure: the backup profile's targets are the three trees, the key, the lock
  and the output; an authority outside the archived trees is refused; a set's
  archive holds a branding file; the dump command keeps owners and
  privileges. The backup, backup boundary, topology, service boundary,
  retarget and provisioning suites pass.
- PostgreSQL 18, by hand against a fresh-install schema database: a dump
  with owners and privileges, restored with the runbook's `pg_restore`
  options into an empty provisioned-style database and again over the
  populated one, reproduces every function's privileges and owner exactly
  (identical digest) and keeps the database's provisioning comment; the old
  `--no-owner --no-acl` dump restores all 55 definer functions executable by
  `PUBLIC`.
- The end-to-end restore on a real deployment is the drill, which the human
  runs on the validation deployment before the gate exits.

## Checkpoint

Implementation and focused validation are complete; the
[review rounds](stewardship-restore-correction-reviews.md), full exact-head
CI, DCO and protected delivery remain open. No deployment, release,
live-provider write or database deletion is authorized by this increment.
