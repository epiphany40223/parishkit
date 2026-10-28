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
backup can be read. To replace it later, see
[Replacing the key](#replacing-the-key).

The key machine runs the command from the release image, with Docker and no
network; `backup-open` in [Restore for real](#restore-for-real) runs the same
way. The image is `linux/amd64` (Docker Desktop runs it on other machines by
emulation), its entry point is `pk-stewardship`, and it runs as your own
user so the key file stays yours. `KEY_DIRECTORY` is an existing directory
you own, not under any runtime root:

```text
docker run --rm --network none --user "$(id -u):$(id -g)" --read-only \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --mount type=bind,source=KEY_DIRECTORY,target=/keys \
  IMAGE backup-keygen --destination /keys/stewardship-backup.key
```

The command prints one JSON line,
`{"public_key": "...", "recipient_fingerprint": "..."}`. Record the
fingerprint with the private key and each of its copies. On the host,
install the public key as the `backup_data` credential: write only the
`public_key` value, the bare base64 string on one line (for example the
output of `jq -r .public_key`), to `credentials/backup_data/credential`
under the runtime root, owned by UID/GID `10001:10001` in a `0700` directory
with mode `0600`, as every credential file is. A file holding the whole JSON
line is refused by every backup. Nothing else reads it.

Every backup prints the `recipient_fingerprint` of the public key it sealed
to; after the first one, confirm it equals the fingerprint `backup-keygen`
printed. If `backup-keygen` ran twice during setup, a mismatch here is the
sign that the kept private key is not the installed public key's pair:
every backup would still succeed and none could be opened. A backup whose
key differs from the previous run's (the `backup_data` file was replaced)
still runs, prints `"recipient_changed": true` and logs a WARNING
`configuration_digest_mismatch` line whose `failure_kind` is
`backup_recipient_changed`. The scheduler then raises the
`backup_key_changed` operational incident (CRITICAL) through the configured
alert routes (when an Administrator applied that key on the portal's
**Backup encryption key** page before the backup completed, it opens as a
WARNING and is routed once it escalates, about 15 minutes later; see
[Replacing the key](#replacing-the-key)), and writes one System log entry
(`configuration_digest_mismatch`, outcome `changed`) that says in plain
words what happened; the entry cannot name the fingerprints, so compare
each backup's printed `recipient_fingerprint` (or its row in
`stewardship_backup_run`) with the one recorded with the key. The incident
stays open for two days after the backup that changed the key, even if
later backups use the new key, and then resolves by itself: resolving means
only that no backup in the last two days changed the key, not that anyone
confirmed the kept key opens them, and nothing clears it earlier. Unless
you installed a new key on purpose, find out why; either way, open the new
set with each kept copy of the private key, as the restore drill does. The
fingerprint names the installed public key, so on its own it proves nothing
about the private key you kept. The
[restore drill](#restore-drill) is the proof: `backup-open` opens a set only
with the matching private key and then prints its `recipient_fingerprint`,
so the drill records that each kept copy of the private key opened the set.

### Replacing the key

An Administrator can replace the public key new backups are sealed to from
the Admin portal: **Integrations → Backup encryption key**. The page shows
the fingerprint of the key in use; before a key is set there, that is the
key the newest backup used from the installed `backup_data` file. The
private key never goes to the server, and the page refuses to continue until
the Administrator proves they hold it:

1. On the key machine, make a new pair with `backup-keygen` into a new file,
   as in [The key](#the-key), and record its fingerprint with it.
2. Paste only the printed `public_key` value on the page and select
   **Continue** (the code expires after 15 minutes).
   The page shows the new fingerprint and a challenge line starting with
   `PKBKP1:`.
3. On the key machine, run `backup-prove` with the new private key and paste
   the challenge line when it waits for input (or save the line to a file in
   `KEY_DIRECTORY` and pass `--input /keys/FILE`):

   ```text
   docker run --rm -i --network none --user "$(id -u):$(id -g)" --read-only \
     --cap-drop ALL --security-opt no-new-privileges:true \
     --mount type=bind,source=KEY_DIRECTORY,target=/keys,readonly \
     IMAGE backup-prove --key /keys/NEW_PRIVATE_KEY_FILE
   ```

   It prints one JSON line with the `code` and the key's
   `recipient_fingerprint`; a key that is not the pasted key's pair is
   refused.
4. Type the code on the page and select **Check code and continue**. Check
   the old and new fingerprints in the dialog and select **Apply**. The
   change is applied like any other settings change.

The next backup seals to the new key and prints `"recipient_source":
"configured"`; the `backup_data` file is no longer used, and the key cannot
be removed from the portal, only replaced. Applying it sends every
Administrator of the last 30 days a "Backup encryption key replaced"
security alert naming who changed it, when, and the key IDs before and
after; it stays on the Admin home page until an Administrator other than
the one who made it acknowledges it. The next backup opens the
`backup_key_changed` incident as a WARNING, which escalates to CRITICAL
and is routed about 15 minutes later like any other; a different key
appearing any other way is CRITICAL at once. If nobody at the
parish planned the change, replace the key again with the parish's own key
and find out who made it. Open that backup with the
new private key, as in [Checking the kept keys](#checking-the-kept-keys).

Keep every old private key, and its copies, until every set sealed to it
has left retention on the host and in Google Drive (the monthly sets keep
it up to about a year). A set names the fingerprint of the key it needs in
its manifest; a lost private key makes its sets unreadable. If a private
key may have been exposed (for example it was pasted into a web page),
replace the key at once; the sets already sealed to it stay readable to
whoever has it until they leave retention.

Keep beside the private key, off the host, everything a restore onto a new
host needs that no backup set holds: the deployment YAML, the deployment
UUID, the Compose project name, the runtime root path and each release's
image digest. The
[deployment runbook](stewardship-deployment-runbook.md#first-installation)
records them in the operators' notes; a copy of those notes that exists only
on the host is lost with it.

## The nightly backup

Run the rendered `backup-worker` profile with the same Compose file and
project name the deployment uses:

```text
docker compose ... run --rm backup-worker
```

It prints one JSON line naming the sizes, the recipient fingerprint (and
whether it changed since the previous run) and the manifest digest and
exits `0`; on any refusal it prints one generic line and
exits `2`, and the process log records only reviewed values: a
`startup_rejected` line whose `failure_kind` is the category (a
configuration refusal, for most causes). When the database dump itself
failed or produced nothing, a `startup_rejected` line with
`backup_dump_failed` comes first, followed by the command's general
configuration refusal. `pg_dump`'s own text is never logged, since it can
name hosts, roles and paths. A dump that never finishes has no time limit
of its own; the overdue alert below is what reports it. Keep the printed
manifest digest off the host: the sealed files prove that they were not
altered, not who made them, and the digest is how a restore proves it is
restoring the set the deployment recorded. The database records it too
(`stewardship_backup_run`), but only on the host, and no set holds its own
row. When the sets go off the host by `rsync` or `rclone`, the cron job can
append the line to a log that goes with them. The Google Drive copy below
sends only the set, so with it, send the line off the host yourself: for
example, have cron mail the command's output to the operators, or append it
to a file that another off-host copy picks up. Schedule it
from the host's cron twice a day, twelve hours apart and in UTC (a host set
to UTC, or `CRON_TZ=UTC` where the host's cron supports it; for example
after the campaign's nightly work and again twelve hours later), and
once immediately before Production activation and before every upgrade. The
overdue alert below fires after 24 hours without a completed backup, so a
single nightly run would page on any late night, and a local-time schedule
gains an hour at the daylight-saving change.

Also schedule **request mode** every five minutes, so an Administrator's
**Take a backup now** on the System health page runs soon after it is asked
for:

```text
*/5 * * * * docker compose ... run --rm backup-worker backup --request --config SERVICE_CONFIG
```

`SERVICE_CONFIG` is the `backup-worker` service configuration the Compose
file already passes to the profile. With no request waiting it exits `0` at
once and prints nothing, so it needs no log of its own. It also exits
quietly when the database cannot be read, when another backup is running,
when offline work (an upgrade, a migration, a restore) holds the startup
interlock, and during a bulk Family email send, when the request waits for
the send to finish. When it runs a backup, it prints the same JSON line as
the scheduled run plus `request_id`, so send its output off the host the
same way. A request it never picks up lapses after 30 minutes and is never
run late: the page then says the server did not pick it up and links this
section. Every backup run, scheduled or requested, holds one backup lock
while it makes its set, so two never make sets at the same time (an
off-site upload that follows may still be running): a scheduled run waits
for a running one for at most 30 minutes, then records a `task_timed_out`
entry (`lock_timeout`) and exits `2`. A scheduled run also completes a
request that is waiting. Both request mode and scheduled runs record
requests they expired or found stuck as `task_timed_out` entries
(`lease`), with the limit, the oldest one's age and how many. During a
restore review no request is claimed; a scheduled run still backs up.

Request mode arrived with the release that added Take a backup now. Its
upgrade is a schema and grant change: run the
[scripted upgrade](stewardship-deployment-runbook.md#scripted-upgrade) with
`STEWARDSHIP_SCHEMA_CHANGE=1`, so step 4 runs the migration and then
`database-grants` (the deployment runbook's
[Take a backup now](stewardship-deployment-runbook.md#take-a-backup-now-530)
section lists the steps), and install the request-mode cron line above only
after that upgrade; until the grants are applied, request mode logs one
`startup_rejected` line per poll. Before any deploy, check that no backup is running, scheduled or
requested (`docker ps --filter name=backup-worker` lists none), as for a
restore. A rollback to a release before it must also remove the
request-mode cron line, since that release has no `--request` option.

Each run leaves a dated directory under
`backups/` in the runtime root with `database.pgdump.sealed`,
`files.tar.sealed` and `manifest.json`; the sets kept are those the
[retention](#retention) rules below name. A failed run leaves its directory
without a manifest for inspection; it never counts toward retention and is
not removed, so delete it by hand once the cause is understood.

### Retention

Each run, after recording its set, removes the complete sets on the host
that these rules do not keep, and the
[off-site copy](#off-site-copies-to-google-drive) applies the same rules to
its set folders. A set's date is its name: the run's start time in UTC. The
rules look only at dates, never at why a set was taken, since a scheduled,
pre-upgrade and manual backup are the same kind of set. Kept are:

- every set from the last seven days, so a day of deploys or manual runs
  does not push out the scheduled sets at once;
- the newest set of each of the last 30 UTC days, today included;
- the newest set of each of the last 12 UTC months, this month included;
- the newest fourteen sets, however old.

These are the operations specification's
[30 daily and 12 monthly](../specs/stewardship/operations/spec.md) defaults,
plus the seven-day window and the fourteen-set floor. The daily and monthly
sets are each day's and month's last, so once a day or month ends the set
kept for it never changes. A restore does not pin a set: stop the backup
cron before restoring, as the restore steps below say, so no run prunes
meanwhile.

The host and Drive share one clock, so a clock that jumps would thin both
at once. A run therefore prunes nothing when the newest set is more than
two days after the one before it, or when a set is dated more than an hour
after the current time. It logs a WARNING `task_failed` line whose
`failure_kind` names the check that fired:
`backup_retention_paused_gap` for the gap (the clock jumped forward, or
backups stopped for days) and `backup_retention_paused_future_set` for a
set dated after the clock. Check the host clock. After fixing a clock that
ran ahead, move the future-dated sets (their names are later than the
current UTC time) out of `backups/` on the host and out of the Drive
folder, or delete them; until then every run pauses, since a set is still
dated after the clock. Otherwise the next run prunes normally once its set
is close to the previous one, and the fourteen-set floor bounds what a
clock that stays wrong can remove.

Get a copy of every set off the host, either with the application's
[off-site copies to Google Drive](#off-site-copies-to-google-drive) or with
`rsync` or `rclone` from the same cron job to the parish's own off-host
storage; the sealed files are safe to store anywhere, and the manifest holds
no secret.

## Off-site copies to Google Drive

An Administrator can turn on off-site copies from the portal: Integrations,
then **Off-site backups (Google Drive)**, or during initial setup, in its
optional **Off-site backups** step, which queues one Test access check of
the folder when setup completes. After each successful backup, the
same `backup-worker` run uploads the new set's three files (the two sealed
files and `manifest.json`, never anything unencrypted) into a subfolder named
like the set, inside the Drive folder the Administrator chose. It then moves
the subfolders [retention](#retention) does not keep to the Drive trash; it
never touches a file or folder it did not create. A subfolder from the last
seven days is never trashed. Older subfolders count only if they hold all
three files and the database records the set's copy as `uploaded` (and so
verified): the daily and monthly sets are chosen among those, so a failed
or partial copy never pushes a good one out. An older subfolder missing a
file is trashed. An older complete one with no recorded copy is left alone,
since it may be a copy that failed verification or one whose row a
restored database lacks; delete it by hand once a verified copy of that
day exists. The host and the Drive folder may therefore keep different sets
for a day whose newest set failed to copy: the host keeps that set and
Drive keeps the day's newest verified copy, so each day in the window still
has a set in each place. Only one run copies and prunes at a time: a run
that finds another still copying (a lock file, `.offsite-copy.lock`, in
`backups/`) leaves the copy to it, and its JSON line's `offsite` field says
`busy`; the run holding the lock looks for new sets once more after its
uploads, so a set left to it this way is copied in the same run. The
`smoke --target backup_drive --send` check takes the same lock and reports
`busy` while a copy is running. Each subfolder is tagged
with the deployment's identity, so two deployments pointed at the same folder
(for example a validation server and production) each keep and prune only
their own sets. Even so, give each deployment its own folder: a shared one
mixes both deployments' sets and makes a restore easier to get wrong, and a
deployment restored from another's backup (for example a validation server
seeded from a production backup) carries the same identity and so the same
tag, so the two would prune each other's sets. Set folders created before
this tagging keep the old tag: they are never pruned, and the first copy
after the change writes a new folder of the same name beside any old one
(which may be a partial copy). Delete the old-tag folders by hand once newer
copies exist. The run's JSON line gains
an `offsite` field (`uploaded`, `failed` with a category, `busy`, or
`not_configured`), and a failed copy never fails the backup itself: the local
set is recorded as usual, and the next run copies any of the three newest
sets not yet in the folder. A failure that belongs to one set (its copy did
not verify, or Drive stayed unreachable through the retries) is recorded
and the run moves on to the next set; any other failure (the key, the
delegation or the folder) stops the run, since every set would fail alike.

The copy never blocks the Admin or Family portals: it runs only in this
one-shot profile, after the backup has released its startup lease, and
outside any database transaction, so it holds no work-order or row lock
while files are in flight. Every request has a timeout, retries are
bounded, and the whole copy stops after four hours, an upload still sending
included (a network operation stalled at that moment can hold it up to five
minutes longer); a slow or failed
copy only records its outcome for the pages and the alert below.

The copy acts as the Google Workspace mail integration's delegated mailbox
user, through the same service account key, so Google needs one-time setup
by a Workspace administrator:

1. In the Google Cloud project that owns the service account, enable the
   **Google Drive API**.
2. In the Google Admin console, under Security → Access and data control →
   API controls → Manage domain-wide delegation, edit the service account's
   client ID and add the scope `https://www.googleapis.com/auth/drive`
   alongside `https://mail.google.com/`. Changes can take several minutes
   to take effect. Domain-wide delegation lets the key act as **any** user
   in the Workspace domain, so with this scope it can reach every user's
   Drive, not only the delegated mailbox user's (the mail scope already
   reaches every mailbox the same way). Keep this service account in a
   Google Cloud project used for nothing else, limit who can create or
   download its keys, and replace a key that may have been exposed.
3. Create the backup folder, preferably in a **shared drive** so it does not
   belong to one person, and add the delegated mailbox user to that shared
   drive as **Content manager** (a My Drive folder owned by that user also
   works).
4. In the portal, paste the folder's link, select **Test access**, and save
   once the test succeeds. The test writes one small file and trashes it
   again, as the delegated user; the Google Workspace credential installer
   runs it, since the web application has no key and no network access.

The Backups page and the administration home show when a set was last
copied. The scheduler raises the `backup_offsite_failed` operational incident
(CRITICAL) when the newest copy attempt failed, when one of the three newest
sets tried still has a failed copy of its own (the Backups page then names
it, even though newer sets were copied), or when a backup finished
more than six hours ago and its copy recorded nothing (the copy was killed,
lost its database connection or could not read the saved folder link before
it could record an outcome). That holds from the first backup after the
folder is first configured or turned back on, before any copy has succeeded;
a backup taken while copies were off is not expected on Drive. It resolves
the incident on the next successful copy or when off-site copies are turned
off. The page names the cause in plain language. The process log's
WARNING `task_failed` line with `failure_kind` `backup_offsite_failed`
carries only the category, as `drive_failure` (`authorization` for a
missing Drive scope, `api_disabled`, `not_found`, `permission`,
`credential`, `verification`, `unavailable` or `unexpected`).

Before uploading a set, the copy checks each sealed file against the
SHA-256 the set's manifest recorded when it was written. A set that no
longer matches (a failing disk, or a file changed by hand) is never
uploaded: its copy is recorded as `verification`, an ERROR `task_failed`
line names `failure_kind` `backup_set_mismatch`, and the copy moves on to
the next set. That set on the host is damaged and cannot be restored;
check the host's disk, and do not rely on it.

A copy stopped by a time limit is recorded, and shown on the page, as
`unavailable`, like a Drive outage. What tells the two apart is a WARNING
`task_failed` line logged when the limit stops the work, whose `timeout`
names the limit, with `limit_seconds` and `elapsed_seconds`:
`drive_copy_budget` (the four hours ran out: no new set or request
starts, a request's timeout is cut to the time left, and an upload still
sending stops, or a network operation that stalled past them timed out), `drive_retry_budget` (a retry refused because it would
start after those four hours), `drive_request` (one Drive request passed
its own timeout and may still be retried) or `drive_probe_wait` (a **Test
access** check waited more than five minutes and was closed unanswered;
this line comes from the Google Workspace credential installer). The
Drive request timeouts (one minute, or five minutes for each wait while
a file uploads, 15 seconds for a **Test access** check) apply to each
network operation, not to a whole upload, which only the four-hour budget
bounds. These lines are in the process
log of the `backup-worker` run (the cron job's output) or of the Google
Workspace credential installer, and each is also written to the durable
operational log in the database, as `work_budget_reached`
(`drive_copy_budget`, `drive_retry_budget`) or `task_timed_out`
(`drive_request`, `drive_probe_wait`) with the same limit and elapsed
seconds.

To check the setup by hand from the host, run the smoke check in the backup
profile; `--send` also uploads the newest complete local set, into a set
folder tagged with this deployment's identity as a real copy would, so the
next backup's copy finds it complete and reuses it and retention prunes it
like any other:

```text
docker compose ... run --rm --entrypoint pk-stewardship backup-worker \
  smoke --config SERVICE_CONFIG --target backup_drive \
  --delegated-email ADDRESS --folder-link LINK [--send]
```

`SERVICE_CONFIG` is the `backup-worker` service configuration the Compose
file already passes to the profile. The
[smoke tools guide](stewardship-smoke-tools.md) describes its output.

### The off-site uploader's login

The dedicated off-site uploader
([#235](https://github.com/epiphany40223/parishkit/issues/235)) is being
added in steps. This release adds its login,
`pk_stewardship_backup_uploader`, its one-shot `backup-uploader` profile
(its own offline network plus `application-egress`, never `backend`) and
its source rule. It copies nothing yet: the backup profile's own copy above
is still the off-site copy. The profile's command is the source check, and
`smoke --target backup_drive` runs there probe-only (`--send` is refused
until the copy arrives):

```text
docker compose ... run --rm backup-uploader
docker compose ... run --rm --entrypoint pk-stewardship backup-uploader \
  smoke --config UPLOADER_CONFIG --target backup_drive \
  --delegated-email ADDRESS --folder-link LINK
```

`UPLOADER_CONFIG` is the `backup-uploader` service configuration
(`config/services/backup-uploader.yaml`). The login's password file is an
ordinary runtime password, `credentials/database/backup-uploader` unless
`postgres.password_files` moves it. Two scripts support only some of those
moves, and both refuse before they stop anything: the upgrade script's
password check mounts only the runtime root, so a file outside it refuses
the upgrade; `pk-restore` keeps only a file at the default path, so for an
image with the uploader it refuses a deployment YAML or rendering that
moves it (restore such a deployment by hand). It is the first login an
existing deployment gains with a password: the upgrade that brings it
creates the file (`retarget-image`) and then the login
(`database-grants`). Because a dump holds no roles, the file and the
cluster's stored password can disagree after a same-host restore of an
older set or a lost file, so every check compares the file with the stored
SCRAM verifier: the upgrade script before it stops anything,
`database-grants` before any grant, and
`pk-stewardship uploader-password-check` by hand (below). A mismatch names
the recovery below.

### Uploader lost-file recovery

Use this when the uploader's password file is lost, or no longer matches
its login (the upgrade script, `database-grants` or `pk-restore` says so).
Nothing else changes, and no other login's password is touched.
`retarget-image` and `database-grants` are offline commands: they need
the startup lock that every running service holds shared, so they refuse
while any of them runs.

1. Confirm that a backup completed within the last 24 hours (the System
   health page, or `stewardship-backup.log`), or take one now by hand (see
   [The nightly backup](#the-nightly-backup)). Step 7's `database-grants`
   refuses without one, and it runs with the services already stopped.
2. Comment out the uploader's cron line, if installed, and the backup's
   cron lines (the scheduled and the request one), so that no one-off run
   contends for the startup lock while the services are down.
3. Stop every online service except caddy, postgres and valkey, as an
   upgrade does in its steps 1 and 2: first the background services
   (every running service but web, caddy, postgres and valkey), then web.
   Caddy keeps serving the maintenance page. The running list is
   `docker compose ... ps --services --status running`; stop by those exact
   names with `docker compose ... stop NAME ...`.
4. Drop the login, as the operator through the socket. It owns nothing (its
   grant admission refuses ownership), so `DROP OWNED` only revokes its
   grants in this database and its `CONNECT`; its outcome rows stay:

   ```text
   docker compose ... exec -T postgres psql --username pk_stewardship_operator \
     --dbname DATABASE -v ON_ERROR_STOP=1 \
     -c 'DROP OWNED BY pk_stewardship_backup_uploader' \
     -c 'DROP ROLE pk_stewardship_backup_uploader'
   ```

5. Move any stale password file out of the credentials tree (for example
   to a root-only directory), so nothing is at its path.
6. Run `retarget-image` with the image the deployment already runs (the
   rendered Compose file's `web` image), as the
   [deployment runbook](stewardship-deployment-runbook.md) shows. It
   creates the absent file and reports it as `passwords_created`.
7. Run `database-grants`, which creates the login from that file (it
   needs the recent backup of step 1):
   `docker compose ... run --rm database-provision database-grants --config PROVISION_CONFIG --confirm-deployment UUID`.
8. Prove the login: `docker compose ... run --rm backup-uploader` (its
   source rule) must report that the sources match, and the password must
   match the stored verifier:

   ```text
   docker compose ... exec -T postgres psql --username pk_stewardship_operator \
     --dbname DATABASE -At \
     -c "SELECT rolpassword FROM pg_authid WHERE rolname='pk_stewardship_backup_uploader'" |
   docker run -i --rm --init --network none --user 10001:10001 --read-only \
     --cap-drop ALL --security-opt no-new-privileges:true \
     --mount type=bind,source=RUNTIME_ROOT,target=RUNTIME_ROOT,readonly \
     --mount type=bind,source=/path/to/deployment.yaml,target=/run/operator.yaml,readonly \
     IMAGE uploader-password-check --config /run/operator.yaml
   ```

   It prints only `match` or `mismatch`, never the file or the verifier.
9. Start the online services (web, then the background services that step
   3 stopped), and put back the cron lines that step 2 commented out.

During a restore that stopped in step 7 (below), do only steps 4 and 5,
then resume with `pk-restore --from 7`: its retarget creates the file and
its `database-grants --after-restore SET_NAME` creates the login. A by-hand
`database-grants` would refuse there, since the restored database holds no
recent backup row.

### Stopping a running uploader

A restore or drill must not start while an uploader run is in progress.
When one is running and must be stopped: comment out the uploader's cron
line, if installed; find the container with
`docker ps --filter label=com.docker.compose.service=backup-uploader`; and
wait for it to exit, or stop that exact container by name with
`docker stop NAME` (never by pattern). Then run the restore.

## Checking

The scheduler raises the `backup_rpo_breach` operational incident, through
the configured alert routes, when no backup has completed in the last 24
hours, once the deployment is in Production or has ever backed up, and
resolves it when one has. When it fires: run the backup by hand. If it
refuses, the log names only categories, so check the usual causes in
turn: the recipient key file is present and readable; the `backups`
directory is owned by `10001:10001` with mode `0700`; the authority store
lies inside the archived trees; no offline work (a migration or an upgrade)
holds the startup lock; the database schema matches the running image (an
image changed without its migration refuses; in the middle of an upgrade,
the [deployment runbook's migrate step](stewardship-deployment-runbook.md#upgrade)
says how to take the backup under the previous image); the configuration,
credentials and media trees hold only regular files and directories (no
symlink) and stay under 256 MiB together; and, when a
`failure_kind` is `database_unavailable` (the command's own connection,
before the dump) or `backup_dump_failed` (the dump itself), that the
database is running and reachable and the backup login's password file
still matches (the health command in the web container checks the database; the
[operator diagnostics ledger](stewardship-operator-diagnostics-reviews.md)
records how these log values were checked). A `failure_kind` of
`database_write_refused` is not an outage: the database answered, but a
constraint or guard refused the write. It can clear on the next retry (an
expired lease or an unreleased gate); if it repeats for the same task, the
data or the code needs a look. The backup also refuses when
it does not run under its own profile and database login (a changed Compose
file, a root user, a writable root filesystem or an extra or writable
mount), so rerender with `retarget-image` if the Compose file was edited
by hand.
Fix the cause, run it again, and
confirm the off-host copy holds the newest set's three files (for Google
Drive, the Backups page shows the newest copied set). The
[gate round 3 ledger](stewardship-gate-round3-fixes-reviews.md) records how
this checklist was checked against the code.

### Checking the kept keys

Every backup succeeds, is copied and stays green whether or not a kept
private key can open it: only opening a set proves that. After the
pre-activation drills, check the kept keys every three months, and again
whenever the `backup_recipient_changed` warning appears or a key copy moves
to new storage. Download the newest set's folder from the Google Drive
folder (or copy it from the host) to the machine that holds the keys, and
run [Restore for real](#restore-for-real) step 2's `backup-open` on
`database.pgdump.sealed` once per kept copy of the private key, each time
into a new, empty output directory. Each copy must open the file and print
the `recipient_fingerprint` recorded with the key. Record the date, the set
name and which copies opened it in the parish's operations notes, then
delete the decrypted files. A copy that is refused is not the installed
public key's pair: find out why before relying on any backup.

## Restore drill

**Never back up from a disposable drill host.** A set restored onto a second
host carries the source deployment's off-site Drive folder, its Google
Workspace key and its deployment identity, so the copy's tag is the same.
One `backup-worker` run there, whether by hand, from a copied cron job or by
following step 9, uploads that host's restored state into the source
deployment's live Drive folder and then prunes the source deployment's own
sets as though they were its own. On a drill host, never run
`backup-worker` (request mode included), never install the backup,
request-mode or off-host copy cron jobs, never
run `smoke --target backup_drive`, and skip step 9. Only the same-host run
on the validation deployment itself backs up after the restore.

Three more guards on every drill host:

- **Build it fresh**, from a clean operating system image, never from a
  snapshot or backup image of the live droplet. A clone carries the live
  host's root crontab with its backup jobs, and every service is
  `restart: unless-stopped`, so a clone boots the whole stack on its own
  and its first cron backup uploads into the live folder and prunes it.
- **Check the crontabs before step 4**: `sudo crontab -l -u root` and
  `crontab -l` as the operator user must show no backup, request-mode or
  off-host copy job.
- **Block outbound traffic** as a backstop, so nothing on the host can
  reach Google. A host firewall such as `ufw` does not stop containers,
  because Docker's own rules bypass it. For a manual drill, once step 3 has
  pulled the image, use the provider's firewall (for example a
  DigitalOcean Cloud Firewall on the droplet) and allow outbound traffic
  only for your SSH session. Step 7's `pull` then fails harmlessly: the
  image is already there from step 3. A
  [scripted drill](#scripted-replacement-and-drill-restore) still needs the
  host's own network while it runs: it pulls the image during its checks,
  step 5 may pull the `postgres` and `valkey` images, and its origin check
  resolves the public name. So block container traffic instead, before the
  run, in the `DOCKER-USER` chain. Containers then have no route out, while
  the host's own pulls and DNS still work. Add the provider's firewall only
  after `pk-restore` finishes.

  ```sh
  out=$(ip route show default | awk '{ print $5; exit }')
  sudo iptables -I DOCKER-USER -o "$out" -j DROP
  sudo ip6tables -I DOCKER-USER -o "$out" -j DROP  # where Docker uses IPv6
  ```

  The rule does not survive a reboot or a Docker restart: after either,
  add it again before anything starts.

On a drill host,
[`pk-restore --drill`](#scripted-replacement-and-drill-restore) runs
steps 1 to 8 and enforces the web-only rule itself. It refuses on a host
that holds a deployment, lacks the drill marker or is the public origin's,
and it starts `web` with no route out. The three guards above still apply,
with the `DOCKER-USER` rule as the scripted drill's outbound block.

A drill that only counts rows proves nothing about startup, so the drill
rehearses [Restore for real](#restore-for-real). Before the pre-launch gate,
and whenever the restore procedure changes:

- **Before activation**, run the whole procedure on the validation
  deployment itself, still in Testing mode, restoring its own newest set.
  Its credentials and mail routing are the ones it already uses, so every
  step, including starting the background services, is safe. Then rehearse
  the replacement-host steps too: restore the same set onto a second,
  disposable host that is not on the public DNS, through web's health check
  in step 8, starting `web` alone there (`caddy` cannot obtain a certificate
  for a host that is not on the public DNS), never running `backup-worker`
  or its cron jobs there (see the warning above), and destroy that host
  afterwards. The same-host run skips steps 3 and 5, and a real replacement
  is when they matter. Testing mode has no delivery pause (the controls exist
  only for the Production campaign), so in the same-host run's step 8 keep
  the scheduler, worker, mail-dispatch and installers stopped until the
  comparison is done, then start them.
- **After activation**, never start a second live copy of Production. Use a
  disposable host that is not on the public origin's DNS, run the procedure
  only up to starting web and checking its health in step 8, and never start
  the scheduler, worker, mail-dispatch, installers, `caddy` or
  `backup-worker` there, nor install its cron jobs: the set carries every
  Production credential, the live Drive folder and every Family's data.
  Destroy the host and its disks afterwards.

Whenever CI's operational job runs (a manual dispatch, or a non-draft pull
request whose changed paths can affect the operational scenarios), it also
rehearses most of this automatically (#305), with the image CI builds from
the release image's Dockerfile. That job's Production scenario completes
the setup wizard against fake providers, then, with a throwaway key made by
`backup-keygen`, runs `backup-worker` exactly as the cron job does and
follows this procedure with the same image. Step 1 is partial: it stops
every online service but leaves `postgres` and `valkey` running, with no
cron jobs to disable. Step 2 runs whole (the manifest digest, `backup-open`
of both files with the key's fingerprint, `restore-check` with the dump as
well as the manifest), followed by a
[comparison](#comparing-a-set-with-another-release) that must report
`same`. Step 3 is replaced by emptying the scenario's PostgreSQL data
directory, so the same host stands in for a replacement with a new, empty
PostgreSQL. Steps 5 and 6 run whole. Step 8 starts `web` alone and checks
its health, as on a drill host, and step 10 deletes the decrypted copies.
It skips step 4 (the files never left the scenario's host; it only checks
that the archive holds every tree and the record), step 7 (the set's image
is the one already running) and step 9. It passes only when every table
holds exactly the rows it held at the backup, the setup's invariants hold
again and `web` reports healthy. It does not replace the drill: it never
touches a real host, the kept copies of the private key, Google Drive or a
mail provider.

The drill returns the validation deployment to the backup's moment, so run
it when staff have no unsaved work in progress, taking the backup
immediately before it. Run both pre-activation drills on the deployment
that goes live: if a schema change forces a reinstall before the gate, run
them again afterwards. The gate approves the evidence of both
pre-activation runs. Record the date, the set name, the manifest digests,
the image digest, the `restore-check` report, the set's
`recipient_fingerprint`, which kept copies of the private key opened it and
the outcome in the parish's operations notes, then delete the decrypted
files. To check each copy, run step 2's
`backup-open` once per copy, each time into a new, empty output directory:
`backup-open` refuses to write over an existing file, and that refusal
prints the same generic error as a key that does not match. A copy that
does not match is refused; one that opens the set prints the same
`recipient_fingerprint`.

## Restore for real

A real restore follows the launch scope's
[manual restore procedure](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
and the deployment runbook's [rollback](stewardship-deployment-runbook.md#rollback).
Commands below follow the deployment's usual `docker compose` prefix: the one
rendered Compose file the deployment runs (`compose.json` or
`compose-slack.json`) and its fixed project name. Paths are the default
layout; where the deployment YAML overrides a path, use that path instead.

1. **Stop.** Disable the host's backup and off-host copy cron jobs,
   including the five-minute request-mode entry, until step 9: a backup that starts mid-restore would archive a mix of old and
   restored files, record itself as the newest set and be copied off the
   host. Then stop every online service and `caddy`: `stop caddy web worker
   scheduler mail-dispatch config-installer` and every credential installer.
   Then wait for any backup already running to finish, as a reinstall
   does: `docker ps --filter name=backup-worker` must list no container. A
   backup keeps running after its dump while it copies to Google Drive, and
   one still archiving files when step 4 moves the trees records a mixed set
   as the newest. A copy can run for about four hours (no new set starts
   after that, and an upload already under way can take a little longer),
   so schedule a restore or a drill away from the backup cron times. Leave
   `postgres` and `valkey` running. The scheduler,
   worker and mail-dispatch services stay stopped until step 8. Unlike an upgrade,
   which keeps `caddy` up to show its maintenance page, a restore stops it
   too: step 4 replaces the configuration tree its Caddyfile comes from,
   and it must not keep serving the previous release's static files.
2. **Open the set.** First find the manifest digest the deployment
   recorded for this set, before anything is restored: the restored
   database will not hold it, because a set's own row is written after its
   dump. Take it from the JSON line the backup printed, kept off the host.
   While the source deployment's database still runs (a drill, or a
   rollback on the same host), its record has it too. Run this on the
   source deployment's host, never on a replacement or drill host, whose
   database does not hold the row. `DATABASE_NAME` is the deployment YAML's
   `postgres.name` (`stewardship` unless it sets another), and `set_name` is
   the name of the set's directory:

   ```sh
   docker compose ... exec -T postgres psql --username pk_stewardship_operator \
     --dbname DATABASE_NAME -c "SELECT to_char(started_at AT TIME ZONE 'UTC',
     'YYYYMMDD\"T\"HH24MISS\"Z\"') AS set_name, manifest_digest,
     recipient_fingerprint FROM stewardship_backup_run
     ORDER BY completed_at DESC LIMIT 5"
   ```

   If the host is lost and no printed line was kept off it, nothing proves
   where the set came from; say so in the restore notes. Then, on the
   machine with the private key, confirm the SHA-256 of the set's
   `manifest.json` (`sha256sum manifest.json`, or `shasum -a 256` on macOS)
   equals that digest, and
   decrypt both files with
   `pk-stewardship backup-open --key PRIVATE_KEY_FILE --input database.pgdump.sealed --destination database.pgdump`
   and the same for `files.tar.sealed`. Each prints the kind, size and
   digest, which must match the manifest, and the key's
   `recipient_fingerprint`, which must match the one recorded with the key. From the release image, as for
   [the key](#the-key), mount the key directory and the set's directory
   read-only and an empty private output directory writable:

   ```text
   docker run --rm --network none --user "$(id -u):$(id -g)" --read-only \
     --cap-drop ALL --security-opt no-new-privileges:true \
     --mount type=bind,source=KEY_DIRECTORY,target=/keys,readonly \
     --mount type=bind,source=SET_DIRECTORY,target=/set,readonly \
     --mount type=bind,source=OUTPUT_DIRECTORY,target=/out \
     IMAGE backup-open --key /keys/stewardship-backup.key \
       --input /set/database.pgdump.sealed --destination /out/database.pgdump
   ```

   **Which image opens a set.** Run `backup-open` from the image the
   set was taken under: the manifest's `image` (its `application_version`
   is the release). A different release's image may refuse the set, and
   then prints only that the sealed backup could not be opened. When the
   `manifest.json` beside the file names another release, the refusal
   says which release and image to use instead; if it does, switch images
   before suspecting the key. A set taken before the manifest recorded its
   image is opened with the release the operators' records name for it.
   In a drill, record which image opened each set.

   Copy `database.pgdump` and `files.tar` to the host over a private
   channel.

   Then, still before anything changes, check the set against the image
   you will restore it with: normally the set's own, which its manifest's
   `image` names, or the current release if you mean to keep it. The check
   opens no database and reads no secret:

   ```text
   docker run --rm --network none --user "$(id -u):$(id -g)" --read-only \
     --cap-drop ALL --security-opt no-new-privileges:true \
     --mount type=bind,source=SET_DIRECTORY,target=/set,readonly \
     IMAGE restore-check --manifest /set/manifest.json
   ```

   It prints one JSON report. Exit 0 (`"result": "match"`) means the set's
   applied migrations are exactly the image's, so the services will start
   on the restored database. Exit 3 (`"mismatch"`) lists `not_in_backup`
   (migrations the image has and the set lacks) and `unknown_to_image`
   (migrations the set has and the image lacks), and `use_image` names the
   image the set was taken under: restore with that image instead. Exit 4
   (`"swap_pending"`) means the set lacks only the users swap and what
   depends on it (`not_in_backup` lists them): it was taken by a repeated
   deploy of the users release between that upgrade's two migrations
   ([Users upgrade](stewardship-deployment-runbook.md#users-upgrade-952)).
   Restore it with this image; the restored database is in the same
   swap-pending state, which every online service refuses, but
   `recover-admin` and `upgrade-users` admit, so finish the users upgrade
   on it (its step 4) before any service starts. A set
   taken before the manifest recorded its migrations needs its decrypted
   dump as well: also mount the output directory read-only
   (`target=/out,readonly`) and add `--dump /out/database.pgdump`. Exit 2 is
   a refusal and says why. Keep the report with the restore notes.
3. **Replacement host only: prepare it.** Never run `provision-runtime`
   here: it would generate new passwords that the restored roles and files
   do not have. Bring the operator's deployment YAML and the deployment UUID,
   both kept off the host with the private key (see [the key](#the-key)):
   `retarget-image` rebuilds its
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
   backup was taken under by its complete digest reference, the manifest's
   `image` (for a set taken before the manifest recorded it, the one in the
   operators' notes), `docker pull IMAGE@sha256:DIGEST`: the Compose files
   that name it come back only in step 4. For a real replacement, point the public
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
   records owner-only modes (`0700` directories, `0600` files). Check that
   `media/branding` and `media/hosted-files` came back that way too: the
   service serves a logo or hosted file only when it owns it with mode
   `0600`, and shows any other as missing.
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
   when the container stops. Then mark every restored backup request as
   expired, so request mode never runs a request from before the restore
   (finished as of its own creation, which System health words as
   cancelled by a restore):

   ```sh
   docker compose ... exec -T postgres psql --username pk_stewardship_operator \
     --dbname DATABASE_NAME -v ON_ERROR_STOP=1 -c "UPDATE stewardship_backup_request
     SET state='expired', claimed_at=NULL, finished_at=created_at
     WHERE state IN ('waiting','running')"
   ```

   A set taken under a release without backup requests has no such table,
   and the command reports that the relation does not exist; skip it then.
   Then delete the example Families of a refused large ParishSoft change
   the set holds (a few Family names and DUIDs that System health shows
   while a refusal is current; see
   [Accepting a large ParishSoft change](stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change)).
   The page already hides any whose refusal is no longer current, and the
   worker's hourly maintenance deletes them once the site is released, so
   this only removes them sooner:

   ```sh
   docker compose ... exec -T postgres psql --username pk_stewardship_operator \
     --dbname DATABASE_NAME -v ON_ERROR_STOP=1 -c "DELETE FROM stewardship_source_drop_example"
   ```

   A set taken under a release without example Families has no such table;
   skip it then.
7. **Point at the set's image.** Run `retarget-image` back to the image the
   backup was taken under, in that image, then `pull`. The manifest's
   `image` is its complete reference and `application_version` its release;
   for a set taken before the manifest recorded the image, the operators'
   notes have its digest. The deployment YAML is not in the set, and must
   be readable by the container's user (mode 0644, as Production keeps it;
   `pk-restore` refuses plainly otherwise). If it names a field that
   release does not know, `retarget-image` refuses with no cause, so
   remove such a field first, as the deployment runbook's
   [rollback](stewardship-deployment-runbook.md#rollback) says. Then give
   that image its own static files, on every host (a replacement host has
   no `cache/static` yet, so it starts at the empty one). First move any current `cache/static` aside under a name that does not
   exist yet (moving onto an existing directory nests the tree). Then either
   put back the tree an upgrade kept for the set's release, or create an
   empty `cache/static` owned by `10001:10001` with mode `0700` and run
   `collect-static` into it in the set's image, as the deployment runbook's
   [upgrade](stewardship-deployment-runbook.md#upgrade) does. The static
   tree is not in the set, and a newer release's scripts must not be served
   with the restored release's pages. Then, with every online service still
   stopped, end every Admin automation session the restored database holds,
   since their session files survive on the host:
   `run --rm admin-recovery revoke-automation-sessions --config RECOVERY_CONFIG --reason restore`
   (see the [Admin automation guide](stewardship-admin-automation.md#restore-and-ending-every-session)).
   A set taken under a release without automation sessions has none, and
   its image has no such command; skip it then. Then close the site for
   review, giving the set's name (its directory, the backup's start in UTC):
   `run --rm admin-recovery restore-begin --config RECOVERY_CONFIG --backup-at SET_NAME`.
   It prints the review's id. Families cannot sign in and no Family email is
   sent until an Administrator releases the site in step 8; no Family code or
   link changes. On a disposable drill host it changes only that host's
   copy and keeps Family sign-in and Family mail closed there, so
   [`pk-restore --drill`](#scripted-replacement-and-drill-restore) runs it
   too. Re-running it for
   the same set while its review is open (after a restart, say) changes
   nothing and reports the open review (#799). A set taken under a release
   without the restore review has no such command; follow
   [the limitations](#restore-limitations-in-v1) instead.
8. **Start web alone and review.** **Disposable drill host: start `web` alone, stop
   after web's health check and go straight to step 10.** Starting the
   installers there would let the Google Workspace installer answer pending
   **Test access** checks, which write into the live Drive folder. Otherwise,
   start `web` and `caddy` only, and run the health command. An
   Administrator signs in and lands on the **Restore review** page. There
   they find the emails that may have gone out, compare each send with the
   mail provider's own sent log for the time since the backup, and choose
   "Assume these were sent" or "Send these again" for each. Then they
   release the site, which opens it again as restored; emails nobody decided
   on stay held and are not sent. Only after the release, start
   `scheduler`, `worker`, `mail-dispatch`, `config-installer` and the
   credential installers.
9. **Take a fresh backup.** Never on a disposable drill host: skip this
   step there (see the warning under [Restore drill](#restore-drill)). On the
   deployment's own host, run the backup at once, then re-enable the backup
   and off-host copy cron jobs. The backup records a new run, which later
   upgrade admissions require, and captures the restored state.
10. **Delete the decrypted copies.** Once the review in step 8 is complete,
    securely delete `database.pgdump`, `files.tar` and the staging directory
    on the host and on the machine that holds the private key, and any
    `pk-restore-*` directory left in the PostgreSQL store
    (`run/persistent/postgresql` by default) by an interrupted
    [scripted restore](#scripted-same-host-restore), which deletes them on
    its next run too. They hold
    every Family's data and every credential in plain form; the sealed set
    off the host is the copy to keep. Keep the moved-aside trees only until
    the restored deployment is accepted, then delete them the same way.

### Scripted same-host restore

On the deployment's own host (a rollback to a set, or the same-host drill
on the validation deployment),
[`tools/stewardship-ops/pk-restore`](../../tools/stewardship-ops/pk-restore)
runs steps 1, 2's check, 4, 6, 7 and 8 above in order and stops for the
Restore review (#608). Step 2's opening of the set (on the machine with the
key), 9 and 10 stay manual. For a replacement or disposable drill host, see
[Scripted replacement and drill restore](#scripted-replacement-and-drill-restore).
Run it as root, after copying the decrypted files to the host:

```text
sudo tools/stewardship-ops/pk-restore plan --confirm-deployment UUID \
  --set /opt/parishkit/backups/SET_NAME \
  --database-dump DECRYPTED/database.pgdump --files-tar DECRYPTED/files.tar
```

`plan` prints the steps and runs nothing; `run` with the same options
does them, after asking for the UUID to be typed again. Before anything
stops, it refuses unless:

- the UUID is the database's provisioning marker;
- no crontab (root's, the `sudo` user's, `/etc/crontab`, `/etc/cron.d`) and
  no systemd timer names a backup, `rsync` or `rclone` job (the
  operating system's own `dpkg-db-backup` timer is ignored). Scripts in
  `/etc/cron.daily` and its siblings, anacron, `at` jobs, other users'
  crontabs and jobs on other machines are not checked: disable those
  yourself;
- the decrypted files match the manifest, and the manifest's SHA-256 is
  the digest the deployment recorded for the set;
- the archive holds only the restored trees and the record, and each
  tree's destination is on the runtime root's filesystem (a path override
  elsewhere is restored by hand);
- the pulled image (the manifest's `image`, or `--image` for an older set)
  carries its digest, loads the deployment YAML (`validate-deployment`;
  see step 7 for a field that release does not know) and its
  `restore-check` answers `match`;
- there is room for the extracted files, the converted dump and the load
  (about twice the database's size).

It also collects the image's static files into `cache/static.next` then,
while the site still serves, and deletes plaintext dump copies an earlier
run left in the PostgreSQL store. It moves every replaced tree, and
`cache/static`, aside as `NAME.pre-restore-STAMP`, never deleting one. It
copies the dump into the PostgreSQL store as the database container's own
user, on disk beside the data directory rather than in the container's
memory-backed `/tmp`, converts it, deletes the dump copy, checks the room
again (about twice the database's size, beside the converted SQL), and
deletes the SQL once the load ends. `retarget-image` runs without the
bulk Family send switch, so it is off after the restore. Every Docker call
runs under a time limit, and a kill is logged with what, the limit and the
elapsed time; each step is logged to `logs/restore-STAMP.log` as well.

After a failure it names the step to resume at with `--from STEP` (4, 6
or 7; the checks and step 1 always run, and step 7, which `restore-begin`
makes safe to repeat, also covers a failure in step 8). A failure or a
signal during step 6 stops the copy, conversion or load at once (including
the `pg_restore` inside the container), ends the load's database session
and reports whether the load committed. The proof is the public schema's
comment, `pk-restore:SET_NAME:MANIFEST_SHA256`, which the load's own
transaction sets last, together with no `stewardship_backup_run` row
started at or after the set's start: the comment stays after a restore and
is copied into later backups, but a fresh load holds no such row. A full
run, `--from 4` and `--from 6` need the set's own row (no restored database
holds it) and the set not loaded; `--from 6` also needs step 4's files in
place; `--from 7` needs the set loaded. A database with neither (an older
restore, or a set the deployment never recorded) is refused: restore it by
hand. On the
[local laptop environment](stewardship-local-environment.md) the image is
the set's local build tag, as a local deploy uses: nothing is pulled and
no digest is checked, and a fake-clock deployment is refused. The script's
header comment lists every option. When it finishes, continue with step 8's review and
release, then steps 9 and 10.

PostgreSQL's connection sources (#2111) add two things. In step 7, right
after `retarget-image` and the pull, it compares the database networks the
retargeted Compose file renders with what Docker runs; when they differ (a
set rendered before the source rules, or a restore with an image from
before them) it removes every container of the project but `caddy` and
the networks `caddy` is not on, never a volume, and starts `postgres` and
`valkey` on the rendered networks before any one-off runs, as the
[deployment runbook](stewardship-deployment-runbook.md#database-connection-sources-2111)
describes for the upgrade; the log says `the database networks change`.
It refuses while a one-off container of the project still runs. Then,
when the retargeted file renders the source rules, it reloads them and
runs the image's hba check, which must answer `t` before any one-off
connects (the log says `the rendered source rules are in force`); a failure
names the condition (for example `the mounted pg_hba.conf differs from the
rendered one`) and stops the restore in step 7, to be repeated with
`--from 7` once fixed. After web's health check it runs the hba check
again (`the hba check answers t`) and the source check from `web` and from
`admin-recovery`; a difference (the output names the condition, or the
login, the source and the service) fails the run with `web` still up, and
the Restore review waits for a fix.

The [off-site uploader's login](#the-off-site-uploaders-login) (#235) adds
three things for an image that has it (its command list names
`uploader-password-check`); an older image does none of them. In step 4,
when the set's credentials tree has no uploader password file (a set from
before the login), the host's own file is copied in from the tree just
moved aside (a regular file, never a link, owner-only), or, on a resumed
run, from the newest other `credentials.pre-restore-*` tree that has one;
the log says which. A linked tree or `database/` directory on either side,
or an existing `backup-uploader.copying`, refuses instead. In step 7,
after the retarget, the full stop and the hba reload, it runs
`database-grants --after-restore SET_NAME`: the restored set's load
evidence (the schema comment above, no later backup row, and no released
restore review of that set) stands in for the recent backup
`database-grants` otherwise requires, so an older set is not refused for
its age, and the result names the set. A `--from 7` resume while the
review is still open passes; once the review is released, the site has
run, and a by-hand `database-grants` needs a recent backup again. It
creates a missing login from the file, or checks an existing one against the
cluster's stored password. In step 8 the source check also runs from the
`backup-uploader` profile. A set restored from before a
[lost-file recovery](#uploader-lost-file-recovery) brings back a file that
no longer matches the login, so step 7 refuses with the services down:
follow that recovery's restore note, then resume with `--from 7`.

### Scripted replacement and drill restore

On a new host, `pk-restore --replacement` also runs steps 3 and 5. With
`--drill`, it runs them on a disposable drill host, stopping after web's
health check. It takes the same options as the
[same-host restore](#scripted-same-host-restore), plus
`--manifest-digest`: the manifest SHA-256 from the JSON line the backup
printed, kept off the host. The new host's database cannot hold that set's
row. If no printed line was kept, pass `--manifest-digest unrecorded`; the
log then says that nothing proves where the set came from, and so must the
restore notes. Bring the deployment YAML and the UUID, as step 3 says. The
paths, the database name and the public origin come from the set's own
`config/services/web.yaml`. Its root must be `--runtime-root`, with every
path under it; a set with a path elsewhere, or one provisioned with
`--bind-source-root`, is restored by hand. `--compose` defaults to the
set's `config/services/compose.json`. `--project` defaults to
`stewardship`, the name the deploy tool and `pk-admin` use; pass the lost
host's if it differed.

```text
sudo tools/stewardship-ops/pk-restore plan --replacement \
  --manifest-digest MANIFEST_SHA256 --confirm-deployment UUID \
  --set DECRYPTED/SET_NAME --database-dump DECRYPTED/database.pgdump \
  --files-tar DECRYPTED/files.tar
```

`--set` names a directory called after the set that holds its
`manifest.json`.

Before anything changes, it refuses unless:

- **This host holds no deployment.** No container exists of the Compose
  project, or of any project whose Compose file is under the runtime root
  (a project started without `-p` is named `parishkit-stewardship`). The
  PostgreSQL store is absent or empty, and (on a full run) no `config`,
  `credentials`, media tree or provisioning record is there. A live host
  fails all three, Production included.
- **The UUID is the deployment's.** It must be the id of the set's own
  runtime row, which the image's `pg_restore` reads from the dump with no
  network. Then it is typed again.
- **The manifest's SHA-256 is `--manifest-digest`.**
- **Every other check of the same-host restore passes,** plus
  `database-roles` in the image.

It then:

- **Step 1:** has nothing to stop on a new host.
- **Step 3:** creates the runtime root and its other directories (owned by
  `10001:10001`, mode `0700`) and `run/startup.lock`, then collects the
  static files. It never runs `provision-runtime`.
- **Step 4:** restores the files.
- **Step 5:** starts `postgres` and `valkey` and runs `database-roles`; the
  database's provisioning marker must then be the UUID.
- **Steps 6 to 8:** as on the same host.

The runtime root and its `logs` directory are created first when missing,
to hold the log. A refusal names them as the only change. A failure names the step to resume at: `--from` 4, 5, 6
or 7. A failure in step 3 runs again without `--from`. `--from 5` and
`--from 6` need step 4's provisioning record in place and an empty
application schema (or, for 5, an empty store). `--from 7` needs the set
loaded, as on the same host. A database that holds anything else is refused.
A resume also refuses any container of the project other than `postgres`,
`valkey`, `web` and (not drilling) `caddy`, and any other project's
container from the same root.

**Drill host (`--drill`).** Use it only on a disposable host built fresh
for the drill (see [Restore drill](#restore-drill)). Mark that host as one
before the run, with its own name:

```sh
sudo mkdir -p /etc/parishkit
echo "parishkit-restore-drill-host $(hostname)" | sudo tee /etc/parishkit/restore-drill-host
```

Never create that file anywhere else. It sits outside the runtime root, so
no backup carries it. Then add the `DOCKER-USER` rule from
[Restore drill](#restore-drill) before the run. `--drill` also refuses
when:

- the file is missing, is not owned by root, or names another host;
- this host's name is the set's public origin host;
- that name does not resolve (`getent ahosts`), since the check cannot
  tell then, or resolves to any of this host's addresses (`hostname -I`).

`hostname -I` lists only the interfaces' own addresses. A public address
the provider maps in by NAT, such as a reserved or floating IP, is not
seen, so the address check is a backstop. The drill marker is the guard.

All of the replacement checks above apply as well. Only `postgres`,
`valkey` and `web` are ever created:

- **Step 7** skips `pull`: the image is already there from the checks.
- **Step 8** creates `web`, disconnects it from every network that is not
  internal (`application-egress`), and checks that only internal networks
  remain and no port is published. Only then does it start `web`, so it has
  no route to a mail provider, Google or ParishSoft. It checks both again
  once `web` runs. A refusal after `web` was created removes it.
- **`restore-begin` still runs.** It closes Family sign-in and holds Family
  mail on this copy.
- **The uploader's source check** (an image with the
  [off-site uploader](#the-off-site-uploaders-login)) runs a short
  `backup-uploader` one-off, which joins `application-egress` and mounts
  the Workspace key like any uploader run. The `DOCKER-USER` rule blocks
  its egress, and it runs only `database-sources`.

`caddy`, the scheduler, the worker, mail-dispatch, the installers and
`backup-worker` are never created. After web's health check, it refuses if
any other container of the project exists. Record the evidence, then go
straight to step 10: destroy the host and its disks.

## Comparing a set with another release

Fail forward stays the default, and a set is restored with its own image.
Only when the Administrator asks to consider restoring a set onto a
release with a different schema (when `restore-check` reports a mismatch
and the set's own image is not wanted) does the operator compare the two
schemas first. `restore-compare` loads the set into a scratch PostgreSQL
server, migrates a second scratch database with the target image and
copies it through the same dump and load (so both sides print their
definitions alike), and prints what differs. It never connects to the deployment's database, changes
nothing on the host and transforms no data.

Compare only a set whose origin step 2 proved: its `manifest.json` digest
matched the one the deployment recorded, and `backup-open` opened it with a
kept key. The load runs the dump's SQL as the scratch server's superuser,
which can run programs inside that container, so a dump of unknown origin
must never be loaded, even in scratch.

Run it on the machine that holds the decrypted dump from step 2 (never the
deployment's host if you can avoid it, and never with the deployment's
networks attached). The scratch server is the pinned PostgreSQL image on
memory-backed storage, reachable only on an internal network, with only the
capabilities its entrypoint needs to set up its data directory and drop to
its own user:

```sh
docker network create --internal pk-restore-scratch
docker run -d --name pk-restore-scratch-pg --network pk-restore-scratch \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --cap-add SETGID --cap-add SETUID --security-opt no-new-privileges:true \
  --tmpfs /var/lib/postgresql -e POSTGRES_PASSWORD_FILE=/run/pw \
  --mount type=bind,source=PASSWORD_FILE,target=/run/pw,readonly \
  POSTGRES_IMAGE
docker run --rm --network pk-restore-scratch --user "$(id -u):$(id -g)" \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --mount type=bind,source=OUTPUT_DIRECTORY,target=/out,readonly \
  --mount type=bind,source=PASSWORD_FILE,target=/run/pw,readonly \
  IMAGE restore-compare --dump /out/database.pgdump \
    --scratch-host pk-restore-scratch-pg --scratch-password-file /run/pw \
  > compare-report.json 2> compare-log.jsonl
```

Keep both output files with the restore notes: the report, and the log,
whose timeout lines say what was stopped, its limit and how long it ran.

`POSTGRES_IMAGE` is the PostgreSQL reference the deployment's Compose file
pins, `IMAGE` the target release and `PASSWORD_FILE` a new owner-only file
holding a random password. The command refuses a server that holds any
database but a fresh server's own (so it cannot run against the deployment's
cluster) and a login that is not that server's superuser. It loads the dump
without owners, privileges, subscriptions or publications, in one
transaction, stopping at the first error, within 30 minutes. Each migration
statement may run 10 minutes, each catalog query 1 minute and each row count
5 minutes. A kill at any of these limits is logged with the limit and the
time the step ran, and the comparison is refused.

It prints one JSON report and exits 0 when the schemas are the same, 3 when
they differ and 2 when refused. The report lists the migrations each side
lacks, the tables and columns only in the set or only in the image, the
columns whose type, nullability, default, identity, generated expression or
collation changed, and, under `objects`, for each kind of schema object
(constraints of every kind, indexes, functions by the digest of their full
definition, triggers, row-level security policies and switches, views,
sequences, each table's persistence and storage options, and domains and
enum types) the names only in the set, only in the image, or defined
differently. It ends with row counts for the set's tables that differ.
"Same" means every one of those matches. Not compared: owners and
privileges (`database-grants` sets them on the deployment), schemas other
than `public` (Stewardship has none), extensions, and composite and range
types (the schema defines none). The report holds names, definitions and
counts, never row values.

The two scratch databases are dropped afterwards. `--keep` leaves them (the
report names them) for an operator-supervised session, in which the
operator, or an LLM working with the operator, plans from the report and
experiments there. A kept database is a superuser copy of every Family's
data:

- **An LLM sees only the report and the catalog** (table, column and object
  definitions). It never queries rows or reads values, unless the
  Administrator has explicitly approved that one query beforehand.
- **The operator stays at the keyboard** for the whole session and runs
  every statement.

Anything beyond the comparison is a separate decision:

- **The Administrator approves** any plan that restores a set onto another
  schema, before it touches the deployment.
- **A data transform** runs only against the scratch copy until it is
  proved, and then as an operator-run step, never automatically.
- **Before switching**, the transformed database is verified. Verified
  means both of these:
  - a `pg_dump --format=custom` of it, compared with `restore-compare`
    against the target image on a fresh scratch server, exits 0 (every
    migration, table, column and schema object above matches);
  - on the deployment, after the restore, `web` starts (every service
    refuses a database whose migrations are not exactly its image's, and
    whose history is inconsistent) and `docker compose ... exec -T web
    pk-stewardship health --config WEB_CONFIG` passes.

  A fresh backup follows at once.
- **No Family code, link or credential** is created, replaced or cancelled
  (the hard rule for emailed credentials).

Destroy the scratch server and its network afterwards
(`docker rm -f -v pk-restore-scratch-pg`, `docker network rm
pk-restore-scratch`): it holds the set's data in plain form.

## Restore limitations in v1

A restore returns the deployment to the backup's moment. With the restore
review (`restore-begin` and the Restore review page), the site stays closed
until an Administrator releases it, and no email that may have gone out after
the backup is sent again unless they choose "Send these again". These
limitations remain:

- **Work after the backup is lost.** Submissions, Admin edits and deliveries
  recorded after the backup are not in the restored database. A Family whose
  submission was lost must submit again.
- **A link emailed after the backup may be unknown.** A restore never changes
  a Family's code or link, but a link created after the backup was taken is
  not in the restored database. "Send these again" sends that Family the link
  the backup holds. To keep this window small, run a backup by hand right
  after each large send as well as nightly.
- **An undecided invitation keeps the Family's reminders back.** After
  release, emails nobody decided on stay held, and the Restore review page is
  gone. Home then links to the **Held emails** page (under Mail and Family
  portal), where an Administrator decides them the same way (#757). The
  release preview says how many Families this affects.
- **Mid-hand-off emails are not held.** An email the provider was being given
  at the backup is settled from the delivery warning on Background work after
  release.
- **A backup taken in Testing.** Restored after go-live, it releases into
  Testing, and the next go-live invites every Family again.

A set taken under a release without the restore review (before migration
0017) cannot be closed for review. Then, at step 8, an Administrator pauses
delivery on the campaign's **Pause and resume mail** page before the
background services start, compares the restored deliveries with the
provider's sent log, and resumes delivery deliberately. Families can sign in
during that review, and mail the provider accepted after the backup can be
sent again.

## Known v1 limitations

- The backup is consistent for the database (one `pg_dump` snapshot) but the
  configuration, credentials and media archive is taken after it, not
  atomically with it; all three change rarely and by operator or Admin
  action.
- Off-host copy other than to Google Drive, the escrow workflow and
  automated restore are the operator's by hand; the deferred remainder is
  listed in the launch scope.
- Replacing the key changes only the key new backups are sealed to: sets
  already made are not re-encrypted, and old private keys are the
  operator's to keep and retire by hand.
- The set covers the default locations: the `config`, `credentials` and
  `media` trees and the provisioning record. A deployment that overrides an
  individual credential or password file to a path outside the credentials
  tree must copy that file off the host itself. An authority store outside
  the archived trees is still a valid deployment, but its backup run refuses.
- The sealed files are anonymous encryption to the public key: they prove
  they were not altered, not who made them. The recorded manifest digest,
  kept off the host, is the origin check; there is no host-held signing key.
