# Operations and quality tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/operations.md) ·
[Normative specification](../../specs/stewardship/operations/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## OPS-01: Development and production Compose topology

Scope and dependencies: [OPS-01 work package](../../plans/stewardship/operations.md#ops-01-development-and-production-compose-topology).

- [ ] OPS-01.01 — Create development and production Compose definitions.
- [ ] OPS-01.02 — Build one non-root image with explicit service commands.
- [ ] OPS-01.03 — Implement cross-platform bind-mounted development reload.
- [ ] OPS-01.04 — Restrict service ports to the intended networks.
- [ ] OPS-01.05 — Test and document Compose startup.

Evidence: Not started.

## OPS-02: Durable runtime paths and least-privilege secrets

Scope and dependencies: [OPS-02 work package](../../plans/stewardship/operations.md#ops-02-durable-runtime-paths-and-least-privilege-secrets).

- [ ] OPS-02.01 — Configure durable runtime paths and overrides.
- [ ] OPS-02.02 — Isolate writable configuration and credential mounts.
- [ ] OPS-02.03 — Enforce secret and service-mount boundaries.
- [ ] OPS-02.04 — Configure safe temporary storage and file permissions.
- [ ] OPS-02.05 — Test topology, identity, isolation, and durable replacement.

Evidence: Not started.

## OPS-03: Production ingress, TLS, and network security

Scope and dependencies: [OPS-03 work package](../../plans/stewardship/operations.md#ops-03-production-ingress-tls-and-network-security).

- [ ] OPS-03.01 — Configure persistent Caddy TLS and trusted forwarding.
- [ ] OPS-03.02 — Redact credentials from access logs.
- [ ] OPS-03.03 — Deny internal health and metrics paths at public ingress.
- [ ] OPS-03.04 — Document DNS, firewall, OAuth, and certificate recovery.
- [ ] OPS-03.05 — Validate Caddy and test network and route boundaries.

Evidence: Not started.

## OPS-04: Bootstrap, migrations, startup, and upgrades

Scope and dependencies: [OPS-04 work package](../../plans/stewardship/operations.md#ops-04-bootstrap-migrations-startup-and-upgrades).

- [ ] OPS-04.01 — Integrate bootstrap, migration, and health commands.
- [ ] OPS-04.02 — Run migrations once before service rollout.
- [ ] OPS-04.03 — Implement backup-aware image upgrades and readiness checks.
- [ ] OPS-04.04 — Document schema evolution and recovery procedures.
- [ ] OPS-04.05 — Test empty startup, mismatch, crash, and upgrade paths.

Evidence: Not started.

## OPS-05: Backup service and purge-triggered backup

Scope and dependencies: [OPS-05 work package](../../plans/stewardship/operations.md#ops-05-backup-service-and-purge-triggered-backup).

- [ ] OPS-05.01 — Implement consistent data/config/media backup manifests.
- [ ] OPS-05.02 — Encrypt, transfer, verify, and retain complete backups.
- [ ] OPS-05.03 — Route every backup through the isolated backup worker.
- [ ] OPS-05.04 — Implement operator-only encrypted secret escrow.
- [ ] OPS-05.05 — Test backup failures, retention, RPO alerts, and restore evidence.

Evidence: Not started.

## OPS-06: Restore and state-aware release

Scope and dependencies: [OPS-06 work package](../../plans/stewardship/operations.md#ops-06-restore-and-state-aware-release).

- [ ] OPS-06.01 — Implement validated empty-target and disaster-recovery restore.
- [ ] OPS-06.02 — Restore data and start with maintenance admission gates.
- [ ] OPS-06.03 — Implement restricted maintenance queues and controls.
- [ ] OPS-06.04 — Inventory uncertain deliveries and durable holds.
- [ ] OPS-06.05 — Integrate atomic lifecycle-aware Admin release.
- [ ] OPS-06.06 — Test every restore state, uncertainty window, and resend case.

Evidence: Not started.

## OPS-07: Housekeeping and retention jobs

Scope and dependencies: [OPS-07 work package](../../plans/stewardship/operations.md#ops-07-housekeeping-and-retention-jobs).

- [ ] OPS-07.01 — Implement temporary retention and owner-only export storage.
- [ ] OPS-07.02 — Implement protected source-snapshot compaction.
- [ ] OPS-07.03 — Constrain cleanup to safe owned records and paths.
- [ ] OPS-07.04 — Integrate gated Testing and exceptional purge cleanup.
- [ ] OPS-07.05 — Test retention boundaries, permissions, paths, and races.

Evidence: Not started.

## OPS-08: Observability, health, and operational runbooks

Scope and dependencies: [OPS-08 work package](../../plans/stewardship/operations.md#ops-08-observability-health-and-operational-runbooks).

- [ ] OPS-08.01 — Implement structured operational and audit logging.
- [ ] OPS-08.02 — Implement internal authenticated metrics and credential rotation.
- [ ] OPS-08.03 — Implement minimal health and detailed CLI diagnostics.
- [ ] OPS-08.04 — Integrate deduplicated alerts and recovery status.
- [ ] OPS-08.05 — Write operational failure and recovery runbooks.
- [ ] OPS-08.06 — Exercise runbooks using controlled failure injection.

Evidence: Not started.

## OPS-09: CI, coverage, browser, acceptance, and release pipeline

Scope and dependencies: [OPS-09 work package](../../plans/stewardship/operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline).

- [ ] OPS-09.01 — Build repository lint, test, migration, and frontend CI.
- [ ] OPS-09.02 — Enforce separate scoped line and branch coverage floors.
- [ ] OPS-09.03 — Build integration, browser, Compose, and image validation jobs.
- [ ] OPS-09.04 — Provide credential-free CI and human-run smoke tools.
- [ ] OPS-09.05 — Complete required suites and acceptance traceability.
- [ ] OPS-09.06 — Publish release artifacts only after the authorized final gate.

Evidence: Not started.
