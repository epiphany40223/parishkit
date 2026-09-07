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

### Campaign lifecycle boundaries

The scheduler owns persistence of date-driven campaign transitions. On every
scan it inserts any due `start` or `close` CampaignBoundaryOccurrence with a
unique campaign/kind/resolved-boundary key and queues an execution hint. A
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
one keyed to the new resolved boundary. It races safely under the Campaign lock:
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
deletion, making retry safe after interruption. It validates campaign ownership
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
receipts, Admin campaign digests, manual report mail, previews, and test sends.
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

The owner heartbeats throughout external work and revalidates its fence before
each upstream write and immediately before snapshot promotion. Loss of ownership
stops further calls and prohibits promotion. Takeover is allowed only after both
lease expiry and the configured maximum external-request timeout plus safety
margin, limiting overlap with a request initiated by an abandoned owner. An
ambiguous already-started ParishSoft write still follows publication
reconciliation rather than being assumed undone. Publication preflight merely
records a source version in a short transaction; it holds no mutation lease or
database lock while fetching data or awaiting human confirmation.

### Delta cycle

Every 15 minutes, the system calls the supported v2 Family changes feed using a
durable watermark. Because that feed is not a complete Member/Ministry/giving
change stream, it is an optimization, not the sole correctness path.

For each delta indication, reload every affected Family and related Members/
contacts available through supported endpoints. If the feed/cursor is
ambiguous, discontinuous, unsupported, too large, or indicates relationship
data that cannot be safely scoped, promote no delta and queue a full refresh.
Ministry/fund data remains from the coherent prior full snapshot unless the
delta loader can prove a complete replacement.

### Full cycle

A full refresh runs nightly at an Admin-configurable local time, default 2:00
a.m., and on initial setup/manual request. It uses shared
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
4. Compares count/drop thresholds to the last successful full snapshot.
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
does not silently turn the unknown attempt into a failure. Every resolution,
evidence note, and resend authorization is audited. Until resolution, the row
is not treated as successful for delivery statistics or as eligible for an
automatic catch-up duplicate.

A permanent address refusal records that recipient/family, suppresses that
normalized address until its source value changes or an Admin clears the
refusal after verification, and continues. A Family whose every otherwise
eligible address is suppressed is included in the
[no-deliverable-email report](../reports/spec.md#families-without-deliverable-email).
A systemic provider/authentication failure stops further sending for that run
and becomes CRITICAL to avoid a flood of identical failures.

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

### Daily campaign digest

After every active local campaign day, default 12:15 a.m. next day and
Admin-configurable, send:

- previous-day first submissions, cumulative active participation, and
  percentages;
- previous-day/current cumulative pledge values when financial stewardship is
  enabled;
- the same participation chart/data basis as the web report, rendered as an
  inline accessible image plus textual summary; and
- current active Families/Members, eligible-email Families, participation,
  deliverable-email Families, campaign pledge, and configured prior comparison
  pledge statistics.

Metrics are snapshotted at digest generation with data-as-of/source snapshot
metadata and the exact ready `CampaignDailyFactSet`. Digest execution waits and
retries while that exact generation is building; a failed materialization makes
the digest visibly failed/retryable rather than substituting stale or mixed
facts. Later source/status changes do not rewrite the sent digest.

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

Changing Admin recipients does not resend past successful digests. An Admin may
manually generate/send a new report occurrence, visibly labeled manual and
independently audited.

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
Files are served only through an authorized application response. A short-lived
single-use download grant authorizes that application response, never proxy
file access or an internal-redirect handoff. Caddy has no export-storage mount.
The application holds the [campaign read guard](../data/spec.md#campaign-read-guards)
through the complete download response, including streaming, so purge file
cleanup cannot race an admitted download. Worker deletion admission and
pre-first-batch drainage use that same service.

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
- repeated source refresh failure/staleness beyond the configured threshold;
- wrong ParishSoft organization or implausible destructive source change;
- systemic mail failure during a due campaign occurrence;
- scheduler/worker health preventing due work;
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

## Shutdown and upgrade behavior

Workers stop claiming new jobs, finish or checkpoint within their termination
grace, and release/expire leases. The scheduler may overlap an old/new process
during rollout without duplicate work because database occurrence keys are
unique. Database migrations run before new web/worker versions receive traffic;
mixed-version compatibility requirements are declared per migration.
