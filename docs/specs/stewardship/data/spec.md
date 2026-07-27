# Stewardship data and reconciliation

This specification defines authoritative records, lifecycle states, source
snapshots, response versioning, and ParishSoft reconciliation. Portal behavior
is defined in the [administration](../admin-portal/spec.md) and
[Parishioner](../parishioner-portal/spec.md) specifications.

## Storage principles

PostgreSQL is the durable source for application state. Every mutable domain
record has created/updated UTC timestamps and actor/correlation metadata.
Configuration and workflow edits use optimistic concurrency or row locks so
two browsers cannot silently overwrite one another.

ParishSoft raw identifiers are retained as DUIDs without reuse. Application
primary keys are opaque UUIDs. Monetary values use fixed-precision decimal
columns and never binary floating point. Date-only values remain date columns;
instants are timezone-aware UTC.

Historical records reference immutable versions rather than mutable display
objects. For example, a submission references the source snapshot and content
version it used, while a delivered email references the redacted rendered
subject/body, intended recipients, template version, and fingerprints for any
credential-bearing substitutions. The plaintext code or link token is never
retained as ordinary rendered content.

## Core records

### Parish and integrations

There is exactly one `Parish` record containing the display name, main website
URL, IANA timezone, valid US main phone number, public origin, branding, and
active configuration version. Logo uploads produce normalized large, menu,
icon, and favicon variants. Accepted inputs are PNG, JPEG, or WebP; files are
decoded and re-encoded before use.

One versioned `SystemConfiguration` holds the global `testing` or `production`
mode, single valid Testing recipient, current campaign pointer, durable
`restore_review_required` gate, and mode-change history. The system starts in
Testing. Mode is not historical campaign data: deliveries/submissions snapshot
the mode in which they occurred so old records retain their meaning after a
later global transition. The restore gate is an independent fail-closed state;
Testing-mode Family access does not override it.

Integration records contain non-secret settings and credential fingerprints:
ParishSoft expected organization, Google OAuth/Workspace delegated identity,
outgoing sender/reply address, optional Slack channel, and backup target.
Secret values remain in credential files.

### Campaign

A `Campaign` includes:

- UUID, unique human name, optional stewardship year label, and lifecycle state;
- local start/end dates and their resolved UTC boundary instants;
- enabled census, Ministry, and financial modules;
- references to the global mode transitions under which it was exercised;
- first-live-delivery and first-live-submission timestamps;
- initial/reminder mail schedules and template versions;
- campaign-owned content versions;
- the selected set of Ministry DUIDs;
- financial period, comparison period, and mapped fund DUIDs;
- configurable share-option versions;
- additional-information enabled flag; and
- daily/weekly Admin digest schedules.

Database constraints prevent overlapping active/scheduled intervals where both
campaigns could become active, and ensure at least one module is enabled. The
financial period has an inclusive end equal to the day before its first
anniversary. The immediately preceding equivalent period is the default
comparison period, but fund mappings are explicit.

The campaign-creation service and a transactional database guard permit at most
one campaign across `draft`, `scheduled`, and `active`. Creating a draft also
requires global Testing mode. Closed and archived campaigns may coexist with
the one current draft so their reporting and reconciliation remain available.

