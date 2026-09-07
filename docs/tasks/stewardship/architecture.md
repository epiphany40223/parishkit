# Architecture tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/architecture.md) ·
[Normative specification](../../specs/stewardship/architecture/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## ARC-01: Dependency decisions and package skeleton

Scope and dependencies: [ARC-01 work package](../../plans/stewardship/architecture.md#arc-01-dependency-decisions-and-package-skeleton).

- [ ] ARC-01.01 — Record and pin supported dependency lines.
- [ ] ARC-01.02 — Create the Django project and app boundaries.
- [ ] ARC-01.03 — Configure validated development, test, and production settings.
- [ ] ARC-01.04 — Add the console entry point and thin wrapper.
- [ ] ARC-01.05 — Create URL namespaces and intentional placeholders.
- [ ] ARC-01.06 — Test imports, settings, entry points, and URL resolution.

Evidence: Not started.

## ARC-02: Shared CLI, configuration, paths, and app startup

Scope and dependencies: [ARC-02 work package](../../plans/stewardship/architecture.md#arc-02-shared-cli-configuration-paths-and-app-startup).

- [ ] ARC-02.01 — Integrate shared ParishKit helpers.
- [ ] ARC-02.02 — Define deployment configuration and precedence.
- [ ] ARC-02.03 — Define versioned YAML authority and materialization interfaces.
- [ ] ARC-02.04 — Apply common runtime roots and path overrides.
- [ ] ARC-02.05 — Validate production configuration and service prerequisites.
- [ ] ARC-02.06 — Configure redacted correlated logging.
- [ ] ARC-02.07 — Test YAML recovery, precedence, startup, and redaction.

Evidence: Not started.

## ARC-03: Django web foundation and security middleware

Scope and dependencies: [ARC-03 work package](../../plans/stewardship/architecture.md#arc-03-django-web-foundation-and-security-middleware).

- [ ] ARC-03.01 — Configure Django request and browser security controls.
- [ ] ARC-03.02 — Separate public and internal routes.
- [ ] ARC-03.03 — Implement safe graphic upload and variant processing.
- [ ] ARC-03.04 — Implement rich-text sanitization and template validation.
- [ ] ARC-03.05 — Implement safe CSV cells and download headers.
- [ ] ARC-03.06 — Test malicious requests, content, files, and error paths.

Evidence: Not started.

## ARC-04: Google identity, authorization sessions, and denial paths

Scope and dependencies: [ARC-04 work package](../../plans/stewardship/architecture.md#arc-04-google-identity-authorization-sessions-and-denial-paths).

- [ ] ARC-04.01 — Implement Google authorization-code authentication.
- [ ] ARC-04.02 — Integrate exact-address precedence and domain policies.
- [ ] ARC-04.03 — Implement durable staff sessions and revocation.
- [ ] ARC-04.04 — Build retryable authentication denial paths.
- [ ] ARC-04.05 — Implement OAuth limiting and distributed-abuse telemetry.
- [ ] ARC-04.06 — Test authentication, session security, and denial boundaries.

Evidence: Not started.

## ARC-05: Family code, token, and Family-session security

Scope and dependencies: [ARC-05 work package](../../plans/stewardship/architecture.md#arc-05-family-code-token-and-family-session-security).

- [ ] ARC-05.01 — Implement Family codes, MAC lookup, and collision-safe migration.
- [ ] ARC-05.02 — Implement sealed email tokens and exchange lifecycle.
- [ ] ARC-05.03 — Implement separate Family sessions and activity handling.
- [ ] ARC-05.04 — Implement and test distributed Family-code guessing controls.
- [ ] ARC-05.05 — Enforce Admin/Staff code visibility and leader exclusion.
- [ ] ARC-05.06 — Test credential, session, outage, and boundary behavior.

Evidence: Not started.

## ARC-06: Enforceable cryptographic service boundary

Scope and dependencies: [ARC-06 work package](../../plans/stewardship/architecture.md#arc-06-enforceable-cryptographic-service-boundary).

- [ ] ARC-06.01 — Define independent versioned keyrings.
- [ ] ARC-06.02 — Implement isolated configuration activation and recovery.
- [ ] ARC-06.03 — Implement target-specific sealed credential replacement.
- [ ] ARC-06.04 — Configure mail-dispatch and token-key-rotation services.
- [ ] ARC-06.05 — Restrict general services to token public keys.
- [ ] ARC-06.06 — Implement key rotation and backup compatibility workflows.
- [ ] ARC-06.07 — Test installer isolation, failures, races, and mount boundaries.

Evidence: Not started.

## ARC-07: Application-level privacy and audit primitives

Scope and dependencies: [ARC-07 work package](../../plans/stewardship/architecture.md#arc-07-application-level-privacy-and-audit-primitives).

- [ ] ARC-07.01 — Implement shared audited service wrappers.
- [ ] ARC-07.02 — Define and enforce structured redaction schemas.
- [ ] ARC-07.03 — Implement concurrency and validation-error helpers.
- [ ] ARC-07.04 — Implement bounded pagination and neutral denial responses.
- [ ] ARC-07.05 — Test secret exclusion across logs and support artifacts.

Evidence: Not started.

## ARC-08: Performance, accessibility, and compatibility baseline

Scope and dependencies: [ARC-08 work package](../../plans/stewardship/architecture.md#arc-08-performance-accessibility-and-compatibility-baseline).

- [ ] ARC-08.01 — Set and enforce interactive query and latency budgets.
- [ ] ARC-08.02 — Create representative scale fixtures.
- [ ] ARC-08.03 — Configure asset versioning and browser matrices.
- [ ] ARC-08.04 — Integrate automated accessibility and focus helpers.
- [ ] ARC-08.05 — Measure baseline page and task-status performance.

Evidence: Not started.
