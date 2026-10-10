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
(`logs export`, see [System logs](#system-logs), and `export download`, see
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
| `schedules` | Each schedule's `id`, `kind`, `date`, `time`, `weekday`, `subject`, `template_version`, its first resolved send times (`resolved`: `key`, `due_at`) and `more` |

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
waits (`delivery_paused`, `planning_held`, `retry_waiting`), the open
`incidents`, the ParishSoft `refresh` outcome, a refused load's
`refused_counts`, the newest backup and the `offsite` copy's state. The
pause's reason and who paused it, and the page's sentences, are not in it.

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
they may still change, the campaign dates, through the same review and
confirmation as the Dates and mail schedules page. Both need a full-scope session.
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
     "template_version": "EMAIL-ID"}
  ]
}
```

| Member | What it holds |
| --- | --- |
| `window` | Any of `start_date`, `end_date` (`YYYY-MM-DD`), `timezone` and `overlap_confirmed` (true or false, only for a campaign with a financial period). Members you leave out keep their values. Changing the dates once they are locked is exit 1 (`stale_version`), as on the page. |
| `schedules` | A list of entries, each one of the three below. Saved schedules you do not name stay as they are; leaving one out never removes it. |

| Entry | Members |
| --- | --- |
| Change a saved schedule | `id` (from `schedule show`) and any of `date`, `time`, `weekday`, `template_version`. Members you leave out keep their values. Its `kind` cannot change, so it may not be given. |
| Remove a saved schedule | `id` and `"delete": true`, nothing else. |
| Add a schedule | `kind` (`initial`, `reminder`, `daily_digest` or `weekly_digest`) and the members its kind needs: `date` for `initial` and `reminder`, `weekday` (0 is Monday) for `weekly_digest`, and `time` (in the campaign's time zone) and `template_version` for all. A `time` may be written in any form the page accepts, such as `09:00:00`, `9:00`, `9am` or `21:00`, and is stored as `HH:MM:SS`. |

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
schedule from 0, or `window` or `schedules` for a problem between fields),
`code` (`required` or `invalid`) and `message`, the page's text.

| `schedule confirm` field | What it holds |
| --- | --- |
| `created` | False when this token was confirmed before: the original request is returned and nothing changes |
| `request` | The request, as `config request show` prints it |

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
`--timezone`: the preview also requests a CSV export of each DUID with the
name the page shows, and `export fetch` downloads it. Delete the file once
used.

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
while an earlier test's outcome is unknown, `delivery resend` and
`delivery refusal-clear`; the catalog's `prompts` flag
names every one. The command writes what it is about to do to standard error and waits for `yes`, or for the page's typed
value (`Production` for the Production confirmation). Anything else, or the
end of input, changes nothing and exits 4 (`confirmation_required`). The
wrapper passes one line typed at your terminal to a prompting command, so
run it from a terminal, or pass `--yes` in a script; `--yes` is required when the
command also reads an input from standard input (`-`). Logs record whether
the answer came from the prompt or from `--yes`.

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
```

`delivery list` takes the page's `--state`, `--send` (send history's `send`
value), `--search` (an exact Family DUID or delivery id, as input only),
`--page`, `--size` and `--sort`. `--sort` takes the page's tokens:
`changed` (when the email last changed; the default is `-changed`, latest
first), `name`, `duid`, `purpose`, `mode`, `state` and `attempts`, and also
`created`. `delivery refusals` takes `--duid`.

