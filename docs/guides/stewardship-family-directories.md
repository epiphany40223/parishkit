# Family-code and postal-outreach directories

This coherent Phase 5 increment starts from verified main `75a20c0a`, after
[PR #67 protected delivery](stewardship-information-exports.md#protected-delivery).
It implements the interactive portion of [RPT-05](../tasks/stewardship/reports.md#rpt-05-family-code-and-postal-outreach-reports),
under the [report specification](../specs/stewardship/reports/spec.md#family-code-lookup)
and [implementation plan](../plans/stewardship/reports.md#rpt-05-family-code-and-postal-outreach-reports).

## Scope and sequencing

Deliver native Admin/Staff Family-code and no-deliverable-email screens together,
with shared source/recipient calculations, private POST search and pagination,
current response state, contact/address details and guarded code decryption.
Codes remain directly visible, campaign-bound and independent of public-login
rate limits; Ministry leaders remain denied. No opaque email-link token is
queried, decrypted or displayed. Unavailable address fields remain explicitly
unavailable rather than being inferred from a primary-address value.

This is the interactive vertical slice for .01/.02/.04 and its .05 tests.
Complete-result CSV/XLSX/PDF exports (.03 and the remaining .05 tests) follow
as a separate coherent increment, reusing the report query and presentation
contracts. This mirrors the reviewed queue/export split for RPT-04 and keeps
each review bounded; it does not claim the whole package is finished early.

## Acceptance and status

Implementation and three independent review/fix rounds are complete; protected
delivery remains pending full exact-head CI/DCO. Tests cover the exact deliverability-card complement,
each reason, active/non-parishioner boundaries, name/DUID/address/phone and
canonical exact-code filters, current source/response changes, deterministic
pagination, Admin/Staff versus leader access, revocation, purge read admission,
Valkey outage, private audit/no-store/POST behavior and mobile/native/no-script
browser flows. Reuse existing database/browser infrastructure. Three completed
dual-source review/fix rounds and full exact-head CI/DCO precede protected
merge; no deployment, release or Gate 3 completion is implied.

## Focused validation

Six actual-WEB-role PostgreSQL cases pass in 25 seconds using one disposable
cluster and shared schema bootstrap. They cover source promotion, archived
source retention, all postal reasons including immutable provider refusals,
52-Family pagination, private search/audits, live response state, Admin/Staff
versus leader/revoked access, purge preparation, limiter outage and safe errors.
Nine native browser cases pass in 15 seconds across Chromium, Firefox and
WebKit, including 320-pixel layouts, keyboard accessibility and no-script POST
navigation. The combined pure parser, CI-contract and role-grant check passes
138 tests in 10.5 seconds. Full Ruff lint/format and migration-drift checks pass;
the initial feature changes no models or schema baseline. Fast draft CI adds only
the small directory-parser module; complete candidate CI remains required.

[Review corrections](stewardship-family-directory-reviews.md) extend the closed
audit-context validator in Python and the fresh-install SQL baseline. They add
no tables or model changes. The main menu now links both complete directories;
the old source-independent code route remains a documented recovery fallback.

## Protected delivery

[PR #68](https://github.com/epiphany40223/parishkit/pull/68) merged on September
19, 2026 at 18:10:27 UTC as `8dc00e9cea61fe2361c561b8f65ec44ad853ba37`.
The signed-off candidate `aed2362f2b171ecd88aa28945e8f14551efa6c19` has the same
tree `3ccd5d2cb1d0cddddeb3b2c78c753f1117ed070a` as retained review history
`420cf4350ba8367d2ea385032a09aae7f42ed043` on
`pr/stewardship-family-directories-reviewed`. All three dual-source rounds and
accepted corrections are recorded in the [review ledger](stewardship-family-directory-reviews.md).

[Full candidate CI](https://github.com/epiphany40223/parishkit/actions/runs/35459440145)
passed all 24 jobs plus DCO, with no skipped full-suite jobs. It ran from
17:52:53 to 18:10:24 UTC: 17 minutes 31 seconds. Protected auto-merge used that
exact head; freshly fetched `origin/main` was verified at the merge commit
before creating `pr/stewardship-directory-exports`. No deployment or release
occurred. RPT-05 still needs complete-result exports and their acceptance tests.
