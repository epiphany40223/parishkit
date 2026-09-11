# Stewardship Phase 1C execution

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
