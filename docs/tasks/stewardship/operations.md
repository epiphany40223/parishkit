# Operations and quality tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/operations.md) ·
[Normative specification](../../specs/stewardship/operations/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## OPS-01: Development and production Compose topology

Scope and dependencies: [OPS-01 work package](../../plans/stewardship/operations.md#ops-01-development-and-production-compose-topology).

- [x] OPS-01.01 — Create development and production Compose definitions.
- [ ] OPS-01.02 — Build one non-root image with explicit service commands.
- [x] OPS-01.03 — Implement cross-platform bind-mounted development reload.
- [x] OPS-01.04 — Restrict service ports to the intended networks.
- [x] OPS-01.05 — Test and document Compose startup.

Evidence: Phase 0 Compose scaffold commit `72f31f3` on
`pr/stewardship-implementation`. See the
[Compose guide](../../development/stewardship-compose.md),
[`deploy/stewardship`](../../../deploy/stewardship/),
[`test_services.py`](../../../tests/stewardship/test_services.py), and
[`test_compose.py`](../../../tests/stewardship/test_compose.py).
The counts below describe that historical implementation run. Subsequent
corrections and current host/image/Compose validation are recorded in the
[M0 review evidence](milestones.md#phase-0-skeleton).
September 7, 2026: application image built on macOS/arm64 with Docker 29.7.2 and
Compose 5.3.1. Host and in-image baseline each passed 570 tests (3 explicitly
opt-in Docker checks skipped). The separately enabled Compose suite passed
5 tests, including both rendered overlays, real source reload, localhost/internal
route checks, no token-bearing access logs, non-root web execution, synthetic
PostgreSQL/Valkey persistence across replacement, production startup refusal,
and graceful web stop/restart. Ruff and Markdown checks passed. Both architecture
manifests are pinned; native Windows/Linux-host and amd64 execution are not yet
claimed and will receive CI/runtime evidence in OPS-09.

OPS-01.02 is partial: one non-root image provides the working development web
entry point, init/signal handling, read-only root, and local liveness probe.
Reserved worker/installer/operator identities have no credential/data mounts
and explicitly refuse startup; they do not pretend to process work or pass
health checks. Their concrete commands/mounts/readiness arrive with the owning
ARC-06, background, and OPS-02/04 packages. Production remains fail-closed.
OPS-02 still owns CLI/YAML-to-Compose path integration, full provisioning, and
mount/queue isolation. No review gate, deployment, or release is claimed.

## OPS-02: Durable runtime paths and least-privilege secrets

Scope and dependencies: [OPS-02 work package](../../plans/stewardship/operations.md#ops-02-durable-runtime-paths-and-least-privilege-secrets).

- [x] OPS-02.01 — Configure durable runtime paths and overrides.
- [x] OPS-02.02 — Isolate online and offline-bootstrap configuration/credential mounts.
- [x] OPS-02.03 — Enforce secret and service-mount boundaries.
- [x] OPS-02.04 — Configure safe temporary storage and file permissions.
- [x] OPS-02.05 — Test topology, identity, isolation, and durable replacement.

Current evidence: [Phase 1C runtime](../../guides/stewardship-runtime.md) and
[review ledger](../../guides/stewardship-phase-1c-reviews.md). Real native-volume
provisioning, exact individual mounts, restricted SQL identities, independent
overrides, ownership, path/symlink refusal, shared/exclusive startup leases and
durability across consumer/proxy replacement pass the 48-check container suite.
Linux containers run on Docker Desktop; native Linux-host CI is still required
at PR handoff, and native Windows execution is not claimed. Reserved later
services remain denied until their owners land. No production approval is implied.

Before granting runtime database access or releasing Gate 1, harden existing SQL
checkpoint/activation/secret emitters and their helper guards against caller
search-path shadowing. Audit-ownership review round 3 identified unchanged
unqualified audit INSERT targets in accounts migrations 0010, 0013 and 0015.
Use forward migrations with fixed trusted paths/schema-qualified targets, respect
optional migration ordering, restrict schema creation/temporary-object privileges
as appropriate, and test each real emitter with a temporary shadow audit table.
The new audit insertion guard protects attribution only once an INSERT reaches
the real table; it does not resolve those pre-existing emitter paths. See the
[scope and review disposition](milestones.md#audit-ownership-increment).

Phase 1C prerequisite: forward migration `0033_trusted_trigger_paths` pins the
legacy emitters and helper guards. Two new temporary-shadow/inventory tests and
117 existing PostgreSQL integration tests pass. Runtime grants and the remaining
OPS-02 scope are still in progress; see the [execution evidence](../../guides/stewardship-phase-1c.md).

## OPS-03: Production ingress, TLS, and network security

Scope and dependencies: [OPS-03 work package](../../plans/stewardship/operations.md#ops-03-production-ingress-tls-and-network-security).

- [x] OPS-03.01 — Configure persistent Caddy TLS, trusted forwarding, and ingress hardening prerequisites.
- [x] OPS-03.02 — Redact credentials from access logs.
- [x] OPS-03.03 — Deny internal health and metrics paths at public ingress.
- [x] OPS-03.04 — Document DNS, firewall, OAuth, and certificate recovery.
- [ ] OPS-03.05 — Validate Caddy hardening, writable-state limits, and network/route boundaries before enabling ingress.

Current evidence: the [operator guide](../../guides/stewardship-runtime.md) and
real production-shaped Compose regression cover standard-port HTTPS redirects,
forged forwarding-header normalization, private-route denial, redacted successful
and failed proxy logs, non-root/restricted-capability execution, writable-state
limits, certificate persistence and backend-network denial. Tests use a local CA,
not real ACME issuance. OPS-03.05 awaits native Linux CI and final Gate 1 validation;
production deployment/issuance is not authorized.

## OPS-04: Bootstrap, migrations, startup, and upgrades

Scope and dependencies: [OPS-04 work package](../../plans/stewardship/operations.md#ops-04-bootstrap-migrations-startup-and-upgrades).

- [x] OPS-04.01 — Integrate bootstrap, offline Admin recovery, startup exclusion, health, and budget validation.
- [x] OPS-04.02 — Run migrations once before service rollout.
- [ ] OPS-04.03 — Implement backup-aware image upgrades and readiness checks.
- [x] OPS-04.04 — Document schema evolution and recovery procedures.
- [ ] OPS-04.05 — Test bootstrap isolation, startup races, mismatch, crash, and upgrade paths.

Current evidence: [runtime startup and recovery](../../guides/stewardship-runtime.md)
and the [review ledger](../../guides/stewardship-phase-1c-reviews.md). Development
and production-shaped tests perform role provisioning, key/bootstrap preparation,
schema-owner migration, restricted grants, bootstrap activation, configured
restart, offline recovery preview/confirmation, mutual exclusion and supervisor
crash recovery. Negative tests cover drift, mismatches, partial provisioning,
mounts and process/SQL budgets. OPS-04.03 and the upgrade part of .05 remain held
for OPS-05's verified-backup evidence; configured migrations fail closed instead
of implementing an unverified upgrade. Initial-install/restart scope is complete.

## OPS-05: Backup service and purge-triggered backup

Scope and dependencies: [OPS-05 work package](../../plans/stewardship/operations.md#ops-05-backup-service-and-purge-triggered-backup).

- [ ] OPS-05.01 — Implement consistent data/config/media backup manifests.
- [ ] OPS-05.02 — Encrypt, transfer, verify, and retain complete backups.
- [ ] OPS-05.03 — Route backup creation and revalidation through the isolated backup worker.
- [ ] OPS-05.04 — Implement operator-only secret escrow and off-host recovery verification.
- [ ] OPS-05.05 — Test backup failures, revalidation, retention, RPO alerts, and restore evidence.

Evidence: Not started.

## OPS-06: Restore and state-aware release

Scope and dependencies: [OPS-06 work package](../../plans/stewardship/operations.md#ops-06-restore-and-state-aware-release).

- [ ] OPS-06.01 — Implement validated empty-target and disaster-recovery restore.
- [ ] OPS-06.02 — Restore data, invalidate sessions/link generations, and set maintenance gates.
- [ ] OPS-06.03 — Implement restricted maintenance queues and controls.
- [ ] OPS-06.04 — Inventory uncertain deliveries and durable holds.
- [ ] OPS-06.05 — Integrate atomic lifecycle-aware release with fresh link-token activation.
- [ ] OPS-06.06 — Test every restore state, uncertainty window, and resend case.

Evidence: Not started.

## OPS-07: Housekeeping and retention jobs

Scope and dependencies: [OPS-07 work package](../../plans/stewardship/operations.md#ops-07-housekeeping-and-retention-jobs).

- [ ] OPS-07.01 — Implement temporary retention and owner-only export storage.
- [ ] OPS-07.02 — Implement separate protected source-snapshot and derived-fact compactors.
- [ ] OPS-07.03 — Constrain cleanup to safe owned records and paths.
- [ ] OPS-07.04 — Integrate gated Testing and exceptional purge cleanup.
- [ ] OPS-07.05 — Test retention boundaries, permissions, paths, and races.

Evidence: Not started.

## OPS-08: Observability, health, and operational runbooks

Scope and dependencies: [OPS-08 work package](../../plans/stewardship/operations.md#ops-08-observability-health-and-operational-runbooks).

- [ ] OPS-08.01 — Implement structured operational and audit logging.
- [ ] OPS-08.02 — Implement internal authenticated metrics and credential rotation.
- [x] OPS-08.03 — Implement minimal health and detailed CLI diagnostics.
- [ ] OPS-08.04 — Integrate deduplicated alerts and recovery status.
- [ ] OPS-08.05 — Write operational failure and recovery runbooks.
- [ ] OPS-08.06 — Exercise runbooks using controlled failure injection.

Foundation evidence: [runtime guide](../../guides/stewardship-runtime.md) and
[review ledger](../../guides/stewardship-phase-1c-reviews.md). Structured redacted
runtime/audit events, request-linked installer diagnostics, bounded internal
dependency metrics, bearer protection, minimal health and protected CLI diagnosis
are implemented. Real composed metrics replacement verifies old-inode refusal,
whole-worker-cohort recreation/acknowledgement and final applied state. Controlled
database/broker, worker-crash, lease and filesystem failures exercise the baseline
runbooks. Broader feature metrics, durable log ingestion/viewing, Admin/Slack
delivery and backup/source/mail/purge runbooks retain their later owners; mixed
tasks .01/.02/.04/.05/.06 therefore remain unchecked.

## OPS-09: CI, coverage, browser, acceptance, and release pipeline

Scope and dependencies: [OPS-09 work package](../../plans/stewardship/operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline).

- [ ] OPS-09.01 — Build repository lint, test, migration, and frontend CI.
- [x] OPS-09.02 — Enforce separate scoped line and branch coverage floors.
- [ ] OPS-09.03 — Build integration, browser, Compose, and image validation jobs.
- [ ] OPS-09.04 — Provide credential-free CI and human-run smoke tools.
- [ ] OPS-09.05 — Complete required suites, saturated-download load tests, and acceptance traceability.
- [ ] OPS-09.06 — Publish release artifacts only after the authorized final gate.

Evidence: Initial Phase 0 coverage/CI commit `8557a78` on
`pr/stewardship-implementation`. The mandatory package plus exact shared CLI/config/path modules
are declared in [`coverage-stewardship.toml`](../../../coverage-stewardship.toml).
The [coverage runner](../../../src/parishkit/stewardship/quality.py) derives its
pytest-cov source arguments from the validated manifest and rejects incomplete
or non-branch reports before evaluating independent floors. The initial 33
regression tests included invalid-manifest categories and either-floor failures;
subsequent corrections, expanded coverage, and current validation are recorded
in the [M0 review evidence](milestones.md#phase-0-skeleton).
September 7, 2026: the complete host runner passed 603 tests (3 opt-in Docker
checks skipped), with 96.91% lines and 95.22% branches. Migration drift reported
no changes. Ruff and Markdown checks passed.

OPS-09.01 is partial: required lint/format/Markdown, manifest-derived coverage,
and scaffold migration drift are in CI; database-backed migration and frontend
checks follow their implementation. OPS-09.03 now defines a Linux CI image-build
and isolated Compose smoke job; remote execution is not yet claimed. The earlier
macOS/arm64 smoke evidence remains under OPS-01. Browser/accessibility, full
database integrations, multi-architecture release/SBOM/scanning/provenance, all
acceptance/load suites, and real-provider human-run smoke tools remain open.
All current tests are fake-backed. No image or release tag is published.
