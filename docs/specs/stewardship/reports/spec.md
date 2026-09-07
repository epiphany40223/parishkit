# Stewardship reports and exports

Reports use an explicitly selected campaign and make their data-as-of source
snapshot/time explicit. The selector is per user/request, encoded as a stable
campaign UUID in the report URL and never written to the global current-campaign
pointer. It lists every retained campaign for which that user has report scope;
purging/purged campaigns expose only permitted status/tombstone views. The
default is the current pointer when authorized/reportable, otherwise the most
recent authorized retained campaign. Exports and pinned links persist the UUID,
so creating a successor cannot silently change a historical report. Admin
inherits every report permission. Staff sees all reports below except logs;
Ministry leaders see only Ministry reports and campaigns/rows for assigned
Ministries.

## Shared report behavior

Every report has a title, purpose/help text, active filters, source/data-as-of
metadata, accessible empty/error state, and role-aware column set. Tables are
server-paginated, sortable only by allowlisted fields, searchable, and
filterable. URLs may contain only non-identifying enumerated filters, sort keys,
page cursors, and the campaign UUID. Free-text search and any filter containing
a Family/Member name, DUID, address, email, phone, code, census value, financial
value, or other identifying text use a CSRF-protected POST body and never a URL
or query string. Proxy/application access logs omit request bodies.

Pagination cursors must obey the same non-identifying URL rule: no cursor may
expose names, DUIDs, contact details, financial values, or other row values.
Use a bounded numeric offset or an opaque server-side reference scoped to the
requester's authorized report/filter state. Encoding or signing a plaintext
sort key does not make it non-identifying. Cursor reuse never bypasses the
normal report authorization and filter-scope checks.

Every interactive report response containing Family PII, Family codes,
financial data, or census data sends `Cache-Control: no-store`, including
details and partial responses.

Exports represent the complete filtered result, not merely the current page,
unless the user explicitly selects rows. Before generation, the UI shows scope,
row count estimate, format, timezone, and sensitive-data warning. Each export
is audited with report, filters, requester, campaign, source snapshot, and row
count, but not a duplicate of every exported value.

