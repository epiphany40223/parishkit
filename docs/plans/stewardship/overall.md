# Stewardship overall implementation plan

This is the controlling implementation sequence for the complete
[Stewardship/Census specification set](../../specs/stewardship/spec.md). It
composes the subsystem work packages in this directory, establishes vertical
milestones, and defines mandatory pauses for review and correction.

The normative specifications decide behavior. Subsystem plans decide how to
break that behavior into work. This master plan decides when the work may begin
and when it is safe to advance.

Execution tracking: [top-level task plan](../../tasks/stewardship/overall.md),
[per-spec checklists](../../tasks/stewardship/README.md), and
[demonstration/review evidence](../../tasks/stewardship/milestones.md).

## Implementation principles

1. Build in small, signed, reviewable commits and topic-branch pull requests;
   avoid a single application-sized change.
2. Keep incomplete externally mutating or destructive capabilities disabled by
   server-side feature/admission checks until their master phase and review gate
   are complete.
3. Build fake-backed adapters and deterministic clocks first. Normal CI never
   uses real ParishSoft, Google, mail, Slack, backup, or cloud credentials.
4. Make PostgreSQL constraints, transactions, leases, and idempotency part of
   the first implementation of a workflow, not later hardening.
5. Add accessible HTML and authorization tests with each capability; do not
   postpone mobile, privacy, or negative-role behavior to the final phase.
6. Finish and correct each formal review gate before starting packages assigned
   to the next gate.
7. If implementation reveals a specification conflict, stop that package,
   amend/review the specification and plans first, then resume code. Do not let
   implementation silently redefine the product.

## Phase and gate summary

