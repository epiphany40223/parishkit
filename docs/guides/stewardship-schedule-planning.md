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

This slice durably coalesces complete, unallocated Family groups. Digest planning
materializes bounded original slots only: the shared pure planner computes the
missed-date range, while the following bounded recovery increment owns durable
aggregate staging and coverage. BG-07 supplies pinned daily facts and final-weekly
item/correction coverage before digest dispatch is enabled. Task/outbox-bound
Family groups stay held for BG-06's journal-aware recovery; this slice never
cancels such work merely because it appears overdue. Those boundaries do not
claim completed mail delivery or a completed BG-04 package.

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
- The next checkpoint integrates bounded Family and digest producers into the
  actual scheduler process. Family groups commit selected/coalesced/skipped
  outcomes atomically; digest keyset pages alternate definitions so a daily
  backlog cannot starve the weekly schedule. Restarting repeats durable keys
  without duplicating work. Neither producer allocates tasks or outbox messages.
- The 92-test evaluator/recovery/planning run passes in 35.91 seconds with 92%
  combined line/branch coverage across the five planning modules. Eighty adjacent
  schedule/reconciliation/preview/runtime-grant tests pass in 56.17 seconds;
  146 runtime-process/grant tests pass in 0.65 seconds. Actual restricted scheduler
  logins can plan but cannot read Family codes, financial answers or outbox
  messages, or claim/rewrite worker-owned occurrences. Further interruption and
  traversal regressions, complete validation and all three reviews remain open.
- The interruption/traversal extension and strict schema baseline pass all 48
  checks in 33.32 seconds. The integrated full baseline passes 5,663 tests in
  58.69 seconds, with 3,636 expected profile skips and two existing warnings.
  Ruff, formatting and Markdown pass. The current application image rebuilt
  successfully; disposable operational checks and review acceptance remain open.

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

The integrated-planner audit compares fresh installations at `e5b3fbe` and the
working tree in `stewardship_family_plan_base_v2_20260915` and
`stewardship_family_plan_current_v2_20260915` on the same disposable port. Only
`stewardship_occurrence_guard_v1()` changes: the scheduler may reconcile only
unallocated pending work, and closed Production campaigns may retain missed
Family slots as skipped outcomes. The separate claim guard still denies Family
delivery after close. All other fingerprint categories and object counts are
unchanged. The strict fingerprint was updated after this comparison.

## Review round 1

The complete `ce1e95d1..65c4768` diff was reviewed in Pika session
`20260915-192104-f6f812`: two generated Claude shards covered all 45 manifest
files, and Pika's Codex reviewer completed successfully in 543 seconds. Both
Claude outputs passed validation and the exact permission preflight succeeded.
Finalization has no degraded source, failed agent or verdict mismatch. Raw
findings are nine Medium and seventeen Low; nine Medium findings were validated,
with no High/Critical findings. This is not yet three-round acceptance.

| Source/finding | Disposition |
| --- | --- |
| Claude 1: DST ordering | Rejected. The claimed reversed times omit their dates: March 7 at 07:30Z precedes March 8 at 07:00Z. An exact-cutoff probe includes both slots and confirms increasing UTC order. |
| Claude 2: digest exclusion tests | Accepted. Added actual delivered-coverage and restore-hold tests, including restart, resolution and reconsideration by the same running producer. |
| Claude 3: installer count scope | Accepted. Cache counts by each changed definition's owning campaign, including when there is no current campaign; a historical-owner preflight regression verifies the scope without inventing impossible archived in-flight rows. |
| Claude 4: omit missed post-close Family rows | Rejected. The normative recovery policy requires durable skipped outcomes when close overtakes missed Family work. Pending-to-skipped is atomic and does not grant dispatch; fulfillment cannot substitute because its dispositions are delivered/coalesced. |
| Claude 5: post-close due admission | Accepted. Family creation after close requires an original due instant inside the campaign interval; the independent claim guard still rejects post-close Family delivery. |
| Claude 6: preview validation performance | Rejected at Medium severity. Five runs under concurrent validation took a median 7.9 ms, maximum 9.6 ms, for 196 one-time plus four digest previews. Even an invalid all-200-daily upper bound took about 18.1 ms. No material latency defect was demonstrated. |
| Codex 1: repeated full digest scans | Accepted. Preserve exhausted cursors; only a changed count/version sum of retained restore inventory rewinds them, so resolved past holds are not lost. |
| Codex 2: failed initial followed by reminder | Accepted. An unresolved failed initial blocks selecting reminders without successful initial coverage; terminal failure is neither retried nor rewritten, while inapplicable pending work can still receive skipped outcomes. |
| Codex 3: contradictory terminal results | Accepted. Replacement blocks incompatible linked occurrence/outbox outcomes, including failed versus delivered and succeeded versus definitive failure; real storage transitions exercise the contradictions. |

