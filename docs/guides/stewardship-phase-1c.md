# Stewardship Phase 1C execution

Current review tracking: [Phase 1C review ledger](stewardship-phase-1c-reviews.md).

This batch follows the [controlling plan](../plans/stewardship/overall.md#1c-operational-foundation)
and [task sequence](../tasks/stewardship/overall.md#phase-1c-runtime-foundation).
It starts from PR #20's merged tip,
`18a37cb5b6c90bbf2b5f60c5fff37f199cd52201`, on
`pr/stewardship-phase-1c`. PR and merge-queue CI passed before branching.

## Scope and internal checkpoints

1. Harden legacy SQL emitters and provision least-privilege runtime access.
2. Integrate durable paths, exact credential mounts and service identities.
3. Implement offline bootstrap/recovery exclusion, migrations and validated
   runtime startup, preserving later owners' disabled capabilities.
4. Harden production ingress and verify actual container/network boundaries.
5. Complete baseline health, metrics, diagnostics and operational runbooks.
6. Run integrated validation and at least three independent dual-vendor
   review-and-correction rounds, including the complete Gate 1 foundation scope.
7. Publish one coherent Phase 1C PR, correct CI, and request human merge/Gate 1
   approval before Phase 2.

These are internal checkpoints, not separate PR boundaries. Source import,
the product setup wizard, Family forms, task/mail workflows, backup/restore and
destructive workflows retain their later-phase owners. No deployment, release
or real-provider mutation is authorized.

## Evidence

Implementation in progress. The first prerequisite adds a forward migration
pinning the legacy configuration/secret trigger search paths and a PostgreSQL
regression that exercises their real effects with a temporary shadow audit
table. Both new PostgreSQL tests and 117 existing configuration/secret/installer
integration tests pass on the disposable database profile. The migration does
not require optional audit projections or rewrite historical migrations, and
reversing its marker does not weaken the lookup-path defense.

Runtime path validation, stable offline/online lifecycle leases and finite
deployment budget primitives are being integrated. No task completion,
production startup or review-gate release is claimed at this checkpoint.

### Runtime primitives and initial authority

The runtime now has strict durable-path/override validation, an owner-only stable
startup inode with shared online/exclusive offline leases, and finite connection
and timeout budgets. Tests include actual cross-process exclusion and inherited
child leases; closing the launcher does not release a surviving child's lease.

Initial authority uses `bootstrap-policy-v1`, containing only the exact initial
Admin rule. It does not invent a parish profile. Forward migrations bind its
policy projections, keep pre-wizard audit deployment-owned, prohibit unrelated
projections and complete-to-bootstrap regression, and refuse populated downgrade.
The historical complete schemas remain unchanged. The matching bootstrap recovery
format retains the existing additive-only, versioned recovery engine and durable
revocation/security evidence.

Two internal offline bootstrap phases now generate independent purpose-bound
keys and target-specific handoffs, then import the exact initial authority into
the migrated empty database. Per-target owner-only candidate journals allow a
crash retry to compare exact bytes before publication; they are secret files,
not configuration, audit, ordinary backup or status output. Configured bootstrap
cannot become a rotation/recovery bypass. After durable activation, a separate
non-secret retirement marker permits interrupted cleanup of those exact temporary
secret copies. Cleanup never changes the working credentials or repeats activation.
These primitives alone do not authorize deployment.

Focused validation includes 112 PostgreSQL configuration/activation/bootstrap
tests, followed by 70 activation/bootstrap tests after the two-phase file/import
integration. The latter covers a committed activation interrupted before its
completion marker and a key changed between phases. Pure tests cover file-write
crashes, mismatches, permissions, online exclusion and deterministic identity.

### Operational integration checkpoint

The in-progress implementation now includes offline bootstrap, migration and
Admin-recovery command dispatch; exact kernel mount inventories; closed per-role
SQL grants; operator-only initial database provisioning; internal health/metrics
and protected CLI diagnostics; and a concrete Compose/Caddy renderer. The web
process and configuration installer retain shared startup leases through their
lifetimes. Gunicorn uses finite process/thread/drain budgets, private error output
and development worker reload without enabling debug pages.

Database provisioning is a separate explicitly invoked operator profile. It reads
individual SQL password files, creates SCRAM-verifier logins with no inherited,
superuser, database-creation or role-creation authority, and binds initial database
and role ownership to the confirmed deployment UUID. Matching retries verify
credentials instead of replacing passwords. Runtime roles have no TEMP/CREATE
authority; only the migration login owns the application schema. Configured
database changes and upgrades remain held pending the later verified-backup owner.

Forward migration `0036_bootstrap_empty_database` prevents initial bootstrap from
adopting unrelated data even when no configuration singleton exists. Its narrow
schema-owner trigger can inspect empty-state evidence without granting bootstrap
general data reads or executable definer authority. Reviewed SELECT-only policies
let that offline schema owner inspect the three FORCE-RLS credential tables;
unreviewed future row-security tables fail closed. Framework migration/permission
metadata and the initial guarded download policy are the only seed exceptions.

The renderer keeps Caddy off the database/broker bridge, denies internal routes
before proxying, bounds HTTP input/transport, removes private request/error fields
from access and runtime logs, and retains certificate state separately. Stock
Caddy's binary carries a `NET_BIND_SERVICE` file capability: real execution with
an empty bounding set failed even on high internal ports. Caddy therefore drops
all capabilities and adds only `NET_BIND_SERVICE`; it still runs as UID/GID
`10001:10001`, with read-only root and no-new-privileges. PostgreSQL and application
services retain an empty capability set. The high internal ports remain 8080/8443,
mapped to public 80/443. See Caddy's
[port/server options](https://caddyserver.com/docs/caddyfile/options),
[filter encoder](https://caddyserver.com/docs/caddyfile/directives/log), and
[transport controls](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).

Checkpoint evidence, not final Gate 1 acceptance:

- The credential-free baseline passed 2,451 tests, with 1,025 database/browser/
  Docker opt-in cases skipped. Further additions require a final repeat.
- Fifteen PostgreSQL bootstrap tests cover activation, recovery, unrelated-data
  refusal and interrupted journal retirement.
- Three explicitly enabled container tests passed: stock Caddy adaptation and
  two isolated PostgreSQL provisioning scenarios. PostgreSQL runs non-root with
  no capabilities; real SCRAM logins, matching retry, mismatch refusal, migration
  ownership, narrow grants and bootstrap materialization are exercised. The
  provisioning scenario also admits the resulting web SQL role and reads the
  coherent Testing authority. No live provider credentials are used.
- Pure tests exercise generated mount/profile inventories, network and image
  validation, offline commands, real process exclusion, bounded installer retry,
  protected metrics and diagnostic-output privacy.
- The broader PostgreSQL quality run passed all 933 cases in eight minutes.
  Its intermediate scoped coverage was 89.86% lines and 80.73% branches; final
  coverage must be repeated after this batch's remaining integration work.
- One complete fake-backed development Compose scenario passes on Docker Desktop
  using native Linux owner-only inodes. It runs the actual role-provisioning,
  pre-bootstrap, migration, grant-provisioning and bootstrap-import commands,
  starts web/config-installer, checks liveness and protected detailed readiness,
  and refuses an offline migration while online startup leases are held. No
  production ingress or full replacement/rollout validation is implied.

The composed test exposed two controls missing from the earlier unit fixtures:
Compose's individual read-only Docker init executable mount, and Valkey INFO/GET
operations needed by durable limiter restart/eviction detection. The mount policy
now admits only exact stock init paths, and the web ACL grants only the limiter/
metrics vocabulary and key prefixes. Flush, ACL/configuration administration and
other services' queue keys remain unavailable.

Host/native-volume provisioning, complete composed runtime startup and replacement,
credential-consumer rotation/recreation, production ingress isolation/persistence,
runbook failure injection and final coverage/browser/review rounds remain in this
same Phase 1C batch. Generated definitions are still internal integration output,
not an authorized deployment or a completed task/review gate. Later feature-owned
services remain unavailable until their implementations are admitted.

## Credential-consumer integration checkpoint

Metrics credentials now use a strict owner-only JSON document with an independent
random public receipt and a separate bearer token. Durable request, checkpoint,
acknowledgement and audit records contain that receipt, never the token or its
hash. Private installer journals retain exact-byte integrity and rollback checks.
Current and historical receipt reuse is refused.

Each admitted Gunicorn worker publishes only loaded credential receipts and
kernel PID/start-time identities into private container tmpfs. The operator-only
`acknowledge-credential --config <web-config> --request-id <UUID>` command runs
inside the recreated web service and verifies its entire live worker cohort,
actual mounted credentials, SQL authority and request state. A one-off container,
partial startup, worker disagreement or old file inode cannot acknowledge.
The current cohort confirmation supports one web container with multiple workers;
it explicitly refuses multi-container replica configurations rather than treating
one replica as evidence for all others. No Docker socket is mounted in the app.

Target-isolated credential installer process loops now run the existing durable
queue. Metrics has complete local candidate validation. Other targets retain a
bounded unavailable/retry outcome until their provider or key-retirement owners
supply the required validation; syntax-only provider acceptance is not enabled.

Validation at this checkpoint:

- Five PostgreSQL metrics scenarios pass, including apply, deadline rollback,
  current/historical receipt reuse and tampered-file refusal. Durable rows are
  checked for both token and token/file-hash absence.
- Fifty-five focused worker/metrics/process tests pass; nineteen credential
  command/process tests pass after adding the CLI integration.
- The credential-free baseline passes 2,524 tests, with 1,032 explicit opt-in
  skips. This is an intermediate count before subsequent integration additions.
- The rebuilt-image development Compose scenario passes in 24 seconds. It
  stages a sealed synthetic replacement through web's actual SQL identity,
  runs the real metrics installer, refuses old-container acknowledgement,
  force-recreates the complete web service, records its acknowledgement through
  the operator command and observes the final applied state. Separate one-off
  container confirmation is refused. Existing bootstrap, health and offline
  exclusion assertions continue to pass.

This is not Phase 1C completion. Provisioning, production ingress hardening and
execution, remaining operational runbooks/failure injection, final validation and
three review/fix rounds remain in the same PR-sized batch.

## Ingress and override integration checkpoint

The explicitly enabled Compose scenario now runs in both development and a
production-shaped topology. Production tests replace only the application image
reference with the local build, published ports with random loopback ports, and
the ACME issuer with a disposable local CA. They do not contact Let's Encrypt,
publish an image or deploy a production service.

Both scenarios pass with real overridden SQL password-file locations and a
non-default initial download capacity. The production scenario additionally
verifies actual HTTPS and standard-port redirect, private health/metrics denial,
application and static-file reachability, narrow writable Caddy state, read-only
root/static storage, non-root identity, capability bounding and no-new-privileges.
A successful Caddy-to-web connection validates the probe before its direct
PostgreSQL/Valkey connections are required to fail. The local CA root and state
sentinels survive proxy replacement. Stopping web yields a real proxy error; its
logs omit cookie/header/path/query canaries just as successful request logs do.

`postgres.password_files` maps closed service identities to independently
overridden password paths. The input profile's scalar `password_file` and web's
`download_password_file` are canonicalized into that map; conflicting references
are refused. Every generated profile and the database provisioner use matching
paths. YAML-relative paths and the per-identity environment override follow the
normal deployment precedence, for example
`PARISHKIT_STEWARDSHIP_POSTGRES_PASSWORD_FILE_WEB`. No password contents are
serialized into these documents. Offline profiles reject unrelated online
connection-file references. Protected storage/installer input overlaps and
duplicate SQL password-file identities are refused.

Initial migration establishes the configured download capacity through the
existing versioned SQL guard while offline exclusion is held and before any
deployment singleton exists. It does not authorize an online resize or bypass
the configured-upgrade backup hold.

Checkpoint validation: 145 focused configuration/path/boundary tests pass; the
credential-free baseline passes 2,539 tests with 1,033 opt-in skips. The rebuilt
development and production-shaped Compose cases both pass in approximately
65 seconds total, including complete sealed metrics rotation and offline
exclusion. Operator provisioning, operational documentation, final acceptance
and review rounds remain open; this evidence does not itself enable ingress or
release Gate 1.

## Operator preparation checkpoint

The [runtime operator guide](stewardship-runtime.md) now covers fresh private
storage provisioning, individual file overrides, Docker-native source mapping,
static collection, exact initial database/bootstrap order, diagnostics and held
upgrade/recovery boundaries. `provision-runtime` opens no database or provider
connection and does not start services. Its private immutable intent permits only
matching interrupted preparation; generated passwords are retained and differing
metadata files are refused. Completed or populated storage is not adopted.

`collect-static` runs in a fresh non-HTTP process with a dummy database and no
deployment credential. The public code-owned tree has private on-disk modes and
is later mounted read-only by Caddy. Unsafe/nonempty destinations are refused,
not cleared or repaired.

The native Docker test runs both real commands as UID/GID `10001:10001`, without
network access, capabilities or a writable root filesystem. It verifies ownership,
file/directory modes, static output and exact daemon-side source mapping. This
test exposed a fixture root-directory ownership omission; the helper now assigns
the root of only its newly created disposable volume, in addition to its contents.
Application consumers continue to receive only narrow individual mounts.

Admin recovery now provides a read-only coherent preview command and prints the
SQL-authoritative minimal diff before applying a confirmed request. Exact-operation
replay still tolerates its own recoverable YAML/database activation window.
The final composed preview/recovery and complete regression checks are in progress;
no task completion, review exit or production authorization is claimed here.
