# ParishKit Stewardship and Census Campaign

This specification set defines a web application for running a parish's annual
census, Ministry stewardship, and financial stewardship campaign. It is
normative: together with the linked subsystem specifications, it should be
sufficient to derive implementation epics, migrations, interfaces, and
acceptance tests without revisiting product decisions.

ParishKit's shared design, runtime paths, credential posture, and service-client
conventions remain defined by the [top-level ParishKit specification](../intro/spec.md).
ParishSoft API capabilities and limitations are defined by the
[API analysis](../../parishsoft-api-analysis.md). This specification overrides
the existing command-line tool convention only where an interactive web
application necessarily differs, as called out below.

## Goals and boundaries

One deployment serves exactly one parish. It supports many archived historical
annual campaigns, but exactly one campaign at a time is treated as the current
campaign through preparation, execution, close, and reconciliation. A
successor cannot be created until the current campaign is archived and the
deployment has completed the guarded return to Testing. A campaign may enable
any combination of census, Ministry stewardship, and financial stewardship,
with at least one enabled.

The application provides:

- a Google-authenticated administration portal under `/admin/`;
- a short, mobile-first Family response flow at `/`;
- repeated atomic synchronization from ParishSoft;
- personalized, scheduled Family email and administrative digests;
- live campaign reports and post-campaign workflows;
- controlled ParishSoft contact-data write-back where the API supports it; and
- durable, replayable operational and audit history.

The application does not process payments, modify ParishSoft Ministry rosters,
create ParishSoft Members or Families, or expose a public integration API. New
pledges and unsupported ParishSoft changes are resolved by report/export or a
human workflow. ParishSoft remains the source of truth; submitted answers are a
versioned proposal overlay until upstream data reflects them.

## Specification map

- [Architecture](architecture/spec.md): components, technology, security,
  deployment, configuration, and nonfunctional requirements.
- [Data and reconciliation](data/spec.md): durable records, snapshots, campaign
  lifecycle, merge rules, and ParishSoft publication.
- [Administration portal](admin-portal/spec.md): login, setup, configuration,
  authorization, workflows, activity indicators, logs, and purge.
- [Parishioner portal](parishioner-portal/spec.md): Family access, data-entry
  flow, validation, submission, and repeat visits.
- [Background processing](background-processing/spec.md): polling, mail,
  schedules, retries, task state, and error notification.
- [Reports and exports](reports/spec.md): report definitions, calculations,
  access, visualizations, and output formats.
- [Operations and quality](operations/spec.md): Compose environments, release
  images, backup/restore, observability, CI, and test expectations.

## Terminology

- **ParishSoft snapshot**: one validated, internally consistent version of
  upstream Family, Member, Ministry, roster, fund, pledge, and contribution
  data. A promoted snapshot is the application's current upstream truth.
- **Family** and **Member**: records identified by their ParishSoft DUIDs.
- **Portal-eligible Family**: an active, registered Parishioner Family. Email is
  not required; every such Family receives a campaign identity and manual code.
- **Email-eligible Family**: a Portal-eligible Family with at least one valid
  email address among the active Members returned by `get_family_heads()`.
- **Email-deliverable Family**: an Email-eligible Family with at least one
  normalized eligible-head address that is not currently suppressed after a
  permanent provider refusal. Deliverability changes when source addresses or
  suppression records change; it is distinct from syntactic eligibility.
- **Effective response**: the latest live submission version for a Family and
  campaign. Testing submissions are never effective live responses.
- **Proposed value**: the value in an effective response after applying any
  authorized review edit; it is not yet necessarily present in ParishSoft.
- **Current value**: the value in the currently promoted ParishSoft snapshot.
- **Baseline value**: the upstream value shown to the Family when the response
  version was started.
- **Local day**: midnight through the instant before the following midnight in
  the configured parish timezone. Timestamps remain UTC in storage.
- **Admin**: a user with the Administrator role. Administrator implies every
  other portal role.
- **Ministry leader**: the canonical name of the role called Minister in some
  source material. It is not an ordained-status designation.

## Actors and authorization

The application uses application-owned authorization after Google verifies an
administration user's identity. It does not delegate campaign RBAC to an
external identity-policy service.

