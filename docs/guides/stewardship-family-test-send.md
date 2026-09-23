# Stewardship chosen-Family test sends

The [Admin portal specification](../specs/stewardship/admin-portal/spec.md#production-transition)
describes a readiness test send that could render a selected Family, but only
the fictional sample existed, and the only way for staff to hold a Testing
code or link was the scheduled Testing invitation run, which mails every
Family with a deliverable address to the one Testing mailbox. This increment
adds the page **Send this email to chosen Families**, linked from the
fictional sample's test page as **Send this email to chosen real Families
(Testing recipient only)**, so staff can validate the Family
form as up to ten real Families at a time. The
[deployment runbook](stewardship-deployment-runbook.md#staff-validation-checklist)
says how staff use it.

## Design

- **A distinct outbox purpose.** Each send is an outbox message of purpose
  `family_test`, routed `testing_override` in the rehearsal credential
  namespace. Every scheduled-Family path filters on the `initial` and
  `reminder` purposes, and the occurrence guard ties a sender to its
  occurrence by semantic key, so a test message can never create, move or
  satisfy an occurrence or a fulfillment; the SQL additionally stops a
  `family_test` sender from writing occurrence or fulfillment rows.
- **A ticket per Family.** The web request records one ticket per chosen
  Family (`stewardship_family_mail_test`) and queues a `family_mail_test`
  task. SQL admits the ticket only for an Administrator whose sign-in is at
  most five minutes old, in Testing mode on the current draft campaign with a live Workspace
  integration and an active Testing credential set, for a Family that is
  active, eligible, deliverable and current, with at most ten tickets or
  messages in progress per campaign. Once a ticket leaves `queued` its Family
  is erased from it; only the outbox message, which Testing cleanup deletes,
  keeps the link.
- **The general worker prepares it.** Under its claim, the worker issues or
  reuses the Family's Testing credential (not date-gated, so it can run
  before the invitation is due; scheduled Testing mail later reuses the same
  credential), renders the chosen template for that Family, seals the
  credential substitutions and creates the outbox message, in one
  transaction. Preparation rechecks that the requesting Administrator is
  still enabled, the configuration still active and the template still in
  use. The mail-dispatch worker then sends it like any Family message.
- **Temporary gates hold; lasting loss ends it.** A restore review, a
  campaign work gate or a dirty or stale source population holds the ticket
  or message without spending its retry budget. A lasting loss of scope (mode,
  campaign, configuration, credential set, Administrator or template)
  cancels it, a Family that stays ineligible after a clean reconciliation
  cancels its message, and a message whose preparation keeps failing is
  cancelled rather than left pending. The scheduler settles tickets whose
  task ended.
- **Resolution.** An unknown outcome can be confirmed delivered or recorded
  as not sent. With the cancellations above, no test message can be left
  unfinished to block Production readiness. Resend and retry are refused.

It does not open the portal outside the campaign dates, does not satisfy the
readiness check (the fictional sample still does), and is deleted by Testing
cleanup. It adds a table, a purpose and guards to the fresh-install schema;
the baseline is regenerated.

## Reviews

This increment follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, since it changes mail dispatch and the schema, with a
correction check after any round that validates a finding, each recorded
here by which sources answered.

### Round 1

Claude and Codex both answered (two Claude shards). Twenty-two raw
findings; eight validated and corrected, with every Low below the cutoff
taken:

- High (both sources): a test message whose dispatch preparation failed
  for good (for example after its template was removed) stayed pending with
  no settling action, blocking Production readiness permanently. Exhausted
  preparation now cancels it, and a removed template cancels it too.
- High: a Family that became lastingly ineligible held its message forever
  in a free retry loop. After a clean, current reconciliation it now cancels.
- Medium (both sources): SQL checked only that the sign-in time matched a
  live session, not that it was within five minutes; it now enforces the
  window, and the cleanup test uses a genuinely fresh session.
- Medium (Codex): preparation did not recheck the requesting Administrator
  or the active configuration; it now does, in Python and in SQL.
- Medium: temporary gates cancelled queued tickets and spent the worker's
  retry budget; they now hold without charge, and only lasting scope loss
  cancels.
- Lows: portal eligibility is required at every admission point, tests pin
  the Production refusal and the closed-portal sign-in, the page labels a
  cleaned-up ticket, the Family link is computed inside the sample's own
  transaction, the preview/confirm form reuses the shared field checks, and
  the documents name the link and the page consistently.

A correction check follows.
