# ParishKit Sync module — Ministry rosters

Writes each configured ministry's roster into a Google spreadsheet.
Overview: [Sync spec](../spec.md).

## Behavior

Roster content and formatting are unchanged from
[`pk-create-ps-ministry-rosters`](../../pk-create-ps-ministry-rosters/spec.md),
which remains the reference.

- Consumes `parishsoft.core` (see
  [ParishSoft download](../parishsoft-download/spec.md)).
- Uploads through the [FileUpload](../providers/spec.md#fileupload) provider.

## What changes from the CLI tool

- **Config:** two kinds of roster record, matching the CLI tool's
  `ministries` and `workgroups` targets (a discriminated union):
  - **Ministry roster:** one or more ministries (`PSMinistryRef` list;
    members of any of them are combined into one roster), a spreadsheet
    (`DriveFileRef`, chosen with a Drive picker that shows the current
    title), the birthday option, and optional **role sheets**, each with a
    name, a list of roles and its own `DriveFileRef`.
  - **Workgroup roster:** member workgroup references (base and/or leader;
    see Leader workgroups below) and a spreadsheet; role sheets are not
    allowed, as in the CLI tool.
- **Unique outputs:** no two outputs (rosters and role sheets) may share a
  spreadsheet, because uploads replace whole files; the form validates this.
- **Leader workgroups:** the CLI tool's `workgroup_leader_suffix` finds a
  workgroup's leaders by a name suffix (a space followed by `Ldr`, by
  default). Like the [Google Groups leader workgroups](../google-groups/spec.md),
  a workgroup roster instead holds an optional base workgroup reference and
  an optional leader workgroup reference, with **at least one** set. A
  leader-only roster stays valid, as the CLI tool allows (covered by
  `test_create_ministry_rosters_validation_uses_custom_leader_suffix`). The
  legacy importer fills both from the suffix convention.
- **IDs, not names** for ministries and workgroups; spreadsheets were already
  bound by file ID.
- **Dry run** becomes Preview.

Parity target: a real parish config with 76 roster spreadsheets.
