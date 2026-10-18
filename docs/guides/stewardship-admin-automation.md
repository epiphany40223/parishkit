# Stewardship Admin automation: operator guide

The Admin automation command line lets an operator, a script or an AI
assistant on the deployment host run Admin commands as a named
Administrator, without clicking through pages. The
[Admin automation specification](../specs/stewardship/admin-automation/spec.md)
defines it; this guide says how to use it. It is delivered in steps
([ADM-11](../tasks/stewardship/admin-portal.md#adm-11-admin-automation-interface)).
This version has the session commands (`login start`, `login wait`,
`logout`, `whoami`, `sessions` and `commands`), the
[status commands](#status-commands) and
[schedule changes](#schedule-changes). The other areas follow in later
releases, each listed in the command catalog.

Anyone who can run Docker on the host already controls the deployment; the
command line adds no limits on top of that, only notices and audit (see the
specification's [threat model](../specs/stewardship/admin-automation/spec.md#threat-model)).
Treat a full-scope session file like the Administrator's own signed-in
browser.

## Before the first session

The host wrapper is
[`tools/stewardship-ops/pk-admin`](../../tools/stewardship-ops/pk-admin), a
POSIX shell script; copy it to the host (it needs `docker`, `openssl` and
standard tools such as `stat`, `base64`, `head`, `mktemp`, `tee`, `wc`,
`ln` and `pgrep`, and no Python). Run it as the operator account that runs Docker
Compose, never as the application user (10001).

Session files live in `ROOT/run/admin-automation`, where `ROOT` is
`PARISHKIT_ROOT` or `/opt/parishkit`. `ROOT/run` belongs to the application
user with mode `0700`, so give each operator account search access to it
and create the directory, owned by that account with mode `0700`:

```sh
sudo setfacl -m u:OPERATOR:x ROOT/run
sudo install -d -o OPERATOR -g OPERATOR -m 0700 ROOT/run/admin-automation
```

The directory is in no backup and mounted into no container, so a session
is never restored from a backup and the web process never sees a session
file. Nothing cleans `ROOT/run` today; any future cleanup of it must skip
this directory. A host rebuild that changes `/etc/machine-id` ends every
session; pair again.

The wrapper reads these settings (all optional):

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARISHKIT_ROOT` | `/opt/parishkit` | The deployment root |
| `PK_ADMIN_COMPOSE_FILE` | `ROOT/config/services/compose.json` | The rendered Compose file the deployment runs (`compose-slack.json` when Slack is set up) |
| `PK_ADMIN_PROJECT` | `stewardship` | The Compose project name |
| `PK_ADMIN_WEB_CONFIG` | `ROOT/config/services/web.yaml` | The web configuration, as the web container sees it |
| `PK_ADMIN_SESSION` | none | The session to use when `--session` is not given |
| `PK_ADMIN_EXPORT_DIR` | `SESSION_DIR/exports` | Where `export fetch` saves files (see [report exports](#report-exports)) |
| `PK_ADMIN_MACHINE_ID` | `/etc/machine-id` | The machine identity file the host digest is computed from |

## Pairing a session

1. Choose a short session name (1 to 32 of `a-z`, `0-9` and `-`) and a
   label that says what the session is for, with no personal data (every
   Administrator sees it). Ask the Administrator which address they sign in
   with.

   ```sh
   pk-admin login start --name ops --label "send monitoring" \
     --expect-email ADMIN_ADDRESS --scope read-only --days 7
   ```

   It creates `ROOT/run/admin-automation/ops.session` with a fresh secret
   and prints, at once, a document with `user_code`, `approve_url` and
   `expires_at` (ten minutes from now).
2. The Administrator opens the link
   (`/admin/users/automation/approval/`) in a browser signed in to the
   portal, confirms their sign-in with Google if the page asks, and enters
   the user code.
   The page shows the label, the address and the requested scope and
   lifetime, which they may lower but not raise, and **Approve**. A code
   meant for another address gets the same reply as a wrong code.
3. Collect the session:

   ```sh
   pk-admin login wait --name ops
   ```

   It waits up to ten minutes. `pairing_pending` (exit 5) means it is not
   approved yet: run it again. `pairing_expired` (exit 5) means the code ran
   out first; the wrapper deletes the file, so start again.

Choose `read-only` for an assistant that only watches, and the shortest
lifetime that fits the task (at most 30 days). A full-scope session will be
able to run every Admin action the Administrator can, including the
irreversible ones, without asking again, once those commands are released.

## Running commands

```sh
pk-admin whoami
pk-admin sessions
pk-admin commands
pk-admin --session ops whoami
pk-admin status
```

The session is `--session NAME`, else `PK_ADMIN_SESSION`, else the only file
in the directory; with several files and no choice the wrapper lists them
and exits 2. Each command prints exactly one JSON document (schema
`pk-admin/1`) on standard output, except one that writes a file there
(`logs export`, see [System logs](#system-logs), `export census`, see
[Census changes download](#census-changes-download), `export limitations`,
see [Limitations download](#limitations-download), `export roster`, see
[Roster changes download](#roster-changes-download), `followup export`, see
[Ministry follow-up download](#ministry-follow-up-download), and `export download`, see
[report exports](#report-exports)), whose document goes to standard error; warnings, such as a session with less than
72 hours left, and structured logs go to standard error. The document's
fields, the exit codes and the error codes are in the specification's
[running a command](../specs/stewardship/admin-automation/spec.md#running-a-command);
`pk-admin commands` prints the catalog of every command with its scope,
options and result fields. On exit 3 or 6, keep the `startup_rejected` or
`task_failed` line from standard error and its `correlation_id` when you
report the failure (see
[correlation and logging](../specs/stewardship/admin-automation/spec.md#correlation-and-logging)).

The wrapper sends the session secret on standard input, never as an
argument, and forwards your own standard input only when the command line
has a `-` input, or from a terminal for a command that prompts (none in this
release), so an interactive run never waits for input. It deletes the
session file only when a command exits 5 reporting `session_ended`, after a
successful `logout`, when `login wait` reports `pairing_expired`, and when a
`login start` fails or is interrupted. `session_missing` keeps the file: the
name may be wrong, or a database restore may be under way. Exit 127 means
`docker`, or `pk-stewardship` in the web container, was not found.

## Ending sessions

- `pk-admin logout` ends the session and deletes its file. If another
  ending (a revocation, for example) got there first, it reports
  `"ended": false` with `"reason": "already_ended"` and that ending's
  `end_reason`, exits 0 and still deletes the file.
- **Automation access** (`/admin/users/automation/`, in the Users and
  access group) opens on every live session of any Administrator; any of
  them can be revoked there. Tick **Include ended sessions** to also see
  your own sessions that ended in the last 30 days, and choose a column
  heading to sort a table. Revocation takes effect at the next command.
- `pk-admin sessions` lists your live sessions; add `--include-ended` for
  the ended ones and `--sort` to order them as the page does. Write a
  descending sort with `=`, as in `--sort=-ended`: a separate `-ended`
  would be read as an option.
- A session ends by itself at its deadline, when its Administrator loses the
  Administrator role or is disabled or removed, and after an offline
  Admin-access recovery.
- Using a session file from another host ends it (`host_mismatch`).

## Notices and alerts

Every Administrator's dashboard shows an **Automation notices** panel for
each approval, refused use and ending, until that Administrator
acknowledges it. Approvals and refused uses also go out by email and, when
configured, Slack, as the fixed-text alerts `automation_approved` and
`automation_refused` (see the
[operational alerts guide](stewardship-operational-alerts.md#automation-incident-kinds)).
Revoke at once any session nobody recognizes, on Automation access.

## Restore and ending every session

A restored database holds the restored sessions' digests while the session
files survive on the host, so every restore ends them all before `web`
starts. With every online service stopped:

```sh
docker compose ... run --rm admin-recovery revoke-automation-sessions \
  --config RECOVERY_CONFIG --reason restore
```

It prints only a count. Run it with `--reason revoked_by_operator` to end
every session at once for any other reason; it is offline in either
case, so stop every online service first, and every session, including
ones still in use, must then be paired again. It is part of the
[real restore](stewardship-backup-runbook.md#restore-for-real) and the
v1 [manual restore](../plans/stewardship/v1-launch.md#manual-restore-for-v1-replaces-item-2).

## Status commands

These read what the matching Admin page shows, as counts, states,
identifiers and UTC instants only: no Family names, email addresses, codes
or other personal data. A read-only session can run every one of them. Each
records the same view event in System logs as its page (`status` the home
page's, `task list` and `task show` Background work's, `send progress` and
`send history` Sent mail's, `system health` System health's);
`schedule show`, `go-live readiness` and `go-live progress` record none,
like their pages. `system health` needs an Administrator's session, as its
page does.

```sh
pk-admin status
pk-admin task list --state failed --size 20
pk-admin task show TASK_ID --watch 5
pk-admin send progress --watch 10
pk-admin send history
pk-admin schedule show
pk-admin go-live readiness
pk-admin go-live progress --watch 30
pk-admin system health --watch 60
```

Each command's `result` member holds the fields below. Instants are UTC
ISO 8601 strings, identifiers are UUID strings, and enumerations are the
stored values (for example `queued`, `initial`, `production`). A section
your roles would not show on the page is `null`.

### `status`

| Field | What it holds |
| --- | --- |
| `as_of`, `mode` | When it was read; `testing` or `production` |
| `campaign` | The current campaign's `id`, `name`, `state`, `version`, `starts_at`, `ends_at` and `delivery_paused`, or `null` |
| `source` | The last ParishSoft refresh: `refreshed_at` (when the current snapshot was promoted), the last full refresh's success, failure (with `full_failed_task_id`) and `full_running`, incremental success and failure, `frequency`, `next_full_at`, `delta_refresh` (`quarter_hour`, `hourly`, `off`, or `times` for a schedule with listed quick times); the [data age and connection](../specs/stewardship/operations/spec.md#parishsoft-data-age-and-connection): `full_started_at`, `data_as_of`, `connection` (`failing`, `not_checked`, `working` or `unknown`) with `connection_at`; `overdue_full_at`, the due time of the first scheduled full refresh since the last one started that has not yet run (`null` when none is due), `out_of_date` when it is more than `source_stale_seconds` late with `late_minutes`, and `held_for_send` when a bulk Family send is holding it within the send's allowance, with `resume_at` (when it runs at the latest) or `catching_up` (the send ended or the resume point passed) |
| `next_mail` | The next Family mail's `kind` and `due_at` |
| `families` | Counts: `active`, `eligible`, `responded`, `eligible_responded` |
| `unreachable_families` | Families no mail can reach (a count) |
| `offsite_backup` | The off-site copy's `state` (`uploaded`, `failed`, `disabled`, `none` or `unset`), `at`, `last_copy_at` and `set_name` |
| `unfinished_keys` | Integrations with a new key not yet switched to, by `target` |
| `ministry_catalog` | Counts of recent catalog `refreshes`, `missing` and `retired` Ministries |
| `security_events`, `automation_notices` | How many are unacknowledged for you |
| `recent_failures` | Tasks failed in the last day: `id`, `type`, `updated_at` |
| `tasks` | Background work counts by state, `active`, and `delivery_unknown` |
| `presence` | `count`: Families on the portal now |

### `task list` and `task show TASK_ID`

| Field | What it holds |
| --- | --- |
| `as_of` | When it was read |
| `counts`, `state`, `task_type` | `task list` only: nonterminal task counts by state, and the filters applied |
| `page`, `size`, `sort`, `has_next`, `matching`, `matching_capped` | The page of rows, as on Background work |
| `tasks` or `task` | Each task's `id`, `root_id`, `parent_id`, `retry_sequence`, `type`, `state`, `action`, `version`, `attempt`, `initiator_id`, `progress` (`phase`, `current`, `total`, `percent`), its instants and `active` |
| `latest_run_id`, `events` | `task show` only: the newest retry, and the history (`version`, `at`, `action`, `state`, `attempt`, `progress`) |

### `send progress` and `send history`

| Field | What it holds |
| --- | --- |
| `campaign_id` | The current campaign |
| `mode`, `paused`, `upcoming` | `send progress` only: the mode, whether delivery is paused, and whether a Production send is about to start |
| `send` | `send progress` only: the send in progress or the latest, or `null` |
| `page`, `pages`, `count`, `size`, `sends` | `send history` only: one page of sends, newest first |

Each send has `kind`, `active`, `paused`, `stalled`, `held`, `total`,
`done`, `percent`, the counts `sent`, `failed`, `uncertain`, `remaining`,
`unprepared`, `unplanned`, `waiting`, `unreachable` and `not_needed`,
`rate_per_minute`, `started_at`, `last_settled_at`, `finished_at`,
`finish_at` and `minutes_left`. In `send history` each also has `send` (the
value Sent mail's send filter takes), `number`, `mode`, `cycle`,
`scheduled_at`, `replaced`, `current`, `earlier`, `live`, `cancelled` and
`minutes`.

### `schedule show`

| Field | What it holds |
| --- | --- |
| `campaign_id`, `editable` | The campaign, and whether its dates may still change |
| `version` | What a schedule change is previewed against |
| `window` | `start_date`, `end_date`, `timezone` |
| `schedules` | Each schedule's `id`, `kind`, `date`, `time`, `weekday`, `subject`, `template_version`, `comparison` (whether a daily digest includes Compared with past campaigns), its first resolved send times (`resolved`: `key`, `due_at`) and `more` |
| `series` | Each remembered reminder series (a repeat saved with `"remember": true`): its `id`, its repeat `rule` and the `schedules` (reminder ids) it holds |

`schedule show` reads the current campaign unless `--campaign UUID` names
another. An unknown campaign, or no current campaign, is exit 1
(`not_available`). A campaign that is not the current one, or that
background work holds while mail is being sent, is exit 1
(`stale_version`), as on the Dates and mail schedules page.

### `go-live readiness` and `go-live progress`

These matter again for the next campaign. Both read the current campaign
unless `--campaign UUID` names another. Starting cleanup, the public web
address check, preparing links, confirming Production and withdrawing stay
on the pages until ADM-11 PR 12.

`go-live readiness` is the Go-live readiness page's preview of the current
Testing draft, without its public web address check:

| Field | What it holds |
| --- | --- |
| `campaign_id`, `observed_at`, `target_state` | The draft, when it was read, and what confirming would make it (`scheduled`, `active` or `closed`) |
| `checks_passed`, `problems` | Whether the settings and data checks passed, and the codes of those that did not (for example `full_refresh_required`, `family_test_mail_required`), which the page explains in words |
| `family_templates` | The selected Family mail templates, by record id |
| `source` | The full ParishSoft refresh the preview relies on: `state` (`ready` or why not), `current_id`, `full_id`, `observed_at`, `expires_at` |
| `families` | Family mail impact counts: `families`, `active`, `eligible` and `not_eligible` (with and without an eligible email address), `deliverable`, `messages` due at once, `coalesced_slots`, `skipped_slots`, `blocked_families` |
| `admin_reports` | Admin report mail impact counts: `daily_messages`, `weekly_messages`, `coalesced_slots`, `empty_weekly_reports`, `blocked_groups` |
| `cleanup` | The Testing data cleanup would delete: `submissions`, `families`, `messages`, `unresolved`, `total`, `inventory` (`category`, `count`) and `message_states` (`state`, `count`) |
| `mail_test_id` | The successful Family test email that counts, or `null` |
| `cleanup_requests` | The ten most recent cleanup requests: `id`, `state`, `created_at` |
| `version` | Changes whenever anything the preview counts changes |

The Admin report recipients and the Testing Families are not shown; the
Testing Families list is an export (PR 12). A campaign that is not the
current Testing draft is exit 1 (`stale_version`), as on the page.

`go-live progress` is the Production activation page:

| Field | What it holds |
| --- | --- |
| `campaign_id`, `campaign_state` | The campaign and its state (`scheduled`, `active` or `closed`) |
| `confirmation_id`, `confirmed_at` | The Production confirmation |
| `withdrawal_available` | Whether the page offers withdrawal (before the start only) |
| `preparation` | The initial mail preparation, or `null` when the campaign was confirmed before its start and has none: `complete`, `task_id` (follow it with `task show`), `task_state`, `updated_at`, `phase`, `items_completed`, `groups_completed`, `failure` (a stored code, or `null`) and `retry_available` (the page offers **Retry failed mail preparation**) |
| `outcomes` | Each count the confirmation previewed, by `key` (`family_messages`, `daily_messages`, `weekly_messages`, `coalesced_slots`, `active_families`, `eligible_families`, `no_email_families`): `preview`, `actual`, `difference`, and `complete` once that count is final |

A campaign that is not the current Production campaign, a Testing draft
included, is exit 1 (`denied`), as on the page; the current Production
campaign with no confirmation receipt is exit 1 (`not_available`). Retrying
failed preparation stays on the page until PR 12.

### `system health`

The [System health](../specs/stewardship/admin-portal/spec.md#system-health)
page as a document: `problems` (each `kind`, its `severity`, `error` or
`warning`, the `tiles` that show it in page order, the `subjects` it is
about, each a `service`, `process` and `target`, and the time `at` its
sentence states; a condition in several processes is one problem, which
the page may split between two tiles, so its `severity` is the worse of
the two), the
`processes` with their state, version and mail sender state,
`missing_services`, `versions`, `schema_current`, the reasons Family email
waits (`delivery_paused`, `planning_held`, `retry_waiting`), the 24-hour
send count `daily_sent`, the `daily_limit` Family email stops at and
`daily_resume_at`, when that count falls below the limit again (null while
it is below), the open `incidents`, the ParishSoft `refresh` outcome, a refused load's
`refused_counts`, the newest backup and the `offsite` copy's state, and
the newest debug-off switch (`debug_switch`: `set_at`, and `cleared_at` and
`cleared_by` once cleared, or null when none was ever set), and the
one-time acceptance of the refusal shown (`drop_acceptance`: `id`,
`created_at`, `expires_at` and `live`, or null while it has none). The
pause's reason and who paused it, who set the debug-off switch, who
accepted a change, and the page's sentences, are not in it.

### `system backup-now`

System health's **Take a backup now**. It needs a full-scope session and
always prompts (or takes `--yes`), showing the page's preview: when the
last backup finished and its size, whether it will also be copied off-site,
that it follows the usual retention, and that it waits while a Family email
send is in progress. It is refused (exit 1, `stale_version`) while a
request is waiting or running or a restore is under review. It is a
privileged ([fresh-gated](#confirmations)) action, so System logs record
`automation_fresh_gate` beside it. `--request-key UUID` (a version 4 UUID,
written to standard error when not given) names the request, so a repeat
returns the first one (`created` false). It prints `request_id`, `created`,
`status` (as the Backups panel words it; `waiting` until the server's
five-minute check picks it up) and `created_at`, and records
`backup_requested` and `admin_cmd_system_backup_now`. Follow it with
`system health --watch 60` (`backup_request`).

```text
pk-admin system backup-now
```

### `system refresh-accept-preview` and `system refresh-accept`

System health's **Accept this change once**, for a full ParishSoft refresh
refused because counts fell too far, after checking in ParishSoft that the
change is real (the
[launch runbooks](stewardship-launch-runbooks.md#accepting-a-large-parishsoft-change)
describe when). Both need a full-scope session.
`system refresh-accept-preview` changes and records nothing; it prints the
refusal's `attempt_id` and `refused_at`, every recorded count (`measure`,
`before`, `after`, `limit_percent`, `failed`; counts only), `examples` (the
page's example Families for each failing count, as Family DUIDs only, never
names; look their names up in ParishSoft or on the page) and
`preview.token`, the page's signed binding, valid for 15 minutes and for
this Administrator only. It is refused (exit 1, `stale_version`) while the
newest refusal cannot be accepted: none is shown, it was accepted already
(live or lapsed), or the load had no Families or no Members.

`system refresh-accept --token TOKEN` accepts that refusal. It always
prompts (or takes `--yes`) with the counts that fell too far and the page's
tick box, "I checked this in ParishSoft, and the change is real". It is a
privileged ([fresh-gated](#confirmations)) action, so System logs record
`automation_fresh_gate` beside it. It records the acceptance (the token's
key names it, so a repeat returns the first, `created` false), asks for a
full refresh as **Refresh now** does, and records `source_drop_accepted`
and `admin_cmd_system_refresh_accept`. Like the page, it also acknowledges
that refusal's own entry in the critical-problems banner
(`critical_events_acknowledged`, with the acceptance as its subject), so
`events list` no longer lists it; other entries stay. It prints
`acceptance_id`, `created`, `created_at`, `expires_at`, `live` and
`task_root_id` (the requested full refresh's root task, or null when it
could not be requested; the next full refresh uses the acceptance anyway).
A refusal that changed since the preview, an expired token or another
acceptance already given is exit 1, `stale_version`: review again. With
`--token -` the token is read from standard input, which then cannot answer
the prompt, so add `--yes`. Follow it with `system health --watch 60`
(`drop_acceptance` and `refresh`).

```text
pk-admin system refresh-accept-preview
pk-admin system refresh-accept --token TOKEN
```

### `system debug-off`

System health's **Turn off debug logging**: it sets the debug-off switch,
which turns debug logging off in every service within about a minute, until
the server operator clears it offline (`pk-stewardship debug-off-clear`, in
the [deployment
runbook](stewardship-deployment-runbook.md#turn-off-debug-logging-530)). It
needs a full-scope session and always prompts (or takes `--yes`), showing
the page's preview: the services with debug logging on, that lines already
written stay in the logs, that the server's setting still says on, and that
only the server operator clears the switch. It is refused (exit 1,
`stale_version`) while the switch is set. It is a privileged
([fresh-gated](#confirmations)) action, so System logs record
`automation_fresh_gate` beside it. `--request-key UUID` (a version 4 UUID,
written to standard error when not given) names the switch, so a repeat
returns the first one (`created` false). It prints `switch_id`, `created`,
`set_at` and `active` (whether that switch is still set), and records
`debug_logging_turned_off` and `admin_cmd_system_debug_off`. Follow it with
`system health --watch 60` (each process's `debug_logging`).

```text
pk-admin system debug-off
```

### `system debug-allow`

System health's **Allow debug logging again**, in Testing only: it clears
the debug-off switch, so services started with `PARISHKIT_DEBUG_LOGGING=1`
log in debug form again within about a minute. It needs a full-scope
session and always prompts (or takes `--yes`), showing the page's preview:
when the switch was set, and that it never turns debug logging on in a
service started without it. It is refused (exit 1, `denied`) in Production,
where only the server operator clears the switch, and (exit 1,
`stale_version`) while no switch is set. It is a privileged
([fresh-gated](#confirmations)) action, so System logs record
`automation_fresh_gate` beside it. `--switch UUID` names the switch to
clear (by default the one set now), so a repeat naming a switch you already
cleared returns it (`created` false). It prints `switch_id`, `created` and
`cleared_at`, and records `debug_logging_allowed` and
`admin_cmd_system_debug_allow`.

```text
pk-admin system debug-allow
```

### `system mail-clear-preview` and `system mail-clear`

System health's **Clear the halt**, in the Mail tile,
for a mail sender halted by a fault that waiting cannot fix, once the
cause is fixed (the
[mail-provider outage runbook](stewardship-launch-runbooks.md#mail-provider-outage)
describes when). Both need a full-scope session; neither needs a recent
sign-in.

`system mail-clear-preview` asks the mail sender to check that the
mailbox still signs in (unless a check is already running, or one passed
in the last five minutes), waits for it (at most two minutes; `--no-wait`
prints at once), and prints `halt` (`halt_id`, `kind`: `before_data` or
`after_data`, `began_at`), `mailbox_check` (`status`: `waiting`, `running`,
`passed`, `refused`, `unavailable` or `unanswered`), `waiting` (emails
waiting to be sent), `unsettled` and `unsettled_ids` (emails in
`delivery_unknown`, which must be settled first, as `delivery resolve`
does), `delivery_paused`, `reason` (why the halt cannot be cleared now:
`checking`, `check_failed`, `check_needed`, `unsettled`; null when it can)
and `preview.token`, the signed binding, valid for 15 minutes and for this
Administrator only (null with a reason). It records no audit event. It is
refused (exit 1, `stale_version`) while no mail sender is halted, or its
halt is not recorded yet (within about 30 seconds of the halt).

`system mail-clear --token TOKEN` clears that halt. It always prompts (or
takes `--yes`) with what clearing does. It records the clear signal (the
token's key names it, so a repeat returns the first, `created` false) and
`mail_halt_cleared` with `admin_cmd_system_mail_clear`. Like the page,
it also acknowledges the halt's own entries in the critical-problems banner
that the preview saw (`critical_events_acknowledged`, with the clear signal
as its subject). It prints
`clear_id`, `halt_id`, `created` and `created_at`. Both mail senders act on
it within about 30 seconds and send one probe email per consumer (so up to
two); if the fault is still there, sending halts again under a new halt. A halt that changed
since the preview, an expired token, a mailbox check that no longer
allows it or an email that became `delivery_unknown` is exit 1,
`stale_version`: review again. With `--token -` the token is read from
standard input, so add `--yes`. Follow it with `system health --watch 60`
(the mail senders).

```text
pk-admin system mail-clear-preview
pk-admin system mail-clear --token TOKEN
```

### Watching and errors

`--watch SECONDS` (2 to 300) on `task show`, `send progress`,
`go-live progress` and `system health` prints one document per poll,
`final` false until the last. It stops with exit 0 once the task has
finished, once no send is in progress or about to start, once System
health lists no problem except those waiting cannot clear (off-site copies
not set up, `offsite_missing`, and a requested backup that failed, did not
finish or was never picked up, `backup_request_failed`: the
`WAIT_CANNOT_CLEAR` set in `parishkit.stewardship.system_health`), or once
Production preparation is complete,
absent or its task has stopped (a failed task waits for the page's retry,
and `retry_available` says so). It stops with exit 7 and the last state
after `--timeout` seconds (default and maximum three hours;
`watch_timeout`), and with exit 5 if the session ends.
Ctrl-C (or TERM, or a closed terminal or SSH session) stops the watch inside
the web container too: `pk-admin` sends it SIGINT there, waits up to 10
seconds for that stop and reports it if it fails or finds no watch, shows
the watch's last document (`watch_interrupted`, the last state) if it
arrives within 5 seconds (after Ctrl-C only), and exits 130 (131 for
QUIT, Ctrl-\, 143 for TERM, 129 for HUP). A second Ctrl-C meanwhile changes nothing. On a host
without util-linux `setsid -w`, Ctrl-C also ends the docker client at once,
so the watch still stops but its last document is not shown, and a second
Ctrl-C can cut the stop off. If the stop fails, the watch runs until its timeout
or until you revoke the session in Automation access; it changes nothing
meanwhile. Run in the container directly, Ctrl-C stops it with exit 7.
Ctrl-C on any other command ends only `pk-admin`; the command in the
container runs to its end. `--timeout` without
`--watch` is refused. For commands that record a view event, only the first
poll is recorded in System logs. An unknown task or campaign is exit 1
(`not_available`). A restore under review, or a configuration change being
applied, is exit 3 (`unavailable`), so try again shortly.

`pk-admin commands` lists every command's result fields. The route-parity
ledger, `src/parishkit/stewardship/admin_parity.py`, says which command
covers each Admin page, or why none does yet.

## Schedule changes

`schedule preview` and `schedule confirm` change mail schedules and, while
they may still change, the campaign dates (for a live campaign, its end date
alone), through the same review and confirmation as the Dates and mail
schedules page. Both need a full-scope session.
Neither asks for a confirmation at the prompt, because the page asks for
none. `config request show` follows the
resulting change; any session may run it.

```sh
pk-admin schedule show
pk-admin schedule preview --expected-version VERSION --changes - < change.json
pk-admin schedule confirm --token - < token.txt
pk-admin config request show REQUEST_ID --watch 5
```

Write a bare `-` as its own argument, as above. The wrapper forwards your
standard input only for that exact argument, so `--changes=-` or
`--token=-` reaches the command with no input. A value given inline,
`--changes '{...}'`, needs no standard input.

1. Read `version` from `schedule show` and pass it as `--expected-version`.
   If the settings changed since then, the preview is exit 1
   (`stale_version`); read again.
2. Write the change document (below). It is a default, pending
   Administrator confirmation.
3. `schedule preview` prints the page's review (below). Problems the page's
   form would show are exit 1 (`invalid`), with `error.fields` listing each.
4. `schedule confirm --token` takes `preview.token` within fifteen minutes,
   as the page does, with the same `--campaign` as the preview (or none
   for both). A token from the page's review works here, and a token from
   here works on the page. An expired token, or one the settings or the
   campaign's sends have overtaken, or one whose previewed send time has
   since passed (preview again with a later time), is exit 1
   (`stale_version`): preview again. An altered token, or one for another campaign, is exit 1
   (`invalid`); another Administrator's is exit 1 (`denied`).
5. The configuration installer then applies the request; follow it with
   `config request show`. Only your own requests are found; any other is
   exit 1 (`not_available`).

### The change document

The document is JSON with two optional members:

```json
{
  "window": {"end_date": "2054-10-30"},
  "schedules": [
    {"id": "SAVED-ID", "time": "10:30:00"},
    {"id": "OTHER-SAVED-ID", "delete": true},
    {"kind": "reminder", "date": "2054-10-20", "time": "09:00:00",
     "template_version": "EMAIL-ID"},
    {"repeat": {"frequency": "weekly", "start": "2054-10-05",
                "end": "2054-10-29", "weekdays": [0, 3]},
     "time": "10:00", "template_version": "REMINDER-EMAIL-ID"}
  ]
}
```

| Member | What it holds |
| --- | --- |
| `window` | Any of `start_date`, `end_date` (`YYYY-MM-DD`), `timezone` and `overlap_confirmed` (true or false, only for a campaign with a financial period). Members you leave out keep their values. Changing the dates once they are locked is exit 1 (`stale_version`), as on the page, except that a live campaign (scheduled or open in Production, before it closes) may change `end_date` alone. Family codes and links keep working until the new end date. |
| `schedules` | A list of entries, each one of the six below. Saved schedules you do not name stay as they are; leaving one out never removes it. |

| Entry | Members |
| --- | --- |
| Change a saved schedule | `id` (from `schedule show`) and any of `date`, `time` and `weekday` (in the campaign's time zone, as for adding one), `template_version` and, for a daily digest, `comparison` (`true` or `false`: whether it includes Compared with past campaigns). Members you leave out keep their values. Its `kind` cannot change, so it may not be given. |
| Remove a saved schedule | `id` and `"delete": true`, nothing else. |
| Add a schedule | `kind` (`initial`, `reminder`, `daily_digest` or `weekly_digest`) and the members its kind needs: `date` for `initial` and `reminder`, `weekday` (0 is Monday) for `weekly_digest`, and `time` (in the campaign's time zone, or in the new one when `window` changes `timezone` in the same document; the Admin portal takes times in your computer's zone instead) and `template_version` for all. A `daily_digest` may add `"comparison": true`; it is off without it. A `time` may be written in any form the page accepts, such as `09:00:00`, `9:00`, `9am` or `21:00`, and is stored as `HH:MM:SS`. |
| Add repeating reminders | `repeat` (the rule below), `time` and `template_version`, as for one reminder, and nothing else. It adds one ordinary reminder per date the rule gives, as if you listed each one. Every date must fit: unlike the page, which leaves out a date that does not fit, the command refuses the whole change. A problem with one date alone, such as a date after the campaign closes, names that date; a conflict between schedules, such as a date at the same moment as a saved reminder or not after the initial invitation, comes back as a plain `schedules` error, as for reminders listed one by one. A rule that gives no dates or more than 30, or skips a month, is exit 1 (`invalid`). The rule itself is not saved unless you add `"remember": true`, which also saves it as a reminder series holding exactly the reminders this repeat adds, so later changes can treat them as one. The reminders are the same either way, and they send as ordinary reminders. |
| Change a remembered series | `series` (a series `id` from `schedule show`) and `scope`: `"following"` with `from` (one of its reminder ids) changes that reminder and the later ones ("this and following"); `"series"` changes the whole series from its first reminder that has not sent. Optionally a new `repeat` rule (checked as above), `time` and `template_version`. The rules for what stays, what is replaced and what is refused are in the [admin-automation spec](../specs/stewardship/admin-automation/spec.md#schedules-and-configuration) (a reminder whose time has passed without any work yet can start a whole-series edit and, if its time or email changes, may send right after confirm). |
| Delete a remembered series | `series` and `"delete": true`, nothing else. Its reminders that have not sent are removed with it; ones that sent stay as ordinary reminders. |

| `repeat` member | What it holds |
| --- | --- |
| `frequency` | `daily`, `weekly` or `monthly` |
| `start`, `end` | The first and last dates (`YYYY-MM-DD`, at most a year apart), both included |
| `weekdays` | For `weekly`: the days of the week, 0 (Monday) to 6 (Sunday), such as `[0, 3]` |
| `monthly` | For `monthly`: `{"day": 5}` for the 5th of each month, or `{"ordinal": 2, "weekday": 6}` for the second Sunday (`ordinal` 1 to 4, or -1 for the last), never both. A range with a month that lacks the chosen day, such as the 31st with September, is refused, naming the months, rather than skipping them as the page does |

`template_version` names a saved email of the schedule's kind: the
`template_version` of a schedule in `schedule show`, or the email's id from
the page and email templates (a command for those arrives later).

### The review and the result

| `schedule preview` field | What it holds |
| --- | --- |
| `campaign_id`, `version` | The campaign, and the configuration version the change was made against |
| `window` | `before` and `after` (`start_date`, `end_date`, `timezone`), and the names of the campaign values that `changed` |
| `changes` | Each change's `id`, `operation` (`add`, `update` or `remove`), `kind`, its values and first send times `before` and `after` (as in `schedule show`; `null` when added or removed), and its `impact`: `delivered`, `cancellable`, `failed`, `blocking`, `occurrences` and `outboxes`, the page's counts |
| `blocking` | Sends in progress or with an uncertain result. While it is above zero, `preview` is `null`, as the page offers no **Confirm** |
| `preview` | `token`, which `schedule confirm` takes |
| `series` | Each reminder series the change remembers (a repeat with `"remember": true`): its new `id`, its `rule` and its reminders' ids (`schedules`); empty otherwise. A series edit lists here the new series it starts |
| `series_changes` | Each remembered series a `series` entry updates or removes: its `id`, `operation` (`update` or `remove`) and, for an update, its new `rule` and `schedules`; empty otherwise |

Each `error.fields` entry has `field` (`window.<name>`,
`schedules.<id>.<name>`, `schedules.new<n>.<name>` for the n-th added
entry from 0, `schedules.new<n>.<YYYY-MM-DD>.<name>` for one date of a
repeat, or `window` or `schedules` for a problem between fields),
`code` (`required` or `invalid`) and `message`, the page's text.

| `schedule confirm` field | What it holds |
| --- | --- |
| `created` | False when this token was confirmed before: the original request is returned and nothing changes |
| `request` | The request, as `config request show` prints it |

| `config request cancel` field | What it holds |
| --- | --- |
| `cancelled` | False when this same cancellation was recorded before; nothing changes |
| `request` | The request, as `config request show` prints it, before the installer ends it |

`config request cancel REQUEST_ID --reason TEXT` cancels a live campaign's
end-date change that is stuck before it applied, for example one the
configuration installer keeps failing on. Through `pk-admin` it needs
`--yes`, since it cannot read a typed answer (its first words are
`config request show`'s). It records your reason and the cancellation, and
the installer then
restores the previous settings and marks the change failed; the end date
stays as it was. See the
[specification](../specs/stewardship/admin-automation/spec.md#schedules-and-configuration)
for which changes it accepts.

| `config request show` field | What it holds |
| --- | --- |
| `request_id`, `sequence` | The request and its latest checkpoint |
| `state` | `staged`, `validating`, `prepared`, `yaml_activated`, `applied`, `failed` or `cancelled`; `--watch` stops at the last three |
| `failure` | The stored reason, only when it failed (`stale_base`, `invalid_candidate`, `actor_unauthorized`) |
| `candidate_version_id`, `applied_version_id` | The configuration the request proposes, and the one applied (only once applied) |

### When the outcome is unknown

Exit 6 (`outcome_unknown`) from `schedule confirm` means the request may
have been recorded: the database connection dropped once the request was
written, or an error came after it committed. The error names the request
in `error.request_id`, fixed before the command acted. Run
`config request show` with it: `not_available` means nothing was recorded.
Or repeat `schedule confirm` with the same token within its fifteen
minutes: it returns the request if there is one and records it if not.
After that, preview again. A session that ended is still exit 5, and a
database error before the request was written is exit 3, with nothing
changed.

System logs show each confirmation as `admin_cmd_schedule_confirm`,
attributed to the approving Administrator with the automation session as
its subject. A schedule change creates no automation notice: notices are
for approvals, refused use, access and key changes, fresh-gated and
irreversible actions, and endings.

## ParishSoft refresh

`refresh start` asks for a full ParishSoft refresh now, as **Refresh now**
on the Refresh from ParishSoft page does. It needs a full-scope session and
asks for no confirmation, as the page asks for none. `refresh status` shows
what the page shows; any session may run it.

```sh
pk-admin refresh status
pk-admin refresh start --request-key "$(uuidgen | tr A-Z a-z)"
pk-admin task show TASK_ROOT_ID --watch 10
```

Pass your own `--request-key` (a UUID), or the command makes one and writes
it to standard error before it acts, as `task retry` does. Repeating it
with the same key returns the same refresh and changes nothing; a key used
on the page works here, and the other way round. A new request while a full
refresh is already waiting joins that one, as on the page; while one is
running, it queues one full refresh to run after it. The key must be a
version 4 UUID (`uuidgen` makes one).

| Field | What it holds |
| --- | --- |
| `created` | `refresh start`: false when this key was used before; the original refresh is returned |
| `request_key` | `refresh start`: the key, yours or the one made for you |
| `refresh` | `refresh start`: `command_id` (the key), `request_id` and `task_root_id` (follow it with `task show`) |
| `running`, `waiting` | `refresh status`: whether a refresh is running, and whether a full refresh waits that a new request would join |
| `lateness_minutes` | `refresh status`: how late a scheduled full refresh may be before it counts as overdue |
| `source` | `refresh status`: the latest full and quick update facts, as in `status`'s `source` without `refreshed_at` |

A key used for another refresh is exit 1 (`invalid`). If ParishSoft is not
configured, or the configured organization changed, it is exit 3
(`unavailable`), as the page reports. Exit 6 (`outcome_unknown`) names the
key in `error.request_id`: repeat the command with it. System logs show
each new request as `admin_cmd_refresh_start`.

## Sample test emails

`test sample-preview` and `test sample` are **Preview and test email** and
its **Send this test email**: one fictional message from an email revision,
sent only to the Testing recipient, never to a Family. Both need a
full-scope session.

```sh
pk-admin test sample-preview REVISION_ID --request-key "$(uuidgen | tr A-Z a-z)" > preview.json
jq -r .result.preview.token preview.json | pk-admin test sample --token - --yes
```

| Field | What it holds |
| --- | --- |
| `subject` | `test sample-preview`: the sample's subject, as the page shows it. Read the message itself on the page |
| `testing_recipient_set` | `test sample-preview`: whether a Testing recipient is configured (its address stays on the page) |
| `pending`, `unknown` | `test sample-preview`: a test is still being sent; an earlier test's outcome is unknown |
| `tests` | `test sample-preview`: the page's recent tests: `id`, `state`, `created_at` and `current` (same configuration and email) |
| `preview` | `test sample-preview`: `token`, which `test sample` takes |
| `created`, `request_key`, `test` | `test sample`: false when this token was sent before; the key; the test's `id`, `state`, `task_id` and `created_at` |

A token made on the page works here and the other way round, and sending
the same token again returns the same test. While an earlier test's outcome
is unknown, `test sample` shows the page's words, "A previous test may have
arrived; I want to send another test.", and waits for `yes` (or takes
`--yes`). An old preview, or a test still being sent, is exit 1
(`stale_version`): preview again. System logs show each send as
`admin_cmd_test_sample`.

## Chosen-Family tests

`test families-preview` and `test families` are **Send to chosen
Families**: the current Testing draft's invitation or reminder, rendered
for up to ten real Families with their own Testing codes and links, and
sent only to the Testing recipient, never to the Families. Both need a
full-scope session; `test status` (any session) lists recent tests.

```sh
pk-admin test families-preview REVISION_ID --family 1234 --family 5678 > preview.json
jq -r .result.preview.token preview.json | pk-admin test families --token - --yes
pk-admin test status
```

| Field | What it holds |
| --- | --- |
| `families` | `test families-preview`: each DUID you gave, in order, with `eligible` and `eligibility` (`eligible`, `unknown`, `ineligible`, `undeliverable` or `stale_source`) |
| `available`, `in_progress` | `test families-preview`: how many more tests may start, and how many are in progress (at most ten) |
| `held`, `credentials_ready` | `test families-preview`: campaign work or a restore review holds sending; the Testing codes exist |
| `preview` | `test families-preview`: `token`, which `test families` takes |
| `export` | `test families-preview --names`: the names export's `export status` document (null without `--names`) |
| `created`, `request_key`, `tickets` | `test families`: false when this token was sent before; the key; each ticket's `id`, `sequence` (the order of your DUIDs), `state` and `task_id` |
| `tickets` | `test status`: recent tickets' `id`, `created_at`, `request_key`, `sequence`, `state` and `message_state`, without DUIDs |

The preview never shows Family names. To check them, add `--names` with
`--timezone`: the preview also requests an export of each DUID with the
name the page shows, and `export fetch` downloads it. The file is CSV unless
you add `--format xlsx` or `--format pdf`. Delete the file once used.

```sh
pk-admin test families-preview REVISION_ID --family 1234 --family 5678 \
    --names --timezone America/New_York > preview.json
export_id="$(jq -r .result.export.id preview.json)"
pk-admin export status "$export_id" --watch 5
pk-admin export fetch "$export_id"
```

Repeating it with the same `--request-key` returns the same export; System
logs show `export_requested` and `admin_cmd_export_family_test_names`. `test
families` always shows the page's words, "I understand that these real
Families' names and codes will be sent to the Testing recipient.", and
waits for `yes` (or takes `--yes`). It is a privileged action, so the
dashboard shows an automation notice and
System logs show `automation_fresh_gate` with `admin_cmd_test_families`. A
token made on the page works here and the other way round; sending it again
sends nothing more. An old preview, a Family that can no longer be tested,
or too many tests in progress is exit 1 (`stale_version`): preview again.

## Family portal maintenance

`portal maintenance show` and `portal maintenance set` are **Family portal
availability**: whether Families can use the portal, and its Close and
Reopen buttons. Any session may run `show`; `set` needs a full-scope
session.

```sh
pk-admin portal maintenance show
pk-admin portal maintenance set --state closed --message "We expect to be back by 3 PM."
pk-admin portal maintenance set --state open
```

| Field | What it holds |
| --- | --- |
| `closed` | Whether the Family portal is closed for maintenance |
| `since` | When it was closed (null while open) |
| `message` | The note Families see while it is closed (empty when there is none) |
| `closed_by_id` | The id of the Administrator who closed it (null while open) |

`--message` (or `-`, to read it from standard input, with `--yes`) goes
only with `--state closed`. It follows the page's rules: at most 500
characters and no email address; anything else is exit 1 (`invalid`)
before any prompt. Leave out personal details: the note is also kept in
System logs. `set` always shows what it is about to do, including the
page's reminders (wait about 10 seconds after closing before changing
data; closing does not stop email), and waits for `yes` (or takes
`--yes`). It is a privileged action, so the dashboard shows an automation
notice, and System logs show
`automation_fresh_gate` and `admin_cmd_portal_maintenance_set` beside the
page's own `family_maintenance_started` or `family_maintenance_ended`. The
result is the new state, as `show` prints it. Asking for the state the
portal is already in changes nothing and is exit 1 (`stale_version`): read
it again with `show`.

## Pause and resume mail

`send controls` is the read of **Pause and resume mail**: whether live mail
to Families is paused, what is queued or held, and what can be paused,
resumed or resolved now. Like the page, it needs an Administrator; a
session of any scope may run it, and it records nothing in System logs, as
opening the page records nothing. It reads the current campaign only;
without one it is exit 1 (`not_available`). The page's pause, resume and
resolution follow below.

```sh
pk-admin send controls
```

| Field | What it holds |
| --- | --- |
| `campaign_id` | The current campaign |
| `available` | Whether the page offers its controls: the current campaign is live (Production) and scheduled, active or closed |
| `paused` | Whether live delivery is paused |
| `paused_at`, `reason` | When it was paused and the reason the pausing Administrator gave (both null when not paused) |
| `inventory` | Live mail by state: `queued` (unsent, held or not), `held` (stopped by the pause), `submitting` (being handed to the mail service), `unknown` (not sure it arrived), `stranded` (held invitations and reminders a closed campaign will not send), and `types`, one entry per mail type: its `kind` and the same four counts |
| `next_due` | The next scheduled send: `at` (null when none remain), `count` (schedules due then) and `types` (each `kind` and `count`); it counts schedules, not emails |
| `health` | The [sender check](../specs/stewardship/admin-portal/spec.md#live-delivery-pause) a resume or release needs: `ready` (true while paused when no check is needed or it has passed), `required`, `trouble` (why one is needed: `test_in_progress`, `provider_failure`, `test_failed` or `sender_changed`; null when not), `checked_at` and `expires_at` |
| `pause_available` | Whether the page offers Preview pause: `available` and not paused |
| `resume_available`, `resolve_available` | Whether the page offers Preview resume, or Resolve held messages (a paused, closed campaign); both false unless `available` |
| `resolution` | The Resolve held messages choices, null unless `resolve_available`: `types` (each `kind`, `name`, `held` and `cancellable`), and whether `release`, `cancel` and `clear` are offered |
| `family_recovery` | While paused, the page's overdue Family email counts: `selected`, `coalesced`, `skipped`, `blocked`, `deferred` and `unmaterialized` (null unless `available` and paused) |
| `report_recovery` | While paused, the page's overdue Admin digest counts: `selected`, `slots`, `unmaterialized`, `prepared` and `blocked` (null unless `available` and paused) |

`paused`, `paused_at`, `reason`, `inventory`, `next_due` and `health` print
whatever `available` says; the controls and choices follow the page, which
draws them only when `available` is true. Every number is a count, apart
from the reason; no recipient, message or mail-service response is printed.

Pausing, resuming and resolving held messages work as on the page: a
preview first, then its confirmation. Each preview asks for a reason (the
page shows it), needs a full-scope session and records nothing; each runs
the page's own preview, so it is refused exactly where the page offers no
such button (exit 1, `stale_version`), for example a resume while any
email is still being handed to the mail service or not sure it arrived, or
before a needed sender check has passed.

```sh
pk-admin send pause-preview --reason "Wrong reminder text" > preview.json
jq -r .result.preview.token preview.json | pk-admin send confirm --token - --yes
pk-admin send resume-preview --reason "Reminder fixed"
pk-admin send resolve-preview --decision cancel --type receipt \
    --type weekly_digest --reason "Campaign closed"
```

`send resolve-preview` is the Resolve held messages form of a paused,
closed campaign: `--decision release` or `cancel` takes one or more
`--type` (`receipt`, `daily_digest`, `weekly_digest`), and `clear` takes
none. A preview prints `action`, `campaign_id`, the `reason`, `expires_at`, the
`inventory` it bound (as `send controls` prints it), for a resume its
`plan` (`before_start`: nothing is overdue yet; `family`: the overdue
`family_recovery` and `report_recovery` counts), for a resolution its
`resolution` (`decision`, `types` and each type's follow-up `coverage`),
and `preview.token`.

`send confirm --token TOKEN` is the preview's Confirm button. The token is
the page's own and lasts five minutes, so a preview made on the page
confirms here and the other way round. It is a privileged action and needs
a full-scope session, so it always asks, naming the action, the reason, the counts and the page's own caution
(for a resolution, each type's follow-up coverage too); `--yes` answers,
and `--token -` (standard input) needs it. The page then checks everything
again, as it does for its own button, and records the control. System logs
then show the page's own events (`campaign_pause` or `campaign_resume`;
for a resolution `delivery_held_release`, `delivery_held_cancel` or
`delivery_held_clear`, with `campaign_resume` when it ends the pause), with `automation_fresh_gate` and
`admin_cmd_send_confirm` beside them. An out-of-date
token is exit 1 (`stale_version`): preview again. Repeating a confirmed
token returns the same control with `created` false and records nothing.
The result is `created`, `command_id`, `action` and `status`, which is
`send controls`'s document after the change.

## Hosted files

`files list` is the **Hosted files** page's library: the files that pages
and emails link to or show, and where each is used. Like the page, it
needs an Administrator; a session of any scope may run it, and it records
nothing in System logs, as opening the page records nothing.

```sh
pk-admin files list
```

It prints `count` and `total_bytes` (the library's totals), `max_files`
and `max_bytes` (its limits), and `files`, sorted by placeholder name, as
the page sorts by default:

| Field | What it holds |
| --- | --- |
| `id` | The file's id |
| `name` | The uploaded file name |
| `slug` | The placeholder name |
| `placeholder` | The placeholder that pages and emails use, such as `{{ file.ministry-guide }}` |
| `image_tag` | For an image, the page's ready-to-copy image tag; empty for a document |
| `type` | The stored type: `pdf`, `docx`, `xlsx`, `pptx`, `png` or `jpeg` |
| `width`, `height` | An image's size in pixels; null for a document |
| `size` | The size in bytes |
| `link` | The file's public link |
| `missing` | Whether the stored copy is gone from the server (the page's Missing) |
| `uses` | Where it is used: `source` (`content`, `pending_change` or `unsent_mail`) and the page's `description` |

Anyone with a file's link can open the file, as the page says. A
library that cannot be read now (the server's file storage is not
configured) is exit 3 (`unavailable`). Uploading files stays on the page
for now.

`files delete` and `files rename` are the page's **Delete** and **Edit**.
Each needs a full-scope session and an Administrator. Each works only on a file that nothing uses: a file in
use is exit 1 (`invalid`), with `error.fields` naming where it is used, in
the page's words, and nothing changes.

```sh
pk-admin files delete FILE_ID [FILE_ID ...]
pk-admin files rename FILE_ID --slug new-name
```

`files delete` takes one to 100 file ids from `files list` and deletes them
all or none. A deletion cannot be undone, so it names the files and asks
you to type `yes` (or pass `--yes`). It prints `deleted` and
`already_deleted`: an id with no file left counts as already deleted, so
repeating a deletion is safe. A busy library is exit 3 (`unavailable`);
retry. System logs show the page's `hosted_file_deleted` for each file and
the command's `admin_cmd_files_delete`.

`files rename` changes the file's placeholder name; its link never changes.
It does not ask for confirmation, as the page does not. A name that is not
lowercase letters, digits and single hyphens (up to 64 characters), or that
another file uses, is exit 1 (`invalid`) with the page's message for the
`slug` field; an id with no file is exit 1 (`not_available`). It prints
`changed` and the `file`, as `files list` prints it. Renaming to the
current name changes nothing; otherwise System logs show the page's
`hosted_file_slug_changed` and the command's `admin_cmd_files_rename`.

## Pages and emails

`content list` is the **Pages and emails** page's list for the current
campaign, `content show` one page's or email's editor as it opens, and
`content history` its **Content history** page. Like the pages, they need
an Administrator; a session of any scope may run them,
and they record nothing in System logs, as opening the pages records
nothing. With no current campaign, each is exit 1 (`not_available`).

```sh
pk-admin content list
pk-admin content show {page,email} SLOT [--revision ID] [--default]
pk-admin content history [--revision ID]
```

`content list` prints `campaign_id`, `version` (the applied
configuration's version) and, in the page's order, `pages` and `emails`:

| Field | What it holds |
| --- | --- |
| `pages[].slot`, `emails[].slot` | The page or mail type, such as `welcome` or `reminder` |
| `pages[].name`, `emails[].name` | Its name on the page, such as "Family welcome" |
| `pages[].state` | `empty`, `default` (the default text) or `edited` (the page's Edited) |
| `emails[].scheduled` | Whether schedules send this mail type (invitations and reminders) |
| `emails[].singleton` | The confirmation email, which has exactly one text |
| `emails[].revisions` | Its emails: `id`, `subject`, `excerpt` (the start of its text), `used_by` (the schedules that send it, named as on Dates and mail schedules), `deletable`, `state` (`default` or `edited`) and `resave` |
| `resave` | Null, or the page's re-save notice: `removed` (markup today's cleaning drops), `lost` (placeholders only that markup held) and `blocked` (the page's "Can't be sent until fixed") |

A confirmation email that is only a retired closing note has a null `id`,
as the page lists it. While background work, such as a mail send, holds
the campaign, the page refuses and so does `content list`: exit 1
(`stale_version`); try again later.

`content show` takes a page or email by its `content list` slot, and one
email of a mail type by its `revisions[].id` with `--revision`. As in the
editor, `email confirmation` needs no `--revision` (it has one text, shown
with any retired closing note folded in), and any other mail type without
`--revision` is the editor's new, empty email. It prints `campaign_id`,
`version`, `kind`, `slot`, `name`, `revision_id` (the saved record shown,
or null), `state` and `resave` (the saved text's, as in `content list`),
`from_default` and `content`:

| Field | What it holds |
| --- | --- |
| `content.subject` | The email's subject; null for a page |
| `content.html` | The saved HTML, as the editor opens it |
| `content.text` | The email's plain-text version; null for a page, which has none in the editor |
| `content.generate_text` | Whether the editor opens with "Generate plain text from HTML" checked (the plain text is exactly what the HTML generates); null for a page |
| `content.show_banner` | Whether this Family email shows the campaign banner; null where the editor has no such choice |

`content` is null for an empty slot. With `--default`, `content` holds the
slot's default text as saving it would store it, and `from_default` is
true, as the editor's "Start from the default text" (or "Reset to the
default text") link pre-fills it; nothing changes. `revision_id`, `state`
and `resave` still describe the saved text, not the default. A slot that
is unknown or not shown for this campaign, `--revision` on a page, or an
email the campaign does not hold is exit 1 (`not_available`), and a held
campaign `stale_version`, as for `content list`.

`content history` prints `campaign_id`, `state` (the campaign's),
`configuration_id` (the retained configuration version the page names in
its technical details) and `entries`, each retained page and email with
its `id`, `kind` (`page` or `email`), `slot`, `name` and `subject`. With
`--revision` and an entry's `id`, `revision` holds that entry's `id`,
`kind`, `slot`, `name` and `sample`: the page's fictional sample, its
`subject`, `html` and `text`; without it, `revision` is null. An ID the
campaign did not retain, a purged campaign, or a campaign whose retained
configuration is gone is exit 1 (`not_available`). Editing a page's or
email's text stays on the page for now.

`content preview --delete` and `content confirm` are the email tables'
**Delete**, split in two as `schedule preview` and `schedule confirm` are
for a schedule's Delete: the preview builds the deletion of one or more
unused emails and prints a token; nothing changes until `content
confirm`. They need a full-scope session and an Administrator. Name each
email by its `content list` `revisions[].id`, with one `--delete` per
email; only emails whose `deletable` is true can go.

```sh
VERSION=$(pk-admin content list | jq -r .result.version)
pk-admin content preview --delete ID [--delete ID ...] \
  --expected-version "$VERSION" |
  jq -r .result.preview.token | pk-admin content confirm --token -
```

| Field | What it holds |
| --- | --- |
| `action` | `delete` |
| `version`, `campaign_id` | The configuration version the deletion was built against, and the current campaign |
| `deleting` | Each email being deleted: `id`, `slot` (its mail type) and `subject` |
| `preview.token` | What `content confirm --token` takes, valid for fifteen minutes |
| `created`, `request` (`content confirm`) | As `parish confirm` prints them; follow the change with `config request show --watch` |

An email a schedule still sends, the confirmation email, an id the
campaign does not hold, or the same id twice is exit 1 (`invalid`), with
the page's explanation at `delete`; nothing is deleted. A version that is
no longer current, background work holding the campaign, or an expired
token is exit 1 (`stale_version`): read `content list` again. An altered
token is exit 1 (`invalid`). `content confirm` records
`admin_cmd_content_confirm` in System logs with the request; repeating it
with the same token returns the same request.

`content preview --html HTML` (or `--html -` to read it, untrimmed, from standard
input) is the editors' live preview: what the server makes of the HTML
you would save. It reads nothing from the campaign, changes nothing and
records nothing. It prints `action` (`plain_text`) and `plain_text`:
`html` (the cleaned HTML), `text` (the plain text "Generate plain text
from HTML" would save) and `removed` (the markup cleaning drops, in plain
words). HTML longer than the editor accepts is exit 1 (`invalid`).

## Campaign images

`artwork show` is the read of **Campaign images**: the current campaign's
email banner and Family page icons. Like the page, it needs an
Administrator; a session of any scope may run it, and it records nothing
in System logs, as opening the page records nothing.

```sh
pk-admin artwork show
```

| Field | What it holds |
| --- | --- |
| `version` | The applied configuration's version |
| `campaign_id` | The current campaign |
| `images` | Every slot, in the page's order `banner`, `welcome`, `member`, `financial`, `closing`: `slot`, `name` (the page's label), `unavailable` and `image` |
| `unavailable` | Null, or the page's reason the slot cannot take an upload now (its Family page is not shown for this campaign's modules); a current image can still be removed on the page |
| `image` | Null without a current image, or its `id`, `width` and `height` (pixels), `size` (bytes) and `sha256` |

Scripts should test whether `unavailable` is null, not match its text:
the wording is the page's and may change. With no current campaign,
`artwork show` is exit 1 (`not_available`).
Uploading images stays on the page.

`artwork remove-preview` and `artwork remove` are the page's **Remove
this image** and its **Apply**, as `parish preview` and `parish confirm`
are for the parish profile. They need a full-scope session and name the
slot, as `artwork show` lists it. Nothing changes until `artwork remove`;
the slot then shows no image until a new one is uploaded on the page.
A slot whose upload is unavailable can still be removed.

```sh
VERSION=$(pk-admin artwork show | jq -r .result.version)
pk-admin artwork remove-preview banner --expected-version "$VERSION" |
  jq -r .result.preview.token | pk-admin artwork remove banner --token -
```

| Field | What it holds |
| --- | --- |
| `version` (`artwork remove-preview`) | The configuration version the removal was reviewed against |
| `campaign_id`, `slot`, `name` | The current campaign, and the slot and its label on the page |
| `image` | The image being removed, as `artwork show` prints it (null if its stored image is missing) |
| `preview.token` | What `artwork remove --token` takes for the same slot, valid for fifteen minutes |
| `created`, `request` (`artwork remove`) | As `parish confirm` prints them; follow the change with `config request show --watch` |

A slot with no image, or no current campaign, is exit 1
(`not_available`). A version that is no longer current, or an expired
token, is exit 1 (`stale_version`): read `artwork show` again. Another
slot's or an altered token is exit 1 (`invalid`). `artwork remove`
records `admin_cmd_artwork_remove` in System logs with the request;
repeating it with the same token returns the same request.

## No-reminders WorkGroup

`workgroup show` reads the current campaign's **No-reminders WorkGroup**:
the ParishSoft Family WorkGroup whose Families get no Reminder emails.
It needs an Administrator; a session of any scope may run it, and it
records nothing in System logs, as opening the page records nothing.

```sh
pk-admin workgroup show
```

| Field | What it holds |
| --- | --- |
| `version` | The configuration version; `workgroup preview --expected-version` takes it |
| `campaign_id` | The current campaign |
| `name` | The WorkGroup's name, or null when no WorkGroup is set |
| `state` | The page's **Now**: `off` (none set), `pending` (the next ParishSoft refresh reads it), `missing` (ParishSoft has no Family WorkGroup of that name: check the name) or `found` |
| `families` | When `found`, how many Families the newest refresh found in it; otherwise null |
| `excluding` | How many Families get no Reminders now (0 when none): after a rename, until a refresh reads the new name, the previous WorkGroup's; when `missing`, the set kept from before |
| `excluding_from` | The WorkGroup `excluding` was read from when it is not `name` (a rename waiting for a refresh); otherwise null, and always null for a set kept from a refresh that did not find its WorkGroup |

`workgroup preview` and `workgroup confirm` are the page's **Review
changes** and **Apply changes**, as `parish preview` and `parish
confirm` are for the parish profile. They need a full-scope session.
Give the new name with `--name`, as ParishSoft shows it (capital letters
and spaces at the ends do not matter), or `--clear` to name none, so
every eligible Family that has not responded gets the Reminders. A blank
`--name` is refused (exit 1, `invalid`): turning the exclusion off takes
`--clear`. Nothing changes until the change from `workgroup confirm` is
applied. Then `--clear` stops every exclusion at once, and going back to
the name the newest refresh read applies its Families at once. Any other
new name applies from the next ParishSoft refresh that reads it: until
then the Families the newest refresh found in the previous WorkGroup are
still excluded, `workgroup show` says `pending`, and `excluding` counts
them. A name ParishSoft does not have (`missing`) keeps the earlier set
until a refresh finds it. Run a refresh after renaming, so the new
WorkGroup applies before the next Reminder send.

```sh
VERSION=$(pk-admin workgroup show | jq -r .result.version)
pk-admin workgroup preview --name "Active: Stewardship 2027" \
    --expected-version "$VERSION" |
  jq -r .result.preview.token | pk-admin workgroup confirm --token -
```

| Field | What it holds |
| --- | --- |
| `version`, `campaign_id` (`workgroup preview`) | The version the change was reviewed against, and the current campaign |
| `before`, `after` | The WorkGroup now and the one proposed (null for none) |
| `preview.token` | What `workgroup confirm --token` takes, valid for fifteen minutes |
| `created`, `request` (`workgroup confirm`) | As `parish confirm` prints them; follow the change with `config request show --watch` |

The same name again, or a name the page refuses (too long), is exit 1
(`invalid`). A version that is no longer current, or an expired token,
is exit 1 (`stale_version`): read `workgroup show` again. An altered
token is exit 1 (`invalid`). With no current campaign, each command is
exit 1 (`not_available`). `workgroup confirm` records
`admin_cmd_workgroup_confirm` in System logs with the request; repeating
it with the same token returns the same request.

## Campaign settings

`campaign show` reads the current campaign's **Campaign settings**: each
setting the page's form has, and which of them can change now. It needs
an Administrator; a session of any scope may run it, and it records
nothing in System logs, as opening the page records nothing.

```sh
pk-admin campaign show
```

| Field | What it holds |
| --- | --- |
| `version` | The configuration version; `campaign preview --expected-version` takes it |
| `campaign_id`, `state` | The current campaign and its state (`draft`, `scheduled`, `active`, ...) |
| `editable` | Whether every setting can change: a Testing-mode draft that has never been live, with no background work running |
| `open` | The settings that can change now. On a live campaign, the end date (while it may still move), the Ministry leader roles and the Ministries, while no background work holds them |
| `settings` | Each setting by the page's form field name: `name`, `year_label`, `timezone`, `start_date`, `end_date`, the module boxes `census`, `ministry` and `financial_enabled`, `ministry_duids`, `ministry_leader_roles`, the financial periods (`financial_start`, `financial_end`, `comparison_start`, `comparison_end`, `fund_duids`, `comparison_fund_duids`, `overlap_confirmed`) and `additional_information` |

`campaign preview` and `campaign confirm` are the page's **Review
changes** and **Apply changes**, as `parish preview` and `parish
confirm` are for the parish profile. They need a full-scope session.
`--changes` is a JSON document shaped like `settings`, with only the
settings to change; every other setting keeps its saved value. Check
boxes are `true` or `false`, the Ministry, fund and leader role lists
JSON arrays (Ministries by the DUID `ministries list` prints, funds by
their ParishSoft DUID), dates `YYYY-MM-DD`, and `year_label` may be null. Nothing
changes until `campaign confirm`.

```sh
VERSION=$(pk-admin campaign show | jq -r .result.version)
pk-admin campaign preview --expected-version "$VERSION" \
    --changes '{"campaign": {"ministry_leader_roles": ["Chairperson"]}}' |
  jq -r .result.preview.token | pk-admin campaign confirm --token -
```

On a live campaign the page reviews a Ministries change on its own: give
`ministry_duids` alone. A live end date is reviewed as Dates and mail
schedules reviews it, so `schedule preview` can change it too. On a
draft that has mail schedules, change its dates with `schedule preview`,
which moves the mailings with them; `campaign preview` refuses a dates
change there, as the page sends it to Dates and mail schedules.

| Field | What it holds |
| --- | --- |
| `version`, `campaign_id` (`campaign preview`) | The version the change was reviewed against, and the current campaign |
| `kind` | Which review it is: `settings`, `end_date` (a live campaign's end date) or `ministries` (a live campaign's Ministries) |
| `changes` | Each stored setting that changes: `field`, `before` and `after`. These are the stored names, not the document's: the module boxes change `modules`, and the financial periods change `financial` |
| `replanned` | For an end date, the ids of the saved schedules it re-plans |
| `ministries` | For Ministries, those to add and to remove (each with its `duid`, `name` and counts of what it touches) and `open_forms`, the Families with a form open now; otherwise null |
| `notes` | The page's notes, such as what a later end date means for Families |
| `preview.token` | What `campaign confirm --token` takes, valid for fifteen minutes |
| `created`, `request` (`campaign confirm`) | As `parish confirm` prints them; follow the change with `config request show --watch` |

No change, a value the page refuses, or Ministries given with another
change is exit 1 (`invalid`), naming the setting where the page does
(`campaign.<field>`). A setting that cannot change now, a version that is
no longer current, or an expired token is exit 1 (`stale_version`): read
`campaign show` again. An altered token is exit 1 (`invalid`). With no
current campaign, each command is exit 1 (`not_available`). `campaign
confirm` records `admin_cmd_campaign_confirm` in System logs with the
request, plus what the page records: `campaign_end_date_requested` for an
end date, `campaign_ministries_requested` for Ministries. Repeating it
with the same token returns the same request.

## Parish, Ministry, logo and Share option settings

`parish show`, `ministries list`, `branding show` and `campaign shares`
are the reads of **Parish settings**, **Ministries**, **Parish logos**
and **Share options**. Like the pages, they need an Administrator; a
session of any scope may run them, and they record nothing in System
logs, as opening the pages records nothing. Each prints `version`, the
applied configuration's version. Changing the parish profile is
`parish preview` and `parish confirm`, and changing Share options is
`campaign shares-preview` and `campaign shares-confirm` (both below);
uploading logos stays on the page for now.

```sh
pk-admin parish show
pk-admin ministries list
pk-admin branding show
pk-admin campaign shares [--campaign ID]
```

| Field | What it holds |
| --- | --- |
| `parish` (`parish show`) | The profile the page's editor shows: `name`, `website`, `timezone`, `phone` (stored as `+1` and ten digits), `online_giving_url` (null when unset) and `date_format` |
| `campaign_id` (`ministries list`) | The current campaign, or null without one |
| `snapshot_id` | The ParishSoft data the Ministry catalog comes from |
| `ministries` | Every Ministry, by name as on the page: its ParishSoft `duid`, `name` and `included` (in the current campaign) |
| `logos` | The current logos the page shows, in the order `large`, `menu`, `icon`, `favicon`: `slot`, `id`, `width` and `height` (pixels), `size` (bytes) and `sha256` |
| `campaign_id` (`campaign shares`) | The campaign, the current one unless `--campaign` names another |
| `editable` | Whether the page would edit the options now: an unlocked current Testing draft that no background work holds, with the financial module |
| `financial` | Whether the campaign has the financial module |
| `options` | Its Share options in order: `id`, `name` (the option's label) and `free_text` |

`ministries list` prints every Ministry, not one page of the table;
filter it with `jq`. Without ParishSoft data for the configured
organization, the page cannot show a catalog and `ministries list` is
exit 3 (`unavailable`). Unlike the Share options page, which only edits
and refuses a locked campaign, `campaign shares` reads a live or ended
campaign's options too, with `editable` false. With no current campaign,
or an unknown one, `campaign shares` is exit 1 (`not_available`). Logo
images stay on the page.

`parish preview` and `parish confirm` are the Parish settings page's
**Review** and **Apply**, as `schedule preview` and `schedule confirm` are
for schedules. They need a full-scope session. The change document has
the shape of `parish show`'s `parish`, with only the fields to change; the
others keep their values, and `"online_giving_url": null` removes the
giving link. No other field may be null, and `date_format` must name a
style. Type a telephone any common way; it is stored as `+1` and
ten digits.

```sh
VERSION=$(pk-admin parish show | jq -r .result.version)
pk-admin parish preview --expected-version "$VERSION" \
  --changes '{"parish": {"phone": "(212) 555-1234"}}'
pk-admin parish confirm --token TOKEN
```

| Field | What it holds |
| --- | --- |
| `version` (`parish preview`) | The configuration version the change was reviewed against |
| `changes` | Each changed `field`, with its stored value `before` and `after` |
| `notes` | The page's review notes, such as that a timezone change does not move existing campaigns |
| `preview.token` | What `parish confirm --token` takes, valid for fifteen minutes |
| `created`, `request` (`parish confirm`) | As `schedule confirm` prints them; follow the change with `config request show --watch` |

A field the page refuses, a document that changes nothing, or an unknown
field is exit 1 (`invalid`), with each problem in `error.fields`
(`parish.phone`, or `parish` for the change as a whole). A version that is
no longer current, or an expired token, is exit 1 (`stale_version`): read
`parish show` again. `parish confirm` records `admin_cmd_parish_confirm`
in System logs with the request; repeating it with the same token returns
the same request.

`campaign shares-preview` and `campaign shares-confirm` are the Share
options page's **Review** and **Apply**. They need a full-scope session
and, like the page, change only an unlocked current Testing draft with the
financial module (`campaign shares` prints `editable`). `--changes` lists
the whole new list in `options`, in order: a saved option by its `id` with
its `name` (the label) and `free_text` (`false` when left out), and a new
option without an `id`. Each saved option the change removes goes in
`delete`, as the page's Delete box. Every saved option must be in one of
the two, so leaving one out by mistake is refused rather than deleting it.
Both commands take `--campaign`, the current campaign unless it names
another.

```sh
pk-admin campaign shares > shares.json
VERSION=$(jq -r .result.version shares.json)
jq '{options: [.result.options[] | .name |= sub("check"; "cheque")]}' \
    shares.json > change.json
pk-admin campaign shares-preview --expected-version "$VERSION" \
    --changes - < change.json |
  jq -r .result.preview.token | pk-admin campaign shares-confirm --token -
```

| Field | What it holds |
| --- | --- |
| `version`, `campaign_id` (`campaign shares-preview`) | The version the change was reviewed against, and the campaign |
| `changes` | The page's review, one line per change: `label` (such as `Added`, `Removed`, `Label` or `Position of …`), `before` (null for an added or removed option) and `after` |
| `options` | The list the change saves, in order: `id`, `name` and `free_text`; a new option's `id` is the one it will have |
| `preview.token` | What `campaign shares-confirm --token` takes, valid for fifteen minutes |
| `created`, `request` (`campaign shares-confirm`) | As `parish confirm` prints them; follow the change with `config request show --watch` |

A label the page refuses is exit 1 (`invalid`) at `options.<n>.name`, `n`
counting from 0 in your `options`. No change, an `id` the campaign does
not have or gives twice, or a saved option in neither list is exit 1
(`invalid`) at `options`. A campaign the page does not edit now (live,
locked or without the financial module), a version that is no longer
current, or an expired token is exit 1 (`stale_version`): read `campaign
shares` again. An altered token, or a token confirmed against another or
an unknown campaign, is exit 1 (`invalid`). No current campaign, or an
unknown one for `campaign shares-preview`, is exit 1 (`not_available`).
`campaign shares-confirm` records `admin_cmd_campaign_shares_confirm` in System logs
with the request; repeating it with the same token returns the same
request.

## Integrations

`integration list`, `integration show` and `integration status` are the
reads of **Integrations** and each integration's settings page. Like the
pages, they need an Administrator; a session of any scope may run them, and
they record nothing in System logs, as opening the pages records nothing.
No key, key file or fingerprint is ever printed. `integration preview` and
`integration confirm` save settings and remove an integration (see
[Changing integration settings](#changing-integration-settings)), and
`integration backup-probe` and `integration dismiss` are the pages' Test
access and Dismiss (see
[Checking the backup folder and dismissing a key change](#checking-the-backup-folder-and-dismissing-a-key-change)),
and `integration key status` and the Finish switching commands follow a key
change and finish switching to it (see
[Following a key change and finishing the switch](#following-a-key-change-and-finishing-the-switch)),
and `integration schedule-preview` previews the ParishSoft refresh schedule
(see [Previewing the refresh schedule](#previewing-the-refresh-schedule)),
and `integration key replace` saves a new key (see
[Replacing a key](#replacing-a-key)), and the `integration backup-key`
commands replace the Backup encryption key (see
[Replacing the backup encryption key](#replacing-the-backup-encryption-key)).

```sh
pk-admin integration list
pk-admin integration show parishsoft
pk-admin integration status slack --watch 5
```

`integration list` prints the configuration's `version` and, for each
integration, its `target`, `name` and whether it is `configured`.

`integration show TARGET` takes `parishsoft`, `google_workspace` (Google
Workspace mail), `email` (Outgoing email addresses), `slack` or `backup`
(Off-site backups); the Backup encryption key has its own commands (see
[Replacing the backup encryption key](#replacing-the-backup-encryption-key)). Google
Workspace mail or Outgoing email addresses not set up is exit 1
(`not_available`), as the page finds nothing to show.

| Field | What it holds |
| --- | --- |
| `version` | The applied configuration |
| `target`, `name` | The integration and the page's name for it |
| `configured` | Whether it is set up; Slack and Off-site backups are optional |
| `removable` | Whether the page offers Remove (Slack and Off-site backups, once set up) |
| `settings` | The saved settings the page shows, such as the ParishSoft organization ID and refresh schedule, the mailbox and addresses, the Slack channel or the Drive folder link; empty when not set up |
| `refresh_schedule` | For ParishSoft, the refresh schedule as its editor shows it, in the shape `integration preview --refresh-schedule` takes (see [Changing the refresh schedule](#changing-the-refresh-schedule)); an older schedule is shown converted to rules; null otherwise |
| `key` | For ParishSoft, Google Workspace mail and Slack, the latest key change line: `state` (`pending`, `updated`, `unselected` when Finish switching is needed, or `failed`), `at`, `message` and `request_id`; null when the page shows none |
| `source` | For ParishSoft, the same refresh facts as `refresh status`'s `source`; null otherwise |
| `offsite_backup` | For Off-site backups, the newest copy: `state`, `message`, `at`, `last_copy_at` and `set_name`; null otherwise |
| `probe` | For Off-site backups, your latest Test access while the page still shows it: `state`, `message`, `at`, `folder_url`, `requested_at` and `saved_after`; null otherwise |

`integration status TARGET` is the status line the page follows while a key
change or a backup folder check runs, for `parishsoft`, `google_workspace`,
`slack` and `backup`: `target`, `pending`, and `key` (or `offsite_backup` and
`probe` for the backup), as `show` prints them. With `--watch SECONDS` it
repeats until nothing is pending.

### Changing integration settings

`integration preview` and `integration confirm` are a settings page's
**Save** (its review, then **Apply**) and its **Remove**. They need an
Administrator and a full-scope session. It is a privileged action, so
`integration confirm` asks you to type `yes` (or takes `--yes`) and leaves a notice on the Admin home
page.

```sh
pk-admin integration show email
pk-admin integration preview email --expected-version VERSION \
    --changes '{"settings": {"reply_to": "office@example.org"}}'
pk-admin integration confirm email --token TOKEN
pk-admin integration preview slack --expected-version VERSION --remove
```

`--changes` is a JSON document (or `-` for standard input) with a
`settings` object shaped like `integration show`'s: any of the page's
fields for that integration (`organization_id`; `delegated_email`;
`sender`, `sender_name`, `reply_to`; `channel_id`; `target`, the Drive
folder link). A field you leave out keeps its saved value; only
`sender_name` may be null or empty, which drops the From name so mail uses
the parish name. Keys are never accepted (they have their own steps), and
the ParishSoft refresh schedule is kept exactly as saved unless you give
`--refresh-schedule` (below). Slack, which its
first key sets up, can't be set up here: the preview is exit 1 (`invalid`
at `key`). `--remove` reviews removing Slack or Off-site backups; anything
else, or one not set up, is `not_available`.

The preview prints `version`, `target`, `name`, `removing`, `changes`
(each `field` with its saved `before` and `after`) and `preview.token`; it
changes nothing. The page's refusals are exit 1 (`invalid`) at
`settings.<name>`, or at `settings` when nothing changed; a configuration
changed since `integration show` is `stale_version`. `integration confirm`
takes the token (or one the page made: tokens belong to you, not to the
browser or the command line), prompts with the integration and the names
of the settings it changes (never their values), and records a configuration request, exactly
as the page's Apply does. It checks the Administrator role first, so a
session whose user is no longer an Administrator is exit 1 (`denied`)
before anything is looked up or prompted. Follow it with `config request show`; the change
applies when the request installs. System logs records
`admin_cmd_integration_confirm` with the request. A Slack change or removal
also alerts every Administrator by email and Slack, as other notification
setting changes do. Repeating the confirmation with the same token returns
the same request and records nothing new.

#### Changing the refresh schedule

`integration preview parishsoft --refresh-schedule DOCUMENT` (or `-` for
standard input) changes the ParishSoft refresh schedule, alone or with
`--changes`. Start from `integration show parishsoft`'s `refresh_schedule`,
edit it, and preview it:

```sh
pk-admin integration show parishsoft
pk-admin integration preview parishsoft --expected-version VERSION \
    --refresh-schedule - < schedule.json
pk-admin integration confirm parishsoft --token TOKEN
```

The document is YAML or JSON, the rules the page's Advanced mode shows:

```json
{
  "rules": [
    {"kind": "full", "at": "02:00"},
    {"kind": "full", "every": 120, "from": "08:00", "to": "18:00"}
  ],
  "skips": [{"from": "12:00", "to": "13:00"}],
  "skip_around_family_emails": true
}
```

| Member | What it holds |
| --- | --- |
| `rules` | Required. Each rule is `{"kind": "full", "at": "HH:MM"}`, or `{"kind": "full", "every": MINUTES, "from": "HH:MM", "to": "HH:MM"}` with `every` one of 15, 30, 60, 120, 180, 240, 360, 480 or 720. A `"quick"` rule is accepted only exactly as `integration show` printed it, so an older schedule's quick updates can be kept; new quick updates can't be added, as on the page |
| `skips` | Optional (none when left out). Each skip is `{"at": "HH:MM"}` or `{"from": "HH:MM", "to": "HH:MM"}` (`to` itself is not skipped) |
| `skip_around_family_emails` | Optional: `true` or `false` (not null), the page's "Skip refreshes around Family emails". Left out, it keeps its current value |

Times are the parish's wall-clock times, in the zone the schedule runs in
(the current campaign's, else the parish's), as on the page; the preview
names it. Give each time as exactly `HH:MM` text; in YAML, quote it
(`"12:30"`), since YAML reads an unquoted `12:30` as a number. A kind,
time or interval that is not one of these is refused in fixed words that
never repeat what you sent. The page's own editor checks the schedule,
so the rules are exactly the page's: times on the quarter hour (an older
schedule's off-quarter-hour times may be kept), refreshes at least 15
minutes apart, at least one full refresh a day, at most 64 rules and 16
skips. Each problem is exit 1 (`invalid`) at `refresh_schedule.rules[N]`,
`refresh_schedule.skips[N]` or `refresh_schedule`, with the page's words.
The schedule you were shown, posted back unchanged, is "No settings have
changed." The editor's mode (Basic or Advanced) is saved as the page would
save it with its toggle untouched.

The preview's `schedule` holds `timezone`, `before` (the schedule as the
page showed it), `after` and `differences`, the review page's list of
what changes in words; it is null when the schedule does not change. The
preview changes nothing. `integration confirm` prompts with the stored
setting names that change (such as `refresh_rules`) and records the
request as above; the new schedule applies when the request installs.

#### Previewing the refresh schedule

`integration schedule-preview` is the ParishSoft page's live check of its
schedule editor: the next seven days of refreshes, the cost and the
freshness line, without saving anything. Without an option it previews the
applied schedule. With `--refresh-schedule DOCUMENT` (or `-` for standard
input) it previews a proposed one, in the same document
`integration preview` takes:

```sh
pk-admin integration schedule-preview
pk-admin integration schedule-preview --refresh-schedule - < schedule.json
```

It needs an Administrator, in a session of any scope, and records nothing
in System logs, as the page's check records nothing. A document of another
shape, or one that adds a quick update, is exit 1 (`invalid`) as for
`integration preview`. The page's own problems are not refused: they are
listed, and the command exits 0, so you can fix them and check again.

| Field | What it holds |
| --- | --- |
| `version` | The applied configuration |
| `timezone` | The zone the schedule's times are in |
| `proposed` | Whether `--refresh-schedule` gave the schedule |
| `changed` | Whether it differs from the applied schedule |
| `schedule` | The checked schedule document; null while a row cannot be read |
| `problems` | Each problem's `field` (`refresh_schedule.rules[N]`, `refresh_schedule.skips[N]` or `refresh_schedule`) and `message`, the page's words |
| `blocking` | Whether the problems would stop this schedule from being saved; an unchanged schedule's never do |
| `status` | The line beside the page's Save |
| `preview` | The page's seven `days` (each `day`, `weekday`, `today`, `change` on a daylight-saving day, `counts`, and `times` with `text`, `struck`, `kind` and `due_at`), plus `line`, `summary`, `warning`, `freshness` and `estimated_windows`; null when there is nothing to preview |

### Checking the backup folder and dismissing a key change

`integration backup-probe` is the Off-site backups page's **Test access**:
it asks for a check that off-site copies can be saved in a Google Drive
folder, signing in as the Google Workspace mailbox user. Give a folder link
with `--folder` to check it before saving it, or leave it out to check the
saved folder. `integration dismiss` is a key change line's **Dismiss**: it
hides a finished change's line (`updated` or `failed`) for every
Administrator. Both need a full-scope session and an Administrator; neither
asks for a confirmation, as on the pages.

```sh
pk-admin integration backup-probe --folder https://drive.google.com/drive/folders/FOLDER
pk-admin integration status backup --watch 5
pk-admin integration show parishsoft
pk-admin integration dismiss parishsoft --request-id REQUEST_ID
```

`integration backup-probe` prints `pending` and `probe`, as `integration
show backup` prints it, and records `admin_cmd_integration_backup_probe` in
System logs. The check itself runs a moment later; follow it with
`integration status backup --watch SECONDS`. It is exit 1 (`invalid`) in
the local environment, at `google_workspace` when Google Workspace mail is
not set up, and at `folder` when no folder is given and none is saved or
the link is not a Google Drive folder, with the page's words. It is
`stale_version` when the Google Workspace mailbox changed meanwhile; run it
again.

`integration dismiss` takes `parishsoft`, `google_workspace` or `slack` and
the `key.request_id` that `integration show` or `integration status`
printed. It prints `target`, `request_id` and `dismissed`. When it dismisses
the line, System logs records `credential_result_dismissed`, as for the
page, and `admin_cmd_integration_dismiss`. A change still running, one that
needs Finish switching, or a request the page no longer shows is not
dismissed: `dismissed` is false, exit 0, and nothing is recorded, as the
page's Dismiss does nothing then.

### Replacing a key

`integration key replace TARGET` is a settings page's **Save** with a new
key pasted: an API key (`parishsoft`), the mail service-account key
(`google_workspace`) or the bot token (`slack`). It needs a full-scope
session and an Administrator, and
always asks before it does anything. The key is never an argument: the
wrapper reads it from a file or from standard input and sends it to the
command on its own line after the session preamble, so it never appears in
the process list, shell history, the command's output, System logs or the
audit trail, and never in the wrapper's work directory.

```sh
pk-admin integration show slack                # note the version
pk-admin integration key replace slack --expected-version VERSION \
    --secret-file ~/slack-token                # prompts at the terminal
pk-admin integration key replace slack --expected-version VERSION \
    --secret-stdin                             # paste, then Ctrl-D, then yes
```

- `--secret-file PATH` reads a regular file you own with no group or other
  permissions (`chmod 600`), at most 128 KiB. A symbolic link, a FIFO, a
  larger file or a file others may read is refused (exit 2) before
  anything runs. Delete the file once the change is done. Keep it in a
  directory only you can write to (your home directory, or one made with
  `mkdir -m 700`): the shell cannot refuse a symbolic link that someone
  swaps in at the instant between the wrapper's checks and its open. What
  it reads must still be your own private regular file, but a private
  directory leaves no such instant to anyone else.
- `--secret-stdin` at a terminal turns echo off, so the key is not shown,
  and reads until you press Ctrl-D at the start of a line (twice in the
  middle of one); a multi-line Google Workspace JSON key can be pasted
  whole. Echo comes back for the prompt's answer. If the command ends
  before it reads the key (Staff is `denied`, the session has ended, the
  web container is down), or what you paste is longer than 128 KiB,
  `pk-admin` says so, keeps echo off and waits: finish the paste and press
  Ctrl-D, and what you pasted is discarded, never left for your shell to
  run or keep in its history. A terminal
  takes at most 1024 bytes on one line on macOS (4096 on Linux), and a
  Google Workspace JSON key's `private_key` line is longer, so on a Mac
  give that key with `--secret-file`. From a pipe it needs
  `--yes`, because standard input then holds only the key, so read what
  `integration show` says first. Never pipe a key from `echo` or a
  variable: the value would stay in shell history or the process list.
  Piping from a password manager's command (`pass show slack | ...`) is
  fine.
- `--changes JSON` saves settings with the key, in `integration preview`'s
  shape (`{"settings": {"channel_id": "C0123ABC"}}`): for example the
  channel that sets Slack up with its first token, or a new ParishSoft
  organization ID before any data is loaded. It is literal JSON, never
  `-`, since standard input carries the key. A refresh schedule change is
  refused with a key, as on the page: change it first.

The prompt names the integration, whether this sets it up or replaces its
key, and the settings saved with it (never their values). Then the key is
sealed for the integration's own installer and staged exactly as from the
page; the installer checks it with the provider, installs it, and the
configuration switches to it. If the check fails, nothing changes and the
current key stays in use. The command prints `version`, `target`, `name`,
`setup`, `changes`, `key_request_id` and `request` (the configuration
request that switches to the key); follow the key change with
`integration key status TARGET --request-id KEY_REQUEST_ID --watch 5`.
It records the page's own events, the privileged command's event and notice,
`admin_cmd_integration_key_replace` and a `policy_change` notice for every
Administrator.

Refusals: Staff is exit 1 (`denied`) before the key is read; another
version is `stale_version` (run `integration show` again); a key change for
the integration still running is `not_available` (wait for it, or follow
it with `integration key status`); a refused setting is `invalid` at
`settings.NAME`, and an empty key, one that is not UTF-8, is too long or is
in the wrong format at `key`, never quoting it. Each run is a new key
change: if the outcome is unknown (exit 6), read the named request with
`config request show` and the key change with `integration key status`
before you try again.

### Replacing the backup encryption key

The `integration backup-key` commands are the **Backup encryption key**
page's three steps, in the same order and with the same checks as the
page and the backup runbook's "Replacing the key". They need a full-scope
session and an Administrator.
Make the new key pair on the key machine first (`pk-stewardship
backup-keygen`) and save only its `public_key` value in a file you own
with mode 600; never give this server the private key.

```sh
pk-admin integration backup-key challenge --public-key-file ~/new-backup.pub
# on the key machine: run backup-prove with the new private key and paste
# the challenge line; it prints a 12-character code
pk-admin integration backup-key preview --intent INTENT --code-stdin
                                    # type the code, then Ctrl-D (not shown)
pk-admin integration backup-key confirm --token TOKEN   # prompts
```

- `challenge` reads the public key from the file, as `--secret-file`
  reads a key (a private regular file, here at most 200 bytes), and sends
  it to the command on its own line, never as an argument. It refuses a
  private key, a text that is not one public key, and the key already in
  use, in the page's words. It prints `version`, `current` (the key in use
  as the page's "Key in use" shows it: `source`, `fingerprint` and, for
  the installed key, `backup_at`), `fingerprint` (the new key's: compare it
  with the one `backup-keygen` printed), `challenge` (the line to paste
  into `backup-prove`) and `intent`, which `preview` takes within fifteen
  minutes. It records nothing.
- `preview` takes the intent and the code `backup-prove` printed, from
  `--code-file PATH` or `--code-stdin` (echo off at a terminal; piped, it
  needs no `--yes`, as nothing prompts). The code is read as the page
  reads it: any case, spaces or dashes. A code that does not match is
  `invalid` at `code`; check that you used the new private key, and try
  again with the same intent. An expired intent, or one made before the
  settings changed, is `stale_version`: start again with `challenge`. It
  prints `version`, `changes` (the fingerprint `before`, null when the
  key in use is not known yet, and `after`) and `preview.token`. It
  records nothing.
- `confirm` prompts with both fingerprints and what the change means,
  then makes the page's configuration request; `--yes` answers the
  prompt, and `--token -` needs it. It records the privileged command's
  event and notice, `admin_cmd_integration_backup_key_confirm` and a `policy_change`
  notice. Once the configuration installer applies it, every
  Administrator is told by the page's `backup_key_replaced` security
  event, and the next backup is sealed to the new key. It prints
  `created` and `request`; follow the request with
  `config request show REQUEST_ID --watch 5`. Keep the old private key
  until every backup sealed to it has been deleted by retention, and open
  the next backup with the new private key, as the restore drill does (see
  the backup runbook's
  [Replacing the key](stewardship-backup-runbook.md#replacing-the-key)).

Staff is exit 1 (`denied`) before the public key or the code is read, and
a read-only session is refused. No private key, code or pasted text is ever
printed or recorded.

### Following a key change and finishing the switch

A new key (API key, service-account key or bot token) is pasted on the
integration's settings page or given to `integration key replace` (see
[Replacing a key](#replacing-a-key)). `integration key status` is the **Key
replacement status** page for one of your own key changes: by default your
latest for the integration, or the one `--request-id` names. Any session of
an Administrator may run it, and it records nothing. It prints `target`,
`request_id`, `state`, `pending` and `selectable`, which is true when the
page offers **Finish switching to the new key**. Another Administrator's
key change, or one of another integration, is exit 1 (`not_available`).
Without `--request-id`, a watch keeps following the key change it found
first. A watch ends once the key is installed and confirmed, as the page's
progress does, even if the automatic switch to it is still queued; run
`integration status TARGET` to see whether the switch finished.

```sh
pk-admin integration key status parishsoft --watch 5
pk-admin integration key finish-switching-preview parishsoft
pk-admin integration key finish-switching-preview parishsoft \
    | jq -r .result.preview.token \
    | pk-admin integration key finish-switching parishsoft --token - --yes
```

The first preview is the review to read; the pipeline takes a fresh
token straight through, so no file holds a usable token until it expires.
The pipeline confirms that fresh preview, not the one you read: if the key
change or the settings may have changed in between, read the review again.
To confirm exactly the token you read, keep the review in a shell variable
instead of a file:

```sh
review="$(pk-admin integration key finish-switching-preview parishsoft)"
printf '%s\n' "$review"
printf '%s' "$review" | jq -r .result.preview.token \
    | pk-admin integration key finish-switching parishsoft --token - --yes
```

When the new key is installed but switching to it did not finish (the
settings page's key change line is `unselected`), the integration is
stopped until an Administrator finishes the switch. `integration key
finish-switching-preview` is that page's review: it needs a full-scope
session and an Administrator and prints `version`, `target`, `name`,
`request_id`, `changes` (the settings saved with the key that the switch
also applies, each `field`, `before` and `after`) and `preview.token`. It
changes nothing. When nothing waits to be switched to it is exit 1
(`not_available`); when the key was saved with a different ParishSoft
organization ID than the loaded data's it is exit 1 (`invalid`) at
`organization_id`, with the page's words. `integration key
finish-switching` takes that token, preferably on standard input
(`--token -`, as above) so it stays out of the argument list and shell
history, or one the page signed for you. Standard input then carries the
token, so `--yes` stands in for the prompt: read the review first. The
token is the page's own signed (not encrypted) binding: like the page's
hidden form field, it includes the new key's fingerprint, though no output
field prints one. Given on the command line, the token is confirmed after
a prompt naming the integration and the settings it also changes. Either
way the command records the configuration request as the page's button
does, with `admin_cmd_integration_key_finish_switching`, the
privileged command's event and notice and a `policy_change` notice for every
Administrator. A token for an earlier key change is exit 1 (`invalid`),
including a repeat of an applied token after a newer key change started;
an expired one, or one whose key is no longer current, is `stale_version`:
review again. If the loaded ParishSoft organization changed meanwhile, the
confirmation is exit 1 (`invalid`) at `organization_id`, where the page
shows its refusal page. Neither command prints a key. Like `integration
confirm`, `integration key finish-switching` checks the Administrator role
first: a session whose user is no longer an Administrator is exit 1
(`denied`) before any lookup or prompt.

## Past campaigns

The `history` commands import the archive workbooks of past campaigns for
the campaign comparison report
([campaign history](../specs/stewardship/campaign-history/spec.md#command-line),
[#1018](https://github.com/epiphany40223/parishkit/issues/1018)). They are
the **Past campaigns** page's actions (Parish data, Past campaigns), need an
Administrator, and print counts, totals, dates, states and checksums only:
never a Family row, a note's text, a pledge band or quartile, a
pledging-Family count or an average. Check files offline first with
`pk-stewardship archive-check`, which needs no session.

```sh
pk-admin history list
pk-admin history preview - < stewardship-2025.xlsx
pk-admin history confirm --token TOKEN
pk-admin history remove 2025 --expected-version 2
```

- `history list` (any session) lists each imported year and each removal's
  tombstone, newest year first, with its `version`, `checksum` (SHA-256 of
  the file), `state` (`active` or `removed`), Families responded, annual
  pledges and whether Family rows were kept. It records nothing.
- `history preview -` (full scope) reads one workbook from standard input
  (the wrapper forwards it because of the `-`), checks it in the isolated
  helper and keeps it as a preview for fifteen minutes. It prints `action`
  (`add`, `replace` with the year's `current` import, or `already` when the
  same file is imported), the figures, `notes_flagged` (the notes to read
  on the page before applying; their text is never printed) and
  `preview.token`. A refused file is exit 1 (`invalid`); `error.fields`
  names each problem's cell and rule (never the cell's value).
  `--no-family-rows` keeps no Family ids, so the renewal figure is not
  available for that year. Another archive being checked is exit 3
  (`unavailable`); retry in a minute. Text values and notes may hold tabs
  and line breaks but no other control character: a carriage return (a
  Windows line ending pasted into a note) is refused as a `control`
  problem at its cell, so replace those line endings before importing.
- `history confirm --token` (full scope) imports the preview. A
  replacement removes the year's earlier import, so it asks you to type
  `yes` (or pass `--yes`); an addition does not ask. It prints `outcome`
  (`imported`, `replaced` or `already`). `already` means the same file is
  imported (`archive` is that import): nothing changes, but the preview is
  used up and the command records `admin_cmd_history_confirm`. An expired,
  applied or racing preview is exit 1 (`stale_version`): preview again.
- `history remove YEAR --expected-version N` (full scope) removes a year's
  import after the prompt, keeping a tombstone; the file can be imported
  again. A wrong version is exit 1 (`stale_version`).

System logs show `campaign_archive_previewed`, `campaign_archive_imported`,
`campaign_archive_replaced` and `campaign_archive_removed` (year, checksum
and counts), and the commands' `admin_cmd_history_preview`,
`admin_cmd_history_confirm` and `admin_cmd_history_remove`.

## Task retries

`task retry` is the **Retry** button of a failed task on Background work,
for the four kinds of work that page can retry: Family mail preparation,
daily and weekly digest work, and export file cleanup. It needs a
full-scope session, and asks for no confirmation, as the page asks for
none. Fix the cause first, as the page says.

```sh
pk-admin task list --state failed
pk-admin task retry TASK_ID --request-key "$(uuidgen | tr A-Z a-z)"
pk-admin task show NEW_TASK_ID --watch 10
```

Pass your own `--request-key` (a UUID). Without one, the command makes one
and writes it to standard error (`pk-admin: request key ...`) before it
acts. Repeating the command with the same key returns the same retry and
changes nothing, and a key used on the page works here, and the other way
round.

| `task retry` field | What it holds |
| --- | --- |
| `created` | False when this key retried the task before: the original retry is returned and nothing changes |
| `request_key` | The key, yours or the one made for you |
| `task` | The retry: `id` (follow it with `task show`), `root_id`, `parent_id` (the failed task), `retry_sequence`, `type` and `state` |

A task the page offers no retry for, or an unknown task, is exit 1
(`not_available`). A task that is not the latest failed run of its chain
(it was retried already, or it has not failed) is exit 1 (`stale_version`):
read it again with `task show`, whose `latest_run_id` names the newest
retry. A key belongs to a task's chain of retries: one another
Administrator used for this task is exit 1 (`invalid`), and one used for
another run of the chain, by anyone, is exit 1 (`stale_version`; `invalid`
for an export cleanup). A key used only for another task is a new key here.
Exit 6 (`outcome_unknown`) names the key in
`error.request_id`: repeat the command with it, or look for the retry with
`task show`. System logs show each retry as `admin_cmd_task_retry`,
attributed to the approving Administrator with the automation session as
its subject; it creates no automation notice.

## Confirmations

Every command for a privileged (fresh-gated) action, and every command whose page asks you to type a value or tick an
acknowledgement, asks at a prompt before it acts, for example `test sample`
while an earlier test's outcome is unknown, `delivery resend`, `delivery
refusal-clear` and `delivery bounce-report`; the catalog's `prompts` flag
names every one. The command writes what it is about to do to standard error
and waits for `yes`, or for the page's typed value (`Production` for the
Production confirmation). Anything else, or the end of input, changes
nothing and exits 4 (`confirmation_required`). The wrapper passes one line
typed at your terminal to a prompting command, so run it from a terminal, or
pass `--yes` in a script; `--yes` is required when the command also reads an
input from standard input (`-`). Logs record whether the answer came from
the prompt or from `--yes`.

A prompting command checks your role first: if your user no longer may do
the action (an Administrator who is now Staff, say), it exits 1 (`denied`)
before it reads any input, looks anything up or prompts. A command Staff
may run, such as `export financial` or `census resolve`, still prompts
Staff.

Since [#1547](https://github.com/epiphany40223/parishkit/issues/1547) no
Admin page asks for a recent sign-in, so a session never stands in for one.
The privileged actions that once needed one keep the catalog's
`fresh_gated` flag. When a session runs one (pausing or resuming mail, a
chosen-Family test, an export of codes or financial data, the Family portal
switch, confirming Production, withdrawal, key changes), System logs record an
`automation_fresh_gate` event beside the action, and the dashboard shows an
automation notice; confirming Production and withdrawal are marked
irreversible and also email and post to Slack.

## Sent mail

The delivery commands read Sent mail and Refused addresses as their
pages do, and resolve a delivery as its page's form does. Each read records
the pages' view event in System logs; any session may run them.
`delivery resolve` needs a full-scope session. No document names a
recipient: there is no email address, Family DUID or Family id, and a
resolution note's text stays on the page. To see who a delivery was for,
open it on the page.

```sh
pk-admin delivery list --state delivery_unknown
pk-admin delivery list --recipient 1234
pk-admin delivery list --state needs_attention --purpose daily_digest --from 2026-10-01
pk-admin delivery show MESSAGE_ID
pk-admin delivery resolve MESSAGE_ID --action accept --expected-version 3 --note - < note.txt
pk-admin delivery refusals
pk-admin delivery refusal-show REFUSAL_ID
pk-admin delivery resend MESSAGE_ID --expected-version 3 --note - --yes < note.txt
pk-admin delivery refusal-clear REFUSAL_ID --source-snapshot-id SNAPSHOT_ID \
    --source-generation 2 --note - --yes < note.txt
pk-admin delivery bounce-report MESSAGE_ID --address ADDRESS \
    --note - --yes < note.txt
```

`delivery list` takes Sent mail's filters: `--recipient` (a Family DUID, an
email address or a delivery id, as input only), `--purpose` (`initial`,
`reminder`, `receipt`, `family_test`, `daily_digest`, `weekly_digest`,
`operational` or `security_event`), `--state` (a state, or the groups
`needs_attention`, `not_delivered` and `still_sending`, as the page
defines them), `--mode`
(`production` or `testing`; both when left out), `--from` and `--to` (whole
days, in the time zone `--zone` names, the parish's time zone by default),
`--send` (send history's `send` value), `--page`, `--size` and `--sort`. The
list includes operational and security notices, as the page does. `--sort` takes the page's tokens:
`changed` (when the email last changed; the default is `-changed`, latest
first), `name` (emails to Administrators last, by their Name or address),
`duid`, `purpose`, `mode`, `state` and `attempts`, and also
`created`. `delivery refusals` takes `--duid`, and its `--sort` takes
`refused`, `name` (the Family's name, as `delivery list`), `duid`
(the default), `address` and `id`.

| Field | What it holds |
| --- | --- |
| `deliveries`, `delivery` | Each message's `id`, `campaign_id`, `purpose`, `mode`, `state`, `version`, `attempt`, `task_id`, `created_at`, `updated_at`, `finished_at` |
| `refused` | `delivery list`, in each of `deliveries`: how many of the email's refused addresses are still unresolved, as Sent mail marks it (0 for none) |
| `state`, `purpose`, `mode`, `start`, `end`, `send`, `page`, `size`, `sort`, `has_next`, `matching`, `matching_capped` | `delivery list`: the filters (never the recipient) and the page of rows |
| `task` | `delivery show`: the latest task's `id`, `state`, `version` and `retry_sequence` |
| `actions`, `retry_unavailable` | `delivery show`: the resolutions the page offers now, and whether a retry is refused by the campaign's resend rule |
| `events` | `delivery show`: the history's `version`, `at`, `state`, `action`, `attempt` and `result` (a stored code) |
| `notes` | `delivery show`: each resolution's `created_at` and `action` |
| `refusals` | `delivery refusals`: each unresolved refusal's `id`, `kind` and `created_at` |
| `id`, `kind`, `created_at`, `resolved`, `source`, `can_clear` | `delivery refusal-show`: the refusal (its `kind` is `provider` when the provider refused the address, `reported_bounce` when an Administrator reported a bounce; a report's note stays on the page), how and when it was resolved (`kind`, `at`), the source version to verify (`snapshot_id`, `generation`), and whether a clearance can be recorded now |

`delivery resolve` takes `--action` (`note`, `accept`, `confirm_unsent`,
`retry_failed` or `retry_unsent`, as offered in `actions`; an offered
`resend` is `delivery resend`), `--expected-version` (the delivery's
`version`), `--note` (or `-`) and
`--request-key`, made for you and written to standard error when left out.
Give evidence that names people as `--note -` from a file or standard
input: an inline `--note` is visible in shell history and the process
list.
Repeating it with the same key returns the same resolution (`created`
false); the page's form with that key does too. A delivery that changed,
or a change that collided with another, is exit 1 (`stale_version`); a key
used for another resolution is exit 1 (`invalid`). Retrying a Family email
loads the web's Family keys for that one command; if they differ from the
running web's (a key rotation in progress), it is exit 2
(`credential_mismatch`): retry after the web service is recreated. While a
key rotation is being applied it is exit 3 (`unavailable`): retry shortly.
A retry never changes a Family's code or link. The result has `created`,
`request_key` and `resolution` (`id`, `message_id`, `action`,
`expected_version`, `previous_task_id`, `retry_task_id`, `created_at`).
System logs show it as `admin_cmd_delivery_resolve`.

The duplicate-risk **Resend** and clearing a refused address ask you to
tick an acknowledgement on the page, so their commands show the page's
words and ask for `yes` (see [Confirmations](#confirmations)). Both need a
full-scope session, a `--note` (or `-`, which needs `--yes`) and take
`--request-key` as `delivery resolve` does; the page's form with the same
key returns the same result.

`delivery resend` takes `--expected-version` and resends a delivery whose
outcome is unknown (`resend` in `delivery show`'s `actions`). The prior
attempt may already have arrived, so the Family may get the email twice.
Its result is `delivery resolve`'s, its errors are too (including the
Family key exits 2 and 3), and System logs show it as
`admin_cmd_delivery_resend`.

`delivery refusal-clear` takes the `source` `snapshot_id` and `generation`
from `delivery refusal-show`; clear an address only after you verified it
with the Family. A refusal already cleared, or a source that moved on, is
exit 1 (`stale_version`): read it again. The result has `created`,
`request_key` and `resolution` (`id`, `refusal_id`, `source_snapshot_id`,
`source_generation`, `created_at`), never the note. System logs show it as
`admin_cmd_delivery_refusal_clear`.

`delivery bounce-report` is the Mail message page's **Report a bounced
address**: when a bounce message for a delivered Family email arrives in the
sending mailbox, it sets that address aside for the Family, as a provider's
refusal does, until `delivery refusal-clear` clears it. It shows the page's
words ("I saw a bounce message for this address from this email") and asks
for `yes`; it needs a full-scope session of an Administrator, as on the
page. When the address is the Family's only one left, the
prompt also warns, as the page's notice does, that the Family will then get
no campaign email until the address is cleared or ParishSoft is corrected;
`--yes` skips that warning too. `--address` is the address the bounce
message names (spaces and capitals are ignored); no command lists an email's
addresses, so take it from the bounce message, or see the email on its page.
`--address` takes the address itself: `-` is not read from standard input.
`--note` (or `-`) records what the bounce message said. An email that is not
a delivered Production Family email, an address it was not sent to, or one
already set aside is exit 1 (`stale_version`); an unknown email is exit 1
(`not_available`); a key used for another report is exit 1 (`invalid`). The
result has `created`, `request_key` and `refusal` (`id`, which is the
request key, `message_id`, `kind` and `created_at`), never the address or
the note; follow it with `delivery refusal-show`. System logs show it as
`recipient_bounce_reported` and `admin_cmd_delivery_bounce_report`.

## System logs

`logs list` reads the System logs page and `logs export` its download, with
the page's filters. Each records the page's event in System logs
(`system_logs_viewed` or `system_logs_exported`, with the number of
entries). Any session may run `logs list`; `logs export`, like every
export, needs a full-scope session.

```sh
pk-admin logs list
pk-admin logs list --correlation CORRELATION_ID
pk-admin logs list --show error --show critical --start 2026-10-01 --zone America/New_York
pk-admin logs list --through THROUGH --page 2
pk-admin logs list --activity sign_in --show audit
pk-admin logs list --activity parishsoft
pk-admin logs list --activity security --show audit
(umask 077; pk-admin logs export --show audit --format jsonl > logs.jsonl)
```

The filters are the page's: `--show` (`debug`, `info`, `warning`, `error`,
`critical` or `audit`, repeated for more; without it, every level but debug
plus audit records, as the page first shows), `--activity` (the page's
Activity choice: `family_mail` lists outgoing email steps, resolutions,
pauses, tests and mail provider failures, and `parishsoft` ParishSoft
refreshes, quick updates, failures, holds and Ministry leader changes,
both with operational entries too; the rest are audit records only, so keep
`audit` among the `--show` kinds: `sign_in` lists every Admin portal sign-in
event, `configuration` every settings, schedule, user, file and key change,
`downloads` report and export requests and downloads, `security` widened
access, automation sessions, restored access and encryption key changes,
and `family_portal`
Family sign-ins, responses, links and maintenance), `--event`
(one type, exactly), `--actor`, `--correlation`, `--campaign` and
`--subject` (UUIDs; a campaign or subject keeps only audit records),
`--text` (the page's search phrase,
without an email address), `--ministry` (one Ministry's DUID), and
`--start` and `--end` (days), which need `--zone`, the time zone the days
fall in. Search text stays in your shell's history, so use `--text` only
when the other filters cannot find what you need. To follow one command's
effects, filter on the `correlation_id` its document printed. A filter the
page would refuse is exit 1 (`invalid`). Each read has the page's time
limit: a read stopped by it is exit 3 (`unavailable`), recorded in System
logs as a timeout; narrow the filters and try again.

`logs list` also takes `--page`, `--size` (25, 50, 100 or 250), `--sort`
(`newest` or `oldest`) and `--through`: give back the `through` the first
page printed to read the next pages of the same snapshot, so entries
written meanwhile do not shift them.

| Field | What it holds |
| --- | --- |
| `through` | The snapshot the page read, for `--through` |
| `page`, `pages`, `size`, `sort`, `has_next` | The page of entries |
| `matching`, `matching_capped`, `depth_limited` | How many entries match (at most 10,000, the paging depth), whether more did, and whether the page asked for lay past that depth |
| `entries` | Each entry's `id`, `source` (`operational` or `audit`), `created_at`, `level` (operational only), `type`, `actor_id`, `actor_kind` (audit only), `correlation_id`, `campaign_id`, `subject_id` and `details`, the recorded fields under their stored names |

Entries name people by identifier only: the actor's email address, which
the page shows, and a Family's or member's DUID in `details` are left out.
Open the page, or export, to see them.

`logs export` writes the page's download, the newest 10,000 matching
entries at most, to standard output, as the page's file (CSV and JSON Lines
byte for byte). It takes the filters above, `--format` (`xlsx`, the
default, `csv` or `jsonl`; CSV and XLSX have the same columns, under plain
headings such as `Correlation identifier`, while JSON Lines keeps keys such
as `correlation_id`) and
`--timezone` (UTC, the default, or a time zone name, for the file's times).
Redirect standard output to a file: the wrapper refuses to write the file
to a terminal.

The file has the same contents as the page's download, actor email
addresses and Family DUIDs included. A redirected file gets your umask's
permissions, so create it private, as in the example above (`umask 077` in
a subshell), or in a directory only you can read, and delete it once used.

The JSON document goes to standard error, as its **last** line: warnings
and structured logs come before it. The wrapper shows standard error as it
arrives, so an interrupted run still shows what came before the
interruption, but the document itself is written only when the command
ends. Check the exit status first: anything
but 0 means the file is incomplete or empty, so discard it. On success, the
document gives `file_name` (the page's), `content_type`, `format`,
`timezone`, `size` and `sha256` of the bytes written, `count` and `limit`;
compare `size` and `sha256` with the file (`sha256sum logs.jsonl`). The page
keeps no record of a download, so neither does the command: there is
nothing for `export fetch` to fetch.

## Reports

The `report` commands read the campaign report pages and print their
counts and totals. They never print a Family's or Member's name, DUID
(except `report source-form`, below), contact details or answers; for
those rows, use the page or an export. A
session of any scope (read-only included) may run them, and each records the same "viewed" events in System
logs as opening the page does. Each reads the current campaign, as the
report pages do, and prints its `campaign_id`.

```sh
pk-admin report list
pk-admin report participation
pk-admin report comparison --day 12
pk-admin report responses --mode testing --grain day
pk-admin report financial
pk-admin report limitations
pk-admin report census --status all --route by_hand
pk-admin report information --disposition all --needed yes
pk-admin report ministry
pk-admin report ministry --ministry 9 --requests join --state unresolved
pk-admin report source-form
pk-admin report data-to-fix
pk-admin report roster --show all --ministry 9
```

| Command | The page | What it prints |
| --- | --- | --- |
| `report list` | Campaign reports | `campaign_id` (null without a current campaign) and `reports`: each report your menu offers, with `command`, `page` and `available` (false while the menu greys it out) |
| `report participation` | Response dashboard (statistics and daily table) | `status` (`current`, `updating` or `unavailable`), `fact_set_id` (for `export create --fact-set`), the figures' source and dates, `statistics` (the active Families, Members, email, responses and, with Financial stewardship, pledge totals, including the comparison pledges of every Family, `comparison_pledge_all`) and `days`, the daily table |
| `report comparison` | Campaign comparison | `day` and `date` (the comparison day: the current campaign's last complete day, or `--day`), `status`, `last_year`, `current` (the current campaign's figures at the day and so far, and its renewal of last year's Families), `years` (each imported year's figures at the day and final figures, renewal and, for a final year, its pledge distribution) and `averages` (the imported years' mean of each figure at the day); see below |
| `report responses` | Response dashboard (Families by stage) | `metrics`: the funnel `stages`, the three figures beside it, the `activity` buckets, the `sends` and each list's length (`lists`), which the page no longer shows; null in Testing without a rehearsal |
| `report financial` | Financial report | The summary over every pledge: `families`, `annual_total`, `frequencies`, `shares` (by share option id), `no_share`, `cannot_give` and the comparison period |
| `report limitations` | Limitations | `cannot_serve` (Members who cannot participate in ministries) and `cannot_attend` (Families who cannot attend Mass or prayer services) |
| `report census` | Census changes | `matching`: how many changes the filters keep, split by `routes` (`automatic`, `by_hand`) and `kinds` (`contact`, `moved`, `deceased`, `new_member`), and `statuses`, the page's summary of every change by status; see below |
| `report information` | Additional information | `matching`: how many items match the filters |
| `report ministry` | Ministry report | `summaries`: each Ministry's DUID (`ministry`), name, state and request counts; with `--ministry` and `--requests`, `matching` is that list's request count |

The options are the pages' own, and a value the page would refuse is exit 1
(`invalid`):

- `report participation`: `--scope` (`historical`, the default, or
  `current`).
- `report comparison`: `--day` (a campaign day from 1 to the comparison
  day; a later day is `not_available`).
- `report responses`: `--mode` (`production`, the default, or `testing`)
  and `--grain` (`auto`, `hour` or `day`).
- `report census`: `--status` (`open`, the default: the work to do,
  which for an Administrator includes the automatic changes to review;
  `to_review`, `to_do`, `conflict`, `being_published`, `published`,
  `already`, `entered`, `ignored` or `all`), `--history` (count superseded
  and cancelled changes too), `--route` (`any`, `automatic` or `by_hand`),
  `--kind` (`any`, `contact`, `moved`, `deceased` or `new_member`) and
  `--start`, `--end` and `--zone`, as `report information` takes them.
- `report information`: `--disposition` (`current_actionable`, the
  default, `superseded`, `withdrawn` or `all`), `--needed` and
  `--completed` (`any`, `yes` or `no`), and `--start` and `--end` (days
  in the time zone `--zone` names, for example `America/New_York`, which
  is required with them, as the page takes days in your browser's zone).
- `report ministry`: `--sort`, `--page` and `--size` for the summary; `--ministry DUID` with `--requests join` or `--requests leave`
  counts one Ministry's list, and takes `--state`, `--history` (`current`
  or `all`) and `--start`, `--end` and `--zone`, as `report information`
  takes them.

No command takes a search: searches match names. A report the campaign does
not include (Financial or Ministry stewardship), or no current campaign, is
exit 1 (`not_available`). In System logs, where the page's event counts
the rows it showed, the command's counts 0: it shows none.

`report comparison` prints aggregates only and applies the
[small-count protection](../specs/stewardship/campaign-history/spec.md#pledge-distribution),
because its output leaves the Admin portal: the current campaign's pledge
distribution and quartiles are never printed; a final imported year's
bands are, except that a band of fewer than 5 Families shows
`fewer than 5` with no total, its neighbour band and that year's
pledging-Family count, averages and shares of pledging Families are
withheld (null), and a year with fewer than 5 pledging Families prints no
bands or quartiles. The average of average pledges is withheld when a
withheld year contributes to it. No data note is printed. A percentage is
a decimal string with one decimal place, money a decimal string.

`report source-form` is the one report that prints DUIDs: it is System
health's **Families the form cannot open** list, the Families whose
ParishSoft data keeps the Family form from opening, so that each can be
corrected in ParishSoft. Like the page, it needs an Administrator; a
session of any scope may run it. It prints the current `campaign_id`
(null without a current campaign, with counts of 0 and no rows),
`eligible` (the portal-eligible Families checked), `families` (how many
cannot open the form) and `rows`, one per problem: `family_duid`,
`member_duid` (null when the Member record itself is unusable) and `field`,
in the page's words. It never prints the Family's name or the refused
value. It records the page's "viewed" event with the number of Families
listed. A check that cannot run now (no ParishSoft data yet, or a restore
under review) is exit 3 (`unavailable`): try again later. The host's
`pk-stewardship source-form-check` runs the same check.

`report data-to-fix` prints the counts on **Data to fix in ParishSoft**,
the summary of the data problems that are fixed in ParishSoft. Like the
page, Administrators and Staff may run it, from a session of any scope;
each sees only the sections whose list they may open (Staff:
`unreachable_families` and `census_changes`). It prints the current
`campaign_id` (null without one) and `sections`, one member per section:
`form_cannot_open`, `refused_addresses`, `unreachable_families` and
`census_changes`. Each is `{"count": N}` (refused addresses add
`families`, how many Families the addresses belong to), or
`{"unavailable": "…"}` with the page's reason when that section cannot
count now, such as no current campaign or a campaign without the census.
A section that cannot count never fails the command. It prints counts
only, never a name or address, and records the page's "viewed" event with
the total it printed. A restore under review is exit 3 (`unavailable`).

`report roster` counts **Roster changes to enter**, the resolved Ministry
joins and leaves that staff enter in ParishSoft by hand. Like the page,
Administrators and Staff may run it, from a session of any scope; a
Ministry leader is refused (`denied`). Its options are the page's:
`--show todo` (the default: not yet marked Entered in ParishSoft) or
`--show all` (marked ones too), and `--ministry DUID` for one Ministry. It
prints `show`, `ministry` (null without `--ministry`), `matching` (the
list's rows), `joins`, `leaves`, `entered` (already marked; 0 with `--show
todo`), `replaced` (the page's "Replaced by a later response") and
`ministries`: the same counts for each Ministry on the list, by DUID
(`ministry`) with its `name`. Each row names a Member and a Family, so the
rows stay on the page and its downloads (`export roster`, see
[Roster changes download](#roster-changes-download)). It records the page's
"viewed" event with a count of 0.

`report census` counts **Census changes**, the census changes Families
reported, which are carried into ParishSoft automatically or by hand. Like
the page, Administrators and Staff may run it, from a session of any scope;
a Ministry leader is refused (`denied`). It prints `filters` (the page's
choices, as `status`, `history`, `route`, `kind`, `start`, `end` and
`zone`), `matching`, `routes`, `kinds` and `statuses`: the page's summary,
every change of the campaign by status key whatever the other filters,
including zeros, with `superseded` and `cancelled` only with `--history`.
Each row names a Family and often a Member, with their contact details, so
the rows stay on the page and its downloads. It records the page's
"viewed" event with a count of 0.

### Recording a census change's outcome

`census resolve` is a Census changes row's **Mark entered in ParishSoft**
(`entered`), **Ignore** (`ignored`) or **Reopen** (`reopened`) icon. Like the
page's buttons it needs a full-scope session; Administrators and Staff may
record Entered and Ignore, and only an Administrator may reopen an ignored
change (Staff get `denied`).
Entered in ParishSoft cannot be undone, so, as the page's dialog does,
`--action entered` shows the change's id and version and asks for `yes`
(see [Confirmations](#confirmations)), or takes `--yes`; Ignore and Reopen
ask nothing.

```sh
pk-admin census resolve PROPOSAL_ID --action entered --expected-version 3
pk-admin census resolve PROPOSAL_ID --action reopened --expected-version 4 \
    --note - --yes < note.txt
```

`PROPOSAL_ID` and the `--expected-version` are the change's id and version,
which `export census --format jsonl` prints (`proposal_id` and `version`,
with the `actions` the change takes now; the page's own downloads leave
them out). `--note` is optional, except to reopen; `-` reads it from
standard input, which keeps a note that names people out of shell history
and the process list, and then needs `--yes`, because standard input cannot
carry both the note and an answer. `--request-key` is made for you and
written to standard error when left out. Repeating the command with the
same key returns the same resolution (`created` false) and records nothing
new; the key used for another outcome or note is exit 1 (`invalid`). A
change someone updated since that version is exit 1 (`stale_version`):
read it again on the page. An outcome the change's status does not allow
(an automatic change, or one already entered), or reopening without a
note, is exit 1 (`invalid`); a change that is not the current campaign's
is exit 1 (`not_available`), and so is a repeat after the current campaign
changed.

The result has `created`, `request_key`, `resolution` (`id`,
`proposal_id`, `action`, `expected_version`, `created_at`) and `proposal`
(the change's `id`, `version`, `decision` and `execution` as the command
left them), never the note, the Family, the Member or the value. System
logs show the page's `census_change_updated` and the command's
`admin_cmd_census_resolve`.

### Reviewing an automatic census change

`census review` is an Automatic row's review: **Approve** (`approved`),
**Ignore** (`ignored`) or **Send back to To review** (`unreviewed`). Only an
Administrator may review (Staff and Ministry leaders get `denied`, before
anything is read), with a full-scope session, as on the page. Every review can be changed again until publication of the change
starts, so, as on the page, it asks nothing. Approving does not publish
anything: publication is a separate step.

```sh
pk-admin census review PROPOSAL_ID --decision approved --expected-version 3
pk-admin census review PROPOSAL_ID --decision ignored --expected-version 4 \
    --note - < note.txt
```

Take `PROPOSAL_ID` and `--expected-version` from `export census --format
jsonl` (`proposal_id` and `version`); its `reviews` key lists the decisions
the change takes now. `--note` is optional; `-` reads it from standard
input. `--request-key` works as for `census resolve`: made for you and
written to standard error when left out, and a repeat with the same key
returns the same review (`created` false) and records nothing new; the key
used for another decision or note is exit 1 (`invalid`). A change someone
updated since that version is exit 1 (`stale_version`). A decision the change does not
take now is exit 1 (`invalid`): a By-hand change, the decision it already
has, approving a Conflict, or any review of a change that is queued for
publication or already has a final outcome. A change that is not the
current campaign's is exit 1 (`not_available`), and so is a repeat after
the current campaign changed.

The result has `created`, `request_key`, `review` (`id`, `proposal_id`,
`decision`, `expected_version`, `created_at`) and `proposal` (the change's
`id`, `version`, `decision` and `execution` as the command left them),
never the note, the Family, the Member or the value. System logs show the
page's `census_change_updated` and the command's `admin_cmd_census_review`.

### Editing a value or resolving a conflict

`census edit` is the same row's **Edit value** (`--value`) and **Clear
edit** (`--clear`): the value publication writes instead of the Family's
answer, which never changes. `census resolve-conflict` is **Use the
Family's value anyway** on a Conflict: it approves the change over
ParishSoft's current value, which publication then expects to find. Both
are an Administrator's only, with a full-scope session, one change at a
time, and ask nothing, as on the page; neither publishes anything.

```sh
pk-admin census edit PROPOSAL_ID --expected-version 3 --value "Patricia"
pk-admin census edit PROPOSAL_ID --expected-version 4 --clear
pk-admin census edit PROPOSAL_ID --expected-version 5 --value - < value.txt
pk-admin census resolve-conflict PROPOSAL_ID --expected-version 6 \
    --note "Checked with the Family"
```

Take `PROPOSAL_ID` and `--expected-version` from `export census --format
jsonl`; its `editable` and `resolvable` keys say whether each command acts
on the change now. `--value` is read as the Family form reads its answer: a
date as `YYYY-MM-DD` no later than today, Gender one of the census choices,
and anything else no longer than ParishSoft takes (never more than 1,000
characters). A value the change
cannot take is exit 1 (`invalid`), with the reason in `error.fields`
(field `value`); the value itself is never printed. An edit keeps the
change's decision. An edit that would change nothing (the value already
set, `--clear` with no edit, or the Family's own answer with no edit)
records nothing and returns `unchanged` true. `--value -` and `--note -`
read standard input, so only one of them may be `-`. To undo a resolved
conflict, use `census review --decision unreviewed` (or `ignored`); the
change is a Conflict again. `--request-key`, stale versions and changes of
another campaign work as for `census review`; any other change the command
does not take now is exit 1 (`invalid`).

`census edit`'s result has `created`, `unchanged`, `request_key`, `review`
(`id`, `proposal_id`, `edited`, `expected_version`, `created_at`, or null
when unchanged) and `proposal` (`id`, `version`, `decision`, `execution`
and `edited`, whether an edited value is now set). `census resolve-conflict`'s
result is `census review`'s. Neither has the value, the note, the Family or
the Member. System logs show the page's `census_change_updated` and the
command's `admin_cmd_census_edit` or `admin_cmd_census_resolve_conflict`.

## Digests

Each emailed daily or weekly report links to a saved copy on the portal,
`/admin/reports/emailed/daily/SNAPSHOT_ID/` or
`/admin/reports/emailed/weekly/SNAPSHOT_ID/`. The `digest` commands read
that saved copy, which never changes, and record the same "viewed" events
as opening it. A session of any scope (read-only included) may read them;
a weekly report needs an Administrator, as its page does.

```sh
pk-admin digest list
pk-admin digest daily SNAPSHOT_ID
pk-admin digest weekly SNAPSHOT_ID --page 2
pk-admin digest weekly-request --yes
pk-admin digest list --requested REQUEST_KEY
```

- `digest list` is the **Emailed reports** page: where to find each
  `SNAPSHOT_ID`. It prints the current `campaign_id`; `daily`, the newest
  ready daily reports (`snapshot_id`, `through_date`, `covered_dates`,
  `observed_at`); `weekly`, the newest weekly reports (`snapshot_id`,
  `manual`, `observed_at`; null for a reader who is not an Administrator);
  `weekly_request_available`, whether the campaign has the weekly report
  schedule `digest weekly-request` needs; and `shown`, how many of each
  list it shows at most (60), newest first. With `--requested` and a
  `digest weekly-request` key, `requested` holds that request's `task_id`
  and, once the report is ready, its `report_id` (null until then); a key
  that is not a request of the current campaign gives null. With no
  current campaign the lists are empty. Like the page, it records no
  event.

- `digest daily` prints the saved figures: `participation` (the figures'
  `fact_set_id`, source and dates), `statistics` (the active Families,
  Members, email, responses and, with Financial stewardship, pledge
  totals) and `days`, the daily table. `comparison` is the digest's
  Compared with past campaigns section when its schedule included it (null
  otherwise): the `day` and `report_date` compared, `compared_with` (the
  years), the headline `figures` (each `figure`, `value` and its
  `comparisons`, with Withheld where the small-count protection applies)
  and the `archives` compared (each `year` and file `checksum`).
- `digest weekly` prints the report's `information_count`,
  `correction_count` and `total`, whether it was `manual`, and `items`, 50
  to a page (`--page`): each item's `item_id`, whether it is new
  `information` or a correction, its `captured` and `current` state
  (`current_actionable`, `superseded` or `withdrawn`) and whether it
  `changed`. It never prints what a Family wrote; open the page for that.
- `digest weekly-request` asks for a manual weekly report now, as the
  Send a weekly report now page does, for the current campaign. A manual
  report may repeat items already reported, which the page asks you to
  tick; the command asks the same at the
  [confirmation prompt](#confirmations), or takes `--yes` (otherwise
  nothing happens, exit 4). It
  takes `--request-key`, as [task retries](#task-retries) do, and prints
  `created`, `request_key` and `task`; follow the task with
  `task show --watch`. Without a weekly report schedule it is exit 1
  (`not_available`); while an earlier report is still being sent or
  awaits review, exit 1 (`stale_version`): try again once it is resolved.
  System logs show the page's `weekly_manual_requested` and
  `admin_cmd_digest_weekly_request`.

An unknown `SNAPSHOT_ID`, or a daily report's id given to `digest weekly`,
is exit 1 (`not_available`).

## Report exports

A report export (the Response dashboard's **Export**, and the export of
every other report page) is a file the server makes in the background. From
the command line, getting one takes three steps: request it, wait until it
is ready, and fetch it. Each step does what the export's status page does
and records the same events in System logs. Following an export needs any
session; every other step, like every export, needs a full-scope session.

```sh
pk-admin export create --fact-set FACT_SET_ID --format csv \
  --timezone America/New_York
pk-admin export status EXPORT_ID --watch 5
pk-admin export fetch EXPORT_ID
pk-admin exports clean
```

- `export create` requests a participation export, as the Response dashboard's export
  form does: `--fact-set` names the participation figures to export (the
  page exports the ones it shows; take their `fact_set_id` from
  `report participation`), `--format` is `csv`, `png`, `pdf` or `xlsx`,
  and `--timezone` is the time zone of the file's dates. Exports of the
  other report pages are requested on those pages for now; every command
  below works on them too. An export's `EXPORT_ID` is in its status
  page's address, `/admin/reports/exports/EXPORT_ID/`.
- `export status` shows the export as its status page does. With
  `--watch SECONDS` it repeats until the export stops changing (anything
  but queued, running, retry_wait or abandoned).
- `export cancel` and `export retry` are the status page's buttons: cancel
  an export that is not ready yet, or run a failed one again once its
  cause is fixed. Exports are point-in-time: a ready file can be fetched
  for a short window, then it is deleted, and an expired export cannot be
  made again; create a new one from current data instead (see
  [point-in-time exports](../specs/stewardship/operations/spec.md#point-in-time-exports)).
- `export fetch` saves a ready export's file on the host (see below).
- `exports clean` deletes every fetched file.

`export create` and `export retry` take
`--request-key`, as [task retries](#task-retries) do: pass your own UUID,
or the command makes one and writes it to standard error before it acts.
Repeating a command with the same key, or cancelling an export already
cancelled, returns the same export and changes nothing (`created` is
false). Exit 6 (`outcome_unknown`) names the key in `error.request_id`:
read the export with `export status`, or repeat the command with the key.

While other work holds the export's campaign (a source refresh being
promoted, for example), the status page turns its buttons off, and
`export status` shows `changes_available` false. Then `export create`,
`export cancel` and `export retry` are exit 1
(`denied`). That refusal is temporary, not a permissions problem: try again
later.

| `export status` field | What it holds |
| --- | --- |
| `id`, `campaign_id`, `report`, `format`, `created_at` | The export |
| `state` | `queued`, `running`, `retry_wait` or `abandoned` (still being made; the worker picks an abandoned run up again), `failed`, `succeeded` (made, file not yet published), `ready`, `expired` or `cancelled`, as the page shows |
| `changes_available` | Whether the page's buttons may act now; false while other work holds the campaign |
| `can_cancel` | Whether the page offers **Cancel** now |
| `expires_at` | When the export ends ([point-in-time exports](../specs/stewardship/operations/spec.md#point-in-time-exports)): for a ready or expired file, the end of its download window, never later than the file recorded; for a `failed` export, when its Retry window ends; null while it is being made or once cancelled |
| `file_name`, `content_type`, `size`, `sha256`, `count` | The file: its name, type, size in bytes, SHA-256 and number of rows (null until it is published; an expired export keeps them) |

`export create`, `export cancel` and `export retry` print `created`,
`request_key` (none for a cancel) and `export`, the export's
`export status` fields afterwards. An unknown export or `--fact-set`, figures that are not
ready, or a file that has expired is exit 1 (`not_available`). Cancelling a
ready export or retrying one that has not failed is exit 1
(`stale_version`): read it again. A key already used
for a different export is exit 1 (`invalid`). System logs show the page's
events (`export_requested`, `export_cancelled`, `export_downloaded`) and,
for each change, `admin_cmd_export_create`, `admin_cmd_export_cancel` or
`admin_cmd_export_retry`, attributed to
the approving Administrator with the automation session as its subject.

### Family-level exports

The exports whose rows name Families and Members are requested like
`export create`, then followed and fetched the same way. Each needs a
full-scope session, does what the page's export form does and records the
same events, plus `admin_cmd_export_financial`,
`admin_cmd_export_information`, `admin_cmd_export_ministry` or
`admin_cmd_export_ministry_packet`. Each prints `created`, `request_key`
and `export`, the export's `export status` fields; the rows themselves
are only in the file `export fetch` saves.

```sh
pk-admin export financial --format csv --timezone America/New_York \
  --filter amount=nonzero
pk-admin export information --format xlsx --timezone America/New_York \
  --history
pk-admin export ministry --format pdf --timezone America/New_York \
  --ministry 9 --requests join
pk-admin export ministry-packet --format pdf --timezone America/New_York \
  --ministry 9 --ministry 12
pk-admin export fetch EXPORT_ID
```

- `export financial` is the Financial stewardship page's export;
  `export information` the Additional information page's, with
  `--history` for each item's workflow history; `export ministry` the
  Ministry requests page's, for one Ministry's list
  (`--ministry DUID --requests join|leave`, both required; the summary
  export was removed, #1126);
  `export ministry-packet` its follow-up packet, for the
  Ministries `--ministry` names (repeat it; none means every Ministry you
  may report on) and `--history` for earlier versions.
- `export financial` is a privileged action, so it asks at the
  [confirmation prompt](#confirmations) (or takes `--yes`), and System logs
  show
  `automation_fresh_gate` beside it. A read-only session is exit 1
  (`denied`).
- `--format` is `csv`, `xlsx` or `pdf`; `--timezone` the time zone of the
  file's times. Each takes `--request-key`, as `export create` does.
- `--filter NAME=VALUE` (repeat for more) is one of the page's filters,
  named as its form names it, for example `amount=nonzero`, `active=active`
  or `frequency=monthly` for the financial export, `disposition=all` or
  `needed=yes` for the information export, and `activity=active` (or,
  with `--ministry`, `state=unresolved`) for the Ministry export. Dates
  (`start=` and `end=` on the information and Ministry exports, and
  `first_start=`, `first_end=`, `latest_start=` and `latest_end=` on the
  financial export) are days in a time zone, so `zone=` is required with
  them, for example
  `--filter start=2026-11-01 --filter zone=America/New_York`. A filter
  the page would refuse is exit 1 (`invalid`), and so are `page` and
  `size`, which the page's export form never sends. A search is refused:
  what you type there names a Family, and it would stay in your shell's
  history.
- A Ministry the campaign does not offer is exit 1 (`invalid`). When an
  export cannot be requested right now (its inputs are being refreshed,
  for example), the command exits 3 (`unavailable`): try again.

#### Family codes and mail merge

`export directory` is the Active parishioner family directory's export of
Family codes, for
the current campaign, and `export postal` its mail merge. Each needs a
full-scope session and records the page's events, plus
`admin_cmd_export_directory` or `admin_cmd_export_postal`. Both are
privileged actions, so each asks at the
[confirmation prompt](#confirmations) (or takes `--yes`), and System logs
show `automation_fresh_gate`
beside it.

```sh
pk-admin export directory --format csv --timezone America/New_York \
  --filter response=no
pk-admin export postal --format xlsx --timezone America/New_York
```

- They take the same `--format`, `--timezone`, `--filter` and
  `--request-key` as the exports above.
- `--filter` refuses `search` and `exact_code` (exit 1, `invalid`): a name
  or a Family code typed on a command line stays in your shell's history
  and is visible to other processes on the host. Choose Families with the
  page's other filters, or use the page.
- Both need the web's Family code key, which this command reads only for
  them, and only for a new request (repeating a request key reads none).
  If that key differs from the one the web started with (a key rotation in
  progress) or cannot be read, they exit 2 (`credential_mismatch`) and
  nothing is made: retry once the web service is recreated. While a key
  rotation holds the keys, they exit 3 (`unavailable`): retry shortly.
- The file holds every matching Family's code (or mailing address). Keep
  it private and delete it once used (`pk-admin exports clean`).

#### Exact daily exports

An exact (latest-data) export calculates today's figures first and then
makes the file; the Response dashboard does not offer it, so the command
line is the way to request one. From the
command line it takes two steps more than `export create`: follow the
calculation, then fetch the export it hands off to.

```sh
pk-admin export exact create --scope current --format csv \
  --timezone America/New_York
pk-admin export exact status EXACT_ID --watch 10
pk-admin export fetch EXPORT_ID
```

- `export exact create` freezes the current campaign's figures as the page's
  form does: `--scope` is `historical` (the default) or `current`,
  `--format` is `csv`, `png`, `pdf` or `xlsx`, and `--timezone` is the time
  zone of the file's dates. It takes `--request-key`. It needs a full-scope
  session.
- `EXACT_ID` is the `exact.id` that `export exact create` prints, or the
  last part of the exact status page's address. It is not an export id:
  `export status EXACT_ID` is exit 1 (`not_available`).
- `export exact status` shows the request as its status page does: `state`
  is the calculation's until it hands off, then the export's. Once handed off
  it gives `export_id`: fetch that with `export fetch`. With `--watch` it
  stops once the file is settled, or the calculation failed or was
  cancelled. Any session may run it.
- `export exact cancel` and `export exact retry` are the status page's
  buttons, before or after the handoff. A repeat (the same retry key, or a
  second cancel) changes nothing (`created` false). After the handoff a
  retry is the export's, so a retry key first used on the calculation is a
  new retry there: `stale_version` unless the export failed.

Each change prints `created`, `request_key` and `exact`, the request's
`export exact status` fields afterwards, and records the page's events plus
`admin_cmd_export_exact_create`, `admin_cmd_export_exact_cancel` or
`admin_cmd_export_exact_retry`. An unknown request, or no figures to freeze,
is exit 1 (`not_available`); cancelling a ready export or retrying one that
has not failed is exit 1 (`stale_version`).

#### One Family's timeline

`export family-timeline` exports one Family's timeline, as the Family
timeline page's **Export this timeline** does: every line of its timeline
(oldest first), in the page's columns, with the Family's details, the
page's summary and its Family code as the report information of an XLSX or
PDF file (a CSV is the timeline table alone). Like the page's full view, it is
for Administrators. Because the file holds the Family's code, it is a
privileged action, so the command asks at the
[confirmation prompt](#confirmations) (or takes `--yes`), and System logs
show `automation_fresh_gate` beside it.

```sh
pk-admin export family-timeline FAMILY_RECORD_ID --format pdf \
  --timezone America/New_York
pk-admin export family-timeline FAMILY_RECORD_ID --mode testing \
  --format csv --timezone America/New_York
```

`FAMILY_RECORD_ID` is the id in the Family's timeline page address,
`/admin/reports/families/FAMILY_RECORD_ID/`. `--mode testing` is
the page's Testing rehearsal view. It takes `--request-key` like the other
exports. A Family that is not in the current campaign is exit 1
(`not_available`).

#### Campaign comparison

`export comparison` is the Campaign comparison page's **Download**: the
Every number table as CSV or XLSX, or, as PDF, the summary (headline
figures and full-width day-by-day charts) followed by that table. It needs
a full-scope session and records the page's `campaign_comparison_exported`
(with the comparison day) plus `admin_cmd_export_comparison`.

```sh
pk-admin export comparison --format pdf
pk-admin export comparison --day 8 --format xlsx --timezone America/New_York
```

- `--day` is a campaign day from 1 to the comparison day (the current
  campaign's last complete day), as on the page; without it, the
  comparison day. A day with no figures is exit 1 (`not_available`).
- `--format` and `--request-key` are as for the exports above.
  `--timezone` (the time zone of the file's request time) is optional:
  the file holds days, not times, so without it the parish's time zone is
  used, as the page asks for none.
- The file is the page's figures with the
  [small-count protection](../specs/stewardship/campaign-history/spec.md#pledge-distribution)
  applied, since it leaves the Admin portal: a figure that could single
  out fewer than 5 pledging Families reads `Withheld`, this campaign's
  and a partial year's quartiles read `On the page only`, and no data
  note is included. It is meant for parish leadership; still share it only
  with them.

#### Campaign archive

`export archive` is Past campaigns' **Download campaign archive**: the
current campaign's archive workbook (one XLSX in the
[campaign archive format](../specs/stewardship/campaign-history/spec.md#archive-workbook-format)),
partial while the campaign runs (through yesterday) and final once it has
closed: through its end date, or, for a campaign closed before its end
date, through its last full day. That is at once when no response arrived
on the day it closed, and otherwise the next day. It holds each Family's
giving, so,
like `export financial`, it asks at the prompt (or takes `--yes`) and needs
a full-scope session. It records
`campaign_archive_exported` (with the cutoff's campaign day) plus
`admin_cmd_export_archive`, and the file is fetched with `export fetch`.

```sh
pk-admin export archive --yes
```

- It takes no format or time zone: the file is always the archive XLSX,
  named `stewardship-<year>.xlsx` or `stewardship-<year>-partial.xlsx`.
- When a download is unavailable now (before the first day has ended, a
  campaign closed today with a response today, no Family responded yet, or
  the report's daily figures still being calculated), it is exit 1
  (`not_available`).
- Keep the file only in the parish's restricted archive folder; only the
  current campaign can be exported, so download the final file before the
  next campaign is created.

The lists of Families behind the response counts are `export directory`
with the directory's Response filters.

### Limitations download

`export limitations` writes the page's download of the **Limitations**
report to standard output, and its document (`campaign_id`, `file_name`,
`content_type`, `format`, `timezone`, `size`, `sha256`, `count`) to
standard error, as `export census` does. It needs a full-scope session, as
every export does; Administrators and Staff may run it, and a Ministry
leader is refused (`denied`). It takes the page's Show menu as `--show`
(`any`, the default, `cannot_serve` for Members who cannot participate in
ministries, or `cannot_attend` for Families that cannot attend Mass or
prayer services), without the page's search, `--format` (`xlsx`, the
default, `csv` or `pdf`) and `--timezone` (UTC, the default, or a time zone
name, for the file's times). It records the page's "downloaded" event
(`talents_report_exported`) with the number of rows in the file.

```sh
(umask 077; pk-admin export limitations --show cannot_serve > limitations.xlsx)
```

The files are the page's own, with the same two tables (Members, then
Families) and columns; `count` is the Member rows plus the Family rows. The
file names Families and Members, so create it private, as above, and
delete it once used; the wrapper refuses to write it to a terminal.

### Roster changes download

`export roster` writes the page's download of **Roster changes to enter**
to standard output, and its document (`campaign_id`, `file_name`,
`content_type`, `format`, `timezone`, `size`, `sha256`, `count`) to
standard error, as `export limitations` does. It needs a full-scope
session, as every export does; Administrators and Staff may run it, and a
Ministry leader is refused (`denied`). It takes `report roster`'s options,
`--show todo` (the default: not yet marked Entered in ParishSoft) or
`--show all`, and `--ministry DUID`, plus `--format` (`xlsx`, the default,
`csv` or `pdf`) and `--timezone` (UTC, the default, or a time zone name,
for the file's times). It records the page's "downloaded" event
(`roster_changes_exported`) with the number of changes in the file.

```sh
(umask 077; pk-admin export roster --ministry 9 --format csv > roster.csv)
```

The files are the page's own, with its columns. They name Members and
Families, so create them private, as above, and delete them once used; the
wrapper refuses to write one to a terminal.

### Ministry follow-up download

`followup export` writes the page's **Export these requests** download of
the Ministry follow-up queue to standard output: every request the
filters select, not one page, with each Member's contact details as the
request page shows them. Its document (`campaign_id`, `file_name`,
`content_type`, `format`, `timezone`, `size`, `sha256`, `count`) goes to
standard error, as for `export roster`. It needs a full-scope session, as
every export does; Administrators and Staff may run it, and a Ministry
leader gets only the requests of the Ministries they lead, as on the page.
It takes `followup
list`'s filters, `--ministry DUID`, `--action`, `--state` (`unresolved`,
the default, `any`, `new`, `in_progress`, `resolved` or
`closed_no_response`), `--outcome` and `--sort`, but no search, page or
size, plus `--format` (`xlsx`, the default, `csv` or `pdf`) and
`--timezone` (UTC, the default, or a time zone name, for the file's
times). A PDF holds at most 1,000 requests; more is exit 1 (`invalid`, at
`format`): choose CSV or XLSX, or narrow the filters. It records the
page's "downloaded" event (`ministry_followup_exported`) with the number
of requests and the Ministries in the file, never their values.

```sh
(umask 077; pk-admin followup export --state any --format csv > requests.csv)
```

The file is made when you run the command and is not kept anywhere else:
run it again for a newer one. It is the page's own file, with its columns,
and names Members with their contact details, so create it private, as
above, and delete it once used; the wrapper refuses to write it to a
terminal.

### Census changes download

`export census` writes the page's download of **Census changes** to
standard output, and its document (`campaign_id`, `file_name`,
`content_type`, `format`, `timezone`, `size`, `sha256`, `count`) to
standard error, as `logs export` does. It needs a full-scope session, as
every export does; Administrators and Staff may run it, and a Ministry
leader is refused (`denied`). It takes `report census`'s filters, without
the page's search, `--format` (`xlsx`, the default, `csv`, `pdf` or
`jsonl`) and `--timezone`, and records the page's "downloaded" event with
the number of rows in the file. The two zones differ: `--zone` is the time
zone the `--start` and `--end` days of the filter fall in (required with
them), while `--timezone` (UTC, the default, or a time zone name) is the
zone the file's times are written in.

```sh
(umask 077; pk-admin export census --status all --format jsonl > census.jsonl)
```

CSV, XLSX and PDF are the page's own files, with the same columns. JSON
Lines is for scripts: one object per change, the page's columns with the
same text under the keys `family`, `parishsoft_status`, `family_duid`,
`who`, `member_duid`, `what`, `parishsoft_now`, `family_answer`, `edited`,
`route`, `status`, `submitted`, `recorded_by`, `recorded_at` and `note`,
plus `proposal_id`
and `version`, which recording a row's outcome needs and the page's files
leave out, and `actions`: the outcomes you may record on that row now
(`entered` and `ignored` on a By-hand change still open, `reopened` on an
ignored one, for Administrators only, and only with a `--note` saying
why), `reviews`: the decisions `census review` takes on that row now
(an Automatic change still open, for Administrators only), and `editable`
and `resolvable`: whether `census edit` and `census resolve-conflict` act
on it now (true or false, for Administrators only). They are empty or false
while other campaign work holds the campaign, as the page then offers no
row actions. The file names Families and
Members with their contact details, so create it private, as above, and
delete it once used; the wrapper refuses to write it to a terminal.

### Fetching an export's file

`pk-admin export fetch EXPORT_ID` reads `export status`, then downloads the
file as the page's download does (recording the page's `export_downloaded`
events) straight into a private file on the host, checks its size and
SHA-256 against `export status`, and prints only its `path`, `size` and
`sha256`. The file's rows never reach the terminal, and no copy is kept
anywhere else.

Fetched files go to the export directory: `--export-dir DIR`, else
`PK_ADMIN_EXPORT_DIR`, else `exports` in the session directory
(`ROOT/run/admin-automation/exports`), which the wrapper creates owned by
you with mode `0700`. Each file, `EXPORT_ID-FILE_NAME`, has mode `0600`
and is never overwritten: a second fetch of the same export is refused.
While the file arrives it is `.EXPORT_ID.partial`; a failed download, a
size or digest that does not match (exit 3: fetch again), Ctrl-C, a closed
terminal or SSH session, and TERM each delete it at once. Each fetch holds
one database connection of the web's for as long as the download takes, at
most the deployment's download time limit (five minutes by default).

`export fetch` and `exports clean` are the wrapper's own verbs: they print
the small JSON object above (`{"removed": N}` for a clean), not a
`pk-admin/1` document. Their own refusals print no JSON at all, only a
`pk-admin:` line on standard error: exit 1 when the export is not ready, 2
before anything is downloaded (a bad id, no `pgrep`, a file already
fetched, an unusable export directory) or when the file's final name
appeared while it downloaded, and 3 for a file that does not match. When
the `export status` or `export download` run itself fails, its
`pk-admin/1` document and exit status come through unchanged, so exit 1 or
3 with a JSON document is the command's, and without one the wrapper's.
`export fetch` needs `pgrep` (the `procps` package on Debian and Ubuntu)
to stop the download on Ctrl-C.

The files hold the export's rows, which can include Family names,
addresses, codes and pledges, exactly as the page's download does. Delete
them once used: `pk-admin exports clean` deletes every fetched file (and
any partial one) and prints how many it removed.

`export fetch` refuses an export that is not ready (exit 1) and says what
to do: follow a running one with `export status --watch`, retry a failed
one, or create a new one for an expired or cancelled one.
Underneath, `export download EXPORT_ID
--stream` writes the file's bytes to standard output and its document
(`id`, `file_name`, `content_type`, `size`, `sha256`, `count`) to standard
error, as `logs export` does; the wrapper refuses to write it to a terminal.

## Portal users

`users list` reads the Portal users page, and `users preview-upgrade` its
Upgrade preview panel ([#952](https://github.com/epiphany40223/parishkit/issues/952)).
An Administrator's session, read-only or full, may run them; each records
the page's event in System logs (`portal_users_viewed`, with the number of
users printed).

```sh
pk-admin users list
pk-admin users list --sort=-role
pk-admin users preview-upgrade
```

`users list` takes `--sort`: `email` (the default), `name`, `role` or
`last_login`, with `--sort=-NAME` for the other direction. `-role` lists
Administrators first and `-last_login` the newest sign-in first; users who
never signed in come last either way.

| `users list` field | What it holds |
| --- | --- |
| `form` | `users` once the configuration holds users, else `sign_in_rules` |
| `sort` | The sort used |
| `users` | Each user's `email`, `name`, `role` (`administrator`, `staff` or `ministry_leader`) and `last_login`, the newest successful sign-in of any Google account with that address, or `null` |

Until the users upgrade, the users are the exact-address rules that grant a
role, each with its highest role and a `null` name. Deny rules and
hosted-domain rules are not users: see the upgrade preview for a domain
rule's people.

`users preview-upgrade` prints what the
[users upgrade](../specs/stewardship/operations/spec.md#users-upgrade-from-sign-in-rules)
would do with today's sign-in rules, the same preview the operator's
`pk-stewardship upgrade-users --preview` prints. It changes nothing. Once
the configuration holds users it is exit 1 (`not_available`).

| `users preview-upgrade` field | What it holds |
| --- | --- |
| `administrators` | The addresses that keep Administrator: the exact list the operator confirms |
| `users` | Each user the upgrade writes: `email`, `name` (the ParishSoft default, or the address's local part) and `role` |
| `address_rules` | Each exact-address rule that grants a role: `email`, its `roles` and the `role` it keeps |
| `domain_rules` | Each hosted-domain rule: `domain`, `roles`, the `role` its people get, and the `users` it converts (its recorded Google accounts that signed in before) |
| `dropped_deny_rules` | Each deny rule: `email`, `name`, and `overrides_identity`, whether it refuses a recorded Google account today |
| `not_converted` | Addresses a domain rule lets in today that do not carry over: `never_signed_in` and `disabled` |

If someone you expect is missing from `users`, add an exact-address rule for
them on the Portal users page before the upgrade.

`users import-leaders` is the Users page's ParishSoft Ministry leaders
table and its import. It needs a full-scope session. `--preview` lists the
leaders without a user and reviews importing everyone (`--all`) or the
chosen addresses (`--email`, repeatable); with neither it only lists them.
`--confirm --token` applies the review, as the page's Apply does. Each
listing is recorded in System logs (`user_leaders_listed`, with the number
of leaders, never an address).

```sh
pk-admin users import-leaders --preview
pk-admin users import-leaders --preview --email ann@example.org
pk-admin users import-leaders --confirm --token -
```

| `users import-leaders` field | What it holds |
| --- | --- |
| `version` | The applied configuration's version the review was made against |
| `leaders` | Each leader without a user: `email`, `name` ("Last, first"), `member_duid` and the `ministries` they lead, by name |
| `unreachable` | How many leaders have no usable email address, so are not listed |
| `added` | Each user the import adds: `id`, `email`, `name`, `member_duid` and `role` (always `ministry_leader`) |
| `skipped` | Each chosen address left out: `email` and `reason` (`user`, already a user; `not_listed`, no longer a leader without a user) |
| `remaining` | Chosen leaders past one change's limit of 100, left for another import |
| `preview` | `token`, which `--confirm` takes, or `null` with nobody to import |
| `created`, `request` | With `--confirm`: as `users confirm` prints them |

`users email-leaders` is the Users page's
[Email Ministry leaders](../specs/stewardship/admin-portal/spec.md#email-ministry-leaders):
one email to each chosen Ministry leader user saying they can sign in, and
the open requests in each of their Ministries. It needs a full-scope
session. `--preview` reviews emailing every Ministry leader user (`--all`)
or the chosen addresses (`--email`, repeatable; one of the two is
required) and sends nothing. `--confirm --token` applies the review, as
the page's Apply does: the emails are queued, and the background engine
sends them. A review lasts fifteen minutes, and the page and the command
line can apply each other's reviews. Applying the same review again sends
nothing more; each new review and its Apply sends again, so check Last
emailed on the Users page first.

```sh
pk-admin users email-leaders --preview --all
pk-admin users email-leaders --preview --email ann@example.org
pk-admin users email-leaders --confirm --token -
```

| `users email-leaders` field | What it holds |
| --- | --- |
| `mode` | `testing` or `production`, the mode the review was made in |
| `note` | In Testing, that every email goes to the Testing recipient instead of the leader (its address stays on the page); otherwise `null` |
| `emailed` | Each leader who will be emailed, by Name: `email`, `name` and the `ministries` the email lists, each with its `duid`, `name` and the open `join` and `leave` request counts |
| `skipped` | Each chosen address left out: `email` and `reason` (`not_leader`, no Ministry leader user now; `no_ministry`, leads none of the campaign's Ministries in ParishSoft) |
| `remaining` | Chosen leaders past one Apply's limit of 100, left for another review |
| `preview` | `token`, which `--confirm` takes, or `null` with nobody to email |
| `created`, `request_id`, `recipient_count` | With `--confirm`: whether this Apply recorded the request (`false` when the same review was applied before), its ID, and how many leaders it emails |

No document names a parishioner. Each review is recorded in System logs
(`user_leader_emails_reviewed`) and each Apply
(`user_leader_emails_requested` and `admin_cmd_users_email_leaders`), with
the number of leaders, never an address. While Production mail is paused
both are exit 1 (`not_available`) and nothing is queued; so is
`--preview` with no current campaign with Ministries or with the campaign
archived. A review that no longer matches (the campaign, mode or Testing
recipient changed, a leader is no longer one, or a leader's Ministries or
counts changed) is exit 1 (`stale_version`), as is an expired one; review
again. Another Administrator's review is `denied`.

## Security events and critical problems

`events list` reads what the Admin home page asks you to acknowledge: the
**Security events awaiting acknowledgement** panel (a widening of who may
sign in, or a replaced backup key) and the **critical problems** banner
(critical problems of the past 24 hours). `events acknowledge` is their
**Acknowledge** buttons. Like the page, both need an Administrator.
`events list` runs on a session of any scope and records nothing in System
logs; `events acknowledge` needs a full-scope session. Neither asks for
confirmation, as the page asks none.

```sh
pk-admin events list
pk-admin events acknowledge --security EVENT_ID
pk-admin events list | jq -r '.result.critical.token // empty' \
  | pk-admin events acknowledge --critical -
```

| `events list` field | What it holds |
| --- | --- |
| `security` | The security events still on your own dashboard, newest first: `id`, `kind`, `name` (as on the page), `target` (the address or domain it is about), `before` and `after` (role names), `key_before` and `key_after` (key IDs, for a replaced backup key only), `created_at` and `actor_id` (the account ID of the Administrator who made the change) |
| `critical` | The banner: `count` (problems not yet acknowledged), `all_ended` (every one of them has ended), `since` (the start of the 24 hours), `events` (`event`, `name`, `count` and `ended_at` for each kind of problem, most frequent first) and `token`, which `--critical` takes (null when nothing awaits), with `acknowledgeable`, how many problems it covers |

A security event leaves your own list once you acknowledge it. When it
leaves every other Administrator's list too is decided as on the page; see
[user change checks and alerts](../specs/stewardship/admin-portal/spec.md#user-change-checks-and-alerts).
In short, an acknowledgement by the Administrator who made the change, or
by the account it grants, does not clear it for the other Administrators
who existed when it took effect, and for a replaced backup key the
maker's own never clears it for anyone else.
`--critical` acknowledges
for every Administrator exactly the problems that `events list` counted.
A problem recorded after the list stays, for the next acknowledgement, as
do problems past the first 500.

| `events acknowledge` field | What it holds |
| --- | --- |
| `kind` | `security` or `critical` |
| `event_id` | The security event, or null for `critical` |
| `acknowledged` | How many were newly acknowledged; 0 when they already were, and then nothing is recorded |

Each acknowledgement is recorded in System logs as on the page
(`security_event_acknowledged` or `critical_events_acknowledged`), with
`admin_cmd_events_acknowledge` naming the session. An unknown security
event is exit 1 (`not_available`), and a token that `events list` did not
print is exit 1 (`invalid`).

## Ministry follow-up

`followup list` reads the follow-up queue on **Ministry requests**, and
`followup show` one request's page with its follow-up history.
`followup update` is a request's **Save**, and `followup bulk` the queue's
bulk outcome (**Mark as Joined**, **Declined**, **Left** or **Stayed**). The
reads run on a session of any scope and record the page's
`ministry_followup_viewed`; the changes need a full-scope session. Each
request names a Member of a Family, so the documents give the request's ID
and version, its Ministry, its status and instants, but never the Member's
name or DUID, the notes or a contact attempt's notes; read those on the
page. No command takes the queue's search.

```sh
pk-admin followup list --ministry 12 --action join
pk-admin followup show REQUEST_ID
pk-admin followup update REQUEST_ID --expected-version 1 --state in_progress \
  --contact-channel phone --contact-at 2054-10-05T14:30:00-04:00
pk-admin followup update REQUEST_ID --expected-version 2 --state resolved \
  --outcome joined
pk-admin followup bulk --outcome join:joined \
  --request REQUEST_ID:1 --request OTHER_ID:3 \
  --contact-channel email --contact-at 2054-10-05T09:00:00-04:00
```

`followup list` takes the queue's filters: `--ministry DUID`, `--action`
(`any`, `join` or `leave`), `--state` (`unresolved` by default: New or In
progress; `any`, `new`, `in_progress`, `resolved`, `closed_no_response`, or
`not_in_parishsoft` for requests whose Member ParishSoft no longer lists),
`--outcome`, `--sort` (`newest`, `name` or `ministry`), `--page` and
`--size` (25, 50, 100 or 250; 50 by default). A page past the end shows
the last page, as on the page.

| `followup list` field | What it holds |
| --- | --- |
| `campaign_id` | The current campaign |
| `filters` | The choices applied: `ministry`, `action`, `state`, `outcome`, `sort` |
| `page`, `size`, `total` | The page shown, rows per page, and how many requests the filters keep |
| `rows` | Each request: `id`, `version` (what `--expected-version` takes), `ministry` (DUID) and `ministry_name`, `in_campaign`, `kind` (`member` or `proposed_member`), `action`, `state`, `outcome`, `open`, `member_status` (`current`, or `missing`, `moved`, `inactive` or `deceased` in ParishSoft), `submitted_at`, `resolved_at`, `source_resolved`, `latest`, `last_contact_at` and `roster` (the Entered in ParishSoft tick: `eligible`, `entered`, `sequence`) |

| `followup show` field | What it holds |
| --- | --- |
| `campaign_id` | The current campaign |
| `request` | The request, as a `followup list` row |
| `history_page`, `more_history` | The history page shown (`--page`), and whether another follows |
| `history` | Each recorded follow-up, newest first: `id`, `request_id` (the request it was recorded on, across the Family's resubmissions), `expected_version`, `state`, `outcome`, `contact_channel`, `contact_at`, `actor_id`, `created_at` and `bulk` (recorded by a bulk outcome) |

`followup update` sets the request's status (`--state new`, `in_progress`,
`resolved` with `--outcome`, or `closed_no_response`) and, with
`--contact-channel` (`email`, `phone`, `in_person` or `other`), a contact
attempt at `--contact-at`, an ISO 8601 instant with its UTC offset, with
optional `--contact-notes` (at most 2,000 characters). `--notes TEXT`
(at most 5,000 characters) replaces the request's notes; left out, the
notes stay as they are. Either `--notes` or `--contact-notes` may be `-`
to read standard input, one of them per run, which keeps the text out of
shell history. The outcome `other` needs notes. An option that would have
no effect is a usage error (exit 2): `--outcome` without `--state
resolved`, and `--contact-at` or `--contact-notes` without
`--contact-channel`.

`followup bulk` resolves every chosen request with one outcome
(`join:joined`, `join:declined`, `leave:leave_confirmed` or
`leave:declined`: every request must be of that kind), each request at the
version `followup list` printed, keeping each one's notes. It takes the
same optional contact attempt (`--contact-channel`, `--contact-at`,
`--contact-notes`), recorded on every chosen request, and at most 250
requests. It asks for confirmation first (or takes `--yes`), since a
resolved request cannot be edited again; a choice too long to record is
refused before it asks.

Either change takes `--request-key`; without one it makes a key and prints
it to standard error. The key binds each request's revision, as on the
page: a repeat with it records nothing twice, and `followup bulk` with the
same key and more requests resolves only the new ones (`changed` counts
them).

| `followup update` field | What it holds |
| --- | --- |
| `created` | False for a repeat with the same key, which recorded nothing |
| `request_key` | The key, for a repeat |
| `revision` | What was recorded: `id`, `request_id`, `expected_version`, `state`, `outcome`, `contact_channel`, `contact_at`, `created_at` |
| `request` | The request now: `id`, `version`, `state`, `outcome` |

| `followup bulk` field | What it holds |
| --- | --- |
| `created`, `request_key` | As for `followup update` |
| `outcome` | The outcome recorded, as given |
| `changed` | How many requests this run resolved (0 for a repeat) |
| `requests` | Each chosen request now: `id`, `version`, `state`, `outcome` |

Each change is recorded in System logs as on the page
(`ministry_request_updated`, and `ministry_followup_bulk_applied` for a
bulk outcome), with `admin_cmd_followup_update` or `admin_cmd_followup_bulk`
naming the session. A request changed since its version, closed, or whose
Member ParishSoft no longer lists is exit 1 (`stale_version`): read it
again. The request is checked before the other rules, so a stale request
is `stale_version` even when another option is also wrong. A choice the
page refuses, or text past its length, is exit 1 (`invalid`), with
`error.fields` naming the option (`outcome`, `notes`, `contact_at`,
`contact_notes` or `request`). A request that is not one of the current
campaign's, or is outside your Ministries, is exit 1 (`not_available`).

The queue's **Export these requests** is `followup export`, see
[Ministry follow-up download](#ministry-follow-up-download).

## Roster tick and Additional information

`followup roster-entered` sets or clears a resolved Ministry request's
**Entered in ParishSoft** tick, as the tick on the request's page does, and
`followup roster-bulk` marks several at once, as **Roster changes to
enter**'s bulk mark does. `followup information-show` reads one
**Additional information** request's page with its follow-up history, and
`followup information-update` is its **Save follow-up**. The read runs on a
session of any scope and records the page's `information_viewed`; the
changes need a full-scope session. Ministry leaders may not tick, and only
Staff and Administrators follow up Additional information, as on the pages.
The documents give IDs, versions, flags, account IDs and instants, never a
Member's or Family's name or DUID, what the Family wrote, or the Staff
notes; read those on the page, or download them with `export information`.

```sh
pk-admin followup show REQUEST_ID
pk-admin followup roster-entered REQUEST_ID --set --expected-version 0
pk-admin followup roster-entered REQUEST_ID --clear --expected-version 1
pk-admin followup roster-bulk --request REQUEST_ID:0 --request OTHER_ID:2
pk-admin followup information-show ITEM_ID
pk-admin followup information-update ITEM_ID --expected-version 3 \
  --completed yes --notes -
```

`--expected-version` for the tick is its current sequence, as `followup
show` prints `roster.sequence` (0 before the first tick); each set or clear
adds one. A tick changed since is exit 1 (`stale_version`). A request that
takes no tick (only a join or leave a person resolved as joined or leave
confirmed takes one) is exit 1 (`invalid`, `error.fields` naming
`request`), and so is a tick that already says so (naming `set` or
`clear`). `followup roster-bulk` takes each chosen request as `ID:VERSION`,
up to 250, and only marks; it asks for confirmation first (or takes
`--yes`), and saves nothing if any request is stale or already marked.
Find the requests with `followup list --state resolved`: a row whose
`roster.eligible` is true and `roster.entered` is false is one to enter, and
its `id` and `roster.sequence` are the `ID` and `VERSION`. The list shows
only each Member's newest request for a Ministry (as the queue does), but
**Roster changes to enter** also lists an older resolved request that a
newer request has replaced, so such a request can be missing from
`followup list`. That is a known gap: mark it on the page, or with
`followup roster-entered` if you already have its ID.

`followup information-update` takes `--needed yes|no` (Follow-up needed),
`--completed yes|no` (Follow-up completed) and `--notes TEXT` (at most 5,000
characters; `-` reads standard input, which keeps the text out of shell
history, and trims surrounding whitespace, as `followup update` does; the
page keeps it as typed). Each one left out stays as it is, as the page's form opens filled
in. Clearing a completed follow-up (`--completed no`) needs
`--confirm-clear`, the page's confirmation box; without it the command is
exit 1 (`invalid`, naming `confirm_clear`). A version that is no longer
current is exit 1 (`stale_version`).

Each change takes `--request-key`; without one it makes a key and prints it
to standard error. A repeat with the key records nothing twice; the key
with another change is exit 1 (`invalid`). A request or item that is not
one of the current campaign's is exit 1 (`not_available`).

| `followup roster-entered` field | What it holds |
| --- | --- |
| `created` | False for a repeat with the same key, which recorded nothing |
| `request_key` | The key, for a repeat |
| `entry` | The tick recorded: `id`, `request_id`, `sequence`, `entered`, `created_at` |

| `followup roster-bulk` field | What it holds |
| --- | --- |
| `created`, `request_key` | As for `followup roster-entered` |
| `changed` | How many requests this run marked (0 for a repeat) |
| `entries` | Each chosen request's tick, as `entry` above |

| `followup information-show` field | What it holds |
| --- | --- |
| `campaign_id` | The current campaign |
| `item` | `id`, `version` (what `--expected-version` takes), `disposition` (`current_actionable`, or why the request no longer is), `submitted_at`, `follow_up_needed`, `completed`, `followed_up_at` and `followed_up_by` (an account ID), `replacement_id` (a later request that replaced this one), `previously_reported` and `correction_resolved` |
| `history_page`, `more_history` | The history page shown (`--page`), and whether another follows |
| `history` | Each Save, newest first: `id`, `expected_version`, `follow_up_needed`, `followed_up`, `confirm_clear`, `followed_up_at`, `followed_up_by`, `actor_id`, `created_at` |

| `followup information-update` field | What it holds |
| --- | --- |
| `created`, `request_key` | As for `followup roster-entered` |
| `revision` | What was recorded, as a `history` row |
| `item` | The item now: `id`, `version`, `follow_up_needed`, `followed_up_at` |

Each change is recorded in System logs as on the page
(`ministry_roster_entered` for each tick, `information_updated`), with
`admin_cmd_followup_roster_entered`, `admin_cmd_followup_roster_bulk` or
`admin_cmd_followup_information_update` naming the session.

## Output changelog

- `pk-admin/1` (ADM-11 PR 2): the first version, with the session commands.
- `pk-admin/1` (ADM-11 PR 3a): additive. The status commands above,
  `watch` and `arguments` in each catalog entry, and the error code
  `watch_interrupted`.
- `pk-admin/1` (ADM-11 PR 3b): additive. `go-live readiness` and
  `go-live progress`.
- `pk-admin/1` (ADM-11 PR 4): additive. `schedule preview`,
  `schedule confirm` and `config request show`, the first three-word
  command, `error.fields` on an `invalid` change, and `error.request_id`
  on an unknown outcome.
- `pk-admin/1` (#621): a deliberate change of meaning, kept on version 1.
  `sessions` lists live sessions only unless `--include-ended` is given
  (it listed ended ones of the last 30 days too), and its `ended_at` is an
  expired session's deadline (it was null; a revoked session's is still
  its revocation time). It mirrors Automation access, and its only readers
  are Administrator-run assistants. `--sort` is additive.
- `pk-admin/1` (#510): additive. `status` `source` gains `full_started_at`,
  `data_as_of`, `connection`, `connection_at`, `overdue_full_at`,
  `out_of_date` and `held_for_send`.
- `pk-admin/1` (#630): a change of meaning, kept on version 1. A quick
  update that found nothing new is recorded instead of promoted, so
  `status` `source` `refreshed_at` (the current snapshot's promotion time)
  no longer moves on an empty quick update; it moves only when new data is
  promoted. `delta_succeeded_at` and `connection_at` still move on every
  quick update that ParishSoft answered, so a reader that used
  `refreshed_at` as "ParishSoft answered recently" should read
  `connection_at` instead.
- `pk-admin/1` (ADM-13 PR 2b, #530): additive. `system health`, with
  `--watch`.
- `pk-admin/1` (ADM-13 PR 3b, #530): additive. `system backup-now`
  (keyed, fresh-gated, always prompts), and `system health`'s
  `backup_request` with the newest request's state.
- `pk-admin/1` (#1214): additive. Each `system health` problem gains
  `severity` (`error` or `warning`) and `tiles` (the System health tiles
  that show it, in page order: `mail`, `source`, `backups`, `debug` or
  `version`).
- `pk-admin/1` (ADM-11 PR 9a): additive. `task retry`, the first command
  that takes `--request-key`, and `error.request_id` on its unknown
  outcome, holding the key.
- `pk-admin/1` (ADM-11 PR 6a): additive. `refresh start` and
  `refresh status`; `status` `source` gains `late_minutes`, `resume_at`
  and `catching_up`.
- `pk-admin/1` (#686): a deliberate change of meaning, kept on version 1.
  A `system health` problem names its processes in `subjects` (a list of
  `service`, `process` and `target`) instead of its own `service`,
  `process` and `target`, and a condition in several processes (debug
  logging, a stopped or silent service, a halted or outage-paused mail
  sender) is one problem. The command was days old, and its only readers
  are Administrator-run assistants.
- `pk-admin/1` (ADM-11 PR 5): additive. `--yes` on prompting commands,
  the catalog's `prompts` flag in use, and exit 4 (`confirmation_required`)
  when a prompt is not answered.
- `pk-admin/1` (ADM-11 PR 6b): additive. `test sample-preview` and
  `test sample`, which prompts while an earlier test's outcome is unknown.
- `pk-admin/1` (ADM-11 PR 6c): additive. `test families-preview`,
  `test families` (fresh-gated, always prompts) and `test status`.
- `pk-admin/1` (ADM-11 PR 9b): additive. `delivery list`,
  `delivery show`, `delivery resolve`, `delivery refusals` and
  `delivery refusal-show`.
- `pk-admin/1` (ADM-11 PR 8a): additive. `logs list` and `logs export`,
  the first command that streams a file (its document on standard error),
  and `streams` in each catalog entry.
- `pk-admin/1` (ADM-11 PR 8b): additive. `export create`, `export status`,
  `export cancel`, `export retry`, `export regenerate` and
  `export download --stream`; the wrapper's `export fetch` and
  `exports clean`.
- `pk-admin/1` (ADM-11 PR 8c): additive. `report list`,
  `report participation`, `report responses`, `report financial`,
  `report talents`, `report information` and `report ministry`.
- `pk-admin/1` (ADM-11 PR 8d): additive. `digest daily`,
  `digest weekly` and `digest weekly-request`, which prompts (or takes
  `--yes`).
- `pk-admin/1` (ADM-11 PR 8e): additive. `export financial`,
  `export information`, `export ministry` and `export ministry-packet`.
- `pk-admin/1` (ADM-11 PR 8f): additive. `export directory` and
  `export postal`.
- `pk-admin/1` (#817): additive. `test families-preview --names` with
  `--timezone`, and its `export` field (null without `--names`).
- `pk-admin/1` (#966): additive. `test families-preview --names --format`
  (`csv`, the default, `xlsx` or `pdf`), and `logs export --format xlsx`.
- `pk-admin/1` (ADM-11 PR 9c): additive. `delivery resend` and
  `delivery refusal-clear`, which prompt. `delivery show`'s `actions` may
  now include `resend` (it listed only `delivery resolve` actions); an
  offered `resend` is `delivery resend`.
- `pk-admin/1` (#944): additive. `config request cancel`, which prompts.
- `pk-admin/1` (#931): a change of meaning, kept on version 1.
  `delivery list --sort` follows the reworked Sent mail page: the
  Family DUID token is `duid` (it was `recipient`, which is now refused),
  the new `name` sorts by the Family's name from the latest ParishSoft
  data, and the default is `-changed`, the latest change first (it was
  `-created`, the newest email first; `--sort=-created` still gives that
  order). The document's fields are unchanged. Its only readers are the
  Administrator and the operator, and the Administrator keeps no legacy
  forms (the
  [exception](../specs/stewardship/admin-automation/spec.md#output-documents)
  records why).
- `pk-admin/1` (#935): additive. `delivery refusals --sort name`, by the
  Family's name, as the Refused addresses page.
- `pk-admin/1` (#1004): additive. Each `delivery list` row has
  `refused`, the email's unresolved refused-address count that Outgoing
  mail shows. `delivery show` is unchanged.
- `pk-admin/1` (ADM-11 PR 7a): additive. `portal maintenance show` and
  `portal maintenance set` (fresh-gated, always prompts).
- `pk-admin/1` (ADM-11 PR 8d follow-up): additive. `digest list
  [--requested KEY]`, the Emailed reports page's lists (any session, no
  event).
- `pk-admin/1` (#463): additive. `report source-form`, System health's
  Families the form cannot open, by DUID (any session, Administrators
  only, the page's event).
- `pk-admin/1` (#463): additive. `files list`, the Hosted files page's
  library and each file's uses (any session, Administrators only, no
  event).
- `pk-admin/1` (#463): additive. `files delete` and `files rename`, the
  Hosted files page's Delete and Edit (full scope, Administrators only, not
  fresh-gated; `files delete` prompts; their events).
- `pk-admin/1` (#970): a deliberate change, kept on version 1.
  `report talents` is now `report limitations`, with no alias, because the
  Family form no longer asks for talents and the report is Limitations. Its
  document drops `collects_talents`, `members` and `talents`, and keeps
  `campaign_id`, `cannot_serve` and `cannot_attend`. It still records
  `talents_report_viewed`, the event its page records. No other command
  changes, and the only readers are Administrator-run assistants; a script
  that ran `report talents` now gets a `usage` error (exit 2).
- `pk-admin/1` (#966): a deliberate change, kept on version 1.
  `logs export --format csv` and `--format xlsx` write the page's file,
  whose column headings are now plain names (`Time`, `Source`, `Level`,
  `Type`, `Actor`, `Actor identifier`, `Correlation identifier`,
  `Campaign identifier`, `Subject identifier`, `Recorded detail`) in the
  same order; they were the snake_case keys. JSON Lines keys are
  unchanged, as are the command's options and its document. The only
  readers are Administrator-run assistants; a script that reads the CSV by
  heading must use the new names.
- `pk-admin/1` (#932): a deliberate change, kept on version 1. The file
  `test families-preview --names` writes now has the columns `Family`, then
  `Family DUID`, in the page's order; they were `Family DUID`, then
  `Family name` (the heading is now `Family`, as on every Admin table).
  `export ministry --ministry N --requests join` or `leave` writes the
  page's list order: `Member`, `Member DUID`, `Status` (it was `State`),
  `Outcome`, the contact columns or `Current Ministry role`, and
  `Submitted` last (it was fourth). Its `Ministry DUID` column moved to the
  report information, beside `Ministry`, since a list is always one
  Ministry's. The commands' options and documents are unchanged, and the
  only readers are Administrator-run assistants; a script that reads these
  files by position or by the old headings must change.
- `pk-admin/1` (#952 slice 2): additive. `users list` and
  `users preview-upgrade`.
- `pk-admin/1` (#952 slice 5): additive. `users preview` and
  `users confirm`. Each ParishSoft Name lookup a preview makes is audited
  and counts toward the Administrator's 60 lookups an hour; past that the
  preview exits 3 with `unavailable`. When the database refuses the lookup
  for the command session (ended, begun before a recovery, or its user no
  longer an Administrator), the preview exits 5 with `session_ended`.
- `pk-admin/1` (#952 slice 6): additive. `users import-leaders --preview`
  and `--confirm`. A session the leaders definer refuses is exit 5
  (`session_ended`), as for the Name lookup.
- `pk-admin/1` (#463, ADM-11 PR 8h): additive. `export exact create`
  (full scope; its event), `export exact status` (any session, `--watch`,
  no event), `export exact cancel` and `export exact retry` (full scope;
  their events), the Participation page's exact export and its status
  page's buttons.
- `pk-admin/1` (#1011): additive. `report data-to-fix`, Data to fix in
  ParishSoft's section counts (any session, Administrators and Staff, the
  page's event).
- `pk-admin/1` (ADM-13 PR 2c, #530): additive. `system health` gains
  `daily_sent` and `daily_limit`.
- `pk-admin/1` (#530): additive. `system health` gains `daily_resume_at`.
- `pk-admin/1` (ADM-13 PR 6b, #530): additive. `system debug-off` (keyed,
  fresh-gated, always prompts), and `system health`'s `debug_switch` with
  the newest debug-off switch.
- `pk-admin/1` (ADM-13 PR 5b, #530): additive. `system
  refresh-accept-preview` and `system refresh-accept` (fresh-gated, always
  prompts), and `system health`'s `drop_acceptance`.
- `pk-admin/1` (ADM-13 PR 5c, #530): additive. `system
  refresh-accept-preview` gains `examples`, the example Family DUIDs of each
  failing count, without names.
- `pk-admin/1` (ADM-13 PR 6c, #1207): additive. `system debug-allow`
  (Testing only, fresh-gated, always prompts).
- `pk-admin/1` (#1018): additive. `history list`, `history preview -`
  (the workbook on standard input), `history confirm` (prompts for a
  replacement) and `history remove` (prompts), the Past campaigns page's
  actions ahead of the page (Administrators only; their events).
- `pk-admin/1` (#463, ADM-11 PR 8g): additive. `export family-timeline`
  (full scope, Administrators only, fresh-gated, prompts; its event).
- `pk-admin/1` (#558): a change of meaning, kept on version 1. The
  information and Ministry report dates are now days in the viewer's time
  zone, as on their pages: `report information` and `report ministry` take
  `--zone`, required with `--start` or `--end` (without it they are exit 1,
  `invalid`), and their `filters` gain `zone`; the information and
  Ministry exports need `--filter zone=…` with dates. Their only readers
  are Administrator-run assistants.
- `pk-admin/1` (#1018): additive. `report comparison [--day N]`, the
  Campaign comparison page's figures (any session; its event), with the
  small-count protection.
- `pk-admin/1` (#463): additive. `content list` and `content history
  [--revision ID]`, the Pages and emails and Content history pages' reads
  (any session, Administrators only, no event).
- `pk-admin/1` (#463): additive. `report roster [--show todo|all]
  [--ministry DUID]`, the Roster changes to enter list's counts (any
  session, Administrators and Staff, the page's event).
- `pk-admin/1` (#463): additive. `report census [--status S] [--history]
  [--route R] [--kind K] [--start DATE] [--end DATE] [--zone ZONE]`, the
  Census changes list's counts (any session, Administrators and Staff,
  the page's event); `report list` offers it.
- `pk-admin/1` (#1018, slice 6): additive. `export comparison [--day N]
  --format csv|xlsx|pdf --timezone ZONE`, the Campaign comparison page's
  download (full scope; `campaign_comparison_exported` and
  `admin_cmd_export_comparison`).
- `pk-admin/1` (#1018 part (a)): additive. `export archive`, Past
  campaigns' Download campaign archive (full scope, fresh-gated;
  `campaign_archive_exported` and `admin_cmd_export_archive`).
- `pk-admin/1` (#1018, slice 7): additive. `schedule show` lists each
  schedule's `comparison`, and a `schedule preview` change document may set
  a daily digest's `comparison`; `digest daily` prints its retained
  `comparison` section (or null).
- `pk-admin/1` (#1126): a deliberate removal, kept on version 1.
  `export ministry` no longer exports the campaign-wide summary, which the
  Ministry requests page no longer offers: `--ministry` and `--requests`
  are both required, and without them the command is exit 1 (`invalid`).
  One Ministry's join and leave lists are unchanged. The only readers are
  Administrator-run assistants.
- `pk-admin/1` (#463): additive. `parish show`, `ministries list`,
  `branding show` and `campaign shares [--campaign ID]`, the Parish
  settings, Ministries, Parish logos and Share options pages' reads (any
  session, Administrators only, no event).
- `pk-admin/1` (#463): additive. `parish preview` and `parish confirm`,
  the Parish settings page's Review and Apply (full scope, Administrators
  only); `parish confirm` records `admin_cmd_parish_confirm`.
- `pk-admin/1` (#463): additive. `artwork show`, the Campaign images
  page's read (any session, Administrators only, no event).
- `pk-admin/1` (#463): additive. `events list` and `events acknowledge
  (--security EVENT_ID | --critical TOKEN|-)`, the Admin home page's
  security events and critical problems and their Acknowledge buttons
  (Administrators only; the list on any session with no event, the
  acknowledgement on a full-scope session with
  `admin_cmd_events_acknowledge`).
- `pk-admin/1` (#463): additive. `content show {page,email} SLOT
  [--revision ID] [--default]`, the content editor's read of one page's or
  email's text (any session, Administrators only, no event).
- `pk-admin/1` (#463): additive. `send controls`, the Pause and resume
  mail page's read (any session, Administrators only, no event).
- `pk-admin/1` (#463): additive. `census resolve PROPOSAL_ID --action
  entered|ignored|reopened --expected-version VERSION [--note TEXT|-]
  [--request-key KEY] [--yes]`, a Census changes row's Record outcome (full
  scope, Administrators and Staff, Reopen for Administrators only;
  `--action entered` prompts; `census_change_updated` and
  `admin_cmd_census_resolve`).
- `pk-admin/1` (#463): additive. `export census [--status S] [--history]
  [--route R] [--kind K] [--start DATE] [--end DATE] [--zone ZONE]
  [--format csv|xlsx|pdf|jsonl] [--timezone ZONE]`, the Census changes
  page's download (full scope, Administrators and Staff, the page's
  event), streamed as `logs export` is; its `jsonl` format adds each
  change's `proposal_id`, `version` and `actions`.
- `pk-admin/1` (#463): additive. `artwork remove-preview SLOT` and
  `artwork remove SLOT`, Campaign images' Remove this image and its Apply
  (full scope, Administrators only); `artwork remove` records
  `admin_cmd_artwork_remove`.
- `pk-admin/1` (#463): additive. `workgroup show`, `workgroup preview
  (--name NAME | --clear) --expected-version VERSION` and `workgroup
  confirm --token TOKEN|-`, the No-reminders WorkGroup page's read, Review
  and Apply (Administrators only; the read in any session, the others in
  a full-scope one); `workgroup confirm` records
  `admin_cmd_workgroup_confirm`. `workgroup show` also prints `excluding`
  and `excluding_from`, the set Reminders skip now (#1313).
- `pk-admin/1` (#1161): additive. `export comparison` no longer needs
  `--timezone`; without it the file uses the parish's time zone.
- `pk-admin/1` (#1211): additive. `delivery bounce-report MESSAGE_ID
  --address ADDRESS --note TEXT|- [--request-key KEY] [--yes]`, the Mail
  message page's Report a bounced address (full scope, Administrators only,
  prompts; `recipient_bounce_reported` and
  `admin_cmd_delivery_bounce_report`), and `kind` (`provider` or
  `reported_bounce`) in `delivery refusals` rows and `delivery
  refusal-show`.
- `pk-admin/1` (#1190): a change of default, kept on version 1. Without
  `--format`, `logs export`, `export census` and `test families-preview
  --names` now write XLSX (they wrote CSV), as every page's format menu now
  preselects XLSX. The document's fields are unchanged; a script that
  needs CSV passes `--format csv`.
- `pk-admin/1` (#1216): a deliberate change, kept on version 1. The files
  `export census` writes have a `ParishSoft status` column right after
  `Family`, and its JSON Lines a `parishsoft_status` key after `family`:
  "Not in ParishSoft" for a Family the current data no longer has, blank
  otherwise. Such a Family's `Family` (and `family`) is now blank; it read
  "Not in ParishSoft". The command's options and document are unchanged,
  and the only readers are Administrator-run assistants; a script that
  reads these files by position must change.
- `pk-admin/1` (#1267): a deliberate removal, kept on version 1, as for
  #1126. The local Ministry activity is gone: `ministries list` rows have
  no `active`, `report ministry` takes no `--activity` and its `filters`
  have no `activity`, and an `--filter activity=...` on `export ministry`
  is refused (`invalid`). The only readers are Administrator-run
  assistants.
- `pk-admin/1` (#1216, migration 0094): a deliberate change, kept on
  version 1, as for `export census` above. The files `export financial`,
  `export information` and `export limitations` write have a `ParishSoft
  status` column right after `Family` (both of `export limitations`'
  tables): "Not in ParishSoft" for a Family the current data no longer
  has, "Inactive in ParishSoft" for one ParishSoft marks inactive
  (`export financial` and `export information`), blank otherwise. Such a
  missing Family's `Family` cell is now blank; it read "Unavailable
  Family" (financial, limitations) or "Family" (information). A Family or
  Member with no usable name in these files and in `export ministry` and
  `export ministry-packet` reads "Name not recorded in ParishSoft" (it
  read "Family" or "Unavailable Member"). The commands' options and
  documents are unchanged, and the only readers are Administrator-run
  assistants; a script that reads these files by position must change.
- `pk-admin/1` (#463): additive. `export limitations [--show
  any|cannot_serve|cannot_attend] [--format csv|xlsx|pdf] [--timezone
  ZONE]`, the Limitations report's download (full scope, Administrators and
  Staff, the page's `talents_report_exported`), streamed as `export census`
  is.
- `pk-admin/1` (#463): additive. `integration list`, `integration show
  TARGET` and `integration status TARGET [--watch SECONDS]`, the
  Integrations pages' reads (any session, Administrators only, no event).
- `pk-admin/1` (#463): additive. `integration preview TARGET
  --expected-version VERSION (--changes JSON|- | --remove)` and
  `integration confirm TARGET --token TOKEN|- [--yes]`, an integration
  settings page's Save and Remove (full scope, Administrators only;
  `integration confirm` fresh-gated and prompting, recording
  `admin_cmd_integration_confirm`).
- `pk-admin/1` (#632): additive. `integration preview parishsoft
  --refresh-schedule DOCUMENT|-` changes the refresh schedule through the
  page's editor (with or without `--changes`, which is no longer required
  when it is given); the preview gains `schedule` (null unless the
  schedule changes) and `integration show` gains `refresh_schedule`
  (ParishSoft only, else null).
- `pk-admin/1` (#463): additive. `integration backup-probe [--folder
  LINK]` (the Off-site backups page's Test access, recording
  `admin_cmd_integration_backup_probe`) and `integration dismiss TARGET
  --request-id ID` (a key change line's Dismiss, recording the page's
  `credential_result_dismissed` and `admin_cmd_integration_dismiss`), full
  scope, Administrators only, neither fresh-gated nor prompting.
- `pk-admin/1` (#463): additive. `integration key status TARGET
  [--request-id ID] [--watch S]` (the Key replacement status page, any
  session, recording nothing), `integration key finish-switching-preview
  TARGET` (Finish switching's review, full scope) and `integration key
  finish-switching TARGET --token TOKEN|-` (its confirm button, full scope,
  fresh-gated and prompting, recording
  `admin_cmd_integration_key_finish_switching` and a `policy_change`
  notice).
- `pk-admin/1` (#463): additive. `integration backup-key challenge
  --public-key-stdin`, `integration backup-key preview --intent TOKEN
  --code-stdin` and `integration backup-key confirm --token TOKEN [--yes]`,
  the Backup encryption key page's steps (full scope; the confirmation is
  fresh-gated and prompting, recording
  `admin_cmd_integration_backup_key_confirm` and a `policy_change` notice).
  The public key and the code follow the preamble as one
  `pk-admin-public-key/1 <base64>` or `pk-admin-proof/1 <base64>` line,
  which the host wrapper's `--public-key-file PATH`, or `--code-file PATH`
  or `--code-stdin`, sends. The results have `version`, `current`,
  `fingerprint`, `challenge` and `intent`; `version`, `changes` and
  `preview`; and `created` and `request`.
- `pk-admin/1` (#463): additive. `integration key replace TARGET
  --expected-version V --secret-stdin [--changes JSON] [--yes]`, a settings
  page's Save with a new key (full scope, fresh-gated and prompting,
  recording `admin_cmd_integration_key_replace` and a `policy_change`
  notice). The key follows the preamble as one `pk-admin-secret/1 <base64>`
  line, which the host wrapper's `--secret-file PATH` or `--secret-stdin`
  sends; the result has `version`, `target`, `name`, `setup`, `changes`,
  `key_request_id` and `request`.
- `pk-admin/1` (#463): additive. `export roster [--show todo|all]
  [--ministry DUID] [--format csv|xlsx|pdf] [--timezone ZONE]`, the Roster
  changes to enter download (full scope, Administrators and Staff, the
  page's `roster_changes_exported`), streamed as `export limitations` is.
- `pk-admin/1` (#469): additive. `schedule preview`'s change document
  takes `series` entries ("this and following", the whole series, or
  delete a remembered series), and the preview gains `series_changes`.
- `pk-admin/1` (#528): additive. `census review PROPOSAL_ID --decision
  approved|ignored|unreviewed --expected-version VERSION [--note TEXT|-]
  [--request-key KEY]`, an Automatic Census changes row's Review (full
  scope, Administrators only, neither fresh-gated nor prompting;
  `census_change_updated` and `admin_cmd_census_review`). `export census
  --format jsonl` records gain `reviews`, the decisions `census review`
  takes on the row now.
- `pk-admin/1` (#463): additive. `campaign show`, `campaign preview
  --changes FILE|- --expected-version VERSION` and `campaign confirm
  --token TOKEN|-`, Campaign settings' read, Review and Apply
  (Administrators only; the read in any session, the others in a
  full-scope one); `campaign confirm` records `admin_cmd_campaign_confirm`
  and the page's own events.
- `pk-admin/1` (#632): additive. `integration schedule-preview
  [--refresh-schedule DOCUMENT|-]`, the ParishSoft page's live refresh
  schedule check (any session, Administrators only, no event).
- `pk-admin/1` (#1288): a change of meaning, kept on version 1.
  `send controls` `health` gains `required` (whether a sender check is
  needed) and `trouble` (why: `test_in_progress`, `provider_failure`,
  `test_failed` or `sender_changed`; null when none is needed). `ready` now
  means "held mail may be released": while paused it is true with `proof`,
  `checked_at` and `expires_at` all null when no check is needed, and
  otherwise only while a passed check is current, which now lasts 30
  minutes (it was five). It used to mean that an accepted test after the
  pause existed. A reader that took `ready` as proof of a test must read
  `required` and `checked_at` instead. The command mirrors the Pause and
  resume mail page, and its only readers are Administrator-run assistants.
- `pk-admin/1` (#1437): a correction, kept on version 1. A campaign
  change with financial stewardship enabled and a required financial field
  left empty reports that field's problem with `code` `required` (it was
  `invalid`), as an empty field elsewhere does. The closed set of codes is
  unchanged, and the only readers are Administrator-run assistants.
- `pk-admin/1` (#463): additive. `send pause-preview --reason TEXT|-`,
  `send resume-preview --reason TEXT|-`, `send resolve-preview --decision
  release|cancel|clear [--type KIND …] --reason TEXT|-` (full scope, no
  event) and `send confirm --token TOKEN|-` (full scope, fresh-gated and
  prompting, recording `admin_cmd_send_confirm`), Pause and resume mail's
  previews and confirmation.
- `pk-admin/1` (#463 PR 11a): additive. `followup list`, `followup show`,
  `followup update` and `followup bulk`, the Ministry follow-up queue.
- `pk-admin/1` (#1434): additive. `census edit PROPOSAL_ID
  --expected-version VERSION (--value TEXT|- | --clear) [--note TEXT|-]
  [--request-key KEY]`, an Automatic Census changes row's Edit value and
  Clear edit, and `census resolve-conflict PROPOSAL_ID --expected-version
  VERSION [--note TEXT|-] [--request-key KEY]`, its Use the Family's value
  anyway (full scope, Administrators only, neither fresh-gated nor
  prompting; `census_change_updated` and `admin_cmd_census_edit` or
  `admin_cmd_census_resolve_conflict`). `export census --format jsonl`
  records gain `editable` and `resolvable`.
- `pk-admin/1` (#463 PR 10): additive. `campaign shares-preview --changes
  JSON|- --expected-version VERSION [--campaign ID]` and `campaign
  shares-confirm --token TOKEN|- [--campaign ID]`, Share options' Review
  and Apply (full scope, Administrators only; the confirm records
  `admin_cmd_campaign_shares_confirm`).
- `pk-admin/1` (#463): additive. `content preview --delete ID [--delete
  ID ...] --expected-version VERSION` and `content confirm --token
  TOKEN|-`, the Pages and emails email tables' Delete (full scope,
  Administrators only, neither fresh-gated nor prompting; `content
  confirm` records `admin_cmd_content_confirm`), and `content preview
  --html HTML|-`, the content editors' live plain-text preview (no event).
- `pk-admin/1` (#1398): a removal, kept on version 1. Exports are
  point-in-time, so `export regenerate` is gone (an unknown command, exit
  2 `usage`), and `admin_cmd_export_regenerate` is no longer recorded;
  earlier events keep it. Make a new export with `export create` or the
  report's own export command instead.
- `pk-admin/1` (#1398, the window): a change of meaning, kept on version 1.
  `export status` `expires_at` is now the end of the export's download
  window (60 minutes after the file is published by default, the
  deployment's `export_download_window_minutes`), never later than the
  file's recorded end, and for a `failed` export (it used to be null) the
  end of its Retry window: one window after its latest failed run. Once
  that has passed, `export retry` and `export exact retry` with a new
  request key are exit 1 (`not_available`, where they used to run);
  repeating a recorded key still returns its result. Retrying a cancelled
  export, or a published one, is still `stale_version`.
- `pk-admin/1` (#558, migration 0110): a change of meaning, kept on
  version 1. The financial export's response dates (`first_start=`,
  `first_end=`, `latest_start=`, `latest_end=`) are now days in the time
  zone `--filter zone=…` names, as on the page; a date without it is exit 1
  (`invalid`). Its only readers are Administrator-run assistants.
- `pk-admin/1` (#463 PR 11b): additive. `followup roster-entered` and
  `followup roster-bulk`, the Entered in ParishSoft tick and Roster changes
  to enter's bulk mark, and `followup information-show` and `followup
  information-update`, an Additional information request's page and Save.
- `pk-admin/1` (#463 PR 11c): additive. `followup export [--ministry DUID]
  [--action A] [--state S] [--outcome O] [--sort S] [--format
  csv|xlsx|pdf] [--timezone ZONE]`, the Ministry follow-up queue's Export
  these requests (full scope, the page's `ministry_followup_exported`),
  streamed as `export roster` is.
- `pk-admin/1` (#1404 PR 6b): a deliberate change, kept on version 1.
  `content list` and `content show` print the text state `edited` where
  they printed `custom`, the word Pages and emails now shows (Edited; it
  was Customized). `empty` and `default` are unchanged. The only readers
  are Administrator-run assistants.
- `pk-admin/1` (#1547, 2026-10-17): a change of meaning, kept on version 1.
  No Admin action checks a sign-in's age any more, so the reason
  `fresh_authentication_required` is gone: a read-only or no-longer-live
  automation session at a fresh-gated (privileged) command is now exit 1
  with reason `denied` in its durable log entry, where it used to report
  `fresh_authentication_required`. The catalog's `fresh_gated` flag, the
  `automation_fresh_gate` event and the `fresh_gated` and `irreversible`
  notices keep their names and now mark a privileged command an automation
  session ran.
- `pk-admin/1` (ADM-13 PR 4a, #530): additive. `system mail-clear-preview
  [--no-wait]` (asks for a mailbox check, records no event) and `system
  mail-clear --token TOKEN` (always prompts; its event), Clear the halt
  ahead of System health's button. Neither needs a recent sign-in (#1547).
- `pk-admin/1` (ADM-13 PR 4b, #530): additive. `system mail-clear` also
  acknowledges the halt's critical-problems banner entries the preview saw,
  as System health's Clear the halt does.
- `pk-admin/1` (#1562, migration 0116): a change of meaning, kept on
  version 1. `delivery list` follows Sent mail: `--search` is replaced by
  `--recipient`, which also takes an email address; `--purpose`, `--mode`,
  `--from`, `--to` and `--zone` are new, `--state` gains `needs_attention`,
  `not_delivered` and `still_sending`, and the list now includes
  operational and security notices. The document adds `purpose`, `mode`,
  `start` and `end`; each row keeps its `mode` and `attempt`. Its only
  readers are the Administrator and the operator, who keep no legacy forms.
- `pk-admin/1` (#1622): additive. `users email-leaders --preview` and
  `--confirm`, the Users page's Email Ministry leaders review and Apply
  (full scope, the page's audit events and `admin_cmd_users_email_leaders`).
