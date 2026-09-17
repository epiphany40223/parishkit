# Stewardship queued exact-export increment

[Controlling sequence](../plans/stewardship/overall.md#phase-4-production-scheduling-delivery-and-notifications)
· [Report tasks](../tasks/stewardship/reports.md)
· [Calculation contract](../specs/stewardship/reports/spec.md#participation-fact-materialization)

## Scope and boundary

Branch `pr/stewardship-exact-report-facts` starts from PR #42's verified merge
`c72b8bdb1fa37c555ee68ca926ff187a7ab52d7b`. This increment implements queued
participation-export requests before their exact calculation exists, durable
input protection, claim priority, and handoff to the existing export worker.

The existing ready-generation export path remains the export-what-is-shown
operation, including explicitly stale reports. The new path captures current
inputs once; it does not substitute a different generation when work waits or
is retried. Request/status/cancel/retry endpoints are a closed backend substrate,
not the Phase 5 report catalog. Scheduled verification, remaining RPT-02
statistics, and BG-07 digest occurrence ownership are later increments. RPT-03
checkboxes and Gate 3 remain open.

## Implementation contract

- `ExactExportRequest` retains requester intent, configuration/branding context,
  source, live submission watermark, campaign timezone configuration, and local
  through-date. Request-key replay compares intent before recapturing inputs.
- Current-population source protection commits with the request, before any
  worker claim. Historical population uses retained eligibility provenance.
  Retained request metadata protects matching calculated generations; neither
  cancellation nor artifact expiry releases input history implicitly.
- The compiled `report_exact_export` owner bypasses ordinary debounce. Claim
  admission rechecks priority under the same work-order lock used by ordinary
  rebuilds. It does not clear pending revisions or preempt frozen ordinary work.
  Another identical exact claim and the ordinary claim-to-allocation gap are
  handled explicitly; broker order is not authority.
- An existing ready generation is reused. A live/recoverable other builder is
  a dependency, not permission to replace its fence. A terminal exact builder
  can be resumed by another exact or ordinary owner for the same tuple without
  reopening its original request. Revoked abandoned exact owners terminate
  without rendering, so their checkpoints do not strand authorized consumers.
  Revocation observed by recovery fails that execution rather than holding it;
  restoring access does not restart it automatically. Its failed status remains
  visible to an Admin and an authorized requester can explicitly retry later.
  A failed ordinary builder retains its demand checkpoint and must be repaired
  by that owner; the dependent exact request fails visibly.
- Resolution, the downstream `report_export` root, the exact ready-generation
  pin and parent task completion commit together. SQL guards reject incomplete
  handoff, arbitrary worker-created exports, and mismatched inputs or ownership.
  The document retains the original request timestamp and configuration; child
  creation time remains the truthful later handoff time.
- Cancellation before handoff is an immutable receipt; after handoff it uses
  the existing export cancellation service. Explicit retries retain the original
  requester policy even when initiated by an Admin. Every claim/effect reloads
  requester and restore/purge/go-live authority.
- The three new record types and guards belong to the fresh-install baseline.
  No development upgrade path or retained database deletion is introduced.

## Validation and delivery evidence

Implementation, three dual-source review/fix rounds and final post-review
validation are complete; protected delivery is pending. PostgreSQL coverage includes actual
restricted web/worker roles, request replay, input pins, exact handoff, ordinary
priority, claim gaps, linked retries after midnight, terminal-builder recovery,
and atomic rollback of incomplete handoff. Baseline and schema inventory audit
evidence are recorded below; no full report-UI package completion is claimed.

The independent fresh-schema comparison used new, owned disposable databases
`stewardship_exact_base_20260916a` and `stewardship_exact_current_20260916a`
on port 55440. The reference matched PR #42's committed inventory. Only the
three exact-export tables and their columns/constraints/indexes, 11 new helper
functions, 10 triggers, and five intentionally extended existing functions
changed. No preexisting relation/column/constraint/index or policy changed or
disappeared. Model declarations match the installed fresh schema. The audit
did not modify or remove retained development databases.

Initial implementation `07d395d` passed 80 focused PostgreSQL integration tests
and the 6,039-test non-PostgreSQL baseline. The eight-shard run completed 3,317
PostgreSQL tests successfully; its one failure identified the immutable-record
test's missing mapping for the three new tables' shared guard. That assertion
was added, not weakened. The post-correction focused run passed 31 tests,
including the actual immutable-guard check. Full exact-head CI remains required.

## Preliminary review attempt (not a completed round)

Pika session `20260916-180044-f088ba` covered the complete PR diff through
`07d395d`. Claude reviewed all 29 files and reported two Medium and seven Low
findings. Codex exited immediately with `Selected model is at capacity` and
produced no result; finalization reported
`codex-reviewer: result artifact missing — codex produced no structured output`.
This degraded attempt counts as **zero** successful dual-source rounds.

Disposition of every raw Claude finding:

- Medium, exact-claim/ordinary-allocation gap: include running exact roots in
  ordinary claim deferral and test this reverse ordering under the real worker.
- Medium, live ordinary dependency treated as terminal: recheck all runs in
  the dependency root at the effect boundary and defer for five seconds, with
  subsequent claims gated on dependency completion.
  Regression coverage retries an ordinary frozen root after the exact claim.
- Low, recovery admission exception: return no recovery plan while a lifecycle
  gate holds; also recheck current requester policy before automatic retry.
- Low, opaque per-row matching: compare input columns directly in compaction
  and dependency queries so the planner can use existing key indexes.
- Low, retained inputs have no release path: indefinite parent retention is
  intentional. The explicit release owner is
  [ADM-10 campaign purge](../tasks/stewardship/admin-portal.md#adm-10-exceptional-campaign-purge-web-workflow),
  which must inventory/remove these retained parents before releasing their
  pins. Artifact expiry or ordinary housekeeping must not simulate that owner.
- Low, broad grants: remove unused exact-table access from the download role
  and the duplicate worker grant. Retain the scheduler's four explicit identity
  columns needed by current-policy admission; do not introduce a new
  security-definer bypass. Restricted-role tests prove revoked requests do not
  block ordinary work or receive new execution hints. An expired/abandoned
  revoked owner may receive a recovery-only hint to fence/terminalize it.
- Low, cancelled failed request retry: reject it with a conflict before
  allocating a doomed retry; test that the root still contains only one run.
- Low, expiry status: expose downstream expiry consistently, null before
  handoff, with both service and HTTP assertions.
- Low, pin-guard test ambiguity: match the new guard's diagnostic explicitly;
  add both missing claim/dependency ordering regressions noted above.

The post-correction fresh audit used a new owned
`stewardship_exact_current_20260916b` database. Relative to the initial audited
schema, only the four intended claimability/priority/source-pin/compaction
function bodies changed; all other catalog entries were identical.

## Review round 1

Successful dual-source session `20260916-181853-6b7264` reviewed the complete
`c72b8bdb..d8c00ba` diff. Both reviewers covered all 30 files. Raw severities:
Claude one Medium/seven Low; Codex two Medium. No failed agents, degradations,
verdict mismatches, or High/Critical findings. Post-fix validation completed
this round; it is not counted as a finding-free review.

Disposition of all ten raw findings:

- Claude Medium and Codex Medium, revoked abandoned owner strands identical
  work: terminalize the revoked owner through fenced recovery, without granting
  new report access. Another exact owner or due ordinary demand may then resume
  its immutable checkpoint. Ordinary adoption is restricted in both Python and
  SQL to terminal exact roots with matching inputs and no frozen demand owner.
- Codex Medium, repeated no-op recovery hints: fence expired running work, but
  only hint already-abandoned work when recovery has an actionable plan. Tests
  use the real scheduler role, restore hold, revoked policy and recovery worker.
- Claude Low, cancellation during materialization: acknowledge the cancellation
  at the next chunk admission boundary. Unrelated permission failures still
  propagate; cancellation cannot disguise a stale lease or unrelated failure.
- Claude Low, input capture race: submissions and source promotion already
  serialize with request creation through the shared work-order lock. A local
  midnight crossing can still reject the request atomically; the caller can
  safely retry its request key. Friendlier retryable HTTP presentation remains
  a Low follow-up for [RPT-01](../tasks/stewardship/reports.md#rpt-01-shared-report-framework-and-campaign-selection),
  not a relaxation of the frozen-input guard.
- Claude Low, backoff wording: describe the fixed five-second dependency
  deferral and subsequent claim gating accurately.
- Claude Low, duplicated capability/parameters: use the shared `Capability`
  enum. Keep the closed participation parameter dictionaries explicit; existing
  SQL equality and real handoff tests reject drift rather than silently widening
  the export contract.
- Claude Low, shared owner refactor: do not parameterize distinct task owners
  in this correction. Exact generation dependency/revocation behavior and
  renderer artifact recovery intentionally differ; sharing entire binding and
  outcome policies would obscure those boundaries.
- Claude Low, possible unbounded priority scan: not established by evidence
  (review confidence 30). Existing campaign/task-root indexes and nonterminal
  task predicates bound the candidate join; do not add speculative schema or
  claim FIFO authority from an optimization. Keep real workload measurement in
  the Phase 5 report framework performance checks.
- Claude Low, missing coverage: add revoked post-allocation ownership, real
  scheduler-held recovery, cancellation during calculation, delegated renderer
  retry/replay and expired downstream status tests. Negative restricted-worker
  tests also prove ordinary work cannot steal any nonterminal exact root.

Fresh audit database `stewardship_exact_current_20260916c` differs from the prior
audited baseline only in `stewardship_fact_runtime_binding_v1`; all tables,
columns, constraints, indexes, triggers and policies remain identical.

Round 1 post-fix validation: 58 focused PostgreSQL tests passed in 93.88 seconds;
26 schema/recovery tests passed in 29.80 seconds; the non-PostgreSQL baseline
passed 6,039 tests (4,176 intentionally skipped, two existing client deprecation
warnings) in 60.56 seconds. Ruff, formatting, Markdown, model drift and diff
checks passed. Two further successful dual-source rounds and protected CI
delivery remain required.

## Review round 2

Session `20260916-183552-3b64fc` reviewed `d8c00ba..bf2c692` with the full task,
request, SQL and prior-finding context. Both sources completed successfully;
Codex reported no findings and Claude reported five Low findings. There were
no Medium/High/Critical findings, failed agents or degraded results.

- Revocation policy clarity: document visible terminal failure of the execution,
  rather than a temporary hold or silent disappearance; assert the real Admin
  status response is `failed`. Restored authority still permits explicit retry.
- Ordinary recovery hint consistency: apply actionable-only hint admission to
  the ordinary owner too. Test scheduler hints inside and after a real restore
  hold without spending its recovery budget.
- Repeated terminal-owner predicates: retain explicit, small SQL admission
  branches and Python's shared `NONTERMINAL_STATES`. This Low refactor is not
  needed for correctness; tests now exercise every nonterminal state through
  the SQL guard, including a queued retry linked to a terminal original run.
- SQL negative-test precision: write the proposed ownership change directly
  under the restricted worker and require the SQL `frozen root` diagnostic.
  The running case no longer stops early in Python; add the queued-root case.
- Cancellation coverage: cancel after staging a real chunk, then prove ordinary
  recovery reuses those rows without reopening the cancelled export. Add
  mid-calculation identity revocation and a non-cancellation `PermissionError`
  test, proving neither is falsely acknowledged as cancellation.

Round 2 post-fix validation passed 31 PostgreSQL tests in 63.28 seconds, along
with repository Ruff/formatting and changed-document Markdown checks. No schema
change was needed. The third successful dual-source round and protected delivery
remain required.

## Review round 3

Session `20260916-184408-cde902` reviewed `bf2c692..ba46b15` plus surrounding
owner/SQL/task context. Both sources completed successfully without degradation
or mismatch. Raw findings: Claude six Low, Codex one Low; no Medium/High/Critical.
All seven raw findings were considered, including Claude's contextual finding
whose unchanged file was excluded by Pika's correction-diff path filter.

- Claude Low, revocation assertion contract: accept the current missing-user
  lookup exception or a future `PermissionError` translation, and still require
  non-cancelled task state. The separate non-cancellation permission-failure
  regression already exercises and protects the actual re-raise branch.
- Claude Low, missing-principal exception normalization: defer changing shared
  `current_principal`/`authorize` behavior outside this correction. Identity
  revocation remains fail-closed and is tested mid-build; recovery terminalizes
  the owner without reading its report data. No raw exception reaches the HTTP
  caller, whose existing closed error handling covers missing objects.
- Claude Low, queued-retry precondition: assert exactly one nonterminal run in
  the original root and its expected state before every direct SQL takeover
  rejection, so a different setup cannot accidentally satisfy the test.
- Claude Low, multi-chunk fixture: the added test deliberately cancels after
  staging and before publication, complementing the pre-staging cancellation
  test. It asserts preserved row identities after real ordinary takeover.
  Generic partial-chunk/replay/conflict behavior already belongs to and is
  covered by `test_fact_recovery_postgresql.py` and
  `test_fact_materialization_postgresql.py`; retain these layered tests rather
  than adding a large synthetic campaign solely to cross the 250-row boundary.
- Claude Low, mid-function import: move the scheduler import to module scope.
- Claude and Codex Low, duplicate binding queries: extract a private recovery
  planner accepting the freshly bound demand. Keep the dispatcher wrapper's
  fresh binding, and prove an abandoned scheduler candidate binds only once.

The reviewed implementation at `ba46b15` passed 48 export/schema PostgreSQL
checks in 49.05 seconds and the 6,039-test baseline in 57.02 seconds (4,180
profile skips, two existing warnings). Final post-correction validation passed
94 PostgreSQL tests in 122.79 seconds, repository Ruff/formatting, tracked
Markdown and whitespace checks. No accepted Medium-or-higher finding remains.

The implementation and review fixups are consolidated into one signed-off
feature commit for delivery. The pre-squash history is retained locally under
`refs/backup/stewardship-exact-report-facts-pre-squash`; the final source tree is
verified byte-identical across that history-only operation. Reviewer session
identities and original endpoints above remain the review record. Final-head
CI and protected merge-group checks must pass before merging.

## Protected delivery

PR #43 merged as `053eed78a935443ff29ae6ed7f84778d582fb48d` on September 16,
2026. All 24 exact-head jobs passed at `3c3c0b8` (run `35159793642`), with
DCO passing and combined coverage of 93.99% statements and 85.22% branches.
All 24 protected merge-group jobs passed (run `35161289648`); the merge is
verified on freshly fetched `origin/main`. The earlier pending-delivery notes
are superseded. Scheduled verification is the next dependency-ready increment;
remaining statistics, BG-07, the complete report UI and Gate 3 stay open.