Every phase follows the [automated phase delivery cycle](#automated-phase-delivery-cycle),
including a human-approved PR merge before the next phase. The formal gates
below add integrated checks; they do not replace each phase's review cycle.

| Phase | Integrated outcome | Mandatory pause |
| --- | --- | --- |
| 0 | Reproducible skeleton, plan traceability, and fast CI | No formal gate; inspect scaffold before foundations |
| 1 | Secure runtime, core schema, identity, lifecycle policy, and Compose | Review Gate 1: foundation/security |
| 2 | First-Admin setup, ParishSoft source truth, and campaign configuration | Continue into one Family slice, then gate |
| 3 | Complete Testing-mode Family response vertical slice | Review Gate 2: data/privacy/Family UX |
| 4 | Production transition, scheduler, mail, confirmation, and digests | Continue into reports, then gate |
| 5 | Reports, exports, user/Ministry management, and follow-up | Review Gate 3: async/RBAC/reporting |
| 6 | Publication, restore, close/archive/reopen, backup, and purge | Review Gate 4: external/destructive workflows |
| 7 | Full acceptance, scale, accessibility, runbooks, and release artifacts | Review Gate 5: release readiness |

## Automated phase delivery cycle

Human-approved workflow, September 8, 2026, with larger delivery batches and
continued delegated execution confirmed September 9, 2026:

1. After the preceding phase's PR is merged and its applicable gate is released,
   fetch `origin` and create a new `pr/stewardship-phase-N` topic branch from the
   current tip of `origin/main`. Do not branch the next phase from an unmerged
   implementation branch. Preserve existing work; Phase 0 continues on its
   already established `pr/stewardship-implementation` branch.
2. Implement all dependency-ready work admitted to that phase, including new
   tests, documentation, and task evidence. Packages spanning phases retain
   their documented scope split. Run repository and phase-specific checks and
   correct failures until all required checks pass.
3. Complete at least three independent review-and-fix rounds per phase, normally
   using `local-review` followed by `local-review-triage`. A round comprises a
   completed dual-source review, evidence-based disposition of its findings,
   applicable fixes/regression tests, and passing post-fix validation. Existing
   completed Phase 0 rounds count; a failed or degraded review is not a completed
   round. A round finding no actionable issues still counts.
4. Fix every accepted Medium, High, and Critical finding. Push back on or discard
   inaccurate, duplicate, already-handled, or out-of-phase findings only with
   concrete evidence and a recorded rationale; use an existing later-phase owner
   for intentionally deferred scope. Do not discard a real in-scope defect just
   to satisfy the exit criterion. Routine technical choices are delegated to
   the implementing agent, including triage decisions with multiple reasonable
   approaches when the specifications establish the intended behavior.
5. Exit the loop only after at least three completed rounds, no validated High
   or Critical finding in the final round, no unresolved accepted Medium-or-
   higher findings, and passing validation. Fixes and their regression tests
   are part of that round; corrections alone do not require another round or
   a finding-free final review. Record raw
   reviewer severities as well as any evidence-backed rejected findings; never
   treat an incomplete review as a clean round.
6. Use one PR per named phase or subphase by default. Group dependency-ready
   schema, policy, services, integration and tests into a coherent demonstrable
   outcome. Do not routinely end a PR at an individual model, migration, helper
   or checklist item. Use logical commits and internal test checkpoints within
   the batch; these do not require human approval or a separate three-round
   review loop. Split only when a substantive independently testable outcome or
   reviewability boundary justifies it, and record why. Each resulting PR follows
   the full review/fix cycle. Formal gates still review the complete integrated
   scope since the previous gate, including already merged changes. Create each
   subsequent branch from refreshed `origin/main` after its predecessor merges.
7. Once local validation and the review loop pass, push and create the PR against
   `origin/main` (or update the existing phase PR). Watch CI for the current head,
   fix failures, rerun affected checks, and push corrections until required CI
   passes. Material CI fixes return to independent review without resetting the
   already completed round count. Record phase/task scope, reviewed SHA, round
   evidence, findings/dispositions, validation, and any permitted Low deferrals
   in the PR handoff and owning task evidence.
8. Stop for explicit human approval before merging. Do not enable auto-merge or
   merge merely because CI passes. At a formal gate, present its required
   evidence with this same approval request; no extra routine approval stop is
   needed earlier in the development cycle. Record gate approval when granted,
   merge only as authorized, then begin the next phase from updated `origin/main`.

Autonomously investigate failures, implement specification-consistent fixes,
commit with sign-off, push backups, run reviews, and correct CI. Ask during the
cycle only when proceeding genuinely needs new authority or a product/security
decision not resolved by the specifications. Permission denials, protected
workflows, unavailable credentials, real external writes, destructive operations,
deployment, and release retain their existing authorization boundaries. This
workflow does not grant blanket permission bypasses or waive gate-specific tests.

Reviewer patience update (human instruction, September 10, 2026): double the
previous reviewer wait budget before asking whether to continue waiting. For
the current Pika baseline, use 40 minutes of wall time and 10 minutes without
progress, instead of 20 and 5 minutes. Apply this per review invocation, without
changing installed skills or global tooling defaults. Use short nonblocking
polls, continue useful independent work, and keep the human informed; slow
progress alone is not a permission blocker or a reason to request approval.
Actual permission failures and invalid/degraded review results retain the
existing handling rules and never count as a completed round.

## Phase 0: Reproducible project skeleton

Goal: every later change has a runnable package, test harness, Compose target,
and traceability owner.

Execute in this order:

1. **ARC-01** — create the Django package/apps, settings, URLs, entry point, and
   safe placeholders.
2. **DOM-01** — establish domain vocabulary and decision records in that
   package skeleton.
3. **ARC-02** — integrate shared ParishKit configuration, define the versioned
   Stewardship YAML authority and materializer interfaces, paths, logging, and
   fail-closed scaffold settings. Exercise configuration contracts with fake
   materializers. Defer concrete database-backed activation/startup checks to
   DAT-01 integration in Phase 1; complete credential/mount/service checks with
   ARC-06 and OPS-02/OPS-04 before Gate 1. Leave tasks with that remaining scope
   unchecked; this phase split does not waive their verification.
4. **OPS-01** — start the development/production Compose topology with web,
   config/credential installers, general worker, backup-worker, mail-dispatch,
   token-key-rotation, scheduler, PostgreSQL, Valkey, and Caddy.
5. Start **OPS-09** with fast lint/format/Markdown/unit/migration checks and
   scoped coverage reporting.
6. Start **DOM-05** with deterministic clock/timezone helpers and an acceptance
   traceability table; defer database-backed factories until DAT-01 exists.

Phase demonstration:

- A developer starts the bind-mounted development environment and receives the
  intentional placeholder routes and internal health response.
- The same tests run outside and inside the application image.
- Production settings fail safely without required configuration.

Do a lightweight scaffold review and correct package/Compose boundaries before
Phase 1. This is not Review Gate 1 and need not run browser/destructive suites.

## Phase 1: Secure foundation and durable domain

Goal: establish trustworthy storage, identity, session, secret, authorization,
and lifecycle foundations before collecting parish data.

### 1A: Base data and lifecycle

1. **DAT-01** — applied-YAML/change/secret-request, parish, audit, and session
   base records. Integrate ARC-02's concrete configuration materializer and
   database-backed digest/mode checks against these records; do not create
   substitute tables during Phase 0.
2. Land **DOM-02** item 1 — the canonical campaign-interval resolver required
   by database interval constraints.
3. **DAT-02** — campaigns, schedules, lifecycle constraints, and boundary
   occurrences.
4. Complete **DOM-02** lifecycle policies over those records.
5. **DAT-05** — portal users, login rules, assignments, and constraints.
6. **DOM-03** — canonical role/capability/object-scope policy.
7. Begin the database-backed **DOM-05** factories/builders after DAT-01 lands.

### 1B: Web and identity security

1. **ARC-03** — security middleware, safe content/upload/export primitives.
2. **ARC-04** and **ADM-01** — Google-only login, PostgreSQL sessions, denial,
   throttling, and unconfigured-state behavior.
3. **DAT-04** then **ARC-05** — Family campaign identity, code/token storage,
   sessions, exchange, guessing defense, and key migration.
4. **ARC-06** — config/target-secret installers, sealed handoff, separate token
   keyrings, and mail-dispatch mount.
5. **ARC-07** — audit, privacy, optimistic concurrency, and validation-error
   primitives.
6. Begin **ARC-08** and complete **DOM-04** shared responsive/accessibility/
   formatting components.

### 1C: Operational foundation

1. **OPS-02** — durable paths/volumes and least-privilege mounts.
2. **OPS-03** — ingress/TLS/proxy/logging boundary.
3. **OPS-04** — migrations/startup/upgrade mechanics.
   Complete ARC-02's production startup integration and PostgreSQL-backed
   failure/recovery tests using DAT-01 records, ARC-06 installer boundaries,
   and OPS-02 mounts. Production must not become runnable through placeholder
   validators or an unconditional readiness result.
4. Establish the baseline portions of **OPS-08** for logs, health, and metrics.

Phase demonstration:

- Google login and every allow/deny/timeout path work against fake provider
  responses.
- Family code/token exchange works against seeded campaigns, including boundary
  denial, without any form capability yet.
- A PostgreSQL constraint/race test proves only one current campaign.
- Compose inspection proves the web/general services lack token private keys.

### Review Gate 1: Foundation and security

Pause all Phase 2 work. Apply the
[review-gate protocol](#review-gate-protocol), including focused reviews of:

- migrations, singleton/current-campaign constraints, UTC/DST behavior;
- Google/domain/address authorization and immediate revocation;
- Family code/token/session/throttling threat model;
- key rotation and service mounts;
- YAML/database activation and crash recovery, target-scoped secret installers,
  configuration/secret/log redaction, and Caddy trust boundaries; and
- restart/migration durability.

Exit only with no unresolved validated Critical, High, or Medium findings and
with the Phase 1 demonstration rerun after corrections.

## Phase 2: Source truth, initial setup, and campaign preparation

Goal: an Admin can bring an empty deployment to a configured Testing campaign
backed by one coherent ParishSoft snapshot.

Execute in this dependency order:

1. **DAT-03** — versioned source corpus and SourceMutationLease.
2. **BG-01** — durable tasks, scheduler hints, leases, progress, recovery, and
   admission checks.
3. **BG-05** — full/delta/manual ParishSoft refresh and atomic promotion.
4. **ADM-02** — bootstrap and first-Admin wizard, including staged load
   and the specified hard setup watchdog.
5. **ADM-03** — dashboard, indicators, parish/integration configuration, and
   secret tests.
6. **ADM-04** — campaign editor, content/templates, schedules, previews, and
   readiness-test sends using a fake mail adapter.
7. Complete campaign-population portions of **DAT-04** and chair-suggestion
   portions of **DAT-05** during source promotion.

Phase demonstration:

- Empty deployment to completed first campaign in Testing.
- Applied YAML and PostgreSQL digests match after every wizard/editor save;
  an induced installer crash fails closed and resumes without partial config.
- Aborted/expired wizard leaves no active product configuration, plaintext, or
  staged secret.
- Valid full and delta refresh promote atomically; invalid or interrupted loads
  preserve prior truth.
- Active/inactive/reactivated Families receive stable campaign codes.
- Admin can preview every configured Family page/email without creating live
  fulfillment.

Do a targeted data-import/setup correction pass here, but defer the formal gate
until the same data is exercised through a Family response in Phase 3.

## Phase 3: Complete Family response vertical slice

Goal: a real Family can authenticate in Testing, review all enabled data, submit
once atomically, revisit, and see a correct source/response merge.

### 3A: Submission data and minimal vertical slice

1. **DAT-06** — immutable submission and proposal overlay.
2. Implement the submission/follow-up subset of **DAT-07** needed by one Family
   census response.
3. **DAT-08** — merge and source reconciliation.
4. **FAM-01**, **FAM-02**, and the smallest end-to-end portion of **FAM-03** plus
   **FAM-06**: code login, one census page, review, no-change/change submit, Thank
   You/logout, and repeat visit.
5. Add the first executable **DOM-05** Family acceptance scenario before adding
   the remaining fields.

### 3B: Complete the Family specification

1. Finish **FAM-03** and implement **FAM-04**, **FAM-05**, **FAM-06**, and
   **FAM-07** for all census, proposed Member, Ministry, financial, additional,
   submit, and repeat-merge behavior.
2. Perform representative **FAM-08** mobile, desktop, privacy, keyboard, and
   screen-reader tests continuously.
3. Complete related follow-up derivation in **DAT-07** and calculation primitives
   from **RPT-02** needed to verify populations/pledges.
4. Use a stubbed confirmation occurrence only; actual dispatch lands in Phase 4.

Phase demonstration:

- Run every enabled-module combination, including no-change, all census fields,
  proposed/terminal Member, Ministry join/leave, zero/positive pledge, share
  options, and additional information.
- Prove no intermediate answers are persisted and failed/stale/double submits
  create no partial or duplicate response.
- Promote upstream changes and demonstrate caught-up, source-only, Family-only,
  and true conflict repeat visits without Admin-edit attribution.

### Review Gate 2: Family data, privacy, and UX

Pause Phase 4. Run the review protocol with special attention to:

- first-Admin staging, abort/expiry cleanup, and secret replacement/testing;
- SourceMutationLease fencing, full/delta validation, and atomic snapshot
  promotion/fallback;
- immutable submission transactions and derived-work rollback;
- three-way merge/provenance and test/live isolation;
- every Family authentication/boundary/session path;
- no intermediate storage or answer leakage;
- mobile form navigation, validation, focus, expiry, and accessibility; and
- ParishSoft terminology exclusion from Family-facing content.

Require at least one full browser review on a narrow mobile viewport and one
desktop viewport after corrections.

## Phase 4: Production scheduling, delivery, and notifications

Goal: move a tested campaign safely to Production and run reliable scheduled
communication through pause, restart, and failure.

Execute in this order:

1. Complete **DAT-07** job/outbox/production-transition records and terminal
   credential scrubbing.
2. **BG-02**, **BG-03**, and **BG-04** — lifecycle boundaries, batched go-live
   cleanup, schedule revisions/fulfillment, mode routing, and recovery.
3. Begin **BG-08** with the deterministic chart-rendering and authorized export-
   job substrate needed by digest/report consumers.
4. **BG-06** — Family mail rendering, sealed substitutions, dedicated dispatch,
   provider outcomes, pause holds, and reconciliation.
5. Before **BG-07**, complete its required **RPT-02** calculation services and
   the **RPT-03** immutable fact-materialization service (item 2, with its
   service-level tests from item 6). Then implement **BG-07** submission
   confirmation and daily/weekly digests against that service. The interactive
   RPT-03 report UI and full report validation remain in Phase 5; leave that
   package partially complete until then.
6. **BG-10** — operational escalation and safe shutdown behavior.
7. **ADM-05** — readiness, cleanup status/cancel, Production activation, and
   pre-start withdrawal.
   Before enabling direct activation, integrate **DAT-02** catch-up demands,
   **BG-04** bounded materialization/coalescing, and **BG-06/BG-07** preparation-
   hold enforcement; exercise the activation load/recovery tests at this handoff.
8. Implement delivery-pause portions of **ADM-06** needed during an active
   campaign.
9. Recheck **ARC-06**, **OPS-01**, and **OPS-02** with actual queue/task and
   credential needs; do not broaden mounts to solve routing mistakes.

Phase demonstration:

- Testing reroutes every campaign message while operational alerts remain
  correctly classified.
- Go-live freezes Testing work, survives interrupted batch cleanup, and commits
  one short Production transition.
- Initial/reminder/confirmation/digest work is idempotent across broker/worker/
  scheduler restarts and missed schedules.
- Provider timeout becomes `delivery_unknown`, never an unsafe automatic resend.
- Pause/resume and close-during-pause preserve submissions and correctly resolve
  held receipts/digests.

Keep live provider dispatch and the final Production-transition commit limited
to fake-backed or disposable environments until Review Gate 3 exits. This is
the review gate associated with Phase 4's external and irreversible behavior.

## Phase 5: Reports, exports, users, and follow-up

Goal: Staff/Admin/leaders can monitor and act on the live campaign within exact
role, Ministry, column, and campaign scope.

Execute in this order:

1. Complete **BG-08** export/chart worker and temporary-file authorization.
2. **RPT-01** and finish **RPT-02** shared selection, authorization, query, and
   calculation services.
3. **RPT-03**, **RPT-04**, **RPT-05**, **RPT-06**, and **RPT-07** participation/
   statistics, additional information, codes/postal outreach, Ministry summary,
   and packet.
4. Implement the reporting half of **RPT-08**.
5. **ADM-07** user rules and Ministry assignment/suggestion UI.
6. **ADM-08** manual refresh, follow-up queues, and log UI.
7. Implement **RPT-09** after ADM-08 provides its log UI; wire RPT-04/RPT-06
   workflows to ADM-08 services and verify BG-07 parity.

Phase demonstration:

- Every report selects live/historical campaigns correctly and produces all
  required formats from the same calculations.
- Admin/Staff can view/export manual Family codes; Ministry leaders cannot.
- Ministry leaders see/edit only assigned-Ministry follow-up and never financial
  or unrelated Family columns.
- Web/chart/export/daily-email statistics match for the same source/campaign/
  timezone parameters.
- Revoking a role/assignment between export creation and download denies access.

### Review Gate 3: Async processing, RBAC, and reporting

Pause Phase 6. Run the protocol with focused reviews of:

- scheduler/outbox semantic idempotency and restart behavior;
- mail recipient privacy, Testing routing, sealed data, private-key mounts, and
  unknown-provider outcomes;
- report population/denominator/historical correctness and email parity;
- per-role, per-Ministry, per-column, queued-job, and download authorization;
- code-bearing report policy and audit redaction; and
- generated CSV/XLSX/PDF/PNG security and temporary retention.

Run a second review after corrections because Phase 6 introduces real external
writes and irreversible deletion based on these foundations.

## Phase 6: Reconciliation and post-campaign operations

Goal: safely finish a campaign, publish supported changes, recover deployments,
archive/return to Testing, and exceptionally purge historical data.

### 6A: Review and ParishSoft publication

1. **DAT-09** publication plans/outcomes and destructive-state schema.
2. **ADM-09** proposal review/edit/subset/preflight/progress UI.
3. **BG-09** fenced ParishSoft PUT/read-after-write/partial retry/final refresh.
4. Complete publication/action portions of **RPT-08** and manual resolution
   links.

Keep actual PUT disabled outside explicit fake/smoke environments until its
focused review passes.

### 6B: Backup, restore, lifecycle completion, and retention

1. **OPS-05** backup, including purge-triggered verified off-host backup.
2. **OPS-06** restore/maintenance gate/uncertainty holds/state-aware release,
   completing **BG-02**'s shared token-preparation slice for restore/reopen.
3. Complete **ADM-06** restore release, closed reopen, held-message resolution,
   archive/unarchive, and post-archive Return to Testing; finish **BG-02**'s
   background token-preparation slice before enabling either release or reopen.
4. **OPS-07** temporary housekeeping and safe retention.

### 6C: Exceptional purge

1. Complete PurgeRequest/gate/batch behavior from **DAT-09**.
2. **ADM-10** guarded web inventory, post-Return-to-Testing/pre-successor
   eligibility, quiescence, backup and operator recovery-evidence freshness,
   confirmations, status, and retry
   UI.
3. **BG-11** — implement purge execution/recovery under BG-01 while preserving
   all state transitions and checkpoints from DAT-09. Integrate the DAT-02
   read guard already used by report/download consumers; demonstrate reader
   drainage before the first batch and safe timeout/restart behavior.
4. Exercise purge only in disposable/restored test environments until Review
   Gate 4 exits.

Phase demonstration:

- Upstream catch-up/conflict/Admin edit and supported/unsupported proposal paths
  all resolve accurately.
- Partial ParishSoft write failure retries only unresolved entities and verifies
  source truth.
- Restore starts fail closed, inventories uncertainty, and releases every
  supported lifecycle state atomically.
- Closed campaign can reopen only through readiness; archive then Return to
  Testing is required before a successor.
- Purge blocks wrong state/stale evidence/concurrent work, recovers every
  interruption boundary, and leaves only the specified tombstone/audit.

### Review Gate 4: External writes and destructive workflows

This is the strictest gate. In addition to the standard protocol:

1. Obtain a focused database transaction/migration review.
2. Obtain a security/authorization/confirmation review of publication, restore,
   archive, and purge.
3. Run failure injection at every lease/checkpoint/external-call boundary.
4. Restore a real-format encrypted backup into an isolated environment and run
   state-aware release scenarios.
5. Run purge on a disposable copy, interrupt it before and after first deletion,
   and verify rollback/retry/tombstone behavior.
6. Re-run review after all corrections; no accepted unresolved Critical, High,
   or Medium finding is permitted for these workflows.

## Phase 7: Production hardening and release readiness

Goal: prove the whole specification under production-like scale and artifacts.

1. Complete **ARC-08** performance/browser/accessibility work.
2. Complete **DOM-05** traceability and every numbered acceptance scenario.
3. Complete **FAM-08** and all report browser/accessibility/scale cases.
4. Finish **OPS-08** metrics, health, alerts, diagnostics, and failure runbooks.
5. Finish **OPS-09** coverage, integration, browser, accessibility, Compose,
   SBOM/provenance/scan, multi-architecture image, and release jobs.
6. Run documented credential-backed smoke tests for ParishSoft read/write,
   Google login/mail, optional Slack, backup target, TLS, and restore with
   redacted output and explicit human control.
7. Conduct data-retention/privacy review against logs, exports, backups,
   temporary files, Testing cleanup, and purge tombstones.
8. Prepare operator/developer documentation, migration/upgrade notes, known
   limitations, and initial deployment checklist.

Phase demonstration is the complete acceptance suite plus a production-image
deployment from empty bootstrap through one small disposable campaign and
post-campaign archive/backup/restore cycle.

### Review Gate 5: Release readiness

Run the standard protocol on the complete branch, then:

- run all required local validation, coverage, integration, browser,
  accessibility, Compose, image, vulnerability, backup/restore, and acceptance
  jobs;
- resolve every validated release-blocking issue and rerun the full local review
  after corrections;
- obtain human product/security/operations approval of known limitations and
  smoke-test evidence; and
- merge through the normal pull-request process. Creating or pushing a semantic
  release tag remains a separate explicitly human-authorized action.

## Review-gate protocol

Every formal gate uses this sequence:

1. Stop new feature work and bring the gate's work packages to coherent,
   logically signed commits. Squash fixups appropriately before final review.
2. Run the repository checks plus gate-specific migration, PostgreSQL/Valkey,
   browser, accessibility, Compose, coverage, and failure-injection suites.
3. Obtain two independent reviews of the complete gate diff, preferably through
   the installed `$local-review` skill, and preserve their findings for triage.
   If that skill is unavailable, stop for the human to provision it or approve
   an equivalent independent dual-review procedure before continuing.
4. Triage all validated findings, preferably through the installed
   `$local-review-triage` skill. Apply the delegated decision policy in the
   [phase delivery cycle](#automated-phase-delivery-cycle); reserve human
   questions for unresolved product/security decisions or new authority.
5. Add regression tests with every correction, run narrow tests while fixing,
   then rerun the full gate validation.
6. Repeat independent review and correction under the phase delivery cycle's
   minimum-round and exit criteria. At a gate, review all integrated work since
   the previous gate even when earlier phase PRs have already merged; a diff
   against current `origin/main` alone may omit that scope. Preserve the gate
   baseline and evidence alongside the current phase's reviewed SHA.
7. Gate exit requires no unresolved validated Critical, High, or Medium finding
   and explicit human approval of the evidence, requested with PR merge
   approval rather than as an additional routine mid-cycle stop. A Low
   finding may be deferred only with a written rationale, owner, and target
   phase; correctness/security/data-loss issues are never deferred merely to
   preserve schedule.
8. Record the reviewed commit SHA, validation results, deferred Low items, and
   human approval in the pull request or implementation-status document before
   Phase work resumes.

Review tools produce evidence and recommendations; they do not themselves grant
authority for destructive smoke tests, real external writes, deployment, merge,
or release. Gate exit and those actions still require the human authority
defined by repository policy.

## Package definition of done

A work package is complete only when:

- every linked normative requirement is implemented or explicitly assigned to a
  later named package;
- migrations/constraints/indexes and rollback notes are present where needed;
- positive, negative-role, invalid-input, concurrency/retry, privacy, and
  accessibility tests appropriate to the package pass;
- no real credentials or generated/local artifacts enter the repository;
- operator/developer documentation and traceability are current;
- the package's narrow tests and standard repository validation pass; and
- incomplete external/destructive behavior remains fail-closed.

## Dependency and change-control rules

- Data-model changes flow through DAT packages and migrations; portals must not
  create private shadow tables for convenience.
- Lifecycle/mode/role/date decisions flow through DOM policies and persistent
  transactional guards; workers and views must not duplicate them ad hoc.
- External work flows through BG durable records and provider adapters; web
  requests never wait for bulk fetch, mail, export, publication, backup, or
  purge.
- Report calculations flow through RPT-02 services; email and export variants do
  not copy formulas.
- Deployment/security changes flow through ARC/OPS plans and must preserve local
  and production topology parity.
- A new requirement discovered after its review gate triggers a scope decision:
  return to that gate if it changes an invariant/security boundary, or assign a
  new reviewed package before dependent phases continue.

## Final completion condition

The project is complete only when all subsystem packages, five review gates,
traceability entries, acceptance scenarios, production-image smoke tests, and
operator documentation are complete. Passing unit tests alone, implementing
only the happy path, or leaving post-campaign/restore/purge behavior as stubs
does not constitute implementation of the specified system.
