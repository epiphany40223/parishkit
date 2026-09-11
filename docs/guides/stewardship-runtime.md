# Stewardship runtime operator guide

This guide covers the [Phase 1C runtime foundation](stewardship-phase-1c.md),
following the [operations specification](../specs/stewardship/operations/spec.md).
It is not production deployment or release approval. Until the Phase 1C review
gate and later product owners finish their work, use disposable/local deployments.
The [deployment settings reference](../development/stewardship-deployment.md)
describes common path precedence and non-secret configuration inputs.

## Storage and identities

Application and infrastructure containers use filesystem UID/GID `10001:10001`.
Only exact mounts determine their file authority; PostgreSQL logins are separate
for each role and credential target. Directories are `0700`, private files `0600`.
Online containers never repair ownership or permissions. Do not recursively
chown an existing deployment to overcome startup refusal.

Operational Compose uses its private PostgreSQL service, with no published SQL
port and explicit non-TLS transport on that isolated bridge. Runtime/provisioning
reject remote SQL hosts instead of negotiating libpq's unverified TLS fallback.
Literal loopback is admitted for isolated operator/test connections; the Compose
renderer still requires `postgres:5432`. External SQL needs a separately designed
verified-TLS profile and is not supported by this runtime.

Linux production uses rooted bind mounts by default. Prepare an empty runtime
root owned by the deployment UID/GID, and run the provisioning command as that
owner. A host operator may create/chown that explicitly selected **new empty**
directory before running the non-root helper; no existing application data is
adopted. Override independent stores and credential paths in the input YAML.
All destinations must be empty and owner-only at initial provisioning.
An overridden credential file must have a dedicated private parent containing
only that target's credential and recognized private protocol files. A shared
directory is not an acceptable installer write mount, even outside the runtime
root. The generated Valkey ACL directory is reserved independently of the broker
password-file override.

Docker Desktop file shares may report unexpected ownership despite host chmod
or chown. Use a newly created native Docker volume instead. The operator initializes
ownership of that empty volume for UID/GID `10001:10001`; only this provisioning
helper sees the broad new storage root. Application services subsequently receive
individual file/directory bind mounts, never the volume root. Determine the
volume's actual daemon-side `Mountpoint` using `docker volume inspect`, and pass
its runtime-root subdirectory as `--bind-source-root`. This rewrites Compose bind
**sources** below the configured runtime root, leaving container targets and
configuration references unchanged. External overrides remain explicit host paths
and need independently provisioned mounts; the option does not map arbitrary
outside paths into the volume. Do not guess a Docker Desktop host-side path.

The native-volume fixture in
[`test_runtime_provisioning_container.py`](../../tests/stewardship/test_runtime_provisioning_container.py)
demonstrates UID ownership, network-disabled provisioning, source mapping and
private static-file collection. Its fixture-only root helper is not an application
service and is restricted to its newly created UUID-named volume.

## Initial preparation

Prepare a non-secret deployment YAML outside the new runtime root. Set an absolute
runtime root, profile and public origin. Production requires a standard HTTPS DNS
origin and the immutable approved GHCR application digest; development uses
`parishkit-stewardship:development`. The operational supervisor/rotation contract
currently supports one web container with multiple Gunicorn workers. All process,
thread, connection, download and overlap budgets still undergo validation.

Run in the application image as UID/GID `10001:10001`, with no network, read-only
root, dropped capabilities and no-new-privileges, mounting only the new destination
storage read-write and the input YAML read-only:

```text
pk-stewardship provision-runtime --config /run/operator.yaml --image IMAGE
```

For native volumes add `--bind-source-root DAEMON_RUNTIME_ROOT`. For development
reload add `--checkout ABSOLUTE_HOST_CHECKOUT`; the generated consumer source mount
is read-only and only its `src` subtree is exposed. Do not put secrets in source.

This creates independent SQL and web-Valkey passwords, the restricted server ACL,
the stable startup inode, exact per-service YAML, and `config/services/compose.json`.
It creates empty provider/key/handoff/storage directories, but does not connect
to a database, initialize an application, start services, or generate provider
credentials. The private provisioning intent/completion markers are not ordinary
application config and must not be edited to bypass refusal.

