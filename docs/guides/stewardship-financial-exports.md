# Financial stewardship exports

This Phase 5 increment begins at verified main `f3bdac13` after PR #76's
[protected delivery](stewardship-financial-report.md#protected-delivery). It
completes [RPT-08.04](../tasks/stewardship/reports.md#rpt-08-census-and-financial-reports)
with the CSV, XLSX and PDF exports the
[financial stewardship detail specification](../specs/stewardship/reports/spec.md#financial-stewardship-detail)
requires, on the
[shared export contract](../specs/stewardship/background-processing/spec.md#exports-and-graph-rendering).
The [review ledger](stewardship-financial-export-reviews.md) records findings,
corrections and delivery evidence.

## Scope

Admin and Staff queue a complete export of the
[financial stewardship detail](stewardship-financial-report.md) from the report
page: every Family matching the applied filters and sort, not the visible page,
with the same columns and the whole-result summary as report metadata. The
capture is immutable and taken once; retries and regeneration render that
capture, so a later submission or source promotion never changes a file.

The existing asynchronous export execution, requester controls, guarded
downloads, cleanup and regeneration are reused unchanged. No Ministry
authorization, new delivery framework or Phase 6 workflow is added. No Ministry
leader queues, reads or regenerates a financial export.

## Design

### One projection, captured by SQL

`stewardship_financial_export_snapshot` is a self-contained capture. Its insert
trigger, never the caller, writes the document by calling the page's own
`stewardship_financial_report_v1` with a NULL page, so the capture is the
complete result that the function already defined for exactly this purpose.
The application's giving proof travels inside the retained parameters, and SQL
honors it only for the snapshot and configuration it selects itself; a capture
taken while a promotion or configuration change is landing withholds source
money rather than misattributing it, as the page does. The trigger refuses a
worker, a campaign that cannot accept work, an actor the SQL policy does not
admit as Administrator or Staff, and a disabled or unavailable projection.

The request table gains a `financial_snapshot_id` reference, its exactly-one-
retained-input check gains the `financial` report, and the request and
publication guards verify the snapshot as they verify the others: same
campaign, same parameters, and a publication row count equal to the capture.

### One wording

The read model's display shaping, previously inline in the page's read, is
`shape_result`, and both the page and the export document pass through it.
Money is canonical text in the capture and the same `$1,234.50` or
`Unavailable` in a cell; share wording is versioned with the configuration each
Family answered under, from the campaign's retained versions; the summary uses
the campaign's current wording, as the page's does. The document builder
refuses a capture whose row count is not its total, so a page can never
masquerade as a complete export. The generic field/value renderer produces the
three formats, with the whole-result summary, the comparison period, the
giving through-date or an explicit unavailability note, and the giving read's
own observation time, stated apart from the source promotion time because a
Family-only refresh keeps an older giving read, carried as metadata.

A Family's share wording can exceed a spreadsheet cell: a configuration may
offer a hundred options and each Other text may run to two thousand
characters, and openpyxl truncates a cell beyond 32,767 characters silently.
Whole entries therefore continue in further rows for the same Family, marked
as continued and carrying the Family, its DUID and the response reference and
nothing else, so no amount is counted twice and the cells concatenate back to
every character. Summary counts are wrapped values, one label per line, never
metadata keys, because the PDF renderer wraps a value to the width left after
its key and a long share label would leave none.

### Authority and privacy

Queuing needs the page's own capability, `financial_detail`, checked by the
web request, again under the owning work transaction at enqueue, and by SQL at
capture through the Administrator-or-Staff policy that is exactly who holds it.
Reading status, downloading and regenerating a financial export require that
capability as well as the campaign reporting capability the other exports need.
Filters travel only in the CSRF POST body; a query string is refused, and the
form's hidden filter fields are the applied ones, never a page number. A
replay with the same request key is the same export even when the proof
recomputed between two identical submissions differs; a different selection
under the same key is refused. The request page shows counts, generations and
times, never a name or an amount, and never loads the captured document.

The export form is offered beside the page's results, disabled while the
campaign cannot accept work, with the requester's browser timezone selected
when scripts run and UTC otherwise.

## Fresh-install schema audit

Independent fresh predecessor and candidate databases were compared on the
disposable PostgreSQL cluster; the predecessor, verified main `f3bdac13`,
exactly matches its committed fingerprint. Additions are one capture table, 11
columns, 17 constraints, six indexes, two functions and two triggers. The
report/input check constraint and the two export guards are the only changed
objects; nothing is removed. The candidate has 210 relations, 2,360 columns,
3,273 constraints, 972 indexes, 568 functions, 535 triggers and 28 policies.
The strict fixture was updated only after this inspected comparison. This is a
pre-production fresh-install baseline; no upgrade path is added and no retained
database was deleted.

## Focused validation

- Seven database-free document cases: every cell's wording in the export
  timezone, unproven money as the word and never zero, an incomplete or unzoned
  capture refused, share wording beyond a spreadsheet cell continued in later
  rows and recovered whole from a workbook round trip with a long share label
  still rendering as PDF, and each of the three formats rendered from one
  document.
- Two PostgreSQL cases under the real web and worker roles: a three-Family
  capture that is the whole result beside a two-row page, the web request
  bringing back only the capture's header, canonical proven money including a
  real zero, exact replay and refused re-binding, SQL immutability refusing
  even the schema owner, the worker holding no insert grant, a filtered
  capture with its own summary and count-only audit rows; and the native flow
  for all three formats
  with CSRF, method, field, query-string and other-campaign refusals, the
  request page without the document, real worker execution, guarded downloads
  with the report's own file name, regeneration after expiry keeping the
  retained capture, and a Ministry leader denied over HTTP, in the service and
  in SQL.
- The page's existing eight PostgreSQL cases, the 22 information-export,
  export-view and exact-export cases, and the two schema baseline cases pass
  with the shared shaping, the new grants and the new fingerprint.
- Nine browser cases across Chromium, Firefox and WebKit: the export controls
  are reachable and accessible in every page state, disabled while the
  campaign cannot accept work, and without scripts the export posts the
  applied filters, the chosen format, UTC and the one-time key natively to the
  export route.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and one dual-source
[review/fix round](stewardship-financial-export-reviews.md) are complete, with
every accepted finding fixed. Two more rounds, full exact-head CI, DCO and
protected delivery remain open. M5 and Gate 3 remain open. No deployment,
release, live-provider write or database deletion is authorized by this
increment.
