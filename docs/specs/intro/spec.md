# ParishKit — Top-Level System Specification

This is the system-level specification for ParishKit. It captures the design
intent, goals, architecture, shared "common code" philosophy, configuration and
runtime model, testing/CI philosophy, and development guidelines for the whole
project. It is written so that a competent team could **re-create ParishKit from
these specs** without the existing source.

Per-tool behavior is specified separately, one document per command, under
`docs/specs/<tool-name>/`. This document is the shared foundation those tool specs
build on; they link back here rather than repeating shared material. The
ParishSoft REST surface ParishKit reads is analyzed in
[`docs/parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md).

> Audience note: this spec is for people building/maintaining ParishKit. The
> repository `README.md` is the operator-facing guide (install, credentials,
> scheduling). Where this spec and the README overlap, the README is the
> authoritative operator documentation and this spec is the authoritative design
> intent; neither should duplicate the other's detail.

## Design intent and goals

ParishKit is a collection of command-line tools that a **parish IT
administrator** runs on a schedule to keep a parish's cloud services in sync
with ParishSoft. The non-negotiable design intents are:

1. **ParishSoft is the system of record.** Data flows *outward* from ParishSoft
   to other services (Google Workspace, Constant Contact, notifications).
   ParishKit reads ParishSoft and pushes derived changes outward. (Note: the
   ParishSoft API is no longer strictly read-only at the contact-field level;
   see [`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md). ParishKit
   today still only reads from ParishSoft.)

2. **Parish-neutral package code.** Nothing under `src/parishkit` may contain a
   parish's name, domain, ministry names, object IDs, credentials, or deployment
   paths. All parish-specific facts live in YAML config and credential files
   that the operator creates and that never enter the repository. This is what
   makes the kit reusable across parishes and safe to open-source.

3. **One tool, one job, one config file.** Each command does a single, auditable
   job, configured by exactly one YAML file passed with `--config`. Tools do not
   share mutable state with each other; they share *code*, not runtime data.