An interrupted preparation resumes with the **same** input configuration, image,
checkout and source mapping. Existing generated passwords are retained; conflicting
artifacts cause refusal. A completed preparation refuses repetition. Never delete
its markers to turn a populated deployment into a fresh installation.
Each retry rechecks the closed planned storage inventory. An empty/partial first
intent can resume only before any other artifact exists; mismatched or unexpected
state is preserved and refused, not repaired or deleted.

Collect packaged public assets with a separate fresh non-HTTP process, mounting
only the empty static destination read-write:

```text
pk-stewardship collect-static --destination RUNTIME_ROOT/cache/static
```

The collector uses a dummy database and ephemeral process signing value. No
deployment/provider credential is required. It refuses nonempty targets and never
clears a directory. Inspect a partial failed output, preserve it under a separate
operator-selected name and recreate an empty destination at the configured path
before retrying; do not mix user uploads or authenticated exports into public static
storage. Caddy mounts the completed static tree read-only.

Supply the Google OAuth client document in its exact owner-only credential file:
JSON with only `client_id` and `client_secret`. Configure authorized Google redirect
URIs for the selected origin. Do not use Google Workspace mail credentials here.
No other provider credential is needed for this pre-wizard foundation.

## First database and application startup

Use the generated Compose file and a stable explicit project name for **every**
command. The following arguments follow that common `docker compose` prefix;
replace the placeholders with the exact generated profile configuration paths.
Generate and retain one deployment UUID and select the initial Admin Google email.

1. `up --detach --wait postgres valkey` starts only persistent dependencies.
2. `run --rm database-provision database-roles --config PROVISION_CONFIG --confirm-deployment UUID`
   binds the empty database and new restricted SQL identities to this deployment.
3. `run --rm bootstrap bootstrap --config BOOTSTRAP_CONFIG --phase prepare --deployment-id UUID --admin-email EMAIL`
   prepares minimal Testing authority, independent application keyrings and
   per-target private handoff keys. No online service may run yet.
4. `run --rm migration` applies the current schema once with the separate schema
   owner and establishes the configured initial download limit through its guard.
5. `run --rm database-provision database-grants --config PROVISION_CONFIG --confirm-deployment UUID`
   installs the closed runtime grants. It never grants future tables automatically.
   Unexpected existing broader grants cause refusal, not automatic revocation.
6. Repeat the bootstrap command with `--phase import` and the same UUID/email.
   Only exact empty/matching database/YAML/key state can be materialized.
7. Start `web`, `config-installer` and target credential installers. After readiness
   and authorized ingress prerequisites, start `caddy` for production. Later worker,
   scheduler, provider and product workflows remain owned by subsequent phases.

Initial bootstrap is not the product setup wizard. The initialized minimal Testing
authority lets the initial Google Admin reach the pre-wizard state; the completed
parish/campaign setup UX is Phase 2. Existing application data without a matching
initialized authority is not imported or silently adopted.

