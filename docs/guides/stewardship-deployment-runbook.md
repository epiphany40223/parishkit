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
linked guide disagree, the linked guide is right; fix the runbook.

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
  Keep it in an operator-controlled place; it holds no secrets.
- The **Google OAuth client** (web application type) whose authorized
  redirect URIs are on the public origin, exported as JSON with only
  `client_id` and `client_secret`; the initial Administrator's Google address;
  the ParishSoft API key; the mail-provider account; and, optionally, the
  Slack webhook. All but the OAuth client are entered through the setup
  wizard and installed by the credential installers, not copied onto the
  host by hand.
- A generated deployment UUID, recorded where the operators keep it. Every
  offline command that confirms the deployment takes the same UUID.

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
only to staff test addresses and no Family link is live. Before the pre-launch
gate, the human runs the smoke tools of the launch scope's item 5 against this
deployment, staff validate the Family form, content, templates, schedules and
reports, and the load check of the launch scope's reduced item 7 runs here.
Bugs found now are fixed by ordinary pull requests and reach the host through
the [upgrade](#upgrade) below, except a release that adds a SQL login, a
runtime path or a table to the fresh-install baseline: retarget and migration
cannot create those in an existing deployment, so before the schema freeze the
validation deployment is reinstalled from scratch to pick such a release up.
The v1 backup release is one of them: a deployment provisioned before it has
no backup login, password, directory or record table, and must be reinstalled.

## Production activation

Activation is an Administrator's action in the portal, not an operator's
command: the campaign's go-live page checks readiness, cleans up the Testing
state and activates Production, as the
[go-live readiness](stewardship-go-live-readiness.md) and
[production activation](stewardship-production-activation.md) guides describe.
Before the Administrator activates, the operator runs the backup and confirms
its off-host copy, as the [backup runbook](stewardship-backup-runbook.md)
says. Activation is scheduled for October 1, 2026 in the launch
scope's [schedule](../plans/stewardship/v1-launch.md#schedule).

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

1. **Back up first.** Run the backup now (`run --rm backup-worker`, as the
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
   process log carries one fixed sentence naming the missing backup. A release that changes neither the schema
   nor a grant skips this step entirely.
5. **Start and check.** Bring the online services back with `up --detach`
   on the same Compose file (`compose.json` or `compose-slack.json`, whichever
   the deployment uses) and project name, then `caddy`. Run the health
   command and open the public origin. Confirm in the portal that background
   work resumed: the home page's latest refresh time advances and the
   background task pages show the scheduler running.

Record the new digest in the operators' notes. The old image stays in the
registry; nothing here deletes it.

## Rollback

An application-only rollback is possible when the new release made no schema
change: stop the online services, run `retarget-image` with the previous
digest (in that previous image), pull, and start; step 4 is not repeated.
When the release changed the schema, an older image must never be pointed at
the newer database; the rollback is a database restore from the backup taken
in upgrade step 1, following the launch scope's
[manual restore procedure](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2)
and the [backup runbook](stewardship-backup-runbook.md#restore-for-real), and
it must include the image: with every online
service still stopped, after the database is restored and before anything
is started, run `retarget-image` back to the previous digest, or the new
image would start against the restored, older schema and refuse. The
restore procedure keeps the scheduler, worker and mail-dispatch services
stopped until an Administrator has compared the restored delivery state with
the mail provider's own logs, so no Family message is sent twice.

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
- The image is single-architecture (`linux/amd64`); the host must be x86-64.
- Restore is a manual procedure and may require re-sending some Family links
  by hand, as the launch scope records for the pre-launch gate to approve.
- Keys generated at install are not rotated during v1.
