# pk-create-ps-ministry-rosters — ParishSoft rosters into Google Drive/Sheets

## 1. Purpose & role

`pk-create-ps-ministry-rosters` reads ministry and member-workgroup membership
from ParishSoft and publishes one formatted roster table per configured target
into Google Drive as a native Google Sheet, so ministry leaders always see an
up-to-date roster without exporting anything by hand. Each configured roster
names one or more ParishSoft sources (ministries or a member workgroup); the
tool builds a 2-D cell grid (title, "Last updated" timestamp, headers, one or
two rows per member), creates a local XLSX workbook with a fixed visual layout
(frozen header rows, blue/yellow header styling, merged title, column widths),
and uploads that workbook over the configured Drive file ID with conversion
back to Google Sheets.

In the [tool catalog](../intro/spec.md#tool-catalog) this is a **Sync/report**
tool that writes to **Google Drive/Sheets**. Unlike the membership-sync tools it
does not reconcile a remote membership set: it always rebuilds and replaces the
target Drive file from ParishSoft (ParishSoft is the system of record per the
[design intent](../intro/spec.md#design-intent-and-goals)). Note that ministry
rosters are **read-only in both ParishSoft API generations** — this tool can
publish roster state outward but cannot push roster edits back into ParishSoft;
see [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) §3.3.

It follows the shared [architecture skeleton](../intro/spec.md#architecture-and-data-flow):
parse argv → load/validate YAML → resolve `CommonOptions` → set up logging →
build ParishSoft + Drive clients → read source → build desired state → upload
(or, in dry-run, only report).

## 2. Invocation

- **Console command:** `pk-create-ps-ministry-rosters` (declared in
  `pyproject.toml [project.scripts]` as
  `pk-create-ps-ministry-rosters = "parishkit.cli:create_ministry_rosters_main"`).
- **cli.py entry point:** `create_ministry_rosters_main(argv=None)` in
  `src/parishkit/cli.py` lazily imports `parishkit.pk_create_ps_ministry_rosters`
  and delegates to its `main()`. Lazy import keeps the optional Google/Slack
  dependencies out of the base CLI import path
  ([shared CLI layer](../intro/spec.md#shared-cli-layer)).
- **Wrapper script:** `scripts/pk-create-ps-ministry-rosters/pk-create-ps-ministry-rosters.py`
  is a `#!/usr/bin/env python3` shim that does
  `raise SystemExit(create_ministry_rosters_main())` and nothing else.
- **`--version`:** prints `pk-create-ps-ministry-rosters <version>` (from
  `importlib.metadata.version("parishkit")`) and returns 0 before any config or
  credential work. It is handled directly in `main()` ahead of `run_user_facing`.

`main(argv=None, *, loader=load_families_and_members, drive_factory=None)` is
the real entry; `loader` and `drive_factory` are dependency-injection seams for
tests (see §11).

## 3. Command-line options

All flags come from the shared parser
(`parser_with_common_options`); this tool adds only `--version`. Tool-specific
behavior is driven almost entirely by config, not flags.

- **`--version`** — boolean; prints the installed version and exits 0.

Shared flags (see [#shared-cli-layer](../intro/spec.md#shared-cli-layer) for
full semantics and precedence): `--config`; tri-state
`--dry-run/--no-dry-run`, `--verbose/--no-verbose`, `--debug/--no-debug`;
`--log-file`, `--log-dir`; `--slack-token-file`, `--slack-channel`,
`--slack-log-level`; `--ps-api-key-file`, `--ps-cache-dir`, `--ps-cache-limit`.

This tool is **write-capable**, so `require_explicit_write_mode` applies: the
operator must set `common.dry_run` explicitly (CLI flag **or** config) or startup
fails with a `ConfigError`. See
[#dry-run-and-write-safety](../intro/spec.md#dry-run-and-write-safety).

There is no tool-specific Google flag: the service account / user token paths and
delegated subject are config-only (the `google` section, §4).

## 4. Configuration schema

The config is one YAML file (`--config`). Shared sections (`common`, `logging`,
`slack`, `parishsoft`) are validated centrally by `resolve_common_options`
(see [#configuration-system](../intro/spec.md#configuration-system)); only the
tool-specific `google` and `rosters` sections are described in detail here. The
shipped template is
[`scripts/pk-create-ps-ministry-rosters/example-config.yaml`](../../../scripts/pk-create-ps-ministry-rosters/example-config.yaml).

### 4.1 Shared sections (used by this tool)

- **`common`** — `dry_run` (must be set explicitly; example ships `true`),
  `timezone` (IANA name; default `America/Kentucky/Louisville`). `timezone`
  drives the "Last updated" timestamp and its abbreviation; `dry_run` gates all
  writes. `verbose`/`debug` also accepted.
- **`parishsoft`** — `api_key_file`, `cache_dir`, `cache_limit`,
  `expected_organization` (tenant guard). Consumed by
  `parishsoft_client_from_config`; see
  [#parishsoft-data-layer](../intro/spec.md#parishsoft-data-layer).
- **`slack`** — optional failure alerting (`token_file`, `channel`, `level`).
- **`logging`** — optional `log_file` / `log_dir`.

### 4.2 `google` section

Validated by `load_drive_credentials`. Only built/read when **not** dry-run
(dry-run never touches Google), so a dry-run config may omit `google` entirely
(asserted by `test_create_ministry_rosters_dry_run_does_not_require_google_config`).

Allowed keys (others rejected by `reject_unknown_keys`):

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_account_file` | string (path) | one of the two | Service-account JSON; resolved via `resolve_path` (config-relative). Loaded with `load_service_account_credentials(scopes=[DRIVE_SCOPE], subject=delegated_subject)`. |
| `user_token_file` | string (path) | one of the two | Installed-app OAuth user token; resolved config-relative. Loaded with `load_user_credentials(scopes=[DRIVE_SCOPE])`. |
| `delegated_subject` | string | optional | Real Workspace user the service account impersonates (domain-wide delegation). Must be a string if present. Applies only to the service-account path. |

Validation rules:

- Setting **both** `service_account_file` and `user_token_file` → `ConfigError`.
- Setting **neither** → `ConfigError`
  ("`google.service_account_file or google.user_token_file is required`").
- `delegated_subject` non-string → `ConfigError`.
- Only the single scope `DRIVE_SCOPE =
  "https://www.googleapis.com/auth/drive"` is requested.
- The broad Drive scope is intentional. The tool updates pre-existing
  configured spreadsheet file IDs in unattended automation, so the narrower
  `drive.file` scope for app-created or user-selected files is not a good fit.

The delegated user must hold **Editor** access to every target spreadsheet file
(or edit-capable access to the shared drive containing it), and the service
account's OAuth client must be authorized for the Drive scope in Workspace
Admin. See [#google-integration-layer](../intro/spec.md#google-integration-layer)
for the delegation mechanics.

### 4.3 `rosters` section

Parsed by `roster_config_from_yaml` into a `RosterConfig`. The section itself is
a mapping (default `{}`); allowed top-level keys (rejecting all others):
`spreadsheet_id`, `workgroup_leader_suffix`, `ministries`, `workgroups`.

**Top-level roster defaults** (applied to every target that omits them):

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `spreadsheet_id` | string | none | Default Google Drive spreadsheet file ID. Optional only if every target sets its own. |
| `workgroup_leader_suffix` | string | `" Ldr"` (`DEFAULT_LEADER_SUFFIX`) | Suffix that names the companion "leaders" workgroup in ParishSoft. |

`spreadsheet_id` must be a string if present; `workgroup_leader_suffix` must be
a string.

At least one of `ministries` or `workgroups` must be non-empty, else
`ConfigError("rosters must configure ministries or workgroups")`. Both must be
lists if present.

**Each `ministries[]` entry** (parsed by `_target`, `source_type="ministry"`).
Allowed keys: `name`, `ministry`, `ministries`, `spreadsheet_id`,
`include_birthday`, `birthday`, `role_sheets`, `role sheets`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `ministry` | string | one source form | A single ParishSoft ministry name. |
| `ministries` | non-empty list[str] | one source form | Multiple ministry names (members from any of them). |
| `name` | string | optional | Display title; defaults to the comma-joined source names. |
| `spreadsheet_id` | string | required (or inherited) | Drive spreadsheet file ID override; falls back to `rosters.spreadsheet_id`. |
| `include_birthday` / `birthday` | bool | optional (default `false`) | Adds a "Birthday" column. `include_birthday` wins; `birthday` is the accepted alias. |
| `role_sheets` / `role sheets` | list | optional | Per-role breakout sheets (below). Both spellings accepted. |

Setting both `ministry` and `ministries` → `ConfigError`. A missing source form
→ `ConfigError(".ministry is required")`. `_string_list` enforces a non-empty
list of strings for `ministries`.

**Each `workgroups[]` entry** (parsed by `_target`, `source_type="workgroup"`,
**no plural source key**). Allowed keys are the same set minus the plural; the
only source key is `workgroup` (a single string, required). Workgroup targets
support `name`, `spreadsheet_id`, and `include_birthday`/`birthday`.
`role_sheets` / `role sheets` are ministry-only and are rejected as unsupported
keys on workgroups because ignoring configured outputs would be unsafe.

**Each `role_sheets[]` entry** (parsed by `_role_target`). Allowed keys: `name`,
`roles`, `spreadsheet_id`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | required | Display title of the breakout sheet. |
| `roles` | non-empty list[str] | required | Roles kept on this sheet; matched exactly against pieces of the member's joined role text. |
| `spreadsheet_id` | string | required | Drive spreadsheet file ID for this role-specific output. It must be separate from the parent because uploads replace whole files. |

**Cross-cutting validation** (run during `roster_config_from_yaml`):

- `validate_unique_roster_targets` — no two outputs (ministries, their role
  sheets, and workgroups) may share the same `spreadsheet_id`. Whole-file uploads
  replace the target Drive file, so sharing a file ID would overwrite another
  output (`test_roster_config_rejects_duplicate_drive_files`).

**Known dead-code / ambiguity:** `_target_spreadsheet_id` reads
`item.get("spreadsheet_id", item.get("gsheet_id", default))`, but `gsheet_id` is
**not** in the allowed-keys set for either target or role-sheet parsing, so a
`gsheet_id` key is rejected by `reject_unknown_keys` before the fallback is ever
reached. The `gsheet_id` alias is therefore unreachable today — flag for cleanup
or for adding it to the allowed set if the alias is intended.

## 5. Source data

The tool calls
`loader(client, active_only=True, parishioners_only=False)` (default loader
`load_families_and_members`), so it loads **active** members without restricting
to registered parishioners. The ParishSoft data layer first validates that the
API key can see exactly one organization and then scopes family/member searches
to that organization ID; zero or multiple visible organizations fail before
roster generation. The loader call shape is asserted in
`test_create_ministry_rosters_main_uploads_workbooks`
(`loader_calls == [{"active_only": True, "parishioners_only": False}]`).

It consumes these derived fields produced by the
[ParishSoft data layer](../intro/spec.md#parishsoft-data-layer) (see
[`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) for which API
endpoints back them):

- `member["py ministries"]` — keyed by ministry name; each entry has `role`
  (the `ministryRoleName`), dates, and the raw record. **Only currently-active
  memberships are present**: `make_member_ministries` filters via
  `ministry_membership_is_current` (start not in the future, end date exclusive),
  so expired/future roster rows never appear. A ministry membership with neither
  a start nor end date is treated as *not current* and is dropped.
- `member["py workgroups"]` — keyed by workgroup name; used to detect base vs.
  leader workgroup membership.
- `member["py friendly name LF"]` — "Last, First" display name (preferred first
  name / nickname when available); falls back to `member_display_name` if absent.
- `member["py family"]` — the linked family (for address fields
  `primaryAddress1/2`, `primaryCity/State/PostalCode`).
- Privacy-aware contact: `get_member_public_phones` (honors
  `family_PublishPhone`, returns mobile→"cell" and home→"home") and
  `get_member_public_email` (honors `family_PublishEMail`, first normalized
  address).
- `member["birthdate"]` — used only when `include_birthday` is set.

The salutation helper `salutation_for_members` is **not** used by this tool;
rosters show plain "Last, First" names.

After loading, `validate_configured_parishsoft_sources` checks that every
configured source name exists in the loaded data **before** building the Drive
service or uploading anything:

- `available_ministry_names` = ministry names from
  `data.ministry_type_memberships` plus every key of every member's
  `py ministries`.
- `available_member_workgroup_source_names` = workgroup names from
  `data.member_workgroup_memberships` plus every member's `py workgroups` keys,
  **plus** the base name derived by stripping any of the suffixes
  (`workgroup_leader_suffix`, `" Ldr"`, `" Leader"`) — so a parish that only has
  the leader companion workgroup still validates the base name.
- A missing ministry or workgroup raises a `ConfigError` that names the target,
  the offending source, the config key to check, and lists the available names.

## 6. Roster-building algorithm

`write_configured_rosters` orchestrates everything. Order of operations:

1. **Compute the update time once** — `current_roster_time(timezone_name)` =
   `datetime.now(ZoneInfo(timezone))` truncated to whole seconds. The same
   timestamp is stamped on every roster in the run.
2. **Build plans** in a fixed order: for each ministry target, the main roster
   plan, then each of its role-sheet plans; then each workgroup target. Plans are
   built fully **before** any upload, so ParishSoft source/config failures abort
   the run before any target file is touched.
3. **Prepare and upload** each plan in order via `prepare_roster_uploads` and
   `upload_prepared_roster`. Dry runs log the intended upload and never build
   Google credentials.

### 6.1 Ministry member selection (`ministry_roster_members`)

- A member is included if **any** key of `member["py ministries"]` is in the
  configured source-name set.
- The member's `role` cell is built from the matching ministries' roles:
  deduplicate (`{... .strip()}`), sort, drop blanks, and join with `", "`.
- A member whose only matching role is blank is **still included**, with an
  empty role cell (ParishSoft allows roleless membership).
- Results are sorted by `member_sort_key`.

### 6.2 Workgroup member selection (`workgroup_roster_members`)

- Only the **first** source name is used (`target.source_names[0]`; workgroups
  have exactly one source).
- Leaders are members in the companion workgroup
  `workgroup_name + leader_suffix` → role `"Leader"`; members in the base
  workgroup → role `"Member"`. The leader check is first, so a member in both
  shows as `"Leader"`.
- Sorted by `member_sort_key`. The custom suffix is honored end-to-end
  (`test_create_ministry_rosters_validation_uses_custom_leader_suffix`).

### 6.3 Role-sheet filtering (`roster_role_matches`)

A role sheet keeps a member when any comma-split, stripped piece of that
member's joined role text is in the sheet's `roles` set. Matching is
**exact-string** on each piece (`roster_role_matches("Lead, Member", {"Lead"})`
is true). Role sheets reuse the parent target's already-computed member list and
its `include_birthday` setting.

### 6.4 Cell-grid construction (`roster_values`)

For a title and a member list (re-sorted by `member_sort_key`):

- Row 0: `["Ministry: {title}"]` — note the literal `"Ministry: "` prefix is used
  for **every** roster, including role sheets and workgroup rosters (a known
  cosmetic quirk: a workgroup roster reads "Ministry: &lt;workgroup&gt;").
- Row 1: `["Last updated: {timestamp}"]` where `timestamp` =
  `format_update_timestamp(now)` → `"%Y-%m-%d %H:%M:%S %Z"` (the `%Z`
  abbreviation is appended only when known, e.g. `EST`).
- Row 2: `[]` (blank spacer).
- Row 3: header row `["Member name", "Address", "Phone / email"]` + `"Birthday"`
  (only if `include_birthday`) + `"Role"`.
- Rows 4+: one or two rows per member via `roster_member_rows`.

`now` defaults to `datetime.now()` but is injected (the run-wide
`update_time`) for a deterministic, timezone-aware timestamp.

### 6.5 Per-member rows (`roster_member_rows`)

- Main row cells: `[name, address, first_contact]` (+ birthday if enabled) +
  `[role]`.
  - `name` = `py friendly name LF` or `member_display_name` ("Last, First",
    stray `", "` stripped).
  - `address` = `member_address`: `primaryAddress1`, `primaryAddress2`, and
    `city_state_zip` joined with `"\n"`, skipping blanks. `city_state_zip`
    comma-joins city/state then appends the postal code after a space.
  - `phone_text` = `member_phone_contact`: each public phone rendered
    `"{number} {type}"`, newline-joined (e.g. `"502-555-1000 cell"`).
  - `email` = `get_member_public_email(member) or ""`.
  - `first_contact = phone_text or email` (phone preferred on the main row).
  - `birthday` = `member_birthday`: `"{%B} {day}"` (e.g. `"May 4"`) from
    `birthdate` (date or datetime), else `""`.
- **Continuation row** — only when **both** phone and email exist: a second row
  `["", "", email]` (+ `""` for birthday if enabled) + `[""]`, so email sits in
  its own row under the phone. If there is no phone, the email stays on the main
  row and no continuation row is produced.

### 6.6 Sorting (`member_sort_key`)

`f"{name} {memberDUID}"` where `name` is the LF display name — a stable, mostly
alphabetical-by-surname ordering with DUID as tiebreaker. (Members are sorted in
both the `*_members` collectors and again in `roster_values`.)

## 7. Google Drive XLSX upload

The roster writer intentionally avoids the Google Sheets API for publishing.
That API path requires multiple calls per roster for values, clearing, and
formatting; using it at parish scale risks exceeding free-tier usage. Instead,
the writer builds a complete `.xlsx` workbook locally with `openpyxl`, then
calls Drive `files().update()` once per configured target with the XLSX file as
media and a Google Sheets MIME type in the request body. Drive replaces the
existing file content and converts the uploaded workbook back into a native
Google Sheet.

See [#google-integration-layer](../intro/spec.md#google-integration-layer) for
shared Google authentication and request execution, and
[#dry-run-and-write-safety](../intro/spec.md#dry-run-and-write-safety) for the
write-mode contract.

### 7.1 Write plan (`write_plan` / `roster_target_plan`)

Each plan captures: `spreadsheet_id`, display `title`, roster `values`, and the
shared `update_time`.

- `roster_target_plan` builds roster values from a `RosterTarget` and its
  selected members.
- `write_plan` wraps already-built values for role-specific roster outputs.
- Plans contain no A1 range, sheet ID, stale-clear range, or formatting request.

### 7.2 Workbook generation (`roster_workbook`)

`roster_workbook(values)` creates a new `openpyxl.Workbook`, uses the active
worksheet, and sets its title to `ROSTER_WORKSHEET_TITLE = "Sheet"`. The sheet
name mirrors the legacy XLSX workflow and avoids depending on pre-existing tab
names in the Google file being replaced.

The workbook writes every value from the 2-D grid exactly as generated by
`roster_values`. String cells are explicitly marked as string data so
ParishSoft text beginning with `=` stays visible text instead of becoming an
XLSX/Google Sheets formula. The workbook then applies layout:

1. Freeze panes at `A5`, equivalent to `ROSTER_FROZEN_ROWS = 4`.
2. Merge `A1:D1` and `A2:D2` (`ROSTER_TITLE_MERGE_COLUMNS = 4`), capped by the
   generated column count.
3. Style rows 1 and 2 with blue fill (`HEADER_BACKGROUND_COLOR = "0000FF"`),
   bold yellow text (`HEADER_TEXT_COLOR = "FFFF00"`), and left horizontal
   alignment.
4. Style row 3 with blue fill only.
5. Style row 4 with the same blue/yellow bold header style and centered
   horizontal alignment.
6. Top-align and wrap roster body cells below the frozen header rows.
7. Set the first five column widths to
   `ROSTER_COLUMN_WIDTHS = (30, 30, 50, 30, 20)`, sliced to the generated column
   count. These are Excel character-width units, not Sheets pixel sizes.

The uploaded workbook is complete, so stale rows from a previous longer roster
cannot remain in the target file.

### 7.3 Drive preflight and uploading plans

In **dry-run**, `log_dry_run_roster_plan` logs
`"dry-run: would upload N row(s) for … to Drive file …"` and returns without
building a workbook or calling Google.

In **live** mode:

1. Before any upload, `preflight_drive_roster_targets` calls
   `get_file_metadata(..., fields="id,name,mimeType,capabilities/canEdit,capabilities/canModifyContent,capabilities/canRename")`
   for every planned output.
2. The run aborts before writing anything if any configured target is missing,
   inaccessible, not editable by the delegated user, not content-modifiable by
   that user, not renameable by that user, or not a native Google Sheet
   (`application/vnd.google-apps.spreadsheet`).
3. `roster_drive_name(title, update_time)` produces the Drive file name:
   `"{safe title} as of {timestamp}"`, where `safe_roster_filename` replaces
   `/` with `-` and `format_update_timestamp` includes the timezone abbreviation
   when available.
4. A temporary directory is created with prefix `parishkit-roster-`.
5. `prepare_roster_uploads` serializes **every** planned workbook into that
   directory before the first Drive update. If local XLSX generation fails for
   any target, the command raises `ConfigError` and no Drive file is touched.
6. `upload_prepared_roster` calls
   `update_file_with_xlsx(drive_service, spreadsheet_id, xlsx_path, name=drive_name)`
   once per prepared workbook.
7. The temporary workbooks are removed when the context exits.

`update_file_with_xlsx` lives in `parishkit.google.drive` and calls:

- `service.files().update(fileId=spreadsheet_id, body={"name": drive_name,
  "mimeType": "application/vnd.google-apps.spreadsheet"}, media_body=MediaFileUpload(...),
  supportsAllDrives=True, fields="id,name,mimeType")`
- `MediaFileUpload(..., mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  resumable=True)`

The `supportsAllDrives=True` flag is required so roster files in Google shared
drives can be replaced.

## 8. Outputs, reporting & notifications

- **Console / logs:** `setup_logging` runs with `verbose = verbose or dry_run`
  (a dry run is always at least verbose). Logged INFO lines include the
  configured ministry/workgroup counts, the loaded member/family/ministry/
  workgroup counts, per-target Drive upload confirmations, and the final
  `"Ministry roster operation completed successfully"`. DEBUG adds per-target
  "Preparing …" lines with structured `extra=log_extra(target)` payloads (in the
  JSONL file only — the human console shows the text, not the dataclass repr,
  confirmed by `assert "RosterTarget(" not in error`). Dry-run logs
  `"dry-run: would upload N row(s) …"` per target.
- **JSONL log file:** the standard structured sink
  ([#logging-and-notifications](../intro/spec.md#logging-and-notifications)),
  carrying the `log_extra` roster-target payloads.
- **Slack:** failure alerting only, via the shared handler at the configured
  threshold; there is no success notification path in this tool.
- **Email:** this tool sends **no** email summary (no email layer is wired in).
- **No generated report files** are written; the Google Sheets are the output.
- **Exit code:** `0` on success (`main`/`_run` return 0); operational failures
  exit `2` via the shared error funnel (§9).

## 9. Failure modes & exit codes

Errors flow through `run_user_facing`
([#shared-cli-layer](../intro/spec.md#shared-cli-layer)): expected operational
errors become a single `ERROR: …` stderr line and **exit code 2**; unexpected
exceptions propagate as tracebacks. `ConfigError`s raised after common options
are resolved and module logging is configured are additionally logged as
`"Configuration validation failed: …"` at ERROR level before re-raising. YAML /
shared common-option failures raised by `resolve_common_options` happen before
the module logger exists and therefore only flow through `run_user_facing`.

| Condition | Surfaced as | Exit |
| --- | --- | --- |
| `common.dry_run` not set explicitly | `require_explicit_write_mode` → `ConfigError` | 2 |
| Invalid YAML | `ConfigError` with line/column + "Check indentation" hint | 2 |
| `rosters` empty / both source keys / unknown key / duplicate output file | `ConfigError` from `roster_config_from_yaml` | 2 |
| `google` missing/both/invalid subject | `ConfigError` from `load_drive_credentials` (live only) | 2 |
| Configured ministry/workgroup name absent from ParishSoft | `ConfigError` listing available names | 2 |
| Drive target missing, inaccessible, non-editable, not content-modifiable, not renameable, or non-Sheets during preflight | `ConfigError` before any upload | 2 |
| Local XLSX workbook generation fails | `ConfigError` before any upload | 2 |
| Drive **403** on upload | `GoogleAPIError` → exit 2 — the delegated Workspace user lacks **Editor** access to that file/shared drive | 2 |
| Drive 429/5xx | one-shot write failure → `GoogleAPIError` / `RetryError` → exit 2 | 2 |
| ParishSoft API / tenant-mismatch errors | `ParishSoftAPIError` / `ConfigError` → exit 2 | 2 |

The repository README and the per-tool README call out the 403 case explicitly:
a Drive 403 on upload means the delegated user is not an Editor of that
spreadsheet file — share it (or its shared drive) with that user as an editor.

## 10. Edge cases & nuances actually in the code

- **Whole-file replacement** means a successful upload cannot leave stale rows
  from a previous longer roster.
- **Plans-before-uploads** — all roster values are built before the first Drive
  upload, so ParishSoft source/config failures abort before any target is
  touched.
- **XLSX serialization-before-uploads** — live mode saves every temporary
  workbook before the first Drive update, so local workbook-generation failures
  do not leave a partially updated roster set.
- **Drive preflight-before-uploads** — live mode checks every target Drive file
  before the first replacement, including `capabilities.canModifyContent` and
  `capabilities.canRename`, so one bad later target does not leave an earlier
  target updated while the rest fail.
- **`"Ministry: " title prefix on everything`** — role sheets and workgroup
  rosters also get the `"Ministry: "` prefix; the title text is the role-sheet
  `name` / workgroup target `name`.
- **Roleless ministry members are kept** with an empty role cell; **blank roles
  are dropped from the joined string** but not from membership.
- **Phone vs. email row split** only happens when both are present; email-only
  members keep email on the main row.
- **Formula-like text is literal** — roster string cells are written as string
  data, so ParishSoft text beginning with `=` is not executed as a spreadsheet
  formula after XLSX import.
- **Privacy flags** (`family_PublishPhone`/`family_PublishEMail`) fully suppress
  the respective contact info, regardless of whether the data exists.
- **Only currently-active ministry memberships** count (start/end-date window;
  no-date memberships excluded) — handled upstream in `make_member_ministries`.
- **`active_only=True, parishioners_only=False`** — inactive/deceased members are
  excluded, but non-registered members are not.
- **Workgroup uses only the first source name**; the leader companion-group
  detection uses the configured suffix, with `" Ldr"`/`" Leader"` also accepted
  when deriving base names for *validation*.
- **Column widths cap at five** — only the first five Excel columns are
  explicitly sized.
- **`gsheet_id` alias is unreachable** (rejected by allowed-keys; see §4.3).
- **Dry-run requires no Google config** and makes no Drive calls at all.
- **No A1 ranges** — legacy `range` / `clear_range` settings are rejected as
  unsupported keys because Drive uploads replace the whole spreadsheet file.

## 11. Testing notes

`tests/test_create_ministry_rosters.py` (mocked; no real credentials) locks down:

- **Credential path resolution** — relative `service_account_file` resolves
  against the config directory (`base_dir`).
- **Config parsing** — keeps `name`, multiple `ministries`, nested role sheets
  including the legacy `"role sheets"` key
  (`test_roster_config_validation_and_role_sheets`); rejects empty targets,
  unknown keys ("unsupported key"), role sheets missing `spreadsheet_id`,
  removed `range`/`clear_range` keys, and duplicate output Drive files.
- **Roster generation** — sort order, ministry roles, workgroup leader-suffix
  detection, and `roster_role_matches`.
- **`roster_values`** — exact title/timestamp/header rows (incl. `EST`
  abbreviation), the phone/email continuation-row split, and birthday formatting.
- **`roster_workbook` / `roster_drive_name`** — workbook sheet name, frozen rows,
  title merges, header styling, body top alignment, formula-like text
  preservation, column widths, and slash-safe timestamped Drive file names.
- **End-to-end `main`** with injected `loader` and `drive_factory`: verifies one
  upload per target, role-sheet/workgroup routing to their own Drive file IDs,
  generated workbook content/formatting, loader kwargs, and the success/target
  log lines.
- **Failure paths** — dry-run skips uploads / needs no Google config; Drive
  preflight failure skips every upload, including the
  `canEdit=true`/`canModifyContent=false` and
  `canModifyContent=true`/`canRename=false` cases; local XLSX build failure
  skips every upload; upload failure stops later uploads; missing ParishSoft
  source, invalid YAML, and config-validation error all exit 2 with expected
  messages; custom leader suffix.

**Injection seams:** `loader` (defaults to `load_families_and_members`) and
`drive_factory` (defaults to `build_drive_service(load_drive_credentials(...))`)
on `main()`. Tests monkeypatch `update_file_with_xlsx` to inspect the generated
temporary workbook before cleanup. `tests/test_google_helpers.py` separately
locks down `update_file_with_xlsx`, including media MIME type, Google Sheets
conversion MIME type, and `supportsAllDrives=True`.

## 12. Re-creation task outline

1. Declare the entry point `create_ministry_rosters_main` in `cli.py` (lazy
   import) and the wrapper script.
2. `main()` — build the shared parser, handle `--version`, delegate to `_run`
   inside `run_user_facing`, with `loader`/`drive_factory` seams.
3. `_run()` — resolve `CommonOptions`; set up logging (`verbose or dry_run`);
   `require_explicit_write_mode`; load YAML; `roster_config_from_yaml`; build the
   ParishSoft client; `loader(active_only=True, parishioners_only=False)`;
   `validate_configured_parishsoft_sources`; build the Drive service only when
   not dry-run; call `write_configured_rosters`; log success; return 0; log
   `ConfigError` as "Configuration validation failed" then re-raise.
4. Config dataclasses (`RosterTarget`, `RoleRosterTarget`, `RosterConfig`,
   `RosterMember`, `RosterWritePlan`) and the `rosters`/`google` parsers with
   exhaustive allowed-key rejection, target default inheritance, source-form
   rules, required role-sheet file IDs, and unique Drive-file validation.
5. ParishSoft source validation against ministry/workgroup names (with
   suffix-stripped base names).
6. Member collectors: `ministry_roster_members` (any-of, dedup/sort/join roles,
   keep roleless), `workgroup_roster_members` (leader-suffix companion group),
   `roster_role_matches`.
7. Grid builders: `roster_values` (title/timestamp/spacer/header + member rows),
   `roster_member_rows` (phone/email continuation split), name/address/
   city-state-zip/phone/email/birthday helpers, `member_sort_key`,
   `current_roster_time`/`format_update_timestamp`.
8. Workbook helpers: `roster_workbook`, `apply_roster_workbook_formatting`,
   `roster_drive_name`, `safe_roster_filename`, and the module constants
   (colors, widths, row indices, frozen-row count).
9. Upload pipeline: `roster_target_plan`/`write_plan`,
   `preflight_drive_roster_targets`, `prepare_roster_uploads`,
   `upload_prepared_roster`, and `google.drive.update_file_with_xlsx` (dry-run
   log; temporary XLSX; Drive metadata preflight; Drive `files.update` with
   conversion and `supportsAllDrives=True`).
10. Operator docs (`README.md`) and `example-config.yaml`; mocked tests covering
    every branch above.

## 13. Cross-references

- [Top-level ParishKit spec](../intro/spec.md) — shared infrastructure:
  [shared CLI](../intro/spec.md#shared-cli-layer),
  [configuration system](../intro/spec.md#configuration-system),
  [logging & notifications](../intro/spec.md#logging-and-notifications),
  [retry policy](../intro/spec.md#retry-policy),
  [ParishSoft data layer](../intro/spec.md#parishsoft-data-layer),
  [Google integration layer](../intro/spec.md#google-integration-layer),
  [dry-run & write safety](../intro/spec.md#dry-run-and-write-safety),
  [tool catalog](../intro/spec.md#tool-catalog).
- [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) — API v2 vs.
  v1; ministry rosters are **read-only in both** (§3.3), constraining this tool
  to publish-only.
- Sibling tools: [`../pk-print-ps-ministries/spec.md`](../pk-print-ps-ministries/spec.md)
  (lists the ministry names that feed this tool's `rosters.ministries`) and
  [`../pk-sync-ps-to-ggroup/spec.md`](../pk-sync-ps-to-ggroup/spec.md)
  (another ParishSoft→Google reconciliation, with membership diffing this tool
  does not do).
- Source of truth: `src/parishkit/pk_create_ps_ministry_rosters.py`,
  `scripts/pk-create-ps-ministry-rosters/{pk-create-ps-ministry-rosters.py,README.md,example-config.yaml}`,
  `tests/test_create_ministry_rosters.py`.
