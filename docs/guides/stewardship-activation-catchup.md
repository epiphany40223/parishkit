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
