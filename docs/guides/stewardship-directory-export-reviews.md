# Directory export review and correction ledger

Scope and acceptance: [complete-result directory exports](stewardship-directory-exports.md).
This uses the controlling [delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle),
including delegated routine triage, correction-focused rounds and exact-head CI.

## Round 1

Pika session `20260919-143734-c61e64` reviewed the complete PR from
`8dc00e9c` to `8b2633f`. Exact Claude permission preflight passed before launch.
Both Claude and Pika's Codex completed; Codex took 355 seconds. Finalization
reported no failed agents, degradation, salvage or verdict mismatch. Raw
severities: one Medium, twelve Low, no High/Critical. One Medium met the cutoff.

Triage: the Medium suggests guarding every code-export step with FAMILY_CODES
instead of CAMPAIGN_REPORT in case a future policy separates them. The current
compiled policy in `accounts/policy.py:allows` grants both to exactly Admin and
Staff; neither is configurable separately, Ministry-only/Family principals have
neither, and unknown roles are rejected. SQL export authorization has the same
Admin/Staff boundary. Current requester authorization is reloaded at worker,
status, publication, download and regeneration boundaries. The new actual-role
test removes Staff while retaining Ministry leader and proves denial. Therefore
this is future hardening, not a current authorization defect; reject the Medium
as false-positive for the specified policy. A future capability split must
change its consuming export authorization and tests together. No accepted
Medium-or-higher finding remains; Low items are below the configured cutoff.

Separate fast-CI correction: 309 tests passed and the packaging test found the
new SQL asset missing from both explicit Docker build allowlists. Add it to
both files, preserving the default-deny build context. This correction goes to
the next independent review rather than being treated as previously reviewed.
Fresh-install schema/model checks pass (two tests, 16.92 seconds); five focused
worker/scheduler assembly tests pass (0.35 seconds). The complete suite is
reserved for the ready candidate.

## Round 2

Pika session `20260919-144541-b6a6b9` reviewed `8b2633f` to `5c80468`,
covering the Docker correction and prior disposition with surrounding policy
and export context. Exact permission preflight passed. Both reviewers completed
without timeout, stall, failed agent or degradation; Codex took 155 seconds.
Finalization returned APPROVE with zero raw or validated findings. The focused
Docker allowlist regression passes in 0.05 seconds; draft CI run `35462198196`
passes validation. Draft-only aggregate failures reflect intentionally skipped
full suites and are not merge evidence. Two rounds are complete; the third
review and full candidate CI/DCO remain required.

## Round 3 and review exit

Pika session `20260919-144947-94ef00` reviewed `8b2633f` to `502c350`.
The scope intentionally retained round two's Docker correction alongside the
new evidence delta, with surrounding schema/runtime context, rather than
reviewing only the new ledger paragraph. Exact permission preflight passed;
both sources completed cleanly, Codex in 206 seconds. Finalization returned
APPROVE: four raw Low findings below cutoff, zero Medium/High/Critical, no
failed agents, degradation or verdict mismatch. No additional correction was
required. Rounds two and three checked round one's policy disposition; there
are no unresolved accepted Medium-or-higher findings.

Three review/fix rounds and applicable focused validation are complete. The
review exit criterion is satisfied, but the ready candidate still needs full
exact-head CI/DCO and protected delivery. Subsequent documentation merely
records these results; the candidate's source tree remains the reviewed code.

## Candidate CI inventory correction

Candidate `453401e` preserves reviewed acceptance tree `73c80882` from retained
head `a1525b6` on `pr/stewardship-directory-exports-reviewed`. Full run
`35462698243` passed validation, all browser/container jobs and ten database
partitions before partition 9 reported one failure: the generic immutability
inventory lacked the directory snapshot's custom trigger/function mapping.
Partition 9 otherwise passed 334 tests. Returning the PR to draft cancelled
the one remaining partition; this run is not full-suite acceptance.

Register `directory_export_capture` / `stewardship_directory_export_capture_v1`
in the existing custom-name and conditional-insert inventories. This does not
change application/schema code or skip immutability validation: the test still
requires an enabled row-level BEFORE INSERT/UPDATE/DELETE trigger, SQLSTATE
23514 and the explicit non-insert rejection. The feature's actual update/delete
denial tests already passed. The corrected inventory test passes locally in
12.55 seconds. A focused independent review of this correction precedes the
next ready candidate; the completed first three rounds remain valid.

Future model additions should include this short guard-inventory check alongside
the fresh-schema/model comparison in the same local bootstrap, so bookkeeping
omissions are caught before a full candidate CI run.
