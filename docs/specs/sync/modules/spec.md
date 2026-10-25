# ParishKit Sync — Modules and Configuration

How a module declares itself, how its configuration is defined, rendered,
stored and upgraded, and how config refers to upstream entities. Overview:
[Sync spec](../spec.md). Execution: [jobs](../jobs/spec.md).

## Manifest

Each module declares:

- `name`, `version` and a short description.
- `config_model` — a Pydantic model (see [Config schema](#config-schema)).
- `config_version` plus upgrade functions from each older version.
- `credentials` — named slots (see
  [credentials](../credential-store/spec.md#slots-and-binding)).
- `consumes` and `produces` — dataset names (see
  [jobs](../jobs/spec.md#datasets)).
- `jobs` — one or more entry points, each with a default schedule, total
  timeout and stall timeout (for example, the Constant Contact module has a
  frequent sync job and a weekly report job).

Modules can be enabled, disabled and scheduled per job. A disabled module
keeps its config.

## Config schema

- Config is a Pydantic model. Field titles, help text and widget hints live
  on the fields.
- Supported shapes include scalars, lists, nested models, **discriminated
  unions** (a `type` field selects the variant, as with Google Groups
  selectors) and [reference types](#references).
- Validation errors are shown next to the fields, in plain language.

## Generated UI

The core renders all config UI from the schema; modules write no UI code.

- Forms for scalar and nested fields.
- **Collection tables** for lists of records, which dominate real configs
  (for example 102 Google Group rules or 76 roster spreadsheets): search,
  sort, add, edit, duplicate, delete, and bulk enable and disable.
- Variant pickers for discriminated unions.
- Live pickers for references.

A module that needs custom UI indicates a gap in the renderer; fix the
renderer instead.

## Saving, Preview and revisions

- **Save applies immediately.** There is no separate Review or Apply step.
  Save validates the schema and the next run uses the new config. This is a
  recorded exception to the shared
  [Review, Apply and Change status](../../stewardship/ui-conventions/spec.md#review-apply-and-change-status)
  rule (Administrator decision, 2026-10-25).
- **Preview this change** runs the module's job as a dry run against the
  unsaved edits and shows the planned changes (for example "adds 14 members
  to the group"). How the edits reach the child, and how Preview queues, is
  in [Preview runs](../jobs/spec.md#preview-runs).
- Each save creates a **revision**: who, when, the `config_version` it was
  written in, and the full document. The UI shows diffs between revisions
  and can **revert** to any one. A revert runs the upgrade functions from the
  revision's `config_version` to the current one, validates the result
  against the current schema, and saves it as a new revision; a revision
  that cannot be upgraded or validated is refused with the reason.
- Config changes are written to the audit trail (see
  [logging](../logging/spec.md#audit)).

## Storage

- Each module's config is one JSON document in SQLite, with its revisions.
- A small **key-value state store** per module holds operational state that
  is not config (for example the date the last unsubscribed report was
  sent).

## Config upgrades

When a new version changes a module's `config_version`, the upgrade functions
run at startup, and the upgraded document is saved as a new revision
attributed to the system. A module never runs with partially upgraded
config.

- **During an update** (the normal case), any module's upgrade failure fails
  the new version's health check, so the updater restores the pre-update
  snapshot and restarts the previous version (see
  [Updater](../deployment/spec.md#updater)). An alert names the module and
  the error. An update never succeeds by silently disabling a production
  sync.
- **Outside an update** (for example a manual restart of the same version
  that finds an un-upgraded document), the failing module is disabled and an
  alert is raised.

## References

Config refers to upstream entities by **durable ID**, never by name.
Administrators pick entities by name from live search pickers.

| Type | Stored ID | Shown as |
|---|---|---|
| `PSMinistryRef` | ParishSoft ministry type ID | current ministry name |
| `PSMemberWorkgroupRef` | ParishSoft member workgroup ID | current workgroup name |
| `PSFamilyWorkgroupRef` | ParishSoft family workgroup DUID | current workgroup name |
| `CCListRef` | Constant Contact `list_id` | current list name |
| `GoogleGroupRef` | Google Directory group ID | current address and name |
| `CalendarRef` | calendar ID | current calendar or resource name |
| `DriveFileRef` | Drive file ID | current file title |

ID stability: ParishSoft ministry type IDs and member workgroup IDs were
verified unchanged across a rename in the ParishSoft UI
([#2210](https://github.com/epiphany40223/parishkit/issues/2210)). Family
workgroup DUIDs are expected to behave the same; verify when a module first
binds one. Constant Contact list IDs, Google group IDs, calendar IDs and Drive
file IDs are documented as stable.

Picker lookups are fetched live; results may be held in memory for a few
minutes at most.

### Pickers

Reference fields that hold a list (for example a selector's ministries) use
a **searchable multi-select picker**, because a parish can have hundreds of
ministries:

- A filter box narrows the list as the administrator types (case-insensitive
  substring match on the current name).
- Each entry has a checkbox; several entries can be selected without
  reopening the picker, and selections persist while the filter changes.
- Selected entries are shown as a removable list, by current name, next to
  the field, with a count.
- "Select all shown" and "Clear" act on the filtered list, so "every
  ministry starting with 1" is a filter plus one click; the result is still
  stored as explicit IDs.

Single-reference fields use the same picker with single selection.

### Current-names table

- Stores the latest name of each **configured** reference only, updated by
  runs and picker lookups.
- The UI always shows the current name.
- Renames are logged (for example "Ministry 123 renamed 'A' to 'B'") and may
  notify.
- This is the one deliberate exception to
  [No cached upstream data](../spec.md#no-cached-upstream-data).

### Missing references

If a referenced ID no longer exists upstream, the rule that uses it **fails
loudly** and the administrator is notified. It is never treated as an empty
source, which could, for example, remove everyone from a Google Group. The UI
flags broken references and offers a picker to fix them.

### No name patterns in stored config

Config never stores name prefixes or regular expressions. A selector that
the CLI tool expressed as a pattern (`ministry_prefix`, `ministry_pattern`)
becomes an explicit list of references. A ministry created later is not
picked up automatically; an administrator adds it with the picker. This is
deliberate: a rename can never silently move an entity into or out of a
rule.

## Legacy YAML import

A back-end management command, run inside the container (for example
`parishkit-sync import-legacy-config --dir /import [--dry-run]`). It is not
an administrator UI feature.

1. Imports credential files into the credential store.
2. Imports the repeated blocks (`common`, `logging`, `slack`, `parishsoft`,
   `google`, `email`) into core settings.
3. Imports each tool's domain config into its module.
4. Resolves every name to a durable ID against live ParishSoft, Google and
   Constant Contact.
5. Reports unresolved names and imports them as flagged broken references;
   nothing is silently dropped.

Selectors with a `ministry_prefix` and/or `ministry_pattern` (both must match
when both are set, as in the CLI tool) are resolved once, against today's
ministry names, to the list of every matching ministry's ID; the prefix and
pattern are then discarded. A selector with **neither** filter matches
every ministry in the CLI tool, so it is resolved to the IDs of all of
today's ministries, and the import report says that ministries created later
will not be added automatically. The import report lists each selector's
matches. A selector that matches no ministry is imported as a flagged
broken selector.

Google Groups workgroup sources follow the CLI tool's leader convention
before any name is classified as unresolved: for a source name "X", the
importer resolves "X" (if it exists) as the base workgroup and each existing
"`X Ldr`" and "`X Leader`" as leader workgroups. The source is broken only
when none of the three exists.

Ministry roster workgroup sources follow the rosters tool instead, which uses
**one** companion workgroup: the importer resolves "X" as the base and "X"
plus the configured `workgroup_leader_suffix` (default: a space followed by
`Ldr`) as the single leader reference. The source is broken only when neither
exists. If "X" validated in the CLI only through a sibling with a different
suffix (so the CLI built an empty roster), the import report flags it.

`pk-cron-runner` settings map
to job schedules and timeouts. `--dry-run` prints only the resolution report.
Tests use sanitized fixtures; real parish configs never enter this
repository.

## Rejected approaches

- **Hand-written Django forms per module:** repeated work and inconsistent
  UX.
- **JSON Schema with a React form library:** too heavy for the footprint and
  the server-rendered UI.
- **Draft then Apply:** more UI and state for a small administrator team;
  Preview and revert cover the risk.
- **Matching upstream entities by name:** a ParishSoft rename has already
  broken production syncs.
