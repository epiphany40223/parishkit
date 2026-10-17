# Stewardship administration portal

All administration functionality is rooted under `/admin/` and uses the custom
ParishKit interface; Django's stock administration site is not exposed as the
product UI. Authorization is defined by the [overview](../spec.md#actors-and-authorization)
and enforced on every view, partial endpoint, object query, job, and export.
The [Admin automation interface](../admin-automation/spec.md) reaches the
same actions and reads from the host command line, through the same service
functions, checks and audit; new Admin actions follow its
[rules for new Admin actions](../admin-automation/spec.md#rules-for-new-admin-actions).
Every Admin page follows the [UX conventions](../ui-conventions/spec.md),
which have precedence for general, portal-wide UX rules; this specification
keeps only the rules of its own pages and links the conventions it follows.

## Login and denial behavior

`/admin/login` offers only "Sign in with Google." A successful Google callback
must provide a verified email and stable subject. The normalized address is
evaluated as follows (#952):

1. If a [user](#portal-user-management) has that address, take the user's
   role. Roles are levels: Administrator includes Staff and Ministry leader,
   and Staff includes Ministry leader.
2. Otherwise deny access.

Sign-in is by email address only. There are no hosted-domain rules and no
explicit-deny entries: an address without a user cannot sign in, and the
signed Google hosted-domain (`hd`) claim plays no part in authorization.

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
setup wizard, and the Admin navigation offers only the wizard. The read-only
background-work list, task detail and header counts stay available so the
Admin can watch the setup's own tasks; their commands stay closed until setup
completes. A non-Admin sees "The system is not configured yet" and can only
log out/retry. Family routes behave similarly. Once configured, a successful
login returns to a validated local destination or the role-appropriate home;
open redirects are prohibited.

Logout revokes the application session and records an audit event. Idle and
absolute expiry follow the [architecture session policy](../architecture/spec.md#identity-and-session-security).

## Bootstrap and first-Admin wizard

Loss of the sole usable Google Admin account is handled only through
[offline operator recovery](../operations/spec.md#offline-admin-access-recovery),
not this wizard or a web login bypass. Its additive grant appears in normal
user management as an Administrator user, with the persistent recovery
security event; subsequent role edits retain the ordinary Admin policy.

The `pk-stewardship bootstrap` command runs once against an empty deployment
through the operator-only [offline bootstrap profile](../operations/spec.md#offline-bootstrap-profile),
which defines its limited provisioning mounts and startup exclusion.
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
bootstrap YAML version containing only the initial Administrator user (its
Name is the email's local part until it is renamed on the Users page). The
version uses the users bootstrap form (`bootstrap-users-v10`,
[#993](https://github.com/epiphany40223/parishkit/issues/993)), so setup
finishes in the users form and needs no
[users upgrade](../operations/spec.md#users-upgrade-from-sign-in-rules).
After migration, the latter is imported as the first applied configuration
snapshot so the initial Admin can authenticate; the wizard supersedes it with
the first complete version.

On the first Admin login, the wizard collects what the system needs to run
and let Admins sign in, and nothing about a campaign, before making the system
configured ([#142](https://github.com/epiphany40223/parishkit/issues/142)):

1. Parish name, website URL, optional HTTPS online giving URL, IANA timezone
   and US main phone.
2. Google Workspace email service-account/delegated mailbox, sender/reply
   address, optional From name, Testing recipient, and test delivery.
3. Optional Slack token/channel and test notification. The channel ID field
   is shown, and required, only while Slack notifications are enabled.
4. The parish logo.
5. The [users](#portal-user-management) who may sign in, while preserving
   the bootstrap Administrator: email addresses listed under Administrator,
   Staff and Ministry leader. Each address becomes one user with the highest
   role it is listed under, Named by its local part until renamed on the
   Users page. Access is by address only; no list grants a whole domain.
6. ParishSoft API key replacement, expected organization, connectivity check,
   and a complete staged source load.
7. Exact preview/readiness summary and final confirmation.

The campaign is created afterwards, from Home, by
[Create the campaign](#create-the-campaign). The wizard has no campaign
pages: the First campaign, Share options, Pages and emails and Dates and
mail schedules pages it had before #142 are gone, and their old addresses
are not kept (no redirects). Finishing setup creates no campaign, Families
or Family codes.

The [parish date format](../ui-conventions/spec.md#date-format) is not a wizard
step: setup starts with the default US long style, and an Admin changes it
afterwards in Parish settings.

The wizard presents these as one ordered sequence of pages, defined once in
code, and follows the general
[wizard rules](../ui-conventions/spec.md#wizards), whose Status says which
of them it meets. The Parish profile comes first, so the administrator starts by
describing their own parish. The Google Workspace and Slack credential pages
follow, each immediately after the public settings its staging depends on
(outgoing mail and Testing recipient for Google Workspace, Slack settings for
the Slack token), then Parish logos and Administrative access. ParishSoft
connection and Load parish data come last before the tests, next to each other
([#1353](https://github.com/epiphany40223/parishkit/issues/1353)): the load
can take a few minutes, so the rest of setup is done before the wait, and a
load that fails on a wrong key or organization is one step after the page
that fixes it. No data-entry page needs the loaded data. Like
[secret replacement](#parish-and-integration-configuration), the credential
pages require fresh Google authentication, but while setup is unfinished a
credential page accepts a sign-in up to **60 minutes** old, the length of the
Admin idle timeout, instead of five minutes
([#1402](https://github.com/epiphany40223/parishkit/issues/1402)), so the
Admin is not asked to confirm again on each credential page. The web check
(`SETUP_FRESH_SECONDS`) and the SQL guard on staged setup credentials
enforce the same hour, both counting a sign-in exactly 60 minutes old as
within it, and the guard admits it only for the setup attempt's original
owner and session while the attempt is collecting, in Testing mode with no
campaign. Each staged credential's audit entry records how old the sign-in
was, in seconds, when it was sealed. Every action after setup,
Integrations' secret replacement included, keeps the five-minute rule;
the idle and absolute session limits and the two-hour load watchdog are
unchanged. Finish setup asks for no fresh sign-in, before or after this
change. An older sign-in is offered "Confirm with Google", which keeps the
setup. The pages mention that rule only when
the sign-in is already older
([page help](../ui-conventions/spec.md#page-help)). The email and Slack
tests, Review and the final confirmation follow the load, in that order, so
Review is the last step before Finish setup and shows the tests' results
([#1476](https://github.com/epiphany40223/parishkit/issues/1476)). Every page shows a
compact progress stepper: the current step by number and name, the count of
completed steps and a slim [step bar](../ui-conventions/spec.md#step-bars)
whose segments are links to the steps that can open, named by step and
status with a tooltip, while a step that cannot open yet is a disabled
segment whose tooltip gives the reason
([#1402](https://github.com/epiphany40223/parishkit/issues/1402)), with the
full ordered list (each
applicable step named as completed, current, not done, optional or not yet
available with the reason), open on a wide screen and a collapsed disclosure on a narrow one ([step bars](../ui-conventions/spec.md#step-bars)). Navigation is
conventional: completed steps and the first unfinished required step are
links; later unfinished steps wait for every earlier required step, and
optional steps never hold later ones back. A step counts as completed only
after its own explicit save, credential, load, accepted test or, for the
review page, an explicit review for the current data with every earlier
step, the tests included, done; defaults seeded by
another page never complete a step. While the source load runs, completed
steps keep their status but are not links. The stepper is presentation only;
each page still enforces its own prerequisites. Each page has one identical
row of Back (secondary) and Save-and-continue (primary) controls (Save and
continue validates, saves and opens the next applicable page not done yet,
or the next page when every later one is done
([wizard rule 8](../ui-conventions/spec.md#wizards)), or redisplays
the page with its errors; on the Parish, Parish logos, Administrative access,
Outgoing email, Slack and Testing pages it is unavailable until every shown
required field is valid, with a short hint after the row saying what is
missing, as [prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)
describe; a logo file over 5 MB is refused at its field as soon as it is
chosen, as on Parish logos), a short introduction, and plain-language help for
every field. Revisiting a page shows its saved values; a credential page never
shows the saved secret (a typed key or token can be checked with the shared
[Show control](../ui-conventions/spec.md#secret-fields)) but says that one is saved (with its public scope, such as
the ParishSoft organization), lets it be kept by leaving the key empty
(unless it was entered for settings changed since, or the ParishSoft
organization ID is changed, when the key is required again), and
explains that it can be replaced until setup finishes and afterwards from
Integrations. A page whose prerequisites are unmet explains what is missing
and links the step that fixes it, keeping the HTTP status of the underlying
refusal; closed JSON errors remain for polling and command endpoints.

A refusal an Admin can correct, in the wizard or the campaign, content and
schedule editors, says what was wrong and how to fix it, with a link to the
page that fixes it when there is one (for example, a template that a mail
schedule still sends, a page changed in another tab, or a missing earlier
step). Where the form can be shown again, the explanation appears beside it and
keeps what the Admin entered; otherwise the error page shows it. The status
code is unchanged, scripts receive the same explanation as a `refusal` JSON
field, and the text is static and reviewed, never exception or submitted text.
Other refusals keep the closed, generic messages.

A report page that cannot be read because of a transient outage (database,
storage, configuration, limiter, or unavailable facts or snapshots) answers
`503` with `Retry-After` and the Admin error page "This report is temporarily
unavailable", never the Family sign-in denial. With debug logging enabled,
the swallowed exception behind any such closed response is logged at debug
level so operators can see what failed.

The setup email test sends one fixed, fictional message to the Testing
recipient from the configured sender and mailbox: the subject "Stewardship
setup test", and a short plain-text and HTML body that names the parish and
says its outgoing email settings work. It carries no Family data, link, code
or campaign content, and nothing in it depends on a campaign.

Test email, Test Slack (only when Slack is on), Review and Finish setup are
ordinary steps with the same action row, not a hub. Test email opens once
every required data-entry step is done and the load succeeded; it does not
wait for Review. On each test page the primary action sends the test until a
test of the current draft revision is accepted; then it is Continue to the
next step (Test Slack, or Review), with sending another test kept as a
secondary button. When the newest test of the current revision was not sent,
the test page says so after its action row, in plain language, with the
same advice about a rejected key as the provider's
[integration settings](#parish-and-integration-configuration), and links the
steps that fix it: Outgoing email settings and Google Workspace connection
for the email test, Slack notifications and Slack connection for the Slack
test. Saving a fix opens the next step not done yet (Google Workspace
connection when its key must be entered again, otherwise Test email), never
Review first; when that step's prerequisites are missing, the save opens the
step that needs work instead. Review's Readiness section says, for each
required test, whether a test of these exact settings was accepted, and
links the test step only for a test that was not; its body links no other
step. Review counts as done only when it is shown with every earlier step,
the tests included, done, and its primary action is Continue to Finish
setup. Any later save bumps the draft version, so Review and both tests are
required again; a changed mail or Testing setting also makes the staged
Google Workspace key stale. Back from Test email goes to Load parish data,
from Review to the last test, and from Finish setup to Review. Finish
setup's primary action is "Check readiness and finish setup", which still
requires accepted tests of the exact current revision; its links to the
tests stay for a test that went stale after Review.

The setup wizard's status pages (Test email, Test Slack, Load parish data and
Finishing setup) [update themselves](#background-indicators) like every other
Admin page that follows background work
([#1354](https://github.com/epiphany40223/parishkit/issues/1354)). Each draws
one live status region that it re-reads from a passive status fragment of
its own status address (`?format=status`). The fragment admits exactly the
sign-in its page admits and never renews idle time. On a test page, Send test
is answered in place, and the Recent tests list is watched while a test is
queued or being sent. While it is, Send (or Send another test) is
unavailable, named by a line after the action row saying a test is on its
way, and, while the page cannot check the test's status, by that status line
too ([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates),
[#1332](https://github.com/epiphany40223/parishkit/issues/1332)). Once the
test settles, the page re-reads its panel in
place, without moving focus, so Continue, Send another test or the
unknown-delivery acknowledgement come from the server. No page offers a
manual check or asks for a reload, and a page restored from the browser's
history checks again by itself: a settled test page re-reads its panel in
place on `pageshow`, so Continue never outlives a change to an earlier
step's settings. Load parish data re-reads its panel in place, and the
Loading parish data page reads its settled status region once more, whenever
the page is shown again (its tab shown, or `pageshow`); each also re-reads
the step bar in place. So neither says the parish data is loaded, nor shows
its step as completed, after the load was discarded from another tab
([#1353](https://github.com/epiphany40223/parishkit/issues/1353)). That
re-check of a settled status never retries and never adds a line under the
status: a failed read (setup finished meanwhile, say) stops it quietly and
leaves the page as it was. A status read the server refuses (signed
out, no longer allowed, or a bad request such as Slack being off) stops the
watching instead of being retried.

Acknowledgment checkboxes and incomplete forms hold their submit buttons as the
[prerequisite gates](../ui-conventions/spec.md#prerequisite-gates) describe.

Field errors and live checks follow the
[field error conventions](../ui-conventions/spec.md#field-errors-and-live-checks).

The mail schedule pages start with a short guide (in the About panel): what
each mail type is, that exactly one initial invitation is required before
go-live, that reminders and the daily and weekly Admin digests are optional (at
most one digest of each kind), that submission receipts and critical alerts are
sent automatically and never scheduled, the time zone send times are entered in
(this computer's on every schedule page, except the retired Copy campaign page,
which keeps the campaign's; see [time
entry](../ui-conventions/spec.md#time-entry)), and the current campaign dates. Wherever a schedule is
edited (the date-change review and the
New and Edit scheduled email pages below), its fields show only those its mail
type uses (initial invitation and reminder: date, time and email; daily digest:
time, email and the **Include Compared with past campaigns** checkbox, off by
default, which adds the
[comparison section](../campaign-history/spec.md#digest-section); weekly
digest: weekday, time and email) and offer only emails of
that type; the page script clears a field it hides. The send time is a [time
entry](../ui-conventions/spec.md#time-entry) field. A saved schedule's mail type is shown but cannot
change. The server reports a missing or inapplicable value on its own field (for
example a weekday on an invitation, or a date outside the campaign with the
campaign's dates); only rules between schedules, such as a second initial
invitation or a reminder before it, are page-level errors. On the date-change
review, "Add another schedule" adds blank rows (with
the same per-type fields) before saving, and a row added this way can be removed
again, so several schedules save in one submission and are validated together.

A schedule's **Email to send** list tells look-alike emails apart
([#446](https://github.com/epiphany40223/parishkit/issues/446)). Each email has
one readable name: its subject, plus the start of its ID only when another
email of the same mail type has the same subject. The list, the scheduled
emails table and [Pages and emails](#default-content) all use that name. Each
choice reads the name and the saved schedules that send it ("sent by Reminder
1, Reminder 2", or "not sent by any schedule", the same verb as the description
below it). Under the list, the chosen email is described in place: the start of
its plain text, the saved schedules that send it, and, when another schedule
sends it too, that editing it changes every one of them. On the New and Edit
scheduled email pages the description also links to the email's test page
(Preview and send a test) and its editor (Edit this email), each in a new tab so
the page's unsaved changes stay; an email saved there makes this page's review
ask for a reload, as any concurrent change does. Test emails can be sent only
while the campaign is a Testing draft, or in Production while live email
delivery is paused ([Live delivery pause](#live-delivery-pause)); at any other
time the description says so in place of the test link, and the test page
itself refuses with the same explanation and a link to Pause and resume mail
rather than asking for a reload
([#923](https://github.com/epiphany40223/parishkit/issues/923)). Choosing an
email still saves only the schedule's reference to it, exactly as before.

The regular page's list, its row actions and the New and Edit scheduled
email page are described under
[Dates and mail schedules](#dates-and-mail-schedules).

The staged ParishSoft load is a complete load of Families, Members, Ministries
and the fund catalog, with no giving window, as any load is before a campaign
exists; the campaign's giving arrives with the refresh that
[Create the campaign](#create-the-campaign) queues. Starting that load fixes
the Parish timezone for this setup attempt, so the source catalog keeps one
civil-date interpretation (the campaign created later has its own zone, see
[Create the campaign](#create-the-campaign)).
The Parish step explains this restriction and keeps other Parish fields editable.
Until a zone is saved, the Parish step's time zone menu starts on the browser's
zone when the list offers it (UTC counts as no zone, as on Create the
campaign), and otherwise on a blank required "Choose a time
zone" entry, never on the list's first zone. Its help mentions the browser's
zone only then
([#1330](https://github.com/epiphany40223/parishkit/issues/1330)).
Changing the timezone then requires cancelling and starting a new setup attempt;
it never silently reinterprets an existing source result.
Wizard progress may be kept in the authenticated session and temporary staging
tables/files, but no staged configuration is active until installer
finalization. While the bootstrap Admin remains on the correlated source-load
progress page, a bounded authenticated renewal POST, sent every 15 seconds
while the page is visible and the load is active, renews only idle expiry
under the
[session-policy exception](../architecture/spec.md#identity-and-session-security).
It is separate from the page's passive live status reads, and stops at the
nearest deadline or once a renewal says the load is no longer active.
Wizard pages do not list the time limits. The shared
[inactivity dialog](../architecture/spec.md#identity-and-session-security)
warns five minutes before sign-out and lets the Admin stay signed in, which
also keeps the setup attempt. The progress page names the three parts of the load
(download, save, check) and, during the download, each ParishSoft collection
as done, in progress (Ministry rosters as "N of M") or waiting, with its
record count; the saved-record bar appears once saving starts. The worker
encodes one step per finished collection in the task's two monotonic
progress counters and the page decodes the task's append-only event history,
so this needs no schema change. The page also shows when the load started and the
loading worker's most recent heartbeat, both counting forward between status
reads; an open Technical details stays open as they update. On success it shows a prominent
result with Continue to the next wizard page; on failure it says what to do
next.

A load that fails for good (for example, a wrong ParishSoft key or
organization ID, which never retries, or a load whose worker stopped on its
last allowed attempt, which recovery then fails) returns setup to collecting
with the failed load still bound, so every page is editable again and the
watchdog, which runs only while loading, stops (#1353). The test email and
test Slack message still require a load that succeeded. A load that has
finished, succeeded or failed, can be discarded, and only then (never one
running, waiting to retry or abandoned): saving a different ParishSoft key
or organization ID discards it, saving the same key and
organization keeps it, and Load again discards the load and queues a new
one. Load again is offered for a load that failed and for one that succeeded
but whose staged data is no longer valid by the check review uses (its
credential version or fingerprint changed, its exchange was scrubbed, or its
snapshot or fences no longer match); the Load parish data page then says the
data needs loading again, and the step is not done. Only a still-valid load
shows as loaded. Each discard is audited as
`setup_source_voided` with why (`key_changed`, `organization_changed` or
`reload`) and how the load had ended, never the key. A discarded load's
staging is never used again: finalization and campaign staging bind only to
the current load. It is disposed of with the attempt's other source staging
when setup ends, and the time zone the load fixed can change again.
The progress page of a load that failed for good names the most common cause
(a wrong key or organization ID) and links to ParishSoft connection and to
Load parish data, whose Load again starts a new load; Back goes to ParishSoft
connection. Only a load that ended with the setup attempt itself (expired)
still says to cancel setup and start again. A discarded load's progress page
stays readable, passively only (it is never renewed), and says the load is no
longer used, with a link to Load parish data and no Continue; a bound load
that succeeded but is no longer valid is shown the same way. While a
still-valid load exists, the ParishSoft connection page says that saving a
different key or organization ID discards the loaded data and that keeping
the saved key keeps it. The page says so in a static line in its saved-key
notice, so nothing moves as the key is typed
([no layout shift](../ui-conventions/spec.md#no-layout-shift)).

The two-hour watchdog is an intentional hard, non-extendable fail-safe. A normal
complete ParishSoft load is expected to take several minutes (about ten for a
parish of a few thousand Families and 200 Ministries);
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
Confirming with Google for a fresh-authentication step is a
[step-up of the same session](../architecture/spec.md#identity-and-session-security),
not a new login, so it keeps the wizard's staging.
Finalization freezes the staged setup, runs each target-specific credential
installer, applies one complete authoritative YAML version through the
configuration installer, and then commits the promoted source snapshot,
Testing mode, configured marker, and one redacted setup audit event. It creates
no campaign, so it creates no Family codes either. The
configured marker is last and cannot become visible until YAML/DB digests match
and every required consumer acknowledges its secret fingerprint. A crash or
failure resumes idempotently from installer checkpoints only within the original
Admin session's idle and absolute lifetime. Viewing finalization progress does
not renew them; the Admin renews idle time only through the inactivity dialog.
Expiry cancels unfinished setup, and a new
attempt requires cleanup and a new login. After confirmation, and whenever the
frozen attempt's owner opens the setup overview, the Admin sees a "Finishing
setup" page on the original login's cancellation route. It lists each step in
plain language (each credential's installation, the server operator's
acknowledgement with a count of acknowledged services, applying the
configuration, the final parish data load, and completion) with the time since
confirmation. Each step changes in place while setup finishes. Once setup
completes, the page shows a Continue link to the Admin home and drops Cancel
setup. A failed step stops the updates. The step-up notice below appears or
goes in place as it becomes needed. A failed step explains what to do: a credential the provider
rejected repeats the integration page's per-provider advice on what to check,
one that did not finish in time asks the operator to check the credential
installers, and each says to cancel and start a new setup (#456). When a credential cannot
start because the original Google sign-in is more than five minutes old, the
page offers the same-session step-up, which keeps the setup. Before the marker,
normal routes remain unconfigured/fail-closed and cancel cleanup removes sealed
staging and any wizard-only files without exposing a partial product setup.
Cancellation after YAML selection uses the
[initial-setup abort journal](../data/spec.md#parish-and-integrations), never a
rollback of applied configuration. Final database activation and the configured
marker share one transaction so cancellation cannot fall between those commits.

**Upgrading while setup is in progress.** A deployment upgraded to the
release that splits setup from campaign creation (#142) while a setup attempt
is still collecting or loading keeps that attempt. The wizard stops showing
the campaign pages; campaign, content, share option and schedule sections the
attempt already saved are never read again and are scrubbed with the
attempt's other wizard data when it completes or expires, and the attempt
finishes without a campaign. An attempt already confirmed (frozen) before
the upgrade finishes as it was confirmed: completion still accepts its
one-campaign shape, so its campaign and Family codes are created as before,
and Create the campaign never appears. In the
[local environment](../local-environment/spec.md#operator-script), resuming the
browser wizard behaves the same way.

Restore is an operator command performed before bootstrap/wizard. A restored,
valid configured database skips initial setup after version/migration and
credential-reference checks.

## Navigation and home

Menus are capability-driven: a role never sees an entry it may never open,
and the [Admin navigation](#admin-navigation) lists which roles see each
entry. An entry the role may open but that the campaign's state or the mode
does not allow right now stays in place, disabled, with its reason. Admins see
campaign setup, mail and the Family portal, reports, parish data, users and
system pages. Staff see permitted reports and workflows. Ministry leaders see
assigned-Ministry reports and workflow queues.

The home page is the Admin's starting point: it shows campaign state and
dates, latest successful ParishSoft refresh, next scheduled mail,
participation summary, unresolved work counts and recent failures appropriate
to the role, and links the next steps for the campaign's state, as
[Home page](#home-page) describes. All pages show consistent breadcrumbs,
[help](../ui-conventions/spec.md#page-help), loading/empty/error states, and responsive layouts.

In Testing mode, every Admin page has a prominent persistent banner naming the
test recipient and linking to mode configuration. Staff/leader pages show a
smaller non-dismissible Testing indicator so report interpretation is clear.

In Production mode, while the web process has debug logging in effect
(`PARISHKIT_DEBUG_LOGGING=1` and no debug-off switch set), every Admin page,
for every role, shows a prominent non-dismissible error banner saying in
plain language that debug logging must be off in Production, because debug
logs can hold personal data. It says that an Administrator can turn it off
now with [turn off debug logging](#turn-off-debug-logging) on System health,
and that the operator removes it for good by recreating the application
containers with the variable `0` or unset. It is a warning only: no process
refuses to start with the switch on. The banner reads only the web process's
own effective state, so it ends once the web process applies the debug-off
switch (within about a minute). For Administrators it links the debug
logging panel of [System health](#system-health), which shows every
service's state; another service still logging in debug form stays a
problem on System health and Home until it reports debug logging off
(ADM-13 PR 6c decided this: reading every service's status record would add
a query to every Admin page).

Every Admin page also shows a critical-problems banner while CRITICAL
operational events from the last 24 hours are unacknowledged. It names each
kind of problem in plain language with its count (for example "ParishSoft data
refresh failed (2×)"), links to the [log screen](#logs) filtered to CRITICAL
operational entries from that window, and offers Acknowledge to Administrators
(the System logs capability). One acknowledgement is shared: it records each
CRITICAL entry the rendered banner counted and the acknowledging Administrator
in append-only rows with one audit event, hides those entries for every Admin,
and changes no log entry. The banner's form carries a signed list of those
entries' ids (at most 500, oldest first; any beyond stay counted and remain
after the acknowledgement), and an altered list is refused, so Acknowledge
never hides an entry the Administrator was not shown. A SQL guard (#389)
admits an acknowledgement only from the web login, only for an existing
CRITICAL entry and with the acknowledging Administrator named, so none can
be recorded in advance for an entry that does not exist yet. Any other CRITICAL
entry, including one recorded after the page was shown or one a long-running
transaction commits after the acknowledgement, brings the banner back. A
problem that has ended reads "ended" with its browser-local end time, and the
title says when every listed problem has ended, so a past outage does not read
as a current one: a kind of problem has ended once the operational intake has
taken in each of its counted entries and every incident they opened has
[resolved](../background-processing/spec.md#what-went-wrong-and-recovery)
(#633). The banner costs the Admin page one query, shared with the delivery
warning count. It is distinct from security-event acknowledgement, which is per
recipient. Acknowledge acts [in place](../ui-conventions/spec.md#in-place-controls): the server still
answers with Home, and the banner is taken from that answer on the page the
Administrator is on, which keeps its address and everything else on it.
A System health action that resolves a problem also acknowledges that
problem's entries, as
[health problems on Home and in the banner](#health-problems-on-home-and-in-the-banner)
describes.

### JavaScript requirement

This general rule lives in the [UX conventions](../ui-conventions/spec.md#javascript-requirement),
which have precedence for it.

### Admin navigation

Admin pages share one layout: a sidebar menu beside the page, and a breadcrumb
trail above the page heading. Both come from a single declarative registry in
`accounts/admin_navigation.py`, keyed by URL name, that gives each Admin page a
menu group, a parent page and a label, so the menu and the trails cannot drift
and no template hand-writes a breadcrumb or a back link that repeats the trail.
Every Admin page follows the general
[navigation rules](../ui-conventions/spec.md#navigation-rules) of the UX
conventions; this section places the pages against them.

This section is the target structure from issue #522, written from a walk
through every Admin page (109 pages) and settled by the Administrator's
[navigation decisions](#navigation-decisions) of 2026-10-04. The sidebar is
moved to it, and the page names, links and URLs fixed against it, by the
follow-up issues #520 (one name per page), #521 (every page reachable, every
flow with a way back) and #525 (one URL scheme). Every page already uses the
table's name (NAV-4, NAV-5a and NAV-5b), except Portal users, which becomes
Users with the Users revamp (#952), which replaced the planned split into
Sign-in rules, Ministry assignments and Chairpersons (Emailed reports exists
since NAV-14). The System pages already have their new
addresses (NAV-6), and so do the Parish data pages and a change's status page
(NAV-7), the Mail and Family portal pages (NAV-8) and the Campaign setup
pages (NAV-9: settings, Copy campaign, content and its history, images,
schedules, Share options and Member talents; NAV-10: the test email pages,
the go-live chain, Production activation and Cancel go-live, and Campaign
Ministries under Ministries) and the report pages (NAV-11: every report, the
response lists (since removed, #933) and the Family timeline, with the
campaign choosers and the old Ministry reports root retired); the Campaign
setup, Mail and Family portal and Parish data group roots open their first
entry, and `/admin/reports/` keeps its meaning. Exports and the reports sent
by email follow (NAV-12): every export shares
`/admin/reports/exports/<request>/`, a latest-data export included, and the
daily and weekly reports sit under `/admin/reports/emailed/`. No old address
is kept ([#864](https://github.com/epiphany40223/parishkit/issues/864)), so
a link in a report email sent before the move no longer opens. The Users
and access pages moved with the Users revamp
([#952](https://github.com/epiphany40223/parishkit/issues/952)). The
sign-in, setup wizard and maintenance pages keep their addresses
(decision 27). Every ADM-12 pull request has landed or been dropped; see
the
[ADM-12 checklist](../../../tasks/stewardship/admin-portal.md#adm-12-admin-navigation-overhaul).

The Admin portal serves one current campaign; controls for more than one
campaign follow the [single-campaign interim](../ui-conventions/spec.md#single-campaign-interim)
rule ([navigation rule 10](../ui-conventions/spec.md#navigation-rules),
[decisions 18 and 19](#navigation-decisions)).

#### Menu groups

The menu is grouped by what an Administrator is doing during a campaign, in
the order a campaign runs: set it up, send it, read the responses, then the
parish data, users and system pages that change rarely. The sidebar starts with
Home, then these groups, each listing the entries the viewer's role may open:

| Group | Entry | URL | Roles | Unavailable when (the reason shown) |
| --- | --- | --- | --- | --- |
| (top) | Home | `/admin/` | Administrator, Staff, Ministry leader | Never |
| Campaign setup | Campaign settings | `/admin/campaign/settings/` | Administrator | No current campaign |
| Campaign setup | Pages and emails | `/admin/campaign/content/` | Administrator | No current campaign, or it is archived |
| Campaign setup | Campaign images | `/admin/campaign/images/` | Administrator | No current campaign, or it is archived |
| Campaign setup | Dates and mail schedules | `/admin/campaign/schedules/` | Administrator | No current campaign, or it is archived |
| Campaign setup | Share options | `/admin/campaign/share-options/` | Administrator | The campaign has no financial module, or it is not an unlocked Testing draft |
| Campaign setup | No-reminders WorkGroup | `/admin/campaign/no-reminders-workgroup/` | Administrator | No current campaign, or it is archived |
| Campaign setup | Go-live readiness | `/admin/campaign/go-live/` | Administrator | The campaign is not a draft (it points to Production activation) |
| Campaign setup | Production activation | `/admin/campaign/production/` | Administrator | Production has not been confirmed for this campaign |
| Mail and Family portal | Pause and resume mail | `/admin/mail/controls/` | Administrator | Testing mode |
| Mail and Family portal | Family email progress | `/admin/mail/family-progress/` | Administrator | No current campaign |
| Mail and Family portal | Family email history | `/admin/mail/family-history/` | Administrator | No current campaign |
| Mail and Family portal | Outgoing mail | `/admin/mail/outgoing/` | Administrator | Never |
| Mail and Family portal | Family portal availability | `/admin/mail/family-portal/` | Administrator | Never |
| Mail and Family portal | Families on the form now | `/admin/mail/presence/` | Administrator | Never |
| Responses and reports | Response dashboard | `/admin/reports/responses/` | Administrator, Staff | No current campaign |
| Responses and reports | Participation | `/admin/reports/participation/` | Administrator, Staff | No current campaign |
| Responses and reports | Campaign comparison | `/admin/reports/comparison/` | Administrator, Staff | No current campaign |
| Responses and reports | Financial stewardship | `/admin/reports/financial/` | Administrator, Staff | The campaign has no financial module |
| Responses and reports | Limitations | `/admin/reports/limitations/` | Administrator, Staff | The campaign has no Ministry module |
| Responses and reports | Census changes | `/admin/reports/census/` | Administrator, Staff | The campaign does not include the census |
| Responses and reports | Additional information | `/admin/reports/information/` | Administrator, Staff | No current campaign, or ParishSoft data has not been downloaded yet |
| Responses and reports | Ministry requests | `/admin/reports/ministries/` | Administrator, Staff, Ministry leader | The campaign has no Ministry module |
| Responses and reports | Active parishioner family directory | `/admin/reports/families/` | Administrator, Staff | No current campaign, or ParishSoft data has not been downloaded yet |
| Responses and reports | Emailed reports | `/admin/reports/emailed/` | Administrator, Staff | Never |
| Parish data | Parish settings | `/admin/parish/settings/` | Administrator | Never |
| Parish data | Parish logos | `/admin/parish/logos/` | Administrator | Never |
| Parish data | Ministries | `/admin/parish/ministries/` | Administrator | ParishSoft data has not been downloaded yet |
| Parish data | Hosted files | `/admin/parish/files/` | Administrator | Never |
| Parish data | Past campaigns | `/admin/parish/past-campaigns/` | Administrator | Never |
| Parish data | Refresh from ParishSoft | `/admin/parish/parishsoft-refresh/` | Administrator | Never |
| Parish data | Data to fix in ParishSoft | `/admin/parish/data-to-fix/` | Administrator, Staff | Never (a section that cannot count says why) |
| Users and access | Users | `/admin/users/` | Administrator | Never |
| Users and access | Signed-in sessions | `/admin/users/sessions/` | Administrator | Never |
| Users and access | Automation access | `/admin/users/automation/` | Administrator | Never |
| System | System health | `/admin/system/health/` | Administrator | Never |
| System | Integrations | `/admin/system/integrations/` | Administrator | Never |
| System | Background work | `/admin/system/background/` | Administrator | Never |
| System | System logs | `/admin/system/logs/` | Administrator | Never |

Notes on the groups:

- Every report has its own entry, so none is reached only through another
  report: the [response dashboard](../reports/spec.md#response-funnel),
  [participation](../reports/spec.md#campaign-statistics),
  [Financial stewardship](../reports/spec.md#financial-stewardship-detail),
  [Limitations](../reports/spec.md#limitations),
  [Census changes](../reports/spec.md#pending-census-changes),
  [Additional information](../reports/spec.md#additional-information),
  [Ministry requests](../reports/spec.md#ministry-change-summary) (one page
  with the Ministry [follow-up](#follow-up-workflows) queue since
  [#1133](https://github.com/epiphany40223/parishkit/issues/1133); the
  separate Ministry follow-up entry is gone), and the
  [active parishioner family directory](../reports/spec.md#active-parishioner-family-directory) (with Family
  campaign codes and its mailing columns). Reports always show the current
  campaign. Ministry leaders see only their own Ministries in the Ministry
  entry. Additional information and Ministry requests show their open
  counts ([#585](https://github.com/epiphany40223/parishkit/issues/585)): for
  Additional information, the current requests not yet marked "Follow-up
  completed" (its default view also lists completed ones); for Ministry
  requests, the total of its queue's default Unresolved view, read through that
  page's own selection with every filter it applies (only the leader's own
  Ministries for a Ministry leader); where that page would say the follow-up
  is unavailable, no number shows. Each is a plain number after the entry's
  name that screen readers announce as "(N open)". No number shows at zero,
  nor on a greyed-out entry. The counts are read when a page loads, never
  updated in place, so a number never appears or disappears under the
  pointer; a save that updates a page in place changes them on the next page
  load. Ministry follow-up has no assignment (#552): a request keeps its
  status, notes and outcome, and its Ministry leader handles it.
- **Emailed reports** is a new page listing the past daily and weekly
  [Administrator digests](../background-processing/spec.md#administrator-digests)
  the viewer may open (Staff see daily reports only, as today), newest first,
  so a digest page is no longer reachable only from its email. Sending a
  weekly report now is an Administrator action on this page and returns to
  it, linking the report it produced. Its button leads the page, above both
  lists, so a long list of daily reports never hides it; while the campaign
  has no Weekly Admin digest schedule (which the report needs) it is
  unavailable, with a short hint after it linking Dates and mail schedules.
  That link, and the one in Send a weekly report now's own missing-schedule
  notice, grey out with the menu's reason while the menu greys Dates and mail
  schedules, as for an archived campaign (#1261;
  [unavailable menu pages](../ui-conventions/spec.md#unavailable-menu-pages)).
- **Ministries** is the one home for Ministries: the parish's
  [ParishSoft Ministries](#ministries-page), with
  [this campaign's Ministries](#changing-a-live-campaigns-ministries) reached
  from it. Campaign settings links to the campaign's Ministries page; it is
  not a second home.
- Mail and the Family portal sit together, so pausing mail
  ([live delivery pause](#live-delivery-pause)) and closing the portal
  ([Family portal maintenance](#family-portal-maintenance)) are side by side.
  The header's background, delivery and presence counts stay as shortcuts to
  their entries.
- [Portal user management](#portal-user-management) is one **Users** entry
  with one table of users (#952). Each review started on it returns to it.
- Emailed reports is a new page: #520 and #521 add its registry entry and
  view.
- Each group's root URL (`/admin/campaign/`, `/admin/mail/`,
  `/admin/reports/`, `/admin/parish/`, `/admin/users/`, `/admin/system/`)
  redirects to the group's first entry available to the viewer, except
  `/admin/reports/`, which keeps its old meaning: Participation (even with
  no current campaign, when Participation's greyed page says why), or Ministry
  requests for a viewer who may not open Participation, and `/admin/users/`,
  which is the Users page itself. A group root with no entry available to
  the viewer redirects to Home.
- Until initial setup completes, the only menu entry is the setup wizard,
  which has its own stepper: nothing else works yet, so this is the one
  exception to a stable menu shape.

The menu always ends with **Sign out**, set apart from the groups, on every
signed-in Admin page (including during initial setup). It is a POST form with
the Admin CSRF token to the logout route, styled like a menu entry, because
logout is CSRF-protected. Pages carry no product footer.

Every Admin page's header, sign-in and setup included, ends with the
**Appearance** switch at its top right, which chooses the portal's light or
dark scheme for this browser
([colour scheme](../ui-conventions/spec.md#colour-scheme)).

The shared header holds a **Find a Family** search box (#561) for
Administrators and Staff only; Ministry leaders get none, since they cannot
open the [active parishioner family directory](../reports/spec.md#active-parishioner-family-directory). It uses the
directory's search query (the Family name with its heads of household, DUID
and address) and its permission checks, so results show only Families the role
may already see, and a result opens that Family's
[Family timeline](../reports/spec.md#family-timeline) (the summary only for
Staff). It searches by a CSRF-protected POST, never a GET, so the search text
stays out of URLs, logs and browser history, as the directory's code search
does.
Results appear in place under the box as the reader types: a search starts
after a short pause in typing and needs at least 2 characters, and a newer
search cancels an older one, so typing does not send a request per keystroke.
The box lists the first 8 matches, each with its DUID and envelope number, and a
"See all" button that opens the directory with the same search when more match.
A Family whose envelope number or DUID is exactly the search text comes first,
however far down the directory lists it; the rest follow in the directory's
order ([#712](https://github.com/epiphany40223/parishkit/issues/712)). Only the
box ranks this way: the directory and its exports keep their own order. The box
reads only the directory's first page, then looks the exact Family up and reads
it alone, so a Family without a campaign record keeps its place. A Family without a
campaign record is listed without a link, as in the directory. Each search
is audited as a directory view (that a search was used, and the row counts),
never its text. A search the
script cancels because a newer one started still runs to its end on the
server, so it is audited too: a few audit records per lookup is the accepted
cost of keeping one audit path with the directory. Down arrow moves
from the box to the results, Up and Down move between them, and Escape closes
them and returns to the box. Because it is the directory's search, it also
finds a Family by any active Member's name and by its envelope number
([#664](https://github.com/epiphany40223/parishkit/issues/664)). Its route
(`find_family`) is a non-page action.

Entries use the same capability checks as the pages they open, and a group
with no entry for the viewer's role is omitted; the menu is not the security
boundary. The entry for the current page, or for the nearest ancestor page
listed in the menu, is marked `aria-current="page"` and its group is
highlighted. On wide screens the sidebar is a sticky column that scrolls on
its own when it is taller than the window; on narrow screens it collapses
behind a Menu disclosure.

Each group is collapsible: a native `<details>` disclosure whose `<summary>`
text is the group's title, styled as a small muted label with a divider above
every group after the first and the group's entries indented beneath it. The
entry list is labelled by the summary (`aria-labelledby`). The title is not a
heading inside the summary, because some screen readers then drop the
summary's disclosure role or its heading. A browser test confirms each
summary exposes its name and its expanded or collapsed state, and a manual
VoiceOver and NVDA check confirms both are announced. The summary is a keyboard control (Enter or
Space toggles it) and shows an arrow for its state.
Groups start open. Each browser remembers which groups an Admin collapsed (only
the group key and its open or closed state, in browser storage, as the
[About panel](../ui-conventions/spec.md#page-help) does); with nothing stored, every group starts
open. The group holding the current page always opens, so the
`aria-current` entry is never hidden. Collapsing is the reader's choice and
does not change the menu's shape.

The menu keeps its scroll position when an Admin follows one of its links
(#620), so the entry just clicked stays at the same height on screen, under
the pointer, on the next page; the page content still starts at its top.
Following a link by click or Enter saves the link's address and its top edge
on screen, under one key in the tab's session storage. The next page reads
the value once and removes it, and only if the value names that page does it
scroll the menu so the same link (or, failing that, the current entry) sits
at the same height. Matching the link's position, not the menu's scroll
offset, holds when the page itself was scrolled or a group above the link
opened or closed. One case cannot be matched: after a click with the page
scrolled down, the new page's menu starts a header's height lower and still
reaches past the window's bottom, so an entry near the menu's end, where the
menu cannot scroll further, can land up to a header's height lower, possibly
below the window. The restore runs after the remembered groups are set and
before the page content is parsed, so the menu is in place before the first
paint where the browser allows. A modified click that opens another tab or
window, an unavailable entry and Sign out save nothing. When nothing usable
was saved (a reload, a new tab, a link from elsewhere), or the saved height
cannot be reached, the current page's entry is scrolled into the menu's
visible part only if it is outside it, and never by scrolling the page. On
narrow screens, where the menu is the Menu disclosure and not a scroll area,
nothing more happens. Blocked storage loses only this convenience, as it
loses the remembered collapsed groups. The position is never sent to the
server: a cookie or query string would leak interface state into requests
and URLs, and making the portal a single-page app was rejected as far
larger.

#### Stable menu shape

For a given role, the menu has the same groups and entries in the same order
in every mode and campaign state. Only the role hides an entry. An entry the
role may open but that is unavailable right now is greyed out: an `<a>`
without `href`, with `role="link"`, `aria-disabled="true"` and
`tabindex="0"`, so keyboard users reach it in tab order and activating it
does nothing. Its reason (for example "Available in Production mode" or "No
current campaign") appears in a small tip when the pointer rests on the
entry, when the entry has keyboard focus, and when it is tapped on a touch or
narrow screen; the tip stays while the pointer is over it or the entry keeps
focus, and Escape, or a tap elsewhere, dismisses it. The reason is also the
entry's accessible description (`aria-describedby`), so screen readers
announce it with the entry. The reason names what to do when there
is something to do. An unavailable entry's page, opened directly, shows the
same reason and a way forward
([unavailable menu pages](../ui-conventions/spec.md#unavailable-menu-pages)).
The conditions come from the campaign
state, modules and mode the chrome already reads, and from whether
ParishSoft data has been downloaded yet (#1242): one small query, run once
per request and only when an entry's condition needs it, for a viewer whose
menu has Ministries, Additional information or the directory. The other
exception is the open counts on Additional information and
Ministry requests ([Menu groups](#menu-groups)): one query reads both, only
when the viewer's menu offers either entry as a link, once per request.

#### Home page

Status: built (ADM-12.21, NAV-18). In order, Home shows its heading, the
System health problems, the campaign and ParishSoft status panels (with the
off-site backup line and the next planned Family mailing), My Ministries,
the security events, automation notices, Ministry catalog changes, Family
participation, the unreachable-Family count, recent failed background tasks
and, last, the **Next steps** panel led by the **Today** line.

Home is a starting point, not only a status page. Its heading is "Home"; the
parish name and the current campaign's name and state are its data line. Below
the status panels described above, Home shows a short **Next steps** list of
links chosen by the campaign's state and the viewer's role:

| State | Administrator | Staff | Ministry leader |
| --- | --- | --- | --- |
| Draft in Testing | Continue campaign setup (the Campaign setup stepper's first step not done yet, #1404), Campaign settings, Pages and emails, Dates and mail schedules, Preview and test email (for the initial invitation's current revision), Go-live readiness, Response dashboard | Response dashboard, Participation | Ministry requests |
| Production being activated | Production activation, Outgoing mail (while the campaign is still a draft, Go-live readiness leads) | Response dashboard | Ministry requests |
| Active | Response dashboard, Additional information and Ministry requests (with open counts), Family email progress, Outgoing mail problems (with count) | Response dashboard, Additional information, Ministry requests (with open counts) | Ministry requests (with open count) |
| Mail paused | Pause and resume mail first, then the Active list | As Active | As Active |
| Closed or archived | Participation, Emailed reports | Participation | Ministry requests |
| No current campaign | Create the campaign when the deployment has never had one; otherwise says so, with no next steps | Says so; no next steps | Says so; no next steps |

On a deployment that has never had a campaign (just after setup), Home's next
step for an Administrator is [Create the campaign](#create-the-campaign). With
no current campaign after an earlier one (after a purge or successor
preparation), there is no way to create one until the single-campaign change
(#145) or the close-out wizard (#527), since New campaign is removed
([decision 11](#navigation-decisions)). That dead end is accepted for now;
Home explains in plain language that there is no current campaign and that
the next one cannot be created here yet.

For a Ministry leader whose follow-up access is limited to particular
Ministries (not Administrators or Staff, who reach every Ministry from the
menu), Home also shows a **My Ministries** panel (#533). It says what it
counts: requests from Families to join or leave the leader's Ministries that
are still open (New or In progress) in the current campaign. It lists each
of the leader's Ministries in the campaign, and any Ministry since removed
from the campaign that still has open requests, marked "no longer in this
campaign" as the queue marks it. Each line gives the Ministry's open
requests to join and to leave, or "No open requests", and, when there are
some, an "Open requests" link to
[Ministry requests](../reports/spec.md#ministry-change-summary)
(`/admin/reports/ministries/?ministry=<DUID>`) with its queue filtered to
that Ministry. The panel's last line, "Ministry requests · Follow-up packet
for my Ministries", links the unfiltered page and the
[follow-up packet](../reports/spec.md#multi-ministry-follow-up-packet) form
at its foot ([#1133](https://github.com/epiphany40223/parishkit/issues/1133)). On
a phone each Ministry is one short block (name, counts, link), never a
squeezed table.

The counts are exact: each is the queue's own selection, with the leader's
scope and default filters, read for one Ministry and action without its
rows, so the panel never counts a request the queue would not list for that
leader. The link carries only the Ministry number (`?ministry=<DUID>`); the
queue accepts that closed value by GET
([follow-up workflows](#follow-up-workflows) lists what such a link may
carry), checked against the person's current scope (a number outside it is
refused), so Back and reload work.
The queue then remembers that view like any other, so its request links,
Save and next, and each request page's "Return to Ministry requests" link
keep that Ministry. Search and every filter other than those
[follow-up workflows](#follow-up-workflows) lists stay private POST state.
The command line's status read does not
compute the panel.

The Family participation panel is a row of tiles
([#720](https://github.com/epiphany40223/parishkit/issues/720)): the active
Families; the responses from Families who can use the Family form now, as
the count, "out of 400 (10%)" and a
[share bar](../ui-conventions/spec.md#charts); and all Families with a
response, not counting Testing. That last count can include Families no
longer active, so it is not drawn as a share. While in Testing mode, a line
under the tiles says Testing responses are not counted there.

The Next steps panel comes last, after every problem and status panel, and
opens with one **Today** line for each role
([decision 29](#navigation-decisions); contents confirmed on #522):
Administrators and Staff see how many Families responded in the past 24
hours (Testing responses while in Testing), and a Ministry leader with My
Ministries sees the open requests in those Ministries. The line does not
repeat the next planned Family mailing (in the campaign panel), the open
queue counts (on the Next steps links) or the problems (listed above).

Each next step is the viewer's own menu entry: a link with the page's name
while the menu offers it, the page's name and the menu's reason as plain
text while the menu greys it out, and left out when the viewer's role has
no such entry. A count follows the link as text. "Production being
activated" is a scheduled campaign, or Production preparation in progress;
"Mail paused" is Production with live mail paused, in any state not closed.
Outgoing mail problems are the emails that may not have arrived (the
header's "Not sure it arrived" count), and the step links Outgoing mail
filtered to them, counted as "N may not have arrived", or plain Outgoing
mail while there are none. Preview and
test email opens the initial invitation's first email and follows Pages and
emails' availability. With no current campaign there is no Next steps panel:
the campaign panel offers Create the campaign or says why there is none.
Building the list adds no query; Home reads the Today count on its Family
participation statement, and the invitation's email only for an
Administrator in a Testing draft.

Problems that need action (failed background tasks, unacknowledged security
events, out-of-date ParishSoft data as
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)
defines it, and the [System health](#system-health)
problems such as an overdue backup or a halted mail sender) stay above Next
steps and link the page that resolves them. A next step is a link only when
the viewer's menu entry for it is available.

#### Page names and placement

Every Admin page has one name
([navigation rule 4](../ui-conventions/spec.md#navigation-rules)). The menu entry, the breadcrumb, the page
heading, the browser title and every link or message that names the page use
it; link text may add a verb ("Return to Pages and emails"). A page that shows
one object (an email, an integration, an export, an error) is named after that
object in both its heading and its breadcrumb; those pages are the listed
exceptions in the table ("Object-named"). Setup wizard headings use the
stepper's label, and a wizard step uses the same name as the page that edits
the same thing after setup.

The table places every page in today's site map: its name, whether it is a
menu entry or which page it is reached from, the roles that may open it, the
names it replaces, and its current and new URL under the
[URL scheme](#url-scheme). "Reached from" is also the page's breadcrumb
parent. Pages "outside the menu" are the sign-in, maintenance and error pages,
which the access gate shows; "setup stepper" pages are the wizard's;
"retired" pages leave the portal and their old URLs answer 404
([#864](https://github.com/epiphany40223/parishkit/issues/864)).

| Page | Name | Reached from | Roles | Current names | Current URL | New URL | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `index` | Home | Menu: Home | Administrator, Staff, Ministry leader | Campaign administration | `/admin/` | (same) | Heading becomes Home; parish and campaign shown as the data line. |
| `configuration_request` | Change status | Settings page the change came from (else Home) | Administrator | Configuration change status; Configuration change | `/admin/changes/<request>/` | (same) |  |
| `campaign_settings` | Campaign settings | Menu: Campaign settings | Administrator | (same) | `/admin/campaign/settings/` | (same) |  |
| `campaign_create` | Create the campaign | Home | Administrator | (new, #142) | `/admin/campaign/first/` | (same) | Offered on Home only while the deployment has never had a campaign; refused once one exists. Not linked from a change's status page. |
| `campaign_clone` | Copy campaign | Campaign settings | Administrator | Clone archived campaign | `/admin/campaign/copy/` | (same) | Decision 18: until #145 removes it, Campaign settings shows Copy campaign greyed out, not an action, with the tip "Disabled; will be removed with the single-campaign change (#145)". The server refuses the clone action. |
| `content_history` | Content history | Campaign settings | Administrator | Retained campaign content | `/admin/campaign/content/history/` | (same) |  |
| `content_history_revision` | Earlier version | Content history | Administrator | Retained campaign content; Revision | `/admin/campaign/content/history/<revision>/` | (same) |  |
| `content_catalog` | Pages and emails | Menu: Pages and emails | Administrator | Campaign content and templates | `/admin/campaign/content/` | (same) |  |
| `content_edit` | _page or email name_ | Pages and emails | Administrator | Edit page or email | `/admin/campaign/content/<kind>/<slot>/` | (same) | Object-named (exception). |
| `content_revision` | _email name_ | Pages and emails | Administrator | _email template name_; Content revision | `/admin/campaign/content/email/<slot>/<revision>/` | (same) | Object-named (exception); links its own test page. |
| `content_delete` | Delete email templates | Pages and emails | Administrator | (new, #879) | `/admin/campaign/content/deletion/` (POST only) | (same) | The email tables' confirmation dialog posts here. |
| `campaign_mail` | Preview and test email | _email name_ | Administrator | Campaign email test | `/admin/campaign/content/test/<revision>/` | (same) | Returns to the page it was opened from (Go-live readiness, Pause and resume mail). |
| `campaign_mail_families` | Send to chosen Families | Preview and test email | Administrator | Send this email to chosen Families | `/admin/campaign/content/test/<revision>/families/` | (same) |  |
| `artwork_settings` | Campaign images | Menu: Campaign images | Administrator | (same) | `/admin/campaign/images/` | (same) |  |
| `artwork_upload` | Campaign images | Campaign images | Administrator | Campaign images (upload error); Campaign image upload | `/admin/campaign/images/<slot>/` (POST only) | (same) | POST-only: a staged upload answers with the review dialog's review of it (#750); a refused one re-renders Campaign images (status 400). Review campaign image is a review, and Remove campaign image a confirmation (#1356), in Campaign images' review dialog; their old addresses are gone. |
| `schedule_settings` | Dates and mail schedules | Menu: Dates and mail schedules | Administrator | Mail schedules and campaign dates; Mail schedules | `/admin/campaign/schedules/` | (same) |  |
| `schedule_new` | New scheduled email | Dates and mail schedules | Administrator | (new, #878) | `/admin/campaign/schedules/addition/` | (same) | Its review confirms through Dates and mail schedules. |
| `schedule_edit` | Edit scheduled email | Dates and mail schedules | Administrator | (new, #878) | `/admin/campaign/schedules/<schedule>/` | (same) | The New page filled in; refuses a read-only schedule. |
| `schedule_email_test` | Send a test of this text | New and Edit scheduled email | Administrator | (new, #446) | `/admin/campaign/schedules/test-email/` (POST only) | (same) | The page script posts the schedule's unsaved email here; nothing is saved. |
| `schedule_calendar` | Campaign calendar | Dates and mail schedules | Administrator | (new, #469) | `/admin/campaign/schedules/calendar/` | (same) | Read-only; every send by date. |
| `schedule_delete` | Delete scheduled emails | Dates and mail schedules | Administrator | (new, #878) | `/admin/campaign/schedules/deletion/` (POST only) | (same) | The table's confirmation dialog posts here. |
| `share_settings` | Share options | Menu: Share options | Administrator | How Families will share | `/admin/campaign/share-options/` | (same) | Gains a Return link and About panel. |
| `reminder_workgroup` | No-reminders WorkGroup | Menu: No-reminders WorkGroup | Administrator | (new, #861) | `/admin/campaign/no-reminders-workgroup/` | (same) | Edit, confirm, apply; stays editable while live. |
| `go_live` | Go-live readiness | Menu: Go-live readiness | Administrator | (same) | `/admin/campaign/go-live/` | (same) |  |
| `go_live_families` | Testing submissions | Go-live readiness | Administrator | Families with Testing submissions; Testing Families | `/admin/campaign/go-live/families/` | (same) |  |
| `go_live_cleanup` | Testing cleanup | Go-live readiness | Administrator | Testing cleanup progress | `/admin/campaign/go-live/cleanup/<request>/` | (same) |  |
| `go_live_links` | Prepare Family links | Testing cleanup | Administrator | Family links | `/admin/campaign/go-live/cleanup/<request>/links/` | (same) |  |
| `production_confirmation` | Confirm Production | Prepare Family links | Administrator | Final Production confirmation | `/admin/campaign/go-live/cleanup/<request>/links/<preparation>/confirmation/` | (same) |  |
| `production_progress` | Production activation | Menu: Production activation | Administrator | Production activation progress | `/admin/campaign/production/` | (same) | Links its cleanup request and Outgoing mail. |
| `production_withdrawal` | Cancel go-live | Production activation | Administrator | Withdraw from Production; Return to Testing | `/admin/campaign/production/cancellation/` | (same) | Links Pause and resume mail and Outgoing mail where it names them. |
| `delivery_control` | Pause and resume mail | Menu: Pause and resume mail | Administrator | Campaign delivery controls; Delivery controls | `/admin/mail/controls/` | (same) |  |
| `family_email_progress` | Family email progress | Menu: Family email progress | Administrator | (same) | `/admin/mail/family-progress/` | (same) |  |
| `family_email_sends` | Family email history | Menu: Family email history | Administrator | Family email sends | `/admin/mail/family-history/` | (same) |  |
| `deliveries` | Outgoing mail | Menu: Outgoing mail | Administrator | (same) | `/admin/mail/outgoing/` | (same) |  |
| `delivery` | _email type and Family name_ | Outgoing mail | Administrator | Mail delivery; Message | `/admin/mail/outgoing/<message>/` | (same) | Object-named (exception, [#1015](https://github.com/epiphany40223/parishkit/issues/1015)): the heading names the email, such as "Initial invitation to Morgan, Henry"; the menu and trail say Mail message and the browser title only the email type. See [Mail message page](#mail-message-page). |
| `delivery_refusals` | Refused addresses | Outgoing mail | Administrator | (same) | `/admin/mail/refusals/` | (same) | Also linked from Data to fix in ParishSoft (#1011). |
| `delivery_refusal` | Refused address | Refused addresses | Administrator | Verify refused address | `/admin/mail/refusals/<refusal>/` | (same) |  |
| `family_portal` | Family portal availability | Menu: Family portal availability | Administrator | (same) | `/admin/mail/family-portal/` | (same) |  |
| `presence` | Families on the form now | Menu: Families on the form now | Administrator | (same) | `/admin/mail/presence/` | (same) | The header's count is still polled at `/admin/presence?format=count` (decision 8); any other read of that address is 404. |
| `held_emails` | Held emails | Reached from: Home | Administrator | (new, #757) | `/admin/mail/held-emails/` | (same) | Not a menu entry: Home links to it while a held email is undecided. Its trail is Home › Mail and Family portal. See [restore release](#restore-release). |
| `response_dashboard` | Response dashboard | Menu: Response dashboard | Administrator, Staff | (same) | `/admin/reports/responses/` | (same) |  |
| `reports` | Participation | Menu: Participation | Administrator, Staff | (redirects) Participation and campaign statistics; 'Campaign reports' only when no campaign exists; Campaign reports | `/admin/reports/` | (same) | Not a group root: redirects to Participation, or to Ministry requests for a viewer who may not open Participation (a Ministry leader). |
| `participation` | Participation | Menu: Participation | Administrator, Staff | Participation and campaign statistics; Campaign reports (via redirect) | `/admin/reports/participation/` | (same) |  |
| `campaign_comparison` | Campaign comparison | Menu: Campaign comparison | Administrator, Staff | (new, [#1018](https://github.com/epiphany40223/parishkit/issues/1018)) | `/admin/reports/comparison/` | (same) | Compares the current campaign with imported past campaigns ([campaign history](../campaign-history/spec.md#comparison-report)). |
| `report_export` | _report page name_ export | The report it came from | Administrator, Staff, Ministry leader | Ministry export / Additional-information export / Family-directory export / Family-directory mail-merge export; Report export (breadcrumb) | `/admin/reports/exports/<request>/` | (same) | Object-named (exception). Every export shares it: a latest-data export too (decision 8). A report's export is named after its report page, for example "Active parishioner family directory export" or "Ministry requests export" (#520), and its breadcrumb says the same name as its heading. Exports that are not one report's keep their own names (Campaign archive, Campaign comparison download, Family timeline export, Chosen-Family test names export). The export error page reads nothing from the database, so it cannot name the export; its link back says "Return to the export status page". Its actions post to `cancellation/`, `retries/` and `download/`. It is a [point-in-time](../operations/spec.md#point-in-time-exports) preparing step, not a history entry: past the download window it answers the fixed "no longer available" page, and no page lists earlier exports. |
| `report_exact` | Latest-data export | Participation | Administrator, Staff | Queued participation export; Exact export | `/admin/reports/exports/<request>/` (the shared export page) | (same) | Decision 8: folded into the shared export page; it keeps its own heading, and its trail names it. Its old addresses (`exact-exports/<request>/` and its actions) are gone (#864). |
| `financial_report` | Financial stewardship | Menu: Financial stewardship | Administrator, Staff | Financial stewardship detail; Financial report | `/admin/reports/financial/` | (same) |  |
| `limitations_report` | Limitations | Menu: Limitations | Administrator, Staff | Talents and limitations | `/admin/reports/limitations/` | (same) | Renamed when talents were retired (#970); the old address is gone. Its CSV/XLSX/PDF download (`limitations/exports/`) is a non-page action. |
| `census_changes` | Census changes | Menu: Census changes | Administrator, Staff | (new, #528) Pending census changes | `/admin/reports/census/` | (same) | Its CSV/XLSX/PDF download (`census/exports/`) is a non-page action; a row's Entered in ParishSoft, Ignore and Reopen post to the page itself. |
| `information_queue` | Additional information | Menu: Additional information | Administrator, Staff | Additional information and follow-up | `/admin/reports/information/` | (same) |  |
| `information_item` | Information request | Additional information | Administrator, Staff | Additional information and follow-up (one request); Information item | `/admin/reports/information/<item>/` | (same) | Return to the queue keeps its filters and page. |
| `ministry_reports` | (retired) | (retired) | Administrator, Staff, Ministry leader | Ministry reports | `/admin/ministry-reports/` | (retired; 404) | Replaced by Ministry requests, which shows the "no campaign" page when there is nothing for the viewer to report. |
| `ministry_report` | Ministry requests | Menu: Ministry requests | Administrator, Staff, Ministry leader | Ministry report; Ministry follow-up | `/admin/reports/ministries/` | (same) | One page with the follow-up queue since [#1133](https://github.com/epiphany40223/parishkit/issues/1133): totals, By Ministry, the queue, Export these requests and the follow-up packet ([Ministry change summary](../reports/spec.md#ministry-change-summary)). No assignee column, filter or bulk assignment (#552). Its Export these requests download (`follow-up/exports/`, #1157) and bulk outcome (`follow-up/bulk/`, #1131) are non-page actions. |
| `ministry_joiners` | (retired) | (retired) | Administrator, Staff, Ministry leader | Ministry requests — Prospective joiners; Joining; Members joining | `/admin/reports/ministries/joining/` | (retired; 404) | Retired with no redirect ([#1157](https://github.com/epiphany40223/parishkit/issues/1157)): each request's contact details are on its Follow-up request page, and its export is Ministry requests' Export these requests. |
| `ministry_leavers` | (retired) | (retired) | Administrator, Staff, Ministry leader | Ministry requests — Requested leavers; Leaving; Members leaving | `/admin/reports/ministries/leaving/` | (retired; 404) | Retired with no redirect (#1157), as Members joining. |
| `ministry_followup` | (retired) | (retired) | Administrator, Staff, Ministry leader | Ministry follow-up; Follow-up | `/admin/reports/ministries/follow-up/` | (retired; 404) | Retired with no redirect ([#1133](https://github.com/epiphany40223/parishkit/issues/1133)): the queue is now Ministry requests, at `/admin/reports/ministries/`. |
| `ministry_followup_item` | Follow-up request | Ministry requests | Administrator, Staff, Ministry leader | Ministry follow-up (one request) | `/admin/reports/ministries/follow-up/<request>/` | (same) | Return to the queue keeps its filters and page. Status, notes and outcome only; no Assign to (#552). Shows the Member's contact details (#1157). A resolved join or leave's Entered in ParishSoft tick posts to `follow-up/<request>/roster/` (#528). |
| `ministry_roster` | Roster changes to enter | Ministry requests | Administrator, Staff | (new, #528) | (none) | `/admin/reports/ministries/follow-up/roster/` | [Roster changes to enter](#roster-changes-to-enter); its CSV/XLSX/PDF download (`roster/exports/`) and its bulk Mark entered in ParishSoft (`roster/marks/`, #879) are non-page actions. |
| `family_directory` | Active parishioner family directory | Menu: Active parishioner family directory | Administrator, Staff | (same) | `/admin/reports/families/` | (same) |  |
| `family_timeline` | Family timeline | Active parishioner family directory | Administrator, Staff | (new, #477) | `/admin/reports/families/<family>/` | (same) | Opened from each directory row (and Find a Family, NAV-19); `<family>` is the opaque campaign record id. Staff see the summary only. |
| `emailed_reports` | Emailed reports | Menu: Emailed reports | Administrator, Staff | (new) | `/admin/reports/emailed/` | (same) | New page (decision 7, ADM-12.17): the current campaign's daily reports (and weekly reports for Administrators), newest first, each linking its page; Send a weekly report now is on it and returns to it, linking the report it produced. |
| `weekly_digest_manual` | Send a weekly report now | Emailed reports | Administrator | Request a manual information report; Manual information report | `/admin/reports/emailed/weekly/new/` | (same) | Was the Manual information report menu entry; now a button on Emailed reports, where it ends, linking the report it produced (or its background task until it is ready). Its address named the campaign until #865; that old address is gone, with no redirect (#864). |
| `weekly_digest_snapshot` | Weekly report | Emailed reports | Administrator | Weekly information report; Weekly summary | `/admin/reports/emailed/weekly/<snapshot>/` | (same) | Its old address, which weekly report emails sent before NAV-12 link, is gone, with no redirect (#864). |
| `weekly_digest_item` | Weekly report item | Weekly report | Administrator | Weekly information report (one item); Weekly summary item | `/admin/reports/emailed/weekly/<snapshot>/items/<item>/` | (same) | Links the live Additional information request. |
| `daily_digest_snapshot` | Daily report | Emailed reports | Administrator, Staff | (report document title) daily report | `/admin/reports/emailed/daily/<snapshot>/` | (same) | Its old address, which daily report emails sent before NAV-12 link, is gone, with no redirect (#864). |
| `parish_settings` | Parish settings | Menu: Parish settings | Administrator | (same) | `/admin/parish/settings/` | (same) | Hand-written Administration link removed. |
| `branding_settings` | Parish logos | Menu: Parish logos | Administrator | (same) | `/admin/parish/logos/` | (same) | An upload answers with the review dialog's review of the staged logos, and the dialog's Apply posts here (#750); a refused upload re-renders Parish logos (status 400). The page takes no query: the old Review parish logos address and the `?review=` and `?request=` addresses are gone. |
| `ministries` | Ministries | Menu: Ministries | Administrator | Ministry activity | `/admin/parish/ministries/` | (same) | A read-only catalog: no Activity column, sort, filter or Review activation / inactivation, and every POST is refused (Administrator, 2026-10-13, #1267). |
| `hosted_files` | Hosted files | Menu: Hosted files | Administrator | (same) | `/admin/parish/files/` | (same) |  |
| `hosted_file_delete` | Delete hosted files | Hosted files | Administrator | (same) | `/admin/parish/files/deletion/` (POST only) | (same) | The table's confirmation dialog posts here (#879); it answers with a redirect or a refusal, never a page. |
| `hosted_file_rename` | Change placeholder name | Hosted files | Administrator | (same) | `/admin/parish/files/<file>/name/` | (same) |  |
| `past_campaigns` | Past campaigns | Menu: Past campaigns | Administrator | (new, [#1018](https://github.com/epiphany40223/parishkit/issues/1018)) | `/admin/parish/past-campaigns/` | (same) | Imports the archive workbooks of past campaigns ([campaign history](../campaign-history/spec.md#past-campaigns-page)). Its Review (`review/`, the one upload route), Apply (`imports/`) and Remove (`removal/`, the table's confirmation dialog) are POST-only form actions that answer with the page in place or a redirect to it. |
| `source_refresh` | Refresh from ParishSoft | Menu: Refresh from ParishSoft | Administrator | ParishSoft refresh | `/admin/parish/parishsoft-refresh/` | (same) |  |
| `parishsoft_fixes` | Data to fix in ParishSoft | Menu: Data to fix in ParishSoft | Administrator, Staff | (new, #1011) | (none) | `/admin/parish/data-to-fix/` | New page ([Data to fix in ParishSoft](#data-to-fix-in-parishsoft), #1011); no parent page, so its trail is Home, Parish data, as for the other Parish data entries. |
| `users` | Users | Menu: Users | Administrator | Portal users; Sign-in rules | `/admin/users` | `/admin/users/` | One table of users (#952), which replaces the planned split into Sign-in rules, Ministry assignments and Chairpersons (decision 13). |
| `user_new` | New user | Users | Administrator | (new, #952) | (none) | `/admin/users/new/` | Its review applies through Users. |
| `user_edit` | Edit user | Users | Administrator | (new, #952) | (none) | `/admin/users/<user>/edit/` | The New page filled in; `<user>` is the user's stable ID. |
| `user_delete` | Delete user | Users | Administrator | (new, #952) | (none) | `/admin/users/deletion/` (POST only) | The table's confirmation dialog posts here. |
| `user_name_suggestions` | (not a page) | New user | Administrator | (new, #952) | (none) | `/admin/users/new/names/` (POST only, JSON) | Read-only Name suggestions from ParishSoft. |
| `user_leader_import` | Import Ministry leaders | Users | Administrator | (new, #952) | (none) | `/admin/users/leaders/import/` (POST only) | Review of the chosen ParishSoft Ministry leaders, shown in the review dialog on Users; applies through Users. |
| `user_rules` | Review sign-in rules | Portal users | Administrator | Review login rule change | `/admin/users/rules` (POST only) | (none) | Retired by #952: removed with the rule tables when Users lands; its old address is not kept (#864). |
| `rule_request` | (not a page) | Portal users | Administrator | Rule change (status) | `/admin/users/rules/requests/<request>` (JSON) | (none) | Retired by #952 with the autosave queue; a user change's status is its Change status page. |
| `assignments` | Review Ministry assignment | Portal users | Administrator | Assignments | `/admin/users/assignments` (POST only) | (none) | Retired by #922: removed with Ministry assignments. |
| `chair_confirmations` | Review Chairperson suggestion | Portal users | Administrator | Chair suggestions | `/admin/users/suggestions` (POST only) | (none) | Retired by #922: removed with Chairperson suggestions. |
| `chair_reviews` | Review Chairperson decision | Portal users | Administrator | Chair reviews | `/admin/users/reviews` (POST only) | (none) | Retired by #922: removed with Chairperson reviews. |
| `signed_in_sessions` | Signed-in sessions | Menu: Signed-in sessions | Administrator | (new, #953) | (none) | `/admin/users/sessions/` | New page ([Signed-in sessions](#signed-in-sessions)); its Sign out dialog posts the session to the same address. |
| `automation_access` | Automation access | Menu: Automation access | Administrator | (same) | `/admin/users/automation/` | (same) | [Admin automation](../admin-automation/spec.md#revocation-and-listing) (ADM-11); the Revoke dialog posts the session to `/admin/users/automation/sessions/` (#879). |
| `automation_approval` | Approve an automation session | Automation access | Administrator | (same) | `/admin/users/automation/approval/` | (same) | Opened from the command line's link; needs a fresh sign-in. |
| `system_health` | System health | Menu: System health | Administrator | (new) | (none) | `/admin/system/health/` | New page ([System health](#system-health), #530, ADM-13). |
| `source_form` | Families the form cannot open | System health | Administrator | (new, #774) | (none) | `/admin/system/source-form/` | New page ([System health page](#system-health-page)); no menu entry. Also linked from Data to fix in ParishSoft (#1011). |
| `integrations` | Integrations | Menu: Integrations | Administrator | (same) | `/admin/system/integrations/` | (same) |  |
| `integration_settings` | _integration name_ | Integrations | Administrator | _integration name_ (e.g. ParishSoft, Google Workspace mail, Slack notifications, Off-site backups, Backup encryption key); Integration; ParishSoft settings; Off-site backup settings (link text) | `/admin/system/integrations/<target>/` | (same) | Object-named (exception); hand-written Integrations link removed. A link that names the page uses its heading ("ParishSoft", "Off-site backups (Google Drive)"); an action link ("Set up off-site backups") stays an action (#520). |
| `credential_status` | Key replacement status | _integration name_ | Administrator | (same); Key change details (link text) | `/admin/system/key-changes/<request>/` | (same) |  |
| `select_credential` | Finish switching to the new key | _integration name_ | Administrator | (same) | `/admin/system/key-changes/<request>/selection/` | (same) |  |
| `background` | Background work | Menu: Background work | Administrator | (same) | `/admin/system/background/` | (same) |  |
| `background_task_page` | Background task | Background work | Administrator | Background task details | `/admin/system/background/<task>/` | (same) |  |
| `logs` | System logs | Menu: System logs | Administrator | (same) | `/admin/system/logs/` | (same) |  |
| `campaign_new` | (retired) | (retired) | Administrator | Create campaign draft; New campaign | `/admin/campaign/new` | (retired; 404) | Decision 11: no New campaign control; the one campaign is created by Create the campaign (#142). |
| `report_campaigns` | (retired) | (retired) | Administrator, Staff | Choose a retained campaign; Choose a campaign | `/admin/reports/campaigns/` | (retired; 404) | Decisions 10 and 19: no campaign chooser; reports show the current campaign. Its greyed link on Participation was removed (#1125). |
| `ministry_report_campaigns` | (retired) | (retired) | Administrator, Staff, Ministry leader | Choose a retained campaign; Choose a campaign | `/admin/ministry-reports/campaigns/` | (retired; 404) | Decisions 10 and 19: no campaign chooser. Its greyed link on Ministry requests was removed (#1125). |
| `chrome` | (shared header and banners) | Every page | Administrator, Staff, Ministry leader | Every Admin page (header and banners) | `/admin/*` | (same) | Acknowledge stays on the current page (#519). |
| `login` | Administration sign-in | (outside the menu) | Anyone | (same) | `/admin/login` | (same) |  |
| `local_sign_in` | Local test sign-in | (outside the menu) | Anyone | (same) | `/admin/local/sign-in` | (same) | LOCAL only. |
| `maintenance` | Restore review | (outside the menu) | Administrator | The site is being restored (Staff, Ministry leader) | `/admin/maintenance` | (same) | Decision 12: the [restore review page](#restore-release) (#537). Staff and Ministry leaders see a plain notice. |
| `availability_setup` | The system is not configured yet | (outside the menu) | Administrator, Staff, Ministry leader | (same) | (any Admin URL before setup completes, non-Administrator) | (any Admin URL before setup completes) |  |
| `error_page` | _error title_ | (outside the menu) | Administrator, Staff, Ministry leader | _error title_ (e.g. Check your entries, This information changed, Access unavailable, Page unavailable, Page not found, Confirm it's you) | (any Admin URL that fails) | (same) | Object-named (exception); offers a way back to the page or Home. |
| `setup` | Initial setup | Setup stepper | Administrator | (same) | `/admin/setup` | (same) |  |
| `setup_step_parish` | Parish profile | Setup stepper | Administrator | (same) | `/admin/setup/parish` | (same) |  |
| `setup_step_mail` | Outgoing email settings | Setup stepper | Administrator | (same) | `/admin/setup/mail` | (same) |  |
| `setup_step_testing` | Testing recipient | Setup stepper | Administrator | (same) | `/admin/setup/testing` | (same) |  |
| `setup_credential_google_workspace` | Google Workspace connection | Setup stepper | Administrator | Setup credential: Google Workspace mail (stepper: Google Workspace connection) | `/admin/setup/credentials/google_workspace` | (same) |  |
| `setup_step_slack` | Slack notifications (optional) | Setup stepper | Administrator | (same) | `/admin/setup/slack` | (same) |  |
| `setup_credential_slack` | Slack connection | Setup stepper | Administrator | Setup credential: Slack (stepper: Slack connection) | `/admin/setup/credentials/slack` | (same) |  |
| `setup_branding` | Parish logos | Setup stepper | Administrator | Parish logo | `/admin/setup/branding` | (same) |  |
| `setup_step_access` | Administrative access | Setup stepper | Administrator | (same) | `/admin/setup/access` | (same) |  |
| `setup_credential_parishsoft` | ParishSoft connection | Setup stepper | Administrator | Setup credential: ParishSoft (stepper: ParishSoft connection) | `/admin/setup/credentials/parishsoft` | (same) |  |
| `setup_source` | Load parish data | Setup stepper | Administrator | (same) | `/admin/setup/source` | (same) |  |
| `setup_source_progress` | Loading parish data | Setup stepper | Administrator | (same) | `/admin/setup/source/<task>` | (same) |  |
| `setup_preview` | Review | Setup stepper | Administrator | Setup preview (stepper: Review) | `/admin/setup/preview` | (same) |  |
| `setup_mail` | Test email | Setup stepper | Administrator | Setup email test (stepper: Test email) | `/admin/setup/mail-test` | (same) |  |
| `setup_notification` | Test Slack | Setup stepper | Administrator | Setup Slack test (stepper: Test Slack) | `/admin/setup/slack-test` | (same) |  |
| `setup_confirmation` | Finish setup | Setup stepper | Administrator | Finish initial setup (stepper: Finish setup) | `/admin/setup/confirm` | (same) |  |
| `setup_cancel` | Finishing setup | Setup stepper | Administrator | (same) | `/admin/setup/cancel` | (same) |  |
| `setup_unavailable` | Setup step not available | Setup stepper | Administrator | (same) | (any setup step whose prerequisites are missing) | (same) |  |

#### URL scheme

Admin URLs follow the menu, so the address says where the reader is
(issue #525):

- **The first segment after `/admin/` is the menu group:** `campaign`
  (Campaign setup), `mail` (Mail and Family portal), `reports` (Responses and
  reports), `parish` (Parish data), `users` (Users and access) and `system`
  (System). Home is `/admin/`, a change's status page is
  `/admin/changes/<request>/`, and the sign-in, maintenance and setup wizard
  pages keep their own prefixes outside the menu.
- **No campaign identifier:** every page is `/admin/<group>/<page>/`, for
  example `/admin/campaign/settings/`, `/admin/mail/family-history/` and
  `/admin/reports/participation/`, and campaign pages show the current
  campaign. This needs no schema change and leaves nothing to undo when the
  system becomes single-campaign (#145). Records that belong to a campaign
  (an export, a digest snapshot, a cleanup request) are addressed by their
  own identifier, which already names their campaign.
- **One trailing-slash rule:** every Admin page URL ends in `/`, and the other
  form redirects permanently to it (301 for GET and HEAD, 308 otherwise),
  keeping the query string, where report filters live. The sign-in, setup
  wizard and maintenance pages are outside the rule and keep their current
  addresses (decision 27).
- **Non-page routes move with their page:** a page's form actions and
  downloads move under its new URL and follow the same rules. The session and
  status JSON endpoints that scripts poll (`session/*`, the background and
  presence counts) keep their addresses.
- **Nouns, not verbs:** plural nouns for collections, a noun for each item,
  and actions are POSTs to the item or collection (no GET target such as
  `/delete` or `/remove`).
- **Old URLs are not kept**
  ([#864](https://github.com/epiphany40223/parishkit/issues/864)): see
  [addresses and legacy URLs](../ui-conventions/spec.md#addresses-and-legacy-urls).

#### Breadcrumbs and flows

The breadcrumb trail runs Home › group › each ancestor page › the current
page, for example Home › Campaign setup › Pages and emails › Initial
invitation. A view may name the current page more specifically (the email
being edited, the integration). Ancestor links reuse the current request's
resolved route arguments. Home shows no trail. Building the menu and trail
runs no queries.

A view may place its page more precisely than its route can, so deep steps of
multi-step flows keep their context: it may name a different parent, supply
route arguments an ancestor link needs, and name an ancestor specifically.
Preview and test email sits under the email it sends (Home › Campaign setup ›
Pages and emails › Initial invitation › Preview and test email), with Send to
chosen Families below it. An export's status page sits under the report it came
from. A configuration change's status page sits under the settings page the
change was confirmed on: confirming remembers that page in the signed-in
session (never in the URL), and the status page shows its trail and a "Return
to" link to it. Without that memory, as in another sign-in, the page stands
under Home. A key's replacement status and its Finish switching page sit under
the integration the key belongs to, never under each other, because only the
Administrator who saved a key may read its status. Some pages are named in
trails but never linked, and "Return to" skips them: those that only answer a
POST (the Import Ministry leaders review); one-time reviews that refuse once their change is
confirmed (Copy campaign); and Finish
switching, which still opens afterward but needs a fresh Google sign-in and has
nothing left to do. A menu page is linked in a trail or "Return to" only while
the viewer's menu entry for it is available, so a page whose entry is
unavailable because it would now refuse (Share options once the campaign is
locked, Campaign images for an archived campaign) is named without a link.
The same holds for a role: a page the viewer's menu does not offer is named
without a link, including a report root that only redirects to a menu entry,
so a Ministry leader's export status page names Participation without linking
it.

Multi-step flows also show a step indicator under the trail: a numbered list
with the current step marked `aria-current="step"` and each step's state in
text. It is orientation only and links nothing, so it cannot skip a review or
confirmation. The flows are: making a settings change (Make changes, Review,
Apply) on every settings editor (Copy
campaign, pages and emails, dates and mail schedules, share options,
campaign images, Parish settings, Parish logos, each integration,
Ministries and Finish switching), Campaign settings, New user and Edit user
(whose review and Apply are in the
[review dialog](../ui-conventions/spec.md#review-dialog), so the page
stays at Make changes) and every change's status
page; going live (Check readiness, Testing cleanup, Family links, Confirm
Production, Activation); sending to chosen Families (Choose Families, Review,
Send and follow); and report exports (Choose report, Prepare file, Download).
Campaign settings with nothing open to change (a closed campaign's) is not
in a flow. Import Ministry
leaders, reviewed in that dialog over Users, shows no step indicator. An
error page never shows a step or a placed trail. Placement and steps are
presentation only and grant nothing.

#### Navigation decisions

The Administrator decided 1 to 19 on the navigation proposal on 2026-10-04.
Decisions 20 to 30 settle section 4 of the implementation plan the same day:
each is marked as the Administrator's or as a coordinator call that follows
an existing rule. All are recorded on issue #522, and the text above already
follows them.

1. **Menu groups and their order?** Home, Campaign setup, Mail and Family
   portal, Responses and reports, Parish data, Users and access, System (as
   proposed).
2. **One menu entry per report, or a Reports landing page?** One entry per
   report (as proposed).
3. **How does an unavailable entry show its reason?** Greyed out, with the
   reason shown on hover; the reason must also reach keyboard and screen-reader
   users (focusable and announced), not mouse only.
4. **Where do Ministries live?** One Ministries entry under Parish data; the
   campaign's Ministries are its child page (as proposed).
5. **Where do Integrations live?** System (as proposed).
6. **Move Outgoing mail, Family portal availability and Families on the form
   now out of System?** Yes, into Mail and Family portal (as proposed).
7. **Add an Emailed reports page with Send a weekly report now on it?** Yes (as
   proposed).
8. **Fold the latest-data participation export into the shared export page?**
   Yes (as proposed).
9. **Accept the proposed renames?** All as recommended, except Withdraw from
   Production becomes Cancel go-live, which avoids a clash with the
   after-archive Return to Testing.
10. **Offer one shared campaign chooser on every report?** No. The system moves
    to a single campaign after this one (#145); reports always show the current
    campaign and the existing choosers are retired.
11. **What should New campaign do while an archived campaign is still
    current?** Remove the New campaign control. The one campaign is created
    by [Create the campaign](#create-the-campaign), offered only while the
    deployment has never had a campaign, after
    [setup](#bootstrap-and-first-admin-wizard), which configures only the
    system (#142).
12. **Should the restore-review maintenance page get a way forward?** Yes, in
    its own issue: #537 (as proposed); it is the
    [Restore review page](#restore-release).
13. **Split Portal users into separate entries?** Superseded by the Users
    revamp (Administrator, 2026-10-10,
    [#952](https://github.com/epiphany40223/parishkit/issues/952)): sign-in
    is by email address only, Ministry leaders come from ParishSoft roles
    (#922), and the one Users page replaces the planned Sign-in rules,
    Ministry assignments and Chairpersons pages. The
    split (#535, #796, #914) is not built.
14. **Should menu groups collapse?** Groups are collapsible, with the state
    remembered per browser.
15. **How should the campaign appear in Admin URLs?** No campaign identifier in
    Admin URLs: the current campaign is implied. Old URLs with campaign UUIDs
    redirect permanently. No schema change, and nothing to undo after #145.
    (Superseded by #864: old URLs are not kept, so each is 404.)
16. **Where does the campaign sit in the path?** Nowhere (see 15): every page
    is `/admin/<group>/<page>/`.
17. **Which trailing-slash rule?** Every Admin page URL ends in a slash; the
    other form redirects; old URLs redirect permanently (301 pages, 308 form
    actions) (as proposed). (Old URLs superseded by #864: each is 404.)
    Sign-in, setup and maintenance keep their addresses (decision 27).
18. **What happens to Copy campaign until the single-campaign change?** Keep it
    until #145, greyed out (disabled, not a link or action) with a hover and
    keyboard-focus tip saying it is disabled and will be removed with the
    single-campaign change (#145). The server refuses the clone action too, so
    the disabled state cannot be bypassed.
19. **What happens to other controls that only serve multiple campaigns?**
    Every remaining reachable or visible control whose only purpose is
    multi-campaign work (campaign choosers and switchers, Copy campaign,
    successor-campaign controls) is greyed out, not a link or action, with a
    hover and keyboard-focus tip saying it is disabled and will be removed with
    the single-campaign change (#145); the server refuses the matching actions.
    New campaign stays removed (decision 11). (Amended by the Administrator on
    2026-10-12, #1125: the greyed campaign choosers on Participation and
    Ministry requests are removed rather than kept, and report pages no longer
    open with the campaign's name; Copy campaign stays greyed, per decision 18.)
20. **No-script fallbacks?** (Coordinator.) None: the Admin portal
    [requires JavaScript](../ui-conventions/spec.md#javascript-requirement) (#565).
21. **Who gets Find a Family, and how does it search?** Administrators and
    Staff only, results scoped to what the role may see (Administrator); by
    CSRF POST (coordinator) (#561). It matches what the directory search
    matches, including any active Member's name and the envelope number
    (coordinator, #664).
22. **What are the #477 response lists?** (Coordinator.) They were removed
    in #933: the directory's Response filters list those Families, and the
    old `/admin/reports/responses/<key>/` addresses are 404.
23. **Dead end with no current campaign?** (Administrator.) Accepted
    until #145; Home explains it. A deployment that has never had a campaign
    is not a dead end: Home offers Create the campaign (#142).
24. **The successor-draft control does not exist yet.** (Coordinator.) The
    server's refusal is enough until it does.
25. **Tip text?** (Administrator.) "Disabled; will be removed with the
    single-campaign change (#145)".
26. **Do non-page routes move?** (Coordinator.) Form actions and downloads
    move with their page; the session and status JSON endpoints stay.
27. **Trailing slashes on sign-in, setup and maintenance?** (Administrator.)
    An optional last pull request. (Dropped, #522: it changes exact paths in
    the access gate's allowlists for no visible gain, and under #864 their
    current addresses would stop working. These pages keep their addresses.)
28. **Old campaign identifiers?** (Administrator.) Refused with 410 for now;
    access to earlier campaigns returns with #145. (Superseded by #864: an
    old address naming a campaign is 404.)
29. **A Home Today line?** (Administrator.) Yes, one per role; its contents
    are confirmed in NAV-18.
30. **Where is the test email's origin kept?** (Coordinator.) In the
    signed-in session.

### Admin tables

This general rule lives in the [UX conventions](../ui-conventions/spec.md#admin-tables),
which have precedence for it.

#### Row actions and confirmation

This general rule lives in the [UX conventions](../ui-conventions/spec.md#row-actions-and-confirmation),
which have precedence for it.

#### Table column order

This general rule lives in the [UX conventions](../ui-conventions/spec.md#table-column-order),
which have precedence for it. Downloads follow the page's column order
except where a report's section says otherwise; the postal mail-merge file
never changes order
(see [mailing columns](../reports/spec.md#mailing-columns)). Every format
of one download (CSV, XLSX and PDF) carries the same columns, with no
internal references
([downloaded files](../ui-conventions/spec.md#downloaded-files)); a CSV is
exactly that table, its report details being in the XLSX and PDF (see the
reports' [shared report behavior](../reports/spec.md#shared-report-behavior)).

### In-place controls

This general rule lives in the [UX conventions](../ui-conventions/spec.md#in-place-controls),
which have precedence for it.

### Review dialog

This general rule lives in the [UX conventions](../ui-conventions/spec.md#review-dialog),
which have precedence for it, including its
[recorded exceptions](../ui-conventions/spec.md#review-dialog). Where a
section below says a page reviews on a separate page or in place under its
form, that is its current state until
[#750](https://github.com/epiphany40223/parishkit/issues/750) converts it.

### Page help

This general rule lives in the [UX conventions](../ui-conventions/spec.md#page-help),
which have precedence for it.

### Time entry

This general rule lives in the [UX conventions](../ui-conventions/spec.md#time-entry),
which have precedence for it.

### Preview lifetimes

An action that is reviewed before it is confirmed carries a signed preview,
and the server refuses a preview older than its lifetime. Pages do not
announce the limit. Previews have two lifetimes
([#523](https://github.com/epiphany40223/parishkit/issues/523)); a few other signed
tokens keep their own (the integration page's one-hour key-replacement intent,
the fifteen-minute backup-key proof and campaign clone seed):

- **Fifteen minutes** for every preview made through the shared
  configuration-change path: parish, integration and setup settings, campaign
  configuration, content, mail schedules, users and access rules, Ministries,
  logos and images, the Preview and test email sends, and the
  [past-campaign archive Review](../campaign-history/spec.md#what-is-stored),
  and System health's
  [Accept this change once](#accept-a-large-parishsoft-change-once), whose
  binding names the refusal and its counts as reviewed (an expired one is
  "This changed since you looked; review it again").
  The command line's
  [configuration requests](../admin-automation/spec.md#schedules-and-configuration)
  use the same fifteen minutes.
- **Five minutes** for the delivery-critical actions, whose previews bind a
  live work inventory or a public web address check: **Start Testing
  cleanup**, the controls on the Testing cleanup and Family links pages
  (prepare, retry, cancel), **Confirm Production** (or sooner, when the
  campaign's start or close, the ParishSoft data's expiry or the next newly
  due mail comes first), **Retry failed mail preparation** on the activation
  progress page, **Cancel go-live** (or sooner, at the campaign start), and
  pausing, resuming and resolving held messages on
  [Pause and resume mail](#live-delivery-pause). The
  [launch runbook](../../../guides/stewardship-launch-runbooks.md#production-activation)
  tells the Administrator about these limits, and the
  [Go live page](#go-live-page) replaces them for going live.

Confirming an expired or out-of-date preview is a correctable refusal ("This
preview is out of date"), usually with a link back to the page that builds it, so one
click starts a fresh review and nothing is saved in between. Today only the
fifteen-minute previews answer that way; an expired five-minute preview gets
the generic "Check this value." refusal
([#398](https://github.com/epiphany40223/parishkit/issues/398)).

### Button labels

This general rule lives in the [UX conventions](../ui-conventions/spec.md#button-labels),
which have precedence for it.

### Shared visual style

This general rule lives in the [UX conventions](../ui-conventions/spec.md#shared-visual-style),
which have precedence for it.

## Background indicators

Administrators have two always-visible indicators:

- **Families on the form now**: count of Family sessions with a heartbeat
  within the last 90 seconds, that is, Families with the form open in their
  browser; a Family that signed in but closed the form is not counted. Detail
  lists, in the [column order](#table-column-order) for Admin tables, the
  Family name as on the active parishioner family directory (surname, then
  the active heads of household, e.g. "Squyres, Jeff and Tracy"), Family
  DUID, the sign-in time, last activity, when the form was last seen open and
  the form section; it never shows answers or credentials.
- **Background work**: count/state of queued and running task runs. Detail shows
  type, initiator, start/heartbeat, phase, processed/total counts and percent,
  sanitized status, and links to completed/failed records. A distinct Admin-only
  delivery warning links to unresolved `delivery_unknown` rows and exposes the
  evidence-note, delivered-resolution, recorded-unsent and acknowledged-resend
  actions defined by the
  [delivery workflow](../background-processing/spec.md#family-invitations-and-reminders);
  it never displays credentials or sealed substitutions.

Family clients heartbeat while a form is actively visible, at no more than one
request every 30 seconds. Expired/closed/ineligible sessions disappear. Worker
heartbeats identify abandoned runs; recovery behavior is task-specific.
Family heartbeat and Admin background-indicator polling are presence-only
requests: neither refreshes the authenticated session's idle-expiry timestamp.
When the Admin session has ended, an indicator poll is refused and the page
stops polling rather than repeating the refused request. Any Admin request made
without a current session is refused with a plain explanation that the sign-in
has ended and a link to sign in again, never a missing-capability message.
The distinct Family activity-keepalive behavior is defined by the
[session policy](../architecture/spec.md#identity-and-session-security); it does
not affect presence semantics or carry form answers.

Every Admin page that follows background work (configuration changes,
credential replacement, Testing cleanup, Family link preparation, chosen-Family
tests, background task details, integration key changes, report exports,
[Family email progress](#family-email-progress) and the
[setup wizard's status pages](#bootstrap-and-first-admin-wizard))
updates itself: while the work is queued or running it shows a worded running
indicator and re-reads its passive status, backing off from 2 to 10 seconds
(or at the fixed pace a page asks for, 2 to 60 seconds) and pausing while the
tab is hidden; it states success or failure prominently
when the work finishes and then stops. These status reads are passive like the
indicator polling above, and a page never reloads itself through a view that
counts as activity, so an open page cannot keep an idle login alive. Nor does
a status read record an audited view: a page whose own view is audited, such
as background task details, reads a status-only fragment instead, so opening
the page records one view however long it stays open. The page never replaces
a control the Admin is using and never re-sends a form.

While a report export's, exact export's or background task's run is still
queued (no worker has claimed it), its status says why it has not started and
how long it has been queued
([#340](https://github.com/epiphany40223/parishkit/issues/340)). When a task
with a live lease holds the consumer process that takes the queued task's
queue, the page names it, e.g. "Waiting for the ParishSoft update to finish
(started 10:00 PM)"; otherwise it says "Waiting for other background work to
finish". The reason follows the real
[worker queues and processes](../background-processing/spec.md#worker-queues-and-processes):
a ParishSoft refresh is named only when the worker login's actual connection
limit (read from `pg_roles` on each status read, which worker startup requires
to match the worker's budget) is too small for a separate source process. A
missing or unlimited login proves nothing, so a refresh is then never named.
Only a reader who may see background work learns what runs ahead; a Ministry
leader viewing their own export sees only the generic reason. The reason is
read from rows the web login already reads and records no audit row. Once the
run is claimed the page shows its normal progress text.

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
Every editor in this section reviews and applies through the
[review dialog](../ui-conventions/spec.md#review-dialog), Parish settings
included: its **Save** opens the dialog, "Save the Parish settings?", with
a [compact review](../ui-conventions/spec.md#review-dialog) (#1356), one
"Setting: old → new" line per changed setting and a new timezone's note;
Apply follows the change there, and the form is redrawn at the applied
version.
Parish settings' address takes no query.

Outgoing email settings (the setup mail step and the post-setup outgoing
email integration) include an optional From name: a single line of at most 100
characters without control characters, `<`, `>`, `@`, quotes or backslashes.
Every outgoing message's From header shows it with the From address, quoted
and RFC 2047-encoded as needed; when it is blank the Parish profile name is
used. It is presentation only: routing, test scope and outbox checks keep
comparing the bare address, and changing it never voids a staged credential
test.

The Parish IANA timezone is the default for non-campaign presentation and the
fallback for a new campaign draft when the browser reports no listed zone
([Create the campaign](#create-the-campaign)). Editing it does not mutate an existing Campaign's
timezone, resolved boundaries, schedules, or historical report buckets. The UI
shows this scope explicitly and links to the separately editable draft campaign
timezone when one exists.

Time zone choices (Parish settings, the setup Parish step, campaign settings
and schedule dates) list canonical IANA names only. An older alias from the
IANA backward links, such as `US/Eastern`, that is already stored stays valid.
It is shown as the current choice with the zone it names ("US/Eastern (older
name for America/New_York)"), and saving without changing it keeps it. A
changed Parish zone, or a new setup's zone, is stored by its canonical name.
Stored history is never rewritten. Instead, every database conversion first
maps a stored alias to its zone, because the production PostgreSQL image
lacks the aliases (#1286).

Logo management previews every generated size. Uploads remain staged until the
YAML version referencing their immutable branding version is applied;
historical email/page previews retain their campaign version. The upload's
review and Apply happen in the
[review dialog](../ui-conventions/spec.md#review-dialog) (#750): an upload
answers with the dialog's review of the staged logos, and Apply posts the
signed preview, with the staged bundle it was signed for, to Parish logos.
Once the change is applied, the form and the current logos below it are
redrawn. An upload from a page whose settings changed elsewhere, and an
Apply of logos that expired, belong to another sign-in or were staged
against settings changed since, are refused in the dialog, which says to
reload the page first.

Parish logos take either one image, resized into all four sizes, or each
size uploaded separately (#1062): the large
logo, the menu logo beside the parish name on both portals, the icon and
the favicon in the browser tab on both portals. The two are the page's
[Basic and Advanced modes](../ui-conventions/spec.md#basic-and-advanced-modes)
(#1062 slice 3): **Basic** is one image, resized for every size, and
**Advanced** is each size uploaded separately. Each staged bundle records
the mode that made it (`stewardship_branding_bundle.mode`, schema
migration 0078; empty for campaign artwork and for bundles staged before
it, all made from one image), and the page opens in the mode that saved
the current logos: Basic when none are stored or they came from Basic (or
before the mode was recorded; the setup step records Basic), Advanced when
they came from Advanced. Basic has no equivalent for logos saved from
Advanced or for a size chosen in Advanced, so switching to Basic then asks
first ("Your custom logos have no basic equivalent.", with Keep Advanced
and Switch to Basic); nothing is replaced until a Basic image is uploaded,
reviewed and applied. Choosing a Basic image empties any size chosen in
Advanced, since a Basic upload replaces all four; the server turns the one
image into the four sizes. Upload and review is unavailable, with the reason
under it, until a Basic image or at least one Advanced size is chosen. The
upload sends the mode as `editor_mode`; an upload without one is Basic, and
one with any other field (such as the `mode` of the page before the toggle,
accepted for one release) is refused.
Each size states its
formats and limits beside its field and is refused, naming the size, the
rule and what the file was, when it breaks them:

| Size | Formats | Refused when |
| --- | --- | --- |
| Large logo | PNG, JPEG, WebP | Over 5 MB, over 16 megapixels, animated, or under 256 pixels on the longer side |
| Menu logo | PNG, JPEG, WebP | Over 1 MB, over 2,048 pixels on a side, wider than 4:1, taller than wide, or under 64 pixels tall |
| Icon | PNG | Over 1 MB, not square within 10%, or under 128 pixels on a side |
| Favicon | PNG | Over 256 KB, not square within 10%, under 16 or over 512 pixels on a side |

The files chosen together are at most 5 MB, so the request stays within
the ingress body limit; the page refuses a file over its size's limit, or
over that total, as soon as it is chosen. Nothing is staged unless every
chosen file is accepted. A size left empty keeps its current image, copied
unchanged into the new bundle, or, when no image of that size is stored and
readable, is generated from the large logo uploaded with it (otherwise it
is refused). An upload from a page whose settings changed elsewhere is
refused before the current logos are read.
Every accepted size is normalized like a generated one and fitted within
the same output bound, never enlarged: large 1,024 × 1,024, menu 512 × 128,
icon 128 × 128 and favicon 32 × 32 pixels. The menu logo may be a wide
wordmark, up to four times as wide as it is tall (#1062 slice 2; the
branding asset insert guard enforces the same bounds from schema
migration 0074), in either mode, so one wide image also keeps a
128-pixel-tall menu logo. The review names every size and says whether
this upload changes it. The setup wizard's Parish logo step keeps the
one-image upload.

The header on both portals draws the menu logo beside the parish name
within 16rem × 4rem (256 × 64 CSS pixels), at its own shape and never
enlarged; on a narrow screen it takes at most half the window's width,
and the name wraps beside it. The image carries its drawn size, so its
whole box is reserved before the file arrives and nothing moves when it
does ([no layout shift](../ui-conventions/spec.md#no-layout-shift)). A
menu logo stored before slice 2 keeps its stored size until a new upload.

Campaign images (Campaign navigation) hold the current campaign's theme
artwork: five optional slots, a wide banner and one small icon each for the
Family welcome, Member, financial and closing pages. The banner appears only at
the top of the Family emails, never on a Family page; each icon appears above
the heading of its Family page
([where images appear](../parishioner-portal/spec.md#form-state-and-navigation)).
A new campaign starts with none. Each upload is normalized like a logo (a
banner fitted within 1,024 pixels, an icon within 256), reviewed, and used
only once its configuration request is applied, the same staging and cleanup
rules as logos. Removing a slot's image is its own reviewed change; a slot
without an image shows nothing.
Each Upload and review waits for a chosen image, with its hint after the
button in a line kept while empty
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)); the image
field carries the server's 5 MB bound as `data-max-bytes`, which will refuse
a larger file at the field as it is chosen once that shared check lands
(pending #1069); until then the server refuses it on upload. An icon whose
Family page the campaign does not show (the Financial page icon while
Financial stewardship is off; the Member pages icon while Census and
Ministries are both off) has its upload controls greyed in place, with the
field and Upload named by that reason, and the server refuses such an upload
at the field; an image the slot already holds can still be removed (#563).
The Financial page can still appear, with its icon, for a Family whose
earlier financial answers await a decision after the module was turned off
(an unresolved `financial.removed` conflict); that transitional page is not
a reason to offer the upload.
An upload and Remove this image (offered only for a slot with an image) are
reviewed and applied in the
[review dialog](../ui-conventions/spec.md#review-dialog) (#750): a staged
upload opens it with the image as it will appear. A removal is a
[confirmation](../ui-conventions/spec.md#review-dialog) (#1356), "Remove
this image?", since the slot and its image are on the page behind it: one
sentence names the slot and says Families will see no image there until a
new one is uploaded. Apply posts the signed preview, with the slot
(and staged image) it names, to Campaign images; the page takes no query. A
refused upload marks the slot's field and says why in the slot's own status
line, with no jump to the top (#1410). An upload that expired,
or was staged against settings changed since, is refused in the dialog with
a request to reload the page and upload it again (the slots keep the version
the page was loaded at).
Artwork is presentation, not structure, so it stays editable while the campaign
is live. Each Family email's editor (initial, reminder and confirmation) has a
"Show the campaign banner at the top of this email" checkbox, on by default.

No-reminders WorkGroup (Campaign navigation, #861) names the one
ParishSoft Family WorkGroup whose Families get no Reminders, even if they have
not responded, for example a second Family record kept for a staff member who
already responded as a parishioner; the page's first sentence says so. The
displayed name (#1061, which retired "Reminder WorkGroup" because it read as
if those Families get Reminders) comes from one constant, from which the page
title, menu entry, URL, counts and notices are drawn. It is the
campaign's optional `reminder_workgroup` setting (the stored key and the
`reminder_workgroup_missing` log event keep their names): one plain-language text
field (the WorkGroup's name as ParishSoft shows it; empty turns it off),
saved through the [review dialog](../ui-conventions/spec.md#review-dialog)
(#750) as a confirmation (#1356): **Save** opens "Save the No-reminders
WorkGroup?", which repeats neither the typed name nor the current one shown
under "Now" and says only that, from the next ParishSoft refresh, Families
in this WorkGroup get no Reminder emails (or, when it is cleared, that every
eligible Family that has not responded gets them again). It is editable while the campaign is live because staff mark Families
in ParishSoft during the campaign. Below that, the page says what the
newest refresh found: no WorkGroup set, the name not read yet (it takes
effect at the next refresh; meanwhile the page says how many Families of
the previous WorkGroup the newest refresh found and still skips, or that no
Family is skipped), no ParishSoft WorkGroup by that name (with how many
Families skipped before are still skipped until a refresh finds it), or how
many Families are in it. The review says when the change applies: a clear at
once, the name the newest refresh read at once, and any other name at the
next refresh, with the Families still skipped until then (#1305). What the exclusion does and when it
applies is in the background-processing specification's
[No-reminders WorkGroup](../background-processing/spec.md#family-invitations-and-reminders)
rules; nothing else about those Families changes.

Hosted files (Parish data) is the Administrator-only library of
PDF, Office and image files that page and email content links or shows
with `{{ file.<slug> }}`: upload, placeholder copy, where each file is used,
and single or multi-select deletion that is refused while a file is in use,
through the shared [row actions and confirmation](#row-actions-and-confirmation).
The page and its rules are defined by the
[hosted files specification](../hosted-files/spec.md#admin-page).

Integration pages expose connection status, last check, safe fingerprint, and
Replace/Test actions. Secret replacement requires fresh Google authentication
(or a full-scope
[automation session](../admin-automation/spec.md#secret-replacement)), and
so does reviewing, confirming or removing a settings change, such as the
reply-to address, the backup folder or Slack
([#547](https://github.com/epiphany40223/parishkit/issues/547)): a stale
sign-in gets the step-up, which returns to the settings page with nothing
saved, and the page says so beside Save.
The UI seals the submitted value to its target-specific installer, shows
staged/testing/installing/consumer-acknowledged progress, and never redisplays
it; a typed key or token has the shared
[Show control](../ui-conventions/spec.md#secret-fields). Failure or expiry destroys sealed staging and leaves the old working
credential installed. Slack is optional; its token and channel must be
supplied/removed together, so while Slack is not set up its token field is
required and Save waits for it
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)). Non-secret integration setting changes use the YAML
configuration-request path rather than the credential installer. The ParishSoft
organization ID can change only until the first ParishSoft data load. After
that the page shows it read-only and refuses a different value, with or without
a new key, in plain language: every refresh must read the organization whose
data is loaded, and a refresh for another organization is refused as a tenant
mismatch.

An integration's settings are saved through the
[review dialog](../ui-conventions/spec.md#review-dialog) (#750). An ordinary edit
is a compact review ("Save the Outgoing email addresses settings?", one line
per changed setting, old → new); a refresh schedule change keeps its full
review, the changes in words and when the new schedule takes effect; and
setting up Off-site backups by its folder ("Turn on off-site copies?"),
Remove Slack ("Remove Slack?") and Turn off off-site copies are
confirmations of one sentence. The dialog shows public settings only: never
a key, a key's fingerprint or the key field's signed request. Save with a
new key pasted is not reviewed: it submits as before, the key is checked
with the provider and its install is followed on the page (the recorded
exception). A sign-in that is not recent at Review or Apply is refused in
the dialog with the shared step-up notice, whose Confirm with Google returns
to the settings page; nothing is queued. Once applied, the settings form is
redrawn at the applied version, with the status line under it saying what
was applied. The step-up, the fresh sign-in checked again under the work
lock, the signed preview, the configuration request and its audit are
unchanged, and `pk-admin integration preview` and `integration confirm`
still share the page's review.

Page text describes what the Admin sees happen, never the machinery: no
configuration files, installers, fingerprints or preview lifetimes. Signed
previews still expire, as [preview lifetimes](#preview-lifetimes) describes.
A finished key change (updated, failed,
cancelled or expired) shows on its integration's page for one hour, and any
Admin may dismiss it sooner for every Admin (the dismissal is an audit event);
one that is installed but not selected (its automatic switch failed) is an
error, not news: it says what is stopped (ParishSoft refreshes, email or Slack
alerts) and offers **Finish switching to the new key**, and it keeps showing
until it is resolved; the Admin home page shows it too. Mail meanwhile waits
without spending attempts; see the
[credential installer guide](../../../guides/stewardship-credential-installers.md#replacing-an-integration-key-from-the-web).
While a ParishSoft, Google Workspace or Slack key change is still being
checked or installed, every later settings change waits behind its selection
in the configuration queue, so every Admin page an Administrator opens shows a
banner naming the integration (linked to its page): other changes wait until
it finishes (usually a minute; still there after an hour, it asks the Admin
to have the server operator check the server), and if the new
key is accepted a change saved before then is not applied and must be made
again.
The queue keeps its order: each change is checked against the settings it was
made from, so of two changes made from the same settings the second fails, and
failing the key's selection instead would stop its integration (#456).
The history
stays on the change's details page and in the audit log. The ParishSoft
settings page edits the refresh schedule, as
[ParishSoft refresh schedule settings](#parishsoft-refresh-schedule-settings)
describes.

**Off-site backups (Google Drive)** is an optional integration with no key of
its own: its one setting is a Google Drive folder link, stored in canonical
`https://drive.google.com/drive/folders/<id>` form, and it is added, changed
and removed through the same configuration-request path. It requires the
Google Workspace mail integration, whose delegated user and key the copies
use. Until Google Workspace mail is set up, **Test access** is shown
unavailable, with that reason and a link to set it up. In the local
environment, which never reaches Google Drive, it is always shown
unavailable with that reason; the server still refuses it there. **Test access** queues a check that the Google Workspace credential
installer answers by writing and trashing one small file in the folder, as
the applied delegated mailbox user only (the database refuses a check naming
any other user, so the web process cannot make the installer impersonate
someone else); the page follows the result with the passive live-status
pattern and explains each failure in plain language. A pending check shows
until it finishes; a finished result shows for up to ten minutes and
disappears as soon as the tested folder is saved after it (other
configuration changes don't hide it). The folder that was tested stays in the
folder field and is named beside the result, so the Administrator can save it
without pasting the link again. The Integrations list always links to this
page: "Set up off-site backups" before it is configured, and "Change" after.
The page and the administration home show
the last successful off-host copy; until the integration is added, the home
page invites the Administrator to set it up, and the setup wizard's
completion panel points to it. The copies themselves, their retention and
their alert are defined by the
[backup runbook](../../../guides/stewardship-backup-runbook.md#off-site-copies-to-google-drive).

**Backup encryption key** lets an Administrator set or replace the public key
every backup is sealed to (#198). Backups are sealed to an X25519 public
(recipient) key whose private half the parish keeps off the server; the
operator installs the first public key by hand as the `backup_data`
credential file, and only the one-shot `backup-worker` profile reads it
(see the [backup guide](../../../guides/stewardship-backup.md)). The design:

- **Storage.** The page stores the public key, never a private key, as the
  one setting of a key-less `backup_key` integration record in the applied
  configuration (the same configuration-request path as every other
  integration setting). The record cannot be removed, only replaced. The
  `backup_data` file stays the install-time key: a backup seals to the
  configured key when one is applied, and to the file otherwise.
- **Pick-up.** Each `backup-worker` run reads the active configuration when
  it starts, as it already does for the off-site folder, so the next backup
  after the change is applied uses the new key. No service restarts and no
  file changes.
- **Validation.** The key must be exactly one standard base64 line of 32
  bytes that is a canonical, usable X25519 public key: below 2^255 - 19
  (so never with the top bit set, which about half of all private keys
  have) and not the all-zero or another low-order point. The web and the
  configuration schema both check it. The Administrator must then prove
  they hold the matching private key: the page seals a short random code to
  the pasted key and shows the sealed text, the Administrator runs
  `pk-stewardship backup-prove` with the private key on their key machine
  (off the server, as `backup-keygen` and `backup-open` do) and types the
  code it prints. The code's keyed hash and the pasted key, encrypted under
  a server-derived key, travel only in the page's signed intent, which
  expires after 15 minutes. Nothing unproved is stored, and the pasted text
  is never shown back, so a private key pasted by mistake (which cannot pass
  the proof) never reaches the page source. Only then is the change offered
  for review and confirmation; the review shows the old and new
  fingerprints.
- **Authority and audit.** Only Administrators with the Configure capability
  may use the page. Starting a change and confirming it each need a Google
  sign-in within the last five minutes. When the sign-in went stale while
  the Administrator was at the key machine, a correctly typed code asks for
  the Google step-up first; the proved change waits in the session (a
  proved public key only) and is shown for review on return.
  Confirmation uses the configuration-request path: its
  `config_request_staged` audit entry is written by the database in the
  same transaction as the request, and each later checkpoint
  (`config_request_applied`, `configuration_activated`) is audited as for
  any settings change. The request's patch names the public key, from which
  the fingerprint follows. A pasted text with the top bit set, or that is
  the private half of the key in use, is refused with a warning to treat it
  as exposed.
- **Announcement.** Applying a changed key records a `backup_key_replaced`
  security event in the same activation transaction, like an Administrator
  grant: it names the Administrator, the time and the key IDs before
  and after (the key before is the previous configured key, or before one
  was set the key the newest backup used, when the activating login may
  read it). Its recipients are every address that held Administrator in
  any configuration in effect during the last 30 days (the activation
  trigger's `key_alert_window`): each configuration activated in that
  window, the configuration each of them replaced (so the one in effect
  when the window opened counts, however long ago it was activated), and
  the configuration just before this one. So an Administrator who first
  removes the others cannot keep it from anyone who was an Administrator
  in the last 30 days. Each is emailed through the security-alert path
  (a removed Administrator is asked to contact the parish or its other
  Administrators, since they can no longer acknowledge it), and
  the event stays on every Administrator's home page, audited as
  `policy_security_event`, until a recipient other than the Administrator
  who made the change acknowledges it. That Administrator's own
  acknowledgement clears it only from their own page, even when they are
  the only recipient; then it stays on each other Administrator's page
  until that Administrator acknowledges it. **Residual risk:** a single
  compromised Administrator account, when no other address has been an
  Administrator in any configuration in effect during the last 30 days
  (for example, the others were removed 31 or more days earlier), can
  still replace the key with nobody else told: the
  email and the page reach that account, and the incident below notifies
  the current Administrators and a Slack channel that same account can
  change. Keeping a second Administrator, and checking the backup key's
  fingerprint in each restore drill, are the remedies.
- **The key-change alert.** The scheduler's `backup_key_changed` incident
  exists to catch a key file replaced by hand. A backup whose new key is the
  key in a configuration applied (activated) before that backup completed
  was announced above, so it opens the incident as a WARNING with its one
  System log entry and no notice of its own. Like every WARNING episode it
  escalates to CRITICAL, and is then sent through the configured alert
  routes (Slack when configured), once it has lasted the escalation window
  (15 minutes by default) and is observed again; the episode stays open
  for the two-day key-change window, so a portal rotation is also paged,
  about 15 minutes later. Any other change opens it as CRITICAL at once.
  The web reads only the backup record's ID, completion time and key
  fingerprint.
- **What the page says.** It shows the fingerprint of the key in use (the
  configured key, or, before one is configured, the key the newest backup
  used) and says in plain words that: existing backups stay sealed to the
  old key, so the old private key must be kept until every backup made with
  it has expired (30 daily and 12 monthly sets, about a year); losing a
  private key makes its backups unrecoverable; the private key is never
  pasted into the server; and the new key takes effect at the next backup.
  The page links to the runbook's
  [key rotation](../../../guides/stewardship-backup-runbook.md#replacing-the-key)
  steps.

The setup wizard does not gain a step: the first key is installed with the
deployment, before the wizard runs, and adding a step would reopen the
frozen setup-draft guards and finalization patch. Instead the wizard's
completion panel points to this page, as it does for off-site backups. A
browser-side key-pair generator is not offered.

### ParishSoft refresh schedule settings

The ParishSoft page edits the one
[refresh schedule](../background-processing/spec.md#refresh-schedule) of
full refreshes, replacing the separate frequency, "At these times" and
quick-update fields
([#632](https://github.com/epiphany40223/parishkit/issues/632)). The design
follows common practice for schedules with exceptions (rules, then
exceptions, then a live preview); the
[refresh schedule plan](../../../plans/stewardship/refresh-schedule.md#research-notes)
records the products compared. It is written for a mid-level IT admin: plain
words, no cron text, and every refused or skipped time explained where it
appears. It follows the [in-place controls](../ui-conventions/spec.md#in-place-controls) and
conditional-field rules: nothing on the page reloads it. It follows the
[Basic and Advanced modes](../ui-conventions/spec.md#basic-and-advanced-modes)
convention ([#1122](https://github.com/epiphany40223/parishkit/issues/1122))
and the shared [schedules](../ui-conventions/spec.md#schedules) rules.

**No quick updates in the Admin UI.** ParishSoft's list of changed
Families returns nothing
([#465](https://github.com/epiphany40223/parishkit/issues/465)), so quick
updates bring no data. Administrator decision (2026-10-12, #1122): no Admin
page, help text or the editor offers or mentions them, except a stored
schedule that still has quick rules (below) and history (the Activity log
and task pages of past runs). The capability stays in the code until
[#1147](https://github.com/epiphany40223/parishkit/issues/1147) removes it,
and so do the `pk-admin refresh status` fields `delta_succeeded_at`,
`delta_failed_at` and `delta_refresh`. Production's stored quick times are
removed when this ships, with the editor's "Remove quick updates" action.

**Time zone.** Schedule times are entered and shown in the refresh
schedule's own time zone (the current campaign's, else the parish's, as the
scheduler reads it), a recorded exception to the browser-local rule
([#558](https://github.com/epiphany40223/parishkit/issues/558)): a recurring
wall-clock schedule belongs to the parish's clock, whose daylight-saving
changes decide when each refresh runs and when reminders are due, and a time
converted from another zone would shift twice a year. The page names that
zone beside the editor ("Times are in the refresh schedule's time zone,
America/New_York: the current campaign's, or the parish's when there is no
campaign") and, when the browser's zone differs, says so and shows
each preview time in the browser's zone as well. See decision 18 in the
[plan](../../../plans/stewardship/refresh-schedule.md#open-decisions).

#### Schedule editor

- **Mode.** The shared Basic / Advanced toggle sits at the top of the
  editor. The editor opens as the convention says: Basic when the stored
  schedule equals a Basic choice and was saved from Basic (or before modes
  existed), else Advanced. The mode is stored beside the rules as the
  optional setting `refresh_schedule_mode` ("basic" or "advanced"), written
  only with a changed schedule, and "basic" only when Basic can show it:
  switching modes alone is no change. It is presentation only; nothing that
  runs refreshes reads it.
- **Basic** offers three choices, as radios each described in words; each
  is exactly one set of Advanced rules, written into the rule rows (and
  clearing the skips) as it is chosen:
  - "Nightly only": one full refresh a night, at 02:00 (Full at 02:00).
  - "Every [2, 3, 4, 6, 8 or 12] hours": a full refresh every few hours,
    starting at 00:00 (Full every N hours from 00:00 to 24−N:00). Choosing
    the hours chooses this choice.
  - "Business hours": a full refresh at 02:00, then every 2 hours from
    08:00 to 18:00.

  Advanced → Basic chooses the Basic choice equal to the rows (any order, no
  skips); with none, the shared dialog asks ("Your custom schedule has no
  basic equivalent."). After Switch to Basic no choice is chosen, and Save
  is unavailable, with "Choose a basic schedule to review it." beside it,
  until one is. No choice needs to avoid the Family email windows by hand:
  the switch does that.
- **Advanced** has the rule rows, the skips and Edit as text below, with no
  presets.
- **Rule rows**, each "every [interval] from [time] to [time]" or "at
  [time]", always of full refreshes, with **Add a rule** and **Remove**. Time
  fields use the shared [time entry](../ui-conventions/spec.md#time-entry), with its live reading
  beside the field, and accept quarter-hour times; a kept off-quarter-hour full time is shown as a single time,
  labeled as kept from the earlier schedule. A schedule has at most 64
  rules, said beside the rules; Add a rule is unavailable at that many.
  "Every" rules give any number of times, and the cap keeps a save within
  the server's limit on posted fields. A stored schedule has the same caps
  ([stored schedule](../background-processing/spec.md#stored-schedule-and-upgrade)),
  so the page can always show and save it back.
- **Skip these times**: single times or from–to ranges, at most 16, said
  and enforced the same way.
- **Skip refreshes around Family emails**, with one line saying what it
  does: refreshes are skipped while a reminder is being prepared and while a
  Family email is being sent. It is shown in both modes, below them. It
  starts on when no schedule is stored, and off for an existing schedule
  shown converted (below), since such a schedule never skips today. Basic
  choices fill the rules and leave it as it is.
  The settings page always has a stored document to show: one with no
  schedule settings at all runs the default schedule (02:00 and quick
  updates every quarter hour) and is shown converted, with the switch off.
- **Edit as text**: the resulting full times as one plain list, for
  pasting. Choosing "Use this list" replaces the rules with single times
  and clears the skips, and the page says so beside the list before it
  does. Since each time becomes a rule, the list holds at most 64 times,
  said beside it; a schedule with more is changed with its rules instead.
  The list and the rows stay in step: each check refills it from the rows.
- **Stored quick rules.** A stored schedule that still has quick rules (the
  default schedule, a converted one, or one saved before #1122) opens in
  Advanced, since Basic offers only full refreshes. Its quick rules are
  shown read-only, as words ("Quick update every 15 minutes from 00:00 to
  23:45") with hidden fields that post them back unchanged, so nothing is
  dropped silently and saving other settings keeps them. A note above the
  rules says why they bring no data, with one **Remove quick updates**
  action that takes every quick row out in place; the change goes through
  the usual review ("Quick updates removed: …") and Apply. A Basic choice
  or Use this list replaces them too, and the review says so the same way.
  The seven-day preview, the cost summary and the grid's legend name quick
  updates only while such a rule remains.
- The page states which full time is the nightly refresh and that it runs
  even while Family emails are being sent.
- A schedule saved before this page existed is shown converted to the
  equivalent rules, by the mapping in
  [stored schedule and upgrade](../background-processing/spec.md#stored-schedule-and-upgrade),
  with one line saying so ("Your current schedule, shown as rules. It runs
  exactly as before until you change it."). Saving other settings leaves it
  stored as it is; a change to it lists what will differ, as that section
  describes.

**Problems, at the row that causes them.** Each appears live, names the
times involved, and clears as soon as the row is fixed; the server applies
the same checks on save.

- Two times too close (possible only with a kept off-quarter-hour time,
  since new times are on the quarter hour): at the later row, for example
  "00:00 is only 10 minutes after the 23:50 full refresh; refreshes must be
  at least 15 minutes apart."
- A time off the quarter hour: at its field ("Use :00, :15, :30 or :45").
- A skip that removes the last full time: at that skip row ("This skips
  02:00, the only full refresh; keep at least one full refresh a day").
- A skip that matches no refresh: at that skip row ("This matches no
  refresh"), so a mistyped time is not silently ignored.
- A rule whose last time is before its start time, or a skip range whose
  end is not after its start (both would cross midnight): at that row ("Use
  two ranges: one up to 23:45 and one from 00:00"). A rule whose last time
  equals its start time is allowed and gives that one time.
- Problems of the schedule as a whole (no full rule, more refreshes a day
  than there is room for, more rows than the cap) have no row: the line
  beside Save says them in full, after its links, rather than a message
  above the rows that would push the rows, Add and Save under the pointer
  ([#736](https://github.com/epiphany40223/parishkit/issues/736)).

A row's controls are described by its messages (`aria-describedby`) and
marked invalid (`aria-invalid`) while the row has problems.

While any problem remains in a changed schedule, Save is unavailable (an
unchanged converted schedule is the exception described under
[live checks and saving](#live-checks-and-saving)) and a line beside it names
what is missing, for example "Fix 2 problems before saving: 00:00 is too
close to 23:50; the skip at 12:30 matches no refresh", each linking to its
row.

#### Live checks and saving

The rules are applied in one place, the server. The page script only adds
and removes rows, writes the Basic choices and reads times through the shared
[time entry](../ui-conventions/spec.md#time-entry); it has no copy of the rules, coverage or spacing.

- **Checking.** Once typing pauses (about half a second, as the time entry
  waits), or a row, Basic choice or switch changes, the page posts the editor's
  unsaved state to a read-only check of this page. The server reads it with
  the same form that saves it, runs the shared validator
  (`refresh_rules.check_schedule`, with the kept off-quarter-hour times) and
  the preview and cost functions, and answers with the problems, the
  preview and the summary, which the page puts in place through the shared
  [in-place controls](../ui-conventions/spec.md#in-place-controls). The check saves nothing, makes no
  configuration request and writes no audit entry; it needs the same Admin
  configuration permission as the page and is never cached. Only the answer
  to the latest check is shown; an older one that arrives late is dropped.
- **What is announced.** The line beside Save (problem count, or "About
  1 h 34 min of ParishSoft time a day") is the check's only live region. It
  keeps room for two lines and changes only when its text changes, and the
  cost and freshness summary and the seven-day list are drawn below Save,
  so neither the line nor a longer preview moves Save or the rows under the
  pointer. A row's messages are drawn under its controls, in room kept for
  one line, so a one-line message that comes or goes moves nothing. Every
  other change the page's script makes (messages, the line beside Save,
  the summary, the seven-day list, the "Not checked" line, the "Edit as
  text" error, a time's reading line, and rows added, removed or
  replaced) keeps the page steady at one height on the screen: the
  pointer's, whatever is beside it; with no pointer (keyboard only, a
  lifted touch, or the pointer has left the window), or with the pointer
  over something that does not scroll with the page (the sticky Admin
  menu), the top of the control being edited, or of Use this list when
  it replaces the rows. The script measures each part it changes before
  and after the change and scrolls by the sum of the height changes of
  the parts wholly above that height. In a part that spans it, it adds how
  far the content at that height moved within the part: the same element,
  followed wherever rows added or removed around it put it (over a gap
  between rows, the row just below the gap). Only where that content is
  gone (its row removed, or the part redrawn) is it followed by position,
  and where nothing is left there, nothing is added. So what is under the
  pointer stays where it was on the screen, within half a pixel, except
  where it is itself removed or redrawn, and as far as the page can
  scroll: near the top or bottom of the page the scroll stops at the
  page's edge. Moving focus (to Add a rule after Remove or Remove quick
  updates) never scrolls the page under the pointer; with no pointer, a
  control off the screen is brought just into view. The browser's own
  scroll anchoring stays on for the page, so a note the header shows
  above the page moves nothing; the parts the script steadies opt out of
  it, so it does not add a second adjustment. Those parts take the same
  room as plain blocks. The "Edit as text" error is drawn after Use this
  list, in room kept for one line.
  The preview and the problems at each row are not live regions. This is
  the same pattern as the mail schedules' repeat panel
  ([#469](https://github.com/epiphany40223/parishkit/issues/469)).
- **When a check fails** (the network or the server), the earlier problems
  and preview stay, marked "Not checked since your last change", and Save
  stays available: saving checks everything again.
- **Saving** uses the ordinary configuration request of this page. The
  form turns the rows into `refresh_rules`, derives the stored lists
  (`refresh_rules.stored_settings`) and checks them; nothing posted can set
  the stored lists directly. A request without the editor's rows keeps the
  stored schedule, and a new key is never saved together with a schedule
  change. The review before applying, in the
  [review dialog](../ui-conventions/spec.md#review-dialog) (#750), lists, in words,
  the full times added and removed (and the quick times, while a stored
  quick rule is removed or kept), the switch if it changed, and
  each difference that
  [stored schedule and upgrade](../background-processing/spec.md#stored-schedule-and-upgrade)
  names for a converted schedule, rather than the stored settings.
- A schedule whose rows (in any order), skips and switch are what the page
  showed is not a change: saving other settings with it keeps the stored
  schedule exactly as it is and writes no new keys. Problems on such an
  unchanged converted schedule are still shown at their rows, but they do
  not make Save unavailable for the page's other settings; they block only
  a change to the schedule. For example, a stored full time at 23:50 with
  quarter-hour quick updates converts to a quick time at 00:00 only 10
  minutes after it, and two stored full times 10 minutes apart are too
  close; both run today and keep running until the Administrator changes
  the schedule.

Rejected: a copy of the rules in the page script, kept in step by a shared
table of cases (as the mail repeat panel does). The preview needs the
server anyway, for the Family email windows, the daylight-saving days and
the measured durations, so a second copy would add a way to disagree
without saving a request.

#### Seven-day preview

Below the editor, updated live as it changes, the page shows the next seven
days from today:

- **A grid**: one row per day and 96 quarter-hour cells, with full refreshes
  marked (and a stored quick rule's runs marked differently, by shape as
  well as color, named in the legend only while one remains), excluded
  windows shaded and labeled with their cause ("Reminder: preparing",
  "Reminder: sending", "Invitation: sending"), and the current time marked.
  A kept off-quarter-hour time is marked in the cell it falls in. The grid
  is read-only. Below 600 pixels wide it scrolls sideways inside its own
  box, with the day labels fixed, so the page itself never scrolls sideways.
  The hour labels are in the refresh schedule's time zone, like the list.
  Each shading has its own pattern, named in a legend under the grid that
  lists only the shadings shown; hovering over a marked or shaded cell names
  its quarter hour and what is in it. Where clocks go forward, the hour that
  does not exist is shaded too. The grid is one image whose text alternative
  points to the list, which gives the same refresh times in words.
- **A list** of the same days for keyboard and screen-reader use, one
  collapsible section per day (today open, the others closed, each heading
  giving the day's counts): each day's refreshes with their kind, and every
  time that will not run, struck through with its reason, one time format
  per line ("06:00 full refresh skipped: reminder being prepared", "02:30
  runs at 03:00: clocks go forward").
- Daylight-saving days are flagged in both.

The preview is computed by the server from the same functions the scheduler
uses, from the edited (unsaved) schedule and the campaign's current upcoming
Family emails. Windows for emails not yet sent are estimates, and the preview
says so. The campaign calendar's optional refresh times
([#469](https://github.com/epiphany40223/parishkit/issues/469)) come from the
same preview functions, so the two pages never disagree.

#### Cost and freshness summary

- **Cost**: the estimated daily ParishSoft time and its share of the day,
  per day of the preview and as an average, for example "About 1 h 34 min
  of ParishSoft time a day (5.1% of the day): 10 full refreshes of about 7.3
  minutes." A stored quick rule's updates are counted too while one
  remains. The durations are the
  medians of the last seven days' successful runs of each kind (manual
  refreshes included: the Admin pages cannot tell a run's cause, and a median
  absorbs the odd one), or
  typical values (7.3 and 2.2 minutes, Production's medians in October
  2026), labeled as such, until there are three runs of a kind. Above 25% of
  the day the summary shows a warning; it never refuses.
- **Freshness**: the longest wait for new ParishSoft data over the seven
  days, counting the excluded windows, with the day it falls on, and when
  the alarm would sound, for example "New ParishSoft data arrives at least
  every 8 hours (00:00 to 08:00). If a full refresh is more than 30 minutes
  late, Administrators are alerted." See
  [ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection).

The command line offers the same schedule, validation and preview (see the
[automation action inventory](../admin-automation/spec.md#integrations-and-credentials)).

### Ministries page

The Ministries page lists the current ParishSoft Ministry catalog with each
Ministry's name and whether it is in the current campaign, and supports
searching by name, in a shared
[Admin table](../ui-conventions/spec.md#admin-tables); it shows no Ministry
DUID ([table column order](../ui-conventions/spec.md#table-column-order)).
The page changes nothing. One visible sentence above the table, and the "In
current campaign" column, link to where a campaign's Ministries are chosen:
Campaign settings → Ministry selections, for a draft and a live campaign
alike (see
[Changing a live campaign's Ministries](#changing-a-live-campaigns-ministries));
the longer explanation is in the page's About panel.

There is no local active/inactive state for a Ministry (Administrator
decision, 2026-10-13,
[#1267](https://github.com/epiphany40223/parishkit/issues/1267)). That
removed the earlier Ministry activity management: the Activity column, sort
and filter, the Review activation and Review inactivation buttons and their
review, and the parish-wide override they set (the configuration's retired
`ministries` section; see the
[data specification](../data/spec.md#retired-ministry-activity)).
ParishSoft's own catalog flag is not used either: it is not reliable. The
page answers every POST with 405, and its retired view parameters
(`state`, `sort=active` and a change's `request`) are refused like any other
unknown parameter, with no redirect.

A Ministry is shown to parishioners when it is present in the current
catalog and in the campaign's selected Ministry set; to hide one, remove it
from the campaign's selections. Staff/Admin records, source rosters, past
submissions and existing follow-up requests remain intact. The server
enforces the same eligibility as the UI and rechecks it at submission. A
stale form cannot create new actions for a Ministry no longer offered, and
omission of hidden fields never cancels retained requests. Use the ordinary
changed-baseline reconfirmation flow for stale forms.

A Ministry's name never affects its visibility. A Ministry name is an opaque
string: every page, report, export, email and the Family form shows the full
ParishSoft name (only a workbook's sheet tab is cut to Excel's 31-character
limit), and nothing reads meaning into a prefix. A leading number or
letter (such as `102-Altar Servers`) or a marker such as `X-` is one parish's
naming convention, not ParishSoft's, so it is kept and means nothing to the
application (Administrator decision, 2026-10-10,
[#971](https://github.com/epiphany40223/parishkit/issues/971)). ParishSoft
names are stored as loaded and repaired only where they are shown: on the
Family form, this screen, Campaign settings and Create the campaign. A name
the form can already show is used exactly as before, only trimmed, so valid
labels and Family form digests never change because of this rule. An unusable name is repaired:
whitespace (including tabs, newlines and non-breaking spaces) becomes single
spaces, and control and other invisible characters are removed. That includes
zero-width joiners and non-joiners and bidirectional marks, because they can
hide or reorder text and the form refuses every such character. A name over
the form's 512-character label limit is cut to fit, ending in an ellipsis. A
blank or missing name shows as "Ministry" and its DUID, such as `Ministry 42`.
One unusable name never makes the Family form unavailable.

A repaired Ministry name writes a `source_ministry_name_repaired` warning to
the process log, once per process for each Ministry and name. The warning
carries only the Ministry DUID. It does not appear on the System logs page.
Catalog changes themselves are reported on the Admin home page (see
[ParishSoft Ministry catalog changes](#parishsoft-ministry-catalog-changes)). Fund names on
Campaign settings and Create the campaign follow the same cleaning, with
"Fund" and its DUID as the fallback, and are not logged.

Historical administrative views retain their recorded inputs; current
parishioner pages and previews use the applied campaign selections.

For Ministry leaders, whose Ministries come from ParishSoft roles
([#922](https://github.com/epiphany40223/parishkit/issues/922)), a leader's
Ministries are those present in the current catalog and chosen for the
campaign, so removing a Ministry from the selections removes it from its
leaders' Ministries on their next request. It does not change any
[user's](#portal-user-management) role.

### ParishSoft Ministry catalog changes

Ministries are keyed by ParishSoft DUID, and only a full refresh re-reads the
Ministry catalog; quick updates copy it unchanged (see the
[delta cycle](../background-processing/spec.md#delta-cycle)). The system never
changes a campaign's Ministry selections because the catalog changed. This
section covers telling Administrators about catalog changes and changing a
live campaign's Ministries
([#342](https://github.com/epiphany40223/parishkit/issues/342)).

#### Catalog change notice

When staging validates a full-refresh corpus that has a base snapshot, it
compares the
base's Ministry catalog with the new one by DUID and records the differences
in the snapshot's cursor as `ministry_catalog`, beside the changed-record
counts (see [Manual ParishSoft refresh](#manual-parishsoft-refresh)). The
web role can already read that column, so no schema, grant or new log event is
needed, and the record survives compaction because manifests do. Each entry is
a DUID with names cleaned by the display rule above:

- **added**: in the new catalog only, with its name;
- **removed**: in the base only, with its last name; and
- **renamed**: in both, with the name before and after.

A first load has no base and records nothing; a snapshot with no Ministry
differences records nothing. A quick update copies its base's catalog, so
it is not compared at all, which keeps the comparison off the global work lock
for those runs. Each list keeps at most 50 entries, plus the full count. Like
the change counts, the comparison is display-only: if it fails, staging omits
it, logs a classified `report_shaping_failed` WARNING and the refresh
continues. That process-log line carries `shaping: ministry_catalog` (the
change counts' failure carries `shaping: source_changes`), so the two can be
told apart without a new event name.

The Admin home page shows Administrators with configuration access a
"ParishSoft Ministry changes" panel listing the changes recorded by promoted
snapshots from the last seven days (at most five refreshes, newest first). A
rename shows the old and the new full names and reads nothing into either
(#971). Every entry names its Ministry and never shows its DUID
([table column order](../ui-conventions/spec.md#table-column-order)). The
panel links to Ministries and, with a current campaign, to its Ministry
selections, where an Administrator can remove a retired Ministry.

While the current campaign is in draft, scheduled or open, entries for
Ministries in its selections are marked "in the current campaign", the panel
also links to that campaign's Ministry selections, and it lists, independent of
the seven days, the campaign's selected Ministries that are missing from the
current catalog (Families cannot see them). A closed or archived campaign shows Families nothing, so none of this
applies to it. The whole panel costs the home page one query.

No System logs entry is written: the closed operational-event list is enforced
in SQL, so a new event would be a schema change. A durable
`ministry_catalog_changed` event can be added after the schema freeze.
ParishSoft's own Ministry active/inactive flag is not compared. It is not
reliable enough to decide visibility (see above), but a Ministry turned
inactive there may signal retirement; adding it to the notice is tracked in
[#342](https://github.com/epiphany40223/parishkit/issues/342).

#### Changing a live campaign's Ministries

A live campaign's structural settings are locked (see
[Campaign configuration](#campaign-configuration)), with one reviewed
exemption: while the current campaign is scheduled or active, an
Administrator may change its Ministry selections. The
[Campaign settings page](#campaign-settings-page) leaves a live campaign's
Ministry list open (#1119; there is no separate Campaign Ministries page or
address), offering only the current selections and the Ministries that may
be added now, and reviews a change of it, as an ordinary configuration
request, in the [review dialog](../ui-conventions/spec.md#review-dialog).
A Ministry change is reviewed on its own: a Review that also changes the
end date or the leader roles is refused in the dialog, saying to apply the
Ministries first, because the activation guard below restores a refused
addition only for a request whose one update changes the selections alone.
While background work holds campaign changes, the list stays locked, its
tip saying it can change once that work finishes. A closed or archived
campaign's selections never change.

- **Remove** takes a DUID out of the selections. Answers already given are
  never deleted or rewritten: submissions keep their recorded inputs, join and
  stop requests stay in the follow-up queue until staff close them, and
  Ministry requests (its By Ministry counts and queue) and follow-up packets
  keep showing them,
  marked "No longer in this campaign" (in the follow-up queue, a "Removed"
  badge; see [follow-up workflows](#follow-up-workflows)), while it has a
  request that was not later withdrawn or replaced. A request for a
  [Member no longer in ParishSoft](#follow-up-workflows) does not count
  (#1219): it is left out of those lists anyway, so the Ministry would only
  appear empty. It is back as soon as one such Member is current again.
  Staff still see it where they list those requests (the follow-up queue's
  "Members no longer in ParishSoft" choice, Roster changes to enter and the
  request page). A Family who opens the form again no
  longer sees that Ministry, and resubmitting does not withdraw its requests.
  Adding the DUID back shows those answers unmarked again, including on the
  Family form.
- **Add** accepts only a DUID that is in the current ParishSoft catalog.
  Families see it on their next visit. Families who already
  submitted are not asked again and get no mail.

The review lists each added and removed Ministry, with each one's submitted
answers (current join and stop requests) and open follow-up requests (the
removed Ministries' table has Ministry, then the two counts; each Ministry
is named, never shown by its DUID), and the number of Families with a form open now. Every
open form lists every offered Ministry, so all of those Families get the
usual changed-baseline review before they can submit; the review cannot be
narrowed to fewer Families. No mail is
sent.

Confirming records a `campaign_ministries_requested` audit event for the
configuration request (the request's own status records whether it applied): the Administrator, the selections before
(`previous_ministry_duids`) and after (`ministry_duids`), and the
`added_ministry_duids` and `removed_ministry_duids`. Reports and follow-up read
the selections from the campaign's configuration version in effect, so the
append-only configuration versions hold the change history; there is no other
history table.

The same rules hold in SQL. The campaign activation guard
(`stewardship_campaign_pointer_v1`) exempts only `ministry_duids` from "Live
structural settings are locked". When the selections of a structurally locked
campaign change, it requires the campaign to be scheduled or active, the
selections to stay a sorted list of distinct whole numbers, and every added
DUID to be visible under the candidate configuration: present in the promoted
catalog and in a campaign with the Ministry module. The
configuration installer holds no source grants, so it reads catalog presence
only through the definer function `stewardship_ministry_catalog_v1()`, which
returns DUIDs and nothing else and which only that login may execute. A
removal reads no catalog. Activation repeats the addition check under the lock
source promotion takes; if a refresh dropped the Ministry after the request
was checked, the request fails and the previous
settings stay in effect, so the Administrator reviews the change again.

## Campaign configuration

Admins create a new draft by cloning selected safe values from a historical
campaign or starting empty. Cloning copies content/share/schedule structures
but not dates, Family codes, submissions, deliveries, workflow state, or fund
records without explicit remapping to current ParishSoft IDs.

The paragraph above and the draft-creation rules below (when a new draft may
be created, and creating a successor after Return to Testing) are superseded
for the Admin portal by the [navigation decisions](#navigation-decisions) 11,
18 and 19: the one campaign is created by
[Create the campaign](#create-the-campaign), there is no New campaign
control, and Copy campaign and successor creation stay greyed out and refused
by the server until the single-campaign change (#145) removes them.

Draft creation and every campaign-editor save apply a new authoritative YAML
version and its normalized PostgreSQL snapshot. Runtime lifecycle/mode fields
are changed only by their dedicated database transactions and are never edited
in YAML. Production readiness rejects pending/failed configuration requests or
a YAML/database digest mismatch and pins the exact applied version it locks.

New draft creation is unavailable if any campaign is draft, scheduled, active,
closed, `purging`, or `purge_cleanup_failed`. Independently, it is unavailable
while the global mode is Production or any nonterminal PurgeRequest exists.
Successfully completed purge tombstones do not block a new draft. The Admin
must finish reconciliation,
archive the current campaign, complete the guarded Return to Testing, optionally
complete an exceptional purge and cleanup, and only then create its successor.
The server checks all conditions in the draft-creation transaction; stale or
direct requests cannot bypass them.

Every editor in this section reviews and applies through the
[review dialog](../ui-conventions/spec.md#review-dialog); the
[Campaign settings page](#campaign-settings-page) says how Campaign
settings does.
On a draft, its Review changes button, like Review changes on
[Create the campaign](#create-the-campaign), stays unavailable, with the hint
described under
[Bootstrap and first-Admin wizard](#bootstrap-and-first-admin-wizard),
until at least one module is ticked. While Financial stewardship is ticked
it also waits for both financial periods, their funds and, while shown, the
overlap confirmation. The server's checks are unchanged (#563).

The campaign editor includes:

- campaign IANA timezone plus modules and whole-campaign-local-day start/end
  dates;
- financial period and explicit current/comparison fund multi-select;
- campaign Ministry multi-select, initially all active Ministries;
- Ministry leader roles (see [Ministry leader roles](#ministry-leader-roles));
- editable/reorderable share options with stable IDs and placeholders, on
  Share options, whose **Save** opens a
  [compact review](../ui-conventions/spec.md#review-dialog) (#1356) that
  marks only what changed, one line each (an option added, removed,
  relabelled, given or losing its free-text response, or moved, with its old
  and new position), never both whole lists;
- initial and repeatable reminder date/time, subject, and templates;
- daily/weekly digest local schedules;
- additional-information toggle;
- named content slots with WYSIWYG/plain-text views, each of which can start
  an empty slot from built-in, parish-neutral default text or reset a saved
  slot to it (the editor is only pre-filled; nothing changes until the normal
  save or preview and apply), with the content list marking each slot as
  default, customized or empty (see
  [default content](#default-content) below). Plain-text controls
  appear only where plain text is delivered: every email. The confirmation
  email is the whole receipt message; its former separate closing note is
  folded into it (see [data](../data/spec.md#content-and-email-templates)).
  Web-only page slots always store plain text generated from their HTML, and
  the editor does not offer it;
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
campaign never unlocks them. The exceptions are the Ministry selections
(see
[Changing a live campaign's Ministries](#changing-a-live-campaigns-ministries))
and the [Ministry leader roles](#ministry-leader-roles). The campaign timezone is chosen when the campaign is created
([Create the campaign](#create-the-campaign)), is editable in `draft`, and is one of these structural
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

A live campaign's end date
([#912](https://github.com/epiphany40223/parishkit/issues/912)) changes in
two places, both for the current Production campaign while it is scheduled
or open, before its closing instant and while no background work holds
campaign changes. On the [Campaign settings page](#campaign-settings-page)
the end date stays open, reviewed with the page's one **Review changes** in
the review dialog. While background work holds campaign changes, the field
stays where it is, disabled, its tip saying only that the end date can
change once that work finishes, and a review posted meanwhile is refused as
locked. Its
review is the Dates and mail schedules review of the same change, built from
the saved schedules left as they are; it names the end date before and
after and every digest re-planned for the new dates, and notes that Family
codes and links already sent keep working until the new end date. A date
that has passed, falls on or before the campaign's start date, or
would need the financial-period overlap acknowledged again is
refused, at the field. When the new date would leave an invitation or
Reminder outside the campaign, the review is refused at the field, naming
the combined date-change review above, on Dates and mail schedules, which
the live check below links, with the proposed date filled in and only the
end date open.

The date is checked as it is typed, so **Review changes** is available only when there is something to review and
it can be reviewed. While the field is empty or holds the saved date,
Review waits, with the gate's hint after it (to choose the new end date, or
to change a setting), and no error is shown. Any other date is posted, after a short
pause, to a no-save live check that applies the Review's own checks,
including stranded mail, and records nothing. Typing another date takes any
earlier error away at once, since it was about the earlier date. While the
check answers, Review waits; when it finds a problem, the field is marked in
error (`aria-invalid`, described by the message) and the problem's words
show in a line reserved under the field, so the message never moves Review,
with the link to the combined review for stranded mail; Review waits with
the same words as its hint. A date the check clears takes the error away.
The reserved line is a polite live region (`role="status"`), so a screen
reader hears each answer. Calendar dates in these messages are the
campaign's own and are never shifted into the browser's time zone.

Review is offered only when the change is known to be possible, so a check
that cannot answer (the network, a page out of date, the service
unavailable) keeps Review held: the reserved line and the hint say "Couldn't
check this end date. Check your connection, then change the date or reload
the page.", or, when the answer is that the sign-in has ended (401 or 403),
"Your sign-in has ended. Sign in again, then reload the page." The field is
not marked invalid, as nothing is known to be wrong with the date. The next
edit, or showing the page again (`pageshow`), checks again. Every Review is
still checked in full by the server, whose refusals above still apply.
Either Apply records
the request and, in the same transaction, binds its exceptional end-edit
intent and a `campaign_end_date_requested` audit event (subject: the
request). The configuration installer applies it with that intent's owning
admission; see the [campaign data model](../data/spec.md#campaign). A change
the installer refuses (the campaign changed or closed, a date passed, or
closing or other campaign work started first) ends as **Not applied**, and
its status in the review dialog, and on Change status, says which, or quotes the
reason of an Administrator's cancellation. The end
date never changes the Family token generation, and Family pages and later
emails that show the end date use the new one.

### Campaign settings page

Campaign settings
([#1119](https://github.com/epiphany40223/parishkit/issues/1119)) is one
editor. Every setting it edits (name, year label, time zone, dates,
modules, additional information, Ministry selections and leader roles,
financial periods and funds) is shown once, in the same place and order in
every campaign state. There is no second, read-only copy of the settings.

- **Open or locked in place.** A setting that can change now is open. Every
  other one stays where it is, disabled, with the reason in its "i" tip
  ([conditional fields](../ui-conventions/spec.md#conditional-fields)),
  never in a line of its own: a draft's settings while background work or
  going live holds them; once the campaign is live, every setting but the
  end date, the [Ministry leader roles](#ministry-leader-roles) and the
  Ministry list ([changing a live campaign's
  Ministries](#changing-a-live-campaigns-ministries)); and every setting
  once it has closed. A live end date or Ministry list that background work
  alone holds says it can change once that work finishes. A disabled
  setting is not sent, and the server keeps its saved value whatever is
  posted, so a locked setting never changes through this page.
- **One Save.** The page has exactly one **Save** (its Review),
  unavailable until the form holds a real change, with its hint after it
  ([review dialog](../ui-conventions/spec.md#review-dialog)); with nothing
  open (a closed campaign) the hint says that nothing on the page can
  change now and that each greyed-out setting's "i" says why. Review and
  Apply happen in the review dialog, titled "Review campaign settings". A
  change of one or two short settings (not the Ministries, the financial
  periods and funds or the share options) is a
  [compact review](../ui-conventions/spec.md#review-dialog) instead
  (#1356), titled "Save this change?": one line per changed setting, such
  as "Campaign name: old → new", with the change's notes (the share
  options a removed Financial stewardship discards). Every other change,
  including a live end date with its re-planned digests and a live Ministry
  change, keeps the full review below.
  Once the change is applied, the form is redrawn in place at the new
  version and the status line under it says "Campaign settings changed."
  and when.
- **What one Review covers.** On a draft, one Review covers every changed
  setting, applied as one change, except that a change of the dates alone,
  while the campaign has schedules, leads to Dates and mail schedules'
  combined review instead (and dates changed with other settings are
  refused, to be reconciled there first). On a live campaign, one Review
  covers a changed end date and changed Ministry leader roles together: one
  configuration request with one campaign update holding both values,
  applied together or not at all (decision 2 on #1119). Its review lists the
  end date, the digests re-planned for it and the leader roles, with the end
  date's notes. A Ministry change is the exception: it is reviewed and
  applied on its own, with its impact (the Ministries to add and remove,
  what each touches and the Families with a form open now), and a Review
  that combines it with another change is refused in the dialog.
- **Refusals.** A Review refused for a value (a required field, an end date
  that has passed or strands mail) stays on the form, the field marked with
  the reason and the summary shown. Any other refusal (settings changed
  elsewhere, an out-of-date preview, background work, mail being sent now,
  a change the installer refused when it ran) shows in the dialog. A Review
  that changes a setting locked since the page was loaded (background work
  began, the draft went live, the campaign closed) is refused in the dialog
  with that setting's reason, so no change is ever quietly dropped.

### Dates and mail schedules

The regular **Dates and mail schedules** page
([#878](https://github.com/epiphany40223/parishkit/issues/878)) is a list. Under
its heading, About panel and campaign name, one line gives the campaign dates
(with a link to change them in Campaign settings while they may still change).
There is no separate campaign window panel. Every editor this page opens
follows the shared [schedules](../ui-conventions/spec.md#schedules) rules.
**New scheduled email**, above the
table, opens the [New scheduled email page](#new-and-edit-scheduled-email), and
the link beside it the read-only [Campaign calendar](#campaign-calendar).

The **Scheduled emails** table
([#448](https://github.com/epiphany40223/parishkit/issues/448)) is an
[action table](../ui-conventions/spec.md#row-actions-and-confirmation) with one row per saved schedule.
It opens in sending order: dated sends by date and time (the Initial invitation
first at a tie), then the digests, with the saved ID as the final tie-break, so
the order never changes between loads. Its **When** heading sorts it in place
(sending order, or the reverse), as any [Admin table](../ui-conventions/spec.md#admin-tables) heading
does. Reminders are numbered in sending order, as [Family email
sends](#family-email-sends) numbers them, whichever way the table is sorted. The
columns are a selection box, the schedule, when it sends (a browser-local time,
like every time in the Admin portal, so the heading names no zone; a digest
shows its day and its next send), the email's name, a status, the last send and
the row's actions. For a Family send with results, the last send gives its
delivered and failed counts with a link to Family email history. Work that
blocks a change (in progress or with an uncertain result) makes a schedule
Preparing before the send time and Sending after it. Otherwise it is Upcoming
before the send time; after it, Sending while any of its work is pending or
running. Once that work is finished it is Sent when any of its email was
delivered or prepared, "Sent (failed)" when its only results are failures (with
the same link), and Done when it settled every Family without an email of its
own (the coalesced and empty dispositions). Otherwise it is "Not sent yet" (the
scheduler, a hold or catch-up may still send it); a digest is Repeats. The
status reads the same counts-only schedule work summary as the review, never
Family data. A short line under the table states the rules between schedules.

A schedule that has run or has work prepared (Preparing, Sending, Sent, "Sent
(failed)" or Done) is read-only: its row is muted and has no selection box and
no actions, because moving or removing a send that already happened would make
a new revision and plan it again. Every other row has **Edit** and **Delete**.
Edit opens the [Edit scheduled email page](#new-and-edit-scheduled-email)
for that schedule. Delete, and **Delete selected** for the ticked rows, open the
confirmation dialog. It names the schedule ("Delete Reminder 2?") or the count
("Delete 3 scheduled emails?") and says that email already sent is not recalled
and that emailed Family codes and links keep working. Confirming removes the
schedules in one configuration change. The server builds the change as the
review does (the same validation of the remaining schedules, the same blocking
work count and the same signed intent, rechecked under the work lock when it is
recorded) and refuses, changing nothing, when a chosen schedule is unknown or
read-only, when another schedule would break a rule without it (reminders left
without an initial invitation), or when any of its sends is in progress or
uncertain. Removing a schedule cancels only its planned sends that have not
started, and never touches sent mail or Family codes and links. Once the change
is applied the table is redrawn in place and the dialog closes; a refusal, or a
change that failed to apply, is shown in the dialog and nothing is redrawn. A
change still waiting after 30 seconds is shown in the dialog with a link to its
status.

Changing the campaign dates while they may change starts on Campaign settings,
which sends the proposed dates to this page when schedules exist. With proposed
dates, the page shows the date-change review instead of the list: the proposed
dates, the same table, and every schedule's editor (opened from its row, past
sends read-only), so mailings that would fall outside the new dates are changed
or deleted in the same reviewed change (see [campaign
configuration](#campaign-configuration)). Choosing an editable row there (its
name, or anywhere else on the row) opens that schedule's editor in place,
without a page load, marks the row as being edited and moves focus to the
editor; an opened editor stays open. Saved schedules' editors start closed,
except one with an error or an unsaved change after a refused preview, and each
row's control reports whether its editor is open (`aria-expanded`). Closed and
read-only editors still post their saved values, so the posted form, its
validation, the review and reminder planning are unchanged. When the browser
restores a value into a closed editor (Back), `pageshow` opens that editor
again; a value restored into a read-only editor is put back to its saved value.

The date-change review shows and takes send dates and times in the browser's
time zone ([#558](https://github.com/epiphany40223/parishkit/issues/558)), as
[New and Edit scheduled email](#new-and-edit-scheduled-email) do: each saved
schedule's editor is filled from its moment
in the browser's zone, the page names that zone beside the dates, and the
server converts each posted row back to the campaign's zone with the browser's
zone the page sends. A row posted as it was shown keeps its saved values, and
without a usable zone Review changes stays unavailable and the server refuses
the post. The campaign dates are calendar dates and are not converted. A draft
campaign's time zone may change in the same review. A schedule whose send date,
time and day of the week are left as shown (even if its email changed) then
keeps its campaign wall-clock time in the new zone, as a time zone change always
has, so its send moves and the review lists the moved sends. A schedule whose
send date or time changed converts from the typed browser time into the new
zone, so it sends at the moment typed. Two rows that show the same time can
therefore send an hour apart after a zone change: an untouched row moves with
the new zone while a changed one keeps the moment typed, and the review shows
each row's moment. A dated send typed at a moment the campaign's clocks show
twice (the hour they repeat when they fall back) is refused on its send time,
since a schedule keeps only a campaign wall-clock time and the scheduler reads
it as the first of the two; the Admin chooses a time outside that hour. A typed
time the browser's own clocks skip reads forward, as every browser-local time
does, and no moment falls in an hour the campaign's clocks skip. A digest's
row also posts the moment the page drew it from, so a page drawn before a
daylight-saving change and posted after it still reads as unchanged. These
rules are the shared conversion, so New and Edit scheduled email follow them
too. The review dialog, like every schedule review, shows when each
schedule sends only as moments in the browser's zone (a digest also says it
recurs), never the saved campaign-local date and time.

The date-change review reviews and applies in the
[review dialog](../ui-conventions/spec.md#review-dialog). **Review changes**
is available when the page opens with proposed dates (they are a change) and
becomes unavailable if the dates are put back to the saved ones and no
schedule is changed; an added schedule left blank is no change. A refusal of
the values posted (a mailing outside the new dates, a field's value) keeps
the dialog shut: the form shows its error summary, marks each field and opens
each schedule's editor with an error. Any other refusal (a stale page or
another tab, sends in progress, an expired preview) shows in the dialog. The
dialog lists the window change and each schedule's change with its affected
work, the same review as New and Edit scheduled email; while sends block the
change it offers no Apply. **Apply** records the change with the same signed
preview, blocking-work recheck under the work lock, stale and two-tab checks
and audit as before; a live campaign's end-date rules, reminder planning and
emailed Family codes and links are unaffected by how the review is shown.
Once the change is applied the reader returns to the list without proposed
dates, whose status line says "Dates and mail schedules changed." and when.

#### New and Edit scheduled email

**New scheduled email** (`/admin/campaign/schedules/addition/`) and **Edit
scheduled email** (`/admin/campaign/schedules/<schedule>/`, opened by a row's
Edit) are one page: the mail type, send date, send time, day of the week and
Email to send of one schedule, with the rules above. Its review and Apply follow the
[review dialog](../ui-conventions/spec.md#review-dialog); once the change is applied the reader returns to
Dates and mail schedules, whose status line says what was applied. Edit
opens it filled in
from the saved schedule, whose mail type is fixed; a schedule that is unknown or
read-only is refused there too, even from an old link (a GET or a POST).
The editor follows the shared
[schedules](../ui-conventions/spec.md#schedules) rules. Planned
([#1129](https://github.com/epiphany40223/parishkit/issues/1129)):
the schedule editors here, including reminder repeats and the Admin digests,
adopt the
[Basic and Advanced modes](../ui-conventions/spec.md#basic-and-advanced-modes)
convention.

The send date, time and day of the week are shown and typed in the browser's
time zone, like every Admin time ([#558](https://github.com/epiphany40223/parishkit/issues/558),
the [browser-local time rule](../ui-conventions/spec.md#dates-and-times)); a note
beside the fields names the zone, and no field or help text names the
campaign's. A schedule still keeps campaign-local values, which the scheduler
resolves. Edit's page carries the schedule's moment (a digest's: its next send)
and the page script fills the fields from it in the browser's zone; the server
converts what is posted, with the browser's zone the page sends, back to the
campaign's zone through the same moment, so the rules above (inside the
campaign dates, after the initial invitation, no two Family mailings at once)
compare the moments the Admin chose. A digest's time and weekday are converted
at its next send; where the two zones change their clocks on different dates, a
digest keeps its campaign wall-clock time between them. A typed time follows
the shared [daylight-saving rule](../ui-conventions/spec.md#schedules); a
saved campaign-local time that the campaign's own change skips stays as saved
and the scheduler resolves it. An
Edit page posted with the date, time and weekday it showed keeps the saved
values exactly, so saving it unchanged never moves the send, even when the two
zones change their clocks at different instants. A page posted without a
usable zone is refused with a hint to check the computer's time zone setting;
while the browser reports none, the button stays unavailable and says why.
The button reads **Review changes** on Edit and **Add the scheduled email**
on New. It is unavailable until the form differs from what it was
drawn with (on Edit, the saved schedule as shown in the browser's zone, so a
time typed another way that reads the same, such as "6am" for 06:00, is no
change; on New, the blank form), with a short hint after it; there is no
"nothing has changed" review (the server still refuses an unchanged Edit,
only as a safeguard). It sends the page to the same review as every schedule
change: the server builds the complete set of schedules (the saved ones
unchanged, plus this one added or changed) and validates, previews and signs
it exactly as the schedule commands do. A refusal of the values posted keeps
the review dialog shut and marks the form, each problem at its field (or,
for a rule between schedules, in the error summary); any other refusal (a
stale page, a schedule that has run meanwhile) shows in the dialog. The
dialog lists the change and its affected work (the same review the
date-change review shows; a repeat lists every reminder it adds, and the
dialog's body scrolls). A New that adds one schedule that sends once,
without an edited email, is a
[confirmation](../ui-conventions/spec.md#review-dialog) instead (#1356):
nothing exists yet, so the dialog, "Add this scheduled email?", repeats none
of the form's values and no counts (all zero for an add) and says only
"This schedules the email to send once, at the date and time chosen;
nothing is sent now." A recurring schedule, a digest, a repeat or an edited
email keeps the full review, since its send times, generated reminders or
email sample are not on the page. While sends block the change it says so and offers
no Apply. **Apply** records the change, with the same signed preview,
blocking-work recheck and audit as before; reminder planning and emailed
Family codes and links are unaffected by how the review is shown.
Apply also checks again, against the database's clock (the clock sending
uses), each send time the change sets that was still ahead at Review
([#1464](https://github.com/epiphany40223/parishkit/issues/1464)). A review
stays valid for 15 minutes, so a time chosen a few minutes ahead can pass
before Apply, and that email would then send at once. If any such time is at
or before the clock, Apply is refused in the dialog: "A send time in this
change has passed since it was reviewed, so that email would send at once."
Nothing changes; the Administrator chooses a later time and reviews again.
The check is made when Apply records the change; the installer activates
it moments later. This check only refuses: it never moves a time and
never sends. A send time the change leaves as it was is not checked, and
neither is one already past at Review (the edited reminder itself, which
the ordinary Edit may move into the past). The same check applies to every schedule review that Apply
confirms on Dates and mail schedules, including series edits and the
date-change review.
**Cancel** on the page returns to the list without saving.

New and Edit both offer **Edit this schedule's email**, a checkbox under Email
to send ([#446](https://github.com/epiphany40223/parishkit/issues/446)). Ticking it
shows, below it, the email's subject, HTML source and plain text with
"Generate plain text from HTML", filled in from the chosen email, and **Reset to
the default text**, which fills the mail type's built-in default (the text
"Start from the default text" uses on Pages and emails). Choosing another email
fills the fields again only while they hold no edits, so typed text is never
replaced; the help under the fields says so, and that Cancel and Edit again
start over from a saved email. The fields take the same cleaning and rules as
the Pages and emails editor (placeholders, hosted files, and an invitation's or
reminder's `{{ family_code }}` and `{{ family_url }}` in both parts), and a
refusal shows at its field with the page's other errors. The visual editor and the campaign
banner choice stay on Pages and emails. Unticked, the fields are not sent.
Changed text is always saved as a **new email that only this schedule sends**:
the one reviewed change adds that email and points the schedule (its email and
subject) at it, so the email it sent before stays exactly as it was for every
other schedule that sends it; one no schedule sends any more shows as "No
schedule" on Pages and emails, where it can be removed. Ticking the box is not
a change by itself: the email's fields count toward Review changes like the
schedule's own, compared with the schedule's saved email, so ticking it
without editing leaves Review changes unavailable. While "Generate plain text
from HTML" is ticked the plain text is not sent and does not count either, so
an older email whose saved plain text differs from what today's generation
shows is no change by itself. Text left as the chosen email's adds nothing,
compared after the same cleaning a save applies, so an older email whose saved
text today's cleaning would rewrite (including such plain text) is still
copied when it is reviewed ticked with another change, as re-saving it on
Pages and emails would
([#832](https://github.com/epiphany40223/parishkit/issues/832)). If the whole
configuration refuses the change, or the email is too large to review, the
refusal is shown at the email's HTML source, linked from the error summary.
The review dialog shows the new email as a fictional sample beside the
schedule's before and after, and applying it re-plans the schedule
as any schedule change does (see [Family invitations and
reminders](../background-processing/spec.md#family-invitations-and-reminders)):
planned sends not started yet are replaced, sends in progress block the change,
and email already sent is not recalled. The change touches only content and
schedules, never a Family's code, link or their sealed credentials. Edit
already refuses a schedule that has run or has work prepared, so its email
can't be changed either.

On New, the mail type is chosen on the page, so the fields are checked against
the mail type posted, and Reset fills that type's default. Until a mail type
is chosen, "Edit this schedule's email" and Reset are unavailable and a
reserved line under the checkbox says "Choose a mail type first."
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)); the
line keeps its space, so choosing a type moves nothing. Clearing the type
unticks the box. The plain-text help states the invitation and reminder rule
for every type, and the help under the fields says the text is checked
against the chosen type. Changing the mail type, which narrows Email to send, fills
the fields again like choosing another email: from the email still chosen,
or empty while none is, so another type's text is never left to be saved as
this one's. An email must still be chosen; the new email is compared with it.
On New the fields start empty, and the change gate compares them with the
blank form like the rest of New. With **Repeat**, every reminder the rule adds
sends the one new email, so they share it; the help and the review say so.

Under the fields, **Send a test of this text** sends them as they are now as
a test, without saving anything
([#446](https://github.com/epiphany40223/parishkit/issues/446), slice 3c).
The page script posts the subject, HTML source, plain text and "Generate
plain text from HTML" with the chosen email to
`/admin/campaign/schedules/test-email/`, with no page load
([in-place controls](../ui-conventions/spec.md#in-place-controls)). The
server checks the text as a save does (the same rules as above), against the
chosen email's mail type, which is the page's, since Email to send only
offers that type's emails. It then sends the test
[Preview and test email](#production-transition) sends, with this text in
place of the saved text: the same fictional sample (parish values, made-up
Family values, the campaign banner), TEST subject and banner, the Testing
recipient only, the same admission (a Testing draft, or Production with live
email delivery paused), one pending test per campaign, the acknowledgment
while an earlier test may have arrived, and the same record and audit. The
record names the chosen email, the one the text started from, so the test
is listed under that email's Recent test results and, in Testing, counts
for Go-live readiness like a test of that email. Nothing is saved (no change
request, email or schedule change), and no Family's code, link or sealed
credential is read or made. A reserved status line after the button says
why it is unavailable ("Choose an email to send first.", the reason the
campaign admits no test mail now, or "Tick the confirmation below to send
another test.") or what the send did: queued, with the time in this
browser's zone; the text's problems, each named by its field; or a refusal,
such as a test already on its way. The acknowledgment of an uncertain
earlier test shows below that line when the page is drawn with one or a send
is refused for one. A send that gets no answer is not repeated, and the line
says to check the Testing mailbox first. The command line has no
counterpart: `test sample` tests a saved email.

On New, choosing Reminder offers a **Repeat** checkbox
([#469](https://github.com/epiphany40223/parishkit/issues/469)); other mail
types do not repeat (one Initial invitation; the digests repeat by themselves).
Checking it replaces the send date with the repeat fields: how often (every day;
every week on the chosen days; or every month on a day of the month or on the
first, second, third, fourth or last chosen weekday) and the first and last
dates (the campaign's dates to start with). The send time and Email to send
apply to every date. Only the fields of the chosen repeat show. As the Admin
types, the page lists every date the rule gives, with its weekday, and says
which it will not add and why: outside the campaign dates, not after the
initial invitation, at the same moment as a saved invitation or reminder, over
30 reminders from one rule, or over the campaign's limit of schedules (the
schedule form set's maximum, 100). A short summary above **Review changes**, the only live region of
the rule, says how many reminders the rule adds, or what is still missing:
"Enter a send time to check these dates" until the time reads, since the timed
checks cannot run without it, and "Choose the email to send" without an email.
Two lines are reserved for it (a phone wraps the longer messages) and the date
list sits below the button, so neither moves the button. A month without the
chosen day (the 31st in September) is skipped and listed as skipped, not
moved. Every day comes with a note that each reminder goes to every Family that
has not responded and that sending spreads out or waits near the mailbox's
daily limit. A rule spans at most a year. Dates are calendar days and the time
is a wall-clock time, both in the browser's zone as above, and each date is
converted on its own, so a 9:00 AM reminder stays at 9:00 AM in that zone across
a daylight-saving change. The page compares each date's moment with the saved
mailings' and the campaign's first and last moments, as the server does. Review changes posts the
dates that fit as ordinary reminders, each its own schedule, reviewed,
confirmed and delivered exactly like reminders added one at a time (see [Family
invitations and
reminders](../background-processing/spec.md#family-invitations-and-reminders)).
The server checks each date as it checks any reminder, and refuses more than 30
at once. The page script's expansion and the server's
(`campaigns/recurrence.py`, used by the command line's
[repeat entry](../admin-automation/spec.md#schedules-and-configuration)) both
run the shared case table
`tests/stewardship/fixtures/recurrence_cases.json`. Repeat is offered on New
only.

The same change also remembers the rule as a **reminder series** holding
exactly the reminders it adds, as the command line's `"remember": true` does,
so they can be changed together later. Delivery never reads a series: its
reminders are planned and sent exactly as they would be without it. The
server reads the rule from the posted repeat fields and remembers it only
when it is a valid rule, every added reminder's date in the campaign's zone is
a day the rule gives (a browser far enough from the campaign's zone can move a
send to the next or previous campaign day) and the configuration is in the
users form; otherwise the reminders are added as before and nothing else
changes (the default, Administrator may change it). The review says when the
reminders are kept as a series and names its rule in plain words.

Edit of a reminder that belongs to a series offers **Apply this edit to**, a
choice above its date: **This reminder only** (the default), **This and
following** and **The whole series**. The choice is described by "Part of: "
and the series' rule in plain words, such as "Every Monday and Thursday,
October 5, 2054 – November 13, 2054", and by one help line that is always
shown. "This reminder only" is the ordinary Edit: moving the reminder to a day
its rule does not give takes it out of the series, and deleting it from the
list does too. The other two grey the send date in place (a [conditional
field](../ui-conventions/spec.md#conditional-fields), not sent), and the
greyed box shows this reminder's own date, with help under it that the date
is used only with "This reminder only". Every reminder they change keeps its
own date and takes the send time and Email to send chosen here (with "Edit
this schedule's email", the one new email they then share). Reminders that
sent or are being prepared don't change. Choosing a scope is not a change by
itself, so Review changes stays unavailable until the time or email changes.

When the page is drawn, the server plans each series scope once with nothing
changed. A scope it would refuse for a reason the page already knows is drawn
unavailable, with that reason beside it
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)): a
reminder from the cut date on has sent or is being prepared, or another
reminder from the cut date on has passed its time without sending (a "missed"
reminder). A series change would add a missed reminder again on its past
date, so it would send right after Apply. The page refuses that, naming the
missed reminder's date. Change that reminder on its own, or choose "This and
following" from a later one. Only the reminder being edited may itself be
missed, as on the ordinary Edit. The command line keeps its documented
behaviour for missed reminders.

The send time is typed in the browser's zone and converted on each date the
change covers (the unsent reminders from the cut date on), as a repeat's dates
are. A time that would move any of them to another day in the campaign's zone
is refused at the choice. A time in the hour the campaign's clocks repeat is
refused at the send time, as on the ordinary Edit. So is a new time that
would put another reminder the change adds at or before now (a 9:00 AM
reminder today moved to 7:00 AM at 8:00 AM), which would send at once: the
refusal names that reminder's date and new time. Only the reminder being
edited may move into the past, as on the ordinary Edit. A time and email left
as shown leave each reminder's own. The server plans the change with the command
line's [series edit](../admin-automation/spec.md#schedules-and-configuration)
(`accounts.series_edits.plan`, "this and following" from this reminder, or
from the series' first reminder that has not sent): the same cut date, kept
members, removed and re-added reminders, and series records, and the same
review, signed preview and confirmation. An in-flight or unknown send still
blocks. A refusal of the plan is shown at the choice, in the page's own plain
words. A scope posted for a reminder that is no longer in a series, and a
scope with no time or email change, are refused in the review dialog, like
an unchanged Edit. The review lists every reminder removed and added, and says
what happens to the series:

- the new send time (in this computer's time) or the new email, from the cut
  date;
- for a split, that the reminders before the cut date stay in the series
  (with its rule, now ending the day before) and that the changed ones become
  a new series (with its rule).

Changing a series' rule, and deleting a series, stay on the command line for
now. Editing from the [campaign calendar](#campaign-calendar) is a later slice
of #469.

#### Campaign calendar

**Campaign calendar** (`/admin/campaign/schedules/calendar/`,
[#469](https://github.com/epiphany40223/parishkit/issues/469)), opened by the
link beside New scheduled email, shows every email the saved schedules send,
by date. It is read-only: it changes nothing and offers no New or Delete. It
reads the current schedules: the Initial invitation, each reminder, and every
day or week of the Daily and Weekly Admin digests, expanded the way the
scheduler evaluates them (a Daily Admin digest's last send is the day after
the campaign closes).

- **By month** draws one month grid per month, Sunday first, from the
  campaign's first day (or its first send, if earlier) to its last day (or
  its last send). Each day lists its sends with their time and schedule name,
  and notes Today and the campaign's first and last days ("Campaign opens",
  "Campaign closes") in words, not only in color.
- **Every email by date** is the same sends as a table, the grids' text
  equivalent: when, the schedule ("Reminder 2"), the email it sends, its
  status and Edit. The status is the scheduled emails table's, from the same
  counts-only work summary; each day or week of a digest shows the digest's
  "Repeats" until its time has passed, then "Past". Every send of a digest is
  listed, however long the campaign.

A send's time is browser-local, like every Admin time (the
[browser-local time rule](../ui-conventions/spec.md#dates-and-times)), and the
grids place it on its day in the browser's zone; table cells use the compact
date form. The campaign's dates are calendar dates and are never shifted. A
send of a schedule that has not run links to its Edit scheduled email page,
in the grid and through Edit in the table; a schedule that has run, or has work
prepared, is listed muted and not linked, as on the list, and so is a digest's
send whose time has passed (editing the digest changes only its later sends).
A muted send is told apart by its status, in words in the table and read to
screen readers in the grid. A week view,
Production activation and go-live milestones, ParishSoft refresh times, the
backup window and editing from the calendar are later slices of #469.

### Ministry leader roles

Campaign settings shows the campaign's Ministry leader roles with its
Ministry selections, only while Ministry stewardship is on (#922). They are
checkboxes, one per role label the current ParishSoft rosters use (each
label's whitespace collapsed as the comparison reads it, and only labels that
can be saved), plus any saved name no roster uses now; with no saved value
the default roles
(Chairperson and Staff) are ticked. The help says, in plain words, that a
Ministry leader sees the reports and follow-up of each Ministry where they
hold one of these ParishSoft Ministry roles, matched by an email address
their ParishSoft Member record lists, and that signing in still needs a
sign-in rule. At least one role is required. Leaving
the roles in effect unchanged writes nothing, so a campaign keeps following
the default until someone changes it.

A draft changes them with its other settings. A live (scheduled, active or
closed) campaign that asks about Ministries keeps them open on the
[Campaign settings page](#campaign-settings-page), where the page's one
Review may change them alone or with the end date, as one change. Who leads
which Ministry, and why, is in the
[data specification](../data/spec.md#ministry-leaders).

### Default content

Pages and emails start with built-in, parish-neutral default text.
[Create the campaign](#create-the-campaign) fills every applicable page and
email slot in the same versioned save as the campaign (a clone copies its
source's content instead), and a later campaign save fills only slots that
became applicable (for example, a newly enabled module). It never replaces
text the Admin saved, and it keeps a slot the Admin explicitly cleared empty.
The content page can also fill every empty applicable slot, including cleared
ones, and, after an explicit confirmation, reset every applicable slot to its
default in one versioned save; schedules that send a replaced email follow
its new revision. A fill result names each slot it kept because it holds the
Admin's own text, and the content list marks every slot as default,
customized or empty, and
[flags saved text the sanitizer now cleans](../data/spec.md#content-and-email-templates).
Each default passes the normal content validation described under
[content and email templates](../data/spec.md#content-and-email-templates).

On Pages and emails, each mail type's emails are a short Admin table shown
whole ([#446](https://github.com/epiphany40223/parishkit/issues/446)): the
subject (linking to its editor, with the start of its ID when two of that
type share a subject), the start of its plain text, the saved schedules that
send it, named as on Dates and mail schedules ("No schedule" when none
does), default or customized text, and its test page (Preview and send
test, a link). Each table is an action table
([row actions](../ui-conventions/spec.md#row-actions-and-confirmation),
[#879](https://github.com/epiphany40223/parishkit/issues/879)): Edit opens
the email's editor, and an email no schedule sends also has Delete and a
selection box for Delete selected. Delete asks in the shared dialog and then
records one configuration change that removes every chosen email, exactly
as the editor's "Remove this selected content" control removes one (which
stays). The page follows that change until it is applied and redraws the
email tables in place. The server refuses the whole request, removing
nothing, if any chosen email is sent by a schedule, is the confirmation
email, or is no longer listed, or if the page is out of date. The
confirmation email has no schedules and stays a single entry, with Edit
only.

### Create the campaign

System setup ends without a campaign
([#142](https://github.com/epiphany40223/parishkit/issues/142)). The campaign
is created afterwards, by an Administrator, from **Create the campaign**: the
next step Home offers while the deployment has never had a campaign (the
Campaign setup menu entries stay greyed "No current campaign" until then). It
is not a multi-campaign control: once any campaign exists it is not shown,
and the server refuses it, as it refuses Copy campaign and successor drafts
until #145 and the close-out wizard (#527).

Create the campaign is step 1 of the campaign wizard, which follows the
general [wizard rules](../ui-conventions/spec.md#wizards)
([#1404](https://github.com/epiphany40223/parishkit/issues/1404)): before
the campaign exists, step 1 is this page, and once it is created, step 1 is
Campaign settings, so no question is asked twice.

Status: the page (its admission, first page and what follows confirming) and
its guided steps are built
([#142](https://github.com/epiphany40223/parishkit/issues/142)), with #1404's
quick wins ([#1407](https://github.com/epiphany40223/parishkit/pull/1407))
and Create the campaign drawn as step 1 (#1404 PR 3).
The rest of #1404 is planned, and each part below marked "(planned, #1404
PR n)" describes the design, not yet the pages: direct saves on draft steps and sub-pages (PR 4); Load
ParishSoft data as a step (PR 5); the Pages and emails item states and the
suggested schedules (PR 6); and Back asking before it discards, with
Go-live readiness's summary lines (PR 7). Setup ends without a campaign, so
a newly set-up deployment reaches the page; a deployment set up before #142
already has its campaign and never does.

**Admission.** Create the campaign follows the
[draft-creation rules](#campaign-configuration) above, with three further
conditions: no campaign row may exist in any state, so it never creates a
successor; no campaign work or credential hold may be in progress
(background campaign work, or a Testing cleanup); and a ParishSoft snapshot
must have been promoted, so there is a catalog to choose Ministries and funds
from. The page, its review and its confirmation each check these, the
confirmation under the same lock that records the request, so a stale page
or a direct request cannot bypass them. Home shows what it already knows:
before any ParishSoft data is loaded, Create the campaign is shown unavailable
with the reason that ParishSoft data must be loaded first (Refresh now is
beside it); while campaign work or a Testing cleanup holds it, it is shown
unavailable with that reason; otherwise it is offered. Once a campaign row
exists, in Production mode, or during a restore review, Home keeps its
no-campaign text instead.

**First page.** It collects the campaign's basics in the ordinary campaign
form: name, modules, start and end dates, Ministries (from the loaded catalog,
initially all active ones) and, for Financial stewardship, the financial
period and current and comparison funds. As step 1 it
is the Campaign settings form itself, so it also asks the
[Ministry leader roles](#ministry-leader-roles), starting on the configured
list, and every Campaign settings question is asked here once; leaving the
configured list ticked saves no list of the campaign's own, as on Campaign
settings. It draws the
wizard's stepper, "Step 1 of N: Campaign settings", with every later step
unavailable, each named "Not available yet: Create the campaign first.". N counts Share
options, because the modules are not chosen yet; once the campaign exists
without Financial stewardship, Share options is no longer a step. Its action
row has no Back and the one primary **Create the campaign and continue**. The
campaign time zone may be any
catalog zone, not only the Parish zone (Administrator, 2026-10-15,
[#1330](https://github.com/epiphany40223/parishkit/issues/1330)): the menu
starts on the zone the browser reports when the list offers it (matched
through the browser's own zone names, so Chromium's `America/Louisville`
selects `America/Kentucky/Louisville`), and otherwise on the Parish zone. A
browser reporting UTC (or an alias such as `Etc/UTC`) counts as reporting no
zone, since that is usually a server, VM or privacy setting. A
form drawn again after a refusal keeps the zone that was posted. The start and
end dates are days in the chosen zone, and its boundaries are resolved in it.
The form's **Create the campaign and continue**
button is unavailable until every required field is filled in ([prerequisite
gates](../ui-conventions/spec.md#prerequisite-gates)). Its Review is a
[confirmation](../ui-conventions/spec.md#review-dialog), not a full review
([#1356](https://github.com/epiphany40223/parishkit/issues/1356)): every value
of the new campaign is in the filled-in form, so the review dialog, titled
"Create the campaign?", lists none of them again. It says what Apply does
("This creates the campaign as a draft in Testing mode; no email is sent.")
and how many pages and emails the campaign starts with in the default text,
and its Apply records one versioned configuration request, in the same
format as every other campaign save, that adds the draft campaign with its
[default content](#default-content) and the default share options. A Review
refused for the form's values stays on the form; any other refused Review or
Apply, including one refused because a campaign now exists, is explained in
the dialog. A plain load of the page while creation is not admitted is
refused whole, on an error page headed "Create the campaign is unavailable"
(not the out-of-date-form heading its 409 status would otherwise give), with
the same explanation and a link to Campaign settings.

**After confirming.** Apply is answered with the page naming the request,
whose region holds the request's live status, polled from its Change status
page; the dialog follows it. The page's own address can't be redrawn once
the change is applied (by then the campaign exists, so the form has nothing
left to create), so the dialog redraws the page's region from the address
naming the request instead, which becomes the page's address, and the status
line under the region says the campaign was created and when. That address
draws the change's status, not the form, and once the campaign exists it
links to Campaign settings, saying the campaign was created only when this
change was applied (a change that failed says a campaign already exists). The configuration installer applies the request, and the
database checks admission again at activation: a new campaign record becomes
the current campaign only when the current-campaign pointer is empty in
Testing mode and every earlier campaign is archived or purged, the rule every
new draft follows, so of two creation requests confirmed at once one applies
and the other fails. Once the request is applied, the dialog does not stay on
the change's status: it opens the next Campaign setup step that is not done
yet ([#1404](https://github.com/epiphany40223/parishkit/issues/1404)), Share
options with Financial stewardship and Pages and emails without it, whose
status line says the campaign was created and when; once Load ParishSoft
data is a step (planned, #1404 PR 5), that next step is Load ParishSoft
data, and step 1 is never opened again after creation. A reload of the address
naming the request, and Change status, still offer
**Load the campaign's ParishSoft data**, which requests a full ParishSoft
refresh and opens that refresh's progress page. Every Campaign setup step and
sub-page also offers it, at its top, until the campaign has its Families;
pressed there, it returns to that step, which then says "ParishSoft data is
loading; Go-live readiness waits for it." while the refresh is under way. The refresh can only be
requested after activation, because a refresh reads the current campaign when
it is requested. The offer disappears once the campaign has its Families. It
reads the campaign's giving window and creates the campaign's Families and
Family codes, as a refresh does for any current
draft. If no one presses the button, the next scheduled quick update cannot
continue from its cursor, because the giving window changed, and runs as a
full refresh instead. Go-live readiness, which already requires the
population to match the current ParishSoft data, stays incomplete until a
full refresh has finished.

**Guided steps.** While the current campaign is a draft in Testing mode
with no go-live started, the
Campaign setup pages show Administrators a stepper with the setup wizard's
look, drawn by the same template for its
[step bar](../ui-conventions/spec.md#step-bars) and list
([#1402](https://github.com/epiphany40223/parishkit/issues/1402)), and follow
the [wizard rules](../ui-conventions/spec.md#wizards): Campaign
settings (its basics, Ministries and funds, one form since
[#1119](https://github.com/epiphany40223/parishkit/issues/1119); Create the
campaign before the campaign exists), Load ParishSoft data (planned, #1404
PR 5), Share
options (shown only when the campaign's modules use it), Pages and emails,
Dates and mail schedules, Campaign images, Preview and test email (for the
initial invitation's current revision), and Go-live readiness. Each step is
named after the page it opens and is that existing page, not a separate
staging page, so every save is that page's ordinary review, confirm and
apply. Only those pages and their sub-pages draw the stepper; Preview and test
email draws it only for the revision its step opens. Campaign settings counts
as completed when the campaign is created. Share options, Pages and emails,
Dates and mail schedules and Campaign images count as completed once a change
confirmed on that page has been applied since the campaign was created: a
change of the share options alone (not the default options Campaign
settings writes when Financial stewardship is turned on or off); of the
campaign images alone; of a mail schedule (added, removed or edited, even
with the new email it sends) or of only the campaign's dates and time zone
(a change Campaign settings can also make, which counts too); or of the
pages and emails (anything else that changes content, including the
schedules re-pointed at an email's new revision). They also count as
completed once the Administrator pressed the step's Continue while its form
held no change, keeping it as is
([#1404](https://github.com/epiphany40223/parishkit/issues/1404)): that is
stored ([`CampaignSetupReview`](../data/spec.md#campaign)), and a later change
elsewhere never undoes it. Preview and test email counts as completed once a
test of the invitation's current revision was accepted, and Load ParishSoft
data once the campaign has its Families (a Family campaign record exists,
the read the load offer already uses), so it needs no stored review
(planned, #1404 PR 5). Defaults
(the default text and share options that Create the campaign adds) never
complete a step by themselves, and Go-live readiness completes only by going
live.

**The steps, page by page.** What each step adds to the
[wizard rules](../ui-conventions/spec.md#wizards):

- **Campaign settings, after creation.** It shows every saved value once and
  is already completed. A change of the dates alone, while the campaign has
  schedules, still leads to Dates and mail schedules' combined review
  ([Campaign settings page](#campaign-settings-page)); on a draft those
  schedules have sent nothing.
- **Load ParishSoft data** (planned, #1404 PR 5). It opens Refresh from
  ParishSoft in wizard mode, saying "A full refresh adds the campaign's
  Families, Family codes and giving. It usually takes a few minutes. You can
  go on to the next steps while it runs; Go-live readiness waits for it." Its
  primary is **Load the campaign's ParishSoft data**; once the load is
  requested, its progress shows in place and the primary becomes Continue to
  the next step while the load runs. A failed load shows its reason and
  **Try again**, never the plain offer again, so it never looks as if no load
  was started. The scheduled full refresh stays the fallback. The load is
  started by that button, never automatically at creation.
- **Share options** and **Campaign images.** The existing pages in wizard
  mode. Removing an image keeps its confirmation, because it discards a file
  ([wizard rule 5](../ui-conventions/spec.md#wizards)).
- **Pages and emails** (item states planned, #1404 PR 6). The list keeps its
  order (pages, then emails) and gives each item one state, as text: **Default
  text**, **Edited**, **Kept as is** (reviewed in the wizard without a change)
  or **Empty** (a slot of a module turned on later). Each row's name opens its
  editor, a sub-page; the editor's **Keep as is and go back** marks the item
  Kept as is, and **Save and go back** saves it. Either returns to the list
  with focus on the row and a status line ("Welcome page saved.", "Welcome
  page kept as is."). The editor's preview, Start from the default text and
  Remove stay as they are. The list's Continue is always available and
  accepts every item not yet edited as is; the per-item marks are optional,
  and Go-live readiness still flags content that must be saved again.
- **Dates and mail schedules** (suggested schedules planned, #1404 PR 6).
  With no schedule saved, the page proposes a starting set under "Suggested
  schedules", drawn as the list draws saved schedules and marked "Not saved
  yet": the initial invitation on the campaign's start date at 9:00 AM in the
  campaign's time zone, one reminder seven days before its end date at
  9:00 AM, and the weekly digest at its configured default; like every
  schedule, they are shown in the browser's time
  ([dates and times](../ui-conventions/spec.md#dates-and-times)). Its primary is
  **Add these schedules and continue**; **Continue without schedules for
  now** (a link) keeps the step as is, and Go-live readiness then still asks
  for exactly one initial invitation schedule. The schedules are proposed,
  never created silently at creation, because they depend on dates the
  Administrator may still change. New and Edit scheduled email and the
  Campaign calendar are its sub-pages.
- **Preview and test email.** Its primary is the page's own test until
  that test is accepted (its Continue, below).
- **Go-live readiness.** The last step; the go-live flow (#462)
  follows from it unchanged. It gains one summary line per step (planned, #1404
  PR 7), such as "Share options: kept the defaults" or "Pages and
  emails: 3 changed, 14 default text", so no step needs a review dialog of
  its own; the outward-facing review stays in Confirm Production.

The stepper's summary names the next step that is not done yet ("Next:
Pages and emails"), as text, and offers **Leave campaign setup**, which opens
Home; Home then leads its Next steps with **Continue campaign setup:** and the
first step not done yet. On a screen 64rem wide or wider the full step list
is shown open; narrower, it is the collapsed disclosure
([step bars](../ui-conventions/spec.md#step-bars)). Each step page ends with
one action row: **Back** (secondary, the previous step) and one primary
control. On Campaign settings, Share options, Pages and emails, Dates and
mail schedules and Campaign images it is **Continue to** the next step not
done yet (on Dates and mail schedules with no schedule saved, **Continue
without schedules for now**; Go-live readiness still lists the missing
initial invitation schedule): it records the step kept as is and opens that
step. While a form on the page holds a change (on Campaign images, a chosen
image not uploaded yet), Continue is unavailable, with
a hint after the row in a line that is always reserved, so the page's own
Save (with its review dialog, for now) stays the way on, until a
direct save makes it Save and continue (planned, #1404 PR 4). Preview and test
email offers Continue to Go-live readiness only once its test is accepted,
because the test is the step's purpose; Go-live readiness, the last step,
has Back only. Back and Continue never save, discard or submit the page's
form, so an Admin who leaves a page without confirming its review keeps the
campaign as it was. Every step stays a link (Preview and test email only
while there is an invitation to test; until then Back and Continue skip it).
The pages a step opens are its sub-pages: Campaign settings' content
history, Pages and emails' editor (with its preview), and Dates and mail
schedules' New and Edit scheduled email and Campaign calendar. They draw the same stepper with their
step current, and their action row is **Return to** that step. The stepper is
an [in-place](../ui-conventions/spec.md#in-place-controls) region: after a
[review dialog](../ui-conventions/spec.md#review-dialog)'s Apply (Share
options, Campaign images) the page's redraw from its own address, or an
in-place refresh, shows the new completion without a reload, and a reload
shows the same. Change status, once a change that completes a step is
applied, offers Return to that step for a sub-page's change (an email's
editor, a mail schedule) and Continue to the next step not done yet for any
other; the change that created the campaign offers
Load the campaign's ParishSoft data instead. The stepper is presentation
only; each page still enforces its own prerequisites. Go-live readiness is
the last step, so the go-live flow (and its redesign in #462) follows on
from it unchanged. Once the campaign leaves draft (it is scheduled or goes
live), or once a go-live has started, the stepper is no longer shown.

**Existing deployments.** A deployment that already has a campaign, such as
Production, never sees Create the campaign, and its campaign, being active,
never shows the stepper; nothing about its setup, campaign, Family codes or
emailed links changes.

### Production transition

Going live is a dedicated workflow, not a toggle. It requires:

- valid, complete campaign configuration and no overlapping active campaign;
- a commit instant before the campaign closing instant, with the preview
  explicitly identifying whether the result will be `scheduled` or `active`;
- a successful full ParishSoft refresh with expected-tenant and scope
  validation that started less than `source_stale_seconds` ago, at Start and
  again at confirmation; the [Go live page](#go-live-page) changes this rule
  (Start accepts an older one because it queues the go-live's own, and
  confirmation requires that one or a later one);
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
  audit payloads, Testing outbox detail, Testing
  [Family engagement](../data/spec.md#family-engagement) rows, and Testing
  ScheduleOccurrence/ScheduleFulfillment rows, with exact submission,
  distinct-Family, and message/result counts plus an Admin-only Family list;
  and
- completion of the gated asynchronous cleanup below, followed by fresh Google
  authentication and a typed Production confirmation (from the command line,
  a full-scope
  [automation session](../admin-automation/spec.md#fresh-gated-actions-from-the-command-line)
  stands in for the fresh authentication).

Readiness also lists the current campaign's saved pages and emails that
today's sanitizer would change, the rows **Pages and emails** marks
"Re-save recommended" or "Can't be sent until fixed"
([retained content](../data/spec.md#content-and-email-templates)),
each linking to its editor
([#838](https://github.com/epiphany40223/parishkit/issues/838)). Only an
invitation or reminder that a mail schedule selects and that is marked
"Can't be sent until fixed" is a readiness problem
(`family_email_unsendable`), because sending refuses it; the rest send their
cleaned form, so they are listed without blocking. Production confirmation
re-reads the same check.

The Admin-only Family list (the **Testing submissions** page) opens only
while the campaign is the current Testing draft, as does Go-live readiness,
the only page that links to it. It names each Family as the other Admin
tables do, the surname then the heads ("Squyres, Tracy and Jeff"), with the
Family DUID in its own column; both columns sort on the server, the name by
surname and then the whole name, as the Family codes directory sorts
([#932](https://github.com/epiphany40223/parishkit/issues/932)). Afterwards the list answers with a plain
notice, "Testing submissions are only available before go-live.", as a 410
refusal: signing in again cannot help, so it never shows the generic
sign-in-again refusal
([#867](https://github.com/epiphany40223/parishkit/issues/867)).

Testing deliveries do not count as live. After readiness and inventory, the
Admin explicitly acknowledges that cleanup is irreversible and starts it on
the [Go live page](#go-live-page), which needs fresh Google authentication
([#547](https://github.com/epiphany40223/parishkit/issues/547)): until the
sign-in is fresh, the page offers **Confirm with Google** in place of the
start button, and the request records that fresh sign-in instant. One
transaction creates a durable ProductionTransitionRequest, acquires the
campaign go-live gate, records the inventory/aggregate described below and
queues an idempotent cleanup task; with the [Go live page](#go-live-page) it
also queues the go-live's own full ParishSoft refresh. The gate rejects new
Testing submissions,
test sends, campaign content/configuration changes, and Testing campaign work;
existing authenticated pages explain that go-live is in progress. The same
transaction invalidates the rehearsal epoch and its sessions; the cleanup
inventory includes rehearsal credential detail under the
[credential lifecycle](../architecture/spec.md#family-credential-security).
Readiness/final confirmation verify that invalidation and completed credential
cleanup without changing stable Production Family codes. Operational
notifications and ParishSoft refreshes continue; with the Go live page,
scheduled refreshes
[wait for the go-live](../background-processing/spec.md#refreshes-wait-for-go-live).

The worker deletes the recorded Testing corpus in bounded, checkpointed batches
and exposes progress/retry in the web workflow. It rechecks the gate before each
batch and never touches production or operational rows. When cleanup completes,
the request becomes `cleanup_complete`; deleted rows are not restored if a
later check fails or the Admin cancels. Cancellation before activation releases
the gate and leaves the campaign in Testing with whatever cleanup completed.

From `cleanup_complete`, once the inactive Family links are prepared on
current ParishSoft data (today by the Administrator on the Family links page;
with the Go live page, by
[go-live sequencing](../background-processing/spec.md#go-live-sequencing)),
fresh authentication made after cleanup completed and typed confirmation
invoke a short final transaction. Under the request, campaign, and global locks it
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
activation also inserts one durable ActivationCatchUpDemand and its idempotent
TaskRun, recording activation time as the due-work cutoff and the applicable
schedule/source/readiness versions. It does not enumerate Families, schedules'
individual occurrences, or outgoing messages under these final locks. The
background [activation catch-up workflow](../background-processing/spec.md#activation-catch-up)
materializes and coalesces due work in bounded batches. A scheduled pre-start
activation leaves due-work creation to ordinary boundary/scheduler processing.

The readiness preview's immediately-due count is computed before confirmation
using the shared coalescing planner and labeled with its input versions/as-of
time; coalesced semantic slots are shown separately. Final confirmation validates
the preview's relevant version guards instead of regenerating its per-Family
plan while holding global locks. Changed inputs require a refreshed preview.
Actual execution rechecks current eligibility and reports any resulting count
differences. The Admin page distinguishes **Campaign active** from **Preparing
initial campaign mail**, shows durable catch-up progress/failures, and offers
safe retry. Catch-up failure after activation does not roll back Production or
silently clear the scheduled-mail preparation hold. Family portal submissions
remain available under normal campaign rules.

Cleanup or final-transition failure leaves global mode Testing and never creates
a partially live campaign. The UI states separately that completed cleanup is
not rolled back. Retry resumes from durable cleanup checkpoints or reruns the
short final transaction; cancelling releases the gate without restoring deleted
Testing data. Only the pre-start `scheduled` result offers **Cancel go-live**
(formerly Withdraw from Production), and not while
[live delivery is paused](#live-delivery-pause). That action
requires fresh Google authentication (or a full-scope
[automation session](../admin-automation/spec.md#fresh-gated-actions-from-the-command-line)),
an entered reason, and explicit
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
the campaign date interval. It renders a fictional safe sample, routes only to
the configured Testing recipient, does not create or satisfy a scheduled
Family-mail occurrence, and does not bypass Family portal date gates. Its
successful provider delivery satisfies the test-Family-mailing readiness check,
making the `scheduled` state reachable before the start date. A test of
unsaved text sent from New or Edit scheduled email
([#446](https://github.com/epiphany40223/parishkit/issues/446)) counts as a
test of the email it started from; saving any change afterwards changes the
configuration, so the check then needs a new test. Go-live readiness says so
beside the check.

For staff validation, a Testing-mode draft campaign also offers a link from the
test send, **Send this email to chosen real Families (Testing recipient only)**,
to the page **Send this email to chosen Families**: a freshly authenticated
Administrator enters up to ten Family IDs, reviews each Family's eligibility
(each reviewed row names the Family as the active parishioner family directory does, the
surname then the heads of household), and confirms that real Family data goes to the Testing recipient. Each eligible
Family's real message for a template its invitation or reminder schedules use is
prepared with that Family's own Testing credential (reusing the credential
scheduled Testing mail already issued for that Family, which later scheduled
Testing mail also reuses), carries the Testing banner and goes only to the
Testing recipient. It needs an active Testing credential set, which exists once
the campaign dates include today and the source population is current; at most
ten such sends are in progress per campaign. It creates and satisfies no
scheduled occurrence or fulfillment, does not satisfy the readiness check,
cannot be resent, and is deleted by Testing cleanup like all other Testing mail.
It does not open the portal outside the campaign dates.
When the Administrator's Google sign-in is older than the fresh window, the
review shows how long ago they signed in and offers **Confirm with Google** in
place of Send. The reviewed Family IDs, never credentials, are kept in the
server-side session across that
[step-up](../architecture/spec.md#identity-and-session-security), so the same
review returns for the Administrator to confirm; nothing is sent automatically.
A stale confirmation is refused with a page that says nothing was sent. After a
send, the page confirms how many tests were requested and refreshes their
status while any is still on its way. A test waiting behind another Testing
email to the same Family that is marked "Not sure it arrived" says so and
points to Outgoing mail; until that email is resolved there, the test waits.

Every other fresh-authentication action refused from a submitted form says that
nothing was done or sent and offers the same step-up; forms that carry a secret,
such as a replacement key, keep nothing and ask for it again.

The transition changes the global system mode. Because only one campaign can be
active, the selected campaign is the sole target of the readiness calculation;
historical records keep their recorded mode. Ordinary Testing-to-Production
transition cannot clear or bypass the independent `restore_review_required`
gate.

#### Go live page

> **Status:** this subsection is the target of
> [#462](https://github.com/epiphany40223/parishkit/issues/462). Until its
> implementation slices land, go-live still uses the separate readiness,
> Testing cleanup, Family links, confirmation and progress pages, with their
> 5-minute page limits, as the
> [launch runbook](../../../guides/stewardship-launch-runbooks.md#production-activation)
> describes. The page keeps its registry name, Go-live readiness, in the
> [page-name table](#page-names-and-placement) until the slice that builds it
> renames it Go live there and in the code together.

Going live is one guided page, **Go live**, at the campaign's existing go-live
address (today's Go-live readiness). It replaces the readiness, Testing
cleanup, Family links and final confirmation pages, and needs no one to time a
click against the [refresh schedule](../background-processing/spec.md#refresh-schedule).
The Administrator acts twice: once to start, acknowledging the irreversible
Testing cleanup, and once to confirm Production, each with a fresh Google
sign-in. The system does everything in between. The page is a stepper with
the same Next/Back and completion marks as the setup wizard, and each step
shows only what applies now:

1. **Check.** What is still missing for go-live, each item with a link to the
   page that fixes it, and the ParishSoft, mail, public web address and test
   email checks listed at the top of this section. The **Check readiness and
   public web address** button runs the DNS and origin check and shows its
   result in place, beside the button. The step also shows, as warnings that
   do not block, when the last backup completed and whether debug logging is
   on. This step is the last step, Go live, of the Campaign setup stepper
   that Create the campaign
   ([#142](https://github.com/epiphany40223/parishkit/issues/142)) adds, and
   only this step shows that stepper's Back; once go-live has started, steps
   2 to 5 show only the Go live page's own steps, because campaign settings
   are locked by the go-live gate.
2. **Start.** The cleanup inventory (counts first, then the Admin-only Family
   list, each Testing Family named as the active parishioner family directory names it), the
   acknowledgement that Testing cleanup cannot be undone, and **Start
   go-live**. Starting needs a fresh Google sign-in, as it does today
   ([#547](https://github.com/epiphany40223/parishkit/issues/547)); a stale
   one gets **Confirm with Google** in place of the button. The server
   re-checks readiness at the POST. Every problem still blocks except one: a full
   refresh that is merely older than `source_stale_seconds`
   (`full_refresh_stale`), which Start waives because it queues the go-live's
   own full refresh in the same transaction. A missing full refresh
   (`full_refresh_required`), a tenant or scope change
   (`source_scope_changed`), a Family population that does not match the
   current data, and every other problem still refuse Start.
3. **Preparing.** One progress view, refreshed in place, with one line per
   stage: Testing cleanup, the go-live's full ParishSoft refresh, and
   preparing the inactive Family links. A stage that waits says what it waits
   for in words ("Waiting for the ParishSoft refresh to finish"), never a
   time to act by. A failed stage shows its sanitized reason and **Retry**,
   which retries the same task and changes no data.
   If ParishSoft data changes after the links were prepared, for example
   after a manual refresh, the system
   [prepares them again](../background-processing/spec.md#go-live-sequencing)
   by itself and the line says so.
4. **Confirm Production.** One impact preview, built once the links are
   prepared: whether the campaign becomes `scheduled` or `active`, the
   active, eligible and no-email Family counts, and the live messages due at
   once. Below it, the typed `Production` box and **Confirm Production**.
   When the Administrator's Google sign-in is older than the fresh window
   **or was made before Testing cleanup completed** (the SQL confirmation
   guard requires a sign-in after cleanup's `complete` event), the button
   reads **Confirm with Google** and uses the existing
   [step-up](../architecture/spec.md#identity-and-session-security): the same
   preview comes back after the sign-in and the Administrator presses Confirm
   Production once more. Nothing is confirmed automatically after a sign-in.
5. **Activation.** The [Production activation](#production-transition)
   progress: the campaign scheduled or active, and initial campaign mail
   preparation, refreshed in place until it finishes.

**Server checks instead of page age.** No step refuses a form for the time it
has been open. The preview shown stays valid until something it shows
actually changes: each POST re-reads the inputs and compares them with the
digest the page was built from. The final confirmation token, which the SQL
confirmation guard requires to be at most 5 minutes old, is minted at the
Confirm Production POST from that re-checked preview, so the guard's limit
never reaches the Administrator. A real change is refused with a message that
names it and shows the updated step, never "Check this value.":

- "ParishSoft data changed since this preview. Review the updated preview,
  then confirm." when the inputs digest differs;
- "The campaign start time has passed, so it will become active at once.
  Review the updated preview." when the target changes from `scheduled` to
  `active`;
- "The campaign has ended, so it cannot go live." at or after its close;
- "ParishSoft refreshes resumed while this waited." when the refresh hold
  has ended (below);
- "Another Administrator stopped go-live." or "Another Administrator already
  confirmed Production." when the request moved on in another tab;
- "Confirm it's you with Google, then confirm again." when the sign-in is
  older than the fresh window or predates cleanup's completion.

**Freshness during a go-live.** Each Start, and each **Refresh and prepare
again** below, begins an **attempt**, numbered from 1 within the transition
request. An attempt queues one full refresh, the attempt's refresh. From
the attempt's start,
[scheduled refreshes wait](../background-processing/spec.md#refreshes-wait-for-go-live)
until the attempt's **hold end**, which that section defines: 60 minutes
after the attempt's refresh has promoted and cleanup has completed, and
never later than 3 hours after the attempt started (the cap, for a refresh
that never promotes). The exact rule, used by every
go-live check after Start (the links step, the confirmation readiness
re-check in `collect_readiness`, and the confirmation deadline) in place of
today's `source_stale_seconds` expiry, is: the source is ready when the
current full snapshot is the attempt's refresh or a later full refresh that
started after the attempt began, it passes the tenant and scope checks, and
the current instant is before the hold end. This is deliberately looser
than today's rule, which expires 30 minutes (`source_stale_seconds`) after
the full refresh started: during the hold no scheduled refresh can change
the data, so the go-live may take up to an hour after its refresh, or three
hours in all. `source_stale_seconds` itself is unchanged and still governs
readiness before Start.

**After the hold ends.** When the hold end passes before confirmation,
whether 60 minutes after the refresh or at the 3-hour cap, scheduled
refreshes resume, the system stops preparing links for that attempt, and the
Confirm step shows "ParishSoft refreshes resumed while this waited" with two
choices: **Refresh and prepare again**, which starts the next attempt (a new
refresh, a new 60-minute hold measured from its promotion and capped 3 hours
after it started, and new link preparation, without a second cleanup), and
**Stop go-live**. There is no limit on attempts; each needs the
Administrator's click.

**Stop go-live.** Every step before confirmation offers **Stop go-live**. It
asks for confirmation, says that Testing data already cleaned up is not
restored, then cancels cleanup and any link preparation through their
existing cancellation paths, releases the gate and the refresh hold, and
returns to the Check step with the campaign still a Testing draft. **Cancel
go-live** remains the name of the
pre-start withdrawal after confirmation, described above.

**Navigation and old addresses.** The Campaign setup menu keeps one **Go live**
entry while the campaign is a Testing draft or a go-live is in progress, and
**Production activation** afterwards. The retired cleanup, links and
confirmation addresses are removed with no redirect
([#864](https://github.com/epiphany40223/parishkit/issues/864)). The
[command line](../admin-automation/spec.md#production-transition-and-withdrawal)
follows the same two actions.

**The current Production campaign is unaffected.** All of this exists only
while the current campaign is a Testing draft going live. While a deployment
is in Production with its campaign live, it has no go-live request, so it
never holds a refresh, never runs the sequencing and never shows these steps;
that campaign, its refreshes, mail and emailed Family codes and links are
unchanged. The flow applies to the next campaign's go-live, after the current
one is archived and the deployment returns to Testing
([#527](https://github.com/epiphany40223/parishkit/issues/527)).

### Restore release

Restore release is a distinct workflow, not a reuse of the `draft`-to-`scheduled`
Production transition. The operator's `restore-begin` command starts the
review after a restore, as the
[restore specification](../operations/spec.md#restore) describes. While the
restore gate is active, the UI can queue only the restricted maintenance work
defined there.

**No Family code or link changes (hard rule).** Families keep using the codes
and links they were emailed before and after the backup. A restore never
creates, replaces or cancels a Family code, link, token generation or
credential epoch, and release activates nothing new. A link that was emailed
after the backup was taken may be one the restored database does not hold;
"send again" sends that Family the link the backup restored.

**Held emails.** During the review, on the Administrator's request, the system
holds the current campaign's Production invitations and reminders that were
due when the site was restored and may have gone out after the backup. Nothing
falls due during the review in this sense: the gate kept it from being sent,
so it is sent normally after release. Work is read for the schedule's current
revision and the campaign's current Production cycle, as the planner reads it.
Held are:

- an email the restored data still has live work for (pending or running);
- with no work yet, the email for every Family, whatever its restored
  eligibility (the hold is inert for a Family that cannot be sent it);
- an invitation whose latest attempt failed, or was skipped as undeliverable
  or ineligible: a later deliverability change would retry it, and the lost
  history may already have done so. "Send again" on such a hold only lets that
  retry happen; it sends nothing until the Family's deliverability changes
  again.

These are left out:

- an email already decided: fulfilled, or kept back by an undecided or
  assumed-sent hold of any restore;
- one being handed to the provider at the backup (submitting, or delivery
  unknown), whose outcome the ordinary
  [delivery resolution](../background-processing/spec.md#family-invitations-and-reminders)
  settles after release (the Admin-only delivery warning);
- other work that ended before the backup without sending (a reminder that
  failed or was skipped, any coalesced email), which nothing revives.

Submission receipts and schedule revisions made after the backup are not held:
a receipt belongs to a response the restore lost, and a lost revision is
simply not in the restored schedule.

A hold only suppresses that one email: planning, preparation, the claim guard,
dispatch and the sender all skip it, and it never applies to operational
notifications. An Administrator settles a hold in one of two ways:

- **Assumed sent**, with a short evidence note. It never counts as a provider
  success, and an unsent copy prepared before the backup is cancelled.
- **Send again**, after a duplicate-risk confirmation. The ordinary planner
  sends it once the site is released, coalesced with any reminder then due.

Each decision is final: an assumption may already have cancelled the unsent
copy, so a later "send again" would send nothing. "Send again" is refused
while another restore's hold still keeps the same email back. Each decision is
versioned, append-only and audited.

An undecided hold stays in force after release, and an undecided invitation
also keeps back every reminder of that Family, so a hold left undecided during
the review can still be decided after release, by the same rules
([#757](https://github.com/epiphany40223/parishkit/issues/757)). After
release a decision is admitted for an undecided hold of any restore, but only
for the current campaign: the planner never acts on another campaign's holds.
"Send again" on a held reminder is refused once a later reminder for the
same Family was delivered, is being sent or has an unknown delivery, even a
reminder removed since, because it would arrive out of order; that hold can
only be assumed sent. The decision guard enforces this for every login,
including a decision the web login inserts directly. If a later reminder
reaches the Family after "send again" was decided, the Family dispatch check
never sends the earlier one. The Held emails page below takes these
decisions. Besides the listing, the web login may insert a hold row directly; the
hold guard admits one only during a review and for its current restore, and a
hold only keeps mail back.

**Release.** A freshly authenticated Administrator confirms release. It is
refused while any email still needs a hold (the list must be found first, and
again after a source refresh adds Families), so every hold can still be
decided during the review. The transaction then clears the gate and records
the release; mode and the current campaign stay as restored. Start and
close boundaries that came due during the review are then applied by the
ordinary [boundary policy](../background-processing/spec.md#campaign-lifecycle-boundaries),
and the holds keep every possibly-sent email from going out again. A current
campaign that is `purging` or `purge_cleanup_failed`, or a campaign work gate
that is preparing or running, blocks release for explicit operator recovery.

Settling a hold and releasing both require an enabled Administrator whose
session signed in with Google within the last five minutes, the same rule as
other high-impact actions; the session and sign-in instant are recorded with
the decision. Like the backup request guard, the SQL check proves a live,
recent session row, not that a real Google round trip happened: a compromised
web process could present one. Listing held emails needs no fresh sign-in,
because a hold only keeps mail back.

**The Restore review page.** During a review the access gate sends an
Administrator to the Restore review page (`/admin/maintenance`); Staff and
Ministry leaders see a plain "The site is being restored" notice, and Families
the neutral maintenance page. The page shows when the backup was taken and when
it was restored, what may be lost between the two (Family responses, Staff and
Administrator changes, sent emails, ParishSoft refreshes), and the current
campaign. In Production it finds the held emails on request and lists them by
send (the invitation and each reminder, by due date) with how many are not
decided, assumed sent, or to be sent again. It also counts the emails that
were being handed to the provider at the backup, which the delivery warning
settles after release. Each send offers "Assume these were sent" and "Send
these again"; each shows the exact count and asks for a short note before the
confirm (unavailable until the note is written), and a count that changed in
the meantime settles nothing. "Send these again" is unavailable, with its
reason beside it, and a posted one is refused with nothing changed, when
another restore's undecided or assumed-sent hold still keeps one of the
send's emails back (a resend would send nothing) or a later reminder already
reached one of its Families or is on its way (it would arrive out of order).
A refused release says why: emails that still need a hold, or a purge or
campaign change still running. Counts name only emails
that would be sent now; holds of Families that cannot be emailed, reminders
waiting for an undecided invitation, and invitations only a deliverability
change would retry are shown apart. "Release the site" stays unavailable, with
its reason beside it, until no email still needs a hold and no purge or
campaign change is running (both read when the page is drawn, as the release
reads them). Without a sign-in from the last five minutes, every decision and
Release are unavailable, named by the shared step-up notice, which is drawn
last in the region so its appearing on a later in-place answer moves nothing
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates), #1331);
the Held emails page does the same. Its preview says what
Families get as things stand (open, mail paused, not yet open, Testing or
closed), how many held emails stay held, and how many Families' reminders an
undecided invitation keeps back, then releases and ends on Home. Every control
acts in place, and a step's outcome or refusal (with the step-up for a stale
sign-in) is drawn after the controls of its own panel, so nothing moves under
the pointer ([UX conventions](../ui-conventions/spec.md#no-layout-shift)).
Times are browser-local, the due times in the held-email table in the
[table-cell form](../ui-conventions/spec.md#date-format). After release the
page is gone, and the Held emails page decides the remaining holds.

**The Held emails page.** After release, holds nobody decided stay in force.
While any hold of the current campaign is undecided, Home shows an
Administrator how many emails are held, says that a held invitation also stops
that Family's reminders, and links to Held emails (`/admin/mail/held-emails/`;
not a menu entry, because it matters only after a restore; its trail is Home ›
Mail and Family portal). The page lists every restore's holds of the current
campaign by send, with the Restore review page's table, previews, notes,
exact-count check and "Send these again" rules, and it settles every
restore's undecided holds of the chosen send at once. Its counts and Home's
are of emails: an email two restores hold counts once, and only "Assume these
were sent" settles it, as the decision guard requires. A decision is refused,
with nothing changed, when the send's undecided holds changed since its
preview or another restore happened since (even one that kept the count the
same). A "send again" is sent by the
ordinary planner with the restored link, coalesced with any reminder then due;
the page itself sends nothing. It says how many emailable Families an undecided
invitation keeps from getting reminders, keeps a send listed once it is
decided, and draws every outcome and refusal (with the step-up) after the
table, so nothing moves under the pointer. Cancel returns focus to the send's
button that opened the preview; after a confirm that button is unavailable
(the send is decided), so focus moves to the list's heading and the outcome is
announced. With no hold for the current
campaign it says so. During a review it redirects to the Restore review page,
and a decision whose preview came from before a review began settles nothing.
Holds of another campaign are not shown: they cannot be decided. The command
line does not take these decisions (the guard admits only a fresh browser
sign-in).

### Live delivery pause

An Admin may pause production delivery without changing global mode or Campaign
lifecycle state. Pause and resume mail is available only in Production, for
the current campaign while it is `scheduled`, `active` or `closed`. The action
requires fresh authentication, a reason, explicit
confirmation, and a preview of queued, submitting, delivery-unknown, and next-
due counts. It atomically sets the Campaign's durable delivery-pause control and
holds every production message that has not begun provider submission. Family
access and live submissions continue; their receipts are accepted but held.
Nothing is rerouted to the Testing recipient. Operational notifications and
explicit readiness/test-recipient sends remain allowed.

Messages already `submitting` may have reached the provider and
`delivery_unknown` messages retain their reconciliation workflow, including an
authorized resend; the pause UI states this limitation and tracks both. A
resent message returns to pending under the current pause hold: on an active
campaign it is sent only after resume, subject to the resume's recovery plan
like any held message (a later due reminder replaces a resent reminder; a
Family's submission, lost eligibility or lost deliverable address cancels a
resent invitation or reminder; and a scheduled report is folded into the next
combined report), and on a campaign closed while paused
it follows the held-message resolution below, where a message that resolution
already released carries no new hold and is sent without waiting. Retries of
failed or unsent messages wait for resume. When the provider's own record
shows an unknown message was not sent and no resend is wanted or admitted, an
Admin records that with evidence instead: the message becomes a failed
delivery without a resend or recipient suppression, so it stops counting as
unknown and resume can proceed (see the
[unsent resolution guide](../../../guides/stewardship-unsent-resolution.md)).
Workers recheck the pause immediately before provider submission, so no later
production attempt crosses the pause.
Every Admin page shows a persistent "Live campaign email delivery is paused."
banner; for an Administrator it also names who paused and why, since when
(and for how long), the held, still-being-handed-to-the-mail-service and
not-sure-it-arrived counts, and links to Pause and resume mail, which shows the
held counts by message type.

Resume requires fresh authentication, a passed sender check when one is
needed, an exact backlog preview, and explicit confirmation.

An ordinary administrative pause needs no sender check: Resume, and a closed
campaign's Release below, go ahead without a test email
([#1288](https://github.com/epiphany40223/parishkit/issues/1288)). A check is
needed only after real sender or delivery trouble: a test email sent since the
pause that is still being sent (wait for its answer), that was not sent or that
may not have arrived; a send by the current sender whose mail-service health
was unavailable or systemic (an outage, or a refusal of the whole account such
as a suspended account or a refused sign-in), whatever its outcome, unless a
healthy send submitted after that failure was recorded came before the pause
(the rule mail-service recovery uses, see
[#1288](https://github.com/epiphany40223/parishkit/issues/1288)); or mail
settings or a credential other than the ones behind the last send before the
pause. A refusal at Gmail's sending limit is not trouble: the email was not
sent, and the limit hold retries it after its own wait. The check is a test email to the Testing recipient
(**Preview and send a test**, on the same page) that the mail service accepted
after the pause and after that trouble, with the current mail settings and
credential. It lasts 30 minutes: long enough to read the preview and confirm a
fresh sign-in, short enough to still describe the sender as it is. The page
says in place which holds: no check is needed; a check is needed, why, and to
send a test email first, with the link; or when the sender was checked and the
time to resume before. While a needed check is missing, Preview resume is shown
unavailable with that reason, as the
[prerequisite gates](../ui-conventions/spec.md#prerequisite-gates) convention
requires. A preview or confirmation the server refuses because the check is
missing or has run out draws the page again with that reason, never the
generic "This information changed" page. Resume is refused
while any message is still being handed to the mail service or not sure it
arrived (resolve those first, as above), while a Family group is blocked or a
report is still being prepared, and, after a direct activation, until the
[activation catch-up](../background-processing/spec.md#activation-catch-up)
has finished. Resuming before the campaign starts has no recovery plan,
because no campaign email is due yet. Under Campaign and
affected-work locks, it applies the normal overdue Family-mail/digest coalescing
plan, cancels redundant pending outbox rows, records semantic coverage, clears
pause holds/control, and queues only selected messages in one transaction.
Submission receipts remain distinct and are all released. Failure leaves every
message held; stable fulfillment keys prevent duplicate delivery.

If the campaign closes while paused, invitation/reminder work is terminally
skipped and its pending outbox cancelled under the ordinary close policy. A
held invitation or reminder with no delivery task left (usually because its
preparation failed) has no worker to apply that policy and cannot be retried
after close, so every resolution below cancels it the same way and counts it
toward clearing the pause; an uncertain provider outcome is never cancelled
this way and still blocks the clear.
Accepted receipts and completed-day Admin digests remain held. Before archive,
an Admin must use a freshly authenticated **Resolve held messages** workflow to
release selected non-Family-access message types (after the sender check
above, when one is needed) or
cancel them with exact counts and a reason. This does not reopen Family access
or campaign schedules. Once every held or uncertain row is resolved, the same
atomic workflow clears the durable pause control; closed campaigns do not use
the ordinary Resume action. Reopen readiness is blocked until the prior pause
and held-message state is resolved. The form offers only choices the server's
rules accept (#563): a message type appears only while it has held messages;
Release only while no sender check is needed or it has passed; Cancel only for
types with no email still being handed to the mail service or not sure it
arrived; Clear only when the pause would clear with no type selected; and the
types only for Release or Cancel, which need at least one ticked. With no
choice left, Preview is shown unavailable with the reason. Whether reports are
still being prepared is checked only by the server, when the preview is built.
Preview otherwise follows the
[complete-before-submit rule](../ui-conventions/spec.md#prerequisite-gates).

### Family email progress

Sending the invitations, and later each reminder, to every Family is a long
background operation (about 1,100 Families take 20 to 25 minutes). The
read-only **Family email progress** page (Mail and Family portal
[menu group](#menu-groups), linked from
Pause and resume mail and Outgoing mail) follows it live
([#413](https://github.com/epiphany40223/parishkit/issues/413)). It is for
Administrators only, like Outgoing mail. It is a moment-in-time view: it
shows progress only while a send is in progress. Outgoing mail remains the
permanent record of every email and its state.

**Which send.** A send is one revision of one Family schedule definition of
the current campaign (the invitation, or one reminder) in the current mode
and, in Production, the current Production cycle. Every Family's occurrence
of that revision is one email of the send, and so is every Family that
planning still owes it (below). Moving or editing a schedule makes a new
revision and so a new send. The earlier revision's emails stay on Outgoing
mail but are not counted in the new send. Each Family counts once, by its
newest occurrence of the revision, so a deliverability recovery that
replaces a failed invitation is not counted twice.

**In progress.** A send is in progress while it still has work to do:

- an email not yet settled: an occurrence not yet prepared, or a message
  pending, waiting to retry or being handed to the mail service;
- a Family that planning still owes; or
- a total that cannot be known yet.

Sent, failed and uncertain emails are settled. Families not emailed, and
reminders held behind a failed or uncertain invitation, are not work to do.
A finished send is therefore not in progress, and neither is one whose
remaining emails were cancelled, for example because its schedule was moved
to a later time. A due send that planning has not started yet is in
progress, as long as some Family is owed it. Every due send is checked, not
only the one that fell due most recently, and the page shows the in-progress
send that fell due most recently. A reminder therefore takes over from the
invitation once it is due and has emails of its own to send, while a
late-joining Family's invitation (due at the invitation's original time)
does not pull the page back. An invitation still sending when a reminder
falls due (the reminder is then coalesced into it), or a schedule moved to
an earlier time that has already passed, stays on the page.

**When no send is in progress,** the page says so ("No Family email send is
in progress right now"), with no progress bar. It summarises the send whose
occurrences fell due most recently in one line: its kind, when it finished,
and how many emails were sent, failed or not sent, and how many are Not
sure it arrived. It links to
Outgoing mail.

**Counts while a send is in progress.** Each Family's email is counted by
its outbox message state, or, before preparation, by its occurrence:

- **Sent**: delivered (the mail service accepted it).
- **Failed**: permanent failure, or a preparation that failed. Failed
  emails link to Outgoing mail; preparation failures, which have no email
  there, link to the failed preparation tasks in Background work. A
  preparation that failed for good leaves its occurrence pending, so the
  failure is read from the preparation task: the newest run of the
  occurrence's current preparation (in Testing, the current rehearsal's)
  failed ([#482](https://github.com/epiphany40223/parishkit/issues/482)).
  Once an Admin retries that preparation, the email counts as remaining
  until the retry fails again.
- **Not sure it arrived**: delivery unknown, linked to Outgoing mail. Counts
  and links use the plain state words of
  [the Family timeline](../reports/spec.md#family-timeline).
- **Remaining**: pending, waiting to retry or submitting, or not yet
  prepared, including the Families planning still owes. The part not yet
  prepared is shown beside it, in brackets.
- **Held: invitation failed or not sure it arrived** (reminders only, shown
  when non-zero): a reminder not yet prepared for a Family whose newest
  invitation failed or may not have arrived. Planning holds such a reminder
  until the invitation is resolved (`initial_unfulfilled` or
  `delivery_unresolved`), so it is not remaining and the send can still
  finish. A reminder whose own preparation
  failed counts as Failed, not held. This is a narrower test than
  planning's own: a reminder held for rarer reasons (a newest invitation
  skipped or coalesced without delivery, an unreviewed restore hold, or
  another uncertain email for the Family) still counts as remaining, and one
  sent because a restore assumed its failed invitation delivered counts as
  held until it is prepared. These need a restore or a deliverability edge
  case, never the launch invitation, and are an accepted v1 limit.
- **Couldn't be emailed**: skipped or cancelled because the Family has no
  deliverable address (`no_deliverable_recipient`) or is no longer eligible
  (`family_ineligible`).
- **Skipped: in the ParishSoft No-reminders WorkGroup** (shown when non-zero; a
  column of the send history): a reminder skipped, or its unsent email
  cancelled, because the Family is in the campaign's No-reminders WorkGroup
  (`workgroup_excluded`, #861). Such a Family is never owed a reminder, so it
  is not remaining either.
- **Not needed** (shown when non-zero): skipped, coalesced or cancelled for
  any other reason, such as the Family having responded, a later email
  replacing this one, or the campaign closing.

The total is sent, failed, uncertain and remaining; the last three
categories are shown beside it, not in it. The scheduler plans Families a
few at a time, so for most of a send many Families have no occurrence yet.
The total counts them from the start. A Family is owed the send when the
rules in
[Family invitations and reminders](../background-processing/spec.md#family-invitations-and-reminders)
would have planning create its email, and it has no occurrence of the
revision yet. A Family whose earlier-revision email was cancelled by a
schedule edit (`schedule_replaced`, see
[Schedule replacement and removal](../background-processing/spec.md#schedule-replacement-and-removal))
with no fulfillment for its slot is owed the new revision. (An edit is
refused while an earlier delivery is still uncertain, so no Family is
owed twice.) Planning creates the send's emails only while its revision is
the schedule's current one and is due, the campaign has not closed, and, in
Testing, a rehearsal is active. Otherwise no Family is owed. Due times and
the close are read on the campaign clock, as planning reads them. Families
planning has not reached are not yet counted under Couldn't be emailed or
Not needed.

While the Family population is being refreshed, who will be emailed is not
known. The page then shows the counts without a total, percentage or finish
estimate, and says the total is not known yet, rather than showing a share
of the emails planned so far. While planning is held, the owed Families
still count, but the page says **Held** instead of showing the send as
sending. Planning is held during a restore review or a campaign change in
progress, and while the campaign has not started or is not in a sending
state. A Family whose planning keeps failing stays owed; Background work
shows the failure.

A labelled progress bar shows the finished share (sent, failed and
uncertain), which reaches 100% only when nothing remains. The current rate
is emails finished per minute over the last five minutes (or since the send
started, if that is sooner, with at least 30 seconds to measure). When
nothing finished in that window, the page says so instead of showing a zero
rate. The estimated finish assumes the rate continues; there is none while
nothing is finishing or while live delivery is paused. A pause is read from
the campaign's pause control, not from held messages, which workers hold
only as they reach them, and the page then says **Paused**. The page also
shows when the send started (its first email was prepared). The summary's
finish time is the last change to any of the send's settled emails, so a
later resolution (accepting an uncertain email, or retrying a failed one)
moves it; this is accepted for v1.

**Live updates.** While there is a current campaign, the page re-reads a
status-only fragment every 5 seconds, whether or not a send is in progress,
like the other [self-updating pages](#background-indicators). The reads are
passive, never renew idle time and are not audited; only opening the page
records an audited Outgoing mail view. A page left open with no send in
progress therefore switches to the next send by itself, in Testing as in
Production. In Production it also says it is waiting for the next emails
while the campaign's activation catch-up is unfinished, or while an
invitation or reminder is due within an hour either side of now and nothing
is scheduled for it yet. That wording hedges: a reminder no Family still
needs schedules nothing, and an activation catch-up that keeps failing never
schedules the invitations, so while waiting the page points to Background
work. The page keeps checking for up to 3 hours after the last check that
brought new counts (other self-updating pages stop an hour after they are
opened), so a page opened an hour early follows the whole send. When it
stops, it says to use **Refresh progress**. The region's counts change on
every read, so it is not itself an ARIA live region. Screen readers instead
hear one short polite announcement when the send passes each quarter,
pauses, is held or is no longer in progress. Technical details sit outside
the updated region. As on Outgoing mail, a restore review that begins while
the page renders withholds it.

**Cost.** Every read uses only rows and columns the web login already reads,
runs in one read-only snapshot and takes no lock, in particular not the
global work-order lock that the send's own workers take. The campaign's
Family occurrences are found through the existing definition index and each
email by primary key, with no schema change. A reminder also reads the
invitations of the Families whose reminder is not prepared yet. The owed
Families are counted from the campaign's Family rows, each checked against
the send's occurrences, fulfillments and restore holds by anti-join. Due
sends are found from each Family schedule's current revision, and each one
other than the latest is counted the same way. At launch scale (about 1,100 Families among 5,000 other occurrences
and messages), the reads take about 0.5, 3 and 2 ms on the test database;
with 50,000 other messages, about 0.7, 6 and 2.5 ms. Counting the owed
Families adds under 1 ms in both, and under 10 ms on the validation server
during a live 1,100-Family Testing send.

### Family email sends

The read-only **Family email history** page (Mail and Family portal
[menu group](#menu-groups), linked from
Outgoing mail and from the [Family email progress](#family-email-progress)
page) is the permanent record of every Family send of the current campaign
([#432](https://github.com/epiphany40223/parishkit/issues/432)). It is for
Administrators only, like Outgoing mail.

**One row per send.** A send is exactly what the progress page counts (see
its **Which send**): one revision of one Family schedule in one mode and
Production cycle, so the same invitation sent in Testing and in Production
is two rows. A schedule edit makes a new revision and so a new row: the
earlier row keeps the emails it sent, shows the unsent ones the edit
cancelled, and is marked as changed later; the new row starts once its
first email is planned. A send that planning has not started yet has no
row; the progress page shows it from its first minute. Rows are newest
first by scheduled time (the revision's due time), then by when the send's
newest email was planned. Reminders are numbered once per campaign, by
their schedules' current due times (Reminder 1 is the earliest current
reminder), so a reminder has the same number in Testing and in Production
and across its edits, and the numbers match the order on Dates and mail
schedules. A
removed reminder has no current due time and is shown without a number. A Production send from before the campaign returned to Testing is
marked as such.

**What each row shows.** The kind (Invitation or Reminder N), the mode,
the scheduled time, when the first email was prepared, when the latest one
got its result, and the time between them; then the total and the counts
Sent, Failed, Not sure it arrived, Not needed, Couldn't be emailed and
Held. Each row is counted by the progress page's own code, so the two pages
always agree;
see [Family email progress](#family-email-progress) for what each count and
the total mean. A row also shows **Cancelled**: the send's emails that were
prepared and then cancelled before sending (by a schedule edit, a response
or the close). Those Families are also counted under Not needed or Couldn't
be emailed, so Cancelled is not added to anything. A send still in
progress says so. Only the send the progress page is showing links to it
(the page shows one send: the in-progress send that fell due most recently);
another send still in progress, such as an invitation still sending when a
reminder falls due, says In progress without the link.

**Layout.** The table has seven columns, so it fits a laptop screen beside
the Admin menu
([#931](https://github.com/epiphany40223/parishkit/issues/931)). **When**
comes first and lists the scheduled time, the first email, the latest
result and the duration as labelled lines. **Email** names the send, with
its mode, a later schedule change and In progress as muted lines under the
name. **Total**, **Sent**, **Failed** and **Not sure it arrived** follow,
then **Not in the total**, a labelled list of Cancelled, Not needed,
Couldn't be emailed, Skipped: No-reminders WorkGroup and Held. Times and counts
never wrap; labels wrap only between words. On a phone the table scrolls
inside its own region and the page never scrolls sideways.

**Links to Outgoing mail.** Sent, Failed, Not sure it arrived and Cancelled
link to Outgoing mail filtered to that send and the matching email state
(Delivered, Failed, Not sure it arrived and Not sent (cancelled)). The send
filter is the query parameter `send=<schedule>:<revision>:<mode>:<cycle>`,
combined with the existing state, search and paging parameters. It keeps
each Family's newest email of the send, exactly the emails the counts
read, so a linked count opens exactly the emails it counts. A malformed
send, or one that is not a Family send, is refused rather than ignored, and
the filtered page names the send and links back to all outgoing mail. When
some of a count has no email (a failure before preparation, or an
occurrence settled without one), the cell shows the count and, separately,
a link to the emails there are; failures before preparation are reviewed on
Background work, as on the progress page.

**Cost.** The page lists the sends with one aggregate over the campaign's
Family occurrences (through the definition index) and counts only the shown
page's sends, each with the progress page's statements plus one count of
its emails by state. A campaign has a handful of sends, so the list is
paged in memory, 25 rows at a time (at most 100; there is no All, since
every shown send is counted). It reads everything in one read-only
snapshot. The filter on Outgoing mail checks only the named send's
occurrences, reads the send's email ids the same way and finds the emails
by primary key; like the rest of Outgoing mail, it reads its page statement
by statement rather than in one snapshot, so during a live send its count
and rows can differ by an email that changed in between. Every read uses
tables the web login already reads and takes no lock, so it never waits on
or delays a send. Both pages check that the viewer is an Administrator
before validating the query. The page does not update itself, records one
audited Outgoing mail view and does not renew the Admin's idle time; as on
Outgoing mail, a restore review that begins while it renders withholds it.

### Outgoing mail

Outgoing mail (`/admin/mail/outgoing/`, Administrators only) lists every
email the system has sent or is about to send, with filters by delivery
state, an exact Family DUID or delivery ID, and a
[Family email send](#family-email-sends). It reads as a log
([#931](https://github.com/epiphany40223/parishkit/issues/931)): each row is
one email, not one Family, and **When** shows a single time, that of the
email's current state (when it last changed, such as when it was
delivered). When sorts by that time (sort token `changed`), newest first by
default; the email's own page keeps its whole timeline. Each row names the
Family and, in a column of its own, its Family DUID; an Administrator
report has no Family. The name is the Active parishioner family
directory's surname-and-heads
name from the latest ParishSoft data, read for the shown page only (at
most two small queries, however many rows), or a muted "Not in current
ParishSoft data" when that data no longer has the Family. The name opens
the email's own page. Family sorts on the server over the whole list (sort
token `name`): by surname, then the whole shown name, then Family DUID
for Families that show the same name, as the Active parishioner family
directory orders them,
each email's name read inside the page's query from the same ParishSoft
snapshot the shown names come from; Family DUID sorts too (token `duid`). Either
keeps each Family's emails together, newest change first (then newest
email) in both directions, and Administrator reports (and, by name,
Families no longer in the data) sort last in both directions. The
columns follow the Admin tables' shared order
([#932](https://github.com/epiphany40223/parishkit/issues/932)): When,
Family, Family DUID, Purpose, Mode, State and Provider attempts. The
command line's `--sort` takes the same tokens and also `created`, when the
email was created. The table fits a 1280-pixel-wide screen beside the
Admin menu without scrolling. Each email's own page is the
[Mail message page](#mail-message-page).

An email whose delivery recorded a [refused address](#refused-addresses)
that is still unresolved is marked in its State cell, on a second line,
"1 address refused" or "N addresses refused"
([#935](https://github.com/epiphany40223/parishkit/issues/935)), counting
each address once even when a retry was refused again. A
permanently refused address does not fail an email that another address
accepted, so the email can be Delivered and still have missed one head of
household; the mark makes that visible. It links to Refused addresses
filtered to that Family's DUID. The counts come from one grouped query for
the shown page (each refusal's outbox event names its email; the web login
reads both columns, never the event's evidence). The page's link line
(Watch Family email progress, See Family email history and Review refused
addresses) sits above the page heading, as the
[page help convention](../ui-conventions/spec.md#page-help) allows. It reads "Review refused addresses (N
unresolved)" while any refusal is unresolved, and "Review refused
addresses" otherwise; it is drawn once per page load, outside the in-place
regions.

#### Sending limit note

While Family email is waiting on a sending limit, a note above the list says
why, in the same words as System health's
[Why sends are waiting](#why-sends-are-waiting) panel, and links to
[System health](#system-health-page) (#382 item M3a). It names each reason
that applies:

- a running mail sender is **waiting for the daily limit**;
- a running mail sender is **held at Gmail's sending limit**, with the time
  sending starts again (browser-local), when the sender reports one;
- some of the current campaign's Family emails, in the current mode, are
  **waiting longer than usual to retry** because the receiving mail service
  or Gmail asked for fewer emails (the recipient-throttle backoff of
  [bulk Family send](../background-processing/spec.md#bulk-family-send), or
  a Gmail limit answering that one message), with how many and, while it is
  still ahead, when the first retry is due.

The sender states are read from the mail consumers'
[service status records](#service-status-records), grouped as the
[mail sender panel](#mail-sender-panel) groups them, so the two pages agree.
An email is on the longer backoff when it is waiting to retry, its latest
outcome was a temporary refusal, and its wait is longer than halfway between
the ordinary retry schedule's longest wait (10 minutes) and the shortest
limit or throttle wait (15 minutes); every value comes from columns the web
login already reads, so the refused addresses are never read. The note says
these emails show as Still sending and need no action. It is drawn once per
page load, outside the in-place regions, so it never appears or vanishes
under the pointer, and it is absent when nothing is held. No schema change
and no lasting record: the process log keeps each hold's WARNING line.

#### Fix many emails at once

After a mail outage many emails can need the same decision. A collapsed
**Fix many emails at once** panel on Outgoing mail offers two bulk actions
(#382 item M4), each shown only while some email of the current campaign
qualifies:

- **Retry all failed emails**: every failed email whose own page offers
  **Retry failed delivery**.
- **Record all "Not sure it arrived" emails as not sent**: every uncertain
  email whose own page offers recording it as not sent. As on that page,
  the evidence is the Administrator's: a required checkbox says they checked
  the mail service's own records and these emails were not sent. Nothing is
  resent and no address is blocked.

A message qualifies only by the rules its own page uses
(`delivery_reads.offered_actions`): the campaign is not archived and its
exports are admitted, its latest delivery task failed, and, for a retry,
its resend admission holds. A chosen-Family test is never retried. Each
form names the email types it covers with their counts (and All types when
there are several) and requires an evidence or reason note, recorded on
every email. Preview is unavailable until the required fields are filled
in. A duplicate-risk resend is never offered in bulk.

**Preview** shows the exact count in place, below the forms, and changes
nothing. Its **Confirm** button posts a signed preview that binds every
selected email's identity and version, a digest of the note and the
Administrator, and expires after 15 minutes. The note itself is not in the
preview: Confirm posts it again beside the preview, and it must match. Changing the form after previewing withdraws the
preview, as an [in-place review](../ui-conventions/spec.md#in-place-controls) does. Confirming
resolves each email through the ordinary per-email resolution, one email per
command and transaction, at its previewed version, so each keeps its own
guard, resolution record and audit event, and a retry prepares the email
exactly as a one-at-a-time retry does: Families' codes and links already
emailed stay valid. An email that changed after the preview, or that its
own guard refuses, is skipped and counted, never forced; a lost sign-in or a
database outage stops the run. Each email's command identity is derived
from the preview and the email, so confirming the same preview again never
repeats a resolution. At most 100 emails are resolved per request, so a
request stays well inside the web time limit; the result then offers
**Continue**, which applies the rest of the same preview. Continue's preview
also lists the emails skipped so far, which are counted again but not
retried, so skipped emails never fill a later batch; it expires when the
original preview would. The result says
how many were resolved, skipped and left, and the list is refreshed in
place. Every application that attempted an email records one
`delivery_bulk_resolved` audit event with the number resolved and the
number previewed. An expired, altered or another Administrator's preview,
or a choice that no longer matches any email, is refused in place with an
error summary and changes nothing.

The bulk actions are page-only for now; the shared functions in
`jobs/delivery_bulk.py` would back an
[Admin automation](../admin-automation/spec.md) command if one is added.
No schema change: they insert the same resolution rows the per-email page
inserts.

#### Mail message page

An email's own page (`/admin/mail/outgoing/<message>/`) reads top to bottom
in the order the Administrator asked for
([#1015](https://github.com/epiphany40223/parishkit/issues/1015)):

1. **Heading.** The page is
   [object-named](#page-names-and-placement): its heading names the email by its
   type and its Family, such as "Initial invitation to Morgan, Henry". The
   name is the Family's heads, surname first, from the latest ParishSoft
   data, read as Outgoing mail reads it (the shared
   [Family name rule](../ui-conventions/spec.md#family-and-member-names)).
   An Administrator report, or a Family that data no longer has, is named
   by its type alone. The browser title is the type alone, so browser
   history does not keep the Family's name, as on the
   [Family timeline](../reports/spec.md#family-timeline).
2. **Type, mode and state** on one line under the heading, such as
   "Initial invitation · Production · Not sure it arrived".
3. **Family and recipients.** The Family's name and its Family DUID (the
   shared
   [Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
   badge when the name is unknown), or "Administrator report". Then "To:"
   and the email's recipient addresses, in their stored order, from its
   newest rendering (every address is a To: address; none is Cc or Bcc). When that rendering sent it elsewhere, which only Testing
   mode does, "Testing mode sends it to:" names where. When the email's
   delivery recorded [refused addresses](#refused-addresses) that are still
   unresolved, they follow, each once and linking its newest refusal's
   page; one an Administrator reported bounced says "(bounce reported)". The recipient lists stay with the email's rendering, which the web
   login never reads (see
   [Why sends are waiting](#why-sends-are-waiting)). This page is the one
   narrow exception
   ([#1024](https://github.com/epiphany40223/parishkit/issues/1024)),
   because the Administrator needs to see whom an email was for to judge
   it: it reads the addresses, and nothing else of the rendering, through
   a definer function that refuses any caller but an Administrator (see
   [outbox recipient reads](../data/spec.md#outbox-recipient-reads)).
   Viewing the page is audited as before.
   Each address the email was sent to is followed by what the latest
   attempt of that rendering recorded for it
   ([#806](https://github.com/epiphany40223/parishkit/issues/806)):
   "delivered", "refused for now", "refused permanently", "not sure it
   arrived" or "not delivered". A label never contradicts what has since
   happened to the email: "not sure it arrived" shows only while the email
   is still in that state (after an Administrator confirms delivery,
   records that it was not sent, or authorizes a resend, the address shows
   no label), and "not delivered" reads "not delivered yet" while the email
   may still be sent (waiting to retry, or being sent again). In Production
   these follow the To:
   addresses; in Testing mode they follow the Testing recipient, the
   address the email was really sent to. One email goes to all its
   addresses at once, and the provider can accept it for some and refuse
   others. A Production invitation or reminder accepted with some
   addresses refused for now is sent again to just those addresses (see
   [background processing](../background-processing/spec.md#addresses-refused-for-now)),
   and such an address then shows what that follow-up did instead:
   "refused for now, trying again" while it is still being tried, or
   "delivered", "refused permanently" or "not sure it arrived"; an address
   shown as just "refused for now" is no longer tried (a receipt, Testing
   mail, an email sent before follow-ups existed, or a follow-up that
   ended). When the email reached only some addresses the line "Delivered
   to 1 of 2 addresses." follows. The page's About panel explains these
   labels, that one email goes to all addresses at once, and the automatic
   retry of an address refused for now (#806). Before the
   rendering's first finished attempt, or when its evidence does not
   validate, no outcome is shown. The outcomes come from the attempt's
   validated delivery evidence and its follow-up through two more definer
   functions with the same caller checks (see
   [outbox recipient reads](../data/spec.md#outbox-recipient-reads)).
   Setting a flaky address aside is a later slice of #806.
4. **Attempt history**: one table of short rows, one per recorded change,
   newest first, with When leading as in any log, then the attempt number,
   what happened and the resulting state (#934).
5. **Technical details** (the delivery ID), closed, as the
   [page help](../ui-conventions/spec.md#page-help) convention places
   internal identifiers.
6. **Latest background task**, linking its page, with its state.
7. **Resolve this email**, shown only when a decision is available: the
   resolutions the email's state allows (confirming delivery from external
   evidence, recording that the provider did not send it, a retry, or a
   duplicate-risk resend), as the
   [delivery workflow](../background-processing/spec.md#family-invitations-and-reminders)
   defines, together with **Save evidence note**. One plain sentence says
   what the actions do. Each needs a note, labelled "Why are you doing
   this? (saved in this email's history)" ("Note to save in this email's
   history" for the evidence note), such as "Gmail Sent folder shows it was
   sent": it is stored with the command, copied into the email's immutable
   event history, shown under Admin evidence and decisions, and never
   emailed to anyone. An uncertain email's "Not sure it arrived" notice, and
   the note when a retry is unavailable, sit just above the section (or,
   without one, just above the evidence note's disclosure described next),
   beside the actions they caution about. When the only thing offered is the evidence note (nothing to
   decide, such as a delivered email), the section is not shown; the note
   form waits in a closed "Add a note to this email's history" disclosure,
   whose summary is the form's only heading.
8. **Report a bounced address**, on a Production Family email only
   ([#806](https://github.com/epiphany40223/parishkit/issues/806) slice 4).
   When a delivered email later bounces (a non-delivery report arrives in
   the sending mailbox, which the system never reads), an Administrator
   chooses the address, says what the bounce said (the date and its short
   reason, not the whole message) and ticks "I saw a bounce message for
   this address from this email". **Report bounce** stays unavailable until
   all three are given, with the hint after it. The address is then set
   aside for that Family like a refused address: it is listed on
   [Refused addresses](#refused-addresses) and stops receiving campaign
   email until it is cleared there or ParishSoft's address changes. A
   one-line notice beside the button says so, and when the Family's only
   address left (its current ParishSoft addresses with no unresolved
   refusal) is one of those offered, a second notice after the button names
   it and says the Family would then get no campaign email (after the
   button, so its removal by the report never moves the button). Only the email's addresses
   the Family has no unresolved refusal of are offered. While the email is
   not delivered, the campaign accepts no Admin changes, or every address
   is already set aside, the button is shown unavailable with that reason.
   The form acts [in place](../ui-conventions/spec.md#in-place-controls)
   and replaces only its own region, so nothing above its button moves;
   the bounces already reported for the email are listed after the form,
   with browser-local times and "cleared" for a cleared one, each linking
   its refused address page. The field help is in "i" toggletips. It needs
   no fresh sign-in, like Clear verified refusal: it only sets an address
   aside, changes no credential, and clearing undoes it. Reporting sends
   nothing and does not change the Family's code or link; the Family's
   other addresses keep receiving the same mail. The report is audited
   (`recipient_bounce_reported`), and the Refused addresses line near the
   top of the page shows it the next time the page is opened. Its command
   line counterpart is
   [`delivery bounce-report`](../admin-automation/spec.md#operations)
   ([#1211](https://github.com/epiphany40223/parishkit/issues/1211)); see
   [data](../data/spec.md#job-outbox-audit-and-purge-records) for the
   record.
9. **Admin evidence and decisions**: the notes and resolutions recorded on
   the email, newest first.

The attempt history and the Admin evidence share one page window, as
before. Each list ends with Previous page and Next page links, named for
both lists ("Attempt history and evidence pages"), that act
[in place](../ui-conventions/spec.md#in-place-controls) and refresh both
lists; the resolution forms sit between the two regions, so a note being
typed survives paging. Paging the attempt history can change its height
and so move the forms and the evidence list below it: a recorded exception
to [no layout shift](../ui-conventions/spec.md#no-layout-shift)
(Administrator, 2026-10-11). No schema change.

#### Refused addresses

Refused addresses (`/admin/mail/refusals/`, Administrators only) lists
every unresolved refused address: one an outgoing Production email's
delivery recorded as refused permanently, either in the email's own
attempt or in its
[follow-up](../background-processing/spec.md#addresses-refused-for-now) to
an address refused for now (listed against that email), or one an
Administrator reported bounced on the
[Mail message page](#mail-message-page) (listed against that email, marked
"Bounce reported" under the address), which stops
campaign email to that address for that Family until it is cleared or
ParishSoft's address changes. It filters by an exact Family DUID (`?duid=`), which the
[Outgoing mail](#outgoing-mail) mark uses. The columns follow the Admin
tables' shared order
([#932](https://github.com/epiphany40223/parishkit/issues/932)): Refused
at, Family, Family DUID, Address and Email
([#935](https://github.com/epiphany40223/parishkit/issues/935)); the
refusal's ID shows only under Technical details on its own page, though
the command line can still sort by it (`--sort id`). The Family's name is
read as Outgoing mail reads it (the latest ParishSoft data, the same
snapshot the Family sort reads, for the shown page only, at most two small
queries) and shows "Not in current
ParishSoft data" when that data no longer has the Family. Family sorts on
the server as Outgoing mail's does (sort token `name`: surname, then the
whole shown name, then Family DUID), with Families no longer in the data
last in both directions; Family DUID sorts too (token `duid`), since a
table that shows a Family name and a Family DUID sorts on both
([#932](https://github.com/epiphany40223/parishkit/issues/932)). Only the
Email column does not sort. The address opens the refusal's own page, and
the Email column's **Open email** opens the email whose delivery recorded
it. A link above the list leads back to Outgoing mail. The two pages stay
separate: a refusal is per address and outlives the email that recorded
it, so it is not a row or a state of Outgoing mail.

A refusal's own page names the address, the Family and its Family DUID,
and, for a reported bounce, "Reported as bounced by an Administrator" with
its browser-local time and the Administrator's note; it
links that Family's refused addresses and the recording email, and offers
the verified clearance: a required note saying how the mailbox was
verified and a required confirmation, available only with current
ParishSoft data and a current campaign. While the clearance cannot be made,
the form is replaced by **Clear verified refusal** shown unavailable, named
by the first reason: no current campaign, an archived campaign, a campaign
not accepting changes (usually other campaign work), or ParishSoft data the
campaign has not taken in
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates), #1331).
Otherwise **Clear verified refusal** is
unavailable until both are filled in, and clearing acts
[in place](#in-place-controls): the page's answer replaces the clearance
region, which then shows the resolution with focus on its heading. A
refused or stale clearance shows its error page, as before. Nothing is
sent, and no schema change is involved.

### Family portal maintenance

An Administrator may close the Family portal for maintenance from Mail and
Family portal → Family portal availability, for example while fixing a data
problem, and
reopen it later. Changing the switch requires fresh authentication and a CSRF
token, and each change is recorded in the audit log
(`family_maintenance_started` or `family_maintenance_ended`); the newest such
event is the switch's state, so a fresh install starts open. The optional
message for Families is the Administrator's own text, at most 500 characters
and without email addresses.

While the portal is closed, every Family page (the sign-in page, personal
links and the form page) returns a self-contained "temporarily unavailable"
page with HTTP 503 and `Retry-After`, showing the message; its "Try again" link
repeats the same page for a GET, and goes to the home page otherwise. The form's
JSON endpoints return a clean 503 that the form script shows without discarding
the answers on the page; a refused Submit is definitely not submitted. No Family
answer is saved. The keepalive and sign-out endpoints stay open so a Family can
keep, or end, their own session: keepalive still refreshes the Family session
row, but skips the Family's campaign activity update while the portal is
closed, so it does not contend with an Administrator's data fix; sign-out still
revokes the session, audits it and cancels the session's baselines. A Family
who stays active on the page keeps their answers; after the 60-minute idle
limit their session ends and unsubmitted answers are lost, so maintenance
should be short. The Administrator page says so.

Two Administrators closing at once cannot silently lose a message: the change
locks the system configuration row before reading the current state. Web
processes reuse the state they read for up to 3 seconds, and requests already
in progress when the portal closes still finish, so the page tells the
Administrator to wait about 10 seconds after closing before changing data. A
change takes effect everywhere within seconds, without a restart. A banner on
every Admin page, for every Admin role, says the portal is closed.

The switch does not stop email. No new receipt can arise while no Family can
submit, and receipts already queued are still sent. Scheduled invitations and
reminders continue unless an Administrator also uses [Live delivery
pause](#live-delivery-pause), which the page links to.

### Reopen and archive

Status: reopen, archive, unarchive, [Return to
Testing](#return-to-testing-after-archive) and [campaign
purge](#campaign-purge) are cut from v1
([v1 launch scope](../../../plans/stewardship/v1-launch.md#cut-from-v1),
items 1 and 2) and are not built; no Admin page or command offers them. The
close-out wizard that brings them back is
[#527](https://github.com/epiphany40223/parishkit/issues/527). These sections
keep the intended design.

Extending a closed campaign into the future can reopen it only through a
readiness workflow equivalent to Production transition, excluding the
`draft`-to-`scheduled` state change. Previously completed Testing cleanup need
not be repeated, but any remaining or newly created readiness-test artifacts
undergo the gated cleanup and aggregate process described below. The proposed
end date is
staged in that workflow and is committed only by the final reopen transaction;
it must place the commit instant inside the reopened half-open campaign
interval. The
single-current-campaign rule means a successor cannot yet exist; the server
nevertheless rechecks that no other campaign is `draft`, `scheduled`, `active`,
or `closed` and reports any inconsistent state rather than surfacing a database-
constraint error. The UI lists reactivated Family access, prepared access-link
token counts, and each explicitly configured future mail occurrence.

Reopen readiness queues a resumable background token-preparation task and
returns a progress page. It prepares a complete inactive generation for the
pinned eligible Family set using only the token public encryption key. The
page offers retry/cancel and does not offer final confirmation until preparation
and the other readiness checks pass. Prepared credentials are never displayed,
sent in Family mail, or accepted by login while staged.

Fresh authentication and final confirmation atomically recheck the
single-current-campaign guard, exact preparation/source/configuration/key
versions, and readiness, move `closed` to `active`, apply the proposed end date,
preserve or assert Production, select the ready token-generation pointer, and
enable Family access. Changed inputs reject confirmation and require refreshed
preparation. The final transaction does no bulk token generation, encryption,
or per-Family insertion; the generation model and invalidation rules are in
[Family campaign identity](../data/spec.md#family-campaign-identity).

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
An unfinished ActivationCatchUpDemand also blocks archive/Return to Testing,
even when its current TaskRun has exhausted retries or no occurrences have yet
been materialized.

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

Superseded for the Admin portal by the
[navigation decisions](#navigation-decisions) 10, 11 and 19: reports show the
current campaign only, and the checklist's "create the successor draft" choice
is greyed out under [navigation rule 10](../ui-conventions/spec.md#navigation-rules) until #145.

## Portal user management

Automation sessions are not listed here: every live
[automation session](../admin-automation/spec.md#revocation-and-listing),
and on request each Administrator's own ended ones, are on the Automation
access page in the Users and access group, where any Administrator may revoke
a live one. The page opens on its live sessions table, above everything else;
an "Include ended sessions" checkbox adds the ended table, and both tables
sort by their headings (the automation specification's
[revocation and listing](../admin-automation/spec.md#revocation-and-listing)
owns the parameters).

The **Users** page (`/admin/users/`, Administrator only) lists everyone who
may sign in to the Admin portal (Administrator decisions of 2026-10-10,
[#952](https://github.com/epiphany40223/parishkit/issues/952)). A **user** is
one email address with a **Name** and exactly one **role**; the
[data specification](../data/spec.md#administration-user-and-policy) defines
the record. The roles are levels, and each includes everything below it:

1. **Ministry leader** signs in and sees only the Ministries they lead in
   ParishSoft ([#922](https://github.com/epiphany40223/parishkit/issues/922)).
   A Ministry leader who leads none signs in and sees an empty My Ministries,
   with a plain sentence saying why.
2. **Staff** has everything a Ministry leader has, sees every Ministry, and
   has the Staff capabilities of the
   [overview](../spec.md#actors-and-authorization).
3. **Administrator** has everything Staff has and can see and change
   everything.

People sign in with the Google account of their user's email address
([login and denial behavior](#login-and-denial-behavior)). The Name is a plain
label: it is not tied to a ParishSoft Member, and only an Administrator changes
it. An address with no user cannot sign in, so deleting a user is how
access is removed; there is no explicit-deny entry and no hosted-domain rule.
Delete is also the emergency switch for a compromised account. Disabling one
recorded Google identity (`PortalUser.disabled`) is retired as an action: no
page, `pk-admin` command or operator command sets it, and none did before the
revamp. Sign-in still refuses an identity whose flag is already set, and the
[upgrade preview](../operations/spec.md#users-upgrade-from-sign-in-rules)
lists such identities.

The page follows the portal's general conventions, which this section does not
repeat: [Admin tables](../ui-conventions/spec.md#admin-tables),
[row actions and confirmation](../ui-conventions/spec.md#row-actions-and-confirmation),
[table column order](../ui-conventions/spec.md#table-column-order),
[Review, Apply and Change status](../ui-conventions/spec.md#review-apply-and-change-status),
[field errors and live checks](../ui-conventions/spec.md#field-errors-and-live-checks)
and [no layout shift](../ui-conventions/spec.md#no-layout-shift). Nothing on it
autosaves.

A small "See sign-in activity in System logs" link beside the page heading
opens [sign-in activity](#sign-in-activity) on System logs, with only the
closed link filters in its address
([#953](https://github.com/epiphany40223/parishkit/issues/953)). The table
rows have no sign-in activity control (Administrator, 2026-10-11, after
pk-local review). A user's Edit page lists one small "Sign-in activity"
control per recorded Google identity at that address, which opens the same
view narrowed to that identity as its actor; the identity is a private
filter, so the control posts it, never putting it in an address
([Admin tables](../ui-conventions/spec.md#admin-tables)). A user with no
recorded identity has none.

Every deployment past initial setup holds users once the users release
starts (startup refuses sign-in rules), so the page has no Upgrade preview
panel; `pk-admin users preview-upgrade` and `upgrade-users --preview` show
the [users upgrade](../operations/spec.md#users-upgrade-from-sign-in-rules)
preview before it.

### Users table

The page opens with **New user** above one table of users. Its columns are
Email (the row header, the most relevant column), Name, Role, Last successful
sign-in and the row actions. Email, Name, Role and Last successful sign-in
sort on the server; Role sorts by level (Administrator first on the first
choice), and Last successful sign-in shows the newest successful Google
sign-in of any identity with that address, as every table cell shows a
time ([date format](../ui-conventions/spec.md#date-format)), with users who
never signed in sorting last; it sorts by the time itself. The default order is Email. The table
refreshes in place like every other Admin table.

Each row is one line. An email or Name too long for its column is cut short
with an ellipsis; the full value stays the cell's text for screen readers and
copying, the cell's `title` attribute shows it whole as a hover tooltip, and
the Edit page shows it whole. This is a recorded exception to
[table fit and row height](../ui-conventions/spec.md#table-fit-and-row-height),
whose long text wraps instead, because the Administrator asked for one-line
rows here (#952).

Each row's actions are **Edit** and **Delete**. The signed-in
Administrator's own row has no Delete (the
[own-Administrator guard](#user-change-checks-and-alerts)); that also covers
the only Administrator, who is always the reader. A line under the table
says why, and the server refuses it anyway. The table has no selection
column: users are deleted one at a time.

Removed with the revamp, and not shown anywhere: the hosted-domain table and
its `gmail.com` check, the Rule origin, Configured roles and grant origin,
Roles granted now, Ministry assignments and Warnings columns, the
"Exact-address rule" and "Explicit deny" wording, and the autosave queue
(Queued, Applying and Applied states, intent keys and its conflict review).

### New and Edit user

**New user** (`/admin/users/new/`) and **Edit user**
(`/admin/users/<user>/edit/`, opened by a row's Edit) are one page with three
fields:

- **Email**, required on New: one address, trimmed and lowercased, which must
  be a valid address and not already a user's. The page checks both as the
  Administrator leaves the field. On Edit it is read-only, because changing
  the address is Delete and then New. When a recorded Google identity with
  that address is disabled, one line under it says so.
- **Name**, required: plain text, at most 200 characters, trimmed. Its
  default from ParishSoft is described below.
- **Role**: three radio buttons, Ministry leader, Staff and Administrator,
  each with one plain-language line saying what that role can see. New starts
  with Ministry leader chosen. On the signed-in Administrator's own Edit
  page (and the only Administrator's) the lower roles are unavailable, with
  the reason in a line under the roles that is reserved while empty (#563);
  the server refuses that change anyway.

On Edit, the button reads **Save** and its dialog, "Save this user change?",
is a [compact review](../ui-conventions/spec.md#review-dialog) (#1356): one
line per changed field, such as "Role: Staff → Ministry leader", since the
old value is the one thing the page no longer shows (the Email never
changes, and the page shows it), with the warning when the change makes the
person an Administrator or no longer one. On New, the
button reads **Add the user** and its dialog, "Add this user?", is a
[confirmation](../ui-conventions/spec.md#review-dialog) (#1356), since every
value is on the form behind it: one sentence says the user is added and can
then sign in with the account of that address, and the dialog adds only the
warning when the new user is an Administrator. **Apply** records one
configuration change. Review and Apply follow the [review dialog](../ui-conventions/spec.md#review-dialog):
Edit's Save is unavailable until a value changes, and once the change is applied
the reader returns to Users, whose status line says what was applied. An unknown
`<user>` (for example, one deleted meanwhile) is refused, even from an old
link.

#### Name from ParishSoft

When the Administrator leaves the Email field on New user, the page asks the
server, in place, for the distinct names of the ParishSoft Members in the
current promoted data whose contact record lists that address (matched
trimmed and case-insensitively, as sign-in matches, with no Gmail dot or plus
folding). The request is a CSRF-protected POST, so the address never appears
in a URL; it is Administrator-only, reads nothing else, and its audit records
only the count of names found. The same answer says when the address is not
one email address or is already a user, shown at the Email field. The web
login reads the names through one SECURITY DEFINER function
(`stewardship_member_names_for_v1`, migration 0053), which returns only
names, never another contact field. The function answers only for the
caller's live Admin session (one begun after the latest Admin-access
recovery) whose user is an Administrator now, records the lookup's audit
event itself, and refuses an Administrator's 61st lookup within an hour;
against a compromised web database login, only that cap and the audit
hold ([residual risk](../operations/spec.md#users-upgrade-from-sign-in-rules)).
The page also refuses an Administrator's 20th lookup within a minute, and
every lookup while the rate limiter is unavailable; a refused lookup says
the names couldn't be looked up, and the Name is typed instead.

- **No match:** Name stays empty and required.
- **One name:** Name is filled in, and stays editable.
- **Several distinct names:** the first is filled in. The order is a Member
  of an active Family first, then a head of household, then by name, then by
  Member DUID, so the choice is always the same. The others are offered as
  the Name field's suggestions (a `<datalist>`).

A default name is the Member's, surname first ("Smith, Ann"): the
Administrator's #952 comment of 2026-10-10 asks for the leaders table's names
in "Last, first" form, and a new user's Name uses the same form so the two
tables match, as the
[Member-name rule](../ui-conventions/spec.md#family-and-member-names) asks.
One help line under the field is always present,
so nothing moves when the answer arrives: "Suggested from ParishSoft: Smith,
Ann; Smith, Bob", or "No ParishSoft Member lists this address". The lookup
never overwrites a Name the Administrator has already typed, and nothing links
the user to a Member afterwards.

### Delete a user

A row's Delete opens the shared confirmation dialog. It names the user's
email, Name and role, and says that they can no longer sign in, that their
open Admin sessions end on their next request, and that the address can be
added again later as a new user. Confirming records one configuration change
and follows it until it is applied, as
[row actions](../ui-conventions/spec.md#row-actions-and-confirmation) describe.

### User change checks and alerts

Every save, delete and import is checked on the server, in the transaction
that records the request and again at activation: the actor is a current
Administrator with the page's CSRF token, the actor is still authorized, and
the **last-Administrator guard** holds (no change may leave no
Administrator), and so does the **own-Administrator guard**: no Administrator
demotes or deletes their own user, even while another Administrator remains;
another Administrator makes that change (Administrator decision, 2026-10-11,
after a self-demotion left its Change status page waiting on a status read
the demoted reader could no longer make). The change builder refuses it at
Review and Delete, and the configuration request's own transaction refuses
it again against the request's base. Both guards are Python's: SQL's
activation check keeps an Administrator but cannot tell who asked, so the
own-Administrator guard has no SQL counterpart. The `pk-admin users` commands
act as the Administrator who approved the automation session and apply the
same guard. A user change asks for no fresh Google sign-in and no
dialog beyond Delete's; this is the accepted low-friction policy of the
[identity security policy](../architecture/spec.md#identity-and-session-security).
The audit records the actor, the target email, the Name and role before and
after, the time and the request correlation. After activation, a demotion or
deletion takes effect on the next request.

Granting Administrator (a new Administrator user, or raising a user to
Administrator) takes effect upon configuration activation and also creates,
in that activation transaction, a durable unacknowledged security event, and
independently queues an operational email to every Administrator who existed
immediately before activation. Other user changes keep the normal audit
without this alert. The event names the actor, the target address, the time
and the roles before and after, without session or provider credentials. It
remains prominent on every Admin dashboard until an existing Admin
acknowledges it; when another Admin existed at activation, acknowledgement by
the granting actor alone does not clear the event for those other recipients.
Delivery failure does not roll back or hide the grant: it follows durable
operational retry and escalation, while the dashboard event remains visible.
Acknowledgements and notification outcomes are audited. Each Acknowledge acts
[in place](../ui-conventions/spec.md#in-place-controls) on Home: the event leaves the list without a
reload.

### Ministry leaders from ParishSoft

Which Ministries a Ministry leader sees is not entered in the Admin portal
(Administrator decision, 2026-10-09, #922). A person signed in as a user of
Ministry leader or higher leads each Ministry where they hold one of the
campaign's [Ministry leader roles](#ministry-leader-roles) in ParishSoft,
matched by an email address their ParishSoft Member record lists. A
ParishSoft role without a user admits no one, and a Ministry leader who holds
no such role signs in and sees no Ministry (Administrator decision,
2026-10-10). Opening a Ministry page then answers 403 with a plain sentence
saying they lead no Ministry in the current ParishSoft data, so there are no
Ministry pages to show, never the generic sign-in denial. The
[data specification](../data/spec.md#ministry-leaders) defines the rule. A
change in ParishSoft reaches the portal with the next full refresh.

The Ministry assignments editor and the Chairperson suggestion, confirmation
and review pages are removed (#922); what became of their records is in the
[data specification](../data/spec.md#administration-user-and-policy).

### ParishSoft Ministry leaders without a user

Below the users table, a second table lists the people ParishSoft counts as
Ministry leaders ([#922](https://github.com/epiphany40223/parishkit/issues/922),
the leaders definition of PR #939) who have no user yet, with a way to import
them (Administrator, 2026-10-10). A leader is listed once for each usable
email address their Member record lists that matches no user (trimmed and
case-insensitive, as sign-in matches). When several leader Members list one
address, it is one row: the Member that the
[Name default order](#name-from-parishsoft) puts first gives the Name and
Member DUID, and Ministries led is the union, as their sign-in scope would
be. Leaders with no usable email are not rows; a muted line under the table
counts them, since they cannot sign in.

The columns, in the [shared order](../ui-conventions/spec.md#table-column-order),
are the selection checkbox, Member (the Member's name, worded as the
default Name the new user gets), Member DUID, Email, Ministries led (a short
list cut off with a count, such as "Choir, Ushers and 3 more") and Role,
which is always Ministry leader. The import review's table heads the same
column Member too, so each Member DUID follows its Member. Member, Member
DUID and Email sort; Member is the default order. The Member DUID stays here
at the Administrator's request (2026-10-12, pk-local batch 12,
[#932](https://github.com/epiphany40223/parishkit/issues/932#issuecomment-6135336883)):
it helps staff find a leader in ParishSoft.

**Import selected** and **Import all** are available only while there is
something to import (and Import selected only while a row is selected). The
**Import Ministry leaders** review, shown in the
[review dialog](../ui-conventions/spec.md#review-dialog), lists each chosen person as a new user
with the role Ministry leader and their default Name, and lists separately
anyone skipped because they became a user meanwhile. When Import selected
imports every ticked row, with nobody skipped and none left over for the
per-change limit, the dialog is a
[confirmation](../ui-conventions/spec.md#review-dialog) instead (#1356),
"Import the selected leaders?": the ticked rows are on the page behind it,
so it says only "This adds the 2 chosen leaders as users with the role
Ministry leader." Import all (which takes rows on other pages), a skipped
row or the limit keeps the full review. **Apply** records one
configuration change for the whole batch, with one audit entry naming the
count and one audit row per new user, and both tables then refresh in place.
Import never changes an existing user, never grants more than Ministry
leader, and so raises no security alert; the last-Administrator guard is
unaffected. Each imported Name stays editable on that user's Edit page.

The bulk bar is the [shared one](../ui-conventions/spec.md#admin-tables):
while no row is ticked, its reserved reason line under the buttons says
"Select at least one leader to use Import selected."; with no row at all,
both buttons are unavailable and the same line says there is nothing to
import. If the leaders cannot be read, the section says so in place of the
table and the users table above stays usable. Reading the leaders has no
rate cap, unlike the [Name lookup](#name-from-parishsoft): one read already
returns every row, so repeating it discloses nothing more;
the audit of each read and storing the session keys only as digests
([#995](https://github.com/epiphany40223/parishkit/issues/995)), so a
stored session value read from the database cannot be replayed to it, are
the protections. One change
adds at most 100 users, the limit of one configuration request; when more
are chosen, the review imports the first 100 by Name and says how many
remain for another import. Posted addresses only choose among the rows the
server reads again for the review: a Name, Member DUID or role is never
taken from the form. The audit's count entry is
`user_leaders_import_requested` and each new user's row
`user_leader_imported`, whose subject is that user's record ID.

The web login reads no Member, contact or roster record. It reads the table
through one SECURITY DEFINER function, `stewardship_leaders_without_user_v1`
(migration 0054), which takes no address, so it answers no question about
an address the Administrator chose. Like the
[Name lookup](#name-from-parishsoft), it answers only for the caller's live
Admin session (one begun after the latest Admin-access recovery) whose user
is an Administrator now, and it records its own `user_leaders_listed` audit
event for each read, with the number of rows and never an address. Its
leader conditions are those of the
[leaders definition](../data/spec.md#ministry-leaders), except that it also
counts leaders with no valid address; a PostgreSQL test pins the two to the
same answer.

Rejected: letting every ParishSoft leader sign in without a user (the
Administrator decided that sign-in needs an explicit user), and importing
automatically at each refresh (that would create accounts silently; the
Administrator sees and confirms who gets access).

### Upgrade from sign-in rules

The release that introduces users converts the old sign-in rules once, as the
[operations upgrade note](../operations/spec.md#users-upgrade-from-sign-in-rules)
describes. Before that release, the Portal users page showed a read-only
**Upgrade preview** panel with exactly what the conversion would do, so the
Administrator could add any missing address first; with the users release
the page shows only users, and `pk-admin users preview-upgrade` and
`upgrade-users --preview` keep the preview.

### Signed-in sessions

The **Signed-in sessions** page (`/admin/users/sessions/`, Administrator only,
in Users and access after Users) lists every interactive Admin session that
can act now, of any portal role
([#953](https://github.com/epiphany40223/parishkit/issues/953)). A session is
listed while it is not revoked, is inside its 12-hour absolute limit, was used
within the 60-minute idle limit
([identity and session security](../architecture/spec.md#identity-and-session-security))
and was issued after the latest offline Admin-access recovery. Command
sessions of the command line are left out; Automation access lists their
automation sessions.

The table's columns are User (the address, with "(you, this browser)" on the
reader's own current session), Signed in (the session's latest confirmation
with Google), Last activity, Expires (the absolute limit) and Actions. Times
are browser-local
([dates and times](../ui-conventions/spec.md#dates-and-times)). It is shown
whole and sorts by every data heading, most recent activity first by default.

Each row but the reader's own current session has **Sign out**, a
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) with
Revoke's icon. Its dialog says the session ends at once and that the person
can sign in again while they have access; deleting their user removes that
access. Confirming posts the session to the page's own address, which locks
the session, ends it if it is still live and redirects back to the table as
sorted, which the dialog redraws in place, announcing "Signed out …". A
session that had already ended by then (signed out, idle or expired, or
replaced by a newer sign-in) is left as it is and nothing is recorded; the
redirect then carries `outcome=already_ended`, and the dialog announces
"That session had already ended; the list is up to date." instead, from a
hidden `data-confirm-announce` line on that page, which the shared dialog
script reads in place of its own done words. `sort` and, on that page only,
`outcome=already_ended` are the page's only query parameters. The list reads
only the shown columns, never a session's key. The reader's own current
session, a command session and an unknown session are refused. The ending is the ordinary `admin_revoked`
[session ending](../architecture/spec.md#identity-and-session-security),
whose actor is the person signed out, and `admin_session_signed_out` records
the Administrator as actor with the same session as subject; both are in
[sign-in activity](#sign-in-activity). Signing out needs no fresh Google
sign-in, like revoking on Automation access, and has no command-line
counterpart (the route-parity ledger lists it as session chrome); the
operator's offline recovery still signs everyone out.

## Manual ParishSoft refresh

Admins may request an immediate full refresh from a confirmation page (the
Refresh from ParishSoft menu entry), or with the "Run a full refresh now"
button on the ParishSoft page and in the Admin home page's refresh
notice (same capability and CSRF rules). The action inserts a durable task and
returns immediately to the Refresh from ParishSoft page, which shows the task's
status ([navigation rule 5](../ui-conventions/spec.md#navigation-rules)). If a poll is running, no
concurrent poll starts; one manual full refresh may be queued to follow it.
Repeated clicks return/link to the existing queued run.

On the page itself, Refresh now acts
[in place](../ui-conventions/spec.md#in-place-controls): only the region
holding the button and the run below it is redrawn, so nothing above the
button moves. The requested run is remembered in the signed-in session, never
in the URL, until another refresh is requested or the sign-in ends; a run that
can no longer be read is forgotten. Below the button the page shows that run's
live status, the same status region its Background task page shows, polled
through the task's passive status read, with a link to that Background task
page for the history and technical details. When the run ends, the page
re-reads itself in place, so the data age above and the request key are
fresh. The breadcrumb is the way back; the page has no Cancel link.

The refresh page and the "Run a full refresh now" button say that a full
refresh re-reads everything (including Ministry rosters and giving) and usually
takes a few minutes; they say nothing of quick updates
([#1122](https://github.com/epiphany40223/parishkit/issues/1122), see
[ParishSoft refresh schedule settings](#parishsoft-refresh-schedule-settings)).
A refresh run's background task page,
and its row in the background-work list, name the run as a "Full refresh" or a
"Quick update" from its request kind (whatever the quick-update cadence, so the
label never claims a 15-minute cadence that is not configured), and describe
the current phase in words ("Downloading from ParishSoft", "Saving the
downloaded records", "Checking the new data", "Making the new data current").
The download phase has no count, so while a refresh is downloading the page
says so instead of a bare wait message; other steps without a count keep the
general message. Counts are labeled as records checked, since every refresh places every record into a
complete new copy and reuses unchanged records. Once a refresh has succeeded,
its task page says how many records it checked and how many changed, by
collection (for example, "Checked 30,639 records from ParishSoft; 12 changed
(3 Families, 9 contacts)."); a refresh with no ParishSoft changes says 0
changed, including a quick update recorded as
[unchanged](../data/spec.md#source-snapshot), which checked the current
snapshot's records without copying them. A record changed when its identity was added, removed or has a
different payload digest than in the previous promoted snapshot (the base
the new snapshot must still match to be promoted). Staging computes this
once, when it validates the corpus, and stores the per-collection counts as
`changes` in the snapshot's cursor, which the web role already reads, so no
schema or grant change is needed. A first load counts every record as
changed. The counts are display-only: if the comparison fails, staging omits
them, logs a classified `report_shaping_failed` WARNING (no exception text)
and the refresh continues; the page then says only how many were checked, as
it does when a snapshot's changes name a collection it does not know. A run that is still pending
or running after a restart explains why, from its newest restart event: an unexpected stop (for example a
server restart, recorded as an expired lease) or a temporary problem (a
retryable failure), with the attempt number. A finished run shows no such
notice; its history table still lists every attempt. Other background tasks get the
same retry explanation and a phase in words. For a task that finished more
than 30 days ago, the history no longer shows its heartbeat and progress
events, which the hourly maintenance removes
([#386](https://github.com/epiphany40223/parishkit/issues/386)); its claims,
transitions, retries and outcome stay.

## Data to fix in ParishSoft

Status: built
([#1011](https://github.com/epiphany40223/parishkit/issues/1011)). The
Administrator decided on 2026-10-11, in the
[#935](https://github.com/epiphany40223/parishkit/issues/935) follow-up,
that one obvious page should show every kind of data problem that is fixed
in ParishSoft, refused email addresses being only one of them. The page is a
summary: each section gives a count and links the existing page that lists
and fixes those problems. Every detail page stays as it is, and Outgoing
mail keeps its "Review refused addresses" link.

The Administrator settled the page's open questions on 2026-10-11
([#1011](https://github.com/epiphany40223/parishkit/issues/1011)), and the
rest of this section follows those decisions:

1. The page name is **Data to fix in ParishSoft**.
2. It is the last entry of the Parish data menu group, not an entry under
   Responses and reports.
3. The sections run: Families the form cannot open, refused email
   addresses, Families no mail can reach, census and roster changes to
   enter.
4. "No mail can reach" means no usable email **and** no usable mailing
   address (Home's definition); postal-only Families are not counted.
5. A role sees only the sections whose detail page it can open, so Staff
   see sections 3 and 4; Staff get no counts for sections 1 and 2.
6. The menu entry carries no count.
7. Home does not link the page for now; NAV-18's Next steps keep it
   unlinked (a default on #522 the Administrator may revisit).

### Data to fix page

- **Name:** **Data to fix in ParishSoft**, used by the menu entry, heading,
  breadcrumb, browser title and every link that names the page
  ([page names](#page-names-and-placement), #520). Its data line is the
  current campaign's name and the time of the ParishSoft data the counts
  were read from, in browser-local time.
- **Where:** the last entry of the Parish data
  [menu group](#menu-groups), after Refresh from ParishSoft, at
  `/admin/parish/data-to-fix/` (URL name `parishsoft_fixes`). Like the
  other Parish data menu entries it has no parent page: its trail is Home,
  Parish data, then the page. It is last so `/admin/parish/` still opens
  Parish settings for an Administrator. Staff, who see no other Parish data
  entry, see the group with this one entry, and `/admin/parish/` opens this
  page for them.
- **Who:** Administrators and Staff; never Ministry leaders, who may open no
  detail page behind it. A role sees a section only when it may open that
  section's detail page, checked through that page's own admission and
  capability check, so the summary never shows a count the viewer could not
  follow. A role that may open none of the sections does not get the menu
  entry. The menu entry is available whenever the role has it ("Never"
  unavailable): a section that cannot count right now says why instead.
- **Layout:** the heading, its "About this page" panel
  ([page help](../ui-conventions/spec.md#page-help)), then one block per
  section in the fixed order below. Each block has the section's name as a
  heading, one sentence with its count in plain words, one short line on
  where the fix is made, and a link to the detail page whose text is
  "Open" plus that page's name. It is a short list of blocks, not an
  [Admin table](../ui-conventions/spec.md#admin-tables): four rows of
  different kinds have nothing to sort, filter or page. The About panel
  explains that each problem is corrected in ParishSoft and shows here
  after the next refresh (linking Refresh from ParishSoft for
  Administrators), except a refused address, which an Administrator may
  also clear once the mailbox is verified.

### Data to fix sections

Sections run from the problem that blocks a Family most directly to the
routine worklist. The order is the same for every role, with the sections a
role may not see left out:

| Order | Section | Count, in plain words | Detail page and link | Roles | Reused read |
| --- | --- | --- | --- | --- | --- |
| 1 | Families the form cannot open | Families that cannot open the Family form because a Member's ParishSoft data has a value the form cannot accept | [Families the form cannot open](#system-health-page) | Administrator | `reports.source_form.blocked_families`, the System health sentence's own read, with its per-campaign and per-snapshot cache |
| 2 | Refused email addresses | Unresolved refused addresses, and how many Families they belong to | [Refused addresses](#refused-addresses) | Administrator | `jobs.delivery_reads.unresolved_refusals()`, the set behind Outgoing mail's link-line count, counted in one query |
| 3 | Families no mail can reach | Active parishioner Families with no deliverable email and no usable mailing address | The [active parishioner family directory](../reports/spec.md#active-parishioner-family-directory) with its "Can't be reached by email or mail" preset | Administrator, Staff | `accounts.admin_dashboard.unreachable_families`, Home's count, which is the directory's own "neither" selection |
| 4 | Census and roster changes to enter | Census changes in the [Census changes](../reports/spec.md#pending-census-changes) default view for the viewer (To do and Conflict, plus To review for an Administrator); later, roster changes to enter | Census changes; later, *Roster changes to enter* (under Ministry requests) | Administrator, Staff | `reports.census_changes.census_changes` with the page's default query; later, the roster filter's own read |

Notes on the sections:

- **Why this order.** A Family that cannot open the form cannot respond at
  all. A refused address stops every later campaign email to it, usually
  for a handful of Families. A Family no mail can reach gets no invitation,
  but the fix (finding an address) is slower. The census and roster
  worklist is the largest and least urgent: it is entered at any time and
  published after the campaign closes
  ([#528](https://github.com/epiphany40223/parishkit/issues/528)).
- **Families no mail can reach** uses the Home definition, not "no
  deliverable email": a Family with a usable mailing address still gets a
  postal invitation, and many Families have no email by choice, so their
  number is not a list of errors.
- **Roster changes** join section 4 with the #528 roster tick: until that
  step lands the section counts census changes only and says nothing about
  rosters. That step also lets the follow-up queue accept its *Roster
  changes to enter* view as a closed GET value (as it accepts `?ministry=`),
  so the link opens the filtered queue.
- **No new SQL.** Every count calls the function its detail page or Home
  already uses, with that page's own filters, so a count always matches the
  list its link opens. The page adds no query to any other page and no
  count to the menu; the source-form read is the cached one System health
  already pays for.

### Data to fix refresh and empty states

- **Read on page load only.** Counts are read when the page loads and are
  never polled or updated in place, as the menu's open counts are, so
  nothing appears, changes size or moves under the pointer
  ([no layout shift](../ui-conventions/spec.md#no-layout-shift)). The page
  has no controls of its own; reloading it, or coming back from a detail
  page, reads the counts again.
- **Each section stands alone.** A section whose read fails, or that cannot
  run now (no current campaign, no promoted ParishSoft data, a campaign
  without the census), keeps its block and says why in place of the count,
  with its link greyed out as an
  [unavailable menu entry](#stable-menu-shape) is when its detail page
  would refuse the visit too. The other sections still show. A failed read
  is logged as a `report_shaping_failed` warning whose shaping step names
  the section (`data_to_fix_<section>`), and the page is not an error
  page.
- **Zero.** A section at zero keeps its block and its link and says what
  that means ("No refused addresses: campaign email reaches every address
  it has tried"). When every shown section is zero, the data line adds
  "Nothing to fix right now". An empty section never disappears, so the
  page keeps one shape for a role.
- **Audit.** A view records one `data_to_fix_viewed` event whose count is
  the total of the section counts the viewer saw, never a name, address or
  value, as the detail pages' view events do. It is one total rather than
  a count per section because the audit context's fields are a closed list
  mirrored in SQL; a count per section would need a schema change.

### Data to fix command and downloads

- **Command line** ([#463](https://github.com/epiphany40223/parishkit/issues/463)):
  `pk-admin report data-to-fix` mirrors the page through its own functions,
  admitting as the page does with any session scope. It prints
  `campaign_id` and, under `sections`, one key per section the session's
  role may see
  (`form_cannot_open`, `refused_addresses`, `unreachable_families`,
  `census_changes`, and later `roster_changes`), each with its count (and
  `families` for refused addresses) or `unavailable` with the page's
  reason, counts only, and records the page's view event. The
  [admin automation coverage](../admin-automation/spec.md#operations) lists
  it.
- **No download.** The page offers no export of its own. Each detail page
  keeps its own downloads (Census changes and the directory offer CSV, XLSX
  and PDF); Refused addresses and Families the form cannot open offer none,
  by Administrator decision on #935. A combined file would join four
  different column sets, which the
  [downloaded files](../ui-conventions/spec.md#downloaded-files) rule (one
  column set, matching its page) cannot express.

## Follow-up workflows

Additional-information items show Family, DUID, text, submission time, needed
checkbox, followed-up checkbox/time, and Staff notes. Admin/Staff may search,
filter, sort, edit workflow fields, and see history. Marking followed up sets
the timestamp/actor; unchecking retains history and clears current state after
confirmation. That confirmation appears only once a completed item's
"Follow-up completed" is unticked, and Save stays unavailable, with a short
hint, until it is ticked (default, pending Administrator confirmation, #519);
the server still refuses an unconfirmed clear.

Save follow-up on an information item and on a Ministry follow-up request acts
[in place](../ui-conventions/spec.md#in-place-controls) (#519): the request's panel, its form and its
history are refreshed where the reader is, and "Follow-up saved." is
announced.

Both follow-up queues remember the view the reader is working through
([#534](https://github.com/epiphany40223/parishkit/issues/534)). Each queue page
remembers its applied filters, sort, page and page size in the signed-in
session (never in the URL), as a change's origin is remembered (navigation
rule 5). Links carry only an opaque token, and the session keeps the last 20
views. The one exception is a link that opens the queue on
[Ministry requests](../reports/spec.md#ministry-change-summary)
(`/admin/reports/ministries/`) for one Ministry by GET: the "Open requests"
link of Home's [My Ministries panel](#home-page) carries only the Ministry
number (`?ministry=<DUID>`), and the By Ministry counts on the same page add
the queue's own closed Request and Status values
(`?ministry=<DUID>&action=join&state=any`,
`?ministry=<DUID>&action=leave&state=any`, or the Ministry alone for
Unresolved,
[#1133](https://github.com/epiphany40223/parishkit/issues/1133)), so the
queue shows exactly the requests counted. On the page itself a By Ministry
count applies those filters in place rather than loading the address. Such a
link may carry only
`ministry`, `action` and `state`, each a single value the queue's filter
form accepts, never a search; a token and a Ministry never travel
together. The Ministry is checked against the person's current follow-up
scope, and one outside it is refused. The queue then remembers that view
under a token like any other. A link the queue cannot use (a malformed or
out-of-date address) says so in plain words and links back to the queue,
without asking the reader to reload it. An item opened from the queue carries the token, so its "Return to"
link reopens the same filters, sort and page. A token the session no longer
holds opens the default queue. Beside Save follow-up, **Save and next** saves
the same way (the same optimistic version check, history and audit event) and
then opens the next open item after this one in that view. The item just saved
is skipped, and so is any item that is not open: a Ministry request that is no
longer New or In progress, or an information item that is not a current
actionable request awaiting completion. An item that isn't in the view leads
to the view's first open item. The item page finds the next item while it
renders, inside its own guarded read, and a help line beside the button says
where it leads. After the last item, Save and next returns to the queue view
(Administrator decision, 2026-10-04). It acts [in place](../ui-conventions/spec.md#in-place-controls):
the next item's panel replaces this one, the address becomes that item's, focus
moves to its heading, and "Follow-up saved." is announced with the item's name.
A refused or stale save stays on the item exactly as Save does. The Save gate
holds both buttons until the save is valid. Opening the next item records the
same read audit as opening it from the queue.

Ministry workflow permissions are row-scoped. Admin/Staff see all; leaders see
and edit only the Ministries they lead. The interface supports queue filters,
status/outcome, contact-attempt entry, notes, history, and the Member's
authorized contact details. It never exposes financial or unrelated
Family data.

Each request's page shows a **Contact details** section for its Member
([#1157](https://github.com/epiphany40223/parishkit/issues/1157)), because
Members can share a name and the request says which Member is meant: email
and phones for either kind; for a join request the Family's known primary
address, gender and age; for a leave request the Member's current role in the
Ministry. Who sees it is exactly who may open the request: Administrators,
Staff, and the leaders of the request's Ministry. A Ministry leader sees only
the email addresses and phone numbers the Member allows to be shared, and
"Not published" for the others; Administrators and Staff see operational
contact. A Member the Family added on the form says so, since the details
are what the Family entered. Where the details come from, and the rule for a
request the report cannot read, are in the
[Ministry change summary](../reports/spec.md#ministry-change-summary). The
request page's view audit (`ministry_followup_viewed`) records the request's
`ministry_duid`, as the retired Members joining and leaving lists' views
recorded theirs; never names or contact values.

Below the queue table, **Export these requests** downloads the queue's
applied view (its filters, search and sort; every page of it, not just the
one shown) as CSV, XLSX or PDF (Administrator decision, 2026-10-12, option
(a) on #1157), replacing the retired Members joining and leaving exports.
A request whose Member is no longer active in ParishSoft is not in the file
(Administrator decision, 2026-10-13): the rows are the queue's own default
selection, which leaves such requests out once open
[#1217](https://github.com/epiphany40223/parishkit/issues/1217) lands, and
the export refuses (400) any Status choice but the queue's own statuses, so
the Staff-only choice of #1217 that lists only those requests is never
exported.
A line says how many requests the file holds; the count and the form's hidden
filters follow an in-place Apply, sort or page change, and the reader's
format and time zone stay as chosen. The rows are the queue selection's own,
so a Ministry leader gets only the Ministries they lead, exactly as on the
queue. The file's columns are the queue's (Member, Member DUID, Ministry,
Request, Status with Outcome split out, Last contact), then the request
page's contact details (Email, Phones, Known primary Family address, Gender,
Age, Current Ministry role; blank where a detail does not apply to the
request's kind). It has no Submitted column, as the queue has none
(Administrator decision, 2026-10-13, on
[#1157](https://github.com/epiphany40223/parishkit/issues/1157)); the request
page still shows when a request was submitted. Its Report information sheet
and PDF card
hold the parish, campaign, data date, request time, display time zone, age
reference date, count, filters and the privacy line. It carries the
retired export's sensitivity notice; like it, it needs no fresh sign-in
(that is for Family codes and financial detail, #547), and it asks for a time
zone, since Last contact is an action time. The file is
rendered on request, as Roster changes to enter is, rather than queued as a
stored export: a stored capture would need a schema change, because the
export capture accepts only one Ministry's join or leave list or a packet.
A PDF holds at most 1,000 requests (`EXPORT_PDF_LIMIT`; Administrator's
default decision, 2026-10-12, which may be revisited); CSV and XLSX are not
limited. Rendered on request, the file must finish inside the 60-second
interactive [read guard](../data/spec.md#campaign-read-guards), and the
shared PDF writers take about 19 ms a request: timed on 1,000 synthetic
Families with 3,002 requests, CSV took 1.2 s, XLSX 2.7 s and PDF 58 s (50 s
as a table), so 1,000 requests is about 20 s, with room for the slower
production server. While the queue's filters select more, the format menu
offers PDF unavailable and the export line says "The PDF is limited to 1,000
requests: choose CSV or XLSX, or narrow the filters."; the menu is redrawn
with each applied view, in place, keeping the reader's choice where it is
still offered. A PDF asked for anyway (a stale page) is refused with that
reason and a link back to the queue, before any contact detail is read.
Each download is audited as `ministry_followup_exported` with its count, the
sorted Ministry DUIDs in the file (`ministry_duids`, which the log's Ministry
filter finds) and whether its contacts were operational or
publish-restricted (`ministry_operational`); never names, contact values,
filters or search text.

The queue's columns follow the shared
[table column order](../ui-conventions/spec.md#table-column-order)
([#932](https://github.com/epiphany40223/parishkit/issues/932)): Member (the
row header, linking to the request), Member DUID, Ministry, Request (Join
or Leave), Status and Last contact. Like every Admin table it has no
Ministry DUID, and it has no Submitted column (Administrator decision,
2026-10-11, [#1064](https://github.com/epiphany40223/parishkit/issues/1064)),
so the table is narrower; the request page shows when the
request was submitted. A Member added
on the form is not in ParishSoft yet, so their Member DUID reads "New
Member". The installed selection orders the rows and returns each request's
Ministry DUID but not its Member key, so the page reads the Member keys of
the rows shown in one query by request id; a schema change was not needed.
The rows are newest first by default, the selection's own order, which no
heading shows; Member and Ministry sort through the selection's own orders,
and the selection's oldest-first order is no longer offered. Member DUID,
Request, Status and Last contact do not sort, since the selection has no
order for them. A long Member name is cut short with an ellipsis on one
line; a long Ministry name wraps to at most two lines, ending in an
ellipsis only when even two are too few, so a leader can tell similar
Ministries apart without hovering
([#1136](https://github.com/epiphany40223/parishkit/issues/1136)). A name
cut short shows whole in a tip on hover, keyboard focus and tap
([table fit and row
height](../ui-conventions/spec.md#table-fit-and-row-height)). Member names read
"Last, first", as the
[Member-name rule](../ui-conventions/spec.md#family-and-member-names) asks,
so the Member sort orders by surname; the search also finds the "first last"
form. Migration 0069 changed the selection to build them
([#932](https://github.com/epiphany40223/parishkit/issues/932)).

Ministry follow-up has no assignment (Administrator decision, 2026-10-04,
[#552](https://github.com/epiphany40223/parishkit/issues/552)): each
Ministry's leader contacts the parishioners who asked to join or leave it,
and the portal does not track coordination between people. The queue and
request pages therefore offer no assignee field, filter or column; the status
choices are New, In progress, Resolved and Closed: no response. A request recorded as Assigned before this decision reads as New
(that state only ever meant "has an assignee") until its next save stores the
chosen status with no assignee; until then an explicit New filter on the
queue does not match it, while Unresolved and Any status do. Past
history entries keep the assignment they recorded. The data model keeps the unused column and state, as described in
the [data specification](../data/spec.md#follow-up-records).

The queue page is compact
([#1064](https://github.com/epiphany40223/parishkit/issues/1064)). Its
filters share one compact filter bar that takes two rows at laptop
widths; at phone width each field takes its own row.
Every row of the table is one line, or two where a long Ministry name wraps
(#1136), per [table fit and row
height](../ui-conventions/spec.md#table-fit-and-row-height): any other cell
holds one short value that does not wrap, so a table wider than the page
scrolls sideways inside its own box. Request reads Join or Leave, and "Leave
(cannot participate)" for a Member who cannot participate in any ministries,
the whole reason being the marker's tooltip and written out on the request
page. Status is one line, with a resolved request's outcome after a colon
("Resolved: Joined the Ministry"); Closed: no response, whose outcome is always
No response, does not repeat it. A Ministry since removed from the campaign
carries a small "Removed" badge after its name, with "No longer in this
campaign" as its tooltip and screen-reader text, instead of that sentence in
the cell. Last contact uses the compact date and time of dense tables
([date format](../ui-conventions/spec.md#date-format)).

The queue lists current requests only
([#1131](https://github.com/epiphany40223/parishkit/issues/1131)). Its filters
are Search, Ministry, Request, Status and Outcome. There is no Request history
filter, and Status offers no Withdrawn or Replaced by Family: those are the
system's bookkeeping for a Family's resubmission, not a leader's decision. A
request page reached by an old link still says the Family has since replaced
or withdrawn the request. Each request page's history shows the past.

Requests for Members no longer in ParishSoft are left out of the Ministry
leaders' work (Administrator, 2026-10-13,
[#1217](https://github.com/epiphany40223/parishkit/issues/1217)). A Member
request is one when ParishSoft's data the campaign reads (the current
snapshot, or an archived campaign's own) no longer has the Member, has them
in another Family, or marks them inactive or deceased. A Member added on the
form is never one. One SQL function decides this
(`stewardship_member_source_status_v1`, migration 0092), the definition
[#1216](https://github.com/epiphany40223/parishkit/issues/1216) shares:

- A Ministry leader never sees, counts or acts on such a request: the queue,
  its totals and the By Ministry counts on
  [Ministry requests](../reports/spec.md#ministry-change-summary), Home's My
  Ministries panel, the menu count and the
  [packets](../reports/spec.md#multi-ministry-follow-up-packet) leave it
  out. Its request page reads as an unknown request.
- Staff and Administrators see the same by default, so their counts match.
  Their queue's Status filter has one more choice, "Members no longer in
  ParishSoft", which lists only these requests, of any status. Each is
  marked beside the Member's name with the shared
  [ParishSoft status](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
  badge ("Not in ParishSoft", "Inactive in ParishSoft" or "Deceased in
  ParishSoft"), so it cannot be taken for a Ministry's activity mark. A
  Member the data no longer has in this Family has no name, so the badge
  alone is the row's link, and its request page's heading names it by that
  status (#1216). That view has no selection column or bulk bar. A
  request page opened from it gives the specific reason (no longer in
  ParishSoft, now in another Family, inactive or deceased) and has no
  follow-up form.
- [Roster changes to enter](#roster-changes-to-enter) still lists such a
  Member's resolved joins and leaves, with the same words in its
  ParishSoft status column and downloads, because the roster work may still
  be real.
- The server refuses a single or bulk edit of such a request, as it refuses
  a stale one (409), naming the request in the bulk dialog, so a page read
  before ParishSoft changed cannot record anything.
- Nothing is deleted, cancelled or resolved, and no Family code or link
  changes. The filter is worked out on every read, so when ParishSoft lists
  the Member again the request comes back as it was.

Leaders record one outcome for several requests at once
([#1131](https://github.com/epiphany40223/parishkit/issues/1131)), through the
shared [selection column, bulk bar](../ui-conventions/spec.md#admin-tables) and
[confirmation dialog](../ui-conventions/spec.md#row-actions-and-confirmation).
Only open requests (New or In progress) have a selection box. The selection
covers the current page and the page help says so.

The bulk bar offers Mark as Joined and Mark as Declined for requests to join,
and Mark as Left and Mark as Stayed for requests to leave, with the outcome
labels of [#1132](https://github.com/epiphany40223/parishkit/issues/1132)
(Declined / no longer interested; Stayed in the Ministry). A button is
available only while every ticked request is of its kind (Administrator,
2026-10-13), as a [prerequisite gate](../ui-conventions/spec.md#prerequisite-gates):
with only requests to join ticked, Left and Stayed are unavailable, and the
other way round; with both kinds ticked, all four are, and the bar's reason
line asks for one kind. Filtering Request to Join or Leave makes Select all
choose one kind. Each button opens one dialog, which names the count and the
outcome and applies to every ticked request. The dialog has an optional
"Record a contact attempt" (How, Date, Time and What happened, as on the
request form), which is applied to each request. Its fields return to their
defaults each time it opens.

Recording posts the chosen requests, each with the version its row showed,
without a page load. The server applies them all or none, in one transaction
under the shared work order, with every request locked first. Each request
gets its own revision, projection and `ministry_request_updated` audit event,
exactly as a single save does. Its notes carry forward, and every event
shares one correlation id. One `ministry_followup_bulk_applied` event records
the action, with how many requests it changed and how many were chosen.

The server refuses the whole batch, saving nothing, when:

- any request is outside the leader's Ministries (denied, as an unknown
  request is);
- any request changed or closed since the page was read, or its Member is
  no longer in ParishSoft (each named in the dialog);
- any request is not of the button's kind (named in the dialog; the page
  never offers such a batch); or
- the contact attempt is refused, as on the request form. Except for a
  missing time zone, which Date and Time cannot fix, that refusal is shown at
  the dialog's Date and Time, which it marks, in place of the box below the
  buttons; Record outcome waits, naming it, until the dialog's fields change
  ([#1332](https://github.com/epiphany40223/parishkit/issues/1332)).

A batch is at most one page of the largest page size. Posting the same
action again replays each request's revision rather than writing a second.
On success the dialog redraws the table from the remembered view, keeping
its filters, sort and page. Each bulk revision appears in the request page's
Follow-up history like any edit, marked "(bulk)". The follow-up commands of
the command line (PR 11,
[#463](https://github.com/epiphany40223/parishkit/issues/463)) are still to
come; the bulk action is listed with them.

The request form is compact
([#1014](https://github.com/epiphany40223/parishkit/issues/1014)): on a
desktop screen Status and Outcome share one row, and How, Date and Time
share another, while Notes and "What happened" span the form; at phone width
every field takes its own row. The form enables only the fields that apply,
and the others stay in place, greyed, so a choice never moves the fields
after it ([no layout shift](../ui-conventions/spec.md#no-layout-shift)). The
outcome is available only for Resolved, which requires one; Closed: no
response always records the outcome No response, and open statuses have none.
A contact attempt's date, time and "What happened" are available only once a
channel is chosen; the date and time are then required and the description is
optional, as the How field's tip says. Notes are optional except for the
outcome Other, which the data model requires. Greyed fields are disabled and
not sent, and Save stays unavailable, with a short hint saying what is
missing, until every available required field is complete. Fields
re-evaluate whenever the browser restores the page or its values (going back
to it). The Admin portal requires JavaScript
([#565](https://github.com/epiphany40223/parishkit/issues/565)), so there is
no script-off variant; the server still ignores whatever does not apply to the
chosen status or channel and refuses an incomplete Resolved. The outcome list
offers only the outcomes the request's kind can record, from the same rule the
server applies, in that kind's words: a join offers Joined the Ministry and
Declined / no longer interested, and a leave offers Left the Ministry and
Stayed in the Ministry, which is the stored `declined` on a leave request (the
Member did not leave), beside the outcomes both kinds share. The queue's
Outcome filter, which covers both kinds, shows declined as Declined / stayed.
Every outcome label follows the
[packet's mapping](../reports/spec.md#multi-ministry-follow-up-packet). If the server still refuses a save (for example a contact time in the
future), it shows the same request page again in place, with the submitted
values kept, a summary naming the one problem, and that problem shown
[at its fields](#bootstrap-and-first-admin-wizard): a missing or unsuitable
outcome marks Outcome, Other without notes marks Notes, a contact attempt in
the future, or one that reached the server without a usable time zone (see
below), marks its Date and Time, a time the [time entry](../ui-conventions/spec.md#time-entry) rules
cannot read marks Time with that rule's message, and an incomplete one marks
whichever of them is missing (both when the date is malformed). The view maps each refusal to
its fields in one table. The contact fields keep the time-zone note in their
description, followed by the error.

The page catches a contact attempt in the future as it is typed (#592). The
Date picker stops at today in the browser's time zone (kept current if the
page stays open past midnight). A later date, with or without a time, or
today with a later time, shows the server's own message by Date and Time at
once, marks both in error and makes Save unavailable with that message as
its hint. It clears as soon as the values are no longer in the future,
including when a time a minute or two ahead has simply passed (the page
checks again each minute while Save is held). The check uses the same message
element as the server's refusal, so only one message shows. The server still
refuses a future time, with a message that also says to check the computer's
clock; when the browser's clock disagrees with it (a wrong clock, or a page
left open), that refusal stands, summary and marks included, until the
reader edits Date or Time.

A contact attempt's date and time are typed in the browser's time zone, named
in a note beside them, and every follow-up time shown (submitted, history,
contact, last contact and source as of) is in that zone ([browser-timezone
rule](../ui-conventions/spec.md#dates-and-times)). The zone is sent only with a
contact attempt. When the browser reports no zone, the note stays hidden and
Save stays unavailable with a hint to check the computer's time zone setting. A
contact attempt that reaches the server without a usable zone (a page opened
before this rule, for example) is refused in place like the other correctable
refusals, keeping the typed values and asking the person to save again.

Manual census items may be marked resolved externally or ignored by Admin or
Staff, with notes, on the
[Census changes](../reports/spec.md#pending-census-changes) worklist
([manual census resolution](../reports/spec.md#manual-census-resolution)).
API-writable changes are view-only for Staff. An Administrator approves or
ignores them on the same worklist
([automatic census review](../reports/spec.md#automatic-census-review)), and
publication follows the [data workflow](../data/spec.md#review-and-publication).

The ParishSoft API cannot change Ministry rosters, so a join or leave is
always entered in ParishSoft by hand
([#528](https://github.com/epiphany40223/parishkit/issues/528), gap G28). A
request resolved by a person as *joined* or *leave confirmed* carries an
**Entered in ParishSoft** tick that Staff and Admin set or clear
[in place](../ui-conventions/spec.md#in-place-controls), on the request's
page and on [Roster changes to enter](#roster-changes-to-enter); who and when
are kept as history. A request resolved because the ParishSoft roster already
shows the change (it has a resolution source) needs no tick and shows
*Already in ParishSoft*. For Staff and Admin, the follow-up queue's Status
column says only "Entered" or "Not yet entered", one line, with who and when
on the request's page. Ministry leaders have no ParishSoft access, so they
never see the tick
([#1160](https://github.com/epiphany40223/parishkit/issues/1160)): no mark,
who, when or form on their request pages and no Status line in their queue,
and the server refuses their set or clear (the view, the service and the SQL
guard each check). Their page help and the Status field's help say Staff
enter roster changes in ParishSoft, where Staff and Admin read "update
ParishSoft yourself". Ministry leaders never see Roster changes to enter
either. A resolved request is a closed outcome
that a later Family response never replaces (a new request for the same
change is a separate request), so the tick stays with the request it was
made on. The tick is not a field on the request: each set or clear is an
immutable row of its own history table, added by forward migration 0061, and
the request row never changes. Marking a request resolved never changes
ParishSoft, and this tick only records that a person did.

### Roster changes to enter

**Roster changes to enter** (`/admin/reports/ministries/follow-up/roster/`,
Administrators and Staff, linked from Ministry requests, which is its
breadcrumb parent) is the list staff work from. It lists the resolved joins
and leaves with no resolution source that are not yet ticked, or, with
**Show** set to all, every one of them, to find and clear a mistaken mark,
and **Ministry** narrows it to one Ministry (a closed DUID value, no schema
change). Its columns follow the
[table column order](../ui-conventions/spec.md#table-column-order): Member,
Member DUID ("New Member" for a Member added on the form), ParishSoft
status (blank while ParishSoft lists the Member in that Family; otherwise
Not in ParishSoft, Inactive in ParishSoft or Deceased in ParishSoft,
[#1217](https://github.com/epiphany40223/parishkit/issues/1217): such a
change stays listed, since it may still need entering), Family, Family
DUID, Ministry, Request (Join or Leave), Resolved, Resolved by,
Replaced by a later response (Yes when the Family answered again after the
change was resolved), Entered in ParishSoft (Yes or Not yet), Entered by and
Entered at, with the tick last as the row's
[row action](../ui-conventions/spec.md#row-actions-and-confirmation); a resolver or marker whose portal user is
gone shows as "Former portal user"; times are browser-local in the compact date format. Every named
column sorts in place, and Show, Ministry, sorting and paging travel in the
address,
since nothing on the list is private search text. A resolved change stays
listed after the Family answers again (marked as replaced by a later
response), since it still has to be entered. A tick there keeps the list's
Show, Ministry, sort and page; a tick someone else changed first leaves the
list as it is with a notice, which is what the page announces; the notice's
two lines are kept while empty
([no layout shift](../ui-conventions/spec.md#no-layout-shift)).

Several changes not yet ticked can be marked at once
([#879](https://github.com/epiphany40223/parishkit/issues/879)) through the
shared selection column, bulk bar and confirmation dialog of
[row actions](../ui-conventions/spec.md#row-actions-and-confirmation): the
bar's only action is Mark entered in ParishSoft, and clearing stays per row.
The dialog posts to `roster/marks/`, which records each chosen tick exactly
as a single tick is recorded (its own entry and audit event), all in one
transaction or none; a change someone else ticked or cleared first refuses
the whole request, naming that change in the dialog. A change listed twice
(the Family answered again and both requests were resolved) names each row,
in its box, its tick and that refusal, with the day it was resolved in the
browser's time zone
([#558](https://github.com/epiphany40223/parishkit/issues/558)); the dialog
posts the browser's zone for the refusal, which leaves the day out rather
than guess when no known zone came.

Its CSV, XLSX and PDF downloads carry the page's columns, without the tick
([downloaded files](../ui-conventions/spec.md#downloaded-files)), for every
row of the chosen Show and Ministry, in the browser's time zone (the PDF in the parish's
compact date format). The view and each download are audited as counts.

The [Ministry change summary](../reports/spec.md#ministry-change-summary)
does not show the tick: its stored export cannot carry it without replacing
the export capture functions, and a page that showed what its downloads do
not would break the downloaded-files rule. Roster changes to enter is where
the tick is read and set; the Administrator may ask for the summary to carry
it too, as its own forward migration
([#528](https://github.com/epiphany40223/parishkit/issues/528)).

## System health

The **System health** page answers "is the system healthy, and what is
running?" in one place, and lets an Administrator fix the routine problems
that used to need someone on the server
([#530](https://github.com/epiphany40223/parishkit/issues/530), gap G07 in
the [#523 use-case analysis](https://github.com/epiphany40223/parishkit/issues/523),
use cases ADM-19, ADM-29, ADM-35 and ADM-40). The Administrator decided on
2026-10-04 that the page has buttons, not only status and instructions: take
a backup now, clear a halted mail sender, accept one large ParishSoft change,
and turn off debug logging. Each button is Administrator-only and audited.
The Administrator settled the remaining questions the same day; the
[System health decisions](#system-health-decisions) record them, and the
text below follows them.

Work package
[ADM-13](../../../plans/stewardship/admin-portal.md#adm-13-system-health-page)
delivers it. Status: the read-only page has landed (PR 1 and PR 2a–2c of the
[ADM-13 checklist](../../../tasks/stewardship/admin-portal.md#adm-13-system-health-page)):
the problems list, the six panels, the status fragment, Home's problem lines,
Families the form cannot open, the daily limit's 24-hour count and resume
time, and the
`system health` command. Three of the four actions have landed: **Take a
backup now** (PR 3), **Accept this change once** (PR 5a to 5c, with its
example Families) and **Turn off debug logging** (PR 6a
and 6b, with Testing's **Allow debug logging again** in PR 6c). For
**Clear the halt** (PR 4) no panel offers a button yet; its problem sentence
names the runbook step instead, and that step, listed under
[runbook steps the page replaces](#runbook-steps-the-page-replaces), remains
the way to do it. A halt's kind arrives with PR 4.

### System health page

- **Where:** the first entry of the System [menu group](#menu-groups), at
  `/admin/system/health/` under the [URL scheme](#url-scheme) (URL name
  `system_health`). Because it is the group's first entry, `/admin/system/`
  opens it ([decision 8](#system-health-decisions)).
- **Who:** Administrators only. Viewing uses the Administrator-only
  `SYSTEM_LOGS` capability that System logs uses. Each action also needs the
  `CONFIGURE` capability, checked again on the server and in SQL (see
  [shared rules for health actions](#shared-rules-for-health-actions)).
  Staff get no view of it ([decision 1](#system-health-decisions)).
- **At a glance** ([#1214](https://github.com/epiphany40223/parishkit/issues/1214)).
  The page is a dashboard: one [overall status line](#overall-status-line),
  then one [tile](#health-tiles) per area, each with its state and a
  one-line summary. Healthy tiles are closed; warnings and errors are open,
  with each problem's sentence and its action together inside the tile, as
  the [health dashboard](#health-dashboard) describes. Each problem is one
  plain sentence saying what is wrong and what the Administrator can do: a
  button on this page, another Admin page, or a runbook section for the
  server operator. For example: "No backup has finished since 2:00 AM
  yesterday. Take a backup now, or ask the server operator to check the
  backup schedule." A condition that affects several services or processes
  at once (debug logging, a service that stopped or never reported, a halted
  or outage-paused mail sender) is one problem whose sentence names them all
  ("Debug logging is on in Production in the web portal, background worker,
  scheduler and both mail senders"), so Home lists it, and screen readers
  hear it, once (#686); the page shows it in the tile the
  [problem registry](#health-problem-registry) names.
- **A refused address not set aside (#1201).** When an
  [address follow-up](../background-processing/spec.md#addresses-refused-for-now)
  could not record an address refused permanently as a recipient refusal,
  its CRITICAL `retry_refusal_unrecorded` entry is a problem for exactly as
  long as the critical-problems banner shows it (inside the banner's 24
  hours and not acknowledged), so the banner's one Acknowledge clears both;
  the alert email and the System logs entry remain. Its sentence says how
  many addresses, since when, that later emails still go to them, and that
  **Report a bounced address** on the email's
  [Mail message page](#mail-message-page), where the address is labeled
  refused permanently, sets it aside; it links the newest such email. It is
  read with the page's other facts in the same statement, so Home's problem
  lines carry it without another query.
- **Families the form cannot open (#774).** Its own tile's details hold one
  sentence that says how many Families cannot open the Family form because a
  Member's ParishSoft data has a value the form cannot accept, linking the
  **Families the form cannot open** list (`/admin/system/source-form/`,
  Administrators only, no menu entry). The list comes from the same check as
  the server's `source-form-check` command, over every portal-eligible
  Family of the current campaign and the current snapshot, so it includes
  Families that have not tried the form. It shows the Family name, Family
  DUID, Member DUID and the field to correct in plain words, never the value
  (not even a Member's name, so it is the one Member table with no Member
  name column; see
  [Table column order](../ui-conventions/spec.md#table-column-order)), sorted and
  paged in place, and says the Family can open the form after the correction
  and the next ParishSoft refresh.
  The Family name is therefore the surname alone, the recorded exception to
  the "Surname, heads" Family name rule
  ([#932](https://github.com/epiphany40223/parishkit/issues/932)): the heads'
  names are Member values, and a head's name can be the very value the form
  refuses.
  The result is cached in memory per campaign, snapshot and campaign
  configuration, so a refresh clears it. The sentence is read on page load
  only, never by the 10-second status poll; if the check cannot run, it
  says so instead. Opening the list records `source_form_viewed` with the
  number of Families listed. One function (`source_form_tile`) picks the
  tile's summary and its state from that read, the first that holds of:
  "Could not be checked" (Warning), "3 Families cannot open the form"
  (Warning), "No ParishSoft data yet", "No current campaign" and "Every
  Family can open the form". While the tile is a Warning, the sentence is
  its problem item; otherwise it is a plain line. The list's link is drawn
  only while Families are listed or the check could not run, since with no
  data, no campaign or no Family listed it would open an empty list
  ([conditional fields](../ui-conventions/spec.md#conditional-fields)).
- **Pages and emails to re-save (#838).** Its own tile's details hold one
  sentence that says how many of the current campaign's saved pages and
  emails should be re-saved, linking **Pages and emails**, followed by the same linked list as
  [Go-live readiness](#production-transition). It is read on page load only,
  in its own read-only snapshot, never by the 10-second status poll, and is
  not one of the problems; with no current campaign, or when the read cannot
  run, the sentence says so instead. The re-save findings stay outside the
  shared problem list, as a recorded exception: see
  [health problems on Home and in the banner](#health-problems-on-home-and-in-the-banner).
  One function (`resave_tile`) picks the tile's summary and its state, the
  first that holds of: "Could not be checked" (Warning), "1 email cannot
  be sent until it is fixed", followed by "and 2 more" for the other
  items (Error), "3 to re-save" (Warning), "No current campaign" and
  "Nothing to re-save". While the tile is an Error or a Warning, the
  sentence is its problem item (an error notice for a blocked email),
  followed by the list; otherwise it is a plain line. **Pages and emails**
  is linked only while items are listed or the read could not run.
- **The page-load tiles' help.** Why these two tiles do not follow the
  poll (each check reads every Family, or every saved page and email, so
  a reload checks again) is in a toggletip beside each sentence. What
  keeps a Family from the form, and how it gets the form back, is in
  "About this page".
- **Panels.** The [health panels](#health-panels) define what each tile's
  details show when opened. The tiles come in a fixed order: mail (why
  sends are waiting and the mail sender, together), ParishSoft refresh,
  backups, debug logging, Families the form cannot open, pages and emails
  to re-save, and version and services. During a send, mail is what the
  Administrator watches, so it comes first.
- **Home.** For viewers who may open this page, Home's problems list (see
  [Home page](#home-page)) adds one line for each System health problem,
  linking its tile on this page. Other viewers see no such line. These
  lines join the problems list that ADM-12's Home work (#568, NAV-18,
  with its Today line) renders, so whichever of the two lands second adds
  its lines to the other's list rather than making a second one. ADM-13
  landed first, so its lines are Home's problems list, a panel at the top
  of Home, each with the page's own sentence; NAV-18 added Next steps without merging
  the lists (a later slice on #522). Until they are merged, a problem
  Home already reports elsewhere (a failed or late ParishSoft refresh, a
  failed off-site copy) is shown once, as the System health line, to those
  who see the lines (dashboard slice 7). The
  [debug logging banner](#navigation-and-home) and the critical-problems
  banner link this page too. How Home's lines and the critical-problems
  banner stay consistent with the tiles is in
  [health problems on Home and in the banner](#health-problems-on-home-and-in-the-banner).
- **Live and in place.** The page needs JavaScript, as every Admin page
  does (see the [JavaScript requirement](../ui-conventions/spec.md#javascript-requirement)). It
  updates itself like the other
  [self-updating pages](#background-indicators): every 10 seconds, it reads
  a status fragment that records no audit row and does not extend the
  session, and it pauses while the tab is hidden. Only opening the page
  records an audited `system_health_viewed` event. The fragment
  (`system_health_status`) and each action's route are listed in the
  automation spec's [action inventory](../admin-automation/spec.md#operations). Each action's preview,
  confirmation and progress appear in place, through the shared
  [in-place controls](../ui-conventions/spec.md#in-place-controls), so the page never reloads or
  moves the reader to the top.
- **Plain words, help hidden.** Times are shown and entered in the browser's
  local time zone, without "UTC" labels. States use words ("Halted",
  "Waiting for the daily limit"), not internal names. Internal names, such as
  an incident kind, appear only under a closed "Technical details"
  disclosure. Each tile shows its state, its summary and, when open, its
  button. What each state means, and what a panel cannot see, is in the
  page's "About this page" panel
  ([page help](../ui-conventions/spec.md#page-help)), hidden by default.
  The panels below say which explanations go there.
- **Cost.** A status read uses one read-only snapshot, takes no lock (in
  particular not the global work-order lock that sending takes), and reads
  only small rows and indexed counts. It never calls ParishSoft, Google or
  Slack. The only checks that reach an outside service are the ones an
  action's preview starts, which the actions below describe.
- **Shared service code.** A new service module,
  `parishkit.stewardship.system_health`, holds one read model
  (`SystemHealth`, with the defined `to_document()` projection that the
  [read models](../admin-automation/spec.md#read-models) rule requires) and
  one function for each action, each taking an
  [`AdminCaller`](../admin-automation/spec.md#caller-seam). The view only
  parses the form and renders the read model. The matching commands call
  the same functions, so the page and the command line cannot disagree about
  what is allowed.

### Health dashboard

The Administrator asked on 2026-10-13
([#1214](https://github.com/epiphany40223/parishkit/issues/1214)) for the
page to read as a dashboard: one glance shows that everything works,
warnings are obvious, errors are more obvious still, and more detail opens
only on request. This section defines that layout. The
[health panels](#health-panels) remain the definition of what each area
shows once opened, and the [health actions](#health-actions) keep their
rules; only where things sit and when they show changes. Nothing on the page
today is removed, only disclosed, including the controls added by
pull requests 1206, 1210 and 1213. The issue records the rejected
approaches: a metrics dashboard with time-series charts, a restyle of the
current page, a colour-only status, hiding healthy areas, and a second page
for details.

Status: the frame and all seven tiles are built. The first dashboard pull
request delivered slices 2a and 2b of the
[delivery slices](#dashboard-delivery-slices), together with the parts of
slice 1 the frame needs: the registry's tile and severity for every
existing kind, the split of a grouped process problem, and `severity` and
`tiles` in the command's document. Slice 3 delivered the Mail tile: its
Working summaries in a fixed order (see
[why sends are waiting](#why-sends-are-waiting)), one line for both mail
senders while they agree, and its explanations moved into toggletips and
"About this page". A halt's kind and **Clear the halt** wait for ADM-13
PR 4, which adds halt identities, so the Mail tile has no action region
yet. Slice 4 delivered the ParishSoft refresh tile: its Working summaries
in a fixed order (see the
[ParishSoft refresh panel](#parishsoft-refresh-panel)), the refused
counts inside the refusal's own problem item, listed last so Accept this
change once follows them, and a panel that repeats none of the tile's
problems. Slice 5 delivered the Backups and Debug logging tiles: their
Working summaries in a fixed order (see the
[backups panel](#backups-panel) and the
[debug logging panel](#debug-logging-panel)), the Backups page findings
as Warning items of the tile's problems, and panels that repeat none of
their tiles' problems. Slice 6 delivered the page-load and services
tiles: the summaries and states of Families the form cannot open and of
pages and emails to re-save chosen in one place (see the
[System health page](#system-health-page)), each finding as its tile's
problem item, the Version and services tile's Working summaries (see the
[version panel](#version-panel)) and a version panel that repeats none of
its problems. Slice 7 delivered Home and the banner, as
[health problems on Home and in the banner](#health-problems-on-home-and-in-the-banner)
describes: Home's icons, state words and tile links, one display of a
problem Home also reports in its own notice, the registry's banner mapping
with the shared words and the banner's tile links, and Accept this change
once acknowledging through that mapping. The rest of slice 1 delivered the
four former page notices as shared kinds of the
[problem registry](#health-problem-registry), so Home lists them too: an
unknown No-reminders WorkGroup (an error while Reminders are scheduled), a
newest backup sealed to another key under the one-warning-per-cause rule, a
requested backup that ended without a backup, and, in Production, off-site
copies that are not set up, with Home's fallback line when the backup key
cannot be checked. The Administrator
answered the dashboard's questions on 2026-10-13; the
[decisions](#administrator-decisions-2026-10-13) record the answers. The
frame applies these defaults:

- **The refusal's counts end the ParishSoft refresh tile's problems**, and
  Accept this change once follows them directly, as the table asks. The
  refusal's item comes after the tile's other problems and the unknown
  No-reminders WorkGroup, whatever the registry order.
- **The overall line on a narrow screen.** The Not current form ("Not
  current. Not checked since 3:42 PM", then the button) cannot fit one
  320-pixel line, nor one line beside the menu on a window just wide
  enough to show it. Wherever the line itself is too narrow, it wraps
  after the state word, and the line reserves the height of that form in
  every state, so it still never moves the tiles. Only a change of window
  width changes this, never a poll.
- **A refused change already accepted once** is shown in the ParishSoft
  refresh tile but does not count toward its state, as the page counted it
  before the dashboard.
- **A refused check.** When a check is refused (signed out or no longer
  allowed), the page turns Not current at once, with no other notice in
  the line; the Admin session's own dialog offers to sign in again.
- **Families the form cannot open, before any ParishSoft download**, is
  Working, with the summary "No ParishSoft data yet".
- **Working summaries** are final for every tile: the Mail tile's
  (slice 3), the ParishSoft refresh tile's (slice 4), the Backups and Debug
  logging tiles' (slice 5), and the page-load and Version and services
  tiles' (slice 6).

#### Overall status line

The page leads with one line, directly after its heading and its "About
this page" panel. It is the first thing in the polled region, so it follows
the 10-second poll. "Last checked", outside the polled region, follows it.
Its look per state (icon shape, colours, start bar) is the
[status indicators](../ui-conventions/spec.md#status-indicators)
convention's; this page adds only the words.

| State | Exact text | Look |
| --- | --- | --- |
| No tile has a warning or an error | All working | Working |
| Some tiles have warnings, none has an error | 1 warning / *N* warnings | Warning |
| At least one tile has an error | 1 error / *N* errors, followed by "and 1 warning" / "and *N* warnings" when there are warnings too | Error |
| Not current (below) | Not checked since 3:42 PM, then a **Refresh System health** button | Not current |

- **What it counts.** *N* counts tiles, not problems: "2 errors and 1
  warning" means two tiles show Error and one shows Warning, which is what
  the eye sees below. A tile counts once, under its worst state.
- **Not current.** The poll stops for good after an hour of watching
  (`GIVE_UP_MS` in `live-status-v1.js`), and today a failed check only
  says "retrying". Either way the last answer could still read All working
  when it is no longer true. So the line changes to **Not current** when
  the poll gives up, and after three checks in a row have failed, with the
  browser-local time of the last successful check. It takes the Not
  current look, and its **Refresh System health** button reloads the
  page's status in place and starts the poll again. Every tile turns Not
  current with it, as [tiles while not current](#tiles-while-not-current)
  defines ([decision 12](#administrator-decisions-2026-10-13)). The line
  and the tiles return to their counted states on the next successful
  check. Slice 2a gives `live-status-v1.js` the hook that tells the line
  and the tiles it has given up or is failing.
- **Screen readers.** The line is the page's first `h2`, so heading
  navigation reaches it, and its icon is hidden from assistive technology.
  The page's existing hidden status announcer speaks one sentence when the
  line's text, or the set of tiles it counts, changes on a poll, and never
  for an unchanged poll or on page load (when the heading is read in
  order): "System health: 1 error (Backups) and 2 warnings (Mail,
  ParishSoft refresh)." or "System health: all working."; the Not current
  sentence is in [tiles while not current](#tiles-while-not-current). It
  replaces today's announcement of the number of problems.
- **Fixed height.** The line reserves one fixed height (3 rem, room for a
  44-pixel button) at every width, and its text is sized so the longest
  form fits on one line at 320 pixels. A poll never changes its height, so
  it never moves the tiles below.
- **Tiles read on page load.** Two tiles (Families the form cannot open and
  Pages and emails to re-save) are read only when the page opens, never by
  the poll. The page passes their states to the status fragment as
  `?loaded=`, for example `?loaded=resave:error`. It is the only query key
  the fragment accepts: any other key is still refused with 400, as every
  key is today, and the page itself still accepts none. Within it, values
  outside the closed set of these two tiles and their two non-Working
  states are ignored. The fragment counts the named states into the line.
  It changes nothing and records nothing, so a forged value can only
  mislabel the requester's own page.

#### Health tiles

Each area is one tile, in this fixed order. A poll never adds, removes or
reorders tiles: an area with nothing to report still has its tile, showing
Working with a summary that says so ("No current campaign").

- **Shape.** A tile is two parts, so the [tile grid](#tile-grid) can
  place them apart: a `section` holding a visually hidden `h2` with the
  area's name, so heading navigation reaches each tile, and the summary
  row; then, directly after it, the tile's details, a region named "*Area*
  details". The summary row is a disclosure button (`aria-expanded`,
  `aria-controls` naming the details) rather than a native `details`
  element, because a native disclosure's content cannot be laid out apart
  from its `summary`. It holds the state icon, the area name, the state
  word (**Working**, **Warning**, **Error** or **Not current**), the
  one-line summary, and a visible "Show details" or "Hide details" cue,
  not only a chevron. The heading sits outside the button, because a
  heading inside a button hides the button role from some screen readers.
- **Anchors.** Each tile has a fixed anchor: `health-mail`,
  `health-source`, `health-backups`, `health-debug`, `health-source-form`,
  `health-resave` and `health-version`.
- **Summary row height.** Two lines at every width (the name and state,
  then the summary), so every summary row in the [tile grid](#tile-grid)
  has the same fixed height. A summary longer than its line is cut with
  an ellipsis by CSS only, so the row's accessible text stays whole and
  the full sentence is in the details. Summaries come from a closed set of
  short sentences, so the cut is rare.
- **Look.** Each tile takes its state's look from the
  [status indicators](../ui-conventions/spec.md#status-indicators)
  convention, so an Error tile stands out more than a Warning tile.
- **Details, in a fixed order.** Opened, a tile shows its current problems'
  sentences, then its action regions, each in its one fixed place given in
  the table below, then the area's panel content and links. Explanations
  move to "About this page" or to toggletips, as
  [page help](../ui-conventions/spec.md#page-help) requires.
- **Summary when not Working.** The summary is the words of the tile's
  most severe problem (the first in registry order when several tie), plus
  "and 1 more" or "and *N* more" when the tile has others.

| Tile | Error when | Warning when | Working summary (examples) | Show details reveals | Action regions, in their fixed order after the problems |
| --- | --- | --- | --- | --- | --- |
| **Mail** | a mail consumer is halted (`sender_halted`); a mail consumer is not running, or mail dispatch has not reported (the `mail-dispatch` part of `not_running` and `not_reported`); the email provider is unavailable (`mail_provider_unavailable`); an address follow-up's permanent refusal could not be set aside (`refusal_unrecorded`, #1201) | a mail consumer is paused after an outage (`sender_outage`) | the first that holds, in the order [why sends are waiting](#why-sends-are-waiting) lists | the [why sends are waiting](#why-sends-are-waiting) panel (every reason, the 24-hour count, the resume time, the Family email progress link) and the [mail sender panel](#mail-sender-panel) (each consumer's line and any open incident) | **Clear the halt** ([ADM-13 PR 4](#clear-a-halted-mail-sender), not built). The panel content keeps its links: the deployment runbook under a not-running line, the mail-provider outage runbook beside an outage pause, Pause and resume mail beside the pause line, and Outgoing mail: waiting to retry beside the retry line. |
| **ParishSoft refresh** | a refused large change (`source_destructive_change`, whose problem block holds the refused counts table); a different organization (`source_tenant_mismatch`); while the campaign has Reminders still scheduled, an unknown No-reminders WorkGroup (`workgroup_missing`, new in the shared list, the #861 concern) | the last full refresh failed (`source_failed`); ParishSoft could not be reached (`source_failing`); the data is out of date (`source_late`); old copies are not being removed (`source_retention_failing`); an unknown No-reminders WorkGroup while no Reminder is scheduled | the first that holds, in the order the [ParishSoft refresh panel](#parishsoft-refresh-panel) lists | the [ParishSoft refresh panel](#parishsoft-refresh-panel): refresh times, and held and skipped refreshes | **Accept this change once** (its tick box, preview and the example Families of #1213), directly after the refusal's counts table, as the Administrator asked on #1210. The problem sentences keep their links (the ParishSoft outage runbook, Check the No-reminders WorkGroup setting), and Refresh from ParishSoft ends the panel content. |
| **Backups** | a backup is overdue (`backup_rpo_breach`) | an off-site copy failed (`backup_offsite_failed`); the backup key changed (`backup_key_changed`); the newest backup, started after the configured key was activated, was sealed to another key (`backup_key_mismatch`, new); a requested backup failed, did not finish or was not picked up (`backup_request_failed`, new, until a newer request replaces it; a newer finished backup also clears one that failed or did not finish, but not one never picked up, since a good nightly backup does not prove the server checks for requests); in Production, off-site copies are not set up (`offsite_missing`, new) | "Last backup 3 hours ago; off-site copy 3 hours ago"; "Backup requested at 10:42 AM is running" | the [backups panel](#backups-panel) | **Take a backup now**. Off-site backups (Google Drive) stays in the panel content beside the off-site line. |
| **Debug logging** | none | in Production, debug logging is in effect in any service (`debug_logging`; a warning because nothing stops working, [decision 11](#administrator-decisions-2026-10-13)) | "Off"; "Turned off from this page by … at …"; "On in 4 services, as expected in Testing" | the [debug logging panel](#debug-logging-panel) | **Turn off debug logging**, drawn only while some service has debug logging in effect; once the switch is set, its status line and, only in Testing, **Allow debug logging again** ([clearing the switch](#turn-off-debug-logging)). |
| **Families the form cannot open** | none | at least one Family cannot open the form; the check could not run | "Every Family can open the form"; "No current campaign" | the sentence and the link of [Families the form cannot open](#system-health-page) | none; the list's link is in the sentence |
| **Pages and emails to re-save** | an invitation or reminder cannot be sent until it is fixed (the list's `blocked` items) | other items are marked "Re-save recommended"; the check could not run | "Nothing to re-save"; "No current campaign" | the sentence and the linked list of [pages and emails to re-save](#system-health-page) | none; each item links its editor |
| **Version and services** | a core service other than mail dispatch is not running or has not reported (the rest of `not_running` and `not_reported`); the services run different versions (`versions_differ`); the database does not match (`schema_mismatch`) | an installer that reports is not running (the installers' part of `not_running`) | "Version 1.12.0, Production, all services running" | the [version panel](#version-panel), then the closed Technical details disclosure (its values as of page load) and the links to System logs and Background work | none; the deployment runbook's upgrade link is in the versions sentence |

Each action region is drawn when the page loads, whatever the tile's
state, and never removed: when its action does not apply it stays greyed
with its reason ("Debug logging is off in every service"), as
[unavailable buttons](#shared-rules-for-health-actions) already do. Pull
requests 1206 and 1210 draw their regions only while their problem is
current; slice 2b draws them always. The exception is
[Accept this change once](#accept-a-large-parishsoft-change-once), which
answers the refused change and is not a standing control: it is drawn
only while the refusal shown can be accepted or was accepted, or while a
step's result is (Administrator, pk-local batch 24a and 2026-10-15).
The page has no plain "Refresh System health" link at its end
(Administrator, pk-local batch 24a): it refreshes itself, and the line's
[Not current](#overall-status-line) button checks again when it stops.

#### Health problem registry

One registry in `parishkit.stewardship.system_health` describes every
problem kind: its home tile, its severity (error or warning), its summary
words, its sentence (one shared template, as today), its action, and the
CRITICAL operational events it accounts for. The table above is its
content. `find_problems` returns each problem with its severity and tile,
in registry order, so the page, Home and the `system health` command
cannot disagree.

- **One home tile.** A problem appears in exactly one tile, except a
  grouped process problem (#686), which is split by subject for the page
  only: its `mail-dispatch` subjects go to Mail, the rest to Version and
  services, each part with the same sentence naming its own subjects. Home
  and the announcement keep the single grouped sentence.
- **The command.** The `system health` document gains `severity` and
  `tiles` for each problem. `tiles` lists the tiles that show it, in page
  order: one tile for most problems, and both tiles for a grouped process
  problem whose subjects the page splits. The document still lists such a
  problem once, as Home does.
- **New kinds.** `workgroup_missing` (an error while Reminders are
  scheduled, otherwise a warning), and the warnings
  `backup_key_mismatch`, `backup_request_failed` and `offsite_missing`
  (Production only), join the shared list, so Home and the page agree on
  them.
  - Their facts are columns of `HEALTH_SQL`, the statement Home already
    runs: the newest backup request, the key the newest backup was sealed
    to and when it started, when the configured key was activated (the
    newest `backup_key_replaced` security event, which every activation that sets
    a different key records at its own time), the current snapshot's
    No-reminders WorkGroup evidence, and whether the current campaign has a
    Reminder still to come. Whether off-site copies are set up, and the
    campaign's WorkGroup name, come from the applied configuration and the
    campaign settings Home has already loaded. Home's query count therefore
    stays the same whichever problems are open, as its query-budget test
    holds. This replaces the earlier plan to read the snapshot's cursor
    through the helpers in Python after that statement, which would have
    cost Home queries whenever a WorkGroup name is set; the evidence is
    still checked by the rule `current_evidence` applies, so the page,
    Home and the Reminder planner agree.
  - **Reminders are scheduled** while the current campaign's applied
    schedule has a Reminder whose time is still ahead on the campaign
    clock. Then `workgroup_missing` is an error, and its sentence adds
    that Reminders are still scheduled; otherwise it is a warning. A
    Reminder whose time has passed but which is still being sent (held,
    paused or spread over a large send) counts as not scheduled.
  - `backup_key_mismatch` raises a `ConfigError` when the configured key
    cannot be read. The System health page keeps its 503 for that, as
    before; only Home falls back, showing "The backup key could not be
    checked" as a warning line (`backup_key_unchecked`, Home's alone)
    instead of failing. That line links no tile, since the page it would
    open cannot be shown.
  - The `system health` document gains no member for them: the unknown
    WorkGroup's name is never part of it, and a failed request's status is
    the document's `backup_request`.
  - **What waiting cannot clear.** `offsite_missing` needs an
    Administrator, and `backup_request_failed` the server operator, a
    newer request or (when it failed or did not finish) the next nightly
    backup, hours away, so `system health --watch` stops once only they
    remain
    (`WAIT_CANNOT_CLEAR` in `system_health.py`; see the
    [automation guide](../../../guides/stewardship-admin-automation.md#watching-and-errors)).
- **Backup key, one warning per cause.** `backup_key_mismatch` holds only
  when the newest backup started after the configured key was activated
  (the time the configuration that set it was applied) and was sealed to
  another key. A backup reads its key as it starts, so it never fires in
  the day between an ordinary key change and the next backup, nor for a
  backup already running when the key changed. `backup_key_changed` (the incident raised when a
  backup used a different key than the one before it) is the expected
  result of a key change. While both hold, the tile lists only
  `backup_key_mismatch`, whose sentence covers the change, so one cause
  never gives two warnings.
- **Page-only findings.** Families the form cannot open and pages and
  emails to re-save are too costly for Home and for the poll. They stay
  outside the shared list as a recorded exception: they set their tiles'
  states and count in the overall line, but Home does not list them
  ([decision 6](#administrator-decisions-2026-10-13)).
- **Deliberate waits are not problems.** An Administrator's pause, the
  daily limit, Gmail's limit, held planning and waiting retries stay
  reasons in the Mail tile's summary and details, not warnings, as the
  [why sends are waiting](#why-sends-are-waiting) panel already defines
  ([decision 3](#administrator-decisions-2026-10-13)).

#### Opening and closing tiles

- **On page load:** Working tiles are closed. Warning and Error tiles are
  open. An address whose anchor names a tile (for example
  `/admin/system/health/#health-backups`, which Home's lines use) opens
  only that tile and leaves every other closed, whatever its state, as
  [a fragment opens only its target](../ui-conventions/spec.md#disclosures)
  requires (Administrator, pk-local batch 24c). The browser stores
  nothing, so every load applies this rule again
  ([decision 9](#administrator-decisions-2026-10-13)).
- **The Administrator decides after that.** Opening and closing is the
  summary row's disclosure button, by pointer or keyboard (Enter or
  Space). The whole summary row is that button, and a click on an open
  tile's band, outside its controls and not selecting text, closes it too
  ([whole-card clicks](../ui-conventions/spec.md#disclosures)). A link on
  the page to a tile, or a changed anchor, opens that tile and closes
  nothing. A poll never opens or closes a
  tile: a tile whose state changes keeps its open or closed state, and only
  its icon, state word and summary change, in place and at the same height.
  A tile that turns Working while open stays open.
- **How the poll reaches the tiles.** One fetch per poll, through the
  existing `data-live-mirror` mechanism. The polled region holds the
  overall line and, for each tile, `template[data-live-mirror]` copies of
  three parts: the summary row's state, icon, word and summary; the
  problems' sentences; and the panel content. Each copy goes to its
  `[data-live-mirror-target]` in the tile, which sits outside the polled
  region. The summary row's `aria-expanded`, the details' `hidden`
  attribute, and every action region, are not mirror targets, so an open tile, an open preview
  and a ticked box survive every poll. As today, a target that holds focus
  is skipped, and an unchanged copy is not applied. Action buttons follow
  the poll through their own mirror copies, as pull requests 1206 and 1210
  already do. An action's answer replaces only its own region, so once it
  is swapped in, the page checks the status at once instead of at the next
  poll, and the tile's summary and the action buttons follow the change
  without a reload
  ([in-place controls](../ui-conventions/spec.md#in-place-controls)).
- **No shift under the pointer**
  ([no layout shift](../ui-conventions/spec.md#no-layout-shift)). The
  summary row is the toggle and never moves when its tile opens or closes,
  and neither do the summary rows beside it: the details open in a band
  below the row, so only content after that row moves, as the
  [tile grid](#tile-grid) defines. A summary row has one height whatever
  its state. Any open details' problems or panel target that a poll
  changes can change its height, which would move the action regions
  after it and every row below its band. Slice 2b therefore extends the
  mirror's focus rule: every tile's problems and panel targets are also
  skipped while the pointer is over the tile grid, and the changes are
  applied on the first poll after the pointer leaves it. The overall line
  and the summary rows, whose heights are fixed, still follow every poll,
  so a change of state still shows at once. Action regions are never added
  or removed (see [health tiles](#health-tiles)).
- **The order is fixed.** Tiles are not sorted by severity, because a tile
  that jumped to the top on a poll would move every tile under the pointer;
  the overall line already says which tiles need attention.

#### Tile grid

On wide screens the tiles form a grid; on narrow screens they are one
column ([decision 7](#administrator-decisions-2026-10-13)). The
Administrator chose the grid knowing that opening a tile moves the rows
below it. The grid is built so that nothing beside the pointer moves:

- **Columns.** As many columns at least 22 rem wide as fit, at most three:
  one column on a phone, two or three on a laptop. The tiles fill the
  rows in the [fixed order](#health-tiles), left to right, so on a wide
  screen Mail, ParishSoft refresh and Backups share the first row. Only a
  change of window width changes the column count; a poll or a click
  never does.
- **Row-aligned summaries.** Every summary row has the same fixed height
  (see [health tiles](#health-tiles)), so the rows line up and a poll
  that changes a summary never changes a row's height.
- **An opened tile's details span the row below its own row.** The
  details are a full-width band placed directly after the row that holds
  the tile's summary row, never inside the tile's grid cell. Opening a
  tile therefore moves no summary row in its own row or above it; only
  the rows after the band move down, exactly as content after an opened
  disclosure moves in one column. When several tiles in one row are
  open, their bands follow that row in tile order. Opening a tile whose
  band comes first moves the other bands of that row down, which is
  again only content after the toggle. Closing a tile removes its band,
  and only the content after it moves up.
- **A band is marked as its tile's.** The band has its tile's state look
  (border, background and start bar from the
  [status indicators](../ui-conventions/spec.md#status-indicators)), and
  the opened tile's summary row joins it with a connector notch in the
  same border colour, so a reader sees which tile a band belongs to.
- **Why this layout.** A band below the row keeps the toggles of a row
  still and keeps every band full width, so tables and action regions in
  the details read as they do in one column. The rejected alternatives:
  opening the details inside the tile's cell (the cell grows, so its row
  grows and every tile in the rows below moves, and the details are
  squeezed to a column's width); a masonry layout (tiles beside an opened
  one move sideways); and one shared details area for the whole grid
  (only one tile could be open at a time, and the Administrator decides
  what is open).
- **How it is laid out.** CSS grid only: the details band is a grid item
  spanning every column, placed after its tile's summary in the page
  source, and the grid's dense placement moves the later summary rows of
  the same row up beside it. No script computes rows.
- **Reading and focus order.** In the page source each tile's details
  follow its own summary row, so a screen reader or the Tab key goes from
  a tile's toggle straight into its details, then to the next tile. On a
  wide screen the next tile's summary row can sit above that band, on the
  toggle's own row. This is the ordinary order of a disclosure grid, and
  the [no layout shift](../ui-conventions/spec.md#no-layout-shift)
  convention records it.

#### Tiles while not current

When the [overall status line](#overall-status-line) turns Not current,
every tile turns Not current with it, until a check succeeds again
([decision 12](#administrator-decisions-2026-10-13)). The page sets this
itself, from the `live-status-v1.js` hook, because no fresh status has
arrived; nothing is fetched to draw it.

- **Look.** Each summary row takes the Not current look of the
  [status indicators](../ui-conventions/spec.md#status-indicators): the
  `unknown` icon, the state word **Not current**, and the neutral colours.
  Its summary keeps the last known words, prefixed "Last known:" and the
  last known state word, for example "Last known: Warning. Paused after
  an outage". An open band takes the Not current border and background;
  its content stays as it was, because removing or greying it would move
  everything below it and lose an open preview or a ticked box.
- **Open and closed are kept.** No tile opens or closes when the page
  turns Not current or current again; the Administrator's choice stands.
- **Sizes do not change.** The Not current words and icon are drawn in
  every summary row when the page loads and shown in place of the
  counted ones by one state on the tile grid, inside the summary row's
  fixed height. Nothing appears or disappears.
- **Actions.** Action regions stay as they were. Each action's preview
  and confirmation re-read the facts on the server and refuse a change
  that no longer applies, so a page that is not current cannot act on old
  facts.
- **Tiles read on page load.** Families the form cannot open and pages
  and emails to re-save turn Not current too, so no tile claims a current
  state that the page did not check.
- **Screen readers.** Each band's name becomes "*Area* details, not
  current", and each summary row's text reads its state word first, so a
  reader moving through the tiles hears "Not current" before the last
  known words. The status announcer speaks one sentence when the page
  turns Not current, "System health is not current; last checked at 3:42
  PM. The tiles show what was last known.", and one when it is current
  again, the counted sentence of the overall line. It speaks nothing in
  between. Focus never moves.
- **Back to current.** On the next successful check, from the poll's own
  retry or from **Refresh System health**, the tiles take their fresh
  states. Their summary rows change at once; their details follow the
  pointer hold of [opening and closing tiles](#opening-and-closing-tiles).

#### Health problems on Home and in the banner

The same problem never appears twice in ways that disagree.

- **Home's lines.** Home's System health lines (`home_problems`) are the
  registry's problems, with the page's sentence, plus the registry's icon
  and state word, and link the problem's tile anchor. Until NAV-18 merges
  Home's lists (see [System health page](#system-health-page)), a problem
  that Home also reports in its own notice (a failed or late ParishSoft
  refresh, a failed off-site copy) is shown once: as the System health line to
  viewers who may open System health, and as the other notice to everyone
  else. A line shown in a notice's place keeps that notice's links and
  actions (for example Refresh from ParishSoft, or the off-site backup
  settings), so nothing Home offers today is lost.
  - **The line.** The icon and the word (Error or Warning) come first,
    then the sentence, then "See *tile* in System health", which links the
    first tile that shows the problem (a stopped mail sender's line links
    Mail). An error line has the error notice's look, a warning line the
    warning notice's. When the sentence already links that tile ("See Mail
    sender", "See the counts", "See Version"), the line adds no second
    link, so each line links its tile exactly once.
  - **Which notices give way.** While `source_failed` is listed, the
    refresh block's failure notice becomes the plain line System health's
    tile uses: when changes appear, and "See what went wrong". While
    `source_late` is listed, the late notice is left out. Either way, the
    one-click Run a full refresh now the notice carried stays in the block,
    once. While `backup_offsite_failed` is listed and the off-site line
    reports the failure, that line is left out. The sentence keeps its
    Off-site backups (Google Drive) link, and the last copy's time is in the
    Backups tile. While `offsite_missing` is listed (Production), Home's
    invitation to set off-site copies up is left out; the sentence keeps
    its Set up off-site backups link.
- **Banner events and problems.** Where a problem kind accounts for a
  CRITICAL operational event, the registry names it. While a mapped
  problem is current, the banner's group for its event links, for
  Administrators, the problem's tile ahead of the filtered log. Once the
  problem has ended, the group reads "ended" as the
  [critical-problems banner](#navigation-and-home) already does (#633).

  | Banner event | Problem kind | Words |
  | --- | --- | --- |
  | `source_destructive_change` | `source_destructive_change` | shared: "ParishSoft refresh refused: too large a change" |
  | `source_tenant_mismatch` | `source_tenant_mismatch` | shared: "ParishSoft organization does not match" |
  | `source_invalid` | `source_failed` | shared: "ParishSoft refresh failed" |
  | `source_provider_failed` | `source_failing` | shared: "ParishSoft could not be reached" |
  | `source_credential_failed` | `source_failing` | the banner keeps "ParishSoft API key was refused", which names the cause; the tile's sentence names it too when the connection record does |
  | `mail_provider_failed` | `sender_halted` | the banner keeps "Email sending failed", because the event is also recorded for failures that halt nothing; it links the Mail tile only while a halt is current |
  | `retry_refusal_unrecorded` | `refusal_unrecorded` | the banner keeps "A refused email address could not be set aside"; the problem lasts exactly as long as the unacknowledged entry ([#1201](#system-health)), so its group links the Mail tile while it shows |

  The other CRITICAL events (`source_held`, `task_failed`, `fact_drift`,
  `due_work_lag` and `boundary_lag`) have no registry problem kind, so
  their groups keep the filtered-log link and their own labels.
  `web_unhealthy` is not mapped either: while it is current the page cannot be
  shown at all, and once the page shows, the web answers and the group
  has ended, so its log link is the useful one. Two banner labels change
  to the shared words: `source_destructive_change` (today "ParishSoft
  refresh would remove too much data") and `source_invalid` (today
  "ParishSoft data refresh failed"). On the page side, the summaries of
  `source_tenant_mismatch`, `source_failing` and
  `source_destructive_change` change to the shared words.

  The group's link reads "See *tile* in System health" and comes after
  the group's count, ahead of the System logs button. A problem counts as
  current while its group has not ended, by the banner's own #633 check.
  The banner therefore stays at its one query, and an ended group reads
  "ended" with no tile link. `mail_provider_failed` links no tile until
  ADM-13 PR 4 adds halt identities, because that one query cannot tell
  whether a halt is current. The mapping (`BANNER_PROBLEMS`) sits beside
  the registry in `system_health.py`. The table's `source_invalid` is the
  event recorded as `source_refresh_invalid`.
- **An action that resolves a problem clears its banner entries.** When an
  action's confirmation resolves a mapped problem, the same transaction
  acknowledges that event's CRITICAL entries through the banner's existing
  acknowledgement rows and its `critical_events_acknowledged` audit event,
  with the acting Administrator recorded as the one who acknowledged them.
  As with the banner's own Acknowledge, that hides them for every Admin.
  Only entries the action's preview showed are acknowledged: the preview's
  signed binding carries their ids (the banner's own `sign` list, at most
  500), or names the one record whose entries they are (Accept this change
  once, below), so nothing recorded after the preview is hidden. The in-place
  answer carries the refreshed banner, so it disappears without a reload.
  A later entry, for example when the accepted refresh is refused again,
  brings it back.
  - **Accept this change once** acknowledges the refusal's
    `source_destructive_change` entries. This is the fix the Administrator
    asked for on #1210. It finds them through the mapping: the entries of
    the events mapped to `source_destructive_change` that name the refused
    task. The worker writes them in the refusal's own transaction, so they
    are exactly the entries any preview of that refusal showed. The
    preview's binding therefore names the refusal rather than carrying
    entry ids.
  - **Clear the halt** (ADM-13 PR 4) acknowledges the halt's
    `mail_provider_failed` entries.
  - **Take a backup now** and **Turn off debug logging** acknowledge
    nothing: an overdue backup ends only when a backup finishes (the
    banner then reads "ended"), and debug logging raises no CRITICAL
    entry.
  - A problem that ends by itself leaves its entries for an Administrator
    to acknowledge ([decision 8](#administrator-decisions-2026-10-13)).

#### Dashboard at phone width and in themes

- **Phone width.** One column on narrow screens, as the
  [tile grid](#tile-grid) defines. At 320 pixels the overall line
  keeps its fixed height, each summary row is two lines and at least 44
  pixels tall, tables in the details scroll inside their own region,
  buttons follow [button labels](../ui-conventions/spec.md#button-labels),
  and the page never scrolls sideways.
- **Themes.** The dashboard uses only the
  [status indicators](../ui-conventions/spec.md#status-indicators) and
  the [shared design tokens](../ui-conventions/spec.md#shared-visual-style),
  never a fixed colour, so the Admin portal's
  [colour scheme](../ui-conventions/spec.md#colour-scheme) covers it by
  redefining the tokens ([decision 10](#administrator-decisions-2026-10-13)).
  In forced-colours mode the borders, icon shapes and state words remain,
  so the state still reads.

#### Dashboard delivery slices

Each slice is a small pull request, in this order, with its own pk-local
check by the Administrator. Each covers the all-working, warning and error
states in unit and browser tests. All of them build on main after pull
requests 1206, 1210 and 1213 have merged.

1. **Registry.** The problem registry, severity and tiles in
   `find_problems`, the new kinds and their facts, the backup key rule,
   Home's fallback when the key cannot be checked, the shared banner words
   and mappings, and `severity` and `tiles` in the `system health`
   command's document. The page and Home look as before, except that the
   new kinds appear. pk-local: Home and the page with each new kind.
2. **Frame**, in two parts:
   - **2a, the line.** The overall status line with its Not current state
     and the `live-status-v1.js` hook, the `?loaded=` key, the new
     announcement, and the
     [status indicators](../ui-conventions/spec.md#status-indicators): the
     reworked severity icons with the new `ok` and `unknown` shapes, the
     state classes and the contrast test. pk-local: all working, a
     warning, an error, and a poll left to give up.
   - **2b, the tiles.** The seven tiles holding today's panel content
     unchanged, the [tile grid](#tile-grid) and its bands, the load rule,
     the mirror targets and the pointer hold,
     [tiles while not current](#tiles-while-not-current), and the controls
     of pull requests 1206, 1210 and 1213 moved into their fixed places
     and always drawn (Accept this change once only while a refusal can be
     or was accepted, since slice 4). pk-local: opening and closing tiles
     in one row of the grid, a poll that changes state while a tile is
     open, an open preview across a poll, a poll left to give up with tiles
     open, and phone width.
3. **Mail tile.** Its summaries, and its explanations moved to "About this
   page" and toggletips. pk-local: a send, a pause, the daily limit and a
   stopped consumer in Testing.
4. **ParishSoft refresh tile.** Its summaries, and the refusal's block with
   Accept this change once and its examples. pk-local: a refused change
   accepted from the tile.
5. **Backups and Debug logging tiles.** pk-local: Take a backup now and
   Turn off debug logging from their tiles.
6. **Page-load and services tiles.** Families the form cannot open, pages
   and emails to re-save, and version and services with Technical details.
   pk-local: each tile's warning and error.
7. **Home and the banner.** Home's icons, state words, tile links and
   single display of a shared problem with the notice's links kept; the
   banner's tile links; and the acknowledgement by actions. If the fix to
   pull request 1210 already acknowledges the banner, this slice moves it
   onto the registry's mapping. pk-local: accept a refused change and
   watch the banner clear on Home and System health.

#### Administrator decisions (2026-10-13)

The Administrator answered the dashboard's twelve questions on
[#1214](https://github.com/epiphany40223/parishkit/issues/1214) on
2026-10-13. Decisions 7, 11 and 12 changed the proposed default; the text
above follows every decision.

1. **One Mail tile**, not separate "Why sends are waiting" and "Mail
   sender" tiles: a halted or stopped sender is the reason sends wait, so
   two tiles would show one problem twice.
2. **The overall line counts tiles**, not problems.
3. **Deliberate waits are Working**, with the reason named in the Mail
   tile's summary; there is no "information" state.
4. **The severity map stands as in the table**: a failed or late
   ParishSoft refresh is a warning (the system keeps the data it has), and
   a stopped installer is a warning.
5. **In Production, no off-site copy set up at all is a warning**
   (`offsite_missing`); in Testing it is only the Backups summary.
6. **Home does not list the page-only findings** (Families the form
   cannot open, pages and emails to re-save); they cost too much for Home,
   and Go-live readiness already lists the re-save items.
7. **Changed: a grid of tiles on wide screens**, one column on narrow
   screens, chosen knowing that opening a tile moves the rows below it.
   The [tile grid](#tile-grid) keeps that movement to content after the
   toggle's row.
8. **A problem that ends by itself does not clear its banner entries**;
   the banner reads "ended" until an Administrator acknowledges it.
9. **The browser does not remember open tiles**; each load opens exactly
   the tiles that need attention.
10. **A dark theme is not part of this work**; it has its own issue,
    [#1264](https://github.com/epiphany40223/parishkit/issues/1264). The
    dashboard uses tokens only, so a portal-wide theme covers it.
11. **Changed: debug logging in Production is a warning**, not an error,
    because nothing stops working. The always-shown
    [debug logging banner](#navigation-and-home) keeps its error style.
12. **Changed: once polling stops or keeps failing, the line reads Not
    current** ("Not checked since 3:42 PM") with **Refresh System
    health**, and every tile turns Not current until a check succeeds, as
    [tiles while not current](#tiles-while-not-current) defines.

### Health panels

Each panel below is what its tile's details show once opened (see
[health tiles](#health-tiles)); "Why sends are waiting" and the mail sender
panel together form the Mail tile.

#### Why sends are waiting

This panel answers "why aren't the emails going out?" (ADM-19). It lists
every reason Family email is waiting right now, with a count, when the
reason ends (if known), and a link to act on it:

- **Mail is paused by an Administrator** ([live delivery
  pause](#live-delivery-pause)): who paused it, when, the reason they gave,
  and a link to Pause and resume mail.
- **Waiting for the daily limit:** the recipients sent in the last 24 hours,
  the limit, and, while the count is at the limit, when sending resumes.
  For example: "Emails went to 1,612 recipients in the last 24 hours.
  Family invitations, reminders and Family tests stop at 1,600. Sending
  resumes at about 3:10 PM." The limit shown is the share Family
  invitations, reminders and Family tests may use (the daily limit less
  the sends kept for receipts, reports and alerts). The resume time is
  when the oldest counted sends leaving the 24-hour window first bring the
  count below that limit, shown as a browser-local clock time (it is
  always within 24 hours), with "tomorrow" added when it falls on the
  browser's next calendar day. It is "about" because a message with
  several recipients may wait a little longer, sends recorded later move
  it later, and a message held at the limit is deferred rather than woken:
  it is tried again every 15 minutes plus up to 5 minutes of jitter, so
  the first send can come up to about 20 minutes after the time
  shown. It needs each counted send's time and recipient count, which the
  web login cannot read, so it comes from a second argument-free
  `SECURITY DEFINER` function, `stewardship_family_daily_resume_v1()`
  (frozen migration 0076), with the limit fixed in its body, that returns
  one instant (#530). The count is the same count the mail
  sender compares with the limit, read by a definer function
  (see [bulk Family send](../background-processing/spec.md#bulk-family-send)
  and the
  [Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers)).
  It is shown even when sending is not held, so the Administrator can see
  the limit coming. The web login reads it through the `SECURITY DEFINER`
  function `stewardship_family_daily_sends_v1()` (frozen migration 0012,
  with #382's `(previous_state, created_at)` index on the outbox events),
  which returns one number and takes no argument: the count is made of the
  routed recipient lists, which the web login never reads (the
  [Mail message page](#mail-message-page)'s own recipients are the one
  narrow exception). This is the
  24-hour count that #382 (item M3) asks to show. Outgoing mail's
  [sending limit note](#sending-limit-note) uses the same words for each
  reason.
- **Held at Gmail's sending limit**, until the time shown.
- **Paused after a mail outage**, until the time shown, linking the
  [mail-provider outage runbook](../../../guides/stewardship-launch-runbooks.md#mail-provider-outage).
- **The mail sender is halted** or **not running:** see the
  [mail sender panel](#mail-sender-panel).
- **Planning is held:** the same reasons, in the same words, for which
  [Family email progress](#family-email-progress) shows **Held**.
- **Waiting to retry:** how many messages wait to retry after a temporary
  refusal, and when the next retry is due, linking Outgoing mail filtered to
  Waiting to retry.

When nothing is waiting, the panel says "Nothing is holding Family email
back". During a send it links Family email progress, which counts the
send's progress; this panel does not count it again. "About this page"
explains the daily limit (including the 200 sends kept for receipts,
reports and alerts), Gmail's own limit, and the outage pause, under one
**Mail** entry named for the tile. Each reason's line keeps its data and
links visible; why it happens (the daily limit, Gmail's limit, the outage
pause, held planning) is in a toggletip beside it, as
[page help](../ui-conventions/spec.md#page-help) asks. A reason that both
mail senders share is listed once ("Waiting for the daily limit (Both
mail senders)").

While the Mail tile is Working, its summary names the first of these that
holds, because a deliberate wait says more than "Sending" does and an
Administrator's pause holds everything else back
([decision 3](#administrator-decisions-2026-10-13)):

1. "Paused by an Administrator since 9:12 AM";
2. "Waiting for the daily limit until about 3:10 PM", while a running mail
   sender is held at it;
3. "Held at Gmail's limit until 4:00 PM", likewise, with the earliest
   time when both mail senders are held until different times;
4. "Planning is held";
5. "3 emails waiting to retry";
6. "Sending", during a Family email send;
7. "Nothing due".

Each time is a browser-local clock time, with "tomorrow" or the full date
when it falls on another day. A limit held only by a sender that is not
running is not named: that sender's stopped state is the tile's error.

#### Mail sender panel

This panel shows the state of each of the `mail-dispatch` service's mail
consumers (two by default; see the
[Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers)),
from its [service status record](#service-status-records):

- **Running:** sending, or ready to send with nothing due.
- **Paused after an outage**, until the time shown.
- **Held at Gmail's limit**, until the time shown.
- **Waiting for the daily limit.**
- **Halted:** a fault that waiting cannot fix stopped all sending. The panel
  shows when the halt began and its kind, taken from the stored outcome of
  the delivery attempt that caused it (see
  [clear a halted mail sender](#clear-a-halted-mail-sender)), and offers
  **Clear the halt**.
- **Not running:** no consumer has reported for more than three minutes. The
  panel shows when each one last reported and that only the server operator
  can start the service, linking the
  [deployment runbook](../../../guides/stewardship-deployment-runbook.md).

When the two consumers differ, each has its own line; while they show
the same state (and, for a halt, an outage pause or Gmail's limit, the same
time), one line names both ("Both mail senders: Running."). A stopped
consumer always has its own line, with when it last reported. An open
`mail_provider_unavailable` or `mail_provider_failed` incident is named in
words, with when it opened. "About this page" explains each state and what
the system does about it.

#### ParishSoft refresh panel

This panel shows the ParishSoft data the system is using and why a refresh
might be held back:

- When the last full refresh finished, whether a refresh is running now,
  and a link to
  [Refresh from ParishSoft](#manual-parishsoft-refresh). The panel states
  "ParishSoft data as of" and "Connection" as two separate lines, with the
  wording Home and the refresh page use, defined in
  [ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection).
- **Refreshes held during a send:** while a bulk send is in progress the
  scheduler holds daytime full refreshes (and any stored quick updates),
  which run as a
  catch-up once the send ends, as
  [deltas wait for a bulk Family send](../background-processing/spec.md#deltas-wait-for-a-bulk-family-send)
  describes. The panel says so and gives the latest time at which they start
  again. Refreshes skipped around a scheduled Family email, which do not run
  later, are listed separately with their window.
- **A refused large change** (`source_destructive_change`, under the
  [full cycle](../background-processing/spec.md#full-cycle) rules): every
  recorded count with its before and after values (for example "Families
  with an email address: 1,084 before, 612 after"), marking each one that
  fell too far, and **Accept this change once** (see
  [accept a large ParishSoft change once](#accept-a-large-parishsoft-change-once)).
  "Before" is the count's highest value over the past week's full refreshes
  (and the current data, for the eligibility counts), not only the last
  full refresh. In the example, 1,084 may come from a full refresh three days
  ago that the last two refreshes each fell below by less than the limit. A
  toggletip beside the table's caption says so, and that the system keeps
  the data it had. The counts come from the refusal's durable record, not
  the worker's log. On the dashboard the table is part of the refusal's
  own problem item, after its sentence, and that item ends the tile's
  problems; on the page the sentence drops its "See the counts" link,
  which Home keeps.
- **A refused organization** (`source_tenant_mismatch`): no button. The
  tile's problem says to stop and check with the parish before changing
  anything, and the panel links the
  [ParishSoft outage runbook](../../../guides/stewardship-launch-runbooks.md#parishsoft-outage)
  it names.
- **Old copies not being removed** (`source_retention_failing`): the
  tile's problem says so, and the panel links the same runbook section.
  It opens when the last three refreshes skipped their
  retention, or when the last 12 all ended it at a lock, statement or time
  limit, removed nothing, and old work was left undone: a compacted copy
  not reclaimed, or a report generation superseded by one created more than
  two days earlier and still not removed
  ([#833](https://github.com/epiphany40223/parishkit/issues/833)). It
  resolves on the first refresh that is neither.
- **An unknown No-reminders WorkGroup** (#861): the newest refresh found no
  ParishSoft Family WorkGroup with the campaign's
  [No-reminders WorkGroup](#parish-and-integration-configuration) name, so
  only the Families skipped before stay skipped until a refresh finds it
  ([#1305](https://github.com/epiphany40223/parishkit/issues/1305)). The
  notice names the setting and links its page; the refresh itself logs a
  WARNING. It is the shared `workgroup_missing` problem, an error while
  [Reminders are scheduled](#health-problem-registry) and otherwise a
  warning, listed after the tile's other problems and before a refused
  change. It clears when a refresh finds the name or the setting is
  cleared.

The panel repeats none of the tile's problems: in the tile, the shared
refresh status leaves out its late notice, which `source_late` already
states, and, while `source_failed` is listed, its failed notice, keeping a
failure's "See what went wrong" link and when changes appear. While
ParishSoft is not answering, `source_failing` is listed instead of
`source_failed`, so the failed notice stays in full. Home and the refresh pages keep those
notices.

While the ParishSoft refresh tile is Working, its summary names the first
of these that holds:

1. "Change accepted once; waiting for the next full refresh", while the
   refusal shown has a live acceptance (it is listed, not counted);
2. "Catching up after the email send", while a held refresh runs or is
   about to because its send ended or its resume time passed (no time is
   named, since it may already be past);
3. "Refreshes held during the email send until about 4:30 PM";
4. "Full refresh running";
5. "Data as of 6:02 AM", in the words of
   [ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection),
   followed by the connection line: "; connection working", "; connection
   not checked since 2:00 AM", "; connection failing since 9:15 AM" or
   "; connection not checked yet";
6. "No ParishSoft data yet".

Each time is a browser-local clock time, with the full date when it falls
on another day.

"About this page" explains what a full refresh reads, why daytime full
refreshes pause during a send, and that every later refresh is refused
until a drop is fixed in ParishSoft or accepted. It does not mention quick
updates (#1122).

#### Backups panel

This panel answers "are backups happening, and is there a copy off the
server?" (ADM-35):

- **Last backup:** when the newest backup finished, how long ago, its size,
  the application version it was taken with, and whether it was sealed to
  the backup key now configured (see **Backup encryption key** under
  [parish and integration configuration](#parish-and-integration-configuration)),
  from the `stewardship_backup_run` record.
- **Off-site copy:** when the newest off-site copy to Google Drive finished
  and whether the newest attempt failed, from the
  `stewardship_backup_upload` record that Home already shows. When off-site
  copies are not set up, the setup page is linked, as the list below says.
- **Problems:** the open `backup_rpo_breach`, `backup_offsite_failed` and
  `backup_key_changed` incidents are the tile's problems, as below.
- **Requested backups:** the newest backup requested from this page, with
  its state: waiting for the server, held for a Family email send, running,
  finished, failed (its category under Technical details), did not finish,
  not picked up (it waited 30 minutes), or cancelled by a restore. The page
  applies the lapse rules before the next poll records them. See
  [take a backup now](#take-a-backup-now). While a request waits, is held
  or runs, when it was requested is the greyed button's reason, so the
  panel line names only its state (and when it started running). Why a
  held request can still show for a few minutes after the send ends (the
  server checks again every five minutes) is in a toggletip beside it. A
  request that ended without a backup is the `backup_request_failed`
  problem, below, until a newer request replaces it. A newer finished
  backup also clears one that failed or did not finish, but not one never
  picked up, since a good nightly backup does not prove the server checks
  for requests.

The panel repeats none of the tile's problems. The open
`backup_rpo_breach`, `backup_offsite_failed` and `backup_key_changed`
incidents are the tile's problems, in their own sentences. The shared
warnings of the [problem registry](#health-problem-registry) follow them,
in this order, each with its sentence and link:

- a newest backup, started after the configured key was activated,
  sealed to another key (`backup_key_mismatch`), linking the **Backup
  encryption key** settings; while it holds, `backup_key_changed` is not
  listed, since one cause gives one warning;
- a requested backup that failed or did not finish (linking the backup
  runbook's "Checking" section) or was not picked up (linking the nightly
  backup);
- in Production, off-site copies that are not set up, linking their setup
  page.

The panel then keeps the facts: the last backup's time, size and version,
"sealed to the backup encryption key now configured" when it is, and the
off-site line with **Off-site backups (Google Drive)**. A requested backup's
progress is not repeated there: it is the line under
[Take a backup now](#take-a-backup-now)'s button. A failed newest copy without an open incident is a
plain notice there. In Testing, off-site copies that are not set up are a
panel line with the setup link, not a problem
([decision 5](#administrator-decisions-2026-10-13)).

While the Backups tile is Working, its summary names the first of these
that holds:

1. "Backup requested at 10:42 AM is running";
2. "Backup requested at 10:42 AM is held during the email send";
3. "Backup requested at 10:42 AM is waiting";
4. "Last backup 3 hours ago", followed by the off-site copy: "; off-site
   copy 3 hours ago", "; no off-site copy yet" (set up, nothing copied),
   or "; off-site copies not set up" (which only Testing reaches while
   Working);
5. "No backup recorded yet".

A request's time is a browser-local clock time, with the full date when it
falls on another day; how long ago is counted in the browser.

The web login already reads the run record's ID, completion time and
recipient fingerprint. ADM-13 adds read access to its `database_bytes`,
`files_bytes` and `application_version` columns only; it still reads no
digest or path.

"About this page" explains what the panel cannot see: a scheduled run that
fails leaves no record (see the
[backup guide](../../../guides/stewardship-backup.md#the-record-is-the-evidence)),
so for scheduled runs the panel can only say that none has finished since a
given time; a copy made outside the application (for example with `rsync`)
leaves no record; and the schedule itself is a cron entry on the server,
which the application cannot read, so there is no "next backup" time. It
links the runbook's
[nightly backup](../../../guides/stewardship-backup-runbook.md#the-nightly-backup)
schedule.

#### Debug logging panel

This panel shows, in Testing, whether debug logging is in effect in each
online application service (web, worker, scheduler, mail-dispatch and the
installers; in Production the `debug_logging` problem names them, as
below), from each one's
[service status record](#service-status-records), and whether the
**debug-off switch** is set (see
[turn off debug logging](#turn-off-debug-logging)). A service's debug
logging is in effect when it was started with `PARISHKIT_DEBUG_LOGGING=1`
and the debug-off switch is not set.
Each such start also logs one WARNING `debug_logging_enabled` line next to
`startup_validated` in that service's log, once per process (each web worker
logs it), so someone reading the logs sees it too (#546). It is not an
operational incident, since this panel already reports it. A service reads
the debug-off switch when it connects to the database, before this line, so
a set switch keeps the line out.

- **In Production, any service with debug logging in effect is a problem**
  in the Debug logging tile, whose band offers **Turn off debug logging**.
  The problem is a warning in the [health tiles](#health-tiles); the
  always-shown [debug logging banner](#navigation-and-home) keeps its
  error style, because it must stand out on every page.
- In Testing it is shown as information, not a problem, because the
  pre-launch deployment tool turns it on deliberately
  ([deployment runbook](../../../guides/stewardship-deployment-runbook.md#pre-launch-fast-deploys)).
  The button is offered in Testing too; the note that says so is left out
  while the switch is set and the button is not drawn.
- While the switch is set, the panel says that the setting on the server
  still says "on"; who set it and when is the region's status line.

The panel repeats nothing the tile already says. In Production the
`debug_logging` problem names every service with debug logging in effect
and what to do, so only Testing lists the services in the panel. That
debug logging is off in every service is the region's status line, and
how the switch is cleared is the toggletip beside the region's
explanation.

While the Debug logging tile is Working, its summary names the first of
these that holds:

1. "Turned off from this page by admin@example.org at 9:12 AM", while the
   switch is set (without the address when it cannot be read);
2. "On in 4 services, as expected in Testing";
3. "Off".

The time is a browser-local clock time, with the full date when it falls
on another day.

"About this page" explains why debug logging must be off in Production
(debug logs can hold personal data, as the
[debug logging banner](#navigation-and-home) says), and that only the
server operator can remove the setting for good (see
[what stays on the host](#what-stays-on-the-host)).

#### Version panel

This panel answers "what is running?" (ADM-40):

- the application version each online service reports, and when each one
  last started. When all agree, the panel shows the version once ("All
  services run version 1.4.2"). When they differ, it shows a problem ("The
  worker runs 1.4.1 and the other services run 1.4.2; an upgrade may be
  unfinished") linking the
  [deployment runbook's upgrade section](../../../guides/stewardship-deployment-runbook.md#upgrade);
- whether the database's schema matches what the running version expects;
- the deployment mode (Testing or Production).

No host name, address, container identifier or image registry path is shown.

The panel repeats none of the tile's problems. Different versions
(`versions_differ`) and a database that does not match (`schema_mismatch`)
are the tile's problems, in their own sentences, so the panel keeps only
the facts: "All services run version 1.4.2" while every service runs one
version, each service's line, "The database matches the version that is
running" while it does, and the mode. Inside the tile, the versions
sentence draws no "See Version" link, which would point at the tile it is
in; Home keeps it. The `mail_provider_unavailable` sentence likewise drops
its "See Mail sender" link inside the Mail tile
([#1385](https://github.com/epiphany40223/parishkit/issues/1385)).

While the Version and services tile is Working, its summary names the
first of these that holds:

1. "Version 1.12.0, Production, all services running";
2. "No service has reported its version".

### Health actions

#### Shared rules for health actions

These rules apply to all four actions. Each action's own subsection adds its
checks.

- **Who:** an enabled Administrator with the `CONFIGURE` capability, checked
  in the service function and again in the action's SQL guard.
- **Fresh sign-in:** each action needs a Google sign-in within the last five
  minutes, as pausing and resuming mail do (see
  [live delivery pause](#live-delivery-pause)). A stale sign-in gets the
  existing "Confirm with Google" step-up, which returns to this page with
  the action's panel still open. The action records its fresh sign-in
  instant, and its SQL guard checks it as
  `stewardship_delivery_control_guard_v1` does. **Take a backup now**
  needs it too, so all four follow one rule
  ([decision 5](#system-health-decisions)).
- **Preview, then confirm:** each action first shows a preview built by the
  server, then a confirm button. Where the preview depends on a state that
  can change (a halt, a refusal), the confirmation carries the signed
  preview binding that other Admin previews use, and the server refuses it
  when that state has changed ("This changed since you looked; review it
  again"). A repeated click returns the first result. **Take a backup
  now**, **Turn off debug logging** and **Allow debug logging again**
  confirm in the shared
  [review dialog](../ui-conventions/spec.md#review-dialog) as a
  confirmation (#1356): their previews would only repeat what the page
  shows (the last backup in the Backups details, the services with debug
  logging on and when the switch was set in its panel, the actions' rules
  in "About this page"), so the
  dialog says in one sentence what Apply does and adds only what the server
  worked out. Their refusals, the step-up included, show in the dialog, and
  Apply's answer redraws the action's region, its button greyed with the
  reason. The server's checks, the preview's key and the
  audit are unchanged.
- **Unavailable buttons are greyed, never hidden.** A button that cannot be
  used right now stays in place, unavailable, with its reason shown and
  announced the same way as an unavailable menu entry's (see
  [stable menu shape](#stable-menu-shape)). Each action lists its reasons.
  Greying only helps the reader: every check also runs on the server, in
  the service function and inside the action's transaction. Accept this
  change once's whole section is drawn only while the refusal shown can
  be accepted or was accepted
  ([where it appears](#accept-a-large-parishsoft-change-once)); while it
  is drawn, its button follows this rule. Turn off debug logging's region
  draws each of its two buttons only while it can act, and a status line
  otherwise ([where it appears](#turn-off-debug-logging); Administrator,
  2026-10-15).
- **Audit:** each action records one audit event, with the Administrator as
  actor and its request, signal, acceptance or switch row as subject, in the
  same transaction as the change. Its context holds only `outcome`, and each
  event type gets a `log_descriptions` sentence.
- **Command line:** each action and the page's read have a command that
  calls the same service function, listed once in the automation spec's
  [action inventory](../admin-automation/spec.md#operations). They follow
  ADM-11's
  [rules for new Admin actions](../admin-automation/spec.md#rules-for-new-admin-actions)
  and
  [fresh-gated actions](../admin-automation/spec.md#fresh-gated-actions-from-the-command-line):
  automation may run all four.
- **Schema order with ADM-11:** ADM-11 PR 5, which added
  `stewardship_automation_fresh_v1`, has landed, and no ADM-13 action was
  built before it, so its migration amended no ADM-13 guard. Each action's SQL
  guard accepts `stewardship_automation_fresh_v1` from the start, and its
  route and command land with it.
- **Notifications:** the page, the logs and the audit event tell other
  Administrators. No action sends email or Slack notices
  ([decision 7](#system-health-decisions)); automation events also get the
  dashboard notice ADM-11 gives every automation event.

#### Take a backup now

**What it does.** It asks the server to run one backup soon, of the same
kind as the scheduled backups: the same sealed files, record, off-site copy
and [retention](../../../guides/stewardship-backup-runbook.md#retention).

**How it reaches the server.** The web process cannot start a backup: it
has no Docker access, does not read the credentials tree and does not hold
the backup login. Instead:

1. Confirming records one backup request (a new
   `stewardship_backup_request` row: who, when, state `waiting`, and the
   fresh sign-in instant) and returns at once.
2. A new host cron entry runs the `backup-worker` profile in **request mode**
   every five minutes. Request mode first reads the database for a request
   that is waiting and not expired, before it takes any lock. When there is
   none, it exits at once, prints nothing and logs nothing, so an idle poll
   leaves no trace and never touches the startup interlock that migration,
   upgrade, restore and recovery hold. When the database cannot be read
   (for example during offline work), it also exits quietly; the page's
   "not picked up" message below is what shows a poll that never runs.
3. When a request is waiting, request mode first takes the startup
   interlock shared, as every backup run does; if offline work holds it, it
   exits quietly and leaves the request waiting. It then tries once,
   without waiting, to take the backup lock that every backup run now
   takes. The lock is a PostgreSQL session advisory lock, held while a run
   makes its backup set (the off-site upload that follows is not covered);
   any database login could take its key and stall backups, which the
   scheduled run's timeout and the overdue-backup alert would then show (a
   lock file would need a new mount on every host). If another backup holds
   it, request mode exits quietly the same way. Otherwise it marks the
   request `running` and runs the ordinary backup, then marks the request
   `finished`, linked to its backup record, or `failed` with the
   `failure_kind` category the process log records. The backup login gains
   read and update access to request rows and nothing else.
4. The scheduled run also takes the backup lock, but waits for it, for at
   most 30 minutes. If it gives up, it writes an operational log entry with
   the limit's name, the limit and the time waited, as every time limit
   does (see [observability](../operations/spec.md#observability-and-health)),
   and exits `2`. A scheduled run that starts while a request is waiting
   takes the request and completes it, even during a bulk send.
5. **Held during a bulk send** ([decision 2](#system-health-decisions)).
   While a bulk Family send is in progress, request mode does not start the
   backup. "In progress" is the scheduler's own definition from
   [deltas wait for a bulk Family send](../background-processing/spec.md#deltas-wait-for-a-bulk-family-send):
   at least ten unpaused pieces of the send's work remain, read without a
   lock. Only that definition is shared; the scheduler's source-staleness
   bound does not apply to backups. One shared SQL helper should answer
   "is a send in progress" for both the scheduler and request mode, so the
   two cannot drift apart. Request mode records on the request the time it
   last saw it held, and exits quietly. The request stays `waiting`, and the
   page shows it as **Waiting for the email send to finish**, with the
   send's progress link. When the send finishes, the next poll runs the
   backup. Scheduled backups are not held: they run on their schedule as
   today, and one that runs during a send also completes a held request. In
   the worst case, a send that runs for days (for example across the daily
   limit), a held request is completed by the next scheduled backup, about
   12 to 24 hours later.
6. **Limits.** A `waiting` request expires 30 minutes after the later of the
   time it was made and the last time a poll saw it held, so a long send
   cannot expire it while polls keep running. The page then shows "The server
   did not pick up this request. Ask the server operator to check that the
   backup schedule includes the request check", linking the [nightly
   backup](../../../guides/stewardship-backup-runbook.md#the-nightly-backup)
   section, and it is never run late. A request still `running` two hours
   after it was claimed (for example, its container died) counts as failed
   ("did not finish") from then on; the next request-mode run or scheduled run
   records that on the row. Expired and failed requests never count as waiting
   or running in any check.
7. **Restore.** The restore procedure already stops the host's backup cron
   jobs. ADM-13 adds the request-mode entry to that step and to the restore
   drill's warning, and adds a step that marks every restored waiting or
   running request `expired` as of its own creation (`finished_at` set to
   `created_at`), so a restored request is never run and the page shows it
   as cancelled by a restore, not as not picked up.

**Preview.** A confirmation in the review dialog (#1356), "Take a backup
now?": one sentence says the server takes one backup soon, of the same kind
as the scheduled ones, and whether it is copied off-site or stays on the
server; during a bulk send, it adds that the backup will wait until the send
finishes, with the send's progress link. When the last backup finished and
its size are in the Backups details behind the dialog, and that a requested
backup follows the usual retention rules and adds to disk use until they
remove it is in "About this page".

**Unavailable when** (greyed, with the reason): a request is already waiting
or running ("A backup was requested at 10:42 AM and is running; it started
at 10:45 AM"), or the system is in restore review.

**Progress.** The line under the button follows the newest request through
the 10-second status, whatever the pointer is doing: while it waits, is held
during a Family email send (with why in a toggletip, and the send's progress
link) or runs, it is the greyed button's reason; once it ends, the button is
offered again and the line says it finished (and when), was cancelled by a
restore, or did not complete, which the tile's problems explain. The
confirmation says "Backup requested. The line under the button follows its
progress until it finishes.", and the page checks its status at once after
each step's answer, so the line shows the request from the start. There is no daily limit: one request at a time
is the only bound ([decision 6](#system-health-decisions)).

**Checks (server):** the [shared rules](#shared-rules-for-health-actions)
and the same two conditions.

**Confirmation:** a confirm button. Nothing typed.

**Audit:** `backup_requested`. The run is recorded by its existing
`stewardship_backup_run` row, and the outcome on the request row.

#### Clear a halted mail sender

**What it does.** After a fault that waiting cannot fix (a SYSTEMIC result)
halts the mail sender, and the cause has been dealt with, this lets sending
start again without restarting `mail-dispatch` on the server.

**Two kinds of halt.** Every halt comes from one delivery attempt's stored
outcome, and the kind is read from that outcome, never guessed:

- **Refused before the `DATA` command was sent** (for example while
  connecting, signing in, or naming the sender and recipients): the message
  was definitely not sent and is a failed delivery. The usual cause is a credential, delegation or
  configuration fault.
- **Fault once `DATA` was sent** (for example an unexpected reply to
  `DATA`, or a protocol or TLS failure while waiting for one): Gmail may
  have accepted the message, so it is `delivery_unknown` and is never
  retried automatically.

The other consumer may have had one message in flight, so a halt can affect
up to two messages, each of either kind (see the
[Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers)).

**How it works.** Today the halt lives inside the container: a marker file
that only a restart removes. Instead:

1. Each halt has an identity: a new UUID that the consumer creates when it
   decides to halt, together with that moment's time. It is written to the
   marker file, to the stored outcome of the attempt that caused it, and to
   the consumer's [service status record](#service-status-records). Today
   the bulk path writes the marker before it stores the outcome, and the
   one-message path stores the outcome first; creating the identity at the
   decision lets both paths write the same value in either order. A
   consumer halted by the other's marker takes on the identity in the
   marker, so both consumers share one halt.
2. Confirming records a clear signal (a new row: the halt identity it
   clears, who, when and the fresh sign-in instant).
3. Each mail consumer reads the newest clear signal in its idle and
   heartbeat loop and in its check before each new send, at most every 15
   seconds. It acts only on a signal naming its current halt: it removes
   the marker file only when the identity matches, resets its outage circuit
   so that exactly one message is sent as a probe (as after an outage
   cooldown), and writes a WARNING log line saying that an Administrator
   cleared the halt. A consumer that is not halted, or whose halt is newer
   than the signal, ignores it.
4. If the fault is still there, the probe meets it again, and the sender
   halts again under a new identity.
5. Clearing never resends, re-plans or re-issues anything, and changes no
   Family's link or code. The messages that met the fault keep their
   outcome: a failed one stays failed until an Administrator uses **Retry
   failed delivery** on Outgoing mail, and a `delivery_unknown` one is
   settled through the
   [delivery workflow](../background-processing/spec.md#family-invitations-and-reminders).

**Mailbox check.** The web process holds no Google Workspace credential, so
the check runs in `mail-dispatch`. Opening the preview records a mailbox
check request (a new row). The `mail-dispatch` main process claims it and
runs it through its private provider-check helper (`provider_check_worker`):
it signs in to Gmail with the current credential as the integration's
delegated user (`delegated_email`), sends no mail, stops at its time limit
(logged as every time limit is), and stores the helper's outcome, which is
one of three:

- **Passed:** Google accepted the credential and the delegation.
- **Refused:** Google refused the credential or its delegation. A revoked
  delegation, a suspended mailbox and a mistyped address all look the same,
  and replacing the key fixes none of them, so the page links the
  [mail-provider outage runbook](../../../guides/stewardship-launch-runbooks.md#mail-provider-outage)'s
  diagnosis steps instead of suggesting a fix.
- **Unavailable:** an outage, the time limit, or a failure the check could
  not explain.

The check runs even while the consumers are halted, because it sends
nothing. On the page it appears in place: "Checking the mailbox…" while it
runs, then its result in words. A check not answered within two minutes
says that the mail sender did not answer. **Check again** starts a new one.
A passed check proves only that signing in works. It cannot show that Gmail
accepts the sending address, or rule out a fault after `DATA`; the preview
says so, and the probe message after a clear is what tests those.

**Preview.** When the halt began and its kind, how many messages are
waiting, whether mail is also paused by an Administrator (clearing does not
resume a paused send), and the mailbox check. For a halt of the second
kind, it also lists the `delivery_unknown` messages since the halt began,
with a link to settle them on Outgoing mail.

**Unavailable when** (greyed, with the reason): no consumer is halted; the
mailbox check is running, failed, or passed more than five minutes ago; or
any `delivery_unknown` message from the halt's attempts, or later ones, is
not settled yet. Requiring settlement first means the Administrator decides
about every message Gmail may have accepted before sending starts again, so
nothing is sent twice by mistake.

**Checks (server):** the [shared rules](#shared-rules-for-health-actions);
the halt matches the one the preview showed; a mailbox check that started
after the halt began passed within the last five minutes; and no
`delivery_unknown` message from the halt's attempts or later is unsettled.

**Confirmation:** a confirm button. Nothing typed.

**Audit:** `mail_halt_cleared`.

**Not changed:** outage pauses and Gmail limit holds still lift by
themselves, and recreating `mail-dispatch` on the server still clears a
halt.

#### Accept a large ParishSoft change once

**What it does.** When a refresh was refused because counts fell too far
and the Administrator has confirmed in ParishSoft that the change is real
(for example, the parish inactivated many Families at once), this lets one
full refresh through without restarting the worker with
`PARISHKIT_SOURCE_MAX_DROP_PERCENT`, as the
[launch runbook](../../../guides/stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change)
requires today.

**How it works.**

1. **Every count is checked and recorded.** Today the drop check stops at
   the first count that fails. ADM-13 changes the loader (in production
   code) to check every record and eligibility count, and to record all of
   them with the refused run: each count's before value, after value and
   limit, and which ones failed. These are counts only, never parish data
   (the examples below are separate). The worker writes them, one
   `stewardship_source_drop_count` row per count, in the transaction that
   rejects the refused attempt; the rows are append-only, the web login
   reads them, and their SQL guard admits only the worker, only for a
   rejected attempt, and only a failure flag that follows the loss rule. A
   quick update that falls too far is not recorded: it falls back to a
   full refresh, as before, which records its counts if it is refused too.
   A refresh refused because it has no Families or no Members records every
   count as well, each marked failed by the same rule as any other refusal.
2. **Example Families** ([decision 4](#system-health-decisions)). When it
   records a refused full refresh, the worker also records up to five
   example Families for each failing count that concerns Families, Members
   or contacts: Families present and counted in the current promoted data
   but missing, or no longer counted, in the refused load (for a Member or
   contact count, the Family the lost record belonged to), lowest Family
   DUID first, each once per count. Each example holds only the
   Family's DUID and its name as the active parishioner family directory
   shows it, taken from the current promoted data; never which Member or
   address was lost. Ministry, roster and fund counts have no examples, nor
   does a load with no Families or no Members (it can never be accepted),
   and a tenant-mismatch refusal records none. The worker compares the
   refused load's record keys, which travel with the refusal in memory
   only, with the current data in the refusal's own transaction; if that
   fails (or there is no usable current data) the refusal is recorded
   without examples, and the preview says none are available. The
   examples go in a new parish-level table (frozen migration 0085) that the
   worker's login writes and deletes and the web login reads; its SQL guard
   admits a row only from the worker, for a count that failed whose drop
   count was recorded no earlier than the inserting transaction began (in
   practice the refusal's own transaction), and nobody changes one. It belongs to no campaign,
   so campaign purge does not touch it. Nothing else from the refused load
   is kept: the worker discards it as it does today. Backups gain no new
   personal data, because each name already exists in the promoted data
   that every backup holds.
3. Confirming records a one-time acceptance (a new row: the refused run,
   every recorded count's before and after values, who, when and the fresh
   sign-in instant) and requests a full refresh, as **Refresh now** does.
   A refusal is accepted at most once: if its acceptance lapses unused,
   **Refresh now** gives a new refusal to review and accept. The refusal of
   a load with no Families or no Members cannot be accepted. The row is
   never changed or deleted; its SQL guard (frozen migration 0080) also
   checks the refusal, the counts and that no other acceptance is live.
4. The next full refresh honors the acceptance only when every recorded
   count's new value is at least the reviewed after value: no count falls
   below what the Administrator saw. The before values are recorded with
   the acceptance but not compared. The eligibility baselines come from the
   current data as well as the recent full refreshes, so every quick update can
   move them, and binding them would refuse almost every accepted refresh
   while quick updates run every 15 minutes. A count that passed in the
   refusal keeps the limit in effect. A load with no Families or no Members
   is still refused. Any other result is refused again, with a new refusal
   to review. A count that fails now must also have failed in the refusal,
   and the Member contact list check
   ([#387](https://github.com/epiphany40223/parishkit/issues/387) M4) stays
   in force. The worker reads the live acceptance only when a full load's
   drop check fails, after the download, so a full refresh already running
   when the Administrator confirms can use it.
5. **Lifetime.** The acceptance ends when any full refresh is promoted,
   whether or not it used the acceptance, when a new refusal is recorded,
   or 24 hours after it was given. Quick updates never use it and never end
   it. The refresh that uses it records in its manifest the acceptance, who
   gave it and the counts that failed, as the environment override's limit
   is recorded today, and logs a WARNING line naming the acceptance. The
   lifetime is derived from these facts, never stored, by one SQL function
   that the guard and the worker share. The refresh is promoted only if its
   acceptance is still live, checked again under the work-order lock in the
   promotion's own transaction; otherwise it is refused, nothing new is
   recorded, and the process log's CRITICAL line names the lapsed
   acceptance. That refusal can never be accepted again; the next full
   refresh (for example **Refresh now**) records a new refusal, and that one
   can be accepted. After it, only the counts that failed start their
   trend again from that refresh
   ([full cycle](../background-processing/spec.md#full-cycle)); every other
   count keeps its week of history, so the next refresh compares with the
   accepted counts at the normal limit and the acceptance never weakens the
   check of a count it did not cover.
6. **Examples are deleted** when their refusal is no longer the newest full
   refresh's outcome, when a full refresh is promoted, when the refusal is
   accepted (the decision), or seven days after the refusal, whichever
   comes first. These four triggers and the seven days are the default,
   pending Administrator confirmation (asked on
   [#530](https://github.com/epiphany40223/parishkit/issues/530)); decision
   4 says only that examples are kept until the decision or their expiry.
   One SQL function names the one refusal whose examples may still be
   shown; the worker's hourly housekeeping deletes every other example, and
   the page and the command read only that refusal's, so they never show an
   example whose deletion condition is already met, even before
   housekeeping runs. A restore can bring back examples from the backup;
   the page hides them by the same conditions, and the restore procedure
   deletes them. Their retention is listed with the other bounded cleanup in
   [temporary retention and housekeeping](../operations/spec.md#temporary-retention-and-housekeeping).

**Preview.** Every recorded count, with its before and after values and the
share it fell by, marking the ones that failed. It says that records
missing from ParishSoft leave the campaign's data after the refresh, and
that a mistake in ParishSoft should be fixed there, followed by **Refresh
now**. Under each failing count it lists the example Families (name and
Family DUID), or says none are available for a failing Family, Member
or contact count; what they are and when they are deleted is in an "i"
toggletip. Only Administrators see them, since only they may open the
page. The examples are part of the preview, drawn with it, so nothing
appears or moves after it.
Example names never appear in a log line, an audit context, the refusal's
log entry or a notice. Opening the preview records no event (see
**Audit** below); the preview's command prints the counts and the example
Family DUIDs (`examples`, by count) without names, as the automation
spec's
[personal data rule](../admin-automation/spec.md#personal-data-on-the-command-line)
requires.

**Where it appears.** In the [ParishSoft refresh tile](#health-tiles)'s
details, directly after the refused change's counts table that ends its
problems (Administrator, pk-local check of #1210: not at the foot of the
page), and before the panel content. The section is drawn only while the
refusal shown can be accepted, offering the button, or was accepted, with
the button greyed and saying so (below). With no refusal shown, and no
step's result to show, it is left out entirely (Administrator, pk-local
batch 24a: with nothing to accept, the
[conditional fields](../ui-conventions/spec.md#conditional-fields) rule leaves the control
out). It is left out as well for a refusal that cannot be accepted: one
whose acceptance lapsed unused, or a load with no Families or no Members
(Administrator, 2026-10-15). Then the refusal's problem line says why
instead, and links [Refresh from ParishSoft](#manual-parishsoft-refresh):
a lapsed acceptance cannot be given again, so **Refresh now** gets a new
refusal to review, and an empty load is refused at any limit, because it
almost always means ParishSoft could not be read properly. The 10-second
status brings the section and takes it away in place, in the same update
as the refusal's problem item and its counts, so it comes and goes with
the problem it answers and moves nothing else. While
a step's result shows (a preview, the acceptance just recorded, or a
refusal of the step), the section stays, and only its button follows the
status. The section is outside the part the status updates replace, so
an open preview, its tick box and the headings above it never move or
disappear when the status changes. If a status update shows another
refusal than the one an open preview reviews, or none (the status names
its refusal for the region, see
[in-place controls](../ui-conventions/spec.md#in-place-controls)), the
preview says "This changed since you looked; review it again" on a line
kept for it and Confirm is held. Once a
refusal is accepted, its line in the problems list says "The refused
ParishSoft change was accepted once by …; waiting for the next full
refresh" and is no longer counted among the problems that need attention
(Home keeps the refusal's own line). Its rules (what the next full
refresh must meet, how the acceptance ends, that a refusal is accepted only
once and an empty load never, and the server operator's override as the
emergency path) are in "i" toggletips. The section does not always say
that the button needs a recent sign-in: only when the sign-in was too old
for it as the page was drawn does the shared step-up notice stand above
the greyed button, which names it, as the
[step-up rule](../ui-conventions/spec.md#page-help) asks (#1159, #1331).
That is fixed for the page's life, so no status update shows or hides the
notice; a sign-in that ages after the page was drawn gets the step-up
answer when the button is used. The preview, the refusals and the
result are answered in place, as the
[in-place controls](../ui-conventions/spec.md#in-place-controls) convention
requires. The preview's binding is signed for the Administrator who
reviewed it and lasts fifteen minutes, the shared
[preview lifetime](#preview-lifetimes); an older one is "This changed since you
looked; review it again", as is any refusal by the acceptance's guard other
than the sign-in's (a newer refusal, a promoted full refresh, an acceptance
already given). After confirming, the greyed button says "Accepted by … at
…; expires at …" in browser-local time and that the next full refresh will
use it. If the full refresh cannot be requested right then, the acceptance
stands and the section says the next full refresh, scheduled or from
**Refresh now**, will use it.

**Unavailable when** (greyed, with the reason): the tick box "I checked
this in ParishSoft, and the change is real" is not ticked; the refusal is
no longer the newest full refresh's outcome; or an acceptance is already
active ("Accepted by … at …; expires at …"). A refusal whose acceptance
lapsed unused, or a load with no Families or no Members, draws no section
at all, as **Where it appears** says. If a step's result is showing when
the refusal becomes one of these, the result stays and, in place of the
button, one line says the refusal cannot be accepted and that its problem
line says why.

**Checks (server):** the [shared rules](#shared-rules-for-health-actions);
the newest full refresh was refused for a drop and no full refresh has been
promoted since (quick updates do not count, as #510 notes); the counts
match the preview binding; the tick box was ticked; no other acceptance is
active; and the refusal is not a tenant mismatch.

**Confirmation:** the tick box, then confirm.

**Audit:** `source_drop_accepted` naming the acceptance, and from the
command line also `admin_cmd_system_refresh_accept`, plus the manifest
record above. Accepting is the Administrator's answer to the refusal, so in
the same transaction it acknowledges the refusal's own CRITICAL
`source_destructive_change` entry in the
[critical-problems banner](#navigation-and-home), through the banner's shared
acknowledgement and its `critical_events_acknowledged` event, recorded for
that Administrator with the acceptance as its subject (Administrator, pk-local
check of #1210). Only that refusal's entry is acknowledged; an earlier
refusal's and any other problem's stay until acknowledged, and a refused,
expired or repeated confirmation acknowledges nothing. The web login reads
which task each refused attempt ran (only the attempt's id and task) to find
the entry. As with the other actions, the preview step records nothing
(not `system_health_viewed`, which only opening the page records).

**Not changed:** the environment override still works for the server
operator. While it is set, the panel shows the limit in use.

#### Turn off debug logging

**What it does.** It sets the **debug-off switch**, which turns debug
logging off in every process at once, without recreating containers. The
switch stays set until it is cleared.

**How it works.**

1. Confirming sets the switch: one durable row with who, when and the fresh
   sign-in instant.
2. **Every process that reaches the database honors it.** Today
   `debug_logging_enabled()` reads the environment on every call. ADM-13
   gives it an in-process override: a process that reads the switch as set
   turns debug logging off for itself: its log formatter goes back to the
   normal form that drops message text and tracebacks, and its logger
   thresholds go back to INFO, so DEBUG records stop. Online processes
   read the switch right after they connect to the database at startup, and
   again with each [service status record](#service-status-records) (every
   60 seconds). One-shot commands that reach the database (`pk-admin`
   commands, `health`, `smoke`, the backup worker and the other operator
   commands) read it when they connect. In practice every new database
   connection reads it. A process started without the setting never reads
   it, because the switch can only turn debug logging off. The read
   answers only whether the switch is set; of the process logins, only the
   web login reads the switch record itself, for the panel's who and when.
3. **Child processes.** A process passes its effective state to every
   helper subprocess it starts: while the switch is set, it removes
   `PARISHKIT_DEBUG_LOGGING` from the helper's environment, so a helper
   that never reaches the database cannot log in debug form either. A
   helper or sibling process started while the switch is set keeps that
   environment, so it stays off after a clear until its service is
   recreated.
4. The page follows the switch at once. A service status record written
   before the switch was set no longer counts as debug logging in effect:
   the process applies the switch before its next record, so only a later
   record can say whether it did. System health and Home therefore stop
   naming the services as soon as the switch is set (the tile turns Working
   and its summary names who set the switch), and name again any service
   whose next record still reports debug logging, because it could not
   apply the switch. The debug logging banner, which reads only the web
   process's own state, ends when that process applies the switch.
5. Lines written before a process first reads the switch at startup are not
   covered. "About this page" says so.

**Preview.** A confirmation in the review dialog (#1356), "Turn off debug
logging?": one sentence says debug logging is turned off now, without
restarting them, in the services the server found with it on (named), until
the switch is cleared. That lines already written stay in the logs, the
startup gap above, and that the setting on the server still says "on" until
the server operator recreates the services are in "About this page", the
region's toggletip and the panel, so the dialog does not repeat them.

**Where it appears.** In the [Debug logging tile](#health-tiles)'s
details, after its problems and before the panel content. The page always
draws the region, in Testing and in Production, whatever the tile's state.
Each of its buttons is drawn only while it can act, never greyed
(Administrator decision, 2026-10-15, on
[#1214](https://github.com/epiphany40223/parishkit/issues/1214), following
the rule #1295 set), and the region shows the first of these that holds:

1. While the switch is set, only the status line "Debug logging was turned
   off by … at …" (without the address when it cannot be read), followed
   in Testing by **Allow debug logging again**. Production never draws that
   button: only the server operator clears the switch there.
2. While some service has debug logging in effect, the region's one-line
   explanation and **Turn off debug logging**.
3. Otherwise the status line "Debug logging is off in every service that
   reports."

These follow the 10-second status in place. The states differ in height,
so the polled copy waits while the pointer is over the tile grid
(`data-live-mirror-hold`, as in the [tile grid](#tile-grid)) and nothing
moves under it. Help is in an "i" toggletip. Beside the explanation, it
says what the switch does (each service reads it within about a minute,
and lines already written stay in the logs). Beside the status line that
names who set the switch, it says who clears the switch, and that services
started again afterwards log in debug form again where the server's
setting is still on.

**Not drawn when:** the switch is already set, or no service has debug
logging in effect any more (the status lines above say which).

**Checks (server):** the [shared rules](#shared-rules-for-health-actions)
and the switch is not already set.

**Confirmation:** a confirm button. Nothing typed.

**Audit:** `debug_logging_turned_off`, and from the command line also
`admin_cmd_system_debug_off`. Each process's WARNING log line records when
it applied the switch.

**Clearing the switch.** Clearing it lets processes started with the
setting log in debug form again; it can never turn debug logging on in a
process that was started without the setting. The server operator can
always clear it with a new operator command,
`pk-stewardship debug-off-clear`. It runs offline from the admin-recovery
profile, with the services stopped, then they are started again: only that
login can be told apart in SQL from the web login, which `pk-admin` and the
online operator commands share. The switch records that the server
operator cleared it, and when, and the command records
`debug_logging_allowed` with `actor_kind` `operator` in the same
transaction. That event names the switch as its subject, as the
Administrator's clear does: the admin-recovery login cannot read the switch
table, so its clear returns the id of the switch it cleared (#1229). While
debug logging is back in effect, the
panel says when the server operator cleared the switch.
In Testing mode the region also offers **Allow debug logging again**
(ADM-13 PR 6c), below the switch's status line, while the switch is set. Its
preview is a confirmation in the review dialog (#1356), "Allow debug logging
again?": when the switch was set is on the page behind it (the status line
and the panel), so one sentence says that clearing it lets services
started with debug logging on (never one started without it) log in debug
form again within about a minute, which can record Families' personal
information; confirming clears
the switch (`cleared_by` `administrator`, the Administrator recorded), under
the [shared rules](#shared-rules-for-health-actions), and records
`debug_logging_allowed` naming the switch (from the command line also
`admin_cmd_system_debug_allow`, `pk-admin system debug-allow`). Each step is
answered in the dialog, with the same fresh sign-in step-up. A repeated
confirmation returns the switch already cleared; one another Administrator
or the server operator cleared first is refused as stale. It is not drawn
while no switch is set, nor ever in Production. In Production the
page never offers it, and the server refuses it ("In Production only the
server operator can clear the debug-off switch")
([decision 3](#system-health-decisions)): the switch's guard (migration
0088) admits the web login's clear only while the global mode is Testing,
for an enabled Administrator with a live session signed in within five
minutes (or a live full-scope automation session), once, changing only the
clear's time, kind and Administrator. Turning debug logging off is offered
in both modes. The operator's offline clear is unchanged and still names no
subject.

### Service status records

The mail sender, debug logging and version panels need to know what each
online process is doing, which today lives only inside each container
(heartbeat files and the mail circuit). ADM-13 adds a small **service
status** record, which each online application process (web, worker and
its source process, scheduler, each mail consumer, and the configuration
and credential installers) writes when it starts and then every 60 seconds:

- the service and process role, when it started, and when it last reported;
- the application version it runs;
- whether debug logging is in effect;
- for a mail consumer, its sender state (running, paused after an outage,
  held at Gmail's limit, waiting for the daily limit, halted), when that
  state began, when it ends if known, and, for a halt, its identity and
  kind.

Each service's database login may write only its own service's rows, and
the web login reads them. The installers' logins gain a write grant they do
not have today, which PR 1's security review checks. A record holds no
host name, address, message, recipient or credential. Housekeeping removes
rows from processes that have not reported for a day. A process whose
record is more than three minutes old is shown as not running.

The record is a `stewardship_service_status` row per process, keyed by an
identity the process makes when it starts, so a restart starts a new row and
leaves the old one until housekeeping removes it. It names the service
(`web`, `worker`, `scheduler`, `mail-dispatch`, `config-installer` or
`credential-installer`, with the credential installer's target) and the
process (`main`, the worker's `source` process or mail dispatch's second
consumer, `mail`). Each web worker process reports on its own. The SQL guard
sets the start and report times, and when the current sender state began,
from the database clock, keeps each row's identity and version fixed, and
lets only the worker's hourly housekeeping delete a row, once it has not
reported for a day. A process writes its record only on a connection it
already holds, or from an idle moment on a short connection of its own,
never inside other work's transaction. A write stopped by its own two-second
statement or one-second lock limit is recorded as a timeout under the
[timeout rule](../operations/spec.md#observability-and-health), naming no
task; an installer's goes to the process log only, as that rule's listed
exception. Any other failed write is logged once (`service_status_failed`).
Neither stops the process. The halt identity and kind are added by
[clear a halted mail sender](#clear-a-halted-mail-sender) (PR 4), which
defines them.

The page groups the rows by service, process and target. In each group the
newest-started row that reported within three minutes stands for it, so the
row a restart left behind never shows its process as not running; with no
such row, the newest-started row does, shown as not running. Several web
worker processes share one group, so the web line counts how many are
running. A core service (web, worker, scheduler or mail dispatch) with no
row at all "has not reported", which is a problem; the installers are shown
only when they report.

The record is for display: no sending, refresh or backup decision reads it,
so a lost write can only make the page out of date. The one action check
that reads it, "a consumer reports Halted" for **Clear the halt**, is safe
even when the record is out of date, because a consumer acts only on a
clear signal that names its current halt.

### Runbook steps the page replaces

| Runbook step today | Replaced by |
| --- | --- |
| [Backup runbook: the nightly backup](../../../guides/stewardship-backup-runbook.md#the-nightly-backup), running the backup by hand before Production activation and before an upgrade | **Take a backup now**. The scripted upgrade's own backup stays a host step. |
| [Backup runbook: checking](../../../guides/stewardship-backup-runbook.md#checking), running the backup by hand when `backup_rpo_breach` fires and confirming the off-site copy | **Take a backup now** and the backups panel. Diagnosing a backup that keeps failing stays on the host. |
| [Launch runbooks: accepting a large ParishSoft change](../../../guides/stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change), recreating the worker with the loss limit override | The ParishSoft refresh panel and **Accept this change once** (landed). Checking in ParishSoft stays, and the override stays the server operator's emergency path. |
| [Launch runbooks: mail-provider outage](../../../guides/stewardship-launch-runbooks.md#mail-provider-outage) step 5, restarting `mail-dispatch` after a systemic failure | **Clear the halt**, after a passing mailbox check and with every uncertain message settled. Steps 1 to 3 stay. |
| [Launch runbooks: Production activation](../../../guides/stewardship-launch-runbooks.md#production-activation) step 1, inspecting each container for `PARISHKIT_DEBUG_LOGGING` | The debug logging panel and **Turn off debug logging**. Recreating the services without the setting still removes it for good. |
| Asking the operator which version is running | The version panel. |

ADM-13 updates each guide in the pull request that adds the matching
action, naming the portal step first and keeping the host step as the
fallback. The backup pull request also names the request-mode cron entry in
the nightly backup schedule, the restore steps and the restore drill's
warning.

### What stays on the host

These remain server-operator work, because the portal cannot do them safely
or cannot do them at all:

- **Starting, stopping, recreating or upgrading services**, and changing
  their settings (Compose file, image, environment variables such as
  `PARISHKIT_DEBUG_LOGGING`, `mail_consumers` or the mail transport). The
  web process has no Docker access on purpose: a break-in to the web process
  must not become control of the server.
- **Restore and the restore drill.** They need the backup private key,
  which is never on the server, and they stop the services the portal runs
  on. The [Restore review page](#restore-release) follows a restore.
- **Backup keys:** making the key pair, opening backups, answering the key
  challenge (`backup-keygen`, `backup-open`, `backup-prove`) and installing
  the first public key. Replacing the public key is already a portal action.
- **The backup schedule and off-host copies made outside the application**
  (the cron entries, including request mode, and any `rsync` or `rclone`
  copy). The application cannot read or change the host's cron.
- **Diagnosing a backup or a service that keeps failing:** file ownership,
  mounts, disk space, the database's own state. The page names the category
  and links the checklist.
- **A ParishSoft key that reaches another organization**
  (`source_tenant_mismatch`), which needs the parish and the key's owner.
- **Offline Admin-access recovery**, when no Administrator can sign in
  ([offline Admin-access recovery](../operations/spec.md#offline-admin-access-recovery)).
- **Ending every automation session at once**
  (`revoke-automation-sessions`), certificates, DNS, and the host's
  operating system.
- **Removing debug logging for good** by recreating services without the
  setting, and clearing the debug-off switch with
  `pk-stewardship debug-off-clear`.

### System health decisions

The Administrator decided these on 2026-10-04 (recorded on
[#530](https://github.com/epiphany40223/parishkit/issues/530)). The text
above already follows them.

1. **Can Staff see the page, read-only?** No. Administrators only.
2. **May a requested backup start during a bulk send?** No. The request is
   held until the send finishes, as
   [take a backup now](#take-a-backup-now) describes. Scheduled backups are
   not held.
3. **May debug logging be turned back on from the portal?** In Testing
   only. Turning it off is offered in every mode.
4. **Should the large-change preview show examples of Families that would
   leave?** Yes: counts and a few example Families (name and DUID only), as
   use case ADM-29 asks. Only the examples are kept, and only until the
   decision or their expiry.
5. **Does Take a backup now need a fresh sign-in?** Yes, like the other
   three actions.
6. **Is there a daily limit on requested backups?** No. One at a time is
   the only bound.
7. **Should accepting a large change or clearing a halt send email or Slack
   notices?** No.
8. **Is System health the first System entry, so that `/admin/system/`
   opens it?** Yes.

## Logs

Only Admins access the combined log screen. It supports:

- levels DEBUG, INFO, WARNING, ERROR, and CRITICAL with accessible, distinct
  indicators: one matched, self-hosted icon set (a grey dot for DEBUG, a blue
  "i", an amber warning triangle, a red cross and a dark red stop sign for
  CRITICAL) whose shapes,
  not only their colors, tell the levels apart. Audit records, which have no
  level, have a purple clipboard icon of their own, also distinct in shape
  ([#601](https://github.com/epiphany40223/parishkit/issues/601)). The filter
  bar's "Show" row is six checkboxes, the five levels and "Audit record", each
  icon beside its word. The table's second column, Level, after Time, shows
  the same icon alone, with its word as screen-reader text and a tooltip, so
  the column stays narrow. CRITICAL rows are highlighted;
- a compact filter bar: the six Show choices, type and date range fit in one
  or two rows at desktop width, and the actor, correlation and campaign
  identifier filters are folded under "Filter by identifier" until one is
  used. The
  page's longer explanation is in its "About this page" panel;
- default exclusion of DEBUG; the first view shows every other level plus
  audit records. A submitted form shows exactly the kinds it ticks: "Audit
  record" alone is the audit trail only, and leaving it unticked shows
  operational entries only. At least one must be ticked: the filter form's
  complete gate keeps Apply unavailable, saying "Tick at least one kind of
  entry to show.", and the server refuses a form with none at the Show
  choices (see refusals at a field, below), not with the identifier
  guidance. A download with none keeps its own error message. A campaign
  filter with Audit record
  unticked lists nothing, and its empty table says to tick Audit record.
  There is no separate Source field. For one release the server still accepts the
  retired `source` value an older open tab may send, mapped onto the
  checkboxes (`audit` clears the levels, `operational` leaves audit records
  out, `both` includes them), and carries only the checkboxes from then on.
  The critical-events banner's link ticks Critical alone, without audit
  records, and the same-campaign icon ticks Audit record alone;
- action/type, Activity ([sign-in activity](#sign-in-activity) and the
  other [activity groups](#activity-groups)), campaign,
  entity, actor, task/request correlation, text, Ministry, date range, and
  the six Show (level and audit record) filters.
  The entity filter is the audit subject identifier (audit records only, like
  campaign), under "Filter by identifier" with the other identifiers;
- text search ([#536](https://github.com/epiphany40223/parishkit/issues/536)):
  a case-insensitive phrase of at most 64 characters, never holding `@`,
  matched against the stored type, the explanation the page shows for the
  entry (for an operational type whose sentence depends on its outcome, the
  sentence for that entry's outcome) and each recorded detail value the page
  shows on its own (numbers, Booleans, lists of numbers and text of at most
  128 characters, of reviewed fields only), never key names, actor
  addresses, the "what went wrong" sentence or credentials. The search text
  is a private filter: it travels only in POST bodies, like the
  [Find a Family](#admin-navigation) search, never in a link. It has no
  text index: it reads the entries in the date range, which the page says.
  Every log read (page or export) runs under the 60-second interactive
  statement limit; a read it stops is recorded as a timeout (what, limit,
  time taken) and answered with the page's unavailable message. A trigram
  index is a schema change left until the log needs one
  ([#765](https://github.com/epiphany40223/parishkit/issues/765));
- bookmarkable views (#536): a GET may carry only the filters that are not
  private (the Show choices, Activity, type, Ministry, From and Through with
  their zone, rows per page and sort). The search text, identifiers (actor,
  correlation, campaign, subject) and the paging snapshot stay in POST
  bodies, following the [Admin tables](../ui-conventions/spec.md#admin-tables) rule that private
  filters keep POST state, because a web address is kept in the server's
  access log and the browser's history; a GET carrying one is refused
  without echoing it. The page draws "Link to these
  filters" with only those filters, and after each in-place answer the
  address bar shows it, so a bookmark or reload keeps them. A link's days
  stay in the zone it was made in, and the page says so when that is not the
  browser's zone; applying the filters again uses the browser's zone;
- before/after detail for audit events;
- a plain-language explanation beside each entry's stored type. Every type the
  application defines has one (a guard test enforces it for the audit and
  operational vocabularies and for types written directly by code and SQL
  triggers). Types built from a prefix and a state, such as
  `config_request_applied`, read as a sentence plus the state in words;
- an actor column that names portal users by address and background worker
  processes as "Background worker". Other audit actors are named by the actor
  kind their entry recorded (a Family, the system itself or the server
  operator), and any remaining identity as a service or former user;
- recorded detail shown with each field named in words, such as "Lag
  microseconds", while exports keep the stored field names. An entry at
  WARNING or above, and a recovery entry, also says in a sentence what
  happened this time, read from its closed context: what failed or was late,
  by how much against which limit, and what happens next (retried after how
  long and on which attempt, or given up), or which problem ended and after
  how long (#633; see
  [what went wrong, and recovery](../background-processing/spec.md#what-went-wrong-and-recovery)).
  An older entry without that context lists its fields only;
- cross-links from each entry, as small icon buttons in the Related column
  ([#1065](https://github.com/epiphany40223/parishkit/issues/1065);
  [row links](../ui-conventions/spec.md#row-actions-and-confirmation)), in
  five fixed slots so each kind lines up down the column: related entries
  (a chain link, "Show entries related to this entry", the same correlation
  identifier), the same actor (a person, "Show entries by the same actor"),
  the same subject (a target, "Show audit records about the same subject")
  and the same campaign (a calendar, "Show audit records for the same
  campaign"), both audit records only, and, for task entries and views of
  one task's page, the task (an arrow out of a box, "Open this task"), a
  link to the background task page. Each icon's accessible name is its
  tooltip. An entry shows only the icons that have something to link to
  (Administrator decision, 2026-10-11): related entries only when the
  related view, which lists the default kinds of entry (every level but
  DEBUG, and audit records), would list another entry with the same
  correlation; the actor, subject, campaign and task icons only when the
  entry has one. An absent icon leaves its slot empty. Only the page reads
  this, for its own entries, in one statement that takes at most two
  entries per correlation from each log through its correlation index; the
  download and the command line's `logs list` skip it. Each filter travels in a POST body
  like the form's and applies
  [in place](../ui-conventions/spec.md#in-place-controls): the filter form above then shows the
  filters applied ("Filter by identifier" opened), and focus returns to the
  same entry's icon in the filtered list, or to the list when that entry
  is no longer in it. Rows show no identifiers and no "Technical details"
  disclosure: a task's page shows its identifiers under its own Technical
  details, including the correlation its log entries share, and the
  [download](#logs) keeps every entry's identifiers (the recorded
  exception in the
  [downloaded files](../ui-conventions/spec.md#downloaded-files) rule). An
  entry without a task therefore shows no identifiers on any page; its
  icons filter by them, and "Filter by identifier" says to copy one from a
  task's page or a download. The table uses the shared Admin table styling;
- the shared [table navigator](../ui-conventions/spec.md#admin-tables) in POST mode. The first view
  records a snapshot instant, and every later page, size or sort change lists
  only entries created at or before it, so new entries never shift pages. An
  entry whose transaction was already running when the snapshot was taken
  can still appear on a later view and shift a page by one entry; that
  overlap is rare and brief. Applying the filters again starts a new
  snapshot at page 1 and keeps the rows-per-page and sort choices. Time is
  the only sortable column (newest or oldest first, both read through the
  `(created_at, id)` indexes on both logs). Level exists only on operational
  entries; no index orders the two logs together by Type (the operational
  log's type is unindexed); Actor is looked up for display rather than
  stored; and Related and Recorded detail are not single values. None of
  them sort. The count stops at 10,000 entries per log, and paging reaches at
  most 10,000 entries deep in either order; past that the page suggests a
  narrower date range or the other order; and
- CSV, XLSX or JSON Lines export of the filtered result (below).

Ministry filtering offers each Ministry of the current ParishSoft catalog by
name and never shows its DUID; the chosen Ministry's DUID (the filter's value,
also in a link) matches an entry whose detail names it as `ministry_duid` or
in any sorted Ministry list, so it includes both
interactive event identifiers and the
[retained export result scope](../reports/spec.md#ministry-change-summary),
including after campaign-detail purge. ADM-08/RPT-09 own this log-query
integration; report owners supply the durable non-sensitive event metadata.

In v1 a log export is a bounded download from the log screen: CSV, XLSX or
JSON Lines of the entries matching the screen's filters, newest first, at most
the newest 10,000, with the same reviewed detail the screen shows. CSV and XLSX
carry the same columns under plain headings, in the screen's words where it
has them (Source names an entry's kind, operational or audit): Time,
Source, Level, Type, Actor, Actor identifier, Correlation identifier,
Campaign identifier, Subject identifier and Recorded detail (#966). JSON
Lines keys each entry by the same fields in snake_case (`time`, `source`,
`level`, `type`, `actor_email`, `actor_id`, `correlation_id`,
`campaign_id`, `subject_id`, `details`), for programs. After its table
sheet the XLSX has the shared "Report information" sheet (#926): Report
("System logs"), Downloaded at (in the chosen zone), Display time zone,
Entries in this file, Entry limit and Privacy. It names no Parish or
Campaign, because the log is system-wide, and, like the other Admin
downloads, it does not list the filters. There is no PDF, and
the identifier columns stay: both are the recorded exception in the
[downloaded files](../ui-conventions/spec.md#downloaded-files) rule. It is
Administrator-only, uses a CSRF POST like the screen's filters, rechecks
authorization after the file is built and records a count-only audit event. An
unbounded export is never assembled in a web request; moving log exports onto
the asynchronous export-job pipeline used by large report exports is deferred.

Stored timestamps are UTC. The screen renders browser-local timestamps to the
second, with the zone name (no UTC offset, #635), so entries can be compared
with times recorded elsewhere; the zone name also tells the repeated hour
apart when clocks fall back. The From and Through date filters are whole days in
the browser's time zone, following the
[timestamp rule](../ui-conventions/spec.md#date-format): the filter form
carries the browser's zone, the server turns From into the start of that local
day and Through into the start of the next local day (exclusive), so a
daylight-saving day is 23 or 25 hours long, and paging, sorting and export
carry the zone with the other filters. A date sent without a zone the server
knows is refused with a hint to apply again; while the browser reports no zone
the page keeps Apply unavailable whenever a date is entered.
The page checks its filters before Apply (a Show choice ticked, a whole
identifier, a whole date, and Through no earlier than From). A request
that reaches the server with one broken is refused at that field
([field errors](../ui-conventions/spec.md#field-errors-and-live-checks),
[#1332](https://github.com/epiphany40223/parishkit/issues/1332)): nothing
ticked at the Show choices, with each box that can be ticked marked (under
an audit-only Activity, Audit record alone, in its hint's words); dates
out of order at Through, with both dates marked. The message is shown in
the filters' hint row. The view is audited
denied. A day outside 2020 to 2999, which no hint names, keeps the error
page, and so does a date without a known zone, and every refusal no field
can fix. The critical-event
banner's link sends the browser's zone with a From day one day before its
24-hour window, so the window is covered in every zone; when the browser
reports no zone it sends no From day. Export
requires choosing UTC or the browser's timezone (offered by the page), as every
download whose rows hold exact action times asks
([time zone question](../ui-conventions/spec.md#downloaded-files)); the
file's timestamps carry their UTC offset. Logins/logouts, configuration, polls/tasks, each email and
reason/recipient routing, report execution/export, errors, Family access,
submission changes, workflow changes, publication, purge and
[refused Admin input](../operations/spec.md#admin-input-refusals) are
recorded.

### Sign-in activity

An Administrator sees every recorded Admin portal sign-in event in one list
([#953](https://github.com/epiphany40223/parishkit/issues/953)) by choosing
"Sign-in activity" in the filter bar's Activity choice; "All activity", the
default, applies no group. The group is a fixed list of audit types, so no one
types internal type names one at a time:

- `admin_login`, a successful sign-in to the portal by any portal role;
- `admin_step_up`, a sign-in confirmed again for a protected action;
- `admin_logout`, `admin_timeout`, `admin_revoked`,
  `admin_reauthenticated` and `admin_privileges_changed`, the
  [session endings](../architecture/spec.md#identity-and-session-security)
  (sign out, inactivity or absolute expiry, access removed, all sessions
  signed out or an Administrator signing the session out on
  [Signed-in sessions](#signed-in-sessions), a new sign-in that replaced the
  previous session, and a session replaced by one carrying the person's
  changed roles);
- `admin_session_signed_out`, an Administrator signing someone's session
  out on Signed-in sessions, with the Administrator as actor;
- `admin_login_refused`, a sign-in by a Google account that Google verified
  and policy then refused (no user with that address, a disabled identity,
  or the SQL session guard finding the user gone at the session insert;
  until the users code of #952 lands, no rule or a rule with no role stands
  for no user). Its actor is that account's `PortalUser`, so the Actor column
  names the address, and it carries no context. It is recorded at most once
  per account every ten minutes, and at most 20 times per deployment in any
  ten minutes across all accounts. Anyone can create Google accounts
  cheaply, so the per-account bound alone would not bound the log; the
  deployment-wide ceiling does. One deployment-wide advisory lock is held
  while both bounds are checked, so concurrent refusals cannot pass either
  bound together. A refusal past the ceiling falls back to the anonymous
  sample below; and
- `admin_login_denied`, the sampled record of every other refused sign-in
  (a stale or repeated sign-in state, a provider error, a rate limit
  including the per-identity limit, or a named refusal past the ceiling): at
  most one per deployment every five minutes, with no actor, so public
  traffic cannot allocate unbounded audit rows. A refusal recorded as
  `admin_login_refused` is not also sampled.

The server maps the choice to that closed `event_type` list. Operational
entries are left out while it is chosen, since none is sign-in activity: the
five level boxes stay in place but greyed out and are not sent
(`data-enabled-when`), so choosing it moves nothing, and Apply waits until
Audit record is ticked, the gate's hint line below the filter bar saying so
(`data-required-when`). A request that bypasses the page with Audit record
unticked gets an empty table that says to tick it. A typed type
narrows further within the group. Every other filter, the snapshot paging,
the 60-second read limit and the view's own count-only audit record apply as
for any other view. Rows keep the log's columns, Time first in browser-local
time, and the Actor column names the person by address. The same-actor icon
on an entry carries the choice, so it lists that person's sign-in activity. The
choice is a link filter, so a bookmark or the page's own link keeps it, and the
download carries it, so the file holds exactly the entries the page lists. The
Portal users page links to it, and to each user's recorded identities' own
sign-in activity ([portal user management](#portal-user-management)).

A refused account's address is personal data. The entry stores only the
account's `PortalUser` id; the address it shows is the one that row already
holds, because a verified sign-in attempt under the current recovery epoch that
passes the per-identity rate limit records its identity whether or not policy admits it ([administration
user and policy](../data/spec.md#administration-user-and-policy)). The entry is kept
like every other audit event, indefinitely by default
([retention and deletion](../data/spec.md#retention-and-deletion)), and is
seen only by Administrators.

The live sessions themselves are listed on
[Signed-in sessions](#signed-in-sessions), where an Administrator can sign
one out.

### Activity groups

The Activity choice offers seven more groups after Sign-in activity, in this
order ([#953](https://github.com/epiphany40223/parishkit/issues/953),
[#1403](https://github.com/epiphany40223/parishkit/issues/1403)). Each
group is a closed list of types kept in one place, `audit.log_rows.ACTIVITY_TYPES`,
which `pk-admin logs --activity` shares. Every member is a type the system
writes: an audit `Action`, a directly written audit type, a trigger-built type
whose closed states `audit.log_descriptions.BUILT_TYPES` lists, or an operational
entry on the log-writer manifest. Every written type is either in a group or
in `audit.log_rows.UNGROUPED`, listed under the reason it is left out (for
example page views, staff follow-up, the campaign lifecycle and Production
steps, and background-work health and task steps (`task_*`) shared by every
kind of work). A unit test requires the groups and
`UNGROUPED` together to be exactly the written types, so a newly written type
fails until it is placed. PostgreSQL tests compare each `BUILT_TYPES` list
with the installed CHECK constraints or functions closing it (`campaign_*`
both the delivery controls and the lifecycle transitions; `production_*` the
Production steps; `setup_mail_*` its states plus `scrubbed`), and require
every `'<prefix>_'||` an installed audit-writing function builds to be a
`BUILT_TYPES` prefix, so a new trigger-built family cannot be missed.

- **Configuration changes** (`configuration`, audit records only): settings
  requests (`configuration_requested` and `config_request_*` from staged
  through validating, prepared and applied to failed, the refused record, or
  cancelled), `configuration_activated`, what an applied change did to access
  (`policy_security_event`, `policy_denial_namespace_reset`), mail schedules
  and live campaign changes (`schedule_selected`, `admin_cmd_schedule_confirm`,
  `admin_cmd_config_request_cancel`, `campaign_ministries_requested`,
  `campaign_end_date_requested`), portal users and their roles
  (`roles_applied`, the Users page's `user_change_requested`, the command
  line's `admin_cmd_users_confirm`, ParishSoft Ministry leaders imported as
  users (`user_leaders_import_requested`, `user_leader_imported`,
  `admin_cmd_users_import_leaders`), and the operator's one-time conversion
  of sign-in rules into users, `operator_users_upgraded`), the command
  line's parish profile changes (`admin_cmd_parish_confirm`) and Campaign
  image removals (`admin_cmd_artwork_remove`), email template deletions
  (`admin_cmd_content_confirm`), No-reminders WorkGroup changes
  (`admin_cmd_workgroup_confirm`), Campaign settings changes
  (`admin_cmd_campaign_confirm`), Share options changes
  (`admin_cmd_campaign_shares_confirm`), integration settings
  changes and removals (`admin_cmd_integration_confirm`), the command line's backup folder checks (`admin_cmd_integration_backup_probe`) and key change dismissals (`admin_cmd_integration_dismiss`), Finish switching to a new key (`admin_cmd_integration_key_finish_switching`), the command line's key replacements (`admin_cmd_integration_key_replace`), hosted files (`hosted_file_*`), stored key
  replacements
  (`secret_replacement_requested`, `secret_request_*`,
  `credential_consumer_acknowledged`) and initial setup, which is
  configuration too (`setup_started`, `setup_credential_staged`,
  `setup_credential_scrubbed`, the test email's `setup_mail_*` and Slack
  message's `setup_slack_*` steps, `setup_frozen`, `setup_expired`).
- **Family mail** (`family_mail`): each outgoing email's steps (`outbox_*`:
  created, prepared, held, sent, accepted, retried, refused, delivery unknown,
  cancelled, resend authorized), the Administrator's resolutions
  (`delivery_resolution_*`, `delivery_held_*`, `delivery_bulk_resolved`,
  `restore_hold_resolved`, `admin_cmd_delivery_*`), cleared refused addresses
  (`recipient_refusal_cleared`), skipped receipts
  (`submission_receipt_skipped`), delivery pauses and the first live send
  (`campaign_pause`, `campaign_resume`, `campaign_first_delivery`), test
  emails (`family_mail_test_*`, `campaign_mail_*`, `admin_cmd_test_sample`,
  `admin_cmd_test_families`) and the operational `delivery_unknown` and
  `mail_provider_failed`. An
  `outbox_*` record does not say whom its email was for, so staff report and
  alert emails are listed too; the page's help says so.
- **ParishSoft refreshes** (`parishsoft`): refreshes (past quick updates
  included) promoted, rejected, superseded or turned into a full reload
  (`source_promoted`, `source_rejected`, `source_superseded`,
  `source_fallback_requested`), the count-only Ministry leader changes they
  cause (`ministry_leaders_changed`, also recorded when a settings change
  changes who leads; see [Ministry
  leaders](../data/spec.md#ministry-leader-changes)), `chair_reconciled`,
  `admin_cmd_refresh_start`
  and the setup load (`setup_source_started`, `setup_source_completed`,
  `setup_source_voided`), and
  the operational failures and holds (`source_refresh_invalid`,
  `source_provider_failed`, `source_credential_failed`,
  `source_tenant_mismatch`, `source_destructive_change`,
  `source_refresh_held`).
- **Reports and downloads** (`downloads`, audit records only): reports and
  exports requested, made, cancelled or downloaded (`export_requested`,
  `export_cancelled`, `export_downloaded`, every `*_exported`,
  `weekly_manual_requested`), and the same from the command line
  (`admin_cmd_export_*`, `admin_cmd_digest_weekly_request`). Opening a report
  page (`*_viewed`) downloads nothing and is in no group.
- **Security events** (`security`, audit records only): a settings change that
  widened access or replaced the backup encryption key
  (`policy_security_event`, also in Configuration changes, the one type in two
  groups) and its acknowledgement (`security_event_acknowledged`), automation
  sessions (`automation_session_approved`, `automation_session_refused`,
  `automation_session_ended`, `automation_fresh_gate`), restored access
  (`operator_admin_recovered`, `limiter_recovered`) and the installation's
  own encryption keys (`credential_key_activated`, `credential_keys_retired`,
  `credential_cipher_batch_migrated`). Sign-in refusals stay in Sign-in
  activity, and acknowledging critical or automation notices stays in no
  group.
- **Family portal activity** (`family_portal`, audit records only): Families
  signing in and out and their sessions ending (`family_login`,
  `family_logout`, `family_session_ended`), responses (`family_submission`,
  `campaign_first_submission`), invalid links (`family_link_invalid`), Family
  links prepared or replaced (`family_tokens_*`, `family_link_rotated`,
  `family_link_population_extended`) and the portal closed for maintenance
  (`family_maintenance_started`, `family_maintenance_ended`).
- **Refused input** (`refused_input`, operational entries only): every
  refused Admin page request and `pk-admin` command (`admin_input_refused`;
  see [Admin input refusals](../operations/spec.md#admin-input-refusals)).
  Its explanation is read from the entry's closed reason code through
  `log_descriptions` and `log_details`, never from stored message text: the
  page or command, the refused fields and the reason in words, followed by
  the expected and actual values when the entry has them, such as "Create
  the campaign: Time zone refused (the campaign must use the parish's time
  zone; expected Africa/Abidjan, got America/New_York)". An entry marked
  `capped` also says that the person's further refusals at that level were
  not kept here for the rest of the window (see the
  [cap](../operations/spec.md#admin-input-refusals)). The Actor column
  names the refused user by address, read from the entry's `PortalUser` id
  like any other actor, and the same-actor icon lists that person's
  refusals.

Time limits are not in ParishSoft refreshes: `task_timed_out` and the other
timeout types are shared by every kind of background work, so a type list
cannot pick a refresh's; the type filter lists them. For the same reason
`admin_cmd_task_retry` is in no group: its record names the automation session,
not the task it retried. Types written only to
the process log, such as `go_live_refresh_held` and
`refresh_lead_window_conflict`, never reach this page and are in no group.

Every group but Family mail, ParishSoft refreshes and Refused input is
audit records only, and behaves as [Sign-in activity](#sign-in-activity)
describes: the level boxes are greyed and Apply waits for Audit record.
Refused input is the reverse: it holds operational entries only, so while
it is chosen the Audit record box is greyed out and not sent, and Apply
waits until a level is ticked, by the same `data-enabled-when` and
`data-required-when` markup; a request that bypasses the page with no level
ticked gets an empty table that says to tick one. Which groups hold
operational entries is named in `audit.log_rows.OPERATIONAL_ACTIVITIES`, and a
unit test compares it with the groups' members on the log-writer manifest.
Family mail and ParishSoft refreshes hold operational entries too, so the level boxes stay available and the Show
choices apply to their operational members as to any entry
(`data-enabled-when` and `data-required-when` list alternatives,
[conditional fields](../ui-conventions/spec.md#conditional-fields)). Every
other rule is Sign-in activity's: a typed type narrows within the group,
the same-actor icon keeps it, the choice travels in links, and the CSV and JSON Lines
downloads hold exactly the entries the page lists.

## Campaign purge

Status: not built in v1; see the status note under
[reopen and archive](#reopen-and-archive).

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
5. Record operator recovery verification for that exact backup using the
   non-secret evidence workflow below. An encrypted upload alone is not proof
   that the backup can be recovered.
6. Explain irreversible effects and retained tombstone fields.
7. Obtain fresh Google authentication.
8. Require the exact campaign name and generated short purge phrase in separate
   confirmation fields.
9. Atomically advance the request to `queued` and create one idempotent purge
   task.

The recovery-evidence page displays the purge request ID, selected backup
reference/manifest digest, and required credential/key fingerprints. An
authorized operator performs the off-host verification defined by
[secret escrow](../operations/spec.md#secret-escrow-recovery-verification).
An Admin records its successful result, operator identity, verification
completion time, escrow bundle reference/manifest digest, recovery-key
fingerprints, and the exact required credential/key set. The server binds the
record to this request, selected backup, and current relevant manifest version,
records the submitting Admin and server receipt time, validates typed fields
and matches fingerprints, and rejects missing, mismatched, future-dated, or
already expired evidence. No arbitrary attachment, secret, or decrypted bundle
is uploaded. This is an accountable operator attestation, not a claim that the
web application has independently tested off-host decryption.

The UI displays and resumes the request states defined by the
[data specification](../data/spec.md#job-outbox-audit-and-purge-records).
Inventory, backup verification, current recovery evidence, and acknowledgement
advance a `draft` request to `ready_for_confirmation`; an expired prerequisite returns it to
`draft` but invalidates only the expired artifact. The UI shows each artifact's
completion and expiration time and offers the corresponding refresh action. An
Admin may cancel through `queued` only while an atomic worker claim has not
occurred. Cancellation and terminal pre-deletion failure release the gate and
leave the archived Campaign eligible for a new request. Once deletion has begun,
the UI offers only status and safe idempotent retry actions, never cancellation
or rollback.

Inventory evidence expires 60 minutes after inventory completion, and verified
backup evidence expires 60 minutes after its latest successful verification
completion; initial backup completion includes its first verification. Both must remain
valid when the final confirmation transaction commits. Any admitted conflicting
mutation or change to the quiescence checkpoint invalidates both immediately.
Expiration preserves completed task history and requires the Admin to refresh
only the expired artifact; an inventory that expires during a long backup is
rerun after backup completion without discarding that still-current backup. The
workflow never silently substitutes an older scheduled backup. A failed backup
leaves the purge request in `draft`, shows redacted failure detail, and permits
an idempotent retry without invalidating current inventory merely because the
backup attempt failed.

Offer **Revalidate selected backup** as an asynchronous action through the same
isolated backup service, including when that backup's evidence has expired.
It verifies the same immutable, purge-request-owned off-host backup rather than
creating a new snapshot. Recheck complete object availability and cryptographic
integrity against its pinned manifest, not merely object existence. Use only
the backup service's existing credential/key authority; unavailable required
keys fail closed rather than expanding worker access.

Pin the request/evidence revision, selected backup reference/manifest digest,
snapshot instant, mutation/quiescence version, and relevant credential/key
manifest version when queuing. Recheck them under the shared request/manifest
guards on completion; a stale, cancelled, superseded, or mismatched run cannot
renew evidence. Successful verification appends a new evidence record with
server-recorded verification start/completion and expiry 60 minutes after
completion. Preserve original backup creation/completion and all prior evidence;
timestamp editing is not revalidation. Revalidation is only available before
confirmation while the request owns its preparation gate, and confirmation
cannot proceed while revalidation is nonterminal.

Revalidating an unchanged backup preserves a still-current recovery attestation
and its original independent expiry. Failed verification never renews evidence;
confirmed missing, incomplete, or corrupt backup contents make that backup
unusable and invalidate dependent recovery evidence. Show sanitized failure
details and require a new verified backup when integrity cannot be established.
Revalidation does not reset ordinary backup retention or substitute another
backup. Thus an off-host recovery check lasting over 60 minutes can be followed
by revalidation of that same backup without restarting the recovery check,
provided the recovery attestation remains current through the deletion guards.

Recovery evidence expires 60 minutes after the recorded verification completion,
not after entry into the web UI. It must remain current at final confirmation
and initial worker claim. Replacing the selected backup, changing a relevant
credential/key or escrow/recovery reference, reporting lost recovery access,
or invalidating backup evidence due to a quiescence/mutation change invalidates
the recovery attestation immediately. The Admin must perform and record a new
verification for the matching artifacts; merely editing its timestamp cannot
renew it. Ordinary expiry of inventory, backup, or recovery evidence affects
only that artifact; refreshing with a different backup invalidates dependent
recovery evidence. Show all three expirations and preserve their audit history.
After deletion has committed, evidence expiry does not interrupt the existing
resumable purge or permit rollback.

While the gate exists, every UI entry point that would create campaign-owned
work explains that purge preparation has paused the campaign and links Admins
to its status; direct requests receive the same server-side rejection. This
includes new exports, publication/reconciliation mutations, workflow-note
changes, and manual/retry task creation. Existing read-only detail and already-
generated downloads remain available during preparation under the
[campaign read guards](../data/spec.md#campaign-read-guards). Their access is recorded as a parish-
owned retained security event with only the campaign UUID/tombstone reference,
under the [shared report policy](../reports/spec.md#shared-report-behavior), so
it does not mutate or invalidate campaign-owned purge inventory.

Final confirmation and worker claim both repeat the global Testing-mode,
null-current-pointer, no-other-current-campaign, gate, quiescence, inventory,
backup, recovery-evidence, and last-mutation checks under the shared
global/Campaign/request locks and relevant manifest-version guard.
Any mismatch at worker claim performs no deletion and
enters `failed_pre_delete`; success atomically moves the request to `running`
and the Campaign to `purging`, making the Campaign inaccessible.
The UI then shows **Waiting for existing reads/downloads** with elapsed time
and the configured drain deadline. New reads are denied; already admitted
readers must finish or be terminated by their bounded request lifetime before
the first deletion batch. The worker acquires the exclusive read guard and
rechecks prerequisites after drainage. Timeout performs no deletion and uses
`failed_pre_delete`; the UI explains that data remains intact and offers a new
purge request/readiness attempt. Worker recovery repeats this barrier whenever
no deletion checkpoint exists. No new request lifecycle state is introduced.
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
