# Stewardship Admin automation: operator guide

The Admin automation command line lets an operator, a script or an AI
assistant on the deployment host run Admin commands as a named
Administrator, without clicking through pages. The
[Admin automation specification](../specs/stewardship/admin-automation/spec.md)
defines it; this guide says how to use it. It is delivered in steps
([ADM-11](../tasks/stewardship/admin-portal.md#adm-11-admin-automation-interface)).
This version has the session commands only: `login start`, `login wait`,
`logout`, `whoami`, `sessions` and `commands`. Status, schedules and the
other areas follow in later releases, each listed in the command catalog.

Anyone who can run Docker on the host already controls the deployment; the
command line adds no limits on top of that, only notices and audit (see the
specification's [threat model](../specs/stewardship/admin-automation/spec.md#threat-model)).
Treat a full-scope session file like the Administrator's own signed-in
browser.

## Before the first session

The host wrapper is
[`tools/stewardship-ops/pk-admin`](../../tools/stewardship-ops/pk-admin), a
POSIX shell script; copy it to the host (it needs `docker`, `openssl` and
standard tools such as `stat`, `base64`, `head`, `mktemp` and `tee`, and no
Python). Run it as the operator account that runs
Docker Compose, never as the application user (10001).

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
`pk-admin/1`) on standard output; warnings, such as a session with less than
72 hours left, and structured logs go to standard error. The document's
fields, the exit codes and the error codes are in the specification's
[running a command](../specs/stewardship/admin-automation/spec.md#running-a-command);
`pk-admin commands` prints the catalog of every command with its scope,
options and result fields.

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
- A session ends by itself at its deadline, when its Administrator loses the
  Administrator role or is disabled or removed, and after an offline
  Admin-access recovery.
- Using a session file from another host ends it (`host_mismatch`).

## Notices and alerts

Approvals and refused uses go out by email and, when configured, Slack, as
the fixed-text alerts `automation_approved` and `automation_refused` (see
the
[operational alerts guide](stewardship-operational-alerts.md#automation-incident-kinds)).
If a session nobody recognizes appears, the only way to end it from the
host in this release is `revoke-automation-sessions --reason
revoked_by_operator` (below), which ends **every** session at once and
needs every online service stopped.

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
`send history` Outgoing mail's); `schedule show` records none, like its
page.

```sh
pk-admin status
pk-admin task list --state failed --size 20
pk-admin task show TASK_ID --watch 5
pk-admin send progress --watch 10
pk-admin send history
pk-admin schedule show
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
| `source` | The last ParishSoft refresh: `refreshed_at`, the last full refresh's success, failure (with `full_failed_task_id`) and `full_running`, incremental success and failure, `frequency`, `next_full_at`, `delta_refresh` |
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
(`stale_version`), as on the Mail schedules page.

### Watching and errors

`--watch SECONDS` (2 to 300) on `task show` and `send progress` prints one
document per poll, `final` false until the last. It stops with exit 0 once
the task has finished or no send is in progress or about to start. It stops
with exit 7 and the last state after `--timeout` seconds (default and
maximum three hours; `watch_timeout`), and with exit 5 if the session ends.
Ctrl-C stops `pk-admin` at once, but `docker exec` does not pass the signal
on, so the watch inside the web container keeps polling until its timeout,
until the read finishes, or until you revoke the session in Automation
access ([#598](https://github.com/epiphany40223/parishkit/issues/598)); it
changes nothing meanwhile. Run in the container directly, it stops with
exit 7 (`watch_interrupted`) and the last state. `--timeout` without
`--watch` is refused. Only the first poll is recorded in System logs. An unknown task is
exit 1 (`not_available`). A restore under review, or a configuration change
being applied, is exit 3 (`unavailable`), so try again shortly.

`pk-admin commands` lists every command's result fields. The route-parity
ledger, `src/parishkit/stewardship/admin_parity.py`, says which command
covers each Admin page, or why none does yet.

## Output changelog

- `pk-admin/1` (ADM-11 PR 2): the first version, with the session commands.
- `pk-admin/1` (ADM-11 PR 3a): additive. The status commands above,
  `watch` and `arguments` in each catalog entry, and the error code
  `watch_interrupted`.
