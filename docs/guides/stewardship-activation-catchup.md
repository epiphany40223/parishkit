# Stewardship activation catch-up and recovery preparation

Branch `pr/stewardship-activation-catchup` starts from verified PR #34 merge
`db8aee09ec781658984f8a08ef727c52c96a7de2` on September 16, 2026 UTC. The
[preceding delivery record](stewardship-schedule-planning.md#protected-delivery)
contains its complete exact-head and merge-group evidence.

## Scope and controlling contracts

Continue [BG-04](../tasks/stewardship/background-processing.md#bg-04-schedule-revision-fulfillment-and-mode-routing)
under the [Phase 4 plan](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications).
Deliver one coherent bounded worker increment: activation demand/task allocation,
resumable Family/digest preparation, exact semantic coverage, interrupted-task
recovery, preparation-hold enforcement and actual runtime integration. Use the
[activation contract](../specs/stewardship/background-processing/spec.md#activation-catch-up)
and [schedule data model](../specs/stewardship/data/spec.md#schedule-revisions-and-fulfillment),
not a second delivery or idempotency scheme.

The ordinary evaluator and pure missed-work decisions already exist. Reuse them
with the existing TaskRun lease/retry chain and durable activation checkpoints.
Each effect is bounded and transactional; no provider call or corpus-wide lock
belongs in a preparation batch. A partial group is never dispatch permission.
Read the linked contracts for exact live-state, revision, restore and close
rechecks and for the difference between coverage and provider delivery.

BG-06 retains recipient rendering, deliverability-recovery initial invitations,
bound-message recovery and provider submission. BG-07 retains pinned daily report
facts, weekly item/correction coverage and digest dispatch. ADM-05 retains the
Admin Production activation UI. This increment must not invent a way around
these owners, release Gate 3 or imply a completed end-to-end mail campaign.

## Implementation and acceptance checkpoints

1. Bind activation's durable demand and canonical task atomically. Add compiled
   worker/scheduler admission with real role restrictions and recovery behavior.
2. Persist bounded recovery preparation and complete-group coverage using the
   existing occurrence and semantic fulfillment contracts. Retain every original
   outcome and prove that revision changes cannot orphan a selected aggregate.
3. Integrate the shared planner for complete Family groups and multi-page digest
   groups. Persist checkpoints with outcomes and recheck live eligibility,
   submissions, current revisions, semantic coverage and holds.
4. Verify completion from retained work/coverage, not TaskRun terminality or an
   empty occurrence table. Release only the activation preparation hold; restart,
   close, restore, pause and explicit retry keep their independent semantics.
5. Add pure, actual PostgreSQL-role, concurrency, failure-injection and runtime
   tests. Cover more than one batch, crash boundaries, hint loss, stale ownership,
   schedule replacement/removal, source/response changes and post-close behavior.
6. Audit fresh-install schema differences in new disposable databases, preserve
   retained development data, run complete validation and coverage, and complete
   three successful dual-source review/fix rounds before PR delivery.

These are internal checkpoints, not separate PRs or requests for routine human
approval. All implementation and acceptance work is open at branch creation.

## Allocation checkpoint

Direct activation now allocates its canonical TaskRun and binds the token
generation's source in the same transaction as the lifecycle change. A deferred
database constraint rejects activation without this final binding. Pre-start
activation creates neither demand nor task. Allocation failure rolls back the
mode, lifecycle, demand and task together; exact replay keeps the original cutoff.

Actual web-role execution exposed missing column-level lifecycle grants for
locking/activating the prepared token generation and releasing the go-live gate.
Those narrowly scoped grants retain the existing transition guards; web cannot
prepare token material. Scheduler recovery reads and compiled worker ownership
are being implemented separately from executable preparation integration.

September 16 UTC checkpoint validation: 86 PostgreSQL tests passed in 49.18s,
covering allocation, restricted-role ownership/recovery, baseline integrity,
boundary/catch-up history, lifecycle corrections and adjacent digest planning.
The fresh-install audit compared separate disposable installs against PR #34's
baseline: exactly one allocation-check function, one deferred constraint and its
trigger were added. Other schema inventories were unchanged; the strict baseline
fingerprints were updated only after inspecting that audit. No retained database
was altered or deleted. This is not full worker or increment acceptance.

## Bounded worker checkpoint

The compiled general-worker handler now prepares the activation-time Family
cohort using stable UUID keysets and each Family's current eligibility/response.
It reuses ordinary Family selection with the original activation cutoff. Later
Family additions remain the source/deliverability owner's work. Configuration
changes restart traversal in a new immutable receipt namespace rather than
rewinding checkpoints or reviving removed revisions.

Digest preparation first materializes all due dates in pages of at most 100,
then coalesces originals in separate bounded coverage transactions. The hold
remains throughout both stages. Selected aggregates use ordinary occurrence
identities; no outbox, provider call or scheduled-delivery hint is created.
Cancelled aggregate predecessors retain their fulfillment rows and gain
append-only recovery edges, so the exact original date inventory remains
available across further configuration replacements. See the linked data
contract and BG-07's explicit lineage consumer requirement.

Each batch renews and checks the exact TaskRun claim, atomically commits its
outcomes and checkpoint, and reports a preparation phase without inventing a
known total. Known transient local failures record only a closed diagnostic
code, retain the hold/cursor, and use bounded retry. Unexpected permission,
constraint and ownership failures are not translated into success. Automatic
abandonment recovery and explicit failed-task retry retain the original demand
and task root. Runtime assembly includes metadata-only scheduler admission and
the executable worker handler; the mail dispatcher gains no new authority.

Focused validation now includes a 109-date, multiple-page digest, rollback both
before and after Family outcomes, real worker/scheduler roles, rejected fabricated
completion, stale fences, current source changes, terminal-task retry, restore,
close and partial-coverage revision replacement. The completed close/restore and
pure-planner checkpoint passed 64 tests in 18.26s. Subsequent focused additions,
full database regression, runtime validation, coverage and review remain pending;
do not treat this checkpoint as final acceptance.

The worker schema audit adds one seven-column immutable recovery-edge table,
its keys/indexes/guards, and three narrow preparation/lineage functions. It
changes only the checkpoint, occurrence-creation and existing worker-write
admission functions; policies and unrelated objects are unchanged. Separate
fresh installs at the committed allocation checkpoint and current working tree
were compared before updating fingerprints. The model-to-database contract test
passes. No historical upgrade or retained-database mutation was introduced.
