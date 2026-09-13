# Stewardship Phase 2 execution checkpoints

The [controlling plan](../plans/stewardship/overall.md#phase-2-source-truth-initial-setup-and-campaign-preparation)
owns scope and dependency order. The [coordinating checklist](../tasks/stewardship/overall.md#phase-2-source-setup-and-preparation)
links every owning task list; this guide records implementation evidence rather
than redefining behavior.

## Admission and branch

The owner authorized continuation once PR #21 merged. It merged on September 11,
2026 at 13:54:50 UTC as `48be3666f0c89cc15586cb67465cd1ba0504203c`, after all
[merge-queue CI checks](https://github.com/epiphany40223/parishkit/actions/runs/34605469838)
passed. Gate 1 is released. Branch `pr/stewardship-phase-2` starts at that
refreshed `origin/main` tip.

## Active checkpoint

The results below describe the pre-consolidation implementation, retained on
`jsq/backup/stewardship-phase-2-before-consolidation-20260913`. The
[consolidation record](stewardship-phase-2-simplification.md) supersedes their
handoff status for the new baseline, test inventory and CI layout. Current
review and full final-head acceptance must pass before merge.

All Phase 2 implementation scope is connected: source storage and full/delta
refresh, restricted background owners, complete original-login setup, selected
financial loading, atomic configured-marker completion, journaled pre-activation
cancellation, and Admin campaign/content/integration/Ministry preparation.
Three independent full-phase reviews have completed with both vendors; the
third returned no High/Critical findings. All retained corrections and focused
regressions are complete through `ea2d5cb`, pushed to the topic branch.

Final integrated acceptance passes: 4,297 baseline tests, all 2,114 PostgreSQL
cases, all 447 browser cases, 59 final-image container/isolation/provisioning
cases and all eight operational Compose scenarios. Scoped coverage is 94.16%
lines and 85.04% branches. The [acceptance index](stewardship-phase-2-acceptance.md) maps each
required demonstration and distinguishes final results from diagnostics. The
[third review record](stewardship-phase-2-full-review-3.md) preserves corrections,
pushbacks and the scheduler mail-admission regression discovered during final
image validation. PR #22 is open and final-head CI is the active work; human merge
approval remains required before Phase 3A. The chronological checkpoints below
record earlier partial progress, not the current scope or phase-exit approval.

## Historical implementation checkpoints

DAT-03 storage was the first package, followed by BG-01. The
first internal checkpoint implements normalized
content-addressed payload tables and snapshot membership maps, server-clock
task/source fencing, bounded staging, database-verified completeness and
relationship evidence, atomic promotion/reconciliation, immutable manifests and
protected reads. Its source-specific PostgreSQL suite passes all 46 tests,
including migration reversal/reapplication; pure canonicalization/lease tests
pass all 33 cases. The complete baseline passes 2,743 tests; Ruff, formatting,
Markdown and migration drift checks pass. At that checkpoint, no Phase 2 task
was complete yet:
retention anchors/services, daily-fact storage, concrete promotion effects and
runtime/refresh/setup consumers still need their owning implementations.

The next internal checkpoint adds deterministic UTC retention anchors, explicit
parent pins, protected-reader/late-pin race handling, bounded source compaction
and permanent count/cutoff evidence. Source promotion and compaction use the
shared typed audit helper without logging source field values. All 63 source
PostgreSQL cases and all 44 pure source cases pass; the final audit-specific
repeat passes 34 PostgreSQL cases. The updated baseline passes 2,754 tests.
DAT-03.01-.05 are complete for their storage scope. Daily-fact storage,
fact-specific tests and concrete runtime consumers remain open, so DAT-03 as a
whole is not yet marked complete.

The third checkpoint completes daily-fact storage: immutable exact-input graph
generations, SQL completeness and provenance checks, non-regressing interactive
pointers, explicit retained-parent/source pins and shared locks through lazy
reads. The input key includes the campaign-local through-date required by the
graph's today bound: midnight cannot reuse yesterday's dates merely because
source and submission versions did not change. Historical facts use permanent
source metadata; current-scope generations pin their reconstructable source.

Durable rebuild demand implements the five-second debounce and thirty-second
cap. Claims freeze their own revision; concurrent events retain an independent
pending window through completion and abandoned-work recovery. Exact exports
use the generation API without consuming interactive demand. Bounded fact
compaction preserves current/building/failed/claimed/pinned/actively read
generations, deletes whole disposable generations atomically, releases only
their source pins and keeps immutable generation-key/count evidence and audit.
Shared task ownership now takes the retry-root lock before its execution row.

All 38 fact PostgreSQL tests pass, including migration reversal/reapplication,
raw-SQL tampering, failed-build recovery, interrupted cleanup, active lazy
readers and both late-pin race orders. The final combined source/fact/TaskRun
regression passes 165 cases; pure fact/lease validation passes 47 cases. The
baseline passes 2,788 tests. These are internal storage APIs, not enabled report
workers: BG-01/BG-05 own concrete runtime admission, source producer wiring and
queue consumers; RPT-02/RPT-03 retain calculation/materializer/UI ownership.
Runtime database grants remain closed until their owning service is admitted.

BG-01 has started with the internal durable hint dispatcher and task scan.
The transport-facing identity is only a TaskRun UUID; its type and handler
are resolved from durable state and the startup-owned registry. Queue isolation
does not authorize work. Claim, heartbeat, progress and verified outcomes repeat
the owning admission check under retry-root/task locks. Execution occurs outside
a transaction; an unexpected exception or return never implies completion or
safe retry. Recovery fences expired owners and applies only an explicit verified
disposition, preserving unresolved work and retry delay.

The bounded keyset scan repeats lost hints without changing durable task state
and advances across held work so an ineligible prefix cannot starve later work.
Nine PostgreSQL tests and sixteen pure tests pass for this initial substrate.
The dispatcher/scanner are not an enabled runtime service. Singleton scheduling,
Celery/Valkey transport, concrete domain admission, worker lifecycle and task
status APIs remain BG-01 work; all BG-01 checklist items remain open.

The next BG-01 checkpoint adds a PostgreSQL session-pinned singleton scheduler
guard and the closed Celery/Valkey transport factory. Reconnects invalidate old
ownership; transport failures advance fair paging without removing durable
work. Celery 5.6.3/Kombu 5.6.2 are validated locally. The factory uses JSON only,
no result backend or remote control, finite transport timeouts and a single
UUID-only task, following the [Celery configuration reference](https://docs.celeryq.dev/en/stable/userguide/configuration.html).
App-local task registration, service-specific default queues and independent
unacknowledged-message keys prevent one service from inheriting another's handler
or consuming its queue by default. Broker passwords remain separate in-memory
options rather than URL components; ambient Celery overrides fail closed.

All 20 dispatcher/scanner/scheduler/consumer PostgreSQL tests pass, as do nine
broker and sixteen dispatcher pure tests. The baseline passes 2,813 tests.
These factories have not yet been enabled by operational runtime: service
mounts, SQL grants, Valkey ACL provisioning, concrete owning admission and
bounded worker heartbeat/shutdown still need integration and container tests.

The broker isolation checkpoint adds a pure queue inventory and explicit
Valkey ACL builders for the scheduler and each consumer identity. The scheduler
may publish but cannot consume or delete queue data; each consumer sees only
its own normal/restore queues and unacknowledged-message keys. None can read
web login counters or administer Valkey. Seven opt-in tests against a fresh
disposable pinned Valkey container pass, exercising real Kombu publication,
receipt, visibility recovery, requeue and acknowledgement plus server-enforced
cross-service denials. Thirty-eight pure broker/dispatcher/ACL tests pass.
This validates the policy and transport, not yet deployment provisioning or
an enabled worker process.

The worker-lifetime checkpoint adds twenty-second renewal on an independent,
short-lived SQL connection, with finite connection/lock/statement timeouts.
Task and optional exact source leases renew atomically; renewal failure blocks
further execution boundaries without inventing a task outcome. Graceful stop
prevents new claims/external units but allows a verified current unit to finish.
Every handler exit drains its renewer. The scheduler retains its actual owning
SQL session across passes and stops publishing the remainder of a page on drain.

Closed process helpers now exercise Celery's real single-execution controller
without gossip, mingle, broker heartbeat, remote control, CLI banners or forked
workers. The stop event is shared with dispatch, and the controller's current
app is scoped to its lifetime. The nine real Valkey tests include actual worker
consumption/acknowledgement and idle SIGTERM shutdown. Thirty PostgreSQL cases
and nineteen new pure lifetime/process cases pass; the full baseline passes
2,837 tests. Deployment still does not launch these helpers: concrete domain
admission, isolated SQL grants/mount provisioning and runtime entry-point wiring
remain in progress before BG-01 can be marked complete.

The admission checkpoint adds fresh transactional campaign checks using the
existing lifecycle policy and durable restore, purge, go-live and rehearsal
epoch state. Source refresh is the explicit cleanup/pause exception and remains
bound to the current campaign's giving window. Compiled campaign handlers enter
their owning scope before task/domain locks at scan, claim, renewal, recovery
and each durable effect. Admission rejects a missing lock order instead of
quietly acquiring it too late. Unrelated tasks retain independent retry-root
locks; no global task-allocation mutex was added. Raised gate denials advance
the scheduler's fair cursor just like an explicit refusal.

All 255 selected PostgreSQL source/fact/task/admission/worker/campaign-race cases
pass, including the existing unrelated-enqueue concurrency tests and the new
claim-versus-cleanup race. Forty-three pure admission/dispatch/lifetime/process
cases pass. Concrete source request binding, operational exception owners,
phase/status APIs and isolated deployment wiring remain open; these internal
admission services are not HTTP authorization or completion evidence.

The progress/status checkpoint adds closed typed task phases, claim resets,
SQL-enforced phase/history binding and refusal of populated history downgrades.
Admin-only bounded JSON endpoints expose whitelisted task metadata and paginated
immutable history; no worker arguments, exceptions, credential values or domain
payloads are serialized. Fresh authorization is repeated at the response
boundary, and passive polling does not renew an abandoned session's idle timer.
All 90 PostgreSQL task/status/history cases pass, including role denial,
revocation, filtering, privacy and migration tests. The full baseline passes
2,849 tests, and lint, formatting and migration-drift checks pass. ADM-03 still
owns the visible background-work interface; runtime integration remains open.

The broker-provisioning checkpoint adds immutable per-identity Valkey path
references and fresh, resumable allocation of independent web/worker/scheduler
passwords. The private server ACL contains only their hashes and the previously
tested isolated command/key rules. Path validation also reserves future SQL
credential identities, preventing later consumers from inheriting an alias.
No provider credentials or future mail/backup broker accounts are created.
All 180 selected configuration/provisioning tests and the 2,862-test baseline
pass. Existing runtime artifacts are never overwritten as an upgrade shortcut;
the worker/scheduler entry-point and concrete source-handler integration remain
open before enabling those services.

The task SQL checkpoint provisions independent scheduler/worker logins and
closed task-only grants. The scheduler can enqueue and lock/scan work but not
claim or transition it; the worker can update fenced task metadata without
campaign configuration, session or provider credential authority. Audited writes
have only the narrow identity/default-column reads their triggers and INSERT
RETURNING require, not access to historical private contexts. Concrete source
owners must extend their table vocabulary explicitly. Eighteen real PostgreSQL
grant tests, 101 initial selected runtime tests and the 2,867-test baseline pass.
The default background reserve accounts for all installer connections plus the
worker's main/renewal pair and scheduler's pinned session through rollout overlap.
These identities do not yet launch an operational worker or enable source I/O.

BG-05's shared adapter checkpoint verifies the live public v2 change-feed
contract and adds an explicitly uncached GET path without changing ordinary
shared cache behavior. Family indications have date-only query bounds, no
server cursor, and only validated distinct identities in the returned value.
Wrong-tenant/relationship transitions, ambiguous timestamps and responses at the
bounded ceiling require full refresh without exposing a partial delta. The
66 shared ParishSoft tests and 2,906-test baseline pass. This is an adapter,
not a scheduler or committed watermark: durable refresh requests, overlapping
window selection, finite transport, whole-corpus validation and promotion still
require the concrete BG-05 owner before runtime can be enabled.

The refresh-request checkpoint adds immutable task-root/tenant/campaign-window
bindings and a separate append-only record for each coalesced command/requester.
Manual requests share one waiting full load after any active poll, including
the interval before its first source lease. Retry-chain, retained-lease and
already-promoted exclusions prevent false fulfillment. Fresh owning admission
runs at creation, replay, claim and each effect; harmless content edits preserve
the window, while changed giving funds or campaign identity invalidate it.
Archived-current pointers permit census refresh without adding archived giving.
SQL independently checks exact canonical window/digest, current configuration,
task and tenant bindings, and refuses a populated-history downgrade.

All 47 selected PostgreSQL request/admission cases and the 2,917-test baseline
pass; the baseline's 1,328 opt-in skips are not claimed as executed integration
tests. Lint, formatting and migration-drift checks pass. These remain internal
services: Admin endpoints, stale-request recovery, durable poll watermarks,
bounded provider transport and concrete promotion/reconciliation are still open.

The read-transport checkpoint adds an opt-in shared Session adapter with a
separate, finite one-request helper process. Keys travel only over private stdin;
the helper uses closed read/search endpoints, no redirects, no inherited proxy
or netrc authority, and an 8 MiB decoded-response ceiling. Error bodies are not
read or returned. The parent polls ownership during the read, kills/reaps only
its helper on timeout or lost ownership, and stops the consumer if drainage is
unconfirmed. JSON decoding preserves decimal values and rejects duplicate keys.
Ordinary shared clients retain existing behavior; coherent clients can disable
cache reads, directory creation and writes altogether.

This process boundary addresses the distinction between Requests' socket-idle
[timeout](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts)
and the parent-owned bounded
[process exchange](https://docs.python.org/3.12/library/subprocess.html#subprocess.Popen.communicate).
The source adapter requires an attached live renewer, rechecks admission before
each attempt, reserves read plus forced-drain plus safety-margin time, and closes
thread-local SQL connections before provider I/O. All 133 shared ParishSoft
tests and four PostgreSQL transport/fencing tests pass, including actual local
helper startup/timeout/reaping without provider credentials. The full baseline
passes 2,984 tests, with 1,332 explicitly opt-in cases skipped; lint and formatting
also pass. Runtime enabling,
strict collection pagination and full/delta promotion remain in progress.

The collection checkpoint adds strict shared pagination and an opt-in coherent
client for the existing full Family/Member aggregation path. Published request
field names and array/envelope contracts are explicit; count, ordinal, unique-ID
and total/size evidence are checked before dictionaries can hide duplicate rows.
Zero-origin probes distinguish an exact zero/one alias, advancing pages and a
verified one-row offset overlap; other overlap fails closed. Empty terminators
cannot excuse missing declared records. Per-page, per-collection and aggregate
request/decoded-byte/time ceilings reject an incomplete scan, never truncate it.
All 79 new pagination/client tests pass, including actual shared full-loader
execution retaining inactive data with caches disabled. These provider-contract
fixtures do not replace credential-dependent operational smoke validation. The
full baseline passes 3,063 tests with the same 1,332 opt-in cases skipped; lint,
formatting and changed-document Markdown checks also pass.

The core normalization checkpoint retains inactive and empty Families through
an opt-in shared loader flag; ordinary recipient tools keep their old default.
Closed, typed field mappings exclude SSNs, unrelated provider PII, pagination
counters and cyclic shared links. Independent contacts/addresses preserve known
blank versus unavailable fields. Email aliases deduplicate after normalization;
eligibility uses active shared Family heads, independently of publication flags.
Malformed identities, types and roster references reject the corpus before
staging. Ministry catalog presence remains distinct from an upstream activity
flag that the type-list response does not supply.

The published v1/v2 Family read models expose a primary address, not separate
home/mailing addresses, country or registration date. The adapter does not
invent those values; later census mapping must preserve their unavailability.
The write-only v1 contact model is not evidence that those fields can be read.
Thirty pure normalization tests and the actual PostgreSQL canonical
round-trip/contact-only deduplication test pass. The full baseline passes 3,095
tests, with 1,333 explicitly opt-in cases skipped. Ruff and formatting pass.
Scoped giving, source worker/recovery, watermark advancement, promotion effects
and setup/configuration integration remain open.

The complete-load checkpoint combines the shared core loader, fund catalog,
typed normalization and selected current/comparison giving. Contributions are
queried through the parish-local data-as-of day; future periods do not issue
contribution reads. Pledge date-filter semantics are undocumented, so bounded
selected-fund reads are filtered by effective `pledgeStartDate`, not the earlier
recording date. No out-of-period detail reaches staging or a disk cache. Exact
cent values retain negative adjustments, reject floats/fractional cents and do
not substitute zero for invalid/missing data. Family DUID/local-ID aliases must
resolve unambiguously; member attribution must agree. Explicit anonymous gifts
produce count-only exclusion evidence, not guessed Family amounts.

Whole loads reject unexpected empty Family/Member data and compare core counts
with the last successful full baseline. The initial internal policy rejects a
drop greater than 25 percent; its bounded argument is not yet an Admin setting.
Giving-window changes and independent contact/address edits are not identity
loss. Tenant query aliases fail closed, and raw email DTO types are checked
before legacy normalization. The 92 selected client/loading/giving cases and
two actual PostgreSQL core/financial round-trip cases pass. The full baseline
passes 3,156 tests, with 1,334 explicitly opt-in cases skipped; Ruff and formatting
pass. Worker attempt ownership, recovery, durable watermarks and runtime/UI
integration remain open; these adapters alone do not complete BG-05.

The delta/recovery checkpoint adds bounded shared household enumeration and
Member search-equivalent reads, with whitelisted contact fallbacks. A complete
delta replaces only affected Family/Member/contact/address data. New households
with new Members are admitted; moved/removed/changed household composition,
changed global Family group definitions and ambiguous scoped endpoints require
full refresh. A missing Family group reference cannot imply active eligibility.
Ministry/fund/giving inputs retain coherent prior full values. Dated roster
currentness is reevaluated without fetching a partial roster; retained giving
keeps its actual older data-as-of date.

Cursor metadata records the manifest's pre-network start instant. Delta windows
overlap one UTC civil day on either side; invalid bindings or a gap exceeding
seven days require full refresh. The last full identity/time survives deltas.
Only the current promoted manifest supplies the next watermark: ready/rejected
cursors do not advance it. A newer safely acquired read-owner fence can reject
old staging without rebinding or deleting its task/fence/payload/cursor evidence.
SQL preserves those fields and prohibits promotion after rejection. Rejection
is audited atomically, including under races and migration round trips.

All 26 selected household/delta tests and 21 pure cursor tests pass. The combined
PostgreSQL delta/rejection/corpus/snapshot run passes 35 tests, including actual
promotion/rejection watermark behavior. The full baseline passes 3,205 tests,
with 1,350 explicitly opt-in cases skipped; Ruff, formatting and migration drift
checks pass. Durable attempt/credential binding, task outcome/recovery handlers,
scheduled/manual producers, source Family/chair effects and runtime/UI setup
remain open. At this checkpoint, Ministry activity policy awaited the owner;
the resolution below adds persistent Admin-managed inactivation.

The attempt-binding checkpoint adds immutable request/manifest/Task-fence/
configuration/loaded-credential receipts. A loaded API key uses the installer's
exact-byte fingerprint, has a private representation and accepts only bounded
single-line key material. Same-claim replay returns one manifest. Fresh service
and SQL checks reject a different key, task, actor, campaign or current giving
window; harmless display-only configuration edits preserve the attempt's
historical configuration identity. Concrete refresh tasks cannot finish generic
unbound staging. Their ready/promoted cursors must describe the exact pre-read
manifest time and coherent full baseline. Attempt history is immutable and
prevents downgrade that would discard its guards.

Twelve pure credential tests and the combined 89-case PostgreSQL
attempt/request/rejection/delta/corpus/snapshot run pass. The combined run exposed
an older downgrade test leaving later reversible migrations unapplied after an
expected refusal; its cleanup now restores every leaf with a fresh executor.
The baseline passes 3,217 tests, with 1,369 explicitly opt-in cases skipped;
lint, formatting and migration drift checks pass. These are not yet enabled
runtime handlers: verified task outcomes, recovery/full-fallback scheduling,
Family/chair reconciliation, worker grants/mounts and setup/UI integration remain.

The Family-effects checkpoint derives campaign eligibility from the exact
newly promoted Family/Member/contact collections, not browser-supplied status.
It verifies live source ownership, the configured tenant/current campaign and
the promoted pointer/generation before invoking the stable-code allocator.
Eligible head addresses and explicit canonical provider-suppression inputs
determine deliverability; publication flags and Family-level email addresses
do not replace head eligibility. Missing email or suppression never prevents
manual-code allocation. Historical campaigns cannot receive new Family effects.

Source pointer, Family population, code fingerprints and cohort history roll
back together if any later required effect fails. Inactivation/reactivation
preserves the original code and cohort. A real concurrent exclusive key-rotation
lock causes a prompt retry instead of waiting while holding source locks.
Twenty-three pure eligibility cases and 23 PostgreSQL source/Family cases pass.
The compiled runtime handler, chair effects and later invitation/suppression
storage owners still need integration; this effect does not dispatch mail.

The HTTP attempt-integration checkpoint requires the immutable attempt UUID
and exact loaded credential when constructing a source Session. Every private
helper request verifies staging state, current configuration/key receipt and
Session key before reserving the source read/drain deadline. Validated attempts
cannot resume observation. Seven PostgreSQL transport cases pass, including
changed campaign/key inputs, same HTTP key with different installed file bytes,
maintained renewal and no SQL connection during the provider wait.

The observation/staging checkpoint now composes the real attempt receipt,
coherent shared full/delta loaders and finite transport. It reads only the
actual promoted base and permanent last-successful-full count evidence, uses
the campaign's frozen timezone (or parish timezone without a campaign), and
records the manifest's pre-read observation instant. Complete normalized
collections enter staging in at most 500-row batches with fresh attempt
validation at each batch. Provider failures or scope changes preserve prior
truth; a validated result remains `ready`, never a claimed Task success.

Seven PostgreSQL pipeline cases replace only the private provider exchange;
all real admission, leases, transport preflights, staging and cursor guards run.
They cover full/delta loads, bad data, changed keys/campaigns, required renewal
and full fallback on an incompatible delta window. The combined pipeline,
transport, source-Family and existing Family-identity run passes 37 cases. The
baseline passes 3,240 tests with 1,388 explicitly opt-in cases skipped. Final
promotion orchestration, durable outcomes/recovery/fallback dependencies,
producers, runtime isolation and setup/editor UI remain in this Phase 2 batch.

The recovery/supersession checkpoint proves completion only from a promoted
manifest concretely bound to the immutable request/retry root. Ready/rejected
staging and another request's successful refresh are not completion proof.
Historical completion survives a newer current snapshot or campaign change.
Interrupted reads wait for the old source lease/read-drain deadline or evidence
of a safely acquired newer fence. A pre-manifest source reservation also delays
retry, avoiding repeated claims against its own unexpired lease. Internal crash
recovery uses five automatic attempts with bounded exponential delays; it does
not change historical Task rows or enable a real provider handler by itself.

Current-scope denial alone remains a hold. Concrete changed tenant/window
evidence allows waiting or safely abandoned work to be cancelled without
rebinding its request; live running workers are never impersonated. Cancellation
records only original/replacement scope fingerprints in its audit, and audit
failure rolls back the transition. Concurrent sweepers produce one outcome.
The actual generic hint/recovery dispatcher now exercises the source metadata
owner, including expiry after a campaign change and post-promotion completion.
The combined source/Family run passes 58 PostgreSQL cases; the final focused
outcome/supersession rerun passes 21. Baseline validation passes 3,240 tests with
1,409 explicitly opt-in cases skipped. Full-fallback dependencies, worker
execution/producer wiring and the remaining Phase 2 runtime/UI work stay open.

The full-fallback checkpoint adds an immutable, SQL-guarded delta-to-full
dependency with creating Task/fence, original request, full command and either
an absent baseline or the exact rejected delta attempt. Normal full-request
coalescing retains every initiating command. Replays cannot create another
dependency, cycles are prohibited, and audit failure rolls back newly queued
full work as well as the link. Retained history prevents destructive downgrade.

The parent can enter durable retry-wait after releasing its source reservation.
Pending dependencies suppress new execution hints/claims without consuming
additional attempts. Only the linked full request's promoted observation can
satisfy the delta, including after configuration changes; ordinary new-work
admission still rejects stale provider reads. Definitive full failure or
cancellation propagates, and generic Task success without promoted proof is an
invariant violation rather than fabricated completion. Fourteen fallback cases
pass, and the full source PostgreSQL regression passes 193 tests with no skips.
The baseline passes 3,240 tests with 1,423 opt-in cases skipped; Ruff, formatting,
whitespace and migration-drift checks pass. Catching load failures and invoking
these services from the compiled worker, scheduled/manual producers, isolated
runtime wiring, chair effects and setup/editor UI remain open in this same batch.

The failed-read checkpoint atomically rejects the exact attempt, releases its
source reservation while retaining its read/drain deadline, settles the Task
and writes a closed value-free diagnostic. Narrow historical cleanup after a
campaign change cannot authorize new observation or promotion. Unknown drainage,
lost ownership, completed snapshots and fallback dependencies retain their
separate outcome owners. Log failure rolls all settlement effects back.
Fourteen PostgreSQL tests cover these paths, database/event vocabulary parity
and refusal to downgrade retained failure history; 18 pure classification tests
pass. The shared transport now retries only confirmed drained connection
failures through the existing bounded retry policy, rechecking reservation on
each attempt; 110 selected transport/client cases pass. Runtime worker wiring
and the rest of the Phase 2 batch remain open.

The compiled-executor checkpoint now connects actual UUID hint claiming and
independent renewal to credential intake, full/delta observation, staging,
promotion and proven Task outcomes. Required reconciliation has no default
success implementation. Fourteen executor PostgreSQL cases cover real campaign
Family/code population, duplicate hints, no-base and incompatible-delta fallback,
scope changes during HTTP, contention, missing credentials, partial-effect
rollback, uncertain drainage and completion failure after committed promotion.
Linked full success/failure is acknowledged without another parent observation,
including after configuration changes. Terminal control flags are set only
after the owning transaction commits while renewal is excluded.

The entire source PostgreSQL suite passes 221 tests with no skips; baseline
validation passes 3,262 tests with 1,455 opt-in cases skipped. The service remains
disabled until required chair effects, actual startup dependencies and isolated
SQL/mount admission are complete. Scheduled producers and the remaining setup/
configuration UI work continue in this same Phase 2 batch.

The scheduling checkpoint adds immutable, SQL-guarded refresh ticks containing
the exact due instant, cadence, timezone, applied configuration and command.
The session-owned scheduler creates at most the latest nightly and quarter-hour
slot per pass, with full first so compatible delta work coalesces. Downtime
does not replay an unbounded backlog of read-only polls. Repeated scans/restarts
reuse ticks even if insertion hints were lost; failed terminal work is not
silently retried by replaying its tick. Current campaign timezone governs its
slots, and otherwise the Parish timezone applies. The shared resolver handles
DST gaps and folds. The configurable-time input exists in the slot calculator;
the applied configuration currently uses the documented 2:00 a.m. default until
ADM-03 adds the versioned time editor/schema.

The compiled scheduler producer also performs a bounded fair supersession sweep
before creating current-scope work. Known source ownership or a retained external
deadline holds hints/claims without consuming attempts; a claim/acquire race
still enters explicit contention wait. A newer validated read retires older
drained staging in bounded transactions without rewriting old manifest/Task
provenance or reusing its payload. Production startup remains disabled pending
complete owning effects and isolated runtime admission.

Eleven pure cadence tests pass. The source plus scheduler-process regression
passes 237 PostgreSQL cases with no skips; after adding drained-staging cleanup,
the 32-case executor/producer/process rerun passes. The baseline passes 3,273
tests with 1,468 explicitly opt-in cases skipped before those final two cleanup
cases. Ruff, formatting, migration drift and whitespace checks pass. No Phase 2
review round has run yet. Runtime wiring, complete setup, configuration/editor
UI and their tests remain open.

Ministry activity decision (September 11, 2026): the owner requires an Admin
web workflow to mark Ministries inactive so they are not shown to parishioners.
The [normative workflow](../specs/stewardship/admin-portal/spec.md#ministry-activity-management)
now defines persistent parish-wide tenant/DUID overrides, defaults, import and
rename stability, visibility of existing memberships as well as choices, and
preservation of source/history/earlier requests. It also defines live-campaign
edits separately from structural selection and the existing seeded-assignment
suspension/reactivation effects. Linked data, Family and implementation/task
documents carry the same requirements without marking their implementation done.
This resolves the policy question; the YAML schema/projections, Admin UI,
authorization integration and Family-form enforcement still need implementation
under their owning Phase 2/later-phase tasks.

The local-activity storage checkpoint adds a new immutable v4 configuration
schema, normal request and additive-only recovery formats. Existing schemas and
retry parsers retain their old behavior. Exact YAML/projection parity, unique
tenant/Ministry identities, retained record bindings and append-only history are
checked in Python and PostgreSQL. Removing an override restores default activity
without permitting its historical record or tenant/Ministry pair to be reused.
The ordinary configuration installer preserves campaign/schedule projections;
runtime grants explicitly include only the new projection's required access.

The pure source Chairperson calculator uses current dated roster relationships,
active Members, valid Member contacts and the applied local activity policy.
It retains distinct Member and roster evidence, groups duplicate relationships,
marks shared addresses ambiguous even when another active owner is not a chair,
and retains the contact publication indicator. Suggestions never create grants
or choose a Member merely because an email matches. Twenty-eight source suggestion
cases and 40 activity cases pass. The suggestion module has 100% focused line
coverage; the activity module's pure run has 86%, with its database preflight
covered separately by PostgreSQL tests. Baseline validation passes 3,341 tests
with 1,488 opt-in cases skipped.

The complete PostgreSQL run passes 1,368 cases and identifies one outdated
shared guard-inventory assumption. Source payloads, snapshot memberships and
daily fact rows use explicit compaction guards rather than permanent append-only
guards. The inventory now checks their exact enabled triggers, update refusal
and guarded retention, without exempting those tables. Ministry policy records
retain the ordinary permanent append-only contract.

After correcting that inventory, the 169-case PostgreSQL rerun passes with no
skips: storage and migration round trips, Ministry activity, restricted installer,
activation, policy and offline recovery. It includes SQL-only rebinding refusal,
locked campaign-selection preservation, and both preflight and SQL barriers
against missing seeded effects. Ruff, formatting, Markdown, migration drift and
whitespace checks pass. Integrated Phase 2 container/browser coverage and the
formal review rounds remain for the completed batch.

This remains an internal checkpoint, not the completed Admin activity feature.
At that checkpoint, preflight and SQL held activity changes with seeded
assignments until the source-effects owner could reconcile overlays and review
tasks in the same activation transaction. This prevented storage support from
leaving stale authorization active. The following checkpoint replaces that
barrier; Admin UI and later Family submission enforcement remain open.

The subsequent Chair reconciliation checkpoint replaces that temporary blanket
barrier with exact transactional receipts. A security-barrier current-source
view exposes only Chairperson Member/Ministry/email relationships and roster
evidence to the restricted installer, not census or giving payloads. Every join
uses one promoted snapshot; staging and historical contacts cannot fill gaps in
current truth. ASCII Chairperson label matching is identical in SQL and Python.

An immutable selection record binds a seeded assignment to the specific Member
and exact original source evidence. No online role can create this evidence yet:
the Admin-confirmed request creator remains with ADM-07. Fixtures representing
previously confirmed seeds exercise the downstream behavior without inventing
an operational confirmation path. Missing evidence suspends rather than choosing
a Member by shared email.

Source and configuration owners now atomically record all seeded decisions,
update the access overlay, open or refresh one suspension-review episode, and
close that episode on relationship return or explicit assignment removal.
Manual assignments and configured roles are untouched. Each receipt has a
parish-owned audit event; retained review/receipt links preserve the full reason
and source/configuration history without copying private contacts into logs.
SQL verifies live source fencing or the owning configuration activation, exact
decisions, and complete effects before promotion/activation can commit. A later
failure rolls back source, overlays, review records and audit together. A missing
source still holds activity changes affecting seeds before YAML selection.

The initial targeted suite passed nine PostgreSQL scenarios, including real
restricted-installer inactivation/reactivation, source loss/return, repeated
imports, explicit removal, stale fences, omitted/forged effects, rollback and
closed-review retention. The completed checkpoint adds fresh authorization's
manual-provenance variants, exact effect retries and a real two-connection
source-promotion/activity-installation race. Browser SQL authority now reads
assignment overlays without INSERT/UPDATE privileges; source/configuration
owners remain responsible for their writes. Receipt/audit attribution uses
the original Task or configuration activation correlation.

The compiled refresh handler also has one concrete Phase 2 effects composition:
it verifies the exact live attempt, applies Chair effects, populates a current
non-archived campaign through the real Family owner, and succeeds only after
both complete. Keys and transactionally fresh suppression reads are explicit
startup dependencies; no implicit empty suppression set or success placeholder
is supplied. Fake-provider integration covers pre-campaign and campaign loads,
Task acknowledgement, replay, and rollback of earlier Chair effects when a
Family dependency fails. Production worker startup is still not enabled by
these internal services.

Validation: the complete PostgreSQL suite passes 1,412 cases in 13 minutes
38 seconds. After the final composition, provenance, concurrency and attribution
changes, a 154-case PostgreSQL rerun passes without skips, including restricted
configuration/authentication grants, bootstrap and storage migration tests.
Its three focused implementation modules have 95% combined line coverage;
the pure suggestion and seeded-decision modules separately have 100% coverage.
The final baseline passes 3,356 tests with 1,536 explicit opt-in cases skipped
and two existing warnings. Ruff, formatting, Markdown, migration drift and
whitespace checks pass. No Phase 2 review round has started.

This checkpoint does not complete the Admin screen, confirmed-seeding workflow,
complete worker wiring or later Family-form activity enforcement.

### Worker composition and initial Admin editors (in progress)

The next increment gives worker and scheduler identities explicit source and
configuration-read grants. Workers can publish source and Family effects but
cannot create confirmed Chair seeds, edit Family activity, read sealed email
links, or rewrite source payloads. Scheduler cancellation has a SQL guard that
requires the scheduler/work locks and a waiting source task; it cannot use a
known worker identifier to heartbeat or cancel running work. Restricted-role
source and background tests pass 37 cases without skips.

Runtime assembly binds every refresh admission to fresh YAML/database coherence,
loads purpose-specific keys once, and records process-start-bound consumer
fingerprints. Long-running task renewal updates process health only after the
lease renewal commits. The focused runtime/receipt/grant suites pass 88 cases.
This is not yet enabled in the Compose service registry: empty-deployment wizard
staging and the worker's initial ParishSoft credential handoff remain required.
The provisional pre-delivery suppression adapter explicitly permits only
Testing and refuses Production; BG-06 must replace it with the durable provider
suppression owner before live delivery. It never supplies implicit empty
Production suppression evidence.

The Ministry activity screen now supports sorted/searchable/filterable catalog
rows, an actor/source/configuration-bound signed impact preview, CSRF-protected
confirmation, durable Applying/Applied receipts, and reactivation preserving the
original override identity. Shared Admin request admission rechecks authorization
and exact scope inside intake's transaction. Passive receipt reads do not renew
idle time. Its 24 PostgreSQL tests cover actual restricted web grants, rejected
stray inputs, preview expiry, stale source/configuration, installer activation,
and retry idempotency.

The Parish profile editor uses that same request boundary and renders a complete
before/after preview. Existing campaign timezone, boundaries, schedules and
branding survive profile edits unchanged. Required-field, forbidden URL, role,
stale-form and real web-grant tests pass. The combined editor suite passes
42 cases without skips. Role-filtered navigation, persistent Testing indicators
and the background-work HTML view are under active validation; broader wizard,
credential/branding, campaign-content and preview acceptance remains open.

The expanded home page shows the current campaign, latest promoted source time,
next planned Family mailing, Staff/Admin participation aggregates, and Admin-only
recent failed tasks. The Testing indicator names the redirected recipient only
for Admins; leaders receive neither Family-code links nor Family aggregates.
Critical-event, restore, delivery-pause and go-live-preparation warnings are
persistent and do not expose private log context. Status-page pagination retains
its filters and presents exact count/percentage formatting. Dashboard reads are
audited and recheck authorization after querying.

Current validation passes 100 focused PostgreSQL/pure integration cases with
96% combined line coverage across the seven new source/runtime/editor/context
modules. Another 49-case run covers dashboard, navigation, Google authentication
and restricted web grants. The runtime lifecycle suite exercises signal
restoration, consumer receipts and cleanup after both successful and failed
startup/drain. The ordinary baseline passes 3,379 tests with 1,607 explicit
opt-in cases skipped and two existing warnings. These are incremental results,
not a substitute for the final complete PostgreSQL, browser, Compose and review
gates.

### Session-bound setup ownership (in progress)

The initial wizard now has an internal, durable attempt owner bound to exactly
one bootstrap Admin session. Another login, even for the same Admin account,
cannot adopt or cancel that staging. Logout, loss of Admin authorization and
expiry fence the attempt; retries preserve its safe tombstone. SQL rejects
session/base/task rebinding, unrelated source tasks, skipped completion,
fabricated renewal and writes after expiry. State changes carry redacted audit
evidence atomically. No configured marker or online setup grants are enabled by
this storage increment, and expiry is not yet a claim that target artifacts have
been scrubbed.

The shared lifetime policy enforces thirty-minute idle expiry, the original
source Task's fixed two-hour watchdog, the session's absolute deadline and at
most one correlated live-worker renewal per five minutes. A completed source
load stops the renewal exception; later steps retain ordinary session expiry.
The combined bootstrap/setup run passes 62 cases with 100% focused line coverage
of the new policy and attempt-admission services. The complete wizard forms,
temporary source/credential artifacts, renewal endpoint, cleanup integration and
final installer/consumer/configured-marker protocol remain open.

Fresh-database testing also exposed a bootstrap regression previously hidden by
test flushing: source migrations seed an idle lease and an empty current pointer,
which the older empty-database trigger treated as occupied application data.
The follow-up migration admits only their pristine zero-history shapes, not
used source state or arbitrary future tables. Explicit pristine/nondefault-source
tests retain the unrelated-data and row-security rejection cases.

The complete PostgreSQL regression run after this storage increment passed
1,511 tests without skips in 17 minutes. The ordinary suite subsequently passed
3,444 tests with 1,685 explicit opt-in skips and two existing warnings. Further
campaign-editor work was still in progress, so these results do not represent
the final Phase 2 acceptance gate.

### Draft campaign editor (in progress)

The Admin can create the sole draft or edit its structural configuration through
exact signed previews and the existing configuration installer. Confirmation
pins source, applied YAML, lifecycle versions and active work gates. Source
promotion or going live invalidates an earlier preview even when its YAML digest
did not change. Identical confirmation retries return the original receipt;
acceptance alone never claims activation. Existing and historical locked
campaigns render structural fields read-only. New drafts copy the Parish
timezone, and later draft changes never alter the Parish default.

Forms validate module-dependent inputs, financial/comparison whole-year periods,
explicit source fund IDs, overlap acknowledgment and current Ministry choices.
Locally inactive Ministries are unavailable for new selection; retained
selection does not reactivate them. HTML previews use readable names, dates and
comma-formatted DUIDs rather than storage UUIDs or object representations.
Progressive module hiding disables unselected fields; server validation still
rejects stray values, and ordinary forms remain usable without JavaScript.

The combined form/editor/profile/navigation run passed 85 tests with 98% line
coverage across the three new campaign modules. All 135 Chromium/Firefox/WebKit
browser cases passed, including mobile/desktop accessibility, module behavior
and no-JavaScript forms. The expanded fixtures also caught and fixed a Ministry
page header override that had hidden the persistent background-work indicator.
Cloning, immutable content/templates, combined schedule
reconciliation and readiness-test mail are still required before ADM-04 closes.

The financial share-option editor now supports the configurable default labels,
stable saved IDs, explicit additions/deletions, and numeric ordering. Submitted
management counts, missing/foreign/repeated identities and out-of-range fields
are rejected rather than silently dropped. Only Parish/pronoun substitutions
are allowed in option labels. Confirmation uses the same exact runtime/source/
configuration scope and remains unavailable once structural settings lock.
Its combined form/campaign/share integration run passed 92 tests with 98% share
module coverage; the browser expansion passed 147 checks across three engines.

### Passive operational indicators and task details

Family sessions now record a separate presence timestamp and closed form-section
name. The browser sends at most one heartbeat per thirty seconds while visible,
never answers or credentials. PostgreSQL stamps its own time, enforces the rate
limit, and rejects attempts to combine presence with idle/absolute renewal.
Admin reads filter the current campaign, eligibility, mode/rehearsal/restore
epochs and session deadlines; observations expire after ninety seconds.
Family names come only from the current configured tenant's source snapshot.

The persistent Admin header polls a count-only presence endpoint and bounded
background metadata. It exposes explicit unavailable-state messages rather than
claiming a failed read means zero activity. Neither polling endpoint renews the
Admin's session. Authorized detail lists show names/DUIDs/times/section, never
answers, codes, access tokens or session cookies. Task details now have an HTML
history/progress view with bounded pagination, initiator identity and browser-
local timestamps; the original JSON metadata API remains available.

The presence/authentication/navigation run passed 46 tests with 93% presence
module coverage. The final task/presence/navigation run passed 50 cases, and all
165 browser cases passed across Chromium, Firefox and WebKit. Browser tests
exercise visible/hidden tabs, thirty-second request limits, expiration, passive
count polling, service failures, accessibility and mobile layout. The ordinary
suite passed 3,470 tests with 1,765 explicit opt-in skips and two existing
warnings. A subsequent full PostgreSQL run was invalidated by adding a migration
while migration-roundtrip tests were running; it is not passing regression
evidence. Its separate Admin query-budget failure was fixed by loading presence
through the passive endpoint instead of recomputing it on every page. The
identity-performance/presence rerun passed 22 tests at the unchanged query budget.

### Immutable content and template editing

The current campaign has named page slots and independently selectable email
templates. Separate reminders may use different revisions/subjects. Editing a
revision allocates a fresh ID and atomically reconciles its referencing campaign
slot or mail schedules; unrelated templates remain unchanged. A referenced email
cannot be deleted until its schedules select another template. Content previews
remain available after structural locking but are invalidated by changed runtime
or source scope. Historical campaigns are not editable through these routes.

Sanitized HTML and independently generated/edited plain text belong to canonical
YAML under the new `campaign-content-v5` discriminator. Existing schema validators
retain their meaning. Normalized immutable projections, ancestry verification,
request admission, offline Admin recovery and SQL guards retain revision payloads
and detect missing or forged projections. Installer and runtime SQL registries
explicitly grant only their required content authority.

The progressively enhanced visual editor supports text formatting and plain-text
paste. Raw HTML source never enters the browser DOM without server sanitization.
Before/after previews use fictional Family data and nonfunctional sample links;
these previews do not send email or establish production readiness. Exact signed
confirmation requests installation and returns its receipt; it does not claim
that content is already applied.

Content form/schema tests pass 42 cases. Real-session/storage tests pass 20 cases
with 92% combined form/view coverage, plus a separate restricted-installer test.
Browser tests cover HTML editing, inert paste/source, sample previews, accessibility
and mobile layout; the complete three-engine run passes 180 cases. The ordinary
suite passes 3,512 tests with 1,801 explicit opt-in skips and two existing
warnings. Ruff, formatting, selected Markdown and migration-drift checks pass.
The schema-stable complete PostgreSQL regression after content editing passed
1,582 tests in 16 minutes, 50 seconds. Review evidence will follow before Phase 2
is declared complete. Cloning, readiness-test mail, staged
wizard finalization and the remaining integration/runtime work are still open.

### Combined mail schedule and draft-date reconciliation

The schedule editor owns initial invitations, repeatable reminders, daily/weekly
digest times and independent template selections. Every saved row retains its
logical ID and type; removal is explicit. Times have whole seconds and no offset,
with all civil values resolved in the campaign timezone. Subjects come from the
selected immutable email revision. Unresolved legacy templates remain visible
but cannot be newly assigned or count as readiness evidence.

Date/timezone edits in the structural editor transfer to reconciliation whenever
the campaign has schedules; no edit is saved by that transfer. The combined
candidate must resolve every out-of-interval mailing and preserve the initial/
reminder ordering. Mixed structural/date edits remain on the original form with
an explanation rather than silently discarding fields. Live structural changes
stay with their dedicated lifecycle owner; this editor does not bypass that lock.

The exact preview includes current-revision work, successful semantic coverage,
safely cancellable occurrences, failed work and blocking in-flight/uncertain work.
Aggregate monotonic occurrence/task versions invalidate stale previews without
loading per-Family records. Current outbox-linked pending work remains blocked
until its delivery owner can prove atomic reconciliation. SQL repeats the
replacement guard and supersedes safely cancellable work in the same transaction.

Changing the campaign timezone now selects new schedule revisions even when their
own civil fields are identical; their UTC time cannot remain pinned to the old
zone. Date-only changes preserve in-range mail's cadence and work; affected
out-of-interval mail still needs explicit replacement/removal. Fulfillment carries
across revisions, and populated downgrade cannot weaken the cadence guard.

The combined form/editor/schedule run passes 70 tests with 94% coverage of the
three schedule UI modules. Three additional SQL tests cover timezone-only
in-flight blocking, bypassed Python admission, rollback and empty/populated
migration reversal. All twelve new mobile/desktop accessibility cases passed
across the three browser engines. The subsequent 62-case focused rerun passes
after template revision labels and overlap acknowledgment previews were added.
The ordinary suite passes 3,535 tests (1,824 opt-in skips); the complete browser
suite passes 192 tests in three minutes, two seconds. The shared-process WebKit
fixture stalled reproducibly before the final navigation; two separate 63-case
subsets passed. Per-scenario browser process isolation resolves the complete
suite without retries, skipped assertions or longer navigation timeouts.

### Archived campaign cloning

Admins can now start empty or clone an archived campaign after the existing
Return-to-Testing/single-current guards admit a successor. The clone receives
fresh deterministic child identities for content, share options and schedules;
all campaign/financial/one-time mail dates, names and fund selections start
empty. Current locally active Ministries are offered instead of importing stale
selections. Digest civil times/weekdays and inert content are retained. No
Family identity, response, delivery or workflow records are copied.

The preview shows the complete new structure and fictional rendered content.
Both the form seed and final preview are actor/base-bound and expire after
15 minutes. Confirmation repeats source/lifecycle/configuration admission;
installation adds the campaign, content and schedules in one YAML transaction.
The new-campaign content allowance cannot modify an archived source's text.
Normal 100-record/request and bounded preview limits still apply explicitly.

The combined clone/campaign/content run passes 44 tests with 91% focused line
coverage (100% cloning helper, 90% HTTP workflow). Tests include actual archive
and Return-to-Testing transitions, installer application, historical preservation,
fresh identities, stale/expired forms, forbidden roles and hidden fields. All
18 selected three-engine browser cases pass, including the twelve new clone
accessibility/mobile cases and the shared campaign fields' module behavior.

### Source runtime topology wiring (in progress)

The operational dispatcher and rendered Compose topology now include the source
worker and metadata-only scheduler. Each has its own SQL and Valkey credentials,
read-only configuration selection and retained offline-exclusion lease. Only the
worker has ParishSoft credentials and external network access; neither receives
mail-dispatch private keys or Workspace credentials. The compiled handler
registry remains closed, and Production source effects still require BG-06's
suppression owner. Missing required credentials continue to fail startup closed.
The focused topology/process/composition/provisioning run passes 57 tests. This
does not yet establish the empty-wizard credential path or a running Compose
demonstration; those remain Phase 2 acceptance work.

### Public credential handoff discovery

After mount/SQL admission, each target installer derives and publishes only its
public encryption key. Publication is immutable and idempotent; mismatching
private-key startup refuses rather than silently stranding sealed requests.
Web has only SELECT authority and resolves an encryption-only `PublicHandoff`.
Target row policies and SQL insertion guards reject cross-target publication,
invalid key lengths and forged human attribution. Published history prevents a
downgrade that would remove these protections. No private key, credential value
or extra credential mount is exposed to web.

The combined discovery/credential isolation run passes 24 tests with 100% line
coverage of the discovery service. Runtime tests check publication before queue
startup. The baseline passes 3,549 tests with 1,856 explicit opt-in skips and two
existing warnings. Provider validators, integration UI and wizard use of these
public handoffs remain in progress; discovery alone does not complete them.
The wider bootstrap/runtime run passes 76 cases after explicitly admitting the
new RLS table to bootstrap's reviewed visibility list. This is not an emptiness
exemption: the eight-case discovery rerun verifies that an existing publication
still prevents initial bootstrap adoption. Ruff, formatting and migration-drift
checks pass.

### Integration configuration and credential checks (in progress)

`/admin/configuration/integrations` exposes existing ParishSoft, Workspace,
outgoing-address and Slack settings. Non-secret edits use exact, actor-bound
fifteen-minute previews and the existing YAML installer; they preserve the
credential fingerprint. Replacement forms require a Google authentication less
than five minutes old and real CSRF admission. A posted candidate is never
redisplayed, including on a validation error, and is sealed to the advertised
target public key before persistence. The locked intake rechecks the applied
configuration digest. Reusing an intent cannot change either the candidate
fingerprint or the immutable public provider scope.

Each target installer can read only its own provider-context rows, not the
complete YAML configuration. SQL permits context insertion only in the original
request transaction, repeats closed-field/type bounds and rejects later mutation
or populated downgrade. Web cannot select sealed ciphertext. The 93-case
credential/context/bootstrap run passes with actual restricted SQL identities.

Provider checks use one isolated child process with credentials passed over
stdin, an empty environment, closed inherited descriptors, discarded stderr and
only a fixed outcome on stdout. The installer closes SQL before external IO and
reserves a maximum thirty-second check plus five-second forced drainage within
the request expiry. Unconfirmed drainage terminates the installer. The helper
rejects redirects and unapproved endpoints, limits response bytes, and uses
shared ParishKit exact-tenant validation and in-memory Google credential loading.
Workspace checks authenticate the delegated SMTP mailbox using
[Google's XOAUTH2 protocol](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol).
Slack uses the authentication-only
[auth.test method](https://docs.slack.dev/reference/methods/auth.test/), avoiding
additional channel-read permissions. Neither check sends a message or attests
to sender/channel delivery. Outages retain sealed input for bounded retry;
rejected credentials follow the existing rollback/scrubbing protocol.

The provider/context run passes 110 tests with 97% focused coverage; normal CI
uses fake HTTP/SMTP and a no-network subprocess case. The Admin/form/grant run
passes 45 cases with 93% focused coverage, and all thirty new Chromium, Firefox
and WebKit accessibility/mobile cases pass. These tests cover exact retry,
write-only errors, CSRF, stale settings/authentication, role denial and real web
SQL grants. Request progress distinguishes checking, installation, consumer
acknowledgement and completed/failed cleanup; it never claims that YAML or
delivery readiness changed just because a credential was installed.

Remaining integration work includes adding/removing optional configuration
through its complete owning workflow, test-only checks of an installed credential,
explicit test deliveries and wizard use of these services. The setup wizard and
final Compose demonstration remain incomplete.

### Acknowledged credential reference selection

After target installation and all required consumer acknowledgements, the
original Admin can review/select the non-secret fingerprint from the replacement
status page. Fresh Google authentication, current provider settings, the latest
applied target receipt, complete matching consumer evidence and absence of a
pending replacement are required. An already-selected reference is displayed
read-only; exact confirmation retries return the original YAML request.

The dedicated `integration-credential-patch-v6` request format admits exactly one
existing integration fingerprint update. It cannot modify settings, add/remove
services or change policy. Ordinary/default and retained v1-v5 parsers still
reject fingerprint edits; only the compiled selection owner opts into this new
format. The installer rechecks receipt/provenance before preparation. A pending
replacement arriving after web intake fails the selection without changing YAML.
The configuration installer receives read-only, target-scoped receipt and public
provider-context access, never ciphertext, private keys or credential-file mounts.
Retained selection requests prevent a schema downgrade that would lose replay.

The combined reference-format, HTTP, configuration-installer and provider-context
run passes 93 cases with 94% focused selection coverage. Tests perform real
private-file replacement and required-worker acknowledgement, use the actual web
and configuration-installer identities, and exercise stale settings/authentication,
CSRF, arbitrary fingerprints, pending replacement races and migration rollback.
Six new three-engine browser cases pass. Selecting a receipt is not a provider
delivery-readiness test and does not complete the initial setup workflow.
The ordinary full suite passes 3,700 tests with 1,998 explicit opt-in skips and
two existing warnings. Ruff, formatting, edited Markdown and migration-drift
checks pass.

### Durable branding and retained content previews

The Admin logo screen normalizes a bounded static PNG/JPEG/WebP into large,
menu, icon and favicon PNG variants. Original filenames and image bytes never
enter YAML or SQL. The private media store writes immutable UUID-named bundles
with exclusive files, bounded inventories and directory/file fsync. Durable
metadata records the original Admin/session, optional setup attempt, applied
base, variant dimensions, length and digest. Only a complete ready bundle can
be previewed, and a signed exact YAML request selects its four references.

The web preview verifies every file against its immutable receipt. Public
opaque image URLs require actual configuration activation, not merely staged
or prepared data. SQL independently enforces variant bounds, original ownership,
expiry and reference consistency. The web identity cannot delete history, and
the configuration installer gets receipt reads without a writable media mount.
Initial-setup uploads retain the original attempt/session binding and become
unusable when that attempt expires or is cancelled.

The closed scheduler/worker registry includes `branding_cleanup`. A bounded
scheduler pass queues one durable root per expired, unselected bundle, including
cancelled setup uploads. Only the worker mounts media read/write; the scheduler
cannot mutate receipts or remove files. Any retained or prepared Parish reference
pins its bundle. Restore review and unresolved configuration requests hold new
cleanup. Task fences and a nonblocking media lock protect short checkpoints
around file removal; interrupted cleanup resumes idempotently, preserving safe
receipts. Automatic retries are bounded, and terminal failure does not produce
a fresh retry root on every scheduling pass.

`/admin/campaign/<id>/content/history` provides read-only fictional samples from
the campaign's selected applied version. Archived previews retain that version's
Parish name, logo and substitutions after a new global Parish edit. Revision
lookups are campaign-scoped, writes are refused, and all responses recheck Admin
authorization and remain non-cacheable. No real Family or provider is consulted.

The combined PostgreSQL/file run passes 68 cases with 95% focused coverage,
including real web/config-installer/worker/scheduler SQL identities. The focused
cleanup/runtime/topology run passes 38 cases. The complete Chromium/Firefox/WebKit
component run passes 252 cases, including actual logo loading and the retained
content screen. Remaining Phase 2 integration acceptance is tracked separately;
these results do not complete wizard finalization, readiness delivery or the full
review gate. The ordinary full suite passes 3,680 tests with 1,981 explicit opt-in
skips and two existing warnings; those skips are not PostgreSQL/browser evidence.
Ruff, formatting, edited Markdown and migration-drift checks pass.

### Scheduler-owned setup expiry

The actual scheduler now checks abandoned setup before producing source and
branding-cleanup tasks. It reads only the original session's identity binding,
revocation and deadlines, plus current Admin policy; it cannot read session
keys, Google subjects or Django session payloads. Column grants and the SQL
owner guard limit it to system-authored expiry, never renewal, cancellation of
a live session, task rebinding or setup completion. Restore review holds expiry.
Duplicate sweeps retain one tombstone and one redacted audit event.

The focused PostgreSQL/runtime run passes 75 cases, including exact deployed
scheduler-role tests. This supplies the runtime expiry fence; removal of all
remaining staged settings, source and credentials stays with the wizard's
target-specific cleanup owners.

### Original-login public setup forms

The initial setup route now provides an explicit CSRF-protected start, a passive
overview and editable Parish profile, additional login rules, public mail/Slack
settings, Testing recipient and private logo upload. GET/HEAD does not create a
draft or extend idle expiry. The original bootstrap Admin remains active while
staged additional rules have no effect. These controls never accept a credential,
active fingerprint, deployment path or Production switch.

Temporary sections belong to the original attempt/login and use the entire
attempt's version to reject conflicting tabs. SQL independently checks the
original live Admin, bootstrap base, declared field vocabulary and lifecycle.
Cancellation or scheduler expiry clears all public values in the same database
transaction; retained tombstones cannot be repopulated or deleted. The scheduler
has scrub-only payload columns and cannot read public draft JSON or rendered
mail. Delivery eligibility compares database-generated draft digests with the
explicit candidate metadata needed by its invoker-only guards. The isolated
Slack target can read equality evidence only for its own Slack step, not other
draft rows. Normalized setup
logos use original-login-only, non-cacheable image URLs and the existing durable
bundle/cleanup owner. They are never public merely because they are ready.

The ordinary suite passes 3,724 tests with 2,078 explicit opt-in skips and two
existing warnings; Ruff, formatting, edited Markdown and migration checks pass.
The focused forms, HTTP, SQL-grant, routing and expiry run passes 73 cases with
98% coverage of the new forms/services/views. Forty-two new browser cases pass
across three engines at mobile/desktop widths. This replaces the placeholder
with real public staging; sealed setup credential intake, staged source loading,
campaign preparation, provider delivery checks and configured-marker finalization
remain incomplete. No Phase 2 acceptance or review-gate completion is implied.

### Correlated setup source progress

The original source Task now has a setup-only progress page and a CSRF POST
renewal endpoint. Passive reads never extend activity. A renewal requires the
same original login, exact bound Task, live Task/source fences and recent worker
heartbeats, and is accepted at most once per five minutes. SQL verifies the
lease evidence independently. Session activity is updated after the SQL renewal
stamp, preserving consistent deadline ordering. Neither the original two-hour
watchdog nor the absolute session deadline can move.

The page shows all deadlines and the expected two-to-three-minute normal load.
Its bounded foreground polling sends only CSRF, stops when hidden/closed or past
a deadline, and honors terminal server responses. Manual progress forms also
work without JavaScript. At the watchdog, the server commits setup expiry rather
than renewing an unhealthy load. The web receives only source-lease observation
columns, never lease-write authority. Source task creation/execution and complete
staging cleanup remain with the upcoming loader owner.

Thirty-nine focused policy, real-lease and HTTP tests pass with 97% service/view
coverage; 54 setup browser checks pass across all three engines. Historical
creation-time fixtures exercise the hard SQL clock under restored timestamps;
all guards are enabled during the actual restricted-role renewal assertions.

The first interim dual-model correction pass is recorded in the
[Phase 2 review ledger](stewardship-phase-2-reviews.md). It found issues and is
still being corrected; it is not phase acceptance. The reviewed commit's full
browser run passes all 312 cases.

The next correction checkpoint passes 318 browser cases, 3,765 baseline cases,
and the focused database runs recorded in that ledger. Full PostgreSQL regression
is in progress; remaining wizard implementation and the three complete phase
review/fix rounds are not finished. The
[setup cancellation decision](stewardship-phase-2-reviews.md#setup-finalization-specification-decision)
is now approved and reflected in the controlling specifications. Its implementation
now has a tested original-login cancellation page, immutable request/abort
journal, installer recovery and temporary-logo cleanup. See the
[implementation checkpoint](stewardship-phase-2-reviews.md#approved-setup-cancellation-implementation-checkpoint).
The bootstrap transition parser is explicit; ordinary intake cannot select it.
Final source/Family/configured-marker activation remains closed until its full
owner is implemented. No PR, merge, release or deployment is claimed.

The subsequent full PostgreSQL regression passes 1,815 tests. Sealed setup
credential forms and their separate temporary store now also pass actual
web/scheduler isolation, cancellation and HTTP tests. They do not enqueue live
credential installation. The latest baseline passes 3,825 tests, and the new
form passes six three-engine mobile/desktop browser checks. The
[latest review-ledger checkpoint](stewardship-phase-2-reviews.md#subsequent-regression-and-credential-intake-checkpoint)
records which later changes have focused rather than full-suite evidence.
Continue with the fenced setup credential exchange and staged source loader;
the overall empty-to-configured demonstration and Phase 2 review cycle remain
incomplete.

The next [execution checkpoint](stewardship-phase-2-reviews.md#ephemeral-exchange-and-staged-source-execution-checkpoint)
connects the real ephemeral exchange, original-login Task intake, bounded source
loader and atomic staging completion. Its 62-case PostgreSQL suite passes with
91% focused coverage; the baseline passes 3,827 tests. The worker supports a
bootstrap-only registry without an installed ParishSoft file. Continue with
abandoned source-row cleanup and the remaining staged campaign/finalization
owners. This is not the empty-to-configured demonstration or phase completion.

The [source-disposal checkpoint](stewardship-phase-2-reviews.md#expired-setup-source-disposal-checkpoint)
adds scheduler-owned cleanup after the real source-drain deadline, exact SQL
deletion restrictions, bounded rollback-safe batches and shared-payload
preservation. Its combined PostgreSQL regression passes 55 tests with 85%
focused cleanup coverage. The next setup work is staged first-campaign
preparation and installer finalization; full Phase 2 acceptance remains open.

The [first-campaign structural checkpoint](stewardship-phase-2-reviews.md#first-campaign-structural-staging-checkpoint)
passes 27 PostgreSQL cases with 95% focused coverage and six three-engine
mobile/desktop accessibility cases. It remains original-login temporary data,
not an active campaign. Continue with wizard content/share/schedule editing,
provider checks and the final coherent installation workflow.

The complete source/setup/configuration batch will receive at least three
independent review/fix rounds and passing local/PR CI before human merge
approval. Normal validation uses synthetic data and fake providers. Later
Family response, production delivery, publication, backup/restore and destructive
workflows retain their controlling-plan owners.
