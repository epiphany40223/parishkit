# Scoped interactive Ministry reporting

This Phase 5 increment starts on `pr/stewardship-ministry-reports` from verified
main `c64a9662`, following [PR #69 delivery](stewardship-directory-exports.md#protected-delivery).
It owns the interactive portions of [RPT-06](../tasks/stewardship/reports.md#rpt-06-ministry-summary-and-detail),
under its [implementation plan](../plans/stewardship/reports.md#rpt-06-ministry-summary-and-detail)
and [report contract](../specs/stewardship/reports/spec.md#ministry-change-summary).

## Scope and acceptance

Deliver discoverable campaign-selected summary and Member/request detail for
Admin/Staff and currently assigned Ministry leaders. Reuse the existing
MinistryRequest derivation/history and source corpus. Counts must select latest
live intent before state filtering, retain hidden-Ministry intent and never
resurrect superseded/cancelled predecessors. Expose deliberate history and
activity filters with source/reference-date metadata and bounded pagination.

Current roles and assignments are reloaded within the response-lifetime read
guard. Leaders see only their assigned Ministries and only publishable phone/
email values. Missing source/privacy data remains explicitly unavailable; no
Family codes, financial information or unnecessary birth dates enter their
projection. Private searches use CSRF POST, and audit records scope/counts
without copying viewed data. Native mobile/keyboard/no-script behavior follows
the existing report framework.

Complete-result Ministry exports follow as their own coherent consumer of this
query/privacy contract; RPT-07 packets and ADM-08 follow-up editing retain their
own acceptance. No whole task/package is claimed complete during implementation.
Three completed dual-source review/fix rounds, focused validation and full
exact-head CI/DCO remain required before protected delivery. M5/Gate 3 remain
open, with no live provider, deployment or release activity authorized here.

## Implementation and validation checkpoint

The native summary and join/leave pages share one bound SQL statement for source
metadata, latest live intent, counts and bounded rows. Current assignment scope
intersects campaign selection before request projection. Leaders receive only
publishable contact columns; leave rows omit joiner contact/demographics.
Current roles are reloaded inside response-owned read protection, including a
regression for an outer Staff decision becoming a scoped leader decision.
Cancelled/superseded history is explicit, locally inactive intent survives, and
proposed Members retain local UUIDs rather than invented source DUIDs. Assignee
editing and export generation remain with their subsequent owners.

Focused local checks before peer review:

- 22 pure filter, capability and private-audit cases passed in 0.20 seconds.
- Six actual-role PostgreSQL/current-schema checks passed in 30.37 seconds:
  request history and privacy, native authorization/search/audit, hidden/proposed
  intent and source reconciliation, plus exact baseline, model parity and the
  immutable-guard inventory. They share one schema bootstrap.
- The additional 52-Member pagination regression passed in 14.64 seconds,
  proving complete, stable pages without repeated per-row contact queries.
- Nine browser checks passed in 14.53 seconds across Chromium, Firefox and
  WebKit, including mobile/desktop accessibility, keyboard navigation, escaping
  and native private POST with scripts disabled. They reuse the component server
  and browser pool rather than adding another infrastructure bootstrap.

The independent fresh-install comparison used retained PR #69 database
`stewardship_directory_exports_20260919b` against newly installed
`stewardship_ministry_reports_20260919a`. Exactly one function changed:
`stewardship_safe_context_v1`, adding the bounded numeric Ministry audit scope.
No tables, columns, constraints, indexes, triggers or policies changed. The
checked-in function fingerprint matches that independently observed delta;
there is no historical upgrade/downgrade path or database deletion.

Fast draft CI replaces the prior directory-rendering module with the focused
Ministry module; the ten-module bound and full ready-candidate suite remain.
Review and exact-head CI evidence will be added before delivery.
