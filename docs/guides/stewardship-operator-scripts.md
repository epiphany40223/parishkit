# Stewardship operator helper scripts

The scripts in [`tools/stewardship-ops/`](../../tools/stewardship-ops/) are
the operator's helpers for releases, upgrades and large sends (#459). They
were first written during the launch in a temporary directory on the
operator's laptop, which was wiped on launch morning; they live in git so
that cannot happen again and so other operators can review and use them.

Each script's header comment is its full description, including its
options, settings and exit statuses: run it with `-h`. This guide says when
to reach for each one and links to the procedures it supports.

## Settings and safety

The scripts read the host, deployment and repository from environment
variables and never name them: this repository is public. Keep your own
values in a file outside the checkout and source it before running a
script, for example:

```sh
# ~/.config/parishkit/stewardship-ops.env (not in git)
export STEWARDSHIP_HOST=HOST          # ssh destination
export STEWARDSHIP_UUID=UUID          # deployment UUID (the upgrade script)
export STEWARDSHIP_TIMEZONE=ZONE      # optional: times in the parish's zone
```

`predeploy-check.sh`, `send-monitor.sh` and `send-report.sh` run psql in
the deployment's database container over ssh, as the backup runbook's
queries do:
`ssh "$STEWARDSHIP_HOST" docker exec -i PROJECT-postgres-1 psql -U pk_stewardship_operator -d DATABASE`.
`pk_stewardship_operator` is the operator (superuser) login. The scripts
stay read-only only because each of their queries runs in a
`BEGIN READ ONLY` transaction, or, for the send report, in a transaction
that is rolled back and read-only after its temporary view. Every query
also runs under a `statement_timeout`. The shared settings, such as the
Compose project and database names, are listed in
[`lib.sh`](../../tools/stewardship-ops/lib.sh).

Every wait, and every ssh, gh and networked git call, has a limit. When
one passes, the script says what it was waiting for, the limit and the
time elapsed, appends that line to `STEWARDSHIP_OPS_LOG` (default
`~/.local/state/parishkit/stewardship-ops.log`) and exits with status 3.
A refusal or failure exits with 1, a usage error with 2, and Ctrl-C with
130 at once, even during a limited call.

## Releases

- [`tools/stewardship-ops/ci-watch.sh`](../../tools/stewardship-ops/ci-watch.sh)
  watches one GitHub Actions run and returns at the first failed job,
  rather than when the slowest shard finishes, or at a merge conflict on
  the pull request named with it.
- [`tools/stewardship-ops/release.sh`](../../tools/stewardship-ops/release.sh)
  publishes a release once its version bump has merged. It checks the
  version on main's head, finds the full CI run that `release.yml` will
  accept as its
  [release evidence](stewardship-test-efficiency.md#release-evidence) (a
  `CI (jobs: all)` run on the same tree, or one differing only in
  documentation: the run you name, an existing one such as the nightly train
  head's, or, when none passed or is running, one it dispatches with all
  jobs), pushes the annotated tag after you type its name, and
  prints the published image digest for the
  [scripted upgrade](stewardship-deployment-runbook.md#scripted-upgrade).
  Pushing a release tag needs a human's explicit authorization; running
  the script is that act.

## Upgrades

- [`tools/stewardship-ops/predeploy-check.sh`](../../tools/stewardship-ops/predeploy-check.sh)
  is the read-only look before
  [upgrade](stewardship-deployment-runbook.md#upgrade) step 1. It fails
  while a task run is running or a message is being submitted, which
  stopping the background services would interrupt, and lists the Families
  active in the last 15 minutes, for choosing the moment: `web` is briefly
  down during an upgrade.
- Bulk Family send: the
  [scripted upgrade](stewardship-deployment-runbook.md#scripted-upgrade)
  carries the deployment's current bulk switch over, so no option is
  needed to keep it on. On a pre-launch deployment,
  `STEWARDSHIP_BULK_FAMILY_SEND=1 tools/stewardship-dev-deploy.sh` renders
  it on, as the
  [Family mail dispatch](stewardship-family-mail-dispatch.md#turning-on-the-bulk-family-send)
  procedure does by hand; that script refuses any deployment already in
  Production.

## Large sends

- [`tools/stewardship-ops/send-monitor.sh`](../../tools/stewardship-ops/send-monitor.sh)
  prints one line per minute while a send runs: the messages of one
  purpose and mode by state, how many were accepted in about the last
  minute, the first and last acceptance, and the deadlock and error lines
  the deployment's containers logged. Start it before or during the send;
  it stops once a poll that saw unfinished work is followed by one with
  none. The Administrator's view of the same send is **Family email
  progress**; see the launch runbooks'
  [measuring the launch send](stewardship-launch-runbooks.md#measuring-the-launch-send).
- [`tools/stewardship-ops/send-report.sh`](../../tools/stewardship-ops/send-report.sh)
  runs the [mail send report](stewardship-mail-send-report.md#running-it).
  It reads the SQL from that guide each time it runs, so the guide stays
  the only copy, and refuses if the guide's report section no longer holds
  one block from `BEGIN;` to `ROLLBACK;`.

## Admin automation

- [`tools/stewardship-ops/pk-admin`](../../tools/stewardship-ops/pk-admin)
  runs on the deployment host itself, not over ssh: the Admin automation
  command line's wrapper, which keeps each session secret in an owner-only
  file and runs `pk-stewardship admin` in the web container. Unlike the
  scripts above it is POSIX `sh` and needs nothing on the host beyond
  Docker, `openssl` and the standard tools. See the
  [Admin automation guide](stewardship-admin-automation.md).

## Deferred

The py-spy profiling aggregation helpers used for #447 are not kept here
yet; #459 lists them as optional.
