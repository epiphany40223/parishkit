# Reports and exports tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/reports.md) ·
[Normative specification](../../specs/stewardship/reports/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## RPT-01: Shared report framework and campaign selection

Scope and dependencies: [RPT-01 work package](../../plans/stewardship/reports.md#rpt-01-shared-report-framework-and-campaign-selection).

- [ ] RPT-01.01 — Implement persistent per-request campaign selection.
- [ ] RPT-01.02 — Integrate report authorization and response-lifetime campaign read guards.
- [ ] RPT-01.03 — Build bounded report filters, sorting, and pagination.
- [ ] RPT-01.04 — Integrate asynchronous exports, bounded downloads, and busy/retry UI.
- [ ] RPT-01.05 — Audit report use without sensitive-value duplication.
- [ ] RPT-01.06 — Test historical selection, direct access, and revocation.

Evidence: Not started.

## RPT-02: Population and calculation library

Scope and dependencies: [RPT-02 work package](../../plans/stewardship/reports.md#rpt-02-population-and-calculation-library).

- [ ] RPT-02.01 — Implement shared population, history, and money calculations.
- [ ] RPT-02.02 — Implement report-specific inactive-population controls.
- [ ] RPT-02.03 — Represent missing values as unavailable.
- [ ] RPT-02.04 — Test denominators, eligibility history, and timezone changes.

Partial evidence: the [financial-response increment](../../guides/stewardship-financial-responses.md)
provides shared exact-cent money/aggregation primitives and typed unavailable
values, with Family-scoped period/coverage checks and regression tests. These
cover the financial prerequisite portions of .01/.03, not the full population,
history, denominator, timezone, or report-service scope; all four tasks remain
open for their later consumers.

In-progress evidence: the [ordinary report-fact increment](../../guides/stewardship-report-facts.md)
adds historical/current populations, first/latest live response and exact pledge
series calculations, plus real source/response integration tests. Its local
validation, three review/fix rounds and protected delivery are complete in PR #41.
Full package
acceptance remains open; the guide records the remaining consumer calculations.

## RPT-03: Participation graph and campaign statistics

Scope and dependencies: [RPT-03 work package](../../plans/stewardship/reports.md#rpt-03-participation-graph-and-campaign-statistics).

- [ ] RPT-03.01 — Build the interactive participation and pledge chart.
- [ ] RPT-03.02 — Implement immutable facts, debounce, pinned priority, and retention guards.
- [ ] RPT-03.03 — Keep historical/current scope and source metadata coherent.
- [ ] RPT-03.04 — Build campaign statistics cards.
- [ ] RPT-03.05 — Share facts with tables, images, and digests.
- [ ] RPT-03.06 — Test rebuild races, cutoffs, parity, and accessible fallback.

In-progress evidence: the [ordinary report-fact increment](../../guides/stewardship-report-facts.md)
connects existing fact storage/demand/retention to compiled scheduler/worker
calculation and immutable publication. Queued exact-input priority and consumer
selection/verification scheduling remain a following prerequisite increment;
interactive reports remain Phase 5. No RPT-03 task is yet claimed complete.

Consumer evidence: the [report selection increment](../../guides/stewardship-report-selection.md)
has completed implementation, local validation and three dual-source review/fix
rounds for authorized current/stale selection, shared export documents and
read-only verification/compaction guards. PR #42 merged as
`c72b8bdb1fa37c555ee68ca926ff187a7ab52d7b` after all 24 exact-head and
merge-queue checks passed. The [queued exact-export increment](../../guides/stewardship-exact-exports.md)
has completed implementation and three dual-source review/fix rounds on
`pr/stewardship-exact-report-facts`, including final correction validation;
protected delivery is pending. Scheduled verification still follows before BG-07.
All package checkboxes remain open.

## RPT-04: Additional-information workflow report

Scope and dependencies: [RPT-04 work package](../../plans/stewardship/reports.md#rpt-04-additional-information-workflow-report).

- [ ] RPT-04.01 — Build searchable information queues and history.
- [ ] RPT-04.02 — Integrate concurrent-safe follow-up and notes editing.
- [ ] RPT-04.03 — Expose corrections to previously digested information.
- [ ] RPT-04.04 — Export complete text and optional workflow history.
- [ ] RPT-04.05 — Test withdrawal, corrections, authorization, and digest parity.

Evidence: Not started.

## RPT-05: Family code and postal-outreach reports

Scope and dependencies: [RPT-05 work package](../../plans/stewardship/reports.md#rpt-05-family-code-and-postal-outreach-reports).

- [ ] RPT-05.01 — Build the authorized Family-code directory and search.
- [ ] RPT-05.02 — Build the no-deliverable-email complement report.
- [ ] RPT-05.03 — Export code and postal data for mail merge.
- [ ] RPT-05.04 — Enforce code privacy and limiter-outage availability.
- [ ] RPT-05.05 — Test delivery reasons, search, columns, and access.

Evidence: Not started.

## RPT-06: Ministry summary and detail

Scope and dependencies: [RPT-06 work package](../../plans/stewardship/reports.md#rpt-06-ministry-summary-and-detail).

- [ ] RPT-06.01 — Build Ministry join/leave counts and cross-links.
- [ ] RPT-06.02 — Build privacy-scoped Member detail.
- [ ] RPT-06.03 — Add scoped search, filters, and exports.
- [ ] RPT-06.04 — Apply request supersession to counts.
- [ ] RPT-06.05 — Test assignment boundaries, privacy, and inactive controls.

Evidence: Not started.

## RPT-07: Multi-Ministry follow-up packet

Scope and dependencies: [RPT-07 work package](../../plans/stewardship/reports.md#rpt-07-multi-ministry-follow-up-packet).

- [ ] RPT-07.01 — Build authorized multi-Ministry selection.
- [ ] RPT-07.02 — Build Ministry packet sections and request rows.
- [ ] RPT-07.03 — Apply specified follow-up outcome and date mappings.
- [ ] RPT-07.04 — Implement PDF, XLSX, and CSV section boundaries.
- [ ] RPT-07.05 — Test packet ordering, overflow, scope, and outcomes.

Evidence: Not started.

## RPT-08: Census and financial reports

Scope and dependencies: [RPT-08 work package](../../plans/stewardship/reports.md#rpt-08-census-and-financial-reports).

- [ ] RPT-08.01 — Build the pending-census change report.
- [ ] RPT-08.02 — Integrate authorized publication and manual-resolution actions.
- [ ] RPT-08.03 — Export change, decision, conflict, and execution detail.
- [ ] RPT-08.04 — Build Family financial detail and list reports.
- [ ] RPT-08.05 — Test source conflicts, edited provenance, and money privacy.

Evidence: Not started.

## RPT-09: Logs and daily email parity

Scope and dependencies: [RPT-09 work package](../../plans/stewardship/reports.md#rpt-09-logs-and-daily-email-parity).

- [ ] RPT-09.01 — Integrate Admin log viewing and text/JSONL export.
- [ ] RPT-09.02 — Apply level, redaction, and detail-display defaults.
- [ ] RPT-09.03 — Implement exact-input daily email parity contracts.
- [ ] RPT-09.04 — Test values across web, chart downloads, and digests.

Evidence: Not started.
