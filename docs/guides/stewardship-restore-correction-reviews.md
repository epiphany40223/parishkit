# Stewardship restore correction reviews

This ledger records the independent review/fix rounds of the
[restore correction](stewardship-restore-correction.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
three rounds, because it changes the backup, with a correction check after
any round that validates a finding. The Codex reviewer has been out of quota
since September 20, 2026; under the human's exemption, extended through
October 30, 2026, a completed Claude-only pass counts as a round, and each
round records which sources answered.

## Round 1

Claude only (Codex produced no output). Twenty-one raw findings, nine
validated (four High, five Medium), all corrected:

- High: the archive named media by tree, but the default media store is
  `run/persistent/media`, so extracting into the runtime root restored it
  where nothing mounts it. The restore now extracts to a staging directory
  and moves each tree to its configured path.
- High: the provisioning record, which `retarget-image` requires, was not in
  the set, so a replacement host could not retarget. It is now mounted
  read-only and archived, and the restore puts it back.
- High: `pg_restore --clean` drops only objects in the dump, so a rollback
  after a schema change would keep or fail on the later release's objects.
  The restore now empties the application schema and loads the dump in one
  transaction, which was verified to reproduce the schema's owner and every
  privilege. Testing the exact commands also showed that piping `pg_restore`
  into `psql` commits an emptied schema with status 0 when `pg_restore`
  fails; the runbook converts the dump to SQL first and forbids the pipe.
- High: running every drill step on a disposable host after activation would
  start a second live Production with every credential and Family's data.
  The drill now runs in full only on the validation deployment before
  activation; afterwards it stops once web is healthy, stays off the public
  DNS and destroys the host.
- Medium: the replacement host's directories outside the set, the startup
  lock and `collect-static` were missing, and `provision-runtime` was not
  forbidden; step 3 now lists them.
- Medium: the archive had no entries for the tree roots, so extraction
  created them with the extractor's umask; the roots are now archived
  `0700`, and the wording no longer claims modes are kept verbatim.
- Medium: the launch scope still required that nothing be resent
  automatically, contradicting the stated limitation; its step 3 now calls
  the review best effort, adds media and `caddy`, and refers to the
  limitation.
- Medium: credential and password file overrides outside the credentials
  tree are silently left out. The backup profile cannot see other roles'
  overrides, so the runbook's limitations now tell the operator to copy such
  files off the host; the authority store remains refused.
- Medium: media is written by online services during the backup. Each file
  is now read from one open descriptor, which a rename-based write cannot
  change, and a media entry that disappears is left out.

The twelve findings the validation step did not confirm were not carried
forward. A correction check follows.
