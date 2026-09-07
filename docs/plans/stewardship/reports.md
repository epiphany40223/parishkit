# Reports and exports implementation plan

Task status: [Reports and exports checklist](../../tasks/stewardship/reports.md).

This plan implements the
[reports and exports specification](../../specs/stewardship/reports/spec.md).
Report calculations live in tested query/services shared by HTML, exports,
charts, and digest mail; templates do not independently recalculate metrics.

## Work packages

### RPT-01: Shared report framework and campaign selection

1. Build per-user/request campaign selection carried by URL UUID and retained
   across filters, pagination, export, and cross-report links.
2. Implement role/object/column authorization hooks using DOM-03, including
   assigned-Ministry scoping and no leader access to financial/code data.
   Wrap all campaign reads in DAT-02's shared guard through lazy evaluation,
   serialization, and response completion; reject closed purge admission.
3. Create reusable filter/sort/pagination/query-state components with bounded
   page sizes and browser-local timezone selection.
4. Integrate asynchronous BG-08 CSV/XLSX/PDF/PNG export controls and chart
   download with requester/status visibility.
   Hold the same guard throughout streaming, with bounded total lifetime and
   cleanup on disconnect, timeout, and connection loss.
   Integrate the dedicated bounded download pool and accessible retryable busy
   response from DAT-02, without invalidating an already generated export.
5. Audit report execution/export without copying viewed sensitive values.
6. Test stale URLs, archived campaigns, role changes, direct export/download,
   and filter serialization.

### RPT-02: Population and calculation library

1. Implement effective live/test exclusion, active/inactive population,
   eligibility/deliverability complements, the durable first-eligibility-
   provenance historical cohort, first/latest response, immutable campaign-
   timezone local-day, pledge, comparison, age-at-reference, and Ministry
   request calculations.
2. Define Include inactive control semantics independently from each report's
   base population and ensure denominators/percentages change coherently.
3. Return typed unavailable/missing values instead of zero when source/mapping
   is incomplete.
4. Add table-driven tests for every calculation, denominator zero, historical
   snapshots, repeat submissions, source changes, timezone boundary, and Parish
   default changes that must not rebucket campaign history.

### RPT-03: Participation graph and campaign statistics

Delivery sequencing: the fact-materialization service in item 2 and its
service-level tests from item 6 land before BG-07 consumes them in Phase 4.
The interactive report and complete package validation finish in Phase 5.

1. Implement daily submissions, cumulative Families, and cumulative effective
   pledges on one interactive, titled, labeled, legend-bearing chart.
2. Materialize immutable complete CampaignDailyFactSet generations for exact
   source-generation/submission/scope/timezone inputs, using durable first-
   eligibility provenance for historical scope, with idempotent event-triggered
   rebuilds, atomic publication, drift verification, and explicit updating/
   stale-as-of states. Implement the per-campaign/scope demand row, five-second
   quiet window with a 30-second maximum debounce, atomic input freezing, and
   one pending follow-up during an ordinary build. Give pinned export/digest
   requests priority with fixed inputs and exact-generation reuse.
   Integrate the data specification's derived-fact retention guards for pointer
   publication, pinning, build/recovery, and active consumers; OPS-07 owns the
   periodic compactor.
3. Keep current-population and ever-eligible historical scope internally
   consistent and expose source-as-of metadata.
4. Implement statistics cards for active Families/Members, eligible and
   deliverable email, responses, effective pledge, and mapped comparison pledge
   using US formatting and `X out of Y (Z%)`.
5. Share one fact-query/chart-rendering service with the accessible table,
   PNG/PDF download, and BG-07 digest.
6. Test repeated responses, source/eligibility changes, failed and concurrent
   rebuilds, local days, missing financial data, pinned parity, stale labeling,
   and accessible tabular fallback. Use a fake clock to test burst coalescing,
   sustained traffic reaching the maximum debounce, duplicate hints, events
   racing claims/completion, crash recovery, and fixed-cutoff priority requests
   that neither chase submissions nor discard newer interactive demand.
   Race compaction against selection, rendering, drift verification, and new
   pins; verify protected generations survive and eligible superseded daily
   rows are reclaimed without changing report values or source history.

### RPT-04: Additional-information workflow report

1. Build Admin/Staff searchable/filterable current queue plus explicit history
   view for actionable, superseded, and withdrawn records.
