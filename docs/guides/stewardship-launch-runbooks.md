# Stewardship launch runbooks

The operator's procedures for the situations the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
(item 6, the launch portion of
[OPS-08.05](../tasks/stewardship/operations.md#ops-08-observability-health-and-operational-runbooks))
expects during the first live campaign: Production activation and
withdrawal, a mail-provider outage, a ParishSoft outage, pausing and resuming
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

Activation is an Administrator's portal workflow with one operator step at
each end. It is irreversible in part: the Testing cleanup in step 4 deletes
the rehearsal data for good, even if the transition is later cancelled or
withdrawn. Plan it for a quiet hour, with the Administrator and the operator
both available. The design is in the
[go-live readiness](stewardship-go-live-readiness.md),
[link preparation](stewardship-production-activation.md) (historical, but
still the design record for preparation and disposal),
[Production confirmation](stewardship-production-confirmation.md) and
[withdrawal](stewardship-production-withdrawal.md) guides.

**Timing.** Two clocks govern steps 3 to 6, so do them in one sitting:

- The readiness checks accept a **full** ParishSoft refresh only for 30
  minutes (the default source-staleness window) from the moment it
  *started*, and they are checked again when cleanup starts and at the final
  confirmation. Start the full refresh with **Refresh now** on the home page
  and finish step 6 within that window.
- The prepared Family links of step 5 are bound to the ParishSoft data they
  were prepared from, and the scheduled delta refresh runs every 15 minutes
  (on the quarter hour), including during go-live; the scheduled full
  refreshes (at the ParishSoft integration's listed times, 02:00 alone by
  default) do the same, so avoid those hours. Any refresh that lands after
  preparation makes it stale. Start step 5 just after a quarter-hour delta
  has finished, and go straight on to step 6.
- Plan for both: cleanup, the wait for a delta and preparation often use up
  most of the 30 minutes. If cleanup finishes late in the window, start a
  second full refresh once cleanup has completed and just after a
  quarter-hour delta has finished, so that the full refresh, the Family
  eligibility catch-up, preparation and confirmation all finish before the
  next quarter hour. Refreshes never run side by side: a delta that comes
  due while the full refresh runs starts right after it and makes the
  preparation stale. Time a full refresh on the validation deployment
  beforehand; if it takes most of fifteen minutes, this path does not fit and
  the first full refresh must carry the whole procedure. Refresh is allowed
  during cleanup's hold. The home page's last-refreshed time shows when a
  refresh finished.

1. **Operator: back up and check debug logging.** Run the backup by hand and
   confirm its off-host copy, as the [backup runbook](stewardship-backup-runbook.md)
   says. Then confirm that debug logging is off in every running container,
   since debug logs can hold Families' personal data and the validation
   deployment tool turns it on by default
   ([deployment runbook](stewardship-deployment-runbook.md#pre-launch-fast-deploys)).
   Check again whenever a container is recreated later:

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
2. **Administrator: clear readiness.** From the campaign's settings page,
   choose **Review go-live readiness and Testing cleanup impact**
   (`/admin/campaign/<campaign id>/go-live`). The page lists what still needs
   attention, each item with its remedy. Clear these beforehand: every
   provider credential must have a current check; a selected Family test
   email must have been previewed and sent successfully with the current
   configuration; no Testing delivery may be unfinished or unknown; and no
   configuration change may be pending. Then start the full refresh with
   **Refresh now** on the home page and wait for it to finish. **Refresh now**
   is the full ParishSoft refresh: the home page link opens the **Refresh from
   ParishSoft** page (`/admin/parish/parishsoft-refresh/`), and that page's **Refresh
   now** button starts it. Reviewing and checking readiness change nothing;
   only **Start Testing cleanup** in step 4 acts.
3. **Administrator: check readiness and read the impact.** Choose **Check
   readiness and public web address** in the **Public web address check**
   panel. Read the Family and Admin report mail impact: the page says whether
   confirming now would make the campaign active immediately (initial mail is
   then prepared at once) or schedule it for its start date. Once the check
   passes, the **Start irreversible Testing cleanup** panel appears with the
   acknowledgement and the cleanup button for step 4.
4. **Administrator: start the cleanup.** Within five minutes of the preview,
   tick **I acknowledge that deleting the inventoried Testing data is
   irreversible.** and choose **Start Testing cleanup**. Rehearsal codes and
   links stop working and no new Testing work starts. Watch the progress page
   until it says **Cleanup is complete.** A failed run offers **Retry failed
   cleanup from its checkpoints**; cancelling releases the hold but restores
   nothing.
5. **Administrator: prepare the links.** Just after a quarter-hour delta
   refresh has finished, choose **Prepare inactive Family links** on the
   cleanup page, then again on the **Prepare Family links** page it opens,
   and wait for **Inactive links are prepared. The campaign remains in
   Testing.** Open or reload the links page only after the delta has
   finished: until the Family eligibility catches up with the new data it
   says the preparation inputs are unavailable, so wait a moment and reload.
   Every button on the cleanup and links pages (prepare, cancel, retry) is
   refused with a generic "Check this value." error once its page has been
   open for more than five minutes; reload the page and try again. Preparation sends no email and changes no Family code. If a
   refresh lands first, the page says the preparation is cancelled or no
   longer current. To prepare again, choose **Cancel and discard these
   inactive links**, wait until its disposal worker finishes (**Retry failed
   disposal** if it fails), and only then does **Prepare inactive Family
   links** reappear. If the 30-minute window has also lapsed, run another full
   refresh first, timed as in the timing notes above, then discard and
   prepare again.
6. **Administrator: confirm.** Choose **Review final Production
   confirmation**. The page requires a Google sign-in made after the cleanup
   completed and within the last five minutes: choose **Confirm with Google**
   in its **Confirm it's you with Google** panel, and the sign-in brings you
   back to this page. Then choose **Verify final readiness and mail impact**,
   check the exact preview (start and close dates, and immediate versus
   scheduled), type `Production`, and choose **Confirm Production**, all
   within five minutes of the sign-in. A changed input means a new preview,
   not a failure.
7. **Administrator: watch the result.** The **Production activation
   progress** page (`/admin/campaign/<campaign id>/production`) shows the
   outcome. A campaign that became active prepares its initial mail in the
   background until **Initial campaign mail preparation complete**; a
   terminal failure offers **Retry failed mail preparation**, and nothing
   rolls Production back. A campaign confirmed before its start is scheduled:
   no initial preparation runs, and the ordinary schedule sends its mail from
   the start date.
8. **Operator: back up again.** Take a backup once the progress page is
   settled, and again after each large send, so a restore loses as little as
   possible.

**Cancel go-live** returns a *scheduled* campaign to draft in Testing, and is
possible only before its start: once the start passes, the campaign is active
and stays live. Open **Cancel go-live** from the progress
page, choose **Confirm with Google** (the sign-in brings you back to this
page), give a reason, tick **I understand that deleted Testing data cannot be
restored.**, choose **Preview cancellation** and then **Confirm cancelling
go-live**. If the preview reports work in flight or uncertain, the confirm
button is withheld: resolve that work, choose **Confirm with Google** again if
more than five minutes have passed since the last sign-in, then preview again
and confirm within five minutes and before the start.
Cancel go-live is refused while delivery is paused: the progress page then
hides the Cancel go-live link, and the Cancel go-live page says go-live can't
be cancelled, as it does for an active campaign. Resume first, which needs
the sender test described below, so do not pause a scheduled campaign whose
go-live you may cancel while the provider is down. After a cancellation, going live
again needs the whole cycle above, including a new cleanup.

The [review ledger](stewardship-activation-runbook-reviews.md) records how
this procedure was checked against the code.

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

**While it runs.** Watch **Campaign › Family email progress** in the Admin
portal
([Family email progress](../specs/stewardship/admin-portal/spec.md#family-email-progress)).
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
and each count opens those emails on Outgoing mail. Once the progress page
is settled, run the report again with `until` after the last outcome, and
record in the operators' notes:

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

Then take the backup that step 8 of the activation asks for.

## Mail-provider outage

**You see:** the `mail_provider_unavailable` incident opens, reaching you by
Slack if configured (its email may not arrive, for the reason above) and as
the critical-events banner; Family or staff mail stops arriving; the
deliveries page (`/admin/deliveries`) shows messages in `retry_wait`.

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
   `valid`), restart campaign sending: `restart mail-dispatch` on the
   deployment's Compose file and project name. Nothing else clears that
   stop. After an outage of unavailable results no restart is needed:
   sending resumes by itself within about 10 minutes of the provider
   recovering.
6. If you paused in step 4, first settle every **Not sure it arrived** message
   (described below; confirming, recording it not sent and the held resend
   all work while paused), then resume once the page's other resume
   conditions hold (see
   [Pausing and resuming delivery](#pausing-and-resuming-delivery)). On
   the deliveries page (`/admin/deliveries`), choose the **Failed** state
   and look at the messages last changed during the outage.
   Open each one that should still go and choose **Retry failed delivery** on
   its page (the button is offered only when delivery is not paused). Then
   check the **Still sending (queued)** and **Still sending (waiting to
   retry)** states for messages last changed during the outage whose page offers **Retry delivery not accepted
   by the provider**, and choose it for each that should still go. If you did
   not pause, settle any **Not sure it arrived** message as described below.
   The [operator diagnostics ledger](stewardship-operator-diagnostics-reviews.md)
   records the last check of this step.

**It is over when:** the incident has resolved, `mail-dispatch` has been
restarted if the failure was systemic, and the **Still sending (queued)**
and **Still sending (waiting to retry)** lists on the deliveries page are no
longer growing and their messages move on as their due times pass. Do not resend by hand outside the
portal, and do not resolve a `delivery_unknown` message without evidence. The
[gate round 3 ledger](stewardship-gate-round3-fixes-reviews.md) records how
this procedure was checked against the code.

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
persists), is housekeeping: the last three refreshes each skipped removing
old ParishSoft copies (the System log shows a `source_retention_skipped`
entry per refresh). Refreshes still work, but the database keeps growing
until the cause is fixed; the worker's log names its failure category. It
resolves after a refresh gets through the cleanup without a skip.

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

### Accepting a large ParishSoft change

A refresh is refused with `source_destructive_change` when, compared with the
last full refresh or the current data (whichever is larger), a record count
(Families, Members, Ministries, roster rows, funds) or an eligibility count
(portal-eligible Families, email-eligible Families, Families with an active
head, contacts with a valid email) falls by more than 25%, or falls to zero
from any number, however small. Every later refresh is refused the same way
until the drop is explained.

1. Find what fell. The worker's log line for the refusal, a CRITICAL
   `source_destructive_change`, carries `source_loss` with the count's name
   and its before and after values (counts only, no parish data; the
   System health page's example Families are separate and never logged):
   `docker compose ... logs worker | grep source_loss`.
2. Check it in ParishSoft. If it is a mistake or an outage there (records
   removed, organization or status fields blanked), fix it in ParishSoft and
   use **Refresh now**; nothing else is needed.
3. If the change is real, such as the parish inactivating many Families at
   once, accept it for one refresh. Recreate the worker with a higher loss
   limit, in percent (`100` accepts any drop; a load with no Families or
   Members is still refused), then use **Refresh now**:

   ```text
   PARISHKIT_SOURCE_MAX_DROP_PERCENT=100 docker compose ... up --detach --force-recreate worker
   ```

4. Wait until the full refresh that **Refresh now** started has completed.
   Its progress page (where **Refresh now** takes you) shows it finished,
   and on `/admin/parish/parishsoft-refresh/` the "last full refresh" time ("ParishSoft
   data as of", when no quick update brought newer changes) moves past the
   moment you clicked, with no "A full refresh is running now". A 15-minute update that completes first is not enough: later
   refreshes also compare with the last full refresh, so removing the
   override before the full one completes gets the next refresh refused
   again. Only then recreate the worker without the variable, so the 25%
   limit applies again:

   ```text
   docker compose ... up --detach --force-recreate worker
   ```

A malformed value keeps the 25% limit. The accepted refresh records the
limit it used in its manifest. While any limit other than 25% is in effect,
the worker logs a WARNING line carrying `source_max_drop_percent` at the
start of every refresh, so a forgotten override shows in its log.

## Replacing a provider credential

An Administrator replaces a key on the integration's own settings page. No
server step is needed; the
[credential installer guide](stewardship-credential-installers.md#replacing-an-integration-key-from-the-web)
describes the design.

1. Sign in with Google within the last five minutes, open **Integrations**,
   then the provider's page (`/admin/system/integrations/<target>/`).
   Paste the new key in its field and select **Save**. The key is sealed at
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

1. Open the campaign's **Pause and resume mail** page
   (`/admin/campaign/<campaign id>/delivery`), signed in with Google within the
   last five minutes.
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
      **Retry failed mail preparation** there and wait for **Initial campaign
      mail preparation complete**. A report preparation that has *failed* never
      reaches its safe point by itself: find the failed daily or weekly
      report task on the Background work page (`/admin/system/background/`; the
      home page lists only the latest five failures of the past day), fix
      its cause (usually ParishSoft
      availability), choose **Retry report work** on the task's page, and
      wait for it to complete.
   2. Three five-minute clocks govern the rest (the sign-in, the sender test
      and the preview), so do steps 2 to 4 in one go.
      Confirming needs a Google sign-in from the last five minutes: choose
      **Confirm with Google**, and the sign-in brings you back to this page.
      Then choose **Preview and send a test to the configured Testing
      recipient**, which opens the campaign mail page, send the test there,
      go straight back to **Pause and resume mail**, and reload until it
      says **The current provider and sender accepted a test after this
      pause.** That proof is also valid for five minutes, and the preview
      and the confirmation both check it again.
   3. Give the reason and choose **Preview resume**. Review the exact
      preview: overdue invitations and reminders are coalesced (redundant
      slots coalesced, inapplicable ones skipped), overdue daily or weekly
      reports each become one report obligation, every held receipt is
      released, and future work keeps its original due time.
   4. Choose **Confirm resume of live delivery**, within five minutes of
      the sign-in, the test and the preview. Every input is checked again;
      anything that changed cancels the confirmation without releasing mail,
      and you preview again. If the confirm button is missing, or confirming
      reports an authentication error, changed inputs or "Check this
      value." (an expired preview), a clock has run out: confirm with Google
      again as in step 2, send a new test, and preview again.

If the campaign closes while delivery is paused, resuming no longer applies:
invitations and reminders follow the ordinary close policy and cannot be
released, and the page instead offers to resolve the held receipts and Admin
reports by type: release the ones that should still go, after the same
sender check, and cancel the rest with a reason. A type cannot be cancelled
while any of its messages is still submitting or unknown, and no resolution
at all (release, cancel or clear) is accepted while a
daily or weekly report is still being prepared: wait for it to finish, and
if its preparation has failed, retry it from the Background work page as in
step 4.1 above. Every resolution (release, cancel or clear) needs a Google
sign-in from the last five minutes and is confirmed within five minutes of
its preview, and a release also needs the five-minute sender test, all in
one go as for a resume. A missing confirm button or an authentication error
means the sign-in has expired, "Check this value." means the preview has,
and "This information changed" means something the preview relied on moved
(the sender test expired, report preparation started, or the held messages
changed); recover as for a resume. This does not reopen Family access.
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
and [Admin reports](../specs/stewardship/background-processing/spec.md#administrator-digests).
The guides linked in step 4 own each kind's resolution workflow.

For each message, from its detail page (`/admin/deliveries/<message id>`):

1. Look for it in the provider's own record: the delegated mailbox's sent
   mail or the Workspace admin log, by time and recipient.
2. Record what you found with **Save evidence note**.
3. If the provider shows it was sent, choose **Confirm delivery using
   external evidence**; the message is marked delivered and its occurrence
   completes.
4. If the provider shows it was not sent and it should still go, choose
   **Authorize potentially duplicate resend**, acknowledging that the Family
   may receive it twice; this creates a new attempt under the same
   occurrence. The button appears
   only when a resend is still permitted, and the conditions differ by kind:
   for an invitation or reminder, the campaign is inside its dates, the
   Family is still active, eligible and reachable and has not submitted, the
   slot is not already fulfilled, and no refresh, activation catch-up or
   restore hold stands in the way; for a submission receipt, no other message
   for that Family is still submitting or unknown; for a daily or weekly Admin
   report, report preparation is complete and the recipient is still an
   Administrator. A pause does not prevent the resend, and resolving the
   message lets the resume proceed: on an active campaign the resent message
   is held with the rest of the paused mail, and the resume's recovery plan
   decides it like any held message. A resent scheduled daily or weekly
   report is cancelled and folded into the next combined report of its
   kind, as for a failed report in step 5 (no content is lost; a resent
   manually requested weekly report is sent); a resent reminder is
   replaced by a later reminder due during the pause, and a resent
   invitation or reminder is cancelled if the Family has since submitted,
   become ineligible or lost every deliverable address; otherwise it is
   sent (the
   [gate round 5 ledger](stewardship-gate-round5-fixes-reviews.md) and the
   [pre-launch gate](stewardship-prelaunch-gate.md) record the checks);
   on a campaign closed while paused, a resent receipt or report
   joins the held messages you release or cancel by type (above), and one
   already released goes without waiting. When the button is absent, a
   resend is not permitted now; use the next step instead. The rules are
   recorded in the
   [Family-mail resolution guide](stewardship-family-mail-resolution.md) for
   invitations and reminders, the
   [submission receipts guide](stewardship-submission-receipts.md) for
   receipts, and the [daily](stewardship-daily-digests.md) and
   [weekly](stewardship-weekly-digests.md) digest guides for Admin reports;
   the [paused resend guide](stewardship-paused-resend.md) covers every kind
   during a pause.
5. If the provider shows it was not sent and no resend is wanted or permitted
   (for example, the Family has since submitted or become ineligible, or the
   report's recipient is no longer an Administrator), choose **Record that the
   provider did not send it (no resend)**, with the provider evidence in the
   note. The message is recorded as a failed delivery, exactly as if the
   provider had refused it, but no recipient address is suppressed; an
   invitation or reminder occurrence becomes failed, not fulfilled. A failed
   report whose recipient is not currently an Administrator counts as settled,
   like a report cancelled for a removed Administrator, so the report completes
   once its other recipients have it, whether the recipient was removed before
   or after; a failed report whose recipient is still an Administrator holds
   the report open until you choose **Retry failed delivery**, when no pause
   is involved. On an active campaign that is paused, that retry is not
   offered, and resuming folds the report into the next combined report of
   its kind, which carries its dates to the current Administrators; do not
   wait for the button. On a campaign
   closed while paused that retry is not available: if the recipient should no
   longer be an Administrator, remove them through the ordinary configuration
   change and the report settles, so the closed resolution can record its
   skip. Do this before you resolve the held reports by type: once that
   resolution has cancelled the report's other copies, a later removal can no
   longer record the skip and the report stays open.
   The message no longer counts as unknown, so a paused campaign can resume,
   and a receipt waiting behind it is no longer blocked. This action is
   available during a pause and on a campaign closed while paused. A later
   **Retry failed delivery** stays subject to the ordinary retry conditions.
   The [unsent resolution guide](stewardship-unsent-resolution.md) records the
   design.
6. If you cannot tell, leave it unresolved and note why; an unresolved
   message blocks schedule edits for its occurrence and is listed until
   resolved.

Never resend outside the portal, never resolve without a note, and never use
**Confirm delivery using external evidence** for a message the provider shows
was *not* sent, even to unblock a resume: record it as not sent instead. The
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

## Index

| Situation | Where |
| --- | --- |
| Install, upgrade, roll back | [Deployment runbook](stewardship-deployment-runbook.md) ([scripted](stewardship-deployment-runbook.md#scripted-upgrade)) |
| Nightly backup, off-host copy, restore drill, real restore | [Backup runbook](stewardship-backup-runbook.md) |
| Check a credential against its provider | [Smoke tools guide](stewardship-smoke-tools.md) |
| Replace a provider credential | [Above](#replacing-a-provider-credential); design in the [credential installer guide](stewardship-credential-installers.md) |
| Alert routing and windows | [Operational alerts guide](stewardship-operational-alerts.md) |
| Measure the launch send | [Above](#measuring-the-launch-send), with the [mail send report](stewardship-mail-send-report.md) |
| Production activation and withdrawal | [Above](#production-activation); design in the [go-live readiness](stewardship-go-live-readiness.md), [link preparation](stewardship-production-activation.md), [Production confirmation](stewardship-production-confirmation.md) and [withdrawal](stewardship-production-withdrawal.md) guides |
| HTTPS certificate expiry monitoring and renewal failures | [Above](#certificate-expiry) |
| Withdraw published campaign artwork | [Above](#withdrawing-published-campaign-artwork) |
