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
