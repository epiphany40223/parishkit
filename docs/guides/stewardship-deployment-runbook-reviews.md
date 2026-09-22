# Stewardship deployment runbook reviews

This ledger records the independent review/fix rounds of the
[deployment runbook](stewardship-deployment-runbook.md), under the
[v1 launch scope](../plans/stewardship/v1-launch.md#v1-process-changes):
two rounds for a documentation increment, with a correction check after any
round that validates a finding. The Codex reviewer has been out of quota
since September 20, 2026; under the human's exemption, extended through
October 30, 2026, a completed Claude-only pass counts as a round, and each
round records which sources answered.

## Round 1

Claude only (Codex produced no output). Thirteen raw findings, three
validated, all corrected:

- High: the upgrade's migration step presented `migration` and
  `database-grants` as working, but on a deployment that has completed
  first installation both are refused, because the upgrade admission a
  configured deployment requires (OPS-04.03) is deferred. The step now says
  so, says the backup increment supplies the admission and that a release
  changing the schema or a grant cannot yet be applied to a configured
  deployment; the validation section, the upgrade introduction, the known
  limitations and the runtime guide's upgrade paragraph say the same.
- Medium: the restore-based rollback omitted the image: the rendered
  topologies would still name the new digest, so the new image would start
  against the restored older schema and refuse. The rollback now retargets
  back to the previous digest with every service still stopped, and the
  application-only rollback is retarget, pull and start, never a repeated
  migration step.
- Medium: running `retarget-image` in the new image makes a release whose
  renderer changes a per-service YAML or the Caddyfile refuse as a changed
  generated document, which the refusal list did not name. The step now
  names that refusal, says why the new image is used and what the operator
  does until retargeting re-renders those documents, which the known
  limitations record.

The ten findings the validation step did not confirm were not carried
forward.

## Round 2

Claude only (Codex produced no output). Eleven raw findings, two validated,
both corrected:

- Medium: the runbook quoted a refusal message the operator never sees:
  only the migration command raises it, the grants command raises another,
  and the offline command wrapper prints one generic line with exit status
  2 in every case. The step now describes the refusal as the operator meets
  it and gives the cause in prose.
- Medium: the runtime guide's upgrade paragraph quoted the same message; it
  now describes the generic refusal and its cause, and this ledger's round 1
  entry no longer quotes it either.

The nine findings the validation step did not confirm were not carried
forward. Since the second round validated findings, a third, correction-only
check follows.
