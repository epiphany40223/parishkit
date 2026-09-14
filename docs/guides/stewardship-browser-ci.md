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

The selector uses each collected item's actual `browser_engine` parameter.
It rejects empty, duplicate, unknown/unowned and missing-engine collections,
partial paths, `-k`/`-m`/`--deselect`/`--lf`/stepwise/ignore selectors, mixed
database profiles, and missing browser opt-in or no-skips protection. The
ordinary local browser command still runs the full suite unchanged.
Review corrections also reject collect-only/setup-only/setup-plan execution,
alternate configuration files, discovery configuration and all CLI INI overrides
except the workflow's exact diagnostic timeout. Every selected assertion body
must actually pass before a zero exit is accepted. For independent collection
inspection use the ordinary serial `--collect-only --collection-manifest`
command without the CI selector; CI execution itself cannot be collection-only.

The CI workflow invokes `python -m parishkit.stewardship.quality_browser --engine
<engine>`. This bounded runner clears inherited pytest selectors, keeps output
live, and requires a fresh private completion receipt outside the checkout.
Only an exact selected/executed case match for the requested engine produces
`CI_BROWSER_COMPLETE`; early `--help`/`--version` exits cannot satisfy the parent
check. Receipts are invocation-local and removed when that runner exits, never
reused as proof for another run. Effective discovery settings use pytest's
public `getini` API, not deprecated configuration internals.

CI runs three independent engine jobs with 15-minute bounds, fresh per-case
browser processes, timestamped start/end progress and 120-second diagnostic
stack dumps. Only the selected engine is installed. The existing protected
`stewardship-browser` aggregate always runs and requires matrix success.

Initial local validation: 112 CI/sharding/gate regressions passed; the full
default suite passed 5,082 tests (3,106 explicit opt-in skips); Ruff lint/format
and all tracked Markdown checks passed. Actual independent collection returned
639 serial cases and 213 per engine, with an exact disjoint union. The tests
also execute real synthetic pytest partitions, assertion failures, per-case
and whole-module skips, forbidden selectors and the workflow's aggregate shell
condition. The first local browser run passed all Chromium and WebKit cases,
but reproduced one pre-existing Firefox assertion race in isolation: a second
Submit click could return before its route callback populated the capture list.
The test now awaits the second response and requires exactly two submissions;
all six engine/choice cases pass. No application behavior changed.

### Review round 1

Session `20260914-021405-9329f8` reviewed the complete diff from
`6e493657da0b48985a642ddcd38430837640e06e` to
`53e6467654fcd3abfc0a4edc54e209a3b023052b`, tree
`4fe5703c3145ecfd261aedc4688b7fc8574b0d8e`. Both reviewers completed without
failure, degradation, mismatch or salvage. Raw severities: one High, two Medium,
three below-cutoff Low; three validated findings, all accepted and fixed:

- Codex High: execution-suppressing pytest modes could exit green without test
  bodies. Reject them and verify every selected passing call, including a real
  `pytest.exit(..., returncode=0)` incomplete-execution regression.
- Codex Medium: discovery overrides could retain three owners while omitting
  cases. Reject CLI/configuration discovery overrides and alternate INI files;
  allow only the exact harmless diagnostic timeout override.
- Claude Medium: negative probes accepted any nonzero code. Require the exact
  assertion/skip/collection error exit and diagnostic markers, including proof
  that the expected partition was selected.

Finalized artifact SHA-256:
`3d475b845dde60baff27f4a1b4a42201ef20539794a019f3ea3f26a532bdffe4`.
Post-correction CI/sharding/gate regressions: 128 passed. Complete browser and
default-suite validation is recorded with the following rounds.

### Review round 2

