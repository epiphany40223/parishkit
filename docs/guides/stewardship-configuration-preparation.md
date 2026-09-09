# Stewardship configuration preparation

This increment implements the preparation portion of
[DAT-01](../plans/stewardship/data.md#dat-01-storage-conventions-and-base-records),
not configuration activation or a usable installer. The
[configuration authority specification](../specs/stewardship/architecture/spec.md)
continues to control the complete YAML/database protocol.

## Persisted contract

`prepare_snapshot` reparses a ConfigurationVersion with the concrete non-secret
schema before opening a transaction. It atomically inserts the canonical
AppliedConfigurationVersion and all normalized Parish/AppliedIntegration rows.
An exact retry returns the original identity and attribution. Conflicting UUID
reuse, unknown predecessors, another root, or changing the parish's stable
record ID fails. Transaction-level PostgreSQL advisory lock `(736210, 1)`
serializes cooperating preparations, including the first insertion; a partial
unique database constraint independently prevents two roots.

Preparation must own its outermost transaction: callers with an active atomic
block or disabled autocommit receive `StorageInvariantError`. A durable atomic
block commits new rows and releases the advisory lock before returning. Future
installer integration must preserve this boundary, not wrap it in a savepoint.

Every projection protects its configuration version with a foreign key; there
is exactly one Parish per successfully prepared version and one integration per
kind/record ID within that version. Versioned profile primary keys identify
historical projections; record IDs identify stable YAML objects across versions.
There is no campaign ownership or cascade. PostgreSQL append-only guards reject
UPDATE/DELETE on canonical and projection rows, including raw SQL. Existing
audit ownership remains deployment-level until its activation integration lands.

`is_prepared` reparses the stored canonical document, checks version identity,
digest, predecessor, validation-schema identity, and normalized digest, then
reconstructs actual projections and checks their digest too. It iteratively
verifies the entire predecessor chain and stable parish/integration identities,
rejecting cycles without a recursion limit. A recursive query plus projection
prefetches loads the history in three database queries; canonical record-ID
sorting in memory preserves the normalized digest. Full verification and parsing
run before the global preparation lock. Under the lock, a concurrent exact
candidate is rechecked against the already-validated document and local
projections. Cooperating writers only append complete immutable versions.
Verification CPU/memory cost remains linear in retained history; this internal
preparation predicate is not a per-request readiness API or a trusted cache.
Keeping full ancestry verification preserves corruption detection rather than
assuming an arbitrary stored predecessor was previously validated.
A missing or mismatched ancestor/projection is not prepared. Database availability
errors and `SchemaEnvironmentError` propagate for the owning readiness boundary
to fail closed. The latter denotes unavailable/corrupt installed schema data,
not invalid user input or corrupt stored history. Preparation is never evidence
that the YAML manifest and database active pointer agree.

## Supported schema subset

The envelope remains schema version 1. Preparation validation evidence is named
`parish-integrations-v1`. Preserve this historical schema's meaning when later
packages add validators; never silently reinterpret existing prepared documents.
Historical rows dispatch through `validator_for` using their stored discriminator;
new candidates use the current validator. Add a separate validator and a database
migration admitting its name for a future schema, retaining historical functions,
helpers, and schema data unchanged.

Version 1's timezone names are frozen in the package's `timezone_names_v1.txt`,
copied from the tzdata 2026.3 catalog. The loader verifies SHA-256
`5027e610a10d1983d286e21fa1fb718f0d34704446cb37f707e81707bb3c1244`.
Dependency or host timezone updates cannot alter this schema's accepted names;
this asset does not freeze timezone transition rules used by runtime clocks.
The wheel-build regression verifies inclusion of the exact asset.
The executable synthetic examples are in
[configuration_factory.py](../../tests/stewardship/configuration_factory.py).

- Exactly one Parish: name, HTTP(S) website without embedded credentials, query,
  or fragment; timezone from the frozen application schema catalog (independent
  of installed tzdata and host TZPATH); canonical US phone; and opaque UUID references for
  all four already-normalized branding variants.
- Optional integration records: expected ParishSoft organization, Google OAuth
  client ID, Workspace delegated email, sender/reply-to emails, Slack channel,
  or a non-credential HTTP(S) backup target. Each kind has an exact settings
  allowlist and an optional lowercase SHA-256 credential fingerprint.
  An integration kind retains its record ID across the entire predecessor chain,
  including removal/re-addition; historical IDs cannot be reused for other kinds.
- Unknown fields and nonempty later-owned sections are rejected. Empty later
  sections can be retained without implying implemented behavior. Branding
  upload decoding/resource validation and provider-specific credential testing
  retain ARC-03/ADM-03/ARC-06 ownership. This preparation subset is not a complete
  first-Admin setup validator and does not require integrations to be configured.

The schema rejects credential fields and secret-bearing URL syntax; it cannot
infer whether someone intentionally pasted a secret into a valid display name.
Callers must retain the specified secret-handoff boundary. Errors never repeat
submitted field values or unknown field names. Raw model writes are not the
supported preparation API; deliberately malformed database inserts in tests
prove that completeness verification refuses them. Runtime database privilege
separation remains an OPS-02/OPS-04 prerequisite before deployment.

## Next integration steps

The [request intake](stewardship-configuration-requests.md) and
[internal activation](stewardship-configuration-activation.md) increments add
ordinary request checkpoints, Testing runtime, active pointer/history, and a
concrete PostgreSQL Materializer. The preparation lock remains transaction-scoped
and must not be mistaken for the cross-checkpoint installer lock. Competing
prepared successors are permitted; installation rejects stale bases. Secret
replacement, operator recovery, complete runtime state, and authorization/service
integration remain required before any route, CLI, or worker exposes these APIs.

Run the [disposable PostgreSQL suite](stewardship-database-tests.md) for
constraints, corruption rejection, races, rollback, restart, and migration
reversal/reapplication. Ordinary baseline skips are not database verification.
