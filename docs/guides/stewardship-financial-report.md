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
canonical option identity on both sides.

Validation runs as ordered statements, each relying only on what an earlier one
established: the object shapes together with the page bounds, then the proof,
then the filter grammar, then the ranges. SQL does not promise to evaluate one condition's terms in order, so
a container operator or a cast beside its own guard could run first and fail
with its own error, echoing a filter value, instead of the closed refusal. An
absent range bound becomes NULL, so no range term depends on another to be safe,
and an undecidable step refuses.

The caller must state a bounded page size, with no default on either side, so
its paging arithmetic cannot drift from the rows returned. A NULL page number
returns the complete result and needs no size. The function is `STABLE`, reads
the campaign's own configuration and source snapshot, and returns `disabled` for
a campaign without the financial module and `unavailable` when its inputs are
missing. The application maps those to denial and to a retryable 503, never to
an empty report.

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
so a later edit of the year label, the financial period or an option never
rewords a retained answer and there is no second copy of the substitution set to
drift. The parish name is the exception: it comes from the system configuration
and is always the current one. An option missing from that configuration renders
as `Unavailable share method` rather than a guess.

The whole-result summary and the filter span every page, so they can only use
the current wording. Counts stay separate per offered option even when two are
worded alike, and only options no longer offered are counted together. Share
methods appear in the order the parish configured, because their identities are
opaque. Labels use the neutral household wording because a report addresses no
single household, and are rendered on the server so the browser never evaluates
a template.

### Private native page

The page follows the sibling campaign reports. Filters and pagination travel
only as CSRF POST state; a query string is refused, so a Family name or amount
never reaches a URL, a log or browser history. The view holds report-read
admission through rendering and streaming, re-resolves the principal inside that
guard so a role change ends the report, and records start and completion audit
events carrying counts only, never a filter, a name or an amount. The campaign
reports page links to the report only when the campaign enables the financial
module.

The campaign is admitted before its filters are judged, so an unknown campaign
is denied rather than described as a filter problem. Only the requester's own
filters then produce a 400, on an accessible page that explains the money format
and links back without echoing any value. A later `ValueError` while shaping
data is a 503 on the same recovery page, because no change of filters could fix
it; it is a persistent defect that every retry reproduces, so it is recorded as
an operational failure event before the page is returned, never hidden by it.
Unavailable inputs, which the shared read guard answers itself, reach that page
too rather than the guard's bare response. Other unexpected errors are
deliberately not mapped: they stay an ordinary logged server error rather than a
quiet recovery page that would hide a defect. A page past the last match keeps
its count and says so, and Previous returns to the real last page.

## Fresh-install schema audit

Fresh databases were installed separately from verified main `1070fd28` and from
the candidate on the disposable PostgreSQL 18.6 cluster and their complete
catalogs compared object by object. The predecessor exactly matched its
committed strict fingerprint. Nothing was removed. Exactly one function was
added, the financial report projection; tables, columns, indexes, triggers and
row policies are unchanged. Only after inspecting that exact delta was the
fingerprint updated, and the audit was repeated after each review correction
that touched the function, with the same single-function delta each time. The
fourth correction check then added one operational event,
`report_shaping_failed`, which the operational log's `operational_event_safe`
check constraint must admit; full CI's existing contract test for every
operational event caught the omission before delivery, and the final audit
shows that one constraint definition changed beside the one added function,
nothing else. The candidate has 566 functions; every other count is unchanged.
No upgrade path was added and no retained
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
- Eight PostgreSQL cases under the real web role, about 35 seconds. One Family:
  exact money, another Family's source rows never appearing, sixteen filter
  outcomes, replacement keeping one row and the first-response date, and money
  withheld without a proof or with a proof of another snapshot or configuration.
  The same Family's own rows in an unmapped fund, outside the window and after a
  backdated cutoff excluded, with both boundary days included. Three real
  Families submitted at distinct campaign-clock times: per-Family attribution, a
  proven complete zero, all six sorts, thirteen filter outcomes each with a
  matching filtered summary, disjoint ordered pages at a page size of two with
  one identical summary, a page past the end, and the complete unpaged mode.
  Twenty-seven parameter values, including scalars and arrays where an object
  belongs and well-formed inverted intervals, and five page bounds refused at
  the statement with the closed refusal, beside accepted positive controls. A
  real year-label change after a Family answered, leaving that row's wording
  intact while the summary and filter change. An archived campaign whose proof
  names its own pinned snapshot under the web role, shows that snapshot's money,
  and withholds for a proof of the successor. The native page with CSRF, a
  refused query string, the error page without an echoed value, a page past the
  end, the recovery page for both unavailable inputs and a data-shaping
  `ValueError` without echoing its text, another campaign denied even with
  malformed filters, the reports-page entry, leader denial and an audit context
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

Implementation, focused validation and three
[review/fix rounds](stewardship-financial-report-reviews.md#round-3-single-source-under-the-second-exemption)
are complete: the first dual-source, the second and third single-source under
the second September 20, 2026 exemption, with every accepted finding fixed and
validated, plus a
[fourth correction check](stewardship-financial-report-reviews.md#round-4-correction-check-single-source-under-the-second-exemption)
of the last delta. Full exact-head CI, DCO and protected delivery remain open. M5 and
Gate 3 remain open. No deployment, release, live-provider write or database
deletion is authorized by this increment.

## Protected delivery

PR #76 delivered candidate `01f2b042`, five logical commits plus the PR #75
receipt, whose tree `8503949b` is identical to the retained commit-by-commit
review history on `pr/stewardship-financial-report-reviewed` and to the landed
tree. The first ready candidate, `76cabd89`, failed one of the 24 exact-head
jobs: PostgreSQL shard 1 found that the new `report_shaping_failed` operational
event was not admitted by the `operational_event_safe` check constraint, which
the SQL baseline had not been told about. The correction was one standalone
commit, re-audited against the fresh schema (one changed constraint beside the
one added function) with the fingerprint updated and the audit note amended.
Exact-head ready-candidate CI `35556839783` and DCO then passed all 24 jobs,
from 03:14:09 to 03:32:29 UTC on September 21, 2026 (18 minutes 20 seconds).
`origin/main` had no intervening commits since the candidate's base `1070fd28`.
Protected auto-merge landed as `f3bdac13be0583bfd956b164687ce76b22835b05` at
03:32:43 UTC and was verified on freshly fetched `origin/main` before the
portal users increment was rebased onto it. This used the standing delivery
authority, without deployment or release, and supersedes the pending delivery
checkpoint above. The failed and cancelled runs are not counted as acceptance.

The interactive financial report of RPT-08 is delivered; its exports follow.
M5 and Gate 3 remain open.
