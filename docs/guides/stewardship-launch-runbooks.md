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
a Family, a credential or a message. The Admin home page lists open security
events; the Background work page lists queued and running work and carries
the Admin-only warning that links to unresolved `delivery_unknown` messages.
If you receive no notices at all for a day, check that the mail provider is
healthy (below) before assuming quiet means healthy.

## Mail-provider outage

**You see:** the `mail_provider_unavailable` incident opens, by email and
Slack if configured; Family or staff mail stops arriving; the deliveries page
(`/admin/deliveries`) shows messages in `retry_wait`.

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
   (`smoke --target google_workspace --delegated-email …`). `unavailable` is
   the provider; `invalid` is the credential.
3. If the credential is invalid, install a replacement through the setup
   wizard's credential flow and acknowledge it, following the
   [credential installer guide](stewardship-credential-installers.md); the
   installer validates the new credential before it is used.
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
   blindly: confirm with the parish which organization is right, then install
   the replacement through the credential flow.
3. When the provider is back, use **Refresh now** on the home page
   (`/admin/source/refresh`) rather than waiting for the next scheduled run.

**It is over when:** the refresh completes and the incident resolves. Data
entered by Families during the outage was never at risk: submissions are
stored against the snapshot and reconciled on the next refresh.

## Pausing and resuming delivery

Pausing holds every live Family message and the digests you select while
leaving the portal and the Family form open; it is the tool for a provider
outage, a content mistake found after the schedule started, or any moment
when you need mail to stop now.

1. Open the campaign's delivery control page
   (`/admin/campaign/<campaign id>/delivery`).
2. Choose **Preview pause**, give the reason, and confirm with the fresh
   preview. Messages already handed to the provider are not recalled; queued
   and due ones are held.
3. While paused, held messages accumulate: Family messages, and by type the
   submission receipts and daily and weekly Admin reports. Fix the cause.
4. To resume, choose **Preview resume**, give the reason, and confirm.
   Resuming releases every held unsent Family message on its schedule. The
   receipts and Admin reports held meanwhile are resolved separately on the
   same page: release the types that should still go, cancel the ones that
   should not, and clear the pause record once every type is resolved; a
   later pause invalidates an unfinished resolution preview.

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
[background-processing specification](../specs/stewardship/background-processing/spec.md#family-invitations-and-reminders)
owns the policy; the
[delivery resolution guide](stewardship-family-mail-resolution.md) owns the
workflow.

For each message, from its detail page (`/admin/deliveries/<message id>`):

1. Look for it in the provider's own record: the delegated mailbox's sent
   mail or the Workspace admin log, by time and recipient.
2. Record what you found with **Save evidence note**.
3. If the provider shows it was sent, choose **Confirm delivery using
   external evidence**; the message is marked delivered and its occurrence
   completes.
4. If the provider shows it was not sent, choose **Authorize potentially
   duplicate resend**, acknowledging that the Family may receive it twice;
   this creates a new attempt under the same occurrence.
5. If you cannot tell, leave it unresolved and note why; an unresolved
   message blocks schedule edits for its occurrence and is listed until
   resolved.

Never resend outside the portal, and never resolve without a note: the note
is the only record of why a Family got one message or two.

## Index

| Situation | Where |
| --- | --- |
| Install, upgrade, roll back | [Deployment runbook](stewardship-deployment-runbook.md) |
| Nightly backup, off-host copy, restore drill, real restore | [Backup runbook](stewardship-backup-runbook.md) |
| Check a credential against its provider | [Smoke tools guide](stewardship-smoke-tools.md) |
| Replace a provider credential | [Credential installer guide](stewardship-credential-installers.md) |
| Alert routing and windows | [Operational alerts guide](stewardship-operational-alerts.md) |
| Production activation and withdrawal | [Production activation guide](stewardship-production-activation.md) |
