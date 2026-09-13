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
106 original Phase 2 commits. The later checkpoint `003f786` is preserved on
`jsq/backup/stewardship-phase-2-before-five-groups-20260913`. The current PR has
five logical signed-off commits; regrouping preserved the complete source tree.

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
- [x] Measure the resulting CI wall-clock improvement, retaining all required
  browser, container, database and baseline verification.
- [x] Review and correct material changes, then consolidate logical commits,
  preserve sign-offs, verify the final tree and update the existing PR safely.
- [x] Pass final-head CI after the final corrections and merge under the human's
  standing authority, then wait for the merge to land on `origin/main`.

PR #22 merged on September 13, 2026 as
`7b2b1dd4478ae4014b167d6c0c203127c9a0ceb9`. Final-head
[CI run 34767983463](https://github.com/epiphany40223/parishkit/actions/runs/34767983463)
passed in exactly ten minutes, and protected merge-group
[CI run 34768576640](https://github.com/epiphany40223/parishkit/actions/runs/34768576640)
also passed. The merge was verified on refreshed `origin/main` before the next
branch was created; no protection bypass, deployment or release occurred.

The full serial database command remains a developer/release equivalent. No
real provider credentials, production deployment or release is authorized by
this consolidation. The human separately authorized merging PR #22 when its
review and validation gates pass and continuing with smaller increments; see
the [delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
Existing developer databases are not reset automatically; the new baseline is
for fresh installations.

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

## CI timing after partitioning

[CI run 34756500835](https://github.com/epiphany40223/parishkit/actions/runs/34756500835)
passed all checks at `4aee4cdec37453f7872bb44154d4dee201c17faa`. From the first
job start to the final required aggregate, the run took 10 minutes 46 seconds,
down from 18 minutes 52 seconds in the preceding complete run. The Compose path
fell to approximately 4 minutes 15 seconds by running all eight operational
scenarios on isolated runners. The browser job took 10 minutes 22 seconds;
PostgreSQL plus aggregation took 10 minutes 46 seconds, down from 11 minutes
32 seconds. The database improvement is modest; the main gain is Compose.

All 2,076 database tests were accounted for; scoped aggregate coverage was
93.84% lines and 85.16% branches. Lease waits, safety windows and representative
test populations were not reduced. The credential-free local baseline passed
4,343 tests in 37.33 seconds, with 2,572 explicit opt-in profile skips and two
existing warnings. Ruff, formatting and Markdown checks passed. Later changes
still require final-head CI. The completed second supplemental dual-source round
and its passing correction tests close the independent review gate; see the
[final round evidence](stewardship-phase-2-consolidation-review.md#supplemental-round-2-completed-correction-review).
