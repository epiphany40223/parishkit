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

An occurrence key identifies revision-specific work, for example
`mail:<campaign>:<schedule-uuid>:<revision>:<target>:<slot>:<mode>`. The stable
schedule fulfillment defined by the
[data model](../data/spec.md#schedule-revisions-and-fulfillment) prevents a new
revision, scheduler restart, or manual retry from creating a second successful
semantic delivery.

Occurrence outcome is `pending`, `running`, `delivery_unknown`, `succeeded`,
`skipped`, `coalesced`, or `failed`. `delivery_unknown` pauses automated mail
handling pending reconciliation. `skipped` and `coalesced` are terminal, include
a structured reason, and never masquerade as successful delivery; `coalesced`
also references the replacement occurrence selected for delivery.

Task state is `queued`, `running`, `retry_wait`, `succeeded`, `failed`,
`cancelled`, or `abandoned`. Workers heartbeat and record phases/progress.
Tasks use bounded timeouts and recover an abandoned claim only after its lease
expires. Cancellation is permitted only at task-defined safe points and never
pretends an in-flight external write was undone.

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

All schedules are evaluated in the parish timezone but persisted as UTC due
instants. DST folds run once; nonexistent local times run at the first valid
instant after the gap. Changing timezone causes future occurrences to be
recomputed, never already successful ones.

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
of the scheduled or active campaign. When no campaign is scheduled or active,
it covers the current draft and, if the immediately preceding campaign is
closed and still undergoing reconciliation, that closed campaign as well. Thus
the window covers at most two campaigns. Archived and all older campaigns read
their immutable retained snapshots and never expand the nightly source window.

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
- it is Email-deliverable, with at least one unsuppressed valid email among
  active `get_family_heads()` Members;
- it has no effective live submission for live mail; and
- that Family/schedule occurrence has not succeeded.

An otherwise qualifying Family with no deliverable head address receives no
outbox row. Its occurrence is `skipped` with the non-error reason
`no_deliverable_recipient`; permanent refusals therefore cannot create empty
recipient messages or systemic-provider failures.

The initial schedule sends once to each qualifying Family. A Family becoming
active after the initial occurrence receives one catch-up initial invitation
after the source promotion. Reminders use the same no-submission rule. A
responder never receives a later reminder even if it proposed email opt-out or
submits again.

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
with application-level encryption and are decryptable only by the dispatch
worker immediately before provider submission. Admin detail, exports, logs, and
error context expose only redacted placeholders and fingerprints. Terminal
delivery or permanent failure destroys the sealed token/code substitutions
while retaining the redacted rendered record. In Testing, envelope recipients
become the single test address, the subject/body prominently say TEST, and
intended names/addresses are safely listed. Test delivery never marks a live
occurrence delivered.

For each later Family message, dispatch decrypts the Family's primary reusable
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
submission instant is rendered in the configured parish timezone with timezone
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
metadata. Later source/status changes do not rewrite the sent digest.

If multiple daily digest occurrences are overdue at recovery, the system sends
one recovery digest per campaign covering the complete missed local-date range.
It includes per-day rows and the end-of-range cumulative statistics/chart rather
than sending several messages together. Each original daily occurrence is
retained as `coalesced` and references the recovery-digest occurrence; the
recovery record stores every pinned daily input needed to reproduce its values.

### Weekly additional-information digest

At the configured local weekday/time, send newly created live
AdditionalInformationItems since the last successful weekly occurrence. Include
Family name/DUID, submitted time, bounded text, and secure Admin link. Skip the
message if there are no new items; record a successful empty occurrence so it
does not reconsider the same interval.

Changing Admin recipients does not resend past successful digests. An Admin may
manually generate/send a new report occurrence, visibly labeled manual and
independently audited.

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
the retention policy defined by
[operations](../operations/spec.md#temporary-retention-and-housekeeping).
Expired files can be regenerated from retained source/config where permitted.
Files are never served directly by the proxy without an authorized application
response or short-lived single-use download grant.

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
