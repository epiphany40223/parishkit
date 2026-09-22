# Stewardship gate round 3 corrections reviews

This ledger records the independent review/fix rounds of the corrections
that the pre-launch gate's third integration round found:

- PL-I5 (Medium): the rendered service configurations omitted the deployment
  YAML's `operational_alerts`, so every service ran the default alert and
  source-staleness windows whatever the operator set; the rendered document
  now carries the policy, with a test that a non-default policy survives
  rendering.
- PL-I2 (Medium): the mail-provider outage runbook said sending resumes by
  itself, but the campaign-mail circuit stops sending until `mail-dispatch`
  restarts, and a systemic failure leaves permanent failures to retry; the
  [launch runbooks](stewardship-launch-runbooks.md#mail-provider-outage) now
  say to restart it and retry them.
- PL-I4 (Low): resume is hidden while an activation catch-up is incomplete,
  including a failed one; the runbook names its retry.
- PL-I5 (Low, Low): the backup refusal log records only a category, and a
  single nightly backup would raise the 24-hour overdue alert on any late
  night; the [backup runbook](stewardship-backup-runbook.md) gives the cause
  checklist and a twice-daily UTC schedule.

It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds, with a correction check after any round that validates a
finding. The Codex reviewer has been out of quota since September 20, 2026;
under the human's exemption, extended through October 30, 2026, a completed
Claude-only pass counts as a round, and each round records which sources
answered.

## Round 1

Claude only (Codex produced no structured output). Eight raw findings, one
validated and corrected:

- Medium: the outage runbook's new steps named internal states and a
  list-page action. The deliveries page filters by labels (**Failed
  delivery**, **Pending**, **Waiting to retry**), offers **Retry failed
  delivery** only on each message's page and only while delivery is not
  paused, and shows no counts; the steps and the exit condition now describe
  the page as it is.

All seven findings below the validation cutoff were taken: a failed delivery
also follows five exhausted attempts or exhausted preparation retries; the
backup checklist adds the startup lock, a pending migration, non-regular
files and the size bound, and no longer names an impossible same-name set;
the runbook says how to schedule in UTC; the alerts guide says environment
overrides act only where the deployment YAML is loaded and must be repeated
at retarget; tests now show every rendered service document carries the
policy and that retarget refuses a changed policy; and the runbooks link
this ledger. A correction check follows.
