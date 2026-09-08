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
verifies the entire predecessor chain and stable parish identity, rejecting
cycles without a recursion limit. Verification cost is linear in retained
history; this internal preparation predicate is not a per-request readiness API.
A missing or mismatched ancestor/projection is not prepared. Database availability errors propagate
for the owning readiness boundary to fail closed. Preparation is never evidence
that the YAML manifest and database active pointer agree.

## Supported schema subset

The envelope remains schema version 1. Preparation validation evidence is named
`parish-integrations-v1`. Preserve this historical schema's meaning when later
packages add validators; never silently reinterpret existing prepared documents.
The executable synthetic examples are in
[configuration_factory.py](../../tests/stewardship/configuration_factory.py).

- Exactly one Parish: name, HTTP(S) website without embedded credentials, query,
  or fragment; IANA timezone; canonical US phone; and opaque UUID references for
  all four already-normalized branding variants.
- Optional integration records: expected ParishSoft organization, Google OAuth
  client ID, Workspace delegated email, sender/reply-to emails, Slack channel,
  or a non-credential HTTP(S) backup target. Each kind has an exact settings
  allowlist and an optional lowercase SHA-256 credential fingerprint.
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

DAT-01.02/.03 must add configuration and secret requests, runtime Testing-default
state, the active pointer/activation history, authorized idempotency/status, and
operator-recovery attribution. ARC-02/ARC-06 must implement the complete
Materializer protocol with an installer lock held across file selection and
atomic activation effects. The preparation lock is transaction-scoped and must
not be mistaken for that cross-checkpoint lock. Competing prepared successor
candidates are permitted; the installer must still reject stale bases before
switching the manifest. No readiness function, route, CLI or worker currently
activates these snapshots or consumes them as runtime configuration.

Run the [disposable PostgreSQL suite](stewardship-database-tests.md) for
constraints, corruption rejection, races, rollback, restart, and migration
reversal/reapplication. Ordinary baseline skips are not database verification.
