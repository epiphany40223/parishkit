# Architecture implementation plan

Task status: [Architecture checklist](../../tasks/stewardship/architecture.md).

This plan implements the
[Stewardship architecture specification](../../specs/stewardship/architecture/spec.md).
It establishes the executable skeleton and security boundaries on which every
other subsystem depends.

## Work packages

### ARC-01: Dependency decisions and package skeleton

1. Record supported Python, Django, PostgreSQL, Celery, Valkey, Caddy, browser,
   and cryptography dependency lines with an upgrade policy; pin reproducible
   development/test inputs.
2. Create `src/parishkit/stewardship/` as one Django project with cohesive apps
   for accounts/configuration, source, campaigns, responses, workflows,
   reports, jobs, and audit.
3. Add settings modules for shared, development, test, and production behavior,
   with startup validation and no credential requirement in normal tests.
4. Add the `pk-stewardship` console entry point and thin executable wrapper;
   initially expose safe version/configuration diagnostics.
5. Create URL namespaces for public, Family, Admin, and internal health routes;
   return intentional placeholders until their owning packages land.
6. Add import, settings, entry-point, and URL-resolution smoke tests.

### ARC-02: Shared CLI, configuration, paths, and app startup

1. Reuse `parishkit.cli`, `parishkit.config`, logging, retry, email, Google,
   ParishSoft, and runtime-path helpers; add shared functionality only when it
   is campaign-neutral.
2. Define typed deployment configuration and YAML/environment precedence for
   database, Valkey, public origin, proxy trust, secret references, and service
   role.
3. Define the schema-versioned Stewardship YAML authority, stable-ID/canonical
   serialization, immutable version files, atomic active manifest, and typed
   importer/materializer interfaces.
4. Route every default runtime path through `PARISHKIT_ROOT` or
   `/opt/parishkit`; preserve CLI/YAML overrides.
5. Implement fail-fast production startup validation for active-YAML/database
   digest agreement, mode, origin, proxy, secret presence/mount separation,
   database migration state, and Valkey requirements.
6. Configure structured redacted logging and request/task correlation before
   feature code emits logs.
7. Test canonical round trips, schema upgrades, atomic manifest recovery,
   configuration precedence, invalid startup, path overrides, and secret
   redaction.

### ARC-03: Django web foundation and security middleware

1. Configure secure production cookies, CSRF, host/origin checks, clickjacking,
   CSP, HSTS, Referrer-Policy, no-store utilities, safe proxy resolution, and
   sanitized error handlers.
2. Implement public/internal route separation so Caddy cannot proxy health
   routes and unknown internal-looking paths reveal nothing.
3. Add upload validation primitives: signature/type validation, size and
   decompression limits, randomized names, decode/re-encode, and safe graphic
   variants.
4. Add rich-text sanitization and template-placeholder allowlisting with
   stored sanitized HTML and generated/edited plain text.
5. Add CSV formula neutralization and safe download headers for later export
   code.
   Integrate DAT-02's response-lifetime read guards for campaign detail and
   streaming downloads, with explicit connection ownership and hard deadlines
   rather than relying on view-scoped transactions alone.
   Use its bounded download admission/pool and retryable busy response; retain
   interactive web execution headroom under OPS-04 deployment validation.
6. Test malicious headers, hosts, HTML, files, filenames, CSV cells, and error
   paths.

### ARC-04: Google identity, authorization sessions, and denial paths

1. Integrate Google authorization-code flow with state, nonce, PKCE, verified
   email, stable `sub`, and signed hosted-domain claim capture; disable all
   password/signup/recovery routes.
   Integrate OPS-04's offline Admin-access recovery with normal Google login;
   no web recovery route, session minting, or Google account rebinding is added.
2. Implement exact-address-over-domain rule evaluation and role loading through
   the canonical DOM-03 policies.
3. Store every Admin/Staff/leader session in PostgreSQL with the idle/absolute
   expiry defined by the architecture specification, revocation checks on every
   privileged request, and standards-compliant logout.
4. Implement uniform re-login-capable denial/error pages for provider failure,
   allowlist denial, no role, unconfigured deployment, and maintenance gates.
5. Implement early and specific Valkey rate limiters, trusted-client address
   handling, progressive retry, distributed-abuse telemetry including rejected
   callbacks, notification thresholds, and fail-closed outage behavior.
6. Add complete authentication, session-fixation, timeout, revocation, hosted-
   domain, limiter, and denial-response tests.
   Test sole-account rename/deactivation, post-recovery mandatory Google login,
   rejected old sessions/OAuth state, and unchanged maintenance admission gates.

### ARC-05: Family code, token, and Family-session security

1. Implement reduced-alphabet eight-letter code generation, canonicalization,
   collision-safe HMAC lookup, general-key encryption, and versioned MAC-key
   migration services, including a stable accepted-key set, campaign generation
   lock, set-based collision filtering, and bounded batch-level retry during
   bulk promotion.
