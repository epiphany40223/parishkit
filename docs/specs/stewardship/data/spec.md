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

Even without a crash, applying a request selects the new YAML a moment
before its database activation commits. While the selected YAML is exactly
one activation ahead (its predecessor digest is the active database digest,
and the active version is not the initial-setup bootstrap), authority checks
report a distinct *activation in progress* rather than a generic mismatch.
Work that meets it waits briefly and tries again, outside any transaction
because the activation needs the work-order lock: background tasks for up to
15 seconds and Admin pages for up to 3. The installer holds its session lock
from YAML selection through activation, so a one-step mismatch seen twice
while that lock is free is a failed activation, not one in progress: it is
the ordinary mismatch that requires recovery, at once. A background wait
that runs out holds the work (never failing it) and records a durable
`task_timed_out` entry (`what` `configuration_activation`, its limit and
elapsed time) at WARNING; an Admin page's logs only the process-log line. An
Admin poll that still meets it answers its ordinary `503` retry response,
which is not logged as a server error; every other `503` still is. Every
other mismatch still requires recovery. See
[background holds](../background-processing/spec.md#configuration-activation-holds).

An exceptional end-date/reopen candidate may lose its date eligibility or
readiness before database activation can succeed. Its dedicated cancellation
workflow records an immutable `CampaignConfigurationAbort` before restoring
the YAML manifest to the exact predecessor that is **still active in the
database**. The installer must prove the candidate was never applied, serialize
cancellation against activation, recheck current Admin authority, and recover
an interrupted manifest restoration from that journal before ordinary recovery.
The request then ends as failed with `invalid_candidate`; the abort preserves
its specific reason. This narrow cancellation never rolls back an applied
version, removes prepared history, or chooses an arbitrary side of a mismatch.
Ordinary configuration recovery retains the forward-only contract above.

Initial setup has the same narrowly scoped journaled cancellation exception,
approved September 12, 2026. An immutable setup abort binds the original setup
attempt, its exact configuration request, the still-applied bootstrap predecessor,
and the selected-but-unapplied candidate. Before restoring that predecessor's
manifest, the installer must prove the candidate was never activated, serialize
against final activation, and verify the original attempt's cancellation or
expiry. Replaying the journal never restores an arbitrary version or rewinds a
later coherent configuration. Final database activation, source selection,
and the configured marker commit atomically (setup creates no campaign, so no
Family codes, #142); there is no applied-configuration
interval in which setup cancellation is still allowed. Abort recovery precedes
ordinary forward recovery, and cleanup cannot finish until selected YAML and
the applied bootstrap digest agree. Safe journal/checkpoint history is retained;
wizard-only values/files are scrubbed by their owning cleanup services.

Configuration requests identify their authority as authenticated Admin or the
explicit [offline operator-recovery workflow](../operations/spec.md#offline-admin-access-recovery).
The latter stores a named operator, reason, confirmed deployment/target, and
stable recovery-operation ID rather than fabricating a PortalUser. Enforce
operation-ID uniqueness and the permitted Administrator-user-only patch in
that dedicated service; web/caller-supplied authority fields cannot select it.
Normal configuration APIs retain current-Admin and CSRF checks. Recovery uses
the same installer checkpoints and atomically records its required session
revocation, parish-owned audit, and security-event/notification intents with
matching database activation.

A configuration request stores a client idempotency key and canonical
payload/base-digest fingerprint. Enforce uniqueness per actor/key: identical
retries return the same request/status, while key reuse with a different
payload is rejected. Status responses include the request's applied-version
ID/digest only after matching YAML/database activation. Lookup and retry
require current authorization; idempotency is not a bypass of role, CSRF, or
activation guards. A retry by the same Administrator with the same capability
returns its original request even when the confirming page's own scope check
would now refuse a new one (for example, Create the campaign once its campaign
exists, or an editor whose settings changed since the review), because no
second change is recorded (#142). [User changes](../admin-portal/spec.md#portal-user-management)
are ordinary requests made by Review and Apply; the user-management autosave
queue this paragraph once served is retired (#952).

There is exactly one materialized `Parish` row for each applied configuration
version containing the display name, main website URL, IANA timezone, valid US
main phone number, optional HTTPS online giving URL and date format, and
branding references. Public origin remains solely
authoritative in deployment configuration and is not duplicated as an editable
Parish value. Logo uploads produce normalized large, menu, icon, and favicon
variants. Accepted inputs are PNG, JPEG, or WebP; files are decoded and re-
encoded before use. A campaign's optional `artwork` value (absent when
empty) holds its theme artwork: `images` maps the `banner`, `welcome`,
`member`, `financial` and `closing` slots to single-image branding bundles (a
`banner` image, or a `section` icon for the page slots), and `hide_banner`
lists, in canonical order, the Family emails that skip the banner. Selected
images are pinned against staging cleanup like the logos, and `artwork` is
exempt from the live-campaign structural lock.

A campaign's optional `reminder_workgroup` value (absent when unset; #861)
is the name, trimmed text of at most 200 characters, of the ParishSoft Family
WorkGroup whose Families get no Reminders. It is exempt from the live-campaign
structural lock too (Python admission and the configuration pointer guard,
frozen migration 0015), so it can change while the campaign runs, and needs
no configuration schema version: like `artwork`, documents without it stay
valid. Each source refresh records what it read in its snapshot cursor's
load evidence, `cursor.load.reminder_workgroup = {name, found,
family_duids}`; when `found` is false, `family_duids` is the set the
snapshot before it applied, kept until a refresh finds the named WorkGroup
(#1305), not a membership ParishSoft reported. Nothing else stores the membership, and no Family row,
eligibility flag, code or link changes because of it. The current snapshot's
record applies while the campaign names any WorkGroup, even one whose `name`
differs from the record's until a refresh reads it, and never while the
setting is cleared (see
[background processing](../background-processing/spec.md#family-invitations-and-reminders)).

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
identity, outgoing sender/reply address, optional Slack channel, backup
target, and backup encryption key. Secret values remain in credential files.

`SecretReplacementRequest` records target type, sealed-staging reference and
expiry, actor and the acting session's sign-in instant, expected prior fingerprint, validation/test and
installer checkpoints, resulting safe fingerprint, consumer acknowledgement,
and terminal outcome. It never stores plaintext. A target-specific uniqueness
constraint permits only one nonterminal request per credential target; only the
matching installer identity may claim it. Expiry/failure cleanup removes the
sealed payload while preserving non-secret audit metadata.

### Retired Ministry activity

The parish-wide Ministry activity override is gone (Administrator decision,
2026-10-13, [#1267](https://github.com/epiphany40223/parishkit/issues/1267)):
a Ministry is offered when it is in the promoted catalog and the campaign's
Ministry selections (see the
[Ministries page](../admin-portal/spec.md#ministries-page)). Its
configuration section, `ministries` (records of a stable UUID, ParishSoft
organization ID, Ministry DUID and an explicit `active` flag), is retired:

- A new or changed configuration (YAML installation, setup, an Admin or
  command-line request, operator recovery) that holds any `ministries`
  record is refused with a validation error naming the section and saying
  to delete its records, as a configuration with a record in a section it
  does not accept is refused. An empty section is accepted. SQL refuses
  such a version too (`stewardship_ministry_complete_v1`).
- Applied history keeps what it recorded: its schema discriminators
  (`ministry-activity-v4` and its request formats) keep their meaning, and a
  stored version with records still verifies, its shape checked as before,
  from the document itself.
- A configuration whose applied version still holds records can change only
  by a request that removes them; any other change carrying them forward is
  refused.

The `stewardship_ministry_activity` table, which projected the section per
applied version, and its trigger functions are dropped by a forward
migration ([post-launch schema policy](../operations/spec.md#post-launch-schema-policy)).
That migration refuses to run while the active configuration still marks a
Ministry inactive, so a hidden Ministry never reappears unannounced;
Production had none.

### Campaign

A `Campaign` includes:

- UUID, unique human name, optional stewardship year label, and lifecycle state;
- an IANA campaign-timezone snapshot, chosen at creation (any catalog zone);
- local start/end dates and their resolved UTC boundary instants;
- enabled census, Ministry, and financial modules;
- references to the global mode transitions under which it was exercised;
- first-live-delivery and first-live-submission timestamps;
- live-delivery-pause flag/version, actor, reason, start/resume times, and
  post-close resolution metadata;
- initial/reminder mail schedules and template versions;
- campaign-owned content versions;
- the selected set of Ministry DUIDs;
- the [Families rule](#campaign-families) (`campaign_families`), fixed at
  creation;
- financial period, comparison period, and mapped fund DUIDs;
- configurable share-option versions;
- additional-information enabled flag;
- [report email](../report-emails/spec.md) schedules (which replaced the
  daily/weekly Admin digest schedules); and
- theme `artwork` and `reminder_workgroup` (described under
  [Parish and integrations](#parish-and-integrations)).

An older campaign may also hold an optional `talent_options` list. The Family
form stopped offering talents
([#970](https://github.com/epiphany40223/parishkit/issues/970)), so the value
is inert history: it is still validated when present and still part of the
Family form's definition digest, so forms issued earlier still verify, but
nothing offers or edits it. The database no longer reads it: migration 0056
dropped the built-in talent defaults function, and the response guard
accepts only an empty `talents` map on each new response's Member entries.
Submitted responses keep whatever talents they recorded.

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
or `purge_cleanup_failed`, and no nonterminal PurgeRequest anywhere in the
deployment. The check uses the same global lock as purge-request creation;
it blocks unfinished purge preparation as well as execution. A `succeeded`
request's permanent tombstone gate does not block successor creation. Only
archived or purged historical campaigns may
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
Campaign/global-mode updates, and activation of the prepared access-token
generation commit atomically; token generation/encryption/insertion finishes
asynchronously before confirmation. The final transaction changes a generation
pointer rather than rewriting every Family token;
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

v1 does not build reopen, archive, unarchive, Return to Testing or purge
([v1 launch scope](../../../plans/stewardship/v1-launch.md#cut-from-v1), items 1
and 2; the close-out wizard is
[#527](https://github.com/epiphany40223/parishkit/issues/527)). Their
lifecycle registry entries (`campaigns/lifecycle.py`) and some storage
primitives exist, but no Admin page or command calls them. With no purge,
there is no `PurgeRequest`. Draft creation checks the mode, the pointer, that
every existing campaign is archived or purged, that no restore review is
pending, and that no `CampaignWorkGate` (the purge reservation) is preparing
or running.

A post-go-live end-date edit
([#912](https://github.com/epiphany40223/parishkit/issues/912); pages and
command in the
[Admin portal spec](../admin-portal/spec.md#campaign-configuration)) binds
its `edit_end` `CampaignConfigurationIntent` in the same durable transaction
that records its configuration request, at the campaign and runtime versions
the confirmation rechecked under the work-order lock, so the installer never
selects the request without it. The configuration installer service passes
the owning admission for `edit_end` (`campaigns/live_end_date.py`); a
`reopen` intent has no owner until
[#527](https://github.com/epiphany40223/parishkit/issues/527), so it would
stay staged. The intent binds no token generation, so Family credentials are
untouched and keep working until the new closing instant; activation retires
the pending close occurrence and allocates one at the new instant.

The installer refuses an end edit that can no longer apply, rather than
retrying it ahead of every later request
([#944](https://github.com/epiphany40223/parishkit/pull/944)): the campaign
or runtime version changed, a restore review began, the campaign closed or
its previous end passed, the new end passed, its close work is claimed, or a
`CampaignWorkGate` is preparing or running. It judges these first in
preflight (the request fails, `stale_base` for a version change and
`invalid_candidate` otherwise, with no file written), again once the
candidate is prepared but before the YAML switch, and last inside the
activation transaction, where a refusal by the activation trigger itself
rolls back to a savepoint. After preparation, the refusal is recorded as the
abort journal above, attributed to the request's actor with a fixed reason,
and its restoration follows, so the request ends failed with
`invalid_candidate` and its predecessor selected, and an interrupted
restoration resumes from the journal. The configuration installer inserts
that journal only for these refusals, but the database does not enforce the
reason: the journal's trigger admits a row only for an unapplied request,
past staging, whose candidate is prepared on the base that is still applied.
An Administrator's cancellation of a
stuck change uses the same journal through `config request cancel` (see the
[Admin automation spec](../admin-automation/spec.md#schedules-and-configuration));
it remains the only way to cancel an unapplied candidate.

`CampaignBoundaryOccurrence` stores campaign, kind (`start` or `close`),
resolved UTC boundary, immutable execution revision, state, attempts/lease,
intended and actual transition times, before/after lifecycle states, and
audit/task correlation. Execution revisions are positive, monotonically
increasing integers per campaign and boundary kind, allocated under the
Campaign/global locks. A unique constraint on campaign, kind, and execution
revision makes scheduler insertion and recovery idempotent: repeated scans
reuse the current revision, not a previously replaced occurrence for that date.
The transition and successful occurrence state commit
in one transaction under Campaign/global locks; obsolete occurrences terminate
with a structured reason rather than rewriting campaign history.
Changing an end date A to B and back to A is supported. Every replacement gets
a fresh occurrence/revision and task root; prior terminal occurrences, task
outcomes and replacement audit remain immutable. Only the current revision
may execute lifecycle effects. Replaying an obsolete hint may acknowledge its
cancellation, but cannot revive it or affect the replacement.

Live delivery pause is orthogonal to lifecycle and global mode. It never changes
an `active` Campaign to Testing, never changes live Submission classification,
and never reroutes a `production` OutboxMessage. Outbox rows held by it remain
in their ordinary nonterminal state with a structured pause-hold reference;
the dispatch claim must check both state and current pause version.

`CampaignSetupReview` (`stewardship_campaign_setup_review`, frozen migration
0105, [#1404](https://github.com/epiphany40223/parishkit/issues/1404)) records a
[Campaign setup step](../admin-portal/spec.md#create-the-campaign) an
Administrator kept as is: campaign, step key (`settings`, `share_options`,
`content`, `schedules` or `images`), item key (empty for the whole step, or,
for `content`, one Pages and emails item: its kind and slot and, when it has
saved text, that revision's id without hyphens, such as
`page:welcome_<32 hex digits>`, or `page:census` for an empty page; #1404
PR 6b), actor and time, unique on
campaign, step and item. It is insert-only: the web role holds SELECT and
INSERT only, and its guard admits an INSERT only from a current Administrator,
for the current campaign while the system is in Testing mode, the campaign is
a draft and no go-live has started, and requires the
`campaign_setup_step_reviewed` audit event in the same transaction. Rows are
retained with their campaign; the campaign purge, when it lands, must
inventory and delete them as campaign-owned setup detail. Because the guard
refuses every DELETE and the campaign foreign key blocks deleting the
campaign while rows remain, that purge needs a forward migration that gives
the guard a purge path (for example, admitting DELETE from the purge
worker under its own lock); until then a purge attempt fails closed.

Deleting a Campaign through ordinary CRUD is impossible. Closing and archiving
retain all relationships. Exceptional purge is defined by the
[Admin portal](../admin-portal/spec.md#campaign-purge).

### Campaign Families

Status: built
([#1393](https://github.com/epiphany40223/parishkit/issues/1393),
Administrator redesign 2026-10-24): the rule's storage, its configuration
guard (frozen migration 0175), refreshes that apply it and the choice on
Create the campaign. Every campaign created before it keeps the
[legacy rule](#the-existing-campaigns-rule).

A campaign is sent to its **campaign Families** ("Families in this
campaign"): the Families its **Families rule** selects from the promoted
ParishSoft data. They are the Families a campaign targets, who may or may not
be what anyone would call active parishioners, so nothing calls them "active
parishioners" any more; the [wording
convention](../ui-conventions/spec.md#campaign-families-wording) lists the
words pages, downloads, the CLI and the specs use. A campaign Family is what
the code and the rest of this specification call *Portal-eligible*
(`portal_eligible`): it gets a Family code and link, the Family form and the
Family email, and it is counted in every campaign population. Nothing else
changes meaning: email eligibility, deliverability, Testing rehearsals, the
[drop check](../background-processing/spec.md#full-cycle) and the
[refresh reconciliation](#parishsoft-refresh-reconciliation) all read the
same flag.

The Administrator chooses the rule on [Create the
campaign](../admin-portal/spec.md#families-in-this-campaign), and it can
never be changed after the campaign is created: a campaign never gains or
loses Families because its rule changed, so no emailed code or link is ever
affected by a rule change. Families still enter and leave the campaign when
their ParishSoft data changes, exactly as Families became and stopped being
active registered Families before (below).

**The rules.** Every rule is three conditions joined by AND:

| Rule | Registered to this parish | Family group | At least one Member whose Status is |
| --- | --- | --- | --- |
| **Active parishioners** (preset) | yes | the group named exactly "Active" | Active |
| **Active parishioners and Staff** (preset) | yes | the group named exactly "Active" or exactly "Staff" | Active |
| **Advanced** | yes, or not required | any of the chosen groups (one or more), where "(No Family Group)" means a Family with no group | any of the chosen statuses (one or more of Active, ActOther, Unknown) |
| **Legacy** (only campaigns created before this rule existed) | yes | any group except the one named exactly "Inactive", or no group | anything except Inactive or Deceased (an empty status counts) |

- *Registered to this parish* is today's `family_is_parishioner`: the
  Family's `registeredOrganizationID` equals the configured ParishSoft
  organization. Advanced's "not required" admits Families registered
  elsewhere or nowhere; it never means "only Families registered elsewhere".
- A Member's Status is ParishSoft's `memberStatus`, compared exactly. A
  Deceased or Inactive Member never satisfies a preset or Advanced rule, since
  neither status can be chosen.
- ParishSoft's Family groups are a fixed list: a parish cannot rename, add
  or delete them (Administrator, 2026-10-24). So preset groups match by exact
  name, as the legacy rule's "Inactive" always has, the presets mean the same
  thing in every parish's data, and nothing handles a renamed group.
- Advanced groups are chosen from the group lookup the latest promoted
  snapshot recorded: every group, including groups no Family is in, the
  "Inactive" group and oddly named ones such as "(blank)" (a real group,
  not the absence of one), each stored by `famGroupID` with its name. Lookup
  IDs are positive integers.
- A Family with no group has `famGroupID` 0 or none, which the snapshot
  already records as no `family_group`. No preset selects it. Advanced
  offers it once, as the choice **(No Family Group)**, stored as
  `{"id": 0, "name": "(No Family Group)"}`, which matches exactly the
  Families with no group. "No Family Group" is only ParishSoft's own screen
  label for such a Family, not an entry in the group lookup: a read-only
  check against real ParishSoft on 2026-10-24 found 18 lookup groups, none
  of them "No Family Group", and a Family set to "No Family Group" in
  ParishSoft returned `famGroupID` 0
  ([#1393](https://github.com/epiphany40223/parishkit/issues/1393)). Should
  a lookup ever hold a group named exactly "No Family Group", Advanced still
  shows one such choice: (No Family Group) then also matches that group, and
  the group is not listed again.
- A chosen group (preset name or Advanced ID) that a later refresh no longer
  finds is not expected, since the list is fixed, but is still safe: it
  matches no Family. The refresh records the missing group in its load
  evidence and raises an Admin alert naming it; the drop check stops a refresh
  that would lose many Families that way, and the Administrator decides with
  [accept a large ParishSoft change once](../admin-portal/spec.md#accept-a-large-parishsoft-change-once).
  Until the Admin alert is built, the refresh logs a WARNING with the
  failure category `campaign_family_group_missing` (never the name).

One parish-neutral predicate in `parishkit/parishsoft.py` (for example
`family_matches_rule(family, rule, organization_id)`) evaluates every rule,
the legacy one by calling today's `family_is_active` and
`family_is_parishioner`, which other ParishKit tools keep using unchanged.

**What each flag means.** The snapshot's Family row keeps `active` and
`parishioner` with today's meanings (the legacy rule's group and Member test,
and registration). `active` stays the flag [Ministry leader](#ministry-leaders)
sign-in reads (Administrator decision, 2026-10-15): the Families rule decides
only which Families get the Family form and email. `portal_eligible` becomes
"matches the campaign's Families rule". `email_eligible` is
`portal_eligible` and at least one head (Head, Husband or Wife) whose Member
Status is one of the rule's statuses (Active for both presets; the chosen
statuses for Advanced), who is not Deceased, and who has a valid email
address (Administrator decision, 2026-10-24). So a campaign Family is never
emailed through a head whose status the rule does not accept; such a Family
is reached by postal mail, as a Family with no email is today. The legacy
rule keeps today's email test exactly (any head not Inactive or Deceased,
with a valid email address), so nothing changes for the
[existing campaign](#the-existing-campaigns-rule). The snapshot's
`active_head_duids` lists the heads the email test accepts: under the
legacy rule exactly today's list, and under a preset or Advanced rule only
the heads whose status the rule accepts, so every reader that reaches a
Family through its heads (the SQL that chooses the email addresses, mail
names, the directory and reports) follows the rule with no change of its
own. Under a preset or Advanced rule each Family row also carries the rule's
digest (`campaign_families`), which the Families check compares with the
rule its load recorded; a legacy row carries none, so it is byte for byte
the row it was before rules existed.

Under an Advanced rule a Family can be a campaign Family while the
snapshot's `active` is false (for example one in a chosen "Inactive"
group). Its `FamilyCampaign` row then records `active` true: on the
campaign's own row `active` means active in this campaign, ParishSoft's flag
or a campaign Family, because delivery reads that row's `active` with its
eligibility. Under the legacy rule every campaign Family is active, so the
row's flag is ParishSoft's, as before; the status reason reads as a campaign
Family, not as inactive.

**Storage.** The rule is a campaign configuration value,
`values.campaign_families`, materialized and versioned like the campaign's
other configurable fields ([Campaign](#campaign)):

- `{"rule": "active_parishioners"}`
- `{"rule": "active_parishioners_and_staff"}`
- `{"rule": "advanced", "registered": true, "family_groups": [{"id": 0,
  "name": "(No Family Group)"}, {"id": 12, "name": "Active"}],
  "member_statuses": ["Active", "ActOther"]}`
  (`registered` is a boolean; both lists are non-empty, without duplicates, in
  a canonical order; group IDs are non-negative, 0 meaning no group;
  statuses are a subset of the three).

A configuration with no `campaign_families` value means the legacy rule;
that value is never written. Applied configuration versions are immutable and
digested, so the existing campaign's versions are not rewritten to add one.

One frozen forward migration (number assigned centrally) per the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy):

Frozen migration 0175 replaces the configuration pointer guard
(`stewardship_campaign_pointer_v1`) with 0166's body plus two checks: a
configuration for a campaign that already exists must carry exactly the
value that campaign's current configuration carries, absent staying absent,
in every campaign state, so the rule is fixed from creation for the
existing campaign and every later one; and a configuration that creates a
campaign may carry a `campaign_families` value only in one of the canonical
shapes above. `campaign_families` is not added to the live structural
exemptions. The function stays an invoker function and the file ends with a
self-verifying `DO` block. The Admin portal and the CLI always write a rule
when they create a campaign; the guard does not require one, because a
campaign created without one simply keeps the legacy rule, and the
disposable test campaigns that prove the legacy behaviour are created that
way. The `family_eligible_has_code_cohort` check is unchanged: a campaign
Family's row is always `active` (above), so the check still holds.

The same file replaces four functions in place, none of them `SECURITY
DEFINER`, keeping their grants, owners and settings, and its `DO` block
checks each. The campaign's exact source scope
(`stewardship_source_current_window_v1`) names the campaign's
`campaign_families` value when it has one, exactly as `source/windows.py`
writes a refresh request's window, because the request guard requires the
two to be equal; without one its text, and so the legacy campaign's window
digest, is unchanged. The Financial stewardship selection (which pledge
review also reads) and both Additional information selections (the page's,
and the one older export captures use) give a Family
row's `active`, which draws its
[No longer in this campaign](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
badge and the Financial Active filter, from the row's `portal_eligible`
when the row was loaded under a rule (it carries `campaign_families`), and
from ParishSoft's `active` otherwise, so the legacy campaign's rows read
exactly as before.

Setup completion's check for a setup that also created its campaign (an
attempt confirmed before
[#142](https://github.com/epiphany40223/parishkit/issues/142)) still
compares each `FamilyCampaign` row's `active` with the snapshot's. Only a
campaign with a rule can differ (an Advanced Family in a chosen "Inactive"
group, above), and such a campaign is only ever created on Create the
campaign, after setup, so the check is unchanged.

It rewrites no rows: no `FamilyCampaign`, code, link token, lookup digest,
schedule occurrence or outbox message changes.

**How refreshes apply it.** The rule travels in the refresh's campaign
window (`source/windows.py`, and SQL's exact scope, above), whose document
names it only when the campaign has one, so a legacy campaign's window
digest is unchanged. A refresh reads the current campaign's rule when
it is requested, as it reads the giving window, and records it in its
snapshot cursor's load evidence, `cursor.load.campaign_families = {rule,
digest, missing_groups}`, beside the Family group lookup it read
(`cursor.load.family_groups`, every `{id, name}`), which the Advanced choice
and the [count preview](../admin-portal/spec.md#families-in-this-campaign)
read. With no current campaign the snapshot uses the legacy rule. The
Families check (`source/families.py`) evaluates the recorded rule against the
row's own facts, as it cross-checks today's flags. The [drop
check](../background-processing/spec.md#full-cycle) compares the
Portal-eligible, email-eligible and active-head counts only with snapshots
recorded under the same rule digest (legacy snapshots record none); the first load after creation, whose rule the
Administrator approved with its count, starts their trend again (the other
counts keep theirs).

**Families whose data changes mid-campaign.** A Family that stops matching
the rule (it leaves the "Active" group, its last Active Member becomes
Inactive, it registers elsewhere) leaves the campaign at the next promotion
and one that starts matching joins it, exactly as [refresh
reconciliation](#parishsoft-refresh-reconciliation) describes: access is
revoked without deleting history, the existing code and link are restored if
it matches again, its unsent follow-ups are cancelled as `family_ineligible`,
a returning responder gets no new invitation, and a joining Family gets its
code and link and is planned by the Family schedule sweep.

#### The existing campaign's rule

The Production campaign was created before campaigns had a rule, and its
Families were emailed codes and links that must never be invalidated
(Administrator's hard rule). It keeps exactly the Families it has. Its
configuration has no `campaign_families` value, which means the legacy rule:
today's code path, evaluated by today's functions, so every refresh selects
the same Families it would have selected before this change, and emails
them through exactly the heads today's email test accepts, and the
migration's guard keeps it that way for the rest of the campaign (no
configuration version may add a rule to it). Campaign settings shows the
rule as "Chosen before this choice existed: Families registered to this
parish, not in the Inactive Family group, with at least one Member who is not
Inactive or Deceased." The new framing applies to its wording too: its pages
say "campaign Families", not "active parishioners". Its successor (#145)
chooses a preset or Advanced rule at creation.

### Schedule revisions and fulfillment

`ActivationCatchUpDemand` is campaign-owned and unique by its activating
`CampaignTransition`, which the Production confirmation creates only when it
moves the campaign straight to `active`. It records activation/due cutoff, the
applied configuration and pinned source snapshot, linked TaskRun/retry chain,
current phase, cursor, counts, completion time, and a coded failure; its
append-only `CatchUpCheckpoint` rows hold the per-group checkpoints and
`CatchUpFailure` rows the failed attempts. Completion is independent of the current task's
terminal state. Its existence without completion supplies the durable
scheduled-mail preparation hold; no rows need to be allocated per Family in
the activation transaction. The
[background workflow](../background-processing/spec.md#activation-catch-up)
owns batching, claim/dispatch guards, completion, and recovery semantics.

`ScheduleDefinition` gives each initial, reminder, or report email schedule
(kind `report`; see
[report email definition](../report-emails/spec.md#report-email-definition)) a stable
logical UUID and campaign/type, and points at its current revision or records
its removal time; exactly one revision is current unless the definition was
removed. Each applied configuration version materializes immutable
`ScheduleRevision` rows holding the schedule's values (local date/time and
template references) and, for Family mail, the resolved due instant. An
immutable
`ScheduleSelection` row records each replacement or removal: the
configuration version, the previous and newly selected revision (none for a
removal), and a per-definition sequence number.

`ScheduleOccurrence` stores campaign, schedule definition/revision, immutable
mode/routing class, semantic target and slot, resolved UTC due instant,
occurrence key, outcome, attempts/lease/heartbeat, TaskRun and OutboxMessage
references, structured terminal reason, pause-hold reference/version, and any
selected replacement occurrence. Outcome is `pending`, `running`,
`delivery_unknown`, `succeeded`, `skipped`, `coalesced`, or `failed`, with the
terminal and reconciliation meanings defined by
[background processing](../background-processing/spec.md#durable-scheduling-and-task-execution).
The occurrence key is unique. That database constraint, rather than scheduler
timing alone, makes insertion/recovery idempotent and prevents two revisions or
workers from creating the same revision-specific work.

Production occurrence identity also includes a campaign execution cycle. A
successful pre-start withdrawal advances that cycle atomically; reactivation
can allocate fresh work even if the schedule revision is unchanged. Retired
occurrences and delivery history remain immutable and cannot be retried into
the new cycle. Testing uses cycle zero. This execution identity is separate
from deliverability-recovery generations and from semantic fulfillment below:
withdrawal never makes an already covered semantic slot deliverable again.

`ScheduleFulfillment` records that a semantic slot is covered independently of
revision. Its disposition is `delivered`, `coalesced`, or `empty`. An `empty`
report email row requires an audited successful occurrence whose entire
original recipient cohort was safely cancelled after failing its recheck, or
that sent nothing because the report had nothing to report
([sending only when there is content](../report-emails/spec.md#sending-only-when-there-is-content));
it does not represent provider acceptance. Its unique key combines
schedule UUID, mode, semantic recipient or audience, and occurrence slot (for
example Family DUID for a one-time Family mail or a campaign-local date, its
[slot key](../report-emails/spec.md#slot-keys), for a report email). A delivered row references the successful occurrence; a
coalesced row references the selected replacement occurrence that covers it
without masquerading as provider success. Thus changing a time or template
never makes a recipient whose slot is already covered in that mode eligible for
the same logical schedule again. Schedule replacement and removal follow the
atomic cancellation policy in the
[background-processing specification](../background-processing/spec.md#schedule-replacement-and-removal).

For activation preparation interrupted by a schedule revision, an append-only
`ScheduleRecoveryReplacement` links a safely cancelled selection to its current
replacement occurrence under the same demand. It is coverage lineage, not a
new delivery identity or a success record. Each predecessor has at most one
successor; links must move to the current revision and cannot cycle. Original
occurrences and fulfillment rows remain unchanged. After activation preparation
has completed, an ordinary report email preparation may own these same forward
edges under its exact task claim instead of reopening the completed demand.
Each edge has exactly one preparation owner; Testing edges are removed only by
the journaled Testing cleanup with their occurrence inventory.
Family preparation must also
forward coalesced reminder coverage when a replacement initial invitation is
selected. Removing all Family schedules preserves history without reviving mail;
configuration continues to forbid reminders without an initial invitation.
Report email generation and post-close obligation resolution must follow this lineage
to include every original covered date, including dates covered before the
configuration edit. Removal without a replacement preserves unresolved history
for the ordinary explicit post-close resolution workflow; it does not claim
delivery.

`PostCloseMailResolution` stores Campaign, immutable mode, semantic obligation
key (receipt submission identity or report email schedule UUID/slot), exact
covered submission versions or report-day range, resolving Admin, UTC time,
required reason, and linked skipped occurrence/cancelled work. Its unique key
combines campaign, mode, obligation key, and coverage digest, making repeated
confirmation idempotent without covering later inputs. It is a durable explicit
skip, separate from delivered/coalesced/empty `ScheduleFulfillment`; it survives
schedule revision and archive/unarchive until campaign purge. See
[post-close reporting obligations](../background-processing/spec.md#post-close-reporting-obligations)
for transactional application and admission checks.

`RestoreDeliveryHold` records restore identifier, campaign/schedule semantic
key, target/slot, backup-snapshot and release-window instants, discovery source,
state, resolution actor/time, evidence note, and linked recovery occurrence.
State is `unreviewed`, `assumed_delivered`, `resend_authorized`, or
`not_applicable`. A unique semantic key makes inventory recomputation
idempotent. Unreviewed and assumed-delivered rows suppress only their referenced
occurrence; they are not `ScheduleFulfillment` and never count as provider
success. Resolution transitions and resend creation follow the
[restore workflow](../operations/spec.md#restore).

A **remembered reminder series**
([#469](https://github.com/epiphany40223/parishkit/issues/469), migration 0075)
records the repeat rule that some of a campaign's reminders came from. It
lives in the configuration's optional `schedule_series` section, under the
document schema `campaign-series-v11`: every rule of `users-v10` plus that
section. A users-form configuration that has the section uses this schema,
even when the section is empty, so the schema never flips back. Each record is
`{id, values: {campaign_id, rule, schedules}}`. `rule` is the repeat rule in
its canonical form (`campaigns/recurrence.py`). `schedules` lists the sorted,
distinct IDs of one or more reminders of that campaign, each dated on a day
the rule gives. A reminder belongs to at most one series, and a series never
moves to another campaign. Each member keeps its own send time and email. A
series is an authoring record only: each member stays an ordinary reminder
with its own definition and revisions, and nothing that plans, coalesces or
sends mail reads it. Because the rule is kept outside the reminders' values,
editing a series never replaces an unchanged reminder's revision or prepared
work. The section has no SQL projection. SQL admits the schema name and treats
it as the users form: the projection and completeness checks handle it as
they handle `users-v10`. A configuration with series follows only a users-form
configuration and never returns to sign-in rules or to `users-v10`: SQL's
users-form activation guard holds the schema as sticky as its selection is.
The users request formats
([operations](../operations/spec.md#users-upgrade-from-sign-in-rules)) build
its candidates. An ordinary schedule edit keeps each series valid in the same
signed intent (`series_schema.upkeep`):

- a deleted reminder leaves its series;
- a reminder moved to a date its rule does not give leaves its series;
- a series left with no reminder is removed; and
- a changed time or email keeps the reminder in its series.

A series is created when a schedule change adds a repeat and asks to
remember it: the same signed intent adds the series record, holding exactly
the reminders that repeat adds (`schedule_changes.build_preview`'s
`series`). So far only the command line asks
([`schedule preview`](../admin-automation/spec.md#schedules-and-configuration)).
The same command's `series` entries edit a series "this and following" or
as a whole, or delete it, in one signed intent that adds and removes
ordinary reminders and updates, adds or removes series records
(`accounts.series_edits.plan`); a reminder that has sent is never changed.

### Source snapshot

`SourceSnapshot` records form an ordered history. Each stores type (`full` or
`delta`), state (`staging`, `ready`, `rejected`, `promoted` or `unchanged`),
the refresh task and source fence that wrote it, the snapshot it was based
on, start/completion/promotion times, the source generation it received when
promoted, ParishSoft organization ID, collection counts, validation result,
source watermark/change cursor, and a content digest. These lightweight
manifests remain indefinitely and record
whether their complete corpus is still reconstructable or has been compacted.
A snapshot's cursor may also carry display-only comparisons with its base: the
changed-record counts and the Ministry catalog differences described in
[ParishSoft Ministry catalog changes](../admin-portal/spec.md#parishsoft-ministry-catalog-changes).

Normalized versioned tables store canonical Family, Member, Ministry, roster,
fund, pledge, and contribution payloads by content digest. Content-addressed
deduplication is mandatory: a snapshot-to-version membership map reuses the
same immutable version whenever identity and canonical payload are unchanged.
No refresh may duplicate unchanged entity payload rows. The `delta` type
and the `unchanged` state remain for historic rows: quick updates (delta
refreshes) were removed in
[#1147](https://github.com/epiphany40223/parishkit/issues/1147) (see
[quick updates removed](../background-processing/spec.md#quick-updates-removed)),
and no new snapshot is a delta or ends `unchanged`.
Querying an uncompacted snapshot must reproduce one coherent corpus. Short-
lived HTTP cache files are operational artifacts, not durable snapshots, and
may be expired normally.

Exactly one promoted snapshot is current. Promotion changes that pointer and
all derived current indexes in one database transaction. A failed or rejected
load never exposes a partial corpus.

The database recomputes a snapshot's evidence when it becomes `ready` or
`unchanged`, and refuses it unless every relationship and contact or address
owner resolves inside that snapshot. That check reads only the snapshot's own
rows, so its cost follows the size of one corpus, not how many snapshots are
retained, and a collection with no rows has nothing to check
([#629](https://github.com/epiphany40223/parishkit/issues/629)).

Until #1147, a quick update whose corpus equalled the current snapshot's was
not staged or promoted
([#630](https://github.com/epiphany40223/parishkit/issues/630)); the rest of
this section records how those historic `unchanged` rows were made and are
read. Promoting it would add no ParishSoft data, yet it would write a full copy
of
the membership rows, rewrite every Family row with a new generation, and leave
the old copy for compaction. Instead its snapshot ends in the terminal state
`unchanged`. It has no membership rows, carries the current snapshot's counts
and content digest, and its cursor counts no change. It never becomes
current. This applies only when promotion would also change nothing outside
the corpus:

- every Family row already holds the current generation and exactly the
  status that today's mail suppressions give it, so a new bounce still
  promotes;
- no eligible Family lacks its code or, under an active link generation,
  its link, and the Family population evidence is clean;
- reconciling open proposals and Ministry requests against the current
  corpus would change none of them, with the same decision promotion makes,
  so a new submission, a staff review edit or a row the last promotion
  itself reconciled does not force another promotion;
- no added Member link (see [Proposed changes](#proposed-changes)) is
  waiting to be recorded;
- an active Family link generation is still current (a stale one fails the
  refresh, as promotion would).

Otherwise, and whenever the corpus differs in any way (compared by canonical
payload digest, including a roster's "current" flag on a new day), the quick
update stages and promotes as before.
An `unchanged` snapshot completes its refresh run, counts as a successful
ParishSoft answer for the
[connection line](../operations/spec.md#parishsoft-data-age-and-connection)
and for recovering refresh-failure alarms, and never moves "data as of". The
next quick update read the change list from its watermark, so the window
did not grow while nothing changed. Its manifest is kept like every other;
with no membership rows, there is nothing for compaction to remove.

Source compaction uses UTC cutoff instants and never compacts the current
snapshot, a snapshot still named by live campaign, credential, activation,
recipient, fact-demand or setup state, or a snapshot protected by a submission baseline/effective version,
pinned report or digest, unexpired Family form baseline, reconciliation/publication record, audit reference,
campaign boundary anchor, restore/delivery hold, or explicit operator hold.
Those snapshots and their membership/payload rows remain fully reconstructable
for the lifetime of the protecting record. Among otherwise unprotected
promoted snapshots, the system retains:

- every reconstructable snapshot for six hours after promotion, except one
  whose content is identical to the current snapshot's, which the current
  snapshot already reproduces and which may be compacted at once;
- after six hours through one year, the content the latest promoted snapshot
  in each UTC calendar day ended in; and
- after one year, the content the latest promoted snapshot in each UTC
  calendar month ended in, indefinitely.

These day and month anchors are chosen over every promoted snapshot. When an
anchor was itself compacted because its content repeated, the latest
uncompacted snapshot with the same content digest is kept in its place. That
is at worst the last snapshot of the unchanged run, so a day or weekend that
ends in content which then stays unchanged still keeps that final content.

Compaction keeps every manifest, including its immutable source generation and
promotion UTC instant used as a historical fact cutoff watermark, but may remove
membership rows for redundant unprotected snapshots and mark those manifests
compacted. Historical cohort reconstruction uses durable first-eligibility
provenance rather than those removed membership rows, so UTC day/month anchor
selection cannot change campaign-local denominators. Compaction removes a
payload version only when no retained reconstructable snapshot or other
protected record references it. Anchor selection is deterministic, and a late
protection reference wins over cleanup under row locks. Cleanup is idempotent,
bounded, audited by counts/cutoffs rather than values, and cannot run while a
source promotion owns the mutation lease.

#### ParishSoft organization scope

A deployment loads exactly one ParishSoft organization, the configured
organization ID, and never stores another organization's data
([#1554](https://github.com/epiphany40223/parishkit/issues/1554); the
Administrator decided on 2026-10-23 that a key may return data for several
organizations and that ParishKit drops all but the configured one). The rules:

- **The organization check.** Before any other read, an uncached
  `organizations/search` must answer with a bounded list (at most 10,000) of
  well-formed organization records, a positive integer `organizationID`
  each and none repeated, exactly one of which is the configured
  organization. A key that also reaches other organizations is accepted; a
  key that does not reach the configured one is the permanent tenant
  mismatch. A configured expected organization name is compared with the
  configured organization's record only.
- **Requests stay scoped.** Every request that takes an organization still
  carries only the configured one, and a caller cannot replace or add to it.
- **Records are filtered by their own organization field**, but only when
  the key reaches more than one organization. A key that reaches only the
  configured organization is answered for that organization alone, so its
  collections are read exactly as before, with no new requirement on their
  fields. With a key that reaches several, each record of these collections
  is kept only when its field names the configured organization; the others
  are dropped and counted per collection, and a record whose field is
  missing, null or not a positive integer fails the load as incomplete
  data, since it cannot be shown to belong to the parish:

  | Collection | Field |
  | --- | --- |
  | Members (`members/search`) | `organizationID` |
  | Ministry types (`ministry/type/list`) | `organizationId` |
  | Member WorkGroups (`members/workgroup/lookup/list`) | `ownerOrganizationId` |
  | Family WorkGroups (`families/workgroup/list`) | `ownerOrganizationID` |
  | Pledges (`offering/pledge/list`) | `organizationID` |
  | Contributions (`offering/contributiondetail/list`) | `organizationId` |

  Members are kept on `organizationID`, never `ownerOrganizationID` or
  `registeredOrganizationID`: on a real key `organizationID` matched the
  requested organization on every Member, while the other two name other
  parishes for some of the parish's own Members (the issue's real-key probe).
  That `organizationID` names the organization a Member was returned for under
  a key that reaches several organizations is an assumption a
  single-organization key cannot confirm; a human-run check with such a key
  should confirm it if one ever becomes available. A Family WorkGroup is kept
  only when its owner is the configured organization, so a shared diocesan
  WorkGroup another organization owns is not read.
- **Member contact rows** are not filtered on their own owner field. They
  are kept only for the Members the search kept, so with such a key Members
  must be read first, and a load that reads contact rows before them fails.
  A kept contact row must name the configured organization as
  `ownerOrganizationID`, else the load fails.
- **No other organization's record is cached.** The shared tools' response
  cache is written after the filter, never before it.
- **Collections with no organization field rely on the request scope**, with
  cross-checks that fail the load on a dangling reference: Families
  (`families/search`; their `registeredOrganizationID` is where a Family is
  registered, not whose record it is, and must not be used to filter), Family
  groups (used only through IDs loaded Families reference), Ministry rosters
  (read only for kept Ministry types; every roster Member must be loaded),
  WorkGroup memberships (read only for kept WorkGroups; with a key that
  reaches several organizations every No-reminders WorkGroup Family must be
  in the full load's Families), and giving (every giving Family must be
  loaded, as before). Every kept Member's Family must be loaded. A loaded
  Family whose Members were all another organization's is kept, as the
  request returned it, and only counted in the log.
- **People in more than one parish** appear only through the configured
  organization's own records; another organization's copy is never merged
  or looked up. Family and Member identities do not depend on the
  organization, so nothing already loaded, and no emailed Family code or
  link, changes.
- **Evidence and logs, counts only.** A load with such a key records, in its
  snapshot's load evidence under `other_organizations`, how many
  organizations the key reaches, the records dropped per collection and the
  number of Families left without Members, and logs exactly that, once, with
  its task, after its last read (a refresh's WorkGroup list included), so the
  log and the snapshot agree. No other organization's ID or name, and no
  parish data, is logged or stored. A key that reaches one organization
  records nothing new. No schema change: the evidence is part of the
  existing cursor. The one exception is the setup wizard's
  [organization choice](../admin-portal/spec.md#parishsoft-organization-choice),
  which shows the setup Administrator every organization the key reaches,
  by name and ID, and scrubs that list as soon as one is chosen.

#### ParishSoft organization name

The configured organization's name is shown wherever the portal names the
organization, as "Name (ID)"
([ParishSoft organization names](../ui-conventions/spec.md#parishsoft-organization-names),
[#1554](https://github.com/epiphany40223/parishkit/issues/1554)). The ID
stays the identity: every request, check, snapshot and versioned record keys
on the integer organization ID, and the name is display text only, never
compared, matched or used to choose an organization.

- **Source.** The name is the `organizationReportName` of the configured
  organization's record in the uncached `organizations/search` answer that
  the [organization check](#parishsoft-organization-scope) already reads, so
  it costs no extra request.
- **Cleaning.** The name is trimmed, runs of white space become one space,
  control characters are removed, and it is cut to at most 200 characters.
  A name that is missing, not text or empty after cleaning is no name: it
  does not fail the load (only a configured expected organization name,
  when one is set, is compared, as before), and the portal then names the
  organization by its ID alone.
- **Stored with each full load.** A full load records the cleaned name in
  its snapshot's load evidence as `organization` (`{"name": "…"}`, or
  `{"name": null}` for no name). A snapshot that is not a full load carries
  its base's value forward. Pages read the current snapshot's value, so a
  name changed in ParishSoft shows after the next full load, and nothing
  else stores it. No schema change: the evidence is part of the existing
  cursor.
- **Before the first load.** The setup wizard shows the name its
  [organization lookup](../admin-portal/spec.md#parishsoft-organization-choice)
  read, held with the lookup, until the setup load records its own.
- **Existing installs.** A snapshot recorded before this change has no
  `organization` value, so the portal names the organization by its ID
  alone until the next full load records the name. Nothing is backfilled
  and no re-setup is asked for.
- **A key that stops reaching the organization** fails the load as the
  tenant mismatch the organization check already reports; the name shown
  stays the last one recorded.

### Campaign daily report facts

`CampaignFactRebuildDemand` has a unique `(campaign, population scope)` key and
stores requested source/submission watermarks, pending first/last-event and due
UTC instants, pending revision, and the claimed generation/TaskRun reference.
Event transactions atomically advance this row; claims freeze a coherent input
tuple and consume only the claimed pending revision under its row lock. Events
after a claim create a new pending revision independently of the running one.
Completion/recovery cannot clear that newer revision. The debounce, priority,
and exact-input request behavior is authoritative in
[participation fact materialization](../reports/spec.md#participation-fact-materialization).

`CampaignDailyFactSet` records one immutable, complete graph-calculation
generation for a Campaign, population scope, promoted-source generation cutoff,
submission-version cutoff, and campaign-timezone version. Its state is
`building`, `ready`, or `failed`; at most one generation for an exact input key
may become ready. Child `CampaignDailyFact` rows hold one campaign-local date's
first-response count, cumulative response count, cohort denominator, percentage
inputs, effective pledge total/availability, and source-as-of metadata. A fact-
set pointer changes only after all expected dates validate and commit, so
readers never combine generations. For historical scope, the source cutoff
selects durable `FamilyCampaign` first-eligibility provenance rather than
requiring every earlier SourceSnapshot membership set. For current scope, it
selects the exact current population from that promoted snapshot. Facts are
derived, rebuildable data; pinned digests/reports protect the precise ready
generation and any reconstructable source input it uses from compaction until
their parent retention ends.

`SourceMutationLease` is the singleton durable exclusion record defined by
[background processing](../background-processing/spec.md#parishsoft-refresh).
It stores owner TaskRun, monotonically increasing fencing token, phase,
acquired/heartbeat/expiry times, and the external-request deadline used for
safe takeover. Task claims and snapshot promotion record and transactionally
verify the expected fencing token.

#### Derived fact retention

Superseded, unpinned ready fact generations are disposable derived data, not
indefinitely retained campaign answers. A dedicated fact-compaction job removes
their `CampaignDailyFact` rows and unreferenced `CampaignDailyFactSet` records
in bounded, idempotent batches. A ready generation becomes eligible when a
newer complete generation replaces it for the same campaign/population scope
and all protections below have ended; no additional age-based retention is
required. Preserve existing task/audit history and its generation-key metadata,
not duplicate calculated daily rows solely for operational history.

Protect the interactive pointer's generation, including a stale generation
displayed while its replacement builds; every generation/input set pinned by
an export that has not ended (one preparing, downloadable, or failed and
still retryable), a digest occurrence, or other retained pinned report;
building or recoverable failed generations and generations needed by
queued/retrying work; and generations actively read, rendered, or verified.
An export's pins are released when it ends, with its saved capture (see
[point-in-time exports](../operations/spec.md#point-in-time-exports)).
Compaction never changes parent retention or discards a pin to reclaim space.

Selecting a generation and acquiring its read/use protection must be atomic
with respect to compaction, as must adding a durable pin, publishing a pointer,
or claiming build/recovery work. Hold transient protection through every lazy
query and fact-dependent serialization/rendering operation. The compactor
rechecks all protections under the same generation/reference guards before
deleting; if a reference or reader wins, skip that generation. If deletion
wins, a subsequent selector retries current selection or schedules an exact
rebuild only when its required inputs remain available. It never substitutes
another cutoff for a pinned request or exposes a partial generation. A mere
expired reader heartbeat is not proof that its protection has ended.

This job obeys campaign purge/restore work gates and deletes no submissions,
source snapshots, provenance, audit records, or protected inputs. Only the
separate source-compaction policy may reclaim source inputs after their final
protection ends. Historical inputs that are no longer protected may cease to
be reconstructable under that existing policy; arbitrary unpinned intermediate
fact generations are not promised permanent replay. Scheduling and operational
monitoring are owned by [housekeeping](../operations/spec.md#temporary-retention-and-housekeeping).

### Family campaign identity

`FamilyCampaign` joins a ParishSoft Family DUID to a Campaign and stores:

- portal eligibility, syntactic email eligibility, current email deliverability
  and its reason, and active status;
- immutable first Portal-eligible UTC timestamp and promoted-source generation,
  plus last eligibility-change timestamp and current status reason;
- encrypted eight-letter display code plus the versioned canonical HMAC lookup
  rows defined by the
  [credential specification](../architecture/spec.md#family-credential-security);
- association with generation-scoped email-link token records containing
  versioned ciphertext, domain-separated SHA-256 lookup digest, destruction
  and rotation times;
- first live submission and current effective submission IDs; and
- the instant of the Family's latest session activity (`last_activity_at`).
  The Admin "Families on the form now" indicator reads the Family session
  rows, not this column.

Invitation and reminder state is kept in the schedule occurrences and outbox
messages, not on this row (#472 removed an unused per-Family invitation state
column).

Each campaign also has one credential row (`CampaignCredentialState`). It
holds the current rehearsal pointer, the go-live gate, and the Family
population the codes and links were last built for: its source snapshot and
generation and an eligibility digest and count. Inserting a Family row, or
changing one's Portal eligibility or source generation, marks that population
dirty (a statement trigger) until source reconciliation records the new
population. While the population is dirty, Family sign-in, Family mail and
link preparation are refused; while the go-live gate is held, Family sign-in
and Family mail are.

The first-eligibility timestamp/generation pair is set atomically when source
promotion first makes the Family Portal-eligible in the Campaign and is never
rewritten by later inactivation, reactivation, source compaction, or refresh.
Historical fact reconstruction includes the Family only when this generation
is at or before the fact set's source cutoff and the timestamp is at or before
the resolved campaign-local day boundary. `FamilyCampaign` is retained with its
Campaign until purge, so this cohort provenance does not protect every
intermediate SourceSnapshot from compaction.

Codes are generated for every newly Portal-eligible ([campaign](#campaign-families)) Family during initial
campaign population or snapshot promotion, even if the Family lacks eligible
email. A code is never changed during that campaign. Token rotation does not
change it. A non-null access-token lookup digest is unique within its campaign
and backed by a database unique index; close-time destruction sets both token
ciphertext and digest null without weakening retained audit metadata.

`FamilyAccessTokenGeneration` stores Production-token Campaign, initiating operation/Admin,
TaskRun, deployment Family-link credential epoch, optional restore-instance
reference, preparation revision, pinned source snapshot and eligibility coverage
digest/count, proposed-end/configuration version, encryption-key ID,
checkpoint/progress, completion time, and state (`building`, `ready`, `active`,
`failed`, `cancelled`, or `superseded`). The Campaign has one nullable active
generation pointer. Every token belongs to one generation and FamilyCampaign;
uniqueness permits at most one token per generation/Family, and non-null token
digests remain unique across the entire Campaign. Initial population also uses
this representation, so authentication has one generation-selection rule.

Every token-generation admission and activation also checks the deployment's
current Family-link credential epoch. A restore keeps the restored epoch,
generations and tokens: it never creates, replaces or cancels a Family code
or link (the Administrator's hard rule; see
[operations](../operations/spec.md#restore)). Population and later
reactivation generate missing tokens only in a current-epoch generation. Draft readiness likewise
requires a current-epoch Production generation before going live. All
credential-bearing OutboxMessages capture the generation/epoch used for their
sealed substitutions so dispatch can reject stale material independently of
their delivery state.

A prepared generation grants no access until the Campaign points to it and all
ordinary campaign/mode/Family admission checks pass. Lookup and mail dispatch
select only that generation, regardless of whether staged digests are present
in the database. Readiness freezes a complete coverage manifest; final reopen
locks/checks its preparation revision, source/configuration/key versions, and
Campaign state before changing the pointer. Any changed input invalidates
readiness; no per-Family token generation or bulk row rewrite occurs in the
confirmation transaction. Failed or cancelled preparation is never selected;
its ciphertext/digests are scrubbed asynchronously and cannot be resurrected
by a stale task. Retry uses the same preparation revision/checkpoints only
while its pinned inputs remain valid; replacement supersedes the old revision.

Rehearsal credentials live separately from Production FamilyCampaign credentials.
`RehearsalEpoch` stores campaign, random immutable epoch ID, creation/invalidation
times, and state; the Campaign has at most one current rehearsal pointer.
`RehearsalCredential` stores epoch/FamilyCampaign, encrypted Testing code and
versioned HMAC lookup rows, and sealed Testing-token ciphertext plus a
domain-separated digest. Uniqueness permits one credential set per epoch/Family
and one matching code/token per epoch. Issuance uses the campaign generation
lock and checks mode/routing authorization, epoch, eligibility, and all
admission gates, including the narrowly authorized readiness-test exception
defined by the credential policy. There is no
fallback to Production credentials if rehearsal preparation fails.

The mode-disjoint formats and epoch admission rules are defined by
[Family credential security](../architecture/spec.md#family-credential-security).
Testing codes are never recycled within a campaign. A separate
`RehearsalCodeReservation` table stores campaign UUID, MAC-key ID,
algorithm/format version, and domain-separated canonical-code HMAC digest.
It has no Family, Member, credential, or rehearsal-epoch link and no plaintext
or recoverable code ciphertext. A unique constraint covers
`(campaign, key ID, algorithm/format version, digest)`; reservation lookup never
uses FamilyCodeFingerprint's per-Family uniqueness rules.

Issuance reserves the candidate under the active key in the same transaction
that inserts its RehearsalCredential. Under the campaign generation lock it
checks the candidate against reservations under every key required by that
campaign's retained reservations, keeping that key set stable against rotation.
Collision rejects the candidate and regenerates it; retry of an already-issued
epoch/Family credential reuses that credential rather than issuing another.
Reserving at issuance, rather than cleanup, makes exclusion span every epoch
without a gap. Reservations survive credential cleanup and are removed only
by campaign purge. Once code ciphertext is deleted, its reservation is not
backfilled under another key; retain the original key for collision checks
under the [MAC-key policy](../architecture/spec.md#configuration-and-secrets).
Rehearsal Family sessions store their epoch and mode. Gate acquisition clears
the current pointer and invalidates the epoch transactionally; cleanup deletes
its credential ciphertexts, lookup rows, and sessions in bounded batches while
retaining only non-sensitive invalidation evidence and code reservations.
Final activation checks that no rehearsal credential detail remains. A fresh
epoch never reuses a prior ID or revives a deleted credential record.

`FamilyCodeFingerprint` stores FamilyCampaign, campaign, MAC key ID/algorithm,
and the canonical digest. Constraints allow at most one row per Family/key and
one digest per campaign/key. Generation and migration use the cross-key
collision protocol defined by the credential specification. Bulk population
uses the surrounding `READ COMMITTED` transaction, campaign generation lock,
set-based collision query, and bounded batch-level retry, so all Family
identities promote atomically without creating one subtransaction per Family.
No view performs decryption scans.

### Family engagement

`FamilyEngagement` keeps, per FamilyCampaign and mode (`live` or `test`, as a
[Submission](#submission) spells it), the response-funnel instants that no
retained record otherwise preserves. Family session rows are deleted 60
minutes after their last activity, or four hours after sign-in, under the
[session policy](../architecture/spec.md#identity-and-session-security), and
their presence column is the current form step, not the furthest one reached.
The row stores:

- the first sign-in instant (`first_link_at`, "link followed"; this includes a
  mail scanner following a personal link, but not a Staff sign-in for the
  Family through [Open form](../reports/spec.md#open-form), which is
  not the Family following its link);
- the first form issuance instant (`first_form_at`, "form opened");
- the first instant a form step past the first one was reported
  (`first_progress_at`);
- the furthest form step reached and when it was first reached
  (`furthest_section`, `furthest_at`), ranked by the presence section order of
  the [parishioner portal](../parishioner-portal/spec.md#form-state-and-navigation);
- the latest instant any of these paths saw the Family (`last_seen_at`); and
- for a `test` row, the rehearsal epoch it belongs to; a `live` row has none.
  One row exists per Family, mode and epoch.

Three existing paths maintain it inside their own transaction with one
monotonic upsert: Family sign-in (every one except an Open form sign-in by
Staff), form issuance, and the presence heartbeat,
which is already bounded to one per 30 seconds per session. A write may move a
`first_*` instant only earlier (never later and never back to unknown), the
furthest step only forward, and `last_seen_at` only later; every `first_*`
instant and `furthest_at` is at or before `last_seen_at`; an update that would
change nothing is skipped. SQL guards refuse anything else, refuse an instant
in the future, and admit writes only from the Family web login (the schema
owner, which migrations and the disposable test schema use, is exempt, as it
is for the cleanup command guard). Reporting data never costs a Family its
access: sign-in and form issuance write the record in a savepoint with its
foreign key checked immediately, so a refusal is rolled back alone, kept as a
durable `family_engagement_failed` operational event, and the request goes
ahead. The heartbeat, which carries nothing but presence, writes directly and
a refusal is its ordinary temporary denial. Rows hold no answers, names or
credentials; they are behavioural evidence about a Family, read only by the
Admin and Staff [response funnel](../reports/spec.md#response-funnel), the
Administrator's [Timeline](../reports/spec.md#timeline) and
the background worker that compiles a Production report email's funnel (its
first instants only).

Retention follows the mode. `test` rows are Testing detail: the
Production-transition cleanup inventories and deletes them with the other
[Testing records](#retention-and-deletion), and invalidated-epoch response
cleanup deletes the rest of a rehearsal's rows. `live` rows are retained with
their campaign; no other path deletes a row today, and the campaign purge,
when it lands, must inventory and delete them as campaign-owned detail, which
means widening the record's SQL retention function to admit the purge worker.
A one-time backfill, `pk-stewardship engagement-backfill`, fills
`first_link_at` from the retained `family_login` audit events since Production
activation (skipping Open form sign-ins, whose session is also the subject of
a `family_assisted_login` event) and `first_form_at` from live form baselines through the same
upsert, in bounded batches, writing one audit event with its counts; repeating
it writes nothing but that event. Progress before the record existed is not
recoverable and is not invented.

### Administration user and policy

`PortalUser` links a Google `sub` and current normalized verified email to the
login/audit history, including the validated Google hosted-domain claim when
present, which is recorded evidence only: authorization never reads it. It is
runtime identity state. A SQL guard (#389) admits an insert
only from the web login, with `verified_at` inside the inserting transaction
(as the sign-in stamps it), and only for an enabled, unattributed, version-1
row with a non-empty subject and email. SQL cannot see the Google sign-in
itself, and it does not check email or hosted-domain normalization.
Every Google-verified Admin sign-in attempt under the current recovery epoch
that passes the per-identity rate limit creates or refreshes its `PortalUser`
before policy decides, whether or not policy then admits it, so a named refusal
([sign-in activity](../admin-portal/spec.md#sign-in-activity)) can show the
address.

Authorization policy materialized from the active YAML version is one list of
**users** (Administrator decisions of 2026-10-10,
[#952](https://github.com/epiphany40223/parishkit/issues/952); the
[Users page](../admin-portal/spec.md#portal-user-management) edits it). The
`login_rules` section, under a new configuration schema version
(`users-v10`, which keeps every other rule of the version before it), holds
only entries of the form `{kind: "user", id, email, name, role}`:

- `id`: a stable opaque UUID assigned when the user is created, kept across
  configuration versions and never reused; the Edit page's address uses it;
- `email`: the normalized address (trimmed and lowercased, with no Gmail dot or
  plus folding), a valid address, unique within the configuration;
- `name`: plain text of 1 to 200 characters after trimming, with no control
  characters; it is a label, not a link to a ParishSoft Member; and
- `role`: exactly one of `ministry_leader`, `staff` and `administrator`. There
  is no empty role, no explicit deny, no domain and no grant provenance.

Activation materializes each user as one row of the `AddressRule` projection
(`stewardship_address_rule`), which gains `name` and `role` columns. Its
`roles` jsonb column holds the **cumulative** set: `administrator` is
`["administrator", "staff", "ministry_leader"]`, `staff` is
`["staff", "ministry_leader"]` and `ministry_leader` is
`["ministry_leader"]`. A check constraint ties `roles` to `role`, so the two
never disagree, and every SQL check that tests `roles` with `?` or `?|` keeps
its meaning without restating the ladder. Python resolves an address to its
one user's role, or to nothing, and treats the roles as levels. The session
admission, Ministry scope, export authorization and policy projection and
completeness functions read only this projection.

Users carry their applied-configuration version and cannot be edited
independently: every change is a `ConfigurationChangeRequest` and takes effect
on activation. Activation refuses a version with no Administrator user (the
last-Administrator guard), and immutable configuration and audit history keep
every earlier version. A Ministry leader's Ministries come only from
ParishSoft roles ([#922](https://github.com/epiphany40223/parishkit/issues/922));
a user of Ministry leader or higher is what lets the person sign in, and Staff
and Administrators see every Ministry.

Retired by the users model, after #922 retired Ministry assignments: the
`DomainRule` (`stewardship_domain_rule`) and `AddressRoleGrant`
(`stewardship_address_grant`) records, rule creation and grant origins
(`manual` and `chair-seed`), empty-role denial rules, the chair-seed
suppression predicate, Chairperson suggestions and their seeded assignments,
and `MinistryAssignment` with its overlay. After the
[users upgrade](../operations/spec.md#users-upgrade-from-sign-in-rules)
nothing writes or reads them; their tables stay, unread, until a later cleanup
migration drops them, and older configuration versions and the audit log keep
their history. Their earlier behavior is in this file's git history from
before #952.

### Ministry leaders

Signing in to the Admin portal needs a user of Ministry leader, Staff or
Administrator (Administrator decision, 2026-10-10, made with the Portal Users
revamp); a ParishSoft role alone admits no one. ParishSoft decides only which Ministries such a
person leads: a person leads a Ministry for the current campaign when an active
Member of an active Family holds one of the campaign's Ministry leader roles on
that Ministry's current roster in the promoted ParishSoft data, and the person
signs in with a valid email address on that Member's ParishSoft contact record
(#922). A person whose highest role is Ministry leader and who leads no
Ministry can sign in but sees no Ministry. Both flags are the promoted source's
own: a Member is active unless ParishSoft marks them Inactive or Deceased, and
a Family is active unless it is in ParishSoft's "Inactive" Family group or has
no active Member, the same flag the [legacy Families rule](#the-existing-campaigns-rule) uses; a campaign's Families rule does not change it (Administrator decision, 2026-10-15). The Family
need not be registered at the parish: a non-parishioner can lead a Ministry, an
inactive Member cannot (Administrator decision, 2026-10-10). The Ministry must
be catalog-present and selected in the current campaign, which asks about
Ministries; there is no local inactive state
([retired](#retired-ministry-activity)). The current campaign may still be a
draft. Role labels match with each run of whitespace (any Unicode whitespace,
the no-break space included) read as one space, the ends trimmed and ASCII
letters case-insensitive, otherwise exactly
(`stewardship_ministry_leader_role_key_v1`; Python's `leader_role_key` is the
same rule). Addresses match case-insensitively, with no Gmail dot or plus
folding, whatever the contact's publication flag says. An address that several
leader Members list gets the union of their Ministries. No current campaign, or
a campaign without Ministry stewardship, means no Ministry leaders. An
address's one user gives its role ([Portal user
management](../admin-portal/spec.md#portal-user-management)); since the users
swap (#952 slice 4) SQL reads only that user, never a hosted-domain rule.
Ministry assignments grant no Ministry.

The campaign's `ministry_leader_roles` value lists the role names: one to 50
distinct trimmed names, none repeated once compared as above. Campaign
settings offers the current roster's role labels with their whitespace
collapsed, leaving out any label that could not be saved, such as one over
200 characters. A campaign without the
value uses the default, `["Chairperson", "Staff"]`, which only SQL's
`stewardship_ministry_leader_roles_v1` writes down; Python reads it there. The
value stays editable while the campaign is live (the structural lock exempts
it in Python and SQL).

`stewardship_ministry_leaders_v1()` is the one definition of who leads what.
It is a SECURITY DEFINER function no login may execute directly.
`stewardship_ministry_leader_scope_v1(user)`, also a definer, returns one
portal user's Ministries, empty unless the user's role is Ministry leader or
higher (until #952 lands, the interim code reads the current login rule's
roles instead); database-grants lets the web, worker and download logins
execute it. `stewardship_address_leader_scope_v1(address, actor)` (frozen
migration 0167, [#1925](https://github.com/epiphany40223/parishkit/issues/1925))
returns the same answer for an address, compared in lower case, so a
user's [Timeline](../admin-portal/spec.md#user-timeline) can show the
Ministries of a user who never signed in and so has no portal user row. It
is read-only (STABLE, no audit of its own; the page's view event names the
reader), admits only the web login (or the schema owner) and an actor who is
an enabled Administrator (`stewardship_export_authorized_v1(actor, true)`),
refusing anything else with SQLSTATE 42501 and a malformed address with
22023, and returns only Ministry DUIDs; database-grants lets only the web
login execute it. `stewardship_ministry_scope_v1` and Python's
`current_principal()` read the per-user scope, so Python never restates the
rule and can never grant more than SQL. The Admin session guard (#306) admits by the
user's role alone (by login rule in the interim code). Every role comes only
from users (until #952 lands, from login rules in the interim code), never
from ParishSoft; Staff and Administrators see every
Ministry. Scope is recomputed on every request,
so a full refresh that removes a role or an address takes effect on the next
request. Nothing is cached
between requests: the web opens a new database connection for each request,
and an Admin page reads the scope at least twice (the principal, and the
menu's follow-up count, which derives the scope in SQL from the actor). The
function therefore reads the current snapshot first and then parses only
that snapshot's roster rows, never the other retained snapshots'. With 3,000
Members, 72 Ministries, 300 leaders and three retained snapshots, the first
read on a fresh connection takes a few tens of milliseconds, and a test
bounds it.

`stewardship_ministry_leader_coverage_v1(session, correlation)` (migration
0169, [#1594](https://github.com/epiphany40223/parishkit/issues/1594)) is a
definer the web login may execute that reads this definition for Staff and
Administrators: for each Ministry with a leader, only the number of leader
addresses, how many of them are users and how many of those users have
signed in. It answers only a live Staff or Administrator Admin session and
audits each read; the Ministry requests page's
[Leaders column](../reports/spec.md#ministry-change-summary) shows it.

#### Ministry leader changes

Because Ministry scope is derived on every request, a full refresh or a
settings change can widen or narrow what a signed-in Ministry leader sees with
no other record. Each change that can move it records a count-only audit event
instead (#1034). A source promotion, and a configuration activation (a change
to the campaign's leader roles or its Ministries),
reads the current source snapshot, configuration and campaign just before its
change and, just after it in the same transaction, calls the SECURITY DEFINER
`stewardship_ministry_leader_changes_v1`. That function compares the
(address, Ministry) pairs sign-in grants now with those at the earlier
pointers and, when any differ, writes one `ministry_leaders_changed` audit
event itself: `count` is the pairs gained and `matching_count` the pairs lost,
so a leader's changed address counts once each way. A promotion's event also
carries the promoted `snapshot_id`; its subject is the refresh task, and an
activation's is the configuration request. The event commits or rolls back
with the change. A refresh or activation that changes no pair records
nothing.

The function returns nothing: no address, name, Member DUID or count reaches
the calling login, so a login that stages changes and rolls them back learns
nothing from it, and the configuration installer cannot read the audit log.
Which people gained or lost a Ministry is in the retained ParishSoft snapshots
while they exist. Since migration 0070 the definition of who leads what is
`stewardship_ministry_leaders_at_v1(configuration, campaign, snapshot)`, which
no login may execute; `stewardship_ministry_leaders_v1()` is a thin wrapper
over it at the current pointers, so the earlier and current sides cannot
drift. The recorder refuses every session but the worker's (promotion and
setup completion), the configuration installer's and the schema owner's, and
`database-grants` gives EXECUTE to those two logins alone. Other logins that
activate a configuration (bootstrap, offline Admin-access recovery) record
nothing. Recording is best-effort: a failure is logged and the promotion or
activation goes on, except a SQL time limit, which fails the attempt like any
other statement in it. Inside a promotion the count is one more statement
under the promotion's own statement limit (`PROMOTION_STATEMENT_SECONDS`, 120
seconds), so a pathologically slow count fails that promotion attempt, which
retries; a normal count takes tens of milliseconds. System logs list the event
in the [ParishSoft refreshes](../admin-portal/spec.md#activity-groups)
Activity group.

#### Person identity reads

A user's [Timeline](../admin-portal/spec.md#user-timeline-family-part)
shows the ParishSoft Family the user belongs to, and a Family's Timeline
names its users ([#1867](https://github.com/epiphany40223/parishkit/issues/1867)).
Both need to match an address against Family and Member contact records,
which the web login cannot read, so frozen migration 0157 adds two
`SECURITY DEFINER` reads with a fixed search path:

- `stewardship_address_people_v1(address, actor)` returns `(family_duid,
  member_duid)` for each Family contact (`member_duid` null) and each Member
  contact (with the Member's Family) in the current ParishSoft snapshot
  holding `address` as a valid email, compared in lower case; only Families
  present in that snapshot are returned.
- `stewardship_family_users_v1(family_duid, actor)` returns the user record
  IDs (`stewardship_address_rule.record_id`) of the active configuration's
  users (rows with a role) whose address, in lower case, is a valid email of
  that Family's contact or one of its Members' contacts in the current
  snapshot.

Both return identifiers only, never an address, name or other contact
field, and nothing before the first promoted snapshot. Like the
[Sent mail reads](#outbox-recipient-reads) they refuse (SQLSTATE `42501`)
any session login but the web login and the schema owner, and any actor
`stewardship_export_authorized_v1(actor, true)` does not admit as an enabled
Administrator; malformed input is refused with `22023`. PUBLIC has no
EXECUTE and `database-grants` grants it only to the web login. The pages'
own view audit (`user_timeline_viewed`, `family_timeline_viewed`) names the
actor and subject, so the reads add no audit of their own. Each reads the
current snapshot's contacts through the snapshot's `(snapshot, source_key)`
key; the address read parses every contact of that snapshot (a few
thousand at parish scale), so it needs no new index.

### Submission

Every final click creates an immutable `Submission` version containing:

- campaign, Family DUID, monotonically increasing Family version, and mode
  (`test` or `live`);
- reviewed baseline source snapshot, source snapshot used for final validation,
  form-baseline/projection version, and prior effective submission, if any;
- submitted UTC time and date derived from the Campaign's immutable timezone;
- complete normalized answers for all enabled sections;
- validation/content/schema versions;
- request/session correlation without storing credentials;
- `entered_by_id`, the portal user who entered it for the Family through
  [Open form](../reports/spec.md#open-form), or null when the Family
  entered it (a soft reference with no foreign key, added by frozen migration
  0065); and
- `upcoming_pledge`, ParishSoft's pledge for the campaign's upcoming
  financial period in the validation snapshot, recorded only for a response
  that answers the financial questions and only when that snapshot's giving
  read is proven complete, else null (frozen migration 0136, provisional
  number; the review pipeline's A for the pledge,
  [Financial stream](../review-pipeline/spec.md#financial-stream)); and
- `reviewed_view` and `shown_items`, what the response's form showed, copied
  unchanged from its form baseline
  ([shown items](../review-pipeline/spec.md#shown-items); added by frozen
  migration 0143).

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
Published or resolved records are outcomes no worker changes; only Back to
review on Census changes, a paired `reopened` resolution with its required
note, returns one to `pending` with its decision kept
([undoing a terminal stage](../review-pipeline/spec.md#undoing-a-terminal-stage)).
A later response creates new proposed-change records where necessary. A later census response that
gives the same answer again for an ordinary field already *published* or
*resolved_external* (not yet in the promoted download) creates none: the
entered change stays as it is
([resubmission rule](../review-pipeline/spec.md#resubmission-before-the-reviewed-form),
[#1683](https://github.com/epiphany40223/parishkit/issues/1683)). The
submission's completeness check admits that absence, and the effective-value
index keeps that change for the next response while each census response
answers it the same way. A terminal request (moved, deceased, a death date)
asked for again after it was entered is a new request, since an entered one
is no longer presented. The same answer again for an open change that
ParishSoft already holds (the answer equals ParishSoft's value) also
creates none: the change keeps its stage and decision on its earlier
response, and the completeness check, the effective-value index and the
census guard's predecessor lookup find it there the same way, so a later
different answer replaces it
([#1895](https://github.com/epiphany40223/parishkit/issues/1895)). A Family response never
resolves a census change upstream.

A Member the Family added on the form has only the form's local UUID, and
the form drops that entry once its New Member change is Entered by hand;
from then on the Family answers for the person under the new ParishSoft
Member, once a refresh shows them. Each promotion
records an added Member link (`stewardship_added_member_link`, one
immutable row per Family campaign and added Member: the local UUID, the
ParishSoft Member DUID, the Entered by hand New Member change it rests on
and the snapshot that showed the Member;
[#1670](https://github.com/epiphany40223/parishkit/issues/1670)) when the
evidence is causal and unique, never by matching names, email or birth
dates:

- the added Member's New Member change is Entered by hand (Staff created
  the person in ParishSoft);
- it is the Family's only Entered by hand New Member without a link; and
- the promoted snapshot has exactly one active, living Member of the Family
  who was not in the Family's ParishSoft household on any live response up
  to the one that first added the person (its `members` answers) and who has
  no link yet.

Anything ambiguous records nothing, and a recorded link never changes. Only
the promoting source worker may insert a link, and its guard checks the
change, the snapshot's Member and the earlier responses again. The Ministry
leader contact rule reads the link
([cannot participate](../reports/spec.md#ministry-change-summary)).

Writability is `api`, `manual`, or `report-only`. It is derived from a shared
ParishSoft capability registry, not guessed in views. The initial registry is:

| Change | Handling |
| --- | --- |
| Family home/mailing contact/address fields | Manual until a verified address read exists; the v2 Family contact PUT accepts them, but they cannot be read back to confirm ([Review and publication](#review-and-publication)) |
| Member first/middle/last/nickname/maiden names | ParishSoft v2 Member contact PUT |
| Member birth date, language | ParishSoft v2 Member contact PUT where semantically sufficient |
| Member gender | Manual: the v2 Member contact PUT accepts a one-character `gender` but the write never changes the Member ([#1603](https://github.com/epiphany40223/parishkit/issues/1603)) |
| Member death date | Manual, with the deceased-status request: writing `dateOfDeath` leaves the Member Active |
| Member email and home/mobile/work phone | ParishSoft v2 Member contact PUT |
| Any answer that removes a recorded value | Manual: the API cannot clear a field |
| Prefix, suffix, marital status | Manual unless a verified API capability is added |
| Deceased-status or moved-household semantic request | Manual; writing a death date alone does not complete the semantic request |
| Proposed Member/Family structure | Manual |
| Parish-wide email opt-out | Manual/report to the responsible source system |
| Ministry join/leave | Ministry workflow/report only |
| New pledge/share method | Financial report/export only |

The capability registry must be covered by tests against recorded, redacted API
shapes and updated when ParishSoft support changes.

The registry follows the live v2 write test of October 17, 2026
([#528](https://github.com/epiphany40223/parishkit/issues/528),
[#1596](https://github.com/epiphany40223/parishkit/issues/1596)). Both contact
PUTs are partial updates: a field left out of the body is left unchanged. The
API cannot clear a value: `null` is ignored and `""` is refused. Setting
`dateOfDeath` stores the date but leaves the Member's status Active. The
Family PUT's `primaryPhone` writes what a Family read returns as
`familyHomePhone`, so publication maps each written field to the field it is
read back from rather than assuming the names match. Family address blocks
cannot be read back at all.

A second live test the same day
([#1603](https://github.com/epiphany40223/parishkit/issues/1603)) sent a
Member contact PUT holding only `gender` and read the Member back from every
v2 read that returns it (as `sex`). A one-character code (`F`, `U`) or `""`
was accepted with HTTP 200 but left the Member's value unchanged in every
read, also ten seconds later; an unknown code (`X`) gave HTTP 500 and a
whole word (`Female`) HTTP 400. Reads return the one-character code (`M`),
not the census word. Gender therefore cannot be published, and is `manual`
like prefix, suffix and marital status; a forward migration re-marks the
open gender changes recorded before it in the same way, with the same
count-only `census_handling_corrected` audit event.

Handling is therefore decided per value, not only per field: a change to an
API-writable field is `manual` when the Family's answer removes the value (a
blank answer, or a birth date of Unknown, over a recorded value). The SQL
proposal guard derives the same handling, so a proposal cannot be stored as
`api` when publication could not carry it. Changes recorded before this
correction that are still open (`pending` or `conflict`) are re-marked
`manual` once by a forward migration, which records one
`census_handling_corrected` audit event with only their count. Their
decision, edited value and execution are kept, so an ignored change stays
ignored and every other one appears in Staff's *By hand* work as *To do*
(or *Conflict* if ParishSoft changed since the Family answered); every other
row (queued, published, resolved, superseded or cancelled) is
left as it is.

A submission that marks a Member deceased and supplies a death date creates two
distinct proposals: a manual `death_date` field proposal and a manual
`deceased_status` semantic request, both entered by hand together. Their
decisions and execution states are independent, and neither queue may collapse
or silently discard the other.

### Follow-up records

An `AdditionalInformationItem` is created only when a live submission's
nonblank text differs from the Family's prior effective text. It retains the
submitted text, submission reference, disposition (`current_actionable`,
`superseded`, or `withdrawn`), `follow_up_needed`, `followed_up_at`, and
versioned Staff notes. In the submission transaction, replacement text marks
the prior current item `superseded` and links the replacement; clearing text
marks it `withdrawn` without deleting history. Default Staff queues include only items still `current_actionable`; a report
email's
[open additional information requests block](../report-emails/spec.md#open-additional-information-requests-block)
lists every such item submitted by its as-of moment and not yet Handled or
Ignored (To review or To handle), on every send, and keeps no state between
sends. History views expose every
disposition.
Each Staff revision also records its review decision (`accepted`, `ignored`
or `none`; empty on revisions saved before schema migration 0126), from which
the [review pipeline](../review-pipeline/spec.md#additional-information-stream)
derives the item's stage.

A cannot-participate or cannot-attend answer stores nothing beyond the live
response. Each review decision on one is an immutable
`stewardship_limitation_review` row naming the Family campaign record, the
answer (a Member's key, or the Family itself), the effective live response
it was decided on, a per-answer sequence, the decision, the note, the actor
and the time ([Limitations](../reports/spec.md#limitations)).

A `MinistryRequest` represents one Member/Ministry requested action (`join` or
`leave`). Latest effective submissions may create, cancel, or supersede a
request but do not erase its history. Workflow state is `new`, `assigned`,
`in_progress`, `resolved`, `closed_no_response`, `cancelled`, or `superseded`.
Resolution outcome is `joined`, `leave_confirmed`, `declined`, `no_response`,
`duplicate`, or `other`. Contact attempts record time, channel, actor, and
notes. Follow-up has no assignment
([Follow-up workflows](../admin-portal/spec.md#follow-up-workflows)): Staff
edits store no assignee and never choose `assigned`. The `assignee_id` column,
the `assigned` state and their database rules remain, unused, for requests
assigned before that decision, which a same-intent Family resubmission still
carries forward until the next Staff edit clears them.

The review pipeline adds three request fields
([#1684](https://github.com/epiphany40223/parishkit/issues/1684), one
additive forward migration; no existing row or value changes):
`reviewed_ministry_duid`, the Ministry an Edit carries forward (set only on a
request resolved as joined or leave confirmed, never its own Ministry, and
cleared by Back to review), which every revision also records;
`parish_added`, true for a request Staff added with no Family answer behind
it, attached to the Family's latest live response; and
`added_in_place_of`, the Ignored request of the same Member it was added
in place of. Each revision also keeps its decision's note
(`decision_note`). The SQL guards admit, besides today's edits: a revision
that sends a resolved or closed request back to In progress (reopening an
ignored one takes an optional note since forward migration 0158,
[#1889](https://github.com/epiphany40223/parishkit/issues/1889)), one that undoes a terminal step (the Entered in
ParishSoft tick, a Confirmed refresh check or a refresh resolution; with a
required note,
[undoing a terminal stage](../review-pipeline/spec.md#undoing-a-terminal-stage));
a request the parish added goes back to In progress like any other, and
no revision withdraws (cancels) one since forward migration 0180
([#2173](https://github.com/epiphany40223/parishkit/issues/2173)), whose
submission completeness check also lets a Family response leave an open
request the parish added in force;
an Edit only by someone who may follow up both the request's Ministry and
the one chosen, which must be another of the campaign's Ministries; and an
added request only from a current Staff member or Administrator, for a
Member of that Family's response and a campaign Ministry. A Family answer
that repeats a decided request's action for the same Member and Ministry
creates no new request: the submission's completeness check accepts the
decided request in its place ("Family asked again"). Authority for a
request is checked against its current Ministry: the edited one once
edited.

Manual census work uses proposed-change execution state rather than a separate
workflow; each resolution's note, actor and time are an immutable resolution
row ([Manual census resolution](../reports/spec.md#manual-census-resolution)).
Admin and Staff review every census change (Accept, Edit, Ignore, Back to
review), each decision an immutable review row
([Automatic census review](../reports/spec.md#automatic-census-review)), and
mark By hand items resolved externally; only an Administrator publishes
([role-specific actions](../review-pipeline/spec.md#role-specific-actions)).

### Content and email templates

Named campaign content slots have immutable versions. Required slots include
Family login help, pre-start, post-end, Family census introduction, Member
census introduction, Ministry introduction, financial introduction,
additional-information prompt, review/attestation introduction, Thank You page,
access-denied contact help, and submission-confirmation text. Empty optional
slots render nothing. The optional `closing` slot is a content-only Family page
between Financial stewardship and Additional information; a campaign without
closing content, or whose closing content has no visible text, has no closing
step.

The receipt email is the confirmation email template alone (see
[submission confirmation](../background-processing/spec.md#submission-confirmation)),
not a second Thank You page; the Family browser uses the separate `thank_you`
slot. The former `submission_confirmation` page slot, a closing note appended
to the receipt, is retired (#260): no new revision may use it, but an applied
configuration that already carries one stays valid and its receipts keep
appending it. The confirmation email editor opens with that note folded into
the email body, and saving the email (or cloning the campaign) stores the
folded body and removes the note in the same request. Receipts render an
email and note exactly as that folded body: the HTML joined, and the plain
text generated from the joined HTML when both parts' plain text was generated,
or otherwise joined after a blank line. The slot and its setup
step name stay allowed in the database schema only so applied history keeps
verifying.

The former `critical_alert` email slot is retired the same way (#913):
operational alerts are sent with fixed text (see
[mode routing](../background-processing/spec.md#mode-routing)), so a template
in that slot was never sent. No new revision may use it, and the content list,
setup content step, defaults and editor no longer offer it. An applied
configuration that already carries one stays valid and the record is inert;
configuration history still labels it. Cloning a campaign, or finishing a
setup whose draft saved one, leaves it behind. The slot and its setup step
name stay allowed in the database schema only so applied history keeps
verifying.

Built-in default text can improve between releases. A change affects only
slots filled or reset afterwards; saved content keeps its text. Content saved
unmodified from an earlier default still counts as default rather than
edited. For example, the confirmation email's online-giving sentence no
longer mentions a pledge (a campaign without the Financial module has none),
and a slot that still holds the earlier sentence still reads as default (#385).

Initial, reminder and confirmation templates, and report email subjects and
introductions (report values, not templates; the retired daily and weekly
digest templates are kept for the saved copies that name them), have separate subject, sanitized HTML, and generated/edited plain-text
versions. Sanitizing keeps the author's structure: the line `<div>` wrappers
that browser editors write become paragraphs, `<b>`/`<i>` become
`<strong>`/`<em>`, and markup-free text keeps blank-line paragraphs and line
breaks; already-sanitized content is unchanged. The visual editor starts new
paragraphs as `<p>`, Shift+Enter inserts a line break (`<br>`) on every
platform, and the editor keeps the line breaks of pasted plain text. While the
Admin edits the HTML source, the visual editor stays visible: after a short
pause the source is sent to the server's sanitizer, and the editor is redrawn
only from the sanitized result (never from the raw source), dimmed and
read-only until it answers. A notice names the markup sanitizing removed
(elements, attributes, unsafe link targets, comments). The same passive
request, which never renews the idle session, also returns the generated
plain text. Generated
plain text separates paragraphs with a blank line, starts list items with a
hyphen (or a number), writes each link as `label: URL`, and drops lines that
hold only spaces or tabs (such as those left by indented HTML source). While
"Generate plain text from HTML" is checked, the editor shows the server's
generated plain text read-only; editing it (or "Edit plain text") unchecks
the box and keeps the text, a saved revision whose plain text was edited
opens unchecked, and the server refuses typed plain text that differs from
the generated text while the box is checked instead of silently dropping it. Previews and
setup email tests fill Family placeholders with realistic but plainly
fictional values (the "Sample" household, "Alex and Sam Sample", a code in the
live format and links on the reserved `.invalid` domain). Every outgoing
email's HTML alternative is wrapped, when the message is built, in one shared
email-client-safe layout (a readable sans-serif font and a centered 600px
column); test messages show one small notice line instead of a large TEST
heading. Administrator report emails use a wider desktop column instead (see
[shared email layout](../report-emails/spec.md#shared-email-layout)).
The layout's colours and shapes follow the
[shared visual style](../ui-conventions/spec.md#shared-visual-style).
Retained content and the plain-text alternative are unchanged.
Content text rules (the sanitizer's canonical form and the placeholder
contracts) apply to content being authored or changed. Revisions already in
an applied configuration version were validated under the rules in force when
they were applied and are integrity-checked by digest, so verifying applied
history, and carrying an unchanged revision into a new candidate, never re-run
today's text rules; a sanitizer improvement therefore cannot block later
configuration changes. Rendering likewise re-sanitizes retained HTML with
today's sanitizer instead of refusing it; plain text stays as stored, and the
placeholder and credential rules still refuse. The Pages and emails list
marks a retained page or email whose HTML today's sanitizer would change as
"Re-save recommended", and its editor names the markup that sending already
removes and any placeholder found only inside it. An invitation or reminder
whose Family code or link is only inside removed markup is marked "Can't be
sent until fixed" instead, because sending refuses it. Previewing and applying
the form stores the cleaned version through the normal configuration change
(after putting back any lost placeholder); nothing rewrites applied history
automatically. [Go live](../admin-portal/spec.md#go-live-page)
and the [System health page](../admin-portal/spec.md#system-health-page) list
the same flagged content.
A configuration request that can never verify against
the applied history fails with a visible reason instead of waiting forever.
The direct submission confirmation selects at most one email template
per campaign; editing replaces its immutable revision, not an arbitrary member
of a template list. Without a selected template, use the built-in non-sensitive
confirmation subject/body. A retired closing note, when an applied
configuration still carries one, is appended as described above, and required
receipt facts always appear independently of the optional authored body. The
receipt template accepts no access-code or secure-link placeholders. Scheduled email templates retain
their explicit per-schedule revision selection.
Family templates support only documented placeholders, including
Family names, code, secure link, generic URL, parish fields, dates, and
campaign fields. A name placeholder means the same people in a Family email
and on a Family page: `head_salutation` is the Family's active heads of
household (every one of them, not only those with an eligible email address),
grouped by surname as a salutation ("Andrew and Betty Test");
`all_family_member_names` is every active, listed Member, grouped the same
way ("Andrew, Betty and Cy Test"); `family_member_names` is the older name
for `head_salutation`, kept because stored templates use it; and
`family_name` is the Family's display name, which the other three fall back
to when they have no usable name (for the salutations, no usable head
name). The email worker reads these names from
the current source snapshot, never from a proposed census value; a Family
page fills them from the same effective projection as the rest of the page.
`parish_email` is the configured outgoing-mail Reply-to
address, and `online_giving_url` is the Parish profile's optional HTTPS online
giving page, falling back to the parish website when none is configured.
Unknown placeholders are validation failures, not empty text.
Page and email bodies may also link to files in the deployment's hosted
file library (`stewardship_hosted_file`) with `{{ file.<slug> }}` and show
hosted images inline; that placeholder family, its sanitizer and delivery-
check rules, and the record itself are defined by
[hosted files](../hosted-files/spec.md).
Initial invitations and reminders require the code and secure-link placeholders
in each body alternative, including generated plaintext. Those credential
placeholders are not allowed in subjects. Authoring, configuration validation
and background preparation enforce the same rule, and the editors name which
version (subject, HTML, typed or generated plain text) lacks or wrongly holds
which placeholder or reserved marker; generated plaintext keeps the
secure link as `label: URL`, and an author can still edit it. Testing subject
presentation reserves its mandatory mode prefix and shortens only
non-credential content.

### Job, outbox, audit, and purge records

`TaskRun` stores task type, optional execution idempotency key, logical-operation
identity, retry-root/parent references and retry sequence, state, progress phase/
counts, append-only attempt/transition history (`TaskRunEvent`), timestamps,
initiator, and heartbeat. It stores no summary or error text: failures are
coded operational log entries. The heartbeat and progress events of a run
that finished more than 30 days ago are pruned by the
[maintenance task](../admin-automation/spec.md#maintenance-task); every other
event is kept. A partial unique constraint on
`(task type, idempotency key)` applies whenever the key is present. Automatic
retry/recovery claims the existing nonterminal row. Explicit authorized retry
of a terminal failed run allocates a new linked row and derived execution key,
preserving the logical operation, domain checkpoints, and semantic delivery key.
Enforce unique retry sequence within a chain and at most one nonterminal run
per chain under its lock. Retry-command deduplication returns the allocated run
instead of allocating another. State sets, fencing, and allowed transitions are
owned by [background processing](../background-processing/spec.md#durable-scheduling-and-task-execution).
`ProductionTransitionRequest` stores campaign/Admin, state, gate version,
inventory digest and counts, non-sensitive Testing aggregate reference, cleanup
TaskRun, batch checkpoints/counts, acknowledgement time and the acting session's sign-in instant,
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
credential namespace and rehearsal epoch when credential-bearing,
nullable delivery-pause hold reference and captured pause version,
non-null idempotency scope, stable semantic idempotency key, provider-key/
message-ID fingerprints,
reconciliation evidence, resolution actor/time, and provider result. Its state
is `pending`, `submitting`, `retry_wait`, `delivery_unknown`, `delivered`,
`permanent_failure`, or `cancelled`; only the final three are terminal.
`delivery_unknown` follows the provider-acceptance workflow in the
[background-processing specification](../background-processing/spec.md#family-invitations-and-reminders).
Until a terminal state, credential substitutions needed for retry are
separately sealed with application-level encryption and a versioned key ID;
only the dispatch worker may decrypt them. Terminal handling scrubs the sealed
values. Provider acceptance means sent; the system does not read bounces, but
an Administrator can report one (see `RecipientRefusal` below).

`AddressRetry` (`stewardship_outbox_address_retry`, frozen migration 0077,
[#806](https://github.com/epiphany40223/parishkit/issues/806)) is a delivered
Production invitation's or reminder's follow-up to the addresses one accepted
attempt refused only for now (see
[background processing](../background-processing/spec.md#addresses-refused-for-now)).
It references the message, that attempt's event (unique, so at most one
follow-up per attempt) and rendering, and its own `family_address_retry` task.
It stores positions in that rendering's routed envelope, never addresses:
`positions` (fixed: the attempt's refused-for-now positions), `remaining`
(still to send) and `refused` (refused permanently), with the latest
follow-up outcome as a status and positions. Its state is `waiting`,
`submitting`, `finished`, `unknown`, `failed` or `cancelled`; the last four
are terminal. Like the message, it keeps a copy of the message's sealed
code-and-link reference (taken before the acceptance scrubbed the message's)
until it ends, and the credential generation and link epoch it was sealed
for. Only `stewardship_address_retry_open_v1`, a `SECURITY DEFINER` function
mail dispatch calls under its live delivery claim, inserts a row and its task
(no login may insert that task type). The guard
`stewardship_address_retry_guard_v1` admits a change only from mail dispatch
under the work-order lock and a live claim of the row's own task (or the
recovery of an abandoned task after its provider deadline, which records
`unknown`), keeps `remaining` shrinking and `refused` growing inside
`positions`, checks each outcome against its move, and never deletes a row
or changes an ended one. It admits "submitting" only in the scope the
message itself would need to be sent, never for a row created at or before
the runtime's `restore_activated_at` (a restore keeps credentials, so a
restored follow-up may already have been sent), and never once a later
invitation or reminder to the Family has been accepted. Mail dispatch reads the Family's link token for a
waiting follow-up only through `stewardship_address_retry_token_v1`, bound to
the same claim. A follow-up's permanent refusal of an address is a
`RecipientRefusal` naming the accepted attempt's event; the refusal guard
(frozen migration 0079) admits it only when that attempt's follow-up, for
the same message and rendering, lists the address's position as refused
permanently in its latest outcome, recorded by the refusal's own worker and
task run.

An Administrator's report that a delivered email bounced at one address
([#806](https://github.com/epiphany40223/parishkit/issues/806) slice 4,
frozen migration 0081) is a `RecipientRefusal` too, so it sets the address
aside for that Family exactly as a provider refusal does. The refusal's
`kind` is `provider` (every refusal mail dispatch records, and every row
before 0081) or `reported_bounce`, and its `evidence_note` is empty for a
provider refusal and, for a report, the Administrator's note (more than
spaces, at most 2000 characters); the `recipient_refusal_kind` check ties
them together. A report names the email's newest delivered event and is
written only by `stewardship_report_recipient_bounce_v1`, a `SECURITY
DEFINER` function only the web login may execute (web keeps SELECT only on
the table). It records the refusal and a `recipient_bounce_reported` audit
event in one transaction. The refusal guard admits a report only from the
web login, for an enabled Administrator actor, with a note, naming a
`delivered` event of a Production-routed email of a Family in the current
campaign (not archived and admitting Admin changes), for an address in that
event's routed and intended recipients that the Family has no unresolved
refusal of. Clearing and ParishSoft-correction resolution work as for any
refusal; a cleared report cannot be filed again against the same email
(one refusal per email and address). No code, link, token or rendering
changes.

The OutboxMessage pause hold records a pause independently of schedule
membership, including directly created submission receipts. Held messages keep
their ordinary pending/retry state; the hold is a separate dispatch-admission
condition. Pause, resume, close resolution, and pre-provider rechecks update
or release it under the Campaign/outbox locks without overwriting delivery
outcomes. ScheduleOccurrence holds and related outbox holds are reconciled
together; a stale dispatch hint cannot bypass either.

The unique `(idempotency scope, mode, semantic idempotency key)` constraint
applies to every OutboxMessage, including operational messages whose scope is
the deployment rather than a Campaign. Provider attempts update the one row;
concurrent producers cannot create duplicate semantic mail.

A [Ministry leader email](../background-processing/spec.md#ministry-leader-emails)
(purpose `leader_email`,
[#1592](https://github.com/epiphany40223/parishkit/issues/1592)) is the only
Testing-routed message that belongs to no Campaign: its scope is the parish,
its Campaign and Family are empty, and its routing is `testing_override` in
Testing and `production` in Production. Three immutable records own it:
`LeaderEmailRequest` (one Administrator's Apply: its actor, configuration,
mode, Testing recipient, the chosen addresses and, filled in by its database
guard, each leader's Ministries with their open join and leave counts),
`LeaderEmailCohort` (the request's addresses frozen by its fenced preparation
Task) and `LeaderEmailRecipient` (one address, its outbox message and the
compiled subject and bodies it sends). None holds a parishioner's name or
contact.

Terminal outbox outcomes stop automatic scheduling. The sole explicit retry
exception is a still-applicable `permanent_failure` returning to `pending`
under the [background retry contract](../background-processing/spec.md#durable-scheduling-and-task-execution).
Preserve immutable numbered delivery-attempt outcomes and the same outbox row/
semantic key; `delivered` and `cancelled` cannot be reopened. Coordinate that
exception atomically with any linked occurrence and TaskRun retry chain, and
repeat credential/admission checks before creating fresh sealed substitutions.
The same contract applies to direct receipts without an occurrence.

`AuditEvent` is append-only and stores event type, subject ID, ownership scope
(deployment, or parish with an optional campaign reference), actor ID, UTC
time, and request/task correlation. An optional one-to-one `AuditContext`
adds the actor kind and structured context validated against a closed schema
(before/after states or versions where an event has them). Neither stores a
source IP address, free text, URL, session key, submitted values, or viewed
report data. `OperationalLog` stores
the five standard levels and structured context. The Admin log view queries both
without pretending DEBUG diagnostics are domain audit events. The campaign
reference is always a plain UUID rather than a campaign-owned foreign key, so
audit history survives a purge; while a campaign purge gate is active,
read-only report access is audited this way too. The
retained event contains no viewed report data and is outside purge inventory;
exports and mutations remain blocked.

The purge records below are the target design; v1 has none of their tables
(see the [campaign status note](#campaign)).

`PurgeRequest` records the selected archived campaign, initiating Admin,
post-quiescence inventory and completion/expiry times, purge-triggered backup
task and immutable verified-backup reference, original backup completion time,
current backup-verification reference/version and expiry,
recovery-evidence reference/version and invalidation reason,
re-authentication time, typed-confirmation digest, estimated counts, gate-
acquisition/quiescence times, state, batch checkpoints, progress,
reader-drain start/deadline/completion and sanitized timeout evidence, and final
non-sensitive tombstone. A request in any state other than
`cancelled` or `failed_pre_delete` owns the durable campaign purge gate. It has
a state machine separate from the associated
[Campaign lifecycle](../spec.md#campaign-lifecycle):

- `draft`: the campaign was selected and inventory/backup checks may be run or
  refreshed;
- `ready_for_confirmation`: inventory and recovery evidence are current, the backup is verified, and
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

`PurgeBackupVerification` records request, backup reference/manifest digest,
original snapshot/completion times, associated verification TaskRun, pinned
request/evidence revision, mutation/quiescence and relevant credential/key
manifest versions, verification start/completion/expiry times, result, and
supersession or invalidation metadata. Initial verification and each subsequent
revalidation append records rather than modifying earlier evidence. Task lease
and revision guards prevent duplicate or stale completions from renewing the
current evidence. Backup revalidation does not create a new backup or extend
its retention; the [purge workflow](../admin-portal/spec.md#campaign-purge)
owns verification, freshness, and recovery-evidence dependency rules.

`PurgeRecoveryEvidence` is an immutable non-secret attestation record containing
request, selected backup reference/manifest digest, escrow reference/manifest
digest, exact credential/key fingerprint set, relevant manifest version,
recovery-key fingerprints, named operator, submitting Admin, verification
completion/server receipt/expiry timestamps, result, and supersession or
invalidation metadata. Store no secrets or arbitrary uploaded payload. New
verification creates a new record rather than rewriting old evidence.
Validation, expiry, dependencies, and the operator-trust boundary are owned by
the [purge workflow](../admin-portal/spec.md#campaign-purge).

The permitted request transitions are `draft` to `ready_for_confirmation` or
`cancelled`; `ready_for_confirmation` back to `draft` when a prerequisite
expires, or to `queued`/`cancelled`; and `queued` to `running` or, through an
atomic claim cancellation, `cancelled`, or to `failed_pre_delete` when the
atomic worker-claim prerequisite recheck fails. An initial `running` attempt may enter
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
occurrence, outbox row, export, and unfinished ActivationCatchUpDemand. A
terminal catch-up TaskRun does not hide its unfinished demand. No new worker may claim queued or retrying
work after acquisition; safely cancellable records become terminal
`cancelled` for TaskRuns/outbox rows or `skipped` with a purge reason for
ScheduleOccurrences, under the authoritative background state tables, while
already running, abandoned, provider-submitting, delivery-unknown, or
otherwise irreversible work must reach an accurately reconciled terminal state.
The request records a quiescence time only after none remain. Inventory and
backup evidence used for confirmation must describe a database snapshot at or
after that quiescence time. Inventory expires 60 minutes after completion;
backup evidence expires 60 minutes after the latest successful verification
completion under the [purge workflow](../admin-portal/spec.md#campaign-purge).
Revalidating the same immutable backup can renew that evidence without renewing
or invalidating an otherwise current recovery attestation. Ordinary expiration
invalidates only that artifact and preserves
the other artifact when it remains current; a later campaign-owned mutation or
change to quiescence invalidates both. The final confirmation transaction checks
both stored expirations and the shared mutation/quiescence invalidation version
under the request and Campaign locks.

Recovery evidence is an additional prerequisite with its own expiry and pinned
backup/credential/escrow manifest references. Initial worker claim and final
confirmation both check its current version, expiry, and dependencies under
the same request/manifest guards; a concurrent relevant manifest change cannot
leave a stale attestation valid. Backup replacement or mutation/quiescence
invalidation propagates to this dependent evidence. Expiry or invalidation
returns an unclaimed ready request to `draft`, or causes claim to enter
`failed_pre_delete`; it never releases a post-deletion gate.

Final confirmation also locks the global current-campaign record and rechecks
Testing mode, a null current pointer, and no other campaign in any current or
purge-in-progress state, using the same lock order as draft/request creation.

The gate is released only when the request becomes `cancelled` or
`failed_pre_delete`, before any deletion has committed. It remains permanent
through `running`, either post-deletion failure state, and `succeeded`. Worker
claim rechecks under row locks that the request owns the gate, the Campaign is
still `archived`, global mode is Testing, the current pointer is null, no other
campaign is in a current or purge-in-progress state, all conflicting work is
terminal, inventory, backup, and recovery
evidence remain current, and no campaign mutation occurred after their
snapshot. Failure performs no deletion and atomically enters
`failed_pre_delete`, releases the gate, and leaves the Campaign `archived`.

#### Outbox recipient reads

An email's intended and routed recipient lists are stored with its rendering
(`stewardship_outbox_render`), beside its subject and body. The web login has
no grant on that table, so no Admin page reads an email's content. The one
exception is the [Mail message page](../admin-portal/spec.md#mail-message-page),
which shows whom the email was for
([#1024](https://github.com/epiphany40223/parishkit/issues/1024)). It reads
them through `stewardship_delivery_recipients_v1(message, actor)`, a
`SECURITY DEFINER` function with a fixed search path (frozen migration 0058).
The function returns only the addresses of the message's newest rendering,
one row each, with kind `intended` or `routed`, in stored order, and no other
column of the rendering. It refuses (SQLSTATE `42501`) any session login but
the web login, the schema owner, or a member of the schema owner role, and
any actor that
`stewardship_export_authorized_v1(actor, true)` does not admit as an enabled
Administrator, which is the same SQL check the page's resolution commands
make. PUBLIC has no EXECUTE; `database-grants` grants it only to the web
login. The page's `delivery_viewed` audit record names the actor and the
email, so the read needs no audit of its own. The table grant is unchanged:
the web login still cannot `SELECT` any rendering column.

[Sent mail](../admin-portal/spec.md#sent-mail) adds two reads of the same
kind, with the same caller and actor checks, fixed search path and grants
(frozen migration 0116,
[#1562](https://github.com/epiphany40223/parishkit/issues/1562)).
`stewardship_delivery_ids_for_address_v1(address, actor)` returns only the
ids of the emails any of whose renderings has `address` among its intended
recipients, compared in lower case; the expression GIN index
`outbox_render_intended_lower` (over
`lower(intended_recipients::text)::jsonb`, operator class `jsonb_path_ops`,
since the read uses only containment) answers it
without reading every rendering. `stewardship_delivery_page_recipients_v1(messages, actor)` returns
`(message, address)` for the intended addresses of the current rendering of
each given email that has no Family (an Administrator report or notice), at
most 500 ids per call, so the list can name each such email's
Administrator; a Family email's addresses never leave it. Neither returns any
other column of the rendering.

It names those people with a third read of the same kind (frozen migration
0145, [#1562](https://github.com/epiphany40223/parishkit/issues/1562)):
`stewardship_delivery_page_member_names_v1(messages, actor)` returns, for
each given email with no Family (at most 500 ids) whose current rendering's
first intended address a ParishSoft Member's contact record in the current
snapshot lists (matched trimmed and case-insensitively), the single best
name, ranked as `stewardship_member_names_v1` ranks the Users page's default
Name. It returns only `(message, name)`, never an address, another contact
field, a DUID or a Family, and only to the web login for an enabled
Administrator. It takes email ids rather than addresses, so it cannot name
an arbitrary address.

The same page says what the latest attempt recorded for each address
([#806](https://github.com/epiphany40223/parishkit/issues/806)). An attempt's
evidence (`stewardship_outbox_event.evidence_note`, outside the web login's
grants) lists only which positions of its rendering's routed envelope the
provider refused for now or for good.
`stewardship_delivery_recipient_outcomes_v1(message, actor)` (frozen
migration 0068), a `SECURITY DEFINER` function with the same fixed search
path, caller checks, grants and audit as the recipients read, returns for the
same newest rendering one row per routed address in stored order: the address
and an outcome, `refused_permanently` or `refused_for_now` for a listed
position, else `delivered` if that rendering's latest finished attempt was
accepted, `unknown` if the provider could not say, and `not_delivered`
otherwise. It reads the evidence only through
`stewardship_family_smtp_result_v1`, which validates it against its own
rendering, and returns no rows when there is no rendering, no finished
attempt on it, or no valid evidence. No evidence, status or provider text
leaves it, and every address it returns is one the recipients read already
returns to the same caller. The page uses the outcomes only when their
addresses are exactly the routed list it read, since the two reads are
separate statements.

When an address was refused for now, the page also reads what that attempt's
follow-up did (#806, frozen migration 0077):
`stewardship_delivery_address_retries_v1(message, actor)`, with the same
fixed search path, caller checks, grants and audit, returns for the same
attempt one row per followed-up position: the position and `delivered`,
`refused_permanently`, `retrying` (still being tried), `retry_unknown` (a try
whose outcome is unknown) or `refused_for_now` (the follow-up ended without
reaching it). It returns no address and no rows when the attempt opened no
follow-up; the page replaces those positions' outcomes with these.

### Campaign read guards

Every campaign-detail read, report query, partial response, and generated-file
download uses the shared campaign read-guard service, including internal
callers. It acquires a shared PostgreSQL advisory transaction lock keyed by a
stable, namespaced campaign identifier before querying campaign-owned data or
opening an export file. After acquiring the guard, it checks current admission
and authorization using a fresh `READ COMMITTED` query. A `purging`,
`purge_cleanup_failed`, or `purged` Campaign admits no sensitive reads;
non-sensitive purge status/tombstone views do not need this guard. Preparation
alone does not close read admission.

The guard remains held on the same pinned database connection through all
queries, lazy evaluation, serialization, and response/file streaming. Explicit
response-lifetime management must cover streaming beyond ordinary Django view
transaction scope. Exceptions, disconnects, and timeout cancel outstanding work
and close the response/transaction; loss of the guard connection aborts the
read without reconnecting or continuing unguarded. Multi-campaign responses
acquire all guards in stable identifier order before reading any campaign.
Use bounded lock acquisition, a 60-second total interactive-read deadline, and
a five-minute total download deadline, configurable with finite maxima.
Background guarded reads (report verification and export rendering) serve no
waiting browser and use a five-minute deadline. A read-only background read
that reaches its deadline has its SQL cancelled and its task fails; only a
read that writes files (an export) also stops its worker process, so no write
outlives the purge barrier. Every deadline stop first writes a
`task_timed_out` operational log entry on its own short-lived connection,
naming the task, the limit and the elapsed time.
Database/proxy/application timeouts must enforce these lifetimes; inactivity
timeouts alone do not bound a slow continuous transfer.

A guarded report page's own access audit (started, then succeeded or failed)
is an append-only record that takes no row locks, so it commits in its own
short transaction outside the common work-order lock; a writer holding that
lock, such as a source promotion or an installer, therefore never delays a
report page.

Downloads of stored export files use a dedicated bounded connection pool and
admission limit, defaulting to four simultaneous downloads across the entire deployment,
not four per process or replica. Acquire capacity before opening a guarded
transaction or export file; do not queue waiting downloads indefinitely or
borrow interactive/background connection capacity. A multi-campaign download
uses one admitted slot and one pinned connection for all its guards. Capacity
exhaustion returns HTTP 503 with `Retry-After: 5` and an accessible busy/retry
message before any file bytes are sent. It does not invalidate the export or
bypass authorization; retries repeat authorization and purge admission checks.
Release capacity only after the stream and guard transaction close, including
disconnect, timeout, exception, and worker loss. Coordination across processes
must not reissue capacity merely because a lease expired while its connection
or stream is still alive. Downloads never fall back to an unguarded transfer.
A small download rendered on request in memory from the same rows as its
page ([Limitations](../reports/spec.md#limitations) after
[#557](https://github.com/epiphany40223/parishkit/issues/557)) is not a stored
export file: it is read under the interactive campaign read guard, with its
60-second deadline, on the web connection, as the page itself is.

The connection budget and timeout relationships are deployment invariants
defined by [operations](../operations/spec.md#download-capacity-and-timeouts).
These limits supplement rather than replace the response-lifetime purge guard.

The initial purge claim closes new read admission by committing `running`/
`purging`, records the `draining_readers` progress phase, and then waits for
the matching exclusive advisory guard without holding Campaign/global/request
row locks. Acquisition proves all previously admitted readers have released
their shared guards. A mere expired heartbeat, elapsed deadline, or empty
process list is not proof of drainage. Default drain timeout is six minutes;
deployment validation requires it to exceed both configured reader lifetimes.

With the exclusive guard held, the worker acquires the ordinary row locks,
rechecks ownership/fencing, all purge prerequisites and evidence freshness,
and commits its first deletion batch and checkpoint. It may then release the
exclusive guard: durable `purging` admission prevents further readers during
later batches and file cleanup. No deletion may occur before this barrier.
Timeout or failure before the first deletion uses `failed_pre_delete`, leaves
all campaign data intact, returns it to `archived`, and permits a new request
after refreshed prerequisites; the UI identifies the drain failure. A crashed
or replaced worker with no committed deletion checkpoint must repeat the drain
barrier. Post-deletion retries retain closed admission and ordinary resumable
semantics, never reopening reads. Buffered bytes already sent to a client
cannot be recalled; this guarantee prevents reads of partially deleted data,
not retention of previously downloaded reports.

### Admin page snapshots

The common work-order lock orders writers so that lifecycle, task, source and
configuration transactions cannot deadlock. A plain Admin page read (GET or
HEAD) takes no row locks and does not join that order: it observes one
`REPEATABLE READ READ ONLY` snapshot, so configuration, policy projections and
the promoted source pointer are seen together as their writers committed them,
and a long writer such as a source promotion or an installer never blocks an
Admin page. Any write inside the snapshot fails closed. Previews that sign a
request, confirmations and mutations keep the work-order lock; access audits
of page views do not, and commit in their own short transaction after the
snapshot. Campaign-detail reads still use the
[campaign read guards](#campaign-read-guards) above; the snapshot replaces
neither. A read whose helpers require the work order keeps the lock. Access
is rechecked after the snapshot ends, so the snapshot cannot hide a revocation
committed while the page was read.

Readers take no shared mode of the work-order lock either. A shared lock would
still wait behind every exclusive writer, which is the delay the snapshot
removes, and it cannot stand in for the writers' order: the Python admission
check and the SQL guards on writer tables require the exclusive mode. So the
Family form's issue and submit requests keep the exclusive lock. Issuing a
form writes a baseline and pins its source snapshot, and submitting writes the
submission and its derived rows, and SQL refuses both without the exclusive
lock.

### Export admission order

Export admission (queueing a report export: its request and snapshot capture)
does not join the work-order lock. It takes only its campaign's exclusive
export lock, so it never waits behind a source promotion, installer or task
transition. A transition that changes what export admission reads for a
campaign takes that campaign's export lock after the work-order lock and
before it locks the campaign row or any row admission uses: the lifecycle and
control transaction owner, configuration activation (an end-date edit or
reopen of the current campaign), the work gate that closes admission for
purge, and closing the go-live gate for Production cleanup. (Go-live intake
may already hold its Admin session row; admission never locks that row.) So such a transition waits for every export already
admitted in that campaign, and an admission that waits for it then sees its
effect and is refused. The request and capture triggers take the campaign
lock themselves, and so do the work gate, lifecycle transition, end-date
admission and go-live gate triggers, so the order holds without the
application helpers. Inside a
transaction that already holds the work-order lock, such as the exact-export
handoff, admission takes nothing more, since that lock already excludes every
transition.

No other transition needs the campaign lock. The restore review flag is set
only by a restore, never by a running transaction. A configuration activation
reads no export, so an export bound to the configuration that was active when
it was admitted is simply ordered before the activation, and the worker
rechecks current policy before it renders. A fact generation that admission
pins is protected by that generation's row lock, which fact compaction also
takes, and each report capture reads one statement snapshot. Exact-export
requests, export cancellation and the worker's attempt, publication and
cleanup keep the work-order lock: an exact request binds the current source
pointer and submission watermark and pins its source snapshot, which order
against source promotion and submission; and cancellation and publication
each check that the other has not happened.

## Effective-value merge

The value displayed on a repeat Family visit is computed from the immutable
Family-submitted value, never an Admin review edit, until the release
with [#1705](https://github.com/epiphany40223/parishkit/issues/1705)'s last
slice deploys. From then on a live form shows the
[reviewed Family form](../review-pipeline/spec.md#reviewed-family-form)
instead ([Families see reviewed changes](../review-pipeline/spec.md#families-see-reviewed-changes)),
drawn by the rules after this list; a Testing form, and a form drawn
before that release, keep these rules:

1. Start with the current ParishSoft value.
2. If there is no prior effective response change, use current.
3. If current now equals the prior Family-submitted value, ParishSoft
   already holds the change (which keeps its stage until its step,
   [#1890](https://github.com/epiphany40223/parishkit/issues/1890)) and
   current is used without a change marker.
4. If a published/resolved ProposedChange has an Admin-edited value and current
   equals that edit, use current without attributing the edit to the Family.
5. If current still equals the prior baseline, overlay the prior Family-
   submitted value and mark it changed.
6. If current differs from baseline, Family-submitted value, and any resolved
   Admin edit, preserve the Family-submitted value, mark a source conflict, and
   say only that the Family's previously supplied value remains new; never show
   or attribute the Admin edit and never mention ParishSoft.

A live form drawn as the reviewed form draws each census field from the
item its baseline's [shown items](../review-pipeline/spec.md#shown-items)
name instead: the Family's own answer by the rules above (so its change
and conflict markers are these), a reviewer's value marked changed when it
differs from the current ParishSoft value, and an Ignored item's current
ParishSoft value unmarked; a field with no shown item (none in force, or
Confirmed) shows the current ParishSoft value
([What the reviewed form shows](../review-pipeline/spec.md#what-the-reviewed-form-shows)).

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

The Family form receives the current effective submission version, source
snapshot ID, and an opaque server-issued `FamilyFormBaseline` reference bound
to its session, Family, Campaign, and live/test namespace. The baseline records
those version references, form schema/content/configuration versions, canonical
dependency-projection version/digest, and expiry bounded by the session's
absolute deadline. It contains no in-progress answers. It protects the source
snapshot and other immutable inputs needed to reconstruct the reviewed form
until replaced, submitted, cancelled, or expired. Baseline creation and source
pinning are atomic with compaction; housekeeping releases expired pins without
saving drafts or extending a session.

Final submission compares the effective Family submission version and the
canonical inputs relevant to this form, not equality of global source-snapshot
IDs. Build the versioned dependency projection from source values displayed or
used for prefilling/validation in enabled sections: Family fields, household
Member identities/composition and relevant Member fields, current Ministry
memberships and offered Ministry choices/eligibility, and applicable financial
fund/period/pledge/contribution values. Include relevant membership additions,
removals, and option availability, not merely IDs of entities present at form
load. Exclude other Families' values, unrelated source metadata, and data in
disabled sections that neither affects the form nor its validation. Canonical
comparisons use the shared typed normalization registry. Tests enumerate these
dependencies so new form fields cannot omit their concurrency protection.
Relevant form-definition/schema/configuration changes use the same review path;
unrelated configuration-version changes are not conflicts. The parish name in
the definitive live Submit action is a relevant definition dependency for all
module combinations, even when financial stewardship is disabled. Transfer required
baseline pins to the immutable Submission atomically on successful submit;
releasing a form pin cannot remove a submission's retention protection.

For financial forms, a newer giving-observation timestamp or through-date alone
does not require review when the scoped amounts, availability and mapped
funds/periods are unchanged. The displayed as-of remains the truthful context of
the retained reviewed snapshot; accepting against a newer validation snapshot
does not relabel that older observation. Changed amounts or availability still
require review. Rendered share wording, including the parish name, is a relevant
definition dependency.

The server reconstructs the baseline projection from trusted pinned inputs and
compares it with the same projection from one current promoted snapshot. A
client-supplied digest, field list, or snapshot ID is not proof of equivalence.
If only unrelated inputs changed, accept against current source after complete
validation while retaining both the reviewed baseline and validation snapshot
references in the immutable Submission. Never attribute an unseen source
change to the Family or discard an existing proposal merely because the global
snapshot advanced. No automatic rebase of changed relevant values is allowed.

If relevant inputs or the effective Family submission version changed, reject
without saving and return an authorized refreshed baseline for review. Preserve
unsaved edits only in the tab's existing memory; show updated records and the
Family's proposed values for affected fields, require resolution of competing
edits or removed/invalid selections, and then require a new definitive Submit.
Unedited fields adopt the refreshed baseline; do not replay the whole old form
as new edits. Never display removed/inaccessible Member detail merely to aid
comparison. No page reload, server draft, or browser-persistent storage is
required for this review. Missing/expired or mismatched baseline references
cannot be accepted; they require a fresh authorized form instead. Loading the
form again in the same session replaces the session's earlier open baseline,
and a deploy that changes the form schema or projection version invalidates
it. The tab then fetches a fresh authorized form itself and follows the same
review path, so its unsaved edits survive.

Under the shared source-promotion and Family submission locks, repeat the
effective-version comparison, relevant-source comparison, complete validation,
and current session/eligibility/campaign/mode/admission checks before commit.
Use the common lock order and keep this transaction limited to local,
Family-scoped work; no provider calls or whole-corpus reload occur in Submit.
A relevant promotion or another Family session's submission racing validation
must either precede these checks or wait until commit, never slip between check
and write. Loss of access or campaign closure rejects submission and follows
the normal session/form cleanup policy, rather than exposing refreshed data.
Multiple read sessions are otherwise allowed.

Within a successful transaction, the system writes the immutable submission,
sets it effective, derives proposals/workflows, records audit events, creates an
idempotent participation-fact rebuild hint, and either inserts the confirmation-
email outbox row or, when no deliverable eligible-head address exists, records
the non-error audit action `submission_receipt_skipped` with reason
`no_deliverable_recipient`. Either all commit or none do.
Only the asynchronous materializer may validate and atomically publish a new
immutable `CampaignDailyFactSet`; submission never mutates published facts.

## ParishSoft refresh reconciliation

Snapshot promotion performs these effects transactionally:

- recompute each Family's match with the campaign's [Families rule](#campaign-families), and active Members, using shared ParishKit
  predicates;
- generate campaign identities for newly Portal-eligible Families, regardless
  of email eligibility;
- revoke access for Families that no longer match the rule without deleting
  prior history;
- restore the existing code if a Family matches the rule again;
- recompute eligible head email addresses and current deliverability after
  applying provider-suppression records;
- store ParishSoft's new value on each open proposal; one that now matches
  upstream keeps its stage, To review, Ready or To enter by hand
  ([#1890](https://github.com/epiphany40223/parishkit/issues/1890): the review pipeline's
  [rule 1](../review-pipeline/spec.md#baseline-refresh-re-evaluation); until
  then it was resolved upstream), except one queued for an update run,
  which is still resolved upstream;
- mark three-way conflicts;
- keep, unchanged, an open census change whose Member is no longer an
  active, living Member of its Family (the review pipeline's
  [rule 4](../review-pipeline/spec.md#refresh-checks)), since publication
  refuses such a Member ([review and publication](#review-and-publication));
- nothing for an open Ministry request whose requested roster state is
  now current: it keeps its stage (#1890; until then it was resolved);
- re-evaluate final census changes and decided Ministry requests against
  the new data, recording a refresh check for each whose outcome changed
  ([refresh checks](../review-pipeline/spec.md#refresh-checks)).

Promotion plans no mail itself. A Family that becomes active, eligible or
deliverable changes its row's planning columns, which the
[Family schedule sweep](../background-processing/spec.md#family-schedule-sweep)
sees and plans again under the
[invitation rules](../background-processing/spec.md#family-invitations-and-reminders).
An Administrator who clears a recipient refusal outside promotion
(`jobs/delivery_admin.py`) changes deliverability the same way, and the
sweep picks it up. Stable occurrence and semantic-fulfillment keys make
repeated planning harmless.

When `restore_review_required` is active, that request is durable deferred
intent only: promotion does not materialize or dispatch an ordinary invitation.
The restore release is refused while any such Family's already-due invitation
still needs a hold, so it is held, and can be decided, before normal work
admission opens.

If a reactivated Family already has a live submission, it remains a responder
and does not receive a new initial invitation. Current metrics exclude Families no longer in the campaign;
historical activity retains submissions made while eligible.

## Review and publication

The Admin review UI is field-oriented and defaults to unreviewed changes. It
supports stable sorting, filters, pagination, row selection, select-all-current-
filter, bulk approve/ignore/reset, and inline proposed-value edits. Bulk actions
must display exact affected counts and cannot operate on records outside the
current authorization/filter snapshot.

`Update ParishSoft` performs an asynchronous mandatory preflight:

1. In a short transaction, create the preflight record with the configured
   expected-organization and promoted source-version identifiers. Preflight
   does not claim the mutation lease.
2. Validate the upstream organization without cache and fetch uncached current
   contact payloads for every affected entity without holding a database lock.
   A change whose Member the promoted data no longer lists as an active,
   living Member of the change's Family (moved to another Family, inactive,
   deceased or gone) is left out of the plan: it stays open and unplanned,
   and the Member is not read for it, so a change a Family made is never
   written to a Member of another household
   ([#1738](https://github.com/epiphany40223/parishkit/issues/1738)). A
   Member with changes from two Families plans only those of the Family
   they are in now.
3. Re-evaluate each approved field against baseline/proposed/current. After
   an Administrator resolved a conflict with *Use the Family's value anyway*
   ([reports](../reports/spec.md#automatic-census-review), ADM-09.03), the
   ParishSoft value they accepted takes the baseline's place. That accepted
   base is recorded on the immutable review row with the source snapshot it
   came from, and counts only while that snapshot is still the proposal's
   comparison source and no later decision review (Back to To review or
   Ignore, which also returns the change to `conflict`) has undone it; a
   later edit keeps it. Reconciliation moves the comparison source whenever,
   and only when, ParishSoft's value changes, so the base goes stale by
   itself. An approval resting on a stale base returns to `unreviewed` while
   the change stays open (a queued change keeps it, and its plan meets a
   conflict), and a same-intent Family resubmission carries neither the base
   nor such an approval.
4. Mark already-matching fields Updated in ParishSoft: in the transaction
   that records a Production-mode plan, each approved change whose field
   row says `already_in_parishsoft` moves from `pending` or `failed` to
   `published` at the next version, its decision and values unchanged, and
   nothing is written for it. Until [#1890](https://github.com/epiphany40223/parishkit/issues/1890) (forward migration 0159,
   provisional number) it moved to `resolved_upstream`, Confirmed at once;
   now the refresh confirms it after the step, as it does every update
   ([rule 1](../review-pipeline/spec.md#baseline-refresh-re-evaluation)). A
   Testing-mode plan marks nothing. This rests on the plan's uncached read
   rather than a promoted snapshot; the plan's field row is its record, and
   the final refresh brings the snapshot level. It is also the only path that
   recognizes an Administrator's edited value already in ParishSoft, which
   reconciliation, comparing with the Family's answer, would call a
   conflict. Back to review on Census changes undoes the mark with a
   required note (#1840,
   [undoing a terminal stage](../review-pipeline/spec.md#undoing-a-terminal-stage)):
   the derived-proposal guard admits the web role's paired `reopened`
   resolution, which returns the change to `pending` with its decision
   kept; nothing is written to ParishSoft. Because it
   rests on one uncached read whose value decoding is provisional until
   the Member contact round trip is confirmed, it can happen only after
   that confirmation: the switch, which must be on before any check runs,
   is turned on only after the read is confirmed (the
   [publication guide](../../../guides/stewardship-member-requests.md#parishsoft-publication)).
   The derived-proposal guard admits it only from the worker, with the
   comparison source unchanged, for a field row with that outcome at the
   proposal's current version in a non-Testing plan created in the same
   transaction; a PostgreSQL test proves that "same transaction" check
   concretely (the plan row's `xmin` is the current transaction's id, or
   its creation time equals `transaction_timestamp()`), and that a plan
   committed earlier is refused. The confirmation then checks such a row as
   `published` (`resolved_upstream` for a plan marked before #1890) at the
   next version instead of refusing the plan.
   The worker's pre-PUT comparison (`matches_confirmed`) leaves out exactly
   the plan's `already_in_parishsoft` field rows whose proposals this check
   moved to `published` (or, before #1890, `resolved_upstream`) at the
   version after the one the row recorded; any other planned row that left the open states still
   invalidates the entity, without relying on a later step to catch it.
   This ships with a new forward migration, which replaces
   `stewardship_response_derived_guard_v1` and
   `stewardship_publication_confirmation_guard_v1` (neither SECURITY
   DEFINER) with a self-verifying DO block, under the
   [post-launch schema policy](../operations/spec.md#post-launch-schema-policy)
   ([Update ParishSoft](../reports/spec.md#update-parishsoft)).
5. If any field the API would write on an entity is a three-way conflict,
   block all writes to that entity and return it for re-review. A field
   reported *By hand* (step 6) is never evaluated, so it never blocks.
6. Build each entity's PUT body from its approved changes only: each
   approved, Automatic change with a non-blank value the recorded PUT schema
   accepts, under the provider's field name. Fields that are unchanged, not
   approved, by hand or not read are never sent, so ParishSoft keeps their
   current values. The live write test observed partial updates on both v2
   contact PUTs (`nickName` on a Member, `primaryPhone` on a Family: a field
   left out of the body was left unchanged) and, on `dateOfDeath`, `null`
   ignored and `""` refused with HTTP 400; the API is taken to be unable to
   clear any field. So a blank value (empty, or only whitespace) is
   reported *By hand* and never planned, and so is a value the recorded
   schema would refuse (a phone under ten characters, an over-long name),
   since the API would reject the PUT
   ([#528](https://github.com/epiphany40223/parishkit/issues/528),
   [#1600](https://github.com/epiphany40223/parishkit/issues/1600)). As
   defence in depth for the [handling registry](#proposed-changes), a change
   the registry no longer marks API-writable is reported *By hand* and never
   planned too, whatever its stored handling says, so a gender change is
   never planned and its read is never compared, whatever code ParishSoft
   returns ([#1603](https://github.com/epiphany40223/parishkit/issues/1603)).
   Only `approved` decisions are planned. An approved proposal that
   reconciliation already holds in `conflict` counts as a conflict until it
   is re-reviewed, and one whose field the fresh read lacks cannot be
   checked, which blocks its entity; a read that lacks only other fields
   does not. The plan also records the record expected after the PUT: the
   fresh read with the written fields applied, each under the field it is
   read back from. A Member contact read uses the request's names; the
   Family PUT's `primaryPhone` reads back as `familyHomePhone`, so Family
   fields need an explicit read-back map before any is planned.
7. Persist the plan with the recorded source version and source-payload digests,
   present counts and conflicts, and require final confirmation before
   queueing writes. The plan also records each
   planned proposal's version, which any decision or Administrator edit
   changes; confirmation requires every version unchanged and the counts
   shown equal to the plan's, and a Testing-mode plan is never confirmed.

Execution claims `SourceMutationLease` and, immediately before each entity PUT,
revalidates its fencing token, confirms the system is still in Production mode
(a switch to Testing after confirmation stops every remaining write; the
database also refuses to queue an entity's changes outside Production mode,
though no guard can refuse the PUT itself),
fetches the uncached contact record, and repeats
canonical merge/conflict evaluation against the confirmed plan digest. As it
queues the entity's changes it also confirms the current promoted data still
lists the Member, active and living, in each written change's Family (a
refresh may have promoted since the preflight); an entity none of whose
changes it has queued yet is checked before its read, so such a Member is
not read either. Any
difference invalidates that entity without writing and returns it for
confirmation; a conditional-write primitive is used when ParishSoft exposes
one. Publication then groups fields by entity and uses idempotent v2 `PUT`
operations with bounded shared retries. Each entity is verified by an uncached
read after write, which must equal the plan's expected record, so the fields
the PUT left out are checked as well as the written ones. Success marks its
fields published/resolved; failure records a sanitized error and leaves the
entity retryable without replaying successful entities. Only a failure where
the PUT may not have landed (a failed read, a refused, timed-out or
unavailable write, or a failed read back) is retried within the run. A read
back that answers with anything other than the expected record
(`verify_mismatch`) ends the entity's attempts: ParishSoft accepted the PUT
but did not apply it as planned (as it ignores `null`), so another attempt
would only send the same body again. The entity is then closed like any
other unfinished one, and its change goes back to `failed`
([#1604](https://github.com/epiphany40223/parishkit/issues/1604)). A resumed
run (a crash or retry before that closing read) sends no further PUT for an
entity that already has a `verify_mismatch` outcome: it goes straight to the
closing read. An entity's written proposals are `queued` from just before
its PUT until its outcome: `published` when verified, or, when the entity is
invalidated, back to `failed` (an attempt failed) or `pending`. Every entity a
run leaves without a published or invalidated outcome, and any left by a
crashed run once its plan's execution window has passed, is closed, so a
new preflight can plan it and it never blocks a later confirmation. One whose
changes the plan queued (its PUT may have landed) is read back first and
recorded published when ParishSoft holds the planned record; any other is
invalidated. A crash after a PUT is recovered by the next run's read, without
a second PUT. If the system switches to Testing mode or the
`parishsoft_publication` setting goes off after such a crash, the next run
refuses before any read or lease, so the crashed entity's proposals stay
`queued`. They stay that way until a run in Production mode with the setting
on (a recovery retry or an Administrator's rerun) reads them back and closes
them. This is safe: a `queued` proposal refuses every review and resolution
and no preflight plans it, so nothing can change it or plan a second write
while its PUT may have landed. Turning publication off never needs them
cleared first. After releasing the lease, a run that published at least one
entity requests one full refresh through the shared refresh request path,
as a manual refresh attributed to the Administrator who confirmed the plan,
carrying the run's correlation id and audited as following an update
(`census_publication_refresh_requested`), which coalesces with a full
refresh already waiting; it reconciles the

promoted snapshot under a new lease claim. A failed request leaves the
run's outcomes as they are and logs a WARNING, and the next scheduled
refresh catches up. Publication ships switched off;
the [publication guide](../../../guides/stewardship-member-requests.md#parishsoft-publication)
says what must happen before it is switched on.

Admins may publish any reviewed subset during or after an active campaign.
They need not finish review in one session. Later Family submissions supersede
unpublished proposals as appropriate but never rewrite publication history.

Only an Administrator publishes. Staff and Administrators record the
per-change decisions on the worklist
([Automatic census review](../reports/spec.md#automatic-census-review)). Only changes the
[handling registry](#proposed-changes) marks API-writable are ever written;
staff enter every other census change by hand from the
[Census changes](../reports/spec.md#pending-census-changes) worklist.
Pledges and share methods have no proposed changes: they reach ParishSoft
through the
[Financial stewardship detail](../reports/spec.md#financial-stewardship-detail)
report, and Ministry rosters through the
[Ministry follow-up](../admin-portal/spec.md#follow-up-workflows) requests.
Family home and mailing addresses are not written until a verified
ParishSoft address read exists, since without one publication can neither
detect a conflict nor confirm the write; until then they are worked by
hand. Every entity written records an audit event naming the fields, never
their values: the event's subject is the attempt's outcome row, whose
immutable plan rows name the fields. In Testing mode publication stops after preflight and writes
nothing. Before the first real write, a human-run smoke tool (outside CI,
reading credentials at runtime) writes one chosen test Member and reads it
back, and before the first Family address write it does the same for one
chosen test Family's address (the Family contact PUT); CI uses only
recorded request fixtures.

Publication and the refresh after it never touch Family codes or links: no
publish, refresh or email change revokes, rotates, reissues or re-sends a
Family's code or link. A changed email changes only where later mail goes.

## Retention and deletion

Live submissions, protected and policy-anchor normalized snapshot history,
lightweight source manifests, workflow history, email metadata/content, and
audit events are retained indefinitely by default. Unprotected redundant source
corpora follow the compaction policy in
[Source snapshot](#source-snapshot). Operational HTTP caches, export files
and their saved captures, and transient task payloads have bounded cleanup
policies defined by operations (for exports,
[point-in-time exports](../operations/spec.md#point-in-time-exports)).

The three exceptions are:

- test responses, Testing [Family engagement](#family-engagement) rows and
  their sensitive audit payloads are deleted in bounded batches during the
  gated Production-transition cleanup phase;
- `testing_override` outbox rows and sensitive delivery audit payloads are
  deleted during that cleanup together with their Testing-only
  ScheduleOccurrence and ScheduleFulfillment rows after producing the non-
  sensitive aggregate defined by the
  [Admin readiness workflow](../admin-portal/spec.md#production-transition);
  `operational` rows are retained under normal policy even when created while
  the global mode was Testing; and
- an Admin-approved campaign purge removes campaign-owned live detail through
  the guarded web workflow while retaining only a non-sensitive tombstone.

Imported past-campaign records (the aggregates of each archive workbook and,
optionally, the identifiers the renewal figure needs) are parish records,
outside every campaign's purge, kept until an Administrator removes the
year; removal keeps a non-sensitive tombstone (see
[campaign history](../campaign-history/spec.md#what-is-stored)).

No foreign-key cascade may accidentally remove shared parish/integration
configuration, another campaign, current Portal users, or backup metadata.
