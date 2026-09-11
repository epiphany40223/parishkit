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
cannot become a rotation/recovery bypass. The operational command/mount boundary,
deployment YAML rendering, journal retirement and complete runtime startup are
still being integrated; these primitives alone do not authorize deployment.

Focused validation includes 112 PostgreSQL configuration/activation/bootstrap
tests, followed by 70 activation/bootstrap tests after the two-phase file/import
integration. The latter covers a committed activation interrupted before its
completion marker and a key changed between phases. Pure tests cover file-write
crashes, mismatches, permissions, online exclusion and deterministic identity.
