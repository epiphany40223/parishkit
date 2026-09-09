# Stewardship secret-request storage

This internal DAT-01 increment implements metadata and cleanup for the
[secret replacement contract](../specs/stewardship/architecture/spec.md#configuration-and-secrets).
It does not enable secret replacement, a web route, worker, or production service.
The [configuration installer](stewardship-configuration-activation.md) continues
to accept only non-secret YAML requests.

## Storage contract

`SecretReplacementRequest` stores a closed credential-target identifier, an
opaque UUID referring to an external sealed object, requesting actor and
reauthentication instant, expiry, and optional expected prior fingerprint.
There are no payload bytes, arbitrary file paths, private keys, free-form errors,
or credential values in these tables or their safe receipts/audit events.
UUID reference uniqueness remains permanent even after cleanup, preventing a
later request from reusing the object identity of a historical request.

The request UUID binds the exact original intent. Identical retries return the
current receipt without creating history, including after expiry or cancellation.
Changed intent under an existing UUID is rejected. Actor-scoped status returns
only request UUID, state and version; unknown and out-of-scope IDs are unavailable.
These actor identifiers are attribution, not proof of current authentication.

A PostgreSQL partial unique constraint reserves each target while its request
is `staged` or `cleanup_pending`. A short deployment-wide advisory transaction
lock serializes metadata commands across absent request/target races. SQL guards
also enforce immutable bindings, version increments, UTC interval ordering,
state transitions, and append-only checkpoints/audit in the same transaction.
Failure of any audit insertion rolls back the state change. Generic deletes and
terminal rewrites are forbidden. Migration 0015 refuses populated downgrade
before removing guards; empty-schema reversal/reapplication remains supported.

## Cancellation and expiry

Cancellation by the requesting actor, or due expiry using the database clock,
moves staged work to `cleanup_pending`. The first cleanup reason wins. A request
remains reserved in this state until deletion of its external staged payload is
confirmed. It cannot be called successfully cancelled/expired just because a
cleanup task was queued.

`clean_secret_request` is an internal target-store port: its supplied callback
removes only the opaque object in that target's store. It receives no path or
credential bytes, runs outside database transactions, must treat already-absent
objects as success, and must raise on failure or uncertain removal with safe
diagnostics. The future storage adapter must preserve these semantics and enforce
the target's service identity/mount boundary. The caller's target string checks
consistency only; it does not grant a service identity or decryption authority.

Once the callback succeeds, a fresh short transaction records `cancelled` or
`expired`, scrub time, checkpoint, and value-free audit, releasing the target.
Callback failure retains pending cleanup. A crash after deletion but before
acknowledgement repeats idempotent deletion. Concurrent callbacks may both run,
but only one terminal transition/audit commits. Terminal retries do not call the
store again. No installer can claim any request in this increment.

## Remaining integration

ARC-04/ADM-03 own current Admin, CSRF, and fresh Google reauthentication checks.
ARC-06/OPS-02 own target-key sealing, bounded staging lifetimes, orphan cleanup,
target-isolated storage/queues/mounts/permissions, and service identity grants.
DAT-01/ARC-06 still need validated/tested/installed checkpoints, expected-prior
fingerprint comparison against the actual file, atomic replacement/recovery,
consumer acknowledgements, and success/failure outcomes. Those states require
an explicit migration; this storage subset cannot claim a credential is tested,
installed, or acknowledged. No claimed installer or plaintext acceptance API is
available. Old working credentials are untouched by all operations here.

The [database test guide](stewardship-database-tests.md) covers disposable test
setup. Tests use synthetic references and callbacks, never provider credentials
or actual secret files. See [milestones](../tasks/stewardship/milestones.md#secret-request-storage-increment)
for validation and independent-review evidence. DAT-01 remains partial until its
remaining records and integration contracts are delivered.
