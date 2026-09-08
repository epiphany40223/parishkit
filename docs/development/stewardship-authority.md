# Stewardship configuration-authority contracts

This records the implemented file/protocol portion of
[ARC-02](../tasks/stewardship/architecture.md#arc-02-shared-cli-configuration-paths-and-app-startup).
The [normative configuration contract](../specs/stewardship/architecture/spec.md#configuration-and-secrets)
remains authoritative. No online installer, product configuration editor,
production schema validator, or PostgreSQL materializer is implemented yet.

## Version envelope

Schema version 1 has exactly four top-level fields:

- `schema_version`: integer `1`;
- `version_id`: stable UUID string;
- `predecessor_digest`: canonical predecessor SHA-256 or null for bootstrap;
- `sections`: named collections of stable-ID records.

Section names are `parish`, `integrations`, `login_rules`, `campaigns`, `content`,
`schedules`, `share_options`, `ministries`, and `funds`. Each record has exactly
`id` (UUID) and `values` (mapping). Record IDs are globally unique within a
version. Collection order canonicalizes by ID; explicit product-schema fields
must represent any user-visible order. Record IDs are never generated on import.

Values use explicit JSON-compatible types: money, dates, and timestamps serialize
as strings, never floats or implicitly parsed YAML date objects. Canonical JSON
bytes use sorted keys and UTF-8; their SHA-256 identifies the configuration.
Human-readable YAML is the durable version document. Equivalent YAML formatting
does not alter the canonical digest. The parser bounds depth, node count, and
canonical size; file reads additionally bound input size. Duplicate YAML keys,
including ambiguous merge overrides, are rejected by the shared opt-in strict
loader without changing legacy ParishKit tools' default YAML behavior.
The shared [strict loading limits](stewardship-deployment.md#strict-yaml-loading)
also bound composition and alias expansion before YAML object construction;
canonicalization checks remain an additional layer, not the first resource bound.

The required `validate_sections` callback owns the complete product schema,
required/unknown/secret fields, and cross-reference checks. No permissive default
exists. Tests use a deliberately tiny synthetic schema; it must never be used
as a production validator. Product validation integrates with DAT-01 and the
ADM-02/ADM-03/ADM-04 configuration workflows.

Unknown schema versions fail closed. Before introducing version 2, implement and
test an explicit versioned importer migration preserving stable IDs and retained
version semantics; never reinterpret old documents using a newer schema.

## Storage and activation

`AuthorityStore` consumes an already provisioned authority directory. It writes
`<version UUID>.yaml` as a restrictive, fsynced immutable file, atomically linking
it into place without overwriting an existing ID. Exact retries are harmless;
same-ID/different-content writes fail. It selects an existing verified version
through an atomically replaced, fsynced `active.yaml` manifest containing schema
version, version UUID, and canonical digest. Reads verify both ID and digest.

These writes target the POSIX application-service filesystem. Windows deployment
uses Linux containers; file durability is not claimed for native Windows calls.
Directory provisioning, ownership/symlink/mount validation, and installer service
authorization belong to OPS-02/ARC-06. The primitives are not exposed through
HTTP or a mutating CLI command.

The `Materializer` protocol requires deployment-wide serialization and durable,
idempotent prepare/activate operations. The coordinator performs:

```text
validate base → persist immutable YAML → prepare matching DB materialization
             → atomically select YAML → activate matching DB snapshot
```

An early failure preserves the old active version. A failure after manifest
selection leaves an intentional mismatch: callers must fail closed until
`recover_active` activates the exact already prepared matching snapshot.
Recovery never guesses a newer side or reconstructs an unprepared candidate.
Database activation must atomically update request/audit checkpoints with the
applied pointer. DAT-01/ARC-06 supply that concrete implementation; the memory
materializer in tests proves coordination behavior, not database durability.

## Remaining integration

Production settings remain intentionally disabled. ARC-02.05 still needs actual
database/migration/mode/configuration agreement and credential/mount/Valkey
checks. Those checks need the records assigned to DAT-01 and the operational
adapters assigned to ARC-06/OPS-04. The approved
[master-plan phase split](../plans/stewardship/overall.md#phase-0-reproducible-project-skeleton)
keeps independent contracts in Phase 0 and completes database-backed production
startup integration in Phase 1 before Gate 1. All review gates remain unchanged;
partial tasks remain unchecked until their remaining verification passes.
