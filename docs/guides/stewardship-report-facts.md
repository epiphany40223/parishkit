# Stewardship ordinary report-fact increment

[Controlling sequence](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
· [Report tasks](../tasks/stewardship/reports.md)
· [Calculation contract](../specs/stewardship/reports/spec.md#participation-fact-materialization)

## Scope and boundary

Branch `pr/stewardship-report-facts` starts at verified PR #40 merge
`737be049dafd8881c02ecee9e61b8d177638eb13`. That PR passed all 24 exact-head CI
jobs (run `35129776022`) and all 24 protected merge-group jobs
(run `35131554150`).

This increment delivers the ordinary participation rebuild end to end: live
source/submission inputs, shared calculation, debounced demand, compiled
scheduler/worker execution, immutable publication, completion evidence and
recalculation verification. It is independently demonstrable without a report
screen or an email provider. Testing responses never enter these facts.

The existing storage foundation already supplies debounce, input freezing,
generation references and compaction guards. This increment connects those
services to actual calculations and the deployed background registry instead of
adding a parallel report store. Historical calculations use permanent promotion
and first-eligibility provenance; current calculations use exact retained source
membership. Both use immutable campaign settings and live submission versions
through the allocated watermark.

Before BG-07, a following materialization increment must add queued exact-input
requests, priority over ordinary work, input protection before those requests
start, and exact-generation waiting/reuse for export/digest consumers. It must
also add scheduled drift verification and the exact-input selection/updating
service used by later consumers. These cross-request lifetime rules are a
separate reviewable boundary from ordinary event-driven calculation. Neither
RPT-03.02 nor RPT-03.06 is claimed complete here. RPT-02's remaining statistics
and comparison calculations also remain prerequisites to their BG-07 consumers.

Interactive charts/cards, report selection controls, complete report workflows
and their accessibility matrix remain Phase 5. Periodic compaction scheduling
remains OPS-07. Gate 3 stays closed; no live-provider validation, Production
deployment, or release is authorized by this increment.

## Internal ownership

- `reports/calculations.py` computes detached daily values; it reads no current
  pointers, settings, clock, database or provider.
- `reports/materialization.py` loads exact columns, stages deterministic bounded
  chunks and publishes through existing fenced storage. Its verifier holds the
  generation read guard through input loading, recalculation and comparison.
- `reports/fact_production.py` reconciles the current campaign's inputs, including
  local-day rollover, and allocates at most one ordinary root per due scope.
  Compiled source refresh commits its hints with source/Family reconciliation;
  the existing final-submit owner already commits its own live-only hints.
- `reports/fact_tasks.py` binds persisted task identity to the demand, freezes
  inputs once and recovers that generation without consuming a later window.
  Completion receipt, demand release and task acknowledgment are atomic.
- `FactBuildReceipt` retains identifiers only. Its soft fact-set identity does
  not prevent compaction of superseded unpinned calculations. SQL checks require
  an actual owned ready generation; worker input pins require their exact
  retained builder. Scheduler grants exclude response answers and pledge values.

## Validation checkpoint

Implementation validation is ongoing; reviews and final CI are not yet claimed.

Passed so far:

- 30 pure calculation cases covering both populations, first/repeat responses,
  exact midnight, DST, skipped dates, missing money, zero denominator, ordering,
  frozen cutoffs and malformed inputs.
- 18 PostgreSQL materialization/worker/model-guard checks, including rebuild
  after actual source compaction and direct restricted scheduler/worker logins.
- Expanded worker suite: 11 tests including retry-root recovery with a newer
  pending window, local-midnight rebuilding, atomic completion failure,
  restore-hold recovery and rejected orphan pins/unrelated task claims. Reusing
  a ready generation also selects its interactive pointer under actual grants.
- 15 compiled source-effect/restricted-worker checks, including rollback of both
  report hints with a failed source promotion.
- 203 runtime/build regression tests after extending the closed registry,
  independent-producer failure matrix and Docker schema asset allowlists.
- Repository Ruff checks and formatting; full baseline: 6,037 passed,
  4,097 profile-specific skips, two existing client deprecation warnings (57.75s).

An earlier baseline run found 31 integration-expectation failures: the new
handler/producer was absent from runtime test expectations and the root Docker
allowlist needed the new schema asset. The focused 203-test rerun above passes;
the subsequent full baseline also passes. The eight-shard coverage run remains
required.

## Fresh schema audit

No upgrade migration or retained-database conversion is introduced. The new
receipt and guards are folded into the unreleased fresh-install baseline, and
Django's dry-run migration check reports no changes.

The independent audit installed exact base `737be049` and the current SQL into
two new disposable databases on port `55440`:
`stewardship_facts_base_20260916a` and `stewardship_facts_current_20260916a`.
It first proved the base inventory matched its committed fingerprint, then
compared the full value-free inventories. Results: one new relation, 11 columns,
19 constraints, five indexes, four functions and four triggers; no removals or
policy changes. The only changed existing function is the narrowly extended
`stewardship_source_pin_guard()` worker fact-input insertion branch.

The disposable audit command was
`.venv/bin/python /tmp/parishkit-report-facts.ca4lVF/audit-schema.py`.
That temporary helper is local evidence, not a promised repository command.
The permanent catalog and model-parity checks are
`tests/stewardship/database/test_schema_baseline_postgresql.py`; the independently
audited expected inventory is `tests/stewardship/database/schema-baseline.json`.
The audit created only the two named databases and deleted no existing data.

## Review ledger

Three successful dual-source review/fix rounds remain required before delivery.
Record their exact heads, every raw finding's disposition and post-fix validation
here. Exact-head PR checks, protected merge-group checks and verification on
freshly fetched main follow; no routine human approval pause is required.
