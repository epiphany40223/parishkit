# Stewardship chosen-Family test sends

The [Admin portal specification](../specs/stewardship/admin-portal/spec.md#production-transition)
describes a readiness test send that could render a selected Family, but only
the fictional sample existed, and the only way for staff to hold a Testing
code or link was the scheduled Testing invitation run, which mails every
Family with a deliverable address to the one Testing mailbox. This increment
adds **Send this email to chosen Families** so staff can validate the Family
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
  task. SQL admits the ticket only for an Administrator with a fresh sign-in,
  in Testing mode on the current draft campaign with a live Workspace
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
  transaction. The mail-dispatch worker then sends it like any Family
  message; its disposition refuses a changed scope, an inactive credential
  set or a campaign that is no longer a Testing draft. The scheduler cancels
  tickets whose scope went stale and fails those whose task failed.
- **Resolution.** An unknown outcome can be confirmed delivered or recorded
  as not sent, so it cannot block Production readiness forever; resend and
  retry are refused.

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