Long-running production services use `unless-stopped`; one-shot offline profiles
and development services do not automatically restart. Explicitly stop online
services for maintenance so a crash restart cannot keep competing with offline
exclusion. Docker health status does not itself restart a live-but-unhealthy
service or authorize ingress. See the [Compose service reference](https://docs.docker.com/reference/compose-file/services/).

## Health, credentials and incidents

Public Caddy routes deny `/health/*` and `/metrics` before forwarding. Internal
liveness is intentionally separate from business/dependency readiness. Check actual
database schema/capacity, coherent authority, broker and private storage using:

```text
docker compose ... exec -T web pk-stewardship health --config WEB_CONFIG
```

The result contains fixed check names and booleans only: exit `0` means all checks
pass, `1` means a dependency check failed, and `2` means diagnostic admission failed.
Do not route traffic away merely because a campaign/business readiness gate closes.
Runtime and proxy logs omit private request/header/query/error values; preserve
structured status/correlation evidence instead of enabling raw credential logging.
Selected installer failures log `installer_request_failed`, using the durable
request UUID as the correlation ID and a closed database, credential,
configuration, filesystem or unexpected-failure category. A separate failed-pass
event records the loop's retry; neither event includes exception text or paths.
Readiness and metrics observations have short caches and bounded response waits;
a hung dependency cannot spawn unlimited monitoring threads. Their two possible
SQL observation connections per web process are included across rollout overlap
in the deployment's auxiliary reserve and actual web-role connection limit.
The default total connection budget is now 91, including 8 auxiliary connections.
Metrics omit dependency samples while no observation is available; readiness
still fails closed. A missing sample is not manufactured into a dependency-down
value. Loopback Host aliases are admitted only for internal health/metrics probes,
not ordinary application routes.

Every long-running service has a liveness check. Installer checks read only private
PID/start-time and recent-loop heartbeat evidence: a loop stuck beyond 90 seconds
is unhealthy, while a completed failed pass still proves liveness. Use
`exec -T SERVICE pk-stewardship installer-healthcheck` for either installer type.
Valkey liveness requires its ordinary unauthenticated ping denial; proxy liveness
checks only its local listeners. Neither is external-provider or campaign readiness.

The [credential installer guide](stewardship-credential-installers.md) controls
rotation and recovery. Metrics uses an independent public receipt, not a stored
token hash. After installation, recreate the **whole** web service and run its
operator confirmation command inside that container. An old inode, incomplete
worker cohort or one-off `compose run` container cannot acknowledge. Installers
cannot restart consumers and receive no Docker socket. Provider/key-retirement
validators remain unavailable until their owning features supply real validation.

Worker-cohort receipts and the supervisor PID file are intentionally private,
ephemeral files under `/tmp/stewardship-consumer` in the individual web container's
tmpfs and PID namespace. They are not durable deployment-root data and must not
be shared across deployments, mounted from a host directory or backed up.
Direct host Gunicorn/cohort operation is not a supported deployment profile;
use the admitted single-container supervisor and command. Durable configuration,
credentials, data and report paths retain their documented overrides.

If a required path has wrong ownership, unknown contents or a symlink, stop the
affected rollout and compare it with the intended mount inventory. Do not broaden
permissions, mount a whole credentials tree, reset a password or delete a journal
as a troubleshooting shortcut. Retry the matching durable operation or use the
authorized recovery procedure for the identified mismatch.

## Offline work and upgrade boundary

Stop web, proxy and every online installer/worker before offline work. Dependency
containers may remain running. The same stable `startup.lock` inode is held shared
for every online process lifetime and exclusively for offline work. A surviving
child retains exclusion; do not replace or unlink the lock file to force progress.
All invocations must use the same resolved runtime/configuration paths.

Preview the current verified policy and minimal proposed diff using the separate
offline profile before confirming a recovery:

```text
docker compose ... run --rm admin-recovery preview-admin-recovery --config RECOVERY_CONFIG --confirm-deployment UUID --target-email EMAIL
```

This displays deployment/parish identity, current Admin addresses, the normalized
target, before/after roles, manual provenance, and any explicit-deny access grant.
It does not create a recovery request or mutate policy. Its output contains
operator-visible email addresses; do not publish it as generic support diagnostics.

`recover-admin` uses its separate operator profile, exact deployment/target
confirmation, operator attribution and the existing versioned additive-only
recovery engine. It cannot create an alternate login method, remove Admins or edit
general configuration. Supply `--operation-id`, `--operator-name`, `--reason`,
`--confirm-deployment`, `--target-email` and matching `--confirm-email`; there is
no default yes. Before activation the command prints its current SQL-authoritative
preview, including on matching operation replay. Complete normal Google sign-in
afterward. Follow the
[normative recovery protocol](../specs/stewardship/operations/spec.md#offline-admin-access-recovery);
notifications retain their later delivery-worker owner; offline recovery does not
send provider email itself.

Configured-deployment migration, SQL changes and production upgrades remain held
until OPS-05 supplies verified recent backup evidence. The first-deployment command
is not a backup bypass. Future upgrades require a pinned image, compatible schema,
expand/migrate/contract discipline, verified backup/restore evidence, and readiness
checks before rollout. An incompatible schema requires the approved restore path,
not an older image pointed at a newer database. Keep credential escrow separate
from ordinary backup output and retain the matching key material.

Before authorized real TLS issuance, verify DNS resolves to the VM, public TCP
80/443 reach only Caddy, no database/broker port is published, and the Google origin
and redirect URIs match. Caddy owns only its `/data` and `/config` persistent stores
plus temporary storage; preserve that certificate/ACME state on replacement.
The local-CA test proves persistence/isolation, not real ACME issuance or renewal.
Never erase certificate state as a default recovery action; restore the approved
matching state or explicitly reissue under the operator's certificate policy.
