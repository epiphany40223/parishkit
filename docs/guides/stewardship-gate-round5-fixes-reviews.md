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
  cancels it and folds it into the next combined report of its kind; the
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
