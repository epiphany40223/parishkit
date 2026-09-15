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

BG-03 must supply a narrowly scoped deletion port for terminal
`testing_override` records, their dependent renders/events and linked task
history. The current immutable/delete guards deliberately provide no such port.
The checkpoint service supplies atomic progress, not permission to delete those
records. BG-03 must implement and test both together before cleanup is exposed.

Semantic creation binds the original logical initiator, not a transient worker
process. Recovery producers pass that same initiator and fresh admission; a
different actor cannot silently reuse the original command. Resealing the same
credential is permitted on replay, but the first committed envelope is retained.
Changed credential identity or render requires an explicit new preparation.
This includes resealing after active-key rotation: key identity and randomized
ciphertext do not define the semantic command. Replay must retain the original
command ID, expected version and proof/options, not substitute a freshly loaded
version. Dispatch must load the retained envelope, not assume its replay input
replaced it.

Explicit failed-message or cleanup retry starts with `work_transaction()` before
allocating its linked TaskRun retry, then advances the domain journal within
that same transaction. A plain `transaction.atomic()` is insufficient. Delivery
transition admission receives a fourth, frozen `DeliveryCommand` argument with
the exact proposed evidence/options and non-secret render/credential metadata;
the proposed submit task is locked before admission, including replay. Other
outbox admission callbacks retain their three-argument contract.

Cleanup crash recovery may mirror a fenced terminal TaskRun `recovery_fail`
without a live worker. It must bind the latest failed run, recovery actor and
fence, retain completed checkpoints and continue holding the go-live gate.
Checkpoint batches have a unique per-request digest as well as a command ID;
retry a lost response with its original command. A deletion callback returns
success only when actual per-category deletion counts equal the proposed batch,
never merely because an already-deleted batch is harmless to repeat.

Production submission independently checks current mode/campaign, restore/go-live
gates and campaign pause state. Holds bind the actual paused version and release
only after a later resume. BG-06 still owns full date/purpose, recipient, live
token-generation and catch-up admission; operational notifications may retain
campaign attribution while their deduplication scope remains the parish.

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

The internal outbox and Production-cleanup journals are implemented. Validation
and peer review are in progress; no DAT-07 checkbox is newly complete.
The checkpoint results below describe the initial implementation, not final-head
acceptance. Follow-up evidence and dispositions are recorded in the
[delivery review ledger](stewardship-delivery-reviews.md).

- Pure transition/input suites: 198 tests passed across delivery states,
  Production states, redacted render inputs and cleanup summaries.
- Full credential-free baseline: 5,498 passed in 49.99 seconds; database and
  explicitly opted-in container tests remain separate. Two existing Valkey
  client deprecation warnings are unchanged.
- Broader PostgreSQL regression: 94 passed in 31.57 seconds with no warnings,
  covering both journals, complete strict schema/model checks and shared storage
  guards. Includes retained-key rejection and exact attempt-replay parameters.
- Outbox PostgreSQL checkpoint: 27 tests passed, including schema/model and
  all-model immutability guards, with no warnings.
- Production PostgreSQL checkpoint: 13 tests passed, including schema/model
  equivalence. A pytest collection warning from an imported model name was
  corrected afterward; the full rerun remains required.
- Actual runtime grant checks deny generic outbox writes to web, worker,
  scheduler and mail-dispatch. Operational delivery and activation entry points
  remain unavailable rather than accepting a caller-supplied permission flag.
- Pre-cleanup counts, gated request state, batch progress and immutable history
  are durable. A synthetic deletion-owner test proves late denial/count/fence
  failures roll back both deleted rows and their checkpoint. Final activation
  is explicitly rejected pending its complete ADM-05 owner.

### Fresh-install schema audit

An independently created reference database was installed from merged
`6c8cd512e5121095961ffbeb3f80f4dfd844043c`. Its full strict fingerprint matches
that commit before comparison with the candidate. Every preexisting relation,
column, constraint, index, function, trigger and policy is unchanged; none is
removed. The additions are eight journal tables, 152 columns, 225 constraints,
52 indexes, 20 functions and 20 triggers. The only new guard attached to an
existing table is the deferred go-live-gate pin on campaign credentials.
All 28 existing row policies are unchanged.

The corrected candidate totals are 137 relations, 1,635 columns, 2,350 constraints,
719 indexes, 334 functions, 332 triggers and 28 policies. Current audit evidence
and the function fingerprint are in the
[review ledger](stewardship-delivery-reviews.md#successful-replacement-review).
The all-model comparison independently confirms current field types, defaults,
nullability, foreign keys and complete constraint/index definitions. The strict
fixture was updated only after inspecting the additive catalog delta.

All SQL remains one atomic fresh installation. Production model state joins the
existing campaigns initial migration. One additional jobs **initial state-only**
step resolves the outbox's cross-app references after campaign/configuration
models exist; putting those references in the earlier jobs step would create a
dependency cycle. There is no retained-database upgrade/downgrade or deletion.
Two explicitly allowlisted SQL files keep the new workflow guards readable
without broadening Docker's default-deny build context.
