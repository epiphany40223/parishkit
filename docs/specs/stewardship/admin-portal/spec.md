# Stewardship administration portal

All administration functionality is rooted under `/admin/` and uses the custom
ParishKit interface; Django's stock administration site is not exposed as the
product UI. Authorization is defined by the [overview](../spec.md#actors-and-authorization)
and enforced on every view, partial endpoint, object query, job, and export.
The [Admin automation interface](../admin-automation/spec.md) reaches the
same actions and reads from the host command line, through the same service
functions, checks and audit; new Admin actions follow its
[rules for new Admin actions](../admin-automation/spec.md#rules-for-new-admin-actions).

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

On the first Admin login, the wizard collects what the system needs to run
and let Admins sign in, and nothing about a campaign, before making the system
configured ([#142](https://github.com/epiphany40223/parishkit/issues/142)):

1. Parish name, website URL, optional HTTPS online giving URL, IANA timezone,
   US main phone, and logo.
2. Domain/address login rules while preserving the bootstrap Admin.
3. ParishSoft API key replacement, expected organization, connectivity check,
   and a complete staged source load.
4. Google Workspace email service-account/delegated mailbox, sender/reply
   address, optional From name, Testing recipient, and test delivery.
5. Optional Slack token/channel and test notification.
6. Exact preview/readiness summary and final confirmation.

The campaign is created afterwards, from Home, by
[Create the campaign](#create-the-campaign). Status: this split is specified
ahead of its implementation pull requests. Until they land, the wizard still
has a First campaign step (name, modules, dates, Ministry/fund selection,
financial period) and Share options, Pages and emails and Dates and mail
schedules pages before Review, its email test sends that campaign's
invitation, and finishing setup creates the campaign with its Family codes.
Create the campaign can land before the wizard changes; until they do, no real
deployment reaches it (setup always leaves a campaign), so it is exercised
only by test fixtures that start without one.

The [parish date format](../spec.md#global-presentation-rules) is not a wizard
step: setup starts with the default US long style, and an Admin changes it
afterwards in Parish settings.

The wizard presents these as one ordered sequence of pages, defined once in
code. The Parish profile comes first, so the administrator starts by
describing their own parish. The credential pages follow, each immediately
after the public settings its staging depends on (outgoing mail and Testing
recipient for Google Workspace, Slack settings for the Slack token). Like
[secret replacement](#parish-and-integration-configuration), they require
fresh Google authentication (a sign-in less than five minutes old); an older
sign-in is offered "Confirm with Google", which keeps the setup, so the order
does not need to race that window. The source load, logos and administrative
access follow, then review, the email and Slack tests and the final
confirmation. Every page shows a compact progress stepper: the current step by
number and name, the count of completed steps and a slim track (each segment
names its step and status on hover; the track is hidden from assistive
technology because the list says the same), with the full ordered list (each
applicable step named as completed, current, not done, optional or not yet
available with the reason) in a collapsed disclosure. Navigation is
conventional: completed steps and the first unfinished required step are
links; later unfinished steps wait for every earlier required step, and
optional steps never hold later ones back. A step counts as completed only
after its own explicit save, credential, load, accepted test or, for the
review page, an explicit review for the current data; defaults seeded by
another page never complete a step. While the source load runs, completed
steps keep their status but are not links. The stepper is presentation only;
each page still enforces its own prerequisites. Each page has one identical
row of Back (secondary) and Save-and-continue (primary) controls (Save and
continue validates, saves and opens the next applicable page, or redisplays
the page with its errors), a short introduction, and plain-language help for
every field. Revisiting a page shows its saved values; a credential page never
shows the secret but says that one is saved (with its public scope, such as
the ParishSoft organization), lets it be kept by leaving the key empty, and
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
or campaign content, and nothing in it depends on a campaign. Before #142 it
sent a sample of the first campaign's invitation instead.

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
script keeps the form's primary button disabled until the box is checked,
and the server refuses a missing acknowledgment.

More generally, a form whose fields depend on other choices keeps its submit
button unavailable until every visible required field is complete, with a
short hint by the button saying what is missing (`data-require-complete` in
the page script; first used by
[Ministry follow-up](#follow-up-workflows), #553). Fields required only in
some states are required only while shown. The Admin portal requires
JavaScript ([#565](https://github.com/epiphany40223/parishkit/issues/565));
server validation is unchanged and still refuses an incomplete submission.
A browser can restore a page from its history (Back or Forward) with the
reader's values but without the events that set the page up, so these
states (shown and hidden fields, unavailable buttons and their hints, a
table's selection, the campaign modules and mail schedule rows) are worked
out again when the page is shown (`pageshow`, #563).

A field error is shown at that field: its message sits directly beside it,
and the field is marked in error (`aria-invalid="true"`, described by the
message, with the shared error border and error-coloured message of
`ui-v1.css`, the same markup Django forms render on the settings and setup
pages). An error that concerns two fields (a date and a time) marks both and
shows its message once, after them. Where a page also shows the error
summary, the summary links to the first marked field and takes focus, as
every Admin refusal summary does (#592).

Where a rule can be checked in the browser, the page checks it too, and
shows its error at the field the same way. The browser's own checks (a
required field, a format) mark a field when the reader leaves it, and clear
once the value is valid. A page can also check a rule live, as the value is
entered (a follow-up contact time in the future). Save is held while such an
error stands only on forms that use the complete-before-submit gate above.
The server stays the authority: it checks every save, and its refusal is
shown at the field as above, even when the browser would have allowed the
value (a wrong computer clock, for example).

A mark clears as soon as its error does, without waiting for Save (#592). An
error the browser checks clears once the value is valid. One only the
server can check (an outcome that doesn't fit the request, for example)
clears from its field, with its message, on the first edit of that field,
since the server checks again on save; an error that marks two fields (a
date and a time) clears from both when either is edited. A field that a
choice hides is not sent, so its marks clear too. Leaving a field without
changing it clears nothing. The summary loses the item for a field that
clears, and goes once it lists nothing. This applies to every Admin form,
Django-rendered ones included, through the shared page script.

The mail schedule page (Dates and mail schedules) starts with a short guide:
what each mail type is, that exactly one initial invitation is required before
go-live, that
reminders and the daily and weekly Admin digests are optional (at most one
digest of each kind), that submission receipts and critical alerts are sent
automatically and never scheduled, that times use the campaign time zone, and
the current campaign dates. Each schedule row shows only the fields its mail
type uses (initial invitation and reminder: date, time and email; daily
digest: time and email; weekly digest: weekday, time and email) and offers
only emails of that type; the page script clears a field it hides. The send
time is a [time entry](#time-entry) field. A saved schedule's mail type is
shown but cannot change. The server reports a missing
or inapplicable value on its own field (for example a weekday on an
invitation, or a date outside the campaign with the campaign's dates); only
rules between rows, such as a second initial invitation or a reminder before
it, are collection errors. "Add another schedule" adds blank rows (with the
same per-type fields) before saving, and a row added this way can be removed
again, so several schedules save in one submission and are validated together.

On the regular Dates and mail schedules page, a **Scheduled emails** table
([#448](https://github.com/epiphany40223/parishkit/issues/448)) lists every
saved schedule above the editors, one row each, in sending order: dated sends
by date and time (the Initial invitation first at a tie), then the digests,
with the saved ID as the final tie-break, so the order never changes between
loads. Reminders are numbered in that order, as
[Family email sends](#family-email-sends) numbers them. Each row shows the
schedule, when it sends (a browser-local time; a digest shows its day and
its next send), the email to send, a status and, for a Family send with
results, its delivered and failed counts with a link to Family email
history. Work that blocks a change (in progress or with an uncertain
result) makes it Preparing before the send time and Sending after it.
Otherwise it is Upcoming before the send time; after it, Sending while any
of its work is pending or running. Once that work is finished it is Sent
when any of its email was delivered or prepared, "Sent (failed)" when its
only results are failures (with the same link), and Done when it settled
every Family without an email of its own (the coalesced and empty
dispositions). Otherwise it is "Not sent yet" (the scheduler, a hold or
catch-up may still send it); a digest is Repeats. The status reads the same counts-only schedule work summary
as the review, never Family data. A short line states the rules between
schedules. The table is short and shown whole, and its order is its point, so
its headings do not sort.

Choosing an editable row (its name, or anywhere else on the row) opens that
schedule's editor in place, without a page load, marks the row as being
edited and moves focus to the editor; an opened editor stays open, so a
change never hides while it waits for Preview. Saved schedules' editors start
closed, except one with an error or an unsaved change after a refused
preview; the blank new row and added rows show as before, each labelled
"New schedule". Each row's control reports whether its editor is open
(`aria-expanded`). To delete a schedule, open it and check Delete. A
schedule that has run or has work prepared (Preparing, Sending, Sent, Sent
(failed) or Done) is read-only and cannot be changed or deleted: its row is muted with no
control and its editor stays closed, because moving a send that already
happened would make a new revision and plan it again. The one exception is
an editor holding a posted change or an error after a refused preview (for
example, the schedule started running while it was being edited): it opens,
so the change can be seen and undone rather than posted again unseen.
Showing a row's "editing below" note never moves its control, because the
note's space is reserved. Closed and read-only editors still post their saved
values, so the posted formset, its validation, the review and reminder
planning are unchanged. When the browser restores a value into a closed
editor (Back), `pageshow` opens that editor again; a value restored into a
read-only editor is put back to its saved value. The first-campaign step
keeps its plain list of editors, since nothing has sent there.

The staged ParishSoft load is a complete load of Families, Members, Ministries
and the fund catalog, with no giving window, as any load is before a campaign
exists; the campaign's giving arrives with the refresh that
[Create the campaign](#create-the-campaign) queues. Starting that load fixes
the Parish timezone for this setup attempt, so the source catalog and the
campaign created later keep the same civil-date interpretation.
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
confirmation. It polls a passive status every 15 seconds while visible,
reloading when a step changes and showing a Continue link to the Admin home once
setup completes. A failed step explains what to do: a credential the provider
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
[help](#page-help), loading/empty/error states, and responsive layouts.

In Testing mode, every Admin page has a prominent persistent banner naming the
test recipient and linking to mode configuration. Staff/leader pages show a
smaller non-dismissible Testing indicator so report interpretation is clear.

In Production mode, while the web process has debug logging on
(`PARISHKIT_DEBUG_LOGGING=1`), every Admin page, for every role, shows a
prominent non-dismissible error banner saying in plain language that debug
logging must be off in Production, because debug logs can hold personal data,
and that the operator turns it off by recreating the application containers
with the variable `0` or unset. It is a warning only: no process refuses to
start with the switch on. Once [System health](#system-health) lands
(ADM-13), the banner links that page, where an Administrator can turn debug
logging off without the operator, and the banner stays until every service
reports it off.

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
transaction commits after the acknowledgement, brings the banner back. A
problem that has ended reads "ended" with its browser-local end time, and the
title says when every listed problem has ended, so a past outage does not read
as a current one: a kind of problem has ended once the operational intake has
taken in each of its counted entries and every incident they opened has
[resolved](../background-processing/spec.md#what-went-wrong-and-recovery)
(#633). The banner costs the Admin page one query, shared with the delivery
warning count. It is distinct from security-event acknowledgement, which is per
recipient. Acknowledge acts [in place](#in-place-controls): the server still
answers with Home, and the banner is taken from that answer on the page the
Administrator is on, which keeps its address and everything else on it.

### JavaScript requirement

The Admin portal requires JavaScript and has no no-script fallback (#565).
Every Admin page, including the sign-in pages, renders its content hidden
behind a `js-required` class beside a plain-language panel: "The Admin portal
needs JavaScript. Turn it on in your browser settings, then reload this page."
The first-party Admin script removes the class at startup, and the hiding rule
lives in the shared stylesheet, so the content security policy still allows
no inline script or style. With script off, a page shows only the panel and
offers no action. Links and forms keep real `href` and `action` attributes,
because they are the targets the scripts request. Every action is still
validated on the server, in the same service code the
[Admin automation interface](../admin-automation/spec.md) calls; client-side
checks are a convenience, never the only guard. Existing no-script fallbacks
are removed when a change touches their code anyway. The Family portal is not
gated and keeps working without script
([client behavior](../architecture/spec.md#accessibility-and-client-behavior)).

### Admin navigation

Admin pages share one layout: a sidebar menu beside the page, and a breadcrumb
trail above the page heading. Both come from a single declarative registry in
`accounts/admin_navigation.py`, keyed by URL name, that gives each Admin page a
menu group, a parent page and a label, so the menu and the trails cannot drift
and no template hand-writes a breadcrumb or a back link that repeats the trail.

This section is the target structure from issue #522, written from a walk
through every Admin page (109 pages) and settled by the Administrator's
[navigation decisions](#navigation-decisions) of 2026-10-04. The sidebar is
moved to it, and the page names, links and URLs fixed against it, by the
follow-up issues #520 (one name per page), #521 (every page reachable, every
flow with a way back) and #525 (one URL scheme). Every page already uses the
table's name (NAV-4, NAV-5a and NAV-5b), except Portal users, which keeps its
name until NAV-15 splits it into Sign-in rules, Ministry assignments and
Chairpersons, and the Emailed reports, Ministry assignments and Chairpersons
pages, which do not exist yet. The System pages already have their new
addresses (NAV-6), and so do the Parish data pages and a change's status page
(NAV-7), the Mail and Family portal pages (NAV-8) and the Campaign setup
pages (NAV-9: settings, Copy campaign, content and its history, images,
schedules, Share options and Member talents; NAV-10: the test email pages,
the go-live chain, Production activation and Cancel go-live, and Campaign
Ministries under Ministries) and the report pages (NAV-11: every report, the
response lists and the Family timeline, with the
campaign choosers and the old Ministry reports root retired), with every old
address redirecting; the Campaign setup, Mail and Family portal and Parish
data group roots open their first entry, and `/admin/reports/` keeps its
meaning. Until the rest of the URL work lands, other pages (the export and
emailed report pages, NAV-12) keep their
"Current URL", and so does every other section of this spec and the other
stewardship specs that name an Admin URL; those follow-up issues update them
with the code.

The Admin portal serves one current campaign. The system moves to a single
campaign after this campaign (#145), so navigation already assumes it: there
is no campaign chooser and no New campaign control (the one campaign is
created by [Create the campaign](#create-the-campaign), offered only while the
deployment has never had a campaign), and no Admin URL names a campaign. Every
remaining control whose only purpose is working with more than one campaign,
such as **Copy campaign** on Campaign settings and the "Choose a retained
campaign" links on Participation and Ministry requests, is shown greyed out
until #145 removes it: an unavailable control, not a link or action, with the
tip "Disabled; will be removed with the single-campaign change (#145)", shown
and announced the same way as an unavailable menu entry's reason. The server
refuses the matching actions too, so a greyed control cannot be bypassed
([navigation rule 10](#navigation-rules), [decisions 18 and
19](#navigation-decisions)).

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
| Campaign setup | Dates and mail schedules | `/admin/campaign/schedules/` | Administrator | No current campaign |
| Campaign setup | Share options | `/admin/campaign/share-options/` | Administrator | The campaign has no financial module, or it is not an unlocked Testing draft |
| Campaign setup | Member talents | `/admin/campaign/talents/` | Administrator | The campaign has no Ministry module, or it is not an unlocked Testing draft |
| Campaign setup | Reminder WorkGroup | `/admin/campaign/reminder-workgroup/` | Administrator | No current campaign, or it is archived |
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
| Responses and reports | Financial stewardship | `/admin/reports/financial/` | Administrator, Staff | The campaign has no financial module |
| Responses and reports | Talents and limitations | `/admin/reports/talents/` | Administrator, Staff | The campaign has no Ministry module |
| Responses and reports | Additional information | `/admin/reports/information/` | Administrator, Staff | No current campaign |
| Responses and reports | Ministry requests | `/admin/reports/ministries/` | Administrator, Staff, Ministry leader | The campaign has no Ministry module |
| Responses and reports | Ministry follow-up | `/admin/reports/ministries/follow-up/` | Administrator, Staff, Ministry leader | The campaign has no Ministry module |
| Responses and reports | Active parishioner family directory | `/admin/reports/families/` | Administrator, Staff | No current campaign |
| Responses and reports | Emailed reports | `/admin/reports/emailed/` | Administrator, Staff | Never |
| Parish data | Parish settings | `/admin/parish/settings/` | Administrator | Never |
| Parish data | Parish logos | `/admin/parish/logos/` | Administrator | Never |
| Parish data | Ministries | `/admin/parish/ministries/` | Administrator | Never |
| Parish data | Hosted files | `/admin/parish/files/` | Administrator | Never |
| Parish data | Refresh from ParishSoft | `/admin/parish/parishsoft-refresh/` | Administrator | Never |
| Users and access | Sign-in rules | `/admin/users/sign-in-rules/` | Administrator | Never |
| Users and access | Ministry assignments | `/admin/users/ministry-assignments/` | Administrator | Never |
| Users and access | Chairpersons | `/admin/users/chairpersons/` | Administrator | Never |
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
  [Talents and limitations](../reports/spec.md#talents-and-limitations),
  [Additional information](../reports/spec.md#additional-information), the
  Ministry report and its [follow-up](#follow-up-workflows) queue, and the
  [active parishioner family directory](../reports/spec.md#active-parishioner-family-directory) (with Family
  campaign codes and its mailing columns). Reports always show the current
  campaign. Ministry leaders see only their own Ministries in the Ministry
  entries. Additional information and Ministry follow-up show their open
  counts. Ministry follow-up has no assignment (#552): a request keeps its
  status, notes and outcome, and its Ministry leader handles it.
- **Emailed reports** is a new page listing the past daily and weekly
  [Administrator digests](../background-processing/spec.md#administrator-digests)
  the viewer may open (Staff see daily reports only, as today), newest first,
  so a digest page is no longer reachable only from its email. Sending a
  weekly report now is an Administrator action on this page and returns to
  it, linking the report it produced.
- **Ministries** is the one home for Ministries: parish-wide
  [Ministry activity](#ministry-activity-management), with
  [this campaign's Ministries](#changing-a-live-campaigns-ministries) reached
  from it. Campaign settings links to the campaign's Ministries page; it is
  not a second home.
- Mail and the Family portal sit together, so pausing mail
  ([live delivery pause](#live-delivery-pause)) and closing the portal
  ([Family portal maintenance](#family-portal-maintenance)) are side by side.
  The header's background, delivery and presence counts stay as shortcuts to
  their entries.
- [Portal user management](#portal-user-management) is three entries instead
  of one long page of five tables: **Sign-in rules** (Google Workspace
  domain rules and exact-address rules, with their roles), **Ministry
  assignments** (Ministry assignments for people a domain rule admits) and
  **Chairpersons** (suspended Chairperson assignments awaiting review, and
  [Chairperson suggestions](#chairperson-suggestions-and-assignments) from
  ParishSoft). Each review started on one of them returns to it.
- Emailed reports, Ministry assignments and Chairpersons are new pages: #520
  and #521 add their registry entries and views.
- Each group's root URL (`/admin/campaign/`, `/admin/mail/`,
  `/admin/reports/`, `/admin/parish/`, `/admin/users/`, `/admin/system/`)
  redirects to the group's first entry available to the viewer, except
  `/admin/reports/`, which keeps its old meaning: Participation, or Ministry
  requests for a viewer who may not open Participation. A group root with no
  entry available to the viewer redirects to Home.
- Until initial setup completes, the only menu entry is the setup wizard,
  which has its own stepper: nothing else works yet, so this is the one
  exception to a stable menu shape.

The menu always ends with **Sign out**, set apart from the groups, on every
signed-in Admin page (including during initial setup). It is a POST form with
the Admin CSRF token to the logout route, styled like a menu entry, because
logout is CSRF-protected. Pages carry no product footer.

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
The box lists the first 8 matches in the directory's order, each with its DUID
and envelope number, and a "See all" button that opens the directory with the
same search when more match. A Family without a campaign record is listed
without a link, as in the directory. Each search is audited as a directory view
(that a search was used, and the row counts), never its text. A search the
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
[About panel](#page-help) does); with nothing stored, every group starts
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
is something to do. An unavailable entry's page refuses a direct visit as it
does today, with the same reason. The conditions come from the campaign
state, modules and mode the chrome already reads, so building the menu adds
no query.

#### Home page

Home is a starting point, not only a status page. Its heading is "Home"; the
parish name and the current campaign's name and state are its data line. Below
the status panels described above, Home shows a short **Next steps** list of
links chosen by the campaign's state and the viewer's role:

| State | Administrator | Staff | Ministry leader |
| --- | --- | --- | --- |
| Draft in Testing | Campaign settings, Pages and emails, Dates and mail schedules, Preview and test email (for the initial invitation's current revision), Go-live readiness, Response dashboard (Testing) | Response dashboard, Participation | Ministry requests |
| Production being activated | Production activation, Outgoing mail | Response dashboard | Ministry requests |
| Active | Response dashboard, Additional information and Ministry follow-up (with open counts), Family email progress, Outgoing mail problems (with count) | Response dashboard, Additional information, Ministry follow-up (with open counts) | Ministry requests, Ministry follow-up (with open count) |
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

Home also shows one **Today** line for each role. Its contents are a
proposal for the Administrator to confirm in NAV-18 (gap G10 on #522):
submissions since yesterday, next scheduled email, open follow-up counts,
problems. How the line differs by role is still to be decided.

Problems that need action (failed background tasks, unacknowledged security
events, out-of-date ParishSoft data as
[ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection)
defines it, and the [System health](#system-health)
problems such as an overdue backup or a halted mail sender) stay above Next
steps and link the page that resolves them. A next step is a link only when
the viewer's menu entry for it is available.

#### Page names and placement

Every Admin page has one name. The menu entry, the breadcrumb, the page
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
"retired" pages leave the portal and their old URLs redirect.

| Page | Name | Reached from | Roles | Current names | Current URL | New URL | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `index` | Home | Menu: Home | Administrator, Staff, Ministry leader | Campaign administration | `/admin/` | (same) | Heading becomes Home; parish and campaign shown as the data line. |
| `configuration_request` | Change status | Settings page the change came from (else Home) | Administrator | Configuration change status; Configuration change | `/admin/changes/<request>/` | (same; old address redirects) |  |
| `campaign_settings` | Campaign settings | Menu: Campaign settings | Administrator | (same) | `/admin/campaign/settings/` | (same; old address redirects) |  |
| `campaign_create` | Create the campaign | Home | Administrator | (new, #142) | `/admin/campaign/create/` | (same) | Offered on Home only while the deployment has never had a campaign; refused once one exists. Not linked from a change's status page. |
| `campaign_clone` | Copy campaign | Campaign settings | Administrator | Clone archived campaign | `/admin/campaign/copy/` | (same; old address redirects) | Decision 18: until #145 removes it, Campaign settings shows Copy campaign greyed out, not an action, with the tip "Disabled; will be removed with the single-campaign change (#145)". The server refuses the clone action. |
| `content_history` | Content history | Campaign settings | Administrator | Retained campaign content | `/admin/campaign/content/history/` | (same; old address redirects) |  |
| `content_history_revision` | Earlier version | Content history | Administrator | Retained campaign content; Revision | `/admin/campaign/content/history/<revision>/` | (same; old address redirects) |  |
| `content_catalog` | Pages and emails | Menu: Pages and emails | Administrator | Campaign content and templates | `/admin/campaign/content/` | (same; old address redirects) |  |
| `content_edit` | _page or email name_ | Pages and emails | Administrator | Edit page or email | `/admin/campaign/content/<kind>/<slot>/` | (same; old address redirects) | Object-named (exception). |
| `content_revision` | _email name_ | Pages and emails | Administrator | _email template name_; Content revision | `/admin/campaign/content/email/<slot>/<revision>/` | (same; old address redirects) | Object-named (exception); links its own test page. |
| `campaign_mail` | Preview and test email | _email name_ | Administrator | Campaign email test | `/admin/campaign/content/test/<revision>/` | (same; old address redirects) | Returns to the page it was opened from (Go-live readiness, Pause and resume mail). |
| `campaign_mail_families` | Send to chosen Families | Preview and test email | Administrator | Send this email to chosen Families | `/admin/campaign/content/test/<revision>/families/` | (same; old address redirects) |  |
| `artwork_settings` | Campaign images | Menu: Campaign images | Administrator | (same) | `/admin/campaign/images/` | (same; old address redirects) |  |
| `artwork_upload` | Campaign images | Campaign images | Administrator | Campaign images (upload error); Campaign image upload | `/admin/campaign/images/<slot>/` (POST only) | (same; old address redirects) | POST-only error re-render of Campaign images; reclassify as a form action. |
| `artwork_preview` | Review campaign image | Campaign images | Administrator | (same) | `/admin/campaign/images/<slot>/<bundle>/` | (same; old address redirects) |  |
| `artwork_remove` | Remove campaign image | Campaign images | Administrator | (same) | `/admin/campaign/images/<slot>/removal/` | (same; old address redirects) |  |
| `schedule_settings` | Dates and mail schedules | Menu: Dates and mail schedules | Administrator | Mail schedules and campaign dates; Mail schedules | `/admin/campaign/schedules/` | (same; old address redirects) |  |
| `share_settings` | Share options | Menu: Share options | Administrator | How Families will share | `/admin/campaign/share-options/` | (same; old address redirects) | Gains a Return link and About panel. |
| `talent_settings` | Member talents | Menu: Member talents | Administrator | Talents Members can share | `/admin/campaign/talents/` | (same; old address redirects) | Gains a Return link and About panel. |
| `reminder_workgroup` | Reminder WorkGroup | Menu: Reminder WorkGroup | Administrator | (new, #861) | `/admin/campaign/reminder-workgroup/` | (same) | Edit, review, apply; stays editable while live. |
| `go_live` | Go-live readiness | Menu: Go-live readiness | Administrator | (same) | `/admin/campaign/go-live/` | (same; old address redirects) |  |
| `go_live_families` | Testing submissions | Go-live readiness | Administrator | Families with Testing submissions; Testing Families | `/admin/campaign/go-live/families/` | (same; old address redirects) |  |
| `go_live_cleanup` | Testing cleanup | Go-live readiness | Administrator | Testing cleanup progress | `/admin/campaign/go-live/cleanup/<request>/` | (same; old address redirects) |  |
| `go_live_links` | Prepare Family links | Testing cleanup | Administrator | Family links | `/admin/campaign/go-live/cleanup/<request>/links/` | (same; old address redirects) |  |
| `production_confirmation` | Confirm Production | Prepare Family links | Administrator | Final Production confirmation | `/admin/campaign/go-live/cleanup/<request>/links/<preparation>/confirmation/` | (same; old address redirects) |  |
| `production_progress` | Production activation | Menu: Production activation | Administrator | Production activation progress | `/admin/campaign/production/` | (same; old address redirects) | Links its cleanup request and Outgoing mail. |
| `production_withdrawal` | Cancel go-live | Production activation | Administrator | Withdraw from Production; Return to Testing | `/admin/campaign/production/cancellation/` | (same; old address redirects) | Links Pause and resume mail and Outgoing mail where it names them. |
| `delivery_control` | Pause and resume mail | Menu: Pause and resume mail | Administrator | Campaign delivery controls; Delivery controls | `/admin/mail/controls/` | (same; old address redirects) |  |
| `family_email_progress` | Family email progress | Menu: Family email progress | Administrator | (same) | `/admin/mail/family-progress/` | (same; old address redirects) |  |
| `family_email_sends` | Family email history | Menu: Family email history | Administrator | Family email sends | `/admin/mail/family-history/` | (same; old address redirects) |  |
| `deliveries` | Outgoing mail | Menu: Outgoing mail | Administrator | (same) | `/admin/mail/outgoing/` | (same; old address redirects) |  |
| `delivery` | Mail message | Outgoing mail | Administrator | Mail delivery; Message | `/admin/mail/outgoing/<message>/` | (same; old address redirects) |  |
| `delivery_refusals` | Refused addresses | Outgoing mail | Administrator | (same) | `/admin/mail/refusals/` | (same; old address redirects) |  |
| `delivery_refusal` | Refused address | Refused addresses | Administrator | Verify refused address | `/admin/mail/refusals/<refusal>/` | (same; old address redirects) |  |
| `family_portal` | Family portal availability | Menu: Family portal availability | Administrator | (same) | `/admin/mail/family-portal/` | (same; old address redirects) |  |
| `presence` | Families on the form now | Menu: Families on the form now | Administrator | (same) | `/admin/mail/presence/` | (same; old address redirects) | The header's count is still polled at `/admin/presence?format=count` (decision 8); only page reads of that address redirect. |
| `response_dashboard` | Response dashboard | Menu: Response dashboard | Administrator, Staff | (same) | `/admin/reports/responses/` | (same; old address redirects) |  |
| `response_list` | _list name_ | Response dashboard | Administrator, Staff | (new, #477) | `/admin/reports/responses/<key>/` | (same; old address redirects) | Object-named (exception): each list behind a dashboard count is named after that list. Its CSV download (`<key>/csv/`) is a non-page action. |
| `reports` | Participation | Menu: Participation | Administrator, Staff | (redirects) Participation and campaign statistics; 'Campaign reports' only when no campaign exists; Campaign reports | `/admin/reports/` | (same) | Not a group root: redirects to Participation, or to Ministry requests for a viewer who may not open Participation (a Ministry leader). |
| `participation` | Participation | Menu: Participation | Administrator, Staff | Participation and campaign statistics; Campaign reports (via redirect) | `/admin/reports/participation/` | (same; old address redirects) |  |
| `report_export` | _report name_ export | The report it came from | Administrator, Staff, Ministry leader | Participation export / Financial stewardship export / Ministry export / Additional-information export / Family-directory export; Report export | `/admin/reports/exports/<request>/` | (same) | Object-named (exception). |
| `report_exact` | Latest-data export | Participation | Administrator, Staff | Queued participation export; Exact export | `/admin/reports/exact-exports/<request>/` | `/admin/reports/exports/<request>/` (the shared export page) | Decision 8: folds into the shared export page; old addresses redirect there. |
| `financial_report` | Financial stewardship | Menu: Financial stewardship | Administrator, Staff | Financial stewardship detail; Financial report | `/admin/reports/financial/` | (same; old address redirects) |  |
| `talents_report` | Talents and limitations | Menu: Talents and limitations | Administrator, Staff | (same) | `/admin/reports/talents/` | (same; old address redirects) |  |
| `information_queue` | Additional information | Menu: Additional information | Administrator, Staff | Additional information and follow-up | `/admin/reports/information/` | (same; old address redirects) |  |
| `information_item` | Information request | Additional information | Administrator, Staff | Additional information and follow-up (one request); Information item | `/admin/reports/information/<item>/` | (same; old address redirects) | Return to the queue keeps its filters and page. |
| `ministry_reports` | (retired) | (retired) | Administrator, Staff, Ministry leader | (redirects) Ministry requests; Ministry reports | `/admin/ministry-reports/` | (retired; redirects to `/admin/reports/ministries/`) | The old address redirects permanently to Ministry requests, which shows the "no campaign" page when there is nothing for the viewer to report. |
| `ministry_report` | Ministry requests | Menu: Ministry requests | Administrator, Staff, Ministry leader | Ministry report | `/admin/reports/ministries/` | (same; old address redirects) |  |
| `ministry_joiners` | Members joining | Ministry requests | Administrator, Staff, Ministry leader | Ministry requests — Prospective joiners; Joining | `/admin/reports/ministries/joining/` | (same; old address redirects) | Becomes a bookmarkable view of Ministry requests (#521). |
| `ministry_leavers` | Members leaving | Ministry requests | Administrator, Staff, Ministry leader | Ministry requests — Requested leavers; Leaving | `/admin/reports/ministries/leaving/` | (same; old address redirects) | Becomes a bookmarkable view of Ministry requests (#521). |
| `ministry_followup` | Ministry follow-up | Menu: Ministry follow-up | Administrator, Staff, Ministry leader | Follow-up | `/admin/reports/ministries/follow-up/` | (same; old address redirects) | No assignee column, filter or bulk assignment (#552). |
| `ministry_followup_item` | Follow-up request | Ministry follow-up | Administrator, Staff, Ministry leader | Ministry follow-up (one request) | `/admin/reports/ministries/follow-up/<request>/` | (same; old address redirects) | Return to the queue keeps its filters and page. Status, notes and outcome only; no Assign to (#552). |
| `family_directory` | Active parishioner family directory | Menu: Active parishioner family directory | Administrator, Staff | (same) | `/admin/reports/families/` | (same; old address redirects) |  |
| `family_timeline` | Family timeline | Active parishioner family directory | Administrator, Staff | (new, #477) | `/admin/reports/families/<family>/` | (same; old address redirects) | Opened from each directory row and response list row (and Find a Family, NAV-19); `<family>` is the opaque campaign record id. Staff see the summary only. |
| `weekly_digest_manual` | Send a weekly report now | Emailed reports (new page) | Administrator | Request a manual information report; Manual information report | `/admin/reports/weekly-digests/request/<campaign>/` | `/admin/reports/emailed/weekly/new/` | Was the Manual information report menu entry; ends on Emailed reports, which links the report it produced. |
| `weekly_digest_snapshot` | Weekly report | Emailed reports (new page) | Administrator | Weekly information report; Weekly summary | `/admin/reports/weekly-digests/<snapshot>/` | `/admin/reports/emailed/weekly/<snapshot>/` |  |
| `weekly_digest_item` | Weekly report item | Weekly report | Administrator | Weekly information report (one item); Weekly summary item | `/admin/reports/weekly-digests/<snapshot>/items/<item>/` | `/admin/reports/emailed/weekly/<snapshot>/items/<item>/` | Links the live Additional information request. |
| `daily_digest_snapshot` | Daily report | Emailed reports (new page) | Administrator, Staff | (report document title) daily report | `/admin/reports/daily-digests/<snapshot>/` | `/admin/reports/emailed/daily/<snapshot>/` |  |
| `parish_settings` | Parish settings | Menu: Parish settings | Administrator | (same) | `/admin/parish/settings/` | (same; old address redirects) | Hand-written Administration link removed. |
| `branding_settings` | Parish logos | Menu: Parish logos | Administrator | (same) | `/admin/parish/logos/` | (same; old address redirects) |  |
| `branding_preview` | Review parish logos | Parish logos | Administrator | Logo preview | `/admin/parish/logos/<bundle>/` | (same; old address redirects) |  |
| `ministries` | Ministries | Menu: Ministries | Administrator | Ministry activity | `/admin/parish/ministries/` | (same; old address redirects) |  |
| `campaign_ministries` | Campaign Ministries | Ministries | Administrator | Change campaign Ministries | `/admin/parish/ministries/campaign/` | (same; old address redirects) | Moves under Ministries, which links it while the campaign is live; its Return link goes to Ministries. Campaign settings keeps a link. |
| `hosted_files` | Hosted files | Menu: Hosted files | Administrator | (same) | `/admin/parish/files/` | (same; old address redirects) |  |
| `hosted_file_delete` | Delete hosted files | Hosted files | Administrator | (same) | `/admin/parish/files/deletion/` (POST only) | (same; old address redirects) |  |
| `hosted_file_rename` | Change placeholder name | Hosted files | Administrator | (same) | `/admin/parish/files/<file>/name/` | (same; old address redirects) |  |
| `source_refresh` | Refresh from ParishSoft | Menu: Refresh from ParishSoft | Administrator | ParishSoft refresh | `/admin/parish/parishsoft-refresh/` | (same; old address redirects) |  |
| `users` | Sign-in rules | Menu: Sign-in rules | Administrator | Portal users | `/admin/users` | `/admin/users/sign-in-rules/` | Portal users is split into Sign-in rules, Ministry assignments and Chairpersons (decision 13). |
| `user_rules` | Review sign-in rules | Sign-in rules | Administrator | Review login rule change; Sign-in rules | `/admin/users/rules` (POST only) | `/admin/users/sign-in-rules/review/` (POST only) |  |
| `rule_request` | (not a page) | Sign-in rules | Administrator | Rule change (status); Rule change | `/admin/users/rules/requests/<request>` | `/admin/users/sign-in-rules/requests/<request>/` (JSON) | Answers JSON only; reclassify as a non-page. |
| `assignments` | Review Ministry assignment | Ministry assignments (new page) | Administrator | Review Ministry assignment change; Assignments | `/admin/users/assignments` (POST only) | `/admin/users/ministry-assignments/review/` (POST only) |  |
| `chair_confirmations` | Review Chairperson suggestion | Chairpersons (new page) | Administrator | Review Chairperson confirmation; Chair suggestions | `/admin/users/suggestions` (POST only) | `/admin/users/chairpersons/suggestions/` (POST only) |  |
| `chair_reviews` | Review Chairperson decision | Chairpersons (new page) | Administrator | Review Chairperson assignment decision; Chair reviews | `/admin/users/reviews` (POST only) | `/admin/users/chairpersons/reviews/` (POST only) |  |
| `automation_access` | Automation access | Menu: Automation access | Administrator | (same) | `/admin/users/automation/` | (same) | [Admin automation](../admin-automation/spec.md#revocation-and-listing) (ADM-11); revoke posts to `/admin/users/automation/sessions/<session>/`. |
| `automation_approval` | Approve an automation session | Automation access | Administrator | (same) | `/admin/users/automation/approval/` | (same) | Opened from the command line's link; needs a fresh sign-in. |
| `system_health` | System health | Menu: System health | Administrator | (new) | (none) | `/admin/system/health/` | New page ([System health](#system-health), #530, ADM-13). |
| `integrations` | Integrations | Menu: Integrations | Administrator | (same) | `/admin/system/integrations/` | (same; old address redirects) |  |
| `integration_settings` | _integration name_ | Integrations | Administrator | _integration name_ (e.g. ParishSoft, Google Workspace mail, Slack notifications, Off-site backups, Backup encryption key); Integration | `/admin/system/integrations/<target>/` | (same; old address redirects) | Object-named (exception); hand-written Integrations link removed. |
| `credential_status` | Key replacement status | _integration name_ | Administrator | (same) | `/admin/system/key-changes/<request>/` | (same; old address redirects) |  |
| `select_credential` | Finish switching to the new key | _integration name_ | Administrator | (same) | `/admin/system/key-changes/<request>/selection/` | (same; old address redirects) |  |
| `background` | Background work | Menu: Background work | Administrator | (same) | `/admin/system/background/` | (same; old address redirects) |  |
| `background_task_page` | Background task | Background work | Administrator | Background task details | `/admin/system/background/<task>/` | (same; old address redirects) |  |
| `logs` | System logs | Menu: System logs | Administrator | (same) | `/admin/system/logs/` | (same; old address redirects) |  |
| `campaign_new` | (retired) | (retired) | Administrator | Create campaign draft; New campaign | `/admin/campaign/new` | (retired; redirects to `/admin/campaign/settings/`) | Decision 11: no New campaign control; the one campaign is created by Create the campaign (#142). The old address redirects to Campaign settings. |
| `report_campaigns` | (retired) | (retired) | Administrator, Staff | Choose a retained campaign; Choose a campaign | `/admin/reports/campaigns/` | (retired; redirects to `/admin/reports/participation/`) | Decisions 10 and 19: no campaign chooser; reports show the current campaign. Its link on Participation is greyed out with the #145 tip until removed. The old address redirects to Participation. |
| `ministry_report_campaigns` | (retired) | (retired) | Administrator, Staff, Ministry leader | Choose a retained campaign; Choose a campaign | `/admin/ministry-reports/campaigns/` | (retired; redirects to `/admin/reports/ministries/`) | Decisions 10 and 19: no campaign chooser. Its link on Ministry requests is greyed out with the #145 tip until removed. The old address redirects to Ministry requests. |
| `chrome` | (shared header and banners) | Every page | Administrator, Staff, Ministry leader | Every Admin page (header and banners) | `/admin/*` | (same) | Acknowledge stays on the current page (#519). |
| `login` | Administration sign-in | (outside the menu) | Anyone | (same) | `/admin/login` | `/admin/login/` |  |
| `local_sign_in` | Local test sign-in | (outside the menu) | Anyone | (same) | `/admin/local/sign-in` | `/admin/local/sign-in/` | LOCAL only. |
| `maintenance` | The system is temporarily unavailable | (outside the menu) | Administrator, Staff, Ministry leader | (same) | `/admin/maintenance` | `/admin/maintenance/` | Decision 12: restore-review dead end. |
| `availability_setup` | The system is not configured yet | (outside the menu) | Administrator, Staff, Ministry leader | (same) | (any Admin URL before setup completes, non-Administrator) | (any Admin URL before setup completes) |  |
| `error_page` | _error title_ | (outside the menu) | Administrator, Staff, Ministry leader | _error title_ (e.g. Check your entries, This information changed, Access unavailable, Page unavailable, Confirm it's you) | (any Admin URL that fails) | (same) | Object-named (exception); offers a way back to the page or Home. |
| `setup` | Initial setup | Setup stepper | Administrator | (same) | `/admin/setup` | `/admin/setup/` |  |
| `setup_step_parish` | Parish profile | Setup stepper | Administrator | (same) | `/admin/setup/parish` | `/admin/setup/parish/` |  |
| `setup_credential_parishsoft` | ParishSoft connection | Setup stepper | Administrator | Setup credential: ParishSoft (stepper: ParishSoft connection) | `/admin/setup/credentials/parishsoft` | `/admin/setup/credentials/parishsoft/` |  |
| `setup_step_mail` | Outgoing email settings | Setup stepper | Administrator | (same) | `/admin/setup/mail` | `/admin/setup/mail/` |  |
| `setup_step_testing` | Testing recipient | Setup stepper | Administrator | (same) | `/admin/setup/testing` | `/admin/setup/testing/` |  |
| `setup_credential_google_workspace` | Google Workspace connection | Setup stepper | Administrator | Setup credential: Google Workspace mail (stepper: Google Workspace connection) | `/admin/setup/credentials/google_workspace` | `/admin/setup/credentials/google_workspace/` |  |
| `setup_step_slack` | Slack notifications (optional) | Setup stepper | Administrator | (same) | `/admin/setup/slack` | `/admin/setup/slack/` |  |
| `setup_credential_slack` | Slack connection | Setup stepper | Administrator | Setup credential: Slack (stepper: Slack connection) | `/admin/setup/credentials/slack` | `/admin/setup/credentials/slack/` |  |
| `setup_source` | Load parish data | Setup stepper | Administrator | (same) | `/admin/setup/source` | `/admin/setup/source/` |  |
| `setup_source_progress` | Loading parish data | Setup stepper | Administrator | (same) | `/admin/setup/source/<task>` | `/admin/setup/source/<task>/` |  |
| `setup_branding` | Parish logos | Setup stepper | Administrator | Parish logo | `/admin/setup/branding` | `/admin/setup/branding/` |  |
| `setup_step_access` | Administrative access | Setup stepper | Administrator | (same) | `/admin/setup/access` | `/admin/setup/access/` |  |
| `setup_campaign` | First campaign | Setup stepper | Administrator | (same) | `/admin/setup/campaign` | `/admin/setup/campaign/` |  |
| `setup_content` | Pages and emails | Setup stepper | Administrator | First-campaign content (stepper: Pages and email templates) | `/admin/setup/content` | `/admin/setup/content/` |  |
| `setup_content_edit` | _page or email name_ | Setup stepper | Administrator | (same) | `/admin/setup/content/<kind>/<slot>` | `/admin/setup/content/<kind>/<slot>/` | Object-named (exception). |
| `setup_shares` | Share options | Setup stepper | Administrator | How Families will share | `/admin/setup/shares` | `/admin/setup/shares/` |  |
| `setup_schedules` | Dates and mail schedules | Setup stepper | Administrator | First-campaign mail schedules (stepper: Mail schedules) | `/admin/setup/schedules` | `/admin/setup/schedules/` |  |
| `setup_preview` | Review | Setup stepper | Administrator | First-campaign setup preview (stepper: Review) | `/admin/setup/preview` | `/admin/setup/preview/` |  |
| `setup_mail` | Test email | Setup stepper | Administrator | Setup email test (stepper: Test email) | `/admin/setup/mail-test` | `/admin/setup/mail-test/` |  |
| `setup_notification` | Test Slack | Setup stepper | Administrator | Setup Slack test (stepper: Test Slack) | `/admin/setup/slack-test` | `/admin/setup/slack-test/` |  |
| `setup_confirmation` | Finish setup | Setup stepper | Administrator | Finish initial setup (stepper: Finish setup) | `/admin/setup/confirm` | `/admin/setup/confirmation/` |  |
| `setup_cancel` | Finishing setup | Setup stepper | Administrator | (same) | `/admin/setup/cancel` | `/admin/setup/finishing/` | Route name to drop 'cancel' (#521). |
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
  form redirects. The sign-in, setup wizard and maintenance pages move to it
  last, in an optional pull request (NAV-13 in
  [ADM-12](../../../plans/stewardship/admin-portal.md#adm-12-admin-navigation-overhaul));
  until then they keep their current addresses.
- **Non-page routes move with their page:** a page's form actions and
  downloads move under its new URL and follow the same rules. The session and
  status JSON endpoints that scripts poll (`session/*`, the background and
  presence counts) keep their addresses.
- **Nouns, not verbs:** plural nouns for collections, a noun for each item,
  and actions are POSTs to the item or collection (no GET target such as
  `/delete` or `/remove`).
- **Old URLs keep working** as permanent redirects to the new ones: 301 for
  pages, 308 for form actions, so a page left open still submits. A redirect
  keeps the query string, where report filters live. An old URL with a
  campaign UUID redirects only when that UUID is the current campaign; for any
  other campaign it shows a plain refusal ("This campaign is no longer the
  current campaign") with status 410 Gone and never redirects, so a form left
  open on an earlier campaign is never re-posted into the current one. 410
  rather than 404 because the address was valid and will not work again: it is
  not a typo, and the reader should not retry it. This removes read access to
  an earlier campaign's pages and records for now: records from another
  campaign addressed by their own identifier (exports, digest snapshots) are
  refused as well, until the single-campaign change (#145). Because the target
  of a campaign-UUID redirect depends on which campaign is current, and
  browsers cache 301 and 308 responses indefinitely, those redirects (and the
  410 refusals) are sent with `Cache-Control: no-store`; redirects that name
  no campaign may be cached. Only a signed-in Admin portal user gets either
  answer: anyone else gets the sign-in refusal before the campaign is
  compared, so the choice between redirect and 410 never tells a stranger
  which campaign is current. Likewise a page for one record (an export, a
  digest snapshot, a cleanup request) refuses a record whose campaign is not
  current. Sent digest emails, bookmarks and the operator runbooks link the
  old forms. A test lists every old pattern with its target.

#### Navigation rules

These rules are requirements; a navigation test checks each one against the
registry and the rendered pages.

1. **Reachable.** Every registered page is a menu entry or names a parent in
   the registry, and following parents always ends at a menu entry or Home.
   Every child page is linked from its parent page in at least one state the
   test renders, or is the result of a form on its parent that the registry
   lists. No page is reachable only from an email, an error page or another
   group's page.
2. **Every report has a menu entry**, and every page listed under a report
   entry is reached from that report.
3. **A way back.** Every page except Home, the sign-in, the setup wizard and
   the pages the access gate shows instead of a page (maintenance, not
   configured yet, and error pages) shows a breadcrumb whose parent crumb is a
   link the viewer may open, or a "Return to" link when the parent cannot be
   linked. An error page offers a way back to the page the reader came from,
   or to Home; the maintenance and not-configured pages offer sign-out (the
   restore-review case is #537). Templates do not hand-write back links that
   repeat the trail.
4. **One name.** A menu entry's page heading equals its menu label; a child
   page's heading equals its breadcrumb label; the browser title starts with
   the same name. The only exceptions are the object-named pages in the table.
   Every "Return to" link names its target by that target's name.
5. **Flows end where the reader started.** After an action, the reader lands
   on the page whose task started it: a settings change's status page returns
   to the settings page; Preview and test email returns to the page it was
   opened from, remembered in the signed-in session as a change's origin is
   (never in the URL); a manual ParishSoft refresh stays on Refresh from
   ParishSoft and shows its progress there; sending a weekly report now ends
   on Emailed reports with a link to the report; Cancel returns to the page it
   was pressed on, never Home unless the flow started there.
6. **Messages link what they name.** A message that tells the reader to use
   another page links that page when the viewer may open it.
7. **Stable shape.** For each role, the menu's groups and entries are the same
   across modes and campaign states (setup pending excepted); an unavailable
   entry is greyed out with its reason, never removed, and stays reachable by
   keyboard with its reason announced.
8. **One home per concept.** A page appears under one menu entry only;
   another page may link it, but its breadcrumb always runs through its home
   (Campaign Ministries runs through Ministries).
9. **Every route classified.** Every Admin route is either a registered page
   or listed as a non-page (form actions, downloads, images, status
   fragments, sign-in and the setup wizard), and a test requires every new
   route to be classified. A route that answers only POST or only JSON is a
   non-page, not a page.
10. **Multi-campaign controls are greyed out.** Every reachable or visible
    control whose only purpose is multi-campaign work is an unavailable
    control with the tip "Disabled; will be removed with the single-campaign
    change (#145)", reachable by keyboard and announced as its description.
    The controls are: Copy campaign on Campaign settings; the "Choose a
    retained campaign" links on Participation and on Ministry requests; and
    the "create the successor draft" choice in the checklist after [Return to
    Testing](#return-to-testing-after-archive), which does not exist yet
    (#527), so until it does the server's refusal alone covers it. The server
    refuses the matching actions: cloning, creating a successor draft, and
    choosing a campaign other than the current one for a report. The retired
    chooser addresses themselves redirect (to Participation and to Ministry
    requests) rather than refuse. New campaign is removed outright ([decision
    11](#navigation-decisions)), not greyed.

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
POST (the sign-in rule, Chairperson suggestion, Chairperson decision and
Ministry assignment reviews); one-time reviews that refuse once their change is
confirmed (Copy campaign, and the campaign image and logo reviews); and Finish
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
Apply) on every settings editor (campaign settings, Campaign Ministries, Copy
campaign, pages and emails, dates and mail schedules, share options, member
talents, campaign images, Parish settings, Parish logos, each integration,
Ministries and Finish switching), the reviews started on Sign-in rules,
Ministry assignments and Chairpersons (sign-in rules, Ministry assignments,
Chairperson suggestions and Chairperson decisions) and every change's status
page; going live (Check readiness, Testing cleanup, Family links, Confirm
Production, Activation); sending to chosen Families (Choose Families, Review,
Send and follow); and report exports (Choose report, Prepare file, Download). A
locked campaign's read-only settings page is not in a flow. Those reviews show
only Review and Apply as current: the page they start on is a list, not step 1,
and a refused review shows its error page rather than going back to a form. An
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
    deployment has never had a campaign (#142; before #142 it was the setup
    wizard's First campaign step).
12. **Should the restore-review maintenance page get a way forward?** Yes, in
    its own issue: #537 (as proposed).
13. **Split Portal users into separate entries?** Split now (for example
    Sign-in rules and Chairpersons). This spec adds a third entry, Ministry
    assignments, for the Portal users table that fits neither.
14. **Should menu groups collapse?** Groups are collapsible, with the state
    remembered per browser.
15. **How should the campaign appear in Admin URLs?** No campaign identifier in
    Admin URLs: the current campaign is implied. Old URLs with campaign UUIDs
    redirect permanently. No schema change, and nothing to undo after #145.
    (Refined in the URL scheme: an old campaign-UUID URL redirects only when it
    names the current campaign; any other campaign gets a 410 refusal.)
16. **Where does the campaign sit in the path?** Nowhere (see 15): every page
    is `/admin/<group>/<page>/`.
17. **Which trailing-slash rule?** Every Admin page URL ends in a slash; the
    other form redirects; old URLs redirect permanently (301 pages, 308 form
    actions) (as proposed).
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
    New campaign stays removed (decision 11).
20. **No-script fallbacks?** (Coordinator.) None: the Admin portal
    [requires JavaScript](#javascript-requirement) (#565).
21. **Who gets Find a Family, and how does it search?** Administrators and
    Staff only, results scoped to what the role may see (Administrator); by
    CSRF POST (coordinator) (#561). It matches what the directory search
    matches, including any active Member's name and the envelope number
    (coordinator, #664).
22. **What are the #477 response lists?** (Coordinator.) One registry row,
    each named after its list, at `/admin/reports/responses/<key>/`; its CSV
    is a non-page.
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
    An optional last pull request.
28. **Old campaign identifiers?** (Administrator.) Refused with 410 for now;
    access to earlier campaigns returns with #145.
29. **A Home Today line?** (Administrator.) Yes, one per role; its contents
    are confirmed in NAV-18.
30. **Where is the test email's origin kept?** (Coordinator.) In the
    signed-in session.

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
A new sort starts again at page 1. The headings are the only sort control: no
page offers a separate sort menu, and a page's filter and export forms carry
the heading's current sort as a hidden field, so applying filters or
exporting keeps the order the table shows.

Page, size and sort are query parameters (`page`, `size`, `sort`), optionally
prefixed so two tables on one page keep their own place, and every navigator
link, heading and filter form keeps the page's filters and the others' choices.
Reports whose filters are private (the active parishioner family directory, System logs and the
campaign reports) keep them in POST state: their navigator and headings are
small CSRF-protected forms that carry the filters as hidden fields, so no
private value reaches a URL. System logs also accept a link carrying only its
non-private filters ([Logs](#logs)). A report whose rows an installed SQL selection
orders offers that selection's sort orders on the columns they order and its
page sizes; its other columns do not sort, since the schema owns those
orders. In v1 that covers:

- the active parishioner family directory (Family and DUID, 50 rows; Family code would need
  every code decrypted per view);
- Financial stewardship detail (Family, Annual pledge and latest response);
- the Additional information queue (Family and Submitted);
- the Ministry report (Ministry; Member and Submitted in one Ministry's view);
- the Ministry follow-up queue (Request, Member and Ministry).

Extending those vocabularies is a schema change. Columns that are only
controls (selection, actions, previews) never sort. Two short before/after
lists of pending setting changes (credential selection and integration
preview), the campaign mail test's at most ten reviewed Families, the
scheduled emails table (fixed sending order), and link preparation history
(panels, not columns) have no sortable columns.

A short table shown whole has no navigator: its headings carry only its sort
token (no page or size), and the page accepts nothing else for it. The two
session tables on Automation access are such tables (see
[portal user management](#portal-user-management)).

A table's navigators and rows sit in one region with a stable id (the table's
anchor, derived from its parameter prefix so two tables on one page differ),
and every heading and navigator control names that id as its URL fragment.
Choosing a heading, Previous, Next, a page number or a
rows-per-page value re-sorts or re-pages the table in place (#478): the
browser fetches the page the control would have loaded, requested as the
control would have requested it (a GET table's link or query, a POST table's
CSRF form with its private filters), and replaces that region, every other
table region on the page and the hidden state fields (sort, size, applied
filters, request keys) of the page's filter and export forms from the fetched
page, so no control is left carrying a choice the table no longer shows; the
reader's visible choices in those forms are kept. The reader keeps their
scroll position, rows still shown keep their selection, focus returns to the
chosen heading or control, the heading's `aria-sort` and a polite live region
announce the new order or the rows now shown, and a GET table's choice
replaces the address so reload, bookmarks and returning to the page keep it; a
POST table's address never changes. Applying a page's filters works the same
way (#484): the filter form is sent as it would have been (a GET query, or a
CSRF POST body), every table region is replaced, and so are the counts,
summaries and filter-dependent panels outside the tables (a report's matching
count and summary, the active parishioner family directory's export column list); focus stays on
the filter button and the live region announces the rows now shown in each
table, including none. A response the
server refuses (a malformed filter's 400, a denial, an unavailable report) is
shown as returned; a POST is never sent twice, since each report read is
audited. While a request is in flight, repeating the same submission is
ignored. The participation report's options reshape its statistics, chart and
exports rather than one table, so its options form is an
[in-place control](#in-place-controls) of all three panels instead. When
the fetch fails or returns another page (a sign-in), the ordinary page load happens and its fragment lands on the table rather than at
the top. Portal users, whose domain and address tables carry role forms bound
once at load, and the link preparation history keep only the fragment and
always load in full. A table region is one kind of region that
[in-place controls](#in-place-controls) refresh; that section states the
shared rules (one request at a time, when a POST may be sent again, the
fallback, focus and announcements).

Lists read straight from a growing database table page on the server with one
extra row to learn whether a next page exists, and count matching rows only up
to 10,000. Past that the navigator says "more than 10,000", omits the page
count and keeps paging by the extra row. They offer 25, 50 or 100 rows. On the
largest tables only indexed columns sort, so a page view never sorts or counts
a whole log.

A table with bulk actions has a selection column. Its header checkbox and a
Select all button choose every row on the current page; the bar above the
table shows how many rows are selected and enables its action buttons only
while at least one is. While none is, the disabled action buttons are
described by a short, visually hidden hint saying what to select (also shown
as their tooltip). Ticking a row never moves a control under the pointer: no
visible line appears or disappears, and the count and Select all button sit
after the action buttons, so their changing text cannot push them (#563). The
server validates every submitted selection.

### In-place controls

An Admin control acts where the reader is: it never reloads the page or sends
the reader back to its top (#519). The table controls above are one case;
the same shared mechanism (`ui-v1.js`) serves any control a page opts in.

- **Regions.** A page marks each part a control can change as a region, an
  element with a stable id (a table's region, or `data-in-place-region`). The
  id is also the control's URL fragment, which is required: the script finds
  the region by it, and an ordinary load lands on the region instead of at
  the top. The fragment must name a region in the same template as the
  control (not one a base or included template draws); a template test
  refuses an in-place control whose fragment names no region there.
- **Controls.** A link that shows another view of the page (`a[data-in-place]`:
  the [response dashboard](../reports/spec.md#response-dashboard)'s mode and
  grain, the [response lists](../reports/spec.md#response-lists)' mode, the
  [Family timeline](../reports/spec.md#family-timeline)'s mode and When sort,
  "Refresh current work" on "Background work", "Refresh list" on
  "Families on the form now", the page links of a mail delivery's
  evidence and attempt history, an information item's and a Ministry
  follow-up request's history, and a weekly information report, and the
  Refresh links of the key replacement status, Family email progress and
  Family test pages) or a form whose answer is the page again
  (`form[data-in-place]`, such as a POST whose server redirects back to the
  page: Save follow-up on an information item and on a
  [Ministry follow-up request](#follow-up-workflows); System logs'
  cross-links, whose answer is the filtered page; Acknowledge on a security
  event; Dismiss on an integration's finished key change; the
  [participation report](../reports/spec.md#campaign-statistics)'s Apply
  report options, which refreshes its statistics, chart and export panels)
  names its region by its
  URL's fragment. A POST form saves a change unless it is marked
  `data-in-place-read` (the System logs cross-links only read): a read may
  be cancelled by a newer choice and, with no answer at all, falls back to
  the ordinary submission. A form marked `data-in-place-filters` sets the
  page's filters from outside its filter form, so after the swap the filter
  form's visible fields, and any disclosure in it, show what the fresh page
  applied, and the next Apply, sort or page keeps them; every other swap
  leaves filters typed but not yet applied alone. A region a
  control can empty (the security events, the critical-problems banner) is
  drawn even when it has nothing to show, so the answer that empties it
  still carries it. A form marked `data-in-place-anywhere` changes a region
  every Admin page draws (the
  [critical-problems banner](#navigation-and-home)'s Acknowledge, which the
  server answers with Home from any page): any same-origin answer will do,
  and only that region is taken from it, never the answer's other regions,
  synced controls or address. A refused acknowledgement shows its error
  page whole, with its own explanation: that page draws the banner too, but
  its banner says nothing about the refusal. Because the banner's region is
  on every Admin page, any other in-place control that refreshes every
  region (a Save, a filter, a view switch) also refreshes the banner from
  its answer, so the banner shows what that answer's page shows. A report whose address has no
  time zone loads the same address with this browser's zone added in place,
  as it loads and again after any in-place refresh that brought a page
  without one (a daily-table link followed before the zone was applied):
  every other parameter and the address's own fragment are kept, focus
  stays where it is and nothing is announced (the request is a link marked
  `data-in-place-quiet`), and if it gets no answer the same address is
  loaded the ordinary way, without a jump to a panel. Because the chart,
  statistics and export panels are all regions, paging or sorting the daily
  table refreshes all three from the same answer: an export format chosen
  but not yet used, an open "Technical details" and the chart's inspected
  date return to their defaults, as an ordinary load would leave them. A checkbox marked
  `data-submit-on-change` submits its own `form[data-in-place]` as soon as it
  changes, with no Apply button (the Admin portal requires script), and keeps
  focus. Each page records the state it shows on the box. The box submits
  again only when its own change got no answer of its own and the page
  shows another state once nothing is in flight: it changed again while its
  request ran, a newer request overtook that one, or a save held it back
  (it then says "Still saving…" beside it too). It never resubmits after
  its request fell back to a full load or got a page shown as returned
  (no answer, a refusal, another page), and never sends a state that
  failed again until the reader changes the box or its own request succeeds, so
  a server that cannot answer gets one attempt and the fallback, not a
  loop (Automation access's "Include ended sessions"). "Refresh current work" keeps
  the reader's state filter, sort, rows per page and page; "Refresh list"
  keeps sort and rows per page and returns to the first page. A form's
  submit button belongs to its form even outside it (`form="…"`); a table's
  sort heading or navigator inside a `form[data-in-place]` (a selection form
  around its table) is still a table control. A POST form should carry a
  `data-in-place-message` ("Saved.") for the live region; without one the
  clicked button's text is announced.
- **Request.** The browser fetches exactly the request the control would have
  made, follows the server's Post/Redirect/Get redirect, and replaces every
  region the fetched page shares with this one, plus the counts, summaries,
  links and form state outside them that follow the view; views keep
  rendering whole pages, so no partial-page endpoint exists. A link marked
  `data-in-place-only` replaces only the region it names: the history
  pages of an information item and a Ministry follow-up request sit inside
  the item's panel, and paging them must not replace the Save follow-up form
  above or discard notes typed but not yet saved; a save still replaces the
  whole panel, history included. One request is
  in flight at a time: a newer choice cancels an older read, but a POST that
  saves a change is never cancelled, and other in-place controls and repeats
  are ignored (the live region says "Still saving…") until it settles.
- **Place, focus and announcement.** The reader keeps their scroll position;
  focus returns to the control (or its fresh copy; when that is gone, the
  link its `data-in-place-fallback` key names, so Next page on the last page
  hands focus to Previous page; when that is gone too or disabled, the
  region's first heading, else the region, or the page's own heading when
  the region is now empty); the region
  is marked busy while the request runs, and a polite live region says what
  happened ("By day", "List refreshed." and the rows now shown). A view
  choice replaces the address, and a followed redirect sets it to the
  redirect's page, so reload, bookmarks and Back never re-send a POST and do
  not step through choices.
- **Errors and fallback.** A POST that got an answer is never sent again; a
  saving POST is never re-sent at all; a read-only table POST with no answer
  falls back to the ordinary submission. A refused POST (an error answer, or
  a page with an error summary, such as a form re-rendered with its errors
  and the values the reader sent) that carries the control's region and was
  not redirected to another page is swapped in like a success (#562): every
  shared region and the data outside them that follows the view are
  replaced, and the address is unchanged. The error summary, which the base
  template draws above the page content, moves to the top of the region when
  it is drawn outside the region; this page's own older summaries outside
  the regions are removed then (a successful swap leaves them). The reader
  keeps their place, focus moves to the summary (or to the control when
  there is none), the fields it names are marked in error beside their
  message as the fetched page draws them, and the live region reads the
  summary's messages, or says
  the server did not accept the change when there is no summary. Any other
  refusal, and a POST's own answer that is not this page, is shown as the
  whole page, as a native submission would show it. That page keeps the
  window, so the old page is first told it is going away (a `pagehide` that
  is not persisted: the pollers that listen for it stop, and a reply already
  in flight can neither re-arm them nor act), and every timer id in the
  window is cleared, so no old timer runs beside the new page's own. The
  sweep relies on browsers numbering timers in sequence, as current engines
  do. A saving POST that got no answer at all may have been saved, so an
  alert at the top of its form says the server could not be reached and to
  reload the page to check. A redirect to another page (elsewhere, or the
  sign-in page) is followed by loading that page's address. A read (a GET)
  that fails falls back to the ordinary load, except when the browser cut
  it off because the reader is leaving the page: their own navigation goes
  ahead. A save with no answer still shows its note then, since a download
  link also starts leaving the page and the page stays.
- **Real targets.** Every control is a real link or form with its `href`
  or `action` and fragment, which the script requests; when an ordinary load
  happens instead (a fallback above), the fragment, kept across a
  fragment-less redirect, lands it on the region. The Admin portal
  [requires JavaScript](#javascript-requirement), so in-place work builds and
  tests no no-script fallback.
- **Policy.** The mechanism runs under the strict content security policy:
  first-party script and same-origin requests only; it changes attributes and
  classes and creates elements with text content, never inline script or
  style, and never uses `eval`. Swapped content is enhanced
  again as the page's own was (dates, selections, copy buttons, charts, and
  a form's conditional fields and its
  [complete-before-submit](#bootstrap-and-first-admin-wizard) gate), and a
  `parishkit:swap` event on it lets any other script do the same. A page
  that watches background work (`live-status-v1.js`) stops watching a
  status region an in-place refresh replaced, and watches the fresh one
  while it is still pending, so a Refresh or Dismiss that brings back work
  in progress keeps updating by itself; there is always one watcher per
  status region. A Refresh reads out the region's coarse status sentence
  when it changed, as a poll does. Dismiss refreshes only an integration's
  status line: if it brings back a new key change in progress (another
  Administrator started one), the settings form and notes below it keep the
  state the page was loaded with until the next load.

### Page help

Admin pages put the page's data first and keep explanation one deliberate
click away, without removing any of it. A page leads with its heading, its own
data line (such as the campaign name or counts), safety notices (such as
Testing mode) and then its data or form; at most a one-line hint that prevents
a likely mistake stays visible above the data. A caution that prevents a
likely mistake (what an action cannot undo or stop, or a lasting consequence
it has) is never only in the panel: it stays visible beside its control as a
one-line hint or a notice. The page's introduction and longer explanation of
how it behaves (how a credential is kept, how mail schedules work, the
placeholder reference) sit in an "About this page" panel beside the heading: a
native disclosure that starts closed, and that each browser remembers open,
per page type, once an Admin opens it. Every page with a panel has exactly
one, placed directly after its heading, so its control always sits on the
heading's line, open or closed, and stays in place when it is toggled, even
when the open help makes the page scroll (the theme always reserves the
scrollbar's space, so a centred page never moves sideways); opened, the help
appears on its own full-width line below. Only that open choice is
stored in the browser, and closing the panel removes it; with nothing stored
the panel starts closed and opens with a click or the keyboard. Help under a field is a short hint; longer
field explanations belong in the About panel or a click-to-open field tip,
never in hover-only tooltips, which touch and keyboard users cannot reach.

Admin pages are laid out for a laptop screen: compact headings, panels,
notices and table cells, and short filter forms in one row. The target is that,
with help closed, a page's first data starts inside the browser window of a
typical laptop (a 1366×768 screen, about 650 pixels of page after the
browser's own toolbars). Pages with short filters meet it now; report pages
with long filter and export forms meet it once those forms are collapsed by
default (a #227 follow-up). The Family portal keeps its own spacing.

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

Template tests guard these rules: one fails when a paragraph shown without a
click holds a message longer than about two sentences (50 words), one fails
when more than about one line (15 words) of help sentences shows between a
page's heading and its data outside the About panel, notices and links, one
fails unless each page's single About panel directly follows its heading, and
one fails when a label for an internal identifier or worker field (such as a
heartbeat, lease, data-load number or request ID) appears outside a Technical
details disclosure. The long-paragraph, introduction and internal-field
checks each keep a short, reviewed list of exceptions, and the long-paragraph
and introduction exceptions must shrink as their pages are converted. A
browser test checks on every component fixture with an About panel that the
closed control is drawn on the heading's line, to its right.

### Time entry

Every Admin field that takes a time of day accepts it in any common form,
shows how it read the entry, and stores the same canonical value as before
([#631](https://github.com/epiphany40223/parishkit/issues/631)). Admins type
`2:00`, `2am`, `0200` or `2:30 PM`, and a native time control's typed entry
differs per browser and locale (Linux WebKit ignores typing, #605), so these
fields are plain text boxes. The cost is the phone's own time picker, which a
text box does not offer; typing a short form such as `9p` is as quick.

**What is read.** Case is ignored, and so is white space around the entry
(the same set of white-space characters on the server and in the page).

- 24-hour, with a colon, `.` or `h` between hour and minutes: `02:00`,
  `2:00`, `14.30`, `14h30`. Seconds are accepted only in the colon form
  (`hh:mm:ss`) and only when they are `00`, since these fields are to the
  minute.
- A bare 1–2 digit number is an hour (`7` is 07:00); a bare 3–4 digit number
  is 24-hour `hmm`/`hhmm` (`830` is 08:30, `1430` is 14:30).
- Any of those, without seconds, followed by `am`/`pm`, `a`/`p` or
  `a.m.`/`p.m.`, with or without a space: `2pm`, `2:30 PM`, `2p`,
  `11 a.m.`, `830pm`. The hour is then 1–12; `12 am` is 00:00 and `12 pm` is
  12:00.
- `noon` and `midnight`.

Refused, with a plain message at the field: an hour above 23 (`24:00`), a
minute above 59 (`7:60`), an hour outside 1–12 with AM or PM (`13pm`,
`0am`), seconds other than `00`, and anything else, including digits from
other scripts. Nothing is guessed: a bare `7` is 07:00, never 7 PM, and the
reading shows both clocks so the Admin can see which. One exception keeps
saved work saving: a mail schedule saved before this change with seconds in
its time keeps that time when it is posted back unchanged.

**On the page** (`ui-v1.js`, fields marked `data-time-entry`):

- A readable entry's reading shows under the field at once, as the Admin
  types, on both clocks: "Reads as 07:00 (7:00 AM)".
- An entry that cannot be read shows its refusal there once typing pauses
  (about half a second) or focus leaves, so a half-typed `2:` is not flashed
  red; the field is marked invalid only once the refusal shows. Save is
  unavailable at once, and the hint that explains it (below) appears with
  the refusal. The refusal clears as soon as the entry reads. A server error
  under the field is removed on the first edit, and the live check then
  speaks for the field.
- A visually hidden live region beside the field announces its reading or
  refusal, only for that field's own typing (once typing pauses) or as focus
  leaves it, and only when the message changed. Loading the page, the page
  being shown again, and another control's change (a mail type, the refresh
  frequency) update the line silently. The reading line keeps one line's
  height while empty, so a first reading does not move the controls below
  it.
- When focus leaves a readable entry it is rewritten in the canonical form
  (`2pm` becomes `14:00`).
- While a shown entry cannot be read the form's submit buttons are
  unavailable, with a one-line hint below them ("Fix the time that can't be
  read to continue."), which the buttons name while it shows. The hint never
  shares the buttons' line, so their labels do not wrap. A field hidden by
  another choice (a mail type that takes no time, a refresh frequency without
  set times) does not hold Save, and a `formnovalidate` button stays usable.
- A list field (the ParishSoft refresh times) reads each entry. Entries are
  separated by commas, semicolons or spaces, and by new lines in a value
  posted without the page (a one-line text box drops line breaks from pasted
  text). A suffix standing alone after a space (`2 pm`) belongs to the entry
  before it, but not across a comma or semicolon (`2, pm` is refused). A time
  listed twice is reported in the reading and saved once.
- A list field whose blank stands for a value says so: a blank entry, or
  one of only separators such as `,`, reads "Blank reads as 02:00 (2:00 AM)"
  and saves that value. A blank single time has no reading; whether it is
  required is the form's rule.

The Admin portal [requires JavaScript](#javascript-requirement), so there is
no no-script fallback. The server stays the authority: one parser
(`parishkit.stewardship.time_entry`, through the form fields
`FlexibleTimeField` and `FlexibleTimeListField`) reads every post and every
`pk-stewardship admin` command that binds the same forms, such as
[`schedule preview`](../admin-automation/spec.md#schedules-and-configuration).
The page script applies the same rules, and one shared table of cases
(`tests/stewardship/fixtures/time_entry_cases.json`) runs against both, so
the two cannot drift.

**Time zones.** The fields read wall-clock times; each keeps its page's zone
rule under the [global presentation rules](../spec.md#global-presentation-rules).
Mail schedule send times stay in the campaign's time zone until their page
moves to the browser-local rule in its #558 PR. The ParishSoft refresh times
are in the parish's time zone, the recorded exception to the browser-local
rule that those rules describe (see also
[ParishSoft refresh schedule settings](#parishsoft-refresh-schedule-settings)).

**Fields.**

| Page | Field | Entry |
| --- | --- | --- |
| ParishSoft settings | At these times (full refresh) | List, parish time |
| Dates and mail schedules; first-campaign Mail schedules | Send time | One time, campaign time |
| Ministry follow-up | Contact attempt time | Native time control for now |
| Logs, reports | Date filters | Dates only, no time of day |

The Ministry follow-up contact attempt keeps its native time control until
its in-place save work
([#592](https://github.com/epiphany40223/parishkit/pull/592)) has merged; it
then moves to this entry as a follow-up. The planned
[refresh schedule editor](#parishsoft-refresh-schedule-settings)
([#632](https://github.com/epiphany40223/parishkit/issues/632)) replaces the
"At these times" list and uses this entry for its rule and exception times.

### Button labels

A button's label never breaks inside a word, and a one-word label never
wraps at all ([#614](https://github.com/epiphany40223/parishkit/issues/614)).
This covers buttons, links styled as buttons, submit inputs and sortable
column headings, whether they are POST buttons or GET links, so both kinds of
heading wrap alike. A longer label may wrap between words, so it never makes
a phone-width page scroll sideways; a table cell grows to fit its buttons,
and a wide table scrolls inside its own region. A browser test checks the
buttons and headings on representative pages at 320 px and 1280 px.

### Shared visual style

The portal and the emails share one visual style
([#732](https://github.com/epiphany40223/parishkit/issues/732)):

- **Design tokens:** `web/design_tokens.py` is the one source of the
  colours, corner radii, fonts and button shape. The portal stylesheet
  declares them as CSS custom properties, and a unit test fails if the two
  disagree, including any `var()` fallback in another stylesheet. Email
  components read the same module and inline the values, because mail
  programs cannot load the stylesheet.
- **Buttons:** templates draw every button, and every link styled as one,
  with the `{% button %}` tag. Its variants are primary (the default),
  secondary, large and link. It renders the stylesheet's classes and keeps
  each attribute in the order the template gives it, so in-place controls
  keep their `data-` attributes. A guard test counts raw button markup in
  every template against a list of reviewed exceptions that may only
  shrink.
- **Email buttons:** `email_button()` renders the same primary and
  secondary buttons for email: a one-cell table with inline styles from the
  tokens, `bgcolor`, and Outlook padding. The label is real text, so the
  button reads with images off and in an inverted dark mode. It links only
  to absolute http(s) addresses.

Notices, cards and headings, and each email's move to the shared
components, follow in later slices of #732.

## Background indicators

Admins have two always-visible indicators:

- **Families on the form now**: count of Family sessions with a heartbeat
  within the last 90 seconds, that is, Families with the form open in their
  browser; a Family that signed in but closed the form is not counted. Detail
  lists the Family name as on the active parishioner family directory (surname, then the
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

Reminder WorkGroup (Campaign navigation, #861) names the one ParishSoft Family
WorkGroup whose Families get no Reminders, for example a second Family record
kept for a staff member who already responded as a parishioner. It is the
campaign's optional `reminder_workgroup` setting: one plain-language text
field (the WorkGroup's name as ParishSoft shows it; empty turns it off),
reviewed and applied like any other change, and editable while the campaign
is live because staff mark Families in ParishSoft during the campaign. The
page says what the newest refresh found: no WorkGroup set, the name not read
yet (it takes effect at the next refresh), no ParishSoft WorkGroup by that
name, or how many Families are in it. What the exclusion does and when it
applies is in the background-processing specification's
[Reminder WorkGroup](../background-processing/spec.md#family-invitations-and-reminders)
rules; nothing else about those Families changes.

Hosted files (Parish data) is the Administrator-only library of
PDF, Office and image files that page and email content links or shows
with `{{ file.<slug> }}`: upload, placeholder copy, where each file is used,
and single or multi-select deletion that is refused while a file is in use.
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

### ParishSoft refresh schedule settings

The ParishSoft settings page edits the one
[refresh schedule](../background-processing/spec.md#refresh-schedule) of
full refreshes and quick updates, replacing the separate frequency, "At these
times" and quick-update fields
([#632](https://github.com/epiphany40223/parishkit/issues/632)). The design
follows common practice for schedules with exceptions (rules, then
exceptions, then a live preview); the
[refresh schedule plan](../../../plans/stewardship/refresh-schedule.md#research-notes)
records the products compared. It is written for a mid-level IT admin: plain
words, no cron text, and every refused or skipped time explained where it
appears. It follows the [in-place controls](#in-place-controls) and
conditional-field rules: nothing on the page reloads it.

**Time zone.** Schedule times are entered and shown in the parish's time
zone, a recorded exception to the browser-local rule
([#558](https://github.com/epiphany40223/parishkit/issues/558)): a recurring
wall-clock schedule belongs to the parish's clock, whose daylight-saving
changes decide when each refresh runs and when reminders are due, and a time
converted from another zone would shift twice a year. The page names the
parish time zone beside the editor ("Times are in the parish's time zone,
America/New_York") and, when the browser's zone differs, says so and shows
each preview time in the browser's zone as well. See decision 18 in the
[plan](../../../plans/stewardship/refresh-schedule.md#open-decisions).

#### Schedule editor

- **Presets** fill the editor, each described with what it includes:
  - "Nightly only": Full at 02:00; no quick updates.
  - "Nightly, then quick updates hourly" (the default): Full at 02:00; Quick
    every hour from 00:00 to 23:00 (23 quick updates; the 02:00 one is
    covered by the full refresh).
  - "Every 2 hours": Full every 2 hours from 00:00 to 22:00; no quick
    updates.
  - "Every 4 hours": Full every 4 hours from 00:00 to 20:00; no quick
    updates.
  - "Business hours": Full at 02:00 and every 2 hours from 08:00 to 18:00;
    Quick every hour from 07:00 to 19:00 (7 quick updates, at 07:00, 09:00,
    11:00, 13:00, 15:00, 17:00 and 19:00; the others are covered by the full
    refreshes).

  Choosing a preset when the editor has unsaved changes asks first, in
  place: "Replace your changes with this preset?" No preset needs to avoid
  the Family email windows by hand: the automatic exclusions do that.
- **Rule rows**, each "[Full | Quick] every [interval] from [time] to [time]"
  or "[Full | Quick] at [time]", with **Add a rule** and **Remove**. Time
  fields use the shared [time entry](#time-entry), with its live reading
  beside the field, and accept quarter-hour times; a kept off-quarter-hour full time is shown as a single time,
  labeled as kept from the earlier schedule.
- **Skip these times**: single times or from–to ranges.
- **Skip refreshes around Family emails** (on by default), with one line
  saying what it does: refreshes are skipped while a reminder is being
  prepared and while a Family email is being sent.
- **Edit as text**: the resulting full and quick times as two plain lists,
  for pasting. Editing them replaces the rules with single times, and the
  page says so before it does. The lists and the rows stay in step.
- The page states which full time is the nightly refresh and that it runs
  even while Family emails are being sent.
- A schedule saved before this page existed is shown converted to the
  equivalent rules, and saving other settings leaves it stored as it is; a
  change to it lists what will differ, as
  [stored schedule and upgrade](../background-processing/spec.md#stored-schedule-and-upgrade)
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

While any problem remains, Save is unavailable and a line beside it names
what is missing, for example "Fix 2 problems before saving: 00:00 is too
close to 23:50; the skip at 12:30 matches no refresh", each linking to its
row.

#### Seven-day preview

Below the editor, updated live as it changes, the page shows the next seven
days from today:

- **A grid**: one row per day and 96 quarter-hour cells, with full refreshes
  and quick updates marked differently (shape as well as color), excluded
  windows shaded and labeled with their cause ("Reminder: preparing",
  "Reminder: sending", "Invitation: sending"), and the current time marked.
  A kept off-quarter-hour time is marked in the cell it falls in. The grid
  is read-only. Below 600 pixels wide it scrolls sideways inside its own
  box, with the day labels fixed, so the page itself never scrolls sideways.
- **A list** of the same days for keyboard and screen-reader use, one
  collapsible section per day (today open, the others closed, each heading
  giving the day's counts): each day's refreshes with their kind, and every
  time that will not run, struck through with its reason, one time format
  per line ("06:00 quick update skipped: reminder being prepared", "09:00
  quick update covered by the 09:00 full refresh", "02:30 runs at 03:00:
  clocks go forward").
- Daylight-saving days are flagged in both.

The preview is computed by the server from the same functions the scheduler
uses, from the edited (unsaved) schedule and the campaign's current upcoming
Family emails. Windows for emails not yet sent are estimates, and the preview
says so.

#### Cost and freshness summary

- **Cost**: the estimated daily ParishSoft time and its share of the day,
  per day of the preview and as an average, for example "About 1 h 34 min
  of ParishSoft time a day (6.5% of the day): 8 full refreshes of about 7.3
  minutes and 16 quick updates of about 2.2 minutes." The durations are the
  medians of the last seven days' successful scheduled runs of each kind, or
  typical values (7.3 and 2.2 minutes, Production's medians in October
  2026), labeled as such, until there are three runs of a kind. Above 25% of
  the day the summary shows a warning; it never refuses.
- **Freshness**: the longest wait for new ParishSoft data over the seven
  days, counting the excluded windows, with the day it falls on, and when
  the alarm would sound, for example "New ParishSoft data arrives at least
  every 8 hours (00:00 to 08:00). If a full refresh is more than 30 minutes
  late, Administrators are alerted." Quick updates are described as
  checking the connection and catching the Family contact changes
  ParishSoft reports, not as keeping the data current. See
  [ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection).

The command line offers the same schedule, validation and preview (see the
[automation action inventory](../admin-automation/spec.md#integrations-and-credentials)).

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
column to where inclusion changes: Campaign Ministries while the current
campaign is live (see
[Changing a live campaign's Ministries](#changing-a-live-campaigns-ministries)),
and Campaign settings → Ministry selections for a draft. When a bulk preview
includes Ministries that are not in the current campaign, the preview names
them, says that activation does not add them to the campaign and links to the
same place.

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
Campaign settings, Create the campaign and (before #142) first-campaign setup.
A name the form can already show
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
Campaign settings, Create the campaign and (before #142) first-campaign setup
follow the same cleaning, with
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
live campaign to its own Campaign Ministries page, which offers only
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

### Create the campaign

System setup ends without a campaign
([#142](https://github.com/epiphany40223/parishkit/issues/142)). The campaign
is created afterwards, by an Administrator, from **Create the campaign**: the
next step Home offers while the deployment has never had a campaign (the
Campaign setup menu entries stay greyed "No current campaign" until then). It
is not a multi-campaign control: once any campaign exists it is not shown,
and the server refuses it, as it refuses Copy campaign and successor drafts
until #145 and the close-out wizard (#527).

**Admission.** Create the campaign follows the
[draft-creation rules](#campaign-configuration) above, with three further
conditions: no campaign row may exist in any state, so it never creates a
successor; no campaign work or credential hold may be in progress
(background campaign work, or a Testing cleanup); and a ParishSoft snapshot
must have been promoted, so there is a catalog to choose Ministries and funds
from. The page, its review and its confirmation each check these, the
confirmation under the same lock that records the request, so a stale page
or a direct request cannot bypass them.

**First page.** It collects the campaign's basics in the ordinary campaign
form: name, modules, start and end dates, Ministries (from the loaded catalog,
initially all active ones) and, for Financial stewardship, the financial
period and current and comparison funds. The time zone comes from the Parish
profile, as it does for every new draft. The review then shows the whole new
campaign, and confirming records one versioned configuration request, in the
same format as every other campaign save, that adds the draft campaign with
its [default content](#default-content) and the default share options;
Member talents show their built-in defaults until edited.

**After confirming.** The next page is the request's status page, as for any
campaign save. The configuration installer applies the request, and the
database checks admission again at activation: a new campaign record becomes
the current campaign only when the current-campaign pointer is empty in
Testing mode and every earlier campaign is archived or purged, the rule every
new draft follows, so of two creation requests confirmed at once one applies
and the other fails. Once the request is applied, the status page offers
**Load the campaign's ParishSoft data**, which requests a full ParishSoft
refresh and opens that refresh's progress page. The refresh can only be
requested after activation, because a refresh reads the current campaign when
it is requested. It reads the campaign's giving window and creates the
campaign's Families and Family codes, as a refresh does for any current
draft. If no one presses the button, the next scheduled quick update cannot
continue from its cursor, because the giving window changed, and runs as a
full refresh instead. Go-live readiness, which already requires the
population to match the current ParishSoft data, stays incomplete until a
full refresh has finished.

**Guided steps.** While the current campaign is a draft in Testing mode, the
Campaign setup pages show a stepper with the setup wizard's look: Basics,
Ministries and funds, Share options and Member talents (each shown only when
the campaign's modules use it), Pages and emails, Dates and mail schedules,
Campaign images, Preview and test email, and Go-live readiness. Each step is
the existing settings page, not a separate staging page, so every save is
that page's ordinary review, confirm and apply. Basics counts as completed
when the campaign is created; any other step counts as completed once a
change confirmed on that page has been applied for this campaign, or for
Preview and test email once a test of the current invitation was accepted.
Defaults (the default text, share options and talents) never complete a
step, and Go-live readiness completes only by going live. Next and Back are
plain links to the neighbouring step: they never save, discard or submit
anything, so an Admin who leaves a page without confirming its review keeps
the campaign as it was. After a change is applied, its status page offers
Continue to the next step. The stepper is presentation only; each page still
enforces its own prerequisites. Go-live readiness is the last step, so the
go-live flow (and its redesign in #462) follows on from it unchanged. Once
the campaign leaves draft (it is scheduled or goes live), the stepper is no
longer shown.

**Existing deployments.** A deployment that already has a campaign, such as
Production, never sees Create the campaign, and its campaign, being active,
never shows the stepper; nothing about its setup, campaign, Family codes or
emailed links changes.

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
- a successful full ParishSoft refresh with expected-tenant and scope
  validation, recent as the [Go live page](#go-live-page) defines it: Start
  accepts an older one because it queues the go-live's own, and
  confirmation requires that one (or a later one);
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

The Admin-only Family list (the **Testing submissions** page) opens only
while the campaign is the current Testing draft, as does Go-live readiness,
the only page that links to it. Afterwards the list answers with a plain
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
campaign go-live gate, records the inventory/aggregate described below, queues
an idempotent cleanup task and queues the go-live's own full ParishSoft
refresh. The gate rejects new Testing submissions,
test sends, campaign content/configuration changes, and Testing campaign work;
existing authenticated pages explain that go-live is in progress. The same
transaction invalidates the rehearsal epoch and its sessions; the cleanup
inventory includes rehearsal credential detail under the
[credential lifecycle](../architecture/spec.md#family-credential-security).
Readiness/final confirmation verify that invalidation and completed credential
cleanup without changing stable Production Family codes. Operational
notifications and manual ParishSoft refreshes continue; scheduled refreshes
[wait for the go-live](../background-processing/spec.md#refreshes-wait-for-go-live).

The worker deletes the recorded Testing corpus in bounded, checkpointed batches
and exposes progress/retry in the web workflow. It rechecks the gate before each
batch and never touches production or operational rows. When cleanup completes,
the request becomes `cleanup_complete`; deleted rows are not restored if a
later check fails or the Admin cancels. Cancellation before activation releases
the gate and leaves the campaign in Testing with whatever cleanup completed.

From `cleanup_complete`, once the system has
[prepared the inactive Family links](../background-processing/spec.md#go-live-sequencing)
on current ParishSoft data, fresh authentication and typed confirmation
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
(formerly Withdraw from Production). That action
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
making the `scheduled` state reachable before the start date.

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
status while any is still on its way.

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
Testing cleanup, and once to confirm Production, with the only Google sign-in.
The system does everything in between. The page is a stepper with the same
Next/Back and completion marks as the setup wizard, and each step shows only
what applies now:

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
   go-live**. Starting needs no fresh sign-in, as today. The server re-checks
   readiness at the POST. Every problem still blocks except one: a full
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
until the attempt's **hold end**, which is the earlier of 60 minutes after
the attempt's refresh promoted and 3 hours after the attempt started (the
cap, for a refresh that never promotes). The exact rule, used by every
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
**Production activation** afterwards. A GET to the retired cleanup, links or
confirmation addresses redirects to the Go live page; their POST endpoints
keep working for one release so a form left open still completes, then are
removed. The
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

Decisions are taken only during the review: once the site is released, the
guard refuses them, and settling the remaining holds is #757. An undecided
hold stays in force after release. An undecided invitation also keeps back
every reminder of that Family. Besides the listing, the web login may insert a
hold row directly; the hold guard admits one only during a review and for its
current restore, and a hold only keeps mail back.

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
because a hold only keeps mail back. The page that shows what was restored, lists the held emails by
send, and offers settlement and release is #537's second part.

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
and held-message state is resolved. The form offers only choices the server's
rules accept (#563): a message type appears only while it has held messages;
Release only after the provider and sender check has passed; Cancel only for
types with no email still being handed to the mail service or not sure it
arrived; Clear only when the pause would clear with no type selected; and the
types only for Release or Cancel, which need at least one ticked. With no
choice left, Preview is shown unavailable with the reason. Whether reports are
still being prepared is checked only by the server, when the preview is built.
Preview otherwise follows the
[complete-before-submit rule](#bootstrap-and-first-admin-wizard).

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
- **Skipped: in the ParishSoft Reminder WorkGroup** (shown when non-zero; a
  column of the send history): a reminder skipped, or its unsent email
  cancelled, because the Family is in the campaign's Reminder WorkGroup
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

Superseded for the Admin portal by the
[navigation decisions](#navigation-decisions) 10, 11 and 19: reports show the
current campaign only, and the checklist's "create the successor draft" choice
is greyed out under [navigation rule 10](#navigation-rules) until #145.

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
visible. Acknowledgements and notification outcomes are audited. Each
Acknowledge acts [in place](#in-place-controls) on Home: the event leaves the
list without a reload.

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
Refresh from ParishSoft menu entry), or with the "Run a full refresh now"
button on the ParishSoft settings page and in the Admin home page's refresh
notice (same capability and CSRF rules). The action inserts a durable task and
returns immediately to the Refresh from ParishSoft page, which shows the task's
status ([navigation rule 5](#navigation-rules)). If a poll is running, no
concurrent poll starts; one manual full refresh may be queued to follow it.
Repeated clicks return/link to the existing queued run.

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
[in place](#in-place-controls) (#519): the request's panel, its form and its
history are refreshed where the reader is, and "Follow-up saved." is
announced.

Both follow-up queues remember the view the reader is working through
([#534](https://github.com/epiphany40223/parishkit/issues/534)). Each queue page
remembers its applied filters, sort, page and page size in the signed-in
session (never in the URL), as a change's origin is remembered (navigation
rule 5). Links carry only an opaque token, and the session keeps the last 20
views. An item opened from the queue carries the token, so its "Return to"
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
(Administrator decision, 2026-10-04). It acts [in place](#in-place-controls):
the next item's panel replaces this one, the address becomes that item's, focus
moves to its heading, and "Follow-up saved." is announced with the item's name.
A refused or stale save stays on the item exactly as Save does. The Save gate
holds both buttons until the save is valid. Opening the next item records the
same read audit as opening it from the queue.

Ministry workflow permissions are row-scoped. Admin/Staff see all; leaders see
and edit only assigned Ministries. The interface supports queue filters,
status/outcome, contact-attempt entry, notes, history, and links to the
Member's authorized report detail. It never exposes financial or unrelated
Family data.

Ministry follow-up has no assignment (Administrator decision, 2026-10-04,
[#552](https://github.com/epiphany40223/parishkit/issues/552)): each
Ministry's leader contacts the parishioners who asked to join or leave it,
and the portal does not track coordination between people. The queue and
request pages therefore offer no assignee field, filter or column and no bulk
selection; the status choices are New, In progress, Resolved and Closed: no
response. A request recorded as Assigned before this decision reads as New
(that state only ever meant "has an assignee") until its next save stores the
chosen status with no assignee; until then an explicit New filter (queue or
Ministry report) does not match it, while Unresolved and Any status do. Past
history entries keep the assignment they recorded. The data model keeps the unused column and state, as described in
the [data specification](../data/spec.md#follow-up-records).

The request form shows only the fields that apply. The outcome appears only
for Resolved, which requires one; Closed: no response always records the
outcome No response, and open statuses have none. A contact attempt's date,
time and "What happened" appear only once a channel is chosen; the date and
time are then required and the description is optional. Notes are optional
except for the outcome Other, which the data model requires. Hidden fields are
disabled and not sent, and Save stays unavailable, with a short hint saying
what is missing, until every visible required field is complete. Fields
re-evaluate whenever the browser restores the page or its values (going back
to it). The Admin portal requires JavaScript
([#565](https://github.com/epiphany40223/parishkit/issues/565)), so there is
no script-off variant; the server still ignores whatever does not apply to the
chosen status or channel and refuses an incomplete Resolved. The outcome list
offers only the outcomes the request's kind can record: Joined ministry only
for a join and Left ministry only for a leave, from the same rule the server
applies. If the server still refuses a save (for example a contact time in the
future), it shows the same request page again in place, with the submitted
values kept, a summary naming the one problem, and that problem shown
[at its fields](#bootstrap-and-first-admin-wizard): a missing or unsuitable
outcome marks Outcome, Other without notes marks Notes, a contact attempt in
the future, or one that reached the server without a usable time zone (see
below), marks its Date and Time, and an incomplete one marks whichever of
them is missing (both when one is malformed). The view maps each refusal to
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
rule](../spec.md#global-presentation-rules)). The zone is sent only with a
contact attempt. When the browser reports no zone, the note stays hidden and
Save stays unavailable with a hint to check the computer's time zone setting. A
contact attempt that reaches the server without a usable zone (a page opened
before this rule, for example) is refused in place like the other correctable
refusals, keeping the typed values and asking the person to save again.

Manual census items may be marked resolved externally or ignored by Admin or
Staff, with notes, on the
[Census changes](../reports/spec.md#pending-census-changes) worklist.
API-writable changes are view-only for Staff. Admin review and publication
follow the [data workflow](../data/spec.md#review-and-publication).

The ParishSoft API cannot change Ministry rosters, so a join or leave is
always entered in ParishSoft by hand
([#528](https://github.com/epiphany40223/parishkit/issues/528), gap G28). A
request resolved by a person as *joined* or *leave confirmed* carries an
**Entered in ParishSoft** tick that Staff and Admin set or clear in place;
who and when are kept as history. A request resolved because the ParishSoft
roster already shows the change (it has a resolution source) needs no tick
and shows *Already in ParishSoft*. Ministry leaders see the tick, read-only,
on their own Ministries' requests. The follow-up queue offers a *Roster
changes to enter* filter (resolved joins and leaves with no resolution
source and not yet ticked), and the
[Ministry change summary](../reports/spec.md#ministry-change-summary)'s
joiner and leaver lists and their export show the tick, so staff can work
through the roster changes in one list. The tick is a new field on the
request and ships with its forward migration. Marking a request resolved
never changes ParishSoft, and this tick only records that a person did.

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
delivers it. Until ADM-13 lands, the runbook steps listed under
[runbook steps the page replaces](#runbook-steps-the-page-replaces) remain
the way to do these things.

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
- **Problems first.** The top of the page lists every current problem as one
  plain sentence, saying what is wrong and what the Administrator can do: a
  button on this page, another Admin page, or a runbook section for the
  server operator. For example: "No backup has finished since 2:00 AM
  yesterday. Take a backup now, or ask the server operator to check the
  backup schedule." When nothing needs attention, the page says "Everything
  is working", with the time of the last check. A condition that affects
  several services or processes at once (debug logging, a service that
  stopped or never reported, a halted or outage-paused mail sender) is one
  problem whose sentence names them all ("Debug logging is on in
  Production in the web portal, background worker, scheduler and both mail
  senders"), so Home and the page list it, and screen readers hear it, once
  (#686).
- **Panels.** Below the problems come the panels described under
  [health panels](#health-panels), in this order: why sends are waiting, mail
  sender, ParishSoft refresh, backups, debug logging and version. During a
  send, the first two are what the Administrator watches, so they come first.
- **Home.** For viewers who may open this page, Home's problems list (see
  [Home page](#home-page)) adds one line for each System health problem,
  linking this page. Other viewers see no such line. These lines join the
  problems list that ADM-12's Home work (#568, NAV-18, with its Today line)
  renders, so whichever of the two lands second adds its lines to the
  other's list rather than making a second one. ADM-13 landed first, so
  until NAV-18 its lines are Home's problems list, a panel at the top of
  Home, each with the page's own sentence. Until NAV-18 merges the lists,
  a problem Home already reports elsewhere (a failed ParishSoft refresh, a
  failed off-site copy) can appear twice on Home: once in its own notice
  and once as a System health line. The
  [debug logging banner](#navigation-and-home) and the critical-problems
  banner link this page too.
- **Live and in place.** The page needs JavaScript, as every Admin page
  does (see the [JavaScript requirement](#javascript-requirement)). It
  updates itself like the other
  [self-updating pages](#background-indicators): every 10 seconds, it reads
  a status fragment that records no audit row and does not extend the
  session, and it pauses while the tab is hidden. Only opening the page
  records an audited `system_health_viewed` event. The fragment
  (`system_health_status`) and each action's route are listed in the
  automation spec's [action inventory](../admin-automation/spec.md#operations). Each action's preview,
  confirmation and progress appear in place, through the shared
  [in-place controls](#in-place-controls), so the page never reloads or
  moves the reader to the top.
- **Plain words, help hidden.** Times are shown and entered in the browser's
  local time zone, without "UTC" labels. States use words ("Halted",
  "Waiting for the daily limit"), not internal names. Internal names, such as
  an incident kind, appear only under a closed "Technical details"
  disclosure. Each panel shows its state and its button. What each state
  means, and what a panel cannot see, is in the page's "About this page"
  panel ([page help](#page-help)), hidden by default. The panels below say
  which explanations go there.
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

### Health panels

#### Why sends are waiting

This panel answers "why aren't the emails going out?" (ADM-19). It lists
every reason Family email is waiting right now, with a count, when the
reason ends (if known), and a link to act on it:

- **Mail is paused by an Administrator** ([live delivery
  pause](#live-delivery-pause)): who paused it, when, the reason they gave,
  and a link to Pause and resume mail.
- **Waiting for the daily limit:** the recipients sent in the last 24 hours,
  the limit, and when sending resumes. For example: "1,612 of 1,600 emails
  for Families sent in the last 24 hours. Sending resumes at about 3:10
  PM." It is the same count the mail sender compares with the limit, read
  by a definer function
  (see [bulk Family send](../background-processing/spec.md#bulk-family-send)
  and the
  [Family mail dispatch guide](../../../guides/stewardship-family-mail-dispatch.md#two-mail-consumers)).
  It is shown even when sending is not held, so the Administrator can see
  the limit coming. The web login reads it through the `SECURITY DEFINER`
  function `stewardship_family_daily_sends_v1()` (frozen migration 0012,
  with #382's `(previous_state, created_at)` index on the outbox events),
  which returns one number and takes no argument: the count is made of the
  routed recipient lists, which the web login never reads. This is the
  24-hour count that #382 (item M3) asks to show. The lasting record of
  each hold on Outgoing mail stays with that issue, and both use the same
  words for each reason.
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
reports and alerts), Gmail's own limit, and the outage pause.

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

When the two consumers differ, each has its own line. An open
`mail_provider_unavailable` or `mail_provider_failed` incident is named in
words, with when it opened. "About this page" explains each state and what
the system does about it.

#### ParishSoft refresh panel

This panel shows the ParishSoft data the system is using and why a refresh
might be held back:

- When the last full refresh and the last quick update finished, whether a
  refresh is running now, and a link to
  [Refresh from ParishSoft](#manual-parishsoft-refresh). The panel states
  "ParishSoft data as of" and "Connection" as two separate lines, with the
  wording Home and the refresh page use, defined in
  [ParishSoft data age and connection](../operations/spec.md#parishsoft-data-age-and-connection).
- **Refreshes held during a send:** while a bulk send is in progress the
  scheduler holds quick updates and daytime full refreshes, which run as a
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
  caption under the table says so. The counts come from the refusal's
  durable record, not the worker's log.
- **A refused organization** (`source_tenant_mismatch`): no button. The
  panel says to stop and check with the parish before changing anything,
  linking the
  [ParishSoft outage runbook](../../../guides/stewardship-launch-runbooks.md#parishsoft-outage).
- **Old copies not being removed** (`source_retention_failing`), linking the
  same runbook section.
- **An unknown Reminder WorkGroup** (#861): the newest refresh found no
  ParishSoft Family WorkGroup with the campaign's
  [Reminder WorkGroup](#parish-and-integration-configuration) name, so no
  Family's Reminders are being skipped. The notice names the setting and
  links its page; the refresh itself logs a WARNING. It is a panel notice,
  not one of the problems listed first, and it clears when a refresh finds
  the name or the setting is cleared.

"About this page" explains the difference between full refreshes and quick
updates, why quick updates pause during a send, and that every later
refresh is refused until a drop is fixed in ParishSoft or accepted.

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
  copies are not set up, the panel links the setup page.
- **Problems:** the open `backup_rpo_breach`, `backup_offsite_failed` and
  `backup_key_changed` incidents, in words.
- **Requested backups:** the newest backup requested from this page, with
  its state: waiting for the server, running, finished, failed (with its
  category in words), or expired. See [take a backup now](#take-a-backup-now).

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

This panel shows whether debug logging is in effect in each online
application service (web, worker, scheduler, mail-dispatch and the
installers), from each one's
[service status record](#service-status-records), and whether the
**debug-off switch** is set (see
[turn off debug logging](#turn-off-debug-logging)). A service's debug
logging is in effect when it was started with `PARISHKIT_DEBUG_LOGGING=1`
and the debug-off switch is not set.
Each such start also logs one WARNING `debug_logging_enabled` line next to
`startup_validated` in that service's log, once per process (each web worker
logs it), so someone reading the logs sees it too (#546). It is not an
operational incident, since this panel already reports it. The line reads
only `PARISHKIT_DEBUG_LOGGING`, so once the debug-off switch exists, it must
honor the switch too.

- **In Production, any service with debug logging in effect is a problem**
  at the top of the page, and the panel offers **Turn off debug logging**.
- In Testing it is shown as information, not a problem, because the
  pre-launch deployment tool turns it on deliberately
  ([deployment runbook](../../../guides/stewardship-deployment-runbook.md#pre-launch-fast-deploys)).
  The button is offered in Testing too.
- While the switch is set, the panel shows who set it and when, and that
  the setting on the server still says "on".

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
  again"). A repeated click returns the first result.
- **Unavailable buttons are greyed, never hidden.** A button that cannot be
  used right now stays in place, unavailable, with its reason shown and
  announced the same way as an unavailable menu entry's (see
  [stable menu shape](#stable-menu-shape)). Each action lists its reasons.
  Greying only helps the reader: every check also runs on the server, in
  the service function and inside the action's transaction.
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
- **Schema order with ADM-11:** each action's SQL guard is written without
  the automation clause, because `stewardship_automation_fresh_v1` exists
  only from ADM-11 PR 5. ADM-11 PR 5's migration amends every ADM-13 guard
  already installed to accept it, and an ADM-13 guard installed after ADM-11
  PR 5 includes it from the start. Until then, each command is a pending
  exemption in the action inventory.
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
3. When a request is waiting, request mode tries once to take the backup
   lock that every backup run now takes, without waiting. If another backup
   holds it, request mode exits quietly and leaves the request waiting. It
   then takes the startup interlock shared, as every backup run does today;
   if offline work holds it, it exits quietly the same way. Otherwise it
   marks the request `running` and runs the ordinary backup, then marks the
   request `finished`, linked to its backup record, or `failed` with the
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
   drill's warning, and adds a step that marks every restored request
   `expired`, so a restored request is never run. A restored request older
   than 30 minutes has expired anyway.

**Preview.** When the last backup finished, whether off-site copies are on,
and the last backup's size; that the new backup follows the usual retention
rules and adds to disk use until they remove it; and, during a bulk send,
that the backup will wait until the send finishes.

**Unavailable when** (greyed, with the reason): a request is already waiting
or running ("A backup was requested at 10:42 AM and is running"), or the
system is in restore review. There is no daily limit: one request at a time
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
   or contacts: Families present and counted in the baseline but missing,
   or no longer counted, in the refused load (for a Member or contact count,
   the Family the lost record belonged to). Each example holds only the
   Family's DUID and its name as the active parishioner family directory shows it, taken from
   the baseline. Ministry, roster and fund counts have no examples, and a
   tenant-mismatch refusal records none. The examples go in a new
   parish-level table that the worker's login writes and deletes and the
   web login reads. It belongs to no campaign, so campaign purge does not
   touch it. Nothing else from the refused load is kept: the worker discards
   it as it does today. Backups gain no new personal data, because each
   name already exists in the promoted baseline that every backup holds.
3. Confirming records a one-time acceptance (a new row: the refused run,
   every recorded count's before and after values, who, when and the fresh
   sign-in instant) and requests a full refresh, as **Refresh now** does.
4. The next full refresh honors the acceptance only when every recorded
   count's new value is at least the reviewed after value: no count falls
   below what the Administrator saw. The before values are recorded with
   the acceptance but not compared. The eligibility baselines come from the
   current data as well as the recent full refreshes, so every quick update can
   move them, and binding them would refuse almost every accepted refresh
   while quick updates run every 15 minutes. A count that passed in the
   refusal keeps the limit in effect. A load with no Families or no Members
   is still refused. Any other result is refused again, with a new refusal
   to review.
5. **Lifetime.** The acceptance ends when any full refresh is promoted,
   whether or not it used the acceptance, when a new refusal is recorded,
   or 24 hours after it was given. Quick updates never use it and never end
   it. The refresh that uses it records in its manifest the acceptance and
   who gave it, as the environment override's limit is recorded today.
6. **Examples are deleted** when their refusal is no longer the newest full
   refresh's outcome, when a full refresh is promoted, when the acceptance
   ends, or seven days after the refusal, whichever comes first. These four
   triggers and the seven days are the default, pending Administrator
   confirmation; decision 4 says only that examples are kept until the
   decision or their expiry. The worker's housekeeping deletes them, and the
   page and the command never show an example whose deletion condition is
   already met, even before housekeeping runs. A restore can bring back
   examples from the backup; the page hides them by the same conditions,
   and the restore procedure deletes them. Their retention is listed with
   the other bounded cleanup in
   [temporary retention and housekeeping](../operations/spec.md#temporary-retention-and-housekeeping).

**Preview.** Every recorded count, with its before and after values and the
share it fell by, marking the ones that failed. It says that records
missing from ParishSoft leave the campaign's data after the refresh, and
that a mistake in ParishSoft should be fixed there, followed by **Refresh
now**. Under each failing count it lists the example Families (name and
DUID), which only Administrators see, since only they may open the page.
Example names never appear in a log line, an audit context, the refusal's
log entry or a notice. Opening the preview records the existing
`system_health_viewed` event only; the preview's command prints the counts
and example DUIDs without names, as the automation spec's
[personal data rule](../admin-automation/spec.md#personal-data-on-the-command-line)
requires.

**Unavailable when** (greyed, with the reason): the tick box "I checked
this in ParishSoft, and the change is real" is not ticked; the refusal is
no longer the newest full refresh's outcome; or an acceptance is already
active.

**Checks (server):** the [shared rules](#shared-rules-for-health-actions);
the newest full refresh was refused for a drop and no full refresh has been
promoted since (quick updates do not count, as #510 notes); the counts
match the preview binding; the tick box was ticked; no other acceptance is
active; and the refusal is not a tenant mismatch.

**Confirmation:** the tick box, then confirm.

**Audit:** `source_drop_accepted`, plus the manifest record above.

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
   commands) read it when they connect.
3. **Child processes.** A process passes its effective state to every
   helper subprocess it starts: while the switch is set, it removes
   `PARISHKIT_DEBUG_LOGGING` from the helper's environment, so a helper
   that never reaches the database cannot log in debug form either.
4. The page shows when every service reports debug logging off. The debug
   logging banner stays until they all do.
5. Lines written before a process first reads the switch at startup are not
   covered. The preview says so.

**Preview.** The services with debug logging in effect; that lines already
written stay in the logs; that the setting on the server still says "on"
until the server operator recreates the services; and the startup gap
above.

**Unavailable when** (greyed, with the reason): the switch is already set
("Debug logging was turned off by … at …"), or no service has debug logging
in effect.

**Checks (server):** the [shared rules](#shared-rules-for-health-actions)
and the switch is not already set.

**Confirmation:** a confirm button. Nothing typed.

**Audit:** `debug_logging_turned_off`. Each process's WARNING log line
records when it applied the switch.

**Clearing the switch.** Clearing it lets processes started with the
setting log in debug form again; it can never turn debug logging on in a
process that was started without the setting. The server operator can
always clear it with a new operator command,
`pk-stewardship debug-off-clear` (recorded with `actor_kind` `operator`).
In Testing mode the page also offers **Allow debug logging again**, which
clears the switch under the same rules and records `debug_logging_allowed`.
In Production the page never offers it, and the server refuses it
([decision 3](#system-health-decisions)): its SQL guard refuses unless the
global mode is Testing, in addition to the shared checks. Turning debug
logging off is offered in both modes.

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
| [Launch runbooks: accepting a large ParishSoft change](../../../guides/stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change), steps 1, 3 and 4 | The ParishSoft refresh panel and **Accept this change once**. Step 2, checking in ParishSoft, stays. |
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
  on. The restore review page that follows a restore is #537.
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
  entry to show.", and the server refuses a form with none with its own
  message, not the identifier guidance. A campaign filter with Audit record
  unticked lists nothing, and its empty table says to tick Audit record.
  There is no separate Source field. For one release the server still accepts the
  retired `source` value an older open tab may send, mapped onto the
  checkboxes (`audit` clears the levels, `operational` leaves audit records
  out, `both` includes them), and carries only the checkboxes from then on.
  The critical-events banner's link ticks Critical alone, without audit
  records, and "Same campaign" ticks Audit record alone;
- action/type, campaign, entity, actor, task/request correlation, text,
  Ministry, date range, and the six Show (level and audit record) filters.
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
  private (the Show choices, type, Ministry, From and Through with their
  zone, rows per page and sort). The search text, identifiers (actor,
  correlation, campaign, subject) and the paging snapshot stay in POST
  bodies, following the [Admin tables](#admin-tables) rule that private
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
- cross-links from every entry: "Show related entries" (same correlation
  identifier), "Same actor", "Same subject" and "Same campaign" (audit
  records) and, for task
  entries and views of one task's page, "Open task" to the background task
  page. Each filter travels in a POST body like the form's and applies
  [in place](#in-place-controls): the filter form above then shows the
  filters applied ("Filter by identifier" opened), and focus returns to the
  same entry's button in the filtered list, or to the list when that entry
  is no longer in it. The raw
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

Ministry filtering takes a Ministry DUID and matches an entry whose detail
names it as `ministry_duid` or in any sorted Ministry list, so it includes both
interactive event identifiers and the
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
second, with the zone name (no UTC offset, #635), so entries can be compared
with times recorded elsewhere; the zone name also tells the repeated hour
apart when clocks fall back. The From and Through date filters are whole days in
the browser's time zone, following the
[timestamp rule](../spec.md#global-presentation-rules): the filter form
carries the browser's zone, the server turns From into the start of that local
day and Through into the start of the next local day (exclusive), so a
daylight-saving day is 23 or 25 hours long, and paging, sorting and export
carry the zone with the other filters. A date sent without a zone the server
knows is refused with a hint to apply again; while the browser reports no zone
the page keeps Apply unavailable whenever a date is entered. The critical-event
banner's link sends the browser's zone with a From day one day before its
24-hour window, so the window is covered in every zone; when the browser
reports no zone it sends no From day. Export
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
