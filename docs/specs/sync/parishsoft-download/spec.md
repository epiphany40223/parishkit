# ParishKit Sync module — ParishSoft download

A producer module: downloads ParishSoft data once per run for every due
consumer. Overview: [Sync spec](../spec.md). Datasets:
[jobs](../jobs/spec.md#datasets).

## Behavior

- Uses the shared ParishSoft v2 client, including pagination with automatic
  progress and the expected-organization check from core settings.
- Performs the load the sync tools use today:
  `load_families_and_members(active_only=True, parishioners_only=False)`.
  Google Groups, Constant Contact and ministry rosters all use this load.
- Produces the dataset `parishsoft.core` (families, members, contact info,
  family and member workgroups with memberships, ministry types with
  rosters), written in a streaming format to the run's temporary directory.
- Updates the [current-names table](../modules/spec.md#current-names-table)
  for configured ParishSoft references and logs renames.

Measured: about 77 seconds (network-bound) and a 106 MB child peak for a
parish of about 1,300 families; the serialized dataset was 7.4 MB (see
[Footprint](../spec.md#footprint)).

## What changes from the CLI tools

- Replaces each tool's own ParishSoft download and the `cache_dir` and
  `cache_limit` cache. Nothing is cached across runs.
- The API key is a credential slot, not a file.

## Config

None beyond core settings (ParishSoft credential slot and expected
organization).
