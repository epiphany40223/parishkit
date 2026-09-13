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

DAT-03 storage is complete; BG-01 is the next dependency-ready package. The
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
remain open. A source-semantics question about Ministry catalog presence versus
an explicit active-Ministry policy is awaiting the owner; unrelated source
worker work continues.

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
review round has run yet. The next source-authorization integration needs the
outstanding owner decision about Ministry catalog presence versus an explicit
active-Ministry policy; no policy choice has been silently applied. Runtime
wiring, complete setup, configuration/editor UI and their tests remain open.

The complete source/setup/configuration batch will receive at least three
independent review/fix rounds and passing local/PR CI before human merge
approval. Normal validation uses synthetic data and fake providers. Later
Family response, production delivery, publication, backup/restore and destructive
workflows retain their controlling-plan owners.
