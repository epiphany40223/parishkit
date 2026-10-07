# Stewardship background processing

All external calls, scheduled mail, large exports, publication, purge, backup,
and source refreshes execute outside web request processes. Interactive actions
create durable work and return a status link. The web application remains
responsive while workers run.

## Durable scheduling and task execution

PostgreSQL stores schedule definitions, occurrence records, task runs, and
outbox rows. Exactly one scheduler service scans due definitions at least once
per minute and inserts occurrences with unique idempotency keys. Celery/Valkey
delivers execution hints; workers always claim/check the PostgreSQL record
before acting.

Each scheduler scan also recovers lost broker hints from PostgreSQL: in bounded
batches it re-emits hints for due, unclaimed pending/retryable TaskRun,
occurrence, and outbox work, subject to its retry time, admission gates, and
owning service queue. Hint publication is retryable even when the durable row
already exists. Valid leases exclude duplicate claims; expired leases follow
the ordinary recovery policy. Provider-submitting or delivery-unknown mail
goes only to reconciliation, never automatic redispatch. Holds remain enforced.
Broker loss may delay work but cannot strand it because its original insertion
hint was lost; duplicate hints still refer to the same idempotent durable row.
A consumer first reads the hinted row without the work-order lock and drops
the hint unless the task is claimable (queued or retry-waiting, and due) or,
for recovery, abandoned or past its lease. Only then does it take the
handler's locks and repeat that check authoritatively. A row that becomes
actionable after the first read is due work that a later scan hints again.

