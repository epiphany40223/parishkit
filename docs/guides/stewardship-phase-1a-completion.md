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

Phase 1A implementation, local validation and four independent review/fix rounds
are complete. CI and human merge approval are tracked on the associated PR;
no formal Gate 1 release is claimed.
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
All campaign storage callbacks use the same four positional arguments:
`admit(action, campaign, runtime, subject)`. The subject is the locked occurrence,
demand, hold or intent when available, otherwise `None`; callbacks must not rely
on different argument counts for different operations. TaskRun's separate
domain-verifier interface is unchanged.

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
  skipped closed-period mail stays terminal. Pre-start activation to `scheduled`
  creates no catch-up demand; only direct activation to `active` does.
- ADM-05/ADM-06 supply current authentication, confirmed readiness, complete
  Testing cleanup and all external quiescence/post-close proof. Restore runtime
  metadata remains frozen until OPS-06 supplies its journalled state-aware
  release, including proposed-state evidence; unrelated runtime writers cannot
  change these fields. No restore command is enabled by these schema additions.
  Archive admission must inventory all semantic obligations, including those
  without an occurrence yet: the storage callback is mandatory, and checking
  existing rows alone is not a complete quiescence proof.
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
Lock contention during read or download admission maps to typed
`ReadUnavailable`/`DownloadBusy` (503, Retry-After: 5); unrelated database errors
are not silently reclassified as ordinary contention.
Direct asynchronous/off-thread response consumption is unsupported: adapters
must marshal disconnect cleanup to the owner. Deadline cleanup closes only the
raw driver handle off-thread; owning-thread teardown marks the lost transaction
closed before unwinding Django, and must not reconnect to restore autocommit.
Normal teardown acknowledges rollback then discards the response-owned session.
This intentionally includes healthy interactive reads: it prevents a deadline
racing rollback from reopening a backend during Django's autocommit cleanup.
Connection budgeting must account for response-owned sessions rather than assume
interactive persistent-connection reuse. Dedicated downloads explicitly construct
their backend wrapper with zero idle retention and health checks disabled.
The deadline remains armed during cleanup so network stalls cannot remove that
last bound; cancellation/loss still closes the exact handle without reconnecting.

Post-close storage currently supports digest obligations identified by
`schedule:<definition UUID>:<slot>`. It requires the exact skipped occurrence,
`admin_post_close_skip` reason, and matching task/outbox references. An owner must
create and cancel any missing occurrence atomically before recording coverage.
One receipt cannot cover a later corrected version. DAT-06/DAT-07 extend this
with concrete receipt/outbox identities and cancellation proof; a soft UUID alone
is never a provider outcome. Coalesced fulfillment covers a semantic slot before
dispatch, as specified; its replacement remains unresolved work until handled.

Exceptional cancellation can be confirmed by another currently authorized Admin.
The immutable cancellation records that resolving Admin; technical request
checkpoints retain the original request's attribution. Recovery also handles a
crash between preparing the exact snapshot and appending its prepared receipt.
An applied request remains immutable and cannot acquire an abort journal.
A journalled, never-applied cancellation can finish its terminal receipt after
an unrelated coherent configuration advances; it never rewinds that newer YAML.

## Validation and reviews

Targeted PostgreSQL tests cover ordered boundary rollback, catch-up fencing,
schedule replacement/fulfillment, pause/withdrawal, archive/successor gating,
restore assumptions, exact post-close versions, reopen and interrupted abort.
The following chronological checkpoints record validation and all four
independent review/fix rounds. Final completion evidence is at the end.

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
Ruff, Markdown, migration-drift and whitespace checks pass. At that checkpoint,
the remaining review rounds and final image/Compose validation were still required.

Round 2 reviewed `d7483b0af1ccd104b3405f963d2ef8775503eab3` against the same
complete base, using five Pika-assigned Claude shards and Pika's Codex reviewer.
Both vendors completed without degradation, failed agents or verdict mismatch;
19 findings were validated, including two High findings. Triage fixes 15 and
retains four existing behaviors with explicit evidence:

