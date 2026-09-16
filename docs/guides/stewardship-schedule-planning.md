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

The corrupted-history boundary is intentional: immutable accepted-provider and
occurrence evidence must not be guessed away by a schedule edit. A discovered
contradiction requires operator investigation and separately authorized repair,
not an automatic retry, recall, success claim or bypass. The new guard prevents
an occurrence writer from contradicting an already-terminal provider result;
it does not suppress a later truthful provider journal entry. BG-06/BG-07 retain
responsibility for coordinated completion/recovery before enabling dispatch.
This increment adds no repair backdoor or development-database upgrade path.

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
