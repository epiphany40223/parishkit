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
400 before any statement; SQL remains the authority. A share method is a
canonical option identity on both sides. Ranges are compared only after the
grammar has passed, because SQL may evaluate one condition's terms in any order
and a cast beside its own guard could fail first and echo a filter value. The
caller passes a bounded page size, so its paging arithmetic cannot drift from
the rows returned. The function is `STABLE`, reads the campaign's own
configuration and source snapshot, and returns `disabled` for a campaign without
the financial module and `unavailable` when its inputs are missing. The
application maps those to denial and to a retryable 503, never to an empty
report.

### Exact money

Money crosses JSON only as canonical two-decimal text. A JSON number would be
parsed as a binary float, so SQL casts to `numeric(24,2)` text and the
application parses it with the existing exact cent helpers. The installment uses
the same half-up rule as the
[Family form](../specs/stewardship/parishioner-portal/spec.md#financial-stewardship):
1,234.50 a year is 102.88 a month, never 102.87. A zero pledge may omit its
frequency, and then has no installment; if it carries one, the installment is a
real zero.

### Source comparison is proven or unavailable

Comparison totals appear only when the application proves that a snapshot's last
giving read is complete for the campaign's comparison window. That proof depends
on a window digest and stays in the one place the Family form already uses. It
is applied to the same snapshot SQL selects: an archived campaign's own pinned
source, otherwise the current one.

The proof and the report are separate READ COMMITTED statements, so the proof
names the snapshot and configuration it proved, and SQL honors it only when both
equal what it then selects itself. A promotion or configuration change in
between withholds money for that request rather than attributing another
snapshot's rows to this window. The proof can therefore only withhold: the
window, its mapped funds and the through-date are read in SQL from the campaign
configuration and the snapshot cursor, never from the caller. Without a matching
proof every total is `Unavailable` and the page says that this does not mean
zero. With one, a Family with no matching source row really is zero.

### Share labels

Each row carries the identity of the configuration its Family answered, and is
worded by the Family form's own label rule against that immutable configuration,
so a later edit of the year label or of an option never rewords a retained
answer and there is no second copy of the substitution set to drift. An option
missing from that configuration renders as `Unavailable share method` rather
than a guess. The whole-result summary and the filter span every page, so they
can only use the current wording, and count options no longer offered together.
Share methods appear in the order the parish configured, because their
identities are opaque. Labels use the neutral household wording because a report
addresses no single household, and are rendered on the server so the browser
never evaluates a template.

### Private native page

The page follows the sibling campaign reports. Filters and pagination travel
only as CSRF POST state; a query string is refused, so a Family name or amount
never reaches a URL, a log or browser history. The view holds report-read
admission through rendering and streaming, re-resolves the principal inside that
guard so a role change ends the report, and records start and completion audit
events carrying counts only, never a filter, a name or an amount. The campaign
reports page links to the report only when the campaign enables the financial
module.

Only the requester's own filters produce a 400, on an accessible page that
explains the money format and links back without echoing any value; a later
failure while shaping data is a 503, because no change of filters could fix it.
A page past the last match keeps its count and says so, and Previous returns to
the real last page.

## Fresh-install schema audit

Fresh databases were installed separately from verified main `1070fd28` and from
the candidate on the disposable PostgreSQL 18.6 cluster and their complete
catalogs compared object by object. The predecessor exactly matched its
committed strict fingerprint. Nothing was removed and no existing definition
changed. Exactly one function was added, the financial report projection.
Tables, columns, constraints, indexes, triggers and row policies are unchanged.
Only after inspecting that exact delta was the fingerprint updated. The
candidate has 566 functions; every other count is unchanged. The audit was
repeated after each review correction that touched the function, with the same
single-function delta each time. No upgrade path was added and no retained
database was deleted.

## Focused validation

All runs are local, on the disposable PostgreSQL 18.6 and Valkey services, and
are focused selections rather than a complete acceptance pass.

- Five database-free cases, under one second: the closed query grammar with 21
  refusals and a repeated parameter, exact money parsing with honest absence,
  the Family form's neutral wording with the start-date year fallback, row
  shaping against a substituted cursor including the half-up installment, a zero
  pledge with and without a frequency, wording versioned by the configuration a
  Family answered, configured share order, the bound proof and page size passed
  to SQL, and denial or unavailable inputs that issue no statement and never
  become an empty report.
- Six PostgreSQL cases under the real web role, about 35 seconds. One Family:
  exact money, another Family's source rows never appearing, sixteen filter
  outcomes, replacement keeping one row and the first-response date, and money
  withheld without a proof or with a proof of another snapshot or configuration.
  The same Family's own rows in an unmapped fund, outside the window and after a
  backdated cutoff excluded, with both boundary days included. Three real
  Families submitted at distinct campaign-clock times: per-Family attribution, a
  proven complete zero, all six sorts, thirteen filter outcomes each with a
  matching filtered summary, disjoint ordered pages at a page size of two with
  one identical summary, a page past the end, and the complete unpaged mode.
  Nineteen parameter objects and four page bounds refused at the statement
  beside an accepted positive control. The native page with CSRF, a refused
  query string, the error page without an echoed value, a page past the end,
  another campaign, the reports-page entry, leader denial and an audit context
  free of names and amounts. A campaign without the module denied and unlinked.
- The 17-case schema contract, about 25 seconds.
- Nine browser cases on Chromium, Firefox and WebKit, 17 seconds: accessibility
  scans of five report states and two error pages at 320 and 1280 pixels,
  escaping, unavailable versus zero, a page past the end, and script-disabled
  native filtering and pagination that carry the applied filters and leave the
  URL clean.
- The three existing financial source cases pass unchanged with the shared
  harness's additive hooks. Ruff lint and format, Markdown lint and the complete
  ten-module fast selection are clean.

Draft CI substitutes this increment's financial module for the packet module,
keeping the ten-module bound; that module stays in the complete baseline.

## Checkpoint

Implementation, focused validation and the first dual-source
[review/fix round](stewardship-financial-report-reviews.md#round-1) are
complete. Further review rounds, full exact-head CI, DCO and protected delivery remain open. M5 and
Gate 3 remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.