| Finding retained | Reason |
| --- | --- |
| Control reverse migration could discard a hypothetical intervening migration | No such migration exists in the dependency chain; restoring the frozen predecessor is correct. |
| Coalesced coverage precedes replacement delivery | Required by the background specification; regression verifies coverage is not delivery success. |
| Abandoned TaskRun blocks schedule replacement | Abandonment is nonterminal; regression proves explicit reconciliation/cancellation unblocks replacement. |
| Foreign-thread read-guard close raises | The synchronous owning-thread contract is intentional; regression verifies rejection does not alter the owner's transaction. |

Corrections cover both High findings, canonical occurrence recovery and SQL
evidence, uniform campaign callback arity, strict command metadata, catch-up
replay/fencing, abort recovery/NULL guards, download capacity reentrancy,
checkpoint downgrade protection and reconnect-free response cleanup. Explicit
close, draft-timezone and parish-default-timezone races complete DAT-02's
required concurrency cases. Focused runs pass 119 and 33 PostgreSQL tests.
The clean full rerun passes 1,958 baseline and 581 PostgreSQL tests, with 96.48%
line and 89.45% branch coverage. An earlier run had two secret-expiry failures;
the unchanged audit group passes all 30 tests separately, then the complete
rerun passes. Reports are `/tmp/parishkit-phase1a-quality6.*`.
All 30 rebuilt-image/Compose checks pass, including the same 1,958 image baseline
tests. Ruff, Markdown, whitespace and migration-drift checks pass.

Round 3 reviewed `9e31e37df3a44acd8a82c3c57d90c0a19d6d111a` against the full
merge base in Pika session `20260910-100528-1d2bfd`. All five Claude shards and
Codex completed successfully; finalization reports 19 validated findings, no
degradation and no failed agents. Both vendors identified one real High issue:
the exceptional-abort recovery callback still omitted its fourth argument.
The recovery call is corrected, and all campaign fixtures now enforce the exact
four-argument signature. TaskRun fixtures have a separate two-argument verifier.

Triage fixes 14 findings, consolidates three duplicate findings and retains two
specified behaviors with evidence:

- Coalesced coverage precedes provider delivery; the previous round's regression
  already verifies the required distinction. No resurrection of original work
  is introduced when its replacement needs retry or explicit resolution.
- Pending boundary interlocks are not dead: durable allocation/binding is
  supported storage for BG-02 and restored state. A new real-ledger regression
  proves a committed pending close with a running task blocks an end edit;
  reconciling that task permits replacement and atomically skips the old boundary.

Other corrections add first-live-effect and pause/resume assertions, exact
schedule-selection history checks, strict failure/version metadata, typed read
contention, explicit rejection of unowned generic transitions, SQL restore gates,
guard-patching multiplicity checks and trigger-order documentation. Race helpers
no longer conceal invariant/SQL failures as ordinary retries. Forced expiration
during read rollback verifies that cleanup cannot open a replacement backend.
DOM-02 evidence now explicitly distinguishes historical checkpoints from current
completion. Because round 3 found a real High issue, a fourth independent review
is required after correction validation; three rounds alone do not close this PR.

Post-round-3 validation passes 1,958 baseline tests and 597 PostgreSQL tests,
with 96.53% line and 89.65% branch coverage. All 30 rebuilt-image/Compose checks
pass, including the same baseline inside the image. Ruff, Markdown, whitespace
and migration-drift checks pass. Local artifacts are
`/tmp/parishkit-phase1a-quality7.*` and `/tmp/parishkit-phase1a-compose-tests4.log`.

Round 4 reviewed `577248f086360474e4f4635cf77252dc7c87815e` against the same
full base in Pika session `20260910-103435-666968`. All five assigned Claude
shards and the independent Codex reviewer completed successfully. Finalization
reports 27 Medium findings, zero High/Critical, and no degradation, failed agents,
verdict mismatch or salvage. The four-round review/fix cycle satisfies the
approved minimum-three-round/no-final-High criterion; final-round Medium fixes
belong to this round, not an additional independent review.

Triage fixes 23 findings and retains four behaviors. All findings below are
Medium; numbering follows Claude's validated findings, then Codex's finding.

