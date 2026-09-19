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

## Round 2: campaign-scope and native-navigation corrections

Pika session `20260919-161426-0f7a31` reviewed
`3099b6ef333200c20696f56ca5604f42c2c51e6a` through
`573775235ae139e04eff7ea65f84fd05ecafa461`, tree
`490daac698a7b824db3f33fb85779077cc8cbb9c`. The fresh exact permission preflight
passed. Both reviewers completed, Codex in 231 seconds; no agent failure,
degradation, timeout or verdict mismatch occurred.

Raw findings: two Medium and six Low, no High/Critical. The two Medium findings
were validated and accepted; six Low findings were below the configured cutoff.

| Finding | Disposition and evidence |
| --- | --- |
| Claude: URL-privacy assertion inspects an earlier 400 body rather than a rendered report | Fixed: successful summary and detail responses each assert that old DUID-bearing URL paths are absent and the native hidden Ministry selection is present. |
| Codex: 32-bit query casts can reject otherwise valid 64-bit draft configuration IDs | Fixed in discovery and row selection: cast configuration values to bigint before filtering to the source/MinistryRequest positive signed-32-bit domain. SQL predicate reordering cannot cause overflow. A real successor configuration includes both boundary-overflow and maximum signed-64-bit values alongside a valid selected Ministry; leader discovery and Admin report rendering remain functional. No source, request or audit domain was widened. |

Both corrected PostgreSQL scenarios passed together in 18.15 seconds; Ruff,
formatting and whitespace checks passed. This completes round 2 with no
unresolved accepted Medium-or-higher finding. Draft CI `35466737240` at its
reviewed head passed fast validation; full candidate acceptance remains pending.

## Round 3: identifier-boundary and successful-page regressions

Pika session `20260919-162134-171938` reviewed
`573775235ae139e04eff7ea65f84fd05ecafa461` through
`b0dfe456c4546d21f7d2d8d730ef8f732977a13e`, tree
`56130bcde48028aa295106d6e27368707252acf7`. Its fresh exact permission preflight
passed. Claude completed; Codex completed cleanly in 157 seconds with zero
findings. Pika's zero-result Codex telemetry says `parsed: false`, but the
actual run exited zero without a timeout/stall and finalization reported no
degradation, failed agent or verdict mismatch.

Raw findings: one Medium and one Low, no High/Critical. The Medium was validated
and accepted; the Low was below the configured cutoff. The accepted finding
identified an unintended consequence of moving operational authority inside the
discovery subquery: Admin/Staff could not discover an empty Ministry draft even
though direct report access remained valid. Fixed by preserving their outer
operational authorization while keeping bigint-safe filtering for leader scope.
The actual-role regression now covers both empty selection and unsupported-only
draft IDs: Admin navigation/picker/detail remain available and leader access
still requires an in-range assigned intersection. The targeted test passed in
17.03 seconds.

This completes three independent review/fix rounds. No accepted Medium-or-higher
finding remains; the final round found no High/Critical. Corrections and their
passing regression are part of round 3 under the controlling delivery cycle,
not an assertion that the raw review was finding-free. Draft CI `35467116278`
passed fast validation. Full exact-head CI/DCO and protected merge remain the
delivery boundary.
