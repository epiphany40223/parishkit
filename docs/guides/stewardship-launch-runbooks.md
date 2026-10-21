# Stewardship launch runbooks

The operator's procedures for the situations the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
(item 6, the launch portion of
[OPS-08.05](../tasks/stewardship/operations.md#ops-08-observability-health-and-operational-runbooks))
expects during the first live campaign: Production activation, Stop
go-live and Cancel go-live, a mail-provider outage, a ParishSoft outage, pausing and resuming
delivery, and messages whose delivery is unknown. It also covers watching the
HTTPS certificate and withdrawing published campaign artwork.
Deployment, upgrade and rollback are the
[deployment runbook](stewardship-deployment-runbook.md), whose
[scripted upgrade](stewardship-deployment-runbook.md#scripted-upgrade) and
[scripted rollback](stewardship-deployment-runbook.md#scripted-rollback)
are the normal way to move a live deployment between release digests;
backup and restore are
the [backup runbook](stewardship-backup-runbook.md); checking a credential
against its provider is the [smoke tools guide](stewardship-smoke-tools.md).
Each procedure below says what you will see, what the system does by itself,
what you do, how you know it is over, and what not to do. Where a procedure
and a linked guide disagree, the guide is right.

## How you learn something is wrong

The application records operational incidents and notifies every current
Administrator by email, and by Slack when the optional channel is configured,
when an incident opens, escalates or resolves; repeat notices are suppressed
and warnings escalate to critical after the windows set in the deployment
YAML's `operational_alerts` (900 seconds each by default, 30 minutes for
source staleness; set before provisioning, since a later change needs a
reinstall), as the
[operational alerts guide](stewardship-operational-alerts.md#operational-policy-configuration)
describes. Notices name the deployment mode and the incident kind only, never
a Family, a credential or a message.

Operational email goes out through the same Google Workspace mailbox as
campaign mail, so during a mail-provider outage its own alert may never
arrive. Configure the optional Slack channel for that reason. In the portal,
every Admin page shows **Critical problems in the past 24 hours** when
there are any, the home page lists open security events and recent failed
background tasks, and the Background work page carries the Admin-only warning
that links to unresolved `delivery_unknown` messages. Notices are sent only
when an incident opens, escalates or resolves, so a quiet day is normal and
proves nothing; for a heartbeat, watch for the daily Admin report arriving on
schedule.

Nothing inside the application watches its own HTTPS certificate: if renewal
fails, the first sign is browsers refusing the site. Set up the outside
monitor described in [certificate expiry](#certificate-expiry).

## Production activation

Activation is an Administrator's portal workflow, the four-step **Go live**
page, with one operator step at each end. The Administrator acts twice, each
time in a dialog: **Start go-live** and **Confirm Production**. The system
does everything in between by itself. Going live is irreversible in part:
Start go-live deletes the rehearsal (Testing) data for good, even if going
live is later stopped or cancelled. Plan it for a quiet hour, with the
Administrator and the operator both available. Every step updates itself;
you can leave a step and come back, and a reload returns to the same step.
The page's design is the admin-portal specification's
[Go live page](../specs/stewardship/admin-portal/spec.md#go-live-page). The
[go-live readiness](stewardship-go-live-readiness.md),
[link preparation](stewardship-production-activation.md),
[Production confirmation](stewardship-production-confirmation.md) and
[withdrawal](stewardship-production-withdrawal.md) guides are the historical
implementation records of the server side it reuses.

**Timing.** No click has to be timed against the ParishSoft refresh
schedule. Check and Start go-live need a completed **full** ParishSoft
refresh for the current organization and campaign, but not a recent one
([#1812](https://github.com/epiphany40223/parishkit/issues/1812)): Start
go-live queues the go-live's own full ParishSoft refresh, and every later
step uses that one. From then on the scheduled refreshes
[wait for the go-live](../specs/stewardship/background-processing/spec.md#refreshes-wait-for-go-live):
they hold until 60 minutes after that refresh has finished and the Testing
cleanup has completed, and never longer than 3 hours after Start (or after
**Refresh and prepare again**, which starts a new hold). Confirm
Production within that hold. If it ends first, Confirm Production says "ParishSoft
refreshes resumed while this waited" and offers **Refresh and prepare
again**. Avoid **Refresh now** during a go-live: a refresh that lands after
the Family links were prepared makes the system prepare them again.

1. **Operator: back up and check debug logging.** Run the backup by hand and
   confirm its off-host copy, as the [backup runbook](stewardship-backup-runbook.md)
   says. Then confirm that debug logging is off in every running container,
   since debug logs can hold Families' personal data and the validation
   deployment tool turns it on by default
   ([deployment runbook](stewardship-deployment-runbook.md#pre-launch-fast-deploys)).
   An Administrator sees it first on System health: its Debug logging panel
   lists every service that has it on, and **Turn off debug logging** turns
   it off in all of them within about a minute, without a restart
   ([deployment runbook](stewardship-deployment-runbook.md#turn-off-debug-logging-530)).
   The Go live page's Check step also warns when the last backup completed
   and while debug logging is on. That leaves the setting on the server, so
   still remove it here. Check again whenever a container is recreated
   later:

   ```sh
   for id in $(docker compose ... ps --quiet); do
     printf '%s ' "$(docker inspect --format '{{.Name}}' "$id")"
     docker inspect --format '{{join .Config.Env "\n"}}' "$id" |
       grep '^PARISHKIT_DEBUG_LOGGING=' || echo '(not set)'
   done
   ```

   Every application service and the proxy must show
   `PARISHKIT_DEBUG_LOGGING=0`; the database and Valkey show `(not set)`. For any `=1`, recreate that
   service from a shell where the variable is unset or `0`
   (`PARISHKIT_DEBUG_LOGGING=0 docker compose ... up --detach --force-recreate
   SERVICE`) and check again before going on. After activation, every Admin page shows a "Debug
   logging is on in Production" notice if the web service still has it on.
   A service started with debug logging on also logs a WARNING
   `debug_logging_enabled` line right after `startup_validated`, so
   `docker compose ... logs SERVICE | grep debug_logging_enabled` finds it.
2. **Administrator: Check.** In the menu's Campaign setup group, choose
   **Go live** (`/admin/campaign/go-live/`); the last step of Campaign setup
   opens it too. The step bar reads "Step 1 of 4: Check". The page lists the
   things to fix before going live, each with a link to the page that fixes
   it. Clear them beforehand: every provider credential must have a current
   check; a selected Family test email must have been previewed and sent
   successfully with the current configuration; no Testing delivery may be
   unfinished or unknown; and no configuration change may be pending. A
   full ParishSoft refresh must have completed since the campaign's dates
   and financial periods were last changed; if Check says one is needed, use
   **Refresh now** on the **Refresh from ParishSoft** page
   (`/admin/parish/parishsoft-refresh/`) and wait for it to finish. An older
   full refresh needs nothing: Start go-live refreshes again. One line says
   what going live now would do: make the campaign active at once (its
   initial mail is then prepared and sent) or schedule it for its start.
   The Family and Admin
   mail counts and the Testing cleanup counts are in a disclosure under it.
   Then choose **Check readiness and public web address**; its result shows
   in place. Checking changes nothing.
3. **Administrator: Start go-live.** Once Check says **Everything is
   ready.**, choose **Start go-live**. The dialog "Start going live?" says
   what happens next, shows the Testing data to be deleted (with a link to
   the Testing submissions) and the caution that deleted Testing data cannot
   be restored and that Testing links and codes stop working now. **Apply**
   is the acknowledgement; there is no separate tick box. Apply checks
   readiness and the public web address again and refuses inside the dialog
   if anything fails, naming why. An accepted Apply opens Prepare.
4. **Administrator: watch Prepare.** Three boxes, **Delete Testing data**,
   **Refresh ParishSoft data** and **Prepare Family links**, each read Not
   started, In progress, Done or Failed, with one detail line for the
   current box. While they run there is nothing to do. Preparing links sends
   no email and changes no Family code. If ParishSoft data changes after the
   links were prepared, the system prepares them again by itself and the
   detail line says so. The step's one action, when there is one:
   - **Retry** when a box is Failed after its automatic retries; it retries
     the same task and changes no data.
   - **Refresh and prepare again** when the go-live's refresh failed, when
     ParishSoft data keeps changing, or when the refresh hold ended; it
     starts a new refresh and prepares new links on it, without deleting
     Testing data again.
   - **Continue to Confirm Production** once all three boxes are Done.
5. **Administrator: Confirm Production.** The step shows one impact
   preview with its as-of time: whether the campaign becomes active now or
   is scheduled (with its start time), the Families with and without an
   eligible email, and the Family and report emails due at once. Check it,
   then choose **Confirm Production**. In the dialog ("Go live now?" or
   "Schedule the campaign?") type `Production` and choose **Apply**. If
   anything shown changed in between (ParishSoft data, emails that fell due,
   the start time passing, the public web address check), Apply confirms
   nothing and says so inside the dialog: close it, review the updated
   preview and confirm again. No step expires for the time it has been
   open.
6. **Administrator: watch Production activation.** Apply opens
   **Production activation** (`/admin/campaign/production/`, the step bar's
   Activation step). A campaign that became active shows three boxes:
   **Go live** (Done), **Prepare initial emails** and **Sending**. Initial
   mail is prepared in the background until the heading says **Campaign
   active. Initial campaign mail is prepared.**; Sending then links to Sent
   mail, where delivery is followed. If preparation fails after its
   automatic retries, its box is Failed and the page offers **Retry**, which
   repeats no work already done; nothing rolls Production back. A campaign
   confirmed before its start says **Campaign scheduled.**: no initial
   preparation runs, and the ordinary mail schedules send its mail from the
   start.
7. **Operator: back up again.** Take a backup once Production activation
   is settled, and again after each large send, so a restore loses as
   little as possible.

**Stop go-live** (on Prepare and Confirm Production, before Production is
confirmed) stops the Testing cleanup at its next safe point and discards any
prepared Family links. Its dialog says what stops; Apply opens Check, which
says **Stopping go-live** until both have finished and then "Go-live stopped
at" its time. Testing data already deleted is not restored, and the
campaign stays a Testing draft. If stopping fails after its automatic
retries, Check offers **Retry stopping go-live**; Start go-live is
unavailable until stopping finishes.

**Cancel go-live** returns a *scheduled* campaign to a Testing draft, and is
possible only before its start: once the start passes, the campaign is active
and stays live. On Production activation, choose **Cancel go-live**. The
dialog "Cancel go-live?" shows the scheduled mail runs and unsent messages it
cancels and what stays in history, and the caution that Testing data deleted
earlier is not restored. Enter the reason and choose **Apply**, which is the
acknowledgement (no tick box). Mail that is being sent, of uncertain
delivery or needing recovery blocks the cancellation: the dialog says so,
with links to Pause and resume mail and Sent mail; resolve that work and try
again before the start. A cancellation opens Check, which says "Go-live
cancelled at" its time.
Cancel go-live is not offered while delivery is paused: Production
activation then says why instead of showing the button. Resume first, which
needs the sender test described below, so do not pause a scheduled campaign
whose go-live you may cancel while the provider is down. After a
cancellation, going live again needs the whole cycle above, from Check,
including a new Testing cleanup.

The [review ledger](stewardship-activation-runbook-reviews.md) records how
this procedure was checked against the code before the Go live page
replaced the separate readiness, cleanup, Family links, confirmation and
Cancel go-live pages
([#1585](https://github.com/epiphany40223/parishkit/issues/1585)).

## Measuring the launch send

The launch sends one invitation to each eligible Family, about 1,100
messages. Measure it with the read-only
[mail send report](stewardship-mail-send-report.md#running-it), so the
next large send (the reminders) can be planned from real numbers.

**Before it starts.** Note the time you confirm Production (or the start
date, for a scheduled campaign). Check that `docker compose ... top
mail-dispatch` lists two application processes: the mail worker and its
second mail consumer (`runtime ... --queue mail`; see
[two mail consumers](stewardship-family-mail-dispatch.md#two-mail-consumers)).
The daily limit counts every recipient sent in the last 24 hours, Testing
sends included, and holds back 200 of its 1,800 for receipts and digests.
The launch's roughly 1,400 recipients fit the remaining 1,600 only if little
else was sent in the 24 hours before it.

**While it runs.** Watch **Mail and Family portal › Mail queue** in the
Admin portal
([Family send progress](../specs/stewardship/admin-portal/spec.md#family-send-progress)).
It updates by itself every few seconds with the sent, remaining, failed and
uncertain counts, the current rate and an estimated finish time, and it does
not slow the send down. It can be opened as soon as Production is
confirmed: it waits for the invitations to be scheduled and then follows
them to the end. For the operator's view, run the report with
`purpose='initial'`, `since` a few minutes before the confirmation and
`until` a time in the future. Its **Overview** shows how many invitations
have gone out so far and the rate. While the send runs, the 15-minute
ParishSoft updates and any daytime full refresh are skipped
([why](../specs/stewardship/background-processing/spec.md#deltas-wait-for-a-bulk-family-send)),
so a ParishSoft change can take up to about 2 hours to arrive; if a change
is urgent (say, a Family's address fix), use **Refresh now** on the home
page after making it. The 15-minute updates carry only Family address, phone,
email and registration changes; a new Family, a status or Send No Mail change,
Members, ministries and giving arrive with the next full refresh, scheduled
at the ParishSoft integration's listed times (02:00 alone by default) or
started with **Refresh now**.

**Afterwards.** **Mail and Family portal › Family email history**
([Family email history](../specs/stewardship/admin-portal/spec.md#family-email-sends))
keeps the send's final counts, its first and last times and its duration,
and each count opens those emails on Sent mail. Once the Family send
progress panel is settled, run the report again with `until` after the
last outcome, and record in the operators' notes:

- **Overview:** the message count, `wall_clock` and `accepted_per_minute`.
  With two mail consumers the send is expected to take about 20 minutes, at
  roughly 50 to 60 accepted per minute. One consumer manages about half
  that.
- **Phase timings:** `request_ms` (preparation) and `data_ms` (Gmail's
  DATA) at p50 and p90. Before the guard-planning fix, `request_ms` was
  about 2.4 s at p50 on the validation host and most of each message's
  time. A large `wait_ms` only means messages queued behind each other.
- **Limits, retries and unknowns:** these should be empty. A limit
  refusal pauses sending and resumes it by itself. Messages left in
  `delivery_unknown` need the [procedure below](#messages-in-delivery_unknown).

Then take the backup that step 7 of the activation asks for.

## Mail-provider outage

**You see:** the `mail_provider_unavailable` incident opens, reaching you by
Slack if configured (its email may not arrive, for the reason above) and as
the critical-events banner; Family or staff mail stops arriving; the
**Sent mail** page (`/admin/mail/sent/`) shows messages in
`retry_wait`.

**The system does:** it opens the incident on a systemic failure or on three
consecutive unavailable results for the same provider configuration, and
resolves the incident by itself only after a real healthy SMTP observation
newer than the last failure, as the
[mail health guide](stewardship-mail-health.md) explains. The same failure
also pauses campaign sending in the running `mail-dispatch` process:
invitations, reminders, receipts and reports stay queued (`pending` or
`retry_wait`). After three unavailable results the pause lasts 10 minutes,
then one message is sent as a probe; a healthy result resumes sending and
resolves the incident, and another failure pauses again at once. Unavailable
results do not use up a message's five provider attempts, so a long outage
fails no message (unless it lasts 7 days). A systemic failure (a
configuration or credential fault that waiting cannot fix) instead stops
campaign sending until `mail-dispatch` restarts. A message that met a
systemic failure before the `DATA` command was sent becomes a failed
delivery, as does one that used up its five provider attempts on other
temporary refusals; one whose
preparation retries ran out stays pending, or waiting to retry, with a
failed task; one the provider may have accepted without confirming, including
one that met a systemic failure after `DATA` was sent (an unexpected reply
to `DATA`, for example), becomes
`delivery_unknown` (below). None of these is retried automatically.

**You do:**

1. Check the provider's own status and the Workspace admin console for the
   delegated mailbox (suspended account, revoked delegation, quota).
2. Run the mailbox smoke check inside `mail-dispatch`
   (`smoke --target google_workspace --delegated-email …`), with the exact
   delegated address the integration uses. `invalid` means Google refused the
   credential *or its delegation*: a revoked domain-wide delegation, a
   suspended mailbox or a mistyped address all read as invalid, and replacing
   the key fixes none of them. `unavailable` means an outage or a failure the
   check could not explain; treat it as the provider until shown otherwise.
3. Only when the admin console shows the delegation and mailbox are fine and
   the check still says `invalid`, replace the service-account key, as
   [Replacing a provider credential](#replacing-a-provider-credential) below
   describes.
4. If the outage is long during the live campaign and reminders would bunch
   up, pause delivery (below) and resume when the provider is back.
5. After a systemic failure, once the fault is fixed (the smoke check says
   `valid`), restart campaign sending. An Administrator can clear the halt
   without the server: settle every **Not sure it arrived** message first
   ([below](#messages-in-delivery_unknown)), then run `pk-admin system mail-clear-preview`, which asks the
   mail sender for its own mailbox check, and `pk-admin system mail-clear
   --token …` once it passes (the
   [automation guide](stewardship-admin-automation.md#system-mail-clear-preview-and-system-mail-clear)
   describes both), or System health's **Clear the halt** in the Mail
   tile, which runs the same check and asks the same. One
   probe email per consumer is then sent; if the fault is still there,
   sending halts again. If System health still shows a halted mail sender
   but the preview says nothing is halted (a halt the sender could not
   record, for example one from a marker an older release wrote), restart
   `mail-dispatch` as below. Otherwise `restart mail-dispatch` on the deployment's
   Compose file and project name, which also clears the halt. After an
   outage of unavailable results no restart is needed:
   sending resumes by itself within about 10 minutes of the provider
   recovering.
6. If you paused in step 4, first settle every **Not sure it arrived** message
   (described below; confirming, recording it not sent and the held resend
   all work while paused), then resume once the page's other resume
   conditions hold (see
   [Pausing and resuming delivery](#pausing-and-resuming-delivery)). On
   the **Sent mail** page (`/admin/mail/sent/`), choose the **Failed** state
   and look at the messages last changed during the outage.
   Open each one that should still go and answer **Send it again?** with
   **Yes** on its page (unavailable while delivery is paused), or,
   when all of one type should go, use **Fix many emails at once** →
   **Failed emails: send them all again?** on the same page, which retries
   each the same way after one preview
   ([Sent mail bulk actions](../specs/stewardship/admin-portal/spec.md#fix-many-emails-at-once)).
   Then check the **Still sending (queued)** and **Still sending (waiting
   to retry)** states for messages last changed during the outage whose
   page offers **Try again now**, and choose it for each that should still
   go. If you did not pause, settle any **Not sure it arrived** message as
   described below.
   The [operator diagnostics ledger](stewardship-operator-diagnostics-reviews.md)
   records the last check of this step.

**It is over when:** the incident has resolved, `mail-dispatch` has been
restarted if the failure was systemic, and the **Still sending (queued)**
and **Still sending (waiting to retry)** lists on the deliveries page are no
longer growing and their messages move on as their due times pass. Do not resend by hand outside the
portal, and do not resolve a `delivery_unknown` message without evidence. The
[gate round 3 ledger](stewardship-gate-round3-fixes-reviews.md) records how
this procedure was checked against the code.

**Not an outage: a message waiting hours to retry.** If one Family's message
sits in `retry_wait` for hours while other mail is delivered, the receiving
mailbox is probably throttling it (every address refused with a temporary
code such as `450 4.2.1`). Such a message retries after 15 minutes, 1 hour,
4 hours and 12 hours, about 17 hours in all, and then fails visibly like any
exhausted retry ([Family mail dispatch](stewardship-family-mail-dispatch.md)).
Nothing needs doing; Sent mail does not yet show the reason (#382).

## Backup encryption key changed

**You see:** the `backup_key_changed` incident (CRITICAL), and in System
logs a `configuration_digest_mismatch` entry whose description says a backup
was sealed to a different encryption key than the backup before it.

**The system does:** it keeps backing up and copying off-site with the new
public key. Nothing checks that the kept private key opens those backups,
so a replaced key file, or a public key that is not the kept private key's
pair, leaves every backup green but impossible to restore. The incident
stays open for two days after the change and then resolves by itself; that
does not mean anyone confirmed the key.

A key an Administrator replaced on the **Backup encryption key** page
also sends a "Backup encryption key replaced" security alert, and this
incident opens as a WARNING that escalates about 15 minutes later; one
that was CRITICAL from the start means the key changed some other way.

**You do:** ask the server operator who holds the private key. Unless they
installed a new key on purpose, they find out why the key file changed.
Either way they open the newest backup with each kept copy of the private
key, as the backup runbook's
[restore drill](stewardship-backup-runbook.md#restore-drill) does with
`backup-open`, and record which copies opened it.

## ParishSoft outage

**You see:** the `source_refresh_failed` incident (a refresh failed) or
`source_stale` (a scheduled full refresh is more than `source_stale_seconds`,
30 minutes by default, late); the home page's "ParishSoft data as of" time
stops advancing, and its connection line may read "failing since".

**The system does:** it keeps serving the last successful snapshot, so the
Family form, reports and staff pages keep working on that data; it retries on
its scheduled full and fifteen-minute cadence and resolves the incident by itself
after a successful refresh of the current scope, as the
[source health guide](stewardship-source-health.md) explains. Two other source
incidents are refusals, not outages: `source_tenant_mismatch` (the key now
sees another organization) and `source_destructive_change` (a load lost an
unexpected share of records or of eligible Families). The application holds
those refreshes and does not overwrite the snapshot; stop and investigate
before touching the credential or the source. For a destructive change, see
[Accepting a large ParishSoft change](#accepting-a-large-parishsoft-change).
A third, `source_retention_failing` (WARNING, escalating to CRITICAL if it
persists), is housekeeping. It opens in either of two cases:

- **Skipped:** the last three refreshes each skipped removing old
  ParishSoft copies. The System log shows a `source_retention_skipped`
  entry per refresh, and the worker's log names its failure category.
- **Stalled** ([#833](https://github.com/epiphany40223/parishkit/issues/833)):
  the last 12 refreshes each stopped their cleanup at its time or lock
  limit, removed nothing, and old work is still waiting (a compacted copy
  not reclaimed, or a superseded report generation not removed, more than
  two days on). The System log shows `work_budget_reached` (INFO, a lock or
  time limit) or `task_timed_out` (WARNING, a statement limit) entries for
  those refreshes, not `source_retention_skipped`. The usual cause is lock
  contention, such as a Campaign row held by a long transition on every
  refresh, or a backlog too large to finish in the budget.

Refreshes still work, but the database keeps growing until the cause is
fixed. It resolves after a refresh gets through the cleanup without a skip
and the stall's conditions no longer hold.

**You do:**

1. Check ParishSoft's status and whether the API key still works: run the
   read-only smoke check inside `worker`
   (`smoke --target parishsoft --organization-id …`).
2. If the key is invalid, do not replace it blindly: confirm with the parish
   that the key belongs to the configured organization, then replace it as
   [Replacing a provider credential](#replacing-a-provider-credential) below
   describes. The organization ID itself cannot change after the first load.
3. When the provider is back, use **Refresh now** on the home page
   (`/admin/parish/parishsoft-refresh/`) rather than waiting for the next scheduled run.

**It is over when:** the refresh completes and the incident resolves. Data
entered by Families during the outage was never at risk: submissions are
stored against the snapshot and reconciled on the next refresh.

Two retried failures can also open `source_refresh_failed` once their
retries run out. The System log entry `source_provider_failed` names which
one:

- `shifted_scan`: ParishSoft's data changed while it was being read. A
  record was added between two collections' reads, a gift changed between
  two reads, or the pages moved. One is normal on a busy day, and the retry
  succeeds. If it persists through every retry, a record really does name
  one ParishSoft does not return, such as a roster entry for a removed
  Member or Ministry. Find it in ParishSoft (the debug log names the
  check), fix it there, and use **Refresh now**.

  If only full refreshes fail with `shifted_scan` while quick updates
  succeed, look for a WARNING `source_provider_failed` line carrying
  `source_contact_coverage`
  (`docker compose ... logs worker | grep source_contact_coverage`). It
  means the Member contact list left out many more Members than the last
  full refresh's did, so the load looked cut short. Its counts (no parish
  data) are the Members searched (`members`), the contact records returned
  (`contact_infos`), the Members the list left out (`missing`), the last
  full refresh's count left out (`baseline_missing`) and how many more are
  allowed (`allowance`). A cut-short read passes on retry; a refusal that
  repeats on every full refresh is a real change, such as a cleanup that
  inactivated hundreds of Members, which ParishSoft leaves off that list.
  Confirm the change in ParishSoft, then accept it for one full refresh
  with the loss limit override as
  [Accepting a large ParishSoft change](#accepting-a-large-parishsoft-change)
  describes (any value above `25` turns this check off for that refresh;
  `26` is enough). The accepted full refresh's count becomes the new
  baseline. The same refusal can come once after a temporary dip: if
  ParishSoft briefly left out fewer Members (say, after a fix there) and a
  full refresh recorded that lower count, the next full refresh back at the
  usual count is refused the same way. Handle it the same way.
- `provider_timeout`: ParishSoft did not answer, or the full load did not
  finish within its 15-minute limit. If it repeats on full loads while
  quick updates succeed, the load has grown or ParishSoft has slowed. Check
  how long recent full refreshes took (each refresh's task page) against
  the limit and ParishSoft's status, and report it if loads keep
  approaching the limit.

### Accepting a large ParishSoft change

A refresh is refused with `source_destructive_change` when a record count
(Families, Members, Ministries, roster rows, funds) or an eligibility count
(portal-eligible Families, email-eligible Families, Families with an active
head, contacts with a valid email) falls by more than 25%, or falls to zero
from any number, however small. "Before" is the count's highest value over
the full refreshes of the past week (and, for the eligibility counts, the
current data), not only the last full refresh. A drop that built up
gradually, each full refresh a little below the one before, can therefore be
refused even though no single refresh fell by 25%. A full refresh accepted
with a raised limit starts that week again; one accepted by an
Administrator starts it again only for the counts that fell. Every later
refresh is refused the same way until the drop is explained.

An Administrator handles it from the portal; the server operator's override
is the emergency path.

1. Find what fell. System health's ParishSoft refresh panel lists every
   count the refused refresh was checked on, with its before and after
   values, marking each one that fell too far (counts only, no parish data).
   The worker's log line for the refusal, a CRITICAL
   `source_destructive_change`, carries `source_loss` with the first count
   that failed (`docker compose ... logs worker | grep source_loss`).
2. Check it in ParishSoft. **Accept this change once** lists, under each
   Family, Member or contact count that fell too far, up to five example
   Families (name and Family DUID) that the current data has and the
   refused refresh lost or no longer counted: look a few of them up in
   ParishSoft. If it is a mistake or an outage there (records removed,
   organization or status fields blanked), fix it in ParishSoft and use
   **Refresh now**; nothing else is needed. The examples are deleted when
   the change is accepted, when a newer refusal or a full refresh replaces
   it, or after seven days.
3. If the change is real, such as the parish inactivating many Families at
   once, an Administrator accepts it once on System health: **Accept this
   change once**, review
   the counts, tick "I checked this in ParishSoft, and the change is real",
   and confirm. That also asks for a full refresh, as **Refresh now** does.
   The section then says who accepted it, when, and when the acceptance
   expires, in the reader's own time zone. The next full refresh goes
   through only if no count falls below the "after" value reviewed and every
   count that was within the limit stays within it; the limit itself is not
   raised, and the Member contact list check stays in force.
4. It is over when the ParishSoft refresh panel no longer shows the refusal:
   the accepted full refresh was promoted. Nothing needs undoing. The
   acceptance ends by itself when a full refresh is promoted, when a new
   refusal is recorded, or after 24 hours.

What the section can say instead:

- **"This changed since you looked; review it again."** A newer refusal, or
  a promoted full refresh, replaced the one reviewed. Review the counts now
  shown, check them in ParishSoft again, and accept that refusal if the
  change is still real.
- **The acceptance ended before a full refresh used it.** A refusal is
  accepted only once. Use **Refresh now**; if the full refresh is refused
  again, accept the new refusal. A refresh that fell further than the counts
  reviewed is refused as well, with a new refusal to review.
- **A refresh that found no Families or no Members can never be accepted.**
  It is refused at any limit, by design, because it almost always means
  ParishSoft could not be read properly. Check the connection and the key,
  as [ParishSoft outage](#parishsoft-outage) describes.

**Emergency path: the operator override.** When no Administrator can use
the portal, or for a refusal the portal's acceptance does not cover (the
Member contact list below), the server operator can accept a change for one
refresh by recreating the worker with a higher loss limit, in percent
(`100` accepts any drop; a load with no Families or Members is still
refused), then using **Refresh now**:

```text
PARISHKIT_SOURCE_MAX_DROP_PERCENT=100 docker compose ... up --detach --force-recreate worker
```

Wait until the full refresh that **Refresh now** started has completed.
Its progress page (where **Refresh now** takes you) shows it finished, and
on `/admin/parish/parishsoft-refresh/` the "last full refresh" time
("ParishSoft data as of", when no quick update brought newer changes) moves
past the moment you clicked, with no "A full refresh is running now". A
15-minute update that completes first is not enough: later refreshes also
compare with the recent full refreshes, and only the accepted full refresh
starts that comparison again, so removing the override before the full one
completes gets the next refresh refused again. Only then recreate the
worker without the variable, so the 25% limit applies again:

```text
docker compose ... up --detach --force-recreate worker
```

The same override, for one full refresh, also accepts a large rise in the
Members the contact list leaves out (`shifted_scan` with
`source_contact_coverage`, under
[the refresh failures above](#parishsoft-outage)):
any value above `25` turns that check off for that refresh.

A malformed value keeps the 25% limit. The accepted refresh records the
limit it used in its manifest. While any limit other than 25% is in effect,
the worker logs a WARNING line carrying `source_max_drop_percent` at the
start of every refresh, so a forgotten override shows in its log.

## Replacing a provider credential

An Administrator replaces a key on the integration's own settings page. No
server step is needed; the
[credential installer guide](stewardship-credential-installers.md#replacing-an-integration-key-from-the-web)
describes the design.

1. Open **Integrations**, then the provider's page
   (`/admin/system/integrations/<target>/`). Paste the new key in its field and select **Save**. The key is sealed at
   once and never shown again.
2. The page follows the change: "Checking and installing the new key", then
   "Switching to it now", then "Key updated." The isolated installer checks
   the key with the provider first and sends no message. A rejected key says
   so, and the previous key stays in use.
3. If the page (or the Admin home page) instead says in red that switching to
   the new key did not finish, select **Finish switching to the new key** right
   away and confirm. After 15 minutes in this state `mail-dispatch` also logs
   `installer_request_failed` with `credential_switch_unfinished` at ERROR.
   The new key is already installed, so until it is selected the integration
   stops: ParishSoft refreshes are refused, and email waits (it is held, not
   failed, and goes out once the switch finishes). This happens when another
   settings change was applied first; **Finish switching** repeats the switch
   on the current settings.
4. Re-run the smoke check to confirm.

Avoid replacing the Google Workspace or ParishSoft key while campaign mail is
sending. The ParishSoft organization ID cannot change after the first
ParishSoft data load.

## Pausing and resuming delivery

Pausing holds all unsent live mail, Family messages, submission receipts and
daily and weekly Admin reports alike, while the portal and the Family form
stay open; Families can still submit, and their confirmations are held too.
It is the tool for a provider outage, a content mistake found after the
schedule started, or any moment when mail must stop now. The controls exist
only for the current Production campaign.

1. Open the **Pause and resume mail** page (`/admin/mail/controls/`).
2. Choose **Preview pause**, give the reason, and confirm with the fresh
   preview. Messages already being submitted may still reach the provider and
   cannot be recalled; everything else unsent is held, and new mail created
   while paused is held as it is created.
3. Fix the cause. The page and the Admin banner show the held, submitting and
   unknown counts.
4. To resume an active campaign:
   1. Clear what the page refuses to resume over: every message still being
      submitted must finish, every unknown delivery must be resolved (below;
      the unknown count also includes messages awaiting an idempotent
      retry), report preparation must reach its safe point, and any blocked
      Family group on the **Overdue Family email** panel must be
      resolved. The resume controls are hidden entirely while an activation
      catch-up is incomplete, including one whose preparation has failed: if
      the **Production activation** page shows a failure, choose
      **Retry** there and wait for **Campaign active. Initial campaign
      mail is prepared.** A report preparation that has *failed* never
      reaches its safe point by itself: find the failed daily or weekly
      report task on the Background work page (`/admin/system/background/`; the
      home page lists only the latest five failures of the past day), fix
      its cause (usually ParishSoft
      availability), choose **Retry report work** on the task's page, and
      wait for it to complete.
   2. The **Provider and sender check** panel says whether a sender check is
      needed. After an ordinary pause it says none is, and you go straight
      to step 3. After sender trouble it says why one is needed, and
      Preview resume stays unavailable until it passes: choose **Preview and
      send a test to the configured Testing recipient**, send the test on
      the campaign mail page, go back to **Pause and resume mail**, and
      reload until it says when the sender was checked and when to resume
      before. That check lasts 30 minutes, and the preview and the
      confirmation both check it again
      ([rule](../specs/stewardship/admin-portal/spec.md#live-delivery-pause)).
   3. Give the reason and choose **Preview resume**. Review the exact
      preview: overdue invitations and reminders are coalesced (redundant
      slots coalesced, inapplicable ones skipped), overdue daily or weekly
      reports each become one report obligation, every held receipt is
      released, and future work keeps its original due time.
   4. Choose **Confirm resume of live delivery**, within five minutes of
      the preview. Every input is checked again;
      anything that changed cancels the confirmation without releasing mail,
      and you preview again. If the confirm button is missing, or confirming
      reports changed inputs or "This preview is out of date.", a clock has
      run out: preview again. If the page comes back saying a sender
      check is needed, send a test as in step 2 first.

If the campaign closes while delivery is paused, resuming no longer applies:
invitations and reminders follow the ordinary close policy and cannot be
released, and the page instead offers to resolve the held receipts and Admin
reports by type: release the ones that should still go, after the same
sender check, and cancel the rest with a reason. A type cannot be cancelled
while any of its messages is still submitting or unknown, and no resolution
at all (release, cancel or clear) is accepted while a
daily or weekly report is still being prepared: wait for it to finish, and
if its preparation has failed, retry it from the Background work page as in
step 4.1 above. Every resolution (release, cancel or clear) is confirmed
within five minutes of its preview, and a release also needs the sender
test, all in one go as for a resume. "This preview is out of date." means
the preview has expired, and "This information changed" means something the preview
relied on moved (the sender test expired, report preparation started, or the
held messages changed); recover as for a resume. This does not reopen Family access.
Held invitations and reminders are cancelled by the mail worker under the
close policy; one with no delivery task left (usually because its
preparation failed) has no worker to do that, so the page counts it and
every resolution cancels it the same way
([review ledger](stewardship-closed-stranded-family-reviews.md)). A
resolution clears the pause only when it leaves nothing else held,
submitting or unknown. When such invitations and reminders are all that is
held, or the last unknown delivery is reconciled afterwards, choose
**Clear a fully resolved pause (select no types)**: release and cancel
need a selected type with held messages.

The [delivery pause guide](stewardship-delivery-pause.md) is the design; the
[delivery journal](stewardship-delivery-journal.md) explains what the
deliveries page shows. Do not stop the `mail-dispatch` container as a way to
pause: the pause is durable and audited, a stopped container is neither, and
its work resumes the moment it restarts.

## Messages in `delivery_unknown`

A message enters `delivery_unknown` when the provider was asked to send it
and did not confirm or deny within the attempt, so the application cannot
know whether a Family received it. Automatic retry stops for that message, a
warning is recorded, and the Background work page shows the Admin-only link
to the unresolved rows. The
[background-processing specification](../specs/stewardship/background-processing/spec.md)
owns the policy for each kind:
[invitations and reminders](../specs/stewardship/background-processing/spec.md#family-invitations-and-reminders),
[receipts](../specs/stewardship/background-processing/spec.md#submission-confirmation)
and [Admin reports](../specs/stewardship/background-processing/spec.md#administrator-report-emails).
The guides linked in step 3 own each kind's resolution workflow.

On **Sent mail**, choose the Delivery filter **Needs attention** (or **Not
sure it arrived — review needed**) and open each message. Its page says at
the top what happened and what to check, and asks one question
([Mail message page](../specs/stewardship/admin-portal/spec.md#mail-message-page)):

1. Do the check the page names: look in the delegated mailbox's Sent
   folder, or the Google Workspace email log search, for that email to the
   addresses it names, sent around the time it names.
2. Answer **What did you find?**, writing in the note what you saw:
   - **It was sent** → **Mark as delivered**: the message is delivered and
     its occurrence completes.
   - **It was not sent**, then **Send it again?**
     - **Yes** → tick that the recipient could get a second copy →
       **Send it again**: a new attempt under the same occurrence. During a
       pause it waits with the paused mail, and resolving it lets the
       resume proceed.
     - **No** → **Record as not sent**: recorded as a failed delivery, as if
       the provider had refused it, with no address suppressed and no
       resend; a paused campaign can then resume. You can still send it
       again later from its page, under the ordinary rules.
   - **I can't tell yet** → **Save note**: the message stays unresolved,
     blocks schedule edits for its occurrence and stays listed until you
     answer again.
3. An answer the rules do not allow now is shown unavailable with its
   reason (for example "The Family has already responded" or "Campaign
   email is paused"); choose another answer. The per-kind rules for
   sending again, and what a pause or a closed campaign does with the
   resent copy, are in the
   [background-processing specification](../specs/stewardship/background-processing/spec.md)
   and the guides for
   [invitations and reminders](stewardship-family-mail-resolution.md),
   [receipts](stewardship-submission-receipts.md),
   [daily](stewardship-daily-digests.md) and
   [weekly](stewardship-weekly-digests.md) reports, the
   [paused resend](stewardship-paused-resend.md) and
   [recording as not sent](stewardship-unsent-resolution.md).

When the provider's records show that many of them were not sent, answer
for all of them at once with **Fix many emails at once** → **Not sure they
arrived: record them all as not sent?** on Sent mail, after one preview and
with one note ([Sent mail bulk actions](../specs/stewardship/admin-portal/spec.md#fix-many-emails-at-once)).
A failed report whose recipient is still an Administrator holds its report
open until it is sent again; on a campaign closed while paused, remove a
recipient who should no longer be an Administrator through the ordinary
configuration change before you resolve the held reports by type, so the
report can record its skip
([unsent resolution guide](stewardship-unsent-resolution.md)).

Never resend outside the portal, never resolve without a note, and never answer
**It was sent** for a message the provider shows was *not* sent, even to unblock a resume: record it as not sent instead. The
note and the resolution are the only record of why a Family got one message,
two or none.

## Certificate expiry

**What you see.** Normally nothing. `caddy` obtains the site's certificate from
Let's Encrypt and renews it automatically, well before it expires (about a
third of its lifetime ahead). The generated Caddyfile names Let's Encrypt as
the site's one ACME issuer (`issuer acme` with the Let's Encrypt directory), so
Caddy's default issuers, which could fall back to ZeroSSL, are not used; the
deployment sets no ACME email either, and Caddy adds its ZeroSSL fallback
only when one is set. There is no second issuer, so if renewal keeps failing
(DNS no longer points at the host, port 80 or 443 blocked, a Let's Encrypt
outage or rate limit), the certificate eventually expires and every browser
refuses the site: Families cannot open their links and Administrators cannot
sign in. The application raises no alert for this,
because the failure is in front of it.

**What you do, once, before launch.** Watch the certificate from outside the
host:

1. Add the public origin (for example `https://stewardship.example.org/`) to an
   external monitoring service that checks certificate expiry, and have it
   alert the operator and a second person when fewer than 14 days remain. A
   healthy renewal never lets it get that close, so the alert means renewal is
   failing, with two weeks to fix it. Most uptime services include this check;
   the same monitor can also alert when the site stops answering. The
   `/health/` paths are not served publicly, so point it at the origin itself.
2. To check by hand from any computer:
   `echo | openssl s_client -connect HOST:443 -servername HOST 2>/dev/null | openssl x509 -noout -enddate`
   prints the expiry (`notAfter=`).

**When the alert fires.**

1. Read the proxy's log:
   `docker compose -f COMPOSE_FILE -p PROJECT logs --since 48h caddy`. Caddy
   logs each failed renewal with the reason.
2. Check the usual causes: the DNS `A` record still names this host (and no
   `AAAA` record exists; see the
   [deployment runbook](stewardship-deployment-runbook.md#before-you-start)),
   TCP 80 and 443 still reach the host from the Internet, and the
   [Let's Encrypt status page](https://letsencrypt.status.io/) shows no
   outage. Fix what is wrong; Caddy retries by itself, so there is nothing to
   restart once the cause is gone.
3. Confirm with the hand check above that `notAfter=` now shows a later date
   than before: the renewed certificate's own expiry date.

**What not to do.** Do not delete or empty Caddy's data directory
(`RUNTIME_ROOT/run/persistent/caddy/data` by default) to force a new
certificate: it holds the ACME account and current certificate, and Let's
Encrypt allows only a few new certificates for one name per week (the
[runtime guide](stewardship-runtime.md) explains why that state is kept).

## Withdrawing published campaign artwork

Use this when an image selected as campaign artwork (a banner or section icon)
must stop being shown, for example a photo used without permission.

**What the system does by itself.** Once an activated campaign configuration
names an image, its public address (`/branding/ASSET.png`) serves it to anyone
with the address, and browsers and email providers may keep a copy for up to
a year. Replacing the image in the campaign only changes pages and emails
from then on: every configuration that ever named the image keeps it on the
server, so earlier emails still show it. There is no withdraw button.

**What you do.**

1. In the Admin portal, replace or remove the image in the campaign's artwork
   and apply the change, so new pages and emails stop naming it.
2. Find the image's storage folder. Its address ends in the asset ID; look up
   its bundle as the database superuser, in a read-only session:
   `docker compose -f COMPOSE_FILE -p PROJECT exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres psql -U pk_stewardship_operator -d DATABASE -At -c "SELECT bundle_id, label FROM stewardship_branding_asset WHERE id = 'ASSET'"`.
3. On the host, move the file out of the served tree into a private folder
   outside the runtime root (do not only rename it inside the folder):
   `RUNTIME_ROOT/run/persistent/media/branding/BUNDLE/LABEL.png`, where
   `BUNDLE` is the bundle ID without dashes. Keep it private (owner-only).

**How you know it is over.** The image's address no longer returns the
picture: the server answers that it is temporarily unavailable, as it does for
any image it cannot verify, and pages and emails that still name it show no
image.

**What not to do.** Do not edit or delete database rows: configuration history
is append-only. A copy already fetched by a browser or an email provider
cannot be recalled. Backups still hold the file, so a restore brings it back:
repeat step 3 after any restore.

## Keeping a Family from getting Reminders

Use this when a Family should not get the campaign's Reminder emails, for
example a second ParishSoft Family record kept for a staff member who already
responded as a parishioner (#861).

**What the system does by itself.** If the campaign names a Reminder
WorkGroup, every ParishSoft refresh (full refreshes and quick updates) reads
which Families are in that Family WorkGroup. A Family in it gets no Reminders:
each one is skipped when it is planned, and one already prepared is not sent.
Nothing else changes. The Family still gets the invitation, receipts and
confirmations, keeps its code and link (emailed codes and links are never
changed), can still respond, and counts in every report. The Family's
timeline says "Reminders skipped: in ParishSoft WorkGroup …", and the Mail
queue's Family send progress panel counts the skipped Reminders separately.

**What you do.**

1. In ParishSoft, create one Family WorkGroup for this campaign, for example
   "Active: Stewardship 2027" (Families, then WorkGroups; any name works, but
   a new one each year keeps last year's list from carrying over by
   accident). Add each Family that should get no Reminders. Staff can add or
   remove Families there at any time without touching any other field.
2. In the Admin portal, open Campaign setup, then **No-reminders WorkGroup**.
   Type the WorkGroup's name as ParishSoft shows it (capital letters and
   spaces at the ends do not matter), preview and apply the change. It can be
   changed while the campaign is live.
3. Wait for the next ParishSoft refresh, or use **Refresh from ParishSoft**.
   Quick updates wait while a Family email send is in progress, so make
   ParishSoft changes before a Reminder's preparation starts (two hours
   before it is due) when you can; a Family added later is still skipped at
   send time once a refresh has read it. After an edit to the name, even of
   its capitals, the Families the newest refresh found in the previous
   WorkGroup are still skipped until the next refresh reads the new name, so
   run **Refresh from ParishSoft** after every edit. Clearing the name stops
   skipping at once.
4. Check it against live ParishSoft right after the deploy, and well before
   the night a Reminder is prepared (preparation starts two hours before it
   is due): set the name, run **Refresh from ParishSoft**, and confirm that
   the No-reminders WorkGroup page shows the number of Families you expect. While
   a name is set, a failure of ParishSoft's WorkGroup calls fails the whole
   refresh, so a refresh that fails right after setting the name points at
   the WorkGroup read.

**How you know it worked.** The No-reminders WorkGroup page says how many
Families the newest refresh found in the WorkGroup, and each one's Family
timeline names it. If the name matches no Family WorkGroup in ParishSoft,
the page and System health say so and the refresh logs a warning; the
Families skipped before stay skipped, and no others are, until a refresh
finds the name: fix it in either place.

**What not to do.** Do not make the Family inactive or move it out of the
parish in ParishSoft to stop Reminders: that removes it from the campaign,
its counts and its portal access. Do not use ParishSoft's Send No Mail flag:
it means postal mail. Clearing the setting sends later Reminders to those
Families again. If delivery is paused and resumed around a Reminder, the
resume preview counts the WorkGroup's Families as about to be emailed; they
are still skipped when sent.

## Index

| Situation | Where |
| --- | --- |
| Install, upgrade, roll back | [Deployment runbook](stewardship-deployment-runbook.md) ([scripted](stewardship-deployment-runbook.md#scripted-upgrade)) |
| Nightly backup, off-host copy, restore drill, real restore | [Backup runbook](stewardship-backup-runbook.md) |
| Check a credential against its provider | [Smoke tools guide](stewardship-smoke-tools.md) |
| Replace a provider credential | [Above](#replacing-a-provider-credential); design in the [credential installer guide](stewardship-credential-installers.md) |
| Alert routing and windows | [Operational alerts guide](stewardship-operational-alerts.md) |
| Measure the launch send | [Above](#measuring-the-launch-send), with the [mail send report](stewardship-mail-send-report.md) |
| Production activation, Stop go-live and Cancel go-live | [Above](#production-activation); page design in the [Go live page](../specs/stewardship/admin-portal/spec.md#go-live-page) specification; server history in the [go-live readiness](stewardship-go-live-readiness.md), [link preparation](stewardship-production-activation.md), [Production confirmation](stewardship-production-confirmation.md) and [withdrawal](stewardship-production-withdrawal.md) guides |
| HTTPS certificate expiry monitoring and renewal failures | [Above](#certificate-expiry) |
| Withdraw published campaign artwork | [Above](#withdrawing-published-campaign-artwork) |
| Keep a Family from getting Reminders | [Above](#keeping-a-family-from-getting-reminders) |
