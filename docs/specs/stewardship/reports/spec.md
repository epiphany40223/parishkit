# Stewardship reports and exports

Reports use the currently selected campaign and make their data-as-of source
snapshot/time explicit. Admin inherits every report permission. Staff sees all
reports below except logs; Ministry leaders see only the Ministry reports and
only rows for assigned Ministries.

## Shared report behavior

Every report has a title, purpose/help text, active filters, source/data-as-of
metadata, accessible empty/error state, and role-aware column set. Tables are
server-paginated, sortable only by allowlisted fields, searchable, and
filterable. Filter URLs are shareable only within an authorized session and do
not contain PII values unnecessarily.

Exports represent the complete filtered result, not merely the current page,
unless the user explicitly selects rows. Before generation, the UI shows scope,
row count estimate, format, timezone, and sensitive-data warning. Each export
is audited with report, filters, requester, campaign, source snapshot, and row
count, but not a duplicate of every exported value.

The campaign purge gate rejects a new report execution or export when it would
create campaign-owned audit, task, or file records. The UI identifies purge
preparation as the reason and links Admins to its status; disabling controls is
not the authorization boundary. Existing read-only views or completed downloads
may continue only when they add no campaign-owned record.

CSV is UTF-8 with a header row and CRLF-compatible output. Cells beginning with
formula-significant characters are neutralized. XLSX uses freeze panes,
filters, meaningful widths, types, repeated print headings, and no macros. PDF
uses parish branding, generation/data-as-of time, page numbers, repeated table
headings, and legible landscape layout where needed.

All charts have title, legend, labeled axes with units, accessible color/line
patterns, hover/focus values, and equivalent data tables. They download as PNG
or PDF. Dollar/count series on one chart use separate labeled axes rather than
comparing unlike units on one scale.

## Population and calculation rules

"Active" means current promoted ParishSoft eligibility. Current participation
and financial totals exclude Families that later became inactive by default;
an Admin/Staff Include inactive option shows them separately. Historical event
views retain a submission made while the Family was eligible.

A Family participates on the parish-local date of its first effective live
submission. Repeat submissions never increase or move that count. Testing
submissions are excluded everywhere except explicit Admin testing views.

The participation graph has an explicit population scope. **Historical as of
day** is the default: each point uses eligibility and effective responses at the
end of that local day. **Current population** applies today's Portal-eligible
Family set consistently to every historical point. Current cards use current
eligibility and effective versions. Monetary values never derive from rounded
installment displays.

Eligible email follows active `get_family_heads()` Members with at least one
syntactically valid normalized address. Publish privacy flags do not suppress
operational Family campaign email eligibility. **Email deliverability** further
requires at least one such address not currently suppressed after permanent
provider refusal. Statistics name these as separate populations; the
no-deliverable-email report complements the deliverable count and explains
invalid, absent, and suppressed head email without showing credential/link data.

## Participation graph

**Access:** Admin and Staff.

The x-axis is every parish-local date from campaign start through the lesser of
campaign end and today. The graph shows:

- bars: Families making their first live submission that day;
- line: cumulative Families that have submitted under the selected population
  scope; and
- line on a dollar axis, when financial is enabled: effective annual pledge
  total at each day end.

For **Historical as of day**, each bar, cumulative count and denominator, and
pledge total uses the Family eligibility/effective response recorded for that
day. A Family that participated while eligible remains in the historical series
after becoming inactive. For **Current population**, every point is recomputed
using the current Portal-eligible Family set; a currently ineligible Family is
excluded from every series.

The UI defaults to Historical as of day and offers a clearly labeled scope
toggle. Changing scope updates every series together. Hover shows scope, local
date, daily count, cumulative count out of the scoped population with
percentage, and pledge. Report URLs, pinned digest inputs, equivalent data
tables, PNG/PDF output, and structured downloads record the scope and exactly
match the displayed values. A scope note explains why the historical endpoint
may differ from current-statistics cards.

## Campaign statistics

**Access:** Admin and Staff.

Cards show:

- active Families and active Members;
- active Families with eligible email out of active Families and percentage;
- active Families with deliverable email out of active Families and percentage;
- active Families with first live response out of active Families and
  percentage;
- effective campaign annual pledge total, when enabled; and
- mapped prior comparison pledge total, when available.

Giving cards include mapped funds/period and source as-of. Missing/incomplete
source displays Unavailable, not zero.

## Additional information

**Access:** Admin and Staff; both may edit its workflow.

One row per distinct `AdditionalInformationItem` shows submission time, Family
name/DUID, text excerpt/full detail, follow-up-needed, followed-up time/actor,
and Staff notes. Search covers authorized text, Family name/DUID, notes, date,
and workflow state. Exports include complete text and workflow history option.

Editing is audited and uses optimistic concurrency. This report is also the
source for the weekly Admin digest.

## Family code lookup

**Access:** Admin and Staff.

List active Families with display name, Family DUID, a masked manual-code field,
current email eligibility/deliverability, and response status. Search supports
full/partial case-insensitive last/family name and DUID. A separate exact-code
search canonicalizes and fingerprints the supplied candidate, returning only
the matching Family without revealing any other code.

