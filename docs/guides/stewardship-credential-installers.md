# Stewardship credential installer protocol

This Phase 1B implementation extends the historical
[secret-request storage increment](stewardship-secret-requests.md) with the
[architecture's replacement contract](../specs/stewardship/architecture/spec.md#configuration-and-secrets).
It is not a production launch or an Admin credential-editor completion claim.
The [Phase 1B checkpoint](stewardship-phase-1b.md) tracks remaining scope and reviews.

## Queue and file ownership

The PostgreSQL request/staging tables are the target-specific queue. Broker task
bodies do not contain candidate credentials or ciphertext. A request and its
target-key-sealed staging row commit atomically; identical retries compare the
safe candidate fingerprint, not randomized encryption bytes. Request UUIDs,
staging references and terminal metadata cannot be rebound to later requests.

The web database role can insert sealed staging and read only its non-secret
metadata columns. It cannot select ciphertext or advance installation states.
Row-level security limits an installer to its own target. The installer admission
check requires the actual login and effective SQL role to match exactly, rejects
superuser/bypass/role-membership/schema-create authority, and rejects grants on
unrelated tables. Audit attribution uses only the active configuration UUID and
Parish identity columns; the installer cannot read campaign answers, configuration
contents or audit payloads. Production provisioning of these grants remains
OPS-02/OPS-04 work; tests create and remove their own disposable restricted roles.

`CredentialInstaller.from_configuration` validates real kernel mounts and SQL
identity before loading the one target handoff private key. Queue operations
repeat SQL admission, including after reconnects. The existing
[runtime storage rules](../specs/stewardship/operations/spec.md#runtime-storage)
control actual mounts. No role string or request UUID grants filesystem access.

## Durable replacement sequence

| Request state | Durable meaning |
| --- | --- |
| `staged` | Bound expiring sealed candidate, awaiting the matching installer |
| `testing` | Target installer owns validation; the working file is unchanged |
| `installing` | Tested fingerprint and sealed rollback journal precede file replacement |
| `awaiting_ack` | Atomic owner-only replacement completed; candidate staging is scrubbed |
| `cleanup_pending` | Applied, failed, cancelled or expired outcome is decided; cleanup remains |
| Terminal outcome | Staging is scrubbed and terminal checkpoint/audit committed |

The target's owner-only directory holds a nonblocking exclusive file lock and
an atomic private journal. The journal seals both the candidate and previous
working bytes to the target key, with different candidate/rollback contexts.
It never stages plaintext outside the selected working credential file. Database
transactions are short and do not enclose provider validation or filesystem IO.

A rename can succeed before an fsync or caller failure. Retry reconciles the
actual selected fingerprint against the journal; it does not assume an exception
means no write occurred. Unknown file fingerprints are preserved and block
completion. The old credential is restored on failure or unacknowledged expiry,
before the database reports a terminal outcome. Cancellation is accepted while
still staged; it cannot race a replacement already being installed.

Provider adapters signal transient validation failures with the fixed-message
`CredentialValidationUnavailable` exception. The request retains its sealed input
and `testing` state; OPS-04 retries with bounded backoff until ordinary expiry.
A false validation result or other validation failure is terminal. Provider
exception text is never part of the receipt or retry diagnostic.

Each consumer acknowledges through its own authenticated SQL login. Wrong-role,
wrong-target, wrong-fingerprint, expired and out-of-state acknowledgements fail.
The immutable acknowledgement and audit commit together. The frozen consumer set
must equal the complete target mount registry; neither web input nor raw SQL can
select only some consumers. Every required consumer
must match before the request can decide `applied`. That decision commits before
rollback ciphertext is destroyed. A crash after the decision replays using its
database acknowledgement instant, so later expiry cannot reverse an already
approved replacement. A scrubbed file journal is released only after the terminal
database receipt is durable; restart reconciles any remaining journal first.

## Metrics receipt privacy

Phase 1C implements the operations specification's
[metrics credential privacy contract](../specs/stewardship/operations/spec.md#observability-and-health)
with a versioned private `metrics-bearer` JSON file. Its bearer token and public
receipt are generated independently. `credential_receipt(value, "metrics")`
returns that opaque receipt, not a hash of the token or credential document.
Intake, durable status and consumer acknowledgements use this receipt; current
and previously used metrics receipt identifiers cannot be reused for a replacement.

The isolated local file journal still verifies exact working bytes and seals
rollback material. Its private byte hashes never cross the metrics database
boundary and are excluded with credential storage from logs, support bundles and
ordinary backups. The installer checks both the public durable receipt and the
private file receipt before cleanup. This avoids weakening atomic-rename or
rollback verification to a comparison of public labels alone. Consumers parse
the file and use only its token for bearer authentication.

Real restricted-role tests cover application, receipt-reuse refusal and expiry
rollback, and inspect durable request/checkpoint/staging/acknowledgement/audit
rows for token and token/file-hash leakage. Whole-consumer recreation and
operational acknowledgement are now covered by the
[Phase 1C consumer checkpoint](stewardship-phase-1c.md#credential-consumer-integration-checkpoint).

## Interrupted-installer recovery

OPS-04 must continually poll every configured target queue, including staged
expiry and cancelled-request cleanup, and alert on a stopped installer or stalled
queue. There is intentionally no alternate web/scheduler ciphertext-cleanup role.
An expired or cancelled request, including one still at `staged`, remains reserved
until its matching installer
finishes rollback or acknowledges its already committed result. This is deliberate:
expiry alone cannot prove which credential file or consumer state is selected.

If the installer is unavailable, the operator first restores that target service's
documented SQL login, private handoff key, owner-only journal directory and file
mounts. Restart the same target installer and replay the original request UUID;
do not stage a replacement under a new UUID or delete the pending receipt. Its
normal reconciliation handles crashes in `testing`, `installing`, `awaiting_ack`
and `cleanup_pending`, including expiry, without a privileged SQL state bypass.

If required journal/key material is missing, or the working file has an unknown
fingerprint, stop target replacement and recover the matching files and database
from the operator's consistent backup/escrow evidence. Preserve the unexpected
files for investigation. Never force `applied`, clear the uniqueness reservation,
disable triggers, or overwrite an unrecognized working credential to unblock the
UI. Operational command assembly and its restore runbook remain OPS-04/OPS-06.

## Runtime integration boundaries

The internal consumer hook attests the credential bytes loaded by a whole
single-instance Compose service, not one arbitrary child process. Its owning
runtime supervisor must wait for all children to load the same replacement.
Individual file bind mounts retain their original inode: replacing the host file
does not update that mount. OPS-04 must recreate the affected consumer container
and verify its fingerprint; a reload signal alone is insufficient. The installer
does not mount the Docker socket or get authority to restart other services.

Provider-specific candidate validation/testing, whole-service startup and
recreation, handoff-key provisioning, and current-Admin/CSRF/fresh-Google web
admission remain explicit integration work. The internal orchestrator requires
a validator but does not supply a production always-successful validator.
No new web credential endpoint or enabled production service exists here.
Keyring retirement additionally needs the separate online-migration and retained-
backup compatibility workflow; successful file replacement does not retire keys.

Phase 1C now supplies the target-specific process loop and metrics validation,
plus an operator-only whole-web acknowledgement command. For the supported
single-container web topology, stop/remove and recreate the complete service
using `docker compose up --detach --force-recreate web`, then execute
`pk-stewardship acknowledge-credential --config <web-config> --request-id <UUID>`
inside that service with `docker compose exec -T web`. Preserve the deployment's
explicit Compose file/project arguments. Do not use `compose run` for this
confirmation: its new process namespace has no live service cohort. All workers
must have completed admission with the replacement loaded. The command refuses
multi-container replica configurations; no partial service acknowledgement is
inferred. Later provider/consumer integrations remain pending as described above.

## Verification

The [file protocol tests](../../tests/stewardship/test_credential_files.py)
exercise bounded private files, sealed rollback, journal swaps, competing locks,
validation failures, expiry and post-rename recovery. The
[restricted-role integration tests](../../tests/stewardship/database/test_credential_isolation_postgresql.py)
exercise actual PostgreSQL role/RLS/grant boundaries together with private files,
consumer acknowledgements, cleanup and crashes before/after durable decisions.
They use only synthetic credentials and an explicitly disposable database.
Run them with the [database test profile](stewardship-database-tests.md).
The application image's real-kernel mount proof is separate from these database
checks; neither alone proves production service provisioning is complete.
