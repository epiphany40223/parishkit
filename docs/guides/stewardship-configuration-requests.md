# Stewardship configuration request intake

This is the durable intake portion of
[DAT-01.02/.03](../plans/stewardship/data.md#dat-01-storage-conventions-and-base-records),
building on [prepared configurations](stewardship-configuration-preparation.md).
The [normative request contract](../specs/stewardship/data/spec.md#parish-and-integrations)
controls the eventual complete workflow. No installer or web editor is enabled
by this increment.

## Implemented boundary

`record_request` stores a validated minimal patch against an exact stored base
whose own canonical document, metadata, and projections verify. It does not
reload/reparse the entire canonical ancestry for each autosave or retry.
`ConfigurationChangeRequest` is immutable and binds one actor UUID/client
UUID key to the canonical patch/base fingerprint and a fixed candidate UUID/digest.
Identical retries return the original request and current checkpoint; different
payload or base reuse is rejected. The key never implies authority or a new save.

The private patch format is a list of 1–100 disjoint stable-record operations.
Each operation names `section`, `id`, and `operation`; add/update also supply
`values`. Parish supports only update. Integrations support add, update, and
remove. An update replaces named top-level fields, including a complete nested
settings/branding value; it is not recursive JSON merge. Missing targets are
never recreated, add never means upsert, and multiple operations on one record
are rejected. Disjoint operations canonicalize by section/ID. The resulting
whole document must pass the strict non-secret preparation schema.

Integration updates cannot contain `kind` or `credential_fingerprint`, even to
resubmit an unchanged value. New integrations require a null fingerprint; only
the future target-specific secret workflow may establish credential evidence.
An addition checks historical kind/ID bindings, including retired identities,
using a recursive UUID-link query returning at most one conflict. That query
still traverses ancestry, but never loads historical canonical JSON into intake.
Removals and settings-only updates require no ancestry query. A remove/add pair
cannot replace a historical kind's ID, and a retired ID cannot name another kind.

Each request retains `parish-integrations-patch-v1` as its request-schema name.
Its frozen builder selects `parish-integrations-v1` validation, including that
schema's frozen timezone catalog. Introduce a new builder and database-admitted
name for a future format; keep historical builders available. Identical retries
dispatch the stored name even after the current builder changes.
The per-key lock is acquired before selecting the schema and validating the
patch, so a concurrent winner's stored format governs the retry, including
during a schema upgrade. Base verification and cheap container checks precede
that lock; the complete history is never verified under it.

`ConfigurationRequestCheckpoint` is append-only: its latest sequence is the
state, without a second mutable status field. PostgreSQL atomically inserts the
initial `staged` checkpoint and a value-free audit event with each request. A
second intake checkpoint can cancel staged intake. The subsequent
[activation increment](stewardship-configuration-activation.md) also admits the
installer's validating/prepared/YAML/applied/failure path. Database guards lock the
request, enforce sequence/actor/time consistency, reject unsupported transitions,
and insert the matching audit in the same transaction. Raw UPDATE/DELETE cannot
rewrite requests or checkpoints. Audit INSERT failure rolls back the operation.

Intake and cancellation require ownership of their outer transaction. Per-key
advisory lock namespace `(736211, hash(actor UUID + key UUID))` serializes equal
keys; the second component is a signed 32-bit SHA-256 prefix. A rare hash collision
only serializes unrelated keys, never combines their identities. Cancellation
locks its request row, not every request sharing its base. SQL actor/key uniqueness
independently prevents duplicates. Returned receipts are committed, and repeated
cancellation cannot duplicate checkpoint/audit history.

Status lookup and cancellation require the submitting actor UUID, concealing
both other actors' and absent requests with the same unavailable error. Receipts
identify the candidate explicitly. Applied fields remain null until the separate
activation workflow commits; historical Applied values do not certify current
readiness. No request values enter audit/error text.

## Required integration before exposure

These are internal storage primitives without a route, CLI, or worker consumer.
Actor UUIDs identify history; they do not authenticate an Admin. ARC-04/ADM-03/
ADM-07 must supply current Admin/session/CSRF and activation/admission checks on
every call, including retries and status lookup. Historical prepared bases may
be recorded by this layer; the installer must reject a stale active base before
manifest selection. No automatic rebase is performed. The complete frozen
candidate can be reconstructed from the retained base, patch, schema, and UUID;
the installer must verify its stored candidate digest before preparing it.
Snapshot preparation retains its full ancestry/corruption verification. The
intake-local check is not a substitute for that check or a readiness certificate.
Activation/readiness must validate fingerprint evidence against installed
credential files and consumer acknowledgement; a null fingerprint on an added
integration neither deletes a credential file nor attests to its absence.

The [activation boundary](stewardship-configuration-activation.md) documents the
implemented installer checkpoints, matching YAML/database activation, historical
applied receipts, and Testing runtime. Session/security effects, full runtime
state, credential validation, and operational service integration remain pending.
Merely recording an intent cannot release startup.
SecretReplacementRequest and target-sealed handoff remain DAT-01/ARC-06 work.
Offline recovery remains OPS-04/DAT-05: no fabricated PortalUser or caller-supplied
operator bypass is accepted by intake. Login-rule, campaign, schedule, and content
patches require their owning concrete schemas/policies before admission.

Use the [disposable PostgreSQL profile](stewardship-database-tests.md) to verify
constraints, migrations, concurrency, committed receipts, and rollback. The
credential-free baseline covers patch canonicalization and rejection; skipped
database tests do not constitute integration evidence. Detailed review/count
evidence belongs to the [milestone ledger](../tasks/stewardship/milestones.md).