Each row has a CSRF-protected **Show code** action for Admin and Staff. It
reauthorizes the object, decrypts only that Family's code, writes an audit event
before returning it, uses `Cache-Control: no-store`, and automatically remasks
on navigation or after 60 seconds. The audit records actor, campaign/Family,
time, request correlation, and source metadata, never the code. Reveal attempts
are limited per user to 30 distinct Families per rolling hour by default;
excess receives `429` and creates one deduplicated Admin WARNING. Production may
configure a stricter threshold.

This report is not offered as a bulk downloadable file by default because it is
a credential directory. If implementation requires print/export for parish
operations, it must be an explicit Admin-only configuration, freshly
authenticated, watermarked, and separately specified; it is not first-release
behavior.

## Families without eligible email

**Access:** Admin and Staff.

The UI labels this report **Families without deliverable email**. It lists every
current active registered Family lacking a deliverable eligible-head email,
sorted by Family name then DUID. This population is the complement of the
deliverable-email statistics card, not of the syntactic eligible-email card.
Filters/search include name, DUID, address, phone presence, and reason (no head,
no address, invalid address, or all otherwise eligible addresses permanently
refused by the provider).

Detail/export contains Family DUID, envelope number where present, Family/head
names, family/member phone numbers, complete home/mailing address, and reason.
Export formats are CSV, XLSX, and PDF suitable for external label/mail-merge
software. It never includes Family code/token unless the distinct credential
report policy is invoked.

## Ministry change summary

**Access:** Admin/Staff for all selected campaign Ministries; leaders for
assigned campaign Ministries only.

The sorted summary has Ministry name/DUID, join-request count, leave-request
count, unresolved count, and follow-up progress. Counts use latest live request
state while retaining links to superseded/history views.

Each Ministry links to:

- prospective joiners: Member name/DUID, gender, age as of report date,
  publishable email/phones for leaders, operational contact for Admin/Staff,
  Family mailing address, request/submission date, assignee/status/outcome; and
- requested leavers: Member name/DUID, current role where known, request date,
  assignee/status/outcome.

Leader phone/email columns honor ParishSoft publish flags and show "Not
published" rather than leaking a value. Admin/Staff may see operational source
contact. Mailing address/demographics are visible to assigned leaders as
explicitly authorized for this workflow. Birth date itself is not shown when
age suffices.

Lists are searchable/filterable/sortable and exportable as CSV, XLSX, or PDF.
Every leader view/export is audited with Ministry scope.

## Multi-Ministry follow-up packet

**Access:** same Ministry scoping as the summary.

The request offers multi-select plus Select all authorized Ministries. For each
Ministry, output its name, chair names, stewardship period/year, and rows with:

- Member name and DUID;
- authorized Member email address(es);
- recorded email-contact date, blank if none;
- authorized Member phone number(s);
- recorded phone-contact date, blank if none; and
- current outcome (`Join ministry`, `No longer interested`, `No response`, or
  a mapped workflow outcome).

Recorded workflow values are prefilled; empty cells remain printable for human
completion. The XLSX is one workbook with a sheet per Ministry. PDF starts each
Ministry on a new page. CSV is one file with a blank row and repeated headings
between Ministries. No ZIP/per-Ministry files are required.

## Pending census changes

**Access:** Admin and Staff. Staff edits only manual-resolution state; Admin has
the full review/publication controls.

The default filter shows unresolved, unreviewed current changes. Columns include
Family/Member, DUID, field/request, proposed summary, writability, decision,
execution, submission time, and conflict/failure indicator. Sensitive values
are masked in the list where appropriate and visible in authorized detail.

Options include:

- show/hide baseline/current/proposed values;
- show only API/manual/report-only classifications;
- omit API-writable items;
- include ignored/resolved/published/superseded history;
- campaign/Family/Member/date/status filters; and
- CSV, XLSX, or PDF filtered export.

Conflict detail shows baseline, current upstream, Family submitted, and
Admin-edited proposed values. It never resolves by silent last-write-wins.

Staff may mark manual items resolved externally or ignored with notes. Admin
review/publish actions are defined by the [data](../data/spec.md#review-and-publication)
and [Admin](../admin-portal/spec.md#follow-up-workflows) specifications.

## Financial stewardship detail

**Access:** Admin and Staff only.

This required report closes the operational path for report/export-only
pledges. One row per currently effective live Family response shows Family name
and DUID, submission/version time, annual pledge, frequency, approximate
installment, selected share-option labels, Other text, active status, and
source comparison pledge/contribution aggregates with as-of time.

Filters include active/inactive, first/latest submission dates, pledge range,
zero/nonzero, frequency, and share method. Summary shows Family count, annual
total, frequency distribution, and share-method counts. Exports are CSV, XLSX,
and PDF. No Ministry leader receives aggregate or Family financial detail.

## System logs

**Access:** Admin only.

The report behavior, levels, source filters, timezone export, and text/JSONL
formats are defined by the [Admin log specification](../admin-portal/spec.md#logs).
It is included in the shared report/export audit pipeline but not offered to
Staff.

## Daily email report parity

The daily Admin digest uses the same query/calculation service and chart data as
the participation/statistics web reports for its stored as-of point and defaults
to Historical as of day. The digest occurrence records the promoted source
snapshot, effective-submission version cutoff, selected population scope,
report parameters, and parish-local day boundary.
The linked report opens in an authorized pinned-snapshot mode using exactly
those inputs even when current eligibility later changes. Separate
implementations that can drift are prohibited. The email simplifies interaction
into an image/text table but its values must be reproducible from those recorded
inputs.
