# Ministry export review and correction ledger

Scope is the [complete-result Ministry increment](stewardship-ministry-exports.md).
The [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
controls review count, correction scope, focused testing and protected delivery.
No incomplete or degraded review counts as a round.

## Round 1

Session `20260919-171202-216c37` reviewed the complete branch from PR #70 merge
`5b0d30513dcc642319f04de9ea8f880310e4eabf` through
`66083413775c2e9dbf7e0e0b4fe578157e6e5665` (tree
`ce28dd58b430475a82dc65713b81588759ce7651`). The exact-command Claude permission
preflight passed. Both vendors completed: Codex exited zero in 459 seconds
without timeout/stall; Claude delivered its validated artifact. Finalization
reported no failed agents, degradation, verdict mismatch or salvage requirement.
Codex returned no findings; its zero-result telemetry is not a missing reviewer.

Raw findings: one High, two Medium and ten Low; three passed the Medium cutoff,
all Claude-only. All three were verified and fixed:

1. **High — Docker build context:** synchronize the specialized Dockerfile
   allowlist with the root allowlist so both new SQL files enter the image.
   Fast draft CI `35469637627` independently caught the same defect; its other
   318 fast cases passed. This is not full-candidate CI evidence.
2. **Medium — audit fan-out:** emit one export event referencing the immutable
   request and its exact retained scope, not one chained audit write per Ministry.
   A real two-Ministry summary asserts one request audit event. Current policy
   still controls every action; this does not broaden scope or change privacy.
3. **Medium — positive leave export coverage:** add a real authorized capture
   with current roster role, worker-role document loading and all three render
   formats. Assert the narrow nine-column leave projection, absence of contact
   columns and the actual Chairperson role. Existing interactive leave tests
   covered the shared query, but did not substitute for export integration.

Six Low suggestions were also addressed: remove redundant scope logic, restore
SQL query rationale, use named SQL arguments, test server/SQL work-gate refusal,
name the summary worksheet accurately, and retain both focused Ministry modules
in the fast suite by replacing the prior directory module (still ten modules).
Four nonblocking Low suggestions remain deferred: use an explicit regeneration
action sentinel, consolidate duplicate DUID parsing, richer native error recovery
under RPT-01/ARC-08, and expanded domain/chair-seed SQL-policy parity at integrated
Gate 3. Existing strict parsing, fixed private errors and coherent policy checks
remain enforced; no accepted Medium-or-higher issue is deferred.

Post-correction validation passed 15 focused build/render/scope cases in 0.83
seconds and five real-role PostgreSQL/report cases in 28.49 seconds, including
the new leave, audit-count and gate assertions. Their shared bootstrap was reused
within that PostgreSQL run. The correction delta and surrounding authorization
owners will receive round 2; no package or gate is closed by this first round.
