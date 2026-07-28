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

`AppliedConfigurationVersion` is the immutable database snapshot of one
schema-versioned authoritative Stewardship YAML document. It records YAML
version ID and digest, schema version, predecessor, canonical non-secret
document, normalized-materialization status/digest, activation instant, actor/
request, and validation evidence. At most one version is active. The active row
must match the atomically selected YAML manifest digest; runtime code cannot
edit a normalized configuration row directly.

`ConfigurationChangeRequest` records requested patch, base digest, actor,
validation/errors, candidate digest, installer checkpoints, and state:
`staged`, `validating`, `prepared`, `yaml_activated`, `applied`, `failed`, or
`cancelled`. The installer state machine is idempotent. A stale base fails
without changing YAML; a crash after YAML activation leaves the application
fail-closed until the prepared matching database snapshot is activated.

There is exactly one materialized `Parish` row for each applied configuration
version containing the display name, main website URL, IANA timezone, valid US
main phone number, and branding references. Public origin remains solely
authoritative in deployment configuration and is not duplicated as an editable
Parish value. Logo uploads produce normalized large, menu, icon, and favicon
variants. Accepted inputs are PNG, JPEG, or WebP; files are decoded and re-
encoded before use.

One versioned `SystemConfiguration` holds the global `testing` or `production`
mode, single valid Testing recipient, current campaign pointer, durable
`restore_review_required` gate, and mode-change history. The system starts in
Testing. Mode is not historical campaign data: deliveries/submissions snapshot
the mode in which they occurred so old records retain their meaning after a
later global transition. The restore gate is an independent fail-closed state;
Testing-mode Family access does not override it. The gate also carries restore
ID, backup snapshot instant, activation time, and release state/proposed-state
evidence. It is cleared only by the state-aware release transaction defined in
operations, which updates current Campaign state and global mode atomically.

Applied integration records materialize non-secret YAML settings and credential
fingerprints: ParishSoft expected organization, Google OAuth/Workspace delegated
identity, outgoing sender/reply address, optional Slack channel, and backup
target. Secret values remain in credential files.

`SecretReplacementRequest` records target type, sealed-staging reference and
expiry, actor/reauthentication, expected prior fingerprint, validation/test and
installer checkpoints, resulting safe fingerprint, consumer acknowledgement,
and terminal outcome. It never stores plaintext. A target-specific uniqueness
constraint permits only one nonterminal request per credential target; only the
matching installer identity may claim it. Expiry/failure cleanup removes the
sealed payload while preserving non-secret audit metadata.

### Campaign

A `Campaign` includes:

- UUID, unique human name, optional stewardship year label, and lifecycle state;
- an IANA campaign-timezone snapshot, initialized from the Parish timezone;
- local start/end dates and their resolved UTC boundary instants;
- enabled census, Ministry, and financial modules;
- references to the global mode transitions under which it was exercised;
- first-live-delivery and first-live-submission timestamps;
- live-delivery-pause flag/version, actor, reason, start/resume times, and
  post-close resolution metadata;
- initial/reminder mail schedules and template versions;
- campaign-owned content versions;
- the selected set of Ministry DUIDs;
- financial period, comparison period, and mapped fund DUIDs;
- configurable share-option versions;
- additional-information enabled flag; and
- daily/weekly Admin digest schedules.

Every configurable Campaign field above is materialized from and references the
exact `AppliedConfigurationVersion` that defined it. Lifecycle state, global
mode references, delivery/workflow state, and other runtime facts remain
PostgreSQL-authoritative and are not written back into YAML. A configuration
change creates a new applied version and normalized Campaign configuration while
preserving prior versions for submissions, messages, reports, and audit.

Database constraints prevent overlapping active/scheduled intervals where both
campaigns could become active, and ensure at least one module is enabled. The
financial period has an inclusive end equal to the day before its first
anniversary. The immediately preceding equivalent period is the default
comparison period, but fund mappings are explicit.

