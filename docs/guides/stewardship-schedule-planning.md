# Stewardship schedule planning increment

Branch `pr/stewardship-schedule-planning` starts from verified PR #33 merge
`ce1e95d129646bae4d3f6fe2acdb0ad8dfd7767c` on September 15, 2026.

## Scope and delivery boundary

This increment starts [BG-04](../tasks/stewardship/background-processing.md#bg-04-schedule-revision-fulfillment-and-mode-routing)
under the [controlling Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).
Implement the campaign-local evaluator and ordinary schedule planning,
revision/semantic identity, safe replacement/removal and coalescing contracts
together with their Admin preview and database/runtime tests. They form one
coherent scheduling outcome, not a separate PR per helper or schema change.
Reuse existing interval resolution, applied configuration, occurrence and
fulfillment owners. The normative
[schedule workflow](../specs/stewardship/background-processing/spec.md#schedule-replacement-and-removal)
controls behavior.

Deliver the bounded asynchronous activation catch-up owner in the following
increment using these shared contracts. Its multi-batch group staging and
preparation hold are a distinct interruption/recovery boundary worth reviewing
separately. Keep BG-04's mixed tasks unchecked until all their named scope is
accepted. This split does not alter acceptance criteria or release Gate 3.

BG-06 retains Family message rendering, deliverability recovery and actual
provider dispatch. BG-07 retains receipt/digest rendering and report-fact
integration. ADM-05 retains the Production activation UI. None of those later
capabilities is enabled by incomplete scheduling work. Keep the fresh-install
schema policy, restricted runtime grants and fake/disposable validation boundary.

## Execution checkpoints

- PR #33 delivery and its complete final-head/merge-group evidence are recorded
  in the [cleanup guide](stewardship-production-cleanup.md#protected-delivery).
- The first implementation checkpoint adds the shared bounded civil-slot
  evaluator and uses it in schedule-edit and archived-campaign clone previews.
  Daily keys name the reported day, including the final day after close;
  recurrence uses the existing gap/fold resolver. Keyset cursors advance over
  wholly skipped days. An optional final weekly candidate is finite and does
  not itself establish an item-coverage obligation or delivery permission.
- The evaluator/form checks pass 50 tests; the actual Admin schedule, clone
  and window workflows pass 25 PostgreSQL tests in 29.00 seconds. An initial
  test queried a nonexistent request state field; the corrected test checks
  unchanged request count and campaign configuration. The full baseline passes
  5,582 tests in 50.42 seconds, with 3,594 expected environment skips and two
  existing warnings. Ruff, formatting and Markdown pass.
- Ordinary durable planning, replacement/removal effects, coalescing,
  restricted-role/runtime integration and the three review/fix rounds remain
  open. No BG-04 task is checked complete based on the evaluator checkpoint.
