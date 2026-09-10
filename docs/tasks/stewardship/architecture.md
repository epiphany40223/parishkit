# Architecture tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/architecture.md) ·
[Normative specification](../../specs/stewardship/architecture/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## ARC-01: Dependency decisions and package skeleton

Scope and dependencies: [ARC-01 work package](../../plans/stewardship/architecture.md#arc-01-dependency-decisions-and-package-skeleton).

- [x] ARC-01.01 — Record and pin supported dependency lines.
- [x] ARC-01.02 — Create the Django project and app boundaries.
- [x] ARC-01.03 — Configure validated development, test, and production settings.
- [x] ARC-01.04 — Add the console entry point and thin wrapper.
- [x] ARC-01.05 — Create URL namespaces and intentional placeholders.
- [x] ARC-01.06 — Test imports, settings, entry points, and URL resolution.

Evidence: Implementation commit `fb3b25c` on `pr/stewardship-implementation`
(`feat: scaffold the stewardship application`). See
[development notes](../../development/stewardship.md) and
[`test_scaffold.py`](../../../tests/stewardship/test_scaffold.py).
Host validation on September 7, 2026: `python -m pytest` passed 356 tests;
the 30 Stewardship smoke tests passed with 92.24% scoped statement coverage
(`python -m pytest tests/stewardship --cov=parishkit.stewardship --cov-fail-under=80`).
Ruff check/format and Markdown checks passed. The production profile deliberately
rejects startup while ARC-02 is unimplemented; its complete deployment validation
is not claimed by this scaffold. Internal readiness and metrics remain closed;
ingress isolation and container verification belong to ARC-03/OPS-01 and M0.

## ARC-02: Shared CLI, configuration, paths, and app startup

Scope and dependencies: [ARC-02 work package](../../plans/stewardship/architecture.md#arc-02-shared-cli-configuration-paths-and-app-startup).

- [ ] ARC-02.01 — Integrate shared ParishKit helpers.
- [x] ARC-02.02 — Define deployment configuration and precedence.
- [ ] ARC-02.03 — Define versioned YAML authority and materialization interfaces.
- [x] ARC-02.04 — Apply common runtime roots and path overrides.
- [ ] ARC-02.05 — Validate production configuration and service prerequisites.
- [x] ARC-02.06 — Configure redacted correlated logging.
- [ ] ARC-02.07 — Test YAML recovery, precedence, startup, and redaction.

Evidence: Shared-helper commit `cbdc391` and deployment/authority-contract commit
`b5851a3`, followed by logging/evidence commit `20330dd`, all on
`pr/stewardship-implementation`. See [deployment metadata](../../development/stewardship-deployment.md),
[authority contracts and remaining integration](../../development/stewardship-authority.md),
and the deployment/authority/observability tests under
[`tests/stewardship`](../../../tests/stewardship/).
Host validation on September 7, 2026: `python -m pytest` passed 515 tests;
`python -m pytest tests/stewardship --cov=parishkit.stewardship --cov-fail-under=80`
passed 187 tests with 98.66% scoped statement coverage. Ruff check/format and
Markdown checks passed.

ARC-02.01 is partial: shared CLI, configuration, runtime roots, strict YAML, and
logging are integrated; provider/retry integration belongs with the actual
provider-using services. ARC-02.03 has canonical envelopes, stable IDs, immutable
files, atomic manifests, and a tested materializer protocol, but only synthetic
product validation/materialization in tests. ARC-02.05 remains unimplemented;
production settings deliberately reject all startup. ARC-02.07 covers pure
precedence, invalid input, strict parsing, redaction, and fake-backed activation
failure/recovery, not actual PostgreSQL durability or runtime prerequisite checks.

Phase split approved by the human on September 7, 2026 and recorded in the
controlling plan: Phase 0 supplies configuration contracts and safe scaffold
rejection. Concrete database materialization/digest/mode checks follow DAT-01 in
Phase 1; credential/mount/service checks and PostgreSQL-backed recovery complete
with ARC-06 and OPS-02/OPS-04 before Gate 1. No durable integration, Compose
milestone, or review gate is claimed complete. Tasks with remaining scope stay
unchecked; do not introduce shadow tables to bypass the dependency.

## ARC-03: Django web foundation and security middleware

Scope and dependencies: [ARC-03 work package](../../plans/stewardship/architecture.md#arc-03-django-web-foundation-and-security-middleware).

- [ ] ARC-03.01 — Configure Django request and browser security controls.
- [ ] ARC-03.02 — Separate public and internal routes.
- [x] ARC-03.03 — Implement safe graphic upload and variant processing.
- [x] ARC-03.04 — Implement rich-text sanitization and template validation.
- [ ] ARC-03.05 — Implement safe CSV cells, download headers, and response-lifetime read guards.
- [x] ARC-03.06 — Test malicious requests, content, files, and error paths.

Evidence: Phase 1B implementation on `pr/stewardship-phase-1b`, including backup
commit `dfb90d2`; see [implementation evidence](../../guides/stewardship-phase-1b.md).
Graphic, rich-text, template and hostile-input tests pass. ARC-03.01/.02/.05
have tested middleware, production settings policy and WSGI response-lifetime
adapters; production startup, actual proxy isolation and deployment headroom
verification remain with Phase 1C. Package completion and reviews are pending.

## ARC-04: Google identity, authorization sessions, and denial paths

Scope and dependencies: [ARC-04 work package](../../plans/stewardship/architecture.md#arc-04-google-identity-authorization-sessions-and-denial-paths).

- [x] ARC-04.01 — Implement Google authorization-code authentication.
- [x] ARC-04.02 — Integrate exact-address precedence and domain policies.
- [x] ARC-04.03 — Implement durable staff sessions and revocation.
- [x] ARC-04.04 — Build retryable authentication denial paths.
- [x] ARC-04.05 — Implement OAuth limiting and distributed-abuse telemetry.
- [x] ARC-04.06 — Test authentication, session security, and denial boundaries.

Foundation handoff: PortalSession protects its Django Session parent. Plain
`clearsessions` aborts the whole sweep when an expired session has protected
metadata. ARC-04.03 must provide ordered revoke/metadata/session cleanup before
login is enabled; see the executable PostgreSQL regression and
[database guide](../../guides/stewardship-database-tests.md#foundation-boundaries).

Evidence: Current-policy authorization and PostgreSQL sessions are integrated
with Google-only HTTP login. Idle/absolute expiry, revocation, separate cookies,
logout, ordered cleanup and privilege-transition rotation pass integration tests;
rotation preserves the original Google authentication time and absolute expiry.
Setup/maintenance admission, recovery/identity-change HTTP cases and configurable
limiter thresholds now pass in the 762-test PostgreSQL checkpoint. Production
runtime assembly stays with Phase 1C and the durable wizard marker with ADM-02;
[Phase 1B evidence](../../guides/stewardship-phase-1b.md) records those boundaries
and the incomplete review status.

## ARC-05: Family code, token, and Family-session security

Scope and dependencies: [ARC-05 work package](../../plans/stewardship/architecture.md#arc-05-family-code-token-and-family-session-security).

- [x] ARC-05.01 — Implement Family codes, MAC lookup, and collision-safe migration.
- [x] ARC-05.02 — Implement credential lifecycles and collision-only reservation keys.
- [x] ARC-05.03 — Implement mode/epoch-scoped Family sessions and activity handling.
- [x] ARC-05.04 — Implement and test distributed Family-code guessing controls.
- [x] ARC-05.05 — Enforce Admin/Staff code visibility and leader exclusion.
- [x] ARC-05.06 — Test credential, session, outage, and boundary behavior.

Evidence: Campaign credentials, MAC lookup/backfill, sealed link generations,
rehearsal epochs, Family sessions and guarded code reports are implemented on
`pr/stewardship-phase-1b`. Family HTTP tests and 39 browser-component checks cover
separate sessions, warnings and activity handling. The guarded code-report role
matrix permits Admin/Staff without fresh reauthentication and denies leaders.
Active-generation arrival/reactivation and restore-fence cases now pass, alongside
the full identity suite. Backup-aware inventory retirement separately requires
ARC-06 owner evidence; actual DAT-03 source promotion remains Phase 2 integration.
See [Phase 1B evidence](../../guides/stewardship-phase-1b.md).

## ARC-06: Enforceable cryptographic service boundary

Scope and dependencies: [ARC-06 work package](../../plans/stewardship/architecture.md#arc-06-enforceable-cryptographic-service-boundary).

- [x] ARC-06.01 — Define independent versioned keyrings.
- [ ] ARC-06.02 — Implement isolated configuration activation and recovery.
- [ ] ARC-06.03 — Implement target-specific sealed credential replacement.
- [ ] ARC-06.04 — Configure mail-dispatch and token-key-rotation services.
- [ ] ARC-06.05 — Restrict general services to token public keys.
- [ ] ARC-06.06 — Implement key rotation and backup compatibility workflows.
- [ ] ARC-06.07 — Test installer isolation, failures, races, and mount boundaries.

Evidence: Independent general-encryption, signing, MAC and sealed-box keyrings,
purpose-bound envelopes, safe fingerprints and owner-only key-file handling are
implemented. Target-specific handoff encryption passes 15 tests. Re-encryption
batches preserve code/token values and roll back on corruption; rotation plus
storage regressions pass 47 tests. Target credential queue/file orchestration
and actual restricted-role isolation now pass ten integration cases; see the
[installer boundary](../../guides/stewardship-credential-installers.md). Twelve
actual-container mount checks pass. Configuration service admission now passes
four real restricted-role cases; backup-aware retirement passes fourteen
cases using explicitly synthetic owner evidence. Phase 1C still supplies
production provisioning, consumer recreation and command assembly; later backup
owners supply actual catalog/escrow evidence. These mixed-phase tasks remain
open rather than claiming production readiness. See the
[remaining owner contracts](../../guides/stewardship-phase-1b.md#installer-and-retirement-handoff).

## ARC-07: Application-level privacy and audit primitives

Scope and dependencies: [ARC-07 work package](../../plans/stewardship/architecture.md#arc-07-application-level-privacy-and-audit-primitives).

- [x] ARC-07.01 — Implement shared audited service wrappers.
- [x] ARC-07.02 — Define and enforce structured redaction schemas.
- [x] ARC-07.03 — Implement concurrency and validation-error helpers.
- [x] ARC-07.04 — Implement bounded pagination and neutral denial responses.
- [ ] ARC-07.05 — Test secret exclusion across logs and support artifacts.

Evidence: Closed typed audit/operational contexts, PostgreSQL schema guards,
optimistic-version checks, progressive validation errors, bounded pagination
and neutral denial responses are implemented. Shared audit/storage checks pass
52 tests and pure security/presentation/contracts pass 75 tests. Ten additional
privileged-action cases cover locked current-role/CSRF/freshness admission into
configuration and sealed-secret intake, including seeded receipt/audit privacy.
The initial wrapper contracts are implemented; later feature forms and generated
support-artifact owners must extend the privacy scan. See
[Phase 1B evidence](../../guides/stewardship-phase-1b.md).

## ARC-08: Performance, accessibility, and compatibility baseline

Scope and dependencies: [ARC-08 work package](../../plans/stewardship/architecture.md#arc-08-performance-accessibility-and-compatibility-baseline).

- [ ] ARC-08.01 — Set and enforce interactive query and latency budgets.
- [ ] ARC-08.02 — Create representative scale fixtures.
- [ ] ARC-08.03 — Configure asset versioning and browser matrices.
- [ ] ARC-08.04 — Integrate automated accessibility and focus helpers.
- [ ] ARC-08.05 — Measure baseline page and task-status performance.

Evidence: Initial scope is in progress. Versioned self-hosted assets and an
explicit Chromium/Firefox/WebKit matrix pass 39 component checks including axe,
keyboard focus, mobile layouts, browser-local times and session behavior.
An initial 5,000-Family/100-live-session fixture verifies constant-size lookup
work and enforces query/p95 budgets for lookup, Family page and Admin shell.
This is not simultaneous-request load or the complete task/report baseline. Full
source/submission/outbox scale fixtures stay with their later phase owners;
see [Phase 1B evidence](../../guides/stewardship-phase-1b.md).
