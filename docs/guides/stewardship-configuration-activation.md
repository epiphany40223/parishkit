# Stewardship configuration activation

This is the internal DAT-01/ARC-02 activation portion of the
[configuration authority protocol](../specs/stewardship/architecture/spec.md#configuration-and-secrets).
It builds on [preparation](stewardship-configuration-preparation.md) and
[request intake](stewardship-configuration-requests.md). It enables no route,
worker, operational bootstrap command, or production startup.

## Durable protocol

`SystemConfiguration` is the singleton PostgreSQL runtime record, distinct from
YAML materializations. This initial schema admits only Testing mode and a valid
Testing recipient. Its protected active-configuration pointer advances only with
an immutable `ConfigurationActivation`. Activation sequence, predecessor,
candidate, request, actor, and correlation bind the event to its intended history.
PostgreSQL commits the pointer, request's Applied checkpoint, and value-free audit
events together; failure of any effect rolls back all of them. Historical
configuration rows remain immutable. The runtime record cannot be deleted or
rewritten through a generic version-advancing update.
Downgrade remains possible for empty/intake-only databases. Migration 0013
refuses reversal before removing any protection if activation or post-intake
checkpoint history exists; it never silently erases or downgrades that history.

`DatabaseMaterializer` implements the existing `apply_version`/`recover_active`
contract. `installation_lock` pins a direct PostgreSQL connection while a
deployment-wide session advisory lock `(736212, 1)` spans file writes and separate
durable transactions. It never nests snapshot preparation inside an outer
transaction. A competing installer gets a retryable busy error instead of waiting
indefinitely. A changed/closed connection or cross-thread invocation fails closed;
cleanup unlocks the original connection, never a newly connected substitute.
Transaction-pooling proxies are incompatible with this session-lock protocol.

`install_request` reuses the retained patch builder, base, candidate identity, and
fingerprint. Its append-only checkpoints are staged, validating, prepared,
yaml_activated, and applied. Cancellation is allowed only from staged. Stale-base
and invalid-candidate failures have fixed safe codes, never arbitrary exception
text. Operational file/database/schema-environment and active-base verification
failures retain a resumable
checkpoint. Once YAML is selected, failure cannot discard that candidate: exact
prepared-version recovery must complete first. Another request cannot repair or
overwrite that mismatch. No implicit rebase, backward activation, or skipped
predecessor is allowed.

Snapshot commit precedes its prepared checkpoint. A crash in between is safe:
retry verifies the immutable snapshot and appends the missing checkpoint. Likewise,
selection may precede its yaml_activated checkpoint; recovery verifies the exact
manifest and prepared history before resuming. Applied and activation evidence
are atomic, so lost acknowledgement cannot duplicate activation or audit events.

Receipts contain applied-version identity/digest and authoritative affected
records only after activation. Removed records have null values. Returned values
are copies; modifying them cannot rewrite a receipt. A historical Applied receipt
continues to describe its own version after later saves. It is not evidence of
current application readiness. `coherent_configuration` performs a bounded
selected-snapshot/projection check with two manifest reads; callers performing
mutations still require their own serialization and fresh admission guards.

## Remaining integration boundaries

`prepare_initial_configuration` is an internal empty-runtime primitive tested
with disposable files. It is not the OPS-04 bootstrap command or its offline
interlock, and cannot apply requestless successors. Matching initialization can
resume; a changed recipient or root cannot overwrite an initialized deployment.
Runtime creation and its recipient commit with root activation, not before file
work. An interrupted initial attempt therefore does not permanently freeze an
uncommitted recipient; the exact prepared root can resume with corrected runtime
input. Once activation commits, recipient changes require the future authorized
runtime configuration workflow. Validation applies both the email validator and
the database's shape restriction before any write. A pre-bootstrap digest is
null, and request installation before initialization fails resumably.
The complete Admin wizard and initial login-rule schema remain later work.

Actor UUIDs are attribution, never authenticated Admin authority. Current role,
session, CSRF, and admission checks are required before any browser/queue exposure.
The supported frozen schema still cannot edit roles, campaigns, schedules, or
secrets. Fingerprints do not attest to installed credential files or consumer
acknowledgement. ARC-06/OPS-02 own service identities, grants, mount separation,
and credential evidence before activation becomes an operational capability.

DAT-01/DAT-02/DOM-02 still own full runtime campaign/mode/history and restore
metadata/guards. The restore flag is not editable here and this primitive cannot
release a restore. Explicit Parish audit ownership remains to be integrated;
current safe audit envelopes retain deployment ownership. Secret replacement and
offline Admin recovery retain their original owners and cannot be selected with a
caller-supplied bypass flag. Production settings continue to reject startup.

Run the [disposable PostgreSQL tests](stewardship-database-tests.md) for real
transaction, interruption, constraint, and concurrency evidence. Results and
independent review dispositions belong to the [milestone ledger](../tasks/stewardship/milestones.md).