4. **Dry-run first.** Any tool that can change an external system refuses to
   write unless the operator explicitly chooses live mode. Example configs ship
   with `dry_run: true`. See [Dry-run and write safety](#dry-run-and-write-safety).

5. **Unattended operation with loud failure.** Tools are built to run from
   `cron` without supervision: they log structured output, can post Slack alerts
   on failure, and can email human-readable change summaries. Healthy runs stay
   quiet.

6. **Guardrails against surprising damage.** Sync tools abort rather than make
   large, unexpected deletions (for example, when a ParishSoft source
   unexpectedly comes back empty), with per-target tunable limits.

7. **Secrets stay on disk, loaded at runtime.** API keys, OAuth tokens, and
   service-account keys are files on the server with tight permissions,
   referenced by path from config and read at call time — never embedded in code
   or config values.

8. **Modest performance, high clarity.** The tools are not performance-critical.
   Prefer shorter, clearer, easily unit-tested code over clever optimization;
   avoid gratuitous inefficiency.

## Architecture and data flow

ParishKit is a single installable Python package (`parishkit`) that exposes
several console commands. Every command follows the same skeleton:

```
parse argv (shared + tool-specific flags)
  → load + validate YAML config
  → resolve CommonOptions (CLI > config > built-in default)
  → set up logging (console text, optional JSONL file, optional Slack)
  → build the clients it needs (ParishSoft / Google / Constant Contact / email)
  → read source data (ParishSoft is the source of truth)
  → compute a desired end state
  → diff desired vs. actual in the target system
  → in dry-run: report the diff and stop; in live mode: apply the diff
  → optionally email a change summary; exit non-zero on operational failure
```

The **reconciliation pattern** (read source → read target → diff → apply or
report) is the heart of every sync tool. It is what makes dry-run meaningful: a
dry run executes everything up to "apply" and prints the diff.

Layering, innermost to outermost:

- **Foundation** (no external service deps): `config`, `logging`, `retry`,
  `files`, `auth`, `cli`.
- **Service clients**: `parishsoft` (+ `parishsoft_runtime`), `google.*`,
  `constant_contact`, `email.*`. Each wraps one external API, maps its failures
  onto ParishKit's error/retry model, and is independently unit-testable with
  mocks.
- **Tool modules**: `pk_<tool>.py`, one per command, holding that command's
  config schema, reconciliation logic, reporting, and `main()`.
- **Console entry points**: thin functions in `cli.py` that lazily import the
  tool module and delegate, declared in `pyproject.toml [project.scripts]`.
- **Wrapper scripts**: `scripts/<tool-name>/<tool-name>.py`, executable shims
  that exist so a tool can be run straight from a checkout; they only delegate to
  the package entry point.

## Repository layout

```text
src/parishkit/            Reusable, parish-neutral package code
  cli.py                  Shared CLI flags, CommonOptions, entry points, error funnel
  config.py               YAML loading + validation helpers
  logging.py              Console/JSONL/Slack logging, rotation, secret-safe files
  retry.py                RetryPolicy + retry_call/retry decorator
  files.py                atomic_write_text (owner-only, crash-safe)
  auth.py                 Credential-loading philosophy (docstring-only package)
  parishsoft.py           ParishSoft v2 client + data model loading/linking/filtering
  parishsoft_runtime.py   Build a ParishSoftClient from CommonOptions + YAML
  constant_contact.py     Constant Contact v3 client, OAuth device/refresh, contact mapping
  google/                 Google API helpers
    auth.py               Service-account/user creds, build_service, execute_google_request
    calendar.py           Calendar v3 list/patch helpers
    sheets.py             Sheets v4 read/clear/update/batchUpdate helpers
    drive.py              Drive v3 metadata helper
    groups.py             Admin SDK Directory + Groups Settings helpers
  email/                  Email notification providers
    base.py               Email/Attachment models, build_message, provider_from_config
    google_workspace.py   Gmail SMTP via XOAUTH2 (service account + DWD)
    ms365.py              Placeholder provider (dry-run only)
  pk_cron_runner.py       Scheduler/runner tool
  pk_query_ps_memfam.py   Member/family lookup tool
  pk_print_ps_ministries.py  Ministry-name listing tool
  pk_validate_gcalendar_reservations.py  Calendar reservation auditor
  pk_create_ps_ministry_rosters.py       Ministry rosters → Google Sheets
  pk_sync_ps_to_ggroup.py Google Group membership sync
  pk_sync_ps_to_cc.py     Constant Contact list sync

scripts/<tool-name>/      One folder per command:
  <tool-name>.py          Executable wrapper (delegates to package entry point)
  example-config.yaml     Documented, fake-data config template
  README.md               Operator-facing per-tool docs
scripts/smoke-tests/      Human-run, credential-dependent checks (see testing section)

tests/                    pytest suite (mocked; no real credentials)
tools/prepare-release.py  Release/version automation
install.py                Installs into a runtime tree
.github/workflows/        ci.yml (lint+format+pytest+DCO), release.yml (tag→build→release)
pyproject.toml            Package metadata, entry points, deps/extras, ruff/pytest config
requirements.txt          `-e .[dev,google,slack]` for dev/CI convenience
CLAUDE.md (== AGENTS.md)  Contributor/agent instructions
docs/                     Documentation
  parishsoft-api-analysis.md  ParishSoft v1-vs-v2 API analysis
  specs/                  These specifications (intro + one per tool)
```

Conventions enforced by this layout:

- Command behavior lives in `src/parishkit` modules exposed through console
  entry points; wrapper scripts only delegate. No ad-hoc `sys.path` edits.
- New wrapper scripts begin with `#!/usr/bin/env python3` and have the
  executable bit set.
- Specifications live under `docs/specs/` (this directory tree); broader
  documentation lives under `docs/`.

## Runtime model and installation

ParishKit installs into a single self-contained tree rooted at a configurable
location, resolved in this order: `--installdir` → `$PARISHKIT_ROOT` →
`/opt/parishkit`. `install.py` creates the tree, a private virtualenv, installs
the package (with chosen extras, default `google,slack`), and links stable
launcher symlinks under `bin/`.

Runtime directory tree and permissions (created by `install.py`):

| Path | Mode | Holds |
| --- | --- | --- |
| `<root>/` | `0750` | install root |
| `<root>/bin/` | `0755` | launcher symlinks to the venv console scripts |
| `<root>/config/` | `0750` | operator YAML config files |
| `<root>/credentials/` | `0700` | API keys, OAuth tokens, service-account keys |
| `<root>/cache/` | `0750` | cached ParishSoft responses |
| `<root>/logs/` | `0750` | JSONL log files |
| `<root>/reports/` | `0750` | generated reports |
| `<root>/run/` | `0750` | lock files and small run-state |

**Path-resolution contract** (implemented in `cli.py`):

- `$PARISHKIT_ROOT` (or `/opt/parishkit`) defines the *built-in default* for
  every runtime path: `config/`, `credentials/`, `cache/parishsoft/`, etc., plus
  derived defaults like `credentials/parishsoft-api-key.txt`.
- Every path is overridable by a CLI option **or** a YAML config value.
- A relative path read from a YAML config is resolved against the **config
  file's own directory** (so configs are portable); a relative path from the CLI
  is resolved against the current working directory.
- Precedence is always: explicit CLI value → config-file value → built-in
  default.

## Common code philosophy

ParishKit centralizes everything cross-cutting so individual tools stay small
and consistent. The rule: **option parsing, YAML loading, startup validation,
logging, Slack/email notification, retry, and authentication all belong in the
shared helpers, not in each tool.** A tool module should read mostly as
domain/reconciliation logic.

### Shared CLI layer

`parishkit.cli` provides the common command-line surface and the
`CommonOptions` dataclass that every tool resolves first.

- **Common flags** (added by `add_common_arguments`): `--config`; tri-state
  `--dry-run/--no-dry-run`, `--verbose/--no-verbose`, `--debug/--no-debug` (all
  default to `None` via `BooleanOptionalAction` so "unset" is distinguishable
  from explicit true/false); `--log-file`, `--log-dir`; `--slack-token-file`,
  `--slack-channel`, `--slack-log-level`; `--ps-api-key-file`, `--ps-cache-dir`,
  `--ps-cache-limit`. Tools call `parser_with_common_options(prog, description)`
  and then add only their own flags.
- **`resolve_common_options(args)`** merges CLI + the `common`, `logging`,
  `slack`, and `parishsoft` YAML sections into `CommonOptions`, applying the
  CLI > config > default precedence, validating the config values before CLI
  overrides are applied (so a bad YAML value still fails even when the same
  option is valid on the command line). It validates timezone / Slack level /
  cache limit and forces `verbose` on whenever `debug` is set. It rejects
  unknown keys in each section (see [Configuration system](#configuration-system)).
- **Tri-state `dry_run`** is special: it records both the resolved value and
  whether it was set explicitly (`dry_run_explicit`), used by the write gate.
- **`require_explicit_write_mode(options, tool_name)`** forces mutating tools to
  state intent: it raises `ConfigError` unless `dry_run` was set explicitly
  (CLI flag or config), so a write-capable tool never silently defaults to live
  writes. See [Dry-run and write safety](#dry-run-and-write-safety).
- **`run_user_facing(action)`** is the uniform error funnel: it runs the tool
  body and converts expected operational errors (`ConfigError`, `OSError`,
  `ParishSoftAPIError`, `GoogleAPIError`, `CCAPIError`, `RetryError`) into a
  single `ERROR: …` line on stderr plus exit code 2, while letting unexpected
  exceptions propagate as real tracebacks (genuine bugs stay visible).
- **Entry points** (`run_main`, `print_member_main`, …) lazily import their tool
  module and delegate to its `main()`. Lazy import keeps tools that don't need
  heavy optional deps (Google, Slack) cheap to start.

Defaults of note: `DEFAULT_PS_CACHE_LIMIT = "14m"`,
`DEFAULT_SLACK_LOG_LEVEL = "CRITICAL"`,
`DEFAULT_TIMEZONE = "America/Kentucky/Louisville"`. Cache-limit durations match
`^[1-9][0-9]*[smhd]$` (e.g. `30s`, `14m`, `12h`, `7d`).

### Configuration system

`parishkit.config` is intentionally tiny and strict:

- `load_yaml_config(path, required=…)` — loads a YAML file as a dict; empty file
  → `{}`; missing-but-required, non-mapping top level, and parse errors raise a
  user-facing `ConfigError` (YAML errors include line/column and a hint).
- Validation helpers: `require_mapping`, `require_keys`,
  **`reject_unknown_keys(section, allowed, name)`** (typo-proofing — every config
  section enumerates its allowed keys and rejects the rest), `validate_with`
  (normalizes `TypeError`/`ValueError` into `ConfigError`), and `resolve_path`
  (required path with config-relative semantics).
- **Philosophy: fail fast and specifically at startup.** Misconfiguration must
  produce a clear `ConfigError` before any external call — never a stack trace
  mid-run or a silently-ignored key. Each tool defines and validates its own
  config sections this way.

Config sections are namespaced. Shared sections (validated centrally): `common`
(`debug`, `verbose`, `dry_run`, `timezone`), `logging` (`log_file`, `log_dir`),
`slack` (`token_file`, `channel`, `level`, `notify_success`, `context`,
`include_output`), `parishsoft` (`api_key_file`, `cache_dir`, `cache_limit`,
`expected_organization`). Tool-specific sections (`google`, `email`,
`constant_contact`, and per-tool sections) are validated by the owning module.

### Logging and notifications

`parishkit.logging` configures three sinks via `setup_logging(...)`:

- **Console** — human-readable text. Threshold layered by verbosity:
  `WARNING` default, `INFO` with `--verbose`, `DEBUG` with `--debug`.
- **File** — **JSON Lines** (one JSON object per line) via `JsonLogFormatter`,
  carrying timestamp/level/logger/message plus optional `exception`, `stack`,
  and a structured `extra` payload (`log_extra(value)` attaches arbitrary
  JSON-able context). Files use a `CompressingRotatingFileHandler` (size-based
  rotation, 50 MB × 50 backups by default, rotated files gzipped) and are
  created `0600` before logging opens them.
- **Slack** — `SlackLogHandler` posts records at/above a threshold (default
  `CRITICAL`) to a channel using a bot token read from a file. `slack_sdk` is
  imported lazily; Slack delivery failures are swallowed (logged once) so a
  notification problem never masks the original error. Supplying only one of
  token-file/channel is a `ConfigError`.

Handler swap is atomic: new handlers are fully built before old ones are removed
and closed, so a construction failure leaves the previously-configured logger
intact and never leaks file descriptors. Console/error/Slack text stays
human-readable; only the log *file* is JSONL.

Email notifications are a separate concern handled by the
[email provider layer](#email-provider-layer); Slack is for alerting, email is
for human-readable change summaries.

### Retry policy

`parishkit.retry` is the single retry mechanism every service client uses.

- **`RetryPolicy`** (frozen dataclass): `attempts` (total tries), `initial_delay`,
  `backoff` (geometric multiplier), `max_delay` (cap), `jitter` (uniform random
  add-on to desynchronize concurrent callers). `delay_for_attempt(i)` computes
  the backoff.
- **`retry_call(func, policy=…, retry_on=…, sleep=…)`** retries `func` until it
  succeeds or attempts run out, then raises `RetryError` chained from the last
  exception. `sleep` is injectable so tests don't wait.
- **`TransientRetryError`** is the base class clients subclass to mark a failure
  retryable; `DEFAULT_RETRY_EXCEPTIONS` also covers timeouts/connection errors.
- **Idempotency rule:** only wrap small, repeatable operations. Writes that
  could duplicate on retry are made one-shot (`RetryPolicy(attempts=1)`) — e.g.
  Constant Contact POST creates and Google membership writes — while idempotent
  reads/updates use the normal multi-attempt policy. Each client maps its API's
  rate-limit/5xx responses to `TransientRetryError` and, on exhaustion,
  re-raises a single terminal error type (`ParishSoftAPIError`, `GoogleAPIError`,
  `CCAPIError`).

### Secrets and filesystem helpers

- `parishkit.files.atomic_write_text(path, text, mode=0o600)` writes via a
  same-directory temp file, `fsync`, then `os.replace`, so readers never see a
  partial file and secrets are never briefly world-readable. Used for token
  files and the ParishSoft cache.
- `parishkit.auth` is a documentation package: it codifies that credentials are
  located by path and read at runtime, never baked into code or committed.
- Secret files (API keys, tokens) are read with whitespace trimmed and kept out
  of logs. Credential files live under `credentials/` (`0700`).

### ParishSoft data layer

`parishkit.parishsoft` is the largest shared module and the read-side core. It
contains both the HTTP client and the parish data model.

- **`ParishSoftClient`** wraps a `requests.Session` preconfigured with the
  `x-api-key` header, applies the shared retry policy, and caches GET/POST
  responses on disk.
  - **On-disk cache**: responses are written (pretty, key-sorted JSON, atomic)
    under a per-tenant cache scope — a non-secret fingerprint of base URL + API
    key, plus the validated organization id — so one deployment can never read
    another tenant's cached data. `cache_limit` (e.g. `14m`) treats older files
    as misses.
  - **Pagination**: `get_paginated` / `post_paginated` follow ParishSoft's two
    conventions (index-style running offset vs. 1-based page number) with
    configurable parameter names, and handle both response shapes (a bare list,
    or an envelope `{data, pagingInfo}`) via `_extract_page`.
  - **`validate_organization()`** confirms the API key maps to exactly one
    organization and, if `expected_organization` is configured, that its report
    name matches — guarding against pointing at the wrong tenant. Returns and
    caches the org id.
  - `post_uncached` exists as the seam for future ParishSoft write calls (see
    [`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md)).
- **Data model**: `load_families_and_members(...)` is the central aggregation
  path. It validates the org, loads each entity type (families, members, family
  groups, family/member workgroups + their memberships, member contact info,
  ministry types + rosters, and optionally funds/pledges/contributions),
  cross-links them (members↔families, workgroups, ministries, giving), derives
  friendly display names and normalized email lists, then filters per flags
  (`active_only`, `parishioners_only`, `include_deceased`,
  `load_contributions`). Derived fields are namespaced with a `"py "` prefix
  (e.g. `"py members"`, `"py ministries"`, `"py emailAddresses"`) to separate
  them from raw API fields.
- **Domain helpers** used across tools: email splitting/normalization,
  `member_is_active`/`member_is_deceased`, `get_family_heads`,
  `family_workgroup_emails` (layered precedence: workgroup members → heads → any
  member → family email), `ministry_membership_is_current`, and
  `salutation_for_members` (builds grammatical salutations for one or more
  members).

Which ParishSoft API ParishKit talks to, what it does and does not use, and the
read-only question are covered in
[`docs/parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md).

`parishsoft_runtime.parishsoft_client_from_config(common, config)` is the bridge
from `CommonOptions` + YAML to a ready `ParishSoftClient`: it reads the API key
from `common.ps_api_key_file` at call time, applies cache dir/limit, and wires
`expected_organization`.

### Google integration layer

`parishkit.google` wraps Google APIs behind one auth/retry seam. All optional
Google libraries are imported lazily so the base install doesn't need them;
absence raises a friendly `ConfigError` pointing at the `parishkit[google]`
extra.

- **`google.auth`**: `load_service_account_credentials(key_file, scopes, subject)`
  (the `subject` enables **domain-wide delegation** — the service account
  impersonates a real Workspace user); `load_user_credentials` /
  `run_user_oauth_flow` for installed-app OAuth bootstrap; `build_service` (with
  discovery cache off); and **`execute_google_request(request, policy, sleep)`**,
  which maps Google `HttpError`s onto the shared retry model (429/5xx →
  transient; else terminal `GoogleAPIError`).
- **`google.calendar`**: `list_events` (paginated, single-events expansion,
  start-time order) and `patch_attendee_response` (set this account's RSVP
  without disturbing other attendees). Writes use a one-shot retry policy.
- **`google.sheets`**: `get_values`, `clear_values`, `update_values`,
  `get_spreadsheet` (metadata for title→sheetId mapping), `batch_update_spreadsheet`.
- **`google.drive`**: `get_file_metadata` (shared-drive aware).
- **`google.groups`**: `build_admin_directory_service` /
  `build_groups_settings_service`; `list_group_members`,
  `get_group_posting_permissions`, `insert_group_member`,
  `update_group_member_role`, `delete_group_member` (membership writes are
  one-shot).

The Google setup (Cloud project, service account, domain-wide delegation,
delegated user, per-tool scopes) is operator-facing and documented in the
repository README; tool specs reference the scopes they need.

### Constant Contact layer

`parishkit.constant_contact` is a self-contained Constant Contact **v3** client
plus its OAuth lifecycle and ParishSoft↔contact mapping.

- **`ConstantContactClient`**: authenticated GET (`get_all`, follows `_links.next`
  pagination, page size 500), `put` (retryable), `post` (one-shot to avoid
  duplicate-create on a hidden success). Errors normalize to `CCAPIError`.
- **OAuth device flow**: `run_device_oauth_flow` (interactive, used by the
  documented smoke-test bootstrap), `refresh_access_token`, and
  `get_access_token` (loads the saved token, returns it if valid, otherwise
  refreshes and writes it back — under an `fcntl` file lock so overlapping
  processes don't race; refresh is suppressed in dry-run so a dry run never
  rewrites credential files).
- **Mapping helpers**: `update_contact_body` / `sign_up_form_body` (copy only
  writable fields, strip periods from first names), `create_contact_dict`,
  `link_cc_data` and `link_contacts_to_ps_members` (resolve list/custom-field IDs
  to names and cross-link contacts with ParishSoft members by email).

### Email provider layer

`parishkit.email` sends human-readable notification emails behind a
provider-neutral interface.

- **`base`**: the `Email` and `Attachment` dataclasses, `build_message` (assembles
  a stdlib `EmailMessage`, multipart/alternative when HTML is present, bcc on the
  envelope only), the `EmailProvider` ABC with `send(message, dry_run=…)` (dry-run
  builds and returns the message unsent for inspection), and
  `provider_from_config` (selects the provider, rejects unknown keys, imports it
  lazily).
- **`google_workspace`**: sends via Gmail SMTP using **XOAUTH2** with the same
  service account + domain-wide delegation (scope `https://mail.google.com/`,
  delegated to a real mailbox) — no stored passwords. Refreshes the OAuth token
  only when needed.
- **`ms365`**: accepted as configuration today but a placeholder — real sending
  raises `ConfigError`; only dry-run works. (Illustrates the intended pattern for
  adding providers.)

## Dry-run and write safety

This is a cross-cutting safety contract, not a per-tool afterthought:

1. **Two-state intent for mutating tools.** A tool that can change an external
   system calls `require_explicit_write_mode`, which forces the operator to set
   `common.dry_run` explicitly (`true` for dry run, `false` for live) via config
   or `--dry-run/--no-dry-run`. There is no implicit live-write default. Example
   configs ship `dry_run: true`.
2. **Dry run reads everything, writes nothing.** A dry run performs the full
   reconciliation (read source + target, compute the diff) and reports exactly
   the additions/removals/role-changes it *would* make — but issues no target
   writes and sends no tool-specific email/report notifications. Shared logging,
   including configured Slack log handlers, still operates in dry-run so
   failures can alert operators. Token files are not refreshed/rewritten in
   dry-run.
3. **Destructive-change guardrails.** Sync tools cap how much they will delete in
   one run and abort instead of making surprisingly large removals (e.g. a source
   workgroup that unexpectedly came back empty), with per-target tunable limits.
4. **Tenant guard.** `expected_organization` is verified against the live
   ParishSoft organization name before any tool acts, so a misconfigured
   deployment cannot operate on the wrong parish.
5. **Idempotent, retry-safe writes.** Non-idempotent creates are one-shot; see
   [Retry policy](#retry-policy).

## Testing, CI, and quality philosophy

ParishKit separates **automated tests** (fast, mocked, credential-free, in CI)
from **smoke tests** (human-run, credential-dependent, never in CI).

- **Unit/automated tests** (`tests/`, `pytest`): cover all package logic with
  mocks/fakes for every external service. Injection seams exist precisely for
  this — `session=` on the clients, `sleep=` on retries, `flow_factory`/`build_fn`
  on Google, `smtp_factory` on email, `input_fn`/`print_fn`/`now`/`sleep_fn` on
  the OAuth flows. **Normal CI must never require real ParishSoft, Google,
  Constant Contact, Slack, or email credentials**, and must not hit the network.
  Tests also cover packaging/wrappers (`test_install.py`,
  `test_script_wrappers.py`, `test_cli_entrypoints.py`) and release tooling
  (`test_prepare_release.py`).
- **Smoke tests** (`scripts/smoke-tests/`): small, documented, human-run scripts
  that confirm a real credential set works *before* it is wired into a scheduled
  job. They read credentials at runtime, **prefer read-only calls, require
  `--send`/dry-run or explicit confirmation before any write, redact sensitive
  values, and stay out of normal CI.** They are operator tooling, not part of the
  test suite — and so are intentionally **not** given their own spec; this
  philosophy is their specification. They cover ParishSoft connectivity, Google
  API access, Constant Contact list access + the device-OAuth bootstrap, Google
  Workspace email, and Slack notification.
- **Lint/format**: `ruff` with rule sets `E,F,I,UP,B,SIM`, line length 88,
  double quotes, LF endings, target py312.
- **CI** (`.github/workflows/ci.yml`) on push-to-main and PRs runs, on Python
  3.12: `ruff check`, `ruff format --check`, `pytest`. PRs also enforce **DCO
  sign-off**.
- **Local validation must match CI** exactly:
  `python -m ruff check .` · `python -m ruff format --check .` · `python -m pytest`.
- **Coverage intent**: every behavior with a branch — config validation,
  precedence resolution, error/retry mapping, reconciliation diffs, guardrail
  limits, salutation/email edge cases — has a focused test. Prefer code shaped
  to be testable (small pure functions, explicit steps) over code that needs
  elaborate mocking.

## Versioning and releases

- **Semantic versioning.** Releases are annotated git tags `vVERSION` (e.g.
  `v1.2.3`). `pyproject.toml` `project.version` is the source of truth.
- **`tools/prepare-release.py`** infers the semver bump from Conventional-Commit
  messages since the last tag (`feat:` → minor, `fix:`/`perf:` → patch,
  `!`/`BREAKING CHANGE:` → major), can write the version, generate release notes,
  build artifacts, and create the annotated tag. It requires a clean worktree to
  tag, verifies `HEAD:pyproject.toml` matches the tag, and **never pushes**.
- **Release workflow** (`.github/workflows/release.yml`) triggers on a pushed
  `v*` tag: it validates the tag is annotated and reachable from `origin/main`
  and matches `pyproject.toml`, re-runs lint/format/tests, builds sdist+wheel,
  produces release notes, and creates/updates the GitHub Release with artifacts.
- **A human must explicitly authorize any `git push origin vVERSION`.** Tags are
  never auto-pushed.

## Development guidelines

- **`main` is production**; no release branches. Do work on a topic branch named
  `pr/<short-topic>` and land it via a GitHub pull request. Never commit directly
  to `main`. Git worktrees are fine (avoid repo-global commands like `git stash`
  / `git worktree prune` inside a worktree).
- **Sign off every commit** (`git commit -s`, real name + email) per the
  Contributor's Declaration; this applies to AI-assisted work too. CI enforces
  DCO on PRs.
- **No AI attribution** in commits — no `Co-Authored-By:` for AI tools, no
  "Generated with" trailers.
- **Commit messages**: short imperative first line (optional area prefix like
  `docs:`), blank line, body explaining *why*, wrapped at ~75 columns; use a
  message file for multi-line messages.
- **One logical change per commit**; keep drive-by fixes as separate commits;
  squash fixups before merge.
- **Specs live in `docs/specs/`** (this tree), broader docs under `docs/`.
  Architecture/behavior documentation belongs here, cross-linked, with no
  duplicated prose — link instead of copying so multiple copies never drift.
- **Code style/comments**: target Python 3.12+; prefer shorter, clearer code that
  is easy to unit-test; functions of three or more lines get at least a short
  docstring; longer/subtler code gets inline comments explaining *why*. Match the
  surrounding code's idiom.
- **Parish-neutral always**: never commit credentials, secrets, local logs,
  caches, generated reports, local runtime config, parish names, domains, or
  object IDs. Generated/third-party artifacts (e.g. fetched OpenAPI specs) are not
  committed either.
- **Reuse shared helpers**: option parsing, YAML loading, validation, logging,
  notification, retry, and auth go through `parishkit.cli/config/logging/retry`
  and the auth helpers by default.
- **API modernization** is allowed during migration when current supported APIs
  or client patterns are better, but preserve behavior unless an intentional
  change is documented, and keep compatibility risks visible.

## Tool catalog

Each command has its own deep specification under `docs/specs/<tool-name>/`. All of
them resolve `CommonOptions` first and follow the
[architecture skeleton](#architecture-and-data-flow) above; the per-tool specs
focus on tool-specific config schema, reconciliation logic, reporting, and edge
cases, and link back to the shared sections here.

| Command | Spec | Category | Writes to |
| --- | --- | --- | --- |
| `pk-cron-runner` | [spec](../pk-cron-runner/spec.md) | Scheduler | Local locks/logs; Slack |
| `pk-sync-ps-to-ggroup` | [spec](../pk-sync-ps-to-ggroup/spec.md) | Sync | Google Group membership |
| `pk-sync-ps-to-cc` | [spec](../pk-sync-ps-to-cc/spec.md) | Sync | Constant Contact lists |
| `pk-create-ps-ministry-rosters` | [spec](../pk-create-ps-ministry-rosters/spec.md) | Sync/report | Google Sheets |
| `pk-validate-gcalendar-reservations` | [spec](../pk-validate-gcalendar-reservations/spec.md) | Audit | Google Calendar RSVPs |
| `pk-query-ps-memfam` | [spec](../pk-query-ps-memfam/spec.md) | Lookup (read-only) | — |
| `pk-print-ps-ministries` | [spec](../pk-print-ps-ministries/spec.md) | Lookup (read-only) | — |

## Recreating ParishKit from these specs

A reasonable build order to reconstruct the system:

1. **Foundation**: `config`, `files`, `retry`, `logging`, then `cli`
   (`CommonOptions`, common flags, precedence, write gate, error funnel,
   entry-point stubs). Establish `pyproject.toml` (entry points, extras
   `google`/`slack`/`dev`, ruff/pytest config) and `install.py`/runtime tree.
2. **Service clients**: `parishsoft` (+ `parishsoft_runtime`) per the
   [data layer](#parishsoft-data-layer) and
   [`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md); then
   `google.*`, `constant_contact`, `email.*`.
3. **Tools**: implement each `pk_<tool>.py` from its spec under
   `docs/specs/<tool-name>/`, plus its wrapper, `example-config.yaml`, and operator
   README. Read-only lookup tools first (`pk-query-ps-memfam`,
   `pk-print-ps-ministries`) are the simplest end-to-end exercise of the stack;
   the sync tools are the most involved.
4. **Operations**: `pk-cron-runner`, smoke tests, CI workflows, and
   release automation (`tools/prepare-release.py`, `release.yml`).
5. **Throughout**: maintain parish-neutrality, dry-run discipline, mocked
   credential-free tests, and the development guidelines above.