A scan does not re-admit a TaskRun it published a hint for in the last 45
seconds while the row's version is unchanged, since that hint is still queued
or was just taken (#394). Any transition bumps the version and ends the skip,
and a lost hint is replaced once the 45 seconds pass, before the broker's
60-second hint expiry. The due-work health
sample still counts a skipped row as admitted, so overdue work is still
reported late. A skip does not recheck admission, so for up to 45 seconds after
a pause, close or mode change a row hinted just before it still counts as
admitted rather than held; nothing is published for it, and recovery needs
five minutes of clear scans in any case. Likewise, a hint refused during a
short pause leaves its row unchanged, so after the resume the row waits until
45 seconds after that hint was published. This memory lives only in the scheduler process: a restarted
scheduler admits and hints every due row again. While the
[bulk Family send](#bulk-family-send) is on, its consumers find their own due
rows, so a scan admits at most two of its rows per task type and page (one
wakeup per consumer process) and skips the rest the same way.

Ordinary Production campaign occurrences are created and claimed only when
global mode is Production and lifecycle/date/admission predicates permit them.
Testing rehearsal work is separately and immutably classified, never satisfies
a Production occurrence, and ordinary rehearsal work runs only for the current
`draft` campaign during its resolved campaign-local interval. Explicit Admin
page/email previews and the readiness test-recipient send are permitted outside
that interval; they never create or satisfy a live schedule occurrence.

An occurrence key identifies revision-specific work, for example
`mail:<campaign>:<schedule-uuid>:<revision>:<target>:<slot>:<mode>`. The stable
schedule fulfillment defined by the
[data model](../data/spec.md#schedule-revisions-and-fulfillment) prevents a new
revision, scheduler restart, or manual retry from creating a second successful
semantic delivery.

The following tables are the authoritative state/transition contract for
TaskRun and ScheduleOccurrence. Transitions not listed are rejected; an
idempotent repeated operation may return the existing state without adding a
second transition. Each transition records actor/worker, time, reason, attempt,
and expected row version/lease fencing. Domain-specific admission and safe-point
rules can restrict a listed transition, never bypass them.

SQL also binds every TaskRun write to the database login, because the worker
identity in a transition is supplied by its caller. Only the login whose
service executes a task type (the worker for the general queue, mail dispatch
for the mail queue) may claim or transition it. Web only creates tasks and
cancels an Admin's waiting cleanup task. The scheduler only cancels superseded
waiting source work. Any login may create only a type that some service executes.

| TaskRun state | Terminal? | Permitted next states and conditions |
| --- | --- | --- |
| `queued` | No | `running` on authorized claim; `cancelled` before execution |
| `running` | No | `succeeded` on verified task completion; `retry_wait` on safely retryable failure; `failed` on permanent failure or exhausted retries; `cancelled` at a verified safe point; `abandoned` after loss of the owning lease |
| `retry_wait` | No | `running` on authorized claim after retry time; `cancelled` at a safe point |
| `abandoned` | No | `retry_wait` after fenced recovery proves retry safe; `succeeded` after verified completion; `failed` after verified permanent failure/exhaustion; `cancelled` after proof of safe cancellation |
| `succeeded` | Yes | None |
| `failed` | Yes | None; explicit retry creates a linked new TaskRun |
| `cancelled` | Yes | None; later independently authorized work is a new operation |

Workers heartbeat and record phases/progress. Recovery of `abandoned` work
requires lease expiry, fencing of the former owner, and task-specific external-
request deadlines/reconciliation. Lease expiry alone never proves that an
external write failed or was cancelled. An unresolved effect must remain
durably represented as blocking work; a task performing only a reconciliation
handoff may complete once that handoff is durable, but the underlying uncertain
occurrence/outbox still blocks quiescence. Cancellation never claims to undo
committed effects or discards resumable domain checkpoints.

Automatic retries and safe abandoned-claim recovery reuse the nonterminal
TaskRun, appending attempt history and advancing fencing. An explicit permitted
retry of a terminal `failed` task creates a new linked TaskRun under the same
logical operation and unchanged domain request/checkpoints. Lock the retry
chain to allocate a monotonically increasing retry sequence and allow at most
one nonterminal run in that chain. A repeated retry command returns the same
allocated run; its derived execution key does not change the stable semantic
delivery/operation key. Original failed runs remain terminal. Recheck current
authorization, configuration, schedule revision, domain applicability, and all
admission gates before retry; a retry button is not a new-work exemption.

| ScheduleOccurrence outcome | Terminal? | Permitted next outcomes and conditions |
| --- | --- | --- |
| `pending` | No | `running` on authorized claim; `skipped` when safely inapplicable/cancelled; `coalesced` when atomically assigned a replacement |
| `running` | No | `pending` for safe retry or durable waiting work; `delivery_unknown` for unresolved provider acceptance; `succeeded` on verified completion; `failed` on definitive failure/exhaustion; `skipped` or `coalesced` only at a verified safe point |
| `delivery_unknown` | No | `succeeded` on evidence of acceptance; `pending` for a safe retry or explicitly authorized resend; `failed` only after definitive non-acceptance and permanent failure/exhaustion |
| `succeeded` | Yes | None |
| `skipped` | Yes | None |
| `coalesced` | Yes | None |
| `failed` | Yes for automatic scheduling | `pending` only through an explicit authorized retry of still-applicable work |

Occurrence retry preserves the same unique occurrence/semantic identity and
appends immutable attempt/transition history rather than reinserting the row
or erasing failure evidence. A failed-occurrence retry and its new TaskRun are
created atomically under the occurrence/fulfillment/retry-chain locks. Repeated
commands cannot create parallel attempts or bypass an already fulfilled slot.
Outbox retry preserves its one-row-per-semantic-delivery constraint: a still-
applicable `permanent_failure` may return to `pending` only through that explicit
authorized retry, appending a new numbered delivery attempt and preserving its
prior permanent-failure evidence. `delivered` and `cancelled` rows cannot be
reopened by retry. Recreate any required sealed substitution through the
ordinary authorized credential path, never resurrect scrubbed ciphertext or
expired tokens. Unknown-delivery resend uses the separately
audited numbered-attempt authorization below and preserves the original
uncertainty; it is never inferred from a timeout or worker crash.

`skipped` and `coalesced` include structured reasons and never count as delivery
success; `coalesced` references its replacement. Schedule replacement/removal
keeps old failures recorded and disables retry. Closed-campaign skips are not
resurrected on reopen. Task success means that task's work completed, not that
its linked mail was delivered. Aggregate occurrence state must reflect all
required delivery work; any unresolved acceptance remains `delivery_unknown`.

Archive, purge, schedule replacement, and recovery use these complete sets,
not ad hoc tests such as state unequal to `running`. Task nonterminal states
are `queued`, `running`, `retry_wait`, and `abandoned`; occurrence nonterminal
outcomes are `pending`, `running`, and `delivery_unknown`. Their terminal
complements do not establish semantic fulfillment: failed/skipped/coalesced
reporting work must still satisfy the separate post-close coverage policy.
In particular, a coalesced obligation remains dependent on its replacement's
resolution. Outbox, publication, export, and purge domain states retain their
own additional guards. Under a purge gate, safely cancelled TaskRuns/outbox
rows use `cancelled`, whereas safely cancelled occurrences use `skipped` with
reason `purge_preparation`; no occurrence `cancelled` state is introduced.

The restore-maintenance gate is also checked at durable task creation and
worker claim. While it is active, only tasks bearing an allowlisted maintenance
type may run on the restricted queue; ordinary tasks remain durable but
unclaimable. The authoritative allowed types and prohibited side effects are in
the [restore specification](../operations/spec.md#restore). Clearing the gate
atomically installs the state/mode-specific admission policy before normal
workers can claim preserved work.

For a `production` OutboxMessage linked to the current Campaign, dispatch also
locks/rechecks the durable live-delivery-pause version immediately before
provider submission. While paused, schedulers and submission transactions may
materialize idempotent due occurrences/receipts, but they attach a pause hold
and create no dispatch hint. A worker holding an older hint leaves the message
pending/held without counting an attempt. `operational` messages and explicit
test-recipient sends are exempt by immutable type; no Admin/caller flag can
claim the exemption.

Resume first computes the recovery/coalescing plan under the affected schedule,
occurrence, fulfillment, and outbox locks. Coalesced originals receive their
coverage rows, redundant pending messages become `cancelled`, distinct held
receipts remain selected, and only then does the transaction clear pause holds
and queue dispatch hints. Provider-submitting/unknown rows continue independent
reconciliation and are never selected as automatic duplicates. A race with
campaign close follows the post-close resolution workflow rather than sending
Family invitations/reminders.

Before creating or claiming campaign-scoped work, schedulers, web services, and
workers use the transactional campaign-work admission check. Existing queued/
retrying tasks are safely cancelled rather than claimed; running or externally
uncertain operations drain and reconcile before purge readiness. Purge
preparation/execution and its operational notifications are the only new
campaign-linked work exempt from the gate. The authoritative gate-active and
release states are defined by the
[purge data model](../data/spec.md#job-outbox-audit-and-purge-records).

The guarded purge UI may also create one purge-backup task after quiescence.
That task invokes the shared backup service asynchronously, records progress on
the PurgeRequest, and publishes a reference only after encryption, off-host
upload, manifest-digest verification, and a database snapshot at or after the
recorded quiescence instant all succeed. Retry uses the same request-scoped
idempotency key until an attempt succeeds; a partial upload never qualifies as
evidence.

All campaign schedules are evaluated in that Campaign's immutable IANA timezone
snapshot but persisted as UTC due instants. DST folds run once; nonexistent
local times run at the first valid instant after the gap. Changing a draft's
campaign timezone recomputes its resolved boundaries and future previewed due
instants before Production readiness. The timezone cannot change once the
campaign is scheduled, so live and historical occurrences are never rebucketed
or recomputed because the Parish default changes.

### Configuration activation holds

A task that meets a configuration
[activation in progress](../data/spec.md#parish-and-integrations) waits it
out at claim, at each effect's admission, at each transition and at recovery,
then continues; its lease renewal retries within seconds instead of stopping
the run. If the wait runs out, a task not yet claimed stays waiting for the
next hint, a ParishSoft refresh settles as a held retry
(`source_refresh_held`, at INFO) that does not use up its provider-failure
retry allowance, and any other running task recovers through its lease as
usual. Holds are logged at INFO or WARNING, never as an ERROR task
failure; a mismatch with no installer running keeps its ordinary ERROR.

### Worker queues and processes

Each consumer process executes one message at a time. The `worker` container
therefore runs two consumer processes so that long, provider-bound source work
never delays short work queued behind it
([#336](https://github.com/epiphany40223/parishkit/issues/336)):

| Process | Queues | Work |
| --- | --- | --- |
| Main worker process | `general`, `restore-general` | Exports, report facts and verification, operational collection and alert fanout, digests, campaign lifecycle and every other general task |
| Source process | `general-source` | ParishSoft refresh (full, delta and manual), setup source load, final setup load and setup staging cleanup: every task that holds the `SourceMutationLease` |

The handler registered for a task type names its queue; the scheduler publishes
each hint to that queue, and a process refuses a hint for a task type bound to
the other process's queue, leaving the durable row for the right consumer.
Both processes share the worker's configuration, credential mounts, SQL login
and broker identity. The main process starts the source process, forwards stop
requests to it so both drain within the container's grace period, and exits
if the source process exits or stays silent too long, so the container is
restarted as a whole. A stale heartbeat alone does not stop it: each late
observation is logged as a `helper_timed_out` entry (`source_helper`) with
the limit and the heartbeat's age, and only silence longer than twice the
container probe's limit stops the worker, logged the same way at `ERROR`.
Each process may hold a task connection, a lease-renewal connection and a
private timeout-log connection, so the worker login's limit is three times
the rollout overlap; because Compose stops a container before starting its
replacement, the overlap's slots serve the second process. The main
process's idle credential-acknowledgement pass runs only between messages.
A runtime budget whose worker limit is
below six keeps one process on all three queues. The source queue shares the
general queue's broker exchange and name prefix, so the Valkey ACL generated
at provisioning already grants it.

The `mail-dispatch` container likewise runs two mail consumer processes by
default, so the launch's Family mail is prepared and sent two messages at a
time. Unlike the worker's processes, both consume every mail queue, and each
has its own batched Family mail helper and SMTP connection. A hint reaches
whichever process takes it first. Each message is still exactly one Task:
its claim locks the TaskRun row, checks that it is still queued and advances
its fence, so a hint taken by both processes runs once and the other claims
nothing. With the [bulk Family send](#bulk-family-send) on, a process claims
several such Tasks at once, each still under its own claim and fence. The
main process supervises the second exactly as the worker
supervises its source process, logging late heartbeats and a kill past the
drain grace as `helper_timed_out` entries for `mail_helper` with no
`helper` field (SMTP helper kills of the same kind name their helper). The
sibling's drain is bounded by what is left of the container's stop grace
period less a margin, measured from the first stop request, so the kill
and its entry come before Docker's; the worker's source process follows
the same rule. A SYSTEMIC mail fault stops both mail processes. The mail
login's limit is three times the rollout overlap for the same reason as the
worker's. The deployment setting `mail_consumers` (1 or 2, default 2) and
its one-command override choose the count; a mail limit below six keeps
one process. The daily sending limit's count is read from PostgreSQL, so
both processes share it; the per-process outage and sending-limit holds and
their bounds are described in the
[Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers).

### Campaign lifecycle boundaries

The scheduler owns persistence of date-driven campaign transitions. On every
scan it inserts any due `start` or `close` CampaignBoundaryOccurrence with a
unique [execution-revision identity](../data/spec.md#campaign)
and queues an execution hint. A
restart scans from durable campaign state and creates overdue occurrences, so a
scheduler outage cannot permanently strand `scheduled` or `active` state.

The worker claims the occurrence, locks the Campaign and global current-
campaign record, and rechecks state, global mode, restore/purge gates, and the
resolved boundary. At or after start, `scheduled` becomes `active`; at or after
close, `active` becomes `closed`. Each successful or no-longer-applicable
occurrence is terminal and audited with intended boundary, actual transition
time, lag, previous/new state, and correlation ID. Transient failure uses the
ordinary durable retry/lease policy and emits operational alerts when boundary
lag exceeds the scheduler-health threshold.

For each Campaign, apply due boundaries in resolved-time order under the same
Campaign/global locks, irrespective of broker delivery order. If close arrives
while start is still unapplied, first materialize and apply the overdue start
and then close in one transaction, with distinct audit/occurrence outcomes.
An unapplied predecessor is not a reason to mark close no-longer-applicable.
Failure rolls back the ordered transitions and leaves recovery retryable. When
both are overdue, the intermediate active state is never externally visible
and does not dispatch campaign mail outside its interval. Restore release uses
the same ordered catch-up policy while its maintenance gate remains closed.

An end-date edit transaction replaces a not-yet-running close occurrence with
one with a fresh execution revision for the new resolved boundary, even when
that date was used previously. Replaced occurrences and task history remain
immutable; A → B → A creates three distinct execution revisions. It races safely
under the Campaign lock:
if closing wins first, changing the date requires the guarded reopen workflow.
The locked start date cannot be rescheduled after Production readiness.

If shortening the interval affects future Family-mail schedules, that same
transaction includes the complete Admin-selected reconciliation plan. Every
affected schedule receives a valid replacement revision or removal marker under
the schedule locks below, related cancellable work becomes terminal with the
specified reason, and the close occurrence is replaced only if every change can
commit. Provider-submitting or delivery-unknown affected rows reject the whole
transaction; no partial end-date or schedule change is visible.

Family portal access, submission, invitation, and reminder admission always
check both lifecycle state and the authoritative resolved half-open interval.
They deny that work immediately at close and never wait for the stored
transition; similarly, they do not admit it before start merely because a stale
state exists. Receipts and Admin daily/weekly reporting mail whose covered event
or local-day interval completed while the campaign was active remain eligible
for their explicit post-close hold/resolution and digest policies. Boundary
recovery therefore reconciles durable state and side effects without creating
an access or delivery gap.

### Reopen token preparation

The existing general worker executes the reopen-readiness token task through
the durable claim/lease machinery. It needs only public token-encryption keys,
never the mail-dispatch private keys. It pins the preparation inputs, generates
random tokens, seals them, and inserts generation-scoped records in bounded
batches with atomic checkpoints. Retry does not replace already prepared
tokens within the same valid preparation revision.

Completion verifies exact eligible-Family coverage and the pinned inputs before
marking the generation ready. Progress does not activate tokens or reopen the
Campaign. A stale/cancelled/superseded task cannot publish readiness or activate
a generation; failed/cancelled staging is scrubbed by idempotent cleanup. The
short activation transaction belongs to the
[Admin reopen workflow](../admin-portal/spec.md#reopen-and-archive), and durable
generation/invalidation rules belong to the
[data model](../data/spec.md#family-campaign-identity).

The same preparation service supports restore readiness on `restore-general`,
using a restore-instance/credential-epoch fence rather than reopening a
campaign. A superseded restore instance cannot publish or activate its work.
Its final activation belongs to the
[restore release workflow](../admin-portal/spec.md#restore-release).

Before submitting any credential-bearing message after restore, dispatch must
verify that sealed substitutions reference the currently admissible generation
and credential epoch. Stale material is scrubbed and never sent. Only after the
ordinary delivery-state, eligibility, schedule, pause, and restore-hold checks
authorize that same delivery may the worker re-render/reseal its credentials
from the new active generation. Preserve the outbox identity, semantic key,
attempt history, and delivery holds; this is not a new mailing. A restored
`submitting`/`delivery_unknown` outcome must be reconciled under the existing
uncertainty policy before any resend. If a required current token is unavailable
or the campaign is closed, follow existing credential-free/closed-mail policy
or block that rendering; never fall back to a restored token. Readiness tests
use only the separate rehearsal credentials.

### Production-transition cleanup

Starting Production cleanup atomically acquires the Campaign go-live gate and
creates one idempotent cleanup TaskRun. The campaign-work admission service
rejects new Testing submissions, test sends, campaign edits, and ordinary
Testing campaign work while the gate is held. Workers holding older hints
recheck the gate before mutation. Source refresh, cleanup itself, and
operational notifications are the only admitted background work.

The cleanup worker selects only rows captured by the request inventory and
deletes them in bounded transactions ordered by stable primary key. This
includes Testing submissions/workflows/audit detail, `testing_override` outbox
rows, and their Testing-only ScheduleOccurrence and ScheduleFulfillment rows.
Each batch commits its high-water checkpoint and deleted counts with the
deletion, making retry safe after interruption. The scan budget limits examined
inventory candidates, not just deletions after dependency filtering. A durable
numeric position in the sealed canonical inventory permits scan-only checkpoints
over blocked parents; later passes revisit them after their children are removed.
Retained scan positions contain no Family or Member identifiers. A complete pass
without any deletion fails instead of indefinitely renewing an unproductive task.
It validates campaign ownership
and immutable Testing routing on every batch, never follows broad cascades, and
cannot select live or operational data. Completion verifies that no inventoried
sensitive Testing detail remains before marking the request
`cleanup_complete`.

The worker never changes global mode or Campaign lifecycle. Those changes occur
only in the final Admin-confirmed transaction. Cleanup failure enters retry wait
with sanitized status. Exhausting automatic retries enters durable
`cleanup_failed`, retains the gate/checkpoints, emits a deduplicated CRITICAL
event, and offers explicit Admin retry or safe cancellation. Cancellation stops
at a safe batch boundary, releases the gate transactionally, and leaves
completed deletions intact.

### Schedule replacement and removal

Editing or removing a schedule is one database transaction that locks its
definition/current revision and related occurrence/outbox rows. Before the
Admin confirms, the UI presents counts for successful fulfillment, safely
cancellable work, terminal failures, and blocking in-flight/unknown work.

Changing the campaign timezone replaces affected schedule revisions. Changing
campaign start/end dates also replaces daily/weekly digest revisions, because
their date range determines the semantic slots; unchanged in-range Family
mailings retain their revision. Returning to an earlier date range creates a
new revision, never rewinds cancelled history, and still excludes fulfilled
semantic slots. The same preview, blocking and atomic cancellation rules apply.

Malformed/shared delivery bindings and contradictory occurrence/outbox outcomes
also block replacement. An occurrence writer cannot finalize an outcome that
contradicts its linked terminal delivery result, or mark work succeeded/skipped/
coalesced while the message remains pending/retry-wait. A later truthful provider
result must still be journaled. Existing conflicting history requires operator
investigation and separately authorized repair, not automatic rewriting, retry,
recall or a schedule-edit bypass. Coordinated dispatch/recovery owners must
reconcile their outcome handoff before allowing replacement.

The change is rejected while an old-revision outbox row is `submitting` or
`delivery_unknown`; the Admin must wait for provider submission to finish or
resolve the unknown result. Otherwise the transaction creates the replacement
revision or removal marker, changes every old-revision `pending` or `retry_wait`
outbox row to `cancelled`, and marks linked work that has not begun provider
submission `skipped` with reason `schedule_replaced` or `schedule_removed`.
Workers holding pre-submission execution hints recheck the locked durable state
and cannot submit cancelled work. Failed old-revision work is marked superseded
and loses its manual-retry action without rewriting its recorded failure.

Successful old-revision deliveries cannot be recalled and remain fulfillment
of the stable logical schedule. The new revision creates work only for semantic
targets/slots not already fulfilled; removing a schedule creates none. The
transaction records old/new revisions, all affected counts, and the confirming
Admin in audit history. Failure rolls back both the revision change and every
cancellation.

Missed occurrences catch up once when services recover. Before executing, the
worker rechecks global system mode, campaign state, and recipient eligibility.
Every missed occurrence retains its own durable outcome, but semantically
redundant mail is coalesced rather than delivered in a burst. A coalesced record
names the selected replacement occurrence and reason and transactionally writes
a `coalesced` ScheduleFulfillment for the original semantic slot. The coverage
row prevents a later schedule revision from rearming that slot and never counts
as provider success. A missed Family mail after campaign close is skipped with
a durable reason, while reports for completed campaign days use the recovery-
digest behavior below.

A semantic occurrence covered by an unreviewed or assumed-delivered
`RestoreDeliveryHold` is excluded from automatic missed-work selection and
coalescing. The hold does not satisfy provider-delivery statistics and does not
block a different future reminder schedule. A resend-authorized hold creates
one explicitly linked recovery occurrence; normal idempotency and ambiguous-
acceptance handling apply to that attempt.

### Activation catch-up

Direct draft-to-active Production activation commits one
`ActivationCatchUpDemand` and one durable task together with the lifecycle/mode
change; a broker hint is emitted only after commit and is recoverable by the
ordinary scheduler scan. The demand's unique activation-request key prevents
repeated confirmation or lost responses from creating another catch-up run.
Its cutoff is the activation instant, not the worker's eventual start time.
Reuse the shared missed-work planner, revision-specific occurrence keys, and
semantic fulfillment constraints; do not introduce a second kind of scheduled
email or delivery identity.

The general worker enumerates targets/slots through the cutoff with stable
keyset cursors and bounded transactions, defaulting to at most 100 occurrence
outcomes per batch. Persist occurrence/outbox/coverage changes and progress
checkpoints atomically. No batch performs provider calls or holds the global
activation locks while scanning the corpus. For a Family with many overdue
schedules, or a digest covering many days, stage the coalescing decision and
coverage across bounded batches; release no selected message until all of that
group's covered slots are durable. Interrupted retries resume the same demand
and checkpoints; explicit failed-task retry uses the canonical linked retry
chain without resetting the demand or duplicating semantic work.

While the demand is unfinished, all initial/reminder and scheduled Admin-digest
dispatch for that campaign observes a durable preparation hold, including mail
materialized by ordinary scheduler scans or source-triggered catch-up. Check
the hold at claim and immediately before provider submission; it is not a
client flag or merely missing broker hints. Concurrent producers use the same
planner, schedule/fulfillment locks, and occurrence keys and cannot dispatch an
older choice before coalescing completes. Direct submission receipts and
operational notifications are not held by this preparation hold, but retain
all their existing pause/restore and other admission checks.

Recheck current schedule revisions/removal markers, semantic coverage, live
responses, recipient eligibility, campaign dates, and other admission gates
as each group is processed. A removed/superseded schedule cannot be revived by
its pinned activation version. Work newly due after the cutoff belongs to the
ordinary scheduler; it remains held until preparation completes, then follows
ordinary missed-work/coalescing checks before dispatch rather than sending a
backlog blindly. If the campaign closes first, remaining Family invitations/
reminders receive durable skipped outcomes, while required completed-day and
weekly reporting follows its existing post-close policy.

When replacement cancels an aggregate that already has committed coverage,
retain and follow the [immutable recovery lineage](../data/spec.md#schedule-revisions-and-fulfillment).
Restarting enumeration under current configuration must not drop the dates
already assigned to its predecessor or reinterpret that predecessor as delivered.

Completion verifies all cutoff work has durable outcomes/coverage, records
aggregate counts, and atomically marks the demand complete and releases only
its preparation hold. Ordinary scheduling recovers dispatch hints afterward;
other holds remain effective. Failure leaves the demand unfinished, preserves
checkpoints/hold, and exposes retry with normal task escalation. Archive and
purge quiescence include the demand independently of TaskRun terminality; an
empty occurrence table or terminal failed task is not proof of completion.
Restore preserves the demand and ordinary work remains paused by the restore
gate until its existing release policy permits recovery. This workflow changes
direct activation catch-up, not the separate restore-release confirmation or
live-delivery-pause semantics.

### Mode routing

Testing routing is global for campaign communication: Family mail, submission
receipts, Admin campaign digests, manual report mail, previews, and test sends,
including the chosen-Family test sends (outbox purpose `family_test`, Testing
mode only), which never create or satisfy a scheduled occurrence. The general
worker's `family_mail_test` task prepares each one; a restore review, a campaign
work gate or a dirty or stale source population holds it without spending its
retry budget, while a lasting loss of scope, a Family that stays ineligible
after a clean reconciliation or preparation that keeps failing cancels it, so
no test message is left unfinished. A scheduler sweep settles tickets whose
task ended.
Those messages have routing class `testing_override`; the envelope has only the
single configured Testing address, and the subject and both body alternatives
prominently identify Testing and safely name the intended recipients. Production
applies none of these overrides.

Safety-critical operational notifications are exempt. CRITICAL alerts and
privileged backup, restore, publication, and purge outcome messages have routing
class `operational` and are sent individually to every current Admin exact
address regardless of global mode; optional Slack routing is likewise
unchanged. Their subject identifies the current deployment mode, but their
content contains no Family/Member data, credentials, rendered campaign content,
or access links. Classification is fixed by notification type rather than an
Admin-editable template or caller flag.

## ParishSoft refresh

A singleton durable PostgreSQL `SourceMutationLease` covers full, delta, and
manual refresh plus ParishSoft publication execution. Its row stores owner task,
monotonically increasing fencing token, phase, heartbeat, and expiry. Claim,
renewal, release, and takeover use short row-locking transactions; no database
connection is held while waiting on ParishSoft. No refresh may run concurrently
with another refresh or with publication writes.
Every task that holds this lease runs on the worker's separate source process
([worker queues and processes](#worker-queues-and-processes)), so a refresh
never delays exports or operational collection.

The owner heartbeats throughout external work and revalidates its fence before
each upstream write and immediately before snapshot promotion. Loss of ownership
stops further calls and prohibits promotion. Takeover is allowed only after both
lease expiry and the configured maximum external-request timeout plus safety
margin, limiting overlap with a request initiated by an abandoned owner. An
ambiguous already-started ParishSoft write still follows publication
reconciliation rather than being assumed undone. Publication preflight merely
records a source version in a short transaction; it holds no mutation lease or
database lock while fetching data or awaiting human confirmation.

### Refresh schedule

Scheduled refreshes follow one daily schedule of parish-local times, each
marked **Full** (a [full cycle](#full-cycle)) or **Quick** (a
[delta cycle](#delta-cycle)) ([#632](https://github.com/epiphany40223/parishkit/issues/632)).
The Administrator edits it on the
[ParishSoft refresh schedule settings](../admin-portal/spec.md#parishsoft-refresh-schedule-settings);
the research behind this design, its open decisions and its delivery order
are in the [refresh schedule plan](../../../plans/stewardship/refresh-schedule.md).
How old the data is, and when that alarms, follows from this schedule as
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)
defines. A schedule saved before this form keeps its slots, slot identities
and commands until the Administrator changes it (see
[stored schedule and upgrade](#stored-schedule-and-upgrade)); how its data
age is judged and what the bulk-send hold does change for every schedule.

#### Schedule entries and rules

The schedule is built in layers, in this order, as an iCalendar recurrence
set is built from rules, added times and excluded times:

1. **Rules.** "Full or Quick, every N minutes from a start time to a last
   time": N is 15, 30, 60, 120, 180, 240, 360, 480 or 720, the times run from
   the start time in steps of N up to and including the last time, and the
   last time is at or after the start time (a schedule that crosses midnight
   uses two rules). Times are counted from the rule's start time, not from
   midnight.
2. **Single times.** "Full or Quick at a time".
3. **Exceptions.** Times, or from–to ranges whose start is included and end
   excluded and whose end is after the start, that remove any time of either
   kind, for example "except 12:00" or "skip from 22:00 to 23:00".
4. **Precedence.** A time that is both Full and Quick is Full: a full
   refresh in the same slot replaces the quick one.
5. **Coverage.** A quick time that a rule generated is left out when a full
   time falls at or after it and less than one rule step after it (for the
   rule's last time too, so the bound never depends on a next time that does
   not exist), or less than 15 minutes before it: the full refresh covers
   it. Coverage compares times on the same day and never wraps past
   midnight, so a full refresh at 00:00 does not cover a quick update at
   23:00. With hourly quick updates on the hour, an hour that has a full
   refresh has no quick update
   ([#630](https://github.com/epiphany40223/parishkit/issues/630)).
6. **Automatic exclusions**, decided when each slot falls due (see
   [skipped around Family emails](#skipped-around-family-emails)).

Layers 1–5 give the **daily times**: at least one full time, and at most 96
times in all, which the spacing rule already implies. There is no other cap;
the [settings page](../admin-portal/spec.md#cost-and-freshness-summary) shows
the cost instead. New times are on the quarter hour (:00, :15, :30 or :45);
a full time already stored off the quarter hour (say 02:10) is kept while the
Administrator keeps it, but no new one can be added. The earliest full time
is the **nightly refresh**, which never waits for a Family send; every other
full time is a daytime refresh that
[waits for a bulk Family send](#deltas-wait-for-a-bulk-family-send). The
schedule is the same every day of the week.

#### Spacing and coverage

Any two daily times must be at least 15 minutes apart, measured on the wall
clock around midnight (23:45 and 00:00 are 15 minutes apart and allowed; a
kept 23:50 and 00:00 are 10 minutes apart and refused). Refusals name the two
conflicting times. A rule's quick time that coverage leaves out is not a
conflict: the preview shows it as covered by the named full refresh. Every
other pair of daily times closer than 15 minutes is refused. On a
daylight-saving day two times may resolve to instants closer than 15
minutes, or to the same instant; refreshes still never overlap, because the
mutation lease serializes them.

#### Skipped around Family emails

When the schedule's **skip refreshes around Family emails** setting is on
(the default for a schedule saved on the new settings page; a document
without `refresh_rules`, that is every existing schedule, has no such
setting and never skips, so nothing changes until an Administrator saves a
schedule with it), the deployment is in Production mode and Production delivery
is not paused, the scheduler skips a scheduled refresh (other than the
nightly) whose due instant falls inside a Family email's window. A window
includes its start and excludes its end:

- **Reminder:** from the
  [lead window's](#preparing-ahead-of-the-due-time) start (two hours before
  the due time) to the end of its send.
- **Initial invitation:** from its due time to the end of its send, since it
  is not prepared ahead.
- **End of the send:** while the occurrence still has active Family send
  work, the window stays open, but never past its due time plus the
  two-hour send allowance (`SEND_ALLOWANCE`). Before its due time, and once
  that work is done, the end is the estimate: the due time plus the
  **estimated send length**, rounded up to 15 minutes, at least one hour and
  at most that same allowance. A send that is retrying or stuck therefore
  cannot keep refreshes skipped: from the cap on, scheduled refreshes are
  decided as usual, the
  [bulk-send hold](#deltas-wait-for-a-bulk-family-send) holds them while the
  send is still active, and the alarm applies with that hold's allowance.
- **Active work for one occurrence:** the hold counts active work globally;
  the window counts the same states for this occurrence only, that is Family
  messages whose semantic key is this occurrence (pending and not paused,
  waiting to retry or being submitted) plus this occurrence's preparation
  tasks (queued, running or waiting to retry), with the same minimum of 10
  pieces below which a send's tail no longer counts.
- **Estimated send length:** for the campaign's most recent completed
  Production occurrence of the same kind (initial invitation or reminder),
  else of the other kind, the time from its due time until its last Family
  message reached a final state; one hour when the campaign has none.

Windows come from the current revision of each upcoming or still-sending
scheduled Family email of the current campaign. While Production delivery is
paused nothing is skipped, as the bulk-send hold already lets a paused send
keep its refreshes; once delivery resumes, a still-sending occurrence's
window applies again. Testing mode has no windows.

**The decision is made once and kept.** Windows change as schedules move and
sends finish, so the scheduler decides each slot when it first falls due and
records the decision durably in the **slot decision record** (the slot
identity, due instant, kind, the decision `skipped` or `held`, and the
window's cause for a skip), in the same transaction in which it would
otherwise have created the slot's refresh. Each loop looks at every due,
undecided slot (one with no refresh and no decision, today or yesterday),
including the slots that fell due while the scheduler was down, which are
decided against the windows as they are when the scheduler returns. Inside a
window such a slot is recorded as skipped, and held by the bulk-send hold it
is recorded as held; otherwise only the latest full slot and the latest
quick slot get a refresh, and older undecided slots stay undecided, with no
row and no refresh. There is no backlog replay. A slot that is recorded as
skipped is never created later, and a slot whose refresh exists is never
skipped later. A slot recorded as held stays due: if a moved reminder's
window later covers it, it is not turned into a skip, and it can still run
as the catch-up. The
[data-age alarm](../operations/spec.md#parishsoft-data-age-and-connection)
treats a recorded skip as "not due", so a later change to the windows cannot
turn a deliberate skip into a missed refresh. A skipped refresh is not moved,
and nothing replaces it: when the latest due full (or quick) slot is
skipped, the scheduler creates no full (or quick) slot for it, rather than
falling back to an older one, and the next scheduled time runs as usual.
The one exception is a held slot: when the hold ends and the latest due slot
of its kind was skipped, the newest held slot of that kind is created as the
catch-up, because it was due and the alarm is counting it.

**Skipped and held are different.** In each loop the scheduler first applies
the windows, then the
[bulk-send hold](#deltas-wait-for-a-bulk-family-send), to a slot that is
due, undecided and not the nightly:

1. inside a window: the skip is recorded, and the slot is **not due**: it
   never runs and the alarm ignores it;
2. otherwise, held by the hold: the hold is recorded, and the slot **is
   due**: once the hold ends it runs as the catch-up if it is still the
   latest due slot of its kind, or the newest held one after a skipped
   latest slot, and is otherwise superseded by a later slot's refresh; the
   alarm counts it with the hold's allowance;
3. otherwise, and only for the latest due full slot and the latest due quick
   slot, its refresh is created; an older slot stays undecided.

**The slot decision record** is a table written only by the scheduler, its
`slot_key` unique, so a slot has at most one decision and a held slot can
never later be recorded as skipped. Each row carries the same inputs as the
tick its slot would have, and its configured time follows the tick's rules:

- a full slot at a listed time: that time, which is part of the slot key;
- a legacy hourly or quarter-hour full slot: the nightly time, as its tick
  records, which is also part of the key;
- a quick slot, listed or legacy: none in the key, as today; the row, like
  the tick, stores the nightly time;
- a schedule-change catch-up (see
  [ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)):
  none, with cause `catch_up`.

A database guard on insert requires the same scheduler and work-order
ownership as the refresh-tick guard, a `slot_key` that equals the identity
derived from the row's inputs, the same cadence check the tick guard makes
for the row's cause (a listed full or quick time resolved through the shared
daylight-saving rules; for a legacy schedule a UTC quarter hour or hour as
`full_refresh` or `delta_refresh` says; for `catch_up` the instant the
applied schedule took effect), and a due instant that is not in the future;
it refuses a skip for a slot that already has a tick, and the refresh-tick
guard refuses a tick whose slot was recorded as skipped (a held slot's tick
is admitted). Rows are removed by the
[temporary retention housekeeping](../operations/spec.md#temporary-retention-and-housekeeping)
after eight days, beyond the seven-day preview and every lookback that reads
them.

The nightly refresh is never skipped; when it falls inside a window the
settings page says so and the existing lead-window WARNING still fires (see
[preparing ahead of the due time](#preparing-ahead-of-the-due-time)). These
windows are planning; the [bulk-send hold](#deltas-wait-for-a-bulk-family-send)
still applies on top of them to any send that is actually running.

#### Slots, identities and daylight saving

Every daily time, Full or Quick, is a parish-local wall time resolved for
each day through the shared daylight-saving resolver: a repeated time (clocks
go back) runs once, at its earlier instant, and a time in the missing hour
(clocks go forward) runs at the first real instant after the gap. On the day
clocks go back, quick times in the repeated hour therefore run once; on the
day they go forward, times in the missing hour coincide, and a full time
there runs as today (the nightly wins a tie, then the later wall time).

The scheduler still creates at most one full and one quick slot per loop:
the latest full time and the latest quick time that have fallen due, today
or yesterday, unless that latest slot is skipped (see
[skipped around Family emails](#skipped-around-family-emails)); a quick slot
due with a full one joins it. After downtime only those latest slots run. Slot identities
keep their existing inputs (the source scope, time zone, cause, due instant
and, for a full slot, the configured time), so:

- a full slot's identity does not change while its configured time is kept;
- a quick slot at a local quarter hour has the same identity as the old
  quarter-hour slot at the same instant, which holds in every time zone
  whose offset is a whole number of quarter hours.

The database's refresh-tick guard accepts a quick tick only at a listed quick
time when the schedule lists them, resolved the same way, as it already does
for full times (#465). That change and the slot decision record are two
forward migrations, the guard change first and the table with the
exclusions, under the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy).

#### Stored schedule and upgrade

The schedule is stored in the ParishSoft integration's settings as optional
keys added to the `source-cadence-v8` settings, as #465 added its own, so
every earlier document stays valid:

- `full_refresh_times`: the sorted, unique daily full times, with no cap of
  eight; the earliest is also `nightly_time`;
- `quick_refresh_times`: the sorted, unique daily quick times, none of them a
  full time, present only when there is at least one;
- `delta_refresh`: `times` when there are quick times, `off` when there are
  none;
- `full_refresh`: `daily`;
- `refresh_rules`: the rules, single times, exceptions and the skip setting
  as the Administrator entered them, so the page can show and edit them.

The v8 schema checks only the shape: canonical `HH:MM` times, sorted and
unique lists, the nightly time as the earliest full time, no time in both
lists, a closed set of rule fields and 15-minute spacing. Whether the lists
are what the rules produce, and the quarter-hour rule for new times, are
checked by the settings form and the command line, which share one
validator; a stored document is never re-derived. Every place that lists or
reads the cadence settings learns the new keys and values:

- `CADENCE_SETTINGS` and `uses_cadence` (`accounts/source_cadence_schema.py`,
  the v8 validator) and the v8 patch, operator-recovery and
  credential-request variants;
- `NOT_KEY_SCOPE` (`accounts/integration_selection.py`, used by
  `accounts/integration_credentials.py`): refresh timing is not part of an
  integration key's scope;
- `source/cadence.py`: `DELTA_REFRESHES` gains `times`, and
  `MAX_FULL_REFRESH_TIMES` and the cap in `canonical_times` go;
- `source/refresh_status.py`, whose schedule reader returns nothing for a
  frequency or quick setting it does not know, so it would hide the refresh
  status of a schedule with `delta_refresh: times`;
- `accounts/integration_views.py`: `_with_defaults` and
  `_retain_unused_time`, which fill and keep the schedule settings on save;
- `admin_reads.py`: the Home read model's refresh fields (`frequency`,
  `delta_refresh`, `next_full_at`);
- for the schedule-change catch-up's cause `catch_up`, every place that
  hard-codes refresh causes: `REFRESH_CAUSES` in `source/refresh_models.py`
  and its `source_refresh_command_cause` check (also in `schema/tables.sql`),
  the refresh-tick guard's `cause NOT IN ('nightly','delta')` test and its
  per-cause branches, and `_waits_for_send` in `source/production.py`, which
  must test for `catch_up` explicitly and hold it by its cause, since its
  tick records the nightly time like the nightly refresh's, which never
  waits.

A release that
predates this change refuses a document with more than eight full times or
with the new keys, so after an Administrator saves such a schedule, going
back to an older image means restoring a backup, as for any post-launch
migration.

No stored document is rewritten. A document without `refresh_rules` is an
existing schedule, and its slots are created exactly as before: the listed
full times, or a full refresh every UTC hour or quarter hour, and quick
updates every UTC quarter hour, every UTC hour or none; a parity test proves
identical slot keys, due times and commands. The settings page shows such a
schedule converted to the equivalent rules, and writes the new keys only when
the Administrator changes the schedule itself; saving other settings keeps
the stored keys as they are. When the schedule is changed, what differs from
the old one is said before saving:

- an hourly or quarter-hour full refresh becomes full times every 60 or 15
  minutes; today every such slot records the nightly time and is never held
  for a send, while as listed times each slot records its own time (a new
  slot identity, so the slot due at the switch may run once more) and every
  one but the nightly waits for a send;
- in a time zone whose offset is not a whole hour, hourly refreshes move
  from the UTC hour to the local hour.

### Delta cycle

At each scheduled quick time (see the [refresh schedule](#refresh-schedule)),
the system calls the supported v2 Family changes feed using a durable
watermark. Because that feed is not a complete Member/Ministry/giving change
stream, it is an optimization, not the sole correctness path.

That feed, `families/change/list` (FamilyChangeList), logs changes to a fixed
set of Family-level contact and registration fields only: address, phone,
email, registration status and registered parish. It does not report Family
creation, Family group or participation status, Send No Mail, Members or
Member emails, ministries or giving, so those reach the application only
through a full refresh. In the first week of production every delta came back
empty while such edits were made in ParishSoft
([#465](https://github.com/epiphany40223/parishkit/issues/465); the
[API analysis](../../../parishsoft-api-analysis.md) records the endpoint's
contract). Administrators are told that ParishSoft changes appear after the
next full refresh or **Refresh now**.

For each delta indication, reload every affected Family and related Members/
contacts available through supported endpoints. If the feed/cursor is
ambiguous, discontinuous, unsupported, too large, or indicates relationship
data that cannot be safely scoped, promote no delta and queue a full refresh.
Ministry/fund data remains from the coherent prior full snapshot unless the
delta loader can prove a complete replacement.

#### Deltas wait for a bulk Family send

Under send load a delta takes about five minutes and halves the Family send
rate, because its promotion and population rebuild compete with the send for
the global work-order lock (#440). So while an initial invitation or reminder
is being sent, the scheduler holds the delta slots, and likewise a
[scheduled full refresh](#full-cycle) at any configured time other than the
nightly one (#465), which competes for the same lock. "Held" here always
means "due, and run later as a catch-up"; "skipped" is kept for the
[recorded skips around Family emails](#skipped-around-family-emails), which
never run. A send is in progress
while at least 10 pieces of its work remain, counting messages pending (not
paused), waiting to retry or being submitted and preparation tasks queued,
running or waiting to retry, read from durable state with no lock taken.
While Production delivery is paused only messages count, so a paused send
keeps its deltas.
The nightly full refresh, a legacy hourly or quarter-hour full refresh (see
[stored schedule and upgrade](#stored-schedule-and-upgrade)) and every
[manual request](#manual-request) still run.

A held slot creates no command, task or failure. Each scheduler loop decides
again, so the first loop after the hold ends creates the current slots'
refreshes, which catch up. The scheduler logs `source_refresh_held` at INFO
to the process log once per held slot, correlated to the slot's command
identity. Since delivery step 1 of the
[refresh schedule plan](../../../plans/stewardship/refresh-schedule.md#delivery-plan),
for a held **full** slot (a daytime time or, from step 2a, a `catch_up`),
never a quick one, it also writes one durable operational entry through the shared
operational-log writer, inside the slot's command correlation: the event
`source_refresh_held` at INFO with the task-free `schedule` context schema.
"Once per held slot" is per scheduler process, so a restart during a hold
writes another entry, which is harmless. The `source_refresh_held` entries
that refresh tasks already write durably for their own held retries (lease
contention, an activation in progress, a scope change) use the `task`
schema and say nothing about a send, so the health check filters on the
event **and** the `schedule` schema. The worker, which runs the health
check, needs a column grant to read that schema: `SELECT (schema)` on
`stewardship_operational_log`, added to the worker's grants registry
(`jobs/grants.py`) and applied by the upgrade's database-grants step, not a
migration. Reusing an existing event and schema needs no change to the
operational log's event check; if the `schedule` schema's safe-context
check cannot carry this entry, a new event (for example
`source_refresh_send_held`) is added instead, with a forward migration of
that check. Neither is CRITICAL, so neither opens an incident. Once the
[slot decision record](#skipped-around-family-emails) exists, the scheduler
also records the slot as held there.

Holding is bounded, and the bound is measured from the
[data-age alarm](../operations/spec.md#parishsoft-data-age-and-connection)'s
own point: the **overdue full slot**, as
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)
defines it (it ignores recorded skips and due times before the schedule
took effect), and the alarm would sound the lateness margin after its due
time. While a send is in progress and scheduled refreshes are actually
being held, the alarm allows two hours
beyond that point. "Being held" is measured from the overdue full slot's due
time, not from when the newest full refresh was read, so quick updates that
ran before that due time do not cancel it: no scheduled refresh of either
kind (held daytime full slots count as well as quick ones) was requested at
or after the overdue slot's due time and before the resume point, though
scheduled refreshes ran within the last day. So the allowance also applies
when quick updates are off, which counting deltas alone would miss. The
scheduler holds only until the resume point, `RESUME_LEAD` before the
allowance ends. `RESUME_LEAD` must leave room for a held full refresh to run
and promote under send load, not just a quick update; it stays 30 minutes
only if a full refresh under send load is measured to promote within 20
minutes, and is raised otherwise. A send that runs longer than that gets its
refreshes back, and a held daytime full refresh then runs mid-send.

**The catch-up keeps the allowance.** When the hold ends, for any reason
(the send finished, fell below the minimum, was paused, or reached the
resume point), the first loop requests the catch-up full refresh for the
latest due full slot. From that request until the catch-up promotes or
fails, the allowance still applies, even though a scheduled refresh has now
been requested after the overdue slot's due time; it never extends past the
allowance's end. The health check is stateless, so it recognizes a catch-up
only from durable evidence that the scheduler held some full slot after the
overdue slot fell due, not only the slot the catch-up was created for. That
covers a send ending just before a loop, scheduler downtime across a full
time, a send ending while the resume-point catch-up runs, and a held
`catch_up` slot. The evidence is a `source_refresh_held` entry with the
`schedule` schema created at or after the overdue slot's due time, with no
scheduled full refresh requested between that due time and the entry. A
request in that span means the overdue slot was not held but ran, and is
late on its own account: with full refreshes at 08:00 and 08:15, an 08:00
refresh requested on time that hangs is not excused by a send that then
holds the 08:15 slot, and the alarm sounds at 08:30. No slot identity is
derived or matched, so a change of schedule, source scope or time zone
during the hold does not lose the evidence. Once the slot decision record
exists, a `held` full or `catch_up` row with a due instant in that span
counts as well. A task's own `source_refresh_held` entry (`task` schema) is
never evidence, and a quick slot's hold writes none. A full refresh with no
evidence, such as an on-time refresh that hangs or one that waited for the
source lease, gets no allowance. Unlike the allowance during the send, the
catch-up rule does not require `family_send_active()`: the send has usually
ended by then. A catch-up that fails, or a scheduled full refresh
requested in the held span that never promotes, alarms without the
allowance. Without this rule a send ending before the resume point would
raise `source_stale` at once and keep it up for the full refresh's run of
about seven minutes. The old staleness check has the same, shorter gap
for its two-minute quick catch-up; under data age quick updates no longer
drive the alarm, and this rule covers the full catch-up, so neither gap
remains.

For example, take full refreshes at 08:00, 10:00 and 12:00, quick updates
every 15 minutes, the default margin, and a send that starts at 09:30 and is
not inside a [window](#skipped-around-family-emails) (say, the exclusion
setting is off). The 09:30 quick update runs or is held as the send begins;
either way it was due before 10:00 and does not count. The 09:45 quick
update and the 10:00 full refresh are held. The overdue full slot is 10:00,
so the alarm would sound at 10:30; nothing was requested from 10:00 on, so
the hold's allowance applies and the alarm waits until 12:30, with the
resume point at 12:00.

- If the send ends at 11:00, the next loop requests the latest due slots:
  the 10:00 full refresh and the 11:00 quick update. The full refresh is the
  catch-up, so the allowance holds until it promotes about seven minutes
  later, and the alarm never sounds.
- If the send is still running at 12:00, the hold ends at the resume point.
  The latest due full slot is now 12:00, so the 12:00 full refresh runs
  mid-send as the catch-up and keeps the allowance until it promotes; the
  10:00 slot is never created.

The [automatic exclusions](#skipped-around-family-emails) are different: a
refresh skipped there is recorded and was never due, so it neither needs nor
uses this allowance, and it does not run later. Sending
never waits on source age: Family preparation requires only that the
population match the current source generation, which a held refresh leaves
unchanged.

### Full cycle

A scheduled full refresh runs at each full time of the
[refresh schedule](#refresh-schedule); the settings page's default is the
nightly refresh at 02:00 with quick updates every hour, while a document
that names no schedule keeps today's defaults (02:00, quick updates every
15 minutes). A scheduled full tick records the
configured time it fell due at, and the database's refresh-tick guard
accepts only a listed time (#465). An existing schedule saved before that
form may still run a full refresh every UTC hour or quarter hour, which
replaces the delta cycle. Quick updates no longer decide whether the data
counts as current, so the settings page accepts any quick-update choice,
including none (see
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)).
A held daytime full refresh still runs mid-send once the send outlasts the
[hold](#deltas-wait-for-a-bulk-family-send). A full refresh also runs on
initial setup and manual request. Scheduled refreshes
never overlap: the mutation lease serializes execution and a waiting full load
absorbs later requests. The Admin home page, the ParishSoft settings page and
the manual refresh page show the last successful full refresh, a newer failed
one, whether one is running, the last incremental update and when the next
scheduled full refresh is due (skipping any
[excluded window](#skipped-around-family-emails)), with the data age and
connection wording that
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)
defines. A failure notice links to the failed run's task
details, says whether the incremental updates are still succeeding, says that
new Families, status and Send No Mail changes, Members, Ministry rosters,
Ministries and giving wait for the next full refresh, and
disappears once a later full refresh succeeds. Admins who may change the
configuration also get a "Run a full refresh now" button there, which submits
the [manual refresh](../admin-portal/spec.md#manual-parishsoft-refresh). A scan that shifted between
pages (a record repeated from an earlier page, or a total or row order that
changed mid-read) is retried within the bounded provider-failure allowance
rather than reported as invalid data. It uses shared
`load_families_and_members` with active/inactive data sufficient for transition
recognition. Giving detail is limited to the financial and comparison periods
of the sole current campaign while it is `draft`, `scheduled`, `active`, or
`closed`. A closed campaign therefore continues receiving current giving data
through reconciliation and cannot be displaced by successor preparation;
single-campaign sequencing prohibits a successor until archive. Archived and
older campaigns read their immutable retained snapshots and never expand the
nightly source window.

The cycle:

1. Validates the expected ParishSoft organization without cache.
2. Loads source collections into staging with bounded shared retries.
3. Normalizes IDs/dates/emails/relationships and validates referential
   integrity, uniqueness, pagination completeness, and plausible counts.
4. Compares core record counts with the last successful full snapshot, and
   derived eligibility counts (portal-eligible and email-eligible Families,
   Families with an active head, and contacts with a valid email) with both
   the last full and the current snapshot, using the same drop threshold
   (25% by default). Every record can stay while the fields eligibility
   depends on disappear, so both are checked. The last full snapshot bounds
   the loss accumulated over a series of deltas. Any count falling to zero
   is refused, even from one or two. An operator accepts a known large change
   for one refresh by raising the threshold, as the
   [launch runbook](../../../guides/stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change)
   describes. Every count is checked before the load is refused, and the
   refused attempt records all of them, failing or not, with their before
   and after values and the limit
   ([accept a large ParishSoft change once](../admin-portal/spec.md#accept-a-large-parishsoft-change-once),
   ADM-13). The refusal's log line names the first count that fell and its
   before and after values, never record data.
5. Builds derived eligibility, roster, giving, and reconciliation data.
6. Promotes all staged data atomically as defined by the
   [data specification](../data/spec.md#source-snapshot).
7. Records counts/duration/deltas and releases the lease.

Unexpected empty/large-loss data fails closed and emits CRITICAL rather than
making it current. A load failure preserves the prior truth. Cache entries are
tenant scoped and cannot substitute for the explicit uncached organization
guard.

### Manual request

An Admin manual request queues one full refresh after any active poll. Multiple
requests coalesce. The Admin status view identifies who requested it and every
phase. Manual does not bypass validation, retry, lock, or atomic promotion.

## Family invitations and reminders

When the current campaign has a banner (see
[campaign images](../admin-portal/spec.md#parish-and-integration-configuration))
and the email does not hide it, the HTML part of each initial, reminder and
receipt email opens with it (after any Testing notice), sized at most 600 pixels wide and loaded from the public
HTTPS origin with the campaign name as its text alternative. The banner is
server-built markup; parish-authored email content admits only
[hosted images](../hosted-files/spec.md#inline-images) through file
placeholders, and the plain-text part carries no banner.

At each configured schedule, the scheduler considers current active registered
Families. One personalized message is due only when:

- the campaign is open and the global mode matches the occurrence's mode;
- the Family is currently eligible for mail;
- it has no effective live submission for live mail; and
- that Family/schedule occurrence has not succeeded.

An occurrence is materialized for every otherwise qualifying Family. At
execution, an Email-deliverable Family has at least one unsuppressed valid email
among active `get_family_heads()` Members and may receive an outbox row. A
Family without one receives no outbox row; its occurrence terminates as
`skipped` with the non-error reason `no_deliverable_recipient`. Permanent
refusals therefore cannot create empty-recipient messages or systemic-provider
failures, while the skipped occurrence preserves reporting and recovery state.

### Family schedule sweep

The scheduler plans a Family again only when something its plan reads may
have changed, or when the campaign clock reaches a time that may change the
answer ([#640](https://github.com/epiphany40223/parishkit/issues/640)).
At most every 15 seconds it reads two fingerprints, without the work-order
lock and with the scheduler's existing grants:

- **Campaign-wide:** the runtime row's version (mode, configuration, current
  campaign, restore review), the campaign's version (state, pause, production
  cycle, configuration), the credential row's go-live gate and rehearsal
  pointer, rehearsal epochs, work gates, unfinished catch-up demands, and every
  schedule definition's version (a new revision moves it). A change queues
  every Family.
- **Clock edges:** the configuration's start and end, each current invitation
  and reminder revision's due time and, on the [bulk path](#bulk-family-send)
  in Production, each reminder's [lead-window](#preparing-ahead-of-the-due-time)
  start. When the campaign clock reaches the next edge, every Family is
  queued. Edges are absolute instants on the same campaign clock planning
  uses, so a daylight-saving change neither skips nor repeats one. The
  campaign clock only moves forward in a deployment; a test that moves it
  back past an edge needs a new scheduler producer before that edge wakes
  the sweep again.
- **Per Family:** the Family row's planning columns (active, email eligible,
  email deliverable, effective response), its eligibility history, its
  responses, the count and version sum of its occurrences and restore holds,
  and the count of its fulfillments. A change queues that Family alone. A
  response, an eligibility or deliverability change from a source promotion,
  and an occurrence moving through preparation or delivery land here. A
  promotion that rewrites the row without changing these columns does not
  queue it.

These are the inputs the scheduler can see changing, not proof that a plan
will change: a queued Family is planned in full, and planning may find
nothing to do. Every mutable row's version rises by one on each update, so a
count plus a version sum sees every insert and update. Fulfillments,
eligibility history and responses are append-only, so their counts suffice.
Every Family is also queued once an hour, as a safety net for any input the
fingerprints miss, and when the scheduler starts, a new campaign becomes
current, or a held Testing gate reopens. Traversal state is memory only; a
restart plans every Family once, as before, so it needs no durable cursor. A
fingerprint read before planning that differs afterward queues the Family
again, so a change that lands while it is planned is not lost.

A Family queued alone goes to the front of the queue, so a change made only by
others (a response, a source change) is planned within about 15 seconds plus
one page, even during a full pass. A Family planned since the previous check
goes to the back instead, since its own planning may have moved it, so its
change does not jump ahead of Families not yet planned. Such a Family, for
example one planned just before its response, can wait behind a full pass
still under way, as can a due time. A full pass's Families go behind anything
already queued, starting after the last Family planned, so a pass that is
queued again while under way still reaches its end. Planning is unchanged and
recomputes everything under the lock; the SQL guards stay authoritative.

A Family is dequeued only once its plan has committed. In bulk, that is when
its chunk commits, so a rolled-back chunk leaves all its Families queued. A
Family whose planning raises an unexpected error (a lock timeout or lost
connection, say) stays queued at the back, so it is retried within seconds
without blocking the others. A Family refused by admission or by an invariant
check is not retried until its inputs or the campaign-wide inputs change, or
the hourly pass comes round. Before #640 the wrapping sweep retried it every
few minutes.

Each scheduler loop plans the queued Families in one page, in stable order.
Each Family is planned in its own transaction, so no lock spans two Families. A
page holds up to 100 Families while the previous page allocated new
preparation work, and 20 after one that allocated none, so a large send
creates mail quickly without an idle or paused campaign holding the work-order
lock for long. A page also ends once 15 seconds of planning have passed,
checked between Families, which bounds this producer's share of the scheduler
loop and its 90-second heartbeat. Ending early is pacing, not a timeout: the
Families not reached stay queued for the next loop (#394). Each such page logs
`work_budget_reached` with the budget and the elapsed seconds; with debug
logging on, every page also logs how many Families it visited. With the [bulk
Family send](#bulk-family-send) on, one transaction plans several Families
instead. A loop with nothing queued plans nothing and takes no work-order lock.

### Bulk Family send

An optional path plans, prepares and sends scheduled Family mail (initial
invitations and reminders) in batches, so each turn on the work-order lock
covers several Families or messages rather than one step of one message
([#430](https://github.com/epiphany40223/parishkit/issues/430)). It is off by
default and turned on for the scheduler, worker and mail-dispatch services
by re-rendering the deployment with the switch on (see the
[Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#turning-on-the-bulk-family-send)).
The switch is not a deployment input: it is never part of the provisioning
record, and a render with it off is byte-for-byte the render of the release
before it, so returning to that release is an ordinary retarget. It writes
the same rows through the same owners and SQL guards, so either path finishes
or recovers what the other started, and the switch may change mid-send. It
reads, renders, decrypts and seals outside the work-order lock, as
[Bulk send work outside the lock](#bulk-send-work-outside-the-lock)
describes.

- **Batches.** A batch is one work-order transaction. It adds items until
  its item limit, or until the time held so far plus its average item so far
  would reach a lock-hold budget of 0.75 seconds. The budget keeps holds
  inside the waits other lock takers allow: a task's lease renewal waits up
  to 2 seconds, which covers two full batches (one per consumer process)
  ahead of it; an in-flight mail check waits 1 second, and Family logins
  wait too. Each item
  runs in its own savepoint and counts only once that savepoint is released;
  an item that any guard or admission check refuses, or that is held, paused,
  cancelled or superseded, rolls back untouched and is handled later by the
  one-at-a-time path.
- **Planning.** The scheduler's Family sweep plans several Families per
  transaction, within the page limits above.
- **Preparation.** A worker claims, prepares and completes several
  preparation Tasks per transaction.
- **Sending.** In one transaction a mail consumer commits up to B messages
  as `submitting` (the durable record made before anything reaches the
  provider; B is `bulk_send_batch`, 1–100, default 20), fewer when the hold
  budget ends the batch first. It then sends them one after another over its
  one SMTP connection, outside any transaction, and records every outcome and
  Task completion in budget-bounded transactions before it begins the next
  batch. Each message keeps its own Task, claim, fence and lease. Each
  message's provider deadline allows a full 30-second helper budget for every
  message ahead of it, up to a cap, and a message is launched only with at
  least 25 seconds of its deadline left, so no send starts short of time.
- **Holds before launch.** Just before each send the consumer checks its
  provider circuit and, without the lock, what family mail admission checks
  under it: mode, current campaign and rehearsal epoch, revision and
  occurrence state, production cycle, close and campaign end, pause, go-live
  gate, source reconciliation, work gates, catch-up and restore holds. A
  message not launched then, or past the batch's launch cutoff or short of
  its deadline, is recorded as definitely unsent. Like the one-at-a-time
  path's admission holds this spends none of its attempt budget, and the
  Task waits in the reconciling phase until it is looked at again. A message
  whose mode, epoch or revision has changed, or a Testing message held by a
  closed go-live gate, is recorded as a definitive failure instead: it was
  never sent and can no longer be. The cutoff, a deadline shortfall and a
  helper stopped at the lease margin are logged as timeouts.
- **Crashes.** A consumer that dies after the `submitting` commit leaves at
  most one batch `submitting`, sent or not. After their lease and provider
  deadline, recovery records each `delivery_unknown`, exactly as for one
  message, and none is sent again automatically. Lower B bounds how many an
  Administrator may need to settle.
- **Draining.** Only a hint for scheduled Family mail (or Family mail
  preparation) starts a drain: the consumer takes the hinted Task first, then
  further batches of due rows itself for up to 30 seconds, waiting up to
  5 seconds (polling without the lock) when it finds none, so newly prepared
  mail is sent without waiting for a scheduler loop. Any other hint, such as
  a receipt, digest or alert, is handled at once by the one-at-a-time path.
  The process heartbeat is beaten between batches and between sends.

Testing routing and the daily sending limit are unchanged; near the daily
limit the bulk path leaves messages to the one-at-a-time path, which decides
them one by one.

The initial schedule sends once to each qualifying Family. A Family becoming
active after the initial occurrence receives one catch-up initial invitation
after source promotion. The same catch-up applies when an already eligible
nonresponder transitions from non-deliverable to deliverable after the initial
occurrence because source contact changed or provider suppression was cleared.
The recovery occurrence has a distinct key containing the durable deliverability
generation, while sharing the initial invitation's semantic fulfillment slot.
It is created once per qualifying transition, and pre-dispatch checks require
the campaign to remain open, the Family to remain eligible/deliverable and
without a live response, and no initial invitation to have succeeded. A later
transition may create another recovery attempt after a terminal failure, but no
more than one initial semantic delivery can succeed. Reminders use the same no-
submission rule. A responder never receives a later reminder even if it proposed
email opt-out or submits again.

When recovery finds multiple overdue Family-mail occurrences for one campaign
and Family, it selects at most one for delivery. If no initial invitation has
succeeded, it sends the initial invitation and marks every already-overdue
reminder `coalesced`. Otherwise, it sends only the chronologically latest
applicable reminder and coalesces older overdue reminders. Eligibility and
submission state are checked again immediately before the selected delivery.
Future reminders that were not overdue at recovery retain their normal
schedules.

One email has all deduplicated eligible head addresses in `To`; no address from
another Family shares that message. Templates include Family names, code,
secure link, generic URL, parish/campaign values, and mode banner. Render
failure for one Family records an error and does not block others.

Mail dispatch respects the sending mailbox's daily limits. It sends nothing
more once the recipients of the last 24 hours' accepted or uncertain
submissions reach the deployment limit, stopping bulk invitations and
reminders earlier so receipts, digests and alerts keep a reserve; capped mail
waits as a hold, not a failed attempt. A provider refusal at Gmail's own
daily or rate limit defers the message without spending its attempt budget,
suppressing an address, or counting as a provider outage; a rate limit in
reply to one message's DATA holds only that message, not all sending. It
fails visibly after 48 hours of continuous limit refusals once no mail has
been accepted in the last 24 hours, and after 7 days in any case; see
[Family mail dispatch](../../../guides/stewardship-family-mail-dispatch.md).

Before provider submission the exact non-secret message content and intended/
routed recipients are persisted. Credential-bearing substitutions are sealed
to the dedicated token-key public key and are decryptable only by the
`mail-dispatch` worker immediately before provider submission. Admin detail,
exports, logs, and error context expose only redacted placeholders and
fingerprints. Terminal
transition to `delivered`, `permanent_failure`, or `cancelled` destroys the
sealed token/code substitutions while retaining the redacted rendered record.
In Testing, envelope recipients become the single test address, the subject/
body prominently say TEST, and intended names/addresses are safely listed.
All Family credential substitutions, including in confirmation mail, come only
from the current rehearsal epoch according to
[Family credential security](../architecture/spec.md#family-credential-security).
Persist the credential namespace/epoch with the outbox and recheck it at claim
and immediately before provider submission. Never substitute Production secrets
into Testing mail or fall back to them when a rehearsal credential is missing.
Old-epoch work is cancelled and scrubbed, not rebound to a new epoch. Test
delivery never marks a live occurrence delivered.

For each later Production Family message, dispatch decrypts the Family's primary reusable
token ciphertext into memory, constructs the secure link, and seals that
message's substitution. Scrubbing a terminal outbox substitution does not
destroy the primary ciphertext; primary token rotation/closure follows the
[credential lifecycle](../architecture/spec.md#family-credential-security).

Each semantic delivery has a stable application idempotency key. The dispatch
adapter supplies it as the provider idempotency key when the provider offers a
contractual idempotent-send facility, and every safe retry reuses it. Provider
acceptance marks success. A failure known to have occurred before acceptance is
transient and retries with shared backoff.

A timeout, connection loss, or malformed response after submission begins is
ambiguous because the provider may already have accepted the message. The
worker first queries provider status by the stable key or returned message ID
when that capability exists. Confirmed acceptance succeeds; confirmed
non-acceptance follows normal retry/failure handling. An unresolved result may
be retried automatically only when the provider contract guarantees that reuse
of the same key cannot create a second delivery. Otherwise the outbox row and
occurrence enter `delivery_unknown`, automatic retry stops, a deduplicated
WARNING is recorded, and Admins are notified in the portal.

The Admin delivery-resolution screen may re-run provider reconciliation, mark
the occurrence delivered when external evidence supports that result, or
explicitly authorize a resend after acknowledging that a duplicate is possible.
The latter creates a numbered attempt under the same semantic occurrence; it
does not silently turn the unknown attempt into a failure. When the provider's
own record shows the attempt was not sent, the Admin may instead record that
with evidence and no resend: the attempt becomes the same definitive
non-acceptance a permanent provider failure records (the occurrence `failed`,
unfulfilled), without suppressing any recipient, and it needs no resend
admission (see the
[unsent resolution guide](../../../guides/stewardship-unsent-resolution.md));
for an Administrator report, the
[digest completion rule](#administrator-digests) decides whether that failure
settles its cohort. Every resolution, evidence note, and resend authorization
is audited. Until resolution, the row is not treated as successful for
delivery statistics or as eligible for an automatic catch-up duplicate.

A permanent address refusal records that recipient/family, suppresses that
normalized address until its source value changes or an Admin clears the
refusal after verification, and continues. A Family whose every otherwise
eligible address is suppressed is included in the
[Family directory's mailing columns](../reports/spec.md#mailing-columns).
A systemic provider/authentication failure stops further sending for that run
and becomes CRITICAL to avoid a flood of identical failures.

### Bulk send work outside the lock

The [bulk Family send](#bulk-family-send) does each Family's reading,
rendering, decryption and sealing while it holds the global work-order lock,
so on the validation host every bulk preparation batch held a single Family
and the launch invitation took about 24 minutes for 1,100 Families
([#447](https://github.com/epiphany40223/parishkit/issues/447)). The bulk
send therefore moves that work outside the lock, and prepares scheduled
Production reminders before they fall due. Only a short recheck and the
writes stay under the lock. The measurements, estimates and PR plan are in
the
[BG-12 work package](../../../plans/stewardship/background-processing.md#bg-12-faster-bulk-family-send).

Two constraints hold for the rest of the current campaign:

- **Every Production credential stays valid.** Each Family's code and link
  token, made at go-live, keep working, including in mail already sent. The
  send only reads them: no rotation, regeneration, re-encryption or change
  to how they are looked up.
- **One guard change only.** Every row, owner, state, ticket and SQL guard
  stays as it is, except one condition of the occurrence guard that lets
  preparation run before the due time (below), installed by a
  [forward migration](../operations/spec.md#post-launch-schema-policy).
  Nothing may be sent before its due time.

It is part of the bulk send, with no switch of its own: the bulk switch
turns it on and off, and turning the bulk send off returns to the
one-at-a-time path, which prepares at the due time.

#### Building outside the lock

- **No threads or extra connections.** Each process builds serially, between
  its batches, on its own database connection. The runtime budgets three
  connections per process, and a fourth would need role, grant and upgrade
  check changes. Building therefore frees the lock for other processes (the
  other consumer, the worker, sign-ins); it does not make one process
  faster on its own.
- **One snapshot per build.** A build reads everything it renders from (the
  occurrence or message, the Family's source inputs, the content version,
  the applied integration, hosted files and branding assets) and its
  fingerprint in one `REPEATABLE READ` transaction, rendering inside it or
  from what it prefetched there. It then decrypts or seals in a short second
  transaction that holds only the credential key-set lock (shared,
  non-waiting, with its inventory check). That second transaction reads
  outside the snapshot; the token generation, credential epoch and key
  inventories only move forward and are rechecked under the work-order
  lock, and a build whose second read differs from its snapshot is dropped.
- **Dropped builds.** A busy key-set lock (a rotation in progress), an
  inventory that is not current, a serialization failure (`40001`) or a
  decryption error drops the build, and the item is built inside the lock as
  today. The rendering, source-loading, sealing and content functions that
  assert the work-order lock today get variants for builds.
- **A small lookahead, carried forward.** A batch's lock hold fits only a
  few items, so a process builds only about as many items as its last batch
  committed, plus a small margin. Builds a batch does not use carry over to
  the next batch and are rechecked there. A carried send build holds a
  Family's plaintext code and link in memory: it is never logged, and it is
  dropped on a fingerprint mismatch, a stop request or the end of the drain.
- **Recheck under the lock.** Each item compares its fingerprint in one
  query inside the batch's lock transaction. If it matches, the prebuilt
  result is written through the existing code and guards. If not, the item
  is rebuilt inside the lock exactly as today, so a change costs time, not
  correctness. A guard refusal still leaves the item to the one-at-a-time
  path, as today.

#### Preparing ahead of the due time

Preparation is serial in the one worker, so a Production reminder's
preparation runs in a lead window of two hours before its due time, leaving
only sending at the due time. The initial invitation at a campaign's start
cannot be prepared ahead, because no occurrence may be created before the
campaign's configuration starts; this campaign has only reminders left.

- **Planning ahead.** On the bulk path the scheduler plans a Production
  reminder's Families once its due time is within the lead window, instead
  of at the due time. Families that become eligible later are planned at
  the due time as today, and catch-up invitations are unchanged.
- **Planning horizons.** Preparation re-runs `plan_family` under its claim,
  and today that planning only looks up to the current time, so it would
  never select a reminder that is not yet due and the task would retry on
  "selection changed". Preparation-time planning therefore looks up to the
  current time plus the lead window for a Production reminder whose
  occurrence is due within that window. The horizon depends on the
  occurrence, not on the bulk switch, so preparation tasks already queued
  when the bulk send is turned off mid-window still complete. The planning
  a send runs at dispatch (`plan_family` in the commit half of
  `begin_submission`) keeps its horizon of the current time.
- **Catch-up merging.** Because preparation plans up to two hours ahead, an
  older reminder that is due but unsent can be merged into the newer one up
  to two hours earlier than today; the Family is then mailed once, at the
  newer reminder's due time. Two reminders less than two hours apart merge
  this way. Only a reminder-only group looks ahead: a Family whose initial
  invitation is still owed plans at the current time, so an unsent
  invitation never absorbs a reminder early.
- **The guard change.** In `stewardship_occurrence_guard_v1`, the branch
  that admits a running claim under a fenced task (a move to `running`, or
  an update that stays `running`) refuses it while the occurrence is not yet
  due (`NEW.due_at > instant`). The
  migration narrows that one condition: for a `family_mail_prepare` task on
  a Production occurrence it refuses only when `NEW.due_at > instant +` the
  lead window (two hours), and for every other task type and for Testing it
  refuses exactly as before. Every other condition of the branch, and of the
  guard, is unchanged. Preparation's own guard
  (`stewardship_family_mail_write_admitted_v1`), which needs that running
  claim, is unchanged.
- **Nothing is sent early.** The dispatch guard
  (`stewardship_family_dispatch_live_v1`) is unchanged and refuses a message
  whose occurrence is not yet due, and the claim branch for delivery tasks
  keeps refusing a not-yet-due occurrence. The message's delivery Task is
  enqueued with `not_before` at the due time, so mail consumers do not claim
  it early and hold it in a retry loop.
- **Changes between preparation and the send are caught.** The dispatch
  guard, with `disposition`, rechecks every message when it is committed
  `submitting`: no live response (a Family that responds during the lead
  window is not sent the reminder), eligibility and deliverability, the
  current revision, pause, close, mode, gates and the current token
  generation and credential epoch. The send build re-renders each message
  from current inputs, so content edited during the lead window is sent as
  edited.
- **Refresh holds and deltas.** The
  [delta wait](#deltas-wait-for-a-bulk-family-send) counts preparation tasks
  as today, but counts only messages whose occurrence is already due, so a
  prepared reminder does not hold deltas for the rest of its lead window.
  Deltas are therefore held while preparation runs and resume once it ends,
  until the send itself begins. If a delta does promote during preparation
  (past the source-age allowance), the promotion marks the population dirty
  and preparation's guard refuses writes until the population is rebuilt, so
  preparation pauses for that rebuild; the lead window absorbs it.
- **The lead window follows the nightly refresh.** The nightly full refresh
  (02:00 by default) runs before a 08:00 reminder's lead window (from
  06:00); a deployment whose nightly time falls inside a lead window is
  flagged by a WARNING when the bulk scheduler first plans the campaign
  under its configuration (once per process for each campaign and
  configuration). The other configured full refresh times, and hourly or
  quarter-hour full refreshes, are checked the same way. With the
  [refresh schedule](#refresh-schedule)'s automatic exclusions on, only the
  nightly refresh can still fall inside a window, and the settings page
  shows it too.
- **Pages and health ignore what is not yet due.** The send progress panel's
  latest send and its upcoming list, and the due-work health check
  (`SCHEDULER_LAG`), count only occurrences and Tasks that are due, so a
  prepared reminder appears as upcoming, not as a send in progress or late
  work.
- **Testing** plans at the due time as today.

#### Bulk preparation builds

For each Production item the build reads the ticket's occurrence, the
Family's source inputs and the template, renders the message and seals its
substitutions. Its fingerprint covers the occurrence version, the Family's
source generation, eligibility, deliverability, response and code
ciphertext, its unresolved address refusals, the promoted source and the
population built from it, both configuration rows the render reads
(the system's active configuration and the campaign's active configuration
values, such as its name and banner), the template, the Production cycle,
all hosted files' public links and all ready branding bundles (a
superset of the files and assets the template names), the active token
generation, credential epoch and the Family's link token row, and the key
inventory digests. Inside the batch's lock
transaction each item still claims its ticket and runs `disposition` and
`plan_family` as today before the fingerprint is compared.

A Testing item writes its Family's rehearsal credential during preparation,
under that Family's ticket, so Testing preparation stays inside the lock as
today. Generating rehearsal credentials once when a rehearsal begins, so that
Testing can prepare outside the lock too, is later Testing-only work
([#555](https://github.com/epiphany40223/parishkit/issues/555)).

#### Bulk send builds

For each candidate message the build re-renders it from current inputs,
decrypts its sealed substitutions and builds the provider message. Its
fingerprint covers the message's and its occurrence's versions, both
configuration rows, the hosted files and branding assets the template names,
the source generation, and the version and digest of the credential row the
link uses (the link token in Production, the rehearsal credential in
Testing), so an in-place token change is caught. Recipient refusals and
their resolutions are not fingerprinted: the existing recipient check
(`stewardship_family_mail_recipients_v1`) refuses a stale recipient list at
the commit. Inside the batch's lock transaction each message is committed
`submitting` as today (`bound_dispatch`, `disposition`, the commit half of
`begin_submission` with `plan_family`, and the dispatch guards) after its
fingerprint is compared. Testing sends use this path too, since sending
writes no credential.

#### Batches, crashes and stale state

- **Batched intent and outcomes, as today.** Each send batch commits its
  "about to send" (`submitting`) records before any of its messages reaches
  the provider, then sends them over the consumer's connection, then
  records their outcomes in budget-bounded transactions. A crash leaves at
  most one batch per mail consumer (two by default, `mail_consumers`) whose
  outcomes are unrecorded. Recovery records those messages
  `delivery_unknown` for Administrator review, and none is ever sent again
  automatically.
- **Stale state is accepted.** A Family that submits after its message's
  `submitting` commit but before the send still gets that message, for
  example a reminder moments after submitting. The Administrator accepts
  this; the window is one batch's sending time.
- **Transitions and contention.** Pause, close, mode changes and every other
  transition still commit under the exclusive lock, so the next batch's
  commit sees them, exactly as the bulk send does today. Family sign-ins
  still contend with the batches about as they do today: planning takes the
  runtime row `FOR UPDATE` while sign-ins take it `FOR SHARE`, and each
  batch's hold stays within its 0.75-second budget, except that an item
  rebuilt under the lock costs about as much as one item today (about 0.8
  seconds on the validation host). Pages, reports, send statistics and
  recovery are unchanged, because the rows are the same.

A streamed design that prepared in sets under a new run owner was specified
and then deferred, since it needs a larger guard migration
([#554](https://github.com/epiphany40223/parishkit/issues/554)). It is
revisited only if a measured send on this path still takes more than about
15 minutes.

## Submission confirmation

When at least one deliverable eligible-head address exists, the live submission
transaction creates one receipt outbox row addressed to those heads. If none
exists, it creates no outbox row and records the non-error audit action
`submission_receipt_skipped` with reason `no_deliverable_recipient`; the
submission still commits.

A receipt contains no sensitive answers or credentials. Its stored UTC
submission instant is rendered in the immutable campaign timezone with timezone
abbreviation; asynchronous email rendering never assumes a browser timezone.
A delivery failure does not roll back the already accepted submission; it is
visible/retryable to Admins. In Testing, it routes only to the test recipient.

## Administrator digests

Recipients are the current normalized exact-address rules granting
Administrator at execution time. Each Admin receives an individual message so
addresses are not exposed to other recipients.

The generation-time recipient cohort is immutable. Before each submission,
recheck the recipient's current Administrator authority. Safely cancel a revoked
recipient's unsent message with `recipient_revoked`, retaining the cancellation
as an audited withdrawal of that recipient obligation, never as delivery. A
message that ended in `permanent_failure` (a provider refusal or an Admin's
[confirmed-unsent record](../../../guides/stewardship-unsent-resolution.md))
whose recipient is not currently an Administrator can never be retried, so it
settles that recipient's obligation the same way, never as delivery. The
occurrence completes when every original recipient is accepted (including
proven earlier accepted coverage), has this guarded withdrawal, or has such a
failure. If no recipient was accepted, record
`daily_digest_no_current_recipients` (weekly:
`weekly_digest_no_current_recipients`) and an `empty` fulfillment, not a
delivered fulfillment. Because the failure settles by the current roster, an
address re-added as an Administrator before completion reopens its still
pending cohort, and its failed message may be retried again; newly added or
later re-added Administrators never reopen completed historical cohorts. A
failure to a current Administrator holds the cohort open until an explicit
retry. Provider-submitting or uncertain messages still require ordinary
reconciliation before cancellation or settlement.

### Daily campaign digest

After every active local campaign day, default 12:15 a.m. next day and
Admin-configurable, send:

- the report day's first submissions, cumulative participation and
  percentage;
- the report day's cumulative annual pledges (USD), the same label as the
  table column, when financial stewardship is enabled; and
- the same participation chart/data basis as the web report, rendered as an
  inline accessible image plus textual summary.

The report day is the schedule slot's local date: the campaign day before the
scheduled send. A late, retried or recovery send never moves it. Every figure
in the email, its table and its chart comes from the report day's end-of-day
row of one exact ready `CampaignDailyFactSet`, so the text always matches the
chart's last point. One plain line states this, for example "All figures are
as of the end of October 6, 2026 (EDT)." The zone abbreviation is the one in
force at the end of the report day (EST after daylight saving time ends). The
email's day-by-day table is captioned "Day by day". Live population figures
(Members, eligible or deliverable email, comparison pledges) are not in the
digest, because they cannot be stated as of the end of a past day; the
Statistics report shows them. The digest retains its send-time observation
and source pin with the snapshot. The digest's ParishSoft line, labeled as
the state when the email was made, states the data age and the connection as
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)
defines them ("ParishSoft data as of …", "Connection: working"). Digest execution waits and
retries while that exact generation is building; a failed materialization makes
the digest visibly failed/retryable rather than substituting stale or mixed
facts. Later source/status changes do not rewrite the sent digest.

The email is desktop-first and visual
([#720](https://github.com/epiphany40223/parishkit/issues/720)): a 960px
column (fixed at 960px in Outlook for Windows; a narrower window shrinks it),
not the 600px column of Family mail. It says each fact once and opens with its
content. The subject names the report, its report day, the campaign and the
parish: the Administrator's subject comes first, followed by the compiled
report title ("Daily campaign digest — October 6, 2026") and whichever of the
campaign and parish names it does not already contain. The body has no title
or name line; it starts with the as-of line, the only place that names the
time zone (a recovery digest starts it with the dates it covers). Any
Administrator-written intro stays above the report. **Campaign totals** follow, one line per
figure: its label, a bar drawn with table cells (so it shows in every mail
program, with images off too), and the exact value, for example "Families
that have responded — 10 out of 93 (10.8%)". Families that have responded are
drawn against the Families they are out of, and "First submissions that day"
against the busiest campaign day so far, which is stated beside the number; a
bar is never drawn full unless the share is complete. Pledges have no bar,
since their only comparison is a live figure. The chart follows (its date axis
names no zone), with alt text carrying the same totals, then the day-by-day
table of the last seven campaign days (every covered date of a recovery
digest, however many), and the button to the saved report, drawn like the
Admin portal's primary button (its accent colour, radius, padding and bold
label, as a table cell so every mail program shows it). There is no small
print: the ParishSoft connection is on the saved report page. The digest validator
admits only the compiler's closed set of inline styles, bar colours and table
attributes, so this markup cannot carry anything else.

If multiple daily digest occurrences are overdue at recovery, the system sends
one recovery digest per campaign covering the complete missed local-date range.
It includes per-day rows and the end-of-range cumulative statistics/chart rather
than sending several messages together. Each original daily occurrence is
retained as `coalesced` and references the recovery-digest occurrence; the
recovery record stores every pinned daily input needed to reproduce its values.

### Weekly additional-information digest

At the configured local weekday/time, send newly created live
AdditionalInformationItems since the last successful weekly occurrence only if
they remain `current_actionable` at generation. Include Family name/DUID,
submitted time, bounded text, and secure Admin link. A correction section names
previously digested items that have since become `superseded` or `withdrawn`,
without repeating withdrawn text unnecessarily. Skip the message if there are
no new actionable items or corrections; record a successful empty occurrence
so it does not reconsider the same interval.

The weekly and manual emails use the daily digest's desktop layout and its
closed set of report markup ([daily campaign digest](#daily-campaign-digest)),
with no image, and likewise open straight into their content: the subject
names the report and its capture date, the campaign and the parish, in the
same way as the daily digest's, so the body has no header line. Each section's heading
carries its count ("5 new actionable requests", "1 correction to previously
reported requests"), then one table row per request: the Family, DUID, link
and submitted time (no zone) on the left and the text on the right. The
actionable requests are numbered 1, 2, 3 in number cells, not list markers,
so the numbers are the same in every mail program and a reader can refer back
to them. A request's text longer than 240 characters is shortened at a word
boundary and ends with "…" and a "Read the full request" link to its page;
complete text has neither. Links use the portal's link colour, and the
report button is the portal's primary button. There is no small print.

Changing Admin recipients does not resend past successful digests. An Admin may
manually generate/send a new report occurrence, visibly labeled manual and
independently audited.

The manual-report confirmation explicitly explains that it selects all current
actionable live items and corrections to previously delivered items, and may
repeat earlier reports. Bind the confirmation to the reviewed configuration;
reject a changed configuration rather than silently changing mode or mail policy.
Replaying the same command returns its original task, not a second report.
Manual occurrences do not advance the regular weekly success watermark or
satisfy regular per-recipient item/correction coverage. Actual manual deliveries
still establish that an item was reported, so later withdrawal or supersession
can be corrected. Apply the same current-Admin, campaign, pause, restore,
unresolved-delivery and Testing restrictions as scheduled reports.

### Post-close reporting obligations

The scheduler and archive-preparation workflow share a deterministic inventory
of receipt/digest obligations, derived from accepted live submissions, active
campaign-local days, applicable schedule definitions/revisions, and weekly
additional-information/correction coverage. It includes every required daily
slot through the final active day and the weekly slot needed to cover the
remaining eligible items/corrections, even when its due time is after close.
It does not invent an endless series of empty weekly obligations after close.
Previously coalesced slots are resolved only when their selected replacement
has completed or has itself been explicitly resolved. A receipt's recorded
no-deliverable-recipient outcome and a digest's audited empty outcome already
resolve their respective obligations.

Archive preparation can inspect future obligations without dispatching them
before their due times. Explicit skip decisions use the durable
`PostCloseMailResolution` record defined by the
[data model](../data/spec.md#schedule-revisions-and-fulfillment). Applying a skip
locks/rechecks the Campaign and affected work, records the covered semantic
slot and input coverage, marks the occurrence skipped (creating it if absent),
and cancels only safely cancellable related outbox/tasks in one transaction.
The reason is `admin_post_close_skip`; it is never counted as delivery success.
Uncertain or provider-submitting work requires existing reconciliation first.

Scheduler creation, worker claim, schedule replacement, and archive/Return
checks consult these resolutions, so retries, new revisions, and later
unarchive/reopen cannot recreate skipped coverage. New submissions or item/
correction versions outside the recorded coverage remain new obligations; an
old skip cannot silently cover them. A newly authorized manual digest is a
distinct, audited request. The final archive/Return recheck uses the same
Campaign locking protocol as producers and workers, preventing new obligations
from racing a successful lifecycle transition.

## Exports and graph rendering

Large CSV/XLSX/PDF/PNG requests create export jobs. The request stores report,
filters, sort, selected IDs, source snapshot, browser timezone, requester, and
authorization scope. The worker rechecks scope before querying and again when
the file is downloaded. Creation and worker claim also use the campaign-work
admission gate; an archived campaign in purge preparation cannot start or
resume an export that could make the verified purge inventory stale.

An export job is requester-scoped report work, not Admin-only operational
background work. Staff and Ministry leaders may view, cancel at a safe point,
and download their own authorized export jobs; leaders remain restricted to the
recorded Ministry scope. Admins may view every export job. No requester gains
access to refresh, delivery, publication, purge, backup, or another user's job
through the export status interface.

Files are written atomically below `<root>/reports`, have opaque names, and use
the owner-only directory/file/temporary-file permissions defined by
[runtime storage](../operations/spec.md#runtime-storage) and the retention policy defined by
[operations](../operations/spec.md#temporary-retention-and-housekeeping).
Expired files can be regenerated from retained source/config where permitted.
A Family directory file's head emails are the one exception to "retained":
they may come from the current ParishSoft data, and the file says so (see
the [Family directory](../reports/spec.md#family-directory)).
Files are served only through an authorized application response. A short-lived
single-use download grant authorizes that application response, never proxy
file access or an internal-redirect handoff. Caddy has no export-storage mount.
The application holds the [campaign read guard](../data/spec.md#campaign-read-guards)
through the complete download response, including streaming, so purge file
cleanup cannot race an admitted download. Worker deletion admission and
pre-first-batch drainage use that same service.

### Export cleanup recovery

Artifact cleanup retries are bounded. Exhaustion records a durable CRITICAL
operational signal with the failed task transition; admission checks alone
must not emit that signal. Cleanup preserves retained requests, receipts,
audit history and calculation pins independently of artifact expiry.

After repairing the cause, an Admin can retry the latest failed cleanup run
from its background-task detail page. The CSRF-protected POST is replay-safe
and selects the same canonical cleanup root, never an unrelated task or a new
unbounded automatic retry series. Older task pages link to the latest run and
do not offer a stale retry form. Staff and Ministry leaders cannot use this
operational action. Conflicting or invalid form submissions show a safe HTML
recovery page with a task-detail link; unrelated internal failures report
unavailability, not a retry conflict.

## ParishSoft publication

Publication runs in a dedicated queue with lower concurrency and uses the same
`SourceMutationLease` as refresh. Preflight and execution are separate task
phases with one durable plan version. A changed decision/source after preflight
invalidates the plan and requires a new confirmation. Execution owns and
heartbeats the lease through final ParishSoft verification, releases it, and
then queues the targeted reconciliation refresh, which claims the lease
normally.

Writes use shared v2 `PUT` contact primitives, expected-tenant guard, current
full payload merge, idempotent retry, and read-after-write verification as
specified in [data reconciliation](../data/spec.md#review-and-publication).
Progress is per entity, not merely per field. Partial success is explicit and a
retry selects only failed/still-applicable entities.

## Critical errors and notification

Every error is durably logged. CRITICAL means timely Admin attention is needed,
including:

- database integrity/unavailability or inability to persist accepted work;
- repeated source refresh failure, or ParishSoft data older than its
  [schedule allows](../operations/spec.md#parishsoft-data-age-and-connection)
  (allowing more time while a
  [Family send holds refreshes](#deltas-wait-for-a-bulk-family-send));
- wrong ParishSoft organization or implausible destructive source change;
- systemic mail failure during a due campaign occurrence;
- scheduler/worker health preventing due work (judged for a
  [bulk Family send](#late-work-during-a-bulk-family-send) by the send's
  progress);
- sustained distributed administration-login or Family-code guessing abuse;
- publication ambiguity after an external write;
- exhausted Production-transition cleanup;
- failed backup beyond RPO; or
- purge inconsistency/exhausted cleanup.

CRITICAL events create deduplicated notification occurrences to current Admin
email addresses and optional Slack. Repeated identical events within a
configurable suppression window update occurrence counts rather than storming.
Recovery sends one resolved notification where useful.

CRITICAL and other privileged operational notifications follow the
[operational routing exception](#mode-routing), including while the deployment
is in Testing or restore review.

Slack delivery failure is logged and cannot mask the original error. If email
itself is failing, the system does not recursively create email-failure alerts;
Slack/logs remain. Sensitive values and full free text never enter Slack.

### Late work during a bulk Family send

The scheduler's due-work check (`SCHEDULER_LAG`) calls a task late when it is
admitted more than 90 seconds after it was due, and CRITICAL `due_work_lag`
follows once lateness has lasted the operational escalation window (15
minutes by default). A [bulk Family send](#bulk-family-send) enqueues every
delivery task at the send's due time, and the mail consumers take tens of
minutes to work through them (about 30 minutes for 1,000 messages), so most
of those tasks start long after 90 seconds by design. Judged that way, every
normal send raised the alarm (#634).

So while a send is in progress, its waiting delivery tasks are judged by the
send's own progress instead. "In progress" is the deployment-wide test the
[delta wait](#deltas-wait-for-a-bulk-family-send) uses, not a per-send count:
at least 10 pieces of Family send work remain across all sends, counting due
messages pending, waiting to retry or being submitted and Family preparation
tasks queued, running or waiting to retry. Each send whose tasks are waiting
is then judged on its own, and is late only when either:

- **it has stalled:** nothing of the send was prepared or settled (sent,
  failed or uncertain) for more than 10 minutes since it fell due. This is
  twice the Send progress panel's five-minute "stalled" window, so a brief
  provider back-off does not by itself start the alarm; with the escalation
  window, CRITICAL follows 25 minutes without progress. Progress made before
  the due time (a reminder prepared ahead) does not count;
- **it has overrun:** it is still in progress 2 hours after it fell due,
  however steadily it progresses. This is the same 2 hours a send may hold
  deltas back for; a launch-size send takes 30 to 75 minutes.

Every other task type, Family delivery tasks whose worker lease expired, other
delivery tasks (receipts, digests, tests and alerts) and a send's last few
messages, once fewer than 10 remain, keep the 90-second rule. While Production
delivery is paused only messages count towards "in progress", as for the delta
wait.

A late check records why, in the CRITICAL entry's closed `due_work` context
(counts, seconds and identifiers only):

- for the 90-second rule: the task type of the worst (most late) task, how
  many tasks were late and that worst lateness against the 90-second limit;
- for a send: its schedule definition and revision ids, how many of its
  messages remain and how many are done, how long it has gone without
  progress, how long since it fell due, and the limit it broke. Durations,
  not times, so System logs shows no raw timestamp. When other tasks also
  broke the 90-second rule in the same check, the send's context is kept
  and their number is added as `other_late_count`.

The scheduler passes the context to the checkpoint trigger in a
transaction-local setting; a context the allowlist refuses is dropped rather
than blocking the CRITICAL entry. If the check cannot read which delivery
tasks are Family send work, or a send's progress, it logs a WARNING and
treats only the tasks it could not judge as unknown (neither late nor
healthy); other delivery tasks keep the 90-second rule, and the scan moves on.

### What went wrong, and recovery

Every operational log entry at WARNING, ERROR or CRITICAL records enough
structured context for [System logs](../admin-portal/spec.md#logs) to say
exactly what went wrong (#633). A table of required context keys per event
([`audit.log_contract`](../../../../src/parishkit/stewardship/audit/log_contract.py))
is checked where entries are written, an event without a row there counting
as missing every key. A failure record must never become a failure itself,
so only the test settings refuse an incomplete entry; in production it is
written with what it has and a WARNING `log_contract_incomplete` process-log
line records the miss. Contract tests check every Python call site and every
SQL trigger that writes such an entry, and that every serious process-log
line carries a category, task, limit or count, not only its event name.

The context stays within the closed operational schemas (counts, durations in
seconds, closed words and identifiers; never names, addresses, provider text
or exception messages), mirrored by `stewardship_safe_context_v1`:

- `failure`: what failed as a closed word (a ParishSoft read's category, such
  as `provider_status` with its HTTP status or `provider_timeout`; a health
  check the operational intake runs; an Administrator alert, security notice
  or Slack alert that could not be sent; the mail provider as a whole), the
  failure's category where an exception caused it, the task, message and
  attempt involved, a closed provider answer (`smtp_transient` and the like),
  and what happens next: `retry` after `retry_seconds`, as attempt `attempt`
  of `attempt_limit`, or `failed` (given up);
- `due_work`: late scheduled work, as described
  [above](#late-work-during-a-bulk-family-send), and a late campaign start or
  close with its occurrence and task, its lateness and the limit; and
- `recovery`, below.

**Recovery is logged.** When an operational incident resolves, the database
writes one INFO `incident_recovered` entry naming the incident, its kind, how
long it lasted and how many times it was observed. It shares the correlation
of the CRITICAL entry that opened the incident, and names that entry, so
"Show related entries" lists the two together. An incident opened without a
log entry (backup and sign-in health) keeps its own correlation. An episode
whose end still needs follow-up (a backup encryption key change, which ends
when the change is no longer recent rather than when anyone checked the key)
reads "Ended", with the same instruction its resolved notice gives, never
"Recovered". Automation-session notices get no entry: they end after an hour
without events, which is not a recovery, and the dashboard's automation
notices already say what happened. The
[critical-problems banner](../admin-portal/spec.md#navigation-and-home)
then says the problem ended.

A `due_work_lag` entry is never empty: when the scheduler's scan cannot name
what was late, or its context is missing or refused, the entry still records
the 90-second per-task limit. System logs says "This entry was recorded
without detail." for a WARNING-or-above entry with neither a sentence nor any
field, such as one written before #633.

Frozen migration 0008 (after #622's 0007) widens the allowlist, gives the SQL
producers that logged an empty context their detail, adds the event and the
recovery trigger, and refuses to commit unless all of it is installed.
Entries written before it keep their original context; System logs lists
their fields without a sentence.

## Shutdown and upgrade behavior

Workers stop claiming new jobs, finish or checkpoint within their termination
grace, and release/expire leases. The worker container's two processes drain
together within the one grace period. The scheduler may overlap an old/new process
during rollout without duplicate work because database occurrence keys are
unique. Database migrations run before new web/worker versions receive traffic;
mixed-version compatibility requirements are declared per migration.