2. Implement follow-up-needed/followed-up/notes editing through ADM-08 services
   with optimistic concurrency and durable actor/time history.
3. Add correction visibility for items already included in a digest.
4. Export complete text and optional workflow history with safe wrapping/
   escaping.
5. Test correction/withdrawal, concurrent Staff edits, denied leaders, and
   weekly digest parity.

### RPT-05: Family code and postal-outreach reports

1. Build Admin/Staff active-Family directory with direct manual code,
   eligibility/deliverability, response state, name/DUID/exact-code search, and
   no-store pages.
2. Build Families without deliverable email as the exact complement population,
   with reason, heads, phones, complete addresses, envelope number, and campaign
   manual code.
3. Provide authenticated CSV/XLSX/PDF output for both, suitable for mail merge;
   never include opaque email-link tokens.
4. Deny Ministry leaders and exclude codes from logs/audit payloads while
   preserving report/export audit and availability during Valkey outage.
5. Test every deliverability reason, partial-name search, export columns, code
   classification, and role boundary.

### RPT-06: Ministry summary and detail

1. Implement sorted campaign-Ministry join/leave counts and cross-links.
2. Build authorized Member detail with DUID, gender, campaign-reference age,
   phones, emails, and mailing address for join; use the narrower specified
   leave data.
3. Add filters/sort/search and CSV/XLSX/PDF under Admin/Staff/all versus leader/
   assigned-Ministry scopes.
4. Ensure latest effective request and supersession rules prevent duplicate
   counts.
5. Test multi-assignment scope, cancelled/superseded requests, privacy columns,
   and active/inactive controls.

### RPT-07: Multi-Ministry follow-up packet

1. Build multi-select/all selection with server-side scope intersection.
2. Generate one section/page per Ministry with name, active chair names,
   stewardship year, and rows derived from latest join/leave requests plus
   existing follow-up state.
3. Implement the exact outcome mapping defined by the
   [follow-up-packet specification](../../specs/stewardship/reports/spec.md#multi-ministry-follow-up-packet),
   and leave human email/phone date columns blank where specified.
4. Implement PDF page breaks, XLSX sheets/sections, and CSV blank-row/repeated-
   header boundaries.
5. Test deterministic ordering, empty Ministries, long content, leader scope,
   and every outcome mapping.

### RPT-08: Census and financial reports

1. Build pending-census change list over derived proposal rows with current/
   submitted/proposed toggle, writable visual indicator, filters, and option to
   omit API-writable rows.
2. Link Admin actions to ADM-09 while Staff remains view-only; expose manual
   resolution where authorized by the core matrix.
3. Implement CSV/XLSX/PDF with writability/decision/execution/conflict fields
   and privacy-safe values.
4. Build Admin/Staff Family financial detail/list with effective pledge,
   frequency, per-period amount, selected stable share labels/Other, prior
   pledge/contribution, mapped periods/funds, and source-as-of.
5. Test unsupported/manual/API changes, source catch-up/conflict, Admin edit
   provenance, zero/missing money, and Ministry-leader denial.

### RPT-09: Logs and daily email parity

Dependencies: ADM-08 log UI and BG-08 export substrate land before this package.

1. Integrate the Admin-only combined log report specified by ADM-08 with BG-08
   text/JSONL export and UTC/browser-local choices.
2. Enforce DEBUG-off default, accessible five-level indicators, redacted search
   fields, and bounded detail.
3. Build a parity contract that renders daily email statistics/chart from the
   same RPT-02/RPT-03 data objects and records the exact report parameters.
4. Add snapshot/structural tests comparing web, downloadable chart, and digest
   values without brittle pixel-only assertions.

## Review handoffs

- Review Gate 3 covers RPT-01 through RPT-07, the reporting/financial subset of
  RPT-08, and RPT-09. It focuses on calculation parity, role/column privacy,
  code access, export authorization, operational log/export behavior, and
  formula safety.
- Review Gate 4 covers the RPT-08 publication/action links.
- Review Gate 5 completes scale, accessibility, and every-format validation.

## Completion criteria

- Every report in the specification exists in authorized HTML and required
  export formats with tested filters and historical selection.
- A single tested calculation service feeds UI, exports, charts, and emails.
- Direct URLs, queued jobs, and downloads cannot bypass role, Ministry, column,
  campaign, or purge gates.
