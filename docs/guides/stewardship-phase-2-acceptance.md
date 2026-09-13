# Phase 2 integrated acceptance

[Execution guide](stewardship-phase-2.md) ·
[Controlling phase](../plans/stewardship/overall.md#phase-2-source-truth-initial-setup-and-campaign-preparation) ·
[Milestone status](../tasks/stewardship/milestones.md#phase-2-setup-and-source-truth) ·
[Review ledger](stewardship-phase-2-reviews.md)

This index identifies executable acceptance evidence for the complete Phase 2
batch. The chronological review ledger remains the record of earlier component
checks and corrections; those checkpoints alone do not close this phase.

## Demonstration and isolation

The `complete` cases in
[the operational Compose suite](../../tests/stewardship/test_operational_compose.py)
start a new disposable database and private native volume with no installed
ParishSoft or Workspace credential. They run the actual bootstrap, migration,
runtime-grant, web, scheduler, worker, mail and target-installer commands.
Both development and production profiles use the built application image.

One original authenticated browser submits the public wizard, sealed credentials,
normalized logo, source-load command, financial campaign, share options, content,
schedules, readiness test and final confirmation. Its requests pass through the
actual Django middleware and restricted web SQL identity. Only Google exchange/
certificate retrieval is synthetic; state, PKCE, nonce, JWT validation, CSRF,
session policy and current authorization remain real.

The source and delivery owners retain real journals, restricted SQL identities,
handoff encryption, private mounts, fences and immutable completion evidence.
Only the external provider responses are replaced. No real parish data or
provider credentials are used, and no real email is sent. Finite helper-process
and uncertain-delivery behavior have separate unit/PostgreSQL acceptance cases;
the Compose stub is not evidence of real provider connectivity.

The host recreates each required consumer from a complete configured topology
and invokes acknowledgement inside that actual running service, never an
ephemeral replacement process. The web never receives Docker access or a target
installer's private filesystem. Completion returns the original browser to the
ordinary Admin portal in Testing with a draft campaign. Read-only operator SQL
then verifies financial-source counts, eligible Family codes, coherent population,
terminal staging scrubbing and absence of live mail fulfillment.

The production-profile form driver uses Django's test client inside the real
web service. It is not a replacement for the separate real Caddy TLS/ingress
checks or the three-engine browser suite.

## Acceptance map

| Required relationship | Executable evidence |
| --- | --- |
| Empty deployment through configured Testing campaign | Both `complete` Compose cases, plus restricted finalization producer/consumer tests |
| Exact YAML/database agreement and interrupted installation | Setup YAML preparation, configuration cancellation, startup and completion PostgreSQL suites |
| Aborted or expired staging cannot return | Setup expiry, credential installation, staged/final disposal and stale-worker tests |
| Full/delta promotion is atomic and failures preserve truth | Source execution, delta, rejection, failure, fallback and outcome PostgreSQL suites |
| Family identities survive inactive/reactivated source rows | Source Family reconciliation and credential lifecycle/race suites |
| Ministry inactivity survives import and preserves manual roles | Ministry activity, chair reconciliation, seed evidence and source-effects suites |
| Page/email previews do not create live fulfillment | Setup/content preview, campaign readiness-mail and browser suites; final Compose SQL proof |

The tests live under [database](../../tests/stewardship/database),
[browser](../../tests/stewardship/browser) and
[stewardship](../../tests/stewardship). Each test's assertions, rather than its
filename alone, define the proof. See the
[database guide](stewardship-database-tests.md) for the fixed disposable services.

## Integrated validation status

Final corrected implementation: `ea2d5cb974bccbe2fc5ec85fadded17a07a8bbeb`.
All results below are from that source tree. Subsequent handoff documentation
and the CI test-fixture correction below do not change production code.
Validation completed September 13, 2026 UTC.

- Latest final-run baseline: 4,297 passed; 2,610 explicit profile
  skips; two existing warnings in 65.08 seconds. These skips require their
  separate opt-in suites below.
- Browser: 447 passed in 438.68 seconds across the supported engines, with no
  skipped cases.
- Final-image container, isolation, provisioning and lifecycle smoke: all 59
  cases passed in 108.97 seconds, with no skipped cases.
- Operational Compose: all eight cases passed in 724.96 seconds against the
  corrected final image, with no skipped cases. This validates both completion
  and selected-but-unapplied abort in development/production topology.
  The [round-3 record](stewardship-phase-2-full-review-3.md) retains failed
  diagnostics and the corrected metadata-only mail-admission query.
- Full PostgreSQL coverage: all 2,114 cases passed in 3,106.18 seconds, with no
  skipped cases. Combined stewardship/shared scope coverage is 94.16% lines and
  85.04% branches, independently exceeding both 80% floors. The disposable JSON
  report is `/tmp/parishkit-phase2-review3-final-coverage.json`, not committed.
- Final Ruff, formatting, all tracked Markdown, migration-drift and whitespace
  checks pass, including this acceptance index and the updated task evidence.
- Three independent full-phase dual-model reviews completed without degradation:
  respectively 36, 23 and 39 validated Medium findings, no High/Critical in any
  of those complete-phase rounds. All retained corrections and focused checks
  are complete, and passing final integrated validation closes the third
  review/fix round; see the [review ledger](stewardship-phase-2-reviews.md).

Phase 2 implementation, local acceptance and the three-round review/fix gate
are complete. Delivery and final-head CI are tracked on
[PR #22](https://github.com/epiphany40223/parishkit/pull/22). The results above are
local acceptance, not a substitute for that PR's required checks. Human merge
approval remains required. Do not begin the Family implementation phase or merge
this branch without that approval.

### CI fixture correction

[The first final-head CI run](https://github.com/epiphany40223/parishkit/actions/runs/34746200392)
passed validation, browser and operational Compose jobs. Its database job passed
2,113 tests but errored while setting up the integration-selection migration
case. The synthetic credential-role fixture lacked column-level
`SELECT(id, validation_schema)` on configuration versions, already present in
production installer provisioning. PostgreSQL generic plans check that audit
fallback relation's privileges even when custom-plan short-circuiting previously
masked the fixture omission.

The fixture now matches those narrow production metadata grants. Two new
regressions force custom and generic plans before credential installation,
verify successful attribution and completion, and still require SQLSTATE
`42501` when reading the private canonical configuration document. Both failed
before the fixture correction; the generic-plan case reproduced the CI stack.
All 47 integration-selection, credential-isolation and integration-view cases
then passed locally in 77.79 seconds. This is a test-only CI correction, not a
production permission expansion or a new implementation review round. The PR
must pass a complete CI rerun before human merge approval.

## Boundaries retained for later phases

Phase 2 enables source refresh, initial setup and preparation, not live campaign
operation. Production transitions, Family submission/merge, scheduled live mail,
ministry follow-up, census publication, reports, restore release and exceptional
purge retain their controlling-plan owners. Mixed-phase DAT-04/DAT-05 tasks keep
their remaining submission/mail/seed-confirmation work explicitly separate from
the source-population and chair-suggestion integration delivered here.
