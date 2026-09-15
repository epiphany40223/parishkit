# Campaign boundary processing

[Coordinating tasks](../tasks/stewardship/overall.md#phase-4-production-scheduling-and-delivery) ·
[BG-02 plan](../plans/stewardship/background-processing.md#bg-02-campaign-boundary-occurrences) ·
[Boundary contract](../specs/stewardship/background-processing/spec.md#campaign-lifecycle-boundaries)

## Scope

Branch `pr/stewardship-campaign-boundaries` starts from verified PR #31 merge
`f4e000c5b3f7024e47c7f6d3dbdd30c6cd4976e1` on refreshed `origin/main`, after
[complete protected delivery](stewardship-delivery-reviews.md#protected-delivery).
Deliver BG-02's Phase 4 start/close behavior as one coherent scheduler/worker
increment: durable occurrence production, compiled admission and execution,
ordered overdue recovery, end-date replacement and their real database/runtime
tests. Individual records or helpers are internal checkpoints, not PRs.

BG-02.03 retains shared restore/reopen token preparation in Phase 6. BG-03/04
retain actual Production cleanup and schedule fulfillment; BG-06 retains mail
dispatch and ADM-05 retains activation. Do not expose those unfinished owners
or weaken existing held-work checks to make this slice runnable. Full affected
mail reconciliation remains BG-04; boundary integration must preserve its
existing conservative admission until that owner is ready. Gate 3 remains
after complete Phase 4/5 integration.

## Internal checkpoints

1. Connect the existing ordered lifecycle journal to bounded durable occurrence
   production and the compiled scheduler/worker registry. Use stable identities,
   authoritative database time, current campaign/mode/gate admission and the
   existing TaskRun lease/recovery substrate. Broker hints remain non-authoritative.
2. Complete transactional start/close execution, exact proof and audit metadata.
   Close-first delivery applies any overdue start in the same transaction, with
   no externally visible active gap and no out-of-interval Family access.
3. Reconcile end-date changes with safe queued-work replacement and current
   worker exclusion. Closing first leaves the change to the guarded reopen
   owner. Preserve complete schedule reconciliation and provider-uncertainty
   blockers; do not introduce a partial date-only escape path.
4. Prove duplicate/restart/DST/exact-boundary behavior, missing/stale/foreign
   claims, crash-before/after domain commit, delayed hints, held gates and real
   races. Verify restricted SQL roles, metadata-only scheduler behavior and
   runtime registry wiring without provider credentials or live external writes.
5. Audit any fresh-install SQL/model deltas without retained-database deletion.
   Run repository and integration validation; complete three dual-source
   review/fix rounds and final-head CI before protected delivery.

## Evidence

Planning and implementation have started; no BG-02 task is newly complete.
Existing boundary/storage race tests are prior substrate evidence, not proof
of the new compiled scheduler and worker integration.