| Field | What it holds |
| --- | --- |
| `deliveries`, `delivery` | Each message's `id`, `campaign_id`, `purpose`, `mode`, `state`, `version`, `attempt`, `task_id`, `created_at`, `updated_at`, `finished_at` |
| `state`, `send`, `page`, `size`, `sort`, `has_next`, `matching`, `matching_capped` | `delivery list`: the filters and the page of rows |
| `task` | `delivery show`: the latest task's `id`, `state`, `version` and `retry_sequence` |
| `actions`, `retry_unavailable` | `delivery show`: the resolutions the page offers now, and whether a retry is refused by the campaign's resend rule |
| `events` | `delivery show`: the history's `version`, `at`, `state`, `action`, `attempt` and `result` (a stored code) |
| `notes` | `delivery show`: each resolution's `created_at` and `action` |
| `refusals` | `delivery refusals`: each unresolved refusal's `id` and `created_at` |
| `id`, `created_at`, `resolved`, `source`, `can_clear` | `delivery refusal-show`: the refusal, how and when it was resolved (`kind`, `at`), the source version to verify (`snapshot_id`, `generation`), and whether a clearance can be recorded now |

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
(umask 077; pk-admin logs export --show audit --format jsonl > logs.jsonl)
```

The filters are the page's: `--show` (`debug`, `info`, `warning`, `error`,
`critical` or `audit`, repeated for more; without it, every level but debug
plus audit records, as the page first shows), `--event` (one type, exactly),
`--actor`, `--correlation`, `--campaign` and `--subject` (UUIDs; a campaign
or subject keeps only audit records), `--text` (the page's search phrase,
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
entries at most, to standard output, byte for byte as the page's file. It
takes the filters above, `--format` (`csv`, the default, or `jsonl`) and
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
counts and totals. They never print a Family's or Member's name, DUID,
contact details or answers; for those rows, use the page or an export. A
session of any scope (read-only included) may run them, and each records the same "viewed" events in System
logs as opening the page does. Each reads the current campaign, as the
report pages do, and prints its `campaign_id`.

```sh
pk-admin report list
pk-admin report participation
pk-admin report responses --mode testing --grain day
pk-admin report financial
pk-admin report talents
pk-admin report information --disposition all --needed yes
pk-admin report ministry
pk-admin report ministry --ministry 9 --requests join --state unresolved
```

| Command | The page | What it prints |
| --- | --- | --- |
| `report list` | Campaign reports | `campaign_id` (null without a current campaign) and `reports`: each report your menu offers, with `command`, `page` and `available` (false while the menu greys it out) |
| `report participation` | Participation | `status` (`current`, `updating` or `unavailable`), `fact_set_id` (for `export create --fact-set`), the figures' source and dates, `statistics` (the active Families, Members, email, responses and, with Financial stewardship, pledge totals, including the comparison pledges of every Family, `comparison_pledge_all`) and `days`, the daily table |
| `report responses` | Response dashboard | `metrics`: the funnel `stages`, the three figures beside it, the `activity` buckets, the `sends` and each list's length (`lists`); null in Testing without a rehearsal |
| `report financial` | Financial report | The summary over every pledge: `families`, `annual_total`, `frequencies`, `shares` (by share option id), `no_share`, `cannot_give` and the comparison period |
| `report talents` | Talents and limitations | `members`, `cannot_serve`, `cannot_attend` and `talents` (by talent key) |
| `report information` | Additional information | `matching`: how many items match the filters |
| `report ministry` | Ministry report | `summaries`: each Ministry's DUID (`ministry`), name, state and request counts; with `--ministry` and `--requests`, `matching` is that list's request count |

The options are the pages' own, and a value the page would refuse is exit 1
(`invalid`):

- `report participation`: `--scope` (`historical`, the default, or
  `current`).
- `report responses`: `--mode` (`production`, the default, or `testing`)
  and `--grain` (`auto`, `hour` or `day`).
- `report information`: `--disposition` (`current_actionable`, the
  default, `superseded`, `withdrawn` or `all`), `--needed` and
  `--completed` (`any`, `yes` or `no`), and `--start` and `--end` (days).
- `report ministry`: `--activity`, `--sort`, `--page` and `--size` for the
  summary; `--ministry DUID` with `--requests join` or `--requests leave`
  counts one Ministry's list, and takes `--state`, `--history` (`current`
  or `all`) and `--start` and `--end`.

No command takes a search: searches match names. A report the campaign does
not include (Financial or Ministry stewardship), or no current campaign, is
exit 1 (`not_available`). In System logs, where the page's event counts
the rows it showed, the command's counts 0: it shows none.

## Digests

Each emailed daily or weekly report links to a saved copy on the portal,
`/admin/reports/daily-digests/SNAPSHOT_ID/` or
`/admin/reports/weekly-digests/SNAPSHOT_ID/`. The `digest` commands read
that saved copy, which never changes, and record the same "viewed" events
as opening it. A session of any scope (read-only included) may read them;
a weekly report needs an Administrator, as its page does.

```sh
pk-admin digest daily SNAPSHOT_ID
pk-admin digest weekly SNAPSHOT_ID --page 2
pk-admin digest weekly-request --yes
```

- `digest daily` prints the saved figures: `participation` (the figures'
  `fact_set_id`, source and dates), `statistics` (the active Families,
  Members, email, responses and, with Financial stewardship, pledge
  totals) and `days`, the daily table.
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

- `export financial` is the Financial report's export; `export
  information` the Additional information page's, with `--history` for
  each item's workflow history; `export ministry` the Ministry report's,
  for the summary or, with `--ministry DUID --requests join|leave`, one
  Ministry's list; `export ministry-packet` its follow-up packet, for the
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
  with `--ministry`, `state=unresolved`) for the Ministry export. A filter
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

The one-Family timeline, the talents and response list downloads and the
exact daily exports are not on the command line yet; use the pages.

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
- `pk-admin/1` (ADM-11 PR 9c): additive. `delivery resend` and
  `delivery refusal-clear`, which prompt. `delivery show`'s `actions` may
  now include `resend` (it listed only `delivery resolve` actions); an
  offered `resend` is `delivery resend`.
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
