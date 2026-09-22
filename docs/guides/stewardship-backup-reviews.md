# Stewardship v1 backup reviews

This ledger records the independent review/fix rounds of the
[v1 backup increment](stewardship-backup.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, since the increment touches backup, credentials and the
database schema. The Codex reviewer has been out of quota since
September 20, 2026; under the human's exemption, extended through
October 30, 2026, a completed Claude-only pass counts as a round, and each
round records which sources answered.

## Round 1

Claude only (Codex produced no output). Twenty-five raw findings, six
validated, all corrected:

- High: granting `pg_read_all_data` gave the backup login a role
  membership, which the shared role check treated as a foreign role, so
  `database-grants` after `database-roles` (and any roles retry) would have
  refused the backup identity on a real database. The check is now
  role-aware: every other login has no membership, the backup login exactly
  `pg_read_all_data` with inheritance and without admin option; the
  fake-cursor cases cover a stray membership, the reader's and an admin
  membership, and the container provisioning test asserts the backup login
  reads an application table after roles and grants.
- High: retargeting read every rendered document, so a release that adds a
  generated document, this one included, refused instead of creating it. A
  missing document is now stale and written; the retarget case removes the
  backup profile's document and sees it recreated.
- Medium: the guide claimed a completion-time index that the schema does
  not define; both sentences now say the primary key is the only index.
- Medium: `pg_dump`'s diagnostics were captured and discarded, and an
  undrained pipe could stall a chatty dump. A thread now drains standard
  error into a bounded buffer while the dump is sealed, and a failed dump
  logs its exit status and printable diagnostics to the process log, never
  to standard output.
- Medium: the read-all admission was never exercised. Fake-cursor cases
  now cover the missing membership, a write beyond the registry, a write
  outside `public` and the clean case, and the container provisioning test
  asserts the membership and the registry's one insert.
- Medium: the sealed box is anonymous encryption, so anyone holding the
  public key can produce a file that opens, and the header was not bound to
  the chunks. The header's digest is now the first authenticated chunk, so a
  rewritten header is refused; the backup prints its manifest digest for the
  operator to keep off the host, the restore drill compares it before
  decrypting, and the guide and runbook record the absence of a host-held
  signing key as a v1 limitation.

The nineteen findings the validation step did not confirm were not carried
forward.