2. Implement independent 256-bit link-token generation, campaign-scoped digest
   lookup, sealed-box ciphertext, exchange to a token-free Family session, and
   close/rotation/reopen lifecycle. Implement separate mode-disjoint rehearsal
   codes/tokens, epoch-scoped lookup/issuance, and no cross-mode fallback using
   the canonical credential policy and DAT-04 records.
   Separate active/lookup-only authentication keys from collision-only keys;
   keep reservation-required keys through campaign purge, enforce cross-key
   non-reuse, and block retirement or issuance when required material is absent.
   Enforce the restore credential-epoch fence for lookup, issuance, generation
   activation, and dispatch without changing stable manual Family codes.
3. Configure PostgreSQL Family sessions with the specified idle/absolute
   expiry, separate cookie namespace, warnings, passive presence, and the
   explicitly untrusted CSRF-protected activity keepalive. Check mode/epoch on
   every Family request so invalidated Testing sessions cannot cross go-live.
4. Add public code-guessing limiters, distributed detection, uniform timing/
   errors, invalid-token audit fingerprints, and Valkey fail-closed behavior.
   Base distributed detection on total invalid attempts and source-IP count;
   candidate diversity is diagnostic only. Test repeated-dictionary attacks,
   threshold boundaries, and exactly-once accounting for rejected attempts.
   Apply the shared-network IP allowance and independent code-pair limit;
   test different Families sharing an IP, threshold enforcement, and expiry.
5. Preserve the low-sensitivity Admin/Staff manual-code access policy while
   excluding codes/tokens from logs and Ministry-leader scope.
6. Test entropy/collisions, normalization, key rotation, token replay/rotation,
   session isolation, throttling, outage, and campaign boundary denial.

### ARC-06: Enforceable cryptographic service boundary

1. Define separate general symmetric, Family-code MAC, signing, and token
   sealed-box keyrings with versioned envelope formats and fingerprints.
2. Implement ConfigurationChangeRequest orchestration and the sole
   `config-installer` service with read-write access only to the Stewardship
   authority directory, prepared database snapshots, exact-digest activation,
   crash reconciliation, and fail-closed mismatch handling.
3. Implement expiring target-key-sealed SecretReplacementRequest handoff and
   isolated `credential-installer-*` queues/services; each instance can decrypt
   and write only one credential target and waits for consumer fingerprint
   acknowledgement.
4. Add dedicated `mail-dispatch` and canonical `token-key-rotation` profiles;
   only mail dispatch loads provider credentials, while both may load token
   private keys.
5. Give web/general workers only token public keys; verify at startup that
   private-key paths are absent from their configuration and mounts.
6. Implement rotation/backfill/verification/retirement workflows and backup-key
   compatibility checks for every keyring.
7. Add crash/race/privacy and Compose inspection tests proving no partial config
   activation, no plaintext staging, no cross-target installer claim, and that
   web, scheduler, reports, and general workers cannot write configuration/
   credentials or decrypt token ciphertext while dispatch can.

### ARC-07: Application-level privacy and audit primitives

1. Provide audited service wrappers for configuration-change requests, applied
   role changes, sealed secret replacement, code-bearing reports, privileged
   reauthentication, and destructive confirmation.
2. Define approved redaction schemas for request, task, email, source, provider,
   and exception context; reject secret-bearing structured payload fields.
3. Add optimistic-concurrency helpers and stable machine-readable validation
   errors for progressive-enhancement endpoints.
4. Implement common pagination/filter bounds and anti-enumeration response
   behavior.
5. Add privacy regression tests that scan logs, audit payloads, responses, and
   generated support artifacts for seeded secrets.

### ARC-08: Performance, accessibility, and compatibility baseline

1. Establish query-count and latency budgets for interactive pages; add indexes
   and pagination contracts before loading production-scale fixtures.
2. Create representative scale fixtures for Families, Members, Ministries,
   submissions, jobs, and logs.
3. Configure static asset versioning, progressive enhancement, and supported-
   browser test matrices.
4. Integrate automated WCAG 2.2 AA checks plus keyboard/focus/error-summary
   helpers used by DOM-04.
5. Add baseline load/query tests for login, dashboard shell, Family lookup, and
   task status so later packages detect regressions.

## Review handoffs

- Review Gate 1 requires ARC-01 through ARC-07, including a focused identity,
  rate-limit, session, and key-mount threat-model review.
- Review Gate 2 rechecks ARC-05 against the completed Family vertical slice.
- Review Gate 3 rechecks ARC-06 against real mail dispatch and export workers.
- Review Gate 5 completes ARC-08 at production-scale fixtures.

See the [master review protocol](overall.md#review-gate-protocol).

## Completion criteria

- Development and production services start from the same application image
  with environment-appropriate settings and enforceable secret mounts.
- Every public/internal route and authentication/session boundary has positive
  and negative tests.
- No feature package must invent its own configuration, authorization,
  cryptography, redaction, validation-error, or audit mechanism.
