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

Provider authentication failure and an unverified email may use distinct safe
error pages. Allowlist denial, no effective role, and any authorization denial
use one generic not-authorized page that does not reveal which check failed.
Each page offers another Google login attempt and parish contact guidance.
Rate-limit and suspicious-login events are logged.
Concrete per-IP, verified-identity, proxy, and deployment-wide limits are
defined by the
[identity security policy](../architecture/spec.md#identity-and-session-security).
All login and callback denial pages preserve the retry path while honoring
`429`/`Retry-After`; they never reveal which authorization check failed.

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
- Django signing/general-encryption, Family-code MAC, and email-link sealed-box
  keyring files;
- database readiness and optional restore intent; and
- enough proxy/trust configuration for the Google callback.

It never collects campaign answers, prints secrets, or stores parish-specific
values in the image. It is idempotent when given identical values and refuses
to replace a configured deployment without a separate restore process.
Bootstrap writes the deployment YAML and a minimal schema-valid Stewardship
bootstrap YAML version containing only the initial exact-address Admin rule.
After migration, the latter is imported as the first applied configuration
snapshot so the initial Admin can authenticate; the wizard supersedes it with
the first complete version.

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
tables/files, but no staged configuration is active until installer
finalization. While the bootstrap Admin remains on the correlated source-load
progress page, bounded authenticated polling renews only idle expiry under the
[session-policy exception](../architecture/spec.md#identity-and-session-security).
The page warns that closing it stops renewal and that the source-load watchdog
expires two hours after TaskRun creation even though the Admin session has a
later 12-hour absolute lifetime. It displays the idle, source-load-watchdog, and
absolute-session deadlines.

The two-hour watchdog is an intentional hard, non-extendable fail-safe. A normal
complete ParishSoft load is expected to take approximately two to three minutes;
reaching two hours indicates an unhealthy or stuck import that must be discarded
and diagnosed rather than resumed. The operator uses the task correlation and
redacted diagnostics to correct the underlying problem before restarting setup.

Cancel, idle expiry after polling stops, the two-hour watchdog, or absolute
expiry marks the staging set expired, safely cancels/abandons its load TaskRun,
and removes staged settings, source rows, files, and credentials idempotently.
The worker checks that state before external-page fetches and before promotion
to staging, so it cannot repopulate expired setup. At the watchdog deadline the
server refuses further renewal, requests cancellation, and cleanup proceeds at
the worker's next safe point; lease expiry handles an unresponsive worker.
Wizard staging is not resumable under a new login in the first release.
Finalization freezes the staged setup, runs each target-specific credential
installer, applies one complete authoritative YAML version through the
configuration installer, and then commits the promoted source snapshot, Family
codes, Testing mode, configured marker, and one redacted setup audit event. The
configured marker is last and cannot become visible until YAML/DB digests match
and every required consumer acknowledges its secret fingerprint. A crash or
failure resumes idempotently from installer checkpoints; before the marker,
normal routes remain unconfigured/fail-closed and cancel cleanup removes sealed
staging and any wizard-only files without exposing a partial product setup.

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
  sanitized status, and links to completed/failed records. A distinct Admin-only
  delivery warning links to unresolved `delivery_unknown` rows and exposes the
  reconciliation, evidence-note, delivered-resolution, and acknowledged-resend
  actions defined by the
  [delivery workflow](../background-processing/spec.md#family-invitations-and-reminders);
  it never displays credentials or sealed substitutions.

Family clients heartbeat while a form is actively visible, at no more than one
request every 30 seconds. Expired/closed/ineligible sessions disappear. Worker
heartbeats identify abandoned runs; recovery behavior is task-specific.
Family heartbeat and Admin background-indicator polling are presence-only
requests: neither refreshes the authenticated session's idle-expiry timestamp.
The distinct Family activity-keepalive behavior is defined by the
[session policy](../architecture/spec.md#identity-and-session-security); it does
not affect presence semantics or carry form answers.

## Parish and integration configuration

Only Admins may view/edit configuration. Required values cannot be cleared.
URL, timezone, phone, email, date, graphic, integration, and cross-field
constraints are validated before a new version is applied. Every save shows a
diff, records actor/before/after, uses the expected active YAML digest for
optimistic concurrency, and creates a `ConfigurationChangeRequest`. The page
shows **Applying**, **Applied**, or a safe validation/error result; it never says
Saved while only PostgreSQL or only YAML has changed. The dedicated installer
and fail-closed mismatch recovery are defined by the
[configuration architecture](../architecture/spec.md#configuration-and-secrets).

The Parish IANA timezone is the default for non-campaign presentation and newly
created campaign drafts. Editing it does not mutate an existing Campaign's
timezone, resolved boundaries, schedules, or historical report buckets. The UI
shows this scope explicitly and links to the separately editable draft campaign
timezone when one exists.

Logo management previews every generated size. Uploads remain staged until the
YAML version referencing their immutable branding version is applied;
historical email/page previews retain their campaign version.

Integration pages expose connection status, last check, safe fingerprint, and
Replace/Test actions. Secret replacement requires fresh Google authentication.
The UI seals the submitted value to its target-specific installer, shows
staged/testing/installing/consumer-acknowledged progress, and never redisplays
it. Failure or expiry destroys sealed staging and leaves the old working
credential installed. Slack is optional; its token and channel must be
supplied/removed together. Non-secret integration setting changes use the YAML
configuration-request path rather than the credential installer.

## Campaign configuration

Admins create a new draft by cloning selected safe values from a historical
campaign or starting empty. Cloning copies content/share/schedule structures
but not dates, Family codes, submissions, deliveries, workflow state, or fund
records without explicit remapping to current ParishSoft IDs.

Draft creation and every campaign-editor save apply a new authoritative YAML
version and its normalized PostgreSQL snapshot. Runtime lifecycle/mode fields
are changed only by their dedicated database transactions and are never edited
in YAML. Production readiness rejects pending/failed configuration requests or
a YAML/database digest mismatch and pins the exact applied version it locks.

New draft creation is unavailable if any campaign is draft, scheduled, active,
closed, `purging`, or `purge_cleanup_failed`. Independently, it is unavailable
while the global mode is Production. The Admin must finish reconciliation,
archive the current campaign, complete the guarded Return to Testing, optionally
complete an exceptional purge and cleanup, and only then create its successor.
The server checks all conditions in the draft-creation transaction; stale or
direct requests cannot bypass them.

The campaign editor includes:

- campaign IANA timezone plus modules and whole-campaign-local-day start/end
  dates;
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

The UI labels structural settings and their Production-readiness lock trigger.
After a `draft` campaign moves to `scheduled` or directly to `active`, the
server rejects structural mutations even if a stale browser exposes controls.
They unlock only through the guarded pre-start withdrawal below; an `active`
campaign never unlocks them. The campaign timezone is initialized from the
current Parish timezone, is editable in `draft`, and is one of these structural
settings. Scheduled, active, closed, and archived pages display it read-only.
Content and future schedules remain versioned/editable under the
[atomic schedule-replacement policy](../background-processing/spec.md#schedule-replacement-and-removal).
The confirmation shows successful, safely cancellable, failed, and blocking
in-flight/unknown counts. A sent message cannot be recalled; its logical
schedule fulfillment carries across revisions. Provider-submitting or
`delivery_unknown` work blocks the edit until it is resolved.

When a proposed end-date shortening would place future Family mail outside the
new interval, the campaign editor opens one combined reconciliation screen. It
lists every affected schedule and requires an explicit valid future replacement
or removal for each; the Admin cannot leave an item unresolved. The final
confirmation shows exact schedule, occurrence, outbox, cancellation, and
replacement counts. One transaction locks the Campaign, close occurrence,
schedule definitions/revisions, occurrences, and outbox rows; rechecks state and
provider uncertainty; and commits the new end date together with every selected
schedule change. Any failure rolls back the complete edit.

### Production transition

Going live is a dedicated workflow, not a toggle. It requires:

- valid, complete campaign configuration and no overlapping active campaign;
- a commit instant before the campaign closing instant, with the preview
  explicitly identifying whether the result will be `scheduled` or `active`;
- recent successful full ParishSoft refresh and expected-tenant validation;
- successful Google email and optional Slack checks;
- valid Admin recipients, sender, templates/placeholders, links, and DNS/public
  origin;
- at least one preview and test Family mailing;
- a summary of active/eligible/no-email Families and live messages that will
  be due immediately;
- every `testing_override` OutboxMessage in a terminal state (`delivered`,
  `permanent_failure`, or `cancelled`), with none `pending`, `submitting`,
  `retry_wait`, or `delivery_unknown`, plus a terminal-delivery summary ready
  for aggregation;
- a cleanup inventory of all Testing submissions/workflows, sensitive test
  audit payloads, Testing outbox detail, and Testing ScheduleOccurrence/
  ScheduleFulfillment rows, with exact submission, distinct-Family, and
  message/result counts plus an Admin-only Family list; and
- completion of the gated asynchronous cleanup below, followed by fresh Google
  authentication and a typed Production confirmation.

Testing deliveries do not count as live. After readiness and inventory, the
Admin explicitly acknowledges that cleanup is irreversible and starts it. One
transaction creates a durable ProductionTransitionRequest, acquires the
campaign go-live gate, records the inventory/aggregate described below, and
queues an idempotent cleanup task. The gate rejects new Testing submissions,
test sends, campaign content/configuration changes, and Testing campaign work;
existing authenticated pages explain that go-live is in progress. Source
refresh and operational notifications may continue.

The worker deletes the recorded Testing corpus in bounded, checkpointed batches
and exposes progress/retry in the web workflow. It rechecks the gate before each
batch and never touches production or operational rows. When cleanup completes,
the request becomes `cleanup_complete`; deleted rows are not restored if a
later check fails or the Admin cancels. Cancellation before activation releases
the gate and leaves the campaign in Testing with whatever cleanup completed.

From `cleanup_complete`, fresh authentication and typed confirmation invoke a
short final transaction. Under the request, campaign, and global locks it
recomputes readiness and compares its commit instant with the resolved half-open
campaign interval. Test-mailing and terminal-delivery requirements are checked
against the immutable pre-cleanup aggregate/readiness evidence because their
sensitive `testing_override` source rows were intentionally deleted; every
other readiness input is re-read from current durable state. Before start the
transaction moves `draft` to `scheduled`; from start
through the instant before close it moves `draft` directly to `active`; at or
after close it is rejected without a mode change. Either successful path changes
global mode to Production, records the readiness result, locks structural
settings, marks the request activated, and releases the gate atomically. Direct
activation materializes/catches up every already-due live occurrence under the
same overdue Family-mail/digest coalescing plan used for service recovery. The
readiness preview's immediately-due count is the post-coalescing provider-
message count, with coalesced semantic slots shown separately. Stable
idempotency/fulfillment rules prevent duplicate catch-up.

Cleanup or final-transition failure leaves global mode Testing and never creates
a partially live campaign. The UI states separately that completed cleanup is
not rolled back. Retry resumes from durable cleanup checkpoints or reruns the
short final transaction; cancelling releases the gate without restoring deleted
Testing data. Only the pre-start `scheduled` result offers **Withdraw from
Production**. That action
requires fresh Google authentication, an entered reason, and explicit
confirmation that Testing data deleted by the prior transition cannot be
restored. Under a campaign row lock, the server must verify that the state is
still `scheduled`, account for and safely cancel future live work under the
schedule-replacement policy, then atomically return global mode to Testing,
invalidate readiness, return the campaign to `draft`, unlock structural
settings, and audit the result. Provider-submitting or `delivery_unknown` work
blocks completion. If the start transition has won the race and the campaign is
already `active`, withdrawal is rejected. Going live again requires a complete
new readiness run, preview/test evidence, cleanup, reauthentication, and
confirmation.

Before the first cleanup batch, gate acquisition writes one non-sensitive
Testing delivery aggregate containing counts by message type and terminal
result, attempt-count totals, cleanup-start time, template-version identifiers,
and the configured Testing-recipient fingerprint. It contains no intended
recipient, Family/Member link, rendered content, provider message identifier,
or error detail. Batch deletion covers all recorded `testing_override`
OutboxMessage rows and sensitive delivery audit payloads. Operational
notification rows are excluded from both aggregation and deletion.

The preview screen has an explicit readiness-test send that is available before
the campaign date interval. It renders a selected Family or safe sample, routes
only to the configured Testing recipient, does not create or satisfy a
scheduled Family-mail occurrence, and does not bypass Family portal date gates.
Its successful provider delivery satisfies the test-Family-mailing readiness
check, making the `scheduled` state reachable before the start date.

The transition changes the global system mode. Because only one campaign can be
active, the selected campaign is the sole target of the readiness calculation;
historical records keep their recorded mode. Ordinary Testing-to-Production
transition cannot clear or bypass the independent `restore_review_required`
gate.

### Restore release

Restore release is a distinct state-aware workflow, not a reuse of the
`draft`-to-`scheduled` Production transition. While the restore gate is active,
the UI can queue only the restricted maintenance work defined by the
[restore specification](../operations/spec.md#restore). It shows each restored
campaign state and resolved date interval, source and integration evidence,
delivery-uncertainty inventory, proposed release state, and whether live Family
access/mail will resume.

After readiness succeeds, a freshly authenticated Admin confirms the exact
state-aware result. Under the current-campaign lock, the release transaction
recomputes boundaries at its commit instant:

- a `draft` campaign remains `draft` and the system remains Testing;
- a `scheduled` or `active` campaign becomes/remains `scheduled` before its
  start, becomes/remains `active` within its open interval, or becomes `closed`
  at/after its closing instant;
- a `closed` current campaign remains `closed` in Production so reporting,
  reconciliation, publication, and operational/digest routing retain their
  ordinary post-campaign semantics, but Family access and live Family mail stay
  disabled;
- an `archived` campaign that is still the current-campaign pointer remains
  `archived` in Production with the pointer intact, preserving its guarded
  unarchive eligibility and requiring the separate Return to Testing workflow
  before successor creation;
- historical `archived` and `purged` campaigns remain in those states and do
  not resume Family access or live mail; and
- `purging`, `purge_cleanup_failed`, an inconsistent request/Campaign pair, or
  any overlapping-current-campaign invariant blocks release for explicit
  operator recovery.

Any sole current campaign resulting in `scheduled`, `active`, `closed`, or
`archived` sets global mode to Production. A `draft` current campaign or no
current campaign releases into Testing; historical archived/purged campaigns
do not select mode. There is no supported current `closed`/`archived` Campaign
in Testing. In the same transaction
the system recomputes/materializes delivery holds, including holds for newly
visible Families whose initial invitation became due during restore, removes
segregated maintenance-test detail under the Testing cleanup policy, records
the chosen state and counts, and clears the gate. Normal worker admission then
resumes according to the resulting state/mode; partial release is prohibited.
Readiness checks for live sender/templates/test delivery and catch-up impact are
mandatory only when a campaign will resume `scheduled` or `active`. A `closed`
Production release instead checks the integrations and permissions needed for
its enabled reconciliation, publication, report, and digest work. Database,
schema, credential-reference, tenant, integrity, and uncertainty-inventory
checks apply to every release.

The restore preview identifies an archived-current-pointer backup as a distinct
case and explains that release does not perform Return to Testing. After
release, the Admin may still unarchive to `closed` or invoke the separately
reauthenticated Return workflow; restore never chooses between those lifecycle
actions implicitly.

Restore readiness displays the backup snapshot/release uncertainty window and
counts by campaign, schedule type, local due date, and hold state. Searchable
Family-level detail never shows credentials. Admins may leave holds unreviewed,
mark selected holds assumed delivered with an evidence note, or authorize a
resend after a duplicate-risk confirmation. Bulk actions show exact affected
counts and are audited. Assumed delivery suppresses that semantic occurrence
without increasing provider-success statistics; resend authorization creates a
new recovery attempt linked to the hold.

### Live delivery pause

An Admin may pause production delivery without changing global mode or Campaign
lifecycle state. The action requires fresh authentication, a reason, explicit
confirmation, and a preview of queued, submitting, delivery-unknown, and next-
due counts. It atomically sets the Campaign's durable delivery-pause control and
holds every production message that has not begun provider submission. Family
access and live submissions continue; their receipts are accepted but held.
Nothing is rerouted to the Testing recipient. Operational notifications and
explicit readiness/test-recipient sends remain allowed.

Messages already `submitting` may have reached the provider and
`delivery_unknown` messages retain their reconciliation workflow; the pause UI
states this limitation and tracks both. Workers recheck the pause immediately
before provider submission, so no later production attempt crosses the pause.
The campaign header and background-work view show a persistent delivery-paused
banner, duration, actor/reason, held counts/types, and provider-uncertain counts.

Resume requires fresh authentication, successful current provider/sender
health, an exact backlog preview, and explicit confirmation. Under Campaign and
affected-work locks, it applies the normal overdue Family-mail/digest coalescing
plan, cancels redundant pending outbox rows, records semantic coverage, clears
pause holds/control, and queues only selected messages in one transaction.
Submission receipts remain distinct and are all released. Failure leaves every
message held; stable fulfillment keys prevent duplicate delivery.

If the campaign closes while paused, invitation/reminder work is terminally
skipped and its pending outbox cancelled under the ordinary close policy.
Accepted receipts and completed-day Admin digests remain held. Before archive,
an Admin must use a freshly authenticated **Resolve held messages** workflow to
release selected non-Family-access message types after a provider check or
cancel them with exact counts and a reason. This does not reopen Family access
or campaign schedules. Once every held or uncertain row is resolved, the same
atomic workflow clears the durable pause control; closed campaigns do not use
the ordinary Resume action. Reopen readiness is blocked until the prior pause
and held-message state is resolved.

### Reopen and archive

Extending a closed campaign into the future can reopen it only through a
readiness workflow equivalent to Production transition, excluding test-data
deletion and the `draft`-to-`scheduled` state change. The proposed end date is
staged in that workflow and is committed only by the final reopen transaction;
it must place the commit instant inside the reopened half-open campaign
interval. The
single-current-campaign rule means a successor cannot yet exist; the server
nevertheless rechecks that no other campaign is `draft`, `scheduled`, `active`,
or `closed` and reports any inconsistent state rather than surfacing a database-
constraint error. The UI lists reactivated Family access, regenerated access-
link-token counts, and each
explicitly configured future mail occurrence. Fresh authentication and final
confirmation atomically rechecks the single-current-campaign guard, moves
`closed` to `active`, applies the proposed end date, preserves or asserts
Production, issues new Family access-link tokens, and enables Family access.

Reopen readiness also requires every `testing_override` OutboxMessage to be
terminal and inventories any Testing outbox, occurrence, fulfillment, workflow,
submission, and sensitive-audit detail. If such rows exist, the ordinary gated
Testing cleanup and immutable aggregate rules run before the final reopen
transaction even though no prior Production-transition test cleanup is assumed.

Reopen does not alter or unlock the original start date. Work that became due
and was durably skipped while the campaign was closed remains terminal and is
not caught up; an Admin must configure a new future reminder schedule when
contact is desired. A reopen failure preserves the prior closed Campaign,
global mode, and end date and leaves Family access disabled with no partial
token/schedule activation.

Archiving cannot occur with a live-delivery pause, held production messages,
provider-submitting or delivery-unknown messages, nonterminal production
outbox/schedule occurrences, or nonterminal publication, export, or purge work.

Archive preparation also inventories every outstanding receipt and required
daily/weekly digest semantic slot, including obligations whose scheduled due
time has not arrived and whose occurrence has not been materialized. It uses
the shared inventory defined by
[post-close reporting obligations](../background-processing/spec.md#post-close-reporting-obligations).
The UI shows the covered event/date range, due time, delivery state, and any
replacement covering a coalesced slot. Each obligation must be successfully
completed (including an audited empty/no-recipient outcome) or explicitly
resolved before archive. A failed delivery is not silently treated as resolved.

An Admin may select outstanding obligations to skip, review their exact
coverage, enter a reason, and confirm. This creates durable semantic skip
resolutions and safely cancels associated cancellable work; absent occurrences
are recorded as skipped without sending mail. Provider-submitting and
delivery-unknown messages must first follow their existing reconciliation
workflow and cannot be bypassed by this action. The UI distinguishes deliberate
skips from delivery success. Archive does not implicitly send early, skip, or
cancel reporting obligations.

The final archive transaction recomputes the inventory and verifies resolutions
under the Campaign and affected schedule/occurrence/outbox locks used by work
admission. Changed definitions, newly covered information/corrections, or a
worker claim invalidate a stale preview and require review of the new inventory.
Return to Testing rechecks this same inventory as part of archive quiescence;
the absence of materialized jobs alone never establishes readiness.

An Admin may return a Campaign from `archived` to `closed` only while it remains
exactly `archived`, remains the global current-campaign pointer, has no other
campaign in `draft`, `scheduled`, `active`, or `closed`, has no active purge
gate as defined by the
[purge data model](../data/spec.md#job-outbox-audit-and-purge-records), has no
conflicting campaign work, and after fresh Google authentication plus explicit
confirmation. The transition is audited and leaves Family access and schedules
disabled; reopening is still the separate readiness workflow above.

### Return to Testing after archive

Archiving does not itself change the global mode. Once the sole current
campaign is archived, the Admin portal exposes **Return to Testing**. This is a
dedicated web workflow; there is no ordinary API or console shortcut.

The workflow requires fresh Google authentication and a confirmation naming the
archived campaign. Its preflight transaction locks the global configuration and
campaign, then verifies that the campaign remains archived, has no purge gate,
and still satisfies every archive quiescence condition above. It also verifies
that no other campaign is `draft`, `scheduled`, `active`, `closed`, `purging`,
or `purge_cleanup_failed`. A failed check preserves the prior global mode and
links to the work that must be resolved.

On success, one idempotent transaction sets or confirms global Testing mode,
clears the current-campaign pointer even if the mode was already Testing,
invalidates campaign-specific Production readiness, and records the actor,
reauthentication time, campaign, preflight
counts, and before/after mode in the audit log. It neither changes the archived
campaign nor reroutes or recreates any historical production message. Draft
creation becomes available only after this transaction commits. Historical
reports remain selectable by campaign.
After the pointer is cleared, unarchive is prohibited; the archived Campaign is
historical. The UI presents a lifecycle checklist with two explicit next
choices: perform any exceptional purge now, or create the successor draft and
defer purge until the next post-archive window. It warns that successor creation
closes the purge window until that successor is archived and returned to
Testing.

## Portal user management

The Admin user page contains sorted domain and exact-address tables. Rows show
normalized value, effective roles, source, last login, and warnings. Role
checkbox changes autosave through a `ConfigurationChangeRequest` with a
transient Applying/Applied/error indicator; each request uses the expected
active YAML digest to prevent lost updates. A security-policy change is
effective only when the installer atomically activates its matching normalized
snapshot, never from an independently edited role row.

Every role addition, removal, or replacement—including an exact-address
Administrator grant—uses this autosave interaction. Role changes do not require
fresh Google authentication or a separate confirmation dialog. This is an
intentional low-friction administration policy. Each apply still
requires a currently authorized Admin session and CSRF token, re-evaluates the
actor's current login rule and role, enforces the last-Administrator guard, and
records the actor, target, before/after roles, timestamp, and request correlation
in the audit log.

The following high-impact expansions take effect immediately upon configuration
activation and also create, in that activation transaction, a durable
unacknowledged security event and independently queue an operational email to
every Administrator who existed immediately before activation:

- adding Administrator to an exact-address rule;
- creating any domain rule; and
- adding Staff to an existing domain rule.

Adding Ministry leader to an existing domain rule and ordinary exact-address
Staff/Ministry-leader grants retain the normal audit controls without this
security alert. The event names the actor, target address/domain, time, rule
creation or role expansion, and before/after roles without including session or
provider credentials. It remains prominent on every Admin dashboard until an
existing Admin acknowledges it; when another Admin existed at activation,
acknowledgement by the granting actor alone does not clear the event for those
other recipients. Delivery failure does not roll back or hide the expansion: it
follows durable operational retry/escalation, while the dashboard event remains
visible. Acknowledgements and notification outcomes are audited.

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
address-over-domain behavior. After activation, removing roles takes effect on
the next request. The last-Administrator and actor-still-authorized guards are
rechecked transactionally at request creation and activation.

### Chairperson suggestions and assignments

After every source promotion, active Members with current Chairperson roles in
active Ministries are matched to valid normalized Member emails. Suggestions
show name, DUID, email, Ministry, whether contact is publishable, current login
rule, and current assignment.

Selecting suggestions creates/updates an exact address override with Ministry
leader role and explicit Ministry assignments. If the address inherited domain
roles, those roles are preselected because the exact rule replaces them.
Admins confirm before applying the resulting YAML configuration request.
Duplicate emails/Members/Ministries are grouped and ambiguities shown, never
silently guessed.

An assignments editor supports manual additions/removals through the same YAML
configuration-request path. Losing a current Chairperson role immediately
suspends a `chair-seed` assignment as derived runtime state during source
promotion, removes its Ministry row scope on the next request, and creates a
persistent Admin review task/notification. Existing sessions are not trusted to
retain cached scope. If no other active assignment remains, the runtime
authorization overlay suppresses a Ministry-leader role that was added solely
for seeding without rewriting its applied YAML rule or changing Staff, Admin,
or independently configured roles. Permanently removing that configured role
requires an applied configuration request.

The suspended list shows prior Member/Ministry/source evidence, suspension
time, current source state, affected user/session, and role effects. An Admin
may revoke/delete the assignment or explicitly restore it as `manual` after
confirmation and an entered reason through an applied configuration request;
restoration never silently rewrites the source. If the same active Chairperson
relationship returns before a decision, the seed reactivates automatically and
closes the task with an audit event.

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

Log exports use the asynchronous export-job pipeline, authorization rechecks,
atomic file publication, purge admission gate, and temporary retention defined
for other large exports. Text and JSONL are additional formats of that shared
pipeline; an unbounded export is never assembled in a web request.

Stored timestamps are UTC. The screen renders browser-local timestamps. Export
requires choosing UTC or browser-local timezone; the chosen zone is recorded in
export metadata. Logins/logouts, configuration, polls/tasks, each email and
reason/recipient routing, report execution/export, errors, Family access,
submission changes, workflow changes, publication, and purge are recorded.

## Campaign purge

Campaign purge is available only to Admins through `/admin/operations/purge/`.
It cannot be invoked by ordinary deletion, API, or console command.

Only archived campaigns without an active gate from another purge request are
eligible; the authoritative gate states are defined by the
[purge data model](../data/spec.md#job-outbox-audit-and-purge-records).
Draft, scheduled, active, and closed campaigns; parish configuration; users;
shared integration state; and the last restorable backup cannot be selected.
Purge preparation also requires global Testing mode, a null current-campaign
pointer, and no other campaign in `draft`, `scheduled`, `active`, `closed`,
`purging`, or `purge_cleanup_failed`; an historical purge cannot be started
after successor preparation begins. The target cannot still be the current-
campaign pointer. An Admin must finish reconciliation, explicitly archive a
closed campaign, and complete Return to Testing before it becomes purgeable.
Existing campaign work is handled by the gate and quiescence stage below;
irreversible in-flight work can delay readiness.

The purge page and post-archive lifecycle checklist explain the recurring
availability window: after Return to Testing and before the next draft is
created. Outside that window the page remains viewable but names the blocking
campaign/state and does not offer request creation. The system does not imply
that an exceptional retention/deletion request can be executed mid-campaign;
operators must plan it for this window.

The workflow has these required stages:

1. Select an eligible campaign and transactionally create or resume its durable
   `draft` purge request and campaign-wide purge gate.
2. Display existing campaign work; cancel queued/retrying work at safe points
   and wait for running, provider-submitting, or delivery-unknown work to reach
   a reconciled terminal state. Record the resulting quiescence time.
3. Run a post-quiescence dry inventory showing campaign identity/dates,
   submission, workflow,
   email, source-version reference, report/media, and sensitive-audit counts.
4. Offer **Create purge backup** in the web workflow. It queues an asynchronous
   backup through the shared backup service, displays durable progress, and on
   success verifies and stores the immutable reference to the encrypted
   off-host backup. Its database snapshot must be at or after quiescence;
   partial, local-only, or unverified uploads do not qualify.
5. Explain irreversible effects and retained tombstone fields.
6. Obtain fresh Google authentication.
7. Require the exact campaign name and generated short purge phrase in separate
   confirmation fields.
8. Atomically advance the request to `queued` and create one idempotent purge
   task.

The UI displays and resumes the request states defined by the
[data specification](../data/spec.md#job-outbox-audit-and-purge-records).
Inventory, backup verification, and acknowledgement advance a `draft` request
to `ready_for_confirmation`; an expired inventory or backup returns it to
`draft` but invalidates only the expired artifact. The UI shows each artifact's
completion and expiration time and offers the corresponding refresh action. An
Admin may cancel through `queued` only while an atomic worker claim has not
occurred. Cancellation and terminal pre-deletion failure release the gate and
leave the archived Campaign eligible for a new request. Once deletion has begun,
the UI offers only status and safe idempotent retry actions, never cancellation
or rollback.

Inventory evidence expires 60 minutes after inventory completion, and verified
backup evidence expires 60 minutes after backup completion. Both must remain
valid when the final confirmation transaction commits. Any admitted conflicting
mutation or change to the quiescence checkpoint invalidates both immediately.
Expiration preserves completed task history and requires the Admin to refresh
only the expired artifact; an inventory that expires during a long backup is
rerun after backup completion without discarding that still-current backup. The
workflow never silently substitutes an older scheduled backup. A failed backup
leaves the purge request in `draft`, shows redacted failure detail, and permits
an idempotent retry without invalidating current inventory merely because the
backup attempt failed.

While the gate exists, every UI entry point that would create campaign-owned
work explains that purge preparation has paused the campaign and links Admins
to its status; direct requests receive the same server-side rejection. This
includes new exports, publication/reconciliation mutations, workflow-note
changes, and manual/retry task creation. Existing read-only detail and already-
generated downloads remain available. Their access is recorded as a parish-
owned retained security event with only the campaign UUID/tombstone reference,
under the [shared report policy](../reports/spec.md#shared-report-behavior), so
it does not mutate or invalidate campaign-owned purge inventory.

Worker claim first repeats the gate, quiescence, inventory, backup, and
last-mutation checks under row locks. Any mismatch performs no deletion and
enters `failed_pre_delete`; success atomically moves the request to `running`
and the Campaign to `purging`, making the Campaign inaccessible.
Database-owned rows are then deleted in bounded, resumable, idempotent batches;
each batch commits separately so a large campaign does not require one
long-running transaction. Shared/deduplicated source entities remain if
referenced elsewhere. A final transaction verifies the deletion inventory and
replaces campaign detail with the tombstone, so no partially deleted campaign
ever becomes visible. Associated generated files are deleted from an
idempotent manifest. A file cleanup failure leaves the campaign in
`purge_cleanup_failed` with a CRITICAL alert; retry continues cleanup without
restoring data and finishes in `purged`.

If the job fails after entering `purging` but before its first deletion batch
commits, it atomically marks the request `failed_pre_delete` and returns the
intact campaign to `archived`. Once any deletion batch commits, rollback to
`archived` is prohibited. An exhausted later database failure marks the request
`deletion_failed`, leaves the Campaign in `purging`, emits CRITICAL, and exposes
an Admin retry that resumes from committed checkpoints. File cleanup maps the
request and Campaign states as specified by the data model.

Completion replaces detail with a non-sensitive tombstone: campaign UUID/name,
date range, initiator, request/start/completion times, backup reference, deleted
counts, result, and optional reason. Sensitive before/after audit payloads owned
only by the campaign are removed. Admins are emailed on success/failure; an
inconsistent or exhausted cleanup also emits CRITICAL Slack when configured.
