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
  back, back up, retarget forward; if the previous image refuses, remove
  any deployment field it does not know from the YAML and put back the
  step 1 set's provisioning record, and use the database-restore rollback
  only if that still refuses; the
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

## Round 3

Claude and Codex both answered; Codex approved with no findings. Claude
raised three findings, two validated and corrected:

- Medium: the record-only recovery decrypted the whole files set without
  the restore procedure's handling; it now follows Restore for real steps
  2 and 4 to open and extract the set and step 10 to delete the decrypted
  copies.
- Medium: the Rollback section still said a deployment field the new
  release added forces a database restore, contradicting the corrected
  upgrade step; it now puts back the step 1 set's record while the
  deployment YAML names only fields the previous release knows, and keeps
  the restore for the other cases.

The Low below the cutoff was taken: this ledger's summary now describes
the corrected recovery. A correction check follows.

## Round 4

Claude and Codex both answered; Codex approved with no findings. Claude
raised two findings, one validated and corrected:

- Medium: a deployment YAML naming a field the previous release does not
  know is refused before the provisioning record is read, so the rollback's
  fallback to a database restore would refuse the same way after
  discarding writes. The rollback now removes such a field from the YAML
  first (it can hold only its default) and then puts back the record, and
  says the database restore needs the same.

The Low below the cutoff was taken: a long source line was rewrapped. A
correction check follows.

## Round 5

Claude and Codex both answered and raised the same Medium, validated and
corrected, with two Lows from Claude also taken:

- Medium (both sources): the upgrade recovery put back the provisioning
  record but not the deployment YAML, which the previous image reads
  first and refuses when it names a field that release does not know. The
  recovery now removes such a field before putting back the record.
- Low: the backup runbook's Restore for real step 7 now says the same,
  since the YAML is not in the set; and the rollback says the YAML change
  and the record swap apply when the previous image's `retarget-image`
  refuses, since its error names no cause.

A correction check follows.

## Round 6

Claude and Codex both answered; Codex approved with no findings and none
of Claude's three findings was validated, so the review rounds are closed.
The three Lows were taken as wording corrections: the recovery checks the
previous digest and the stopped services before treating a refusal as a
new deployment field, this ledger's summary includes the YAML step, and a
long source line was rewrapped.

## Protected delivery

PR #107 delivered candidate `ee53f53d`, two logical commits plus the
receipt of PR #106, whose content is the retained review history on
`pr/stewardship-upgrade-backup-recovery-reviewed` (`e1f5d2bd`), squashed
with identical content, plus exactly that receipt; Markdown lint passed on
that tree, and the candidate tree `63d3ee2c` is the landed tree. The six
rounds above were dual-source: Claude and Codex both answered each round
(round 1's Codex pass was run by hand with pika's own command). Rounds 1 to
5 validated findings, all corrected, and round 6 validated none. The pull
request was marked ready before the candidate was pushed. Exact-head
ready-candidate CI `35796135950` and DCO passed all 25 checks, from
23:10:39 to 23:30:06 UTC on September 22, 2026 (19 minutes 27 seconds).
Earlier runs on superseded draft heads are not counted as acceptance.
`origin/main` had no intervening commits since the candidate's base
`d23d20d5`. Protected auto-merge landed as `d4206390` at 23:30:09 UTC and
was verified on freshly fetched `origin/main`, whose second parent's tree
is the candidate's, before the next increment was committed. This used the
standing delivery authority, without deployment or release; no real
provider was contacted.