The campaign purge gate rejects every new export and any report action that
would mutate or create campaign-owned records. The UI identifies purge
preparation as the reason and links Admins to its status; disabling controls is
not the authorization boundary. Existing read-only report views and completed
downloads may continue during preparation, protected for their entire response
lifetime by the [campaign read guards](../data/spec.md#campaign-read-guards).
Purge execution closes new admission and drains those readers/downloads before
any deletion. All data loading, deferred queries, and file streaming use the
guard; a check performed only when the view starts is insufficient. During the
gate, each view writes a parish-owned,
indefinitely retained security audit containing actor, time, action, and the
campaign UUID/tombstone reference but no report data or campaign-owned foreign
key. That access audit neither enters nor invalidates the campaign purge
inventory.

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
and financial cards/tables exclude Families that later became inactive by
default; their Admin/Staff **Include inactive** option adds a separately labeled
inactive subtotal/row set without changing the active denominator. Historical
event views retain a submission made while the Family was eligible.

A Family participates on the campaign-local date, resolved with the Campaign's
immutable timezone snapshot, of its first live submission version. Repeat
submissions never increase or move that count. Testing
submissions are excluded everywhere except explicit Admin testing views.

The participation graph has an explicit population scope. **Historical as of
day** is the default: each point uses the ever-eligible cohort through the end
of that local day and response versions as of that instant. A Family enters the
cohort on its first Portal-eligible instant in the campaign and never leaves the
historical cohort, even if later inactive. **Current population** applies
today's Portal-eligible Family set consistently to every historical point.
Current cards use current eligibility and effective versions. Monetary values
never derive from rounded installment displays.

The participation graph exposes only its mutually exclusive **Historical as of
day** / **Current population** scope control; it does not also expose **Include
inactive**. Historical scope includes every Family first eligible on or before
each day, so neither its denominator nor cumulative participant count can
shrink and its percentage cannot exceed 100%. Current-population scope excludes
currently inactive Families at every point. Statistics cards and current-scope
detail tables may expose **Include inactive** using the separate-subtotal rule
above. Digest parameters record whichever single population scope applies, so
digest parity never combines the controls.

Eligible email follows active `get_family_heads()` Members with at least one
syntactically valid normalized address. Publish privacy flags do not suppress
operational Family campaign email eligibility. **Email deliverability** further
requires at least one such address not currently suppressed after permanent
provider refusal. Statistics name these as separate populations; the
no-deliverable-email report complements the deliverable count and explains
invalid, absent, and suppressed head email without showing credential/link data.

## Participation graph

**Access:** Admin and Staff.

The x-axis is every campaign-local date from campaign start through the lesser
of campaign end and today, using the Campaign's immutable timezone snapshot.
The graph shows:

- bars: Families making their first live submission that day;
- line: cumulative Families that have submitted under the selected population
  scope; and
- line on a dollar axis, when financial is enabled: effective annual pledge
  total at each day end.

For **Historical as of day**, the source cutoff is the last promoted source
generation at or before the resolved end instant of that local day, selected
from permanent manifest generation/promotion metadata even if that snapshot's
membership was later compacted. The cohort comes from durable `FamilyCampaign`
first-eligibility provenance: it includes a
Family only when its immutable first Portal-eligible generation is at or before
that source cutoff and its first-eligibility timestamp is at or before the day
boundary. It does not reconstruct a union from retained intermediate snapshot
memberships. For each Family, response values come from its latest live
submission version committed at or before that instant, rather than from
whichever version is effective today. The numerator includes cohort Families
whose first live submission was accepted while eligible on or before that
instant. If no source generation exists by that boundary, the point is
unavailable rather than inferred from a later snapshot. A Family remains in the
historical cohort after becoming inactive. For **Current population**, every
point is recomputed using the current Portal-eligible Family set; a currently
ineligible Family is excluded from every series.

### Participation fact materialization

The graph is served from immutable, versioned `CampaignDailyFactSet` and
`CampaignDailyFact` records defined by the
[data specification](../data/spec.md#campaign-daily-report-facts), not from a
separate
per-day live aggregation on each request. The materializer calls the same
calculation library used for validation and creates a complete fact set for an
exact campaign, scope, promoted-source generation cutoff, effective-submission
cutoff, and campaign-timezone version. Historical scope reads immutable first-
eligibility provenance through that generation; current scope reads the exact
cutoff snapshot population. It atomically publishes that generation only
after every expected date validates; UI, accessible table, PNG/PDF, and digest
consumers never combine rows from different input generations.

A successful source promotion, live submission, qualifying eligibility change,
or approved rebuild request transactionally creates an idempotent rebuild hint.
Hints advance one durable rebuild-demand row per campaign/population scope;
they are not separate requests for every intermediate submission cutoff. The
first pending event starts a debounce window. Each new event advances the
requested source/submission watermarks and sets the due instant to the earlier
of five seconds after the latest event or 30 seconds after the first pending
event. With a healthy available worker, ordinary builds become eligible at that
instant; queue outages or an existing build can delay actual execution and are
reported through queue lag and the report's **Updating** state.

At claim time, the worker atomically freezes the latest requested inputs into
an immutable fact-set key and consumes that pending window. Only one ordinary
build runs per campaign/scope. Events during it accumulate in one new pending
window, retaining their original first-event deadline, so completion schedules
at most one follow-up build without losing newer demand. Duplicate delivery of
a hint does not advance watermarks or reset timers. Failed/interrupted work
retains its frozen inputs and is recoverable without clearing newer demand.

The worker rebuilds only affected dates where that is provably equivalent and
otherwise rebuilds the complete small campaign series. Historical-scope changes
normally affect the current campaign-local date and later dates; a new current-
population source snapshot can affect every displayed date. Exact-input
uniqueness still deduplicates builds after inputs are frozen. Failed or
interrupted generations remain unpublished and retryable. Publishing an older
pinned or recovered generation never moves the interactive pointer backward.

Exports pin their inputs when requested; digests pin theirs when the occurrence
first records its report inputs, using its stored as-of boundary. These requests
bypass ordinary debounce and receive priority exact-generation work. They reuse
an existing ready/building generation with the same key and never chase later
submissions, including on retry. Pinning protects the required input records
before the build starts. Priority affects claim order, not preemption of a
running build, and an exact request never consumes newer interactive demand.

If the exact current input generation is not ready, the interactive report
shows the last complete generation only when it is conspicuously labeled with
its data-as-of values, together with a non-blocking **Updating** state; it never
labels stale facts current. The user can refresh after the queued generation
publishes. A pinned export or digest waits/retries for its exact generation and
fails visibly rather than substituting a different cutoff. Recalculation from
the pinned source/submission inputs must reproduce every stored fact, and a
verification job detects drift.

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
disposition, replacement/withdrawal link, and Staff notes. The default queue
shows only `current_actionable`; history filters expose superseded/withdrawn
items. Search covers authorized text, Family name/DUID, notes, date, disposition,
and workflow state. Exports include complete text and workflow history option.

Editing is audited and uses optimistic concurrency. This report is also the
source for the weekly Admin digest.

## Family code lookup

**Access:** Admin and Staff.

List active Families with display name, Family DUID, manual code, current email
eligibility/deliverability, and response status. Search supports full/partial
case-insensitive last/family name and DUID. Exact canonicalized-code search uses
a separate CSRF-protected POST body and never places the candidate in a URL or
query string. The code
is directly visible to Admin and Staff; there is no per-row reveal action,
reauthentication ceremony, distinct-Family reveal budget, or Valkey dependency.
The server rechecks the report role and campaign scope on each request and uses
`Cache-Control: no-store` for interactive responses.

The manual code is intentionally a low-sensitivity, campaign-bound access
mechanism. Admin and Staff already hold broader parish-data access, and the code
is unusable while closed but may become usable again if that same campaign is
formally reopened; it never carries into a successor campaign. This
classification does not make it public: report access and exports remain
authenticated, codes are excluded from logs, and the public Family login
retains its guessing protections.

CSV, XLSX, and PDF exports include the same columns, including the manual code.
They use the standard asynchronous, short-lived, requester-authorized export
pipeline, including its explicitly accepted plaintext storage and owner-only
permissions under the
[export retention policy](../operations/spec.md#temporary-retention-and-housekeeping).
Interactive report execution and exports are audited at report,
campaign, actor, filter, and row-count granularity without copying codes into
the audit payload. Exact-code-search audit records omit the raw filter and store
only a keyed fingerprint when correlation is operationally necessary.

## Families without deliverable email

**Access:** Admin and Staff.

The UI labels this report **Families without deliverable email**. It lists every
current active registered Family lacking a deliverable eligible-head email,
sorted by Family name then DUID. This population is the complement of the
deliverable-email statistics card, not of the syntactic eligible-email card.
Filters/search include name, DUID, address, phone presence, and reason (no head,
no address, invalid address, or all otherwise eligible addresses permanently
refused by the provider).

Detail/export contains Family DUID, envelope number where present, Family/head
names, family/member phone numbers, complete home/mailing address, reason, and
campaign manual code. Export formats are CSV, XLSX, and PDF suitable for
external label/mail-merge software. It never includes the opaque email-link
token.

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
Ministry, output its name, chair names, stewardship period/year, and one row for
each latest effective `join` or `leave` MinistryRequest in that Ministry,
including resolved/cancelled history only when the requester selects the
history option. Rows contain:

- Member name and DUID;
- authorized Member email address(es);
- recorded email-contact date, blank if none;
- authorized Member phone number(s);
- recorded phone-contact date, blank if none; and
- current outcome using this exact mapping: unresolved workflow state is blank;
  `joined` is `Joined ministry`; `leave_confirmed` is `Left ministry`;
  `declined` is `Declined / no longer interested`; `no_response` is
  `No response`; `duplicate` is `Duplicate request`; and `other` is `Other`
  with its notes/reference.

For Ministry leaders, every email and phone value in the packet follows the
summary report's ParishSoft publish-flag rule and renders `Not published`
instead of the source value. Admin/Staff retain the operational-contact access
defined by that rule. This privacy policy applies identically to screen, CSV,
XLSX, and PDF output.

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
report parameters, and campaign-local day boundary. That boundary is resolved
using the immutable campaign timezone recorded with the campaign, not the
current Parish default.
The occurrence waits for and records the exact ready `CampaignDailyFactSet`.
The linked report opens in an authorized pinned-snapshot mode using exactly
those inputs and fact generation even when current eligibility later changes.
Separate implementations that can drift are prohibited. The email simplifies
interaction into an image/text table but its values must be reproducible from
those recorded inputs.
