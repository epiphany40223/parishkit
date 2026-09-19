# Complete-result Family and postal exports

This Phase 5 increment starts from verified main `8dc00e9c`, after
[PR #68 protected delivery](stewardship-family-directories.md#protected-delivery).
It completes [RPT-05.03 and remaining .05](../tasks/stewardship/reports.md#rpt-05-family-code-and-postal-outreach-reports)
under the [report contract](../specs/stewardship/reports/spec.md#family-code-lookup)
and [implementation plan](../plans/stewardship/reports.md#rpt-05-family-code-and-postal-outreach-reports).

## Scope and acceptance

Reuse the shared export request, worker, status, cancellation/retry, expiration,
regeneration and guarded requester-download lifecycle. Capture the complete
filtered directory once, never a page series or a later silently changed
population. CSV/XLSX/PDF include manual codes, eligibility/deliverability,
response, envelope, heads, phones, address components and postal reasons.
Unavailable home/mailing distinctions remain explicit. Opaque email-link
tokens never enter the query, capture, renderer or artifact.

Private filters remain CSRF POST state. Before generation show complete scope,
estimated count, selected format/timezone and privacy warning. Current roles
and campaign work/read gates apply throughout the existing pipeline. Durable
capture/filter metadata and audits must not introduce plaintext manual codes;
only the accepted short-lived export artifact contains their rendered values.

Validate complete results beyond one page, every format, source changes after
capture, expired-file regeneration, exact-code privacy and all real service
role boundaries using the existing disposable PostgreSQL and browser fixtures.
Update only the fresh-install baseline, never historical upgrade compatibility.
Three completed dual-source review/fix rounds and full exact-head CI/DCO remain
mandatory before protected delivery. RPT-05/M5/Gate 3 remain open during work.

## Implementation and focused validation

The shared SQL selection now serves bounded interactive pages and complete,
immutable database-owned captures. Exact-code input resolves under the key lock
to a stable campaign Family reference, never a persisted plaintext code or
rotation-sensitive fingerprint. Workers decrypt only selected manual codes,
inside the existing campaign read guard, into the short-lived artifact.

Native export forms preserve applied private filters and show count, scope,
format, timezone and privacy. They retain a no-script UTC fallback. Existing
requester status, cancellation, retry, regeneration and guarded downloads are
reused for all three formats. Regeneration retains original source/response
values even after a newer source promotion. CSV provides an explicit Record
column to distinguish Family mail-merge rows from report metadata.

Focused local checks on September 19, 2026:

- 39 pure directory/parser/rendering and shared information-rendering tests
  pass in 1.30 seconds.
- The original nine three-engine directory accessibility/no-script tests pass;
  six new mobile export cases pass in 8.44 seconds, with browser timezone
  selection, applied-filter privacy and gated controls.
- The two initial real-role PostgreSQL cases pass in 19.75 seconds: 52-Family
  complete capture versus 50-row UI, empty/postal/exact selection, immutable
  captures, idempotence, all three worker/download formats and regeneration.
- Staff capture/gate/revocation and metadata-only service access pass, together
  with the existing native information-export worker/download regression.
  Both reuse one schema bootstrap; no live provider credentials are needed.
- The moved interactive query still passes its actual 52-row execution-plan
  test: display contact aggregation is limited to the 50-row selected page.

The independent catalog comparison used retained PR #68 database
`stewardship_directory_audit_20260919a` and new disposable fresh installation
`stewardship_directory_exports_20260919b`. Only the expected directory snapshot
table (10 columns), export-request FK column, 17 constraints, six indexes,
three functions and two triggers were added. The existing export-report check
and request/publication guard functions changed; no objects were removed and
no policy changed. The resulting inventory has 207 relations, 2,322 columns,
3,218 constraints, 955 indexes, 553 functions, 529 triggers and 28 policies.
This is a fresh-install baseline update, not an upgrade promise or DB deletion.

Fast draft CI replaces one older follow-up test module with the new directory
rendering module, retaining the ten-module limit. Complete candidate CI remains
the final delivery check; reviews and protected delivery are still pending.
