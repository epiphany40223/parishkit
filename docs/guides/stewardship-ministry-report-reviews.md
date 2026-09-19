# Scoped Ministry reporting review ledger

This ledger supplements [scope and validation](stewardship-ministry-reports.md)
for PR #70. Three completed independent review/fix rounds and exact-head CI/DCO
are required before protected delivery. M5/Gate 3 remain open.

## Round 1: full increment

Pika session `20260919-160021-b2ebeb` reviewed base
`c64a9662a3b63d448012d597e3bca3a05186ff26` through
`3099b6ef333200c20696f56ca5604f42c2c51e6a`, tree
`39f30fc582d267b7ffd183bdf2af2c055c2e640d`. The exact Claude permission preflight
passed. Claude and Codex completed, Codex in 383 seconds, with no failed agent,
degradation, timeout or verdict mismatch.

Raw findings: four Medium and six Low, no High/Critical. Finalization validated
three Medium findings after cross-source agreement: one agreed, one Claude-only,
one Codex-only. Six Low findings were below the configured cutoff, not represented
as accepted Medium findings.

| Finding | Disposition and evidence |
| --- | --- |
| Agreed: generic default campaign selection can choose a disabled or unassigned campaign; no Ministry picker | Fixed with one Ministry-enabled/readable-source/current-assignment identity predicate for default navigation and guarded explicit selection. The page links to the picker. Disabled direct reports deny access instead of returning a permanent retryable outage. |
| Claude: no discoverable authorized retained-campaign selection | Duplicate of the agreed finding; handled by the same picker and two-campaign regression. |
| Codex: an unassigned leader summary can reveal campaign metadata | Fixed: a leader summary with no campaign-Ministry intersection is denied before rendering, just like detail. Actual web-role coverage uses disjoint campaign selections and checks that unauthorized campaign names/URLs are absent. |

The controlling shared report contract was also checked during triage. Ministry
DUID selection now travels only through CSRF-protected POST forms, never report
URLs; native summary-to-detail and pagination controls retain that selection.
No parish-wide report capability was added to leaders.

Post-fix validation: four existing Ministry PostgreSQL scenarios passed in the
26.74-second focused run; the new two-campaign scenario initially exposed an
invalid test fixture that attempted successor creation before archival. The
fixture now uses actual close, archive and return-to-Testing owners without
weakening production guards. Its targeted rerun passed in 15.18 seconds. Nine
cross-engine browser checks passed in 14.07 seconds; Ruff passed. All three
validated findings are resolved, and this completes round 1.

Draft CI run `35466019822` at the reviewed head passed `validate`, including
327 fast application/CI tests in 39.97 seconds. The skipped full suites and
blocking aggregates are expected draft behavior, not final acceptance evidence.