| # | Disposition | Correction or rationale |
| --- | --- | --- |
| 1 | Fixed | PostgreSQL cases reject every destructive campaign state, missing read scope, reused/nested guards and malformed drain inputs. |
| 2 | Fixed | Runtime-history downgrade refusal consistently uses SQLSTATE `23514`. |
| 3 | Fixed | Recovery matrix asserts the precise unknown-outcome or unreconciled TaskRun error. |
| 4 | Fixed | Missing/mismatched download policy tests prove typed refusal and capacity cleanup. |
| 5 | Fixed | Pin a known SHA-256 advisory key and sorted, deduplicated UUID inputs. |
| 6 | Fixed | Fulfillment checks its exact outcome before falling back to SQL constraints. |
| 7 | Fixed | Recovery rejects boolean, float, string and nonpositive expected versions before querying. |
| 8 | Fixed | Restore inventory/review tests assert semantic trigger messages, not incidental FK failures. |
| 9 | Retained | Per-chunk server probes preserve fail-closed connection continuity; a cached producer may never query again. |
| 10 | Fixed | Twelve shared scenario helpers move into `campaign_builders`; their callers no longer import them from other test modules. Distinct pre-existing activation/audit setup fixtures are not conflated. |
| 11 | Fixed | A real runtime-only populated downgrade case reaches the previously uncovered refusal. |
| 12 | Fixed | Occurrence reasons require bounded lowercase codes rather than arbitrary provider text. |
| 13 | Fixed | Canonical occurrence allocation/transition/recovery inputs have direct regression coverage. |
| 14 | Fixed | Restore decisions require canonical identity, version, state and bounded evidence for exact replay. |
| 15 | Fixed | Post-close metadata is validated before stripping text, querying or comparing replay identity. |
| 16 | Fixed | Deterministic stale confirmations follow each race, independently of installer-lock contention. |
| 17 | Fixed | All three runtime SQL helper search paths are pinned and checked through PostgreSQL's catalog. |
| 18 | Fixed | Reject irrelevant ownership metadata and require a replacement exactly for coalescing. |
| 19 | Fixed | Exceptional checkpoint reversal reconstructs the frozen accounts migration predecessor. |
| 20 | Retained | Discarding healthy response-owned sessions closes the tested deadline/rollback reconnect race; see the connection contract above. |
| 21 | Fixed | Dedicated downloads explicitly construct their zero-idle backend wrapper rather than call Django's test convenience method. |
| 22 | Retained | Bounded installer serialization preserves selected-YAML/database agreement; background owners retry contention with backoff. |
| 23 | Fixed | Downgrade tests assert exact applied-migration-set restoration; reapply exceptions already fail the originating test. |
| 24 | Fixed | Catch-up failure receipts obey the same restore/current-campaign/work-gate constraints as progress; a restore regression verifies rollback. |
| 25 | Fixed | Structural preflight compares complete filtered dictionaries and rejects added/removed keys with a typed configuration error. |
| 26 | Retained | Scheduled Close remains an outstanding obligation: the worker must apply Start first. Skipping Start while remaining scheduled is not a valid path; narrowing the guard would permit losing Close. Existing ordered-boundary and rollback tests verify the prerequisite. |
| 27 | Fixed | Boundary-only history now blocks downgrade before its guards/table can be removed; a real migration regression verifies preservation. |

The final-round focused PostgreSQL suite passes all 138 cases. The rebuilt image
passes all 30 Compose checks, including 1,958 baseline tests inside the image.
The final full run passes 1,958 baseline tests and 634 PostgreSQL tests, with
96.75% line and 90.44% branch coverage. Ruff, formatting, tracked Markdown,
whitespace and migration-drift checks pass. Artifacts are
`/tmp/parishkit-phase1a-quality8.*`, `/tmp/parishkit-phase1a-compose-tests5.log`
and `/tmp/parishkit-phase1a-compose-build5.log`; generated logs and reports are
not committed. The rebuilt development image is
`sha256:21c301d398774f599a6e0ccc72483b1c7bb052a97a4b75ebac1ae76f7017d21b`.

No accepted Medium-or-higher finding remains unresolved. This completes Phase 1A,
not the integrated Gate 1 milestone. After this PR's human-approved merge,
proceed to Phase 1B beginning with ARC-03. No merge, deployment, release or
operational startup is authorized by this completion record.
