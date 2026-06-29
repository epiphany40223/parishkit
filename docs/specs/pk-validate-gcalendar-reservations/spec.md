# pk-validate-gcalendar-reservations — Google Calendar resource reservation auditor

## 1. Purpose and role

`pk-validate-gcalendar-reservations` is ParishKit's **Audit** tool. It reviews the
*pending* invitations sitting on one or more Google Calendar **resource
calendars** — for example a parish room, hall, or shared piece of equipment that
people book by inviting the resource to a calendar event — and automatically
**accepts** or **declines** each pending invitation according to configured
rules, including declining reservations that would **double-book** a calendar
against an already-accepted event.

It is the only ParishKit command in the [tool catalog](../intro/spec.md#tool-catalog)
whose category is **Audit** and the only one that touches **Google Calendar**.
Unlike the sync tools, it is *not* a ParishSoft reconciliation: **it never reads
or writes ParishSoft.** ParishSoft is the system of record for the rest of the
kit (see [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md)), but
this tool's source of truth is the live Google Calendar event list itself. The
shared `--ps-*` flags and the `parishsoft` config section are still *accepted*
(they are part of the shared CLI surface) but are **never used** here.

It does change an external system — it writes attendee RSVPs back to Google
Calendar — so it is governed by the dry-run / write-safety contract
([intro](../intro/spec.md#dry-run-and-write-safety)) and resolves
`CommonOptions` first like every other tool
([architecture skeleton](../intro/spec.md#architecture-and-data-flow)).

Source of truth for this spec: `src/parishkit/pk_validate_gcalendar_reservations.py`,
its wrapper, `example-config.yaml`, operator `README.md`, and
`tests/test_calendar_reservations.py`. Shared infrastructure (CLI, config,
logging/Slack, retry, Google auth/domain-wide delegation, the Calendar helper
functions) is described once in the [intro spec](../intro/spec.md) and only
**linked** here, not repeated.

## 2. Invocation

- **Console command:** `pk-validate-gcalendar-reservations`
- **Entry point:** `pk-validate-gcalendar-reservations = "parishkit.cli:calendar_reservations_main"`
  (declared in `pyproject.toml [project.scripts]`). `calendar_reservations_main`
  lazily imports `parishkit.pk_validate_gcalendar_reservations.main` and delegates,
  so Google client libraries are only imported when the command actually runs.
- **Wrapper script:** `scripts/pk-validate-gcalendar-reservations/pk-validate-gcalendar-reservations.py`
  — a `#!/usr/bin/env python3` shim that does nothing but
  `raise SystemExit(calendar_reservations_main())`. It lets the tool run straight
  from a checkout; all behavior lives in the package.
- **`--version`:** prints `pk-validate-gcalendar-reservations <version>` (the
  installed `parishkit` distribution version, e.g. `0.1.0`) and returns `0`
  *before* any config loading or write-mode check. This is purely an
  "is the entry point installed" probe.

`main(argv=None, *, service_factory=None, now=None)` is the testable seam: tests
pass a fake Calendar `service_factory` and a fixed `now` clock; production runs
leave both `None` (real credentials, wall-clock UTC). See
[§11 Testing notes](#11-testing-notes).

## 3. Command-line options

The only **tool-specific** flag is:

| Flag | Action | Effect |
| --- | --- | --- |
| `--version` | `store_true` | Print the entry-point version and exit `0`. |

Every other flag comes from `parser_with_common_options(...)` and behaves exactly
as documented in the [shared CLI layer](../intro/spec.md#shared-cli-layer):
`--config`; tri-state `--dry-run/--no-dry-run`, `--verbose/--no-verbose`,
`--debug/--no-debug`; `--log-file`, `--log-dir`; `--slack-token-file`,
`--slack-channel`, `--slack-log-level`; and the inherited `--ps-api-key-file`,
`--ps-cache-dir`, `--ps-cache-limit`. The `--ps-*` flags are present on the
parser but have **no effect** for this tool (it never builds a ParishSoft
client). Precedence is always CLI value > config-file value > built-in default.

There is no positional argument and no other tool-specific flag.

## 4. Configuration schema

One YAML file passed with `--config`. Shared sections (`common`, `logging`,
`slack`, `parishsoft`) are validated centrally by `resolve_common_options`
(see [configuration system](../intro/spec.md#configuration-system)); the two
**tool-owned** sections are `google` and `calendars`. There is **no `email`
section** — this tool sends no email (see [§8](#8-outputs-reporting-and-notifications)).
See `scripts/pk-validate-gcalendar-reservations/example-config.yaml` for a
fully-commented template.

### 4.1 `common` (shared) — relevant keys

- `dry_run` — **must be set explicitly** (`true` or `false`) via config or
  `--dry-run/--no-dry-run`. The tool calls `require_explicit_write_mode`; an
  unset value is a `ConfigError`. The example config ships `dry_run: true`.
- `timezone` — IANA name; becomes the **default timezone** passed into
  `calendar_reservation_config` as `default_timezone`, used only if
  `calendars.timezone` is omitted (see below). Default
  `America/Kentucky/Louisville`.

### 4.2 `slack`, `logging`, `parishsoft` (shared)

Standard shared sections. `slack` enables failure alerting; `logging` controls
the JSONL log file. `parishsoft` is accepted but unused. See
[logging and notifications](../intro/spec.md#logging-and-notifications).

### 4.3 `google` — validated by `load_calendar_credentials`

Allowed keys (anything else → `ConfigError` via `reject_unknown_keys`):
`{service_account_file, user_token_file, delegated_subject}`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_account_file` | string path | one-of | Service-account key file. Resolved via `resolve_path` relative to the **config file's directory** (CLI/config-relative semantics). |
| `user_token_file` | string path | one-of | Stored installed-app user token, resolved the same way. |
| `delegated_subject` | string | optional | Real Workspace user to impersonate via **domain-wide delegation** when using a service account. Must be a string if present. Operationally this should be a real delegated Workspace user, not the service-account email, but the loader does not inspect the service-account JSON to enforce that distinction. Has no meaning with `user_token_file`. |

Validation rules:

- Setting **both** `service_account_file` and `user_token_file` →
  `ConfigError` ("must not set both ...").
- Setting **neither** → `ConfigError`
  ("`google.service_account_file` or `google.user_token_file` is required").
- `delegated_subject` non-string (and not `None`) → `ConfigError`.
- Non-string credential file values are not type-reported directly. The loader
  only treats string values as usable paths, so a non-string file value falls
  through to the required-key error unless both file keys are truthy, which hits
  the both-set guard first.
- If `service_account_file` is a string it wins: calls
  `load_service_account_credentials(path, scopes=[CALENDAR_SCOPE], subject=delegated_subject)`.
- Otherwise if `user_token_file` is a string: calls
  `load_user_credentials(path, scopes=[CALENDAR_SCOPE])`.

**Scope:** `CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"` — the
**write-capable** Calendar scope (not `.../calendar.readonly`), because the tool
patches attendee RSVPs. The delegated user must have access to the resource
calendars and permission to respond to their invitations, and the service
account's OAuth client ID must be authorized for this scope in Workspace Admin.
Google auth/DWD details are in the
[Google integration layer](../intro/spec.md#google-integration-layer).

### 4.4 `calendars` — validated by `calendar_reservation_config`

Allowed keys (else `ConfigError`):
`{acceptable_domains, calendars, timezone, lookback_days, lookahead_days}`.

| Key | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `acceptable_domains` | list of strings | **yes** | — | Non-empty. Creator email **domains** whose reservations may be accepted. Each is **casefolded** and stored in a `frozenset`, so matching is case-insensitive. Empty list or non-string items → `ConfigError`. |
| `calendars` | list of mappings | **yes** | — | Non-empty list of resource-calendar entries (see below). |
| `timezone` | string | no | `default_timezone` (= `common.timezone`) | IANA name used to interpret all-day and offset-less event times and for log messages. Overrides the inherited common timezone. Non-string → `ConfigError`; unknown zone → `ConfigError("calendars.timezone is unknown: ...")`. |
| `lookback_days` | positive int | no | `31` | How many days *before* now to scan. Must be `int >= 1`; YAML booleans are explicitly rejected (`isinstance(value, bool)` guard). |
| `lookahead_days` | positive int | no | `547` | How many days *after* now to scan (~1.5 years). Same positive-int rule. |

Each entry of `calendars.calendars[i]` is a mapping with allowed keys
`{name, calendar_id, id, check_conflicts}`:

| Key | Type | Required | Default | Notes |
| --- | --- | --- | --- | --- |
| `name` | non-empty string | **yes** | — | Human-readable label used **only in logs**. Empty/non-string → `ConfigError`. |
| `calendar_id` | non-empty string | **yes** | — | The Google Calendar ID / resource email. `id` is accepted as a fallback key (`item.get("calendar_id", item.get("id"))`). Empty/non-string → a detailed `ConfigError` that names the entry (`calendars.calendars[0] ('Room').calendar_id`), states "non-empty string", echoes the bad value's type/value (e.g. `int 12345`), and hints "make sure it is indented under the same `- name:` item." |
| `check_conflicts` | bool | no | `True` | When true, decline pending reservations overlapping an already-accepted booking on this calendar. When false, skip overlap detection entirely. Non-bool → `ConfigError`. |

The result is a frozen `ReservationConfig(acceptable_domains, calendars,
timezone, lookback_days, lookahead_days)` holding a tuple of frozen
`ReservationCalendar(name, calendar_id, check_conflicts)` objects.

## 5. Inputs

The only external input is the Google Calendar event list for each configured
calendar, read through `parishkit.google.calendar.list_events`
([Google layer](../intro/spec.md#google-integration-layer)). For each calendar
the tool calls:

```python
list_events(service, calendar_id,
            time_min=<UTC isoformat>, time_max=<UTC isoformat>)
```

which issues `service.events().list(...)` with `singleEvents=True` (recurring
series expanded into individual instances), `orderBy="startTime"`,
`maxResults=2500` per page, following `nextPageToken` until exhausted, and
returns the full list of event dicts. The time window is computed once per run in
UTC (see [§6](#6-decision-algorithm)). Per-event fields the tool reads:
`attendees` (list of `{email, responseStatus}`), `creator.email`, `start`, `end`
(each `{dateTime}` or `{date}`, optionally with `timeZone`), `created`,
`transparency`, `summary`, and `id`.

## 6. Decision algorithm

The flow is **read all calendars and compute all decisions first, then apply
writes** (a deliberate preflight, see [§7](#7-applying-responses)).
`process_calendars` does:

1. **Compute the window.** `current_time = (now or now-UTC)()`; a naive value is
   treated as UTC. Then `time_min = current_time(UTC) - lookback_days` and
   `time_max = current_time(UTC) + lookahead_days`. Both are passed to
   `list_events` as `.isoformat()` RFC-3339 strings.
2. **For each calendar (in config order):** `list_events`, log the count, then
   call `reservation_decisions(events, calendar, config)` and collect a
   `CalendarDecisionPlan(calendar, decisions)`. No writes yet.
3. **After all plans are built:** loop the plans and `respond_to_decisions` each.

`reservation_decisions` is the heart. For one calendar's event list:

### 6.1 Classify each event by this resource's attendee status

For each event, find this calendar account's attendee entry with
`calendar_attendee(event, calendar_id)` — it scans `event["attendees"]` and
matches `email.casefold() == calendar_id.casefold()` (case-insensitive). Its
`responseStatus` (or `None` if the resource is not an attendee) decides the
bucket:

- **`needsAction` (pending):** this is an invitation awaiting a response.
  - Resolve `attendee_email` = the attendee's actual `email` (preserving Google's
    exact casing), falling back to `calendar_id`.
  - Read `creator_email = event["creator"]["email"]` (default `""`).
  - `creator_domain(email)` returns the casefolded text after the last `@`, or
    `""` when there is no `@`. **If that domain is not in
    `acceptable_domains`, decline immediately** with reason
    `"creator <email> is not in an acceptable domain"`.
  - Otherwise the event is queued as a **pending** candidate for accept/conflict
    evaluation.
- **`declined`:** ignored entirely — no decision, and does *not* contribute to
  conflict detection (the resource already said no).
- **Anything else** (`accepted`, `tentative`, or `None` because the resource is
  the owner / not in the attendee list) **and not transparent:** treated as a
  fixed **existing** booking that occupies time and feeds conflict detection.
  `event_is_transparent(event)` is `event.get("transparency") == "transparent"`;
  transparent (free / non-blocking) events are dropped from the conflict
  baseline.

So after this pass, `decisions` already contains the out-of-domain declines (in
original event order), `pending_events` holds in-domain pending candidates, and
`existing_events` holds the blocking background.

### 6.2 If `check_conflicts` is false

Skip all overlap logic. For each pending event, in original order: if its
start/end cannot be parsed (`event_interval_or_none` returns `None`) decline it
as malformed (reason `"event start/end time is malformed"`); otherwise **accept**
it. Return.

### 6.3 If `check_conflicts` is true

1. **Seed the baseline.** Compute the interval of every `existing_events` entry;
   keep `(event, interval)` pairs whose interval parsed successfully.
2. **Process pending oldest-first.** Sort pending events by
   `str(event.get("created", ""))` ascending, so among mutually conflicting
   requests the **earliest-created booking wins** deterministically.
3. For each pending event:
   - Parse its interval; if `None`, decline as malformed and continue.
   - Find the first baseline interval that overlaps (`intervals_overlap`).
   - **No overlap → accept.** Then, *unless the event is transparent*, append its
     `(event, interval)` to the baseline so later pending events also avoid
     colliding with this newly-accepted one (the baseline grows as accepts
     accumulate).
   - **Overlap → decline** with reason
     `"conflicts with existing event '<summary>' (ID: <id>)"` naming the
     conflicting event.

### 6.4 Interval and overlap math

- `event_interval(event, tz)` returns `(start + 1s, end - 1s)`. The one-second
  **inward nudge** is deliberate: back-to-back events (one ending exactly when
  the next begins) do **not** count as overlapping.
- `intervals_overlap(left, right)` is `left[0] <= right[1] and right[0] <= left[1]`
  (closed-interval overlap).
- `event_time(value, tz)` parses a Calendar start/end field:
  - **Timed event** (`"dateTime"` present): `datetime.fromisoformat`. If it
    carries an offset, use it. If offset-less, fall back to the field's own
    `"timeZone"` (`ZoneInfo(...)`), and if that is absent, to the configured
    `timezone`.
  - **All-day event** (`"date"` present): anchored to **midnight in the
    configured `timezone`** (`datetime.combine(date, time(), tzinfo=tz)`).
  - Missing both keys → `KeyError`, surfaced as malformed.
- `event_interval_or_none` wraps the above and, on `KeyError`/`TypeError`/
  `ValueError`/`ZoneInfoNotFoundError`, logs a `WARNING` naming the event and
  returns `None` instead of raising. Malformed **existing** events are simply
  dropped from the baseline; malformed **pending** events are declined.

The function returns a list of frozen `EventDecision(event, response, reason,
attendee_email)`. Because out-of-domain declines are produced in pass 6.1 before
any accepts, a calendar's decision list naturally orders **declines before
accepts** (visible in the write order, see [§7](#7-applying-responses)).

## 7. Applying responses

`respond_to_decisions(service, calendar, decisions, dry_run, log)` walks the
plan's decisions and, for each:

- Logs an `INFO` line: `Event '<summary>' (ID: <id>) will be <response>[ because <reason>]`.
- **Skips events without an `id`** (logs a `WARNING`, "refusing to respond") —
  the API cannot patch an event with no ID. A missing `summary` is labelled
  `untitled` but still acted on (so a malformed unauthorized reservation is still
  declined rather than left pending forever).
- **Dry-run:** logs `dry-run: would have <response> <summary> <id>` and issues
  **no** API call. (Matches the global
  [dry-run/write-safety contract](../intro/spec.md#dry-run-and-write-safety):
  reads everything, writes nothing.)
- **Live:** calls `patch_attendee_response(service, calendar_id, event_id,
  response, attendee_email=decision.attendee_email)`. That helper PATCHes the
  event with `sendUpdates="all"` (so other participants are notified) and a body
  of `attendeesOmitted: True` plus a single-element `attendees` list
  `{email, responseStatus}` — updating **only this resource's RSVP** and leaving
  all other attendees untouched. `attendee_email` preserves Google's exact-case
  address. The Calendar write uses a **one-shot** retry policy
  (`RetryPolicy(attempts=1)`) so a notification-sending PATCH cannot duplicate on
  retry (see [retry policy](../intro/spec.md#retry-policy)).

**Preflight ordering guarantee:** because `process_calendars` builds *every*
calendar's plan (all `list_events` + decisions) before any `respond_to_decisions`
runs, a listing failure on a *later* calendar prevents *earlier* calendars from
being patched at all — the run fails before any write. This is asserted in the
tests.

## 8. Outputs, reporting, and notifications

- **Console / file logging** via `setup_logging` with logger name
  `parishkit.pk_validate_gcalendar_reservations`. Notable nuance: verbosity is
  `verbose = common.verbose or common.dry_run` — **dry-run forces INFO-level
  logging on** so a dry run is self-explanatory. Logged events include the
  configured calendar/domain/timezone summary (`INFO`), per-calendar download
  counts and the resolved time window (`INFO`), and `DEBUG` lines with
  `log_extra(...)` JSON payloads of the calendars, raw events, and computed
  decisions for the JSONL file. Each decision's "will be accepted/declined" line
  and a final `... validation completed successfully for N calendar(s)` line are
  emitted at `INFO`.
- **JSONL log file** — one JSON object per line via the shared
  `CompressingRotatingFileHandler`, carrying the `extra` payloads above. See
  [logging and notifications](../intro/spec.md#logging-and-notifications).
- **Slack** — failure alerting only, through the shared `SlackLogHandler`
  (default threshold `CRITICAL`); configured via the shared `slack` section.
- **No email.** This tool does **not** use the
  [email provider layer](../intro/spec.md#email-provider-layer); there is no
  `email` config section and no change-summary email. (Flagged because the task
  brief anticipated an optional email summary — the source has none.)
- **No JSONL report artifact** beyond the standard log file; the tool does not
  write to `reports/`.

## 9. Failure modes and exit codes

- **`--version`** → prints and returns `0`.
- **Success** → `_run` returns `0` after processing all calendars.
- **Expected operational failures** → the whole body runs inside
  `run_user_facing(...)` ([error funnel](../intro/spec.md#shared-cli-layer)),
  which converts `ConfigError`, `OSError`, `GoogleAPIError`, `RetryError`, etc.
  into a single `ERROR: ...` stderr line and **exit code 2** (no traceback).
- **Config-validation failures specifically** (missing/explicit-write-mode,
  bad `google`/`calendars` config, bad credentials build) are caught by an inner
  `except ConfigError` that first logs `Configuration validation failed: <exc>`
  at `ERROR` (so it appears in the JSONL log and via the console/Slack handlers)
  and then re-raises into the funnel → exit 2. Runtime `GoogleAPIError`s raised
  during `process_calendars` are *not* given that prefix; they go straight to the
  funnel.
- **`require_explicit_write_mode`** raises `ConfigError` (→ exit 2) when
  `common.dry_run` was never set explicitly, refusing to default to live writes.
- **Unexpected exceptions** (genuine bugs) propagate as real tracebacks.

## 10. Edge cases and nuances (as actually coded)

- **All-day events** (`{"date": ...}`) are anchored to midnight in the configured
  timezone; they participate in overlap math like any timed event.
- **Recurring events** are expanded to individual instances by `singleEvents=True`
  in `list_events`; each instance is evaluated independently.
- **Offset-less `dateTime`** falls back to the event field's own `timeZone`, then
  to `calendars.timezone` — so a pending event in one zone is correctly compared
  against an existing event in another (tested with LA vs NY).
- **Back-to-back events do not conflict** thanks to the ±1-second inward nudge in
  `event_interval`.
- **Already-responded invitations:** `accepted` (and `tentative`, and events
  where the resource is owner / not an attendee) become non-decided **background
  bookings** that block conflicting pending events; `declined` events are ignored
  completely.
- **Transparent / free events** (`transparency == "transparent"`) never block a
  reservation — they are excluded from the conflict baseline, including a
  pending event that gets accepted but is itself transparent (it is not added to
  the growing baseline).
- **Out-of-domain creators** are declined regardless of conflicts; an email with
  no `@` yields domain `""`, which is not in `acceptable_domains` unless an empty
  string was explicitly configured.
- **Malformed times:** a pending event with unparseable/absent start or end (or
  an unknown per-event `timeZone`) is **declined** with
  `"event start/end time is malformed"`; a malformed *existing* event is dropped
  from the baseline (logged at WARNING). Both behaviors are exercised in tests.
- **Events with no `id`** are skipped at write time with a WARNING (cannot patch).
- **Events with no `summary`** are still patched/labelled `untitled`.
- **Case-insensitivity:** both the resource attendee match (`calendar_id` vs
  attendee `email`) and the creator-domain match are casefolded; the patch
  preserves Google's exact-case attendee address.
- **Tie-breaking:** mutually conflicting pending requests are resolved
  oldest-`created`-first, so the earliest booking deterministically wins and
  later overlapping requests are declined.
- **`check_conflicts: false`** short-circuits: pending events are accepted (or
  declined only if malformed) with no baseline computed at all.

## 11. Testing notes

`tests/test_calendar_reservations.py` locks down the behavior with fakes (no real
credentials or network), exercising the `service_factory`/`now` injection seams
on `main` and a fake `Service`/`Events`/`Request` that records `list`/`patch`
calls and simulates pagination by popping canned pages. Key assertions:

- **Config validation:** `acceptable_domains` is casefolded to a frozenset;
  `ReservationCalendar` entries build correctly; `calendars.timezone` falls back
  to the supplied `default_timezone`; missing `acceptable_domains`, an unknown
  per-calendar key (`unsupported key`), and a non-string `calendar_id`
  (message names the entry, type, and the indentation hint) all raise
  `ConfigError`; a YAML boolean is rejected for `lookback_days`.
- **Decisions:** out-of-domain creators and time conflicts are declined,
  others accepted, already-accepted events skipped; with `check_conflicts: false`
  only pending events are decided and no baseline is consulted; offset-less
  datetimes honor their per-event `timeZone` (LA existing vs NY pending →
  conflict); malformed pending times are declined while malformed existing times
  are skipped (with WARNING log); transparent events don't block.
- **End-to-end `main`:** paginates two list pages (`nextPageToken`), patches
  **declined-before-accepted**, sends `sendUpdates="all"` +
  `attendeesOmitted: True`, uses the resolved `timeMin` (one-month lookback from
  the injected `now`), and matches attendee email **case-insensitively**
  (`Room@Example.Org`). A later-calendar listing `GoogleAPIError` yields exit
  `2` and **no patch calls** (preflight). Events without a `summary` are still
  patched by ID. **Dry-run issues no patches.** A tool-specific config error is
  logged at `ERROR` with the `Configuration validation failed` prefix and exits
  `2`. Relative `google.user_token_file` paths resolve against the config
  directory.

## 12. Re-creation task outline

1. Define frozen dataclasses `ReservationCalendar`, `ReservationConfig`,
   `EventDecision`, `CalendarDecisionPlan` and `CALENDAR_SCOPE`.
2. Implement `calendar_reservation_config(config, default_timezone)` validating
   the `calendars` section: allowed-key set; non-empty string-list
   `acceptable_domains` (casefolded → frozenset); non-empty `calendars` list with
   per-entry validation (`name`, `calendar_id`/`id`, `check_conflicts`, detailed
   error messages); IANA `timezone` (default = `default_timezone`); positive-int
   `lookback_days`/`lookahead_days` (reject bool) defaulting 31 / 547.
3. Implement `load_calendar_credentials(config, base_dir)`: allowed-key set on
   `google`; exactly-one-of service-account / user-token; optional
   `delegated_subject`; load with `[CALENDAR_SCOPE]`, resolving paths against the
   config dir.
4. Implement event helpers: `calendar_attendee`/`attendee_status` (casefold
   match), `creator_domain`, `event_is_transparent`, `event_time` (offset →
   field `timeZone` → config tz; all-day midnight), `event_interval` (±1s nudge),
   `event_interval_or_none` (log + skip), `intervals_overlap`,
   `malformed_time_decision`.
5. Implement `reservation_decisions`: classify by resource status (pending /
   declined-ignore / blocking background incl. transparent filter); decline
   out-of-domain pending; `check_conflicts: false` accept path; conflict path
   (seed baseline, oldest-`created`-first, grow baseline on non-transparent
   accepts, decline on overlap).
6. Implement `respond_to_decisions`: skip no-id events, `untitled` label,
   dry-run logging, live `patch_attendee_response`.
7. Implement `process_calendars`: compute UTC window from `now`, **build all
   plans before applying any** (preflight), then apply.
8. Implement `main`/`_run`: `--version`; `parser_with_common_options`; resolve
   `CommonOptions`; `setup_logging` with `verbose=verbose or dry_run`;
   `require_explicit_write_mode`; build service from `service_factory` or
   credentials; run; wrap in `run_user_facing`; log `ConfigError` at ERROR.
9. Wire the `calendar_reservations_main` lazy entry point in `cli.py` and the
   `pyproject.toml [project.scripts]` mapping; add the executable wrapper.
10. Ship `example-config.yaml` (dry_run true, the `google` + `calendars`
    sections) and the operator README; add the mocked tests above.

## 13. Cross-references

- [Top-level system spec](../intro/spec.md) — shared architecture and
  philosophy.
- [Shared CLI layer](../intro/spec.md#shared-cli-layer) — common flags,
  `CommonOptions`, `require_explicit_write_mode`, `run_user_facing`.
- [Configuration system](../intro/spec.md#configuration-system) —
  `load_yaml_config`, `reject_unknown_keys`, `resolve_path`.
- [Logging and notifications](../intro/spec.md#logging-and-notifications) —
  console/JSONL/Slack sinks.
- [Retry policy](../intro/spec.md#retry-policy) — one-shot write policy.
- [Google integration layer](../intro/spec.md#google-integration-layer) —
  auth/domain-wide delegation, `list_events`, `patch_attendee_response`.
- [Dry-run and write safety](../intro/spec.md#dry-run-and-write-safety) —
  the write-mode contract this tool obeys.
- [`docs/parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) — for
  context only; **this tool does not use ParishSoft.**
- Operator docs: `scripts/pk-validate-gcalendar-reservations/README.md` and
  `example-config.yaml`.
