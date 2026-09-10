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
  Catch-up input bindings, fenced group checkpoints, sanitized failure receipts
  and explicit completion preserve a hold independently of TaskRun completion.
  Failure records do not advance coverage counts or clear the preparation hold.
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
  bounded storage transactions only, not batch work or external sends. Each joins
  installer serialization to preserve agreement with selected YAML; callers must
  reschedule `ConfigurationBusy` with bounded backoff, never spin inside a locked
  transaction. BG-02 adds its explicit fenced preparation-allocation path before
  using occurrences during the catch-up hold; ordinary allocation and dispatch
  remain held. Reopen intentionally does not create activation catch-up: already
  skipped closed-period mail stays terminal.
- ADM-05/ADM-06 supply current authentication, confirmed readiness, complete
  Testing cleanup and all external quiescence/post-close proof. Restore runtime
  metadata remains frozen until OPS-06 supplies its journalled state-aware
  release, including proposed-state evidence; unrelated runtime writers cannot
  change these fields. No restore command is enabled by these schema additions.
- DAT-09/BG-11 bind the shared work-gate request identity to the actual
  PurgeRequest, enforce purge-worker transitions and perform destructive drainage.
  No deletion, backup or purge executor is exposed in this phase.

Database-backed builders in `tests/stewardship/database/campaign_builders.py`
and the linked scenario tests use actual configuration installation, TaskRuns,
ledgers and SQL constraints. Their explicitly synthetic admission callbacks are
not reusable application validators. Later DOM-05 factories extend these with
source, Family, submission, outbox and destructive-state scenarios.

## Restore and response integration details

Restore inventory insertion requires the active restore ID, backup instant and
current campaign. OPS-06 inventories with the global gate raised, then performs
its state-aware release. Per-slot holds remain after global release. An Admin
may subsequently bind an authorized resend to an exact pending occurrence;
Family initial/reminder occurrences still require the live campaign interval,
while daily/weekly digest obligations may continue in `closed`. Global release
never reopens Family access or revives terminal mail. Restored-runtime test
fixtures load synthetic offline state using disposable schema-owner privileges;
all tested application calls run with their SQL guards enabled.

Read adapters must map `DownloadConfigurationUnavailable` to the same accessible
503/Retry-After response as exhausted capacity, and surface an operator diagnostic
without private values. The SQL policy is a materialized deployment budget, not
an independently adjustable feature flag. OPS-04 owns its YAML/CLI integration.
Until that owner is implemented, an internal capacity change requires a
coordinated maintenance window: stop new downloads on all replicas, drain all
existing responses, update the singleton's capacity and increment its version,
configure matching `ReadLimits`/connection budgets on every replica, verify
agreement, then resume admission. Never roll out mismatched limits or derive
independent per-replica global caps.

The guard deliberately probes its pinned connection before reading and before
emitting each chunk; connection-loss detection is not rate-limited. Response
adapters choose practical chunk sizes. A successful deadline abort confirms
producer and transport termination and returns local capacity after closing the
connection, even if the abandoned consumer never resumes. An abort callback
that raises has violated this contract; local capacity stays reserved until
owning-thread cleanup rather than claiming the producer stopped.

Exceptional cancellation can be confirmed by another currently authorized Admin.
The immutable cancellation records that resolving Admin; technical request
checkpoints retain the original request's attribution. Recovery also handles a
crash between preparing the exact snapshot and appending its prepared receipt.
An applied request remains immutable and cannot acquire an abort journal.

## Validation and reviews

Targeted PostgreSQL tests cover ordered boundary rollback, catch-up fencing,
schedule replacement/fulfillment, pause/withdrawal, archive/successor gating,
restore assumptions, exact post-close versions, reopen and interrupted abort.
The complete validation run and three independent review/fix rounds remain in
progress; this file will record their evidence before the PR is opened.

Round 1 reviewed `f4c9588bfc4cb4b1e589fba64ef3617be25eacc7` against the complete
PR #18 merge base, with four Pika-assigned Claude shards and Pika's independent
Codex reviewer. Both vendors completed without degradation or verdict mismatch;
the result was `REQUEST_CHANGES` with 38 validated findings.

Triage fixes 30 findings (including expanded negative and concurrent tests).
Eight findings are discarded or consolidated:

| Finding | Disposition and evidence |
| --- | --- |
| Bare schedule `values` supposedly fails at runtime | False positive: repeated real configuration activation and semantic-delivery/revision tests pass on PostgreSQL 18. |
| Reduce connection probes during streaming | Retain the specified per-chunk fail-closed connection check; adapters control chunk size. |
| Abort lacks an outer-transaction guard | Already enforced by the shared `installation_lock`; file recovery cannot nest inside `campaign_transaction`. |
| Remove installer serialization from bounded campaign writes | Retain SQL/YAML coherence; document retryable contention for background owners. |
| Capacity DELETE supposedly raises an unassigned-record error | False positive: a real DELETE returns the intended SQLSTATE `23514`. |
| Duplicate capacity-policy source-of-truth finding | Consolidated with typed 503 and the coordinated capacity-change contract above. |
| Abort could attach to an applied request | Already rejected by SQL before journal insertion; new regression verifies the applied receipt survives. |
| Duplicate mutable restore-gate finding | Consolidated with freezing reserved restore metadata and exact inventory provenance checks. |

Claims that reverse migrations were entirely untested were not accurate: the
existing suite already reverses/reapplies the full empty campaign schema and
refuses populated downgrade. Triage nevertheless adds focused new-ledger and
active-download reversal cases. Runtime purge execution/races remain with
DAT-09/BG-11; current reservation/release guards are tested, including refusal of
later-owner running/tombstone sentinel states without executing a purge.

Post-round-1 validation passes 1,958 baseline tests and 539 PostgreSQL tests;
scoped coverage is 96.01% lines and 88.56% branches. A subsequent focused
13-test read-guard run also covers cancellation failure during deadline cleanup.
Ruff, Markdown, migration-drift and whitespace checks pass. The remaining review
rounds and final image/Compose validation are still required before handoff.
