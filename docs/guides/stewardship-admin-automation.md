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
[Census changes download](#census-changes-download), and `export download`, see
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
`send history` Outgoing mail's, `system health` System health's);
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
value Outgoing mail's send filter takes), `number`, `mode`, `cycle`,
`scheduled_at`, `replaced`, `current`, `earlier`, `live`, `cancelled` and
`minutes`.

### `schedule show`

| Field | What it holds |
| --- | --- |
| `campaign_id`, `editable` | The campaign, and whether its dates may still change |
| `version` | What a schedule change is previewed against |
| `window` | `start_date`, `end_date`, `timezone` |
| `schedules` | Each schedule's `id`, `kind`, `date`, `time`, `weekday`, `subject`, `template_version`, `comparison` (whether a daily digest includes Compared with past campaigns), its first resolved send times (`resolved`: `key`, `due_at`) and `more` |

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
page as a document: `problems` (each `kind`, the `subjects` it is about,
each a `service`, `process` and `target`, and the time `at` its sentence
states; a condition in several processes is one problem), the
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
last backup finished and its size, whether it will also be copied
off-site, that it follows the usual retention, and that it waits while a
Family email send is in progress. It is refused (exit 1, `stale_version`) while a
request is waiting or running or a restore is under review. The session
stands in for the page's recent sign-in. `--request-key UUID` (a version 4
UUID, written to standard error when not given) names the request, so a
repeat returns the first one (`created` false). It prints `request_id`,
`created`, `status` (as the Backups panel words it; `waiting` until the
server's five-minute check picks it up) and `created_at`, and records
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
tick box, "I checked this in ParishSoft, and the change is real". The
session stands in for the page's recent sign-in. It records the acceptance
(the token's key names it, so a repeat returns the first, `created` false),
asks for a full refresh as **Refresh now** does, and records
`source_drop_accepted` and `admin_cmd_system_refresh_accept`. Like the
page, it also acknowledges that refusal's own entry in the critical-problems
banner (`critical_events_acknowledged`, with the acceptance as its subject),
so `events list` no longer lists it; other entries stay. It prints
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
which turns debug logging off in every service within about a minute,
until the server operator clears it offline (`pk-stewardship
debug-off-clear`, in the
[deployment runbook](stewardship-deployment-runbook.md#turn-off-debug-logging-530)).
It needs a full-scope session and always prompts (or takes `--yes`),
showing the page's preview: the services with debug logging on, that lines
already written stay in the logs, that the server's setting still says on,
and that only the server operator clears the switch. It is refused (exit 1,
`stale_version`) while the switch is set. The session stands in for the
page's recent sign-in. `--request-key UUID` (a version 4 UUID, written to
standard error when not given) names the switch, so a repeat returns the
first one (`created` false). It prints `switch_id`, `created`, `set_at`
and `active` (whether that switch is still set), and records
`debug_logging_turned_off` and `admin_cmd_system_debug_off`. Follow it with
`system health --watch 60` (each process's `debug_logging`).

```text
pk-admin system debug-off
```

### Watching and errors

`--watch SECONDS` (2 to 300) on `task show`, `send progress`,
`go-live progress` and `system health` prints one document per poll,
`final` false until the last. It stops with exit 0 once the task has
finished, once no send is in progress or about to start, once System
health lists no problem, or once Production preparation is complete,
absent or its task has stopped (a failed task waits for the page's retry,
and `retry_available` says so). It stops with exit 7 and the last state
after `--timeout` seconds (default and maximum three hours;
`watch_timeout`), and with exit 5 if the session ends.
Ctrl-C (or TERM, or a closed terminal or SSH session) stops the watch inside
the web container too: `pk-admin` sends it SIGINT there, waits up to 10
seconds for that stop and reports it if it fails or finds no watch, shows
the watch's last document (`watch_interrupted`, the last state) if it
arrives within 5 seconds (after Ctrl-C only), and exits 130 (143 for
TERM, 129 for HUP). A second Ctrl-C meanwhile changes nothing. On a host
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
Neither asks for a fresh Google sign-in or a confirmation at the prompt,
because the page asks for neither. `config request show` follows the
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
   campaign's sends have overtaken, is exit 1 (`stale_version`): preview
   again. An altered token, or one for another campaign, is exit 1
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
| `schedules` | A list of entries, each one of the four below. Saved schedules you do not name stay as they are; leaving one out never removes it. |

| Entry | Members |
| --- | --- |
| Change a saved schedule | `id` (from `schedule show`) and any of `date`, `time`, `weekday`, `template_version` and, for a daily digest, `comparison` (`true` or `false`: whether it includes Compared with past campaigns). Members you leave out keep their values. Its `kind` cannot change, so it may not be given. |
| Remove a saved schedule | `id` and `"delete": true`, nothing else. |
| Add a schedule | `kind` (`initial`, `reminder`, `daily_digest` or `weekly_digest`) and the members its kind needs: `date` for `initial` and `reminder`, `weekday` (0 is Monday) for `weekly_digest`, and `time` (in the campaign's time zone) and `template_version` for all. A `daily_digest` may add `"comparison": true`; it is off without it. A `time` may be written in any form the page accepts, such as `09:00:00`, `9:00`, `9am` or `21:00`, and is stored as `HH:MM:SS`. |
| Add repeating reminders | `repeat` (the rule below), `time` and `template_version`, as for one reminder, and nothing else. It adds one ordinary reminder per date the rule gives, as if you listed each one. Every date must fit: unlike the page, which leaves out a date that does not fit, the command refuses the whole change. A problem with one date alone, such as a date after the campaign closes, names that date; a conflict between schedules, such as a date at the same moment as a saved reminder or not after the initial invitation, comes back as a plain `schedules` error, as for reminders listed one by one. A rule that gives no dates or more than 30, or skips a month, is exit 1 (`invalid`). The rule itself is not saved. |

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
waits for `yes` (or takes `--yes`). The session stands in for the page's
recent Google sign-in, so the dashboard shows an automation notice and
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
`--yes`). The session stands in for the page's recent Google sign-in, so
the dashboard shows an automation notice, and System logs show
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
without one it is exit 1 (`not_available`). Pausing, resuming and
resolving held messages stay on the page for now.

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
| `health` | The provider and sender check a resume needs: `ready`, `checked_at` and `expires_at` |
| `pause_available` | Whether the page offers Preview pause: `available` and not paused |
| `resume_available`, `resolve_available` | Whether the page offers Preview resume, or Resolve held messages (a paused, closed campaign); both false unless `available` |
| `resolution` | The Resolve held messages choices, null unless `resolve_available`: `types` (each `kind`, `name`, `held` and `cancellable`), and whether `release`, `cancel` and `clear` are offered |
| `family_recovery` | While paused, the page's overdue Family email counts: `selected`, `coalesced`, `skipped`, `blocked`, `deferred` and `unmaterialized` (null unless `available` and paused) |
| `report_recovery` | While paused, the page's overdue Admin digest counts: `selected`, `slots`, `unmaterialized`, `prepared` and `blocked` (null unless `available` and paused) |

`paused`, `paused_at`, `reason`, `inventory`, `next_due` and `health` print
whatever `available` says; the controls and choices follow the page, which
draws them only when `available` is true. Every number is a count, apart
from the reason; no recipient, message or mail-service response is printed.

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
Each needs a full-scope session and an Administrator, and, like the page,
no recent sign-in. Each works only on a file that nothing uses: a file in
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
| `pages[].state` | `empty`, `default` (the default text) or `custom` |
| `emails[].scheduled` | Whether schedules send this mail type (invitations and reminders) |
| `emails[].singleton` | The confirmation email, which has exactly one text |
| `emails[].revisions` | Its emails: `id`, `subject`, `excerpt` (the start of its text), `used_by` (the schedules that send it, named as on Dates and mail schedules), `deletable`, `state` (`default` or `custom`) and `resave` |
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
configuration is gone is exit 1 (`not_available`). Editing and deleting
content stay on the page for now.

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

## Parish, Ministry, logo and Share option settings

`parish show`, `ministries list`, `branding show` and `campaign shares`
are the reads of **Parish settings**, **Ministries**, **Parish logos**
and **Share options**. Like the pages, they need an Administrator; a
session of any scope may run them, and they record nothing in System
logs, as opening the pages records nothing. Each prints `version`, the
applied configuration's version. Changing the parish profile is
`parish preview` and `parish confirm` (below); changing the other settings
stays on the pages for now.

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
| `ministries` | Every Ministry, by name as on the page: its ParishSoft `duid`, `name`, `active` (offered to parishioners) and `included` (in the current campaign) |
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
full-scope session, and asks for no fresh sign-in or confirmation, as the
page asks for none. Fix the cause first, as the page says.

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

Every command for an action that needs a recent Google sign-in on its page,
and every command whose page asks you to type a value or tick an
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

No command of this release does an action that needs a recent Google
sign-in; they arrive in later releases. When a session stands in for one
(pausing or resuming mail, a chosen-Family test, a financial export, the
Family portal switch, confirming Production, withdrawal, key changes),
System logs record an
`automation_fresh_gate` event beside the action, and the dashboard shows an
automation notice; confirming Production and withdrawal are marked
irreversible and also email and post to Slack.

## Outgoing mail

The delivery commands read Outgoing mail and Refused addresses as their
pages do, and resolve a delivery as its page's form does. Each read records
the pages' view event in System logs; any session may run them.
`delivery resolve` needs a full-scope session. No document names a
recipient: there is no email address, Family DUID or Family id, and a
resolution note's text stays on the page. To see who a delivery was for,
open it on the page.

```sh
pk-admin delivery list --state delivery_unknown
pk-admin delivery list --search 1234
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

`delivery list` takes the page's `--state`, `--send` (send history's `send`
value), `--search` (an exact Family DUID or delivery id, as input only),
`--page`, `--size` and `--sort`. `--sort` takes the page's tokens:
`changed` (when the email last changed; the default is `-changed`, latest
first), `name`, `duid`, `purpose`, `mode`, `state` and `attempts`, and also
`created`. `delivery refusals` takes `--duid`, and its `--sort` takes
`refused`, `name` (the Family's name, as `delivery list`), `duid`
(the default), `address` and `id`.

| Field | What it holds |
| --- | --- |
| `deliveries`, `delivery` | Each message's `id`, `campaign_id`, `purpose`, `mode`, `state`, `version`, `attempt`, `task_id`, `created_at`, `updated_at`, `finished_at` |
| `refused` | `delivery list`, in each of `deliveries`: how many of the email's refused addresses are still unresolved, as Outgoing mail marks it (0 for none) |
| `state`, `send`, `page`, `size`, `sort`, `has_next`, `matching`, `matching_capped` | `delivery list`: the filters and the page of rows |
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
for `yes`; it needs a full-scope session of an Administrator, but no recent
sign-in, as on the page. When the address is the Family's only one left, the
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
byte for byte). It takes the filters above, `--format` (`csv`, the default,
`xlsx` or `jsonl`; CSV and XLSX have the same columns, under plain
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
| `report participation` | Participation | `status` (`current`, `updating` or `unavailable`), `fact_set_id` (for `export create --fact-set`), the figures' source and dates, `statistics` (the active Families, Members, email, responses and, with Financial stewardship, pledge totals, including the comparison pledges of every Family, `comparison_pledge_all`) and `days`, the daily table |
| `report comparison` | Campaign comparison | `day` and `date` (the comparison day: the current campaign's last complete day, or `--day`), `status`, `last_year`, `current` (the current campaign's figures at the day and so far, and its renewal of last year's Families), `years` (each imported year's figures at the day and final figures, renewal and, for a final year, its pledge distribution) and `averages` (the imported years' mean of each figure at the day); see below |
| `report responses` | Response dashboard | `metrics`: the funnel `stages`, the three figures beside it, the `activity` buckets, the `sends` and each list's length (`lists`); null in Testing without a rehearsal |
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
- `report ministry`: `--activity`, `--sort`, `--page` and `--size` for the
  summary; `--ministry DUID` with `--requests join` or `--requests leave`
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
rows stay on the page and its downloads. It records the page's "viewed"
event with a count of 0.

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

`census resolve` is a Census changes row's **Record outcome**: Entered in
ParishSoft (`entered`), Ignore (`ignored`) or Reopen (`reopened`). Like the
page's buttons it needs a full-scope session; Administrators and Staff may
record Entered and Ignore, and only an Administrator may reopen an ignored
change (Staff get `denied`). It needs no recent sign-in, as on the page.
Entered in ParishSoft cannot be undone, so, as the page's dialog does,
`--action entered` shows the change's id and version and asks for `yes`
(see [Confirmations](#confirmations)), or takes `--yes`; Ignore and Reopen
ask nothing.

```sh
pk-admin census resolve PROPOSAL_ID --action entered --expected-version 3
pk-admin census resolve PROPOSAL_ID --action reopened --expected-version 4 \
    --note - --yes < note.txt
```

`PROPOSAL_ID` and the `--expected-version` are the change's id and version.
No command or download prints them yet: they are only in the hidden fields
of the change's row form on the Census changes page (the page's downloads
leave them out). `export census` will print them. `--note` is optional,
except to reopen; `-` reads it from standard input, which keeps a note that
names people out of shell history and the process list, and then needs
`--yes`, because standard input cannot carry both the note and an answer. `--request-key` is made for you and
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

A report export (the Participation page's **Export**, and the export of
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

- `export create` requests a Participation export, as the page's export
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
- `export cancel`, `export retry` and `export regenerate` are the status
  page's buttons: cancel an export that is not ready yet, run a failed one
  again once its cause is fixed, or request an expired one again from the
  figures it was made from (a new export, with a new `EXPORT_ID`).
  Regenerating a Family directory, mail-merge or financial export needs a
  recent Google sign-in on the page, so for those `export regenerate` asks
  at the [confirmation prompt](#confirmations) (or takes `--yes`); the
  session stands in for the sign-in, and System logs show
  `automation_fresh_gate` beside it.
- `export fetch` saves a ready export's file on the host (see below).
- `exports clean` deletes every fetched file.

`export create`, `export retry` and `export regenerate` take
`--request-key`, as [task retries](#task-retries) do: pass your own UUID,
or the command makes one and writes it to standard error before it acts.
Repeating a command with the same key, or cancelling an export already
cancelled, returns the same export and changes nothing (`created` is
false). Exit 6 (`outcome_unknown`) names the key in `error.request_id`:
read the export with `export status`, or repeat the command with the key.

While other work holds the export's campaign (a source refresh being
promoted, for example), the status page turns its buttons off, and
`export status` shows `changes_available` false. Then `export create`,
`export cancel`, `export retry` and `export regenerate` are exit 1
(`denied`). That refusal is temporary, not a permissions problem: try again
later.

| `export status` field | What it holds |
| --- | --- |
| `id`, `campaign_id`, `report`, `format`, `created_at` | The export |
| `state` | `queued`, `running`, `retry_wait` or `abandoned` (still being made; the worker picks an abandoned run up again), `failed`, `succeeded` (made, file not yet published), `ready`, `expired` or `cancelled`, as the page shows |
| `changes_available` | Whether the page's buttons may act now; false while other work holds the campaign |
| `can_cancel` | Whether the page offers **Cancel** now |
| `expires_at` | When a ready file is deleted |
| `file_name`, `content_type`, `size`, `sha256`, `count` | The file: its name, type, size in bytes, SHA-256 and number of rows (null until it is published; an expired export keeps them) |

`export create`, `export cancel`, `export retry` and `export regenerate`
print `created`, `request_key` (none for a cancel) and `export`, the
export's `export status` fields afterwards (for `export regenerate`, the
new export's). An unknown export or `--fact-set`, figures that are not
ready, or a file that has expired is exit 1 (`not_available`). Cancelling a
ready export, retrying one that has not failed, or regenerating one that has
not expired is exit 1 (`stale_version`): read it again. A key already used
for a different export is exit 1 (`invalid`). System logs show the page's
events (`export_requested`, `export_cancelled`, `export_downloaded`) and,
for each change, `admin_cmd_export_create`, `admin_cmd_export_cancel`,
`admin_cmd_export_retry` or `admin_cmd_export_regenerate`, attributed to
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
- `export financial` needs a recent Google sign-in on its page, so it asks
  at the [confirmation prompt](#confirmations) (or takes `--yes`); the
  session stands in for the sign-in, and System logs show
  `automation_fresh_gate` beside it. A read-only session is exit 1
  (`denied`).
- `--format` is `csv`, `xlsx` or `pdf`; `--timezone` the time zone of the
  file's times. Each takes `--request-key`, as `export create` does.
- `--filter NAME=VALUE` (repeat for more) is one of the page's filters,
  named as its form names it, for example `amount=nonzero`, `active=active`
  or `frequency=monthly` for the financial export, `disposition=all` or
  `needed=yes` for the information export, and `activity=active` (or,
  with `--ministry`, `state=unresolved`) for the Ministry export. Dates
  (`start=` and `end=` on the information and Ministry exports) are days in
  a time zone, so `zone=` is required with them, for example
  `--filter start=2026-11-01 --filter zone=America/New_York`. A filter
  the page would refuse is exit 1 (`invalid`), and so are `page` and
  `size`, which the page's export form never sends. A search is refused:
  what you type there names a Family, and it would stay in your shell's
  history.
- A Ministry the campaign does not offer is exit 1 (`invalid`). When an
  export cannot be requested right now (its inputs are being refreshed,
  for example), the command exits 3 (`unavailable`): try again.

#### Family codes and mail merge

`export directory` is the Family directory's export of Family codes, for
the current campaign, and `export postal` its mail merge. Each needs a
full-scope session and records the page's events, plus
`admin_cmd_export_directory` or `admin_cmd_export_postal`. Both need a
recent Google sign-in on the page, so each asks at the
[confirmation prompt](#confirmations) (or takes `--yes`); the session
stands in for the sign-in, and System logs show `automation_fresh_gate`
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

When the Participation page has no complete figures ready yet, its exact
export calculates today's figures first and then makes the file. From the
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
for Administrators. Because the file holds the Family's code, the page asks
for a recent Google sign-in, so the command asks at the
[confirmation prompt](#confirmations) (or takes `--yes`), and System logs
show `automation_fresh_gate` beside it; regenerating one asks the same.

```sh
pk-admin export family-timeline FAMILY_RECORD_ID --format pdf \
  --timezone America/New_York
pk-admin export family-timeline FAMILY_RECORD_ID --mode testing \
  --format csv --timezone America/New_York
```

`FAMILY_RECORD_ID` is the id in the Family's timeline page address,
`/admin/reports/families/FAMILY_RECORD_ID/`. `--mode testing` is
the page's Testing rehearsal view; such an export cannot be regenerated once
that rehearsal has ended, so export it again instead. It takes `--request-key` like the other
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
a full-scope session in place of the page's recent sign-in. It records
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

The Limitations download is not on the command line yet; use the page. The
lists of Families behind the response counts are `export directory` with
the directory's Response filters.

### Census changes download

`export census` writes the page's download of **Census changes** to
standard output, and its document (`campaign_id`, `file_name`,
`content_type`, `format`, `timezone`, `size`, `sha256`, `count`) to
standard error, as `logs export` does. It needs a full-scope session, as
every export does; Administrators and Staff may run it, and a Ministry
leader is refused (`denied`). It takes `report census`'s filters, without
the page's search, `--format` (`csv`, the default, `xlsx`, `pdf` or
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
same text under the keys `family`, `family_duid`, `who`, `member_duid`,
`what`, `parishsoft_now`, `family_answer`, `edited`, `route`, `status`,
`submitted`, `recorded_by`, `recorded_at` and `note`, plus `proposal_id`
and `version`, which recording a row's outcome needs and the page's files
leave out, and `actions`: the outcomes you may record on that row now
(`entered` and `ignored` on a By-hand change still open, `reopened` on an
ignored one, for Administrators only, and only with a `--note` saying
why). `actions` is empty while other campaign work holds the campaign, as
the page then offers no Record outcome. The file names Families and
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
one, regenerate an expired one, or create a new one for a cancelled one.
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
  `delivery list --sort` follows the reworked Outgoing mail page: the
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
  Hosted files page's Delete and Edit (full scope, Administrators only, no
  recent sign-in; `files delete` prompts; their events).
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
- `pk-admin/1` (#1161): additive. `export comparison` no longer needs
  `--timezone`; without it the file uses the parish's time zone.
- `pk-admin/1` (#1211): additive. `delivery bounce-report MESSAGE_ID
  --address ADDRESS --note TEXT|- [--request-key KEY] [--yes]`, the Mail
  message page's Report a bounced address (full scope, Administrators only,
  prompts; `recipient_bounce_reported` and
  `admin_cmd_delivery_bounce_report`), and `kind` (`provider` or
  `reported_bounce`) in `delivery refusals` rows and `delivery
  refusal-show`.
