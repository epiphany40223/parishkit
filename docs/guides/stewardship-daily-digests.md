# Daily Administrator digests

## Scope and prerequisites

This coherent Phase 4 increment starts on `pr/stewardship-daily-digests` from
PR #46's verified merge `849cc71f`. It implements
[BG-07.02](../tasks/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests)
and its daily-specific BG-07.05 tests under the
[controlling plan](../plans/stewardship/background-processing.md#bg-07-submission-confirmations-and-admin-digests).
The authoritative behavior is the
[daily digest contract](../specs/stewardship/background-processing/spec.md#daily-campaign-digest),
[report parity](../specs/stewardship/reports/spec.md#daily-email-report-parity),
and [immutable input model](../specs/stewardship/data/spec.md#campaign-daily-report-facts).
Weekly digests, full post-close resolution, the complete Phase 5 report UI and
Gate 3 are not completed by this increment.

## Internal acceptance checkpoints

1. Produce accessible daily/recovery content and a compiled inline chart from
   the same immutable participation document and statistics library as reports.
   Add reusable in-memory inline-image MIME support without enabling arbitrary
   file attachments or image markup in Family receipt templates.
2. Freeze coherent report observations and exact fact-build inputs under durable
   digest ownership. Integrate source/fact pins, exact-generation recovery and
   compaction protection without consuming interactive debounce revisions.
3. Materialize bounded ordinary/recovery work through existing schedule and
   immutable replacement lineage. Never release a truncated missed-day group.
   Route separate messages to current exact-address Admins, enforcing Testing,
   current authority, pause, restore and post-close gates.
4. Reuse fenced provider-attempt ownership, truthful uncertainty and explicit
   recovery. Aggregate completion cannot claim success while any required
   recipient result remains unresolved. Preserve pinned report-link parity and
   integrate Testing cleanup and Admin observability.
5. Verify pure calculations/MIME, real-role PostgreSQL admission and races,
   fake-provider commit order, responsive/accessibility behavior, current
   fresh-install schema and container isolation. Run full coverage and three
   successful dual-source review/fix rounds, exact-head CI/DCO and the normal
   protected merge queue before advancing.

## Execution evidence

Implementation is in progress. The checkpoints above are targets, not completion
claims; BG-07.02 remains unchecked. No provider credentials, real delivery,
deployment, release or retained database deletion is authorized.

### Rendering checkpoint

The detached daily/recovery renderer reuses participation charts and current
statistics, rejects mixed observation cutoffs, and includes an accessible daily
table and protected snapshot-link identity. Shared MIME supports bounded
in-memory raster images without filesystem attachments. Admin digest templates
accept only public campaign substitutions; individual routing preserves the
intended Admin while sending Testing mail only to the configured test address.

Validation: 212 focused report/MIME/statistics/coverage checks and 236
content/routing/editor checks passed. The credential-free baseline passed
6,283 tests, with 4,288 database/browser/runtime cases intentionally skipped
and two existing Redis-client deprecation warnings, in 58.67 seconds. Ruff
check and format validation passed. Durable ownership, the linked report route,
worker scheduling/delivery, integration validation and review gates are still
in progress; these results do not claim a functioning scheduled digest yet.

### Private transport checkpoint

The one-Admin chart adapter now shares the established SMTP and private-pipe
transport while retaining a distinct closed payload. It permits only the
compiled inline PNG, validates HTML and image bounds, and preserves unknown
acceptance without automatic resend. Family input restrictions and the smaller
Family/readiness transport limits remain unchanged.

Validation: 252 focused digest/Family/transport/report checks passed, including
the actual isolated helper with deliberately invalid synthetic credentials and
no Django configuration. The full credential-free baseline passed 6,345 tests
with 4,288 profile skips and the same two deprecation warnings in 58.58 seconds.
This adapter has no scheduled execution authority yet; database ownership and
runtime integration remain the next checkpoint.

### Durable input and complete-coverage checkpoint

Daily preparation now has an opaque task root and bounded, fenced discovery and
coverage pages. Its first executed page freezes the recovery cutoff, so a delayed
queued hint includes intervening missed days without allowing subsequent pages
to chase an endlessly growing range. SQL independently rejects premature date
exhaustion, incomplete coverage, stale claims and phase jumps. Ordinary recovery
retains earlier aggregates through immutable occurrence and replacement edges;
Testing dates cannot enter the default Production coverage reader.

The worker captures one coherent statistics observation and its source pin in
the same transaction, then uses the shared exact fact builder without consuming
an interactive debounce window. Compaction respects the retained input even
before a fact pin exists. Compilation retains the exact chart bytes, content,
Admin selection and fact pin atomically before exposing the fanout phase. A
failed older task does not permanently prevent new daily obligations.

Validation: 29 PostgreSQL schema/ownership/capture/build checks and 47 existing
catch-up, exact-export, fact-recovery and recurring-schedule regressions passed.
Five additional recovery tests passed for delayed first execution, raw shortcut
denial, exact SQL fencing, nested aggregate lineage and new work after failure.
The credential-free baseline passed 6,345 tests with 4,305 profile skips and the
same two deprecation warnings in 59.47 seconds. Django's model-state check
reported no changes. This remains an internal checkpoint: production scheduler
registration, recipient fanout/dispatch, aggregate delivery resolution, the
authorized report route, Testing cleanup and the final review gates are not yet
complete. BG-07.02 remains unchecked; no scheduled digest delivery is claimed.

#### Fresh-install schema audit

Independent empty PostgreSQL installations compared the committed `849cc71f`
baseline with this checkpoint. No preexisting relation, column, constraint,
index, trigger or row policy changed or disappeared. Four new ownership tables
add 60 columns, 83 constraints, 24 indexes, 12 functions and 11 triggers.
Five existing function bodies change only to admit the fenced daily owner,
protect its exact inputs, and permit terminal non-interactive generation
recovery. Their actual installed definitions were compared before updating the
strict catalog fingerprint. Model declarations match the installed schema.
No retained development database was upgraded, reset or deleted.

### Testing cleanup checkpoint

The closed cleanup inventory now includes Testing digest recipients, compiled
content, private observations and their fact/source pins. Dependency-aware
batches remove child records first and delete an observation with its source
pin as an indivisible two-record unit. Shared live report facts are not Testing
data and remain available to their other owners. Direct deletion still fails;
only the journaled cleanup checkpoint can remove its exact inventoried records.
Opaque preparation history remains so stale workers can cancel safely after
the private data and selected occurrence have gone. Activation independently
rejects retained Testing observations.

Validation: 13 real PostgreSQL digest/batch checks, 37 existing inventory,
request, worker and bounded-scan regressions, and 30 schema/planning/recovery
checks passed. The credential-free baseline passed 6,345 tests with 4,311 profile
skips and the same two deprecation warnings in 60.05 seconds. Django reports no
model state changes; Ruff check and format validation passed. This remains an
internal checkpoint, not scheduled delivery completion.

An independent fresh-install comparison against `a7002ed` found only the expanded
cleanup-category check, one new private immutability function, six changed
function bodies and four added/three changed cleanup triggers. All relations,
columns, indexes, row policies, owners and grants remain unchanged. The actual
installed function deltas were inspected before updating the catalog fixture;
existing development databases were preserved.

### Preparation and recipient-intent checkpoint

The provider-free handler now advances the entire finite preparation under a
maintained task lease. Shared-generation waits retain the original observation;
fact unavailability retries within the task budget. Individual Admin outbox,
render, task and recipient records commit as one unit in bounded pages. A
deferred SQL check rejects messages without their recipient owner. SQL also
rejects detached sender, recipient, Testing route and required report content.
The SQL content proof binds the compiled report suffix, not arbitrary authored
introductory prose. The closed Python renderer validates that prose and adds
Testing labels; MAIL independently rerenders current routing before submission.
Retries preserve previous recipient bindings and rendered messages.

Unrelated configuration edits no longer strand existing work: preparation
follows its unchanged campaign dates/timezone and schedule revision, while
capture uses the current configuration once and subsequently keeps its original
facts. Public campaign substitutions are shared with Family mail without
changing the latter's private placeholders or credential behavior.

Validation: 13 focused worker/fanout checks, 10 worker/configuration/retry-order
checks, 31 existing Family preparation/guard regressions and 17 schema checks
passed. The credential-free baseline passed 6,345 tests with 4,326 profile skips
and two existing warnings in 60.83 seconds. Ruff and Django model-state checks
passed. Independent fresh schemas against `46063a3` show only two added
functions, five changed function bodies and one new deferred constraint/trigger;
all other catalog objects and grants remain unchanged.

These handlers are not registered for runtime execution yet. Multi-Admin
schedule reconciliation, accepted-delivery coverage, provider dispatch,
authorized report links, Admin retry UI and final validation/reviews remain
required before BG-07.02 can be marked complete or this PR can be delivered.

### Schedule replacement and partial-delivery checkpoint

Schedule previews and reconciliation now account for every individually
addressed digest message and its task. An unresolved or in-flight Admin message
blocks schedule replacement; proven-unsent siblings are cancelled atomically,
and accepted siblings retain their original history. Ordinary daily preparation
forwards cancelled aggregate coverage through the same immutable lineage used
by activation catch-up, including after that activation demand has completed.
The scheduler can recover covered dates even when no new original slot exists.
The report cannot advance to capture while predecessor coverage remains.

An Admin whose previously accepted messages cover every required date gets a
durable reference to those messages, not a duplicate send. An incomplete date
range, another Admin's acceptance, or an uncertain outcome cannot satisfy this
proof. Testing replacement edges belong to the closed cleanup inventory and
are deleted before their occurrence endpoints; direct deletion remains denied.

Validation: 10 schedule/partial-acceptance/cleanup tests, 31 planning/recovery/
activation tests and 55 existing schedule/cleanup/task/fanout regressions passed.
The credential-free suite passed 6,345 tests with 4,337 profile skips and the two
existing deprecation warnings in 65.36 seconds. Independent fresh schemas
against `a822387` showed the intended recipient coverage and preparation-owner
columns, nullable alternatives guarded by exact ownership, expanded cleanup
category, one private multi-recipient view, two updated summary views, five new
functions, eight changed function bodies and the replacement cleanup triggers.
No indexes or row policies changed. Installed function differences were reviewed
before updating the strict fingerprint. Existing development databases were
neither upgraded nor deleted. Runtime delivery, report links, retry UI and the
final review gates are still required; BG-07.02 remains unchecked.

### Provider dispatch and aggregate completion checkpoint

Individually addressed digests now use the maintained MAIL claim, isolated
chart transport and shared bounded SMTP outcome protocol. Current Admin
membership, revision, Testing epoch, pause and restore gates are checked before
submission; no Family credential is decrypted or Family refusal propagated.
Provider uncertainty remains explicit and cannot trigger an automatic resend.

Completion requires every member of the immutable recipient cohort, including
independently proven earlier acceptance. One successful sibling cannot complete
a multi-Admin digest. A late accepted observation can commit after its provider
task fails; a separate metadata-only task finishes the aggregate under a fresh
claim without rewriting that failure. Crash recovery does not duplicate the
fulfillment. Fully covered replacements finish without sending new messages.

Validation: 32 dispatch/worker/finalization PostgreSQL tests, 78 shared-mail and
schedule regressions, and 28 schedule/schema checks passed. The credential-free
suite passed 6,345 tests with 4,370 profile skips and two existing deprecation
warnings in 63.69 seconds. Ruff check and format validation passed. Independent
fresh schemas against `0f58942` show one private metadata view, two changed
schedule views, six new/four changed functions and three completion triggers;
no existing columns, indexes or row policies changed. Installed function deltas
were inspected before updating the strict catalog fingerprint. Both Docker
build contexts include the new SQL asset. No retained database was modified.

This remains an internal checkpoint. Admin reconciliation/retry controls, the
authorized report route, runtime registration, integration validation and three
review rounds still precede completion of BG-07.02 and PR delivery.

### Runtime and Admin recovery checkpoint

The runtime now registers daily preparation and metadata finalization after
recurring-slot discovery. Each producer is isolated so one owner's failure
cannot block unrelated work; scheduler registrations explicitly refuse worker
execution. Existing maintained MAIL execution handles the resulting messages.

Admin delivery pages include daily messages. External acceptance preserves the
failed provider task; an explicitly acknowledged resend or definitive-failure
retry creates a numbered child task. Web can request only a fixed, non-sendable
seed, never author report content or load Family keys. MAIL must rerender the
original facts under current routing before another submission. Failed daily
preparation/finalization has a CSRF-protected retry form that rejects stale
selected runs and reloads current Admin authority, including on replay.

Validation: nine daily-resolution tests, 73 existing Family/receipt-resolution
and UI checks, 22 retry/schema tests and 214 runtime/grant checks passed. The
credential-free suite passed 6,361 tests with 4,384 profile skips and two existing
warnings in 94.31 seconds. Ruff passed. Independent fresh schemas against
`f0d4901` found only one new private seed function and three changed resolution
functions; their installed definitions were inspected before updating the
fingerprint. No existing catalog shape, triggers or policies changed.

The protected exact-report link, complete integration/coverage validation and
three review rounds remain open. BG-07.02 is not yet complete.

### Pinned report and interactive chart checkpoint

Digest links now open a current-authorized Staff/Admin snapshot page. Its
statistics and table use the same immutable inputs, fact generation and exact
formatting as the email. The image and bounded PNG download serve the original
retained bytes, never today's chart. Hover, touch and keyboard date inspection
share the renderer's axis geometry; the full table and image remain available
without JavaScript. Timestamp display follows the browser timezone while
campaign dates remain in the retained campaign timezone.

HTML, image and download reads retain campaign/purge guards through response
closure, recheck current session/roles and audit opaque report identity without
logging values. Downloads use the separately authenticated bounded read pool.
Historical report identity is distinct from current audit attribution after a
configuration edit. Outgoing-mail pages distinguish Administrator reports from
Family messages instead of formatting an absent Family DUID. Preparation tasks
expose coarse phases and heartbeat evidence without inventing progress totals.

Validation: five real-session PostgreSQL report/download checks, 20 daily-work
and runtime SQL regressions, 145 rendering/build/grant checks and nine browser
checks passed. Browser checks cover Chromium, Firefox and WebKit at mobile and
desktop widths, keyboard/hover inspection, no-script fallback and automated
accessibility. Source promotion leaves the displayed inputs and PNG unchanged;
Ministry-only and anonymous users cannot read them. Ruff passed and Django
reports no model changes. Complete coverage, container validation and three
dual-source reviews remain required before BG-07.02 completion.

### Preliminary review and correction checkpoint

Pika session `20260917-072257-9262a8` reviewed `849cc71f..931a103`.
All four Claude shards completed, with 30 raw findings: one High, seven Medium,
and 22 Low. Finalization reported `codex-reviewer: result artifact missing —
codex produced no structured output`. This degraded attempt **does not count**
toward the three required dual-source rounds. A new complete-diff review is
required. The following IDs identify shard number and raw finding position,
including below-cutoff findings; technical triage uses the delegated autonomous
delivery procedure in the controlling plan.

| ID | Raw severity | Disposition and evidence |
| --- | --- | --- |
| 1.1 | Medium | Fixed: guarded revoked-recipient cancellation resolves only that recipient obligation; mixed cohorts retain actual acceptance, wholly withdrawn cohorts retain explicit empty fulfillment. PostgreSQL tests cover both completion orders, whole-cohort revocation and forged waiver denial. |
| 1.2 | Low | Fixed: closed translated labels distinguish daily reports and submission receipts; page tests reject the unknown-status fallback. |
| 1.3 | Low | Deferred simplification: retry-view imports and repeated stale checks are harmless defense in depth; `retry_digest` independently checks actor, parent and root under the work transaction. |
| 1.4 | Low | Fixed: the private JSON budget accounts for six-byte body escapes; maximum valid escaped text round-trips through the actual decoder. |
| 1.5 | Low | Deferred admission-message refinement: the final SQL predicate still independently rejects mismatched scope before SMTP; ordinary restore, catch-up, pause and lifecycle holds are already checked in `_planning_scope`. A stale configuration must never bypass SQL admission. |
| 1.6 | Low | Rejected unsupported performance claim: both replacement relations have replacement-ID indexes (`schedule_recovery_successor` and the occurrence replacement index); no measured regression establishes the alleged full scan. |
| 2.1 | High | Fixed: persisted task binding is separate from mutable completion proof. Expiry can fence its owner; invalid abandoned proof fails visibly; explicit failure transitions remain available. Regression tests cover these cases and forged binding denial. |
| 2.2 | Medium | Rejected unreachable configured state: complete login-policy validation requires a specific-address Administrator, and the last-Admin guard rejects its removal. `retain_daily_content` reads this applied projection under the same serialized work transaction. Post-generation revocation is separately fixed by 1.1. |
| 2.3 | Medium | Duplicate of 1.1; fixed by the same guarded cohort resolution. |
| 2.4 | Medium | Already handled by the specified bounded retry policy and explicit Admin retry: a failed builder remains visibly failed rather than silently reassigning owned coverage. The retry endpoint preserves that root and its pinned observation; daily task/retry tests cover dependency waiting and successful explicit retry. |
| 2.5 | Low | Fixed presentation clarification: the protected table explicitly contains the whole pinned chart range; email rows highlight the covered mailing dates without changing any values. |
| 2.6 | Low | Deferred small refactor: duplicate retry constants/formulas have identical five-attempt behavior; no differing result or defect was identified. |
| 2.7 | Low | Deferred exception-style consistency: the public producer currently documents/tests `ValueError` for a non-UUID identity; both producer constructors reject invalid identities before work. |
| 2.8 | Low | Fixed: guarded authorization verifies ready content before reading it, returning temporary unavailability for an unfinished retained snapshot. |
| 2.9 | Low | Already handled at runtime configuration: `deployment._origin` calls `_host`, which rejects quotes and invalid DNS characters before any worker is launched. |
| 3.1 | Medium | Duplicate of 1.1; fixed. |
| 3.2 | Medium | Fixed coverage gap: cleanup now exercises fanned-out failed and delivered messages, recipient rows and renders. The existing repeated-replacement cleanup test additionally covers accepted-message references and replacement edges, with independent SQL/Python inventory equality. |
| 3.3 | Low | Deferred redundant SQL hardening: `cover_dates` selects only pending, unexcluded rows under the serialized work lock; final MAIL admission independently rejects other states. No current executable path binds the alleged terminal occurrence. |
| 3.4 | Low | Already protected before sending: the compiled initial renderer supplies Testing labels, while MAIL rerenders and SQL revalidates the exact current labels at prepare/submit. A fanout-only assertion would be defense in depth, not send authority. |
| 3.5 | Low | Rejected unreachable configured state: admitted Testing runtime requires its nonempty validated Testing recipient; retry seed creation also requires current admitted delivery scope. |
| 3.6 | Low | Deferred error-code consistency: malformed JSON fails closed as a database error and is sanitized by the caller; no caller relies on the proposed narrower SQLSTATE. |
| 3.7 | Low | Rejected duplicate-delivery claim: existing preparations retain exclusive occurrence ownership and later discovery excludes those IDs. A failed older root and a newer root cover separate slots; explicit retry does not steal or repeat the newer root's coverage. |
| 3.8 | Low | Duplicate of 1.6; both replacement indexes already exist. |
| 3.9 | Low | Fixed: cleanup tests use the defining `source.snapshot_models` import. |
| 4.1 | Medium | Fixed: restricted Web assertions require permission-denied errors. The replacement-edge deletion test intentionally uses the schema owner, so it instead requires the exact immutable-cleanup guard error, not an unrelated database failure. |
| 4.2 | Low | Fixed: the subprocess asserts no ORM, Matplotlib or report-renderer modules were loaded. Shared stateless Django validators are intentionally permitted; the test name/docstring now state the actual isolation boundary. |
| 4.3 | Low | Fixed: current authentication/authorization precedes query validation. |
| 4.4 | Low | Fixed: Staff/Ministry and anonymous checks cover HTML, inline PNG and restricted-pool downloads. |
| 4.5 | Low | Fixed: removed the redundant 50-ms wait; the fixture already waits past the actual PostgreSQL lease and provider deadline. |
| 4.6 | Low | Fixed: anonymous requests require the deterministic 403 response on every representation, including malformed queries. |

The recipient-resolution clarification is specified in the
[Administrator digest contract](../specs/stewardship/background-processing/spec.md#administrator-digests).
Empty fulfillment is explicit and distinct from delivered fulfillment; it never
changes a cancelled OutboxMessage into an accepted one. The fresh-install schema
audit against `931a103` found the intended completion-view disposition column,
fulfillment constraint, resolved-predicate rename, four changed functions and
one changed completion trigger, with unchanged indexes and policies. Installed
function definitions were inspected before updating the catalog fingerprint.

At `931a103`, the credential-free suite passed 6,361 tests, the image built and
all 12 container-isolation checks passed. Chromium and WebKit each completed all
268 browser cases. The concurrently run Firefox partition and eight local
database coverage partitions exceeded their time budgets; they are **not**
passing evidence and no coverage percentage is claimed. Isolated reruns and
final-head validation remain required, as do three completed dual-source rounds.

Correction checks: the credential-free suite passed 6,368 tests with 4,406
explicit-profile skips and two existing warnings in 79.28 seconds. A separate
133-test pure digest/transport/grant batch passed, as did the final 32-test
report-access, worker and schema batch. Revocation, cleanup, explicit resolution
and finalization regression batches passed their corrected cases; broader
same-head validation is still running. Ruff, formatting, changed-document
Markdown validation and Django model-state checks passed.

### Complete dual-source round 1

Pika session `20260917-080730-7320d4` reviewed the full increment from
`849cc71f6c8a6478677749f8b2e5dce8a06334ff` to
`479734180d76454b742ac6d9db4db2ac44defeef` (tree
`fb83bc027290766468ea6dfc446e4a773d0cc1e0`). All five Claude shards and the
Pika-launched Codex reviewer completed, without degradation, salvage or verdict
mismatch. Raw findings: three Medium and 27 Low, none High/Critical. Pika
retained two Medium above its reporting threshold. The following dispositions
include every raw finding; routine decisions follow the delegated delivery
cycle. `C1` is Codex; other identifiers are Claude shard/finding numbers.

| Finding | Raw severity | Disposition and evidence |
| --- | --- | --- |
| 1.1 | Low | Fixed: reject the daily allocation marker in digest authoring, configuration and assembled content; the existing parameterized tests now include it. |
| 1.2 | Low | Rejected authority escalation: these are invoker functions, not security-definer entry points. Underlying grants still apply; blanket PUBLIC revocation would break existing admitted calls without conferring a missing boundary. |
| 1.3 | Low | Fixed the stale exact-builder comment: terminal exact and digest owners may be recovered; ordinary frozen debounce demands remain separately owned. |
| 1.4 | Low | Fixed shared helper errors to use delivery-neutral wording. |
| 1.5 | Low | Fixed the integer watermark label to “Submission sequence cutoff.” |
| 2.1 | Medium | Rejected duplicate/stuck retry claim: already owned occurrences are excluded and an unselected obsolete root finishes empty. New real-worker regressions cover failure in both dates and cover, another root finishing, then explicit retry of the original; no duplicate outbox is created. |
| 2.2 | Low | Already handled: `validate_campaign_sections` rejects a second digest of the same kind for a campaign before projecting current schedule revisions. |
| 2.3 | Low | Intentional dependency boundary: an ordinary materialization's frozen demand cannot be stolen by an exact/digest owner. Its own recovery must release it; explicit retry remains available afterward. |
| 2.4 | Low | Deferred optional test expansion: shared retry storage already binds command identity and the daily service restricts task types; current real-session tests cover CSRF, replay, stale selection, current roles and superseded revisions. |
| 3.1 | Medium | Fixed terminal audit error masking. Expected database/storage failures emit a typed, redacted operational signal, preserve the original HTTP result and do not claim a committed terminal audit. Six PostgreSQL cases cover denial, unavailability and stream closure with both exception classes. |
| 3.2 | Medium | Fixed the correlated TaskRun scan. Resolve a deduplicated preparation/child root set before indexed lookup and aggregate distinct finalizer roots once. Synthetic PostgreSQL EXPLAIN ANALYZE with 100 preparations and 100,000 tasks returned identical totals: 1,472.465 ms before, 20.912 ms after. This is diagnostic evidence, not a production latency guarantee. |
| 3.3 | Low | Fixed: finalizer retries now participate in the real database work-before-root lock-order regression. |
| 3.4 | Low | Fixed the controls null guard; all three browser engines test a missing controls container without page errors. |
| 3.5 | Low | Deferred small query deduplication: both current paths use the same hold states. Discovery's candidate slots and coverage's database subqueries have different bounded query shapes; no current behavior divergence was found. |
| 3.6 | Low | Fixed the shared outbox guard's Family-only error wording. |
| 3.7 | Low | Fixed the cross-helper contract by exposing `decode_envelope`; each entry point still chooses its own closed mail class and size limit. |
| 3.8 | Low | Fixed the unnecessary function-local finalizer imports. |
| 4.1 | Low | Fixed defense in depth: SQL now independently refuses to queue a recipient whose complete date coverage was already accepted. A regression suppresses only Python's coverage query and proves the SQL guard rolls the new send back. |
| 4.2 | Low | Deferred lineage-query consolidation: the bounded Python reader and independent SQL equality proof currently agree, including nested/replaced/rehearsal cases. Replacing pagination with a whole JSON aggregate is not necessary for this increment. |
| 4.3 | Low | Deferred calendar-helper consolidation, not the proposed NULL shortcut: a skipped local day cannot justify ignoring later due dates. Existing discovery/guard tests preserve bounded complete coverage; no current supported campaign regression was demonstrated. |
| 4.4 | Low | Rejected specification divergence: ordinary Testing rehearsal work is explicitly limited to the draft campaign's resolved interval. Production-only post-close work is intentional; preview/readiness remains separately available outside that interval. |
| 4.5 | Low | Clarified the documented boundary: SQL binds the required compiled suffix and envelope, not arbitrary authored introductory prose. The closed Python renderer validates prose; dispatch independently rerenders current routing and Testing labels. |
| 4.6 | Low | Fixed tampered-envelope tests to assert the exact preparation-ownership rejection; the omitted-recipient test also names its exact completion guard. |
| 4.7 | Low | Fixed the grant comment: Web may read per-message Admin addresses for delivery observability, but not the ready row's recipient snapshot or compiled prose. |
| 5.1 | Low | Deferred to Phase 6 restore inventory/release, not dismissed: a restored uncertainty inventory must cover aggregate lineage dates as well as recovery slots before releasing the global restore gate. That restore workflow is not implemented by BG-07.02; existing global restore admission already blocks all normal digest work while review is required. |
| 5.2 | Low | Fixed daily retry guard duplication: the view delegates replay/latest-run/Admin binding to `retry_digest`; the Family preparation-identity route retains its existing check. |
| 5.3 | Low | Deferred minor repeated metadata lookups: every lookup is bounded by exact IDs; retaining fresh admission reads is preferable to broadening this correction into cached authorization state. |
| 5.4 | Low | Deferred redundant declarative owner check/index: the immutable insertion trigger already rejects zero/two owners, and lineage uses the indexed predecessor/replacement edges, not preparation lookup. |
| 5.5 | Low | Fixed the broad `None` assertion to check the actual Family DUID heading/paragraph elements. |
| C1 | Low | Fixed together with 3.1: audit failure no longer escapes stream closure or overwrites sanitized admission responses; the closure marks finalization only after the audit commits. |

Correction checks: 83 PostgreSQL report/TaskRun checks, 62 PostgreSQL
fanout/schema/schedule-reconciliation checks, 265 focused Python tests, and
12 chart tests across Chromium, Firefox and WebKit passed. Fresh installed
catalog comparison against `4797341` changed only the daily work view and two
reviewed trigger functions; columns, constraints, indexes, policies and triggers
were unchanged. Existing databases were not upgraded or deleted.

The complete 16-part coverage run uses a separate checkout pinned to `4797341`;
it is not evidence for later correction code. One partition lost its connection
after the disposable PostgreSQL checkpointer was killed by signal 9. Eight idle
prior-run disposable tmpfs databases were stopped to free roughly 2 GB, and the
affected partition is being repeated on a fresh cluster with its original
failure artifacts retained. Neither failed/incomplete coverage nor the earlier
degraded review counts as successful validation.

The full run also exposed an incomplete schema-wide test inventory: custom
digest guards were not registered in the global mutable/immutable model audit.
The inventory now verifies the preparation's enabled row trigger, exact
immutable identity tuple, monotonic version and database timestamp, plus each
private digest table's enabled immutable trigger and exact cleanup category.
All 39 schema-wide storage checks pass, including the recovery replacement's
guarded cleanup contract. This does not exempt a model or weaken a guard.
The complete Python suite passes 6,374 tests (4,419 expected profile skips).
Because that correction changes the tested tree, final aggregate coverage must
use a newly pinned checkout.

### Complete dual-source round 2

Pika session `20260917-084625-427aea` reviewed corrections from `4797341` to
`5eb388feebcee588eaeb41a1b956c3d4d783c9be` (tree
`883d298b471464753e721ac8fde1c84ac783ae07`), including surrounding ownership,
delivery and report context. Claude reviewed all 25 manifest files and Codex
completed successfully. No degradation, failed agents, salvage or verdict
mismatch occurred. There was one raw Medium and seven Low findings, none
High/Critical; the Medium documentation issue was the only above-cutoff item.

| Finding | Raw severity | Disposition and evidence |
| --- | --- | --- |
| Claude 1 | Medium | Fixed: restore preliminary finding 4.6 and its validation narrative to the preliminary section, before the complete round-1 section. Rewrap the interrupted sentence. |
| Claude 2 | Low | Fixed: a replay command selecting the wrong parent again raises `StaleRecordError`, preserving HTTP 409. A real-session CSRF-protected view regression now replays the original command against its child run. Different-Admin identity conflicts retain their existing invalid-command behavior. |
| Claude 3 | Low | Clarified the performance bound: finalizers require one TaskRun scan per view read, not one scan per preparation. The benchmark and correction never claim a constant-time single-occurrence lookup; further indexing is deferred unless measurement justifies it. |
| Claude 4 | Low | Rejected unreachable normal-runtime configuration race: applied runtime cannot be deleted, its selected configuration cannot become null, and retained configuration/parish foreign keys remain protected. The terminal audit does not call a rate limiter or reload YAML. Expected database/storage failures are handled; arbitrary programming errors are not silently swallowed. |
| Claude 5 | Low | Fixed the test to omit the controls marker in the original HTTP response before the script first executes, preserving actual CSP. All engines verify no error, hidden controls and no slider enhancement. |
| Claude 6 | Low | Deferred module rearrangement: the digest adapter already shares the stateless SMTP implementation and closed outcome types. Importing a Family mail class grants no Family data/key access; the subprocess isolation test verifies no ORM, chart renderer or report module is imported. This correction makes the existing shared parser contract explicit, not a claim of disjoint module graphs. |
| Claude 7 | Low | Pre-existing dependency: `_status` was already imported inside the function and throughout related workers. Moving it to the header does not introduce a new dependency; a project-wide naming refactor is outside this correction. |
| Codex 1 | Low | Duplicate of Claude 1; fixed by restoring the complete preliminary table and its associated evidence before round 1. |

At the reviewed head, all 119 daily PostgreSQL tests passed in 371.97 seconds;
the corrected image built as
`sha256:cb000654a33da51d5eccb8648021e44ed3ce3abaa580406bf304166c1e4281a3`,
and all 12 real container mount-isolation tests passed. All 24 retry/fresh-schema
PostgreSQL checks passed after the correction. The revised first-load chart
test passed all 12 Chromium/Firefox/WebKit cases. Full same-source
coverage was started in a separate checkout pinned to `5eb388f`; final-head
validation must cover all later corrections, including retry status and the
first-load browser regression, not just that pinned run.

### Complete dual-source round 3 and full-suite correction

Pika session `20260917-085447-5810c1` reviewed `5eb388f` through
`a000b216303121ebd917bbd7ec483c782099f28e` (tree
`09b5a39b4a96cdca47411900320b6387aed025bb`). Claude covered all five changed
files and surrounding implementation; Codex approved with no findings. All
reviewers completed successfully with no degradation or verdict mismatch.
Claude's three raw Low findings were below Pika's threshold; none were
Medium/High/Critical.

| Finding | Raw severity | Disposition and evidence |
| --- | --- | --- |
| Claude 1 | Low | Fixed: the first-load missing-controls case now requires the actual script response to succeed, rejects errors exposed through Playwright's page/console channels, and requires exactly one deliberately unmarked controls container. This negative case does not independently prove script execution on every engine; the separate positive control-page tests require functional pointer/keyboard enhancement under the same CSP. |
| Claude 2 | Low | Fixed: the pinned-run caveat now covers every subsequent correction, including the browser test, rather than mentioning only retry status. |
| Claude 3 | Low | Fixed: a second current specific-address Admin cannot adopt another Admin's retry command. PostgreSQL regressions cover both original-parent invalid-command and wrong-parent stale-conflict outcomes without allocating a new run. |

During full validation, the new `report_audit_failed` operational event was
found missing from the SQL event allowlist. Both independent registry-parity
tests caught this. The fresh-install constraint now includes that exact closed
event, without permitting arbitrary text. Independent fresh catalogs against
`a000b21` differ only in `operational_event_safe`; its actual installed definition
was inspected before updating the fingerprint. All 75 retry/source-failure/
registry regression checks pass, as do all 12 browser cases. The baseline Python
profile at `a000b21`, before the SQL allowlist and two additional PostgreSQL
retry cases in `c4bbc9b`, passed 6,374 tests with 4,419 profile skips in 81.31
seconds. Those profile skips exclude the PostgreSQL checks that found the
allowlist failure; this is not a claim that complete validation passed.

The incomplete `5eb388f` aggregate run is not passing evidence: its known
registry failures invalidated the run, so remaining owned test processes were
stopped rather than spending further time on superseded source. Logs remain
available. This narrow schema correction receives an additional focused
dual-source review without resetting the three completed rounds; complete
coverage is rerun against a fresh pinned corrected checkout.

### Additional focused dual-source round 4

Pika session `20260917-090156-db91da` reviewed `a000b21` through
`c4bbc9bf2ea58a9ca64a542d917b90a013ac1219` (tree
`6db18ed57f1ccac034df8cfa56c0120339c67a63`). Both reviewers completed all five
changed files and surrounding context without degradation or verdict mismatch.
Codex approved with no findings. Claude reported three Low findings, none
Medium/High/Critical; Pika's finalized action was approve. All raw findings are
accounted for below, including the below-threshold notes.

| Finding | Raw severity | Disposition and evidence |
| --- | --- | --- |
| Claude 1 | Low | Clarified the baseline-profile evidence to name `a000b21` and its actual log duration/counts, distinguish its PostgreSQL skips, and explicitly exclude the later added cases. The counts came from a rerun, not a copied result; final aggregate validation remains separate. |
| Claude 2 | Low | Narrowed the round-3 wording to page/console errors actually exposed by each engine. The missing-controls case tests safe static fallback, while the separate positive control-page cases prove functional execution under the actual CSP. No production-only instrumentation was added solely to test a defensive early return. |
| Claude 3 | Low | Deferred cosmetic SQL wrapping: this exact closed literal and installed constraint are already verified by both registry-parity tests and an independent fresh-schema audit. The nearby long registry lists have no enforced line-width rule; no functional or readability-sensitive ambiguity is introduced. |

Complete eight-part coverage is running against a clean, separate checkout at
`c4bbc9b`, using four exclusive PostgreSQL/Valkey slots in two waves to limit
memory pressure. Prior failed or incomplete aggregate runs remain excluded.

### Final validation environment and history consolidation

The first `c4bbc9b` coverage attempt was interrupted by confirmed macOS
clamshell sleep beginning at 09:16 Eastern on September 17, followed by
maintenance wakes until full wake at 10:05. Failed checks reported expired
leases, authentication and interval constraints; those failures remain recorded,
not waived. Complete affected partitions are repeated unchanged on fresh
disposable PostgreSQL services, with a process-scoped idle-sleep assertion.
This assertion does not prevent lid-close sleep. Successful same-source shard
receipts remain eligible only through the normal complete-partition verifier.
Stopped services contained only synthetic temporary test databases; retained
development and fresh-schema audit databases were not reset or deleted.

The nine review-correction commits were consolidated into `8f7cba7`, whose tree
is exactly `6db18ed57f1ccac034df8cfa56c0120339c67a63`, matching the reviewed
`c4bbc9b` tree. The subsequent review-documentation commit is now `326c695`;
its tree `4235f9cf6163b6e04b22a77fd7d5731940a7e94d` matches pre-squash
`7909c28` exactly. This reduces 20 commits to 12 logical signed-off commits
without changing implementation or tests. Original history is retained in
`backup/stewardship-daily-pre-squash-20260917`. The remote update used an exact
expected-head `--force-with-lease`, not an unconditional force push.

### Generic-plan integration correction

The unchanged awake rerun passed complete partitions 3 and 4, but partition 1
exposed an independent defect: an unrelated MAIL task retry could cause the
production-cleanup trigger to plan a read of `stewardship_production_request`.
MAIL correctly lacks that privilege. The isolated default-plan case passed;
forcing generic plans reproduced the same class of problem earlier in optional
outbox pause-binding validation. This is a code defect, not another sleep
failure, and the incomplete aggregate is not accepted as validation.

The two trigger functions now branch in PL/pgSQL before planning those optional
domain reads. Actual cleanup completion and non-null pause bindings retain
their existing checks. No new grant, security-definer conversion, clock
override or weakened ownership fence was introduced. All 17 digest-resolution
cases pass with explicitly forced custom/generic plans, including a direct
assertion that MAIL still cannot read the production-request table.

Independent fresh catalogs based on `326c695` differ only in the installed
definitions of `stewardship_production_task_actor_v1()` and
`stewardship_outbox_state_v1()`. Their actual before/after definitions were
inspected before updating the function fingerprint. Superseded coverage
processes were stopped with their logs retained; complete coverage must be
rerun against the corrected source after focused validation and review.

The existing production journal, cleanup recovery and outbox-boundary suite
also passed all 77 cases under `force_generic_plan` in 96.33 seconds. The
complete browser profiles passed 269 cases each: Chromium in 150.79 seconds,
Firefox in 572.64 seconds and WebKit in 250.64 seconds. These browser runs
exercise the unchanged final UI; full corrected-source aggregate coverage and
exact-head CI remain outstanding.
