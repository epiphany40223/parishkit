# Durable campaign delivery journal

[Coordinating tasks](../tasks/stewardship/overall.md#phase-4-production-scheduling-and-delivery) ·
[DAT-07 plan](../plans/stewardship/data.md#dat-07-follow-up-content-templates-jobs-and-audit) ·
[Data contract](../specs/stewardship/data/spec.md#job-outbox-audit-and-purge-records) ·
[Background contract](../specs/stewardship/background-processing/spec.md#durable-scheduling-and-task-execution)

## Scope

Branch `pr/stewardship-delivery-journal` starts from verified PR #30 merge
`6c8cd512e5121095961ffbeb3f80f4dfd844043c`, after
[Gate 2 protected delivery](stewardship-gate-2-reviews.md#protected-delivery).
This begins the controlling plan's Phase 4 DAT-07 prerequisite. Deliver the
outbox and Production-transition records together with guarded service state
changes, durable history, semantic identity, terminal scrubbing and real
PostgreSQL ownership/concurrency evidence. Individual models are internal
checkpoints, not separate PRs.

This increment does not expose ordinary campaign dispatch or Production
activation. BG-02/03/04 retain boundary/cleanup/scheduler workers; BG-06 retains
Family rendering and provider dispatch; BG-07 retains receipt/digest delivery;
ADM-05/06 retain their Admin workflows. Export/publication and Staff follow-up
records remain the later DAT-07 consumers. Preserve those mixed-phase tasks
as incomplete rather than claiming the whole package from its first services.

## Internal checkpoints

1. Implement typed outbox/attempt transitions, immutable scope and routing,
   semantic uniqueness, exact TaskRun/worker binding, uncertain-delivery
   evidence, and separately sealed substitution metadata. Prove terminal
   scrubbing and explicit retry history without automatic unknown resend.
2. Implement the Production-transition journal and its gate/checkpoint state
   contract, with atomic rehearsal invalidation and no restoration of completed
   cleanup. Keep later readiness, inventory execution and activation admission
   with their owning services; do not expose a generic bypass route or command.
3. Maintain the pre-production fresh-install SQL/model-state baseline. Audit
   exact catalog differences before updating strict fingerprints, preserving
   every retained development database and existing runtime permission boundary.
4. Demonstrate idempotent concurrent creation, denied stale/wrong-role changes,
   rollback of late effects, durable uncertain/terminal history and secret
   scrubbing with actual restricted PostgreSQL roles. Add pure transition/input
   tests and run the complete CI-equivalent validation.
5. Complete three dual-source review/fix rounds, record accepted corrections and
   evidence-backed dispositions, then use the normal PR and protected delivery
   cycle. Gate 3 remains after the complete Phase 4 integration.

All new external behavior stays fake-backed or disposable as required by the
master plan. No real-provider write, deployment, release or Gate 5 approval is
authorized by this increment.

## Evidence

Planning and dependency inspection are complete. Implementation, validation and
review are in progress; no DAT-07 checkbox is newly complete.
