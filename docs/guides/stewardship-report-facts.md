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
- Runtime SQL deliberately admits only the compiled ordinary builder in this
  increment. The queued exact-request owner must add its own closed task/input
  binding before using these storage primitives; it must not simply exempt
  other task types. OPS-07 separately owns compactor grants (compaction deletes
  generations, rather than updating the builder's checkpoint).
- Abandonment at five attempts exhausts automatic recovery. Failed task metadata
  and attempt history remain visible in the existing Admin task list. A frozen
  generation/demand stays protected for the canonical explicit linked retry;
  later demand stays pending until that checkpoint completes. Neither a tick
  nor a later hint resets that root's budget or discards its frozen inputs.
  A terminal root that never froze inputs is different: a later demand revision
  may allocate an independent root. Explicit retry of the obsolete pre-claim
  root is denied, both before and after that replacement is queued. An existing
  nonterminal, unfrozen root still coalesces new events and claims the latest
  pending inputs normally.
  Replaying an already-bound retry command returns that run's actual status
  after fresh campaign admission; it allocates/restarts nothing, even when a
  newer revision has superseded the run. This is distinct from a new retry.
  The report selection/status and operator retry controls remain later RPT-03
  integration; this increment tests the actual retry service under worker grants.

## Validation checkpoint

Delivered in PR #41: exact-head run `35141293392` passed all 24 jobs and DCO;
protected merge-group run `35143121665` passed all 24 jobs. It merged as
`2eade2a53163d621ade3145ede092f7316055bda` at 2026-09-16 20:10:11 UTC,
verified on freshly fetched `origin/main`. The following
[consumer increment](stewardship-report-selection.md) integrates read-only
selection/verification before queued exact-request ownership. The partial
package boundaries above remain unchanged.

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
the subsequent full baseline also passes. The full eight-shard run at
`80b6bf5` also passed: all 3,253 PostgreSQL tests accounted for, 93.98% line
coverage and 85.16% branch coverage. Individual database shard times were
approximately 9–12 minutes on the local disposable fixtures.

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

This section is a delivery ledger, including temporary local evidence above,
not a promise that another maintainer has those local paths or databases.
Durable CI evidence and the merge checkpoint supersede in-progress statements.

### Round 1

Full PR range `737be049..80b6bf5`, Pika session
`20260916-145332-44e885`: both sources completed without degradation or repair.
Pika requested changes: one High and three Medium findings passed its filter;
all 12 raw findings are dispositioned below, including eight Low findings.

- Claude 1, High, source/report admission: accepted and fixed. Source refresh
  explicitly remains admitted during go-live cleanup; report writes do not.
  The source owner now defers only an admission-denied hint, with a real
  go-live/release/scheduler regression. Admitted hint failures still roll back
  source promotion. Restore and purge were already denied by source admission.
- Claude 2, Medium, failed roots returned by the producer: fixed the misleading
  allocation result. A replayed terminal root is no longer returned as newly
  queued work. Rejected silently allocating a replacement or clearing the
  checkpoint: the normative [TaskRun retry contract](../specs/stewardship/background-processing/spec.md#durable-scheduling-and-task-execution)
  requires an explicit linked retry after terminal failure, retaining the
  unchanged domain checkpoint. Exhaustion-before/after-freeze regressions prove
  that behavior and preservation of the newer pending window.
- Claude 3, Medium, future exact/compactor writers: documented the closed
  current binding and the next owner's required extension above. Rejected
  pre-authorizing unimplemented task types. Exact requests and periodic
  compaction have explicit later owners; the current compactor uses DELETE,
  outside this INSERT/UPDATE binding trigger.
- Claude 4, Low, ready-checkpoint recovery coverage: fixed. A failed completion
  acknowledgment is followed by actual linked retry, reuse without calculation,
  one receipt and demand release. Exhaustion regressions cover both claim cases.
- Claude 5 and Codex 2, Low, duplicate pointer publication: fixed once for both.
  Only successful demand completion selects the interactive pointer; the ready
  generation remains recoverable if that completion transaction fails.
- Claude 6, Low, repeated historical scans: deferred to the remaining RPT-02
  calculation/performance matrix. The complete small campaign series is an
  expressly permitted rebuild strategy; retain the simpler deterministic
  implementation until measurements justify another cursor/index algorithm.
- Claude 7, Low, contradictory per-Family sequence/timestamp ordering: fixed.
  Detached inputs now reject such chronology before calculation, with a pure
  regression; no response can be counted as first on two dates.
- Claude 8, Low, pre-start empty generations: retained intentionally. Exact
  source/date hints and empty series obey the same provenance/deduplication
  contract before start; these are two bounded local builds per day, not
  provider calls. Any optimization must preserve exact-input selection.
- Claude 9, Low, temporary evidence/in-progress prose: addressed as this explicit
  delivery ledger; the permanent validation entry points are already named.
  Final-head CI and merge evidence will be appended before claiming delivery.
- Claude 10, Low, receipt zero revision/fence: rejected as already guarded.
  The receipt must match an actual claimed demand revision and live task fence;
  demand claim requires the positive pending revision, and task claim increments
  the fence. The zero-capable column constraints follow Django's existing
  PositiveBigIntegerField baseline; zero cannot satisfy the receipt guard.
- Codex 1, Medium, exhaustion retaining the checkpoint: rejected the proposed
  automatic discard/restart for the same retry-contract reason as Claude 2.
  Terminal failure is visible through the existing task status/event UI, not
  represented as successful facts. The new five-attempt regressions exercise
  actual recovery, retained ownership, explicit retry and pending follow-up.

Round-1 post-fix validation passed: 32 PostgreSQL materialization, worker,
source-effect and verification-race tests (63.96s), 196 focused pure/runtime
tests (1.08s), Ruff and Markdown checks. Two more successful dual-source
review/fix rounds were still required at that checkpoint. The full subsequent
baseline also passed: 6,038 tests, 4,100 profile-specific skips, two existing
client deprecation warnings (55.72s); model drift, Ruff and Markdown passed.

### Round 2

Correction range `80b6bf5..be5784f`, Pika session
`20260916-151218-fa901a`: both sources completed without degradation or repair.
Codex approved with no findings; Claude supplied two Medium and five Low
findings. Every raw finding is dispositioned here:

- Claude 1, Medium, pre-claim supersession documentation/coverage: fixed the
  distinction above. A later revision can replace a terminal root with no
  checkpoint; it never resets that root or discards frozen inputs. New tests
  exercise replacement and separately show queued roots still coalesce events.
- Claude 2, Medium, obsolete explicit retry racing replacement: fixed. An
  unclaimed terminal root must still match the pending revision's execution key
  to admit a new explicit retry. At this checkpoint an additional competing-root
  check applied to retry/replay; Round 3 below replaces that redundant branch
  with correct read-only replay behavior. Tests cover both orders for the key
  check: superseding hint before
  retry, and replacement allocation before retry. Frozen-root retry still owns
  its original inputs and does not compare against a newer pending revision.
- Claude 3, Low, backward-clock chronology: retained fail-closed validation.
  The earlier calculation could count a Family first on two dates; SQL complete-
  series validation already rejected that result. This moves rejection earlier,
  rather than introducing an inability to publish an otherwise valid series.
  Reinterpreting immutable response timestamps/first-submission identity is not
  a permitted automatic report repair; failed task evidence remains visible.
- Claude 4, Low, admitted hint exception coverage: the existing `facts_failed`
  case already proves RuntimeError rollback. Added `facts_denied` to prove a
  PermissionError raised after admitted hint effects also rolls everything back.
- Claude 5, Low, exhaustion fixture wording: clarified the attempt-counter
  criterion above. Attempts 1–4 seed real retryable-failure transitions; attempt
  5 uses actual expiry/recovery. The separate restore-hold test already drives
  real earlier recovery into `retry_wait` without spending attempts while held.
- Claude 6, Low, real one-second retry waits: retained the actual PostgreSQL
  timing/transition guards. These two cases add about ten seconds across the
  partitioned suite; replacing immutable task timing or disabling guards would
  weaken the regression. No long lease sleeps or busy polling were added.
- Claude 7, Low, current-campaign hint commentary: clarified the verified current
  campaign ownership and narrower report admission. The suggested non-current
  refresh cannot reach these effects: `require_source_refresh` and the repeated
  `verify_refresh_attempt` require the current campaign under the work-order lock.

Round-2 post-fix validation passed: 24 PostgreSQL worker/source-effect tests
(55.39s), 196 focused pure/runtime tests, Ruff and Markdown. One more successful
dual-source round, final-head PR checks, protected merge-group checks and fresh-main
verification remain required; no routine human approval pause is required.

### Round 3

Correction range `be5784f..d8f6557`, Pika session
`20260916-152323-937bc0`: both sources completed without degradation or repair.
There were no High/Critical findings. One Medium and five Low raw findings are
dispositioned below; corrections are part of this third round, not a requirement
for an otherwise finding-free fourth round.

- Claude 1, Medium, untested competing-root/replay branch: fixed. Removed the
  redundant competing-root query: the window-key check, unique execution key,
  and work-order-serialized producer's nonterminal exclusion already prevent a
  competing new retry. Added real saved-command replay tests before and after
  supersession/replacement completion; they prove no run is allocated or revived.
  Corrected Round 2's coverage wording above.
- Claude 2, Low, replay depending on mutable demand state: fixed. A repeated
  command returns its already-bound persisted result after fresh campaign
  admission, independent of current debounce/claim ownership. The test also
  proves restore admission still denies replay; no new-work gate is bypassed.
- Claude 3, Low, newly postponed queued window: added a separate not-yet-due
  regression proving the root stays queued at attempt zero with the new window
  intact. The existing due-window test proves later latest-input claiming.
- Claude 4, Low, paragraph wrapping: fixed.
- Codex 1, Low, missing replay test: fixed by the same saved-command regression
  as Claude 1; its outcome follows the corrected read-only replay contract.
- Codex 2, Low, duplicated execution-key formula: fixed. Allocation and admission
  share the pure `rebuild_execution_key`; a unit test fixes the existing UUID
  derivation and rejects malformed identity/revision inputs.

The broader 72-test PostgreSQL fact storage/demand/materialization/recovery/
retention/race suite passed at the reviewed head (85.01s). The full baseline at
that head passed 6,038 tests with 4,104 profile-specific skips and two existing
client deprecation warnings (57.37s). Final post-fix validation passed:
26 PostgreSQL worker/source-effect tests (56.37s), 35 pure calculation/runtime
tests (0.15s), and the full baseline (6,039 passed, 4,106 profile-specific skips,
two existing client warnings, 59.21s). Ruff, Markdown, model drift and whitespace
checks passed. There are no unresolved accepted Medium-or-higher findings.
All 25 raw findings across three successful rounds have recorded dispositions.
Protected CI/merge evidence belongs to the PR delivery record and the next
increment's verified-main checkpoint; this ledger does not claim a merge yet.
