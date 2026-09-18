# Background processing tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/background-processing.md) ·
[Normative specification](../../specs/stewardship/background-processing/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

Phase 2 results at `ea2d5cb` below are pre-consolidation evidence, retained on
the named backup branch. Follow the
[consolidation record](../../guides/stewardship-phase-2-simplification.md) for
current baseline/review/CI acceptance; old counts do not certify the current tree.

## BG-01: Durable task, scheduler, lease, and recovery substrate

Scope and dependencies: [BG-01 work package](../../plans/stewardship/background-processing.md#bg-01-durable-task-scheduler-lease-and-recovery-substrate).

- [x] BG-01.01 — Implement task/occurrence transitions, claims, and retry chains.
- [x] BG-01.02 — Configure singleton scheduling, lost-hint recovery, and isolated queues.
- [x] BG-01.03 — Implement transactional campaign-work admission.
- [x] BG-01.04 — Expose authorized task progress and status.
- [x] BG-01.05 — Test all state transitions, admission guards, retries, crashes, and leases.

Evidence: Implemented and accepted in Phase 2 at `ea2d5cb`: durable Task transitions,
UUID-only hint dispatch, singleton scheduling, finite lease renewal, queue/type
isolation, lost-hint recovery, source request bindings, compiled owning admission,
and Admin-only passive task/count/history pages. Actual worker, scheduler and
mail service startup uses isolated mounts, grants and Valkey credentials.
Tests cover state transitions, races, failed/drained work, restricted SQL roles
and real disposable-Valkey transport/controller behavior. The
[acceptance index](../../guides/stewardship-phase-2-acceptance.md) controls current
integrated validation and the required three full-phase review/fix rounds; all
pass. PR CI and human merge approval remain required. Earlier incremental counts
and implementation history are
retained only in the [checkpoint guide](../../guides/stewardship-phase-2.md).

## BG-02: Campaign boundary occurrences

Scope and dependencies: [BG-02 work package](../../plans/stewardship/background-processing.md#bg-02-campaign-boundary-occurrences).

- [x] BG-02.01 — Materialize unique campaign boundary occurrences.
- [x] BG-02.02 — Implement locked start and close transitions.
- [ ] BG-02.03 — Replace future close work and implement shared restore/reopen token preparation.
- [x] BG-02.04 — Recover overdue boundaries in order while enforcing exact access gates.
- [x] BG-02.05 — Test DST, restart, duplicate, and boundary races.

Evidence: [Campaign-boundary increment](../../guides/stewardship-campaign-boundaries.md)
begins from verified PR #31 merge `f4e000c5`. BG-02.01/.02/.04/.05 now deliver
compiled scheduler/worker integration, restricted-role and timing/lag evidence,
DST/concurrent execution, and human-approved repeated-date execution revisions
with immutable replacement history. BG-02.03's end-date replacement is complete;
its shared restore/reopen token worker stays in Phase 6, so that task remains
unchecked. Three completed dual-source review/fix rounds, 102 boundary/schema
regressions, 5,540 baseline tests and operational Compose evidence are recorded
in the linked guide. All 24 final-head PR CI jobs and all 24 merge-group jobs
passed; PR #32 merged as `5c85d26f`, verified on refreshed `origin/main`.
These task completions do not release Gate 3 or enable later mail owners.

## BG-03: Production-transition cleanup worker

Scope and dependencies: [BG-03 work package](../../plans/stewardship/background-processing.md#bg-03-production-transition-cleanup-worker).

- [x] BG-03.01 — Enforce the go-live gate and rehearsal invalidation.
- [x] BG-03.02 — Delete inventoried Testing and rehearsal credential detail in resumable batches.
- [x] BG-03.03 — Verify cleanup completeness before readiness.
- [x] BG-03.04 — Implement safe retry and cancellation semantics.
- [x] BG-03.05 — Test interrupted cleanup and concurrent Testing work.

Evidence: Implemented and locally accepted on `pr/stewardship-production-cleanup`,
based on PR #32 merge `5c85d26f`. The
[increment guide](../../guides/stewardship-production-cleanup.md) records all three
complete dual-source review/fix rounds, 100 passing cleanup tests, 93.57% line
and 81.15% branch coverage, schema/ownership/race/runtime validation and submitted-
Family scale measurements. PR #33 passed all 24 final-head and all 24 merge-group
jobs and merged as `ce1e95d1`, verified on refreshed `origin/main`; see the guide's
[delivery record](../../guides/stewardship-production-cleanup.md#protected-delivery).
This does not release Gate 3 or enable Production
activation; ADM-05 and its other dependencies retain those boundaries.

## BG-04: Schedule revision, fulfillment, and mode routing

Scope and dependencies: [BG-04 work package](../../plans/stewardship/background-processing.md#bg-04-schedule-revision-fulfillment-and-mode-routing).

- [x] BG-04.01 — Evaluate schedules using campaign-local intervals.
- [ ] BG-04.02 — Implement revision and semantic-fulfillment identity.
- [x] BG-04.03 — Implement locked replacement, removal, and cancellation.
- [ ] BG-04.04 — Enforce immutable Testing/Production/operational routing.
- [x] BG-04.05 — Implement missed-work coalescing and bounded asynchronous activation catch-up.
- [ ] BG-04.06 — Test schedule, mode, revision, and restart combinations.

Execution checkpoint: `pr/stewardship-schedule-planning` starts from verified
PR #33 merge `ce1e95d1`. The
[increment scope](../../guides/stewardship-schedule-planning.md) covers campaign-local
evaluation and ordinary schedule planning/reconciliation. Bounded activation
catch-up follows as its own coherent increment. BG-04.01/.03 pass local acceptance
and three dual-source review/fix rounds; the linked guide records corrections,
140 extended PostgreSQL/pure checks, 35 terminal-reconciliation checks, the full
baseline and browser evidence. Final-head PR/merge-group CI remains pending.
Receipt identity, bounded activation/digest recovery and dispatch routing keep
the remaining mixed tasks open. Gate 3 is not released.

Delivery update: PR #34 passed all 24 final-head and all 24 merge-group CI jobs
and merged as `db8aee09`, verified on `origin/main`. The preceding pending-CI
note is superseded. The [activation catch-up increment](../../guides/stewardship-activation-catchup.md)
continues bounded preparation, recovery and hold enforcement; mixed tasks remain
open until their complete scope is accepted.

September 16 local acceptance: BG-04.05 is implemented and passes three completed
dual-source review/fix rounds. See the
[activation acceptance record](../../guides/stewardship-activation-catchup.md#local-acceptance-and-delivery-handoff)
and [review evidence](../../guides/stewardship-activation-catchup-reviews.md).
The full reviewed-tree run passed 2,950 PostgreSQL tests; final corrections passed
147 database regressions, 5,674 baseline tests and both configured Compose checks.
Exact-head PR and protected merge-group CI remain pending. BG-04.02/.04/.06 stay
open for their receipt/dispatch and integrated mode/consumer scope; completing
preparation is not completion of BG-06/BG-07 or release of Gate 3.

## BG-05: ParishSoft delta and full refresh

Scope and dependencies: [BG-05 work package](../../plans/stewardship/background-processing.md#bg-05-parishsoft-delta-and-full-refresh).

- [x] BG-05.01 — Implement supported change-feed and watermark adapters.
- [x] BG-05.02 — Implement delta indications and affected-Family reloads.
- [x] BG-05.03 — Implement scheduled and manual complete source refreshes.
- [x] BG-05.04 — Fence source mutations and validate promotion inputs.
- [x] BG-05.05 — Coalesce manual refresh and exclude concurrent publication.
- [x] BG-05.06 — Test invalid corpora, retries, takeover, and reconciliation.

Evidence: Implemented and accepted in Phase 2 at `ea2d5cb`: coherent bounded full and
Family-delta reads, exact source/Task/credential bindings, complete-corpus
validation, atomic promotion and Family/chair reconciliation, immutable
full-fallback dependencies, scheduled/manual request coalescing, and drained
recovery/cleanup. The compiled isolated runtime binds actual key inventories,
provider mounts and restricted database grants. Admin-managed Ministry activity
survives subsequent source refreshes. Shared client, pure normalization/cadence,
PostgreSQL ownership/race and operational Compose tests cover these paths.

The Admin-editable nightly time now has a versioned schema and integration
editor, with passing pure validation, Admin-to-scheduler, projection, credential
replay and offline-recovery regressions. Full integrated acceptance and the
required three complete-phase review/fix rounds pass. See the
[acceptance index](../../guides/stewardship-phase-2-acceptance.md) for current
validation and the [checkpoint guide](../../guides/stewardship-phase-2.md) for
chronological history. PR CI and human merge approval remain required.

## BG-06: Family invitations and reminders

Scope and dependencies: [BG-06 work package](../../plans/stewardship/background-processing.md#bg-06-family-invitations-and-reminders).

- [ ] BG-06.01 — Materialize eligible Family mail slots.
- [ ] BG-06.02 — Implement deliverability-recovery invitations.
- [ ] BG-06.03 — Render personalized templates with mode/epoch-scoped credentials.
- [ ] BG-06.04 — Seal substitutions for isolated mail dispatch.
- [ ] BG-06.05 — Implement provider outcomes, reconciliation, and scrubbing.
- [ ] BG-06.06 — Implement pause holds, close cancellation, and resume.
- [ ] BG-06.07 — Test recipients, suppression, routing, races, and failures.

Family selection replacement during activation retains immutable recovery edges
for its already-coalesced reminder coverage. The delivery owner must preserve
that lineage and never interpret a cancelled predecessor as successful delivery.
Preparation may select remaining reminders while the initial slot is independently
restore-held. BG-06's delivery-time missed-work recheck must not treat an
unreviewed initial hold as delivered or send that reminder ahead of required
initial recovery. This dispatch prerequisite must pass before ADM-05 activation
is enabled; preparation completion itself grants no provider permission.

Evidence: In progress on `pr/stewardship-family-deliverability`, beginning at
verified PR #36 merge. The [increment guide](../../guides/stewardship-family-deliverability.md)
records scope, internal checkpoints and validation. Recipient projection,
source/refusal integration and scheduler/activation recovery pass complete and
focused validation; three dual-source review/fix rounds are recorded there.
Protected PR delivery is pending; the full BG-06 checkboxes remain open.
No provider dispatch or Production activation is enabled by this preparation.
BG-06.05 also owns the [partial-refusal dispatch prerequisite](../../guides/stewardship-family-deliverability.md#partial-refusal-dispatch-prerequisite):
remaining usable head addresses must continue through definitive-unaccepted
outbox retry ownership, not a fabricated Family deliverability edge.

PR #37 has since merged after all 24 exact-head and all 24 merge-group jobs
passed. The pending-delivery note above is superseded by its guide's
[protected delivery evidence](../../guides/stewardship-family-deliverability.md#protected-delivery).
Work now continues from that verified merge on
`pr/stewardship-family-mail-preparation`; its [guide](../../guides/stewardship-family-mail-preparation.md)
defines the personalized outbox/credential outcome and remaining dispatch split.

That preparation increment now has three completed dual-source review/fix
rounds and passing full local validation: 3,075 PostgreSQL tests, 94.09% line/
85.38% branch coverage, 5,827 baseline tests, 41 Compose/isolation checks and
both full disposable setup layouts. See its guide's
[final handoff](../../guides/stewardship-family-mail-preparation.md#final-local-validation-and-handoff).
Protected exact-head CI and queue delivery remain required. BG-06.03/04's
preparation behavior is implemented; dispatch-time integration and the full
package checkboxes remain open, not silently completed by a prepared outbox.

PR #38 subsequently merged with all 24 exact-head jobs, DCO and all 24
merge-group jobs passing; its [protected delivery receipt](../../guides/stewardship-family-mail-preparation.md#protected-delivery)
supersedes the pending-CI note. Work continues from verified fresh `origin/main`
on `pr/stewardship-family-mail-dispatch`, connecting the prepared message to
isolated provider submission and durable outcome/reconciliation ownership.
The [dispatch increment guide](../../guides/stewardship-family-mail-dispatch.md)
records its testable provider boundary and the subsequent Admin-resolution UI
increment. Both precede BG-07 and full BG-06 completion.

The dispatch boundary now has three completed dual-source review/fix rounds,
including every raw finding's disposition in the guide. Final local corrections
pass 125 provider/private/circuit tests, 90 affected PostgreSQL cases and 5,952
baseline tests. The guide distinguishes earlier passing full coverage from a
later contended performance-test diagnostic and its passing isolated rerun.
Protected final-head/merge-group CI remains required. Current-source rendering,
private submission, partial refusals, uncertainty, retry/circuit control and
pause/close/crash drainage are implemented; explicit Admin resolution/retry,
verified refusal clearance and durable notification remain with the next
increment. The package checkboxes and Gate 3 remain open.

PR #39 subsequently passed all 24 exact-head jobs, DCO and all 24 protected
merge-group jobs, then merged as `9e5b6e99`, verified on fresh `origin/main`.
Its [protected delivery receipt](../../guides/stewardship-family-mail-dispatch.md#protected-delivery)
supersedes the pending-CI note. The next
[Admin-resolution increment](../../guides/stewardship-family-mail-resolution.md)
is in progress on `pr/stewardship-family-mail-resolution`.

The increment now implements Admin evidence, acceptance/retry, verified refusal
clearance and persistent uncertainty warnings, with three independent review
results and every raw finding dispositioned in its guide. Full local validation
at `7ada2ce` passed 5,994 baseline and 3,217 PostgreSQL tests with 93.97% line /
85.19% branch coverage; later corrections have focused regression evidence.
Protected final-head/queue CI and verified main delivery remain pending. BG-10
retains operational email/Slack escalation; the full package and Gate 3 remain
open, and the next dependency-ready increment is RPT-02/RPT-03's Phase 4 fact
calculation/materialization prerequisite before BG-07.

## BG-07: Submission confirmations and Admin digests

Scope and dependencies: [BG-07 work package](../../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests).

- [x] BG-07.01 — Create idempotent submission confirmations.
- [x] BG-07.02 — Build daily digests against exact immutable facts.
- [x] BG-07.03 — Build weekly information and correction digests.
- [ ] BG-07.04 — Integrate post-close obligation inventory and explicit resolutions.
- [ ] BG-07.05 — Test digest coverage, parity, recovery, and repeat safety.

Activation preparation retains original date coverage across cancelled aggregate
revisions through [immutable recovery lineage](../../specs/stewardship/data/spec.md#schedule-revisions-and-fulfillment).
BG-07 fact pinning and post-close resolution must traverse that lineage, not
only the current aggregate's directly attached fulfillment rows. The bounded
`campaigns.recovery_coverage.covered_dates()` reader supplies exact date identities,
not report facts or evidence of delivery.

Evidence: BG-07.01 is implemented on `pr/stewardship-submission-receipts`, from
PR #45's verified merge `3bfec17a`. The
[increment guide](../../guides/stewardship-submission-receipts.md) defines the
coherent receipt delivery scope and records three successful dual-source
review/fix rounds, full parallel coverage and passing correction regressions.
Exact-head CI/DCO and protected delivery remain pending. Daily and weekly
digests, complete post-close inventory and Gate 3 remain open.

BG-07.04 and the Phase 6 archive owner must replace the interim receipt archive
guard with the complete shared obligation inventory and explicit semantic skip
resolution. Until then, an undeliverable accepted Production receipt blocks
archive and therefore the next campaign, even after retries are exhausted.
This is a visible pre-production limitation, not a supported operational escape
path; do not enable production use or waive unresolved obligations at a gate.

PR #46 has now landed as `849cc71f` after exact-head CI/DCO and all protected
merge-group checks passed; its
[delivery receipt](../../guides/stewardship-submission-receipts.md#protected-delivery)
supersedes the pending note. BG-07.02 is in progress on
`pr/stewardship-daily-digests` from that verified fresh `origin/main` tip; its
[increment guide](../../guides/stewardship-daily-digests.md) records the coherent
daily report-to-delivery scope and internal checkpoints.

BG-07.02 and its daily-specific tests now pass five successful dual-source
review/fix rounds and [final local acceptance](../../guides/stewardship-daily-digests.md#final-local-acceptance):
3,582 PostgreSQL cases, 93.97% line/85.07% branch coverage, the full baseline,
three browser engines, fresh schema and container isolation. Exact-head CI/DCO
and protected delivery remain pending. Weekly digests, complete post-close
resolution, the full BG-07.05 matrix and Gate 3 remain open.

PR #47 landed as `e548809c` after all 24 exact-head CI jobs plus DCO and all
24 merge-group jobs passed. Its
[delivery receipt](../../guides/stewardship-daily-digests.md#protected-delivery)
supersedes the pending note. BG-07.03 now starts on
`pr/stewardship-weekly-digests` from that verified fresh `origin/main` tip;
the [weekly increment guide](../../guides/stewardship-weekly-digests.md) records
its scope and internal acceptance checkpoints.

The weekly renderer/isolated transport, coherent input selection and
[durable capture/schedule-coverage checkpoints](../../guides/stewardship-weekly-digests.md#durable-capture-and-schedule-coverage-checkpoint)
now have passing focused PostgreSQL, baseline and independently audited fresh
schema evidence. The [per-Admin allocation/replacement checkpoint](../../guides/stewardship-weekly-digests.md#per-admin-allocation-and-replacement-coverage-checkpoint)
now has focused regression evidence, as does the
[interval-completion/maintained-worker checkpoint](../../guides/stewardship-weekly-digests.md#interval-completion-and-maintained-preparation-checkpoint).
The [provider-dispatch/Admin-recovery checkpoint](../../guides/stewardship-weekly-digests.md#provider-dispatch-and-admin-recovery-checkpoint)
now has maintained-worker, recovery, browser-command and baseline evidence.
The [Testing-retention checkpoint](../../guides/stewardship-weekly-digests.md#testing-retention-cleanup-checkpoint)
also has bounded cleanup and independent schema evidence.
The [protected-report checkpoint](../../guides/stewardship-weekly-digests.md#protected-report-and-detail-checkpoint)
has exact-role, response-barrier, baseline and three-engine accessibility evidence.
The [manual-report/runtime checkpoint](../../guides/stewardship-weekly-digests.md#manual-reporting-and-runtime-integration-checkpoint)
has focused integration, security, fresh-schema and browser evidence. Four
successful dual-source review/fix rounds are now complete, with no High issues
in the final round; the [handoff ledger](../../guides/stewardship-weekly-digests.md#review-round-four-and-handoff)
records dispositions and correction tests. BG-07.03 is complete for the weekly
implementation. Exact-head CI/DCO and protected delivery remain pending;
BG-07.04/.05 and Gate 3 remain open.

## BG-08: Export and graph workers

Scope and dependencies: [BG-08 work package](../../plans/stewardship/background-processing.md#bg-08-export-and-graph-workers).

- [ ] BG-08.01 — Implement requester-scoped export jobs and pinned inputs.
- [ ] BG-08.02 — Recheck authorization throughout export and download.
- [ ] BG-08.03 — Write owner-only atomic exports with expiration.
- [ ] BG-08.04 — Implement shared deterministic chart rendering.
- [ ] BG-08.05 — Test large exports, revocation, cancellation, and purge races.

Evidence: In progress on `pr/stewardship-export-foundation` after verified PR #35
delivery. The [increment record](../../guides/stewardship-export-foundation.md)
defines the Phase 4 substrate and preserves the Phase 5 completion boundary.
The compiled participation export and Admin cleanup-recovery slice passed three
dual-source review/fix rounds and full local validation. PR #36 passed exact-head
and merge-group CI and landed as `7d9a3b0`; see its
[protected-delivery evidence](../../guides/stewardship-export-foundation.md#protected-delivery).
The unchecked items retain their later full-catalog/UI integration
scope rather than claiming this substrate completes the entire work package.

## BG-09: ParishSoft publication worker

Scope and dependencies: [BG-09 work package](../../plans/stewardship/background-processing.md#bg-09-parishsoft-publication-worker).

- [ ] BG-09.01 — Claim source lease for confirmed publication plans.
- [ ] BG-09.02 — Recheck source payload, conflicts, and fencing before PUT.
- [ ] BG-09.03 — Implement grouped writes, bounded retries, and verification.
- [ ] BG-09.04 — Record partial outcomes and request final source refresh.
- [ ] BG-09.05 — Test external races, ambiguous writes, and partial recovery.

Evidence: Not started.

## BG-10: Critical notification and service shutdown

Scope and dependencies: [BG-10 work package](../../plans/stewardship/background-processing.md#bg-10-critical-notification-and-service-shutdown).

- [ ] BG-10.01 — Create durable deduplicated critical events.
- [ ] BG-10.02 — Dispatch operational Admin and optional Slack alerts.
- [ ] BG-10.03 — Implement escalation, suppression, and recovery notices.
- [ ] BG-10.04 — Implement graceful worker and scheduler shutdown.
- [ ] BG-10.05 — Test notification failures and interrupted shutdown.

Evidence: In progress on `pr/stewardship-operational-alerts` from verified
PR #49 merge `70797cb2`. The [increment checkpoints](../../guides/stewardship-operational-alerts.md)
record typed content/policy, durable episode/notice ownership, configured windows
and independently audited fresh schema, followed by critical-log/auth intake,
current-Admin fanout and operational SMTP dispatch. The guide records focused
validation per checkpoint. This PR ends at the coherent incident-to-email path;
Slack outcome ownership, remaining health producers and shutdown acceptance
follow in the next increment before ADM-05/Gate 3. Three successful dual-source
review/fix rounds are complete, with no HIGH or MEDIUM findings in the final
round; the guide records every disposition. Final consolidated-head CI/DCO and
protected merge remain pending. No full notification or shutdown task is
complete yet.

PR #50 subsequently merged after all required reviews and exact-head checks;
the [protected receipt](../../guides/stewardship-operational-alerts.md#protected-delivery)
supersedes its pending delivery status. The
[remaining health/notification increment](../../guides/stewardship-operational-health.md)
owns the unfinished BG-10 scope from verified merge `1895949`.
PR #51 is the independent Slack/shutdown and admission-hold slice, with a
separate fixture-efficiency checkpoint. Remaining health producers/recovery
follow in the next fresh-main increment before BG-10 acceptance or ADM-05.

## BG-11: Exceptional purge worker

Scope and dependencies: [BG-11 work package](../../plans/stewardship/background-processing.md#bg-11-exceptional-purge-worker).

- [ ] BG-11.01 — Recheck purge prerequisites and drain readers before the first deletion batch.
- [ ] BG-11.02 — Delete campaign data in stable checkpointed batches.
- [ ] BG-11.03 — Enforce rollback limits and resume after deletion starts.
- [ ] BG-11.04 — Complete file cleanup, tombstone, and credential invalidation.
- [ ] BG-11.05 — Escalate purge inconsistency and cleanup failures.

Evidence: Not started.
