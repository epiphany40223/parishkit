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

## Round 2

Claude only (Codex produced no output), two shards. Thirty-one raw findings,
eleven validated, all corrected:

- Critical: `pg_dump` runs with row security off, which PostgreSQL refuses
  for a login bound by a forced row-level policy, and seven tables force
  one, so the nightly dump could never complete on a real deployment. The
  backup login is now created `BYPASSRLS`, the only login that is; the
  role check expects it; the guide says so.
- High: the container provisioning test read only an unprotected table
  under the default row security. It now reads a policy-forced table with
  row security off under the backup login, `pg_dump`'s precondition.
- Medium: the dump's failure path was untested. Cases now drive a nonzero
  exit and an empty dump, asserting the refusal, the bounded printable
  diagnostics in the process log and nothing on standard output.
- Medium: the runbook promised the process log names the cause of a
  refused upgrade, but the operator wrapper logged nothing. A refused
  backup admission is now its own exception, and the wrapper logs one fixed
  sentence naming the missing backup; the runbook and guide say exactly
  that.
- Medium: retention counted failed sets, so a run of failures evicted good
  ones. Only complete sets, those with a manifest, count and go; failed
  directories stay for the operator, as the runbook now says.
- Medium: the read-all admission dropped the column-privilege sweep. It now
  refuses column-level writes beyond the registry; fake-cursor cases cover
  both sweeps.
- Medium: the retarget intent comparison used the fully defaulted
  deployment document, so a release adding a default (this one adds the
  backups path) would refuse. The recorded document is now re-derived
  through the running loader before comparison, so only operator inputs are
  compared; a case retargets a record without the backups path and still
  refuses a changed origin.
- Medium: the new backups path was outside the runtime layout's overlap
  checks; it is now a root, and the path suites cover it.
- Medium: the exact PGDG pin disappears when a newer build is published,
  breaking every image build until bumped. The release image guide and the
  Dockerfile record the coupling with the server digest and the expected
  failure.
- Medium: the backup command did not prove its database identity. It now
  requires the session to be the backup login with exactly its attributes
  and membership, and no temporary authority, before dumping or recording.
- Medium: nothing woke the collector for the first overdue night on an
  idle deployment. The observation need now includes an overdue backup, and
  the PostgreSQL case asserts the scheduler produces the collection.

The twenty findings the validation step did not confirm were not carried
forward.

## Round 3

Claude only (Codex produced no output), two shards; the first attempt of
both shards ended on a usage limit before reporting and is not counted, and
the relaunched shards reviewed the same snapshot. Thirteen raw findings,
five validated (two the same defect), all corrected:

- High (twice): the round 2 re-derivation wrote the recorded deployment
  document to a temporary file, but retarget runs with a read-only root and
  no writable temporary directory, so every documented retarget would have
  failed. The deployment loader now accepts an already parsed document and
  validates it exactly as a file, in memory; a case runs retarget with no
  usable temporary directory, and another covers the parsed form and its
  refusal of a second source.
- Medium: the backup identity admission was untested. Fake-cursor cases
  cover the clean row and each deviation: another login, a mismatched
  session user, superuser, no row-level bypass, inheritance, an extra or
  missing membership and temporary authority.
- Medium (twice): a deployment provisioned before this release has no
  backup login, password, directory or record table, which retarget and
  migration cannot create, while the runbook said every fix reaches the host
  through the upgrade. The deployment runbook, backup guide, backup runbook
  and release image guide now say such a release is taken by reinstalling
  before the schema freeze, name this release as one, and record the
  post-freeze gap as a known limitation.

The eight findings the validation step did not confirm were not carried
forward. Since the third round validated findings, a fourth, correction-only
check follows.

## Round 4

Claude only (Codex produced no output), two shards. Correction check: five
raw findings, none validated. This closes the review rounds: three full
rounds and one correction check, every accepted finding fixed.
