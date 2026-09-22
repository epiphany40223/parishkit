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

## Round 2

Claude only (Codex produced no output). Correction check: eleven raw
findings, four validated (all Medium), all corrected:

- Medium: the authority check ran whenever the backup profile's topology
  rendered, so it would have blocked provisioning and `retarget-image` for a
  supported authority override. It now runs when a backup runs, and a test
  shows the override still renders.
- Medium: the replacement host also needs the operator's deployment YAML and
  UUID from off the host, the same runtime root path, the same overrides and
  the same bind-source layout; step 3 now says so.
- Medium: the gate-approved drill was a same-host restore, which skips the
  replacement-host steps. A pre-activation run on a second disposable host,
  through web's health check, now rehearses them.
- Medium: the nightly backup cron could start mid-restore and record a mixed
  set as the newest; step 1 now disables the backup and off-host copy jobs
  until the fresh backup of step 9.

The seven findings the validation step did not confirm were not carried
forward. A further round follows.

## Round 3

Claude only (Codex produced no output). Nine raw findings, none validated.
Several of the low-severity notes were plain inaccuracies an operator would
meet in a real restore, so they were corrected anyway: the limitation cited
step 6 where web now starts at step 8; the replacement-host step moved DNS
without exempting a drill host; the directory list missed `run/persistent`
and `run/persistent/caddy` themselves and did not name the image to pull;
moving a tree aside onto an existing name would nest it; the design guide
and the boundary module's docstring did not mention the record and media;
and the size bound was checked only after a file had been read, which now
happens before. Because these changes follow the third round, a correction
check follows.

## Round 4

Claude only (Codex produced no output). Correction check: six raw findings,
none validated; the review rounds are closed. The low-severity notes (the
size-bound test does not isolate the before-read order, a docstring and a
test name that omit the provisioning record, an `O_NOFOLLOW` refusal that
surfaces as an operating-system error rather than the generic refusal, a
media directory vanishing during enumeration, and a few long lines) were not
carried forward.
