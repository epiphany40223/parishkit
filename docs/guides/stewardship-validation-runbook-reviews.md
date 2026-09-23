# Stewardship validation runbook reviews

This ledger records the independent review rounds of the runbook
corrections made before staff validation, when compiling the operator's
validation instructions exposed gaps the runbooks left to the operator:

- The [deployment runbook](stewardship-deployment-runbook.md) now gives the
  Google Workspace mail account's requirements (a service account with
  domain-wide delegation for the `https://mail.google.com/` scope, linking
  the [README's Google setup](../../README.md#google-cloud-and-google-workspace)),
  the Slack bot token and channel instead of a webhook, a new deployment
  UUID per installation, a readable deployment YAML, the full isolated
  `docker run` form of the offline commands and the generated file paths
  ([Commands and generated paths](stewardship-deployment-runbook.md#commands-and-generated-paths)),
  a non-destructive
  [reinstall procedure](stewardship-deployment-runbook.md#reinstalling-the-validation-deployment),
  and a
  [staff validation checklist](stewardship-deployment-runbook.md#staff-validation-checklist)
  that says how the Family form can be reached in Testing mode (only through
  scheduled Testing invitations inside the draft campaign's dates, sent for
  every Family to the one Testing recipient), browser checks, and what cannot
  be tested before activation.
- The [backup runbook](stewardship-backup-runbook.md) now runs `backup-keygen`
  and `backup-open` from the release image on the key machine, says when to
  run the restore drills and to repeat them after a reinstall, starts only
  `web` on the off-DNS replacement host, and keeps background services
  stopped during the Testing-mode drill's review, since Testing has no
  delivery pause.
- The [v1 launch scope](../plans/stewardship/v1-launch.md#schedule) names the
  Slack bot token and channel.

It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds for documentation, with a correction check after any round that
validates a finding, each recorded by which sources answered.
