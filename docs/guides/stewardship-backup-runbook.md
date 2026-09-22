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
exits `2`, and the process log records only the failure's category (a
configuration refusal, for most causes), plus `pg_dump`'s own message when
the dump itself failed. Keep the printed manifest digest with the off-host
copy (the cron job can append the line to a log there): the sealed files
prove that they were not altered, not who made them, and the digest is how a
restore proves it is restoring the set the deployment recorded. Schedule it
from the host's cron twice a day, twelve hours apart and in UTC (for
example after the campaign's nightly work and again twelve hours later), and
once immediately before Production activation and before every upgrade. The
overdue alert below fires after 24 hours without a completed backup, so a
single nightly run would page on any late night, and a local-time schedule
gains an hour at the daylight-saving change. Each run leaves a dated directory under
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
resolves it when one has. When it fires: run the backup by hand. If it
refuses, the log names only the category, so check the usual causes in
turn: the recipient key file is present and readable; the `backups`
directory is owned by `10001:10001` with mode `0700`; the authority store
lies inside the configuration tree; no set directory with the same minute's
name already exists; and the database is reachable (a failed dump logs
`pg_dump`'s own message). Fix the cause, run it again, and confirm the
off-host copy holds the newest set's three files.

## Restore drill

A drill that only counts rows proves nothing about startup, so the drill
rehearses [Restore for real](#restore-for-real). Before the pre-launch gate,
and whenever the restore procedure changes:

- **Before activation**, run the whole procedure on the validation
  deployment itself, still in Testing mode, restoring its own newest set.
  Its credentials and mail routing are the ones it already uses, so every
  step, including starting the background services, is safe. Then rehearse
  the replacement-host steps too: restore the same set onto a second,
  disposable host that is not on the public DNS, through web's health check
  in step 8, and destroy that host afterwards. The same-host run skips steps
  3 and 5, and a real replacement is when they matter.
- **After activation**, never start a second live copy of Production. Use a
  disposable host that is not on the public origin's DNS, run the procedure
  only up to starting web and checking its health in step 8, and never start
  the scheduler, worker, mail-dispatch, installers or `caddy` there: the set
  carries every Production credential and every Family's data. Destroy the
  host and its disks afterwards.

The gate approves the evidence of both pre-activation runs. Record the date, the
set name, the manifest digests, the image digest and the outcome in the
parish's operations notes, then delete the decrypted files.

## Restore for real

A real restore follows the launch scope's
[manual restore procedure](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
and the deployment runbook's [rollback](stewardship-deployment-runbook.md#rollback).
Commands below follow the deployment's usual `docker compose` prefix: the one
rendered Compose file the deployment runs (`compose.json` or
`compose-slack.json`) and its fixed project name. Paths are the default
layout; where the deployment YAML overrides a path, use that path instead.

1. **Stop.** Disable the host's backup and off-host copy cron jobs until
   step 9: a backup that starts mid-restore would archive a mix of old and
   restored files, record itself as the newest set and be copied off the
   host. Then stop every online service and `caddy`: `stop caddy web worker
   scheduler mail-dispatch config-installer` and every credential installer.
   Leave `postgres` and `valkey` running. The scheduler, worker and
   mail-dispatch services stay stopped until step 8.
2. **Open the set.** On the machine with the private key, confirm the set is
   the one the deployment recorded (the SHA-256 of `manifest.json` equals the
   digest the backup printed and the off-host log kept), then decrypt both
   files with
   `pk-stewardship backup-open --key PRIVATE_KEY_FILE --input database.pgdump.sealed --destination database.pgdump`
   and the same for `files.tar.sealed`. Each prints the kind, size and
   digest, which must match the manifest. Copy `database.pgdump` and
   `files.tar` to the host over a private channel.
3. **Replacement host only: prepare it.** Never run `provision-runtime`
   here: it would generate new passwords that the restored roles and files
   do not have. Bring the operator's deployment YAML and the deployment UUID,
   both kept off the host with the private key: `retarget-image` rebuilds its
   plan from that YAML and refuses unless everything but the image matches
   the restored record, and the rendered documents hold absolute host paths.
   So use the same runtime root path, the same path overrides and the same
   bind-source layout as the lost host. Create the runtime root as
   [Storage and identities](stewardship-runtime.md#storage-and-identities)
   says, then create, owned by `10001:10001` with mode `0700`, the
   directories that are not in the set: `backups`, `cache`, `logs`,
   `reports`, `run`, `run/persistent`, and under it `caddy`,
   `caddy/config`, `caddy/data`, `postgresql` and `valkey`. Create
   `run/startup.lock`, owned by `10001:10001` with mode `0600`, containing
   exactly the line `parishkit-stewardship-startup-v1`. Pull the image the
   backup was taken under (its digest is in the operators' notes; the
   restored Compose files name it). For a real replacement, point the public
   origin's DNS at this host (`caddy` obtains a new certificate; its store is
   not backed up); a drill host stays off the public DNS.
4. **Restore the files, whole.** The archive holds the `config`,
   `credentials` and `media` trees, named by tree rather than by host path,
   and the provisioning record `.stewardship-provisioned.json`. Restore all of
   them from the same set, never only the files that are missing: a
   configuration or credential changed after the backup no longer matches the
   restored database, and the services refuse to start against it. Extract
   `files.tar` into an empty private staging directory. Move each current
   tree aside under a name that does not exist yet (for example `config` to
   `config.pre-restore-DATE`; moving onto an existing directory would nest
   the tree inside it), never delete it, then move `config` to the runtime root's `config`, `credentials` to its
   `credentials`, `media` to `run/persistent/media`, and the record to the
   runtime root itself. Give everything back to `10001:10001`; the archive
   records owner-only modes (`0700` directories, `0600` files).
5. **Replacement host only: roles.** Start `postgres` and `valkey` with
   `up --detach --wait postgres valkey`, then run
   `run --rm database-provision database-roles --config PROVISION_CONFIG --confirm-deployment UUID`
   with the deployment's UUID. It creates the roles with the restored
   password files on the new, empty database.
6. **Restore the database.** Replace the application schema's whole
   contents in one transaction, as the cluster's operator login, inside the
   database container. First copy the dump in and convert it to SQL, so a
   damaged or truncated dump fails before anything changes:

   ```sh
   docker compose ... exec -T postgres sh -c 'cat > /tmp/restore.pgdump' < database.pgdump
   docker compose ... exec -T postgres pg_restore --file=/tmp/restore.sql /tmp/restore.pgdump
   ```

   Only when both succeed, empty the schema and load it as one transaction:

   ```sh
   docker compose ... exec -T postgres sh -c 'printf "%s\n" \
     "DROP SCHEMA public CASCADE;" "CREATE SCHEMA public;" \
     "GRANT USAGE ON SCHEMA public TO PUBLIC;" > /tmp/prefix.sql'
   docker compose ... exec -T postgres psql --username pk_stewardship_operator \
     --dbname DATABASE_NAME --single-transaction -v ON_ERROR_STOP=1 --quiet \
     -f /tmp/prefix.sql -f /tmp/restore.sql
   docker compose ... exec -T postgres rm /tmp/restore.pgdump /tmp/restore.sql /tmp/prefix.sql
   ```

   Never pipe `pg_restore` straight into `psql`: if `pg_restore` fails
   mid-stream, `psql` still commits the empty schema and reports success.
   Emptying the schema first removes objects a later release added (the
   rollback after a schema change), which a plain `pg_restore --clean` would
   leave behind. Keep the deployment's database itself: never drop and
   recreate it, because its provisioning marker and database-level
   privileges belong to the database, not to the dump. The dump carries every
   owner and privilege, including the schema's owner, so no `database-grants`
   or `migration` step follows. Any error in the load rolls the whole
   transaction back and leaves the previous contents in place; fix the cause
   and run it again. The container's `/tmp` is memory-backed and disappears
   when the container stops.
7. **Point at the set's image.** Run `retarget-image` back to the image the
   backup was taken under, in that image, then `pull`. The manifest's
   `application_version` names the release; the operators' notes record its
   image digest. Then give that image its own static files, on every host
   (a replacement host has no `cache/static` yet, so it starts at the
   empty one). First move any current `cache/static` aside under a name that does not
   exist yet (moving onto an existing directory nests the tree). Then either
   put back the tree an upgrade kept for the set's release, or create an
   empty `cache/static` owned by `10001:10001` with mode `0700` and run
   `collect-static` into it in the set's image, as the deployment runbook's
   [upgrade](stewardship-deployment-runbook.md#upgrade) does. The static
   tree is not in the set, and a newer release's scripts must not be served
   with the restored release's pages.
8. **Start web alone and review.** Start `web` and `caddy` only, and run the
   health command. An Administrator pauses delivery on the campaign's
   delivery control page if it is not already paused, then compares the
   restored deliveries with the mail provider's own sent log for the period
   after the backup, and notes every message the provider sent that the
   restored state does not show as delivered (see the limitations below).
   Then start `scheduler`, `worker`, `mail-dispatch`, `config-installer` and
   the credential installers, and resume delivery deliberately.
9. **Take a fresh backup.** Run the backup at once, then re-enable the
   backup and off-host copy cron jobs. The backup records a new run, which
   later upgrade admissions require, and captures the restored state.
10. **Delete the decrypted copies.** Once the review in step 8 is complete,
    securely delete `database.pgdump`, `files.tar` and the staging directory
    on the host and on the machine that holds the private key. They hold
    every Family's data and every credential in plain form; the sealed set
    off the host is the copy to keep. Keep the moved-aside trees only until
    the restored deployment is accepted, then delete them the same way.

## Restore limitations in v1

A restore returns the deployment to the backup's moment. v1 has no
restore-review workflow, so the operator and the Administrator must plan for
the following, and the pre-launch gate approves them as known limitations:

- **Family access stays open during the review.** v1 has no control that
  closes the Family portal. From step 8, Families can sign in to the restored
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
- The set covers the default locations: the `config`, `credentials` and
  `media` trees and the provisioning record. A deployment that overrides an
  individual credential or password file to a path outside the credentials
  tree must copy that file off the host itself. An authority store outside
  the archived trees is still a valid deployment, but its backup run refuses.
- The sealed files are anonymous encryption to the public key: they prove
  they were not altered, not who made them. The recorded manifest digest,
  kept off the host, is the origin check; there is no host-held signing key.