The rebuilt review image passed three disposable operational scenarios in
277.83 seconds: configured development, complete initial setup, and configured
Production topology. Four isolated full PostgreSQL partitions completed with
2,866 passing cases and one failure. That failure confirms Claude 5: an old
regression requires rejecting a Family due instant at the exclusive campaign
end. It remains unchanged and passes after the guard correction. The failed
partition is not counted as complete quality/coverage acceptance.

The first correction runs pass 128 planner/recovery/review-guard tests in
49.34 seconds and 46 schedule/reconciliation tests in 37.41 seconds. The new
historical-owner test initially called the configuration document method as an
attribute; that fixture mistake was corrected before the extended rerun.
The fresh-install correction audit compares `65c4768` against the corrected SQL
in new disposable databases `stewardship_schedule_review1_base_20260915` and
`stewardship_schedule_review1_current_20260915`. Only the private
`stewardship_schedule_work_row` view and `stewardship_occurrence_guard_v1()` differ;
all object counts, other functions, constraints, indexes, triggers and policies
are unchanged. Fingerprints were updated after the comparison. Extended
validation and the next two completed review/fix rounds remain required.

The extended correction run passes 65 tests in 55.37 seconds, including the
strict fingerprint, historical owner scope, actual Admin previews and cloning.
The full baseline passes 5,664 tests in 57.50 seconds, with 3,643 expected skips
and two existing warnings. Ruff, formatting, Markdown and whitespace checks
pass. Round 1's six accepted findings are fixed and its three rejected findings
have the evidence above; Round 1 is complete. Final-head full CI/coverage and
two additional completed review/fix rounds remain required.

## Review round 2

Pika session `20260915-194837-08a854` reviewed the correction delta
`65c4768..456d161`, with surrounding contracts and the Round 1 dispositions.
The exact permission preflight passed. Claude reviewed all ten manifest files;
Codex finished in 361 seconds and explicitly reported no actionable findings.
Finalization has no failed/degraded source or verdict mismatch. Claude reported
four validated Medium findings and six Low findings, with no High/Critical.

| Finding | Disposition |
| --- | --- |
| Successful-delivery negative control | Accepted. A delivered message, succeeded occurrence and delivered semantic coverage permit replacement; the contradictory-pair tests still block it. |
| Earlier draft start behind digest cursor | Accepted. Cursor invalidation now includes the immutable active campaign-configuration identity. A real date-only edit introduces earlier daily slots without changing the cadence revision or restarting the producer. |
| Irreversible contradictory terminal states | Partially accepted. Occurrence outcome writes now reject an already-terminal contradictory delivery result. The adversarial persisted-corruption test first verifies that denial, then uses explicit disposable schema-owner fixture writes to verify replacement still fails closed. Automatic rewriting of conflicting terminal history is deliberately rejected; see the corruption boundary below. |
| Skipped/coalesced initial before reminders | Accepted. All non-delivered terminal initial states hold reminder selection unless successful initial coverage is supplied. Parameterized pure tests retain inapplicable-work skips without reviving the initial. |

