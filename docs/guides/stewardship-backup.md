# Stewardship v1 backup

This guide records the reduced backup of the
[v1 launch scope](../plans/stewardship/v1-launch.md#reduced-for-v1) (reduced
item 6, launch-critical item 4): a sealed nightly dump of the database and a
sealed copy of the runtime's configuration and credentials, a human-held
decryption key, an alert through the existing operational incidents when a
backup is overdue, and the upgrade admission a configured deployment needs
before its schema or grants change. It delivers the v1 portion of
[OPS-05](../tasks/stewardship/operations.md#ops-05-backup-service-and-purge-triggered-backup)
under the [operations specification](../specs/stewardship/operations/spec.md#backup),
whose consistent manifests, isolated backup-worker routing, purge-triggered
backup, revalidation, escrow workflow and retention beyond the host stay
deferred, and follows the
[pre-production development policy](../specs/stewardship/operations/spec.md#pre-production-development-policy).
The [backup runbook](stewardship-backup-runbook.md) is the operator's
procedure; this guide is the design.

## Scope

A new one-shot Compose profile, `backup-worker`, runs
`pk-stewardship backup --config BACKUP_CONFIG` beside the online services. It
dumps the database with `pg_dump` under its own SQL identity, archives the
configuration and credentials trees, seals both to the operator's public key,
writes a plaintext manifest of sizes and digests, records one row in
`stewardship_backup_run`, and keeps the newest thirty sets on the host. Two
console commands run wherever the operator keeps the private key:
`backup-keygen` makes the key pair and `backup-open` decrypts one sealed file.
The scheduler's operational collection opens the existing
`backup_rpo_breach` incident when no backup completed in the last 24 hours
and resolves it when one has. The migration profile and `database-grants`
admit a configured deployment when a backup completed within that window.
`retarget-image` re-renders every generated document, so a release whose
renderer changed reaches a deployment through the same command.

Off-host copying is the operator's cron job over the sealed files, as the
runbook says; the application transfers nothing.

## Design

### Sealed to a key the host never holds

Each output is a fresh 32-byte key's XSalsa20-Poly1305 stream in 8 MiB
chunks whose nonces are their positions, so a chunk cannot be dropped, moved
or repeated undetected and a truncated or appended file is refused; the data
key travels in a sealed box only the X25519 private key opens. The header
names the recipient key's fingerprint, so a wrong key is a clear refusal
before any chunk is read. The public key is installed as the `backup_data`
credential file by hand, since it is not secret and no installer flow exists
for it; the private key is written owner-only by `backup-keygen` to a place
the operator chooses off the host and is never read by the application.

### The whole trees, read-only, by one identity

The backup profile is neither an online role nor an offline profile. It
mounts the configuration and credentials trees read-only, its output
directory read-write, and nothing else beyond its own inputs, admitted from
kernel mount evidence like every other profile; it holds the startup
interlock shared, so it cannot overlap offline work and offline work cannot
start under it. Its SQL identity, `pk_stewardship_backup_worker`, reads every
table through `pg_read_all_data` (granted with inheritance, since the login is
`NOINHERIT`) and may insert and read back its own record and nothing else,
which provisioning verifies instead of the ordinary grant comparison. The v1
launch scope accepts this reduced escrow, a read-only view sealed to a
human-held key, in place of the deferred operator escrow workflow.

### The record is the evidence

Only a completed run inserts a row, after every output is durable, so a row
never names a set that does not exist and a failed run leaves absence plus a
failed set directory without a manifest. The row holds sizes, digests, the
recipient fingerprint and the application version, never a path or a
credential, and is append-only. Two readers use it: the scheduler's
collection, which opens the overdue incident when the newest row is older
than 24 hours, and, before any row exists, only in Production, so a Testing
install is not paged before its nightly backup is set up; and the offline
upgrade commands, which admit a configured deployment only when a row
completed within the same window. The day-long window is the operations
specification's; the reduced form of the deferred upgrade admission is that
this evidence stands in for verified restore evidence, which the runbook's
tested restore supplies by hand.

### Retargeting re-renders

The retarget command now re-derives every generated document from the
recorded inputs by the code that is running and rewrites those that differ,
so a release that changes a per-service document or the ingress document is
applied the same way as one that changes only the image; passwords and the
broker ACL are generated once and kept, and only the deployment inputs may
not differ. The runbook's earlier limitation is closed.

## Schema

One added table, `stewardship_backup_run`, with its append-only guard and a
completion-time index; the fresh-install baseline and fingerprint are updated
under the pre-production policy. No other object changes.

## Focused validation

- Database-free: every chunk boundary of the sealed format roundtrips and a
  wrong key, truncation, appended data, a flipped byte, a swapped chunk and a
  broken header are refused; the backup profile's mount inventory is exactly
  the two trees, the output and its inputs, and a missing tree, a writable
  tree, an extra mount, a writable root, another role, another secret or an
  output inside a tree is refused; a set is written sealed, both outputs open
  with the private key, the archive holds the configuration and credentials
  files and no backups, the manifest names sizes and digests and the record
  matches; retention keeps the newest thirty dated sets and leaves other
  directories alone; a symlink in a tree, a missing key and a failed dump
  record nothing; the dump step needs `pg_dump`, passes the password only
  through the environment and runs as the backup identity; the console makes
  a key, opens a set with it and refuses generically; provisioning's operator
  admission and the migration command accept a configured database only with
  a recent recorded backup; the rendered topology carries the backup profile
  and every other profile keeps its mounts; retargeting rewrites differing
  generated documents and still refuses changed inputs and missing passwords.
- PostgreSQL: the backup identity inserts one row and cannot update, delete or
  insert an invalid one; the overdue incident opens for a Production install
  without a backup and for any install whose newest backup is stale, resolves
  when a fresh one is recorded, and does not open for a Testing install with
  none; the admission accepts a run inside the window and not one outside;
  the worker and scheduler read the record and cannot write it; the
  operational collection and due-work suites still pass.
- The schema audit shows one added table, one function, one trigger and two
  indexes; ruff, formatting, Markdown lint and migration drift pass.
- The real `pg_dump` runs only in the application image; the runbook's
  restore drill is the human-run end-to-end check.

## Checkpoint

Implementation and focused validation are complete; review/fix rounds, full
exact-head CI, DCO and protected delivery remain open. No deployment,
release, live-provider write or database deletion is authorized by this
increment.
