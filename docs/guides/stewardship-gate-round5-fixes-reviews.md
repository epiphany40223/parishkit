# Stewardship gate round 5 corrections reviews

This ledger records the independent review/fix rounds of the corrections
the pre-launch gate's fifth round (a correction check of PL-I2 and PL-I5)
found:

- PL-I5 (Medium): once an upgrade's `retarget-image` has run, the backup
  profile runs the new image, which refuses the not-yet-migrated schema, so
  a step 1 backup that aged past 24 hours before the migration left the
  operator able neither to back up nor to migrate. The
  [deployment runbook's upgrade](stewardship-deployment-runbook.md#upgrade)
  now says to run steps 1 to 4 in one sitting and how to recover: retarget
  back, back up, retarget forward, or, when the release added a deployment
  field, the database-restore rollback from the step 1 backup; the
  [backup runbook](stewardship-backup-runbook.md#checking) checklist points
  there.
- PL-I2 (Low): the launch runbooks said a report resent on a paused active
  campaign goes when delivery resumes, but the resume's recovery plan
  cancels a scheduled daily or weekly report and folds it into the next
  combined report of its kind; the
  [launch runbooks](stewardship-launch-runbooks.md#messages-in-delivery_unknown)
  and the [paused resend guide](stewardship-paused-resend.md)'s validation
  note now say so.

It follows the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds, with a correction check after any round that validates a
finding. The Codex reviewer has been out of quota since September 20, 2026;
under the human's exemption, extended through October 30, 2026, a completed
Claude-only pass counts as a round, and each round records which sources
answered.

## Round 1

Claude and Codex both answered (Codex had credits again; its pass was run
by hand with pika's own command after the automatic launch found none).
Ten raw findings, seven from Claude and three from Codex; the text they
quoted was corrected before the round was finalized, so each was checked
by hand against the code. Accepted and corrected:

- Medium (both sources): the recovery said restoring the step 1 backup
  loses nothing, but step 1 runs while the online services still write;
  the text now says the restore loses what they wrote before step 2 and
  needs the full restore procedure, including the mail log comparison.
- Medium (Claude; Low from Codex): the resume folds only scheduled daily
  and weekly reports; a resent manually requested weekly report is held
  and sent like other mail. The launch runbooks, the paused resend note
  and this ledger now say so.
- Medium (Codex): the first draft said a report resend gains nothing over
  waiting for the resume, but resume refuses while any delivery is
  unknown; that sentence is gone.

The Claude findings below the validation cutoff were taken: a release that
changes no schema, or whose migration already succeeded, backs up in the
new image directly; the forward retarget runs in the new image; the
previous image's retarget refusal is not tied to a cause the operator
cannot see; and the corrected texts link this ledger. A correction check
follows.

## Round 2

Claude and Codex both answered; Codex approved with no findings. Claude
raised two findings, one validated and corrected:

- Medium: when the previous image's `retarget-image` refuses, the
  migration was refused before applying anything, so the database is
  untouched and a full database restore would discard the online services'
  writes for nothing. The recovery now restores only the provisioning
  record from the step 1 set and retargets back again, keeping the
  database-restore rollback as the fallback if that still refuses.

The Low below the cutoff was taken: the paragraph after the recovery is
rewrapped. A correction check follows.
