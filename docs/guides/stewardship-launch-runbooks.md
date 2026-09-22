# Stewardship launch runbooks

The operator's procedures for the situations the
[v1 launch scope](../plans/stewardship/v1-launch.md#launch-critical-remaining-work)
(item 6, the launch portion of
[OPS-08.05](../tasks/stewardship/operations.md#ops-08-observability-health-and-operational-runbooks))
expects during the first live campaign: a mail-provider outage, a ParishSoft
outage, pausing and resuming delivery, and messages whose delivery is unknown.
Deployment, upgrade and rollback are the
[deployment runbook](stewardship-deployment-runbook.md); backup and restore are
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
source staleness), as the
[operational alerts guide](stewardship-operational-alerts.md#operational-policy-configuration)
describes. Notices name the deployment mode and the incident kind only, never
a Family, a credential or a message.

Operational email goes out through the same Google Workspace mailbox as
campaign mail, so during a mail-provider outage its own alert may never
arrive. Configure the optional Slack channel for that reason. In the portal,
every Admin page shows **Critical events recorded in the past 24 hours** when
there are any, the home page lists open security events and recent failed
background tasks, and the Background work page carries the Admin-only warning
that links to unresolved `delivery_unknown` messages. Notices are sent only
when an incident opens, escalates or resolves, so a quiet day is normal and
proves nothing; for a heartbeat, watch for the daily Admin report arriving on
schedule.

## Mail-provider outage

**You see:** the `mail_provider_unavailable` incident opens, reaching you by
Slack if configured (its email may not arrive, for the reason above) and as
the critical-events banner; Family or staff mail stops arriving; the
deliveries page (`/admin/deliveries`) shows messages in `retry_wait`.

**The system does:** it opens the incident on a systemic failure or on three
consecutive unavailable results for the same provider configuration, keeps
retrying due messages on their schedule, and resolves the incident by itself
only after a real healthy SMTP observation newer than the last failure, as the
[mail health guide](stewardship-mail-health.md) explains. A message the
provider may have accepted without confirming becomes `delivery_unknown`
(below) rather than being retried.

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

**It is over when:** the incident resolves by itself after the next
successful send, and `retry_wait` messages drain. Do not resend by hand and
do not resolve a `delivery_unknown` message without evidence.

## ParishSoft outage

**You see:** the `source_refresh_failed` incident (a refresh failed) or
`source_stale` (no successful refresh within the freshness window, 30 minutes
by default); the home page's latest refresh time stops advancing.

**The system does:** it keeps serving the last successful snapshot, so the
Family form, reports and staff pages keep working on that data; it retries on
its nightly and fifteen-minute cadence and resolves the incident by itself
after a successful refresh of the current scope, as the
[source health guide](stewardship-source-health.md) explains. Two other source
incidents are refusals, not outages: `source_tenant_mismatch` (the key now
sees another organization) and `source_destructive_change` (a full load lost
an unexpected share of records). The application holds those refreshes and
does not overwrite the snapshot; stop and investigate before touching the
credential or the source.

**You do:**

1. Check ParishSoft's status and whether the API key still works: run the
   read-only smoke check inside `worker`
   (`smoke --target parishsoft --organization-id …`).
2. If the key is invalid or the organization differs, do not replace it
   blindly: confirm with the parish which organization is right, then replace
   it as [Replacing a provider credential](#replacing-a-provider-credential)
   below describes.
3. When the provider is back, use **Refresh now** on the home page
   (`/admin/source/refresh`) rather than waiting for the next scheduled run.

**It is over when:** the refresh completes and the incident resolves. Data
entered by Families during the outage was never at risk: submissions are
stored against the snapshot and reconciled on the next refresh.

## Replacing a provider credential

A replacement is a timed, two-sided change: the installer checks and installs
the new credential, and every service that uses it must then be recreated
and acknowledge it, within one hour, or the previous working credential is
restored.

1. In the portal, open **Integrations**, then the provider's **Replace
   credential** page (`/admin/configuration/integrations/<target>/credential`),
   after signing in with Google within the last five minutes. Submit the new
   credential; it is sealed at once and never shown again. The isolated
   installer checks connectivity first and sends no message.
2. When the status page says the candidate passed and is installed, recreate
   every consuming service (for the mailbox, `mail-dispatch`; for ParishSoft
   and Slack, `worker`) with `up --detach --force-recreate`, using exactly one
   Compose file (`compose.json`, or `compose-slack.json` when Slack is
   configured) and the deployment's usual project name, never an overlay
   combination.
3. Acknowledge the credential inside each recreated service yourself; nothing
   does it for you. Take the request UUID from the status page's **Request:**
   line and run, for each consumer,
   `docker compose ... exec -T <service> pk-stewardship acknowledge-credential --config <that service's configuration> --request-id <UUID>`.
   Use `exec` into the running service, never `compose run`: a one-off
   container cannot acknowledge. The
   [credential installer guide](stewardship-credential-installers.md) and the
   runtime guide's setup section describe the protocol.
4. When the status page says every required consumer acknowledged, choose
   **Review and select the acknowledged fingerprint**, so the integration's
   configuration names the credential that is now installed. Until then the
   credential cannot support normal work.
5. Re-run the smoke check to confirm.

If the status page instead says the replacement was not applied (it failed,
or the hour ran out), the previous credential is restored and the candidate
file removed. Recreate the same consumers again with
`up --detach --force-recreate`, not a restart: a restarted container keeps the
removed file's old mount and goes on failing. Then re-run the smoke check
before trying again.

## Pausing and resuming delivery

Pausing holds all unsent live mail, Family messages, submission receipts and
daily and weekly Admin reports alike, while the portal and the Family form
stay open; Families can still submit, and their confirmations are held too.
It is the tool for a provider outage, a content mistake found after the
schedule started, or any moment when mail must stop now. The controls exist
only for the current Production campaign.

1. Open the campaign's delivery control page
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
      Family group on the **Overdue Family-mail planning** panel must be
      resolved. The resume controls are hidden entirely while an activation
      catch-up is still running.
   2. Choose **Preview and send a test to the configured Testing recipient**
      and wait until the page says **The current provider and sender accepted
      a test after this pause.** The proof is valid for five minutes.
   3. Give the reason and choose **Preview resume**. Review the exact
      preview: overdue invitations and reminders are coalesced (redundant
      slots coalesced, inapplicable ones skipped), overdue daily or weekly
      reports each become one report obligation, every held receipt is
      released, and future work keeps its original due time.
   4. Choose **Confirm resume of live delivery**. Every input is checked
      again; anything that changed cancels the confirmation without releasing
      mail, and you preview again.

If the campaign closes while delivery is paused, resuming no longer applies:
invitations and reminders follow the ordinary close policy and cannot be
released, and the page instead offers to resolve the held receipts and Admin
reports by type: release the ones that should still go, after the same
sender check, and cancel the rest with a reason. A type cannot be cancelled
while any of its messages is still submitting or unknown, or while report
preparation is running. This does not reopen Family access. A resolution
clears the pause only when it leaves nothing held, submitting or unknown; if
the last unknown delivery is reconciled afterwards, choose **Clear an empty,
fully resolved pause (select no types)** to clear it.

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
   is held with the rest of the paused mail and goes only when delivery
   resumes; on a campaign closed while paused, a resent receipt or report
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
   the report open until you choose **Retry failed delivery**. On a campaign
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

## Index

| Situation | Where |
| --- | --- |
| Install, upgrade, roll back | [Deployment runbook](stewardship-deployment-runbook.md) |
| Nightly backup, off-host copy, restore drill, real restore | [Backup runbook](stewardship-backup-runbook.md) |
| Check a credential against its provider | [Smoke tools guide](stewardship-smoke-tools.md) |
| Replace a provider credential | [Above](#replacing-a-provider-credential); design in the [credential installer guide](stewardship-credential-installers.md) |
| Alert routing and windows | [Operational alerts guide](stewardship-operational-alerts.md) |
| Production activation and withdrawal | [Production activation guide](stewardship-production-activation.md) |