Session `20260914-022510-04a058` reviewed the correction diff from
`53e6467654fcd3abfc0a4edc54e209a3b023052b` to
`7bf804fe41c3c164255956d0b84aeda6b04138c7`, tree
`85cfb9a654a63fdc133aa2577003e6056412dc49`, with surrounding selector/workflow
context and the independently reproduced test synchronization correction.
Both reviewers completed without failure, degradation, mismatch or salvage.
Raw severities: one High, two Medium, five below-cutoff Low. All three validated
findings were accepted:

- Codex High: help/version/marker early exits can precede pytest session hooks.
  Add the external completion-receipt runner and real subprocess probes, reject
  markers explicitly, and keep the aggregate dependent on the runner's result.
- Claude Medium: replace deprecated `config.inicfg` access with public effective
  discovery values; test real INI-file overrides as well as CLI overrides.
- Claude Medium: reject implicit stepwise skip/reset flags before builtin hook
  ordering can enable partial selection; verify both real CLI destinations.

Finalized artifact SHA-256:
`271be9b8a5f87d1855f5f497fa80f284f327f0b26f516624119fb0c40c5c7954`.
Round-1 correction validation completed: 5,098 default tests passed; all 639
browser cases passed in parallel (Chromium 115.77 seconds, WebKit 190.08 seconds,
Firefox 395.19 seconds). Their executed teardown IDs and selected manifests
exactly matched the independent serial collection, 213 per engine. Final
external-runner validation is recorded below.

### Review round 3

Session `20260914-023939-b19499` reviewed the correction diff from
`7bf804fe41c3c164255956d0b84aeda6b04138c7` to
`27a1f1b864f4d48c409a51c96611099fc7e1dfb6`, tree
`546a2ff64f5bf227aea1b4712db34b9995f6ff2b`, including the external runner,
workflow, surrounding selector, and regression tests. Both reviewers completed
without failure, degradation, mismatch or salvage. The finalized result is
APPROVE: zero validated findings; raw seven Low, zero Medium/High/Critical.
Six Low findings were below cutoff and one referenced a path outside the diff.
Codex's actual structured result is APPROVED with an empty findings list and
exit 0 (244 seconds, no timeout/stall); finalize's `parsed: false` telemetry for
that empty list is not a missing review.

Finalized artifact SHA-256:
`9ad1c2d46dd7eb4023dd9200c3d508fbb026e6cb48ee6f7b9b82098f7749a4ac`.
All six accepted Medium-or-higher findings from rounds 1 and 2 are fixed. Three
dual-source rounds are complete, and the final round has no High/Critical
findings. No accepted Medium-or-higher issue remains unresolved.

### Final local validation and delivery

- 5,129 default tests passed; 3,106 explicit runtime opt-in skips remain outside
  the default profile. CI/sharding/gate regressions: 159 passed.
- The exact `quality_browser` CI command passed all 639 cases, 213 per engine:
  Chromium 130.02 seconds, WebKit 194.79 seconds, Firefox 395.56 seconds. Each
  parent runner validated a fresh completion receipt. Selected and executed
  IDs exactly matched the independent complete serial collection with no
  overlaps, omissions or skips.
- Ruff lint/format, all tracked Markdown, whitespace checks and current
  model/baseline drift checks passed. No development database was deleted or
  upgraded and no application/provider/deployment/release action occurred.

Consolidation preserves the complete reviewed history on local branch
`pr/stewardship-browser-ci-reviewed`. Implementation `1bb17476d4ae2a8f37be5d3fc9da6cd1d253e9fe`
has exactly the same tree as reviewed `27a1f1b`; the direct diff is empty. Keep
the pre-squash review endpoint mapping, not a misleading triple-dot correction
comparison across the rewritten ancestry. Subsequent changes only record this
review/validation evidence and task status.

PR final-head CI and protected merge-group CI remain required. The human's
standing merge/continue authority applies after these pass. Measure actual
GitHub engine/gate timing before claiming a CI speedup. The next implementation
increment is financial forms, only after this PR's merge is verified on main;
Gate 2 and later OPS-09 release/load-test work remain open.
