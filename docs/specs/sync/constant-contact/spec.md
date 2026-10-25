# ParishKit Sync module — Constant Contact

Keeps Constant Contact lists in sync with ParishSoft workgroups. Overview:
[Sync spec](../spec.md).

## Behavior

Reconciliation behavior is unchanged from
[`pk-sync-ps-to-cc`](../../pk-sync-ps-to-cc/spec.md), which remains the
reference.

- Consumes `parishsoft.core` (see
  [ParishSoft download](../parishsoft-download/spec.md)).
- Credential slot: Constant Contact OAuth client and tokens; the core
  refreshes them (see [credentials](../credential-store/spec.md#refresh)).

## Jobs

- **sync** — frequent list synchronization.
- **unsubscribed-report** — the weekly report of filtered unsubscribed
  contacts, on its own schedule. Its last-sent date moves from a state file
  to the module state store (see [modules](../modules/spec.md#storage)).

## What changes from the CLI tool

- **Config:** list mappings from a source member workgroup
  (`PSMemberWorkgroupRef`; the CLI tool reads member workgroups only) to a
  target list (`CCListRef`) with notify
  addresses; module settings for name updates and the report.
- **Email:** change notifications and the unsubscribed report go through
  the core send-email service (`ctx.mail`; see
  [providers](../providers/spec.md#mail)).
- **IDs, not names:** workgroups by ParishSoft ID and lists by `list_id`.
- **Dry run** becomes Preview.