| Capability | Administrator | Staff | Ministry leader | Family |
| --- | --- | --- | --- | --- |
| Configure parish/campaign/integrations | Yes | No | No | No |
| Manage login rules and Ministry assignments | Yes | No | No | No |
| View/export Family manual codes | Yes | Yes | No | Own code through login only |
| Trigger/view operational background work | Yes | No | No | No |
| Trigger/view own authorized report exports | Yes | Yes | Assigned Ministries only | No |
| View all campaign reports | Yes | Yes, except system logs | Assigned-Ministry reports only | No |
| View Family-level financial detail | Yes | Yes | No | Own Family only |
| Edit additional-information follow-up | Yes | Yes | No | Submit own text |
| Edit Ministry follow-up | All Ministries | All Ministries | Assigned Ministries | Submit own requests |
| Resolve unsupported census work | Yes | Yes | No | Submit own proposals |
| Review/edit/publish API-writable census work | Yes | View only | No | No |
| View system/audit logs | Yes | No | No | No |
| Purge an archived campaign | Yes, guarded workflow | No | No | No |

"Read-only" for Staff and Ministry leaders therefore has explicit operational
exceptions: Staff may maintain follow-up/manual-resolution state, and assigned
leaders may maintain their own Ministry follow-up state. Neither exception
allows editing a Family submission or configuration.

## Campaign lifecycle

A campaign has `draft`, `scheduled`, `active`, `closed`, `archived`, `purging`,
`purge_cleanup_failed`, and `purged` states. State transitions are explicit and
audited, although entering `active` and `closed` is driven by the configured
local dates once the campaign is live.

1. **Draft**: configuration is editable and Testing mode is mandatory. A draft
   cannot be created while another campaign is `draft`, `scheduled`, `active`,
   or `closed`; the prior campaign must be archived and the Admin must
   explicitly return the deployment to Testing.
2. **Scheduled**: Production readiness has passed, but the local start date has
   not arrived. An Admin may use the guarded pre-start withdrawal workflow to
   return it to `draft` and Testing.
3. **Active**: the half-open interval begins at 12:00:00 a.m. on the start date
   and ends immediately before 12:00:00 a.m. following the end date in the
   parish timezone. At that closing instant the campaign is `closed`; portal
   submissions and scheduled sends at exactly that instant are rejected.
4. **Closed**: Family access is denied, but reporting and reconciliation remain
   available.
5. **Archived**: the campaign is retained and read-only except for permitted
   workflow notes, guarded return to `closed`, and the guarded purge operation.
6. **Purging**: an archived campaign has entered the irreversible guarded purge
   job and is inaccessible except for Admin purge status.
7. **Purge cleanup failed**: database-owned campaign data is no longer visible,
   but generated-file cleanup needs an idempotent retry. The campaign cannot be
   restored to an earlier lifecycle state.
8. **Purged**: only the non-sensitive tombstone and purge audit metadata remain.

The only purge transitions are `archived` to `purging`; `purging` to `archived`
only if the job fails before its first deletion batch commits; `purging` to
`purged` or `purge_cleanup_failed`; and `purge_cleanup_failed` back through
cleanup to `purged`. After irreversible deletion starts, the campaign remains
inaccessible until cleanup succeeds.

The only unarchive transition is `archived` to `closed`. It is permitted only
while the Campaign is still exactly `archived`, has no nonterminal
`PurgeRequest`, and has no conflicting campaign work. It requires fresh Google
authentication, explicit confirmation, and audit. Unarchive does not enable
Family access, restart schedules, or enter Production; those effects require
the distinct closed-campaign reopen workflow.

The only reopen transition is `closed` to `active`. The Admin first extends the
closing date so the commit instant is within the resulting campaign interval
and passes the guarded readiness workflow. Reopen never returns to `scheduled`
or changes/unlocks the original start date. It atomically enters Production,
issues new Family access-link tokens, and restores Family access. Occurrences
that became due and were skipped while closed remain terminal; only explicitly
configured future schedules run after reopening.

Resolving either campaign boundary to UTC uses the scheduling DST policy: a
nonexistent local midnight moves to the first valid instant after the gap, and
an ambiguous local midnight uses its earlier UTC occurrence. The same resolved
instants gate portal access and local-day report buckets.

Structural settings lock when Production readiness atomically moves the
campaign from `draft` to `scheduled` before its start or directly to `active`
within its open interval: enabled modules, financial periods and fund mappings,
campaign Ministry set, identity/population semantics, and the campaign start
date. A Production transition at or after the closing instant is rejected.
Testing activity does not lock them. Administrators may continue editing
content, future unsent schedules, and the end date. An `active` campaign never
unlocks structural settings.

Before the resolved start instant, a freshly authenticated Admin may use an
explicit, confirmed **Withdraw from Production** workflow. It transactionally
locks the campaign, verifies that it is still `scheduled`, cancels all safely
cancellable future live work, changes global mode to Testing, invalidates the
readiness result, moves the campaign to `draft`, unlocks its structural
settings, and records the reason and complete audit event. Provider-submitting
or delivery-unknown work blocks withdrawal until resolved. A race that has
already moved the campaign to `active` is rejected. Previously deleted Testing
responses and delivery details are not restored; a later Production transition
must pass the complete readiness workflow again. Reopening a closed campaign
likewise requires readiness validation, fresh Google authentication, explicit
confirmation, and an audit event before its atomic transition to `active`.

