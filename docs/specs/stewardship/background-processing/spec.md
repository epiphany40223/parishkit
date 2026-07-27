# Stewardship background processing

All external calls, scheduled mail, large exports, publication, purge, backup,
and source refreshes execute outside web request processes. Interactive actions
create durable work and return a status link. The web application remains
responsive while workers run.

## Durable scheduling and task execution

PostgreSQL stores schedule definitions, occurrence records, task runs, and
outbox rows. Exactly one scheduler service scans due definitions at least once
per minute and inserts occurrences with unique idempotency keys. Celery/Redis
delivers execution hints; workers always claim/check the PostgreSQL record
before acting.

An occurrence key identifies semantic work, for example
`family-initial:<campaign>:<family>:<schedule-version>` or
`daily-digest:<campaign>:<local-date>`. Re-delivery, scheduler restart, or
manual retry cannot create a second successful occurrence.

Occurrence outcome is `pending`, `running`, `succeeded`, `skipped`, `coalesced`,
or `failed`. `skipped` and `coalesced` are terminal, include a structured reason,
and never masquerade as successful delivery; `coalesced` also references the
replacement occurrence selected for delivery.

Task state is `queued`, `running`, `retry_wait`, `succeeded`, `failed`,
`cancelled`, or `abandoned`. Workers heartbeat and record phases/progress.
Tasks use bounded timeouts and recover an abandoned claim only after its lease
expires. Cancellation is permitted only at task-defined safe points and never
pretends an in-flight external write was undone.

All schedules are evaluated in the parish timezone but persisted as UTC due
instants. DST folds run once; nonexistent local times run at the first valid
instant after the gap. Changing timezone causes future occurrences to be
recomputed, never already successful ones.

Missed occurrences catch up once when services recover. Before executing, the
worker rechecks global system mode, campaign state, and recipient eligibility.
Every missed occurrence retains its own durable outcome, but semantically
redundant mail is coalesced rather than delivered in a burst. A coalesced record
names the selected replacement occurrence and reason. A missed Family mail
after campaign close is skipped with a durable reason, while reports for
completed campaign days use the recovery-digest behavior below.

Testing routing is global and applies to every application email: Family mail,
submission receipts, Admin digests, critical alerts, and manual test/report
mail. The envelope has only the single configured Testing address; the subject
and both body alternatives prominently identify Testing and safely name the
intended recipients. Slack routing is unchanged. Production applies none of
these overrides.

## ParishSoft refresh

One named PostgreSQL advisory lock, `parishsoft-source-mutation`, covers full,
delta, and manual refresh plus ParishSoft publication execution. No refresh may
run concurrently with another refresh or with publication writes. Publication
preflight may hold the lock only while it reads and records a source version;
it never holds a database lock while awaiting human confirmation.

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
recognition. Giving detail is limited to at most two campaigns: the financial
and comparison periods for the current operational campaign and, if separately
present, the immediately upcoming draft campaign. Every other campaign reads
its immutable retained snapshots; its periods do not expand the nightly source
window.

The cycle:

1. Validates the expected ParishSoft organization without cache.
2. Loads source collections into staging with bounded shared retries.
3. Normalizes IDs/dates/emails/relationships and validates referential
   integrity, uniqueness, pagination completeness, and plausible counts.
4. Compares count/drop thresholds to the last successful full snapshot.
5. Builds derived eligibility, roster, giving, and reconciliation data.
6. Promotes all staged data atomically as defined by the
   [data specification](../data/spec.md#source-snapshot).
7. Records counts/duration/deltas and releases the lock.

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
- it has at least one valid email among active `get_family_heads()` Members;
- it has no effective live submission for live mail; and
- that Family/schedule occurrence has not succeeded.

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

Provider acceptance marks success. Transient failures retry with shared
backoff; permanent address refusal records that recipient/family and continues.
A systemic provider/authentication failure stops further sending for that run
and becomes CRITICAL to avoid a flood of identical failures.

## Submission confirmation

The live submission transaction creates one receipt outbox row addressed to
current eligible heads. It contains no sensitive answers or credentials. A
delivery failure does not roll back the already accepted submission; it is
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
  campaign pledge, and configured prior comparison pledge statistics.

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
the file is downloaded.

An export job is requester-scoped report work, not Admin-only operational
background work. Staff and Ministry leaders may view, cancel at a safe point,
and download their own authorized export jobs; leaders remain restricted to the
recorded Ministry scope. Admins may view every export job. No requester gains
access to refresh, delivery, publication, purge, backup, or another user's job
through the export status interface.

Files are written atomically below `<root>/reports`, have opaque names, and
expire after seven days by default. Metadata/audit persists. Expired files can
be regenerated from retained source/config where permitted. Files are never
served directly by the proxy without an authorized application response or
short-lived single-use download grant.

## ParishSoft publication

Publication runs in a dedicated queue with lower concurrency and uses the same
`parishsoft-source-mutation` lock as refresh. Preflight and execution are
separate task phases with one durable plan version. A changed decision/source
after preflight invalidates the plan and requires a new confirmation. Execution
holds the lock through its final ParishSoft verification, releases it, and then
queues the targeted reconciliation refresh, which acquires the lock normally.

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
- publication ambiguity after an external write;
- failed backup beyond RPO; or
- purge inconsistency/exhausted cleanup.

CRITICAL events create deduplicated notification occurrences to current Admin
email addresses and optional Slack. Repeated identical events within a
configurable suppression window update occurrence counts rather than storming.
Recovery sends one resolved notification where useful.

Slack delivery failure is logged and cannot mask the original error. If email
itself is failing, the system does not recursively create email-failure alerts;
Slack/logs remain. Sensitive values and full free text never enter Slack.

## Shutdown and upgrade behavior

Workers stop claiming new jobs, finish or checkpoint within their termination
grace, and release/expire leases. The scheduler may overlap an old/new process
during rollout without duplicate work because database occurrence keys are
unique. Database migrations run before new web/worker versions receive traffic;
mixed-version compatibility requirements are declared per migration.
