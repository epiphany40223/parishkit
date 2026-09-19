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
