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
- The backup runbook also corrects how the public key is installed:
  `backup-keygen` prints a JSON line, and only its `public_key` value belongs
  in the credential file.
- The [v1 launch scope](../plans/stewardship/v1-launch.md#schedule) names the
  Slack bot token and channel.
- The [pre-launch gate record](stewardship-prelaunch-gate.md#exit) lists staff
  validation, including the load check and browser checks, among what the
  human completes before approving, and indexes this ledger.

It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds for documentation, with a correction check after any round that
validates a finding, each recorded by which sources answered.

## Round 1

Claude only (Codex was out of credits). Five raw findings, two validated
and corrected:

- Medium: the backup runbook said to write the printed line to the key
  file, but `backup-keygen` prints a JSON object and the sealer accepts only
  the bare base64 key, so every backup would refuse. It now says to write
  only the `public_key` value.
- Medium: the reinstall procedure left the old deployment's backup cron
  jobs running and the new one without backups. It now disables the old
  jobs and sets up the key, cron jobs and first backup for the new one.

The three findings below the cutoff were taken: overridden paths outside
the runtime root need their own mounts, an unknown Testing delivery must be
resolved rather than waited for, and this ledger is indexed from the gate
record. A correction check follows.

## Round 2

Claude only (Codex was out of credits). Two raw findings, one validated
and corrected:

- High: the reinstall only stopped the old project, which keeps its
  networks, and the rendered topology gives the internal networks fixed
  subnets, so the new project's first start would have failed with an
  overlapping address pool. The old project is now taken `down` (never
  `down -v`), which removes only containers and networks; its data stays in
  the runtime root's bind mounts.

The Low below the cutoff was taken: the named-volume case now says to mount
the volume in place of the runtime root's bind mount. A correction check
follows.

## Round 3

Claude only (Codex was out of credits). Correction check: three raw
findings, none validated; the review rounds are closed. The three Lows were
taken: the reinstall waits for a running backup before `down`, names the
fixed `backend` and `proxy` subnets and the `runtime_network` setting, and
no longer offers moving the old root aside, since its rendered files hold
absolute paths under it.
