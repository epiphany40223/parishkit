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
   `database-roles` (which also installs the
   [text search extension](#text-search-extension-765)), bootstrap
   `prepare`, `migration`, `database-grants`, bootstrap `import`, then the
   online services. Start `caddy` last, once the
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
   design), and the Go live page's Testing submissions list.
5. Before going live, move the start date back to its real date,
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

Its `retarget-image` renders the documents with the
[bulk Family send](stewardship-family-mail-dispatch.md#turning-on-the-bulk-family-send)
off unless `STEWARDSHIP_BULK_FAMILY_SEND=1` is set for the run.

Its images skip CI, so they are never deployed to a campaign serving real
Families: go-live runs a digest from a real release. The script enforces
this on the host: before it builds, pushes or stops anything, it refuses
once the deployment's campaign has been activated to Production (a
`stewardship_production_request` row has `activated_at` set), and it also
refuses when it cannot read that answer. From then on, deploy only a
release digest through [upgrade](#upgrade), normally with the
[scripted upgrade](#scripted-upgrade), which the refusal names.

By default it also starts the services with debug logging
(`PARISHKIT_DEBUG_LOGGING=1`, which the generated Compose files pass to every
application service): log lines then keep the original message, logger and
traceback that normal logging drops, and DEBUG records appear. Those can hold
personal data and secrets (personal-link tokens and other secret URL values
are still [redacted](../specs/stewardship/operations/spec.md#production-ingress-and-tls)),
so use it only while the data is disposable;
`STEWARDSHIP_DEBUG_LOGGING=0` turns it off. Running `docker compose up` by hand
without the variable recreates services with debug logging off. Should a web
service still have debug logging on once the deployment is in Production,
every Admin page shows a "Debug logging is on in Production" notice; step 1
of the launch runbooks'
[Production activation](stewardship-launch-runbooks.md#production-activation)
checks every container before activation. An Administrator can also turn it
off at once from System health, without recreating anything; see
[turn off debug logging](#turn-off-debug-logging-530).

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
[production upgrade](../specs/stewardship/operations/spec.md#production-upgrades)
sequence with the commands that exist.

1. **Check and prepare the release, stop the background services, then back
   up.** Read the release notes first: a release that narrows a runtime
   grant cannot be taken by this procedure (see step 4). While the site is
   still up, do everything that needs only the new image, so none of it adds
   to the time `web` is down:
   - Verify its build provenance, unless `release.sh` printed this digest
     (it has already verified it):
     `gh attestation verify oci://NEW_DIGEST --repo OWNER/REPOSITORY --signer-workflow OWNER/REPOSITORY/.github/workflows/release.yml --source-ref refs/tags/vX.Y.Z --deny-self-hosted-runners`,
     from any machine with `gh` signed in. Stop if it fails: the image was
     not built by this repository's release workflow for that tag. The
     [release image guide](stewardship-release-image.md#the-pushed-digest-is-smoke-tested-and-attested)
     says what it checks.
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
   anything else, including an error, first install the
   [text search extension](#text-search-extension-765) as the database
   superuser (it changes nothing when already installed), then run both:
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
   before the freeze, by reinstalling). For the release whose notes announce
   the users upgrade, run this step as [Users upgrade](#users-upgrade-952)
   says.
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
   with `up --detach --wait`. A service whose database is not ready yet
   waits for it for about 60 seconds before refusing, and holds the online
   startup lease while it waits, so stop every online service before
   offline work even when one is still starting (see
   [online service startup](../specs/stewardship/operations/spec.md#online-service-startup)).
   Caddy reads its
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
   The bulk Family send is off after a `retarget-image` run without its
   variable; to keep it on, run step 3 with it
   ([Turning on the bulk Family send](stewardship-family-mail-dispatch.md#turning-on-the-bulk-family-send)).
   `docker compose ... top mail-dispatch` lists two application processes
   (`runtime ... --queue mail` is the second). The release that introduced
   them raised the mail login's SQL connection limit, which needs the
   one-time step in [mail dispatch connection limit](#mail-dispatch-connection-limit).

Record the new release's complete `IMAGE@sha256:DIGEST` reference in the
operators' notes, and keep the previous ones: a restore onto a new host
pulls the image a set was taken under by that reference. The old image
stays in the registry; nothing here deletes it.

### Scripted upgrade

[`tools/stewardship-upgrade.sh`](../../tools/stewardship-upgrade.sh) runs
steps 1–6 above one for one, over ssh from a checkout, on a deployment in
Testing or in Production (issue #460). The steps themselves are its host
half, [`tools/stewardship-upgrade-host.sh`](../../tools/stewardship-upgrade-host.sh),
which it uploads and runs on the host; the
[local laptop environment](stewardship-local-environment.md)'s `deploy`
runs the same file in its `local` mode, so a local deploy rehearses these
steps on a seeded local deployment. The manual steps remain the reference:
where the script and this runbook differ, the runbook is right; fix the
script. One step 1 check stays with you: the script does not verify the
image's build provenance, because its local mode deploys images no release
built; take the digest from `release.sh`, which verified it, or run step 1's
`gh attestation verify` first. Run it from the checkout with the release's
complete digest reference:

```sh
STEWARDSHIP_HOST=HOST STEWARDSHIP_UUID=UUID \
STEWARDSHIP_IMAGE=IMAGE@sha256:DIGEST tools/stewardship-upgrade.sh
```

It reads its settings from the environment: `STEWARDSHIP_HOST`,
`STEWARDSHIP_UUID` and `STEWARDSHIP_IMAGE` are required;
`STEWARDSHIP_ROOT` (default `/opt/parishkit`), `STEWARDSHIP_PROJECT`
(`stewardship`), `STEWARDSHIP_YAML`
(`/etc/parishkit/stewardship-deployment.yaml`) and
`STEWARDSHIP_IMAGE_REPO` (the official repository) override the
defaults; `STEWARDSHIP_SCHEMA_CHANGE=1` says the release notes announce a
schema or grant change. The script's header comment is the authoritative
list.

Before anything stops it refuses: an image reference that is not
`REPOSITORY@sha256:` with 64 lowercase hex digits (locally, before ssh); a
host without the Compose project; a `worker`, `scheduler` or
`mail-dispatch` running under a runtime fallback (per-message transport,
one mail consumer or the bulk send quick-stop), which a recreate would
silently undo; a pulled image that does not carry the named digest; and,
unless `STEWARDSHIP_SCHEMA_CHANGE=1`, an advisory upgrade check that
answers anything but `t`, which is step 1's cue to read the release notes.
It then stops the background services and takes the required backup; a
backup without a recorded off-host copy abandons the upgrade and restarts
those services. There is no separate drain step: as in step 1, stopping
the background services is the drain (each finishes within its stop grace
period) while `web` keeps serving. Only then does it stop `web`, and from
there it follows
steps 3–6: `retarget-image` in the new image with the deployment's current
bulk Family send switch and batch size carried over, so an upgrade never
turns the bulk send off; migration and grants only when step 4's check
answers anything but `t`; the static tree refreshed in place with the
replaced tree kept at `cache/static.PREVIOUS_DIGEST`; `web`, then `caddy`
(recreated only when its loaded Caddyfile differs), then the remaining
online services; and the checks: health, every online service running and
healthy, `PARISHKIT_DEBUG_LOGGING=0` in every container, the Compose file
naming the new digest, the bulk switch still on in the worker,
mail-dispatch and scheduler documents when it was on, the application
version, the public origin answering, and the worker and mail-dispatch
process counts; step 6 reads the service list from the re-rendered
Compose file, so a service the new release adds is started and checked. A
failure before `web` stops brings only the background services back; a
failure after `web` stops but before the retarget starts every online
service of the current file again; a failure from the retarget on leaves
`caddy`'s maintenance page up and says to re-run with the same digest or
roll back. Every step is timed, and the log is also kept on the host as
`/var/log/stewardship-upgrade-TIMESTAMP.log`, with the backup's result
appended to `/var/log/stewardship-backup.log`.

After a successful upgrade or rollback it prunes old release images (#1661,
the operations specification's
[Production upgrades](../specs/stewardship/operations/spec.md#production-upgrades)
rules) with
[`tools/stewardship-prune-images.sh`](../../tools/stewardship-prune-images.sh),
uploaded and run the same way. Only images of `STEWARDSHIP_IMAGE_REPO` are
candidates; it keeps every image a container uses, the release just
started, the replaced release the run reported, and the
`STEWARDSHIP_KEEP_RELEASES` newest others (default 4), removes the rest by
image ID, then clears Docker's build cache (`STEWARDSHIP_KEEP_BUILD_CACHE=1`
keeps it). It prints each image removed or kept and the space freed, and
keeps that log on the host as `/var/log/stewardship-prune-TIMESTAMP.log`.
`STEWARDSHIP_PRUNE` defaults to `on`, which removes; the Administrator
approved that default (#1671) after reviewing a Production dry-run log.
`STEWARDSHIP_PRUNE=dry-run` lists what it would remove and removes nothing;
`STEWARDSHIP_PRUNE=off` skips pruning.
It prunes only when the host half's last line reports the replaced release
as a digest of `STEWARDSHIP_IMAGE_REPO`; when that line is missing,
reworded or empty, it warns and removes nothing. A re-run with the digest
already running replaced the release with itself, so the release before it
is not known by name; that run says so and skips pruning, and the next
upgrade prunes. A failed lookup of what to keep (other than an image that
is simply not on the host) stops the pruning before anything is removed. A
rollback keeps the release it rolls back from, because that release is the
one it just started. A pruning failure prints a warning and the upgrade still
exits 0. The pruning ssh is limited to `STEWARDSHIP_PRUNE_TIMEOUT` seconds
(default 1200, enough for about 70 images and the build cache); on expiry it
is killed, a `TIMEOUT:` line logs what was killed, the limit and the elapsed
time, and the upgrade still exits 0, so check the host's prune log. To preview
or prune without an upgrade, run it on the
host directly (`--keep N`, `--protect IMAGE`, `--dry-run`,
`--keep-build-cache`; its header lists them):

```sh
ssh HOST bash -s -- --dry-run < tools/stewardship-prune-images.sh
```

It does not do everything an upgrade may need. The one-time steps a
release may require before its grants run, the
[worker connection limit](#worker-connection-limit-339) and the
[mail dispatch connection limit](#mail-dispatch-connection-limit)
`ALTER ROLE`s, are the operator's: run them by hand first, because the
advisory check answers `f` for such a release and `database-grants` would
refuse after `web` stopped. It does not read the release notes for you,
check the host's disk space or registry login, or repeat a runtime
fallback (it refuses one instead). The operational log writer guard is
among the things the upgrade check itself verifies. It does install the
[text search extension](#text-search-extension-765) before every migration
it runs, since that step is safe to repeat.

The [operator helper scripts](stewardship-operator-scripts.md) cover the
steps around it: `tools/stewardship-ops/release.sh` publishes a release
and prints the digest to pass as `STEWARDSHIP_IMAGE`, and
`tools/stewardship-ops/predeploy-check.sh` is a read-only look, just
before running the upgrade, for background work it would interrupt and
Families who are active.

Two recorded runs, both without a schema change (#460): the validation
deployment from v0.3.9 to v1.0.0 took 126 s with `web` down for 18 s (pull
19 s, static collection 5 s, render and advisory check 5 s, background stop
6 s, backup and off-host copy 28 s, `web` stop 2 s, retarget 3 s, static
refresh under 1 s, `web` and `caddy` start 12 s, the other 16 online
services 36 s, checks 8 s); the Production deployment from v1.0.0 to
v1.1.0 took 137 s with `web` down for 22 s. The migration and grants path
was rehearsed on the
[local laptop environment](stewardship-local-environment.md) on 2026-10-04,
whose `deploy` runs this host half in local mode on a seeded deployment: a
build carrying a migration was refused before anything stopped without the
override, and with it step 4 ran `migration` then `database-grants` in 3 s
(30 s in all, `web` down 8 s); the image-only rollback attempted afterwards
was refused before anything stopped, as [Rollback](#rollback) says; and a
backup the backup worker refused abandoned the upgrade with the background
services restarted and `web` never stopped. The remaining abandon paths (a
failed stop, a failed retarget, a partial static refresh) are exercised only
by the script's stand-in tests so far; #460 tracks their rehearsal. For a
release that announces a schema or grant change, run the script with
`STEWARDSHIP_SCHEMA_CHANGE=1` and watch step 4, or take the manual steps
above.

### Users upgrade (#952)

The release that turns sign-in rules into users converts the configuration
offline between two migrations, in the order the operations specification's
[users upgrade](../specs/stewardship/operations/spec.md#users-upgrade-from-sign-in-rules)
fixes. Its last migration refuses an unconverted configuration, so step 4
becomes, with every online service stopped:

1. `run --rm migration migrate --config MIGRATION_CONFIG --users-upgrade-base`:
   every migration except the swap and what depends on it.
2. `run --rm database-provision database-grants --config PROVISION_CONFIG --confirm-deployment UUID`.
3. `run --rm admin-recovery upgrade-users --config RECOVERY_CONFIG --confirm-deployment UUID --preview`:
   read the Administrators it lists. Before the release, the Portal users
   page's Upgrade preview and `pk-admin users preview-upgrade` showed the
   same list, so any missing address was already added.
4. `run --rm admin-recovery upgrade-users --config RECOVERY_CONFIG --confirm-deployment UUID --operation-id NEW_UUID --operator-name NAME --reason REASON --confirm-administrators ADDRESSES`,
   with `ADDRESSES` exactly the preview's list, comma-separated.
5. `run --rm migration` and `database-grants` again, as step 4 always does.

The configuration files are the services' own under `config/services/`
(`migration.yaml`, `database-provision.yaml`, `admin-recovery.yaml`). The
scripted upgrade runs these five when given the conversion's inputs, all
four or none, with `STEWARDSHIP_SCHEMA_CHANGE=1`:

```sh
STEWARDSHIP_USERS_OPERATION=NEW_UUID STEWARDSHIP_USERS_OPERATOR='NAME' \
STEWARDSHIP_USERS_REASON='REASON' STEWARDSHIP_USERS_ADMINISTRATORS=ADDRESSES \
STEWARDSHIP_SCHEMA_CHANGE=1 STEWARDSHIP_HOST=HOST STEWARDSHIP_UUID=UUID \
STEWARDSHIP_IMAGE=IMAGE@sha256:DIGEST tools/stewardship-upgrade.sh
```

It refuses before anything stops when they are partial or malformed, and
for a rollback. Without them, it stops at the swap's refusal with the
maintenance page up and nothing of the swap committed; repeat it with them.
The local environment's `deploy --schema-change` passes the same variables.

Every stop in this step 4 (no inputs, a refused conversion, a failed final
migration, a failed `database-grants`, an interruption) leaves only the
swap pending, which every online service refuses, so the maintenance page
stays up until a repeated run finishes. The repeat runs from step 1: its
backup admits a database with only the swap pending (that set restores
exactly this state; `restore-check` reports it as `swap_pending`, see the
[backup runbook](stewardship-backup-runbook.md#restore-for-real)), and
because `web` was already stopped when it began, a failure before its
retarget starts nothing and leaves the maintenance page up rather than
starting services that would refuse the schema.

A base migration that stopped partway (a failure or its time limit) leaves
migrations outside the swap pending too, which no backup admits. Repeat
the run the same way, with the same digest and the `STEWARDSHIP_USERS_*`
variables, within 24 hours of the first run's backup: its step-1 backup
then finds that state and reports the first run's backup instead of taking
one (`backup --users-upgrade-resume`). That backup, taken before anything
changed, stays the upgrade's restore point (with the previous release); the
run prints `REUSED BACKUP:` with its time and manifest digest, and
`stewardship-backup.log` records the line. Only the scripted repeat passes
that option, and only when the documents already name the release, so a
first run always backs up. Without the variables, or after 24 hours, the
backup refuses and the maintenance page stays up: fail forward with a
repeat within the window, or roll back by restoring the pre-upgrade backup
with the previous release ([Rollback](#rollback)); an image-only rollback
refuses a changed schema.

Every step-4 command of the script runs under a time limit (30 minutes for
each migration, 15 for the conversion, 10 for the preview and each
`database-grants`), in a one-off container named after the project, the
run and the step (`PROJECT-upgrade-TIME-PID-STEP`). A command that exceeds
it is killed, and the log records a `TIMEOUT:` line naming it with its
limit and elapsed time; the script then kills and removes that container by
name (`docker kill`, `docker rm --force`) and logs it, since stopping the
`docker compose run` client alone leaves the container running. The run
stops with the maintenance page up. Ctrl-C or a kill of the host half does
the same with an `INTERRUPTED:` line in the log. A dropped ssh session
delivers no signal: the running command finishes or reaches its limit, and
the run ends at its next output. Before repeating a run that ended any
other way, check that no one-off container is left (`docker ps`). A step-4
command that fails has all of its output printed and logged after a
`FAILED:` line, not only its last line, less what may carry database
values: a PostgreSQL `DETAIL` line is withheld, and a Python traceback is
reduced to its exception's class. The operator commands themselves print
only the exception's class and SQLSTATE (or a configuration refusal's own
sentence), never its message.

If the conversion refuses (a list that differs from the preview's, or SQL's
last-Administrator guard), the configuration keeps its sign-in rules, the
swap is not installed and the services stay down. `recover-admin` and
`preview-admin-recovery` work on this schema, before and after the
conversion, as [Admin-access recovery](stewardship-runtime.md#offline-work-and-upgrade-boundary)
describes, so add or restore the missing Administrator and repeat the
conversion with a new operation ID (a refused one is terminal). An
interrupted run resumes with the same operation ID. Once the conversion has
activated, the previous release cannot run on the configuration: fail
forward, or restore the pre-upgrade backup together with the previous
release.

### Report email conversion (#1786)

The release that brings report emails converts the Daily and Weekly
digests in step 4, after the migrations and `database-grants`:
`run --rm admin-recovery upgrade-reports --config RECOVERY_CONFIG --confirm-deployment UUID`.
The scripted upgrade runs it on every upgrade, needs no inputs, and does
nothing once nothing is left to convert. The operations specification's
[report email conversion](../specs/stewardship/operations/spec.md#report-email-conversion)
lists what to do if the migration or the conversion refuses. In short: if
the migration refuses because a digest send is unfinished, restore the
pre-upgrade backup with the previous release ([Rollback](#rollback)), let
the send finish and upgrade again; if `upgrade-reports` fails after the migration, fix the
cause and repeat the run (fail forward). The later release that retires
the digests (#1834, migration 0171) needs the conversion done first: the
scripted upgrade refuses it in step 1, with nothing stopped or changed,
while the database has not applied the conversion's migration or still
has a digest definition; deploy the conversion's release first, then the
later one. If its migration refuses anyway, 0161 may already have
committed: restore the pre-upgrade backup with the previous release
([Rollback](#rollback)) before anything else, then upgrade through the
conversion's release. Run the precheck before the
window, with the previous release still running, through the operator
login as the backup runbook does for its queries. Every count must be 0:

```sql
SELECT
  (SELECT count(*) FROM stewardship_schedule_occurrence o
     JOIN stewardship_schedule_definition d ON d.id = o.definition_id
     WHERE d.kind IN ('daily_digest', 'weekly_digest')
       AND o.state IN ('pending', 'running', 'delivery_unknown')) AS sends,
  (SELECT count(*) FROM stewardship_outbox_message
     WHERE purpose IN ('daily_digest', 'weekly_digest')
       AND state NOT IN ('delivered', 'permanent_failure', 'cancelled'))
     AS messages,
  (SELECT count(*) FROM stewardship_task_run
     WHERE task_type IN ('daily_digest_prepare', 'daily_digest_finalize',
       'weekly_digest_prepare', 'weekly_digest_finalize')
       AND state IN ('queued', 'running', 'retry_wait', 'abandoned'))
     AS tasks;
```

Resolve any old stuck one first (Sent mail, Background work). This query
lists the failed digest sends the conversion may owe again (it does not
leave out sends a restore hold or post-close resolution covers, skipped
dates, Testing or removed digests, so it can list more than are owed);
each digest owed one gets one coalesced catch-up report to its
Administrators after the upgrade:

```sql
SELECT d.kind, o.slot FROM stewardship_schedule_occurrence o
  JOIN stewardship_schedule_definition d ON d.id = o.definition_id
  WHERE d.kind IN ('daily_digest', 'weekly_digest') AND o.state = 'failed'
    AND NOT EXISTS (SELECT 1 FROM stewardship_schedule_fulfillment f
      WHERE f.definition_id = o.definition_id AND f.mode = o.mode
        AND f.target = o.target AND f.slot = o.slot)
  ORDER BY d.kind, o.slot;
```

### Family engagement backfill (#477)

The release that adds the durable Family engagement table is a schema and
grant change: with the [scripted upgrade](#scripted-upgrade) set
`STEWARDSHIP_SCHEMA_CHANGE=1`; the upgrade check reports `f` and step 4 runs
migration and then `database-grants` (or, by hand, run step 4's migration
then `database-grants`). Right after step 6, fill the table from
what Production retained, so the response funnel's "link followed" and "form
opened" counts start from go-live rather than from the upgrade:

```text
docker compose ... exec -T web pk-stewardship engagement-backfill --config WEB_CONFIG
```

It runs in the web container under the web database login, needs Production
mode and a current campaign, writes in batches of 50 Families, prints one
JSON line with the counts and records them in the System logs. It can be
repeated: a second run writes nothing but its log entry. Form progress before
the upgrade was never retained and is not reconstructed.

### Full refresh times (#465)

The release that lets Administrators list the ParishSoft full refresh times
is a schema change without a grant change: with the
[scripted upgrade](#scripted-upgrade) set `STEWARDSHIP_SCHEMA_CHANGE=1`; the
upgrade check reports `f` and step 4 runs migration
(`stewardship_source.0002_refresh_tick_times` replaces the refresh-tick guard
and refuses to commit unless the new body is installed) and then
`database-grants`, which changes nothing. No other step is needed: the
nightly refresh keeps its time until an Administrator lists more on the
ParishSoft integration page.

### Take a backup now (#530)

The release that adds Take a backup now's request record and request mode
is a schema and grant change: with the [scripted upgrade](#scripted-upgrade)
set `STEWARDSHIP_SCHEMA_CHANGE=1`; the upgrade check reports `f` and step 4
runs migration (`stewardship_jobs.0023_backup_request` installs frozen file
0062: the `stewardship_backup_request` table and its guard, and refuses to
commit unless both are installed as declared) and then `database-grants`,
which gives the web login its request reads and inserts and the backups
panel's size and version reads, and the backup login its request reads and
column updates. Before you start, check that no backup is running, scheduled
or requested (`docker ps --filter name=backup-worker` lists none). Then:

1. After step 6, install the five-minute request-mode cron line from the
   backup runbook's [nightly backup](stewardship-backup-runbook.md#the-nightly-backup)
   section on the host, next to the scheduled backup. Not before: the
   previous release has no `--request` option, so the line would fail
   every five minutes until the upgrade.
2. On System health, the Backups panel shows the last backup's size and
   version.

Rolling back: the previous image refuses the migrated database, so this is
fail forward, as the general policy is. If the catastrophic case restores
the pre-upgrade backup and redeploys the previous release, remove the
request-mode cron line first.

### Turn off debug logging (#530)

The release that adds the debug-off switch is a schema and grant change:
with the [scripted upgrade](#scripted-upgrade) set
`STEWARDSHIP_SCHEMA_CHANGE=1`; the upgrade check reports `f` and step 4 runs
migration (`stewardship_jobs.0028_debug_off_switch` installs frozen file
0078: the `stewardship_debug_off_switch` table, its guard and its two
functions, and refuses to commit unless all are installed as declared) and
then `database-grants`, which gives the web login its switch reads and
inserts, every service login the one-answer read of whether the switch is
set, and the admin-recovery login the clear. No other step is needed.

System health's **Turn off debug logging** sets the switch: every service
started with `PARISHKIT_DEBUG_LOGGING=1` turns its own debug logging off
within about a minute, without a restart, and the Debug logging panel says
who set it and when. The switch stays set, and the setting on the server
still says `1`, until you clear it. Only the server operator can clear it,
offline, from the admin-recovery profile. With every online service
stopped:

```sh
docker compose ... run --rm admin-recovery debug-off-clear --config RECOVERY_CONFIG
```

It prints `{"debug_off_cleared": true}` (or `false` when no switch was
set), and records `debug_logging_allowed` in System logs as the server
operator. Then start the services again: those whose setting is still on
log in debug form again. To remove debug logging for good,
recreate the services without the setting instead, as
[pre-launch fast deploys](#pre-launch-fast-deploys) describes.

### Accept a large ParishSoft change once (#530)

The release that adds System health's **Accept this change once** button is
a grant change without a schema change: the acceptance table and its guard
came with frozen file 0080. With the [scripted upgrade](#scripted-upgrade)
set `STEWARDSHIP_SCHEMA_CHANGE=1`; the upgrade check reports `f` and step 4
runs migration (nothing new) and then `database-grants`, which gives the web
login its reads and inserts of acceptances and its read of which task each
refused attempt ran (so accepting acknowledges that refusal's critical
problem). No other step is needed. An
Administrator then accepts a refused large change on System health, as
[Accepting a large ParishSoft change](stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change)
describes; the `PARISHKIT_SOURCE_MAX_DROP_PERCENT` override stays as the
server operator's emergency path.

### Example Families of a refused large change (#530)

The release that adds the example Families to **Accept this change once**
has a schema change: frozen file 0085 creates the example table, its guard
and the function that says which examples may be shown. With the
[scripted upgrade](#scripted-upgrade) set `STEWARDSHIP_SCHEMA_CHANGE=1`;
step 4 runs `migration` and then `database-grants`, which gives the worker
its reads, inserts and deletes of examples and the web login its reads. No
other step is needed. Only refusals recorded after the upgrade have
examples; the preview of an earlier refusal says none are available.

The later release that adds **Allow debug logging again** (#1207) is also a
schema and grant change: with `STEWARDSHIP_SCHEMA_CHANGE=1`, step 4 runs
migration (`stewardship_jobs.0031_debug_allow` installs frozen file 0088,
which replaces the switch's guard and refuses to commit unless the new one
is installed) and then `database-grants`, which gives the web login its
column update on the switch's clear. In Testing an Administrator can then
clear the switch from System health; in Production the guard still refuses
every clear but this offline one.

### Preparing reminders ahead (BG-12)

The release that prepares bulk-send reminders before their due time
([#447](https://github.com/epiphany40223/parishkit/issues/447)) is a schema
change without a grant change: with the [scripted upgrade](#scripted-upgrade)
set `STEWARDSHIP_SCHEMA_CHANGE=1`; step 4 runs migration
(`stewardship_campaigns.0003_occurrence_prepare_ahead` replaces the
occurrence guard, so Family mail preparation may claim a Production
occurrence up to two hours before its due time, restores the guard's
`SECURITY DEFINER`, and refuses to commit unless both are installed) and
then `database-grants`, which changes nothing. No credential, code, link
token or token lookup changes. Deploy outside a reminder send and its lead
window (from two hours before the due time until the send has settled),
and only with the Administrator's approval.

Rolling back:

- **Turn the bulk send off.** This is the immediate rollback and needs no
  approval: its quick stop or a re-render without the switch
  ([Family mail dispatch guide](stewardship-family-mail-dispatch.md#turning-on-the-bulk-family-send)).
  It stops planning ahead and building outside the lock; the one-at-a-time
  path prepares at the due time and sends what is already prepared. Only
  the looser guard condition remains, and nothing then uses it except
  preparation already queued for a reminder planned ahead.
- **Fail forward.** Any other fault is fixed in a later release, as the
  general policy is; there is no corrective migration restoring the old
  guard condition. The previous image refuses the migrated database, so an
  image retarget alone is not possible.
- **Catastrophic case only:** restore the pre-upgrade backup and redeploy
  the previous release. This returns the old guard but loses every response
  and change since that backup.

The Administrator chose this (turn the bulk send off, then fail forward) on
[#447](https://github.com/epiphany40223/parishkit/issues/447#issuecomment-5988416157).

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

### Text search extension (#765)

The release with migration file 0117 adds trigram indexes for the System
logs text search, which need PostgreSQL's `pg_trgm` extension. It ships
with the pinned PostgreSQL image, but creating an extension needs CREATE on
the database, which no service login holds, so the migration login cannot
install it: the operator superuser (`pk_stewardship_operator`) does. Without
it, the migration refuses with `permission denied to create extension
"pg_trgm"` and applies nothing.

- A fresh installation needs nothing: `database-roles` installs it.
- The [scripted upgrade](#scripted-upgrade), the local environment's deploy
  and `tools/stewardship-dev-deploy.sh` install it before every migration
  they run.
- A manual upgrade runs this in step 4, before `migration`:

```sh
docker compose -f COMPOSE -p PROJECT exec -T postgres \
  psql -U pk_stewardship_operator -d DATABASE -v ON_ERROR_STOP=1 \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"
```

It is safe to repeat and changes nothing once installed. The extension
lives in the `public` schema, so a database restore (which empties and
reloads `public` from the dump) drops it and re-creates it with the dump, and
a restore of a set from before this release leaves it out. A rollback to an
earlier image after the migration ran is a database restore anyway (the
schema changed). Building the six indexes takes about half a minute at
2,000,000 entries per log, and about a second at Production's size in
October 2026, while nothing writes.

What to watch after deploying it: a System logs search for a word of three
or more letters or digits answers in milliseconds instead of reading every
entry in the date range; shorter words read the entries as before.

Each log write now also updates the four new indexes; at Production's rate (tens of thousands of audit events a month and about a hundred operational rows) this is negligible, and the only visible effect would be a rare insert that flushes a GIN index's pending list.

### Database inspection login (#2148)

The release that adds the SELECT-only inspection login,
`pk_stewardship_inspect`
([operations spec](../specs/stewardship/operations/spec.md#database-inspection-login)),
is a grant change with no migration. With the
[scripted upgrade](#scripted-upgrade), set `STEWARDSHIP_SCHEMA_CHANGE=1`:
the upgrade check reports `f` (the login is missing; it may instead fail
with `role "pk_stewardship_inspect" does not exist`, which the script treats
the same way), and step 4's
`database-grants` creates the login and grants it SELECT. By hand, run step
4's `database-grants`. No data, Family code or link changes. An
application-only [rollback](#rollback) stays possible: the previous image's
check does not look at this login, which stays in place and unused.

From then on, inspect Production with the wrapper on the host, never as the
operator:

```sh
tools/stewardship-ops/pk-inspect                            # interactive
tools/stewardship-ops/pk-inspect -c "SELECT count(*) FROM stewardship_outbox_message"
tools/stewardship-ops/pk-inspect -At -v ON_ERROR_STOP=1 -f - < query.sql
```

Set `PK_INSPECT_PROJECT` when the Compose project is not `stewardship`
(pk-local's is `parishkit-local`). Every table write, DDL and `SET ROLE` is
refused, and so is any read of credential material
(`permission denied for table ...`). A restore of a backup set from before
this release leaves the login without grants until `database-grants` runs
again.

### Refresh schedule modes (#1122)

The release that gives the ParishSoft refresh schedule editor Basic and
Advanced modes
([#1122](https://github.com/epiphany40223/parishkit/issues/1122)) has no
database migration and no grant change: the
[scripted upgrade](#scripted-upgrade) runs without
`STEWARDSHIP_SCHEMA_CHANGE`. It adds one optional ParishSoft setting,
`refresh_schedule_mode`, written the first time an Administrator saves a
changed refresh schedule.

After deploying, remove Production's stored quick updates: on the
ParishSoft integration page the schedule opens in Advanced with a note
about quick updates. Choose **Remove quick updates**, then Save, check that
the review lists "Quick updates removed", and Apply. (From the release that
removes quick updates,
[#1147](https://github.com/epiphany40223/parishkit/issues/1147), this step
is gone: the editor no longer shows stored quick rows or that action, and
nothing schedules them.)

Rolling back: until a schedule is saved on this release, the previous image
runs on the configuration unchanged. Once one is saved (removing the quick
updates included), the stored settings carry `refresh_schedule_mode`, which
the previous release's configuration check refuses, so an image-only
rollback is no longer possible: fail forward, as the general policy is, or
restore the pre-upgrade backup together with the previous release
([Rollback](#rollback)).

### Quick updates removed (#1147)

The release that removes quick updates
([#1147](https://github.com/epiphany40223/parishkit/issues/1147)) has no
database migration, no grant change and no configuration change. What to
expect after deploying: a quick update still queued at the upgrade runs
once without calling ParishSoft and waits for one full refresh, which
the worker requests at once; System health shows that full refresh
running, the connection line is unaffected, and the quick update's task
succeeds when the full refresh promotes (a few minutes). A stored schedule
with quick rules keeps them, ignored, until the next schedule change saved
on the ParishSoft page drops them.

## Rollback

An application-only rollback is possible only when the new release changed
neither the schema nor any runtime grant. Every release that carries a
forward migration (the first was v1.2.0 with
`stewardship_campaigns.0002_family_engagement`, #477; then
`stewardship_source.0002_refresh_tick_times`, #465, and
`stewardship_campaigns.0003_occurrence_prepare_ahead`, #447) rules it out: the
previous image refuses a database whose applied migrations it does not know,
so roll back by database restore (below) or roll forward with a fix. For a
release that qualifies:
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

### Scripted rollback

`tools/stewardship-upgrade.sh --rollback` performs the application-only
rollback above, with the same settings, safety structure, logging and checks
as the [scripted upgrade](#scripted-upgrade); `STEWARDSHIP_IMAGE` names the
previous release's digest, as the operators' notes record it:

```sh
STEWARDSHIP_HOST=HOST STEWARDSHIP_UUID=UUID \
STEWARDSHIP_IMAGE=IMAGE@sha256:PREVIOUS_DIGEST tools/stewardship-upgrade.sh --rollback
```

It never restores a database, and it decides which case it is in the way
this section does: it renders the *previous* image's upgrade check and
runs it as the advisory preflight before anything stops, and again,
decisively, once `web` has stopped and before anything is retargeted.
Only `t` (the schema and every runtime grant already match the previous
image) lets it continue; on anything else it refuses and names this
section's database-restore rollback. The first refusal, before anything
stops, changes nothing; only the second, after `web` stopped, starts every
online service again, since nothing was retargeted.
`STEWARDSHIP_SCHEMA_CHANGE` is refused rather than ignored. It then runs
`retarget-image` with the previous digest in the previous image, with the
bulk Family send switch carried over, skips step 4, puts the previous
release's static tree back in place from the copy the upgrade kept at
`cache/static.PREVIOUS_DIGEST` (collecting it again in the previous image
when that copy is missing or empty, as above), keeps the replaced tree
under the replaced digest's name, and starts and checks as step 6 does,
reading the service list from the re-rendered file so a service only the
newer release had is neither started nor reported. The required backup
before `web` stops runs in the current image, whose schema it matches. If
the previous image's `retarget-image` refuses, the cause is the deployment
field case above; the script leaves the maintenance page up and says so.
The previous release's image is normally still on the host, since the
[scripted upgrade](#scripted-upgrade)'s image pruning keeps the replaced
release and the newest few others; if it was removed, the rollback's pull
fetches it again.
The rollback has not yet been rehearsed on a host; that is tracked in
issue #460 and belongs on the
[local laptop environment](../specs/stewardship/local-environment/spec.md)
(#476).

## Known v1 limitations

- A worker or mail-dispatch container that exits with status 70 stopped
  itself on purpose (see the runtime guide's worker section): Compose
  restarts it and the interrupted task recovers through its lease. If the
  sibling process (source, or second mail) stopped itself, the container
  exits 1 instead, with the same CRITICAL line in its log. No
  incident opens for it; if it repeats, read the `task_failed` CRITICAL
  line and the timeout entry before it, and report the task.

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
