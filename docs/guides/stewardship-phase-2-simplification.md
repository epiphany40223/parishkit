# Phase 2 pre-production consolidation

[Acceptance](stewardship-phase-2-acceptance.md) ·
[Task coordination](../tasks/stewardship/overall.md)

On September 13, 2026 the human approved replacing unreleased stewardship
migration history with a fresh-install baseline, without promising compatibility
with existing development databases or deleting those databases. They also
approved grouping this PR into approximately eight to ten logical signed-off
commits and updating its branch with `--force-with-lease`.

The preserved reference is `5f686e37de238761a7fb09bad802db1f6ef231fa`, pushed to
`jsq/backup/stewardship-phase-2-before-consolidation-20260913`. It contains all
106 original Phase 2 commits. Later simplification work remains in progress;
this record is not new acceptance evidence.

## Required work

- [x] Capture the exact current schema from a newly created, empty reference DB.
- [x] Replace 183 development migrations with a dependency-ordered initial schema
  and Django model-state baseline, retaining current functions, triggers,
  constraints, indexes, defaults, row policies and seed sentinels.
- [x] Replace obsolete intermediate upgrade/downgrade tests with fresh-install
  equivalence and current-protection tests; do not waive current invariants.
- [x] Distribute all database tests across isolated CI runners, prove complete
  test accounting and aggregate the existing independent coverage floors.
- [x] Add timestamped test progress, slow-test diagnostics and finite job limits.
- [ ] Measure the resulting CI wall-clock improvement, retaining all required
  browser, container, database and baseline verification.
- [ ] Review and correct material changes, then consolidate logical commits,
  preserve sign-offs, verify the final tree and update the existing PR safely.
- [ ] Pass final-head CI and hand the open PR back for human merge approval.

The full serial database command remains a developer/release equivalent. No
real provider credentials, production deployment, release or PR merge is
authorized by this consolidation. Existing developer databases are not reset
automatically; the new baseline is for fresh installations.

## Local consolidation evidence

- Fresh-install catalog equivalence passed; see the
  [schema guide](stewardship-schema.md#equivalence-and-regression-evidence) for
  object counts and the narrowly verified PostgreSQL cast-rendering differences.
- Forty fresh-schema/storage tests passed in 7.33 seconds, including schema
  creation (6.02 seconds) and the independent catalog fingerprint regression.
- Eighty-two CI partition, coverage-union, build and required-check tests passed
  in 4.56 seconds. Failure probes cover missing/tampered evidence, wrong source,
  incomplete partitions, real failed/skipped pytest runs and child timeouts.
- The credential-free baseline passed 4,327 tests in 35.78 seconds, with 2,563
  explicit opt-in profile skips and two existing warnings. This is not a claim
  that PostgreSQL, browser or Compose verification can be skipped.
- Migration drift reports no changes. Thirty-eight obsolete historical test
  functions were retired; mixed tests retain their current-state assertions.

The [supplemental review](stewardship-phase-2-consolidation-review.md) records
37 findings and their corrections or evidence-backed rejections. This includes
the missing SQL image assets reported by the first rewritten-head Compose CI
run; the rebuilt local operational group passed all 12 tests in 751.32 seconds.
That CI run also had cancelled database/browser jobs and is not acceptance.
Final-head CI timing and the next independent review remain pending.
