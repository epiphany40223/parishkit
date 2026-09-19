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

## Round 2

Session `20260919-173304-7ce5b8` independently reviewed the correction delta
`66083413775c2e9dbf7e0e0b4fe578157e6e5665` to
`804bc1a9444c13de60fd8b507407e12c0e2c4cce` (tree
`aaec2bebc6daf95a78202c7d082e60051d90f7ae`) with surrounding lifecycle context.
Exact permission preflight and both reviewers completed. Codex exited zero in
202 seconds without timeout/stall and returned no findings. Finalization had no
failed agent, degradation, verdict mismatch or salvage requirement.

Raw findings: no High/Critical, one Medium and seven Low; the Medium was
Claude-only. It requested normative clarification of the constant-cost scope
reference adopted in round 1. The specification now explicitly describes that
representation and requires Ministry-scoped audit queries to resolve the
retained request reference. The database assertion verifies the retained request's
exact operational `[4, 9]` scope. The change documents audit
representation, not permission expansion or weaker contact privacy.

Low corrections reconcile the stale fast-CI paragraph, explain its focused
selection trade-off, and assert literal join/leave worksheet labels. Deferred
Low suggestions are named SQL notation in the capture caller, document-label
refactoring, extra per-action audit count assertions, and full real purge-owner
state setup. The latter remains Phase 6/Gate 4 work; this phase's explicitly
synthetic disposable sentinel checks service/SQL admission without claiming a
completed purge workflow. No accepted Medium-or-higher issue is deferred.

Fast draft CI `35470734473` passed validation after round-one corrections,
including packaging and migration state. Full candidate CI remains deferred
until round 3 and its corrections pass. Round-two correction validation passed
the strengthened PostgreSQL capture/audit case in 15.06 seconds and three
literal worksheet/column cases in 0.25 seconds. Ruff lint/format and changed
Markdown checks passed. No production code changed in this correction round.

## Round 3

Session `20260919-174739-e7bf12` independently reviewed
`804bc1a9444c13de60fd8b507407e12c0e2c4cce` to
`2848f94af4b2186f85f29993ee3a129febd474e7` (tree
`99d7997d9dbd1cd6dc17827d2c80488fee31e55d`), explicitly including surrounding
export/audit lifecycle authority. Exact Claude permission preflight passed.
Codex exited zero in 165 seconds with no timeout/stall; Claude delivered its
validated artifact. Finalization reported no failed agents, degradation,
verdict mismatch or salvage requirement.

Raw findings: no High/Critical, three Medium and six Low. The three validated
Medium findings (two Claude, one Codex) were verified and corrected:

1. **Filtered result scope:** lifecycle scope intentionally includes the full
   authorized selection, but audit attribution must use the Ministries actually
   included after filtering. SQL now captures this distinct numeric set and
   the audit helper records it without changing authorization.
2. **Audit-side test strength:** selecting a known request's event did not prove
   reverse Ministry lookup. Tests now query campaign audit events independently,
   verify exact detail, unfiltered, filtered and empty-summary scope, and search
   retained context by Ministry without joining an export request.
3. **Retention:** reference-only scope would be lost after campaign-detail
   purge. The event now retains only numeric Ministry identifiers and the
   privacy boolean in a closed Python/SQL-validated context. Tests reject
   private values, malformed IDs, booleans-as-IDs, duplicates and disorder.

The [report contract](../specs/stewardship/reports/spec.md#ministry-change-summary)
and linked ADM-08/RPT-09 log owner control this representation. No private
report rows, names, contact values or search filters enter the audit context.
All six Low comments were also addressed: name the log-query owner, remove the
duplicate audit query, consolidate worksheet assertions, cross-link normative
prose, accurately describe round-two evidence, and reflow the fast-CI paragraph.

An independent fresh-schema comparison changed only two existing functions;
there are no table/column/constraint/grant changes or retained-database deletions.
Post-correction validation passed 34 fast output/scope/privacy cases in 0.79
seconds and 22 actual-role PostgreSQL, independent SQL privacy and schema cases
in 33.44 seconds, sharing one database bootstrap. These fixes complete round 3
under the automated cycle, not an automatic fourth round. No accepted Medium-
or-higher finding remains. Full exact-head CI/DCO and protected delivery remain
required; this record does not release M5 or Gate 3.

## Candidate CI correction

The first ready candidate `0bef267d748d1f81a2e3a4f21338bda3a5295d50`
preserved corrected tree `25a05858f21c238c68d41fe3e2f0f481539bf465` from
`5fd4b028b45f9ddcae38dac989a769e12e26b7f4`. Full run `35471954307`
passed validation, DCO, all browser engines and all container scenarios. Database
shards 10 and 6 exposed the shared helpers' assumption that every request has a
`report` field; `ExactExportRequest` does not. Other database shards were
cancelled by matrix failure handling. This is a real application regression,
not a flaky provider/infrastructure test or valid merge evidence.

The PR returned to draft. Both shared authorization and audit helpers now apply
Ministry-specific behavior only to the compiled `ExportRequest` model. Exact
requests retain their existing global report capability and ownership rules.
A new fast test first reproduced the audit failure, then all 55 fast cases
passed in 0.87 seconds, including 21 shared-model audit/authorization cases.
Ten focused real-role PostgreSQL cases passed in 27.53 seconds: exact-generation
handoff, cancellation, compaction protection, recovery ownership and retained
Ministry capture. No schema changed. The correction receives an independent
dual-source review before the new full exact-head candidate run; the three
completed rounds above are retained rather than restarted.
