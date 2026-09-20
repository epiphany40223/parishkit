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

Implementation, focused acceptance and three independent dual-source review/fix
rounds are complete. Full exact-head CI/DCO precede protected delivery. RPT-07
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
locally. Role-downgrade acceptance and review corrections are recorded below.

Independent schema comparisons retained the prior PR #70 fresh database,
installed `stewardship_ministry_exports_20260919a`, and then independently
installed `stewardship_ministry_exports_20260919b` after correcting the model's
constraint-expression ordering. Changes are one capture table, its references,
indexes and guards, six functions, and five existing export authorization
guards. The final ordering correction changes only `export_report_known`.
Round 3 independently installed `stewardship_ministry_exports_20260919c`
and compared it to the retained `b` database. Only the capture and safe-audit-
context functions changed; all other installed objects were identical. The
fingerprint was updated only after this independent inspection. No retained
database was deleted and no historical migration path was introduced.

Draft CI includes both focused Ministry modules, substituting the previous
directory module while retaining the ten-module bound. Directory parsing is not
modified by this increment and remains covered by the complete candidate suite;
the fast selection prioritizes this PR's new/changed behavior instead of growing
on every PR. Exact-head full CI, three completed dual-source rounds and
protected merge are still required.

The final Staff-to-leader downgrade regression passed in 15.27 seconds. The
first [independent review/correction round](stewardship-ministry-export-reviews.md)
is complete, with its High packaging omission and both Medium findings fixed.
Focused post-correction checks pass. Later rounds now review that delta with
surrounding lifecycle context; full ready-candidate CI and delivery remain open.

Rounds 2 and 3 completed their independent reviews with no High/Critical
findings. Round 3 strengthened the audit representation and its tests; the
[review ledger](stewardship-ministry-export-reviews.md#round-3) records the
corrections against the [normative audit contract](../specs/stewardship/reports/spec.md#ministry-change-summary).

All accepted Medium-or-higher findings are fixed, with passing focused tests.
RPT-06.03 is implemented; exact-head full CI/DCO and protected PR #71 delivery
remain open. No integrated gate, deployment or release is authorized by this
package checkpoint alone.

## Protected delivery

PR #71 delivered candidate `49dd5427584a9bdca4c96d8592e7f06a4a6504f1`, whose
tree `1fd390f92ca052c4b09f259f0e86b4a3504cdc3f` matches retained correction
checkpoint `52404dd0` on `pr/stewardship-ministry-exports-ci-reviewed`. The
earlier three-round history remains on `pr/stewardship-ministry-exports-reviewed`.
Exact-head ready-candidate CI `35473195305` and DCO passed all 24 jobs.

The first attempt (22:23:29–22:39:58 UTC, September 19, 2026) passed 23 jobs.
PostgreSQL partition 2 exited 245 inside the unmodified
`test_reference_confirmation_and_family_submit_during_incomplete_catchup` load
case: the 120-second `faulthandler` watchdog fired mid-test and the interpreter
crashed while dumping its traceback, before any assertion ran. That case took
77.2 seconds on `main` run `35468347156` and 120.1 seconds in PR #70's passing
run, so its margin depends on the hosted runner. Attempt 2 reran only the
failed jobs on the same head (13:35:05–13:46:15 UTC, September 20); the case
passed in 77.5 seconds. No code changed between attempts. The narrow watchdog
margin is a CI reliability defect for a separate correction, not acceptance
evidence against this increment. Failed, cancelled and draft-skipped runs are
not counted as acceptance.

`origin/main` had no intervening commits since the candidate's base `5b0d3051`.
Protected auto-merge landed as `c08fd51d54d188ccb7a85cffb9800ace3ee9a761` at
13:46:17 UTC, verified on freshly fetched `origin/main` with the identical
candidate tree, before starting the Ministry follow-up increment. This used the
standing delivery authority, without deployment or release, and supersedes the
pending delivery checkpoint above. RPT-07, ADM-08.03 and M5/Gate 3 remain open.
