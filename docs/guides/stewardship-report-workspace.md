# Stewardship report workspace increment

## Scope and dependencies

This Phase 5 increment starts at PR #62's verified main merge `2c3151e6`,
on `pr/stewardship-report-workspace`. Follow the
[coordinating task list](../tasks/stewardship/overall.md#phase-5-reports-and-staff-workflows),
[report plan](../plans/stewardship/reports.md), and
[report specification](../specs/stewardship/reports/spec.md).

Deliver the Admin/Staff shared campaign-selected workspace, participation
chart and accessible table, current-population statistics, truthful freshness
metadata, and native asynchronous participation export controls. Reuse the
existing fact selection, calculation, presentation and export owners; do not
introduce a second calculation path or expose generic database query controls.
Campaign and fact-generation protection must remain held through response
completion. Passive status checks must not extend login sessions.

RPT-01, RPT-03 and BG-08 are the owning packages. Record partial completion
where their full acceptance spans later report consumers. This increment does
not complete the full report catalog, assigned-Ministry consumers, additional
information follow-up, census publication, retention or integrated Gate 3.
No placeholder report links, live-provider writes, deployment or release are
admitted. Existing development databases remain retained.

## Validation and delivery

Add focused actual-role database and browser coverage for current and retained
campaigns, role revocation, unavailable/updating data, exact chart/table/export
parity, session behavior and guarded downloads. Reuse compatible fixtures to
avoid repeated infrastructure startup. GitHub owns the full suite; local runs
target changed behavior and regressions. Fresh baseline changes require the
[independent schema audit](stewardship-schema.md), not upgrade tests.

Implementation is in progress. No new package or gate is complete yet.
Record implementation checkpoints, focused validation, all three dual-source
review/fix rounds, final-head CI/DCO and the protected delivery receipt before
advancing to the next fresh-main reporting increment.

## Initial implementation checkpoint

The Admin/Staff workspace now selects explicit retained campaign URLs, displays
shared current-population statistics and historical/current daily facts, and
retains fact-generation protection until the guarded response closes. Chart
subrequests name that exact generation, never substitute a newer pointer, and
do not renew login activity. Daily rows are paginated separately from the full
chart; all formats export the complete selected generation in ascending date
order. Separate inactive cards never change the graph's population control.

Native forms create, inspect, cancel, retry and download requester-owned exports
using the existing services and dedicated download pool. Busy responses retain
the artifact and provide a status-page retry. The initial native UI exports
the complete generation displayed on the page. Existing queued exact-input
APIs remain available; their native waiting/regeneration controls and the full
report catalog remain later BG-08/RPT-01 work, not completed by this slice.

XLSX joins CSV/PNG/PDF in the compiled worker and both immutable request types.
It has typed cells, literal formula-like labels, complete source/request
metadata, freeze panes, filters and print headings. Amounts exceeding Excel's
15-digit precision remain exact decimal text rather than silently rounding.

Independent fresh catalogs `before-a` at merged `2c3151e6` and `after-a` differ
only in `export_format_known` and `exact_export_format`, both adding `xlsx`.
The 3,167-constraint fingerprint is
`8b9bd30c61052f73f0a2f790590d1d933da3197711f903d8224010497f58dfcc`.
Every other object, owner, grant and policy is unchanged. Both audit databases
and all prior development databases remain retained.

Initial validation passed 57 focused presentation/spreadsheet/digest tests in
1.63 seconds and nine Chromium/Firefox/WebKit mobile/desktop/no-script checks
in 11.64 seconds. Actual-role report/export and unchanged schema checks passed;
the expanded regression batch exposed a test fixture replacing immutable policy
provenance, corrected to update only roles/grants. Its targeted rerun and all
three dual-source reviews remain required before acceptance.

## First correction checkpoint

The [round-one ledger](stewardship-report-workspace-reviews.md#round-1) records
four accepted Medium fixes and passing focused validation. Individual report
reads now guard only their own campaign; the separate campaign selector retains
multi-campaign protection for its labels. Browser timezone initialization,
transient export recovery and native retry/expiry/gating coverage are corrected.
PR #63 remains draft pending two further rounds and final-head CI/DCO.

## Second correction checkpoint

The [round-two ledger](stewardship-report-workspace-reviews.md#round-2) records
two accepted Medium fixes: retryable picker lifecycle admission and validation
before report-view audit. Three actual-role PostgreSQL workflows pass in
28.29 seconds. One further completed review round and final-head CI/DCO remain
before accepting this slice.

## Third correction checkpoint

The [round-three ledger](stewardship-report-workspace-reviews.md#round-3) records
three accepted Medium findings and their shared read-admission correction.
Unknown UUIDs and global closures remain distinct from retained-campaign
unavailability, consistently across default navigation, the campaign picker,
HTML and exact PNG. Existing restore maintenance routing remains unchanged.
The focused tests pass, and all three dual-source review/fix rounds are complete.
RPT-03.01/.03/.04/.05 are implemented and verified; full catalog/native queued
exact waiting and integrated package/gate acceptance remain open.

PR #64's human-prioritized CI feedback work was developed separately while this
branch was preserved. Rebase onto its verified protected merge before the final
reporting candidate, then record final-head CI/DCO and protected delivery.

## CI prerequisite delivered

PR #64 merged as `07f80e5b809b675798d8c7cb4f6a9c2c11e0ac7b`, verified on
freshly fetched `origin/main`. Candidate `c16436c` passed all 24 CI jobs plus
DCO in run `35441966120`; the run lasted 17m02s versus the measured prior
17m18s, within normal variation. Its major saving is fast draft feedback
(roughly 1–2 minutes), not a demonstrated large final-suite speedup. Full
coverage, all database/browser/container scenarios and protections remain.

This reporting branch rebased cleanly onto that merge. The pre-rebase reviewed
and corrected history is retained at `pr/stewardship-report-reviewed`
(`be4b5af`); the rebased tree differs only by the delivered CI increment before
this receipt and adding the 22 focused workspace unit tests to fast preflight.
Consolidate the review fixups into the implementation commit while retaining
the separate M4 handoff commit. Final exact-head CI is still required.
