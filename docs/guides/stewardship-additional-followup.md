# Additional-information staff workflow

This Phase 5 increment begins at verified main `b60e39f4` after PR #65's
[protected delivery](stewardship-exact-export-ui.md#protected-delivery).
It implements the current/history queue, detail, durable notes and follow-up
editing for [RPT-04](../plans/stewardship/reports.md#rpt-04-additional-information-workflow-report),
the additional-information portions of [ADM-08](../plans/stewardship/admin-portal.md#adm-08-manual-refresh-follow-up-queues-and-logs)
and [DAT-07](../plans/stewardship/data.md#dat-07-follow-up-content-templates-jobs-and-audit).
Their linked specifications control behavior. Async complete-text/history
exports remain RPT-04.04's following owner, not a synchronous shortcut here.

## Scope and acceptance

Admin/Staff can search, filter and paginate live additional-information items,
inspect correction/withdrawal and Staff history, and edit follow-up state/notes
with optimistic concurrency. Ministry leaders cannot access this queue.
Identifying search stays in CSRF POST bodies, not URLs or logs. Current-source
names and item disposition must remain coherent; missing/inactive Families do
not silently lose submitted requests. Weekly digest actionability stays tied to
Family replacement/withdrawal, not a Staff checkbox.

Edits append history and advance the item version in one authorized transaction.
Replays cannot create a second revision; stale Staff forms cannot overwrite a
new edit or Family replacement. Unchecking completion requires confirmation and
retains previous actor/time in history. Notes-only edits preserve completion
time. Purge work gates close mutations; admitted reads retain their guards
through response completion. History never rewrites submitted Family text.

Use the fresh-install baseline only, with a precise independent schema delta
audit and no retained-database deletion or upgrade compatibility work. Group
unchanged denial matrices around prepared fixtures; preserve real role and
concurrency checks. Use focused local tests and draft fast CI, three completed
dual-model review/fix rounds, then full exact-head protected CI/merge. No
production provider calls, deployment or release are authorized by this slice.

## Checkpoint

Implementation, focused validation and [three review/fix rounds](stewardship-additional-followup-reviews.md#round-3)
are complete; protected candidate delivery remains open in PR #66. No whole
package or integrated gate is
claimed complete. This increment does not implement the remaining export task.

## Fresh-install schema audit

Compared fresh databases built independently from `b60e39f4` and the candidate,
on the disposable PostgreSQL 18.6 test cluster. The predecessor inventory
exactly matched its committed strict fingerprint. No preexisting object
disappeared. Additions are one revision table, 13 columns, 18 constraints,
five indexes, four functions and three triggers; row policies are unchanged.
Review corrections change only the existing weekly history and snapshot-guard
functions and the post-close-current view: narrow history projection and shared
digest-semantic tokens exclude Staff-only edits. Only after independently
inspecting this exact revised delta was the fixture updated.
The candidate has 205 relations, 2,300 columns, 3,185 constraints, 943 indexes,
547 functions, 525 triggers and 28 policies. Initial strict fingerprint and full
Django model/schema parity checks passed (two cases, 21 seconds); the
[review ledger](stewardship-additional-followup-reviews.md) records corrections
and their additional validation.

No historical upgrade migration, retained database deletion, or compatibility
promise was added. The initial SQL and model state remain a fresh-install pair.

## Focused validation

- Four shared-fixture PostgreSQL scenarios pass in 22 seconds: actual-role
  editing and audit/history pairing, native CSRF forms and private search,
  replay/stale/confirmation handling, independent competing Staff writers,
  Family replacement/withdrawal, Staff-to-leader revocation, gated mutation
  with retained reads, and current-source absence/weekly parity.
- Nine Chromium/Firefox/WebKit checks pass in 19 seconds: 320/1,280-pixel
  layouts, keyboard traversal, native POST filters, escaped submitted text,
  history/correction links, disabled gated controls, WCAG scanner and no-script
  operation. PostgreSQL tests, not component fixtures, prove authorization.
- Pure parser/recovery tests join the bounded draft-CI selection. Full
  candidate CI remains required after three independent dual-source rounds.

The review ledger records final correction validation, including four real
correction/post-close scenarios in 62 seconds and nine browser checks in 15
seconds. Fast draft CI passes after correcting the explicit Docker asset list.

The first concurrent test attempt exposed a test-fixture role-creation race,
not an application deadlock. Creating the restricted role once and sharing its
connection hook lets the two independent application transactions race safely.
History reads are capped at the displayed version so a newly committed edit
cannot appear beside an older item projection.

## Protected delivery

PR #66 merged as `6a6366802a7eb1f5a70621022e01e2b8878a8c41`, verified on
freshly fetched `origin/main`. Candidate `a2211e1387b38eda8c1a89d6ea2618892ffef649`
passed all 24 full-CI jobs in run `35451868456`, plus DCO. The corrected run
took 16 minutes 19 seconds; its test-only correction is recorded in the
[review ledger](stewardship-additional-followup-reviews.md#candidate-ci-correction).
Its tree `f6df82f6a078b10a98b123529d160c02c0a32424` matches preserved history
`513db34d99713cddcbc0ea215bc4c3250a2c88b1`. This supersedes pending-delivery
notes, without claiming the remaining export task or integrated Gate 3.

The next fresh-main increment implements [complete-text/history exports](stewardship-information-exports.md).
