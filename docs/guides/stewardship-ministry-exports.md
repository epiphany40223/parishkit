# Complete-result Ministry exports

This Phase 5 increment starts on `pr/stewardship-ministry-exports` from verified
PR #70 merge `5b0d3051`. It completes [RPT-06.03](../tasks/stewardship/reports.md#rpt-06-ministry-summary-and-detail)
under the [implementation plan](../plans/stewardship/reports.md#rpt-06-ministry-summary-and-detail)
and [report contract](../specs/stewardship/reports/spec.md#ministry-change-summary).

## Scope and acceptance

Capture every filtered summary/join/leave row through the same query and
privacy projection as interactive reporting. Reuse the requester-owned export
task, fenced publication, seven-day artifact retention, safe cancellation,
bounded guarded download and immutable-input regeneration. Native CSRF forms
retain private filters without putting Ministry DUIDs or search in URLs.

Admin/Staff retain operational columns; assigned leaders receive only their
Ministries and publishable contacts from the recorded source snapshot. Current
role and assignment checks remain mandatory throughout the export lifecycle;
a captured operational report must not survive a Staff-to-leader downgrade.
Old job identities never grant access to another requester's work. Do not widen
the general campaign-report capability to implement Ministry-only exports.

Validate all three formats, complete results beyond one page, source stability,
scope revocation and SQL enforcement, plus native browser controls. Related
PostgreSQL cases share one bootstrap; browser cases reuse the existing server
and engine pool. Update the fresh-install baseline and independently compare
installed schema objects; no historical upgrade path or database deletion.

Implementation and validation are in progress. Three independent dual-source
review/fix rounds and full exact-head CI/DCO precede protected delivery. RPT-07
packets, ADM-08 follow-up editing and integrated M5/Gate 3 retain their owners.

## Implementation checkpoint

Interactive pages and complete captures now share `stewardship_ministry_report_v1`.
SQL derives immutable capture scope and independently enforces it at request,
attempt, publication, cancellation and download boundaries. The application
reloads coherent policy for those paths, status, retry and regeneration without
granting leaders general campaign-report access. Source contacts/publication
flags remain explicitly source-as-of; current account/assignment authority is
not frozen with them.

The first focused checks passed 14 pure output/scope cases in 0.72 seconds and
12 mobile/desktop/native-form checks across three browser engines in 18.63
seconds. Actual-role checks cover all three rendered formats, guarded download,
retained-input regeneration, 52-row selection, immutable capture, assignment
revocation and SQL enforcement. Correction runs share the existing PostgreSQL
bootstrap and rerun only affected cases; full candidate CI is not duplicated
locally. Additional role-downgrade acceptance and peer reviews remain in progress.

Independent schema comparisons retained the prior PR #70 fresh database,
installed `stewardship_ministry_exports_20260919a`, and then independently
installed `stewardship_ministry_exports_20260919b` after correcting the model's
constraint-expression ordering. Changes are one capture table, its references,
indexes and guards, six functions, and five existing export authorization
guards. The final ordering correction changes only `export_report_known`.
No retained database was deleted and no historical migration path was introduced.

Draft CI includes both focused Ministry modules, substituting the previous
directory module while retaining the ten-module bound. Directory parsing is not
modified by this increment and remains covered by the complete candidate suite;
the fast selection prioritizes this PR's new/changed behavior instead of growing
on every PR. Exact-head full CI, three completed
dual-source rounds and protected merge are still required.

The final Staff-to-leader downgrade regression passed in 15.27 seconds. The
first [independent review/correction round](stewardship-ministry-export-reviews.md)
is complete, with its High packaging omission and both Medium findings fixed.
Focused post-correction checks pass. Later rounds now review that delta with
surrounding lifecycle context; full ready-candidate CI and delivery remain open.

Round 2 completed with no High/Critical findings. Its Medium audit-representation
clarification is recorded in the normative report specification: a single event
references the exact immutable scope, and Ministry-scoped audit queries must
resolve that reference. This is not a reduction in data authorization or privacy.