Deleting a Campaign through ordinary CRUD is impossible. Closing and archiving
retain all relationships. Exceptional purge is defined by the
[Admin portal](../admin-portal/spec.md#campaign-purge).

### Schedule revisions and fulfillment

`ScheduleDefinition` gives each initial, reminder, or digest schedule a stable
logical UUID and campaign/type. Its immutable `ScheduleRevision` rows contain
the versioned local date/time, template references, and replacement/removal
metadata; exactly one revision is current unless the definition was removed.

`ScheduleFulfillment` records a successful semantic delivery independently of
revision. Its unique key combines schedule UUID, mode, semantic recipient or
audience, and occurrence slot (for example Family DUID for a one-time Family
mail or campaign-local date for a daily digest). It references the successful
occurrence. Thus changing a time or template never makes a recipient already
fulfilled in that mode eligible for the same logical schedule again. Schedule
replacement and removal follow the atomic cancellation policy in the
[background-processing specification](../background-processing/spec.md#schedule-replacement-and-removal).

### Source snapshot

`SourceSnapshot` records form an ordered history. Each stores type (`full` or
`delta`), start/completion/promotion times, ParishSoft organization ID,
collection counts, validation result, source watermark/change cursor, and a
content digest.

Normalized versioned tables store Family, Member, Ministry, roster, fund,
pledge, and contribution facts associated with a snapshot. Implementations may
deduplicate unchanged entity payloads, but querying a snapshot must reproduce
one coherent corpus. Short-lived HTTP cache files are operational artifacts,
not durable snapshots, and may be expired normally.

Exactly one promoted snapshot is current. Promotion changes that pointer and
all derived current indexes in one database transaction. A failed or rejected
load never exposes a partial corpus.

### Family campaign identity

`FamilyCampaign` joins a ParishSoft Family DUID to a Campaign and stores:

- portal eligibility, email eligibility, and active status as of the current
  snapshot;
- first/last eligible timestamps and status reason;
- encrypted eight-letter display code plus the unique canonical HMAC fingerprint
  defined by the
  [credential specification](../architecture/spec.md#family-credential-security);
- hashed email-link token, token generation, and revocation time;
- initial/live invitation state;
- first live submission and current effective submission IDs; and
- latest session/activity metadata used by the Admin indicator.

Codes are generated for every newly active registered Family during initial
campaign population or snapshot promotion, even if the Family lacks eligible
email. A code is never changed during that campaign. Token rotation does not
change the code.

### Administration user and policy

`PortalUser` links a Google `sub` and current normalized verified email to the
login/audit history, including the validated Google hosted-domain claim when
present. Authorization policy uses:

- `DomainRule`: normalized domain with Staff and/or Ministry-leader roles;
  Administrator is prohibited;
- `AddressRule`: normalized exact address with any role set, including an empty
  set that explicitly denies access; and
- `MinistryAssignment`: user/address to Ministry DUID, source (`chair-seed` or
  `manual`), active flag, and audit metadata.

An exact address rule replaces, rather than unions with, a matching domain
rule. When the UI creates an override for a chairperson already inheriting a
domain role, it preselects the inherited roles plus Ministry leader so the
Admin can see and confirm the replacement. `gmail.com` is prohibited as a
domain rule but individual Gmail addresses are allowed. Every domain rule is a
Google Workspace/Cloud Identity hosted-domain rule: it grants roles only when
the signed `hd` claim and verified email suffix both match. An absent or
mismatched `hd` claim never falls back to suffix-only authorization.

Chairperson synchronization only seeds missing assignments; it never silently
revokes an existing explicit assignment. Stale seeded assignments are flagged
for Admin review.

### Submission

Every final click creates an immutable `Submission` version containing:

- campaign, Family DUID, monotonically increasing Family version, and mode
  (`test` or `live`);
- baseline source snapshot and prior effective submission, if any;
- submitted UTC time and derived parish-local date;
- complete normalized answers for all enabled sections;
- validation/content/schema versions; and
- request/session correlation without storing credentials.

The current effective live response points to the latest accepted live version.
Test versions are isolated from live calculations, response prefilling,
workflows, mail eligibility, and reports. Production transition permanently
deletes test submissions and their sensitive audit payloads, retaining only a
non-sensitive count/timestamp event.

The submission schema stores Family census answers, existing Member answers,
proposed Members with local UUIDs, Ministry join/leave choices, annual pledge,
frequency, share-option stable IDs and optional Other text, and additional
information. Share-option labels are snapshotted so later edits cannot change
the meaning of prior answers.

### Proposed changes

After submission, reconciliation derives one `ProposedChange` per atomic field
or semantic request. It records entity type/identifier, field, baseline,
submitted, current, Admin-edited proposed value, writability classification,
and provenance.

Decision and execution are orthogonal:

- decision: `unreviewed`, `approved`, or `ignored`;
- execution: `pending`, `conflict`, `queued`, `published`, `resolved_upstream`,
  `resolved_external`, `failed`, `superseded`, or `cancelled`.

Ignored decisions remain searchable and may return to unreviewed/approved.
Published or resolved records are immutable outcomes; a later response creates
new proposed-change records where necessary.

Writability is `api`, `manual`, or `report-only`. It is derived from a shared
ParishSoft capability registry, not guessed in views. The initial registry is:

| Change | Handling |
| --- | --- |
| Family home/mailing contact/address fields | ParishSoft v2 Family contact PUT |
| Member first/middle/last/maiden names | ParishSoft v2 Member contact PUT |
| Member birth date, language, gender | ParishSoft v2 Member contact PUT where semantically sufficient |
| Member death-date field correction | ParishSoft v2 Member contact PUT |
| Member email and home/mobile/work phone | ParishSoft v2 Member contact PUT |
| Prefix, suffix, marital status | Manual unless a verified API capability is added |
| Deceased-status or moved-household semantic request | Manual; writing a death date alone does not complete the semantic request |
| Proposed Member/Family structure | Manual |
| Parish-wide email opt-out | Manual/report to the responsible source system |
| Ministry join/leave | Ministry workflow/report only |
| New pledge/share method | Financial report/export only |

The capability registry must be covered by tests against recorded, redacted API
shapes and updated when ParishSoft support changes.

A submission that marks a Member deceased and supplies a death date creates two
distinct proposals: an API-writable `death_date` field proposal and a manual
`deceased_status` semantic request. Their decisions and execution states are
independent, and neither queue may collapse or silently discard the other.

### Follow-up records

An `AdditionalInformationItem` is created only when a live submission's
nonblank text differs from the Family's prior effective text. It retains the
submitted text, submission reference, `follow_up_needed`, `followed_up_at`, and
versioned Staff notes. Clearing/changing the effective text does not delete
older items.

A `MinistryRequest` represents one Member/Ministry requested action (`join` or
`leave`). Latest effective submissions may create, cancel, or supersede a
request but do not erase its history. Workflow state is `new`, `assigned`,
`in_progress`, `resolved`, `closed_no_response`, `cancelled`, or `superseded`.
Resolution outcome is `joined`, `leave_confirmed`, `declined`, `no_response`,
`duplicate`, or `other`. Contact attempts record time, channel, actor, and
notes. Assignees must be authorized for that Ministry.

Manual census work uses proposed-change execution state and notes rather than a
separate workflow. Admin and Staff may mark manual items resolved externally or
ignored; only Admin may approve/edit/publish API-writable items.

### Content and email templates

Named campaign content slots have immutable versions. Required slots include
Family login help, pre-start, post-end, Family census introduction, Member
census introduction, Ministry introduction, financial introduction,
additional-information prompt, review/attestation introduction, Thank You page,
access-denied contact help, and submission-confirmation text. Empty optional
slots render nothing.

Initial, reminder, confirmation, daily digest, weekly digest, and critical-alert
templates have separate subject, sanitized HTML, and generated/edited plain-text
versions. Family templates support only documented placeholders, including
eligible names, code, secure link, generic URL, parish fields, dates, and
campaign fields. Unknown placeholders are validation failures, not empty text.

### Job, outbox, audit, and purge records

`TaskRun` stores task type, idempotency key, state, progress phase/counts,
attempts, timestamps, initiator, heartbeat, summary, and sanitized error.
`OutboxMessage` stores exact intended/routed recipients, redacted rendered
content, template version, reason, campaign/Family links, mode, delivery
attempts, stable semantic idempotency key, provider-key/message-ID fingerprints,
reconciliation evidence, resolution actor/time, and provider result. Its state
is `pending`, `submitting`, `retry_wait`, `delivery_unknown`, `delivered`,
`permanent_failure`, or `cancelled`; only the final three are terminal.
`delivery_unknown` follows the provider-acceptance workflow in the
[background-processing specification](../background-processing/spec.md#family-invitations-and-reminders).
Until a terminal state, credential substitutions needed for retry are
separately sealed with application-level encryption and a versioned key ID;
only the dispatch worker may decrypt them. Terminal handling scrubs the sealed
values. Provider acceptance means sent; bounce processing is outside the first
release.

`AuditEvent` is append-only and stores actor type/ID, action, entity, campaign,
UTC time, request/task correlation, source IP metadata, and redacted structured
before/after values. `OperationalLog` stores the five standard levels and
structured context. The Admin log view queries both without pretending DEBUG
diagnostics are domain audit events.

`PurgeRequest` records the selected archived campaign, initiating Admin, recent
backup reference, re-authentication time, typed-confirmation digest, estimated
counts, state, batch checkpoints, progress, and final non-sensitive tombstone.
It has a durable state machine separate from the associated
[Campaign lifecycle](../spec.md#campaign-lifecycle):

- `draft`: the campaign was selected and inventory/backup checks may be run or
  refreshed;
- `ready_for_confirmation`: inventory is current, the backup is verified, and
  the irreversible effects have been acknowledged;
- `queued`: fresh re-authentication and both typed confirmations succeeded and
  the idempotent worker task exists, but no worker has claimed it;
- `running`: the worker claimed the request and atomically moved the Campaign
  from `archived` to `purging`;
- `failed_pre_delete`: execution failed before any deletion batch committed and
  the intact Campaign was atomically returned to `archived`;
- `deletion_failed`: at least one database deletion batch committed, a later
  database phase exhausted automatic retries, and the Campaign remains
  inaccessible in `purging` pending an Admin retry;
- `cleanup_failed`: database deletion completed but generated-file cleanup
  exhausted automatic retries, corresponding to Campaign
  `purge_cleanup_failed`;
- `succeeded`: deletion, verification, and cleanup completed, corresponding to
  Campaign `purged`; and
- `cancelled`: an Admin cancelled before a worker claim, leaving the Campaign
  `archived`.

The permitted request transitions are `draft` to `ready_for_confirmation` or
`cancelled`; `ready_for_confirmation` back to `draft` when a prerequisite
expires, or to `queued`/`cancelled`; `queued` to `running` or, through an atomic
claim cancellation, `cancelled`; `running` to `failed_pre_delete`,
`deletion_failed`, `cleanup_failed`, or `succeeded`; `deletion_failed` back to
`running` on retry; and `cleanup_failed` to `succeeded` after idempotent cleanup.
The four other states are terminal. Every transition is audited. A database
constraint permits at most one request in a nonterminal state for a Campaign;
request and Campaign transitions that must correspond occur in one transaction.

## Effective-value merge

The value displayed on a repeat visit is computed per field:

1. Start with the current ParishSoft value.
2. If there is no prior effective response change, use current.
3. If current now equals the prior proposed value, the proposal is resolved
   upstream and current is used without a change marker.
4. If current still equals the prior baseline, overlay the prior proposed value
   and mark it changed.
5. If current differs from both baseline and proposal, preserve the proposal,
   mark a source conflict, and show the Family only that its previously supplied
   value remains new; never mention ParishSoft.

Every equality test uses the field type's canonical comparison value, not its
display or raw upstream representation. Email is Unicode/case normalized;
phones use normalized dialable components; ordinary text applies the specified
Unicode and whitespace normalization; dates, enums, booleans, identifiers, and
money compare in their typed canonical forms; and addresses compare normalized
components. The same comparison registry is used for portal change markers,
snapshot reconciliation, and publication preflight. Display forms remain
unchanged unless an accepted proposal changes them.

Terminal Member states (deceased or no longer in household) and proposed
Members follow the prior effective response until cancelled by a new
submission or resolved by current source structure.

An incoming snapshot reruns this merge for all unresolved proposals. It does
not mutate an immutable submission; it updates derived current/reconciliation
records.

## Submission concurrency

The Family form receives the current effective submission version and source
snapshot IDs. Final submission uses compare-and-swap semantics. If another
session submitted first, the stale submit is rejected without saving; the user
is told newer answers exist and must reload/review the merged form. Multiple
read sessions are otherwise allowed.

Within a successful transaction, the system writes the immutable submission,
sets it effective, derives proposals/workflows, records audit events, updates
participation facts, and inserts the confirmation-email outbox row. Either all
commit or none do.

## ParishSoft refresh reconciliation

Snapshot promotion performs these effects transactionally:

- recompute active registered Families and Members using shared ParishKit
  predicates;
- generate campaign identities for newly Portal-eligible Families, regardless
  of email eligibility;
- revoke access for newly inactive/non-Parishioner Families without deleting
  prior history;
- restore the existing code if a Family reactivates;
- recompute eligible head email addresses;
- resolve proposals that now match upstream;
- mark three-way conflicts;
- resolve Ministry requests whose requested roster state is now current;
- refresh seeded Chairperson suggestions/assignment warnings; and
- request the initial-invitation evaluation defined by
  [background processing](../background-processing/spec.md#family-invitations-and-reminders)
  for each newly active Family.

If a reactivated Family already has a live submission, it remains a responder
and does not receive a new initial invitation. Current metrics exclude inactive
Families; historical activity retains submissions made while eligible.

## Review and publication

The Admin review UI is field-oriented and defaults to unreviewed changes. It
supports stable sorting, filters, pagination, row selection, select-all-current-
filter, bulk approve/ignore/reset, and inline proposed-value edits. Bulk actions
must display exact affected counts and cannot operate on records outside the
current authorization/filter snapshot.

`Publish to ParishSoft` performs an asynchronous mandatory preflight:

1. Acquire `parishsoft-source-mutation` and validate expected organization.
2. Fetch uncached current contact payloads for every affected entity.
3. Re-evaluate each approved field against baseline/proposed/current.
4. Mark already-matching fields resolved upstream.
5. If any field on an entity is a three-way conflict, block all writes to that
   entity and return it for re-review.
6. Merge approved values into the freshly fetched full payload, leaving
   unapproved fields at their current upstream values.
7. Persist the plan with the source payload digests, release the lock, present
   counts and conflicts, and require fresh Google authentication and final
   confirmation before queueing writes.

Execution reacquires `parishsoft-source-mutation` and, immediately before each
entity PUT, fetches its uncached full payload and repeats canonical merge/conflict
evaluation against the confirmed plan digest. Any difference invalidates that
entity without writing and returns it for confirmation; a conditional-write
primitive is used when ParishSoft exposes one. Publication then groups fields by
entity and uses idempotent v2 `PUT` operations with bounded shared retries. Each
entity is verified by an uncached read after write. Success marks its fields
published/resolved; failure records a sanitized error and leaves the entity
retryable without replaying successful entities. After releasing the lock, a
final targeted/full refresh reconciles the promoted snapshot.

Admins may publish any reviewed subset during or after an active campaign.
They need not finish review in one session. Later Family submissions supersede
unpublished proposals as appropriate but never rewrite publication history.

## Retention and deletion

Live submissions, promoted normalized snapshot history, workflow history,
email metadata/content, and audit events are retained indefinitely by default.
Operational HTTP caches, temporary export files, and transient task payloads
have bounded cleanup policies defined by operations.

The two exceptions are:

- test responses and their sensitive audit payloads are deleted during the
  Production transition;
- Testing-mode outbox rows and sensitive delivery audit payloads are deleted
  during that transition after producing the non-sensitive aggregate defined by
  the [Admin readiness workflow](../admin-portal/spec.md#production-transition);
  and
- an Admin-approved campaign purge removes campaign-owned live detail through
  the guarded web workflow while retaining only a non-sensitive tombstone.

No foreign-key cascade may accidentally remove shared parish/integration
configuration, another campaign, current Portal users, or backup metadata.