The [normative replacement contract](../specs/stewardship/background-processing/spec.md#schedule-replacement-and-removal)
controls corrupted-history handling. BG-06/BG-07 retain responsibility for
coordinated completion/recovery before enabling dispatch. This increment adds
no repair backdoor or development-database upgrade path.

Round 1's corrected five planning modules passed 105 cases with 97.00% line and
90.32% branch coverage. Its rebuilt development and Production runtime scenarios
passed in 126.86 seconds. Eighteen existing schedule-page browser cases passed
across Chromium, Firefox and WebKit. Their fixture was then extended to use the
actual resolved preview, with six additional cases verifying the parish's civil
time remains visible while its UTC instant displays in the browser timezone.
All 24 cases passed in 28.39 seconds. These browser-only additions are included
in the next review, not claimed as part of the Round 2 reviewed SHA.

The Round 2 planner/recovery changes pass 71 tests in 28.41 seconds; the full
baseline passes 5,666 tests in 57.36 seconds, with 3,651 expected profile skips
and two existing warnings. The corruption fixture initially called a
transaction-owning service inside its raw fixture transaction; it now injects
only the corrupt row with explicit SQL, after separately asserting the actual
service is denied. The extended regression run remains pending.

Fresh databases `stewardship_schedule_review2_base_20260915` and
`stewardship_schedule_review2_current_20260915` independently compare `456d161`
against this correction. Only `stewardship_occurrence_guard_v1()` differs; all
other objects, counts, constraints, triggers, policies and functions are unchanged.
The strict fingerprint was updated after this comparison. The third completed
review/fix round and final-head CI remain required before protected delivery.

The extended suite passes all 148 schedule, campaign-mail, adversarial recovery
and strict-schema checks in 158.32 seconds, including the existing real lease/drain
tests. Ruff, formatting, Markdown and whitespace validation pass. Round 2 is
complete with the dispositions above; no accepted Medium-or-higher finding is
left unresolved. The browser fixture/timezone tests remain explicitly included
in the upcoming third-review delta.

## Review round 3

Pika session `20260915-200635-501234` reviewed `456d161..fb53d32`, including the
browser additions and surrounding scheduling contracts. Claude covered all ten
manifest files; Codex completed in 317 seconds and Claude in 563 seconds. Both
sources passed validation, with no failure, degradation or verdict mismatch.
Raw results are five Medium and seven Low, with no High/Critical; all five
Medium findings validated. The exact permission preflight passed.

| Finding | Disposition |
| --- | --- |
| Clone browser preview still uses raw values | Fixed. Both preview fixtures use the production resolved-slot presenter; the mobile/desktop timezone regression covers both routes on all three engines. |
| Terminal contradiction policy missing from controlling spec | Fixed. Added the rule to the linked replacement contract, replacing this guide's duplicate normative prose with that link. |
| Unsuccessful initial hold is opaque | Partially accepted. Pure and durable results now distinguish `initial_unfulfilled`, `delivery_unresolved` and `scope_held`. Actual contact correction plus a later reminder retains a diagnostic hold. Reject bypassing an unsent initial: BG-06.02 explicitly owns a distinct deliverability-recovery attempt, and dispatch is not enabled here. No terminal occurrence is revived. |
| Contracted digest window leaves old slots pending | Fixed through existing atomic revision replacement, not an ad hoc deletion pass. Date-only digest changes now replace revisions in Python preflight, Admin impact preview and SQL selection. Actual daily/weekly start/end contraction and A-B-A restoration tests retain skipped history and create fresh execution revisions. In-range Family mail remains unchanged. Running digest work blocks date edits through both admission layers. |
| Duplicated/incomplete terminal conflict predicate | Fixed. One immutable state-only SQL function serves both the count view and occurrence-write guard, including pending/retry-wait messages versus succeeded/skipped/coalesced occurrences. No private row-read authority is added. |

The first correction suite had 105 passes and three fixture failures: coalesced
outcomes lacked their mandatory replacement and a worker-fencing negative
control reached the newly stronger message guard first. Corrected fixtures
provide valid replacement identities and safely cancel the message before
testing forged worker authority; neither guard was relaxed. The extended run
passes 140 tests in 60.23 seconds. The full baseline passes 5,666 tests in
55.89 seconds, with 3,671 expected profile skips and two existing warnings.
Twelve resolved-preview browser cases pass in 14.69 seconds. Ruff and format
checks pass. Additional terminal-negative controls and final lint remain pending.

The additional cancelled/coalesced controls and successful-delivery preservation
assertions pass all 35 reconciliation cases in 31.46 seconds. The deliberate
corruption fixture drains its real deferred replacement FK before restoring
triggers; no runtime constraint is disabled. Ruff, format, all tracked Markdown,
and whitespace validation pass. Round 3's accepted findings are corrected,
completing the required three review/fix rounds with no final-round High/Critical
or unresolved accepted Medium. Exact-head CI remains required before merge.
The final application image rebuilt successfully and passed both configured
development and Production operational scenarios in 111.89 seconds. These are
disposable container checks, not deployment to a parish environment.
The final focused coverage run passes 113 checks in 40.16 seconds: 357 of 368
lines (97.01%) and 112 of 124 branches (90.32%) across the five planning modules.

Fresh databases `stewardship_schedule_review3_base_20260915` and
`stewardship_schedule_review3_current_20260915` independently compare `fb53d32`
against the corrections. The sole added function is the pure conflict predicate;
the occurrence guard, schedule-selection function and private work-row view
change. All other objects, constraints, indexes, triggers, policies and ACLs
remain unchanged. The strict fingerprint was updated after this comparison.
No retained database was upgraded or deleted.

### Below-cutoff review observations

These are explicit dispositions, not unrecorded accepted blockers. The review
loop's acceptance threshold remains Medium. The implementing Phase 4 owner owns
the deferred test/documentation refinements below before integrated Gate 3.

- Round 2's redundant predicate is removed by the Round 3 shared function.
  Hold-fingerprint collision is rejected for ordinary planning: retained hold
  identity is append-only outside exceptional cleanup, which independently
  closes planning. Adding `Max(id)` would not prove collision freedom.
- Round 2's proposed digest SQL date bounds are deferred to BG-07's final-weekly,
  explicit post-close resolution and aggregate-slot contract. This increment's
  finite evaluator is tested and grants no dispatch; a simple due-before-end
  rule would reject legitimate completed-day reports and recovery aggregates.
- Round 2's additional owner-cache and committed cross-DST ordering assertions
  are low-priority Gate 3 test refinements. Existing tests establish owner scope
  and exact gap/fold instants; no functional counterexample was demonstrated.
- Round 2 and Round 3's restore-held-initial concern is rejected against the
  explicit spec: an unreviewed/assumed-delivered hold excludes its semantic
  occurrence but does not block a different future reminder. The held row must
  not be consumed or reinterpreted as success.
- Round 3's successful-delivery preservation and cancelled/coalesced contradiction
  controls are accepted as small additional regression tests.
- Round 3's insert-branch placement and cursor-map naming are nonfunctional
  readability refinements deferred to the Phase 4 integration pass. The guard
  does not query messages for valid pending inserts; configuration identity
  remains an intentional defensive cursor input.
- Round 3's suggested session-wide replication-role bypass is rejected. The
  deliberate corruption fixture requires the disposable schema owner and an
  exclusive transactional table lock, so other writers cannot observe disabled
  triggers. Runtime roles never receive bypass authority. Suppressed history is
  intentional corrupt-input injection, not an application mutation path.
- Round 3's missing Low-disposition observation is addressed by this ledger.

## PR validation correction

PR #34's first exact-head run `35040794976` at `b53f1db` completed all jobs.
All browser engines, container scenarios, baseline checks and six PostgreSQL
shards passed. Shards 3 and 4 each exposed one parameter of the same cleanup
fixture: it linked a terminal skipped occurrence to a pending external message.
The stronger occurrence guard correctly rejects that setup before the intended
cleanup external-reference check. The fixture now cancels its external message
through the ordinary outbox journal first, preserving that retained reference
and leaving the cleanup denial assertion unchanged. No production code, guard,
schema or test assertion is weakened. This is a test-fixture correction to the
Round 3 guard, not a newly accepted application defect or a clean CI claim.
The corrected fixture, adjacent cleanup inventory and full schedule
reconciliation suite pass all 56 cases in 51.92 seconds. Ruff, formatting,
Markdown and whitespace checks pass. The fixture correction is folded into
the logical Round 3 commit; the changed head requires fresh CI.

## Protected delivery

Exact head `27422ab90accfef03b90034a3c3bbe1e574e82f2` passed all 24 jobs in
[PR CI run 35041901470](https://github.com/epiphany40223/parishkit/actions/runs/35041901470)
and DCO. The quality aggregator accounted for all 2,893 database cases across
eight shards; overall stewardship coverage is 94.11% of lines and 85.58% of
branches. The normal protected queue accepted that exact head without bypasses.

All 24 jobs in
[merge-group run 35042954268](https://github.com/epiphany40223/parishkit/actions/runs/35042954268)
passed, including the complete three-engine browser matrix. PR #34 merged as
`db8aee09ec781658984f8a08ef727c52c96a7de2` on September 16, 2026 UTC, verified
on refreshed `origin/main`. Earlier pending-CI checkpoints above are superseded.
BG-04.01/.03 are accepted; bounded activation/digest recovery continues in the
[next increment](stewardship-activation-catchup.md). This is not Gate 3 release,
deployment, live provider authorization or release-tag approval.
