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
- The next checkpoint implements selection-owned cancellation of proven-unsent
  messages and pending/local-running occurrences, with retained failure and
  delivery history. Counts-only preview admission includes message and retry
  chain versions. Uncertain, malformed or shared delivery bindings block before
  YAML selection. The installer no longer has direct occurrence UPDATE access;
  its exact selection trigger owns cancellation and a private transaction proof.
- Twenty new PostgreSQL regressions pass in 20.63 seconds, covering actual
  restricted roles, ciphertext scrubbing, stale hints/retries, uncertainty,
  malformed links and rollback after a late injected audit failure. The preceding
  60-test schedule/installer run passed in 42.46 seconds. Sixteen independent
  model-contract checks pass in 16.17 seconds; 58 pure evaluator/context/Compose
  checks pass with 12 explicit operational opt-in skips. These are checkpoints,
  not full increment acceptance or completed review rounds.
- The combined strict-schema/reconciliation/outbox run passes 67 tests in
  44.48 seconds. Full baseline validation initially caught an omitted specialized
  Docker build-context entry and two test registries needing the new closed
  schema/view vocabulary. The fixes pass their 94 focused checks; the corrected
  full baseline passes 5,638 tests in 53.35 seconds, with 3,614 expected skips
  and two existing warnings. This total includes the subsequent pure recovery
  planner's 43 passing cases; durable scheduler integration remains open.

## Fresh-install schema audit

The reconciliation checkpoint independently installed the exact PR #33 SQL from
`ce1e95d1` and the current SQL into two new disposable databases, confirming the
reference against the committed strict fingerprint before comparing objects.
No existing development database was changed or deleted. The only additions are
two count-boundary views, one private transaction-proof table, 30 corresponding
columns, seven proof-table constraints, two indexes and two private functions.
The changed constraint admits the closed schedule audit schema. Five existing
functions implement the occurrence proof exception, stale delivery rejection,
typed context, selection admission and transactional selection effects. All
other existing objects, triggers, policies, ownership and ACLs are unchanged;
the three newly privileged trigger functions explicitly revoke PUBLIC execute.
The fingerprint was updated only after inspecting these differences.

The disposable audit databases on the owned PostgreSQL service at port 55442 are
`stewardship_schedule_base_20260915` and
`stewardship_schedule_current_20260915`. They are reference evidence, not databases
to upgrade. Later implementation changes require another explicit difference
audit before updating the strict fingerprint again.