The campaign-creation service and a transactional database guard permit at most
one current campaign across `draft`, `scheduled`, `active`, and `closed`.
Creating a draft also requires global Testing mode, a null current-campaign
pointer, no campaign in any of those four states, and no campaign in `purging`
or `purge_cleanup_failed`. Only archived or purged historical campaigns may
coexist with a new current draft; archived campaigns remain available for
historical reporting.
The only backward transition among these current-campaign states is the
guarded, pre-start `scheduled` to `draft` Production withdrawal defined by the
[campaign lifecycle](../spec.md#campaign-lifecycle). It changes global mode to
Testing and invalidates readiness in the same transaction; database guards
reject it after the campaign has become `active`.

Initial Production readiness may move `draft` to `scheduled` before the
resolved start or directly to `active` within the half-open interval. The
transaction rejects a commit at/after close and locks structural settings in
either successful state, including the campaign-timezone snapshot. Every
campaign boundary, schedule occurrence, digest day, report bucket, and
campaign-local rendering thereafter uses that immutable snapshot rather than
the mutable Parish default. Direct activation creates already-due live work
under the ordinary stable idempotency and fulfillment keys.

The guarded reopen transition moves `closed` directly to `active` only when its
proposed extended closing instant is after the transaction time. The end-date,
Campaign/global-mode updates, and new access-token issuance commit atomically;
failure preserves the prior end date and mode. Original start and structural
settings remain locked.

The guarded post-archive Return to Testing transaction requires the current
campaign to remain archived and quiescent, sets or confirms global Testing mode,
and clears the current-campaign pointer under the same global lock even if the
mode was already Testing.
It does not alter historical OutboxMessage modes or the archived Campaign row.
Draft creation and this transition therefore cannot race to violate the
single-current-campaign invariant.

The guarded unarchive transaction can move `archived` to `closed` only while
that Campaign remains the current-campaign pointer and no other current-state
Campaign exists. Clearing the pointer through Return to Testing permanently
makes that archived Campaign historical and ineligible for unarchive.

`CampaignBoundaryOccurrence` stores campaign, kind (`start` or `close`),
resolved UTC boundary, state, attempts/lease, intended and actual transition
times, before/after lifecycle states, and audit/task correlation. A unique
constraint on campaign, kind, and resolved boundary makes scheduler insertion
and recovery idempotent. The transition and successful occurrence state commit
in one transaction under Campaign/global locks; obsolete occurrences terminate
with a structured reason rather than rewriting campaign history.

Live delivery pause is orthogonal to lifecycle and global mode. It never changes
an `active` Campaign to Testing, never changes live Submission classification,
and never reroutes a `production` OutboxMessage. Outbox rows held by it remain
in their ordinary nonterminal state with a structured pause-hold reference;
the dispatch claim must check both state and current pause version.

Deleting a Campaign through ordinary CRUD is impossible. Closing and archiving
retain all relationships. Exceptional purge is defined by the
[Admin portal](../admin-portal/spec.md#campaign-purge).

### Schedule revisions and fulfillment

`ScheduleDefinition` gives each initial, reminder, or digest schedule a stable
logical UUID and campaign/type. Its immutable `ScheduleRevision` rows contain
the versioned local date/time, template references, and replacement/removal
metadata; exactly one revision is current unless the definition was removed.

`ScheduleFulfillment` records that a semantic slot is covered independently of
revision. Its disposition is `delivered` or `coalesced`. Its unique key combines
schedule UUID, mode, semantic recipient or audience, and occurrence slot (for
example Family DUID for a one-time Family mail or campaign-local date for a
daily digest). A delivered row references the successful occurrence; a
coalesced row references the selected replacement occurrence that covers it
without masquerading as provider success. Thus changing a time or template
never makes a recipient whose slot is already covered in that mode eligible for
the same logical schedule again. Schedule replacement and removal follow the
atomic cancellation policy in the
[background-processing specification](../background-processing/spec.md#schedule-replacement-and-removal).

`RestoreDeliveryHold` records restore identifier, campaign/schedule semantic
key, target/slot, backup-snapshot and release-window instants, discovery source,
state, resolution actor/time, evidence note, and linked recovery occurrence.
State is `unreviewed`, `assumed_delivered`, `resend_authorized`, or
`not_applicable`. A unique semantic key makes inventory recomputation
idempotent. Unreviewed and assumed-delivered rows suppress only their referenced
occurrence; they are not `ScheduleFulfillment` and never count as provider
success. Resolution transitions and resend creation follow the
[restore workflow](../operations/spec.md#restore).

### Source snapshot

`SourceSnapshot` records form an ordered history. Each stores type (`full` or
`delta`), start/completion/promotion times, ParishSoft organization ID,
collection counts, validation result, source watermark/change cursor, and a
content digest. These lightweight manifests remain indefinitely and record
whether their complete corpus is still reconstructable or has been compacted.

Normalized versioned tables store canonical Family, Member, Ministry, roster,
fund, pledge, and contribution payloads by content digest. Content-addressed
deduplication is mandatory: a snapshot-to-version membership map reuses the
same immutable version whenever identity and canonical payload are unchanged.
No nightly or delta refresh may duplicate unchanged entity payload rows.
Querying an uncompacted snapshot must reproduce one coherent corpus. Short-
lived HTTP cache files are operational artifacts, not durable snapshots, and
may be expired normally.

Exactly one promoted snapshot is current. Promotion changes that pointer and
all derived current indexes in one database transaction. A failed or rejected
load never exposes a partial corpus.

Source compaction uses UTC cutoff instants and never compacts the current
snapshot or a snapshot protected by a submission baseline/effective version,
pinned report or digest, reconciliation/publication record, audit reference,
campaign boundary anchor, restore/delivery hold, or explicit operator hold.
Those snapshots and their membership/payload rows remain fully reconstructable
for the lifetime of the protecting record. Among otherwise unprotected
promoted snapshots, the system retains:

- every reconstructable snapshot for 90 days after promotion;
- after 90 days through one year, the latest promoted snapshot in each UTC
  calendar day; and
- after one year, the latest promoted snapshot in each UTC calendar month
  indefinitely.

Compaction keeps every manifest but may remove membership rows for redundant
unprotected snapshots, marking those manifests compacted. It then removes a
payload version only when no retained reconstructable snapshot or other
protected record references it. Anchor selection is deterministic, and a late
protection reference wins over cleanup under row locks. Cleanup is idempotent,
bounded, audited by counts/cutoffs rather than values, and cannot run while a
source promotion owns the mutation lease.

### Campaign daily report facts

`CampaignDailyFactSet` records one immutable, complete graph-calculation
generation for a Campaign, population scope, source-snapshot cutoff,
submission-version cutoff, and campaign-timezone version. Its state is
`building`, `ready`, or `failed`; at most one generation for an exact input key
may become ready. Child `CampaignDailyFact` rows hold one campaign-local date's
first-response count, cumulative response count, cohort denominator, percentage
inputs, effective pledge total/availability, and source-as-of metadata. A fact-
set pointer changes only after all expected dates validate and commit, so
readers never combine generations. Facts are derived, rebuildable data; pinned
digests/reports protect the precise ready generation and its source inputs from
compaction until their parent retention ends.

`SourceMutationLease` is the singleton durable exclusion record defined by
[background processing](../background-processing/spec.md#parishsoft-refresh).
It stores owner TaskRun, monotonically increasing fencing token, phase,
acquired/heartbeat/expiry times, and the external-request deadline used for
safe takeover. Task claims and snapshot promotion record and transactionally
verify the expected fencing token.

### Family campaign identity

`FamilyCampaign` joins a ParishSoft Family DUID to a Campaign and stores:

- portal eligibility, syntactic email eligibility, current email deliverability
  and its reason, and active status;
- first/last eligible timestamps and status reason;
- encrypted eight-letter display code plus the versioned canonical HMAC lookup
  rows defined by the
  [credential specification](../architecture/spec.md#family-credential-security);
- versioned encrypted reusable email-link token, its domain-separated SHA-256
  lookup digest, token generation, destruction, and revocation times;
- initial/live invitation state;
- first live submission and current effective submission IDs; and
- latest session/activity metadata used by the Admin indicator.

Codes are generated for every newly active registered Family during initial
campaign population or snapshot promotion, even if the Family lacks eligible
email. A code is never changed during that campaign. Token rotation does not
change it. A non-null access-token lookup digest is unique within its campaign
and backed by a database unique index; close-time destruction sets both token
ciphertext and digest null without weakening retained audit metadata.

`FamilyCodeFingerprint` stores FamilyCampaign, campaign, MAC key ID/algorithm,
and the canonical digest. Constraints allow at most one row per Family/key and
one digest per campaign/key. Generation and migration use the cross-key
collision protocol defined by the credential specification. Bulk population
uses the surrounding `READ COMMITTED` transaction and savepoint-scoped retries,
so all Family identities promote atomically while a rare unique-index conflict
retries only one random candidate. No view performs decryption scans.

### Administration user and policy

`PortalUser` links a Google `sub` and current normalized verified email to the
login/audit history, including the validated Google hosted-domain claim when
present. It is runtime identity state. Authorization policy materialized from
the active YAML version uses:

- `DomainRule`: normalized domain with Staff and/or Ministry-leader roles;
  Administrator is prohibited;
- `AddressRule`: normalized exact address with any role set, including an empty
  set that explicitly denies access; and
- `MinistryAssignment`: user/address to Ministry DUID, source (`chair-seed` or
  `manual`), state (`active` or `suspended`), suspension reason/time, and audit
  metadata.

Domain rules, address rules, and the configured base of Ministry assignments
carry their applied-configuration version and cannot be edited independently.
Source-driven suspension/reactivation and its review task are runtime overlays
that can remove scope immediately without rewriting YAML; an Admin decision to
create, restore as manual, or delete configured policy goes through a
`ConfigurationChangeRequest` and becomes effective on activation.

An exact address rule replaces, rather than unions with, a matching domain
rule. When the UI creates an override for a chairperson already inheriting a
domain role, it preselects the inherited roles plus Ministry leader so the
Admin can see and confirm the replacement. `gmail.com` is prohibited as a
domain rule but individual Gmail addresses are allowed. Every domain rule is a
Google Workspace/Cloud Identity hosted-domain rule: it grants roles only when
the signed `hd` claim and verified email suffix both match. An absent or
mismatched `hd` claim never falls back to suffix-only authorization.

Chairperson synchronization creates or refreshes suggestions only; it never
creates an AddressRule, grants a role, or creates an active assignment. The
Admin-confirmed suggestion configuration request is the sole creator of a
`chair-seed` assignment and any corresponding exact-address/Ministry-leader
grant. For an
existing `chair-seed`, a promoted snapshot that no longer shows the active
Member as Chairperson of that active Ministry atomically changes it from
`active` to `suspended`, records the source evidence, and opens an Admin review
task. A suspended assignment grants no row scope on the next authorization
check. Manual assignments are never changed from source data. If the exact
AddressRule and Ministry-leader role were created solely for seeded assignments,
the role is removed when no active assignment remains; unrelated roles/rules
are preserved. If the source Chairperson relationship returns before review,
the seeded assignment reactivates and the task closes with audit.

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
| Member first/middle/last/nickname/maiden names | ParishSoft v2 Member contact PUT |
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
submitted text, submission reference, disposition (`current_actionable`,
`superseded`, or `withdrawn`), `follow_up_needed`, `followed_up_at`, and
versioned Staff notes. In the submission transaction, replacement text marks
the prior current item `superseded` and links the replacement; clearing text
marks it `withdrawn` without deleting history. Default Staff queues and weekly
digest content include only items still `current_actionable` at generation.
The next digest includes a compact correction section for items sent in a prior
digest and since superseded/withdrawn, preventing stale emailed work from
remaining silently actionable. History views expose every disposition.

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
`ProductionTransitionRequest` stores campaign/Admin, state, gate version,
inventory digest and counts, non-sensitive Testing aggregate reference, cleanup
TaskRun, batch checkpoints/counts, acknowledgement and reauthentication times,
readiness evidence, activation result, and sanitized failure. Its states are
`cleanup_queued`, `cleanup_running`, `cleanup_retry_wait`, `cleanup_complete`,
`cleanup_failed`, `activated`, and `cancelled`. `cleanup_failed` means automatic
retries were exhausted; it retains checkpoints and the go-live gate and exposes
explicit retry/cancel recovery with CRITICAL escalation. Every state except the
last two owns the Campaign's go-live gate; a constraint permits at most one
gate-owning request. Cleanup
checkpoints and deletions commit together, while final campaign/mode activation
and gate release commit together. No transition restores a deleted Testing row.
`OutboxMessage` stores exact intended/routed recipients, redacted rendered
content, template version, reason, campaign/Family links, mode, immutable routing
class (`testing_override`, `production`, or `operational`), delivery attempts,
stable semantic idempotency key, provider-key/message-ID fingerprints,
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

`AuditEvent` is append-only and stores ownership scope, actor type/ID, action,
entity, optional campaign, UTC time, request/task correlation, source IP
metadata, and redacted structured before/after values. `OperationalLog` stores
the five standard levels and structured context. The Admin log view queries both
without pretending DEBUG diagnostics are domain audit events. While a campaign
purge gate is active, read-only report access uses parish ownership and a plain
campaign UUID/tombstone reference rather than a campaign-owned foreign key. The
retained event contains no viewed report data and is outside purge inventory;
exports and mutations remain blocked.

`PurgeRequest` records the selected archived campaign, initiating Admin,
post-quiescence inventory and completion/expiry times, purge-triggered backup
task and immutable verified-backup reference, backup completion/expiry times,
re-authentication time, typed-confirmation digest, estimated counts, gate-
acquisition/quiescence times, state, batch checkpoints, progress, and final
non-sensitive tombstone. A request in any state other than
`cancelled` or `failed_pre_delete` owns the durable campaign purge gate. It has
a state machine separate from the associated
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
expires, or to `queued`/`cancelled`; and `queued` to `running` or, through an
atomic claim cancellation, `cancelled`. An initial `running` attempt may enter
`failed_pre_delete` only while its durable checkpoint proves no deletion batch
has committed; any `running` attempt may enter `deletion_failed`,
`cleanup_failed`, or `succeeded` as appropriate. `deletion_failed` returns to
`running` on retry or remains `deletion_failed` when that retry exhausts without
progress. `cleanup_failed` moves to `succeeded` after idempotent cleanup or
remains `cleanup_failed` when cleanup retry again exhausts. No post-deletion
retry path can reach `failed_pre_delete` or release the gate.
The three terminal states are `failed_pre_delete`, `succeeded`, and `cancelled`.
Every transition is audited. A database constraint permits at most one request
in a nonterminal state for a Campaign; request and Campaign transitions that
must correspond occur in one transaction.

Creating the `draft` request and acquiring its purge gate are one transaction
under the Campaign and global current-campaign locks. It requires the target to
remain `archived`, global Testing mode, and a null current-campaign pointer; the
Admin must already have completed Return to Testing. It also requires no other
campaign in `draft`, `scheduled`, `active`, `closed`, `purging`, or
`purge_cleanup_failed`, so purge cannot begin after successor preparation. The
request transaction rejects a stale target that is still the current pointer,
preventing completion from leaving a pointer to a `purged` tombstone. The shared
campaign-work admission service checks
that gate under the same lock before it creates any new campaign-owned task,
occurrence, outbox message, export, publication plan, workflow mutation, or
other durable campaign work. Only purge preparation/execution, its operational
notifications, safe cancellation/drain actions, and gate cancellation are
admitted while the gate exists. Read-only access that creates no campaign-owned
record may continue. This check applies to web requests, schedulers, workers,
retries, and internal service calls rather than relying on disabled UI alone.

Gate acquisition inventories every already nonterminal campaign task,
occurrence, outbox row, and export. No new worker may claim queued or retrying
work after acquisition; safely cancellable records become terminal
`cancelled`, while already running, provider-submitting, delivery-unknown, or
otherwise irreversible work must reach an accurately reconciled terminal state.
The request records a quiescence time only after none remain. Inventory and
backup evidence used for confirmation must describe a database snapshot at or
after that quiescence time. Each expires 60 minutes after its respective
completion. Ordinary expiration invalidates only that artifact and preserves
the other artifact when it remains current; a later campaign-owned mutation or
change to quiescence invalidates both. The final confirmation transaction checks
both stored expirations and the shared mutation/quiescence invalidation version
under the request and Campaign locks.

The gate is released only when the request becomes `cancelled` or
`failed_pre_delete`, before any deletion has committed. It remains permanent
through `running`, either post-deletion failure state, and `succeeded`. Worker
claim rechecks under row locks that the request owns the gate, the Campaign is
still `archived`, all conflicting work is terminal, inventory and backup
evidence remain current, and no campaign mutation occurred after their
snapshot. Failure performs no deletion and atomically enters
`failed_pre_delete`, releases the gate, and leaves the Campaign `archived`.

## Effective-value merge

The value displayed on a repeat Family visit is computed from the immutable
Family-submitted value, never an Admin review edit:

1. Start with the current ParishSoft value.
2. If there is no prior effective response change, use current.
3. If current now equals the prior Family-submitted value, the proposal is
   resolved upstream and current is used without a change marker.
4. If a published/resolved ProposedChange has an Admin-edited value and current
   equals that edit, use current without attributing the edit to the Family.
5. If current still equals the prior baseline, overlay the prior Family-
   submitted value and mark it changed.
6. If current differs from baseline, Family-submitted value, and any resolved
   Admin edit, preserve the Family-submitted value, mark a source conflict, and
   say only that the Family's previously supplied value remains new; never show
   or attribute the Admin edit and never mention ParishSoft.

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
participation facts, and either inserts the confirmation-email outbox row or,
when no deliverable eligible-head address exists, records the non-error audit
action `submission_receipt_skipped` with reason
`no_deliverable_recipient`. Either all commit or none do.

## ParishSoft refresh reconciliation

Snapshot promotion performs these effects transactionally:

- recompute active registered Families and Members using shared ParishKit
  predicates;
- generate campaign identities for newly Portal-eligible Families, regardless
  of email eligibility;
- revoke access for newly inactive/non-Parishioner Families without deleting
  prior history;
- restore the existing code if a Family reactivates;
- recompute eligible head email addresses and current deliverability after
  applying provider-suppression records;
- resolve proposals that now match upstream;
- mark three-way conflicts;
- resolve Ministry requests whose requested roster state is now current;
- refresh seeded Chairperson suggestions/assignment warnings; and
- request the initial-invitation evaluation defined by
  [background processing](../background-processing/spec.md#family-invitations-and-reminders)
  for each newly active Family and each eligible nonresponder whose durable
  deliverability generation changed from non-deliverable to deliverable.

Provider-suppression removal outside snapshot promotion invokes the same
transactional evaluation service after it increments the Family's deliverability
generation. Stable occurrence and semantic-fulfillment keys make repeated
evaluation harmless.

When `restore_review_required` is active, that request is durable deferred
intent only: promotion does not materialize or dispatch an ordinary invitation.
The restore-release transaction re-evaluates each affected Family and
atomically creates the applicable occurrence and uncertainty hold before it
opens normal work admission.

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

1. In a short transaction, create the preflight record with the configured
   expected-organization and promoted source-version identifiers. Preflight
   does not claim the mutation lease.
2. Validate the upstream organization without cache and fetch uncached current
   contact payloads for every affected entity without holding a database lock.
3. Re-evaluate each approved field against baseline/proposed/current.
4. Mark already-matching fields resolved upstream.
5. If any field on an entity is a three-way conflict, block all writes to that
   entity and return it for re-review.
6. Merge approved values into the freshly fetched full payload, leaving
   unapproved fields at their current upstream values.
7. Persist the plan with the recorded source version and source-payload digests,
   present counts and conflicts, and require fresh Google authentication and
   final confirmation before queueing writes.

Execution claims `SourceMutationLease` and, immediately before each entity PUT,
revalidates its fencing token, fetches the uncached full payload, and repeats
canonical merge/conflict evaluation against the confirmed plan digest. Any
difference invalidates that entity without writing and returns it for
confirmation; a conditional-write primitive is used when ParishSoft exposes
one. Publication then groups fields by entity and uses idempotent v2 `PUT`
operations with bounded shared retries. Each entity is verified by an uncached
read after write. Success marks its fields published/resolved; failure records a
sanitized error and leaves the entity retryable without replaying successful
entities. After releasing the lease, a final targeted/full refresh reconciles
the promoted snapshot under a new lease claim.

Admins may publish any reviewed subset during or after an active campaign.
They need not finish review in one session. Later Family submissions supersede
unpublished proposals as appropriate but never rewrite publication history.

## Retention and deletion

Live submissions, protected and policy-anchor normalized snapshot history,
lightweight source manifests, workflow history, email metadata/content, and
audit events are retained indefinitely by default. Unprotected redundant source
corpora follow the compaction policy in
[Source snapshot](#source-snapshot). Operational HTTP caches, temporary export
files, and transient task payloads have bounded cleanup policies defined by
operations.

The three exceptions are:

- test responses and their sensitive audit payloads are deleted in bounded
  batches during the gated Production-transition cleanup phase;
- `testing_override` outbox rows and sensitive delivery audit payloads are
  deleted during that cleanup together with their Testing-only
  ScheduleOccurrence and ScheduleFulfillment rows after producing the non-
  sensitive aggregate defined by the
  [Admin readiness workflow](../admin-portal/spec.md#production-transition);
  `operational` rows are retained under normal policy even when created while
  the global mode was Testing; and
- an Admin-approved campaign purge removes campaign-owned live detail through
  the guarded web workflow while retaining only a non-sensitive tombstone.

No foreign-key cascade may accidentally remove shared parish/integration
configuration, another campaign, current Portal users, or backup metadata.
