# Stewardship Phase 1B: web and identity security

PR #19 merged as `6fd21eb586a635333be9f55fc7db9caa39284e60`.
Branch `pr/stewardship-phase-1b` starts at that updated `origin/main`.
This batch implements the complete Phase 1B assignment in the
[controlling plan](../plans/stewardship/overall.md#1b-web-and-identity-security),
not a sequence of individual foundation PRs.

## Internal execution checkpoints

1. ARC-03: request security, ingress separation, content/image/export safety
   and response-lifetime guard integration, with negative tests.
2. ARC-04 and ADM-01: Google-only authentication, current policy enforcement,
   durable sessions, denial/setup routing and Valkey abuse controls.
3. DAT-04 and ARC-05: Family identity/credential storage, code and token
   exchange, isolated sessions, epochs, key migration and guessing defenses.
4. ARC-06: enforceable keyring and isolated installer/service boundaries,
   building on the merged configuration and sealed-replacement protocols.
5. ARC-07, DOM-04 and the initial ARC-08 scope: shared audit/privacy/error
   contracts, responsive accessible components, formatting and baseline tests.
6. Full local validation; at least three independent full-branch dual-model
   review/fix rounds, with no validated High/Critical in the final round;
   one signed/pushed PR and green CI before human merge approval.

Dependencies may require a keyring primitive before its Family consumer; these
are internal checkpoints, not new phase boundaries. Source-promotion integration,
full source/submission/outbox scale fixtures and operational workflow consumers
remain with their existing later owners. Phase 1C completes production
startup, offline operator commands, credential/mount provisioning and the
integrated Gate 1 demonstration. No production startup, external provider
mutation, deployment, release or gate approval is implied by this batch.

## Status and evidence

Implementation is in progress. No Phase 1B task or review gate is claimed complete.
The per-package checklists remain authoritative; evidence is added as each
deliverable and its tests finish.

Internal checkpoint (September 10, 2026): trusted ingress/content/export
primitives and the WSGI response-lifetime adapter are implemented. The targeted
pure security/content/cryptography/scaffold run passes 121 tests, and seven
PostgreSQL response/transport tests pass. Google-only HTTP login passes 19
PostgreSQL tests using signed synthetic Google tokens; ten real-Valkey tests
cover admission, outage fallback, aggregate detection and durable incident
intent. Eight Family population tests pass for stable codes, cohort provenance,
cross-key collisions, rollback and bounded batch savepoints. Family token,
rehearsal/session, isolated installer, shared UI and review work remains in
progress; these are not completion claims for their larger work packages.

The PostgreSQL integration job now also requires the pinned disposable Valkey
service on loopback port `56379`; no real provider credentials are used.

The first complete PostgreSQL pass exercised 697 tests and found one missing
immutable-guard registration, since corrected. The strengthened token activation
and reopening path passes 77 lifecycle/storage regressions using actual prepared
generations. The responsive component suite passes 36 cases across Chromium,
Firefox and WebKit, including 320-pixel layouts, axe WCAG checks, keyboard focus,
browser-local timestamps, no-JavaScript fallbacks and session activity/expiry.
The shared formatter and CI-service-parity check passes 32 tests. These are
intermediate results, not the final full-suite or peer-review evidence.

Subsequent integrated checkpoint: all 712 PostgreSQL tests pass, the baseline
passes 2,113 tests, and 39 browser checks pass. Audit schema/storage tests pass
52 cases; Admin/Staff code-report, Family and Google HTTP integration passes
31 cases. Owner-only key-file primitives pass 15 tests, and the pure service
mount-policy matrix passes 16 tests. Isolated installer execution, rotation
completion and actual mount/DB-role proof remain unfinished. No peer-review
round or Phase 1B completion is claimed by the intermediate backup commit.

## Browser component validation

Install test-only Python inputs from `requirements/stewardship-browser.txt`, run
`npm ci --ignore-scripts --prefix tests/stewardship/browser`, then
`python -m playwright install chromium firefox webkit` (on Linux, include
`--with-deps`). Run `PARISHKIT_RUN_BROWSER_TESTS=1 python -m pytest
tests/stewardship/browser -q`. CI and release validation explicitly enable this
job; absent browser tooling is a failure there, not a silent skip. The ordinary
credential-free unit suite skips browser cases unless explicitly enabled.

The matrix exercises the three current browser engines on mobile/desktop sizes.
Actual current/previous branded Chrome, Edge, Firefox and Safari plus manual
screen-reader evaluation remain the OPS-09 release compatibility checklist;
engine automation alone does not claim those human checks complete. Assets are
self-hosted and versioned under `stewardship/ui-v1.*`; change that version when
the client contract changes. No scanner or browser-test dependency ships as a
user-facing asset, and no form answers enter browser storage.

Test-clock behavior follows [Playwright clock](https://playwright.dev/python/docs/clock)
and automated accessibility checks use the [axe API](https://www.deque.com/axe/core-documentation/api-documentation/).

## Dependency references

Google integration follows the maintained
[django-allauth Google provider](https://docs.allauth.org/en/latest/socialaccount/providers/google.html).
Content safety uses [nh3's allowlist sanitizer](https://nh3.readthedocs.io/en/stable/index.html)
and [Pillow's image validation/decompression protections](https://pillow.readthedocs.io/en/stable/reference/Image.html).
Pinned installation inputs are recorded in `requirements/stewardship.txt`.
