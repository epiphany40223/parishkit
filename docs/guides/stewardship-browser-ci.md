# Browser CI parallelization increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[OPS-09 plan](../plans/stewardship/operations.md#ops-09-ci-coverage-browser-acceptance-and-release-pipeline) ·
[Quality specification](../specs/stewardship/operations/spec.md#ci-and-local-validation)

## Boundary

Branch `pr/stewardship-browser-ci` starts from PR #27's verified main merge,
`6e493657da0b48985a642ddcd38430837640e06e`, after complete merge-group CI passed.
This is an OPS-09 maintenance increment before financial forms. The owner
requested materially shorter CI delays; PR #26's browser job took 16 minutes
36 seconds, and PR #27's first run took 17 minutes 23 seconds. Its corrected
run took 14 minutes 50 seconds. All 639 browser cases passed in both PR #27 runs.

Partition CI by the actual Chromium/Firefox/WebKit fixture parameter, never by
substring test-name matching. Each test belongs to exactly one supported engine;
unknown, unowned, duplicate or partially selected cases fail the CI selector.
Retain the existing full-suite local command, fresh processes per test, normal
timeouts/skip protection, and the required `stewardship-browser` check name as
an always-running aggregate that fails on failed, missing or cancelled work.
No application behavior, database schema, dependency version, deployment or
release changes belong here. OPS-09's later release/load-test work remains open.

## Checkpoints

1. Add pure engine partitioning and an explicit opt-in pytest CI selector with
   strict complete-collection validation; preserve ordinary local behavior.
2. Run engine matrix jobs in parallel, install only their required browser,
   retain no-skips/progress diagnostics, and preserve the protected aggregate.
3. Test disjoint/exhaustive ownership, invalid/partial selection, failure/skip
   propagation and workflow gate behavior. Collect the actual complete suite
   and run every engine's selected tests locally.
4. Complete at least three dual-model review/fix rounds, then final-head and
   protected merge-group CI. Compare observed critical-path runtime without
   claiming a guaranteed speedup before measurement.

## Evidence

Preparation only; implementation and the new review cycle are in progress.
