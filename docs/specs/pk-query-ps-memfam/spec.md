# pk-query-ps-memfam — ParishSoft member/family record inspector

## 1. Purpose and role

`pk-query-ps-memfam` is a **read-only** command-line lookup tool that prints one
ParishSoft member record, one family record, or every member matching a name
search. It exists as a debugging / reference utility — the operational
descendant of an older Epiphany `print-member.py` script — for confirming what
ParishSoft actually holds for a person or household before or after a sync. It
never writes to ParishSoft or any other external system.

Category: **Lookup (read-only)**. It follows the shared command skeleton
described in the [top-level spec](../intro/spec.md#architecture-and-data-flow)
(parse argv → load+validate config → resolve `CommonOptions` → set up logging →
build the ParishSoft client → read data → render) but stops at "read + render":
there is no desired-state computation, no diff, and no write phase. Because it
performs no writes, it does **not** call `require_explicit_write_mode` and is not
subject to the [dry-run write gate](../intro/spec.md#dry-run-and-write-safety);
`--dry-run`/`--no-dry-run` are accepted (for surface consistency with other
tools) but have no effect.

> Naming note: the command is `pk-query-ps-memfam`, but several internal
> identifiers retain the tool's former "print member" name — the package module
> is `src/parishkit/pk_query_ps_memfam.py`, the console entry point is
> `parishkit.cli:print_member_main`, and the tool-specific config section is
> `print_member`. These are intentional historical names, not typos; preserve
> them when re-creating the tool. Its sibling read-only lookup is
> [`pk-print-ps-ministries`](../pk-print-ps-ministries/spec.md), which shares
> this document's structure.

## 2. Invocation

- **Console command:** `pk-query-ps-memfam` (declared in `pyproject.toml`
  `[project.scripts]` as `pk-query-ps-memfam = "parishkit.cli:print_member_main"`).
- **Entry point:** `parishkit.cli.print_member_main` is a thin shim that lazily
  imports the tool module and calls `pk_query_ps_memfam.main()`
  (see [shared CLI layer](../intro/spec.md#shared-cli-layer)).
- **Wrapper script:** `scripts/pk-query-ps-memfam/pk-query-ps-memfam.py` —
  `#!/usr/bin/env python3`, executable, body is only
  `raise SystemExit(print_member_main())`. It exists so the tool can run from a
  checkout and only delegates to the package entry point.
- **`--version`:** prints `pk-query-ps-memfam <version>` (the installed
  `parishkit` distribution version via `importlib.metadata.version`) and returns
  `0`. This short-circuits **before** selector validation, config loading, and
  any external call, so `pk-query-ps-memfam --version` works with no selector and
  no config.

## 3. Command-line options

### 3.1 Shared flags

All shared flags are added by `parser_with_common_options(...)` and resolved by
`resolve_common_options(...)`; see the
[shared CLI layer](../intro/spec.md#shared-cli-layer) for their full semantics
and CLI > config > default precedence. The ones that matter most here are
`--config`, the ParishSoft client flags `--ps-api-key-file` / `--ps-cache-dir` /
`--ps-cache-limit`, the logging flags (`--verbose`/`--debug`, `--log-file`,
`--log-dir`), and the Slack flags. `--dry-run`/`--no-dry-run` are present but
inert for this read-only tool.

### 3.2 Tool-specific flags

Added by `add_arguments(parser)`:

| Flag | Form | Meaning |
| --- | --- | --- |
| `--version` | `store_true` | Print the entry-point version and exit 0 (see §2). |
| `--member-duid DUID` | `type=int` | Select one member by ParishSoft member DUID. |
| `--family-duid DUID` | `type=int` | Select one family by ParishSoft family DUID. |
| `--name NAME` | string | Case-insensitive substring search over member name fields. |
| `--load-contributions [YYYY-MM-DD]` | `nargs="?"`, `const=True`, `default=None` | Load giving history; bare flag uses the default window, an argument sets the start date. |
| `--no-load-contributions` | `store_const`, `const=False`, `dest=load_contributions` | Disable contribution loading even when config enables it. |
| `--full` | `store_true` | Print full `membership` lists instead of replacing them with a marker. |

Selection rules:

- `--member-duid`, `--family-duid`, and `--name` form a single
  `argparse` **mutually exclusive group**, so at most one may be supplied;
  passing two is an argparse usage error (exit 2).
- The group is *not* marked `required` at the argparse level. Instead `main()`
  enforces that **exactly one** selector is present after parsing:
  - A `--name` value that is `None`-distinct but blank/whitespace-only triggers
    `parser.error("--name must not be blank")`.
  - If `member_duid`, `family_duid`, and `name` are all unset,
    `parser.error("one of --member-duid, --family-duid, or --name is required")`.
- `_selector(args)` reduces the active choice to a `(kind, value)` pair, ordered
  by precedence: `("member", member_duid)`, else `("family", family_duid)`, else
  `("name", name or "")`. This pair is passed to the loader as `selector=` (the
  default loader ignores it — see §5).

Contribution flags:

- `--load-contributions` with no argument yields `True`; with a `YYYY-MM-DD`
  argument yields that string; absent it stays `None` (meaning "fall back to
  config").
- `--no-load-contributions` writes `False` to the same `load_contributions`
  dest. The two flags are not in a mutex group, so if both appear the last one
  on the command line wins by normal store semantics.

## 4. Configuration schema

The config file is a single YAML mapping (`--config`, loaded by
`load_yaml_config`). Sections used by this tool:

### 4.1 Shared sections (validated centrally)

- **`parishsoft`** — required for any real run. Keys (per the
  [configuration system](../intro/spec.md#configuration-system) and
  [ParishSoft data layer](../intro/spec.md#parishsoft-data-layer)):
  `api_key_file` (path to the read-only API key file), `cache_dir` (local
  response cache directory), `cache_limit` (duration like `30s`/`14m`/`12h`/`7d`,
  default `14m`), `expected_organization` (tenant guard name). These are merged
  into `CommonOptions` and consumed by `parishsoft_client_from_config`; this tool
  adds no extra `parishsoft` keys.
- **`common`** (`dry_run`, `timezone`, …), **`slack`** (`token_file`, `channel`,
  `level`, …), **`logging`** (`log_file`, `log_dir`) — all resolved/validated by
  the shared CLI layer. The shipped `example-config.yaml` sets `common.dry_run:
  true` with a comment that it is "accepted for consistency" and that this
  read-only command does not write external systems, plus `common.timezone`, a
  `slack` block, and the `parishsoft` block.

### 4.2 Tool-specific section: `print_member`

Validated entirely inside `_load_contributions_value`:

- The section must be a **mapping**; otherwise
  `ConfigError("print_member configuration must be a mapping")`.
- `reject_unknown_keys(section, {"load_contributions"}, "print_member")` — the
  **only** allowed key is `load_contributions`; any other key is a `ConfigError`.
- **`load_contributions`** (optional, default `False`):
  - Allowed value types, normalized by `_normalize_load_contributions`:
    - boolean → used as-is (`true` = load default window, `false` = skip);
    - a YAML `date` object → converted to its ISO `YYYY-MM-DD` string;
    - a string → empty string `""` means disabled (`False`); a non-empty string
      must match `^\d{4}-\d{2}-\d{2}$` **and** round-trip through
      `date.fromisoformat`, otherwise
      `ConfigError("load_contributions date must use YYYY-MM-DD format")`;
    - any other type →
      `ConfigError("print_member.load_contributions must be a boolean or date
      string")`.

`example-config.yaml` documents `print_member.load_contributions: false` with a
note that it may also be `true` or a `YYYY-MM-DD` start date.

### 4.3 Effective `load_contributions` precedence

`_load_contributions_value(args, config)` resolves the final value:

1. If the CLI set `--load-contributions`/`--no-load-contributions`
   (`args.load_contributions is not None`), that value wins and is normalized
   (so a bad CLI date string is rejected with the same `YYYY-MM-DD` error).
2. Otherwise the `print_member.load_contributions` config value (default
   `False`) is normalized and used.

The resolved value is a `bool` or a `YYYY-MM-DD` string, passed straight to the
loader.

## 5. Source data and selection

### 5.1 What is loaded

`_run` builds the ParishSoft client via
`parishsoft_client_from_config(common, config)`
(see [ParishSoft data layer](../intro/spec.md#parishsoft-data-layer)), then
calls the injectable `loader`. The default loader is `load_lookup_data`, which
delegates to the central
[`load_families_and_members`](../../parishsoft-api-analysis.md) aggregation with:

- `active_only=False` and `parishioners_only=False` — inactive members and
  non-registered families are retained so almost anyone can be looked up;
- `load_contributions=<resolved value>` — funds/pledges/contribution detail are
  loaded only when truthy (a string is treated as the giving start date,
  otherwise giving from one year ago).

`load_lookup_data` accepts a `selector` keyword (and arbitrary `**_kwargs`) for
test/API compatibility but **ignores it**: it always fetches and cross-links the
**full** dataset for the tenant, not a targeted single-record fetch. This is
deliberate — the tool mirrors the original debugging utility and reuses the same
rich, cross-linked structure the sync tools use. The returned `ParishSoftData`
therefore carries the full set of raw API fields plus the derived `"py "`-prefixed
fields (e.g. `py family`, `py members`, `py workgroups`, `py ministries`,
`py contactInfo`, `py friendly name FL`/`LF`, `py emailAddresses`); the exact
field derivation lives in the data layer and is documented in
[`parishsoft-api-analysis.md`](../../parishsoft-api-analysis.md) and the
[intro data-layer section](../intro/spec.md#parishsoft-data-layer).

`_run` passes `active_only=False, parishioners_only=False,
load_contributions=…, selector=_selector(args)` to whatever `loader` is in
effect, then prints `render_selection(data, args)` and returns `0`.

### 5.2 How the requested record is found

`render_selection(data, args)` selects from the loaded dataset by selector kind:

- **`--member-duid`** → `data.members[duid]`; a missing key raises
  `ConfigError("member DUID not found: <duid>")`.
- **`--family-duid`** → `data.families[duid]`; a missing key raises
  `ConfigError("family DUID not found: <duid>")`.
- **`--name`** → `find_members_by_name(data.members, name)`, returning **all**
  matching member dicts (never an error, even on zero or many matches).

`find_members_by_name` casefolds and strips the query; a blank query returns
`[]`. It does a plain case-insensitive **substring** test against
`_member_search_text(member)`, which joins these member fields with spaces and
casefolds them: `firstName`, `lastName`, `middleName`, `preferredName`,
`py friendly name FL`, `py friendly name LF`. So a search matches legal,
preferred, and friendly name forms. Result order follows `members` dict
iteration order.

## 6. Output format

The single output is `print(render_selection(...))` — a Python
`pprint.pformat(..., width=200)` rendering of the selected object:

- a **member** dict for `--member-duid`,
- a **family** dict for `--family-duid`,
- a **list** of member dicts for `--name` (an empty match prints `[]`).

The rendered object is the raw ParishSoft record **plus** its derived `"py "`
cross-link fields — this is intentional debugging output, not a curated report.
Tests lock in that both raw fields (`'memberDUID': 1`, `'familyDUID': 10`) and
derived fields (`'py friendly name FL': 'Janie Smith'`) appear.

### 6.1 Membership-list handling (default vs. `--full`)

`_display_value(value, full=args.full)` decides what goes to `pformat`:

- **Default (no `--full`):** `_omit_membership_lists(value, {})` returns a
  recursive *copy* in which every dict entry whose **key is exactly
  `"membership"` and whose value is a `list`** is replaced by the constant string
  `"omitted for brevity"` (`OMITTED_MEMBERSHIP`). Workgroup/ministry roster lists
  are often enormous, so this keeps the surrounding metadata (workgroup names,
  ministry names, notes) visible while abbreviating the roster rows. A memo table
  keyed by `id()` preserves shared references and cycles so the copy represents
  cross-links without recursing forever. A `"membership"` key whose value is not
  a list is recursed normally, not replaced.
- **`--full`:** the original object graph is passed through unchanged.
  `pformat` itself emits Python recursion markers (e.g.
  `<Recursion on dict with id=…>`) for the cyclic family↔member back-references.
  Full membership rows (and the data inside them, e.g. a ministry role like
  `Reader`) then appear in the output.

There is no JSON output mode and no field-selection option; format toggles are
limited to `--full` (membership detail) and `--load-contributions` (whether
giving data is present in the dumped object).

> Documentation discrepancy to preserve awareness of: the operator README
> (`scripts/pk-query-ps-memfam/README.md`) describes the output as "a bounded,
> readable summary, not a raw ParishSoft record dump." In practice the output
> *is* a pretty-printed raw+derived record dump; only `membership` lists are
> bounded (unless `--full`). Treat the code behavior as authoritative.

## 7. Failure modes and exit codes

The tool body runs inside `run_user_facing` (the shared
[error funnel](../intro/spec.md#shared-cli-layer)), which maps expected
operational errors (`ConfigError`, `OSError`, `ParishSoftAPIError`, etc.) to a
single `ERROR: …` line on stderr plus **exit code 2**, and lets unexpected
exceptions propagate as real tracebacks.

| Situation | Outcome |
| --- | --- |
| `--version` | Print version, exit `0` (before any validation/IO). |
| Success (record or list rendered) | Print result, exit `0`. |
| No selector given | `argparse` usage error, exit `2`. |
| Blank/whitespace `--name` | `argparse` usage error ("--name must not be blank"), exit `2`. |
| Two selectors given | `argparse` mutually-exclusive error, exit `2`. |
| Unknown member DUID | `ERROR: member DUID not found: <duid>`, exit `2`. |
| Unknown family DUID | `ERROR: family DUID not found: <duid>`, exit `2`. |
| Name search with zero matches | Prints `[]`, exit `0` (not an error). |
| Bad CLI/YAML contribution date | `ERROR: load_contributions date must use YYYY-MM-DD format`, exit `2`. |
| `print_member` not a mapping / unknown key | `ConfigError` → exit `2`. |
| Missing API key file / config errors | `ERROR: …`, exit `2`. |
| `ParishSoftAPIError` from loader | `ERROR: ParishSoft API error …`, exit `2`. |

Note there is **no "ambiguous match" failure**: a name that matches many members
prints all of them; one that matches none prints `[]`.

## 8. Edge cases and nuances

- **Full load regardless of selector.** Even a single-DUID lookup loads and
  cross-links the entire tenant dataset (`load_lookup_data` ignores `selector`).
  Output is bounded by the membership-omission marker, but the *fetch* is full —
  expect the same load cost as the sync tools.
- **Deceased members are unreachable.** `load_lookup_data` passes
  `active_only=False, parishioners_only=False` but does **not** pass
  `include_deceased`, so `load_families_and_members` defaults `include_deceased`
  to `False` and prunes deceased members (and families left with no retained
  members). A deceased member's DUID therefore yields "member DUID not found"
  despite the tool's "look up anyone" intent. Flag this if deceased lookups are
  ever required.
- **`--version` bypasses everything**, including selector validation and config.
- **DUID match is exact and integer-typed** (`type=int`); name match is a
  case-insensitive substring across multiple name fields including friendly
  names, so partial and reordered queries can match.
- **CLI contribution value beats config**, and CLI date strings are validated the
  same way as YAML ones.
- **`membership` replacement is key-and-type specific** — only `dict["membership"]`
  values that are `list`s are replaced; the memo preserves cycles and shared
  references so the abbreviated copy still mirrors the real object graph.
- **Inert `--dry-run`** — accepted for CLI consistency; the tool issues no
  writes and skips the write gate.

## 9. Testing notes

`tests/test_print_tools.py` covers this tool (alongside `pk-print-ps-ministries`)
and locks in the behavior above. Key assertions for `pk-query-ps-memfam`:

- `test_find_members_by_name_matches_friendly_names` — a query (`"janie"`)
  present only in the friendly-name fields matches, proving those fields are
  searched.
- `test_print_member_selects_member_and_load_contributions` — `--member-duid 1
  --load-contributions 2026-01-01` passes loader kwargs `active_only=False,
  parishioners_only=False, load_contributions="2026-01-01", selector=("member",
  1)`; output contains `'memberDUID': 1` and the derived `'py friendly name FL':
  'Janie Smith'`, shows `'membership': 'omitted for brevity'`, and omits the
  roster row text `member row`.
- `test_print_member_full_includes_membership_lists` — `--full` removes the
  omission marker and shows `member row` and `Reader`.
- `test_print_member_name_selector_runs_through_main` — `--name janie` resolves
  to and prints member 1.
- `test_print_member_selects_family` — `--family-duid 10` prints `'familyDUID':
  10`, with the membership marker and no `family row` text.
- `test_print_member_reports_missing_member_without_traceback` — `--member-duid
  999` exits `2` with `ERROR: member DUID not found: 999` (no traceback).
- `test_print_member_missing_api_key_is_user_facing` — a missing API key file
  exits `2` with an `ERROR:` line.
- `test_print_member_load_contribution_overrides_and_yaml_dates` — across three
  runs the resolved `load_contributions` values are `["2026-01-01", False,
  True]`: a YAML date passes through, `--no-load-contributions` overrides a YAML
  `true`, and a bare `--load-contributions` yields `True`.
- `test_print_member_lookup_loader_uses_full_parishsoft_loader` — `load_lookup_data`
  calls `load_families_and_members(client, active_only=False,
  parishioners_only=False, load_contributions=True)` and does **not** forward
  `selector`.
- `test_print_member_allows_contributions_for_name_search` — `--name Jane
  --load-contributions` succeeds (the full loader supports giving for name
  searches).
- `test_print_member_rejects_bad_contribution_date` / `…_rejects_compact_iso_date`
  — `2026/01/01` and `20260101` each exit `2` with a `YYYY-MM-DD` hint.
- `test_print_member_reports_parishsoft_api_errors` — a `ParishSoftAPIError` from
  the loader exits `2` with `ParishSoft API error`.
- `test_print_member_rejects_blank_name_selector` — `--name " "` raises
  `SystemExit`.
- `test_print_member_requires_one_selector` — no selector raises `SystemExit`.
- `test_print_member_verbose_shows_parishsoft_loader_logs` — `--verbose` surfaces
  `parishkit.parishsoft` INFO logs on stderr.

**Injection seams:** `main(argv, *, loader=…)` lets tests supply a fake dataset
(default `load_lookup_data`); tests monkeypatch
`parishkit.pk_query_ps_memfam.parishsoft_client_from_config` to return a dummy
client and `parishkit.pk_query_ps_memfam.load_families_and_members` to assert the
delegation contract. No real ParishSoft credentials or network access are needed
(per the [testing philosophy](../intro/spec.md#testing-ci-and-quality-philosophy)).

## 10. Re-creation task outline

1. Create `src/parishkit/pk_query_ps_memfam.py` with module docstring and the
   `OMITTED_MEMBERSHIP = "omitted for brevity"` constant and a `Loader` alias.
2. `add_arguments(parser)`: add `--version`; a mutually-exclusive selector group
   (`--member-duid`/`--family-duid` `type=int`, `--name`); `--load-contributions`
   (`nargs="?"`, `const=True`, `default=None`, metavar `YYYY-MM-DD`);
   `--no-load-contributions` (`store_const False`, shared dest);
   `--full` (`store_true`).
3. `main(argv, *, loader=None)`: build `parser_with_common_options(
   "pk-query-ps-memfam", description=…)`, add tool args, parse; handle
   `--version`; reject a blank `--name`; require exactly one selector; default
   `loader = load_lookup_data`; return `run_user_facing(lambda: _run(args,
   loader))`.
4. `_run`: `resolve_common_options` → `load_yaml_config` → `setup_logging` →
   `parishsoft_client_from_config` → `_load_contributions_value` → call
   `loader(client, active_only=False, parishioners_only=False,
   load_contributions=…, selector=_selector(args))` → `print(render_selection(...))`
   → return 0.
5. `load_lookup_data(client, *, load_contributions=False, selector=None,
   **_kwargs)`: delegate to `load_families_and_members(client, active_only=False,
   parishioners_only=False, load_contributions=load_contributions)` (ignore
   `selector`).
6. `_selector`: map the set selector to a `(kind, value)` pair in
   member→family→name precedence.
7. `render_selection`: branch on selector; DUID lookups raise `ConfigError` on
   `KeyError`; name lookup uses `find_members_by_name`; render with
   `pformat(_display_value(value, full=args.full), width=200)`.
8. `_display_value` / `_omit_membership_lists`: `--full` passes through; default
   deep-copies replacing list-valued `"membership"` entries with the marker,
   using an `id()`-keyed memo to preserve cycles/shared refs.
9. `find_members_by_name` / `_member_search_text`: casefolded substring match
   over the six name fields; blank query → `[]`.
10. `_load_contributions_value` / `_normalize_load_contributions`: CLI-over-config
    precedence; validate the `print_member` mapping and its single
    `load_contributions` key; normalize bool/date/`YYYY-MM-DD`-string and reject
    bad dates and wrong types with `ConfigError`.
11. Wire the entry point `pk-query-ps-memfam = "parishkit.cli:print_member_main"`
    and add the executable wrapper `scripts/pk-query-ps-memfam/pk-query-ps-memfam.py`.
12. Ship `example-config.yaml` (`common`, `slack`, `parishsoft`, `print_member`)
    and the operator `README.md`.

## 11. Cross-references

- [Top-level ParishKit spec](../intro/spec.md) — shared design, architecture,
  and the foundational layers this tool builds on:
  [shared CLI layer](../intro/spec.md#shared-cli-layer),
  [configuration system](../intro/spec.md#configuration-system),
  [logging and notifications](../intro/spec.md#logging-and-notifications),
  [retry policy](../intro/spec.md#retry-policy),
  [secrets and filesystem helpers](../intro/spec.md#secrets-and-filesystem-helpers),
  [ParishSoft data layer](../intro/spec.md#parishsoft-data-layer),
  [dry-run and write safety](../intro/spec.md#dry-run-and-write-safety),
  [testing/CI philosophy](../intro/spec.md#testing-ci-and-quality-philosophy).
- [ParishSoft API analysis](../../parishsoft-api-analysis.md) — the v2 REST surface
  read by `load_families_and_members` and what the dumped records contain.
- [`pk-print-ps-ministries`](../pk-print-ps-ministries/spec.md) — sibling
  read-only lookup tool sharing this structure.
- Source of truth: `src/parishkit/pk_query_ps_memfam.py`,
  `scripts/pk-query-ps-memfam/pk-query-ps-memfam.py`,
  `scripts/pk-query-ps-memfam/example-config.yaml`,
  `scripts/pk-query-ps-memfam/README.md`, and `tests/test_print_tools.py`.
