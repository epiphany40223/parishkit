# Financial stewardship detail report

This Phase 5 increment begins at verified main `1070fd28` after PR #75's
[protected delivery](stewardship-ministry-packets.md#protected-delivery). It
implements the interactive half of the Family financial detail in
[RPT-08](../plans/stewardship/reports.md#rpt-08-census-and-financial-reports).
The [financial stewardship detail specification](../specs/stewardship/reports/spec.md#financial-stewardship-detail)
controls behavior.

## Scope and acceptance

Admin and Staff read one row for each currently effective live Family response
that carries a financial answer: Family name, DUID and active status, first and
latest submission times with the Family version, annual pledge, frequency and
approximate installment, selected share methods with any Other text, and the
source comparison pledge and contribution totals. A summary over the whole
filtered result, not the visible page, shows the Family count, annual total,
frequency distribution and share-method counts. Filters cover every dimension
the specification lists, plus a name or DUID search and a sort order. Results
are paged fifty Families at a time.

Testing responses and replaced submissions never appear: a later submission
replaces the earlier row and keeps the Family's first-response date. No Ministry
leader reaches the page, its data or its aggregates.

The specification's CSV, XLSX and PDF exports are **not** in this increment.
They follow as a separate PR on the shared export lifecycle, so RPT-08.04 stays
open here. The read model already accepts a complete unpaged result and a closed
parameter object so that a later immutable capture shares this page's meaning.

## Design

### One closed projection

`stewardship_financial_report_v1` validates a closed, bounded parameter object
and refuses anything else at the statement, so the page and a later capture
cannot drift apart. The application parses the same grammar first and returns a
400 before any statement; SQL remains the authority. The function is `STABLE`,
reads the campaign's own configuration and source snapshot, and returns
`disabled` for a campaign without the financial module and `unavailable` when
its inputs are missing. The application maps those to denial and to a retryable
503, never to an empty report.

### Exact money

Money crosses JSON only as canonical two-decimal text. A JSON number would be
parsed as a binary float, so SQL casts to `numeric(24,2)` text and the
application parses it with the existing exact cent helpers. The installment uses
the same half-up rule as the
[Family form](../specs/stewardship/parishioner-portal/spec.md#financial-stewardship):
1,234.50 a year is 102.88 a month, never 102.87. A zero pledge has no frequency
and therefore no installment.

### Source comparison is proven or unavailable

Comparison totals appear only when the application proves that the current
snapshot's last giving read is complete for the campaign's comparison window.
That proof depends on a window digest and stays in the one place the Family form
already uses. It can only withhold money: the window, its mapped funds and the
through-date are read in SQL from the campaign configuration and the snapshot
cursor, never from the caller. Without the proof every total is `Unavailable`
and the page says that this does not mean zero. With it, a Family with no
matching source row really is zero.

### Share labels

Share-option labels are versioned with the configuration the Family actually
saw, and an option missing from that configuration renders as
`Unavailable share method` rather than a guess. Labels render in the neutral
household wording because a report addresses no single household, and are
rendered on the server so the browser never evaluates a template.

### Private native page

The page follows the sibling campaign reports. Filters and pagination travel
only as CSRF POST state; a query string is refused, so a Family name or amount
never reaches a URL, a log or browser history. The view holds report-read
admission through rendering and streaming, re-resolves the principal inside that
guard so a role change ends the report, and records start and completion audit
events carrying counts only, never a filter, a name or an amount. The campaign
reports page links to the report only when the campaign enables the financial
module.

## Fresh-install schema audit

Fresh databases were installed separately from verified main `1070fd28` and from
the candidate on the disposable PostgreSQL 18.6 cluster and their complete
catalogs compared object by object. The predecessor exactly matched its
committed strict fingerprint. Nothing was removed and no existing definition
changed. Exactly one function was added, the financial report projection.
Tables, columns, constraints, indexes, triggers and row policies are unchanged.
Only after inspecting that exact delta was the fingerprint updated. The
candidate has 566 functions; every other count is unchanged. No upgrade path was
added and no retained database was deleted.

## Focused validation

All runs are local, on the disposable PostgreSQL 18.6 and Valkey services, and
are focused selections rather than a complete acceptance pass.

- Five database-free cases, under one second: the closed query grammar with
  nineteen refusals and a repeated parameter, exact money parsing with honest
  absence, neutral share labels with the start-date year fallback, row shaping
  against a substituted cursor including the half-up installment and an
  unavailable share method, and denial or unavailable inputs that issue no
  statement and never become an empty report.
- Three PostgreSQL cases under the real web role, 29 seconds: the effective
  response with exact money and decoy source rows that never appear, sixteen
  filter outcomes, replacement keeping one row and the first-response date,
  withheld comparison money, eleven parameter objects refused at the statement,
  and the native page with CSRF, a refused query string, invalid filters, another
  campaign, the reports-page entry, leader denial and an audit context free of
  names and amounts.
- The 17-case schema contract, 30 seconds.
- Nine browser cases on Chromium, Firefox and WebKit, 20 seconds: accessibility
  scans of four states at 320 and 1280 pixels, escaping, unavailable versus zero,
  and script-disabled native filtering and pagination that carry the applied
  filters and leave the URL clean.
- Ruff lint and format and the complete ten-module fast selection, 359 cases,
  are clean.

Draft CI substitutes this increment's financial module for the packet module,
keeping the ten-module bound; that module stays in the complete baseline.

## Checkpoint

Implementation and focused validation are complete. Dual-source review/fix
rounds, full exact-head CI, DCO and protected delivery remain open. M5 and
Gate 3 remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
