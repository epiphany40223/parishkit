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
user management with manual provenance and the persistent recovery security
event; subsequent role edits retain the ordinary Admin policy.

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
bootstrap YAML version containing only the initial exact-address Admin rule.
After migration, the latter is imported as the first applied configuration
snapshot so the initial Admin can authenticate; the wizard supersedes it with
the first complete version.

On the first Admin login, the wizard collects all required base and first-
campaign configuration before making the system configured:

1. Parish name, website URL, optional HTTPS online giving URL, IANA timezone,
   US main phone, and logo.
2. Domain/address login rules while preserving the bootstrap Admin.
3. ParishSoft API key replacement, expected organization, connectivity check,
   and a complete staged source load.
4. Google Workspace email service-account/delegated mailbox, sender/reply
   address, optional From name, and test delivery.
5. Optional Slack token/channel and test notification.
6. First campaign name, modules, dates, Ministry/fund selection, financial
   period, share options, content, mail schedules, digest schedules, and test
   recipient.
7. Exact preview/readiness summary and final confirmation.

The [parish date format](../spec.md#global-presentation-rules) is not a wizard
step: setup starts with the default US long style, and an Admin changes it
afterwards in Parish settings.

The wizard presents these as one ordered sequence of pages, defined once in
code. The Parish profile comes first, so the administrator starts by
describing their own parish. The credential pages follow, each immediately
after the public settings its staging depends on (outgoing mail and Testing
recipient for Google Workspace, Slack settings for the Slack token). Like
[secret replacement](#parish-and-integration-configuration), they require fresh
Google authentication (a sign-in less than five minutes old); an older sign-in
is offered "Confirm with Google", which keeps the setup, so the order does not
need to race that window. The source load follows, then the pages that need
the loaded catalog, then review, the email and Slack tests and the final
confirmation. Every page shows a compact progress stepper: the current step by
number and name, the count of completed steps and a slim track (each segment
names its step and status on hover; the track is hidden from assistive
technology because the list says the same), with the full ordered list (each applicable step named as completed, current, not done,
optional or not yet available with the reason) in a collapsed disclosure.
Navigation is conventional: completed steps and the first unfinished required
step are links; later unfinished steps wait for every earlier required step,
and optional steps never hold later ones back. A step counts as completed
only after its own explicit save, credential, load, accepted test or, for
the share options and the review page, an explicit review for the current
data; defaults seeded by another page never complete a step. While the
source load runs, completed steps keep their status but are not links. The
stepper is presentation only; each page still enforces its own
prerequisites. Each page has one identical row of Back (secondary) and
Save-and-continue (primary) controls (Save and continue validates, saves and
opens the next applicable page, or redisplays the page with its errors), a
short introduction, and plain-language help for every field. Revisiting a
page shows its saved values; a credential page never shows the secret but
says that one is saved (with its public scope, such as the ParishSoft
organization), lets it be kept by leaving the key empty, and explains that it
can be replaced until setup finishes and afterwards from Integrations. A page
whose prerequisites are unmet explains what is missing and links the step
that fixes it, keeping the HTTP status of the underlying refusal; closed JSON
errors remain for polling and command endpoints.

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

Review, Test email, Test Slack (only when Slack is on) and Finish setup are
ordinary steps with the same action row, not a hub: the Review page links no
later step from its body and its primary action is Continue to the email
test. On each test page the primary action sends the test until a test of the
current draft revision is accepted; then it is Continue to the next step,
with sending another test kept as a secondary button. Finish setup's primary
action is "Check readiness and finish setup", which still requires accepted
tests of the exact reviewed revision.

Wherever a form requires an acknowledgment checkbox (finishing setup, a test
that may already have arrived, chosen-Family tests, Testing cleanup,
withdrawal, refusal removal, manual reports, duplicate resends), the page
script keeps the form's primary button disabled until the box is checked.
Without the script the button is enabled and the server refuses a missing
acknowledgment as before.

Pages and emails start with built-in default text. Saving the first campaign
fills every applicable page and email slot the draft has never set, in the same
versioned save as the campaign, and a later campaign save fills only slots that
became applicable (for example, a newly enabled module). It never replaces text
the Admin saved, and it keeps a slot the Admin explicitly cleared empty. The
content page can also fill every empty applicable slot, including cleared ones,
and, after an explicit confirmation, reset every applicable slot to its default
in one versioned save; schedules that send a replaced email follow its new
revision. A fill result names each slot it kept because it holds the Admin's
own text, and the content list marks every slot as default, customized or
empty. Each default passes the normal content validation described under
[content and email templates](../data/spec.md#content-and-email-templates).

The mail schedule pages (the first-campaign step and the regular schedule
settings) start with a short guide: what each mail type is, that exactly one
initial invitation is required before final confirmation or go-live, that
reminders and the daily and weekly Admin digests are optional (at most one
digest of each kind), that submission receipts and critical alerts are sent
automatically and never scheduled, that times use the campaign time zone, and
the current campaign dates. Each schedule row shows only the fields its mail
type uses (initial invitation and reminder: date, time and email; daily
digest: time and email; weekly digest: weekday, time and email) and offers
only emails of that type; the page script clears a field it hides, and
without the script every field shows. A saved schedule's mail type is shown
but cannot change. The server reports a missing or inapplicable value on its
own field (for example a weekday on an invitation, or a date outside the
campaign with the campaign's dates); only rules between rows, such as a
second initial invitation or a reminder before it, are collection errors.
"Add another schedule" adds blank rows (with the same per-type fields) before
saving, and a row added this way can be removed again, so several schedules
save in one submission and are validated together; without the script each
save offers one blank row.

The staged ParishSoft load provides the Ministries/funds needed by later steps.
Starting that load fixes the Parish timezone for this setup attempt, so the
source catalog and first campaign retain the same civil-date interpretation.
The Parish step explains this restriction and keeps other Parish fields editable.
Changing the timezone then requires cancelling and starting a new setup attempt;
it never silently reinterprets an existing source result.
Wizard progress may be kept in the authenticated session and temporary staging
tables/files, but no staged configuration is active until installer
finalization. While the bootstrap Admin remains on the correlated source-load
progress page, bounded authenticated polling renews only idle expiry under the
[session-policy exception](../architecture/spec.md#identity-and-session-security).
Wizard pages do not list the time limits. The shared
[inactivity dialog](../architecture/spec.md#identity-and-session-security)
warns five minutes before sign-out and lets the Admin stay signed in, which
also keeps the setup attempt. The progress page names the three parts of the load
(download, save, check) and, during the download, each ParishSoft collection
as done, in progress (Ministry rosters as "N of M") or waiting, with its
record count; the saved-record bar appears once saving starts. The worker
encodes one step per finished collection in the task's two monotonic
progress counters and the page decodes the task's append-only event history,
so this needs no schema change. The page also shows the elapsed time and the
loading worker's most recent heartbeat. On success it shows a prominent
result with Continue to the next wizard page; on failure it says what to do
next.

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
configuration installer, and then commits the promoted source snapshot, Family
codes, Testing mode, configured marker, and one redacted setup audit event. The
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
confirmation. It polls a passive status every 15 seconds while visible,
reloading when a step changes and showing a Continue link to the Admin home once
setup completes. A failed step explains what to do; when a credential cannot
start because the original Google sign-in is more than five minutes old, the
page offers the same-session step-up, which keeps the setup. Before the marker,
normal routes remain unconfigured/fail-closed and cancel cleanup removes sealed
staging and any wizard-only files without exposing a partial product setup.
Cancellation after YAML selection uses the
[initial-setup abort journal](../data/spec.md#parish-and-integrations), never a
rollback of applied configuration. Final database activation and the configured
marker share one transaction so cancellation cannot fall between those commits.

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

In Production mode, while the web process has debug logging on
(`PARISHKIT_DEBUG_LOGGING=1`), every Admin page, for every role, shows a
prominent non-dismissible error banner saying in plain language that debug
logging must be off in Production, because debug logs can hold personal data,
and that the operator turns it off by recreating the application containers
with the variable `0` or unset. It is a warning only: no process refuses to
start with the switch on.

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
never hides an entry the Administrator was not shown. Any other CRITICAL
entry, including one recorded after the page was shown or one a long-running
transaction commits after the acknowledgement, brings the banner back. The
banner costs the Admin page one query, shared with the delivery warning count. It is distinct from security-event acknowledgement, which is
per recipient.

### Admin navigation

Admin pages share one layout: a sidebar menu beside the page, and a breadcrumb
trail above the page heading. Both come from a single declarative registry in
`accounts/admin_navigation.py`, keyed by URL name, that gives each Admin page a
section, a parent page and a label, so the menu and the trails cannot drift and
no template hand-writes a breadcrumb.

The sidebar starts with Home, then these sections, each listing the entries the
viewer may open:

- **Campaign**: Campaign settings (or New campaign), Campaign images, Pages and emails, Mail
  schedules, Share options (financial campaigns), Member talents (Ministry
  campaigns), Go-live readiness (drafts), Family email progress, Family
  email sends and Delivery controls (Production).
- **Reports**: Campaign reports, Ministry reports, Family directory (Family
  codes and, with its mailing columns, postal outreach) and the Manual
  information report. The campaign reports page also
  links the [Talents and limitations](../reports/spec.md#talents-and-limitations)
  report.
- **Parish and integrations**: Parish settings, Parish logos, Hosted files,
  Integrations, ParishSoft refresh and Ministry activity.
- **Users**: Portal users.
- **System**: Background work, Outgoing mail, Families on the form now and
  System logs.

The menu always ends with **Sign out**, set apart from the sections, on every
signed-in Admin page (including during initial setup). It is a POST form with
the Admin CSRF token to the logout route, styled like a menu entry, because
logout is CSRF-protected. Pages carry no product footer.

Entries use the same capability checks as the pages they open, and a section
with no visible entry is omitted; the menu is not the security boundary. The
entry for the current page, or for the nearest ancestor page listed in the
menu, is marked `aria-current="page"` and its section is highlighted. Each
section title is a level-2 heading that names its list of entries, styled as a
small muted label (not like a link) with a divider above every section after
the first and the section's entries indented beneath it. On wide
screens the sidebar is a sticky column; on narrow screens it collapses behind a
Menu disclosure that works without script.

The breadcrumb trail runs Home › section › each ancestor page › the current
page, for example Home › Campaign › Pages and emails › Initial invitation. A
view may name the current page more specifically (the email being edited, the
integration). Ancestor links reuse the current request's resolved route
arguments. Home shows no trail. Building the menu and trail runs no queries.
Until initial setup completes, the only menu entry is the setup wizard, which
has its own stepper. Every Admin route is either a registered page or listed
as a non-page (form actions, downloads, images, status fragments, sign-in and
the setup wizard), and a test requires every new route to be classified.

A view may place its page more precisely than its route can, so deep steps of
multi-step flows keep their context: it may name a different parent, supply
route arguments an ancestor link needs, and name an ancestor specifically.
Preview and test email sits under the email revision it sends (Home › Campaign
› Pages and emails › Initial invitation › Preview and test email), with Send to
chosen Families below it. An export's status page sits under the report it
came from. A configuration change's status page sits under the settings page
the change was confirmed on: confirming remembers that page in the signed-in
session (never in the URL), and the status page shows its trail and a "Return
to" link to it. Without that memory, as in another sign-in, the page stands
under Home. A key's replacement status and its Finish switching page sit under
the integration the key belongs to, never under each other, because only the
Administrator who saved a key may read its status. Some pages are named in
trails but never linked, and "Return to" skips them: those that only answer a
POST (the login rule, chair suggestion, chair review and assignment reviews);
one-time reviews that refuse once their change is confirmed (New campaign,
Copy campaign, and the campaign image and logo reviews); and Finish switching,
which still opens afterward but needs a fresh Google sign-in and has nothing
left to do. A sidebar page is linked in a trail or "Return to" only while the
viewer's sidebar offers that same page, so a page the sidebar hides because it
would now refuse (Share options once the campaign is locked, Campaign images
for a campaign that is no longer current) is named without a link.

Multi-step flows also show a step indicator under the trail: a numbered list
with the current step marked `aria-current="step"` and each step's state in
text. It is orientation only and links nothing, so it cannot skip a review or
confirmation. The flows are: making a settings change (Make changes, Review,
Apply) on every settings editor (campaign settings, a live campaign's
Ministries, Copy campaign, pages and emails, mail schedules, share options,
member talents, campaign images, Parish settings, Parish logos, each
integration, Ministry activity and Finish switching), the reviews started on Portal users (login rules, chair
suggestions, chair reviews and assignments) and every change's status page;
going live (Check readiness, Testing cleanup, Family links, Confirm
Production, Activation); sending to chosen Families (Choose Families, Review,
Send and follow); and report exports (Choose report, Prepare file, Download).
A locked campaign's read-only settings page is not in a flow. The reviews
started on Portal users show only Review and Apply as current: Portal users
itself is a list, not step 1, and a refused review shows its error page
rather than going back to a form. An error page never shows a step or a
placed trail. Placement and steps are presentation only and grant nothing.

### Admin tables

Admin tables share one component, so paging, sorting, selection and styling
behave the same everywhere. A long table has a row navigator above and below
it: the rows shown and "Page N of M", a rows-per-page choice (25, 50, 100, 250
or All, where the table's source allows it), Previous and Next, and a
page-number field. A page number past the end shows the last page.

Column headings sort the table on the server. Each sortable heading is a
control that sorts by that column; choosing the sorted column again reverses
it, and times and counts sort newest or largest first on the first choice.
The sorted heading carries `aria-sort`, a small arrow marks it, and each
control's accessible name says which direction it will choose. A table accepts
only its own whitelisted sort tokens, each mapped to server-owned ordering, and
appends a unique tiebreak so rows with equal values never move between pages.
Rows with no value in the sorted column (a task without a heartbeat, a user
who never signed in) sort last in either direction.
A new sort starts again at page 1.

Page, size and sort are query parameters (`page`, `size`, `sort`), optionally
prefixed so two tables on one page keep their own place, and every navigator
link, heading and filter form keeps the page's filters and the others' choices.
Reports whose filters are private (the Family directory, System logs and the
campaign reports) keep them in POST state: their navigator and headings are
small CSRF-protected forms that carry the filters as hidden fields, so no
private value reaches a URL. A report whose rows an installed SQL selection
orders offers that selection's sort orders on the columns they order and its
page sizes; its other columns do not sort, since the schema owns those
orders. In v1 that covers:

- the Family directory (Family and DUID, 50 rows; Family code would need
  every code decrypted per view);
- Financial stewardship detail (Family, Annual pledge and latest response);
- the Additional information queue (Family and Submitted);
- the Ministry report (Ministry; Member and Submitted in one Ministry's view);
- the Ministry follow-up queue (Request, Member and Ministry).

Extending those vocabularies is a schema change. Columns that are only
controls (selection, actions, previews) never sort. Two short before/after
lists of pending setting changes (credential selection and integration
preview), the campaign mail test's at most ten reviewed Families, and link
preparation history (panels, not columns) have no sortable columns.

A table's navigators and rows sit in one region with a stable id (the table's
anchor, derived from its parameter prefix so two tables on one page differ),
and every heading and navigator control names that id as its URL fragment.
With script, choosing a heading, Previous, Next, a page number or a
rows-per-page value re-sorts or re-pages the table in place (#478): the
browser fetches the page the control would have loaded, requested as the
control would have requested it (a GET table's link or query, a POST table's
CSRF form with its private filters), and replaces that region, every other
table region on the page and the values of the page's filter and export
controls from the fetched page, so no control is left carrying a choice the
table no longer shows. The reader keeps their scroll position, rows still
shown keep their selection, focus returns to the chosen heading or control,
the heading's `aria-sort` and a polite live region announce the new order or
the rows now shown, and a GET table's choice replaces the address so reload,
bookmarks and returning to the page keep it; a POST table's address never
changes. Filter changes themselves still load the page in full (#484). Without
script, or when the fetch fails or returns another page (a sign-in), the
ordinary page load happens and its fragment lands on the table rather than
at the top. Portal users, whose domain and address tables carry role forms
bound once at load, and the link preparation history keep only the fragment
and always load in full.

Lists read straight from a growing database table page on the server with one
extra row to learn whether a next page exists, and count matching rows only up
to 10,000. Past that the navigator says "more than 10,000", omits the page
count and keeps paging by the extra row. They offer 25, 50 or 100 rows. On the
largest tables only indexed columns sort, so a page view never sorts or counts
a whole log.

A table with bulk actions has a selection column. Its header checkbox and a
Select all button choose every row on the current page; the bar above the
table shows how many rows are selected and enables its action buttons only
while at least one is. The server validates every submitted selection, so the
controls also work without script.

### Page help

Admin pages put the task first and keep explanation one deliberate click
away, without removing any of it. A page leads with its heading, at most a
short introduction, safety notices (such as Testing mode) and the form.
Longer explanation of how the page behaves (how a credential is kept, how
mail schedules work, the placeholder reference) sits in an "About this page"
panel: a disclosure that starts open, and that each browser remembers closed,
per page type, once an Admin closes it. Only that open or closed choice is
stored in the browser; without script or browser storage the panel stays
open. Help under a field is a short hint; longer field explanations belong in
the About panel or a click-to-open field tip, never in hover-only tooltips,
which touch and keyboard users cannot reach.

Field help longer than about one line opens from an "i" button beside the
field's label (the shared toggletip). Only a one-line hint stays visible under
the label, for fields whose format or rule is needed every time (an example
address, "one per line", a key's paste rule), and a warning that blocks the
field, such as having no emails to schedule, always stays visible. The field
remains described by its full help, so screen readers announce it without
opening the tip. Checkbox help stays beside the box.

Internal identifiers and bookkeeping fields that matter only for
troubleshooting (delivery, refusal and test references, a retained
configuration version, an export's requester reference and data-load
numbers, and the log cross-link identifiers) sit in a "Technical details"
disclosure, closed by default, instead of in the page's main text. Nothing
is removed, and exports keep every field. An empty list says what to do
next rather than only that it is empty.

Two template tests guard these rules: one fails when a paragraph shown
without a click holds a message longer than about two sentences (50 words),
and one fails when a label for an internal identifier or worker field (such
as a heartbeat, lease, data-load number or request ID) appears outside a
Technical details disclosure. Each keeps a short, reviewed list of
exceptions, and the long-paragraph exceptions must shrink as their pages are
converted.

## Background indicators

Admins have two always-visible indicators:

- **Families on the form now**: count of Family sessions with a heartbeat
  within the last 90 seconds, that is, Families with the form open in their
  browser; a Family that signed in but closed the form is not counted. Detail
  lists the Family name as on the Family directory (surname, then the
  active heads of household, e.g. "Squyres, Jeff and Tracy"), DUID, start
  time, last activity, and form section; it never shows answers or
  credentials.
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
When the Admin session has ended, an indicator poll is refused and the page
stops polling rather than repeating the refused request. Any Admin request made
without a current session is refused with a plain explanation that the sign-in
has ended and a link to sign in again, never a missing-capability message.
The distinct Family activity-keepalive behavior is defined by the
[session policy](../architecture/spec.md#identity-and-session-security); it does
not affect presence semantics or carry form answers.

Every Admin page that follows background work (configuration changes,
credential replacement, Testing cleanup, Family link preparation, chosen-Family
tests, background task details, integration key changes, report exports and
[Family email progress](#family-email-progress))
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
a control the Admin is using and never re-sends a form; its manual refresh
link remains for browsers without JavaScript.

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

Outgoing email settings (the setup mail step and the post-setup outgoing
email integration) include an optional From name: a single line of at most 100
characters without control characters, `<`, `>`, `@`, quotes or backslashes.
Every outgoing message's From header shows it with the From address, quoted
and RFC 2047-encoded as needed; when it is blank the Parish profile name is
used. It is presentation only: routing, test scope and outbox checks keep
comparing the bare address, and changing it never voids a staged credential
test.

The Parish IANA timezone is the default for non-campaign presentation and newly
created campaign drafts. Editing it does not mutate an existing Campaign's
timezone, resolved boundaries, schedules, or historical report buckets. The UI
shows this scope explicitly and links to the separately editable draft campaign
timezone when one exists.

Logo management previews every generated size. Uploads remain staged until the
YAML version referencing their immutable branding version is applied;
historical email/page previews retain their campaign version.

Campaign images (Campaign navigation) hold the current campaign's theme
artwork: five optional slots, a wide banner and one small icon each for the
Family welcome, Member, financial and closing pages. A new campaign starts with
none. Each upload is normalized like a logo (a banner fitted within 1,024
pixels, an icon within 256), previewed, and used only once its configuration
request is applied, the same staging and cleanup rules as logos. Removing a
slot's image is its own reviewed change; a slot without an image shows nothing.
Artwork is presentation, not structure, so it stays editable while the campaign
is live. Each Family email's editor (initial, reminder and confirmation) has a
"Show the campaign banner at the top of this email" checkbox, on by default.

Hosted files (Parish and integrations) is the Administrator-only library of
PDF, Office and image files that page and email content links or shows
with `{{ file.<slug> }}`: upload, placeholder copy, where each file is used,
and single or multi-select deletion that is refused while a file is in use.
The page and its rules are defined by the
[hosted files specification](../hosted-files/spec.md#admin-page).

Integration pages expose connection status, last check, safe fingerprint, and
Replace/Test actions. Secret replacement requires fresh Google authentication.
The UI seals the submitted value to its target-specific installer, shows
staged/testing/installing/consumer-acknowledged progress, and never redisplays
it. Failure or expiry destroys sealed staging and leaves the old working
credential installed. Slack is optional; its token and channel must be
supplied/removed together. Non-secret integration setting changes use the YAML
configuration-request path rather than the credential installer. The ParishSoft
organization ID can change only until the first ParishSoft data load. After
that the page shows it read-only and refuses a different value, with or without
a new key, in plain language: every refresh must read the organization whose
data is loaded, and a refresh for another organization is refused as a tenant
mismatch.

Page text describes what the Admin sees happen, never the machinery: no
configuration files, installers, fingerprints or preview lifetimes. Signed
previews still expire after fifteen minutes; confirming an expired or
out-of-date preview is a correctable refusal ("This preview is out of date")
that links back to the page that builds it, so one click starts a fresh review
and nothing is saved in between. A finished key change (updated, failed,
cancelled or expired) shows on its integration's page for one hour, and any
Admin may dismiss it sooner for every Admin (the dismissal is an audit event);
one that is installed but not selected (its automatic switch failed) is an
error, not news: it says what is stopped (ParishSoft refreshes, email or Slack
alerts) and offers **Finish switching to the new key**, and it keeps showing
until it is resolved; the Admin home page shows it too. Mail meanwhile waits
without spending attempts; see the
[credential installer guide](../../../guides/stewardship-credential-installers.md#replacing-an-integration-key-from-the-web).
The history
stays on the change's details page and in the audit log. The ParishSoft daily
refresh time is shown only for the once-a-day frequency; for hourly and
15-minute refreshes the stored time is kept unchanged and is not a change to
review.

**Off-site backups (Google Drive)** is an optional integration with no key of
its own: its one setting is a Google Drive folder link, stored in canonical
`https://drive.google.com/drive/folders/<id>` form, and it is added, changed
and removed through the same configuration-request path. It requires the
Google Workspace mail integration, whose delegated user and key the copies
use. **Test access** queues a check that the Google Workspace credential
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
  security event in the same activation transaction, like a widened
  sign-in rule: it names the Administrator, the time and the key IDs before
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

### Ministry activity management

Admins can mark a Ministry inactive or reactivate it through an Admin web
screen. The screen lists the current Ministry catalog with name, DUID, local
active/inactive state and campaign inclusion, and supports searching and
filtering by name, DUID and status, in a shared [Admin table](#admin-tables).
Admins select one or more Ministries and activate or inactivate them together.
Saves use the ordinary versioned YAML configuration-request workflow, with
optimistic concurrency, an impact preview and audit; one bulk change is one
request of at most 100 Ministries, applied atomically, and selected Ministries
already in the requested state are listed and left alone. A pending save is
not presented as applied.

Activity and campaign inclusion are separate settings, and this screen changes
only activity. It says so in one sentence and links its "In current campaign"
column to Campaign settings → Ministry selections. When a bulk preview includes
Ministries that are not in the current campaign, the preview names them, says
that activation does not add them to the campaign and links to the same place.

ParishSoft's Ministry catalog does not supply a reliable active/inactive flag.
Catalog entries default to locally active unless an Admin has marked them
inactive. The parish-wide override is keyed by ParishSoft organization and
Ministry DUID, not its name or a campaign. Refreshes, renames, new campaigns and
temporary disappearance/reappearance in the catalog never erase an override.
A Ministry absent from the current catalog is unavailable regardless of its
local setting. Reactivation never creates a source Ministry or changes its
upstream roster.

Inactive Ministries are not shown to parishioners: they are omitted from both
current-membership displays and join/leave controls. Visibility requires current
catalog presence, local active status and inclusion in the campaign's selected
Ministry set. Staff/Admin records, source rosters, past submissions and existing
follow-up requests remain intact; hiding a Ministry is not a request to leave
it or to withdraw earlier interest. The server enforces the same eligibility
as the UI and rechecks it at submission. A stale form cannot create new actions
for an inactive Ministry, and omission of hidden fields never cancels retained
requests. Use the ordinary changed-baseline reconfirmation flow for stale forms.

A Ministry's name never affects its visibility. ParishSoft names are stored as
loaded and repaired only where they are shown: on the Family form, this screen,
Campaign settings and first-campaign setup. A name the form can already show
is used exactly as before, only trimmed, so valid labels and Family form
digests never change because of this rule. An unusable name is repaired:
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
Campaign settings and first-campaign setup follow the same cleaning, with
"Fund" and its DUID as the fallback, and are not logged.

Local activity may be changed during a campaign without editing its
Ministry-selection set. Reactivating an excluded Ministry does not add it to
that set; see
[Changing a live campaign's Ministries](#changing-a-live-campaigns-ministries). Historical administrative views retain their recorded inputs;
current parishioner pages and previews use the applied visibility policy.

For [Chairperson suggestions and assignments](#chairperson-suggestions-and-assignments),
an active Ministry means one present in the current catalog and locally active.
Applying an activity change reevaluates suggestions and seeded assignment
overlays against the current source in the configuration-activation transaction,
using the same suspension/reactivation and review rules as source promotion.
It does not delete authoritative grants or alter manual Ministry assignments,
Staff roles or Admin roles. The impact preview identifies affected seeded
assignments before confirmation.

### ParishSoft Ministry catalog changes

Ministries are keyed by ParishSoft DUID, and only a full refresh re-reads the
Ministry catalog; the 15-minute updates copy it unchanged (see the refresh
cadence in
[background processing](../background-processing/spec.md)). The system never
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
differences records nothing. A 15-minute update copies its base's catalog, so
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
rename whose new name starts with `X-` and whose old name did not is labeled as
possibly retired: some parishes rename a retired Ministry that way. It is a
display hint only and changes no behavior. The panel links to Ministry
activity, where an Administrator can mark a Ministry inactive.

While the current campaign is in draft, scheduled or open, entries for
Ministries in its selections are marked "in the current campaign", the panel
also links to that campaign's Ministry selections, and it lists, independent of
the seven days, the campaign's selected Ministries that are missing from the
current catalog (Families cannot see them) or whose current name starts with
`X-`. A closed or archived campaign shows Families nothing, so none of this
applies to it. The whole panel costs the home page one query.

No System logs entry is written: the closed operational-event list is enforced
in SQL, so a new event would be a schema change. A durable
`ministry_catalog_changed` event can be added after the schema freeze.
ParishSoft's own Ministry active/inactive flag is not compared. It is not
reliable enough to decide visibility (see above), but a Ministry turned
inactive there may be a stronger retirement signal than the `X-` hint; adding
it to the notice is tracked for after launch in
[#342](https://github.com/epiphany40223/parishkit/issues/342).

#### Changing a live campaign's Ministries

A live campaign's structural settings are locked (see
[Campaign configuration](#campaign-configuration)), with one reviewed
exemption: while the current campaign is scheduled or active, an
Administrator may change its Ministry selections. Campaign settings links a
live campaign to its own "Change campaign Ministries" page, which offers only
the Ministry list and goes through the usual edit, review, apply flow as an
ordinary configuration request. A closed or archived campaign's selections
never change.

- **Hide** a Ministry without removing it: mark it inactive on Ministry
  activity. It disappears from every Family form; nothing else changes.
- **Remove** takes a DUID out of the selections. Answers already given are
  never deleted or rewritten: submissions keep their recorded inputs, join and
  stop requests stay in Ministry follow-up until staff close them, and the
  Ministry report, follow-up queue and follow-up packets keep showing them,
  marked "No longer in this campaign", while it has a request that was not
  later withdrawn or replaced. A Family who opens the form again no
  longer sees that Ministry, and resubmitting does not withdraw its requests.
  Adding the DUID back shows those answers unmarked again, including on the
  Family form.
- **Add** accepts only a DUID that is in the current ParishSoft catalog and
  locally active. Families see it on their next visit. Families who already
  submitted are not asked again and get no mail.

The review lists each added and removed Ministry, with each one's submitted
answers (current join and stop requests) and open follow-up requests, and the
number of Families with a form open now. Every open form lists every offered
Ministry, so all of those Families get the usual changed-baseline review before
they can submit; the review cannot be narrowed to fewer Families. No mail is
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
catalog, locally active and in a campaign with the Ministry module. The
configuration installer holds no source grants, so it reads catalog presence
only through the definer function `stewardship_ministry_catalog_v1()`, which
returns DUIDs and nothing else and which only that login may execute. A
removal reads no catalog. Activation repeats the addition check under the lock
source promotion takes; if a refresh dropped or an Administrator inactivated
the Ministry after the request was checked, the request fails and the previous
settings stay in effect, so the Administrator reviews the change again.

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
while the global mode is Production or any nonterminal PurgeRequest exists.
Successfully completed purge tombstones do not block a new draft. The Admin
must finish reconciliation,
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
- editable/reorderable Member talents (see [Member talents](#member-talents));
- initial and repeatable reminder date/time, subject, and templates;
- daily/weekly digest local schedules;
- additional-information toggle;
- named content slots with WYSIWYG/plain-text views, each of which can start
  an empty slot from built-in, parish-neutral default text or reset a saved
  slot to it (the editor is only pre-filled; nothing changes until the normal
  save or preview and apply), with the content list marking each slot as
  default, customized or empty. A new campaign that is not a clone starts
  with the default text for every applicable slot, added in its creation
  request; a clone copies its source's content instead. Plain-text controls
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
campaign never unlocks them. The one exception is the Ministry selections
(see
[Changing a live campaign's Ministries](#changing-a-live-campaigns-ministries)). The campaign timezone is initialized from the
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

### Member talents

The talent checkboxes on each Member's ministry page (see
[Family portal](../parishioner-portal/spec.md#talents-and-cannot-participate))
are a campaign list edited like the share options: ordered rows with stable
identities, explicit deletion, a free-text flag, a signed preview and confirm.
A campaign that never edited its talents shows the built-in defaults, and the
first save stores them with their fixed identities, so earlier answers still
match; an emptied list stays empty. Talents are structural settings (above) and
are offered only when Ministry stewardship is enabled.

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
existing authenticated pages explain that go-live is in progress. The same
transaction invalidates the rehearsal epoch and its sessions; the cleanup
inventory includes rehearsal credential detail under the
[credential lifecycle](../architecture/spec.md#family-credential-security).
Readiness/final confirmation verify that invalidation and completed credential
cleanup without changing stable Production Family codes. Source
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
the campaign date interval. It renders a fictional safe sample, routes only to
the configured Testing recipient, does not create or satisfy a scheduled
Family-mail occurrence, and does not bypass Family portal date gates. Its
successful provider delivery satisfies the test-Family-mailing readiness check,
making the `scheduled` state reachable before the start date.

For staff validation, a Testing-mode draft campaign also offers a link from the
test send, **Send this email to chosen real Families (Testing recipient only)**,
to the page **Send this email to chosen Families**: a freshly authenticated
Administrator enters up to ten Family IDs, reviews each Family's eligibility
(each reviewed row names the Family as the Family directory does, the
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
status while any is still on its way.

Every other fresh-authentication action refused from a submitted form says that
nothing was done or sent and offers the same step-up; forms that carry a secret,
such as a replacement key, keep nothing and ask for it again.

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

Readiness for a resulting `scheduled` or `active` campaign includes fresh
email-link preparation for every currently eligible Family. Reuse the
resumable public-key-only preparation service from the
[reopen workflow](#reopen-and-archive), bound to this restore instance and its
current source/configuration/key versions. The UI shows progress, retry, and
the warning that every pre-restore email link will stop working; stable manual
Family codes are unchanged. Preparation never clears maintenance admission.

Final release rechecks the preparation manifest and restore instance, and
atomically selects the new generation with the state/mode/hold changes below.
Stale or incomplete preparation leaves the gate closed. If the commit-time
result is instead `closed`, no generation is activated and staged secrets are
scrubbed; a later reopen prepares its own generation. Other non-live resulting
states do not activate restored tokens. Replacement tokens alone create no
mail or resend authorization and do not satisfy or release delivery holds.
Queued credential-bearing mail follows the
[restore dispatch rule](../background-processing/spec.md#reopen-token-preparation)
so no restored sealed substitution can reintroduce an old link.

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

These are resulting states, not additional lifecycle edges. An overdue
`scheduled` campaign reaches `closed` by applying its start and close boundaries
in order within the release transaction, using the shared
[boundary policy](../background-processing/spec.md#campaign-lifecycle-boundaries).
Both transitions are audited; the maintenance gate remains closed throughout,
so the intermediate active state admits no Family access or mail.

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
skipped and its pending outbox cancelled under the ordinary close policy. A
held invitation or reminder with no delivery task left (usually because its
preparation failed) has no worker to apply that policy and cannot be retried
after close, so every resolution below cancels it the same way and counts it
toward clearing the pause; an uncertain provider outcome is never cancelled
this way and still blocks the clear.
Accepted receipts and completed-day Admin digests remain held. Before archive,
an Admin must use a freshly authenticated **Resolve held messages** workflow to
release selected non-Family-access message types after a provider check or
cancel them with exact counts and a reason. This does not reopen Family access
or campaign schedules. Once every held or uncertain row is resolved, the same
atomic workflow clears the durable pause control; closed campaigns do not use
the ordinary Resume action. Reopen readiness is blocked until the prior pause
and held-message state is resolved.

### Family email progress

Sending the invitations, and later each reminder, to every Family is a long
background operation (about 1,100 Families take 20 to 25 minutes). The
read-only **Family email progress** page (Campaign section, linked from
Delivery controls and Outgoing mail) follows it live
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
and how many emails were sent, failed, uncertain and not sent. It links to
Outgoing mail.

**Counts while a send is in progress.** Each Family's email is counted by
its outbox message state, or, before preparation, by its occurrence:

- **Sent**: delivered (the mail service accepted it).
- **Failed**: permanent failure, or a preparation that failed. Failed
  emails link to Outgoing mail; preparation failures, which have no email
  there, link to the failed preparation tasks in Background work.
- **Uncertain**: delivery unknown, linked to Outgoing mail.
- **Remaining**: pending, waiting to retry or submitting, or not yet
  prepared, including the Families planning still owes. The part not yet
  prepared is shown beside it, in brackets.
- **Held: invitation failed or uncertain** (reminders only, shown when
  non-zero): a reminder not yet prepared for a Family whose newest invitation
  failed or is uncertain. Planning holds such a reminder until the invitation
  is resolved (`initial_unfulfilled` or `delivery_unresolved`), so it is not
  remaining and the send can still finish. This is a narrower test than
  planning's own: a reminder held for rarer reasons (a newest invitation
  skipped or coalesced without delivery, an unreviewed restore hold, or
  another uncertain email for the Family) still counts as remaining, and one
  sent because a restore assumed its failed invitation delivered counts as
  held until it is prepared. These need a restore or a deliverability edge
  case, never the launch invitation, and are an accepted v1 limit.
- **Couldn't be emailed**: skipped or cancelled because the Family has no
  deliverable address (`no_deliverable_recipient`) or is no longer eligible
  (`family_ineligible`).
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
revision yet. Planning creates the send's emails only while its revision is
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

The read-only **Family email sends** page (Campaign section, linked from
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
and across its edits, and the numbers match the order on Mail schedules. A
removed reminder has no current due time and is shown without a number. A Production send from before the campaign returned to Testing is
marked as such.

**What each row shows.** The kind (Invitation or Reminder N), the mode,
the scheduled time, when the first email was prepared, when the latest one
got its result, and the time between them; then the total and the counts
Sent, Failed, Uncertain, Not needed, Couldn't be emailed and Held. Each row
is counted by the progress page's own code, so the two pages always agree;
see [Family email progress](#family-email-progress) for what each count and
the total mean. A row also shows **Cancelled**: the send's emails that were
prepared and then cancelled before sending (by a schedule edit, a response
or the close). Those Families are also counted under Not needed or Couldn't
be emailed, so Cancelled is not added to anything. A send still in
progress says so. Only the send the progress page is showing links to it
(the page shows one send: the in-progress send that fell due most recently);
another send still in progress, such as an invitation still sending when a
reminder falls due, says In progress without the link.

**Links to Outgoing mail.** Sent, Failed, Uncertain and Cancelled link to
Outgoing mail filtered to that send and the matching email state
(delivered, failed delivery, delivery unknown and cancelled). The send
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

### Family portal maintenance

An Administrator may close the Family portal for maintenance from System →
Family portal availability, for example while fixing a data problem, and
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

## Portal user management

The Admin user page contains sorted domain and exact-address tables. Rows show
normalized value, effective roles, source, last login, and warnings. Role
checkbox changes autosave through a `ConfigurationChangeRequest` with a
transient Applying/Applied/error indicator; each request uses the expected
active YAML digest to prevent lost updates. A security-policy change is
effective only when the installer atomically activates its matching normalized
snapshot, never from an independently edited role row.

Each open user-management page serializes its configuration mutations through
one in-memory queue shared by both role tables and that page's other YAML-backed
rule/assignment actions. At most one request from that queue may be nonterminal.
Further checkbox changes remain interactive but visibly **Queued — not saved**;
store ordered logical intents (stable target, role, desired checked value), not
copies of the whole configuration or toggle commands. A queued change to an
in-flight checkbox does not mutate the submitted request. After that request
reaches `applied`, adopt its returned applied-version ID/digest and authoritative
values, then form the next minimal patch from the remaining intent. Do not
advance on HTTP acceptance, `prepared`, or `yaml_activated`. Show Applied only
for confirmed values; newer queued intent remains visibly distinct.

Each submitted intent has a client-generated idempotency key bound to its actor
and immutable payload/base digest. A lost response resumes status lookup or
retries that same request key; it never creates a second grant, audit event, or
security notification. Until the outcome is known, pause further dispatch and
show an uncertain/reconnecting state. Installer failure, cancellation, validation
failure, or stale-digest conflict also pauses the queue rather than cascading
later failures or silently retrying a changed payload.

For a genuine conflict, fetch the latest authorized configuration and show the
current values beside the remaining desired changes. Preserve unsaved intents
in the page for review; do not automatically rebase them onto another Admin's
edits. The Admin can discard or select intents to retry as new requests against
the refreshed digest. Deleted targets and newly invalid choices need explicit
resolution; retry never recreates a deleted rule implicitly. Changes from
another tab follow the same conflict path. Current-Admin, CSRF, last-Admin,
and provenance guards still apply to every request and activation. Lost access
stops dispatch and clears restricted page data; session expiry requires normal
login before any retry, not a role-change-specific reauthentication step.

Warn before leaving the page with unsent intents; they are not saved durably
and are discarded on page teardown. An already accepted request continues
durably and is reconciled by its request ID/status when the page is revisited;
do not replay a former browser queue. Inline conflict resolution is exceptional
error recovery, not a new confirmation dialog for ordinary role changes.

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

The user/assignment UI shows rule and role-grant provenance from the
[authorization data model](../data/spec.md#administration-user-and-policy),
including whether a Ministry-leader grant is independently configured or
subject to chair-seed suppression. It never infers origin from the current
checkboxes. A **Keep role independently** action for a seeded Ministry-leader
grant explicitly records a manual origin through the ordinary role-change
configuration request, with the same no-reauthentication policy and audit.
It does not create a Ministry assignment or broaden row scope. Unrelated
autosaves preserve provenance, and a role removal removes all its grant origins.

An assignments editor supports manual additions/removals through the same YAML
configuration-request path. Losing a current Chairperson role immediately
suspends a `chair-seed` assignment as derived runtime state during source
promotion, removes its Ministry row scope on the next request, and creates a
persistent Admin review task/notification. Existing sessions are not trusted to
retain cached scope. The runtime authorization overlay applies the data model's
explicit rule/grant provenance predicate without rewriting its applied YAML
rule or changing Staff, Admin,
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

Admins may request an immediate full refresh from a confirmation page (the
ParishSoft refresh menu entry), or with the "Run a full refresh now" button on
the ParishSoft settings page and in the Admin home page's refresh notice (same
capability and CSRF rules). The
action inserts a durable task and returns immediately to its status page. If a
poll is running, no concurrent poll starts; one manual full refresh may be
queued to follow it. Repeated clicks return/link to the existing queued run.

The refresh page and the "Run a full refresh now" button say that a full
refresh re-reads everything (including Ministry rosters and giving) and usually
takes a few minutes, while the automatic 15-minute updates read only the
Families ParishSoft reports as changed. A refresh run's background task page,
and its row in the background-work list, name the run as a "Full refresh" or a
"15-minute update" from its request kind, and describe the current phase in
words ("Downloading from ParishSoft", "Saving the downloaded records",
"Checking the new data", "Making the new data current"). The download phase has
no count, so while a refresh is downloading the page says so instead of a bare
wait message; other steps without a count keep the general message. Counts are
labeled as records checked, since every refresh places every record into a
complete new copy and reuses unchanged records. Once a refresh has succeeded,
its task page says how many records it checked and how many changed, by
collection (for example, "Checked 30,639 records from ParishSoft; 12 changed
(3 Families, 9 contacts)."); a refresh with no ParishSoft changes says 0
changed. A record changed when its identity was added, removed or has a
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
same retry explanation and a phase in words.

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
  indicators: one matched, self-hosted icon set (a blue "i", an amber warning
  triangle, a red cross and a dark red stop sign for CRITICAL) beside the
  level word, with the icon decorative so meaning never depends on color, and
  CRITICAL rows highlighted;
- a compact filter bar: levels, source, type and date range fit in one or two
  rows at desktop width, and the actor, correlation and campaign identifier
  filters are folded under "Filter by identifier" until one is used. The
  page's longer explanation is in its "About this page" panel;
- default exclusion of DEBUG;
- operational/audit source, action/type, campaign, entity, actor, task/request
  correlation, text, date range, and level filters;
- full-text search over approved indexed fields, never credentials;
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
  microseconds", while exports keep the stored field names;
- cross-links from every entry: "Show related entries" (same correlation
  identifier), "Same actor", "Same campaign" (audit records) and, for task
  entries and views of one task's page, "Open task" to the background task
  page. Each filter travels in a POST body like the form's. The raw
  identifiers themselves (correlation, actor, campaign, subject) are under a
  per-row "Technical details" disclosure, closed by default; the table uses
  the shared Admin table styling;
- the shared [table navigator](#admin-tables) in POST mode. The first view
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
- text or structured JSONL export of the filtered result.

Ministry filtering includes both interactive event identifiers and the
[retained export result scope](../reports/spec.md#ministry-change-summary),
including after campaign-detail purge. ADM-08/RPT-09 own this log-query
integration; report owners supply the durable non-sensitive event metadata.

In v1 a log export is a bounded download from the log screen: CSV or JSON
Lines of the entries matching the screen's filters, newest first, at most the
newest 10,000, with the same reviewed detail the screen shows. It is
Administrator-only, uses a CSRF POST like the screen's filters, rechecks
authorization after the file is built and records a count-only audit event. An
unbounded export is never assembled in a web request; moving log exports onto
the asynchronous export-job pipeline used by large report exports is deferred.

Stored timestamps are UTC. The screen renders browser-local timestamps to the
second, with the zone name and UTC offset, since the date filters are whole
UTC days. Export
requires choosing UTC or the browser's timezone (offered by the page); the
file's timestamps carry their UTC offset. Logins/logouts, configuration, polls/tasks, each email and
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
