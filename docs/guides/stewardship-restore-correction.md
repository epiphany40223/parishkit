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

The first review round then found that the corrected procedure still could
not finish on a replacement host or after a schema change: the archive named
media by tree while the default media store is `run/persistent/media`, the
provisioning record that `retarget-image` needs was not in the set, the
directories outside the set were not listed, `pg_restore --clean` leaves a
later release's objects behind, the tree roots had no recorded modes, a
branding upload or cleanup during the backup could fail or truncate it, and a
drill of every step on a disposable host would start a second live copy of
Production. It also found that piping `pg_restore` into `psql` commits an
emptied schema and reports success when `pg_restore` fails.

## The correction

- `pg_dump` now keeps owners and privileges. The same role names exist
  wherever the deployment's roles were provisioned (the same cluster, or a
  replacement host after `database-roles`).
- The archive holds the `config`, `credentials` and `media` trees, named by
  tree, with owner-only root entries, and the completed provisioning record;
  the backup profile mounts `media` and the record read-only. Each file is
  read from one open descriptor, and a media file that disappears during the
  backup is left out rather than failing it. The backup refuses to run when
  the authority store is outside the archived trees.
- The runbook's **Restore for real** gives exact steps: stop the online
  services; open and verify the set; on a replacement host, create the
  directories and startup lock outside the set without running
  `provision-runtime`; restore the three trees to their configured paths and
  the record to the runtime root, moving the current ones aside; on a
  replacement host, collect static files and run `database-roles`; convert
  the dump to SQL inside the database container, then empty the application
  schema and load it in one `psql --single-transaction`, as the operator
  login, keeping the database itself; retarget to the set's image; start web
  alone, pause delivery, compare with the provider's sent log, then start the
  background services and resume deliberately; and take a fresh backup.
- A new **Restore limitations in v1** section states what the gate must
  approve: Family access stays open during the review, work after the backup
  is lost, and mail the provider accepted after the backup can be sent again.
  It advises a backup by hand after each large send. The deployment runbook's
  rollback and the launch scope's manual restore now say the same.
- The drill rehearses the whole real restore on the validation deployment
  before activation. After activation it runs on a disposable host off the
  public DNS, stops once web is healthy, never starts the background
  services, and destroys the host afterwards.

## Focused validation

- Pure: the backup profile's targets are the three trees, the key, the lock,
  the provisioning record and the output; an authority outside the archived
  trees is refused; a set's archive holds a branding file, the provisioning
  record and `0700` tree roots; a media file removed mid-backup is left out,
  while a missing configuration file still fails it; the dump command keeps
  owners and privileges. The backup, backup boundary, topology, service boundary,
  retarget and provisioning suites pass.
- PostgreSQL 18, by hand against a fresh-install schema database: the old
  `--no-owner --no-acl` dump restores all 55 definer functions executable by
  `PUBLIC`. A dump with owners and privileges, loaded by the runbook's exact
  step 6 commands, reproduces every function's privileges and owner (identical
  digest) and every object (1282), and removes an object the dump does not
  have; a truncated dump fails at conversion and changes nothing. On a small
  schema with a non-default owner, the same load reproduces the schema's
  owner and privileges, every table and function privilege and the data, and
  keeps the database's comment. Piping `pg_restore` into `psql` instead was
  shown to commit an emptied schema with exit status 0 when `pg_restore`
  failed.
- The end-to-end restore on a real deployment is the drill, which the human
  runs on the validation deployment before the gate exits.

## Checkpoint

Implementation and focused validation are complete; the
[review rounds](stewardship-restore-correction-reviews.md), full exact-head
CI, DCO and protected delivery remain open. No deployment, release,
live-provider write or database deletion is authorized by this increment.
