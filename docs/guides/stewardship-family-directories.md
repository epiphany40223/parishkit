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

Implementation is complete; independent review and protected delivery remain
pending. Tests cover the exact deliverability-card complement,
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
