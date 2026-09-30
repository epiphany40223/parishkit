# Stewardship deployment runbook

This runbook is the operator's path from an empty Linux host to a running
Stewardship deployment, and from one release to the next. It is the
deployment, first-install and upgrade runbook the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
requires (item 3). It orders and cross-links the normative procedures rather
than restating them: the [runtime operator guide](stewardship-runtime.md) owns
each command's exact behaviour and refusals, the
[deployment settings reference](../development/stewardship-deployment.md) owns
the YAML fields, and the [release image guide](stewardship-release-image.md)
owns how the image is published and retargeted. Where this runbook and a
linked guide disagree, the linked guide is right; fix the runbook. Outages,
delivery pauses and unknown deliveries during the campaign are the
[launch runbooks](stewardship-launch-runbooks.md); backup and restore are the
[backup runbook](stewardship-backup-runbook.md).

Nothing here authorizes a release, a deployment or a Production activation by
itself. The human pushes release tags, installs on the production host and
approves the launch at the pre-launch gate.

## Before you start

The host is one Linux virtual machine with a supported Docker Engine and the
Compose plugin, reachable from the Internet on TCP 80 and 443 only. Everything
else (SSH, the Docker socket, PostgreSQL, Valkey, the application's port 8000)
stays closed at the host firewall; the rendered topology publishes no other
port. Create the DNS `A` record for the public hostname before the proxy
starts, because Caddy obtains its certificate from Let's Encrypt on first
start and needs the name to resolve to this host. Publish no `AAAA` record:
the rendered ingress network has no IPv6, so Docker would forward every IPv6
visitor from one bridge address, and the per-address sign-in limits would
treat all of those Families as one client and start refusing them.

Collect, outside the runtime root and outside the repository:

- The **image digest** of the release to install, copied from the GitHub
  Release's notes as the complete
  `ghcr.io/<owner>/<repository>/stewardship@sha256:<64 hex>` reference. The
  release workflow writes that line when the human pushes a `vX.Y.Z` tag; a
  tag such as `:1.2.3` is never deployed, only the digest. The host pulls it
  without a registry credential, so the package must be public, a one-time
  step after the first release (see the
  [release image guide](stewardship-release-image.md#the-image-is-published-by-the-release-tag-and-only-then)).
- The **deployment YAML**: schema version 1, `profile: production`, the HTTPS
  `public_origin`, `trusted_proxy_hops: 1`, and the absolute `paths.root`.
  Every field, default and validation rule is in the
  [settings reference](../development/stewardship-deployment.md#schema-version-1).
  Keep it in an operator-controlled place, with a copy off the host beside
  the backup's private key: restoring onto a new host needs it, and no
  backup set holds it. It holds no secrets. The offline
  commands read it as UID `10001`, so it must be readable by that user (for
  example mode `0644`). Set `operational_alerts` now: a later change needs a
  reinstall.
- The **Google OAuth client** (web application type) whose authorized
  redirect URIs are on the public origin, exported as JSON with only
  `client_id` and `client_secret`, with the authorized redirect URI
  `<public origin>/admin/oauth/callback`; the initial Administrator's Google
  address; the ParishSoft API key and its organization; the Google Workspace
  mail account; and, optionally, a Slack bot token and channel ID. All but the
  OAuth client are entered through the setup wizard and installed by the
  credential installers, not copied onto the host by hand.
- The **Google Workspace mail account**: a service account with a JSON key,
  authorized for domain-wide delegation with the scope
  `https://mail.google.com/`, and the real mailbox it sends as (the
  delegated address). The
  [README's Google setup](../../README.md#google-cloud-and-google-workspace)
  walks through the Cloud project, the service account, the delegation and
  the delegated user; stewardship needs only the Gmail scope above, plus
  `https://www.googleapis.com/auth/drive` if the optional
  [off-site backup copies](stewardship-backup-runbook.md#off-site-copies-to-google-drive)
  are turned on.
- A newly generated deployment UUID, recorded where the operators keep it.
  Every offline command that confirms the deployment takes the same UUID.
  Each installation, including a reinstall from scratch, gets a new UUID; a
  restore reuses the UUID of the deployment it restores.
- A fixed Compose project name for this deployment.

## Commands and generated paths

The runbooks write Compose commands as `docker compose ... ARGS`, where
`...` is the deployment's one rendered Compose file and its fixed project
name:

```text
docker compose -f RUNTIME_ROOT/config/services/compose-initial.json -p PROJECT ARGS
```

Use `compose-initial.json` until the setup wizard finishes, then
`compose.json`, or `compose-slack.json` when Slack is configured; always
exactly one file, never an overlay. `provision-runtime` writes the
per-service configuration each command's `--config` names as
`RUNTIME_ROOT/config/services/SERVICE.yaml`: `WEB_CONFIG` is `web.yaml`,
`PROVISION_CONFIG` is `database-provision.yaml`, `BOOTSTRAP_CONFIG` is
`bootstrap.yaml`, and a smoke check's `SERVICE_CONFIG` is the configuration
of the service it runs in. `docker compose ... config` shows the exact
paths and the credential installer services (named
`credential-installer-TARGET`). Provisioning also creates the empty
credential directories; the Google OAuth client goes in
`RUNTIME_ROOT/credentials/google_oauth/credential` and the backup public key
in `RUNTIME_ROOT/credentials/backup_data/credential` (both `10001:10001`,
mode `0600`), unless the deployment YAML overrides those paths.

The offline commands `provision-runtime`, `collect-static` and
`retarget-image` run in the application image with no network and nothing
writable but what they need. For `provision-runtime` and `retarget-image`,
the runtime root is mounted read-write at its own path and the deployment
YAML read-only:

```text
docker run --rm --init --network none --user 10001:10001 --read-only \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777 \
  --mount type=bind,source=RUNTIME_ROOT,target=RUNTIME_ROOT \
  --mount type=bind,source=/path/to/deployment.yaml,target=/run/operator.yaml,readonly \
  IMAGE provision-runtime --config /run/operator.yaml --image IMAGE
```

The image's entry point is `pk-stewardship`, so the subcommand follows the
image. `retarget-image` uses the same line with
`retarget-image --config /run/operator.yaml --image NEW_DIGEST`, run in the
new image. `collect-static` needs no YAML; mount only the empty static
directory read-write:

```text
docker run --rm --init --network none --user 10001:10001 --read-only \
  --cap-drop ALL --security-opt no-new-privileges:true \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,mode=1777 \
  --mount type=bind,source=RUNTIME_ROOT/cache/static,target=RUNTIME_ROOT/cache/static \
  IMAGE collect-static --destination RUNTIME_ROOT/cache/static
```

A store or credential path that the deployment YAML overrides to a place
outside the runtime root needs its own read-write bind mount at the same
path. When the runtime root lives in a Docker named volume instead of a host
directory, mount that volume in place of the runtime root's bind mount
(`--mount type=volume,source=VOLUME,target=` a parent of the configured
root, in both forms) and add `--bind-source-root DAEMON_RUNTIME_ROOT` to
`provision-runtime`, as
[Initial preparation](stewardship-runtime.md#initial-preparation) says.

## First installation

Run the steps of the [runtime guide](stewardship-runtime.md) in this order.
Each step is idempotent in the sense the guide describes: a repeated command
either resumes with the same inputs or refuses; none deletes or adopts data.

1. Create the empty runtime root, owned by UID/GID `10001:10001`, and follow
   [Storage and identities](stewardship-runtime.md#storage-and-identities).
2. Run `provision-runtime` with the deployment YAML and the release digest, as
   [Initial preparation](stewardship-runtime.md#initial-preparation)
   specifies. It renders `config/services/compose-initial.json`,
   `compose.json` and `compose-slack.json` under the runtime root together
   with every per-service configuration; those rendered files are the only
   production Compose files. Then run `collect-static` into the empty static
   destination and install the Google OAuth client document at its
   credential path.
3. Follow the numbered steps of
   [First database and application startup](stewardship-runtime.md#first-database-and-application-startup)
   with `compose-initial.json` and a fixed project name: dependencies,
   `database-roles`, bootstrap `prepare`, `migration`, `database-grants`,
   bootstrap `import`, then the online services. Start `caddy` last, once the
   DNS record resolves; it publishes 80 and 443 and obtains the certificate.
4. Check the deployment with `pk-stewardship health --config WEB_CONFIG`
   inside the web container, as
   [Health, credentials and incidents](stewardship-runtime.md#health-credentials-and-incidents)
   describes, and open the public origin: it must serve the sign-in page over
   HTTPS and deny `/health/` and `/metrics`.
5. Sign in as the initial Administrator and run the setup wizard, entering
   the ParishSoft, mail-provider and optional Slack credentials. When the
   installers report `awaiting_ack`, recreate the `worker` and
   `mail-dispatch` services from `compose.json` or `compose-slack.json` and
   acknowledge each request inside the recreated containers, exactly as the
   runtime guide's setup section and the
   [credential installer guide](stewardship-credential-installers.md) say.
   The wizard finishes by importing the parish's data; the deployment stays
   in Testing mode with a draft campaign and sends no Family mail.

Record the complete release image reference
(`ghcr.io/<owner>/<repository>/stewardship@sha256:<64 hex>`, never only a
version tag), the deployment UUID, the project name and the runtime root in
the operators' notes: an upgrade and a restore both need them, and a
restore onto a new host pulls the image by that reference.
Keep those notes, and the deployment YAML, off the host with the backup's
private key, as the
[backup runbook](stewardship-backup-runbook.md#the-key) says: a restore onto
a new host starts from them, and no backup set holds them.

## Validation in Testing mode

The deployment is safe to explore in Testing mode: Testing-routed mail goes
only to the one configured Testing recipient (a staff address, set in the
setup wizard) and no Production Family code or link is live. Before the pre-launch
gate, the human runs the [smoke checks](stewardship-smoke-tools.md) inside the
deployed containers (ParishSoft read, Google Workspace mailbox with one test
message, optional Slack, and the Google OAuth client followed by a real
sign-in), staff validate the Family form, content, templates, schedules and
reports, and the load check of the launch scope's reduced item 7 runs here.
Bugs found now are fixed by ordinary pull requests and reach the host through
the [upgrade](#upgrade) below, except a release that adds a SQL login, a
runtime path or a table to the fresh-install baseline: retarget and migration
cannot create those in an existing deployment, so before the schema freeze the
validation deployment is reinstalled from scratch to pick such a release up.
The v1 backup release is one of them: a deployment provisioned before it has
no backup login, password, directory or record table, and must be reinstalled.

### Staff validation checklist

**The Family form.** The portal opens in Testing only while today is inside
the draft campaign's dates, and a Family signs in only with a Testing code or
link, which only Testing mail carries. To test chosen Families without
mailing the whole parish:

1. In the draft campaign, set the start date to today (not earlier: a past
   start makes the scheduler produce catch-up daily reports for the days in
   between) and leave the initial invitation and reminders at their real
   dates, so no scheduled invitation is due yet. On its next pass the
   scheduler creates the campaign's Testing credentials; daily and weekly
   Admin reports to the Testing recipient start too.
2. On the campaign's content page, open the Family email's test page and
   choose **Send this email to chosen real Families (Testing recipient
   only)**. Enter up to ten Family IDs (ParishSoft Family DUIDs), choose
   **Check these Families**, review each Family's status, tick the
   acknowledgment and choose **Send these Family tests**. If the page says
   Testing credentials are not ready, wait for the scheduler's next pass.
   At most ten such tests are in progress per campaign.
3. Each chosen Family's real message arrives at the Testing mailbox (subject
   `[TEST]`, a banner naming the intended Family) with that Family's Testing
   code (it starts with `I`) and Testing link (`/access/test.…`). Sign in by
   the link, or by the code on the portal's home page `/`, choose **Continue
   with test**, fill in the form, tick the acknowledgment and choose
   **Submit test response**. A Testing receipt then arrives at the Testing
   mailbox. Test answers never count, never appear in reports and are
   deleted at activation.
4. Check the daily and weekly Admin reports and a manual weekly report at the
   Testing mailbox (they show zero participation: they count only live
   answers), the reports and exports pages (which exclude Testing answers by
   design), and the Production readiness page's list of Testing submissions.
5. Before Production readiness, move the start date back to its real date,
   send the fictional sample test again (any configuration change voids the
   earlier one as readiness evidence), and wait until every Testing message
   has finished (delivered, failed or cancelled): readiness requires it, and
   cleanup at activation deletes all Testing data. A Testing message whose
   outcome is unknown does not finish by waiting; an Administrator resolves
   it as the launch runbooks'
   [unknown-delivery procedure](stewardship-launch-runbooks.md#messages-in-delivery_unknown)
   describes.

Moving the invitation itself into the validation window instead would send a
Testing invitation for every Family with a deliverable email address to the
one Testing mailbox; the chosen-Family send makes that unnecessary. The
[chosen-Family test guide](stewardship-family-test-send.md) records the
design.

**Browsers.** On a phone and on a desktop browser, check the Family portal's
code entry, link sign-in, every form step, the review and submit, and
sign-out, and the main staff pages: home, campaign and schedules,
reports and exports, deliveries, and background work.

**Load.** The launch scope's single load check at the parish's real Family
count runs against this deployment, inside its web container, while the
Testing Family portal is open (the draft campaign's dates include today and
its Testing credentials exist, as for the Family-form test above):

```text
docker compose ... exec -T web pk-stewardship load-check --config WEB_CONFIG > load-check.json
```

It only reads, in read-only transactions with statement and lock time
limits. It times loading the Family form's source inputs for a sample of
eligible Families (`--samples`, default 200: half the largest households,
half spread across the rest), serially and with a few concurrent readers
(`--concurrency`, default 4, never more than the web service's threads or
the web database login's spare connections), and the first pages of the
statistics, financial and information reports, 20 times each, one at a
time. It sends no mail, signs no one in, writes nothing, never touches Valkey
and prints only counts and seconds. The verdict compares each p95 with the
architecture specification's targets: 2 seconds for the Family form's inputs
and the statistics page, 3 seconds for a report's first page. The form timing
covers the per-Family source read, not the whole page, so it is a lower bound
for opening the form. A phase stops after five failed reads, and the whole
check after 15 minutes; anything not reached counts as not run and fails
the check. A Family that stops being eligible during the check is skipped,
but a phase that measures fewer than half its samples fails.

- Exit `0`, `"result": "pass"`: passed; keep the JSON with the gate evidence.
- Exit `1`, `"result": "fail"`: a target was missed or reads failed; report
  it as a launch blocker.
- Exit `2`: it did not produce a verdict, and the one-line error says why.
  It was refused (invalid options, no portal-eligible Families, no spare web
  database connections, offline work in progress, or otherwise not the web
  container of an open Testing campaign with promoted data); the Testing
  portal closed, the campaign became unavailable or the parish data was
  refreshed during the check (run it again); or an unexpected error stopped
  it (see the process log).

If a Testing invitation run exists for the current Testing credentials, the
output also times it under `invitation_run`, for information only. Run the
check outside an upgrade. The [load check guide](stewardship-load-check.md)
records its design.

**ParishSoft values the form refuses.** One malformed Member value in
ParishSoft (a name or other field over its length limit, a tab or line break,
a birth date that is not a real date, an over-long email or phone) stops that
whole Family's form from loading. Before launch, and after a large ParishSoft
cleanup, list every such value for the current campaign's portal-eligible
Families:

```text
docker compose ... exec -T web pk-stewardship source-form-check --config WEB_CONFIG > source-form-check.json
```

Like the load check it runs in the web container under the web database
login, only reads, and needs a current campaign and promoted parish data,
but not an open portal. It prints the Family DUID, Member DUID, field name
and kind of each refusal, never the value. Kind `value` is a field value to
fix; kind `record` is a Member record (field `member`) or a Member's contact
record that cannot be read at all. Exit `0` (`"result": "clean"`) means
none; exit `1` (`"result": "findings"`) means fix each listed field in
ParishSoft, refresh the parish data and run it again; exit `2` means it was
refused or failed, and the one-line error says which.

It checks Member and contact values only. A clean result does not rule out
a form refused for Ministry or financial source data, or for the snapshot
itself.

**Not testable before activation.** Delivery pause and resume exist only for
the Production campaign.

### Reinstalling the validation deployment

Provisioning needs a new, empty runtime root and never adopts existing data,
so a reinstall builds a second deployment beside the old one rather than
resetting it. Nothing in it deletes data:

1. Disable the old deployment's backup and off-host copy cron jobs and wait
   for any run already in progress to finish (`docker ps` shows no
   `backup-worker` container). Then take the old deployment down with
   `down` (never `down -v`) on its Compose file and project name. That
   removes its containers and networks and frees ports 80 and 443 and the
   fixed subnets of its internal `backend` and `proxy` networks
   (`172.29.240.0/24` and `172.29.241.0/24` unless the deployment YAML's
   `runtime_network` sets others), which a second project could not
   otherwise create.
   Every byte of its data lives in bind mounts under its runtime root, and
   `down` leaves those untouched.
2. Leave the old runtime root, database files and credentials where they
   are: its rendered files hold absolute paths under that root, so it can be
   started or restored again only at that path. Never delete its markers to
   make it look empty.
3. Install the new deployment from [First installation](#first-installation)
   into a different, empty runtime root, with a new deployment UUID and a
   different project name. Its `caddy` obtains a new certificate on first
   start; Let's Encrypt allows a handful of certificates for one name per
   week, so avoid reinstalling repeatedly in a short span. Then set up
   backups for it as the
   [backup runbook](stewardship-backup-runbook.md#the-key) says: install the
   same public key as its `backup_data` credential, point the cron jobs at
   its Compose file, project name and `backups/` directory, and run the first
   backup, which upgrades and grants require within 24 hours.
4. Deleting the old runtime root or database is a separate decision for
   the human, never part of the reinstall. The launch scope
   forbids deleting the validation deployment's database without explicit
   authorization.

## Pre-launch fast deploys

Before launch, while the validation deployment holds only disposable data,
[`tools/stewardship-dev-deploy.sh`](../../tools/stewardship-dev-deploy.sh)
moves it onto the current checkout in a few minutes, without CI or a release.
It sends the tracked files (including uncommitted edits) to the host, builds
the image there, pushes it to GHCR to obtain the digest production admits,
and then follows this runbook's [upgrade](#upgrade) steps: the upgrade
check and a fresh static tree prepared while the site still serves, the
background services stopped, a best-effort backup, then `web` stopped,
`retarget-image`, migration and grants (skipped when the upgrade check
answers `t`), the static tree swapped in, `web` started and then the rest,
and health. The host must be logged in to GHCR with a token that can write
packages (`docker login ghcr.io`).

Only the steps between stopping `web` and `web` turning healthy again are
downtime, and the script reports that span. Before #162 that span was 65–80
seconds: about 6 for stopping, 3–4 for `retarget-image`, 12 for migration
and grants, 3 for static collection and 50 for starting every service at
once and checking health. Without a schema or grant change it should now be
about 15–20 seconds (stopping `web`, `retarget-image`, the no-op query and
`web`'s own start), and about 12 seconds more when migration and grants run.

It chooses the Compose file from the database's setup completion marker, not
from whatever happens to be running: `compose.json` once setup has completed
(or `compose-slack.json`, which renders the same mounts, if the project
already runs under it), otherwise `compose-initial.json`. It then starts every
online service of that file, so a run also repairs a deployment that an
interrupted deploy left partly stopped. It finishes by checking that each
online service is running and healthy, naming any that is not, and prints a
timestamp for every step plus how long `web` was down. If the
database itself is not running, it refuses before stopping anything and says
how to start it. Run it from the checkout:

```sh
STEWARDSHIP_HOST=HOST STEWARDSHIP_UUID=UUID tools/stewardship-dev-deploy.sh
```

Its images skip CI, so they are never deployed to a campaign serving real
Families: go-live runs a digest from a real release. The script enforces
this on the host: before it builds, pushes or stops anything, it refuses
once the deployment's campaign has been activated to Production (a
`stewardship_production_request` row has `activated_at` set), and it also
refuses when it cannot read that answer. From then on, deploy only a
release digest through [upgrade](#upgrade).

By default it also starts the services with debug logging
(`PARISHKIT_DEBUG_LOGGING=1`, which the generated Compose files pass to every
application service): log lines then keep the original message, logger and
traceback that normal logging drops, and DEBUG records appear. Those can hold
personal data and secrets (personal-link tokens and other secret URL values
are still [redacted](../specs/stewardship/operations/spec.md#production-ingress-and-tls)),
so use it only while the data is disposable;
`STEWARDSHIP_DEBUG_LOGGING=0` turns it off. Running `docker compose up` by hand
without the variable recreates services with debug logging off.

## Production activation

Activation is an Administrator's workflow in the portal, bracketed by the
operator's backups; the step-by-step procedure, including withdrawal, is the
launch runbooks'
[Production activation](stewardship-launch-runbooks.md#production-activation).
Activation is scheduled for October 1, 2026 in the launch scope's
[schedule](../plans/stewardship/v1-launch.md#schedule).

## Upgrade

An upgrade moves the deployment from the recorded release digest to a newer
one. Until the pre-launch gate freezes the schema, the validation deployment
may instead be reinstalled from scratch when a baseline change lands, as the
launch scope's
[production-readiness activation](../plans/stewardship/v1-launch.md#production-readiness-activation-and-schema-freeze)
allows; after the freeze, every schema change is a forward migration and this
procedure is the only way forward. It applies the operations specification's
[production upgrade](../specs/stewardship/operations/spec.md#production-upgrades-deferred)
sequence with the commands that exist.

1. **Check and prepare the release, stop the background services, then back
   up.** Read the release notes first: a release that narrows a runtime
   grant cannot be taken by this procedure (see step 4). While the site is
   still up, do everything that needs only the new image, so none of it adds
   to the time `web` is down:
   - Pull it: `docker pull NEW_DIGEST`.
   - Collect its static files: create an empty `cache/static.next` owned by
     `10001:10001` with mode `0700` and run `collect-static` into it in the
     new image, exactly as first installation does. Nothing serves that
     directory until step 5.
   - Render its upgrade check: in the new image, with the same isolation as
     step 3 but the runtime root mounted read-only, run
     `pk-stewardship upgrade-check --config /run/operator.yaml --confirm-deployment UUID`
     and save its output as `upgrade-check.sql`. It opens no database; it
     prints the read-only query step 4 runs. You can also run that query now,
     exactly as step 4 does, as an advisory preflight: `f` tells you step 4
     will run migration and grants. For a release whose notes promise no
     schema or grant change, or one that narrows a grant, `f` is the cue to
     stop and investigate before anything stops. Only step 4's answer
     decides anything.

   Then stop the background services: `worker`, `scheduler`,
   `mail-dispatch`, `config-installer` and every credential installer, with
   `stop` on the current Compose file and project name. `web` keeps serving
   while they drain, which can take up to their stop grace period (360
   seconds by default) when the worker is finishing a task. While they are
   stopped, background work waits:
   - outgoing mail (Family confirmations and receipts, personal-link and
     setup mail) and Slack alerts queue until step 6;
   - configuration and credential requests an Admin submits wait for their
     installers. Time-limited ones can expire meanwhile: an integration
     setup intent lasts an hour, and a fresh re-authentication five
     minutes.

   Keep the gap short, and tell Admins not to start credential changes
   during the upgrade.

   Then run the backup (`run --rm backup-worker`, as the
   [backup runbook](stewardship-backup-runbook.md#the-nightly-backup) says;
   it runs beside `web` under the shared interlock) and confirm its off-host
   copy. Taking it now, after the drain and just before `web` stops, keeps
   the loss window small. A database-restore rollback loses only what `web`
   accepted after the backup's snapshot: Family submissions during the
   backup's own run (about half a minute on the validation host) and the
   moment until step 2. Do not continue without the backup: it is the only
   rollback. When step 4 runs migration and grants, they refuse a configured
   deployment whose newest recorded backup is more than 24 hours old. When
   step 4's check lets them be skipped, nothing checks the backup's age, so
   confirm yourself that this backup completed.
2. **Stop `web`** with `stop` on the same Compose file and project name. The
   site is down from this moment. If a background service was restarted
   since step 1, stop it again too: every online service must be stopped
   before step 3. Leave `postgres` and `valkey` running, and leave `caddy`
   running too: it holds no startup interlock, and while `web` is down it
   answers every request with its own self-contained "We're updating the
   site" page (HTTP 503 with `Retry-After`) instead of a refused
   connection. A `caddy` from a release before that change still holds the
   interlock and makes every offline step refuse, so stop it too on the
   first upgrade to this release. Stopping, not restarting, matters (here
   and in step 1): online services use
   `unless-stopped`, so a crashed service would otherwise come back during
   the offline work, as
   [Offline work and upgrade boundary](stewardship-runtime.md#offline-work-and-upgrade-boundary)
   warns.
3. **Retarget the image.** In the *new* application image, with the same
   isolation as provisioning (UID/GID `10001:10001`, no network, read-only
   root, the runtime root read-write and the deployment YAML read-only), run
   `pk-stewardship retarget-image --config /run/operator.yaml --image NEW_DIGEST`.
   It re-renders every generated document (the three Compose files, the
   per-service configurations and the Caddyfile) from the recorded inputs
   with the new digest, rewrites those that differ and updates the
   provisioning record; it runs in the new image so that the new release
   renders the documents it will run under. It refuses a changed deployment
   YAML and a still-running online service with nothing written; passwords
   are never regenerated, and a password, login or directory the new release
   needs but the deployment never had is a refusal (see the Testing-mode
   section above). If the command was interrupted, run it again with
   the same digest. Details:
   [release image guide](stewardship-release-image.md#retargeting-re-renders-the-generated-documents).
4. **Migrate.** First run step 1's query as the database superuser, in a
   read-only session, now that nothing online can change the answer:
   `docker compose -f COMPOSE_FILE -p PROJECT exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres psql -U pk_stewardship_operator -d DATABASE -At -v ON_ERROR_STOP=1 < upgrade-check.sql`,
   where `DATABASE` is the deployment's configured database name (the
   query itself also refuses any other database).
   It prints `t` only when both commands below would change nothing: the
   applied migrations are exactly the new image's, the download capacity is
   the budgeted one, and every foundation login already has exactly the
   attributes, connection limit and grants that the new image's
   `database-grants` requires, installs and admits, with this release's
   operational log writer guard in place. Then skip both commands; every
   service still verifies the schema and its own grants when it starts. On
   anything else, including an error, run both:
   `run --rm migration`, which applies forward migrations with the schema
   owner, and `run --rm database-provision database-grants --config PROVISION_CONFIG --confirm-deployment UUID`,
   which installs new runtime grants. On a deployment that has completed
   first installation both commands admit the change only when a backup
   completed within the last 24 hours is recorded (step 1); otherwise each
   refuses with the generic offline-refusal error and exit status 2, and the
   process log carries a `startup_rejected` line whose `failure_kind` is
   `upgrade_backup_required`. Run steps 1 to 4 in one sitting: once step 3
   has run, the backup profile runs the new image, which refuses a schema
   with migrations still to apply, so a step 1 backup that has aged cannot
   simply be taken again. When either command refuses for a missing
   backup, keep the online services stopped. If the release changes no
   schema, or `migration` already succeeded, the schema matches the new
   image: take the backup (`run --rm backup-worker`), confirm its off-host
   copy and repeat this step. Otherwise run `retarget-image` back to the
   previous digest in that previous image, take the backup and confirm its
   off-host copy, then run `retarget-image` with the new digest again in
   the new image, as step 3 does, and repeat this step. If the previous
   image's `retarget-image` refuses, first confirm the previous digest
   against the operators' notes and that every online service is stopped,
   since its error names no cause. The database is still untouched,
   because the migration refused before applying anything; the likely
   cause is a deployment field the previous release does not know, in
   the deployment YAML or in the provisioning record step 3 rewrote.
   Remove any such field from the deployment YAML (step 3 admitted only
   inputs equal to the recorded ones, so it can hold only its default).
   Then open the step 1 set's files as the backup runbook's
   [Restore for real](stewardship-backup-runbook.md#restore-for-real) steps
   2 and 4 describe (confirm the set against its recorded manifest digest,
   open `files.tar.sealed` with `backup-open` where the private key is
   kept, bring the result to the host privately and extract it into an
   empty private staging directory), move the current
   `.stewardship-provisioned.json` aside under a new name (never delete
   it), put the set's record in its place, owned by `10001:10001` with
   mode `0600`, and run the previous image's `retarget-image` again; then
   securely delete the decrypted `files.tar` and the staging directory on
   the host and on the key machine, as that procedure's step 10 does, and
   continue as above. Restore nothing else from the set: the online
   services changed the database and the other trees after it was taken.
   Only if that retarget still refuses, follow the database-restore
   [rollback](#rollback) from the step 1 backup, which loses whatever the
   online services wrote between the step 1 backup and step 2 and needs
   the full restore procedure, including its comparison with the mail
   provider's logs. The
   [gate round 5 ledger](stewardship-gate-round5-fixes-reviews.md) records
   how this recovery was checked. A release that changes neither the
   schema nor a grant makes the query print `t`, so both commands are
   skipped. `database-grants` never revokes: for a release that
   *narrows* a runtime grant on a table that still exists, it refuses the
   whole run, because a login already holds a privilege the new release no
   longer lists, and the new release's services would refuse that excess
   privilege anyway. Reading the release notes in step 1 (and step 1's
   advisory run of the query, which answers `f` for such a release)
   catches it before `web` stops: before the schema freeze it
   is taken by reinstalling, and after it the release must bring its own
   revocation step. `migration` runs first and commits, so if
   `database-grants` refuses after a successful migration, start neither
   image: recover with the database-restore [rollback](#rollback) (or,
   before the freeze, by reinstalling).
5. **Refresh the static files.** `caddy` serves the packaged JavaScript and
   stylesheets from `cache/static`, which `collect-static` fills once and
   never overwrites, so a release that changes or adds a static file would
   otherwise ship its templates with the previous release's scripts. Step 1
   collected the new tree into `cache/static.next`. Copy the current
   `cache/static` aside under the name
   of the release being replaced (for example
   `cache/static.PREVIOUS_DIGEST`, never deleting it), then refresh
   `cache/static` *in place*: empty it and copy `cache/static.next`'s
   contents into it. Do not move or replace the `cache/static` directory
   itself: the running `caddy` has it bind-mounted, and a directory moved
   aside stays mounted in its place. Keep the old tree until the release is
   accepted; a rollback copies it back the same way.
6. **Start and check.** Start `web` alone first with `up --detach --wait web`
   on the same Compose file (`compose.json` or `compose-slack.json`, whichever
   the deployment uses) and project name: every other online service imports
   the application and runs its health probes at the same time, so starting
   them together makes `web` take several times longer to turn healthy. Then
   `caddy`, then, once the site answers again, the remaining online services
   with `up --detach --wait`. Caddy reads its
   Caddyfile only when it starts, so compare what the running `caddy`
   loaded (`exec -T caddy sha256sum /etc/caddy/Caddyfile`) with the host's
   `Caddyfile`; when they differ, use `up --detach --force-recreate caddy`,
   otherwise `up --detach caddy` leaves it alone. Comparing the loaded copy
   rather than a checksum taken before step 3 also catches a re-run after
   a failed upgrade. Until `web` is healthy again, `caddy` keeps serving its
   maintenance page, so a failed upgrade leaves that page up until the
   upgrade is re-run or rolled back. Run the health
   command and open the public origin. Confirm in the portal that background
   work resumed: the home page's latest refresh time advances and the
   background task pages show the scheduler running.
   `docker compose ... top worker` lists two application processes: the
   worker and its [source process](stewardship-runtime.md#the-workers-source-process)
   (`runtime ... --queue source`). A release that adds or removes that
   process changes no Compose service. The release that introduced it also
   raised the worker's SQL connection limit, which needs the one-time step
   in [worker connection limit](#worker-connection-limit-339).

   The new image recreates `mail-dispatch`, which returns it to batched
   Family mail; if the one-helper-per-message fallback is in use, put
   `PARISHKIT_STEWARDSHIP_FAMILY_MAIL_TRANSPORT=per_message` before this
   `up --detach` as well
   ([Falling back to one helper per message](stewardship-family-mail-dispatch.md#falling-back-to-one-helper-per-message)).
   Likewise it returns to two mail consumer processes; if the one-process
   fallback is in use, put `PARISHKIT_STEWARDSHIP_MAIL_CONSUMERS=1` before it
   too ([Falling back to one mail consumer](stewardship-family-mail-dispatch.md#falling-back-to-one-mail-consumer)).
   `docker compose ... top mail-dispatch` lists two application processes
   (`runtime ... --queue mail` is the second). The release that introduced
   them raised the mail login's SQL connection limit, which needs the
   one-time step in [mail dispatch connection limit](#mail-dispatch-connection-limit).

Record the new release's complete `IMAGE@sha256:DIGEST` reference in the
operators' notes, and keep the previous ones: a restore onto a new host
pulls the image a set was taken under by that reference. The old image
stays in the registry; nothing here deletes it.

### Worker connection limit (#339)

The release that runs ParishSoft source work on the worker's second process
raises the `pk_stewardship_worker` login's connection limit from four to six
(three per process: task, lease renewal and timeout log). Provisioning only
creates roles and never alters one, and both `database-grants` and worker
startup refuse a login whose limit differs from the release's, so a
deployment provisioned earlier needs one `ALTER ROLE` by the operator
superuser (`pk_stewardship_operator`, the login `database-roles` uses; the
migration owner cannot alter another role). Run it *before* this release's
`database-grants` step; the running worker is unaffected, because the limit
is checked only at startup:

```sh
docker compose -f COMPOSE -p PROJECT exec -T postgres \
  psql -U pk_stewardship_operator -d DATABASE -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
ALTER ROLE pk_stewardship_worker CONNECTION LIMIT 6;
DO $check$
BEGIN
    IF (SELECT rolconnlimit FROM pg_roles WHERE rolname = 'pk_stewardship_worker')
        IS DISTINCT FROM 6 THEN
        RAISE EXCEPTION 'pk_stewardship_worker connection limit is not 6';
    END IF;
END
$check$;
COMMIT;
SQL
```

`tools/stewardship-dev-deploy.sh` runs `database-grants` itself, so run the
command before the script. Rolling back to an earlier image needs the same
command with `4`, or its worker refuses to start. A fresh installation needs
neither.

While the new image starts, a refresh hint that the old scheduler queued on
the `general` queue can reach the new main worker process, which logs one
`Task type is unavailable to this isolated consumer` error and leaves the
task in PostgreSQL. It is harmless: the next scheduler sweep re-publishes the
task to the source queue.

What to watch after deploying it:

- `helper_timed_out` entries for `source_helper` in the operational log: a
  `WARNING` is a late source heartbeat (usually a slow promotion), an
  `ERROR` means the worker stopped the source process's container.
- The general queue still waits behind a long general task (a large export or
  fact rebuild); only source work moved. A promotion can also briefly wait
  for, or delay, a general task's short write transaction (#147).
- A hint delivered twice may be claimed by nobody the second time; the
  durable row decides, so this shows only as a skipped duplicate.
- The main process notices a source process that is alive but hung only
  through its heartbeat; one blocked outside a renewal or tick shows as the
  warnings above before it is stopped.
- A ParishSoft read helper started by the source process may outlive that
  process by up to its own deadline after a forced stop.
- The worker container uses roughly one more Python process's memory
  (about 150 to 250 MB); check `docker stats` on the 8 GB host.

### Mail dispatch connection limit

The release that runs two mail consumer processes in `mail-dispatch` raises
the `pk_stewardship_mail_dispatch` login's connection limit from four to six
(three per process), for the same reasons and with the same rules as the
[worker connection limit](#worker-connection-limit-339). Run this as the
operator superuser *before* the release's `database-grants` step, and as
close as possible before `mail-dispatch` is recreated. The running mail
worker is unaffected, but from this point until the recreate an old mail
container that restarts for any reason refuses to start (its image expects
four), so Family mail stops until the new one is up:

```sh
docker compose -f COMPOSE -p PROJECT exec -T postgres \
  psql -U pk_stewardship_operator -d DATABASE -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
ALTER ROLE pk_stewardship_mail_dispatch CONNECTION LIMIT 6;
DO $check$
BEGIN
    IF (SELECT rolconnlimit FROM pg_roles
        WHERE rolname = 'pk_stewardship_mail_dispatch') IS DISTINCT FROM 6 THEN
        RAISE EXCEPTION 'pk_stewardship_mail_dispatch connection limit is not 6';
    END IF;
END
$check$;
COMMIT;
SQL
```

Rolling back to an earlier image needs the same command with `4`, or its mail
worker refuses to start. A fresh installation needs neither. Falling back to
one mail consumer ([one command](stewardship-family-mail-dispatch.md#falling-back-to-one-mail-consumer))
needs no change to the limit.

What to watch after deploying it:

- `docker compose ... top mail-dispatch` lists two application processes
  (`runtime ... --queue mail` is the second), and up to two Family mail
  helpers while mail is going out.
- `helper_timed_out` entries for `mail_helper` with no `helper` field
  are the second process's: a `WARNING` is a late heartbeat, an `ERROR`
  means it was stopped or killed. Entries naming a `helper` (such as
  `family_delivery_worker`) are SMTP helper deadline kills, as before.
- The daily sending limit is shared through the database; a Gmail limit or
  outage pause may be logged once by each process.
- The container uses roughly one more Python process's memory (about 150 to
  250 MB), plus a second helper while sending.

## Rollback

An application-only rollback is possible only when the new release changed
neither the schema nor any runtime grant:
stop the online services, run `retarget-image` with the previous digest (in
that previous image), pull, put the previous release's static tree back
*in place*, as step 5 refreshes it (empty `cache/static` and copy the kept
tree's contents into it, or collect into an empty `cache/static.next` in
the previous image and copy that in), because the running `caddy` has
`cache/static` bind-mounted, and start, applying step 6's rule to recreate
`caddy` when its loaded Caddyfile differs from the host file;
step 4 is not repeated. A grant the new release added is refused as
excessive by the previous release's services, so it needs the database
restore below. A deployment field the new release added makes the previous
release's `retarget-image` refuse the provisioning record the new release
wrote, and a deployment YAML that names that field is refused before the
record is read, by this rollback and by the database restore alike; the
error names no cause. So when the previous image's `retarget-image`
refuses, remove any field the previous release does not know from the
deployment YAML and put back the step 1 set's record, both as
[upgrade step 4](#upgrade) describes, and run it again; the database
restore below needs the same YAML change. When the release
changed the schema, an older image must never be pointed at the newer
database; the rollback is a database restore from the backup taken
in upgrade step 1, following the launch scope's
[manual restore procedure](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
and the [backup runbook](stewardship-backup-runbook.md#restore-for-real), and
it must include the image: with every online
service still stopped, after the database is restored and before anything
is started, run `retarget-image` back to the previous digest, or the new
image would start against the restored, older schema and refuse. The
restore procedure keeps the scheduler, worker and mail-dispatch services
stopped until an Administrator has compared the restored delivery state with
the mail provider's own logs; v1 cannot prevent a second copy of mail sent
after the backup, as its
[restore limitations](stewardship-backup-runbook.md#restore-limitations-in-v1)
explain.

## Known v1 limitations

- An upgrade cannot add a SQL login, a once-generated password, a runtime
  path or a baseline table to an existing deployment. Before the schema
  freeze such a release is taken by reinstalling; after the freeze, new
  tables arrive as forward migrations, and a release needing a new login or
  path needs a provisioning-extension step that does not exist yet and must
  be built with that release.
- The upgrade admission is the reduced form of OPS-04.03: a recorded backup
  within 24 hours stands in for verified restore evidence, which the backup
  runbook's restore drill supplies by hand. Automated readiness checks and
  upgrade-path tests are deferred; the operator follows this runbook by hand
  and the backup is the safety net.
- An upgrade cannot narrow a runtime grant on a table that still exists: the
  grant command refuses a login that already holds a privilege the release
  no longer lists, and nothing revokes it. Before the schema freeze such a
  release is taken by reinstalling; after it, the release must bring its own
  revocation step.
- The image is single-architecture (`linux/amd64`); the host must be x86-64.
- Restore is a manual procedure and may require re-sending some Family links
  by hand, as the launch scope records for the pre-launch gate to approve.
- Keys generated at install are not rotated during v1.

The [runbook corrections ledger](stewardship-runbook-corrections-reviews.md)
records how the upgrade, rollback and restore procedures were last checked
against the code.
