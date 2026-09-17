# Scheduled report-fact verification

## Scope and controlling contracts

This coherent RPT-03 service increment follows PR #43 from verified main
`053eed78a935443ff29ae6ed7f84778d582fb48d`. It implements scheduled drift
detection, immutable results, fenced execution/recovery, input protection and
actual-role tests. Follow the [reports plan](../plans/stewardship/reports.md),
[materialization contract](../specs/stewardship/reports/spec.md), and
[derived retention policy](../specs/stewardship/data/spec.md#derived-fact-retention).
Full report UI, remaining RPT-02 statistics, BG-07 digests, OPS-07 compaction
scheduling and Gate 3 remain later work.

## Execution design

The scheduler selects non-disposable ready generations in bounded batches,
at most once per generation per UTC day. It creates an immutable verification
request and TaskRun together, freezing the exact generation and input metadata.
Only one unresolved nonterminal verification may own a generation at a time;
a new day does not reset a running or recoverable request's retry budget.
The scheduler reads metadata only; the general worker performs recalculation.
Current pointers and durable pins keep their generations eligible. Obsolete
unreferenced generations awaiting compaction do not receive new checks; the SQL
request guard independently enforces this rule before allocating protection.

Queued/retrying work protects its exact generation against compaction. The
worker holds campaign and generation read protection while recalculating, without
holding the global work-order write lock. A bounded isolated-worker deadline
must stop the producer before releasing its read protection. Results and task
acknowledgment commit together under fresh gates and fencing checks.

Matching facts and detected drift are distinct successful check outcomes;
unavailable inputs, an interrupted calculation and exhausted retries are never
reported as matching facts. Drift records contain no report values and do not
rewrite published facts. Existing critical operational logging records drift
and exhausted verification work; BG-10 retains notification transport ownership.
Completed evidence retains generation/input identifiers without permanently
pinning disposable calculated rows. Later ADM-10 purge inventory must include
these campaign-owned request/result records.

The daily key is an operational UTC bucket, not a campaign chart date; campaign
statistics retain their existing parish-local day semantics. Unresolved work
blocks later buckets for the same generation. After bounded terminal failure,
a later day's independent periodic check may run; the failed run stays visible,
and same-day production never resets its retry budget. Explicit retries retain
the original request and require that its exact generation is still available
and not already owned by another nonterminal check.

## Fresh schema audit

Fresh disposable schemas were compared with the independently audited PR #43
baseline. The changes add two tables, 24 columns, 39 constraints (including
deferred owner/acknowledgment proofs), nine indexes, five functions and six
triggers. Existing objects change only the fact-disposal predicate and the
closed operational-event constraint. No existing object is removed; all other
definitions and policies are unchanged.
No retained developer database was changed or deleted. Model state is folded
into the fresh-install baseline; no historical upgrade support was introduced.

## Acceptance and delivery evidence

Initial implementation passes 66 focused PostgreSQL tests in 96.19 seconds,
including the complete schema/model contract, scheduled checks, ordinary
rebuilds and verification/retention races. The baseline passes 6,047 tests in
63.68 seconds (4,194 profile skips and two existing Valkey-client warnings).
Repository Ruff/formatting, changed Markdown, whitespace checks and Django's
model-state drift check pass. Broader sharded validation and independent review
follow; these initial results are not protected delivery approval.

Required coverage includes
cadence/idempotency, exact-input parity and drift, original inputs across retry,
crash/lease recovery, unavailable inputs, restore/purge admission, atomic result
acknowledgment, immutable/direct-SQL guards, actual scheduler/worker privileges,
compaction protection and its release, and runtime handler/producer integration.
Complete three successful dual-source review/fix rounds, exact-head CI/DCO and
all protected merge-group checks before delivery under the
[standing automated cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).

## Independent review record

Round 1 reviewed the full change at `457637a`, with successful Claude and Codex
results (no failed/degraded reviewers). Codex found no issues; Claude reported
two Medium and six Low findings. Pika's default cleanup removed the validated
Codex artifact; its `parsed: false` telemetry means zero accepted findings, not
a missing or failed result. Later rounds retain raw artifacts explicitly.

All raw findings, including those below Pika's confidence cutoff, were triaged:

1. Medium, unnecessary old-generation work: accepted. Production and SQL skip
   disposable generations, with actual-role pointer/pin selection tests.
2. Medium, speculative read deadline exhaustion: retained the existing bounded
   isolated-worker guard. Reference calculations for 5,000 Families, 50,000
   response versions and 365 days measured 1.355 seconds historical and 1.109
   seconds current, under concurrent regression load. Added both reference-size
   correctness tests. This measures calculation, not database I/O; Phase 7
   integrated performance acceptance remains required. Ordinary builds also
   calculate the whole series; only staging is chunked. The solo worker has no
   unrelated concurrent task to interrupt. No evidence supports weakening the
   established deadline/purge safety contract.
3. Low, immediate permanent failure for unavailable input: rejected. Transient
   `FactBusy` subclasses `FactUnavailable`; terminalizing that class would lose
   recoverable work. Bounded lease recovery and critical exhaustion are tested.
4. Low, drift audit outcome: accepted. A completed check records success, while
   its immutable result and separate critical drift event identify differences.
5. Low, UTC midnight batch race: deferred. The whole batch safely rolls back and
   the independent producer retries next tick; no partial ownership or false
   result is possible. Keep the shared campaign clock, including its controlled
   testing semantics, instead of introducing a second clock contract here.
6. Low, duplication: adopted shared `fact_inputs` equality. Kept domain-specific
   task binding/recovery helpers because their lifecycle admission differs.
7. Low, test gaps: added actual database-role cross-owner INSERT denials, audit
   ownership/context assertions and producer restore-hold exclusion.
8. Low, local audit coordinates: removed session-specific database name and port
   from the durable guide; retained portable catalog audit evidence.

The first full eight-partition regression run passed seven partitions and
exposed one test-inventory omission: the schema-wide immutable-record guard
test did not recognize the two new tables' shared guard names. Its explicit
mapping now covers those triggers; the production guards were already present.
This failed run is diagnostic evidence, not an acceptance receipt.

Round-1 corrections pass 107 focused schema/storage/verification/calculation
tests in 46.88 seconds and 192 verification/runtime tests in 36.33 seconds,
including the actual-role SQL bypass denial. The fresh catalog comparison,
repository Ruff/formatting and changed Markdown checks pass.

Round 2 reviewed `457637a..836f9ce` with both sources completing successfully;
Codex found no issues and Claude reported five Low findings. The actual-role
SQL bypass test now pairs rejection with the same manual allocation succeeding
after adding a retention pin, using the production UTC date convention. Redundant
pointer grants were simplified without changing effective permissions and the
invoker-rights scheduler dependency is explained. The long paragraph was wrapped.
Potential disposal-query tuning remains a Phase 7 measured-performance concern;
no observed budget failure justifies a speculative query rewrite. Review
receipts remain in this task-linked delivery ledger as required by the controlling
plan; they are historical evidence, not a promise of current benchmark timings.

Complete measurement at `836f9ce` accounts for all 3,355 PostgreSQL tests and
6,049 baseline tests, with 93.97% line and 85.15% branch coverage. One partition
initially failed an existing Family form-baseline guard assertion. The complete
16-test scheduling file and another 27 baseline/submission tests passed in
isolation; the entire 425-test partition then passed unchanged in 514.99 seconds.
The original failure remains retained externally and is not labeled a proven
clock defect. Only the complete successful same-revision receipts were combined.
Final-head CI will measure the subsequent test/documentation corrections anew.
Round-2 corrections pass all 22 focused actual-role verification/grant tests in
36.81 seconds, repository Ruff/formatting, tracked Markdown and whitespace checks.

Round 3 reviewed `836f9ce..95fc476`; both sources completed, with two Claude Low
findings and one Codex Low finding, and no Medium-or-higher findings. The grants
comment now names `stewardship_fact_disposable` and its broader metadata
dependencies. The positive control uses an operator pin rather than implying
that scheduled verification creates persistent pins. Codex's midnight-race
finding is already handled: `response_service` yields inside `campaign_clock`
at the campaign start, freezing both allocations. The test now explicitly
asserts that fixed instant so a future fixture change cannot hide the premise.
The current baseline also passes 6,049 tests in 58.77 seconds, with the same
profile skips and existing client warnings. All raw findings from all three
rounds are dispositioned; no accepted Medium-or-higher finding remains.
Final round-3 corrections pass all 22 actual-role verification/grant tests in
37.78 seconds, plus repository Ruff/formatting, tracked Markdown and whitespace
checks. The fixups are consolidated for PR delivery; exact-head CI/DCO and the
protected merge-group checks remain required before merge.

## CI identity-performance investigation

The consolidated implementation `2147a7d` passed all 24 exact-head CI jobs and
DCO in run `35168775231`. Its first attempt failed an existing Production
identity lookup budget at 5,000 Families (p95 3.303 seconds versus the unchanged
two-second limit). Two unchanged local reproductions passed, including branch
coverage instrumentation. Rerunning the complete failed 420-test partition
passed unchanged in 916.54 seconds, and the coverage aggregate accepted all
same-head receipts.

Protected merge-candidate run `35171368538` nevertheless reproduced the same
failure at p95 3.316 seconds; the other 419 tests in that partition passed.
The merge is not complete. Local passes do not establish that CI is merely
noisy, and repeated blind retries are not a resolution. The investigation adds
failure-only per-sample and per-query-ordinal timing evidence, never SQL text or
parameters, while preserving both original performance and query-count limits.

Focused dual-source review `20260916-220138-f37a05` examined the diagnostic delta
`2147a7d..77c4ee6`. Both sources completed successfully; Codex found no findings,
and Claude found two Low issues. Both are accepted: include the actual p95
sample's query timings as well as the slowest sample, and attach safe evidence
to query-count failures too. Four deterministic tests verify the failure
diagnostics, private-value exclusion, exact percentile and unchanged boundary.
The original diagnostic change also passed both PostgreSQL performance tests
in 50.91 seconds. After both review corrections, the two database tests pass in
54.87 seconds and the baseline passes 6,053 tests in 64.25 seconds (4,199
profile skips and the same two client warnings). Repository Ruff/formatting,
changed Markdown and whitespace checks pass. CI remains required; diagnostics
alone do not claim to fix the underlying slowdown.
