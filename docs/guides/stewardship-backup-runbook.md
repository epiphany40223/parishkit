# Stewardship backup runbook

The operator's procedure for the v1 backup: making the key, running the
nightly backup, copying it off the host, checking it, and restoring it into
a disposable environment. The [backup guide](stewardship-backup.md) explains
the design; the [deployment runbook](stewardship-deployment-runbook.md) says
when a backup is required (before Production activation and before every
upgrade). Where this runbook and the guide disagree, the guide is right.

A deployment provisioned before the release that introduced the backup has
no backup login, password, directory or record table; reinstall it from
scratch first, as the deployment runbook's Testing-mode section says.

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
rehearse [Restore for real](#restore-for-real) end to end on a deployment
that is not Production: the validation deployment before activation, or a
disposable host built from the same release. A drill that only counts rows
in a database proves nothing about startup, so it runs every step, including
starting the web service and checking its health. The gate approves the
drill's evidence. Record the date, the set name, the manifest digests, the
image digest and the outcome in the parish's operations notes, then delete
the decrypted files.

## Restore for real

A real restore follows the launch scope's
[manual restore procedure](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
and the deployment runbook's [rollback](stewardship-deployment-runbook.md#rollback).
Commands below follow the deployment's usual `docker compose` prefix: the one
rendered Compose file the deployment runs (`compose.json` or
`compose-slack.json`) and its fixed project name.

1. **Stop.** Stop every online service and `caddy`: `stop caddy web worker
   scheduler mail-dispatch config-installer` and every credential installer.
   Leave `postgres` and `valkey` running. The scheduler, worker and
   mail-dispatch services stay stopped until step 6.
2. **Open the set.** On the machine with the private key, confirm the set is
   the one the deployment recorded (the SHA-256 of `manifest.json` equals the
   digest the backup printed and the off-host log kept), then decrypt both
   files with
   `pk-stewardship backup-open --key PRIVATE_KEY_FILE --input database.pgdump.sealed --destination database.pgdump`
   and the same for `files.tar.sealed`. Each prints the kind, size and
   digest, which must match the manifest. Copy `database.pgdump` and
   `files.tar` to the host over a private channel.
3. **Restore the files, whole.** The archive holds the `config`,
   `credentials` and `media` trees of the runtime root, taken together with
   the database. Restore all three from the same set, never only the files
   that are missing: a configuration or credential changed after the backup
   no longer matches the restored database, and the services refuse to start
   against it. Move each current tree aside (for example to
   `config.pre-restore`), never delete it, extract `files.tar` into the
   runtime root, and give the three trees back to UID/GID `10001:10001`. The
   archive keeps owner-only modes.
4. **Restore the database.** On the same host, the cluster keeps the
   deployment's roles. On a replacement host, first start `postgres` on the
   restored runtime root and run
   `run --rm database-provision database-roles --config PROVISION_CONFIG --confirm-deployment UUID`,
   which creates the roles with the restored password files on the new, empty
   database. Then replace the database's contents in one transaction, as the
   cluster's operator login, from inside the database container:

   ```sh
   docker compose ... exec -T postgres pg_restore \
     --username pk_stewardship_operator --dbname DATABASE_NAME \
     --clean --if-exists --single-transaction --exit-on-error < database.pgdump
   ```

   Keep the deployment's database: never drop and recreate it, because its
   provisioning marker and database-level privileges belong to the database,
   not to the dump. The dump carries every owner and privilege, so no
   `database-grants` or `migration` step follows. A failed restore rolls back
   whole and leaves the previous contents in place.
5. **Point at the set's image.** Run `retarget-image` back to the image the
   backup was taken under, in that image, then `pull`. The manifest's
   `application_version` names the release; the operators' notes record its
   image digest.
6. **Start web alone and review.** Start `web` and `caddy` only, and run the
   health command. An Administrator pauses delivery on the campaign's
   delivery control page if it is not already paused, then compares the
   restored deliveries with the mail provider's own sent log for the period
   after the backup, and notes every message the provider sent that the
   restored state does not show as delivered (see the limitations below).
   Then start `scheduler`, `worker`, `mail-dispatch`, `config-installer` and
   the credential installers, and resume delivery deliberately.
7. **Take a fresh backup.** Run the backup at once. It records a new run,
   which later upgrade admissions require, and captures the restored state.

## Restore limitations in v1

A restore returns the deployment to the backup's moment. v1 has no
restore-review workflow, so the operator and the Administrator must plan for
the following, and the pre-launch gate approves them as known limitations:

- **Family access stays open during the review.** v1 has no control that
  closes the Family portal. From step 6, Families can sign in to the restored
  state and submit.
- **Work after the backup is lost.** Submissions, Admin edits and deliveries
  recorded after the backup are not in the restored database. A Family whose
  submission was lost must submit again.
- **Mail sent after the backup can be sent again.** The restored database
  does not know about messages the provider accepted after the backup, and v1
  has no control to mark such a message sent or to cancel it on an active
  campaign. When delivery resumes, the invitations and reminders that the
  restored state still considers due are sent, coalesced by the ordinary
  overdue plan, so those Families can receive a second copy. To keep this
  window small, run a backup by hand right after each large send (the initial
  invitations and each reminder wave) as well as nightly.

## Known v1 limitations

- The backup is consistent for the database (one `pg_dump` snapshot) but the
  configuration, credentials and media archive is taken after it, not
  atomically with it; all three change rarely and by operator or Admin
  action.
- Off-host copy, retention beyond the host, the escrow workflow and
  automated restore are the operator's by hand; the deferred remainder is
  listed in the launch scope.
- The private key is not rotated during v1.
- The sealed files are anonymous encryption to the public key: they prove
  they were not altered, not who made them. The recorded manifest digest,
  kept off the host, is the origin check; there is no host-held signing key.
