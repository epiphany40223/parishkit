# Operations and quality implementation plan

This plan implements the
[operations and quality specification](../../specs/stewardship/operations/spec.md).
Operational behavior is developed alongside application phases, not deferred to
the end.

## Work packages

### OPS-01: Development and production Compose topology

1. Create base, development, and production Compose definitions for web,
   config-installer, target-specific credential installers, general worker,
   mail-dispatch, scheduler, PostgreSQL, Valkey, and Caddy.
2. Use one application image with explicit service entry commands, non-root UID,
   signal handling, health checks, and read-only filesystem where practical.
3. Bind-mount source/templates/static inputs for cross-platform development
   reload; use immutable GHCR image references in production.
4. Keep database/Valkey/worker ports internal and publish only intended local
   development HTTP or production Caddy 80/443.
5. Add Compose config/startup smoke tests and document commands.

### OPS-02: Durable runtime paths and least-privilege secrets

1. Map config, credentials, cache, logs, reports, and run paths through the
   required root behavior; define PostgreSQL, Valkey, media, and Caddy durable
   volumes.
2. Give only config-installer a narrow read-write Stewardship-authority mount;
   give each credential installer one target subdirectory/handoff key and each
   consumer only its individual read-only credential.
3. Prohibit whole-credentials and Docker-socket mounts and enforce token-private/
   mail credentials only in mail-dispatch/rotation.
4. Configure temporary files/directories, restrictive modes, no unsafe symlink
   traversal, and container replacement persistence.
5. Add automated mount/identity/topology inspection, cross-target denial, atomic
   replacement, and restart/upgrade persistence tests.

### OPS-03: Production ingress, TLS, and network security

1. Configure stock Caddy HTTPS, HTTP redirect, Let's Encrypt issuance/renewal,
   static/media policy, request/body/time limits, and one trusted proxy hop.
2. Redact access-token path segments and cookie/query secrets in access logs.
3. Add highest-priority Caddy matchers that explicitly return not-found for
   `/health/live` and `/health/ready` before the catch-all proxy while keeping
   internal service health usable.
4. Document DNS/firewall/origin/OAuth redirect prerequisites and certificate
   recovery.
5. Add Caddy config validation and container tests for external/internal route
   behavior.

### OPS-04: Bootstrap, migrations, startup, and upgrades

1. Integrate bootstrap/config validation/migration/health entry commands with
   Compose and deployment documentation.
2. Ensure migrations run once through an explicit job before service rollout;
   application containers do not race.
3. Require a recent successful backup, migration checks, pinned image pull, and
   health verification for production upgrade.
4. Establish expand/migrate/contract rules and rollback/recovery guidance for
   incompatible schema releases.
5. Test empty startup, bootstrap YAML import, active-manifest/database mismatch
   recovery/denial, configured restart, migration drift/failure, partial
   rollout, and signal-driven worker recovery.

### OPS-05: Backup service and purge-triggered backup

1. Implement consistent PostgreSQL custom/logical dump plus media, deployment
   YAML, retained Stewardship YAML versions/active manifest, schema/application
   versions, credential manifest, file list, and cryptographic digests.
2. Encrypt before off-host transfer, publish only complete verified manifests,
   enforce target configuration, and apply the specified retention defaults.
3. Expose scheduled/operator invocation and the guarded Admin purge task through
   the same service without giving the web process backup credentials.
4. Build the separate operator-only secret-escrow profile for all credential
   files and versioned data-backup keys, encrypted to recovery material absent
   from the VM; verify matching fingerprints and restore usability.
5. Emit CRITICAL after the specified backup RPO and test partial upload,
   corruption, retry, retention, key rotation/retirement, missing escrow, and
   purge-evidence expiration behavior.

### OPS-06: Restore and state-aware release

1. Implement empty-target default and explicit in-place disaster-recovery modes
   with manifest/digest/version/tenant/credential validation and destructive
   confirmation.
2. Restore database/media/config, run permitted forward migrations, start in
   Testing, and atomically set `restore_review_required` before web readiness.
3. Configure a restricted maintenance queue/type allowlist and block Family,
   production dispatch, publication, export, and ordinary work fail closed.
4. Build uncertainty-window inventory and durable holds for potentially missing
   deliveries and newly discovered Families.
5. Support Admin state-aware release through ADM-06 with atomic hold
   materialization, deterministic mode/state selection, Production for a sole
   current scheduled/active/closed campaign, and no partial release.
6. Add isolated restore smoke tests for every campaign/purge state, including
   closed-Production post-campaign behavior, ambiguous delivery choice,
   interruption, wrong tenant, missing key, and release race.

### OPS-07: Housekeeping and retention jobs

1. Implement temporary export, upload staging, failed wizard staging, old static
   bundle, expired session, worker result, log rotation, and cache cleanup with
   explicit retention periods.
2. Implement the dedicated source-compaction task using the data specification's
   protected-reference rules and all/daily/monthly retention tiers.
3. Make generic cleanup idempotent, record-scoped, path-safe, and unable to
   delete snapshots, submissions, audit, backups, or broad/unresolved
   directories.
4. Integrate test-data cleanup and exceptional campaign purge only through their
   dedicated gated workflows.
5. Add age/anchor-boundary, protected-reference, promotion race, restart,
   symlink/path, concurrent-download, and retained-data regression tests.

### OPS-08: Observability, health, and operational runbooks

1. Emit structured correlated logs to stdout and durable audit/log tables with
   DEBUG through CRITICAL and privacy-safe context.
2. Add metrics for HTTP, sessions, queue/task/scheduler/outbox, snapshots,
   database/Valkey, disk, backups, and TLS.
3. Implement minimal liveness/readiness endpoints plus detailed protected CLI
   diagnostics.
4. Add deduplicated Admin/Slack alert routing and recovery indicators.
5. Write runbooks for deployment, backup/restore, queue outage, database/Valkey,
   failed source refresh, mail ambiguity, stuck transitions, purge recovery,
   TLS, and key rotation.
6. Exercise runbooks with failure injection before final release.

### OPS-09: CI, coverage, browser, acceptance, and release pipeline

1. Add dependency installation, Ruff, formatting, PyMarkdown, pytest, migration
   drift, frontend static/build, and the normative scoped coverage gates.
2. Add and validate `coverage-stewardship.toml`; measure the stewardship package
   and listed shared modules with separate 80% line and branch floors.
3. Add PostgreSQL/Valkey integration jobs using production major/minor lines,
   browser/accessibility jobs, Compose smoke, image build, SBOM, provenance,
   vulnerability scan, and multi-architecture build.
4. Keep all normal CI fake-backed and credential-free; add documented human-run
   redacted smoke tools for real integrations.
5. Implement every required suite and numbered acceptance scenario, linked to
   DOM-05 traceability.
6. Publish only after Review Gate 5 and explicit human release authorization;
   never push a release tag automatically.

## Review handoffs

- Review Gate 1 requires OPS-01 through OPS-04 and baseline OPS-08/OPS-09.
- Review Gate 3 rechecks queue/service topology and mail/private-key mounts.
- Review Gate 4 requires an independent destructive-workflow and restore review
  of OPS-05 through OPS-07.
- Review Gate 5 exercises OPS-08/OPS-09, every runbook, and the release artifact.

## Completion criteria

- A new developer can start the complete environment on Linux, macOS, or
  Windows; production can start only with validated secure configuration.
- Container replacement, upgrade, backup, and restore preserve required durable
  state and enforce gates.
- CI and documented pre-release commands reproduce every mandatory quality,
  security, browser, Compose, and acceptance check without real credentials.
