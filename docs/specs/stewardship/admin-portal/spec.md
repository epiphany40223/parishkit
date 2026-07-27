# Stewardship administration portal

All administration functionality is rooted under `/admin/` and uses the custom
ParishKit interface; Django's stock administration site is not exposed as the
product UI. Authorization is defined by the [overview](../spec.md#actors-and-authorization)
and enforced on every view, partial endpoint, object query, job, and export.

## Login and denial behavior

`/admin/login` offers only "Sign in with Google." A successful Google callback
must provide a verified email and stable subject. The normalized address is
evaluated as follows:

1. If an exact address rule exists, use only its roles, including an empty role
   set as an explicit denial.
2. Otherwise, use the matching domain rule, if any.
3. Expand Administrator to include Staff and Ministry leader.
4. Deny access if no effective application role remains.

Authentication failure, unverified email, allowlist denial, no role, and
authorization failure use safe, specific-enough error pages without disclosing
the allowlist. Each page offers another Google login attempt and parish contact
guidance. Rate-limit and suspicious-login events are logged.

If bootstrap exists but setup is incomplete, an Admin is routed only to the
setup wizard. A non-Admin sees "The system is not configured yet" and can only
log out/retry. Family routes behave similarly. Once configured, a successful
login returns to a validated local destination or the role-appropriate home;
open redirects are prohibited.

Logout revokes the application session and records an audit event. Idle and
absolute expiry follow the [architecture session policy](../architecture/spec.md#identity-and-session-security).

## Bootstrap and first-Admin wizard

The `pk-stewardship bootstrap` command runs once against an empty deployment.
It interactively or non-interactively obtains:

- public origin and deployment identifier;
- initial Admin email;
- Google OAuth client ID and client-secret file;
- Django signing/encryption secret files;
- database readiness and optional restore intent; and
- enough proxy/trust configuration for the Google callback.

It never collects campaign answers, prints secrets, or stores parish-specific
values in the image. It is idempotent when given identical values and refuses
to replace a configured deployment without a separate restore process.

On the first Admin login, the wizard collects all required base and first-
campaign configuration before making the system configured:

1. Parish name, website URL, IANA timezone, US main phone, and logo.
2. Domain/address login rules while preserving the bootstrap Admin.
3. ParishSoft API key replacement, expected organization, connectivity check,
   and a complete staged source load.
4. Google Workspace email service-account/delegated mailbox, sender/reply
   address, and test delivery.
5. Optional Slack token/channel and test notification.
6. First campaign name, modules, dates, Ministry/fund selection, financial
   period, share options, content, mail schedules, digest schedules, and test
   recipient.
7. Exact preview/readiness summary and final confirmation.

The staged ParishSoft load provides the Ministries/funds needed by later steps.
Wizard progress may be kept in the authenticated session and temporary staging
tables/files, but no durable product configuration is visible until final
commit. Cancel/expiry removes staged settings and credentials. Finalization
atomically installs staged credential files, commits configuration/snapshot,
generates Family codes, enters Testing mode, and records one setup audit event
with secret values redacted.

Restore is an operator command performed before bootstrap/wizard. A restored,
valid configured database skips initial setup after version/migration and
credential-reference checks.

## Navigation and home

Menus are capability-driven and never display inaccessible actions. Admins see
configuration, users, campaigns, source refresh, background work, reports,
reconciliation, logs, and operations. Staff see permitted reports and
workflows. Ministry leaders see assigned-Ministry reports and workflow queues.

The home page shows campaign state/dates, latest successful ParishSoft refresh,
next scheduled mail, participation summary, unresolved work counts, and recent
failures appropriate to the role. All pages show consistent breadcrumbs,
help/context, loading/empty/error states, and responsive layouts.

In Testing mode, every Admin page has a prominent persistent banner naming the
test recipient and linking to mode configuration. Staff/leader pages show a
smaller non-dismissible Testing indicator so report interpretation is clear.

## Background indicators

Admins have two always-visible indicators:

- **Active Families**: count of Family sessions with a heartbeat within the
  last 90 seconds. Detail lists Family display name, DUID, start time, last
  activity, and form section; it never shows answers or credentials.
- **Background work**: count/state of queued and running task runs. Detail shows
  type, initiator, start/heartbeat, phase, processed/total counts and percent,
  sanitized status, and links to completed/failed records.

Family clients heartbeat while a form is actively visible, at no more than one
request every 30 seconds. Expired/closed/ineligible sessions disappear. Worker
heartbeats identify abandoned runs; recovery behavior is task-specific.
Family heartbeat and Admin background-indicator polling are presence-only
requests: neither refreshes the authenticated session's idle-expiry timestamp.

## Parish and integration configuration

Only Admins may view/edit configuration. Required values cannot be cleared.
URL, timezone, phone, email, date, graphic, integration, and cross-field
constraints are validated before a new version is committed. Every save shows
a diff, records actor/before/after, and uses optimistic concurrency.

Logo management previews every generated size. Replacing a logo creates a new
branding version; historical email/page previews retain their campaign version.

Integration pages expose connection status, last check, safe fingerprint, and
Replace/Test actions. Secret replacement requires fresh Google authentication.
Failure leaves the old working credential installed. Slack is optional; its
token and channel must be supplied/removed together.

## Campaign configuration

Admins create a new draft by cloning selected safe values from a historical
campaign or starting empty. Cloning copies content/share/schedule structures
but not dates, Family codes, submissions, deliveries, workflow state, or fund
records without explicit remapping to current ParishSoft IDs.

New draft creation is unavailable while any campaign is draft, scheduled, or
active and while the global mode is Production. After the current campaign
closes or is archived, the Admin must complete the guarded return to Testing
before creating its successor. The server checks both conditions in the draft-
creation transaction; stale or direct requests cannot bypass them.

The campaign editor includes:

- modules and whole-local-day start/end dates;
- financial period and explicit current/comparison fund multi-select;
- campaign Ministry multi-select, initially all active Ministries;
- editable/reorderable share options with stable IDs and placeholders;
- initial and repeatable reminder date/time, subject, and templates;
- daily/weekly digest local schedules;
- additional-information toggle;
- named content slots with WYSIWYG/plain-text views;
- page/email preview using safe sample data or an explicitly selected Family;
  and
- Testing/Production controls.

At least one module is required. Census-only configurations do not require
Ministries/funds; analogous module-dependent fields remain hidden and invalid
when stray values are submitted. Reminder times follow initial mail and all
Family mail occurs within the open interval.

The UI labels structural settings and their lock trigger. After locking, the
server rejects structural mutations even if a stale browser exposes controls.
Content and future unsent schedules remain versioned/editable. Moving/removing
a schedule whose outbox work already exists cannot recall a sent message; the
UI shows delivered/queued counts before confirmation.

### Production transition

Going live is a dedicated workflow, not a toggle. It requires:

- valid, complete campaign configuration and no overlapping active campaign;
- recent successful full ParishSoft refresh and expected-tenant validation;
- successful Google email and optional Slack checks;
- valid Admin recipients, sender, templates/placeholders, links, and DNS/public
  origin;
- at least one preview and test Family mailing;
- a summary of active/eligible/no-email Families and live messages that will
  be due immediately;
- deletion of all Testing submissions/workflows and sensitive test audit
  payloads, with exact submission and distinct-Family counts, an Admin-only
  Family list for review, and explicit confirmation; and
- fresh Google authentication plus a typed Production confirmation.

Testing deliveries do not count as live. If a live occurrence is already due,
the scheduler catches it up once after transition. Transition failure leaves
Testing mode and test data intact unless deletion and mode activation can
commit together; no partial go-live is allowed.

The preview screen has an explicit readiness-test send that is available before
the campaign date interval. It renders a selected Family or safe sample, routes
only to the configured Testing recipient, does not create or satisfy a
scheduled Family-mail occurrence, and does not bypass Family portal date gates.
Its successful provider delivery satisfies the test-Family-mailing readiness
check, making the `scheduled` state reachable before the start date.

The transition changes the global system mode. Because only one campaign can be
active, the selected campaign is the sole target of the readiness calculation;
historical records keep their recorded mode.

Returning a live deployment to Testing is allowed to halt live delivery, but
requires fresh authentication, a warning listing affected queued schedules,
and audit. It does not turn existing live responses into test data.

### Reopen and archive

Extending a closed campaign into the future can reopen it only through a
readiness workflow equivalent to Production transition, excluding test-data
deletion. The UI lists new reminder implications and reactivated Family access.
Archiving is reversible only while the campaign has not been purged and cannot
occur with running publication/purge jobs.

## Portal user management

The Admin user page contains sorted domain and exact-address tables. Rows show
normalized value, effective roles, source, last login, and warnings. Role
checkbox changes autosave with a transient saved/error indicator; each request
uses an expected row version to prevent lost updates.

Domain rows expose Staff and Ministry-leader columns. Administrator is visibly
disabled. Creating `gmail.com` fails client and server validation. Address rows
expose all roles; an empty role set is clearly labeled Explicit deny rather
than appearing accidental.

The domain table labels its rules as Google Workspace/Cloud Identity hosted-
domain rules and explains that an email suffix alone never matches. Login-rule
detail shows whether successful Google sign-ins have presented the matching
signed hosted-domain claim, without exposing tokens. Personal or consumer-domain
users must be authorized by exact address.

Adding/removing a rule shows affected currently logged-in users and exact
address-over-domain behavior. Removing roles takes effect on the next request.
The last Administrator guard is enforced transactionally.

### Chairperson suggestions and assignments

After every source promotion, active Members with current Chairperson roles in
active Ministries are matched to valid normalized Member emails. Suggestions
show name, DUID, email, Ministry, whether contact is publishable, current login
rule, and current assignment.

Selecting suggestions creates/updates an exact address override with Ministry
leader role and explicit Ministry assignments. If the address inherited domain
roles, those roles are preselected because the exact rule replaces them.
Admins confirm before saving. Duplicate emails/Members/Ministries are grouped
and ambiguities shown, never silently guessed.

An assignments editor supports manual additions/removals. Losing a current
Chairperson role flags a seeded assignment as stale but does not revoke it.

## Manual ParishSoft refresh

Admins may request an immediate full refresh from a confirmation dialog. The
action inserts a durable task and returns immediately to its status page. If a
poll is running, no concurrent poll starts; one manual full refresh may be
queued to follow it. Repeated clicks return/link to the existing queued run.

## Follow-up workflows

Additional-information items show Family, DUID, text, submission time, needed
checkbox, followed-up checkbox/time, and Staff notes. Admin/Staff may search,
filter, sort, edit workflow fields, and see history. Marking followed up sets
the timestamp/actor; unchecking retains history and clears current state after
confirmation.

Ministry workflow permissions are row-scoped. Admin/Staff see all; leaders see
and edit only assigned Ministries. The interface supports queue filters,
assignee/status/outcome, contact-attempt entry, notes, bulk assignment, and
links to the Member's authorized report detail. It never exposes financial or
unrelated Family data.

Manual census items may be marked resolved externally or ignored by Admin or
Staff, with notes. API-writable changes are view-only for Staff. Admin review
and publication follow the [data workflow](../data/spec.md#review-and-publication).

## Logs

Only Admins access the combined log screen. It supports:

- levels DEBUG, INFO, WARNING, ERROR, and CRITICAL with accessible, distinct
  indicators;
- default exclusion of DEBUG;
- operational/audit source, action/type, campaign, entity, actor, task/request
  correlation, text, date range, and level filters;
- full-text search over approved indexed fields, never credentials;
- before/after detail for audit events; and
- text or structured JSONL export of the filtered result.

Stored timestamps are UTC. The screen renders browser-local timestamps. Export
requires choosing UTC or browser-local timezone; the chosen zone is recorded in
export metadata. Logins/logouts, configuration, polls/tasks, each email and
reason/recipient routing, report execution/export, errors, Family access,
submission changes, workflow changes, publication, and purge are recorded.

## Campaign purge

Campaign purge is available only to Admins through `/admin/operations/purge/`.
It cannot be invoked by ordinary deletion, API, or console command.

Only archived campaigns that are not running publication/export/purge tasks are
eligible. Draft, scheduled, active, and closed campaigns; parish configuration;
users; shared integration state; and the last restorable backup cannot be
selected. An Admin must finish reconciliation and explicitly archive a closed
campaign before it becomes purgeable.

The workflow has these required stages:

1. Select an eligible campaign.
2. Run a dry inventory showing campaign identity/dates, submission, workflow,
   email, source-version reference, report/media, and sensitive-audit counts.
3. Verify a successful encrypted off-host backup completed within the 24-hour
   RPO and store its immutable reference.
4. Explain irreversible effects and retained tombstone fields.
5. Obtain fresh Google authentication.
6. Require the exact campaign name and generated short purge phrase in separate
   confirmation fields.
7. Create one idempotent durable purge job.

The task first commits the campaign's `purging` state, making it inaccessible.
Database-owned rows are then deleted in bounded, resumable, idempotent batches;
each batch commits separately so a large campaign does not require one
long-running transaction. Shared/deduplicated source entities remain if
referenced elsewhere. A final transaction verifies the deletion inventory and
replaces campaign detail with the tombstone, so no partially deleted campaign
ever becomes visible. Associated generated files are deleted from an
idempotent manifest. A file cleanup failure leaves the campaign in
`purge_cleanup_failed` with a CRITICAL alert; retry continues cleanup without
restoring data and finishes in `purged`.

Completion replaces detail with a non-sensitive tombstone: campaign UUID/name,
date range, initiator, request/start/completion times, backup reference, deleted
counts, result, and optional reason. Sensitive before/after audit payloads owned
only by the campaign are removed. Admins are emailed on success/failure; an
inconsistent or exhausted cleanup also emits CRITICAL Slack when configured.
