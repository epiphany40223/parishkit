# Production-transition cleanup implementation checkpoint

[Tasks](../tasks/stewardship/background-processing.md#bg-03-production-transition-cleanup-worker) ·
[Work package](../plans/stewardship/background-processing.md#bg-03-production-transition-cleanup-worker) ·
[Cleanup contract](../specs/stewardship/background-processing/spec.md#production-transition-cleanup) ·
[Controlling delivery plan](../plans/stewardship/overall.md#automated-phase-delivery-cycle)

## Scope and starting point

Branch `pr/stewardship-production-cleanup` starts from verified PR #32 merge
`5c85d26ff586cad5539ff2c324c89cd621cbcf9d` on refreshed `origin/main`.

Deliver BG-03's five items as one independently testable general-worker
increment: exact Testing inventory and non-sensitive aggregate, atomic gate and
epoch invalidation, bounded checkpointed deletion, completion verification,
retry/recovery and safe cancellation. The existing Production journal is a
foundation, not evidence that these operational owners already exist.

Keep the later ADM-05 readiness/activation web workflow, BG-04 schedule owners
and BG-06 dispatch owners disabled. Cleanup never changes mode or lifecycle.
Normal validation uses synthetic data, disposable databases and fake providers;
no retained database deletion, real provider writes, deployment or release is
authorized by this increment. Gate 3 remains after complete Phase 4/5 integration.

## Validation checkpoints

- Exact inventory/category ownership and aggregate privacy, including mixed
  Testing/live/operational and other-campaign preservation.
- Bounded transactions, checkpoint/deletion atomicity, interruption/replay,
  superseded claims, cancellation and verified completion.
- Rehearsal invalidation and denial of concurrent Testing work; stable Production
  codes and anonymous rehearsal code reservations remain intact.
- Actual restricted runtime roles, SQL denial tests, worker configuration
  authority and fresh-install schema/catalog validation.
- Credential-free baseline, Ruff, formatting, Markdown and relevant Compose
  checks, followed by the required three dual-source review/fix rounds.
- Exact-head PR CI and all merge-group checks, then protected delivery and
  verification on refreshed `origin/main` before the next increment.

Implementation is in progress. No BG-03 task or review gate is claimed complete.
