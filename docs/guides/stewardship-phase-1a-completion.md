# Stewardship Phase 1A completion

Branch `pr/stewardship-phase-1a-completion` starts at PR #18's merge,
`9f644b0e1fdef1fb09e009bc1576f979c096f643`. This delivery boundary is the
complete remaining [Phase 1A scope](../plans/stewardship/overall.md#1a-base-data-and-lifecycle),
not another individual storage increment. Gate 1 remains after Phases 1B/1C.

## Execution checkpoints

1. Complete DAT-02 runtime structure, transition/boundary/catch-up records and
   atomic configuration integration, retaining later readiness/token/work owners.
2. Complete schedule definitions, occurrence transitions, semantic fulfillment,
   replacement/removal, restore holds and post-close resolution storage.
3. Implement pinned campaign read guards and deployment-wide bounded download
   admission, including lifetime, connection-loss and exclusive-drain tests.
4. Integrate DOM-02 persistent policy/race tests and Phase 1A DOM-03/DAT-05
   authorization foundations; begin shared database-backed DOM-05 factories.
5. Reconcile all owning checklists with the controlling phase assignments,
   validate host/PostgreSQL/image/Compose behavior, complete at least three
   full-branch review/fix rounds, open one PR and resolve CI failures.

Implementation and validation are in progress. No review gate is claimed.
No public lifecycle endpoint, provider action, credential operation or Production
startup is authorized by internal storage primitives. Concrete owning services
must supply current authorization, readiness and external-effect evidence before
exposure; their Phase 1B/1C/2+ assignments are not replaced by synthetic fixtures.

## Implemented foundations

- Campaign and global runtime ledgers have separate optimistic versions from
  YAML activation sequence numbers. Configuration, lifecycle and purge
  preparation share installer/global/runtime/campaign lock ordering.
- Activation, withdrawal, ordered start/close, archive/unarchive and Return to
  Testing retain immutable evidence. A successor retains historical campaigns;
  a global preparing/running purge gate blocks its creation.
- Exceptional end edits bind one immutable configuration request to exact
  runtime inputs. Reopen atomically selects its new projection and already
  prepared token-generation reference. Its
  [unapplied-candidate cancellation](../specs/stewardship/data/spec.md#parish-and-integrations)
  recovers interruption without rewinding applied history.
- Pause/resume and first-live-effect records are orthogonal to lifecycle/mode.
  Catch-up input bindings, fenced group checkpoints and explicit completion
  preserve a hold independently of TaskRun completion.
- Logical schedule selection survives unrelated configuration changes.
  Occurrence history, explicit retry identity, semantic fulfillment, restore
  decisions and exact-version post-close skip records have SQL guards.
- Read/download guards pin one PostgreSQL session and read transaction through
  lazy response consumption. Dedicated zero-idle download pools share a database
  capacity cap; an exclusive-drain primitive uses the same stable lock keys.

## Integration contracts for subsequent phases

Owning callbacks must raise on failed authorization, readiness or external-effect
proof. They execute while the relevant records are locked, must not call external
providers, and are required again on idempotent replay. UUID attribution, a token
generation reference, an occurrence state or a stored intent is not permission.

- Phase 1B supplies actual Google/Family sessions, token-generation records and
  service boundaries. No Family credential is generated or usable here.
- Phase 1C connects read limits/capacity to whole-deployment connection budgeting,
  least-privilege database roles and startup readiness. The download adapter must
  stop both its producer and transport before its abort callback returns, and
  close the guard on its owning thread on every disconnect.
- DAT-06/DAT-07 attach concrete submission, outbox and coverage references.
  Pending occurrences with an outbox reference are conservatively unreplaceable
  until their owner can prove and atomically perform safe cancellation.
- BG-01/BG-02/BG-04 implement operational scheduler queues, root retries, catch-up
  enumeration, coalescing and provider reconciliation. Current primitives perform
  bounded storage transactions only, not batch work or external sends.
- ADM-05/ADM-06 supply current authentication, confirmed readiness, complete
  Testing cleanup and all external quiescence/post-close proof. Restore runtime
  metadata remains reserved for OPS-06's state-aware release; no restore command
  is enabled by these schema additions.
- DAT-09/BG-11 bind the shared work-gate request identity to the actual
  PurgeRequest, enforce purge-worker transitions and perform destructive drainage.
  No deletion, backup or purge executor is exposed in this phase.

Database-backed builders in `tests/stewardship/database/campaign_builders.py`
and the linked scenario tests use actual configuration installation, TaskRuns,
ledgers and SQL constraints. Their explicitly synthetic admission callbacks are
not reusable application validators. Later DOM-05 factories extend these with
source, Family, submission, outbox and destructive-state scenarios.

## Validation and reviews

Targeted PostgreSQL tests cover ordered boundary rollback, catch-up fencing,
schedule replacement/fulfillment, pause/withdrawal, archive/successor gating,
restore assumptions, exact post-close versions, reopen and interrupted abort.
The complete validation run and three independent review/fix rounds remain in
progress; this file will record their evidence before the PR is opened.