Successor-campaign preparation is intentionally sequential. The administration
UI disables draft creation while another campaign is draft, scheduled, active,
or closed, and the server enforces the same rule transactionally. Closing a
campaign stops Family access and live schedules but leaves it as the sole
current campaign in Production while reporting and reconciliation finish. The
Admin must resolve remaining campaign work, archive the campaign, and then use
the guarded web workflow to return the deployment to Testing before creating a
successor draft. Archived campaigns remain available for historical reporting.

Temporarily stopping outgoing campaign email uses the Campaign's independent
live-delivery pause, not a transition from Production to Testing. An active
campaign remains active, Family access/submissions remain live, and production
messages are durably held until the guarded resume/coalescing workflow. Testing
mode is reserved for draft preparation, pre-start Production withdrawal, or a
deployment without an open live campaign.

All configured Family mail times must fall within the open campaign interval
and be chronological. Financial stewardship describes a period whose inclusive
end date is the day before the first anniversary of its start. An overlap
between campaign dates and that period is allowed only after a visible warning
and Admin confirmation.

## End-to-end sequence

1. An operator runs the minimal bootstrap command and starts Compose.
2. The initial Admin authenticates with Google and completes the transactional
   setup wizard, including an initial ParishSoft load and the first campaign.
3. Admins preview pages/mail, exercise Testing mode, and correct configuration.
4. A readiness workflow verifies integrations and deletes segregated test
   responses before moving to Production and truthfully selecting `scheduled`
   or `active` from the commit instant.
5. The system opens the campaign, sends idempotent scheduled invitations and
   reminders, refreshes ParishSoft, collects versioned responses, and reports
   progress.
6. Staff and assigned leaders perform follow-up while Admins review and publish
   supported census changes in batches.
7. The campaign closes; remaining Ministry, financial, and unsupported census
   work is exported/resolved and the campaign is archived.

## Global presentation rules

The administration portal is desktop-first but fully functional on tablets and
phones. The Family portal is mobile-first with equivalent desktop fidelity.
Both use one design system and parish branding, meet WCAG 2.2 AA, support the
current and previous major versions of Chrome, Edge, Firefox, and Safari, and
remain keyboard operable.

Application strings ship in English but use a localization framework from the
start. Admin-authored content may use any language. The application does not
claim a translated Spanish UI in the first release.

Displayed ordinary numbers use US grouping separators. Money is USD with two
fractional digits. Counts written as "X out of Y" also show a percentage;
percentages use one fractional digit unless they are exact integers. A zero
denominator displays an em dash rather than a misleading percentage.

All instants are stored as timezone-aware UTC. Browser-facing timestamps are
rendered in the browser timezone and include a timezone abbreviation in detail
views. Campaign dates, scheduled jobs, and report day buckets use the parish
timezone. Local-day conversion must handle daylight-saving gaps and folds
without running an occurrence twice.

## Requirement traceability

Every source requirement maps to one normative section:

| Source area | Normative specification |
| --- | --- |
| Initial setup, Google login, RBAC, config, users | [Admin portal](admin-portal/spec.md) |
| Deployment, database, sessions, responsive/security rules | [Architecture](architecture/spec.md) |
| ParishSoft refresh and atomic truth | [Background processing](background-processing/spec.md) and [data](data/spec.md) |
| Family email, reports by email, critical alerts | [Background processing](background-processing/spec.md) |
| Family login and census/stewardship form | [Parishioner portal](parishioner-portal/spec.md) |
| Reports, charts, filters, downloads | [Reports](reports/spec.md) |
| Review and ParishSoft publication | [Data](data/spec.md) and [admin portal](admin-portal/spec.md) |
| Logs and campaign replay | [Admin portal](admin-portal/spec.md) and [data](data/spec.md) |
| Compose, TLS, persistence, release, backups, tests | [Operations](operations/spec.md) |

When requirements conflict, the explicit decisions and definitions in this
specification set take precedence over the
[initial narrative](../../reference/stewardship-initial-prompt.md). In
particular, email links use opaque tokens rather than exposing the manual code;
the manual code uses eight letters from a confusable-free alphabet rather than
six alphanumeric characters to resist credential guessing; campaign history is
retained; Staff has the named workflow-write exceptions; whole local days
replace the earlier 12:01/11:59 wording; safety-critical operational
notifications reach all current Admins instead of using the Testing-recipient
override; and campaign purge is an Admin web workflow rather than an offline
command.
