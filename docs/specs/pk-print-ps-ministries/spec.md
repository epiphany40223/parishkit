# pk-print-ps-ministries — list ParishSoft ministry names

## Purpose and role

`pk-print-ps-ministries` is a read-only lookup tool that prints the ministry
names defined in ParishSoft, one per line, in sorted order. Its purpose is
operator support: the sync and roster tools map ParishSoft ministries to
external targets, and those mappings must use the ministry names *exactly* as
ParishSoft spells them. This tool gives the operator an authoritative,
copy-pasteable list of those names without opening the ParishSoft UI. It never
writes any external system.

- **Category:** Lookup (read-only). It has no reconciliation step, no write
  gate, and no dry-run semantics beyond accepting the shared `dry_run` key for
  config uniformity.
- It is the smallest tool in the kit and, with
  [`pk-query-ps-memfam`](../pk-query-ps-memfam/spec.md), the simplest
  end-to-end exercise of the shared stack. It follows the standard command
  skeleton (parse → load/validate config → resolve `CommonOptions` → set up
  logging → build the ParishSoft client → read source data → emit output)
  described in [`../intro/spec.md#architecture-and-data-flow`](../intro/spec.md#architecture-and-data-flow).

Implementation: `src/parishkit/pk_print_ps_ministries.py`.

## Invocation

- **Console command:** `pk-print-ps-ministries` (installed by `pyproject.toml`
  `[project.scripts]`).
- **Entry point:** `pk-print-ps-ministries = "parishkit.cli:print_ministries_main"`.
  `cli.print_ministries_main` lazily imports `parishkit.pk_print_ps_ministries`
  and delegates to its `main()`. **Note:** the `cli.py` entry point calls
  `main(argv)` and does **not** forward the `loader` injection seam; only a
  direct call to `pk_print_ps_ministries.main(..., loader=...)` can override the
  loader (the tests do exactly this — see [Testing notes](#testing-notes)).
- **Wrapper:** `scripts/pk-print-ps-ministries/pk-print-ps-ministries.py` is a
  `#!/usr/bin/env python3` shim that calls
  `print_ministries_main()` and raises `SystemExit` with its return code. It
  contains no logic.
- **`--version`:** prints `pk-print-ps-ministries <version>` (where `<version>`
  comes from `importlib.metadata.version("parishkit")`) and returns `0`. This is
  handled *before* any config is loaded or any client is built, so `--version`
  needs neither a config file nor credentials. Its help text is "show that the
  console entry point is installed".

Typical run:

```sh
pk-print-ps-ministries --config /opt/parishkit/config/pk-print-ps-ministries.yaml
```

## Command-line options

This tool adds exactly one tool-specific flag of its own; everything else is the
shared common surface.

- **`--version`** (tool-specific, `store_true`) — print the installed version
  string and exit `0`, as described above.

All other flags come from `parser_with_common_options(...)` and behave
identically across tools; see
[`../intro/spec.md#shared-cli-layer`](../intro/spec.md#shared-cli-layer)
for their precedence and semantics. As confirmed by `--help`, the inherited
flags are:

- `--config CONFIG`
- `--dry-run / --no-dry-run` (accepted but inert here — this tool never writes)
- `--verbose / --no-verbose`
- `--debug / --no-debug`
- `--log-file LOG_FILE`, `--log-dir LOG_DIR`
- `--slack-token-file SLACK_TOKEN_FILE`, `--slack-channel SLACK_CHANNEL`,
  `--slack-log-level SLACK_LOG_LEVEL`
- `--ps-api-key-file PS_API_KEY_FILE`, `--ps-cache-dir PS_CACHE_DIR`,
  `--ps-cache-limit PS_CACHE_LIMIT`

There is **no** option that changes the output format, sorting, or filtering;
filtering is configured only through YAML (below). The parser description string
is "Print sorted ParishSoft ministry names."

## Configuration schema

The config file is a single YAML mapping. The shared sections (`common`,
`logging`, `slack`, `parishsoft`) are parsed and validated centrally by
`resolve_common_options`; see
[`../intro/spec.md#configuration-system`](../intro/spec.md#configuration-system)
and [`#shared-cli-layer`](../intro/spec.md#shared-cli-layer). Only the
tool-specific `print_ministries` section is owned by this module. The shipped
template is `scripts/pk-print-ps-ministries/example-config.yaml`.

### Shared sections (validated centrally — not re-specified here)

- **`common`** — `dry_run`, `timezone`. The example sets `dry_run: true` "for
  consistency"; it has no effect because the tool issues no writes and calls no
  write gate. `timezone` is used only by shared runtime helpers.
- **`slack`** — `token_file`, `channel`, `level` (logging/alert sink).
- **`parishsoft`** — the section that actually drives the run:
  `api_key_file`, `cache_dir`, `cache_limit` (a duration matching
  `^[1-9][0-9]*[smhd]$`, e.g. `14m`), and `expected_organization` (tenant
  guard). The API key is read from `api_key_file` at call time. Each of these is
  also overridable by the corresponding `--ps-*` flag. See
  [`#parishsoft-data-layer`](../intro/spec.md#parishsoft-data-layer).

### Tool-specific section: `print_ministries` (optional)

Parsed by `ministry_filters(config)`. The whole section is optional; if absent
it defaults to an empty mapping and every ministry name is printed.

- If present, the section **must be a mapping**, else
  `ConfigError: "print_ministries configuration must be a mapping"`.
- Unknown keys are rejected via `reject_unknown_keys(...)`. The **only** allowed
  keys are exactly: `include_patterns`, `include_names`, `exclude_patterns`.
- Each of the three keys, when present, **must be a list of strings**, else
  `ConfigError: "print_ministries.<key> must be a list of strings"` (raised by
  `_string_list`).

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `include_patterns` | list of strings (Python regex) | `[]` | A ministry whose name matches *any* of these (via `re.search`) is included, unless excluded. |
| `include_names` | list of strings (literal) | `[]` | Exact ministry names that are always included (unless excluded). **Not** regex — compared by set membership. |
| `exclude_patterns` | list of strings (Python regex) | `[]` | A ministry whose name matches *any* of these (via `re.search`) is dropped. Exclusion is checked first and always wins. |

Validation specifics worth noting:

- `include_patterns` and `exclude_patterns` are compiled up front by
  `_validate_filters` (which calls `_compile_patterns`). A bad regex raises
  `ConfigError: "print_ministries.<key> contains invalid regex"` **before** any
  ParishSoft call.
- `include_names` is **not** compiled and **not** regex-validated — entries are
  treated as literal exact strings, so an entry like `"["` is a valid name to
  match, never a regex error.

Example (from `example-config.yaml`):

```yaml
print_ministries:
  include_patterns:
    - '^\d\d\d-'            # names beginning with three digits and a dash
  include_names:
    - Example Special Ministry
  exclude_patterns: []
```

## Source data

The tool reads exactly one ParishSoft entity type: **ministry types**. The
default loader is `parishkit.parishsoft.load_ministry_types`, which hits the
`ministry/type/list` endpoint (1-based `PageNumber` pagination) and returns a
`dict[int, dict[str, Any]]` keyed by ministry id, where each value carries only
`{"id": <int>, "name": <str>}`.

What this means for the tool:

- It uses **only the `name` field** of each ministry type for output, plus the
  id implicitly as the dict key (which is why distinct ids sharing a name are
  de-duplicated — see below).
- It does **not** fetch minister rosters, membership counts, roles, or any
  per-ministry detail. `load_ministry_type_memberships` exists in the data layer
  but this tool never calls it. No counts of any kind are produced.
- Before loading, `client.validate_organization()` is called (the tenant guard);
  its return value is unused here but the call enforces the
  `expected_organization` check.

The ParishSoft API generation, the ministry endpoints, and the read-only nature
of ministry data are documented in
[`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) (see the
"Ministry & Workgroup records — READ-ONLY in BOTH" section) and the data layer
in [`#parishsoft-data-layer`](../intro/spec.md#parishsoft-data-layer).

## Output format

Plain text on stdout: **one ministry name per line, nothing else.** There are no
headers, no counts, no ids, no separators, no JSON, and no format toggles.

The set of names and their order come from
`sorted_ministry_names(ministry_types, **filters)`:

1. **Exclude first.** For each ministry, if its name matches any
   `exclude_patterns` regex (`re.search`), it is skipped entirely. Exclusion
   always wins because it is tested before any include logic.
2. **Include rule.** A non-excluded name is kept when any of the following hold:
   - there are **no** include filters at all (both `include_patterns` and
     `include_names` are empty) — in that case every non-excluded ministry is
     kept; or
   - the name is listed verbatim in `include_names` (exact set membership); or
   - the name matches any `include_patterns` regex (`re.search`).
3. **De-duplicate.** Kept names accumulate in a `set`, so two distinct ministry
   ids with the identical name collapse to a single output line.
4. **Sort.** The result is `sorted(names)` — Python's default ascending string
   sort (case-sensitive, Unicode codepoint order: digits < uppercase <
   lowercase). Each name is then emitted with `print(name)`.

Because matching uses `re.search` (not `match`/`fullmatch`), unanchored patterns
match anywhere within a name; the example `'^\d\d\d-'` anchors to the start
deliberately. Worked example (from the tests, with the example filters applied
to ministries `001-Readers`, `002-Ushers`, `Historical Ministry`,
`Example Special Ministry`): output is

```text
001-Readers
002-Ushers
Example Special Ministry
```

`Historical Ministry` is dropped (matches no include pattern and is not an
explicit name); `Example Special Ministry` survives via `include_names`. With no
filters configured, all four names print in sorted order.

## Failure modes and exit codes

The whole run body is wrapped in `run_user_facing(...)`, the shared error funnel
(see [`#shared-cli-layer`](../intro/spec.md#shared-cli-layer)). It converts
expected operational errors into a single `ERROR: <message>` line on stderr plus
**exit code 2**; unexpected exceptions still propagate as real tracebacks.

- **Exit 0** — success (names printed), or `--version`.
- **Exit 2 (user-facing `ERROR:`):**
  - Invalid include/exclude regex →
    `print_ministries.<key> contains invalid regex` (raised by
    `_validate_filters` **before** the org is validated or any data is loaded).
  - `print_ministries` not a mapping → `print_ministries configuration must be
    a mapping`.
  - A filter list containing a non-string / not a list →
    `print_ministries.<key> must be a list of strings`.
  - An unknown key in `print_ministries` → `ConfigError` from
    `reject_unknown_keys`.
  - Any other `ConfigError` from shared config/`CommonOptions` resolution (bad
    timezone, malformed cache limit, partial Slack config, missing/non-mapping
    config file, etc.).
  - Missing/unreadable API key file or other `OSError`.
  - `ParishSoftAPIError` from `validate_organization()` or the ministry loader
    (surfaces as `ParishSoft API error ...`), and `RetryError` on retry
    exhaustion.
- **argparse exits:** `-h/--help` exits `0`; unknown/invalid flags exit `2` via
  argparse's own `SystemExit` (not the error funnel).

## Edge cases and nuances

- **Filters validated before any remote call.** `_run` extracts
  (`ministry_filters`) and compiles (`_validate_filters`) the regex filters
  *before* `setup_logging`, before building the client, and before
  `validate_organization`/loading. A bad regex therefore exits without
  contacting ParishSoft (locked down by tests).
- **`include_names` is literal, not regex.** It is never compiled, so it can
  never raise an "invalid regex" error and matches by exact string equality.
- **Exclusion precedence is absolute.** A name matching both an include and an
  exclude pattern is excluded, because exclusion is checked first with
  `continue`.
- **"No include filters" means "keep everything".** Setting only
  `exclude_patterns` (with both include lists empty) prints every name except
  the excluded ones. Setting any include filter switches to allow-list mode.
- **De-duplication by name.** Distinct ministry ids with the same name yield one
  line (set semantics).
- **Empty result is valid.** If no ministries pass the filters (or ParishSoft
  returns none), the tool prints nothing and exits `0`.
- **`dry_run` is inert.** The tool accepts `common.dry_run` / `--dry-run` for
  config uniformity but performs no writes and calls no write gate
  (`require_explicit_write_mode` is not used). Contrast the sync tools' write
  safety in [`#dry-run-and-write-safety`](../intro/spec.md#dry-run-and-write-safety).
- **`--version` short-circuits.** It returns before config load, so it works
  with no config and no credentials.
- **The loader takes only the client.** The default and injected loaders are
  called as `loader(client)` with no keyword arguments (unlike
  `pk-query-ps-memfam`, whose loader takes selector/contribution kwargs).
- **Sort is case-sensitive.** No locale-aware or case-folded ordering is
  applied; plain `sorted()` is used.

## Testing notes

Locked-down behavior lives in `tests/test_print_tools.py` (which also covers
`pk-query-ps-memfam`; the ministry-specific tests are below). Shared fixtures: a
`FakeClient` whose `validate_organization()` sets a `validated` flag and returns
`7`; a `data()`/`ministry_types()` helper supplying four ministries
(`001-Readers`, `002-Ushers`, `Historical Ministry`, `Example Special
Ministry`); and `write_config(tmp_path, ...)` which writes a `parishsoft`
section plus a `print_ministries` section using the example filters.

Tests asserting this tool's behavior:

- `test_print_ministries_outputs_sorted_unique_names` — monkeypatches
  `parishkit.pk_print_ps_ministries.parishsoft_client_from_config` to return the
  `FakeClient`, injects `loader=lambda _client: ministry_types()`, and asserts
  exit `0`, that `client.validated` is `True`, and that stdout lines equal
  `["001-Readers", "002-Ushers", "Example Special Ministry"]` (sorted,
  de-duplicated, filtered).
- `test_print_ministries_verbose_shows_parishsoft_loader_logs` — with
  `--verbose`, an INFO log emitted from a `parishkit.parishsoft` logger inside
  the loader appears on stderr.
- `test_sorted_ministry_names_deduplicates_and_sorts` — pure-function test of
  `sorted_ministry_names` with `include_patterns`/`include_names`/
  `exclude_patterns`, confirming `Historical Ministry` is dropped.
- `test_sorted_ministry_names_without_filters_returns_all_names` — with no
  filters, all four names are returned sorted.
- `test_print_ministries_reports_bad_regex` — an invalid `include_patterns`
  entry (`"["`) gives exit `2`, `"invalid regex"` on stderr, and asserts
  `client.validated` is **False** (no remote work happened).
- `test_print_ministries_validates_bad_regex_before_loading` — with the same bad
  regex and a loader that raises `ParishSoftAPIError`, the loader is never
  reached, so `"invalid regex"` surfaces instead of an API error — proving the
  validate-before-load ordering.

**Injection seams:** the `loader` keyword on `pk_print_ps_ministries.main`
(default `load_ministry_types`), and monkeypatching the module-level
`parishsoft_client_from_config`. The public functions `sorted_ministry_names`
and `ministry_filters` are importable and tested directly. (Recall the `cli.py`
entry point does not forward `loader`, so tests call the module `main` directly.)

## Re-creation task outline

1. Create `src/parishkit/pk_print_ps_ministries.py` with module constants
   `DEFAULT_INCLUDE_PATTERNS = []`, `DEFAULT_INCLUDE_NAMES = []`, and a `Loader`
   type alias.
2. `main(argv=None, *, loader=load_ministry_types)`: build
   `parser_with_common_options("pk-print-ps-ministries", description="Print
   sorted ParishSoft ministry names.")`, add the `--version` store-true flag,
   parse; if `--version`, print `pk-print-ps-ministries <version>` and return
   `0`; otherwise return `run_user_facing(lambda: _run(args, loader))`.
3. `_run(args, loader)` in this exact order: `resolve_common_options` →
   `load_yaml_config` → `ministry_filters(config)` → `_validate_filters` →
   `setup_logging` → `parishsoft_client_from_config` →
   `client.validate_organization()` → `loader(client)` → `print` each name from
   `sorted_ministry_names(ministry_types, **filters)` → return `0`.
4. `ministry_filters(config)`: read optional `print_ministries`; require it be a
   mapping; `reject_unknown_keys` to `{include_patterns, include_names,
   exclude_patterns}`; return the three lists via `_string_list` with defaults
   of `[]`.
5. `sorted_ministry_names(...)`: compile include/exclude patterns; build the
   `include_names` set; for each ministry, skip on any exclude match, else keep
   when (no include filters) or (name in `include_names`) or (any include match);
   accumulate in a set and return `sorted(...)`.
6. Helpers: `_validate_filters` (compile include + exclude patterns only),
   `_compile_patterns` (raise `ConfigError "...contains invalid regex"`), and
   `_string_list` (raise `ConfigError "...must be a list of strings"`).
7. Add the `cli.py` entry point `print_ministries_main` (lazy import +
   delegate, no `loader` forwarding) and register it in `pyproject.toml`.
8. Add the wrapper `scripts/pk-print-ps-ministries/pk-print-ps-ministries.py`,
   `example-config.yaml`, and the operator README.
9. Add tests matching [Testing notes](#testing-notes).

## Cross-references

- Shared foundation and command skeleton:
  [`../intro/spec.md#architecture-and-data-flow`](../intro/spec.md#architecture-and-data-flow),
  [`#shared-cli-layer`](../intro/spec.md#shared-cli-layer),
  [`#configuration-system`](../intro/spec.md#configuration-system),
  [`#logging-and-notifications`](../intro/spec.md#logging-and-notifications),
  [`#parishsoft-data-layer`](../intro/spec.md#parishsoft-data-layer).
- ParishSoft ministry data and read-only constraints:
  [`../../parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md).
- Sibling read-only lookup tool:
  [`../pk-query-ps-memfam/spec.md`](../pk-query-ps-memfam/spec.md).
- Operator documentation: `scripts/pk-print-ps-ministries/README.md`.
- Source: `src/parishkit/pk_print_ps_ministries.py`;
  wrapper `scripts/pk-print-ps-ministries/pk-print-ps-ministries.py`;
  tests `tests/test_print_tools.py`.
