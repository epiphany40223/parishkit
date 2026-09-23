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
port. Create the DNS `A`/`AAAA` records for the public hostname before the
proxy starts, because Caddy obtains its certificate from Let's Encrypt on
first start and needs the name to resolve to this host.

Collect, outside the runtime root and outside the repository:

- The **image digest** of the release to install, copied from the GitHub
  Release's notes as the complete
  `ghcr.io/<owner>/<repository>/parishkit@sha256:<64 hex>` reference. The
  release workflow writes that line when the human pushes a `vX.Y.Z` tag; a
  tag such as `:1.2.3` is never deployed, only the digest.
- The **deployment YAML**: schema version 1, `profile: production`, the HTTPS
  `public_origin`, `trusted_proxy_hops: 1`, and the absolute `paths.root`.
  Every field, default and validation rule is in the
  [settings reference](../development/stewardship-deployment.md#schema-version-1).
  Keep it in an operator-controlled place; it holds no secrets. The offline
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
  the delegated user; stewardship needs only the Gmail scope above.
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

Record the release digest, the deployment UUID, the project name and the
runtime root in the operators' notes: an upgrade and a restore both need them.

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

**The Family form.** No Admin page shows a Testing code or link, and the
readiness test send is a fixed sample that cannot sign anyone in. A Testing
code or link exists only in a scheduled invitation or reminder sent in
Testing mode, which goes to the Testing recipient. The portal opens in
Testing only while today is inside the draft campaign's dates. So, to
validate the form before the real start date:

1. In the draft campaign, move the start date to the first validation day
   and the initial invitation (and any reminders) inside the validation
   window. Invitation and reminder times must stay inside the campaign
   dates. Moving the start into the past also makes the scheduler produce
   catch-up daily reports for the days in between.
2. Soon after the invitation time passes, the scheduler sends one Testing
   invitation for **every** Family with a deliverable email address, all to
   the Testing mailbox (subject `[TEST]`, a banner naming the intended
   Family). There is no way to limit it to a few Families; for a whole
   parish this is a large burst to one mailbox, subject to the Workspace
   account's sending limits. Each carries that Family's Testing code (it
   starts with `I`) and Testing link (`/access/test.…`).
3. Sign in by the link, or by the code on the portal's home page `/`, choose
   **Continue with test**, fill in the form, tick the acknowledgment and
   choose **Submit test response**. A Testing receipt then arrives at the
   Testing mailbox. Test answers never count, never appear in reports and are
   deleted at activation.
4. Check the daily and weekly Admin reports and a manual weekly report at the
   Testing mailbox (they show zero participation: they count only live
   answers), the reports and exports pages (which exclude Testing answers by
   design), and the Production readiness page's list of Testing submissions.
5. Before Production readiness, move the invitation and reminders back to
   their real dates first, then the start date, and wait until every Testing
   message has finished (delivered, failed or cancelled): readiness requires
   it, and cleanup at activation deletes all Testing data. A Testing message
   whose outcome is unknown does not finish by waiting; an Administrator
   resolves it as the launch runbooks'
   [unknown-delivery procedure](stewardship-launch-runbooks.md#messages-in-delivery_unknown)
   describes.

**Browsers.** On a phone and on a desktop browser, check the Family portal's
code entry, link sign-in, every form step, the review and submit, and
sign-out, and the main staff pages: home, campaign and schedules,
reports and exports, deliveries, and background work.

**Load.** The launch scope's single load check at the parish's real Family
count runs against this deployment; its tool is development work scheduled
for September 25–29 and is not yet available.

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

1. **Check the release, then back up.** Read the release notes first: a
   release that narrows a runtime grant cannot be taken by this procedure
   (see step 4). Then run the backup (`run --rm backup-worker`, as the
   [backup runbook](stewardship-backup-runbook.md#the-nightly-backup) says)
   and confirm its off-host copy. Do not continue without it: it is the only
   rollback, and step 4 refuses a configured deployment whose newest recorded
   backup is more than 24 hours old.
2. **Stop the online services**: `caddy`, `web`, `worker`, `scheduler`,
   `mail-dispatch`, `config-installer` and every credential installer, with
   `stop` on the current Compose file and project name. Leave `postgres` and
   `valkey` running. Stopping, not restarting, matters: online services use
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
4. **Migrate.** Pull the new image (`pull` on the rewritten Compose file).
   Then, *only when the release changed the schema or the runtime grants*,
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
   schema nor a grant still pulls, but skips the migration and grant
   commands. `database-grants` never revokes: for a release that
   *narrows* a runtime grant on a table that still exists, it refuses the
   whole run, because a login already holds a privilege the new release no
   longer lists, and the new release's services would refuse that excess
   privilege anyway. Step 1's check
   catches such a release before anything stops: before the schema freeze it
   is taken by reinstalling, and after it the release must bring its own
   revocation step. `migration` runs first and commits, so if
   `database-grants` refuses after a successful migration, start neither
   image: recover with the database-restore [rollback](#rollback) (or,
   before the freeze, by reinstalling).
5. **Refresh the static files.** `caddy` serves the packaged JavaScript and
   stylesheets from `cache/static`, which `collect-static` fills once and
   never overwrites, so a release that changes or adds a static file would
   otherwise ship its templates with the previous release's scripts. With
   `caddy` still stopped, move `cache/static` aside under the name of the
   release being replaced (for example `cache/static.PREVIOUS_DIGEST`, never
   deleting it), create an empty `cache/static` owned by `10001:10001` with
   mode `0700`, and run `collect-static` into it in the *new* image, exactly
   as first installation does. Keep the old tree until the release is
   accepted; a rollback puts it back.
6. **Start and check.** Bring the online services back with `up --detach`
   on the same Compose file (`compose.json` or `compose-slack.json`, whichever
   the deployment uses) and project name, then `caddy`. Run the health
   command and open the public origin. Confirm in the portal that background
   work resumed: the home page's latest refresh time advances and the
   background task pages show the scheduler running.

Record the new digest in the operators' notes. The old image stays in the
registry; nothing here deletes it.

## Rollback

An application-only rollback is possible only when the new release changed
neither the schema nor any runtime grant:
stop the online services, run `retarget-image` with the previous digest (in
that previous image), pull, put the previous release's static tree back
(move the new `cache/static` aside and restore the one step 5 kept, or
collect into an empty `cache/static` in the previous image), and start;
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
