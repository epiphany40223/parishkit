# Native queued exports and regeneration

This Phase 5 slice starts on fresh main `351cf375` after PR #63's verified
protected delivery. Follow [RPT-01](../plans/stewardship/reports.md#rpt-01-shared-report-framework-and-campaign-selection),
[BG-08](../plans/stewardship/background-processing.md#bg-08-export-and-graph-workers)
and their linked normative specifications. It completes participation's native
queued-exact controls, not the entire export/report catalog or Gate 3.

## Scope

Keep displayed-generation exports distinct from requests that freeze current
inputs at submission and wait for their exact calculation. Expose native
requester-owned status, cancellation/retry, immutable input metadata and links
to the existing renderer/download. Reuse existing exact/export workers and
response-lifetime guards. Passive status must not renew login activity.

Offer explicit regeneration of expired files from their retained fact set,
format and timezone. Regeneration creates a new authorized request with a new
request time; retry preserves its existing request. Never modify expired
publication history or silently replace old facts with current inputs.

Preserve CSRF, fresh role/object admission, no-store, work/restore/purge gates,
bounded downloads, fixed private errors and three-engine accessible forms.
No new schema, live provider, deployment, release or retained-database deletion
is planned. Use focused local tests and draft fast CI, then three completed
dual-source review/fix rounds and final exact-head protected CI/merge.

## Validation checkpoint

Implementation and three dual-source review/fix rounds are complete; protected
delivery remains open. Pre-review validation used disposable PostgreSQL and exact restricted WEB/
WORKER logins: two end-to-end scenarios pass in 24.14 seconds. They cover native
CSRF/shape rejection, immutable-input replay, failed-calculation retry, worker
handoff, XLSX publication, expiry/regeneration, cancellation before/after handoff,
requester ownership, role revocation and the campaign work gate. Unchanged
rejection matrices share their prepared corpus instead of bootstrapping one
database per rejected form. No retained database is deleted or reused.

The pre-review baseline had 29 fast report/exact-UI unit tests. After review
corrections, all 34 pass in 0.57 seconds. All twelve native
report component checks pass across Chromium, Firefox and WebKit in 21.39
seconds, including keyboard/mobile/no-script controls and accessibility.
Repository Ruff and formatting checks pass. The focused exact-UI module is
included in draft CI; complete candidate CI remains required before merge.

The [review ledger](stewardship-exact-export-ui-reviews.md) records round endpoints,
raw severities, accepted corrections and evidence-backed dispositions. Existing
native workspace regressions also passed: three PostgreSQL cases in 27.12 seconds.

## Protected delivery

PR [#65](https://github.com/epiphany40223/parishkit/pull/65) merged September 19,
2026 at 13:56:53 UTC as `b60e39f4`, verified on freshly fetched `origin/main`.
Candidate `f13f8f5ead464aa28ed895f56163be587c3c4789` is one signed-off logical
commit; tree `cf2833ab26a77e9a6a633f1a578a33ebe622c5d7` exactly matches preserved
pre-squash checkpoint `cb8ddc04`. The final-head
[full CI run](https://github.com/epiphany40223/parishkit/actions/runs/35446386814)
passed all 24 jobs, including twelve PostgreSQL shards, three browser engines,
all container scenarios and coverage aggregation. DCO also passed. The run
lasted 17 minutes 56 seconds (13:38:54–13:56:50 UTC); draft pushes ran only fast
checks. This delivers the increment, not the complete catalog or Gate 3.
