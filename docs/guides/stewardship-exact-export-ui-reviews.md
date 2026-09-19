# Native exact-export review ledger

Scope and acceptance: [increment guide](stewardship-exact-export-ui.md).
Follow the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle).
All rounds use exact Claude permission preflights and Pika's independent
Claude/Codex roster. Failed/degraded rounds never count as approvals.

## Round 1

Session `20260919-092409-aee3db`, full base `351cf375` to `9aa5a1f`.
Both reviewers completed; finalization approved with no failed agents,
degradation or verdict mismatch. Raw: eight Low, zero Medium/High/Critical;
validated Medium+: zero. All eighteen changed files were covered.

Low dispositions:

- Clarify regeneration's fact/configuration terminology: fixed. The retained
  fact set keeps its calculation configuration; presentation uses current policy.
- Strengthen new-request timestamp assertion: fixed against the original export
  rather than the earlier exact-calculation request.
- Add fast regeneration errors/method coverage: fixed using established native
  unit-test patterns for 405, outage, malformed keys and conflict recovery.
- Missing retained facts might be permanent: no policy change. Retained requests
  pin their facts; successful publication and ready fact state are immutable.
  Purge/restore admission is checked independently. A hypothetical violated
  retention invariant is not ordinary user conflict.
- Sibling status admission differs: retained existing export behavior; actual
  restore is intercepted by common middleware. The new exact page uses the
  shared report read helper and never converts refusal into authorization.
- Raw state labels: permitted Low presentation follow-up under full-catalog/
  Gate 5 accessibility/localization review; existing export status has the same
  convention. No authorization or outcome depends on translated labels.
- Private view helper imports: intentionally reuse existing fixed recovery and
  body/principal logic. A new generic abstraction is not required for two callers.
- Row estimate configuration: labeled estimate, frozen inputs selected only at
  POST. Campaign start is immutable after activation; historical/current scope
  does not substitute another campaign. No calculation or export uses the estimate.

Post-fix validation: 34 focused unit tests pass in 0.53 seconds; the corrected
real PostgreSQL workflow passes in 19.69 seconds. Ruff/format/Markdown pass.
Draft CI `35445669922` passed its fast preflight in 96 seconds; skipped full
suites leave aggregate gates blocking by design and are not merge evidence.

## Round 2

Session `20260919-093118-be8b53`, correction base `9aa5a1f` to `705aaee`.
Both reviewers completed; finalization approved with no failed agents,
degradation or mismatch. Raw: four Low, zero Medium/High/Critical; validated
Medium+: zero. Two Claude Low suggestions add the guide-to-ledger link and
explicit no-store/safe-error/non-retryable assertions to the fast 400/409
regeneration cases. Both are fixed. Two Codex Low findings fell below Pika's
configured cutoff; finalization retained no actionable findings from them.

The second draft preflight (`35446025487`) passed in 85 seconds. Only focused
unit and lint validation is repeated for these documentation/assertion-only
corrections; the unchanged PostgreSQL and browser evidence remains applicable.

## Round 3

Session `20260919-093603-53750b`, correction base `705aaee` to `ac372e8`.
Both reviewers completed; finalization approved without failed agents,
degradation or mismatch. Raw: one Low, zero Medium/High/Critical; validated
Medium+: zero. The remaining Low was stale guide wording/counts, corrected to
distinguish the initial PostgreSQL/browser baseline from the final unit evidence.
The 34 focused unit cases pass in 0.57 seconds; Ruff/format/Markdown pass.

Three completed rounds satisfy this increment's review loop. Final-head full
CI/DCO and protected merge remain required; draft aggregate failures cannot be
treated as passing validation. Squashing will preserve the candidate tree and
the reviewed endpoints above; no schema, permissions or runtime behavior changed
after the full initial review.
