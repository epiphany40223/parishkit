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

## Output changelog

- `pk-admin/1` (ADM-11 PR 2): the first version, with the session commands.
