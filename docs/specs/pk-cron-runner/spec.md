# pk-cron-runner — scheduled job runner for the other pk-* tools

**Category:** Scheduler / operations. Writes to: local lock files, the JSONL log
file, and Slack (no external service data is changed).

## 1. Purpose & role in ParishKit

`pk-cron-runner` is the operations wrapper the parish IT administrator points
`cron` at instead of invoking the sync/lookup tools directly. It runs an ordered
list of configured commands (typically the other ParishKit tools such as
[`pk-sync-ps-to-ggroup`](../pk-sync-ps-to-ggroup/spec.md),
[`pk-sync-ps-to-cc`](../pk-sync-ps-to-cc/spec.md), and
[`pk-create-ps-ministry-rosters`](../pk-create-ps-ministry-rosters/spec.md))
while adding the safety features bare `cron` lacks: a single-instance lock so two
runs cannot overlap, stale-lock recovery so a crashed run does not block future
runs forever, per-job timeouts that tear down a hung child and its descendants,
captured child output, and an optional Slack summary on failure (or success). It
embodies the "unattended operation with loud failure" intent in
[the system spec](../intro/spec.md#design-intent-and-goals); see also the
[architecture skeleton](../intro/spec.md#architecture-and-data-flow) and the
[tool catalog](../intro/spec.md#tool-catalog).

Unlike the sync tools, this runner does **not** read ParishSoft or write to any
external service, so it never performs reconciliation and never gates on the
dry-run write contract; it orchestrates *other processes*.

## 2. Invocation

- **Console command:** `pk-cron-runner` (declared in `pyproject.toml`
  `[project.scripts]` as `pk-cron-runner = "parishkit.cli:run_main"`).
- **Entry point:** `parishkit.cli.run_main(argv)` lazily imports
  `parishkit.pk_cron_runner.main` and returns its exit code. Note: `run_main`
  does **not** wrap the call in the shared `run_user_facing` error funnel —
  `pk_cron_runner.main` performs its own error handling and exit-code mapping
  (see [Failure modes](#7-failure-modes--exit-codes)).
- **Wrapper script:** `scripts/pk-cron-runner/pk-cron-runner.py` is a
  `#!/usr/bin/env python3` shim that calls `run_main()` and exits with its
  return value, so the tool runs from a checkout without installation.
- **`--version`:** prints `pk-cron-runner <package-version>` (the installed
  `parishkit` version, e.g. `pk-cron-runner 0.1.0`) and exits `0`. This is the
  tool's own short-circuit flag, evaluated before any config/logging work — its
  documented purpose is to "show that the console entry point is installed."

Typical scheduling (operator docs: `scripts/pk-cron-runner/README.md`):

```cron
*/15 * * * * /opt/parishkit/bin/pk-cron-runner --config /opt/parishkit/config/runner.yaml
```

## 3. Command-line options

The tool registers the shared flags via `add_common_arguments`, then adds its
own. For the shared flags' semantics and precedence see
[Shared CLI layer](../intro/spec.md#shared-cli-layer).

**Shared flags that take effect here:** `--config`; `--verbose/--no-verbose`,
`--debug/--no-debug`; `--log-file`, `--log-dir`; `--slack-token-file`,
`--slack-channel`, `--slack-log-level`. These drive `resolve_common_options`
and `setup_logging`.

**Shared flags accepted but unused:** `--dry-run/--no-dry-run` and the
ParishSoft flags `--ps-api-key-file`, `--ps-cache-dir`, `--ps-cache-limit`. They
are parsed and resolved into `CommonOptions` (and their config sections are
validated) but the runner never reads them, because it makes no ParishSoft calls
and is not a mutating tool. The runner therefore never calls
`require_explicit_write_mode`.

**Tool-specific options** (all defined in `build_parser`):

| Option | Type / form | Effect |
| --- | --- | --- |
| `--version` | flag | Print version line and exit 0 (short-circuits everything else). |
| `jobs` (positional) | `nargs="*"` | Names of configured jobs to run; empty = all enabled jobs. |
| `--include-disabled` | flag | Run jobs whose `enabled: false`, including when named explicitly. |
| `--continue-on-failure` | flag | Force `stop_on_first_failure=False` for this run (overrides config). |
| `--lock-file` | path | Override the lock file path (config and default ignored). |
| `--stale-after` | duration string | Override the stale-lock age threshold (e.g. `16m`). |
| `--stale-action` | choice | One of `exit-and-alert`, `remove-and-continue`, `fail-closed`. |
| `--timeout` | duration string | Blanket per-job timeout applied to **every** job this run. |
| `--command` | `argparse.REMAINDER` | Run a single ad-hoc command instead of a config file. |

Notes on the tool-specific flags:

- `--command` consumes the rest of the argv as the child's argv list (argv-style,
  no shell). It is **mutually exclusive with `--config`**: combining them raises
  a `ConfigError` → exit 2. An empty `--command` (no following words) is a usage
  error → exit 2, even if a default config exists (the default job must not be
  used as a fallback).
- Duration strings (`--stale-after`, `--timeout`) are parsed by `parse_duration`
  (see [§4](#4-configuration-schema)). A CLI `--timeout` overrides the timeout on
  *all* jobs; an absent `--timeout` leaves configured per-job timeouts untouched.
- `--lock-file`, `--stale-after`, `--stale-action` each fall back to the
  config-file lock value when not given (`_apply_cli_overrides`).

## 4. Configuration schema

The runner config is one YAML file (template:
`scripts/pk-cron-runner/example-config.yaml`). `load_runner_config(path)` reads
it with `load_yaml_config(path, required=True)` and hands the mapping to
`parse_runner_config(data, base_dir=path.parent)`, which normalizes every scalar
at the boundary into typed dataclasses (`RunnerConfig`, `LockConfig`,
`JobConfig`). Relative `lock.path` and per-job `cwd` are resolved against the
**config file's directory** (`base_dir`), matching the shared
[path-resolution contract](../intro/spec.md#runtime-model-and-installation).

The runner reads these sections. The shared `common`, `logging`, `parishsoft`,
and the Slack credential keys (`token_file`, `channel`, `level`) are consumed by
`resolve_common_options` / `setup_logging` — see
[Configuration system](../intro/spec.md#configuration-system). The runner's
own `parse_runner_config` reads `lock`, `runner`, `slack`, and `jobs`. It does
**not** reject unknown *top-level* keys; only the per-section allowed-key sets
below are enforced (`reject_unknown_keys`).

### `lock` (mapping) — allowed keys: `path`, `stale_after`, `stale_action`

| Key | Type | Default | Rules |
| --- | --- | --- | --- |
| `path` | string path | `default_run_dir()/runner.lock` | Via `_path`; `~` expanded; relative resolved against config dir. |
| `stale_after` | duration | `None` (staleness off) | Via `parse_duration`. `None` → locks never go stale. A YAML value of numeric `0` also disables staleness because `parse_runner_config` stores `None` for falsy parsed values. |
| `stale_action` | choice | `exit-and-alert` | One of `exit-and-alert`, `remove-and-continue`, `fail-closed`. |

### `runner` (mapping) — allowed keys: `stop_on_first_failure`, `notify_success`, `context`

| Key | Type | Default | Rules |
| --- | --- | --- | --- |
| `stop_on_first_failure` | bool | `true` | Strict bool (`_bool_value`); a string like `"false"` is rejected. |
| `notify_success` | bool | `false` | Legacy fallback for `slack.notify_success` (see precedence below). |
| `context` | string | `None` | Optional label prefixed to summary messages. |

### `slack` (mapping) — allowed keys: `token_file`, `channel`, `level`, `notify_success`, `context`, `include_output`

`token_file`, `channel`, `level` are validated here for typo-proofing but
consumed by the shared Slack/logging setup. The runner additionally reads:

| Key | Type | Default | Rules |
| --- | --- | --- | --- |
| `notify_success` | bool | `false` | Preferred source for "notify on success"; falls back to `runner.notify_success`. |
| `context` | string | `None` | Used only if `runner.context` is unset (`runner.context` wins). |
| `include_output` | bool | `false` | When true, failed jobs' captured stdout/stderr are embedded in the Slack failure summary (redacted). Maps to `RunnerConfig.include_output_in_slack`. |

Cross-section resolution in `parse_runner_config`:

- `notify_success = slack.notify_success` if present, else `runner.notify_success`,
  else `False`.
- `context = runner.context or slack.context` (runner section wins).

### `jobs` (list) — each item is a mapping; allowed keys: `name`, `command`, `enabled`, `cwd`, `env`, `timeout`

| Key | Type | Default | Rules (`_parse_job`) |
| --- | --- | --- | --- |
| `name` | string | required | Non-empty string; must be **unique** across jobs (`_validate_unique_job_names`) or `ConfigError`. |
| `command` | list[str] | required | Non-empty list of strings (`_string_list`); an argv list, never a shell string. |
| `enabled` | bool | `true` | Strict bool; disabled jobs are skipped unless explicitly included. |
| `cwd` | string path | `None` | `~` expanded; relative resolved against config dir; passed to the child as its working directory. |
| `env` | map[str,str] | `{}` | Must be a mapping of string→string; layered over the inherited environment. |
| `timeout` | duration | `None` | `parse_duration`; `None` = wait indefinitely. |

### `parse_duration` (used by `lock.stale_after`, `job.timeout`, `--stale-after`, `--timeout`)

- `None` or `""` → `None`.
- `bool` → `ConfigError` (bool is a subclass of int and must not be accepted).
- `int`/`float` → seconds; must be `>= 0`, else `ConfigError`. For YAML
  `lock.stale_after`, numeric `0` is parsed successfully but then treated as
  unset (`None`) by `parse_runner_config`, so locks never go stale. In direct
  `LockConfig` construction, `timedelta(seconds=0)` means immediately stale.
- `str` → last char is the unit (`s`=1, `m`=60, `h`=3600, `d`=86400); the rest
  must be all digits with integer value `>= 1`. So `"30m"`, `"16m"`, `"7d"` are
  valid; `"0s"`, `"1.5m"`, `"abc"`, `"m"` raise `ConfigError`.
- Other types → `ConfigError`.

### `--command` (single-command) mode config

When `--command` is used, `_single_command_config` builds a `RunnerConfig`
without a YAML file: one `JobConfig(name="command", command=args.command,
timeout=parse_duration(args.timeout))`, lock path from `--lock-file` (else the
default `run/runner.lock`), `stale_after`/`stale_action` from CLI (default action
`exit-and-alert`), and `stop_on_first_failure=True`.

## 5. Behavior & algorithm

`main(argv)` orchestrates a single invocation:

1. **Parse args.** If `--version`, print and return `EXIT_SUCCESS`.
2. **Mode guard.** `--command` together with `--config` → `ConfigError`
   ("`--command` cannot be combined with `--config`").
3. **Resolve the effective config path** (`_effective_config_path`):
   `--config` → its absolute resolved path; else `--command` present → `None`;
   else the default `config/runner.yaml` **if it exists**, otherwise `None`.
   `args.config` is then set to this path so `resolve_common_options` reads the
   same YAML the runner does (so the config's `logging`/`slack` sections drive
   logging even with no `--config` flag). The default path is read through
   `_default_runner_config_path`, which honors a monkeypatched
   `DEFAULT_RUNNER_CONFIG` (a test seam).
4. **Resolve common options** (`resolve_common_options`) and **set up logging**
   (`setup_logging` for logger `parishkit.pk_cron_runner`). A logging-setup
   failure (`OSError`/`RuntimeError`/`ValueError`, e.g. a Slack channel given
   with no token) is wrapped as `ConfigError` → exit 2.
5. **Load or build the runner config** (`_load_or_build_config`): a config path
   → `load_runner_config`; else `--command` → `_single_command_config`; else
   `RunnerConfigError("no runner config found at <default>; provide --config or
   --command")` → exit 2.
6. **Apply CLI overrides** (`_apply_cli_overrides`): lock path / stale-after /
   stale-action from CLI flags (else config values); a CLI `--timeout` rebuilds
   every job with that timeout (`replace(job, timeout=...)`).
7. **Continue-on-failure:** if `--continue-on-failure`, rebuild the frozen
   `RunnerConfig` with `stop_on_first_failure=False` (other fields preserved).
8. **Log a startup summary** (info: job count, selection, include-disabled) and
   a debug line carrying the **redacted** config (`redacted_runner_config` via
   `log_extra`).
9. **Run under guards.** Enter `_signal_handlers()` and a `LockFile`, then call
   `run_jobs(...)`. The lock metadata records the *redacted* invocation argv
   (`sys.argv`, or the passed `argv` in tests).
10. **Summarize** (`_log_summary`) **after** the lock is released, and return the
    aggregate exit code.

### Job selection (`select_jobs`)

- No names given: all jobs in config order, dropping disabled unless
  `--include-disabled`.
- Names given: unknown names → `RunnerConfigError("unknown job(s): ...")`;
  results follow the **requested order** (not config order); requested-but-
  disabled jobs are still dropped unless `--include-disabled`.
- An empty resulting selection → `RunnerConfigError("no jobs selected")` (raised
  in `run_jobs`).

### Running jobs (`run_jobs` → `run_job`)

For each selected job, `run_jobs` logs `running job <name>: <redacted argv>`
(info), runs it, appends the `JobResult`, and on failure logs `job <name> failed
with exit code <rc>` (error); if `stop_on_first_failure` it breaks. The captured
child stdout/stderr is kept in the result. It is not logged by `run_jobs`, but if
`slack.include_output` / `RunnerConfig.include_output_in_slack` is true, the
bounded redacted output is embedded in the CRITICAL failure summary, which goes
to both Slack (when configured) and the runner's normal log handlers.

`run_job` (no exception escapes — every outcome is a `JobResult`):

- `env = os.environ.copy()` then `update(job.env)` (job env layered on top).
- stdout/stderr spooled to `tempfile.TemporaryFile()`s.
- `subprocess.Popen(command, cwd=job.cwd, env=env, stdout, stderr,
  start_new_session=True)` — the child gets its own session/process group so a
  timeout can signal the whole group, not orphan grandchildren.
- **Spawn failure** (`OSError`, e.g. missing executable or missing `cwd`):
  `JobResult(returncode=EXIT_JOB_FAILED, stdout="", stderr=str(exc))`.
- The live process is tracked in `_ACTIVE_PROCESSES` (for signal cleanup).
- `process.wait(timeout=job.timeout)`. On `TimeoutExpired`: `_terminate_process`
  the group, `wait()`, return `JobResult(returncode=EXIT_TIMEOUT, ...,
  timed_out=True)` with captured output. Otherwise return a `JobResult` with the
  real `returncode`. `finally` removes the process from `_ACTIVE_PROCESSES`.
- `JobResult.ok` ⇔ `returncode == 0 and not timed_out`.

**Output capture bound** (`_read_captured_output`): only the last
`MAX_CAPTURED_OUTPUT_BYTES` (200,000) bytes are read back; if truncated, the text
is prefixed with `"... output truncated ...\n"`. Decoded as UTF-8 with
`errors="replace"`.

**Aggregate exit code** (`_results_exit_code`): any timed-out job → `EXIT_TIMEOUT`
(4); else any non-ok job → `EXIT_JOB_FAILED` (1); else `EXIT_SUCCESS` (0). A
timeout therefore *wins* over an ordinary non-zero exit in the aggregate.

### Process teardown (`_terminate_process`, `_signal_handlers`)

`_terminate_process`: if the child already exited, return. Else `os.killpg(pid,
SIGTERM)` (preferred so grandchildren are signalled); `ProcessLookupError` →
return; other `OSError` → fall back to `process.terminate()`. Wait up to 5s; if
still alive, escalate to `os.killpg(pid, SIGKILL)` (falling back to
`process.kill()`), then `wait()`.

`_signal_handlers` is a context manager that installs SIGTERM/SIGINT handlers for
the runner's lifetime. On a signal it terminates every tracked child, releases
every held lock (`_ACTIVE_LOCKS`), then raises `SystemExit(128 + signum)`
(shell convention). Previous handlers are restored on exit.

### Lock acquisition & stale-lock recovery (`LockFile`)

A single-instance lock guarantees only one runner runs at a time. Each `LockFile`
gets a unique ownership token (`uuid4().hex`).

- **Acquire** (`acquire`): `os.open(path, O_CREAT|O_EXCL|O_WRONLY, 0o600)` creates
  the lock atomically. The whole attempt runs inside `_stale_recovery_guard`, an
  advisory `fcntl.flock(LOCK_EX)` on a sibling `<lockname>.recovery` file, so two
  runners cannot both decide the same stale lock is removable and race. On
  `FileExistsError`, `_handle_existing_lock` decides; then it retries the
  `os.open` once. A second `FileExistsError` → `LockUnavailable("runner lock
  changed while recovering stale lock")`. Any other `OSError` (e.g. unwritable
  parent) → `LockUnavailable("runner lock failed: ...")`.
- **Metadata** (`_metadata`, written by `_write_metadata` with `json.dump`,
  `flush`, `fsync`): `host` (`socket.gethostname`), `pid`, `command` (redacted
  argv), `start_time` (UTC ISO-8601), `timeout`, `token`. A write failure unlinks
  the partial file and re-raises. On success the lock is appended to
  `_ACTIVE_LOCKS`.
- **Existing-lock decision** (`_handle_existing_lock`): read metadata; empty/
  unreadable → `LockUnavailable("runner lock metadata is unavailable")`. If not
  stale (`is_lock_stale`) → `LockUnavailable("runner lock is active")`. If stale,
  the configured `stale_action`:
  - `remove-and-continue`: re-read metadata; if it changed since the staleness
    check → `LockUnavailable("runner lock changed while checking stale lock")`
    (covers both a different token and an unreadable re-read); else unlink and
    let acquisition retry.
  - `fail-closed`: `LockUnavailable("runner lock is stale; failing closed")`.
  - `exit-and-alert` (default / any other): `LockUnavailable("runner lock is
    stale")`.
- **Staleness** (`is_lock_stale`): `stale_after is None` → never stale; a missing
  or unparseable `start_time` → treated as **stale**; naive timestamps assumed
  UTC; stale iff `now(UTC) - start > stale_after`.
- **Release** (`release`, idempotent): re-reads the on-disk metadata and only
  unlinks if the stored `token` still equals ours — so a runner never deletes a
  lock that another (e.g. post-recovery) runner has taken over. `FileNotFoundError`
  on unlink is ignored; other `OSError` is logged as a warning and the lock is
  still marked released. The lock is removed from `_ACTIVE_LOCKS`.

### Summary & Slack notification (`_log_summary`)

After the run, with a `context` prefix (`"<context>: "` when set):

- **Success** (`EXIT_SUCCESS`): message `"<ctx>runner completed successfully (<n>
  job(s))"`. Logged at **CRITICAL** if `notify_success` (so it reaches Slack at
  the default Slack threshold), otherwise at **INFO** (stays quiet).
- **Failure** (any non-zero): logged at **CRITICAL** with `_failure_summary`.
  That summary is `"runner failed (<n> job(s), exit <code>)"` followed by one
  `"- <name>: <status>"` line per failed job, where `<status>` is `timeout` (if
  timed out) or `exit <rc>`. When `include_output_in_slack` is set, each failed
  job's `_bounded_output` (stderr then stdout, redacted, trimmed to 1,500 chars
  with a truncation marker) is appended.

Slack delivery itself is the shared `SlackLogHandler` posting CRITICAL-level
records; see [Logging and notifications](../intro/spec.md#logging-and-notifications).
Whether Slack actually fires depends on `slack.level`/`--slack-log-level`
(default `CRITICAL`) and a configured token+channel.

### Output redaction

Likely secrets are scrubbed in three places:

- **Command/env** (`_redacted_command`, `_redacted_env`, `_looks_sensitive`):
  used for lock metadata, the start-of-job log line, and the debug config dump.
  A token following a sensitive flag (`--token secret` → `--token [redacted]`),
  a sensitive `key=value` (`--client-secret=x` → `--client-secret=[redacted]`),
  and env values whose key looks sensitive are redacted. Sensitivity = the
  upper-cased, `-`→`_` name contains any of `SENSITIVE_WORDS` (`TOKEN`, `SECRET`,
  `PASSWORD`, `PASS`, `KEY`, `CREDENTIAL`, `AUTH`).
- **Slack failure output** (`_redacted_output`): `SECRET_ASSIGNMENT_RE` turns
  `KEY=value`/`token: value` into `KEY=[redacted]`; `EMAIL_RE` → `[redacted-email]`;
  `LONG_TOKEN_RE` (any run of 32+ token-like chars) → `[redacted-token]`.

## 6. Outputs & side effects

- **Lock file** at `lock.path` (default `<root>/run/runner.lock`), JSON metadata,
  mode `0600`, created on acquire and removed on clean release.
- **Recovery guard file** `<lockname>.recovery` (sibling of the lock), used for
  the `flock` serialization of stale recovery. It is created/opened during every
  acquire and is **not** deleted afterward; it lingers as a zero-or-small file.
- **JSONL log file** (shared logging): the runner's own records — startup summary,
  per-job start lines (redacted), failure lines, final summary. Child stdout/
  stderr is not written by per-job logging, but it is included in the final
  CRITICAL failure summary when `include_output` is enabled.
- **Slack notifications**: failure summary at CRITICAL; success summary at
  CRITICAL only if `notify_success`.
- **Child processes**: each job runs in its own session/process group, inheriting
  the environment plus `job.env`, in `job.cwd` if set. Their effects (Google
  Group changes, etc.) are the responsibility of the invoked tools.
- **No external-service writes** by the runner itself.

## 7. Failure modes & exit codes

`pk-cron-runner` defines its own exit codes and maps errors **inside `main`**
(it does not use the shared `run_user_facing` funnel described in
[Shared CLI layer](../intro/spec.md#shared-cli-layer)):

| Constant | Value | Meaning |
| --- | --- | --- |
| `EXIT_SUCCESS` | 0 | All selected jobs succeeded. |
| `EXIT_JOB_FAILED` | 1 | At least one job exited non-zero (and none timed out). |
| `EXIT_CONFIG_ERROR` | 2 | Config / usage error (also a spawn failure's per-job code). |
| `EXIT_LOCKED` | 3 | Lock held / active / stale-and-refused / recovery race lost. |
| `EXIT_TIMEOUT` | 4 | At least one job exceeded its timeout. |

Exception hierarchy (each carries an `exit_code`): `RunnerError`
(`EXIT_JOB_FAILED`) → `LockUnavailable` (`EXIT_LOCKED`), `RunnerConfigError`
(`EXIT_CONFIG_ERROR`). `main` catches `RunnerError` (logs CRITICAL, returns
`exc.exit_code`) and `ConfigError` (logs CRITICAL `"configuration error: ..."`,
returns `EXIT_CONFIG_ERROR`). Unexpected exceptions propagate as real tracebacks.

Specific tool failures: `--command` with `--config` (2); empty `--command` (2);
logging setup failure (2); no config and no command (2); unknown/duplicate/empty
job selection (2); active or stale-refused lock (3); recovery race lost (3);
job non-zero exit (1); job timeout (4); job spawn failure (contributes
`EXIT_JOB_FAILED` for that job → run code 1 unless another job timed out).

## 8. Edge cases & nuances

- **Overlapping runs:** a second runner finding a fresh lock exits 3 without
  touching it; the first run's `release` only unlinks if its token still matches.
- **Crashed run / stale lock:** with `stale_after` set and
  `remove-and-continue`, a lock older than the threshold is removed and the run
  proceeds; with `fail-closed` or the default `exit-and-alert`, it exits 3 so a
  human investigates. The example config pairs a conservative `stale_after` (16m)
  with `remove-and-continue` for unattended cron.
- **Concurrent stale recovery:** two runners racing over the same stale lock are
  serialized by the `.recovery` flock; exactly one acquires, the other exits 3
  cleanly (locked under test).
- **Unreadable/empty lock metadata:** never treated as stale-removable — an empty
  or corrupt lock yields `LockUnavailable("metadata is unavailable")` (3), so a
  garbled lock is not silently deleted. (Note: a *missing* `start_time` inside
  otherwise-valid JSON *is* treated as stale by `is_lock_stale`.)
- **Replaced lock during recovery:** if the metadata changes between the
  staleness check and removal (different token, or now unreadable), takeover
  aborts with `LockUnavailable("...changed...")` and the on-disk lock is left in
  place.
- **Hung job:** killed via SIGTERM→(5s)→SIGKILL to the process group, so
  grandchildren are not orphaned; the run's aggregate code becomes 4.
- **Signal during a run:** SIGTERM/SIGINT tears down children and releases locks,
  then exits `128+signum`.
- **Disabled jobs:** skipped even when named explicitly; only `--include-disabled`
  runs them.
- **`--command` ignores the default config:** in command mode the default
  `runner.yaml` is never loaded; the ad-hoc command runs instead.
- **Timeout precedence in aggregate:** an earlier timeout's code 4 wins over a
  later ordinary failure when `stop_on_first_failure=False`.
- **`dry_run` is inert:** resolved but unused — a dry-run flag does not stop the
  runner from launching real child jobs (the children enforce their own dry-run).
- **Unknown top-level config keys are not rejected** (only per-section keys are);
  a typo'd section name is silently ignored by `parse_runner_config`.

## 9. Testing notes (`tests/test_runner.py`)

Locked-down behavior and the injection seams used:

- **Lock lifecycle:** metadata + `0600` mode on acquire, removal on context exit;
  active lock → `LockUnavailable`; filesystem error (lock parent is a file) →
  `LockUnavailable("runner lock failed")`.
- **Stale recovery:** `remove-and-continue` takes over a 2h-old lock and removes
  it on release; takeover aborts when the re-read token differs
  (`monkeypatch read_lock_metadata` to return stale-then-replacement), when the
  re-read is empty, or when the original metadata is empty/unparseable
  (`"metadata"`); two real `multiprocessing` (`spawn`) processes racing a stale
  lock yield exactly `["acquired", "locked"]` with clean exit codes.
- **Release safety:** releasing a stale lock that was already replaced leaves the
  replacement intact; an `OSError` during unlink (monkeypatched `Path.unlink`)
  logs a warning and still clears `_acquired`.
- **`is_lock_stale`:** `{}` (no start time) is stale.
- **`run_job`:** success captures stdout; output bounded when
  `MAX_CAPTURED_OUTPUT_BYTES` is monkeypatched to 12 (truncation marker +
  expected tail); timeout sets `timed_out` and `EXIT_TIMEOUT`; missing `cwd`
  reports `EXIT_JOB_FAILED` with the OS error in stderr.
- **Redaction:** `redacted_runner_config` hides env + CLI secret args; lock
  metadata redacts secret command args; the start-of-job log line shows
  `--token '[redacted]'` and never the secret.
- **`run_jobs`:** child stdout/stderr stay in results and are not logged by the
  per-job loop; `_log_summary` can still log bounded redacted output when
  `include_output` is enabled. `stop_on_first_failure=False` runs all jobs and
  surfaces the failure code; an earlier timeout's code 4 beats a later exit-7 in
  the aggregate.
- **`select_jobs`:** disabled skipped unless `include_disabled`; unknown name →
  `RunnerConfigError`.
- **`parse_runner_config`:** resolves relative paths against `base_dir`, parses
  durations to seconds, carries `stop_on_first_failure`; rejects string booleans
  for `enabled`, non-scalar `stale_action`/`runner.context`/`slack.context`,
  shell-string commands, unknown job keys (`"job has unsupported key"`), and
  duplicate job names. `parse_duration(True)` rejected.
- **`main` end-to-end:** single `--command` run succeeds; command mode ignores the
  monkeypatched `DEFAULT_RUNNER_CONFIG`; `--command` + `--config` → 2; empty
  `--command` → 2 (default job not run); logging-setup failure (Slack channel,
  no token) → 2; CLI lock/stale/timeout overrides applied (override lock used,
  configured lock untouched, short timeout fires → 4); named YAML job runs only
  that job; default config drives logging (log file created); no config and no
  command → 2.
- **Summaries:** `_log_summary` with a `FakeLogger` logs success at `critical`
  when `notify_success` (else `info`) and failure at `critical`; `_failure_summary`
  embeds and redacts failed-job output when `include_output_in_slack`.

Primary seams: `parse_runner_config(dict, base_dir=)`, `run_job(JobConfig)`,
`run_jobs(config, logger=)`, `main(argv)`, monkeypatched `DEFAULT_RUNNER_CONFIG`
(honored via `_default_runner_config_path`), monkeypatched `read_lock_metadata`
and `MAX_CAPTURED_OUTPUT_BYTES`, and `_log_summary` with a fake logger.

## 10. Re-creation task outline

1. Define exit-code constants (`EXIT_SUCCESS=0`, `EXIT_JOB_FAILED=1`,
   `EXIT_CONFIG_ERROR=2`, `EXIT_LOCKED=3`, `EXIT_TIMEOUT=4`) and the exception
   hierarchy (`RunnerError`/`LockUnavailable`/`RunnerConfigError` with
   `exit_code`).
2. Define the frozen dataclasses: `LockConfig`, `JobConfig`, `RunnerConfig`,
   `JobResult` (with `.ok`).
3. Implement `parse_duration`, `_path`, `_string_list`, `_bool_value`,
   `_optional_string`, `_choice` scalar readers (strict typing, `ConfigError`).
4. Implement `parse_runner_config` / `_parse_job` / `_validate_unique_job_names`
   with `reject_unknown_keys` per section, cross-section `notify_success` and
   `context` precedence, and `base_dir`-relative path resolution; plus
   `load_runner_config`.
5. Implement the redaction helpers (`_looks_sensitive`, `_redacted_command`,
   `_redacted_env`, `redacted_runner_config`, `_redacted_output`) and the regex
   constants / `SENSITIVE_WORDS`.
6. Implement `LockFile` (token, `O_CREAT|O_EXCL` acquire, `_stale_recovery_guard`
   flock, `_write_metadata` with fsync, token-checked `release`,
   `_handle_existing_lock`), plus `read_lock_metadata` and `is_lock_stale`.
7. Implement `run_job` (temp-file capture, `start_new_session`, timeout →
   `_terminate_process`, spawn-failure handling), `_read_captured_output` with
   `MAX_CAPTURED_OUTPUT_BYTES`, and `_ACTIVE_PROCESSES`/`_ACTIVE_LOCKS` tracking.
8. Implement `select_jobs`, `run_jobs`, `_results_exit_code`.
9. Implement `_terminate_process` (group SIGTERM→SIGKILL escalation) and
   `_signal_handlers` (SIGTERM/SIGINT cleanup → `SystemExit(128+signum)`).
10. Implement `build_parser` (shared + tool-specific flags, `--version`),
    `_single_command_config`, `_effective_config_path`,
    `_default_runner_config_path`, `_load_or_build_config`,
    `_apply_cli_overrides`, `_log_summary`, `_failure_summary`, `_bounded_output`.
11. Implement `main(argv)` wiring all of the above with internal error mapping;
    wire `run_main` in `cli.py` and the `pyproject` entry point; add the wrapper
    script and `example-config.yaml`.
12. Port the tests in `tests/test_runner.py`.

## 11. Cross-references

- System spec: [`../intro/spec.md`](../intro/spec.md) — especially
  [Shared CLI layer](../intro/spec.md#shared-cli-layer),
  [Configuration system](../intro/spec.md#configuration-system),
  [Logging and notifications](../intro/spec.md#logging-and-notifications),
  [Runtime model and installation](../intro/spec.md#runtime-model-and-installation),
  [Dry-run and write safety](../intro/spec.md#dry-run-and-write-safety),
  [Tool catalog](../intro/spec.md#tool-catalog).
- Operator docs: `scripts/pk-cron-runner/README.md`; config template
  `scripts/pk-cron-runner/example-config.yaml`.
- Tools this runner schedules:
  [`pk-sync-ps-to-ggroup`](../pk-sync-ps-to-ggroup/spec.md),
  [`pk-sync-ps-to-cc`](../pk-sync-ps-to-cc/spec.md),
  [`pk-create-ps-ministry-rosters`](../pk-create-ps-ministry-rosters/spec.md),
  [`pk-validate-gcalendar-reservations`](../pk-validate-gcalendar-reservations/spec.md),
  [`pk-query-ps-memfam`](../pk-query-ps-memfam/spec.md),
  [`pk-print-ps-ministries`](../pk-print-ps-ministries/spec.md).
</content>
</invoke>
