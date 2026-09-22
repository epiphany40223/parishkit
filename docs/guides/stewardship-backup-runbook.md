# Stewardship backup runbook

The operator's procedure for the v1 backup: making the key, running the
nightly backup, copying it off the host, checking it, and restoring it into
a disposable environment. The [backup guide](stewardship-backup.md) explains
the design; the [deployment runbook](stewardship-deployment-runbook.md) says
when a backup is required (before Production activation and before every
upgrade). Where this runbook and the guide disagree, the guide is right.

## The key

Once, on a machine that is not the host, run
`pk-stewardship backup-keygen --destination PRIVATE_KEY_FILE`. It writes the
private key owner-only to a new file and prints the public key. Keep the
private key where the parish keeps its other recovery material, with a copy
in a second place; whoever holds it can read every backup, and without it no
backup can be read. Record its fingerprint from the first backup's manifest.
The key is not rotated during v1.

On the host, install the public key as the `backup_data` credential: write
the printed line to `credentials/backup_data/credential` under the runtime
root, owned by UID/GID `10001:10001` in a `0700` directory with mode `0600`,
as every credential file is. Nothing else reads it.

## The nightly backup

Run the rendered `backup-worker` profile with the same Compose file and
project name the deployment uses:

```text
docker compose ... run --rm backup-worker
```

It prints one JSON line naming the sizes, the recipient fingerprint and the
manifest digest and exits `0`; on any refusal it prints one generic line and
exits `2`, and the process log has the cause. Keep the printed manifest
digest with the off-host copy (the cron job can append the line to a log
there): the sealed files prove that they were not altered, not who made
them, and the digest is how a restore proves it is restoring the set the
deployment recorded. Schedule it from the host's cron every night,
after the campaign's own nightly work, and once immediately before Production
activation and before every upgrade. Each run leaves a dated directory under
`backups/` in the runtime root with `database.pgdump.sealed`,
`files.tar.sealed` and `manifest.json`; the newest thirty complete sets are
kept. A failed run leaves its directory without a manifest for inspection;
it never counts toward the thirty and is not removed, so delete it by hand
once the cause is understood.

Copy the `backups/` directory off the host after each run, with `rsync` or
`rclone` from the same cron job to the parish's off-host storage; the sealed
files are safe to store anywhere, and the manifest holds no secret. The
application does not transfer anything itself.

## Checking

The scheduler raises the `backup_rpo_breach` operational incident, through
the configured alert routes, when no backup has completed in the last 24
hours, once the deployment is in Production or has ever backed up, and
resolves it when one has. When it fires: run the backup by hand, read its
refusal cause in the log, and fix the cause (usually the recipient key file,
the output directory's ownership, or the database). Confirm the off-host copy
holds the newest set's three files.

## Restore drill

Before the pre-launch gate, and whenever the restore procedure changes,
restore the newest set into a disposable environment; the gate approves the
drill's evidence:

1. On a machine with the private key, first confirm the set is the one the
   deployment recorded: the SHA-256 of `manifest.json` must equal the
   manifest digest the backup printed and the off-host log kept (or the
   `manifest_digest` of the matching `stewardship_backup_run` row in the
   live deployment). Then decrypt both files:
   `pk-stewardship backup-open --key PRIVATE_KEY_FILE --input database.pgdump.sealed --destination database.pgdump`
   and the same for `files.tar.sealed`. Each prints the kind, size and
   digest, which must match the manifest.
2. Start a disposable PostgreSQL 18 with an empty database and restore the
   dump with `pg_restore --no-owner --no-acl --dbname DISPOSABLE database.pgdump`.
   Confirm the restored `stewardship_backup_run` and campaign tables hold the
   expected rows.
3. Unpack `files.tar` and confirm the configuration and credentials trees are
   complete: every per-service YAML, the three topologies, the Caddyfile and
   every credential file are present.
4. Record the date, the set name, the manifest digests and the outcome in the
   parish's operations notes. Then destroy the disposable database and delete
   the decrypted files.

## Restore for real

A real restore follows the launch scope's
[manual restore procedure](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
and the deployment runbook's [rollback](stewardship-deployment-runbook.md#rollback):
stop every online service; restore the database from the decrypted dump into
the deployment's PostgreSQL after emptying it, and any missing configuration
or credential file from the decrypted archive; run `retarget-image` back to
the image the backup was taken under; start only the web service with Family
access closed; have an Administrator compare the delivery and outbox state
with the mail provider's own logs; and only then start the background
services, with delivery paused if there is any doubt.

## Known v1 limitations

- The backup is consistent for the database (one `pg_dump` snapshot) but the
  configuration and credentials archive is taken after it, not atomically
  with it; both change rarely and by operator action.
- Off-host copy, retention beyond the host, the escrow workflow and
  automated restore are the operator's by hand; the deferred remainder is
  listed in the launch scope.
- The private key is not rotated during v1.
- The sealed files are anonymous encryption to the public key: they prove
  they were not altered, not who made them. The recorded manifest digest,
  kept off the host, is the origin check; there is no host-held signing key.
