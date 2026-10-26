# ParishKit Sync module — Google Groups

Keeps Google Group membership in sync with ParishSoft ministries and
workgroups. Overview: [Sync spec](../spec.md).

## Behavior

Reconciliation behavior is unchanged from
[`pk-sync-ps-to-ggroup`](../../pk-sync-ps-to-ggroup/spec.md), which remains
the reference for what a sync adds, removes, preserves and reports.

- Consumes `parishsoft.core` (see
  [ParishSoft download](../parishsoft-download/spec.md)).
- Credential slot: a Google service account with a delegated subject.

## What changes from the CLI tool

- **Config** moves from YAML to the generated UI (see
  [modules](../modules/spec.md)). Each group rule has: the target group
  (`GoogleGroupRef`), notify addresses, source ministries
  (`PSMinistryRef`), source member workgroups (`PSMemberWorkgroupRef`; the
  CLI tool reads member workgroups only), selectors, and static members.
  Module settings include the Google mail domains.
- **Leader workgroups:** the CLI tool pairs a source workgroup "X" with
  sibling workgroups named "`X Ldr`" and "`X Leader`" (either or both may
  exist). Members of X become group members; members of either sibling are
  **included as owners even if they are not in X**; and X is a valid source
  even when only a sibling exists. That pairing is name-based, so a rename
  breaks it. Each workgroup source instead holds an optional base workgroup
  reference and a list of leader workgroup references (all
  `PSMemberWorkgroupRef`), with at least one of them set. Members of the
  base workgroup are members; members of any leader workgroup are owners,
  whether or not they are in the base. The legacy importer fills these from
  the naming convention (see
  [legacy YAML import](../modules/spec.md#legacy-yaml-import)).
- **Selectors** are a discriminated union of the CLI tool's three types:
  `ministry_chair`, `all_ministry_chairs` (plus staff-owner domains) and
  `ministry_role`. A `ministry_role` selector matches a member whose role is
  in its `member_roles` or its own `leader_roles`; a matched member becomes
  an owner when their role is in the selector's `leader_roles` **or** in the
  module-wide `leader_roles` below, exactly as the CLI tool does (so
  "Chairperson" in `member_roles` still yields an owner). Each keeps its optional
  `purpose` text. Every selector applies to an explicit **list of
  ministries** (`PSMinistryRef` list, chosen with the
  [searchable multi-select picker](../modules/spec.md#pickers)) instead of
  the CLI tool's `ministry_prefix`/`ministry_pattern` name matching (see
  [no name patterns](../modules/spec.md#no-name-patterns-in-stored-config)).
- **Leader roles:** the module setting `leader_roles` (default
  `["Chairperson", "Staff"]`, distinct from a `ministry_role` selector's own
  `leader_roles`) defines who counts as a ministry leader, exactly as in the
  CLI tool:
  - **ministry sources:** a member whose role is in `leader_roles` is an
    owner;
  - **`ministry_chair`:** selects ministry leaders, who are owners;
  - **`all_ministry_chairs`:** selects ministry leaders, who are owners
    **only when** their email domain is in the selector's
    `staff_owner_domains`; otherwise they are plain members;
  - **`ministry_role`:** a matched member whose role is in `leader_roles` is
    an owner (as above).
- **IDs, not names:** ministries, workgroups and groups are bound by durable
  ID; a missing ID fails that rule and never empties the group (see
  [missing references](../modules/spec.md#missing-references)).
- **Dry run** becomes Preview.
- **Notifications** go through the core send-email service (`ctx.mail`; see
  [providers](../providers/spec.md#mail)).

Parity target: a real parish config with 102 group rules.
