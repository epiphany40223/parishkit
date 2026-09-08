# Operations and quality implementation plan

Task status: [Operations and quality checklist](../../tasks/stewardship/operations.md).

This plan implements the
[operations and quality specification](../../specs/stewardship/operations/spec.md).
Operational behavior is developed alongside application phases, not deferred to
the end.

## Work packages

### OPS-01: Development and production Compose topology

1. Create base, development, and production Compose definitions for web,
   config-installer, target-specific credential installers, general worker,
   backup-worker, mail-dispatch, token-key-rotation profile, scheduler,
   PostgreSQL, Valkey, and Caddy.
   Include the explicitly invoked one-shot bootstrap profile, excluded from
   ordinary service startup.
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
   rooted default bind mounts with independent overrides. Test that changing
   PARISHKIT_ROOT relocates every default and generic cleanup excludes stores.
2. Give only the online config-installer a narrow read-write Stewardship-authority mount;
   give each credential installer one target subdirectory/handoff key and each
   consumer only its individual read-only credential.
   Implement the separately enumerated operator-only provisioning exception
   from the [offline bootstrap profile](../../specs/stewardship/operations/spec.md#offline-bootstrap-profile).
3. Prohibit whole-credentials and Docker-socket mounts and enforce token-private/
   mail credentials only in mail-dispatch/token-key-rotation and backup
   credentials only in backup-worker.
   Keep bootstrap's initial token-key provisioning exception limited to that
   profile; do not grant it ordinary mail-provider or backup authority.
4. Configure temporary files/directories, restrictive modes, no unsafe symlink
   traversal, and container replacement persistence.
5. Add automated mount/identity/topology inspection, cross-target denial, atomic
   replacement, and restart/upgrade persistence tests.

### OPS-03: Production ingress, TLS, and network security

1. Configure stock Caddy HTTPS, HTTP redirect, Let's Encrypt issuance/renewal,
   static/media policy, request/body/time limits, and one trusted proxy hop.
   Before enabling the `pending-ingress` profile, implement explicit non-root
   execution, a read-only root filesystem, dropped capabilities,
   `no-new-privileges`, and init/signal handling. Select and document the minimum
   privilege needed for port binding rather than retaining default capabilities.
   Coordinate with OPS-02 on ownership and narrowly writable persistent
   `/data`/`/config` mounts and any required temporary storage; certificate
   state must remain writable and survive replacement.
   Give Caddy a network path to the web service without direct connectivity to
   PostgreSQL or Valkey, while retaining the egress required for certificate
   operations. The current root/default-capability, writable-root, shared-backend
   scaffold is not production-ready. Keep ingress disabled until these controls
   and item 5's validation are complete, along with OPS-04 startup prerequisites.
2. Redact access-token path segments and cookie/query secrets in access logs.
3. Add highest-priority Caddy matchers that explicitly return not-found for
   `/health/live` and `/health/ready` before the catch-all proxy while keeping
   internal service health usable.
4. Document DNS/firewall/origin/OAuth redirect prerequisites and certificate
   recovery.
5. Add Caddy config validation and container tests for external/internal route
   behavior.
   Inspect effective runtime UID, capabilities, root-filesystem restrictions,
   no-new-privileges, and init behavior. Test permitted state writes, prohibited
   writes outside those mounts, certificate-state persistence, web reachability,
   and denial of direct database/broker connectivity. Include Linux-host
   ownership/port-binding evidence. Passing template adaptation alone does not
   satisfy these hardening or ingress-enable prerequisites.

### OPS-04: Bootstrap, migrations, startup, and upgrades

1. Integrate bootstrap/config validation/migration/health entry commands with
   Compose and deployment documentation. Provision the metrics bearer credential
   alongside required keyrings before web startup; OPS-08 consumes and rotates
   this file rather than owning its initial creation.
   Implement the offline bootstrap service identity, narrow mounts, and
   bootstrap/online-start mutual exclusion from the operations specification.
   Validate the [download capacity and timeout contract](../../specs/stewardship/operations/spec.md#download-capacity-and-timeouts),
   including deployment-wide pool/process budgets, database and web headroom,
   rollout overlap, and isolated download-role timeout settings.
   Implement the separately invoked `recover-admin` profile through the existing
   config-installer identity and canonical
   [offline recovery protocol](../../specs/stewardship/operations/spec.md#offline-admin-access-recovery):
   limited additive grant, explicit operator authority/confirmation, durable
   versioned activation, session revocation, and security evidence. Do not reuse
   bootstrap or expose an online recovery API.
2. Ensure migrations run once through an explicit job before service rollout;
   application containers do not race.
3. Require a recent successful backup, migration checks, pinned image pull, and
   health verification for production upgrade.
4. Establish expand/migrate/contract rules and rollback/recovery guidance for
   incompatible schema releases.
5. Test empty startup, bootstrap YAML import, active-manifest/database mismatch
   recovery/denial, configured restart, migration drift/failure, partial
   rollout, and signal-driven worker recovery.
   Test bootstrap mount/identity isolation, concurrent online-start refusal,
   idempotent partial provisioning, and nonmatching configuration/key refusal
   in development and production Compose.

### OPS-05: Backup service and purge-triggered backup

1. Implement consistent PostgreSQL custom/logical dump plus media, deployment
   YAML, retained Stewardship YAML versions/active manifest, schema/application
   versions, credential manifest, file list, and cryptographic digests.
2. Encrypt before off-host transfer, publish only complete verified manifests,
   enforce target configuration, and apply the specified retention defaults.
3. Expose scheduled/operator invocation and the guarded Admin purge task through
   the dedicated backup queue/service without giving web/general workers backup
   credentials.
   Implement same-backup revalidation and append-only verification evidence
   under the [purge workflow](../../specs/stewardship/admin-portal/spec.md#campaign-purge),
   without replacing the backup or resetting retention/recovery expiry.
4. Build the separate operator-only secret-escrow profile for all credential
   files and versioned data-backup keys, encrypted to recovery material absent
   from the VM; verify matching fingerprints and restore usability.
   Add isolated off-host verification tooling and runbook for the exact purge
   backup/escrow pair, recovery-private-key usability, complete credential/key
   coverage, and data-backup decryption. Emit only the strict non-secret
   summary consumed by ADM-10's operator-attestation workflow.
5. Emit CRITICAL after the specified backup RPO and test partial upload,
   corruption, retry, retention, key rotation/retirement, missing escrow, and
   purge-evidence expiration behavior.
   Test revalidation against missing/partial/corrupt objects, unavailable keys,
   stale/retried task completions, concurrent backup or manifest replacement,
   mutation/quiescence changes, and preservation of prior evidence timestamps.

### OPS-06: Restore and state-aware release

1. Implement empty-target default and explicit in-place disaster-recovery modes
   with manifest/digest/version/tenant/credential validation and destructive
   confirmation.
2. Restore database/media/config, run permitted forward migrations, start in
   Testing, invalidate all restored sessions/OAuth/reauthentication state, and
   atomically set `restore_review_required` before exposing web routes.
   Initialize a fresh Family-link credential epoch, clear restored generation/
   rehearsal pointers, and fence old preparation work including inactive
   Families' tokens; resume the same epoch only on recovery of this instance.
3. Configure a restricted maintenance queue/type allowlist and block Family,
   production dispatch, publication, export, and ordinary work fail closed.
   Route maintenance work to the existing general/mail-dispatch/backup services
   and their specified restore queues without broadening secret mounts.
4. Build uncertainty-window inventory and durable holds for potentially missing
   deliveries and newly discovered Families.
5. Support Admin state-aware release through ADM-06 with atomic hold
   materialization, the Admin spec's deterministic mode/state selection,
   preserved Production/pointer for archived-current, and no partial release.
   Integrate BG-02 public-key preparation and ADM-06 atomic fresh-generation
   activation for scheduled/active release without changing manual codes or
   releasing delivery holds. Reject stale sealed outbox credentials.
6. Add isolated restore smoke tests for every campaign/purge/pointer state,
   including closed-Production, archived-current versus historical archived,
   ambiguous delivery choice, interruption, wrong tenant, missing key, and
   release race; reject restored Admin/Staff/leader/Family cookies and verify
   fresh Google login plus maintenance queue isolation.

### OPS-07: Housekeeping and retention jobs

1. Implement temporary export, upload staging, failed wizard staging, old static
   bundle, expired session, worker result, log rotation, and cache cleanup with
   explicit retention periods. Retain plaintext exports for seven days by
   default, require owner-only export directories/files/temporary files from
   creation, and validate configured path ownership and permissions.
   Expire Family form-baseline metadata/pins under DAT-06's session bounds,
   preserving any transferred submission pins and never persisting draft answers.
2. Implement the dedicated source-compaction task using the data specification's
   protected-reference rules and all/daily/monthly retention tiers.
   Implement the separate hourly derived-fact compactor under
   [derived fact retention](../../specs/stewardship/data/spec.md#derived-fact-retention),
   with bounded batches, gate checks, protected-reference rechecks, and
   task/backlog observability. Integrate DAT-03/RPT-03 guards without expanding
   the source-compaction service's authority.
3. Make generic cleanup idempotent, record-scoped, path-safe, and unable to
   delete snapshots, submissions, audit, backups, or broad/unresolved
   directories.
4. Integrate test-data cleanup and exceptional campaign purge only through their
   dedicated gated workflows.
5. Add age/anchor-boundary, protected-reference, promotion race, restart,
   symlink/path, concurrent-download, and retained-data regression tests;
   verify export permissions before and after atomic rename, expiry, and
   overridden storage paths.
   Add fact-generation accumulation, current/pinned/building/reader protection,
   pin/publication/claim races, parent-versus-file expiry, and compactor crash/
   retry tests; prove original campaign data and operational history survive.

### OPS-08: Observability, health, and operational runbooks

1. Emit structured correlated logs to stdout and durable audit/log tables with
   DEBUG through CRITICAL and privacy-safe context.
2. Add Prometheus-compatible metrics for HTTP, sessions, queue/task/scheduler/
   outbox, snapshots, database/Valkey, disk, backups, and TLS at the internal
   bearer-authenticated `/metrics` route; consume the OPS-04 bootstrap credential,
   implement its isolated rotation, deny the route at Caddy, and test both
   boundaries.
3. Implement minimal liveness/readiness endpoints plus detailed protected CLI
   diagnostics.
4. Add deduplicated Admin/Slack alert routing and recovery indicators.
5. Write runbooks for deployment, backup/restore, queue outage, database/Valkey,
   failed source refresh, mail ambiguity, stuck transitions, purge recovery,
   TLS, and key rotation.
   Include the offline Admin-access recovery runbook, verification of ordinary
   Google login, and subsequent authenticated review of obsolete grants.
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
   Include saturated slow-download load tests alongside Family submissions,
   background work, purge drainage, and multi-process restart/rollout races;
   verify bounded admission, prompt busy responses, safe slot release, and
   reference-load responsiveness.
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
