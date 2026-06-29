# pk-create-ps-ministry-rosters — ParishSoft rosters into Google Sheets

## 1. Purpose & role

`pk-create-ps-ministry-rosters` reads ministry and member-workgroup membership
from ParishSoft and publishes one formatted roster table per configured target
into Google Sheets, so ministry leaders always see an up-to-date roster without
exporting anything by hand. Each configured roster names one or more ParishSoft
sources (ministries or a member workgroup); the tool builds a 2-D cell grid
(title, "Last updated" timestamp, headers, one or two rows per member), writes
it to a configured spreadsheet/tab/range, clears stale rows left by a prior
longer roster, and applies a fixed visual layout (frozen header rows, blue/
yellow header styling, merged title, column widths).

In the [tool catalog](../intro/spec.md#tool-catalog) this is a **Sync/report**
tool that writes to **Google Sheets**. Unlike the membership-sync tools it does
not reconcile a remote membership set: it always rebuilds and overwrites the
target ranges from ParishSoft (ParishSoft is the system of record per the
[design intent](../intro/spec.md#design-intent-and-goals)). Note that ministry
rosters are **read-only in both ParishSoft API generations** — this tool can
publish roster state outward but cannot push roster edits back into ParishSoft;
see [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) §3.3.

It follows the shared [architecture skeleton](../intro/spec.md#architecture-and-data-flow):
parse argv → load/validate YAML → resolve `CommonOptions` → set up logging →
build ParishSoft + Sheets clients → read source → build desired state → write
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

`main(argv=None, *, loader=load_families_and_members, sheets_factory=None)` is
the real entry; `loader` and `sheets_factory` are dependency-injection seams for
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

Validated by `load_sheets_credentials`. Only built/read when **not** dry-run
(dry-run never touches Google), so a dry-run config may omit `google` entirely
(asserted by `test_create_ministry_rosters_dry_run_does_not_require_google_config`).

Allowed keys (others rejected by `reject_unknown_keys`):

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `service_account_file` | string (path) | one of the two | Service-account JSON; resolved via `resolve_path` (config-relative). Loaded with `load_service_account_credentials(scopes=[SHEETS_SCOPE], subject=delegated_subject)`. |
| `user_token_file` | string (path) | one of the two | Installed-app OAuth user token; resolved config-relative. Loaded with `load_user_credentials(scopes=[SHEETS_SCOPE])`. |
| `delegated_subject` | string | optional | Real Workspace user the service account impersonates (domain-wide delegation). Must be a string if present. Applies only to the service-account path. |

Validation rules:

- Setting **both** `service_account_file` and `user_token_file` → `ConfigError`.
- Setting **neither** → `ConfigError`
  ("`google.service_account_file or google.user_token_file is required`").
- `delegated_subject` non-string → `ConfigError`.
- Only the single scope `SHEETS_SCOPE =
  "https://www.googleapis.com/auth/spreadsheets"` is requested.

The delegated user must hold **Editor** access to every target spreadsheet (or
edit-capable access to the shared drive containing it), and the service
account's OAuth client must be authorized for the Sheets scope in Workspace
Admin. See [#google-integration-layer](../intro/spec.md#google-integration-layer)
for the delegation mechanics.

### 4.3 `rosters` section

Parsed by `roster_config_from_yaml` into a `RosterConfig`. The section itself is
a mapping (default `{}`); allowed top-level keys (rejecting all others):
`spreadsheet_id`, `range`, `clear_range`, `workgroup_leader_suffix`,
`ministries`, `workgroups`.

**Top-level roster defaults** (applied to every target that omits them):

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `spreadsheet_id` | string | none | Default Google spreadsheet ID. Optional only if every target sets its own. |
| `range` | string (A1) | `Roster!A1` (`DEFAULT_RANGE`) | First cell of the write region; sheet/tab prefix before `!` must exist. |
| `clear_range` | string (A1) | `Roster!A:Z` (`DEFAULT_CLEAR_RANGE`) | Column or bounded range erased of stale rows after a write. |
| `workgroup_leader_suffix` | string | `" Ldr"` (`DEFAULT_LEADER_SUFFIX`) | Suffix that names the companion "leaders" workgroup in ParishSoft. |

`spreadsheet_id` must be a string if present; `range`/`clear_range` are
"optional strings" (treated as unset when `None` or `""`);
`workgroup_leader_suffix` must be a string.

At least one of `ministries` or `workgroups` must be non-empty, else
`ConfigError("rosters must configure ministries or workgroups")`. Both must be
lists if present.

**Each `ministries[]` entry** (parsed by `_target`, `source_type="ministry"`).
Allowed keys: `name`, `ministry`, `ministries`, `spreadsheet_id`, `range`,
`clear_range`, `include_birthday`, `birthday`, `role_sheets`, `role sheets`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `ministry` | string | one source form | A single ParishSoft ministry name. |
| `ministries` | non-empty list[str] | one source form | Multiple ministry names (members from any of them). |
| `name` | string | optional | Display title; defaults to the comma-joined source names. |
| `spreadsheet_id` | string | required (or inherited) | Per-target override; falls back to `rosters.spreadsheet_id`. |
| `range` | string (A1) | optional | Falls back to `rosters.range` then `Roster!A1`. |
| `clear_range` | string (A1) | optional | Falls back to `rosters.clear_range` then `Roster!A:Z`. |
| `include_birthday` / `birthday` | bool | optional (default `false`) | Adds a "Birthday" column. `include_birthday` wins; `birthday` is the accepted alias. |
| `role_sheets` / `role sheets` | list | optional | Per-role breakout sheets (below). Both spellings accepted. |

Setting both `ministry` and `ministries` → `ConfigError`. A missing source form
→ `ConfigError(".ministry is required")`. `_string_list` enforces a non-empty
list of strings for `ministries`.

**Each `workgroups[]` entry** (parsed by `_target`, `source_type="workgroup"`,
**no plural source key**). Allowed keys are the same set minus the plural; the
only source key is `workgroup` (a single string, required). Workgroup targets
support `name`, `spreadsheet_id`, `range`, `clear_range`,
`include_birthday`/`birthday`, and `role_sheets`/`role sheets` exactly as
ministries do. Workgroup `role_sheets` are parsed into the target config for
schema compatibility, but current write planning ignores them: only the main
workgroup roster sheet is preflighted and written.

**Each `role_sheets[]` entry** (parsed by `_role_target`). Allowed keys: `name`,
`roles`, `spreadsheet_id`, `range`, `clear_range`.

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `name` | string | required | Display title of the breakout sheet. |
| `roles` | non-empty list[str] | required | Roles kept on this sheet; matched exactly against pieces of the member's joined role text. |
| `spreadsheet_id` | string | optional | Defaults to the parent target's resolved `spreadsheet_id`. |
| `range` | string (A1) | optional | Defaults to the parent target's resolved `range`. |
| `clear_range` | string (A1) | optional | Defaults to the parent target's resolved `clear_range`. |

**Cross-cutting validation** (run during `roster_config_from_yaml`):

- `_validate_same_sheet_range` — for every target and role sheet, the
  `clear_range` tab must equal the `range` tab, else
  `ConfigError("…clear_range must target the same sheet as …range")`. Tab name is
  extracted by `sheet_name_from_a1_range` (handles quoted/escaped names; a range
  with no `!` is treated as tab `"Sheet1"`).
- `validate_unique_roster_targets` — no two outputs (ministries, their role
  sheets, and workgroups) may share the same `(spreadsheet_id, range)`. This is
  what stops a role sheet that inherits the parent's range from overwriting the
  parent roster (`test_roster_config_rejects_duplicate_output_ranges`).

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
`test_create_ministry_rosters_main_writes_sheet_values`
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
configured source name exists in the loaded data **before** building the Sheets
service or writing anything:

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
2. **Preflight** (live only) — `preflight_roster_targets` (see §7).
3. **Build plans** in a fixed order: for each ministry target, the main roster
   plan, then each of its role-sheet plans; then each workgroup target. Plans are
   built fully **before** any write, so a config/width/missing-tab problem in a
   later target aborts the run before earlier targets are touched
   (`test_create_ministry_rosters_plans_generated_width_before_writes`).
4. **Apply** each plan in order via `write_values`.

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

## 7. Google Sheets writing

See [#google-integration-layer](../intro/spec.md#google-integration-layer) for
the Sheets helper internals (`get_spreadsheet`, `update_values`, `clear_values`,
`batch_update_spreadsheet`) and [#dry-run-and-write-safety](../intro/spec.md#dry-run-and-write-safety).

### 7.1 Range resolution & A1 helpers

- `sheet_name_from_a1_range` extracts the tab from a sheet-qualified A1 range,
  unquoting `'...'` and unescaping `''`; a range without `!` yields `"Sheet1"`.
- `a1_column_number` converts a column label to a 1-based number;
  `a1_start_row` reads the starting row of the write range (default 1).
- `clear_range_width` parses `clear_range` with `_CLEAR_RANGE_RE` (accepts
  column ranges like `Roster!A:Z` and bounded ranges like `Roster!A1:Z500`,
  tolerating `$` absolute markers) and returns the column span; a malformed range
  → `ConfigError`, and an end-before-start span → `ConfigError`.

### 7.2 Preflight (`preflight_roster_targets`, live runs only)

Before the first write, for every `(spreadsheet_id, range, clear_range)` tuple
in write order (`configured_sheet_ranges`):

1. `clear_range_width(clear_range)` and `stale_row_clear_range(clear_range, 0, …)`
   are evaluated to surface bad ranges early.
2. The spreadsheet metadata is fetched **once per spreadsheet ID** with
   `get_spreadsheet(..., fields="sheets.properties")`, building a
   title→sheetId map.
3. If the write range's tab is not present in that spreadsheet, raise a
   `ConfigError` naming the missing tab and pointing at `rosters.*.range` /
   `rosters.*.clear_range` (`test_create_ministry_rosters_preflights_missing_sheet_before_writes`
   confirms no writes happen first).

The function returns `{(spreadsheet_id, tab_title): sheetId}` for all tabs,
which becomes the `sheet_ids` map used to resolve the numeric sheet ID each
formatting call needs. In dry-run, preflight is skipped and `sheet_ids` is empty.

### 7.3 Write plan (`write_plan` / `roster_target_plan`)

Each plan captures: `spreadsheet_id`, `range_name`, `clear_range`,
`padded_values`, `stale_clear_range`, and the resolved `sheet_id`.

- `padded_values` = `rectangular_values(values, clear_range_width)` — every row
  is right-padded with `""` to the clear-range width, so an `update` overwrites
  (clears) stale trailing cells in each written row. If the generated content is
  wider than the clear range, `ConfigError` ("clear_range must be at least as
  wide…"). In the standard `A:Z` config this pads each row to 26 cells
  (asserted in the main write test).
- `stale_clear_range` = `stale_row_clear_range(clear_range, len(values), range_name)`
  — the sub-range covering only rows **below** the newly written roster. Start
  row = `max(clear_range_start_row, write_range_start_row + row_count)`. If the
  clear range is bounded and that start exceeds its end row, returns `None`
  (nothing to clear). Examples: `("Readers!A:Z", 7)` → `"Readers!A8:Z"`;
  with `range_name="Readers!A5"` → `"Readers!A12:Z"`; `("Readers!A1:Z7", 7)` →
  `None`. A clear range that is not a parseable column/bounded range (e.g. a
  single cell `Readers!A1`) → `ConfigError`.
- `sheet_id` = `sheet_ids[(spreadsheet_id, tab)]` (None in dry-run).

### 7.4 Applying a write (`write_values`)

In **dry-run**: logs `"dry-run: would write N row(s) to spreadsheet … range …"`
and returns; no Sheets call is made (`test_…_dry_run_skips_sheet_writes` asserts
zero `get`/`update`/`clear`/`batchUpdate` calls).

In **live** mode, in this deliberate order:

1. If `sheet_id is None` → `ConfigError` (internal planning error guard).
2. **`update_values`** writes `padded_values` to `range_name` with
   `valueInputOption="RAW"` (the Sheets helper default — roster text is stored
   verbatim, never parsed as formulas/dates). Update is done **first** so a
   failed write leaves the previously published roster intact and unclearned
   (`test_…_update_failure_does_not_clear_existing_sheet`).
3. **`clear_values`** clears `stale_clear_range` (if not `None`) — removing rows
   left over from an older, longer roster — issued as
   `clear(spreadsheetId, range, body={})`.
4. **`format_roster_sheet`** applies layout via a single
   `batch_update_spreadsheet`. Formatting runs **after** the stale-row clear, so
   even if formatting fails the stale rows are already gone
   (`test_…_clears_stale_rows_before_formatting`).
5. A `GoogleAPIError` is passed through `sheet_range_config_error`: a 400 whose
   message contains "Unable to parse range" is re-raised as a friendly
   `ConfigError` naming the spreadsheet, `range`, `clear_range`, and expected
   tab(s); other errors propagate unchanged.

### 7.5 Formatting requests (`roster_format_requests`)

`format_roster_sheet` is called with `column_count = max(len(row))` of the
padded values (i.e. the clear-range width) and `row_count = len(padded_values)`.
`roster_format_requests` builds, in order:

1. `updateSheetProperties` → `gridProperties.frozenRowCount = 4`
   (`ROSTER_FROZEN_ROWS`) — freezes the title/timestamp/spacer/header rows.
2. `repeatCell` over rows `0..max(row_count, 4)`, cols `0..column_count` →
   `verticalAlignment: TOP`, `wrapStrategy: WRAP` (all roster cells top-aligned
   and wrapped).
3. `repeatCell` header style over rows `0..2` (`ROSTER_TITLE_ROWS`),
   `horizontalAlignment: LEFT` → blue background (`HEADER_BACKGROUND_COLOR =
   {red:0,green:0,blue:1}`), bold yellow text (`HEADER_TEXT_COLOR =
   {red:1,green:1,blue:0}`), `verticalAlignment: MIDDLE`, `wrapStrategy: WRAP`.
4. `repeatCell` spacer row 2 (`ROSTER_SPACER_ROW_INDEX`) → blue background only.
5. `repeatCell` column-header row 3 (`ROSTER_COLUMN_HEADER_ROW_INDEX`),
   `horizontalAlignment: CENTER` → same blue/yellow bold header style.
6. For each title row `0` and `1`: an `unmergeCells` then a `mergeCells`
   (`MERGE_ALL`) across columns `0..min(4, column_count)`
   (`ROSTER_TITLE_MERGE_COLUMNS`). Unmerge-then-merge keeps the request
   idempotent because Sheets rejects re-merging an already-merged span.
7. For each width in `ROSTER_COLUMN_WIDTHS = (220, 280, 360, 160, 200)` (sliced
   to `column_count`): an `updateDimensionProperties` setting that column's
   `pixelSize`. Only the first five columns are ever explicitly sized, even when
   the padded width is wider (e.g. 26 for `A:Z`).

No `updateSpreadsheetProperties` request is emitted (asserted in tests). The
formatting mirrors the legacy XLSX roster workflow.

## 8. Outputs, reporting & notifications

- **Console / logs:** `setup_logging` runs with `verbose = verbose or dry_run`
  (a dry run is always at least verbose). Logged INFO lines include the
  configured ministry/workgroup counts, the loaded member/family/ministry/
  workgroup counts, per-target update/clear/format confirmations, and the final
  `"Ministry roster operation completed successfully"`. DEBUG adds per-target
  "Preparing …" lines with structured `extra=log_extra(target)` payloads (in the
  JSONL file only — the human console shows the text, not the dataclass repr,
  confirmed by `assert "RosterTarget(" not in error`). Dry-run logs
  `"dry-run: would write N row(s) …"` per target.
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
| `rosters` empty / both source keys / unknown key / mismatched clear-range tab / duplicate output range | `ConfigError` from `roster_config_from_yaml` | 2 |
| `google` missing/both/invalid subject | `ConfigError` from `load_sheets_credentials` (live only) | 2 |
| Configured ministry/workgroup name absent from ParishSoft | `ConfigError` listing available names | 2 |
| Target tab missing (preflight) | `ConfigError` naming the tab | 2 |
| Generated roster wider than `clear_range` | `ConfigError` (width) | 2 |
| `clear_range` not a column/bounded range | `ConfigError` | 2 |
| Sheets "Unable to parse range" (400) | re-mapped `ConfigError` naming ranges/tab | 2 |
| Sheets **403** on write | `GoogleAPIError` → exit 2 — the delegated Workspace user lacks **Editor** access to that spreadsheet/shared drive | 2 |
| Sheets 429/5xx | retried then `GoogleAPIError` (one-shot for writes per retry policy) → exit 2 | 2 |
| ParishSoft API / tenant-mismatch errors | `ParishSoftAPIError` / `ConfigError` → exit 2 | 2 |

The repository README and the per-tool README call out the 403 case explicitly:
a Sheets 403 on write means the delegated user is not an Editor of that
spreadsheet — share it (or its shared drive) with that user as an editor.

## 10. Edge cases & nuances actually in the code

- **Update-before-clear ordering** guarantees a failed update never blanks the
  existing roster, and a failed *format* never leaves stale rows visible.
- **All-targets-planned-before-any-write** — width checks, missing-tab preflight,
  and stale-range parsing for every target happen before the first `update`, so
  one bad target aborts the whole run cleanly.
- **`"Ministry: " title prefix on everything`** — role sheets and workgroup
  rosters also get the `"Ministry: "` prefix; the title text is the role-sheet
  `name` / workgroup target `name`.
- **Roleless ministry members are kept** with an empty role cell; **blank roles
  are dropped from the joined string** but not from membership.
- **Phone vs. email row split** only happens when both are present; email-only
  members keep email on the main row.
- **Privacy flags** (`family_PublishPhone`/`family_PublishEMail`) fully suppress
  the respective contact info, regardless of whether the data exists.
- **Only currently-active ministry memberships** count (start/end-date window;
  no-date memberships excluded) — handled upstream in `make_member_ministries`.
- **`active_only=True, parishioners_only=False`** — inactive/deceased members are
  excluded, but non-registered members are not.
- **Workgroup uses only the first source name**; the leader companion-group
  detection uses the configured suffix, with `" Ldr"`/`" Leader"` also accepted
  when deriving base names for *validation*.
- **Column widths cap at five** — extra padded columns are never explicitly
  sized.
- **`gsheet_id` alias is unreachable** (rejected by allowed-keys; see §4.3).
- **Dry-run requires no Google config** and makes no Sheets calls at all.
- **A1 quoting** is handled: `'Sunday Roster'!A1`, `'Pastor''s Roster'!A1`, and
  bare `A1:E20` (→ `"Sheet1"`) all parse correctly.

## 11. Testing notes

`tests/test_create_ministry_rosters.py` (mocked; no real credentials) locks down:

- **Credential path resolution** — relative `service_account_file` resolves
  against the config directory (`base_dir`).
- **Config parsing** — keeps `name`, multiple `ministries`, nested role sheets
  including the legacy `"role sheets"` key
  (`test_roster_config_validation_and_role_sheets`); rejects empty targets,
  unknown keys ("unsupported key"), a clear range on a different sheet, and
  duplicate output ranges.
- **Roster generation** — sort order, ministry roles, workgroup leader-suffix
  detection, and `roster_role_matches`.
- **`roster_values`** — exact title/timestamp/header rows (incl. `EST`
  abbreviation), the phone/email continuation-row split, and birthday formatting.
- **`roster_format_requests`** — the exact request order, header styling,
  frozen rows, title merges, and the five column widths; asserts no
  `updateSpreadsheetProperties`.
- **`sheet_name_from_a1_range`** — quoted/escaped/bare ranges.
- **End-to-end `main`** with injected `loader` and `sheets_factory`: verifies
  update-then-stale-clear per target, role-sheet/workgroup routing to their own
  spreadsheet IDs, 26-wide padded rows, the `get` metadata calls per spreadsheet,
  `frozenRowCount==4` batchUpdates per sheetId (101/102/103), loader kwargs, and
  the success/target log lines.
- **Failure paths** — dry-run skips writes / needs no Google config; missing tab
  and too-narrow later target abort before writes; formatting failure still
  clears stale rows; update failure does not clear; missing ParishSoft source,
  invalid YAML, config-validation error, and unparseable Sheets range all exit 2
  with the expected messages; custom leader suffix.

**Injection seams:** `loader` (defaults to `load_families_and_members`) and
`sheets_factory` (defaults to `build_sheets_service(load_sheets_credentials(...))`)
on `main()`. The shared Sheets helper `build_sheets_service` itself takes a
`build_fn` seam (see [#google-integration-layer](../intro/spec.md#google-integration-layer)),
though this tool's tests inject at the `sheets_factory` level. The fake
`SheetsService` records `get`/`update`/`clear`/`batchUpdate` calls and can be
configured to raise `GoogleAPIError` on each.

## 12. Re-creation task outline

1. Declare the entry point `create_ministry_rosters_main` in `cli.py` (lazy
   import) and the wrapper script.
2. `main()` — build the shared parser, handle `--version`, delegate to `_run`
   inside `run_user_facing`, with `loader`/`sheets_factory` seams.
3. `_run()` — resolve `CommonOptions`; set up logging (`verbose or dry_run`);
   `require_explicit_write_mode`; load YAML; `roster_config_from_yaml`; build the
   ParishSoft client; `loader(active_only=True, parishioners_only=False)`;
   `validate_configured_parishsoft_sources`; build the Sheets service only when
   not dry-run; call `write_configured_rosters`; log success; return 0; log
   `ConfigError` as "Configuration validation failed" then re-raise.
4. Config dataclasses (`RosterTarget`, `RoleRosterTarget`, `RosterConfig`,
   `RosterMember`, `RosterWritePlan`) and the `rosters`/`google` parsers with
   exhaustive allowed-key rejection, default inheritance, source-form rules,
   same-sheet and unique-range validation.
5. ParishSoft source validation against ministry/workgroup names (with
   suffix-stripped base names).
6. Member collectors: `ministry_roster_members` (any-of, dedup/sort/join roles,
   keep roleless), `workgroup_roster_members` (leader-suffix companion group),
   `roster_role_matches`.
7. Grid builders: `roster_values` (title/timestamp/spacer/header + member rows),
   `roster_member_rows` (phone/email continuation split), name/address/
   city-state-zip/phone/email/birthday helpers, `member_sort_key`,
   `current_roster_time`/`format_update_timestamp`.
8. A1/range utilities: `sheet_name_from_a1_range`, `a1_column_number`,
   `a1_start_row`, `clear_range_width`, `stale_row_clear_range`,
   `rectangular_values`, and the `_CLEAR_RANGE_RE`/`_A1_START_RE` regexes.
9. Write pipeline: `configured_sheet_ranges`, `preflight_roster_targets`
   (title→sheetId map), `roster_target_plan`/`write_plan`, `write_values`
   (dry-run log; update→stale-clear→format; `sheet_range_config_error`).
10. Formatting: `roster_format_requests` + `format_roster_sheet`,
    `header_format_request`, `spacer_format_request`, `title_merge_requests`,
    `repeat_cell_request`, and the module constants (colors, widths, row
    indices, frozen-row count).
11. Operator docs (`README.md`) and `example-config.yaml`; mocked tests covering
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
</content>
</invoke>
