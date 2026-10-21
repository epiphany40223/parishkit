# Stewardship reports and exports

Reports use an explicitly selected campaign and make their data-as-of source
snapshot/time explicit. The selector is per user/request and never written to
the global current-campaign pointer. It lists every retained campaign for
which that user has report scope; purging/purged campaigns expose only
permitted status/tombstone views. The default is the current pointer when
authorized/reportable, otherwise the most recent authorized retained campaign.
Exports persist their campaign, so creating a successor cannot silently change
a historical report. Admin inherits every report permission. Staff sees all
reports below except logs; Ministry leaders see only Ministry reports and
campaigns/rows for the Ministries they lead (see
[Ministry leaders](../data/spec.md#ministry-leaders)).

The campaign selector and the default to the most recent retained campaign are
superseded for the Admin portal by the [navigation
decisions](../admin-portal/spec.md#navigation-decisions) 10, 15 and 19:
reports show the current campaign, with no selector link and no line naming
the campaign (#1125). Exports keep their own identifiers.

The comparison of the current campaign with past campaigns, and the archive
workbook it imports, are specified in
[campaign history and comparison](../campaign-history/spec.md).

## Shared report behavior

Every report has a title, purpose/help text, active filters, source/data-as-of
metadata, accessible empty/error state, and role-aware column set. Tables are
server-paginated, sortable only by allowlisted fields, searchable, and
filterable. URLs may contain only non-identifying enumerated filters, sort
keys and page cursors, never a campaign identifier (Administrator decision,
2026-10-10; see the [addresses
rule](../ui-conventions/spec.md#addresses-and-legacy-urls)). Free-text search
and any filter containing a Family/Member name, DUID, address, email, phone,
code, census value, financial value, or other identifying text use a
CSRF-protected POST body and never a URL or query string. Proxy/application
access logs omit request bodies.

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
row count estimate and format, with no sensitive-data banner (the
[downloaded files](../ui-conventions/spec.md#downloaded-files) rule). It never
asks for a time zone: the file uses the browser's zone and names it, except
where the page lets the reader view times in UTC (the
[time zones](../ui-conventions/spec.md#downloaded-files) rule,
[#1679](https://github.com/epiphany40223/parishkit/issues/1679)). Each export
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

Downloads of stored export files also use the deployment-wide bounded
admission and dedicated pool defined by
[campaign read guards](../data/spec.md#campaign-read-guards), which also
defines how a small download rendered on request in memory is read.
When capacity is busy, show the retryable response without discarding the
generated export; a retry performs fresh authorization and purge checks.
A background export whose render reaches its read guard's deadline stops the
general worker (the guard's hard stop, logged first as a `read_guard`
timeout entry), and recovery retries it like any abandoned export, up to five
attempts; after a first such stop the retry waits the longest delay (10
minutes), so one slow spell does not fail it. After a second such stop it
fails instead
([#386](https://github.com/epiphany40223/parishkit/issues/386)): a render
that overran once may have met a slow moment, but one that overruns twice
would only stop the worker again. Recovery counts those entries through
`stewardship_read_guard_kills_v1`, a narrow definer function, since the
worker cannot read the log's context; a stop whose entry did not land counts
only toward the five attempts.
Admin recovery of expired-file housekeeping is a separate operational workflow
defined by [export cleanup recovery](../background-processing/spec.md#export-cleanup-recovery),
not a permission granted by ordinary report access.

CSV is UTF-8 with CRLF-compatible output, and is exactly the report's
table: one header row, then one row per record, with the same columns as the
XLSX table and the PDF
([#966](https://github.com/epiphany40223/parishkit/issues/966)). Report
details (parish, campaign, capture and request times, filters, counts and
notes) are in the XLSX "Report information" sheet and the PDF's header,
footer and report card, never repeated as CSV columns or rows; the CSV's
timestamps carry their UTC offset. The Ministry follow-up packet's CSV is
the one multi-section file: its report details and each Ministry's details
head the sections, as on the packet's XLSX sheets. Cells beginning with
formula-significant characters are neutralized, except canonical money
amounts (below). XLSX uses freeze panes,
filters, meaningful widths, types, repeated print headings, and no macros.
XLSX money is a number cell with the dollar format `"$"#,##0.00`, so staff can
sum it, and a negative amount shows as `-$50.00`. Each value is built exactly
from whole cents and stored as a spreadsheet number (an IEEE double), which is
accurate to the cent when opened in a spreadsheet. Unavailable money stays the word "Unavailable" and
an absent amount stays blank, never zero; for an amount beyond Excel's 15
significant digits the file's text stays exact (Excel rounds on open). CSV
money is the
canonical amount: a plain signed decimal with two places, such as `1234.50`
or `-50.00`, with no dollar sign, thousands separator or formula-guard
apostrophe, so spreadsheets and scripts read it as a number; unavailable
money stays the word and an absent amount stays blank (#388 L5). PDF money
is the page's text, such as `$1,234.50`. PDF
uses parish branding, report-request/data-as-of time, page numbers, repeated
table headings, and legible landscape layout where needed. The participation
chart's PDF is the exception for the request and as-of time: it carries them
in the file's metadata, not on the page (see
[Participation graph](#participation-graph)).
In every output format, including CSV, the report-request timestamp is
immutable across render retries; output labels must not describe it as the
wall-clock time of a later rendering attempt.

### File design

Every Admin portal PDF and XLSX shares one look (#926), in the portal's
colors (`web/design_tokens.py`): dark text on white or light tints, so files
print cleanly in grayscale, and color never the only signal.

PDF pages share one frame (`reports/pdf_design.py`): the parish and
campaign above the report title, and the capture time with its display time
zone on the right. Directory PDFs add
short count and filter lines under the title. The footer holds the privacy line, any Testing-mode note and "Page N
of M". Text is set in proportional DejaVu Sans with a DejaVu Serif title,
fonts matplotlib already bundles. Wrapping measures the fonts' glyph widths
and never drops a character of a value it draws, and characters the fonts
cannot draw become visible escapes. Inside the frame, the Family-code
directory is a zebra-striped table whose heading row repeats on every page,
with each phone number and each head's emails on its own line; each response
list and the Census changes list is the same table; the postal mail merge is a
grid of address cards. The [participation chart](#participation-graph)'s PDF
is one page with the chart in the frame's body, titled with the population
scope; like its picture it states no request or capture time. The
[Campaign comparison](../campaign-history/spec.md#downloads-and-sharing)
PDF is a summary (headline tiles, then full-width day-by-day charts that
flow onto following pages) followed by its table, whose type is set
smaller (`pdf_design.Table` scale) so its fifteen columns of figures fit. The
[Timeline export](#timeline-export)'s PDF is an infographic sheet
(`reports/timeline_pdf.py`): under the title, every page names the Family
in a heading of its own, larger than the detail lines (#1841), then a line
with its DUID, envelope number and any
[mode detail](#timeline-export). Page one shows one card per Summary line
in the page's order and words, the Family code's card set apart in the
warning tint with a short sensitive-data notice (or the page's words when no
code is shown), and one tile per kind of line (sign-ins and form opens,
emails, emails not sent, form progress, submissions) with its marker and
count. The timeline follows, oldest first, under the page's column headings
When, What happened and Details: lines are grouped under day headings, and
each shows its time, a marker on a vertical spine (a different shape per
kind, never color alone), its words in bold and its detail. Later pages
repeat the column headings, and a page that starts mid-day repeats that
day's heading marked "(continued)".

A field/value report (additional information, Ministry, financial, Family
test names, packets) prints one card per record. Which columns each format
carries follows the
[downloaded files rule](../ui-conventions/spec.md#downloaded-files): every
format the same columns, with internal references left out; a card shows
every column of its CSV and XLSX row. So that a page is easy to scan:

- The first card, "About this report", shows counts and amounts as large
  tiles, then the remaining details. It leaves out what the page frame
  already states (parish, campaign, capture time, privacy and Testing-mode
  lines). Filters and sort read as words ("Search: none · Sort: newest").
  The note on escaped characters appears only when the file contains one.
- Each record card is titled "Name (DUID N)" ("Name (New Member)" for a
  Member added on the form), with its disposition, state or status in a
  chip on the right. A packet's Ministry details card is titled by the
  Ministry's name alone, since no download shows a Ministry DUID
  ([table column order](../ui-conventions/spec.md#table-column-order)).
- The report card leaves out the digest-resolution note and the display
  and date-filter time zones (the page frame names the display time zone).
- Blank fields are left out. A packet is completed by hand, so its blank
  fields print as write-in rules instead.
- A workflow revision (a row with a Changed at time) nests under its item, headed by when and by whom it
  changed, without the item fields its row repeats; a row that continues a record (long financial share wording)
  joins that record's card.
- Two neighboring fields that each fit on one line share a line.
- A card that fits the space left on a page is never split; it moves to the
  next page. Only a card taller than a whole page splits, between lines, and
  each later piece repeats its title's first line marked "(continued)". A
  title too tall to leave room for a body line on a page is cut with an
  ellipsis. The report card
  and, in a packet, each Ministry's details card start a new page.

XLSX tables share one style (`reports/xlsx_design.py`): a bold white-on-teal
heading row as tall as its most-wrapped heading, Arial text, a frozen heading
row (and the identifying first column on Family-keyed sheets that start
with it: the Family-code directory, financial detail, additional
information, census changes and limitations; the postal mail
merge starts with the DUID and freezes only its
heading row), filters, light zebra banding as a
display rule, widths fitted to the content within limits with long text
wrapped, and a landscape print setup that fits one page wide, repeats the
heading row and numbers the pages. Label/value blocks ("Report information"
sheets and a packet's Ministry details) use one tinted label style; a
packet's Ministry detail values span the width of the member table below
them. A sheet about one subject (a packet's Ministry, the
[Timeline export](#timeline-export)'s Family) opens with title rows: the
same label/value style above its table, a blank row, then the table, whose
heading row stays the frozen and printed one, so a printed or forwarded
sheet still says whom it is about (#1841). Styling never changes a cell's
value or type. Every report workbook's document properties (Excel's File > Info) name its report as its
PDF's page title does and "ParishKit Stewardship" as its author. Its
creation time is a queued report's request time, or the download time of
Limitations and Census changes, as its PDF's is, so a retried rendering
states the same time; System logs, which has no PDF, also states its
download time. The
[past-campaign archive workbook](../campaign-history/spec.md#archive-workbook-format) is not a
report download and is not covered.

Charts follow the [chart conventions](../ui-conventions/spec.md#charts).

### Chart engine

Every Admin chart from the [response funnel](#response-funnel) on is one
Vega-Lite specification, built by a pure function of the report's query
result (`reports/chart_specs.py`) together with a plain-language summary and
a table of its exact values. The same JSON is drawn two ways, so the portal
and the emails cannot disagree (the
[participation graph](#participation-graph) predates the engine: only its
[Response dashboard](#response-dashboard) chart is drawn by it, and its files
and email image keep their own renderer):

- **In the browser**, `chart-v1.js` reads the specification embedded in the
  page and draws it as SVG with the vendored Vega runtime, within the
  [Content Security Policy](../architecture/spec.md#web-security-and-privacy):
  Vega runs in its interpreter mode (no `eval`), nothing injects a stylesheet
  (the tooltip's is served as a static file), there is no actions menu, and
  no asset comes from a third party; the runtime's data loader refuses every
  load, since a specification carries its data inline. A chart follows its
  panel's width and is measured again whenever the panel changes width after
  it was drawn; charts in a region refreshed in place are drawn again, keeping
  their space until they are; a chart in a closed disclosure is drawn when
  the disclosure opens, since its panel has no width before then
  ([#1161](https://github.com/epiphany40223/parishkit/issues/1161)). On an
  Admin page a chart is drawn in the page's
  [colour scheme](../ui-conventions/spec.md#colour-scheme), and drawn again
  in place when it changes. The
  Vega, Vega-Lite
  and vega-embed bundles are vendored under the static files, each pinned by
  SHA-256 with its version and source URL, with the license notices of
  everything they bundle (`reports/chart_assets.py`). vl-convert reports the
  same Vega and vega-embed releases and the same Vega-Lite minor version (no
  interface reports its patch release; the locked build embeds the vendored
  6.4.1), so the two renderers run the same code. A browser test on the component
  asserts that both charts render, take a pointer, open their table and
  follow a resize with zero policy violations or script errors.
- **On the server**, vl-convert-python renders the same specification to PNG
  (at twice its CSS size, for the report emails' `cid:` images) and SVG, without
  Node or a browser, and reports the image's CSS width and height. These are
  the engine's two formats; how a report page offers the downloads above is
  specified in a later increment (the [response dashboard](#response-dashboard)
  does not offer them yet). A report email's
  [participation charts block](../report-emails/spec.md#participation-charts-block)
  is the participation graph's renderer. The
  [Compared with past campaigns](../campaign-history/spec.md#digest-section)
  block's two charts are drawn by this renderer at the email's width (the
  axes and legend inside it) under the timeout name `digest_chart`, and
  retained as PNGs with the report's snapshot so a retry sends the same bytes. Each is
  an inline `cid:` image in the email's related part, never a `data:` URI,
  which Gmail does not show. A chart the renderer cannot draw fails the
  compile, which is retried; on the last attempt the report is compiled
  without any of these server-drawn charts and sent anyway, since its tables
  and text part carry every number they draw. That send retains no images,
  keeps its comparison block without charts, and its saved page draws none
  either, while one ERROR `task_failed` entry (no incident) names the chart and the
  attempt. A report email is never left unsent for a chart.
  Rendering happens only in workers and
  [export tasks](../background-processing/spec.md#exports-and-graph-rendering),
  never on a page view, and always in a short-lived helper process isolated
  like the mail helpers (no environment, no error text back): the worker
  kills it at its time limit and refuses an oversized specification or
  image. A kill leaves a `helper_timed_out`
  [durable timeout entry](../operations/spec.md#observability-and-health)
  naming the caller's timeout name (each caller adds its own to the
  reviewed list) and the helper `chart_render_worker`.

Each chart is accessible in every rendering. On a page the chart is a figure
whose view has the chart's name and is described by its summary, and is
followed by the collapsed exact-values table, a region named by the table's
caption. A chart that draws its values in the chart itself leaves that table
out ([#1266](https://github.com/epiphany40223/parishkit/issues/1266)) and does
not show its summary, which stays its view's description and shows only if
the chart cannot be drawn
([caption rule](../ui-conventions/spec.md#charts)); so does a chart that the
page's own table of the same values directly follows (the
[participation graph](#participation-graph)'s daily table). Its feature spec
says so (Campaign comparison's
[pledge bands](../campaign-history/spec.md#page-layout) are one): each bar, or
each band of a stacked bar, shows its figures (its count or dollars, and its
share where the chart gives one) on or after the mark or in its axis label, a
band too narrow for its label having them in the chart's legend instead, and
its tooltip and accessible
name carry the same figures. A pointer shows exact values in tooltips, and so
does a tap (`chart-v1.js` replays a tap on a chart as a pointer move, which is
what Vega's tooltips follow; a tap elsewhere closes the tooltip); the
view takes keyboard focus so a keyboard or screen-reader user hears its name
and summary, and gets the exact values from the table, or without one from
the marks' names, not the tooltips. A chart on a date, time or campaign-day
axis marks the inspected x value with the
[hover guide line](../ui-conventions/spec.md#charts) (`chart_specs.hover_guide`).
Series are told apart by dash pattern as
well as color. A layer with nothing to draw (no activity yet,
or no send to mark) is hidden from assistive technology rather than announced
as an unnamed graphic, and an activity chart with no activity still draws
its axes on explicit scales (its sends' span, or the last day by hour or
week by day, and a Families axis labelled only 0) rather than from empty
data, which Vega warns about on every draw
([#1327](https://github.com/epiphany40223/parishkit/issues/1327)); its
Families axis labels whole numbers only, never one twice. In an email the
PNG carries the summary as its alt text and the table follows it. Times on
an axis are the campaign's wall clock whatever time zone draws the chart, and
the axis names the campaign's zone.

## Population and calculation rules

"Active" means current promoted ParishSoft eligibility. Current participation
and financial cards/tables exclude Families that later became inactive.
Historical event views retain a submission made while the Family was eligible.

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
day** / **Current population** scope control. Historical scope includes every
Family first eligible on or before each day, so neither its denominator nor
cumulative participant count can shrink and its percentage cannot exceed 100%.
Current-population scope excludes currently inactive Families at every point.
The statistics cards have no inactive subtotal; the
[Response dashboard](#response-dashboard) ignores the retired `inactive`
query parameter so old bookmarks still load. Report email
parameters record whichever single population scope applies, so report
email parity never combines the controls.

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
The graph is stacked panels on that one shared date axis, top to bottom, each
named by its own title (so there is no legend):

- **First submissions that day:** bars of Families making their first live
  submission that day;
- **Cumulative participating Families:** a line of cumulative Families that
  have submitted under the selected population scope; and
- **Effective annual pledges,** when financial is enabled: a line of the
  effective annual pledge total at each day end, its ticks in short dollars
  ("$250K", "$1.6M").

By Administrator decision
([#720](https://github.com/epiphany40223/parishkit/issues/720), question 7)
there is no second y-axis. Two independently scaled axes made any two growing
totals draw almost the same curve, and a shared Families axis dwarfed the
daily bars under the running total. Every panel's axis is linear and starts at
zero. It is scaled to that panel's own values, with a top at the largest value
plus about 10% headroom, and its ticks step by round 1, 2 or 5 multiples. No
axis stretches to the eligible Family total, which would flatten the bars and
line; the eligible total is not drawn (the table's participation column still
states it per day). The date labels appear once, under the last panel.

The chart image draws no source snapshot, request time or other provenance
text. By Administrator decision
([#575](https://github.com/epiphany40223/parishkit/issues/575)) the file
carries that provenance as metadata instead: the PNG's `Description` text
chunk and the PDF's Info `Subject`, both holding the same "Source as of … Requested
… (time zone)" text, without the internal source generation or submission
cutoff numbers ([#966](https://github.com/epiphany40223/parishkit/issues/966)).
The XLSX export keeps it in its "Report information" sheet. The "Source as
of" and "Requested" times use the zone the page was drawn in (the
browser's), which the file names
([time zones](../ui-conventions/spec.md#downloaded-files)).

The participation CSV and XLSX exports carry one row per campaign date
under the same columns, in this order: Campaign date, Population, First
submissions, Families that have responded, Families in population,
Cumulative participation, Source as of, and Cumulative annual pledges (USD)
when financial is enabled. An unavailable value is blank in the CSV and
the word "Unavailable" in the XLSX, never zero. Neither
carries the campaign or fact-generation UUIDs, source generation numbers
or the submission cutoff
([#966](https://github.com/epiphany40223/parishkit/issues/966)); the PNG
and PDF are the chart, not a table.
A report email states a plain as-of line instead
([shared email layout](../report-emails/spec.md#shared-email-layout)),
and its chart PNG keeps the metadata. The PNG and PDF exports and the report
email draw this plot with matplotlib; the PDF sets it in the shared
report page frame ([file design](#file-design)), while the export and email
PNGs keep their own titles and layout. Each process draws one matplotlib file
at a time, because matplotlib's style settings are process-global; only
workers and export tasks draw one.

The [Response dashboard](#response-dashboard) draws the same design in the
browser with the [chart engine](#chart-engine)
([#720](https://github.com/epiphany40223/parishkit/issues/720)): the same
panels, titles, colors, zero-based axes and round ticks, each campaign date one
band of the shared date axis, and as many date labels under the last panel as
fit without overlapping, in the parish's compact date style as the exports'
ticks are. The page's daily table directly follows the chart and is its exact
values, so the chart adds no exact-values table of its own and does not show
its summary, which would only repeat that table. The table opens newest
first, five rows per page
([#1534](https://github.com/epiphany40223/parishkit/issues/1534),
[#1537](https://github.com/epiphany40223/parishkit/issues/1537)): `date_desc`
is the default sort, so an address without `sort` opens newest first, and a
saved link with `sort=date_asc` still opens oldest first; the exports keep
their dates ascending. Its rows-per-page choices are 5, 25, 50, 100, 250 and
All ([Admin tables](../ui-conventions/spec.md#admin-tables)). The default
sort and size are left out of the page's links. Its marks and axes are hidden
from assistive technology, since their automatic names would read raw values
(ISO dates, unrounded dollars) unlike the table's. There is no
page image, so a page view draws nothing on the server.

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
after every expected date validates; UI, accessible table, PNG/PDF, and
report email consumers never combine rows from different input generations.

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

Exports pin their inputs when requested; report emails pin theirs when the
occurrence first records its report inputs, using its stored as-of boundary
(the end of its report day, or its capture instant;
[as of](../report-emails/spec.md#as-of)). These requests
bypass ordinary debounce and receive priority exact-generation work. They reuse
an existing ready/building generation with the same key and never chase later
submissions, including on retry. Pinning protects the required input records
before the build starts. Priority affects claim order, not preemption of a
running build, and an exact request never consumes newer interactive demand.

If the exact current input generation is not ready, the interactive report shows
the last complete generation only together with a conspicuous, non-blocking
**Updating** notice saying that the chart may not yet include the newest data;
it never labels stale facts current. By Administrator decision
([#575](https://github.com/epiphany40223/parishkit/issues/575)), the
Response dashboard's chart block shows no "data as of", campaign time zone or
technical-details header, and no instruction line above the chart; the
chart's as-of values are in the chart file's metadata and in its exports, and
the x-axis label names the campaign time zone. While the page is open it
follows the queued generation and shows it by itself once it is ready
([pages that update themselves](../ui-conventions/spec.md#pages-that-update-themselves),
[#1586](https://github.com/epiphany40223/parishkit/issues/1586)), so the user
never refreshes the page to check. That read records the dashboard's views
like any other (`response_dashboard_viewed` and `participation_viewed`),
each marked `automatic: true` in its audit context so it reads as an
automatic refresh, not as the reader opening the page. A pinned export or
report email waits/retries for its exact generation and fails visibly rather than
substituting a different cutoff.
Recalculation from the pinned source/submission inputs must reproduce every
stored fact, and a verification job detects drift.

Superseded, unpinned calculated generations are automatically compacted under
[derived fact retention](../data/spec.md#derived-fact-retention). Current,
pinned, building/recoverable, and actively consumed generations stay protected.
Report selection, pinning, rendering, and drift verification use that policy's
atomic reference/read guards; no consumer assumes an unpinned old generation
will remain available indefinitely.

The UI defaults to Historical as of day and offers a clearly labeled scope
toggle. Changing scope updates every series together. On the Response
dashboard, pointing at or tapping anywhere in a date's column, in any panel, shows
that date's tooltip, and a faint guide line crosses every panel at that date
([hover guide line](../ui-conventions/spec.md#charts)). The tooltip has the
local date as its heading, then "First submissions",
"Families that have responded" (out of the scoped population, with
percentage) and, when financial is enabled, "Effective annual pledges", with no
explanatory text. A keyboard or screen-reader user gets every date's figures
from the daily table ([chart engine](#chart-engine)). A report email's saved
copy shows its retained chart image instead: pointing at or tapping a date, or
choosing it with the date slider, draws the guide line over the image at that
date and shows a short tooltip: the local date as a
small heading, then "Families" (cumulative count), "New today" (daily count)
and, when financial is enabled, "Pledges", aligned on the colon. On a narrow
screen that tooltip sits under the chart in reserved space, so the slider does
not move; the screen-reader live text and the slider's value text keep the
fuller form: scope, local date, daily count, cumulative count out of the scoped
population with percentage, and exact pledge. Either tooltip's pledge is whole
dollars (halves round up, such as $760,410); everything else keeps exact
cents. Report URLs, pinned report email inputs, equivalent data
tables, PNG/PDF output, and structured downloads record the scope and exactly
match the displayed values. A scope note explains why the historical endpoint
may differ from current-statistics cards.
On the Response dashboard the toggle is the Chart population
[binary choice](../ui-conventions/spec.md#binary-choices), Historical as of
day | Current population, with no Apply button
([#1535](https://github.com/epiphany40223/parishkit/issues/1535)), directly
under the Daily participation panel's heading, since it changes only the
daily chart, its table and the export
([#1534](https://github.com/epiphany40223/parishkit/issues/1534)): each
segment links to the page with that scope, keeping the mode, the daily
table's sort and page size and the browser's time zone (its page returns to
the first), and choosing one refreshes the page in place.

## Campaign statistics

**Access:** Admin and Staff.

Cards show:

- active Families and active Members;
- active Families with eligible email out of active Families and percentage;
- active Families with deliverable email out of active Families and percentage;
- active Families with first live response out of active Families and
  percentage;
- effective campaign annual pledge total, when enabled; and
- "Last year's pledges (all Families)": every Family-linked ParishSoft pledge
  record in the mapped comparison funds and period (pledges with no Family are
  not stored), across all Families in the source
  snapshot whatever their status, when available. Every record counts; there
  is no de-duplication. It is the only comparison figure: there is no
  per-population comparison subtotal, and only this one aggregate is captured,
  never any Family's own pledges.

Giving cards include mapped funds/period and source as-of. Missing/incomplete
source is never shown as zero: it reads Unavailable, or, in a tile, "—".

On the [Response dashboard](#response-dashboard) the cards are the first row
of its Summary panel's [stat tiles](../ui-conventions/spec.md#stat-tiles)
([#720](https://github.com/epiphany40223/parishkit/issues/720),
[#1537](https://github.com/epiphany40223/parishkit/issues/1537)): Active
Families, Active Members and, with giving enabled, Current annual pledges and
Last year's pledges (all Families). The responded, eligible email and
deliverable email counts are not on the page: the Summary's
[Families by stage](#funnel-stages) row shows the responses, so the
"Responses and email reach" chart that drew those three counts was removed
as redundant (Administrator, 2026-10-18), and `pk-admin report participation`
still reports all three. Without an observation every tile reads "—", with a
toggletip saying no ParishSoft data has been loaded yet. A pledge total that
cannot be added up reads "—" the same way, with the reason for its cause:
no giving definition for the campaign, an answer whose amount is not known
(current annual pledges), or a snapshot that does not hold every pledge for
last year's period (last year's pledges). No reason promises that a later
refresh will fix it. Under the tiles one line, "Data as of" the
moment the statistics were counted, shows that time in the browser's own
zone, with no "UTC" label
([browser-local times](../ui-conventions/spec.md#dates-and-times),
[#1537](https://github.com/epiphany40223/parishkit/issues/1537)); the panel
shows no other time, no pledge periods or funds, and no technical details
(source generation, submission cutoff), which the
[command line](../admin-automation/spec.md) still reports.
Choosing a Chart population redraws the chart in place, keeping its space until
it is drawn.

## Response funnel

**Access:** Admin and Staff (`CAMPAIGN_REPORT`).

The response funnel counts distinct Families per campaign and mode at an
explicit **as-of** cutoff. Production is the default and reads live responses
and Production mail; a Testing funnel is one rehearsal epoch's responses and
that rehearsal's mail. Every count comes from a durable timestamp that no
later event can change, so the same campaign, mode and as-of instant give the
same numbers for as long as the evidence is retained (Production-transition
cleanup removes a rehearsal's records, and the campaign purge the campaign's),
as [daily email report parity](#daily-email-report-parity) requires. A cutoff
of "now" can miss rows committing at that moment; a report meant to be
reproduced uses a cutoff in the past, as report emails as of the end of the previous day do. Mail
evidence is bounded by the as-of instant alone, across every Production cycle
of the campaign, so a withdrawal and re-activation after the cutoff cannot
change an earlier reading; in Testing it is the rehearsal epoch's: that
epoch's messages, and the occurrences and skips that fell within the epoch's
lifetime. The funnel never reads Family session rows, which the
[session policy](../architecture/spec.md#identity-and-session-security)
deletes, and reports no value that later activity rewrites, such as the
furthest form step reached or a Family's current eligibility; those belong to
live views. The query returns one row per Family of the campaign with each
instant as it stood at the cutoff, so the totals, the series below and the
lists of Families behind any count all come from the same rows. It is the
installed SQL function `stewardship_family_response_v1` (migration 0050,
[#933](https://github.com/epiphany40223/parishkit/issues/933)), which the
dashboard, report emails and the
[directory's Response filter](#response-filters) all read, so their counts
cannot drift apart. The
[Response dashboard](#response-dashboard) shows its stages as stat tiles
(no funnel chart is drawn anywhere, neither on the page nor in report
emails; the daily report dropped its chart in
[#1370](https://github.com/epiphany40223/parishkit/issues/1370)),
`pk-admin report responses` reports the activity series, and the
directory's [Response filters](#response-filters) list the Families behind
the counts; the
[Timeline](#timeline) shows what happened with one Family; and
a Production report email's
[response funnel block](../report-emails/spec.md#response-funnel-block)
states the funnel at the report's as-of moment (the end of its report day,
or its capture instant).

### Funnel stages

A Family counts once in a stage when, by the as-of instant:

- **Invited:** an `initial`
  [outbox message](../data/spec.md#job-outbox-audit-and-purge-records) to it
  in the mode was delivered (its finished instant).
- **Link followed:** the
  [Family engagement record](../data/spec.md#family-engagement)'s
  `first_link_at`. Wherever this stage is shown it is labelled "Includes
  mail-scanner prefetches": a scanner that follows the personal link signs in
  exactly as the Family would.
- **Form opened:** the engagement record's `first_form_at`, which the
  backfill filled from live form baselines, or its `first_progress_at` or the
  first submission if either is earlier or the record has no form open.
- **Progressed past the first step:** the engagement record's
  `first_progress_at`, or the first submission if that is earlier or the
  record has none.
- **Submitted:** its first [Submission](../data/spec.md#submission) in the
  mode was submitted.

A submission is made from the form and passes every step of it, so a Family
that submitted had opened the form and progressed past the first step, even
when the engagement record does not say so: progress was not recorded before
the record's release (1.2.0), so Families that submitted earlier have none,
and a form open can go unrecorded. Progress is made on the form too (a
presence heartbeat can record it without a form open), so it implies Form
opened. Form opened, Progressed and Submitted are therefore nested, each at
most the one before it. Link followed stays as
recorded: a Family can sign in by typing its code instead of following its
link, so a submission says nothing about the link.

Each stage's share ("Compared with invited") is of the Invited count and can
exceed 100%: the stages are not all nested, so a Family can follow its link
or submit without a delivered invitation.

Three figures are reported beside the funnel, not as stages of it:

- **Skipped: already responded:** Families whose planned invitation was later
  skipped because they had already responded (the occurrence reason
  `family_responded`), dated by the immutable occurrence transition. A Family
  that responded before any invitation was planned has no occurrence to skip
  and is not counted here.
- **Submitted without a delivered invitation:** Families that had submitted
  with no delivered invitation by the as-of instant, whether their invitation
  was skipped or never planned.
- **Submitted more than once:** Families with more than one submission in the
  mode by the as-of instant.

Stages are not all nested: a Family may submit without a delivered invitation,
and a Family whose planned invitation was skipped because it had responded is
counted as submitted and as skipped rather than as invited.

### Response activity over time

The activity series buckets the same first instants, link followed, form
opened (as the funnel counts it, so including the first submission) and
submitted, by campaign-local hour or day, using the Campaign's
immutable timezone snapshot as the [participation graph](#participation-graph)
does; a repeated autumn hour is two buckets. Over every bucket each series
sums to its funnel total. The chart draws each quiet bucket between the first
and the last busy one as zero, so a line never suggests activity across a
silent hour or day; its exact-values table lists only the busy buckets. Send
markers name each invitation and reminder send
of the mode that had planned an email by the as-of instant (a *send* as the
[Family email history](../admin-portal/spec.md#family-email-sends) page defines
and names it: one revision of one Family schedule in one mode and Production
cycle, each keyed by its own occurrences' cycle) with its scheduled time and
how many of its emails were delivered by then, with the first and last
delivery instants. A marker's key, scheduled time and counts are
reproducible; its display name ("Reminder 2") follows the schedules as they
stand when the report is read, as on the sends page, so adding, removing or
moving a reminder later can rename an earlier reading's markers. In Testing, a
marker counts occurrences planned during the rehearsal epoch; after a go-live
is cancelled mid-cleanup, a new epoch can reuse the previous epoch's Testing
occurrences, so its emails count as invited without appearing in a marker. Interactive and emailed renderings of the series use this
one result. No Admin page draws it since
[#1537](https://github.com/epiphany40223/parishkit/issues/1537), which
retired the activity chart; `pk-admin report responses` reports its buckets
and send markers at the hour or day grain (`--grain`; without it, hourly
while everything it shows lies within three days of the cutoff, and daily
after that).

### Response dashboard

**Access:** Admin and Staff (`CAMPAIGN_REPORT`); the Testing view is Admin
only.

`/admin/reports/responses/` is the campaign's one response report
([#1537](https://github.com/epiphany40223/parishkit/issues/1537), which
merged Participation into it; `/admin/reports/participation/` is gone, with
no redirect, per [#864](https://github.com/epiphany40223/parishkit/issues/864)).
Data comes first; the explanation sits in the
[About this page](../ui-conventions/spec.md#page-help) panel after the
heading, and the page sends `Cache-Control: no-store`. Top to bottom:

1. The heading and a line of links to the other campaign reports (no
   self-link).
2. **Responses shown**, Production | Testing rehearsal, a
   [binary choice](../ui-conventions/spec.md#binary-choices) offered to
   Administrators only.
3. **Summary**: one panel of
   [stat tiles](../ui-conventions/spec.md#stat-tiles) in two rows
   (Administrator, 2026-10-18,
   [#1537](https://github.com/epiphany40223/parishkit/issues/1537)), ending
   with one "Data as of" line.
   - **Active population** (a Production view only): Active Families,
     Active Members, Current annual pledges and Last year's pledges (all
     Families), the last two with giving enabled
     ([campaign statistics](#campaign-statistics)). "Data as of" is the
     moment these were counted.
   - **Families by stage**: Invited, Link followed, Form opened, Progressed
     past the first step and Submitted, a tile per
     [funnel stage](#funnel-stages) with its count, its share compared with
     Invited and a share bar drawing that share
     ([#720](https://github.com/epiphany40223/parishkit/issues/720)), counted
     at the database's current instant; Link followed's tile says it
     includes mail-scanner prefetches. Invited's own bar is full, as the
     reference the others are read against, and a share past 100% (a Family
     can follow its link without a delivered invitation) fills its bar.
     Until an invitation has been delivered the tiles leave out their shares
     and bars and the panel says so in one sentence. The three figures
     reported beside the funnel are not shown (`pk-admin report responses`
     still reports them).

   The page draws no funnel chart and no "Responses and email reach" chart:
   the Families by stage tiles give the funnel's counts and shares and draw
   its bars, and the responses that chart drew are the Submitted tile, so
   both were removed as redundant (`pk-admin report participation` still
   reports the email reach counts). The emailed reports have drawn no
   funnel chart since
   [#1370](https://github.com/epiphany40223/parishkit/issues/1370), so no
   funnel chart remains anywhere; the Administrator can ask for one back.
4. **Daily participation**: the Chart population choice, the daily chart
   and its table ([participation graph](#participation-graph)). Before the
   chart has a day, the panel says "No campaign dates to show yet. The daily
   table starts on the Campaign start date and gains a row each day."
   ([empty tables](../ui-conventions/spec.md#empty-tables-and-lists),
   [#1828](https://github.com/epiphany40223/parishkit/issues/1828)).
5. **Export**: the export buttons **Export XLSX**, **Export CSV** and
   **Export PDF**, with no sentence on what they export
   ([export actions](../ui-conventions/spec.md#downloaded-files)), which
   export every daily row of the chart shown
   ([file design](#file-design); the statistics and the funnel join the
   file in [#1557](https://github.com/epiphany40223/parishkit/issues/1557)). While no complete chart has
   been calculated, or other campaign work holds new exports, the buttons are
   shown unavailable with the sentence that says why
   ([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)). The
   latest-data export is requested with `pk-admin export exact create`; the
   page no longer offers it.

The page shows counts only, never a Family's name or identifier; the
directory's [Response filters](#response-filters) list the Families behind
each count. The Lists of Families panel and the activity chart that the
earlier Response dashboard showed are gone
([#1537](https://github.com/epiphany40223/parishkit/issues/1537)); the lists'
lengths and the activity series stay in `pk-admin report responses`.

The URL carries only closed choices and the browser's zone. `mode` is
`production` (the default) or `testing`: Production reads live responses and
Production mail; Testing reads the campaign's active rehearsal epoch, is
labelled as Testing, and is offered and admitted for Administrators only,
since Testing responses appear only in explicit Admin testing views (see
[population and calculation rules](#population-and-calculation-rules)). The
statistics, the daily chart and the export count Production responses only,
so a Testing view shows the Summary's Families by stage row only and,
instead of the other rows and panels, one line: "Population statistics, daily participation and the
export count Production responses only. Choose Production to see them."
([conditional fields](../ui-conventions/spec.md#conditional-fields)). With
no active rehearsal, the Testing view says there is nothing to show. `scope`,
`page`, `sort` and `size` are the daily chart's and table's; `timezone` is
the browser's ([participation graph](#participation-graph)). The removed
activity chart's `grain` is still accepted, and ignored, so old bookmarks
load. Every choice is an
[in-place control](../ui-conventions/spec.md#in-place-controls): no reload,
the reader's scroll position and focus on the chosen link kept, the charts
drawn again, and the address replaced (so Back does not step through the
choices). The Admin portal
[requires JavaScript](../ui-conventions/spec.md#javascript-requirement), so
there is no no-script form.

Each view reads the funnel once, and a Production view the statistics and
the daily chart's facts, all inside the report's one
[campaign read guard](../data/spec.md#campaign-read-guards) with its role
recheck. When the statistics or the daily facts cannot be read from their
inputs, the Production view still shows the Summary's Families by stage
row and, instead of the other rows and panels, one line saying they are unavailable right
now, with no zero assumed, and that they appear by themselves; a database or
guard failure still answers the report's unavailable page. The line never
tells the reader to reload the page
([pages that update themselves](../ui-conventions/spec.md#pages-that-update-themselves),
[#1586](https://github.com/epiphany40223/parishkit/issues/1586)): the
page's figures status stays beside it and keeps watching, through the same
passive, unaudited status read as the **Updating** notice, at a fixed
60-second pace and with no Updating notice of its own (its address carries
`retry=1`, the only value `retry` takes, and only for the dashboard; any
other is refused with 400). Once a generation other than the newest one
when the page was drawn is ready (any ready one, when there was none), the
page re-reads itself once, as it does for newer figures. So when only the
ParishSoft data is missing ("They appear here by themselves after the
first ParishSoft update completes"), the page waits for the generation that
update builds; when the daily facts could not be read (a busy generation is
already passed over for the next candidate, so what is left seldom clears
by itself), the page waits for a different generation too, rather than
re-reading itself, and recording an automatic view, about once a minute
while nothing has changed. A view records `response_dashboard_viewed` with
its outcome, and a Production view also `participation_viewed`, started and
then succeeded or failed (failed, too, when its panels were unavailable);
neither copies a reported value. The browser draws the charts; a
page view never renders an image on the server.

### Response lists

The five response list pages (`/admin/reports/responses/<list>/` and their
`csv/` downloads: Families that submitted, started but did not submit,
invited but never opened, submitted more than once, and ParishSoft data to
check) were removed
([#933](https://github.com/epiphany40223/parishkit/issues/933)). The
[active parishioner family directory's Response filters](#response-filters)
are the only way to list those Families, in Production and (for
Administrators) Testing, with Family codes, contact details and the queued
CSV, XLSX and PDF export (the Response dashboard's Lists of Families links
into them were removed with that panel,
[#1537](https://github.com/epiphany40223/parishkit/issues/1537)). Per the Administrator's rule against legacy
addresses ([#864](https://github.com/epiphany40223/parishkit/issues/864)),
the old addresses are not redirected or aliased: they are 404.

The `submitted` and `more-than-once` lists' **Entered by Staff** column (how
many of a Family's submissions Staff entered through
[Open form](#open-form)) is intentionally dropped with them: the
directory marks a Family "(entered by Staff)" only when Staff entered its
current response, and the Family's [Timeline](#timeline) shows each response Staff entered.

Their audit event types (`response_<list>_list_viewed` and
`response_<list>_list_exported`) and closed context values (`report_mode`,
`report_filter` with the lists' Show keys, `search_used`, `snapshot_id` and
the `report_sort` column tokens,
[#556](https://github.com/epiphany40223/parishkit/issues/556),
[#851](https://github.com/epiphany40223/parishkit/issues/851)) stay in the
audit vocabulary and in System logs' wording, so earlier entries still
validate and read; nothing records them now.

### Timeline

**Access:** Administrators see the full timeline; Staff see the summary only
(`CAMPAIGN_REPORT`; the Administrator's decision of 2026-10-04 on
[#523](https://github.com/epiphany40223/parishkit/issues/523)). Ministry
leaders have no access.

`/admin/reports/families/<family>/` (**Timeline**) answers "what happened
with this Family?" and "did you get my response?" for one Family of the
current campaign. It replaced the Family timeline page
([#1570](https://github.com/epiphany40223/parishkit/issues/1570)) at the same
address, so nothing is redirected or aliased
([#864](https://github.com/epiphany40223/parishkit/issues/864)); the name
"Family timeline" is retired from the page, its navigation and its help.
People's own timelines (Administrators, Staff and Ministry leaders, who may
also be ParishSoft Family members) are planned on the same page design in a
later slice of #1570; until then the page has only the Family subject.

`<family>` is the Family's campaign record id, an opaque random identifier
like a Mail message's; the Family's name, DUID, envelope number and code never
enter the URL. The page is the Family's
[information hub](../admin-portal/spec.md#family-information-hub): a header
strip, then tabs, the first of which (Timeline) holds everything described
here. The heading is the Family's name, but the browser title is "Timeline"
alone, so browser history does not keep the name. Every
Admin table that names a Family links the name to this page through one
shared helper (the `{% family_link %}` template tag), drawn only for roles
that may open the page (the shared rule is in
[Family and Member names](../ui-conventions/spec.md#family-and-member-names)):
the [active parishioner family directory](#active-parishioner-family-directory)
(each row), the header's Find a Family box (each match; see
[Admin navigation](../admin-portal/spec.md#admin-navigation)), the
[Financial stewardship](#financial-stewardship-detail) Family table, Census
changes, Publish to ParishSoft's newest check, Ministry requests, both
Limitations tables, Families on the form now, the Additional information
request page's heading, each Weekly report item, Refused addresses and each
Refused address page, and Families the form cannot open. The
names stay unlinked where the page must not open a Timeline: the Additional
information queue (each name is already the link to its request, and the
request page links the Timeline), Go-live's Testing submissions and the
chosen-Family test send (Testing only), and the examples of a refused
ParishSoft load. Sent mail's Recipient stays the button that lists
everything that recipient was sent, in place
([#1571](https://github.com/epiphany40223/parishkit/issues/1571); see
[Sent mail](../admin-portal/spec.md#sent-mail)), so it is not a Timeline
link, and the Mail message page follows
[#1563](https://github.com/epiphany40223/parishkit/issues/1563). Its breadcrumb
parent is the active parishioner family directory. The page reads the
Family's records at the database's current instant (**Counted at**).

Data comes first: the heading with the Family's name (the directory's
surname-and-heads name from the latest ParishSoft data; "Timeline" alone when
that data no longer has the Family, whose line then starts with the shared
[Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
badge), a line with its DUID and envelope number (and the hub's
[Last portal action](../admin-portal/spec.md#hub-header-strip), for both
roles), with no link to the directory (the Admin menu has it), a line with
the campaign's name and Counted at, then, in the Timeline tab, a summary that both roles see:

- **Submitted:** Yes, with the first submission's time and, after more than
  one, how many and the latest, noting any that Staff entered for the Family
  through [Open form](#open-form); or "Not yet".
- **Last email:** the last email sent to the Family (Invitation, Reminder N,
  Submission receipt or a chosen-Family test email), planned last among those
  not cancelled (a cancelled email was never sent), with its time and outcome
  in plain words: Delivered (the mail service accepted it, which does not
  prove it reached the inbox), Failed, Not sure it arrived, or Still sending.
  No email content is shown. Sent mail, its state filter and the Mail
  message page name each email's state in the same words, from one shared
  table, adding where a Still sending email is (queued, waiting to retry, or
  being handed to the mail service) and Not sent (cancelled). The Admin
  status bar, the pause and resume pages, Family email progress and sends,
  and the test-email results use the same words.
- **Family code**, for roles that may see Family codes (`FAMILY_CODES`; the
  code is neither decrypted nor shown otherwise), as the directory shows it,
  with **Open form** (described below), as the directory has. Unlike the
  directory, the page shows no notice beside it (the Administrator's choice
  on #590). Open form is a
  disabled button with its reason beside it while the system is in Testing
  mode (the Family sign-in then accepts only rehearsal codes), when the
  Family has no code for the campaign, when the campaign is not the current
  one, or when ParishSoft no longer lists the Family as active and
  registered (#1331), the refusals Open form's hand-off makes (below). In
  Testing mode the reason links "Try
  the Family form as a chosen Family" for roles that may open that page
  (Administrators).

While the Family is in the campaign's No-reminders WorkGroup, both roles see
"Reminders skipped: in ParishSoft WorkGroup" with its name: the WorkGroup
whose Families are skipped now, which is the previous one while a rename
waits for a refresh (#1305); while the named WorkGroup is missing in
ParishSoft and the earlier set is kept, it is the configured name.
Administrators also see whether campaign email can reach the Family (or why
not), the furthest form step reached and when the Family was last seen on the
form (live values of the
[Family engagement record](../data/spec.md#family-engagement)), and the
timeline table described next.

**Records shown.** The page shows Production records only: there is no
Production or Testing control (the Administrator's decision on #1570), and a
`mode` in the address is refused as an invalid filter (400). While a
rehearsal is active, an Administrator sees Testing responses with
Responses shown set to Testing rehearsal on the
[response dashboard](#response-dashboard) and, per Family, on the
[Active parishioner family directory](#response-filters). Emails,
submissions, form opens and the engagement record carry their mode. A
sign-in is the durable `family_login` audit event, which
carries no mode, so it is shown only when the system was in Production at
that instant (from the runtime mode transitions); a sign-out or session end
belongs to the Family session its sign-in started, so it is shown exactly
when that sign-in is.

**Timeline table.** A [shared Admin table](../ui-conventions/spec.md#admin-tables)
with one row per line and the columns When, What happened and Details. It
is newest first (the Administrator's decision on #590); When is its one sort
heading, which reverses the order in place. Rows at the same instant are
listed in the order the Family took them in, reversed with the rest newest
first, so each line still reads as happening when it did in both
directions. A Family has few lines, so the table
shows them all, with no paging or row navigator. Its lines are:

- each Family email with its outcome (including Not sent (cancelled)),
  linked to its Mail message page;
- the invitation skipped because the Family had already responded (the
  [funnel's](#funnel-stages) skip), which stands in for that invitation's
  cancelled email, so the one planned email is listed once; likewise each
  reminder not sent because the Family is in the campaign's
  [No-reminders WorkGroup](../background-processing/spec.md#family-invitations-and-reminders)
  (#861);
- each sign-in, labelled as possibly a mail scanner checking the link,
  except one Staff started through [Open form](#open-form), which reads
  "Staff opened the form" and never names who
  ([#795](https://github.com/epiphany40223/parishkit/issues/795));
- each **Signed out** (the Family chose Sign out, `family_logout`) and
  **Session ended** (the system ended the Family's form session, for
  example after a submission or when the mode or the campaign changed,
  `family_session_ended`); a session that simply timed out records
  neither. A new sign-in in the same browser ends the earlier session in
  the same transaction, so that end has the sign-in's instant: it reads
  **Earlier session ended** (replaced by a new sign-in) and is listed as
  happening just before that sign-in in both sort directions (below it
  newest first, above it oldest first), so it never reads as the new
  session ending;
- each form open, getting past the first step and the furthest step
  reached;
- each submission, noting when Staff entered it for the Family and when no
  receipt was sent because the Family had no email address.

Staff get none of these lines. For a Staff view the server reads only the
sign-ins, session ends and engagement record behind the
[Last portal action](../admin-portal/spec.md#hub-header-strip), and keeps
only the lines that can be it.

**Filters.** One compact filter bar above the table (it wraps on a phone)
narrows the lines; it is an
[in-place control](../ui-conventions/spec.md#in-place-controls) of the
page's one table region, and the Summary is not filtered. Its choices are
closed values in the address (`kind`, `start`, `end` and the browser's
`zone`), so a reload or bookmark keeps them, and the When sort keeps them
too:

- **Kind:** All (the default), Family form activity (form opens, progress
  and submissions), Emails (every email line and the not-sent lines), or
  Sign-ins and sign-outs (sign-ins, Staff opened the form, Signed out,
  Session ended and Earlier session ended). A person's timeline will add
  Admin actions and Role changes; a Family's shows only the kinds it can have
  ([conditional fields](../ui-conventions/spec.md#conditional-fields)).
- **From** and **Through:** whole days in the browser's time zone, as on
  System logs, placed as UTC bounds on the server (so a daylight-saving day
  is 23 or 25 hours); either may be blank. A From after Through, or a day
  without the browser's zone, is refused (400).

With every line filtered out, the table says so and names the way back
(choose All and clear the days). Every time is shown in the browser's time
zone.

**Audit and reads.** Each view admits the report read, reads the Family
under the campaign read guard with the role recheck (a Family of another
campaign is refused like a missing one), decrypts the code under the
credential key-set lock as the directory does, sends `Cache-Control:
no-store` and records one `family_timeline_viewed` audit event ("Someone
opened a Family's timeline") whose subject is the Family's opaque campaign
record id, with the campaign and the outcome and no name, DUID, code,
filter or shown value, as a Mail message view names its message. A Family
that is not found in the campaign (an unknown id, or another campaign's
Family) is refused without an audit row, as a Mail message that is not found
is, so no row pairs a campaign with a Family that is not in it. Recording
which Family was viewed lets a later review see who looked at whom, and a
view left unrecorded could never be filled in; the Administrator confirmed
it on 2026-10-05 ([#477](https://github.com/epiphany40223/parishkit/issues/477)).
The subject is a soft reference with no foreign key, so, like other report
access audit, the event is outside the purge inventory (see
[audit records](../data/spec.md#job-outbox-audit-and-purge-records)). The
reads are a handful of small statements and none runs per row: the emails,
form opens and submissions through their Family indexes, the skips through
the campaign's invitation schedules, and the sign-ins, sign-outs and session
ends in one read through the audit's event-type and time index, which scans
the campaign's Family session events rather than one Family's. The filters
narrow the lines in memory, so they add no statement.

#### Open form

**Open form** lets Staff fill in the form for a Family over the phone
([#529](https://github.com/epiphany40223/parishkit/issues/529)). It is a
CSRF-protected POST, from the [timeline](#timeline) or the directory, for
roles that may see Family codes; it is refused, with one plain page that
gives no reason, unless the system is in Production and the Family is in the
current campaign with a code. A signed-in Admin's refusal is audited as a
denied `family_form_opened` (the Admin and the Family asked for, no reason).
The POST carries the address of the page it was clicked on as `next`, and
Back from the new tab returns there: only the Active parishioner family
directory (with its query, so its filters, sort and page survive) or this
Family's own timeline is accepted; anything else, including another host,
falls back to the timeline, so `next` is never an open redirect. It stores a
single-use hand-off in Valkey for one minute, keyed by the SHA-256 of a fresh
256-bit secret and bound to the signed-in Admin and their session, records a
`family_form_opened` audit event (the Admin, the Family's opaque record id,
the campaign; never the code or the secret), and opens a new tab that posts
the secret straight to the Family form. The secret and the code never appear
in a URL, log or audit. The Family form takes the hand-off with one atomic
read-and-delete, so it cannot be replayed to start a session later, checks
that the Admin's session is still live and that the Admin may still see
Family codes, and then signs the tab in as the Family through the ordinary
code sign-in, recording `family_assisted_login` (the Admin and the new
Family session). Every refusal shows the same page, and a used or expired
hand-off counts against the browser's address like a wrong code. A response
submitted in that session records the Admin in its `entered_by_id` (frozen
migration 0065). The Admin's session and role are checked only when the
hand-off is redeemed: if they end later while the Family tab is open, the
Family session continues until its own timeout, like any Family session, and
a response submitted in it is still marked as entered by Staff. Pages and
lists say only "Entered by Staff", never who: who opened the form is in the
System logs. The receipt email is the ordinary one. No Family code or link is
created, replaced or cancelled, so emailed codes and links keep working.

#### Timeline export

The Administrator's full view offers **Export this timeline** (the shared
[export buttons](../ui-conventions/spec.md#downloaded-files) for XLSX, CSV
and PDF, in the browser's time zone), the shared export lifecycle's
`family_timeline` kind (ADM-11 PR 8g, frozen migration 0063, unchanged by
#1570). Its form posts to `reports/families/<family>/exports/`, which only
an Administrator may use, as only an Administrator sees the full timeline;
Staff get no form, and their post is denied. The form carries the format,
the browser's time zone and a one-time request key, and nothing else; the
export is always of Production records (the capture's `mode` is
`production`), and old Testing exports keep their status pages, whose
breadcrumb opens the timeline. The file holds every line of the timeline,
whatever the page's Kind and day filters show, and keeps the title "Family
timeline", since it is always about one Family. The file can hold the
Family's live code, so creating it is audited like the Active parishioner
family directory's exports. The request captures what the page shows,
through the page's own reads, inside the campaign's export lock: the
Family's name, DUID, envelope number, email reach and the Reminder WorkGroup
holding it now (the page's Reminders line), the mode, the page's Summary
(submitted and when, the last email, the furthest step and the last time the
Family was seen) and every timeline line with its time, words and detail,
into an immutable `stewardship_timeline_export_snapshot`. The file follows
its page
([Downloaded files](../ui-conventions/spec.md#downloaded-files)): its table
is the page's Timeline table, When, What happened and Details, with the lines
oldest first; the Family's identity line and the Summary panel are its report
information, with the page's words in the page's order (Family, Family DUID,
Envelope number, the mode, Counted at, Submitted, Last email, Family code,
Reminders, Campaign email can reach this Family, Furthest step reached, Last
seen). The mode detail is there only for a Testing capture; a Production
file states no mode, as the directory exports (#1841). The report
information also names the campaign, from the request's configuration, so
the PDF's frame shows it above the title as every Admin PDF does. The XLSX's
Timeline sheet opens with [title rows](#file-design): Family, Family code,
Campaign and Counted at, in the report information's words and time zone,
then the table. For a Family the data no longer has, Family is blank and a
ParishSoft status detail right after it reads
[Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft),
which the PDF's Family heading and the XLSX's Family title row show in
place of the name, as the
page's heading shows its badge. As for every shared export, the CSV is the
table alone; the XLSX's Report information sheet and the PDF's summary cards
carry the rest, including the code. The PDF is an infographic sheet
([File design](#file-design)); its Sign-ins and form opens kind counts the
sign-out and session-end lines too. The page builds its timeline in Python,
so the web supplies that document; the capture trigger admits it only from
the web login, for a current Administrator, an admitted campaign, the active
configuration and a Family of that campaign (with its DUID), with the
document's keys and value types checked and its size bounded, and sets the
event count itself. The Family code is never captured: the worker decrypts it
into the file, under the key-set lock, only when the requester may see
Family codes. The status page, download, Retry, Cancel and the short
download window are the shared
[point-in-time](../operations/spec.md#point-in-time-exports) lifecycle's.
The request records `export_requested` with its line count, and the download
`export_downloaded`, never a name, code or line of text. The command line's
[`export family-timeline`](../admin-automation/spec.md#reports-and-exports)
requests the same export; its `--mode testing` is refused with a plain
error, since the page no longer offers Testing records (#1570).

## Additional information

**Access:** Admin and Staff; both may record its review decisions.

The page is the Additional information stream's
[review worklist](../review-pipeline/spec.md#review-worklist)
([#1685](https://github.com/epiphany40223/parishkit/issues/1685)), at the
same address. Its stages, words and per-role actions are the
[review pipeline](../review-pipeline/spec.md#additional-information-stream)'s;
this section adds only what is particular to the stream. One row per
`AdditionalInformationItem` shows, in the Admin table
[column order](../admin-portal/spec.md#table-column-order): Family (surname,
then heads, as on the
[active parishioner family directory](#active-parishioner-family-directory);
the name opens the Family's Timeline), Family DUID, the Family's answer (its
text, shortened, as a link to the request's own page, which shows it in
full), Stage, Submitted, Last decision (who, when, the note) and the row's
actions. There is no Baseline (the parish holds nothing for this text before
the Family writes it) and no Reviewer's value (the stream has no Edit).

The filters are always open: **Stage** first, To review by default, each
choice with its count, then a search (Family name, DUID, the text and Staff
notes). There is no submitted-date filter: the four review worklists offer
the same kinds of filter, and the Administrator judged a submitted date
range not useful (2026-10-20,
[#1888](https://github.com/epiphany40223/parishkit/issues/1888)). History,
a Stage choice of its own after the stages, lists requests a later answer from the Family replaced
(superseded) or cleared (withdrawn); Every stage counts the current
requests only. The installed SQL selection searches the shown name (surname,
then heads), as the directory does; the table sorts by Family (surname, then
that name), Family DUID and Submitted
([#960](https://github.com/epiphany40223/parishkit/issues/960)). A Family
the data no longer has has no name: the page shows the
[ParishSoft status](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
badge in its place, and marks an inactive Family beside its name (#1216).
No chart or summary sits above the table: the counts are the Stage choices'.
The search and filters travel in POST bodies; the one address a link may
carry is a Stage alone (`?stage=<value>`, a
[My review work](../review-pipeline/spec.md#role-views) card's,
[#1818](https://github.com/epiphany40223/parishkit/issues/1818)), and any
other query string, or a Stage the filter does not offer, is refused with
the page's plain-language filter error (a 400). The removed date filter's
`start`, `end` and `zone`, in a query string or a POST body (a page drawn
before #1888), are ignored, never refused. The page's address carries
its Stage the same way (To review is the plain address), so a reload after
an in-place answer draws the same Stage.

Staff and Administrators decide each request as the pipeline's
[role-specific actions](../review-pipeline/spec.md#role-specific-actions)
allow: on a To review request, **Accept** (the parish will follow it up: To
handle) or **Ignore** (nothing to follow up); on a To handle request,
**Handled** once the follow-up is done, or **Back to review**; on an
Ignored request, Back to review, which needs a note saying why. Handled is
final. Each decision takes an optional note, which becomes the request's
Staff notes (the export's and the search's); a decision with no note keeps
the notes as they were. The bulk bar records
Accept, Ignore, Back to review or Handled on every ticked row, all or none.
There is no Add (the stream records what the Family wrote) and no
assignment ([follow-up workflows](../admin-portal/spec.md#follow-up-workflows)).
A Family that writes different text again creates a new request, To review,
and the earlier one goes to History with its decision, as before
([Follow-up records](../data/spec.md#follow-up-records)); the same text
again creates nothing, so the request keeps its stage.

The stages replace the former "Follow-up needed" and "Follow-up completed"
choices, which each request still stores
([existing state to stage](../review-pipeline/spec.md#existing-state-to-stage)):
To handle is "needed, not completed" and Handled is "completed". Each
decision is one immutable Staff revision with its decision, note, actor and
time, paired with the `information_updated` audit event, which names only
the versions. A request whose latest revision was saved before schema
migration 0126 with follow-up not needed reads as Ignored, since a Staff
member chose that; a request no Staff member has saved is To review.

Each request's own page shows the submitted text in full, its disposition and
replacement link, whether a weekly digest already reported it (a report email,
which replaced the weekly digest in #1786, records no such mark), its Stage,
the Family's **Contact details**
([#1595](https://github.com/epiphany40223/parishkit/issues/1595)) and the
request's history, each entry worded by its decision (Accepted, Ignored,
Back to review, Handled; an entry saved before migration 0126 by its
follow-up choices). Decisions are made on the worklist, not on this page;
its link back opens the worklist. The Contact details are so Staff
can reply without looking the Family up in ParishSoft: the Family record's
email addresses and phones, then those of each active head of household
(the heads the page's Family name lists), each under its own name. Other
Members, such as children and inactive heads, are deliberately not listed:
the heads are who Staff reply to. The details come
from the same ParishSoft data the page names the Family from. The values are
operational contact, as Administrators and Staff see it on Ministry requests
([Ministry change summary](#ministry-change-summary)): ParishSoft's publish
flags do not hide them. Source text that is not a valid address is shown
with "not a valid address; fix in ParishSoft", as the
[directory](#active-parishioner-family-directory) shows it, and a detail
with no value reads "None recorded". A Family the data no longer has shows
no details and says so. If the data is replaced while the page is read,
the section says the details appear by themselves once the newer ParishSoft
data can be read, and the page
[brings them in by itself](../ui-conventions/spec.md#pages-that-update-themselves)
([#1863](https://github.com/epiphany40223/parishkit/issues/1863)): the
notice is a status region that reads the passive status
`/admin/reports/information/contacts/status/?shown=<generation>`
(`information_contacts_status`), which admits the same readers as the page,
never extends the sign-in, records no audit event, and says only whether a
ParishSoft data generation other than the page's own (`shown`) is now in
use, never a contact value. Once one is, the page re-reads its own address
once, in place and marked automatic, which records one
`information_viewed` view with `automatic: true` and reads the details from
the newer data. Who sees the section is exactly who may
open the page, Administrators and Staff (`ADDITIONAL_FOLLOWUP`), checked
again by the contact read itself; Ministry leaders still cannot open the
page. Only the request page reads the details, never the worklist, its
exports, report emails or `followup information-show`, and the page's
`information_viewed` access audit covers them; the audit records no contact
values. The section reads the snapshot's Family, head Member and contact
rows directly, as the directory's Family head emails do, so it needed no
schema change.

The export pane below the table queues the complete export, which includes
complete text and a workflow history option, and names each Family as the
page does, from the snapshot its capture was read from; a Family without a
surname is "Name not recorded in ParishSoft", as the selection names it.
It takes the worklist's view: the search and the sort, and the
Stage as the stored follow-up choices it stands for (To handle: needed, not
completed; Handled: completed; History: every request, current or not).
To review and Ignored are both "not needed, not completed" there, so either
exports both, and the pane says so. The exports have a ParishSoft status
column right after Family (#1216). The name form is
chosen once for the whole file, in the same database read as the names: if
compaction has begun on that snapshot by the time the file is rendered,
every row keeps the captured surname instead, so one file never mixes the
two forms. The file's "Family names" report detail says which form it uses
("Surname, then heads of household", or "Surname only" with the reason). A
late render (a retry) can therefore differ from an earlier render of the same
capture only in that name form.
The CSV, XLSX and PDF share one set of columns and carry no internal
references: no row type or row version column (the Administrator, 2026-10-09,
and decision B1 on
[#966](https://github.com/epiphany40223/parishkit/issues/966)). An item's
row leaves Changed at and Changed by blank; its "Earlier workflow"
revisions fill them, and the PDF nests them under the item. Earlier workflow
rows are listed directly under their item and repeat its Family name, Family
DUID, Submitted time, disposition and submitted text, which identify the item
to a reader even after the sheet is sorted or filtered. A superseded item's
"Replaced by request submitted" value is the Submitted time of the later
request that replaced it, which finds that request's row, or "Not in this
export" when the export's filters left that request out.

The selection itself keeps its submitted-date filters (from, through), which
the host command line still takes (`--start`, `--end` and `--zone`, see
[Admin automation](../admin-automation/spec.md#reports-and-exports)):
whole days in the given zone, as the
[Logs screen's date filters](../admin-portal/spec.md#logs) are in the
browser's, and a date without a known zone is refused, never read in another
zone. An export captures the filters with their zone, and its retry asks for
exactly those filters; a capture made before schema migration 0064 has no
zone, keeps its campaign-zone days and is retried unchanged. The export pane
posts no dates; a pane drawn before #1888 still posts its empty ones, which
are dropped unread. A file whose capture has no dates names no date range:
its "Filters and sort" leaves out the empty days and zone, and it has no
"Campaign date-filter timezone" line; a dated capture keeps both.

Every decision is checked against the request's version the row showed; a
request someone else changed first is left as it is and the notice says so
([Concurrency](../review-pipeline/spec.md#concurrency)). This report is also
the source for report emails'
[open additional information requests block](../report-emails/spec.md#open-additional-information-requests-block).

## Active parishioner family directory

**Access:** Admin and Staff.

One **Active parishioner family directory** page serves both Family-code
lookup and postal outreach; they were separate pages until
[#202](https://github.com/epiphany40223/parishkit/issues/202). The page lists
the campaign's Families: those both active and registered at the parish
(portal-eligible). Active Families registered elsewhere, and inactive
Families, are left out by design, and the page's name says so
([#870](https://github.com/epiphany40223/parishkit/issues/870)). The page
shows no population summary or separate code-privacy line above its filters;
the page's About help covers code privacy. Each row has the Family's name,
Family DUID (in its own column, headed "Family DUID"), envelope number,
manual code, current email eligibility/deliverability, and whether the Family
has a current live response (Responded, noting when that response is one
Staff entered through [Open form](#open-form)). The columns follow the shared order,
most relevant column first
([#932](https://github.com/epiphany40223/parishkit/issues/932)). The
directory is a Family table, so Family (the row header) leads, then Family
DUID, Envelope number and Family code (which therefore never moves), then any
Submissions and What to check, then any
[response dates](#response-filters), and then the directory's own columns.
Both Family and Family DUID sort. The name is the Family's
surname followed by its heads of household, so same-surname Families can be
told apart: "Smith, Anna and John" (three or more heads read "A, B and C"); a
head whose surname differs from the Family's is shown in full ("Smith, Anna and
John Jones"); without heads it is just the surname. The default order is
surname, then that whole name, then DUID. Search supports full/partial
case-insensitive match of that name (so a head's first name finds the Family),
any active Member's first and last name or nickname and last name
([#664](https://github.com/epiphany40223/parishkit/issues/664)), DUID,
envelope number and address. A Member match changes nothing in the row: it
shows the same Family name, heads and columns as any other match, and never
the matched Member. Searching by a Member's name can still confirm that some
Family has an active Member by that name, even one the directory does not list
(for example, with no phone); Staff with report access accept this. Exact
canonicalized-code search uses a separate CSRF-protected POST body and never places the candidate in a URL or
query string. Apply waits until the Exact Family code is a whole
eight-letter code or blank; a malformed code that reaches the server is
refused at that field
([field errors](../ui-conventions/spec.md#field-errors-and-live-checks),
[#1332](https://github.com/epiphany40223/parishkit/issues/1332)): the page
is drawn again (400) with the filters sent in the form, the message in the
filters' hint row and in the summary, and the Families, export and links
for no filters (keeping the sort and the mailing-columns choice). Its access
audit ends denied. The code
is directly visible to Admin and Staff; there is no per-row reveal action,
reauthentication ceremony, distinct-Family reveal budget, or Valkey dependency.
The server rechecks the report role and campaign scope on each request and uses
`Cache-Control: no-store` for interactive responses.

An empty table says why, by the Filters panel's own count
([empty tables](../ui-conventions/spec.md#empty-tables-and-lists),
[#1828](https://github.com/epiphany40223/parishkit/issues/1828)). With
Include mailing columns and no other filter, nothing is wrong: "No active
Family needs a postal invitation: each one has a Family head email that
campaign mail can reach, or no usable mailing address. Turn off Include
mailing columns to list every Family." With any other filter: "No Families
match these filters. Select Clear filters to list every active Family."
With none: "No active Families in the ParishSoft data yet. Families are
listed here once a ParishSoft refresh loads them; an Administrator starts
one from Refresh from ParishSoft." Include response columns adds columns
and lists the same Families, so it does not count as a filter here, though
the panel's summary counts it.

In Testing mode the Family sign-in accepts only rehearsal codes, so the page
says the listed codes work only after go-live; its exports download in place
from that page ([downloads happen in place](../ui-conventions/spec.md#downloads-happen-in-place)),
and an export status page opened directly says the same.
For Administrators the notice links "Try the Family form as a chosen Family",
the chosen-Family test send they alone may open; Staff get no link and are told
to ask an Administrator for a test invitation
([#591](https://github.com/epiphany40223/parishkit/issues/591)).

The manual code is intentionally a low-sensitivity, campaign-bound access
mechanism. Admin and Staff already hold broader parish-data access, and the code
is unusable while closed but may become usable again if that same campaign is
formally reopened; it never carries into a successor campaign. This
classification does not make it public: report access and exports remain
authenticated, codes are excluded from logs, and the public Family login
retains its guessing protections.

Filters include how campaign mail can reach a Family: by deliverable email, by
postal mail only (no deliverable email but a usable mailing address: a street
line and a city, plus a state or postal code), or neither. A one-click
"Can't be reached by email or mail" preset opens the page filtered to
neither. It, the mailing-columns preset (`?mailing=yes`) and the
[response presets](#response-filters) (`response`, `responses`, `check` and
`sort`) are the only values accepted in a link, since none carries a private
value; search text and exact codes stay in the POST body. The page
with mailing columns (which never lists those Families) and the Admin home
page show how many active Families across the campaign no campaign mail can
reach, linking to that list.

The old postal-outreach page and export addresses
(`reports/<campaign>/postal/` and its `export`) are retired with no redirect
(#758, #864): they are Admin-only, and the mail merge is the directory
export with mailing columns on.

Each row's **Contact details** list the active heads (the ones the Family
name lists) with their email addresses from the same ParishSoft data
([#604](https://github.com/epiphany40223/parishkit/issues/604)). An address
several heads share, compared case-insensitively after trimming, is shown once
with all of them ("Anna Example and Ben Example — family@example.org"); a head
with an address of their own is listed separately, and a head without one
reads "No email on file". Entries follow the heads' order, then each head's
addresses in order. A valid address is selectable text with a `mailto:` link;
source text that is not a valid address is shown, marked "not a valid
address; fix in ParishSoft", and is not linked. The exports carry the same
list in a Family head emails column. Head emails never appear in URLs (query
strings or paths), logs or audit records. If the page's ParishSoft data is compacted while the page
is being read, that request fails (the next one reads the new data) rather
than show a head as having no email.

Without [mailing columns](#mailing-columns), CSV, XLSX, and PDF exports are
one header row plus one row per Family, with the page's columns in the
page's order: Family (the same surname-and-heads name as the page),
ParishSoft status, Family DUID, Envelope number, Family code, any
Submissions, What to check and response dates (see
[Response filters](#response-filters)), and Family head emails.
The list filtered to Families no campaign mail can reach
adds Phone numbers for follow-up calls, before Family head emails. In the
[shared words](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft),
ParishSoft status, the page's badge in a column of its own, reads
"Inactive in ParishSoft" for a Family no longer active in ParishSoft (its
name stays in Family) and "Not in ParishSoft" for one the data no longer
has (Family is blank); it is blank for every other Family. The PDF is the
[shared design](#file-design)'s table: the columns share the page width, and long cells and headings wrap, so a file
with many columns still fits the landscape page; times use the parish's
compact date format and counts are grouped, as on the page. Family
head emails reads like the Contact details: "Anna Example and Ben Example:
family@example.org; Cara Example: (no email)", with invalid source text
followed by "(not a valid address; fix in ParishSoft)". The emails are not
part of the export's captured selection; they are read when the file is
rendered, from the captured ParishSoft data while it is still kept.
ParishSoft data is refreshed often (every 15 minutes under the default
[refresh schedule](../background-processing/spec.md#refresh-schedule)) and
superseded data is soon compacted, so a render that comes later (a retry)
reads the same heads' emails from the current ParishSoft data instead,
and the file's report details add "Head emails as of" that data's refresh
time. CSV files carry no report details, so that note appears only in XLSX
and PDF files. A head the current data no longer has as a Member at all
reads "(Not in ParishSoft)" rather than "(no email)". A retried file
therefore has the same Families, names,
codes and other columns as the capture, but its head emails may be newer. Only
when no ParishSoft data is available at all does the render fail, with its
own failure kind (`directory_head_emails_unavailable`) in the logs; the
export is retried like any failed render. Report details (parish, campaign, capture time, "Head emails as of"
when it applies, Families in the file, filters applied and the privacy line "Sensitive: Family codes.
Authorized recipients only.") are in the PDF header and footer and the XLSX
"Report information" sheet, never in columns. The page names neither the
file's count nor its columns beside the export buttons
([export actions](../ui-conventions/spec.md#downloaded-files)). Every
file's times use the browser's zone
([time zones](../ui-conventions/spec.md#downloaded-files)). The
export's capture keeps only the private contact columns its file
renders (#388 L6): the address for the mailing-columns file, phones for the
list filtered to Families no campaign mail can reach, and the envelope number
for the Family-code file only (#933). The rest are stored empty. Exports use
the standard asynchronous, short-lived, requester-authorized export
pipeline, including its explicitly accepted plaintext storage and owner-only
permissions under the
[point-in-time export rule](../operations/spec.md#point-in-time-exports).
Queuing an export needs no recent sign-in
([#1547](https://github.com/epiphany40223/parishkit/issues/1547)), although
the file holds every listed Family's code. Interactive report execution and exports are audited at
report, campaign, actor, filter, and row-count granularity without copying
codes into the audit payload. Exact-code-search audit records omit the raw filter and store
only a keyed fingerprint when correlation is operationally necessary.

### Response filters

The directory folds in the former [response lists](#response-lists)
([#933](https://github.com/epiphany40223/parishkit/issues/933)) and is now
the only page that lists the Families behind the response counts. Its
**Response** filter is a closed choice: Any (the default); Submitted;
Submitted more than once; Not submitted; Started, not submitted; Started, got
past the first step; Opened the form only; Invited, never opened; Invited,
link followed but never opened; Invited, link not followed; and No invitation
delivered. It replaces the earlier Campaign response filter (Responded / Not
yet responded); Not submitted is the old Not yet responded list in one step.
Each choice
except Any reads the [funnel](#response-funnel)'s per-Family rows at the
moment of the request, shown as **Counted at** beside the matching count, and
chooses Families by the rules the lists used, from the same
[funnel stages](#funnel-stages):

| Response | Families listed | The list it reproduces |
| --- | --- | --- |
| Submitted | a submission | `submitted` |
| Submitted more than once | more than one submission | `more-than-once` |
| Started, not submitted | form opened, nothing submitted | `started` |
| Started, got past the first step | as Started, and got past the first step | `started`, got past the first step |
| Opened the form only | as Started, and not past the first step | `started`, opened the form only |
| Invited, never opened | a delivered invitation, form never opened | `not-opened` |
| Invited, link followed but never opened | as Invited, never opened, with the link followed | `not-opened`, link followed |
| Invited, link not followed | as Invited, never opened, without the link followed | `not-opened`, link not followed |
| Not submitted | active Families with nothing submitted | none (the old Not yet responded) |
| No invitation delivered | active Families with a campaign record, no delivered invitation and the form never opened | none (new) |

No invitation delivered is a response status only: no invitation email was
delivered to the Family. It is not a postal list. Who gets a postal invitation
is the Administrator's rule in [mailing columns](#mailing-columns)
([#951](https://github.com/epiphany40223/parishkit/issues/951)), and neither
Response choice applies that rule. Not submitted includes active Families with
no campaign record (no Family code for this campaign and nothing delivered or
submitted), as the old Not yet responded did. No invitation delivered leaves
them out: with no Family code, no invitation could be sent, and the funnel
and the dashboard count only campaign Families. Its earlier inclusion was for
a postal use that no longer applies. For the active Families with a campaign
record, Started, not submitted; Invited, never opened; and No invitation
delivered split Not submitted with no overlap: a Family that opened the form
without a delivered invitation (from a test or a resent link, say) counts as
started, not as uninvited. An active Family with no campaign record is in Not
submitted only.

Population: with Response Any, Not submitted or No invitation delivered the
rows are active Families only, and the page header reads "Active registered
Families from ParishSoft data as of *time*". Under every other choice the
header reads "Families from ParishSoft data as of *time*" and the rows are
every campaign Family the funnel selects, so the matching count equals the
[dashboard](#response-dashboard)'s figure. A campaign Family the current
ParishSoft data no longer lists as active and registered is then listed too,
marked with the shared **Inactive in ParishSoft** badge beside its name. Its
name and envelope number come from the current data when that data still has
the Family; otherwise the name cell holds only the **Not in ParishSoft**
badge
([Members and Families no longer in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)).
Campaign mail reaches it neither way: its email availability reads the same
words as its name cell ("Inactive in ParishSoft" or "Not in ParishSoft"), it
counts as reachable by neither email nor postal mail, and the postal mail
merge leaves it out when mailing columns are on, since that list covers only
the Families postal mail is for
([#951](https://github.com/epiphany40223/parishkit/issues/951)). Its code is still shown when the campaign has one,
but without an Open form link. The active and unreachable totals still count
active Families only.

**ParishSoft data to check** is a second closed filter: Any (the default),
Anything to check, Blank mailing name, or Envelope number 0. It selects
active Families whose current ParishSoft record has a missing or blank
mailing name (whitespace only counts as blank) or an envelope number of
exactly 0, the launch-day problems the `data-quality` list showed. When it is
applied, a **What to check** column names the problems. "Active" here is the
directory's own rule: registered and active (`portal_eligible`) in the
current ParishSoft data. The `data-quality` list instead took the campaign
Families whose campaign record was marked active at the last refresh, so the
two can differ slightly: the directory also checks an active Family with no
campaign record, and follows a change in ParishSoft before the campaign's
records are refreshed.

An **Include response columns** checkbox, off by default (explained in its
toggletip, with no help line under it, like
[Include mailing columns](#mailing-columns)), adds the columns
the chosen Response's list showed: Any shows Invitation delivered, Form
opened, First submitted and Submissions; Submitted shows First submitted and
Submissions; Submitted more than once shows First submitted, Last submitted
and Submissions; the Started choices show Form opened and Got past the first
step; the Invited choices show Invitation delivered and Link followed; Not
submitted shows Invitation delivered, Form opened and Got past the first
step; No invitation delivered shows Link followed and Form opened.
Submissions follows Family code, and the dates follow Submissions and
What to check, in the order the stages happen: Family
leads this table, and dates lead only event or log tables
([#932](https://github.com/epiphany40223/parishkit/issues/932)). Times are shown in the browser's time
zone; a stage not reached reads "Not yet" (Link followed reads "No"). The
checkbox never changes which Families are listed,
and the Family-code export gets the same columns; the mail-merge file keeps
its fixed columns. Turning the response columns on, choosing a Response or
sorting by a response column reads the funnel; otherwise the directory does
not.

Each response column sorts both ways (newest or largest first on the first
click, Families that have not reached the stage last, then by name). These
orders are part of the installed selection, so they page and export like
the Family and Family DUID orders, which also sort both ways (#932). A
response column orders the rows only while it is
shown: when Include response columns is off, or the chosen Response does not
show that column, the order falls back to Family (by name), for the page, the
export and the audit entry alike.

The directory opens from closed link presets in the URL, one per list of
Families, which the Response dashboard's **Lists of Families** panel linked
to until [#1537](https://github.com/epiphany40223/parishkit/issues/1537)
removed it; saved links keep opening the same lists. The four funnel lists'
links turn on Include response columns,
so the page shows that list's response columns in its default order; the
ParishSoft data to check link shows the What to check column instead:

| List | Link presets |
| --- | --- |
| `submitted` | `?response=submitted&responses=yes&sort=submitted` (First submitted, earliest first) |
| `started` | `?response=started&responses=yes` |
| `not-opened` | `?response=never-opened&responses=yes` |
| `more-than-once` | `?response=more-than-once&responses=yes&sort=submissions_desc` (most submissions first) |
| `data-quality` | `?check=anything` |

The four funnel lists' links list exactly the Families the dashboard's
stage tiles count.
The ParishSoft data to check link lists the directory's own population, so
it can also include an active Family with no campaign record, which the old
`data-quality` list left out (see
[ParishSoft data to check](#response-filters) above); this is intended.
A Testing dashboard's links are the same presets followed by `&mode=testing`.

**Testing switch.** Administrators get **Responses shown** above the
Filters panel: Production (the default) | Testing rehearsal, a
[binary choice](../ui-conventions/spec.md#binary-choices) with its
explanation in its toggletip
([#1535](https://github.com/epiphany40223/parishkit/issues/1535)). It is
drawn only for an Administrator and only while the campaign has an active
rehearsal (or the page already shows one); Staff and an Administrator with
no rehearsal never see it, as on the
[response dashboard](#response-dashboard). Choosing a segment applies it at
once, in place, with the filters applied (posted privately, as the table's
own controls post them, back to the first page), and the filter form
carries the mode as a hidden field, so Apply filters keeps it. The mode is
not counted among the Filters panel's applied filters. `mode` is one of the
closed link presets, so the address bar after an in-place answer includes
`mode=testing` while Testing is shown. Testing rehearsal reads the campaign's active rehearsal
epoch for the Response choice, the response columns, their sort orders and
Counted at; everything else (the population, Family codes, contact details,
email availability, reach and data checks) is the same in both modes. A
result never mixes modes: the **Responded** column, which reads live
responses, is left out in Testing rehearsal, and a visible notice says the
response choices use the rehearsal only. A Staff member asking for
`mode=testing` (link or form) is refused (403), and a Testing view asked
for after the rehearsal ended gets a refusal (409) saying there is no active
rehearsal, never a Production view. A Testing page's export form carries
the rehearsal epoch the page read, and the export's selection pins that
epoch: if it is no longer the campaign's active rehearsal when the export
is queued, the export is refused (409) and nothing is made, so the file
never holds a later rehearsal than the page showed. The
[command line](../admin-automation/spec.md#reports-and-exports) has no page,
so it reads the rehearsal active when it runs. A Testing export is admitted
for Administrators only (queuing, downloading and retrying alike) and its
columns are the Production file's. Its XLSX and PDF report
details add **Responses shown: Testing rehearsal**; a CSV has no report
details, so the downloaded file's name says it instead
(`family_directory-testing.csv`, `postal_outreach-testing.csv`, and the same
for XLSX and PDF). Each directory view's audit entry and every directory export's
lifecycle audit entry record `report_mode` (`production` or `testing`; an
export captured before the switch is Production). A Production selection's
parameters keep their earlier shape, without a mode, so a request key bound
before the switch still matches its repeat.

Each value must be in its filter's closed vocabulary, as in the form; an
unknown value, a repeated parameter or any other parameter is refused (400).
A valid `sort` whose column the link does not show (a response column without
`responses=yes`, or one the chosen Response does not show) is not refused:
the page opens (200) in Family name order, as the form does. The presets only
open the page: the next filter change, sort or page is the usual private
POST to the page's address without a query string. After each in-place
answer the address bar shows the view's own address (the page's
`data-page-address`, as on [System logs](#system-logs)): the page with the
closed link presets now applied when no search text or exact code is, and
the bare page otherwise, never with a page number. So Reload, or Back from
another page, never brings back the preset the page was opened with; a
search is cleared.

The selection is `stewardship_directory_report_v2` (migration 0050). It reads
the funnel through `stewardship_family_response_v1`, so the directory and the
dashboard share one definition. Version 1 stays installed and unused. The
selection also takes an optional mode and rehearsal epoch, which the
Testing switch above uses to read one rehearsal's funnel. An export's captured parameters keep the Response,
data check and response columns choices, and its report details add
**Responses counted at** and name each applied choice. Each view is audited
with the closed values `directory_response`, `directory_data_check`,
`directory_response_columns`, `directory_sort` and `report_mode` (older entries' `yes` and
`no` Response values still read correctly), never a name or search text.
The selection itself still accepts v1's `yes` and `no` Response values, read
as Submitted and Not submitted for active Families only, so the previous
release's application can still queue directory exports if it is ever
restored after migration 0050.

### Mailing columns

An **Include mailing columns** checkbox, off by default, lists the Families
postal invitations are for, adds Addressee and Mailing address columns to the
table and makes the export a postal mail merge. By Administrator decision
([#951](https://github.com/epiphany40223/parishkit/issues/951)), postal
delivery of invitations is only for active parishioner Families where no head
of household has a deliverable email address: a valid address that has not
been permanently refused (or whose refusal was resolved), the rule the
invitation sender uses. So with mailing columns on, Campaign mail can reach
is always "By postal mail only" (no deliverable email and a usable mailing
address), whatever reach was chosen. Only active parishioner Families have
that reach, so a campaign Family no longer active in ParishSoft, which some
[Response](#response-filters) choices list, is left out. While the box is
ticked, the Campaign mail can reach select stays in place but shows "By
postal mail only", disabled (the page script's `data-locked-when`,
re-applied on `pageshow`; #563, #736). The filter bar shows no help line
under either field, since a line there makes that field taller than the
others and misaligns the bottom-aligned row (Administrator, pk-local batch
5, 2026-10-10): the box's and the select's toggletips and the About panel
say what the box does and why the select is set. The rule is
applied when the rows are selected, never to the page's own filters: the
page still sends and keeps the reach the reader chose, which comes back when
the box is unticked (Any by default), and the server applies the rule to
every request whatever the page sends. The audit records the reach applied,
and an export's captured filters hold reach `mail`; its status page returns
to the directory with mailing columns on and no reach preset. The other
filters still narrow the list. Families with
neither a deliverable email nor a usable mailing address cannot be mailed
and stay on the "Can't be reached by email or mail" list. Families without
deliverable email are the complement of the deliverable-email statistics
card, not of the syntactic eligible-email card. The Addressee column names
the Family as the mail-merge file does. Mailing columns show nothing the
active parishioner family directory's contact details do not already show
Admin and Staff, and Ministry leaders are denied either way. Viewing with
mailing columns is audited as postal outreach, and its export is a
`postal_outreach` export request. An export captured before #951 keeps its
own filters, including a reach other than "By postal mail only", for the rest
of its [download window](../operations/spec.md#point-in-time-exports).

Detail contains Family DUID, envelope number where present, Family/head names,
head email addresses, family/member phone numbers, complete primary address, reason, and campaign
manual code.

The export is a mail merge for envelope labels and cover letters: one header
row plus one row per Family, with the columns ParishSoft DUID, Family,
Addressee, Family heads, Address line 1–3 (empty optional lines omitted), City,
State, ZIP (with its +4 extension when present), Family code and Family head
emails (as in the [active parishioner family directory](#active-parishioner-family-directory) export). Addressee and
Family heads join the active heads' names naturally ("Aaron and Isabelle
Williams" when they share a surname, "Aaron Williams and Isabelle Smith"
otherwise); Addressee falls back to the Family name. The file has exactly the
rows the filters list on the page. In a file captured before #951, a Family
without a usable mailing address keeps its row: its Addressee, Address line
1–3, City, State and ZIP are blank (never a partial address), and the file's
report details count those rows. Existing columns keep their names and order (Family head
emails was added at the end), so existing mail-merge templates keep working.
The PDF lays the same content out as address blocks (a block without an
address says "No usable mailing address"), leaving out the head emails,
with the report details in its header and footer. It
never includes the opaque email-link token.

## Ministry change summary

**Access:** Admin/Staff for all selected campaign Ministries; leaders for the
campaign Ministries they lead in ParishSoft only.

The database enforces this scope, not only the application. The Ministry
requests page (its By Ministry counts and its
[follow-up](../admin-portal/spec.md#follow-up-workflows) queue), the
Member contact details on each follow-up request and in its export read
through `stewardship_ministry_report_v2` and
`stewardship_ministry_followup_v2` (migration 0040). These take the signed-in
user and derive the scope from that user's current roles and the Ministries
they lead in ParishSoft with `stewardship_ministry_scope_v1`, as export
captures do (since migration 0042, #922, the led Ministries come only from
ParishSoft roles, not from assignments). A user with no current
scope reads nothing. The application's own view of the user's roles and
Ministries is a pre-check and a cross-check. If the database's scope is wider,
the page is refused. If it is narrower, the narrower scope applies
([#389](https://github.com/epiphany40223/parishkit/issues/389) L3). A user the
application treats as Admin or Staff but the database gives no scope is refused
rather than shown an empty report. A leader's My review work cards and the
follow-up menu count read through the same function and cross-check, so neither
shows more than the queue would.

**One page** ([#1133](https://github.com/epiphany40223/parishkit/issues/1133)
step 2b, Administrator approval 2026-10-14). The Ministry requests summary and
the Ministry follow-up queue are one page and one menu entry, **Ministry
requests**, at `/admin/reports/ministries/`. It is built on the
[follow-up queue](../admin-portal/spec.md#follow-up-workflows), because
recording happens per request. The queue's old address,
`/admin/reports/ministries/follow-up/` (and its slashless form), answers 404
with no redirect
([legacy URLs](../ui-conventions/spec.md#addresses-and-legacy-urls)). The
request pages, the bulk outcome, Export these requests and Roster changes to
enter keep their addresses under `follow-up/`. The separate summary page is
retired, along with its Ministry-name search, its paging and sort, its per-row
Follow-up progress column and bars, and its "Follow up these requests" link.
Its totals and its follow-up packet move to the one page. Rejected: keeping
two pages with links only (step 1 did that, and two pages still listed the
same requests), making the summary the base, and a separate leader portal
(rejected in #533). The host `report ministry` command is unchanged.

**The review worklist** ([#1684](https://github.com/epiphany40223/parishkit/issues/1684)).
Ministry requests is the Ministry stream's
[review worklist](../review-pipeline/spec.md#review-worklist): the shared
component, its words and its selected-state icons, at the same address. It
replaces the queue table, its Status and Outcome filters and its bulk
outcomes; the request pages, the remembered view,
Export these requests and the packet stay. From the top, the page shows:

1. The heading, the page help, the ParishSoft data date and the notice
   shown while other campaign work locks edits. (Roster changes to enter,
   once linked here, is this page's To enter by hand Stage since R39;
   [roster entry](../admin-portal/spec.md#roster-entry-on-ministry-requests).)
2. **By Ministry**, a compact table described below. It stays above the
   worklist's filters, not between them and the table: its counts apply
   filters in place and the table's height changes with them, so anything
   below the table would move under the pointer
   ([no layout shift](../ui-conventions/spec.md#no-layout-shift)).
3. The worklist: its reserved notice line, then its filters, always open:
   **Stage** first, then Search Member or Ministry name, Ministry and
   Request, and for Staff and Administrators **Members**, whose second
   choice lists the requests for
   [Members no longer in ParishSoft](../admin-portal/spec.md#follow-up-workflows)
   (#1217) in place of the old Status choice; then the selection bar, the
   table and **Export these requests**, as
   [follow-up workflows](../admin-portal/spec.md#follow-up-workflows)
   describes.
4. The [follow-up packet](#multi-ministry-follow-up-packet). The packet's
   choices are still every Ministry in the reader's scope.

No totals and no chart sit above the table
([#1676](https://github.com/epiphany40223/parishkit/issues/1676), folded
into #1684): a review worklist has no summary tiles or charts
([review worklist](../review-pipeline/spec.md#review-worklist)). The Join
requests and Leave requests tiles and the "Follow-up progress" chart
(#720, #1133) are removed with their chart code; how many requests are at
each stage is in the Stage filter's choices, and how many each Ministry has
is in By Ministry. Rejected: the two stat tiles with resolved counts
that #1676 first proposed, since the worklist would remove them again.

**By Ministry** has the columns Ministry, Join requests, Leave requests and
Unresolved, with one row for every Ministry in the reader's scope. It does
not follow the filters, because it is the way to choose one Ministry. The
Ministry name is the row header and shows no Ministry DUID
([#1123](https://github.com/epiphany40223/parishkit/issues/1123)). Its
counts are the summary selection's current counts: join requests and leave
requests of any status, and unresolved requests of either kind. Counts use
the latest live request state. Requests for
[Members no longer in ParishSoft](../admin-portal/spec.md#follow-up-workflows)
are left out for everyone (#1217).

Each count that is not zero applies that Ministry and request type to the
worklist below in place, when the reader may follow up that Ministry. It does
not reload the page or jump to the top, and the filters then show what it
applied. A join count sets Request to Join, and a leave count sets it to
Leave; both set Stage to Every stage. An Unresolved count sets Request to
Join or leave and Stage to To review, since an unresolved request is one
still to review. Each count also clears the search. A count is also an
ordinary link carrying only the worklist's closed link filters
(`?ministry=<DUID>&kind=join&stage=all` and the like; see
[follow-up workflows](../admin-portal/spec.md#follow-up-workflows)), so
opening it in a new tab shows the same view. A Ministry leader gets links only
for the Ministries they lead. A zero count is plain text, and each link's
accessible name names its Ministry ("3 join requests for Choir"), as
[count links](../ui-conventions/spec.md#admin-tables) ask.

A reader whose scope holds one Ministry sees a single line instead of a
table. A long list scrolls inside a bounded box. By Ministry is redrawn with
every in-place refresh, so the change a decision makes shows in its counts.

"Not in ParishSoft data" and "No longer in this campaign" are the
Administrator's concern and change nothing a leader does with a request, so
only Admin/Staff see them: each Ministry's notes in By Ministry and the page
help about them
([#1135](https://github.com/epiphany40223/parishkit/issues/1135)).
There is no Ministry activity filter (Active, Inactive) any more
([#1267](https://github.com/epiphany40223/parishkit/issues/1267)): the
page's packet, the export and the command line refuse an `activity` field
from anyone, and they always send the neutral `any` that the installed
selection and the export capture still take. The queue likewise explains its
"Removed" badge only to Admin/Staff. A leader never meets one, since a
leader leads only the Ministries the campaign asks about (#922).

The page reads its rows and export through the follow-up selection with its
cross-check; #1684's migration widens that selection so a request edited
into another Ministry is in the scope of both Ministries' leaders
([follow-up workflows](../admin-portal/spec.md#follow-up-workflows)). By
Ministry and the packet's choices read the Ministry report selection with
its own cross-check, so a reader needs both scopes, which the same roles
hold; they still count and list each request under the Family's Ministry,
while the worklist's Ministry filter also finds an edited request under its
Edit's Ministry, so after an Edit a count link can show one request more
or less than its count ([#1718](https://github.com/epiphany40223/parishkit/issues/1718)
tracks counting by the Edit).
The page's view audit is
the queue's `ministry_followup_viewed`; it no longer records
`ministry_report_viewed`, which the host `report ministry` command still
records.

One Ministry's join or leave list, which the host `export ministry` command
still writes, follows the Admin table
[column order](../admin-portal/spec.md#table-column-order), leading with the
Member: a list's Member DUID has its
own column next to the Member's name, and a Member added on the form, who has
no ParishSoft DUID yet, shows "New Member" there, followed by the note that
the Member was added on the form and the proposed Member's reference under
technical details; the row header is just the name. By Ministry names each
Ministry and shows no Ministry DUID. The Member DUID column sorts in the
installed selection's order
([#960](https://github.com/epiphany40223/parishkit/issues/960)), with a
Member added on the form last in either direction. The CSV, XLSX and PDF of a list carry the same
columns as each other, with no internal references
([#966](https://github.com/epiphany40223/parishkit/issues/966)), in the
page's order: Member, Member DUID, Status, Outcome, the contact details or
current role, and Submitted last. The page's combined cells are split into
their parts with the page's own words ("Status and outcome" is Status and
Outcome; "Contact and demographics" is Gender, Age, Email, Phones and Known
primary Family address), as the
[downloaded files](../ui-conventions/spec.md#downloaded-files) convention
allows. A list is always one Ministry's, so its
Ministry name heads the file in its report information, as it heads the
page's list, rather than repeat on every row
([#932](https://github.com/epiphany40223/parishkit/issues/932)); the
Ministry DUID is left out
([#1123](https://github.com/epiphany40223/parishkit/issues/1123)). A Member
added on the form reads "New Member" in the Member DUID column, and the
proposed Member's reference stays on the page's technical details. When two
Members added on the form share a name, they read "New Member 1", "New
Member 2" and so on in form order, so their rows stay distinct in every
format. Form order is the order the Family form lists those Members once
saved: ascending proposed Member reference, whatever the list's sort, with
every request of one Member sharing its label.

The Members joining and Members leaving lists that once opened from each
Ministry's counts, with their **Contact details** controls and their CSV,
XLSX and PDF exports, were retired (Administrator decision, 2026-10-12,
[#1157](https://github.com/epiphany40223/parishkit/issues/1157), the first
slice of #1133 step 2). Members can share a name, and a list of names did not
say which Member a request meant; the request does. Each request's
[follow-up page](../admin-portal/spec.md#follow-up-workflows) now shows its
Member's contact details, and the follow-up queue's **Export these requests**
downloads the queue's filtered set with them (as CSV or XLSX at any size, and
as a PDF of at most 1,000 requests, a limit set by the time a PDF takes to
render; see
[follow-up workflows](../admin-portal/spec.md#follow-up-workflows)). The lists' addresses
(`/admin/reports/ministries/joining/`, `/leaving/` and the export form's
`/admin/reports/ministries/exports/`) answer 404, with no redirect
([legacy URLs](../ui-conventions/spec.md#addresses-and-legacy-urls)). Export
files made from the lists before then stay downloadable for the rest of
their [download window](../operations/spec.md#point-in-time-exports), and the
host `export ministry` command still writes one Ministry's join or
leave list.

Neither list nor its export shows a resolved request's **Entered in
ParishSoft** tick; it is read and set on
Ministry requests at its To enter by hand Stage
([roster entry](../admin-portal/spec.md#roster-entry-on-ministry-requests)).
Ministry leaders never see the tick anywhere
([follow-up workflows](../admin-portal/spec.md#follow-up-workflows)).
The contact details are read through the installed selections (no schema
change for #1157; frozen migration 0119 changed their leader rule for
[#1591](https://github.com/epiphany40223/parishkit/issues/1591), below):
`stewardship_ministry_report_v4`, read for one Ministry and
request kind with every version, gives a join request's gender (a
ParishSoft code read as its census word, as the
[Family form](../parishioner-portal/spec.md#existing-member-census) reads
it), age as of the report date, email, phones and known primary Family
address, and a leave request's current role in the Ministry; the
[follow-up packet's](#multi-ministry-follow-up-packet)
`stewardship_ministry_packet_v1` gives a leave request's email and phones
(the packet's projection, which leaders already receive), its scope derived
in SQL from the signed-in user as an export capture derives it. An earlier
version of a request takes its Member's current values. A Member the Family
added on the form is not in ParishSoft yet, so the details are what the
Family entered, and the page says so; in a file such a Member reads "New
Member", and two such Members with one name read "New Member 1", "New
Member 2" and so on in form order (ascending proposed Member reference), so
their rows stay distinct in every format. A request whose Ministry the report
cannot read (one removed from the campaign whose requests were all withdrawn)
shows no details and says so.

Leader phone and email values honor ParishSoft publish flags and show "Not
published" rather than leaking a value, with one exception (Administrator
decision, 2026-10-17, "show own contacts" on
[#1591](https://github.com/epiphany40223/parishkit/issues/1591)): asking to
join or leave a Ministry is consent to be contacted about it, so on a
Member's standing request that Ministry's leaders see the requesting
Member's own email and phones even when ParishSoft does not publish them.
A request stands while the Member's latest version of it is neither
withdrawn (cancelled) nor replaced; an earlier version shown with the
history option follows that latest version, as its details already do.
For a Member the Family added on the form, the values are the ones the
Family entered with the request. A Member whose latest live answer in the
campaign marks them as unable to participate in any ministries (the form's
physical-limitations checkbox, recorded as `cannot_serve` in the response's
`service` answers; their leaves show as "Leave (cannot participate)") shares
nothing unpublished with any Ministry's leaders on any
of their requests, current or in history, in any state (Administrator
decision, 2026-10-18, on
[#1591](https://github.com/epiphany40223/parishkit/issues/1591), applied per
Member). This holds whatever the roster shows: a cannot-participate
response adds leaves only for the Ministries the Member is on, so an earlier
join that was declined or resolved, a leave a ParishSoft refresh confirmed,
and a request to a Ministry the form no longer offers follow the publish
flags too. A Member's answer is the one in the Family's latest live
response that answers the question for that Member (lists them in its
`service` answers with a true-or-false checkbox value; any other entry is
not an answer). Only a later live response that answers it for the
Member without the checkbox lets their standing requests share again; a
response that leaves the Member out, such as one reporting them moved out
or deceased, does not, and a Member never answered is not withheld. For a
Member the Family added, the answer is the one for the form's own entry for
that person: such a Member is not linked to the ParishSoft record Staff
create for them later, so a later answer for that ParishSoft Member does
not cover their earlier added-Member requests
([#1670](https://github.com/epiphany40223/parishkit/issues/1670)). Both
selections read the same answer, so the request page, Export these
requests and the packet agree.
Every other Member's standing requests share as above. Nothing else
widens: a Member with no standing request to the Ministry (so every other
Member of the Family), a withdrawn request and other Ministries' requests
keep the publish flags, and
a leader still reads only the Ministries they lead. The rule is enforced in
the SQL selections themselves (`stewardship_ministry_report_v3`, which v4
reads, and `stewardship_ministry_packet_v1`), so the request page, Export
these requests and the packet agree in every format. Admin/Staff may see
operational source contact. Mailing address/demographics are visible to a
Ministry's leaders as
explicitly authorized for this workflow. Birth date itself is not shown when
age suffices.

The per-Ministry counts have no export
([#1126](https://github.com/epiphany40223/parishkit/issues/1126)): they hold
no parishioner data, and the page already shows them. The page's downloads
are Export these requests and the
[follow-up packet](#multi-ministry-follow-up-packet). A request for a summary
export is refused as an invalid selection; an existing summary file stays
downloadable for the rest of its
[download window](../operations/spec.md#point-in-time-exports). The page shows
the ParishSoft data date but not the data load number, an internal counter.
The packet has no date filter. Every leader view/export is audited with
Ministry scope: a follow-up request page's view records its request's
`ministry_duid`, and **Export these requests** records the sorted Ministry
DUIDs in its file (`ministry_duids`) and whether its contacts were
operational or publish-restricted (`ministry_operational`), as described in
[follow-up workflows](../admin-portal/spec.md#follow-up-workflows).
An export audit event retains the sorted Ministry DUID set actually included
after filtering, plus whether the captured contact projection was operational
or publish-restricted. This result scope is distinct from the potentially wider
authorization scope used for lifecycle checks. A named detail section retains
its Ministry even with no matching Members. One event per action is sufficient; per-Ministry audit fan-out is not
required. These non-sensitive identifiers survive campaign-detail purge with
the audit event and contain no names, contacts, filters or Member values.
The [Admin log owner](../admin-portal/spec.md#logs) includes this set when
filtering audit events by Ministry; it must not depend on retained export rows.
This representation does not change current-policy checks or report columns.

## Multi-Ministry follow-up packet

**Access:** same Ministry scoping as
[Ministry requests](#ministry-change-summary).

The request offers multi-select plus Select all authorized Ministries: one
box per Ministry, and no box ticked selects every Ministry the requester is
authorized for. The form sits at the foot of
[Ministry requests](#ministry-change-summary). The boxes list every Ministry
in the requester's scope by name, whatever the page's filters, sort or page
show, so a leader of many Ministries can choose a subset; filtering, paging
or sorting the table keeps the ticks
([#533](https://github.com/epiphany40223/parishkit/issues/533)). At most
200 Ministries may be ticked for one packet, as the form's help says. While
more are ticked, Queue is unavailable and its reason line gives the count and
says to untick some or all
([#1332](https://github.com/epiphany40223/parishkit/issues/1332)); the server
still refuses more with a message saying to tick fewer or leave every box
unticked, which includes them all. A person's request refused that way is
answered with Ministry requests drawn again (400), refused at the boxes
([field errors](../ui-conventions/spec.md#field-errors-and-live-checks)):
the ticks sent among the boxes kept, every box marked, and the message in
the packet's hint row and in the summary; the queue is the default view.
A script gets the same refusal as JSON. The list is
read through the same scoped Ministry report selection as By Ministry, whose
whole-scope read it reuses. It shows
only each Ministry's full name (no DUID,
[#1123](https://github.com/epiphany40223/parishkit/issues/1123)) and no
request data. For each
Ministry, output its name (never its DUID), chair names, stewardship period/year, and one row for
each latest effective `join` or `leave` MinistryRequest in that Ministry,
including resolved/cancelled history only when the requester selects the
history option, and never one for a
[Member no longer in ParishSoft](../admin-portal/spec.md#follow-up-workflows)
(#1217). Rows contain:

- Member name and DUID, where a Member added on the form reads "New Member"
  (or "New Member N"), numbered by the same rule as the follow-up queue's
  [export](#ministry-change-summary) but within each Ministry's packet rows;
- authorized Member email address(es);
- recorded email-contact date, blank if none;
- authorized Member phone number(s);
- recorded phone-contact date, blank if none; and
- current outcome using this exact mapping: unresolved workflow state is blank;
  `joined` is `Joined the Ministry`; `leave_confirmed` is `Left the
  Ministry`; `declined` is `Declined / no longer interested` on a join request
  and `Stayed in the Ministry` on a leave request; `no_response` is
  `No response`; `duplicate` is `Duplicate request`; and `other` is `Other`
  with its notes/reference.

These are the outcome labels everywhere a Ministry request's outcome shows:
the follow-up queue, the request page and its history, the queue's Export
these requests, the retired join and leave lists' stored files, and packets
([#1132](https://github.com/epiphany40223/parishkit/issues/1132)). The
stored code is the same for both kinds, so a leave request someone resolved
as `declined` before the labels changed reads `Stayed in the Ministry`: the
Member did not leave. The follow-up queue's Outcome filter covers both
kinds, so there `declined` reads `Declined / stayed`. Nobody records
`duplicate` any more (the system never creates duplicates, since a Family's
resubmission replaces its request), so neither the request form nor the
Outcome filter offers it; a request already resolved that way keeps its label
([#1131](https://github.com/epiphany40223/parishkit/issues/1131)).

For Ministry leaders, every email and phone value in the packet follows the
Ministry report selection's leader contact rule
([Ministry change summary](#ministry-change-summary)): published values, and the
requesting Member's own on a standing request other than a
cannot-participate leave; any other renders `Not published`
instead of the source value. Admin/Staff retain the operational-contact access
defined by that rule. This privacy policy applies identically to screen, CSV,
XLSX, and PDF output.

Recorded workflow values are prefilled; empty cells remain printable for human
completion. Every format carries the report's privacy line. The XLSX is one workbook with a sheet per Ministry. PDF starts each
Ministry on a new page. CSV is one file with a blank row and repeated headings
between Ministries. No ZIP/per-Ministry files are required. Its dates and
capture times use the browser's zone
([time zones](../ui-conventions/spec.md#downloaded-files)).

## Pending census changes

**Access:** Admin and Staff; never Ministry leaders. Staff and
Administrators review every census change and record what was entered by
hand; only an Administrator starts an update run
([role-specific actions](../review-pipeline/spec.md#role-specific-actions)).

The page is **Census changes** under Review work
([#528](https://github.com/epiphany40223/parishkit/issues/528)): the
census stream's [review worklist](../review-pipeline/spec.md#review-worklist)
([#1683](https://github.com/epiphany40223/parishkit/issues/1683)), listing
every census change Families reported, from review to the moment it reaches
ParishSoft, by hand or by
[publication](../data/spec.md#review-and-publication). It reads the existing
[proposed-change rows](../data/spec.md#proposed-changes) (one per atomic
Family request) and the handling registry there that classifies each field,
and derives each row's stage from its stored decision and execution
([existing state to stage](../review-pipeline/spec.md#existing-state-to-stage));
it stores no stage of its own. Testing responses are excluded, as in the
other reports. Pledges, share methods and Ministry rosters are not census
changes; the
[data workflow](../data/spec.md#review-and-publication) says where they go.
The page's shared parts (the Stage filter, the row icons and their selected
state, the bulk bar, the dialogs and their notes) are the review worklist's;
this section says only what is particular to census changes. Its words are
the [pipeline vocabulary](../review-pipeline/spec.md#pipeline-vocabulary):
*Approve* and *To do* are no longer used on the page.

### Census change rows

One row per change. The columns follow the shared
[column order](../ui-conventions/spec.md#table-column-order): Family (the row
header: surname, then heads, as on the
[active parishioner family directory](#active-parishioner-family-directory);
the search, sort and downloads use the same name, and a Family the data no
longer has shows the
[Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
badge), Family DUID, then:

- **Who**: the Member, "New Member" with the proposed Member's name, or the
  Family for its household fields (home and mailing address, email opt-out).
- **Member DUID**, beside Who: the Member's ParishSoft DUID, "New Member"
  for a Member added on the form, and blank for a household change. Rows
  without a DUID follow the Members on the first click.
- **What changed**: the field's plain label (Mobile phone, Home address,
  No longer in this household, Deceased, New Member, and so on).
- **How it reaches ParishSoft**, in the downloads only (the table leaves
  it out so it stays narrow, and the filter of that name still narrows
  the rows; Administrator, 2026-10-19): *Automatic* for a field the
  [handling registry](../data/spec.md#proposed-changes) marks API-writable,
  *By hand* for every other row. A Family address, a death date, a gender,
  and any answer that removes a recorded value are *By hand*, because
  ParishSoft's API cannot carry them (see the
  [handling registry](../data/spec.md#proposed-changes) and the
  [data workflow](../data/spec.md#review-and-publication)). A deceased Member
  has two rows, *Deceased* and *Death date*: Staff enter them in ParishSoft
  together and mark both entered.
- the worklist's shared columns: **Baseline** (ParishSoft's value when the
  Family answered; blank where no verified ParishSoft read exists for the
  field, today every Family household field), **Family's answer**,
  **Reviewer's value** (an Edit's value), **ParishSoft now** (on Conflict
  rows only), **Stage** (with the decision behind it, an earlier decision a
  Conflict holds but that is not in force, and a failed update), **Submitted**
  and **Last decision** (who, when and the note of the latest review or
  mark), then the row's action icons. Values appear in full: Staff need them
  to type into ParishSoft, and every view and download is audited. A blank
  Family's answer means the Family removed the value: remove it in
  ParishSoft, or Ignore the row to keep it. A birth date the Family gave as
  Unknown shows *Unknown*.

Family, Family DUID, Who, Member DUID, What changed and How it reaches
ParishSoft sort the table; Stage is chosen with the Stage filter.

A *Conflict* means ParishSoft changed after the Family answered: ParishSoft
now differs from the Baseline. It never resolves by silent last-write-wins:
the reviewer decides again against ParishSoft's current value
([below](#automatic-census-review)).

### Census change filters and downloads

The filters sit above the table, always open, as on every
[worklist](../ui-conventions/spec.md#admin-tables): **Stage** first, with
each stage's count (To review by default; Conflict, Ready to update,
Updating, To enter by hand, Entered by hand, Updated in ParishSoft,
Confirmed, Ignored, History for superseded and cancelled rows, and Every
stage, which counts everything but History), then a Family name or DUID
search, how the change reaches ParishSoft (Automatic and by hand, Automatic
only, By hand only) and kind of change (contact details, moved, deceased,
new Member). There is no submitted-date filter, as on
[Additional information](#additional-information) (#1888). The page has no
summary: the counts are the Stage filter's. Sorting, paging and filters act
[in place](../ui-conventions/spec.md#in-place-controls), and the address
bar then shows the view's own address with its Stage, closed filters and
table choices, so a reload draws the same view; the name search stays in
POST bodies, so a reload clears only it, and a link carrying a search is
refused (R38). The removed Submitted from and Through filter's `start`,
`end` and `zone`, in a link or a form (a page drawn before #1888), are
ignored, never refused; `pk-admin report census` and `export census` keep
their own `--start`, `--end` and `--zone`.

The filtered list downloads, from the pane below the table, as CSV, XLSX or
PDF, every format with the page's values
([downloaded files](../ui-conventions/spec.md#downloaded-files)), audited with
a count like the other report downloads. The PDF is the shared table, its
Submitted time in the parish's date format; CSV and XLSX give it in ISO 8601.
After its table sheet the XLSX has a "Report information" sheet stating what
the PDF's page frame states: parish, campaign, download time and zone, the
number of changes and the privacy line
([#926](https://github.com/epiphany40223/parishkit/issues/926)); like the
PDF, it does not list the filters. `pk-admin export census` writes these
same files through the same writer.
The files' columns are: Family, ParishSoft status (the page's
[Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
badge in a column of its own, with Family blank, for a Family the data no
longer has; blank otherwise), Family DUID, Who, Member's ParishSoft status
(the Who cell's badge, for a change whose Member is no longer an active,
living Member of its Family; blank otherwise,
[#1752](https://github.com/epiphany40223/parishkit/issues/1752)), Member
DUID worded as on the page (the DUID, "New Member", or blank for a
household change), What
changed, ParishSoft now, Family's answer, Reviewer's value, How it reaches
ParishSoft, Stage (the page's stage word), Submitted, then Recorded by,
Recorded at and Note, the latest review or mark
([#932](https://github.com/epiphany40223/parishkit/issues/932),
[#528](https://github.com/epiphany40223/parishkit/issues/528)). Until
[#1683](https://github.com/epiphany40223/parishkit/issues/1683) the Stage and
Reviewer's value columns were Status (with the words *To do*, *Published*
and so on) and Edited value; a spreadsheet that reads them by heading needs
updating. The page's Actions column has no file column. Sixteen columns
share the PDF's one landscape table, so its Submitted and Recorded at times
keep the date on one line and the time on the next. The downloads move to
the shared export component with
[#1687](https://github.com/epiphany40223/parishkit/issues/1687).

The page and its downloads are enough for Staff to apply every change by hand
if publication is not ready.

### Manual census resolution

A By hand change is reviewed like any other
([decision 2](../review-pipeline/spec.md#decisions)): **Accept** moves it
from *To review* to *To enter by hand*, and **Entered by hand** (a check in
a box) records that someone typed it into ParishSoft (execution
*resolved_external*). On a *To review* By hand row, Entered by hand accepts
and marks it in one step, so Staff working through a list are not slowed
down. Before #1683 these rows were *To do* and went straight to Entered in
ParishSoft or Ignore; the page's help says that they are now To review. A
Conflict By hand row is accepted first, then entered. Entered by hand is
final, as every resolved outcome is: a mistaken mark is corrected in
ParishSoft, and a later different Family answer creates a new row (the same
answer again keeps the entered change,
[resubmission](../review-pipeline/spec.md#resubmission-while-the-setting-is-off)).
Its dialog says it
is final and to check ParishSoft first. Who, when and the note are kept as
history.

Before #528 the web role could change a proposal only after a later Family
response, to *superseded*, *cancelled* or *resolved_upstream*, so marking
shipped with forward migration 0060, following the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy).
Each mark is one immutable resolution row (who, when, the note), which the
database pairs with the proposal's result and a `census_change_updated`
audit event naming only the versions. The web role may set
*resolved_external* only with such a row, and the row's own guard checks the
actor, the campaign, the proposal's version and its starting state again (a
By hand change, pending or in conflict, not ignored). The page records
Ignore and Back to review as reviews (below); the resolution row's
*ignored* and *reopened* actions remain for `pk-admin census resolve`.

Since Family addresses are not yet written by publication (see the
[data workflow](../data/spec.md#review-and-publication)), an address
entered in ParishSoft stays *To enter by hand* until it is marked.

### Automatic census review

Every census change is reviewed on the worklist by Staff or an Administrator
([decision 1](../review-pipeline/spec.md#decisions)), with the row's icons
or the bulk bar, each decision with an optional note, acting
[in place](../ui-conventions/spec.md#in-place-controls):

- **Accept** (a check mark): decision *approved*. An Automatic change
  becomes *Ready to update*, waiting for an Administrator's update run
  ([Publish to ParishSoft](#publish-to-parishsoft)); a By hand change
  becomes *To enter by hand*. On a *Conflict*, Accept is decided against
  ParishSoft's current value: it is today's "use the Family's value anyway"
  ([#528](https://github.com/epiphany40223/parishkit/issues/528), ADM-09.03).
  The review records ParishSoft's current value, and the ParishSoft
  snapshot it came from, as the *accepted base*, and the change becomes
  *pending*. Publication then merges against the accepted base instead of
  the Family's baseline ([data workflow](../data/spec.md#review-and-publication)),
  so it writes the Family's answer (or the reviewer's value) only while
  ParishSoft still has the value the reviewer saw. The Family's baseline is
  never changed. Accept on a Conflict whose ParishSoft value is unknown (a
  change ParishSoft could not be read for) is not offered; it waits for a
  refresh.
- **Edit** (a pencil): Accept with a value, the reviewer's value that is
  carried forward instead of the Family's answer, shown as *Accepted
  (edited)* ([#528](https://github.com/epiphany40223/parishkit/issues/528),
  ADM-09.04). The Family's answer is never changed and stays in its column
  beside it. It is offered for every field a value can be typed for: a
  Member's names, prefix, suffix, birth date, gender, marital status,
  email, phones, language and death date, and a Family's home and mailing
  address. Moved, deceased, the email opt-out and a New Member are Accept
  or Ignore only. The editor starts with the value carried forward now (the
  reviewer's value, else the Family's answer): a date field no later than
  the campaign's today for a birth or death date, the census choices for
  Gender and Marital status, an address's own parts checked as the Family form
  checks them, and otherwise text no longer than the field takes (for an
  Automatic change, no longer than ParishSoft's request schema takes:
  First name 30 characters, Email address 150, a phone 25). The value is
  read as the Family form reads its own answer (a phone must be a whole
  number, email addresses are checked and joined, a required name cannot
  be blank), and an Automatic change's must fit ParishSoft's request schema
  as publication will send it; Gender is held to its census choices only,
  since its ParishSoft encoding is not settled yet. A value the server
  refuses is answered with the reason at the field in the dialog, and the
  field is marked in error until it is edited
  ([field errors](../ui-conventions/spec.md#field-errors-and-live-checks));
  nothing is recorded. A blank value is refused: Ignore leaves ParishSoft as
  it is. A value equal to the Family's answer is a plain Accept, so
  Reviewer's value never repeats it. On a Conflict, Edit accepts against
  ParishSoft's current value as Accept does. Edit is per change only, never
  part of bulk review.
- **Ignore** (an eye struck through): decision *ignored*, leaving ParishSoft
  as it is.
- **Back to review** (an arrow turning back): decision *unreviewed*, on an
  accepted change not yet in its update or entered, or on an ignored one.
  Reopening an ignored change says why: the note is required. Back to review
  on a change accepted over a Conflict puts it back to *Conflict*, and the
  accepted base no longer counts, so no later Accept can publish over
  ParishSoft's value without deciding the Conflict again. A reviewer's
  value is kept (a later Accept keeps it too) until the next Edit.

The address editor (Edit's, and [Add](#census-add)'s for a Family address)
asks for the parts in the Family form's order, Country first ([Family
census](../parishioner-portal/spec.md#family-census), #1785): Country,
Street, "Street, second line (optional)", City, State, and ZIP code. Country
starts as the address's own, or United States for a blank address. For the
United States, State is the Family form's drop-down of the regions the
server accepts ("Kentucky (KY)", submitting the code) and the postal field
is labelled "ZIP code"; for any other country, State is free text labelled
"State, province or region" and the postal field "Postal code". Changing
Country swaps the State control in place, at the same position, and each
State control keeps its value while the other shows, so switching back
restores it; only the shown one is sent. A US region the list does not offer
(parish records take any text) is kept, shown and selected, so the server
refuses it at State ("Choose a state from the list.") rather than it being
silently dropped. As before, the ZIP code's format is checked by the server
only, which refuses it at the field. Unlike the Family form, typed region
text does not carry over to the drop-down on switching to United States: a
reviewer corrects one address at a time and picks the state. The server
checks the parts with the Family form's own address rules and messages.

Each is offered only while the change's stage allows it
([individual actions](../review-pipeline/spec.md#individual-actions)): never
while it is *queued* for publication (Updating) or once it has a final
outcome. Each review changes the proposal's version, so a publication plan
that included it no longer matches and must be confirmed again with the
review in view. If a refresh brings a new ParishSoft value first, the
accepted base no longer counts either: an open change's approval goes back
to *To review* and the change is compared with the Family's baseline again
(a change already queued for publication keeps its approval, and its
publication then meets a conflict). A Family's later same answer does not
carry the accepted base either
([resubmission](../review-pipeline/spec.md#resubmission-while-the-setting-is-off)):
the change comes back as a *Conflict* that shows the earlier Accepted as
held, not in force, with the history line "Reopened: the Family answered
again, so the earlier Accept over ParishSoft's changed value no longer
counts".

**Bulk.** Accept, Ignore, Back to review and Entered by hand sit in the bulk
bar ([bulk actions](../review-pipeline/spec.md#bulk-actions)). Every row
with a bulk-eligible action has a box, By hand rows included, and a button
is available only while every ticked row offers it. The dialog names the
exact count ("Accept 3 changes?") and takes one optional note, recorded on
each change (required when a ticked ignored change is sent back to review).
The selection covers the rows on the page shown, at most 100. A page
showing more rows offers no boxes and says to show fewer. Each change is
recorded exactly as its own icon would record it (its own review or
resolution row, version and audit event), all or none, in one work
transaction with the chosen changes locked in primary-key order, each
against the version its row showed. A change someone else updated first
leaves every chosen change as it is, and the notice names it. Each change's
request key is derived from one key per drawn page, so a repeated post
records nothing more. Bulk review adds no audit action of its own.

While other campaign work holds the campaign (a restore review, or a
cleanup, purge or go-live step), the page offers no row or bulk actions and
says why; the list and its downloads still read. On the command line,
`pk-admin census review`, `census edit`, `census resolve-conflict` and
`census resolve` record reviews and marks through the same services
([admin automation](../admin-automation/spec.md#reports-and-exports)); they
keep their earlier words until
[#1688](https://github.com/epiphany40223/parishkit/issues/1688) renames them.

Each review is one immutable review row (who, when, the note and the
resulting decision and edit fields), added by forward migration 0096 under
the [post-launch schema policy](../operations/spec.md#post-launch-schema-policy).
The database pairs it with the proposal's result at the next version and a
`census_change_updated` audit event naming only the versions. The web role
may change a proposal's decision only with such a row, and the row's own
guard checks again that the actor may review, the campaign admits changes,
the version is current, the change is open, and the decision or the edit
changes. Migrations 0107 and 0108 added the edit and the accepted base.
Forward migration 0128 (provisional number,
[#1683](https://github.com/epiphany40223/parishkit/issues/1683)) widens the
guard for the pipeline: a current Staff member or Administrator; any census
change the worklist lists, By hand ones included; a decision and an edit
together when the decision is Accept (Edit is Accept with a value), also
with an accepted base on a Conflict; and a required note to send an ignored
change back to review. An edit's value must have its field's shape (a phone
record for a phone, an address of its six parts with a street line for an
address, text otherwise, at most 1,024 bytes), must not be blank and must
differ from the Family's answer, as the census compares values; moved,
deceased, the email opt-out and a New Member take no edit. Clearing needs an
edit to be set. The value's full rules stay in the service, except for an
address: forward migration 0130 (provisional number,
[#1719](https://github.com/epiphany40223/parishkit/issues/1719)) holds an
edited Family address to the same SQL address rules as the Family's own
answer (a known country; for a US address a state or territory and a ZIP or
ZIP+4; a city; each part's length; normalized single-line text), so no
writer can store an address the Family form would refuse.

### Census Add

**Add**, in the selection bar, records a census change no Family answer
asked for ([Reviewed stage](../review-pipeline/spec.md#reviewed-stage),
[#1710](https://github.com/epiphany40223/parishkit/issues/1710)). Staff and
Administrators see it; it is gone while other campaign work holds the
campaign, as every action is. Its dialog asks, in order, what changed (a
Member field Edit takes a value for, or the Family's home or mailing
address), the Family DUID, the Member DUID for a Member field, and the new
value in the editor Edit draws for that field; only the controls the chosen
field needs are shown. It may name the Family's ignored change it is added
in place of, and takes the optional note.

The server checks it as the [Edit](#automatic-census-review) checks a value,
by the handling registry's handling for the field (a Family address by the
Family form's address rules), and also that the Family responded in this
campaign, that the current ParishSoft data lists the Member as current in
that Family, that the field has no open change already (an open addition,
or the Family's own change not ignored, which is decided with its own icons:
an Edit corrects the Family's value), and that a change it is added in place
of is that Family's ignored one. A refusal is shown in the dialog at the
field it concerns, with its own code (`add_field`, `add_family`,
`add_no_response`, `add_member`, `add_not_in_parishsoft`, `add_value`,
`add_exists`, `add_in_place_of`); nothing is recorded. The same request key
again with the same Add records nothing more, and with any other Family,
field, value or replaced change is refused.

An added change is a row of the worklist like any other: Who, What changed,
the Reviewer's value (the added value) and no Family's answer, Submitted
when it was added, and Last decision its creator, time and note. Its stage
is *To enter by hand*, with *Added by the parish* under it and its Accept
drawn selected, and *How it reaches ParishSoft* is *By hand*: until the
update run is rebuilt
([#1688](https://github.com/epiphany40223/parishkit/issues/1688)) nothing
writes an addition to ParishSoft, whatever the handling registry says of its
field. It offers only **Entered by hand** (final, as for any change) and
**Back to review**, which withdraws it: it moves to *History*, and the field
may be added again. Both are in the bulk bar too, together with the
Family's own changes, all or none; the bulk dialog says a ticked added
change is withdrawn. Each ignored change it replaced shows "Added in its
place:" with the field and value of every addition made for it under its
stage, and the added change says it was added in place of an ignored change.

The field never has two open changes by the worklist's own decisions: while
an addition of the field is open, an ignored change of the same field
offers no Back to review, and one posted anyway is refused (`reopen_added`;
withdraw the addition first), as is `pk-admin census review` or `census
resolve` reopening it; the check runs under the work order. A later Family response may still ask for the
same field again; the open addition then says the Family has an open change
of this field too, so Staff decide that one and withdraw the addition if it
is no longer needed. Its Baseline is ParishSoft's value of the field when it
was added (blank where it cannot be read, as for every Family address),
which the database records with the snapshot it came from: the baseline the
refresh rules re-evaluate an open addition against once
[#1689](https://github.com/epiphany40223/parishkit/issues/1689) ships. Until
then a refresh leaves additions as they are. The downloads
and `pk-admin report census` include additions; the other `pk-admin census`
commands act on the Family's changes only.

An addition is not a proposed change: proposals belong to the Family submit
path, whose guards stay as they are, and the Family form never shows an
addition. Forward migration 0129 (provisional number) adds two
migration-owned tables, each INSERT only: the addition (who, when, the note,
the Family, the Member or Family, the field, the value and the change it
replaced) and its one later step (Entered by hand or withdrawn, at most one
per addition). Their guards check the actor and campaign again and,
for an addition, the rules above, the value's shape as 0128 checks an
edit's, and record the baseline themselves. Each row is paired with a
`census_change_updated` audit event naming only its id and version.

### Publish to ParishSoft

**Access:** Administrator only. Staff and Ministry leaders never see the
page or its link, and its addresses deny them.

**Publish to ParishSoft** ([#528](https://github.com/epiphany40223/parishkit/issues/528),
ADM-09) is the page that writes approved Automatic changes into ParishSoft.
It drives the publication pipeline the
[data workflow](../data/spec.md#review-and-publication) defines (preflight,
plan, confirmation, fenced writes, final refresh) and the
[background tasks](../background-processing/spec.md#parishsoft-publication)
that run it; this section says only what the Administrator sees and does.
It is at `/admin/reports/census/publication/`, reached from a **Publish to
ParishSoft** link above the Census changes table, and is not a menu entry:
its breadcrumb is Home › Review work › Census changes › Publish to
ParishSoft.

What can be written is narrow. ParishSoft's v2 API writes only contact
fields: a Member's contact record (names, birth date, language, gender,
email and phones) and a Family's contact record. Publication writes only the
Member changes the
[handling registry](../data/spec.md#proposed-changes) marks API-writable,
and never one that removes a value, which the API cannot do; Family
addresses wait for a verified address read, and every other census change
(deceased with its date, moved, new Members and the rest), as well as Ministry
rosters, pledges and share methods, is entered by hand from Census changes
or its own page. Publication never changes a Family's code or link, and
never sends mail.

#### Publishing switched off

Publication ships **off**, and this page stays inert until the
Administrator turns it on. While the deployment's `parishsoft_publication`
setting is off:

- Census changes shows **Publish to ParishSoft** unavailable, with its
  reason in a reserved line beside it ([prerequisite
  gates](../ui-conventions/spec.md#prerequisite-gates)): "Publishing to
  ParishSoft is switched off. Type Ready to update changes in by hand."
- The page itself, opened by its address, says the same in its page help and
  offers no button. Nothing is queued, read or written, and every post to
  it is refused (`publication_off`).

Turning publication on is the Administrator's decision, made outside the
page, and it needs two things first, in this order, as the
[publication guide](../../../guides/stewardship-member-requests.md#parishsoft-publication)
says:

1. **The contact read is confirmed.** The publication gateway's read
   refuses with `read_unconfirmed` until a reviewed change replaces it.
   That change needs human evidence first: an operator runs the human-run
   `pk-stewardship smoke --target parishsoft_contact ... --send` round trip
   against one chosen test Member and pastes its `confirmed: true` line on
   [#528](https://github.com/epiphany40223/parishkit/issues/528), as the
   [Member contact round trip](../../../guides/stewardship-smoke-tools.md#recording-the-result)
   says. No pull request replaces `read_unconfirmed` without that comment,
   and CI never runs the tool.
2. **The Administrator switches it on**, on both the web and worker
   services. No pull request, agent or default turns it on.

If the switch is on while the read is still unconfirmed, a check fails as a
whole in the background and plans nothing; the check's status region says
"ParishSoft's contact details cannot be checked yet, so nothing was
planned" (the preflight task's `census_publication_read_unconfirmed`
failure; see [Publish refusals and errors](#publish-refusals-and-errors)).

In Testing mode the page works up to the check and stops there: it shows
the plan with the line "Testing mode: ParishSoft was checked, and nothing is
written or marked", and offers no Publish button
([data workflow](../data/spec.md#review-and-publication)).

#### Checking ParishSoft

The page opens with what is waiting: the number of approved Automatic
changes not yet published, for how many Members, with a link back to Census
changes to review more. **Check ParishSoft** is available while at least one
approved, open Automatic Member change exists and no publication run is queued
or running; otherwise its reserved line says why ("No approved changes to
publish. Approve changes on Census changes first." or "Publishing is
running."). The running gate is the page's and the command line's: the
preflight service itself does not refuse a check during a run, so
`publish-check` states it too. It acts [in place](../ui-conventions/spec.md#in-place-controls),
as Refresh now does on [Refresh from
ParishSoft](../admin-portal/spec.md#manual-parishsoft-refresh): it queues
the preflight task with a request key drawn with the page, so a repeated
click returns the same check, and the region below the button shows the
check's live status through the task's passive status read, with a link to
its Background task page. The check reads every affected Member from
ParishSoft without the cache and without the source lease, so it can run
beside a refresh. When it ends, the page redraws in place with the plan.

The page shows the current campaign's newest plan, whoever made it, with
"Checked by (sign-in address) at (time)" in browser-local time. Older
plans are not listed; their history is in [System
logs](#system-logs) and Background work. The plan table is a shared [Admin
table](../ui-conventions/spec.md#admin-tables), grouped by Family and then
Member, with sorting and paging in place, and the Census changes columns
that apply, in the same order: Family, Family DUID, Who, Member DUID, What
changed, **ParishSoft at the check**, **Will write**, and **Outcome**:

- **Will be written**: ParishSoft still held the value the change was
  compared with (the Family's baseline, or the accepted base after *Use the
  Family's value anyway*), so ParishSoft at the check shows that value and
  Will write shows the edited value, else the Family's answer.
- **Already in ParishSoft**: ParishSoft already holds the value to write,
  so ParishSoft at the check and Will write both show that value (the
  edited value, else the Family's answer). Nothing is written, and the
  change is marked *Already in ParishSoft* (`resolved_upstream`) at the
  check, so it leaves the To do list (Production mode only). The mark is
  final: Reopen on Census changes cannot undo it (see the
  [data workflow](../data/spec.md#review-and-publication), step 4).
- **Conflict**: ParishSoft changed since the Family answered. Nothing for
  this Member is written until the change is resolved on Census changes
  (Ignore, or *Use the Family's value anyway* after the next refresh shows
  the new value).
- **Cannot check**: ParishSoft could not be read for this Member. Nothing
  for this Member is written; check again later.
- **Held back**: a change that would be written, on a Member that has a
  Conflict or Cannot check, says so, since a Member gets all of its changes
  or none of them.

The plan holds no values (only ids, versions, outcomes and payload hashes;
see the data workflow), so the page reads the values from the changes
themselves. For a Conflict or Cannot check row it therefore shows no
ParishSoft value: "Changed since the Family answered" or "Could not be
read"; the new value appears on Census changes after the next refresh.
Values are shown in full, as on Census changes, and each view is audited
with its count. Changes the plan leaves at ParishSoft's value (not approved,
or By hand) are not rows; a line under the table counts them ("4 other
changes for these Members are not approved and stay as they are in
ParishSoft"). The page has no download: Census changes' files carry every
value.

Above the table a summary line counts the plan: "12 changes for 5 Members
will be written. 2 Members are held back. 3 changes were already in
ParishSoft." These are the counts the confirmation binds.

#### Confirming and running

**Publish 12 changes** (the count is the plan's) opens the shared [review
dialog](../ui-conventions/spec.md#review-dialog) as a confirmation, since
the table already shows every value. Its title asks "Write 12 changes for 5
Members to ParishSoft?", and its sentence says: "Each Member's contact
record in ParishSoft is read again and replaced with its current details
plus these changes; nothing else changes. 2 Members are held back. This
cannot be undone from here." It offers **Apply** and **Cancel**. Apply posts
the plan and the counts shown, and the server confirms the plan and queues
its run in one transaction (`confirm_and_publish`), re-checking every rule
the data workflow lists: Production mode, the switch on, the plan less than an hour old, every
planned change at the version the plan recorded, the counts equal to the
plan's, and no change being written by another unfinished plan. A repeated
Apply returns the same run.

The button is unavailable, with its reason in its reserved line, when the
plan writes nothing, in Testing mode, while a run is queued or running, while
other campaign work holds the campaign, and once the plan is an hour old
("This check is more than an hour old. Check ParishSoft again."); the page
script re-checks the age on `pageshow`. A newer plan made in another tab
or by another Administrator makes Apply refuse the older one
(`publication_plan_superseded`), so the Administrator always confirms the
newest check they have seen. No SQL guard or service checks this today:
the page's view and the `publish-confirm` command check it in Python,
through one shared helper, before calling `confirm_and_publish`, so both
refuse an older plan the same way.

Once the run is queued the dialog closes and the page redraws in place: the
run's live status replaces Check ParishSoft's region ("Writing to
ParishSoft: 3 of 5 Members done"), polled through the task's passive read.
Census changes shows a change as *Being published* only while the worker is
writing its Member: the worker queues a Member's changes when it reaches
that Member under the source lease, so changes of Members not yet reached
still show *To do*. Leaving the page does not stop the run. When the run
ends the page redraws in place, and the Outcome column gives each Member's
result. A run that completes leaves no Member unfinished: the worker closes
every Member it did not publish as `invalidated`, and the page derives the
two "not published" outcomes from the Member's recorded attempts:

- **Published**: written, and read back from ParishSoft as planned
  (`published`).
- **Not written: changed since the check**: `invalidated` with no failed
  attempt. ParishSoft or the change moved after the check, so nothing was
  written; the change is back to *To do* (`pending`), or *To review* if its
  decision changed, and the next check plans it afresh.
- **Failed**: `invalidated` after at least one `failed` attempt. The write
  did not complete, with the last attempt's plain reason ("ParishSoft did
  not answer", "ParishSoft refused the change", "The details read back
  differently"); the change is *To do* again (`failed`). Run again cannot
  retry it, because the plan has finished with that Member: a new **Check
  ParishSoft** plans it again.

**Run again** appears only when the run's task itself ended without
completing (refused partway, stopped after its recovery limit, or crashed)
and the plan still has a Member with no final outcome. It retries that
plan's own run (`rerun_publication`), which reads each Member again before
writing and never repeats a write that landed. It asks first in the shared
confirmation dialog ("Try the 2 Members this run did not finish?"),
without a new sign-in, since the plan was confirmed already and the data
workflow bounds its execution window. After a run that completed, the page
offers Check ParishSoft instead.

When a run that wrote at least one Member ends, the worker requests one full
ParishSoft refresh, so ParishSoft now on Census changes catches up. The page
says "ParishSoft is being refreshed so Census changes shows the new values"
with a link to Refresh from ParishSoft. The published changes are already
final; the refresh only updates the copy of ParishSoft's data. It is
recorded as a manual refresh attributed to the Administrator who confirmed
the plan, carrying the publication's correlation id, and its System logs
description says it followed a publication, so nobody mistakes it for a
click on Refresh now. If the request fails, the run's outcome stands, a
WARNING is logged, and the next scheduled refresh catches up.

#### Publish refusals and errors

Every refusal is plain language with a stable reason code, shown where its
control is: Check ParishSoft's in its reserved status line, Apply's and Run
again's inside the dialog, below its buttons
([review dialog](../ui-conventions/spec.md#review-dialog)). Each is logged
by the shared hook ([Admin input
refusals](../operations/spec.md#admin-input-refusals)), never with a value.

| Code | When | The page says |
| --- | --- | --- |
| `publication_off` | The switch is off | Publishing to ParishSoft is switched off. |
| `publication_testing` | Testing mode, at Apply or Run again | Testing mode: nothing is written to ParishSoft. |
| `publication_nothing_approved` | No approved, open Automatic change | No approved changes to publish. |
| `publication_running` | A run is queued or running | Publishing is running. Wait for it to finish. |
| `publication_campaign_held` | Other campaign work holds the campaign | Publishing waits until (the work) finishes. |
| `publication_plan_expired` | The plan is an hour old | This check is more than an hour old. Check ParishSoft again. |
| `publication_plan_changed` | A planned change or the counts changed since the check | Something changed since the check. Check ParishSoft again. |
| `publication_plan_superseded` | A newer plan exists | A newer check exists. Review it first. |
| `publication_overlap` | A change is still being written by another plan | Some of these changes are still being written. Wait for that run to finish. |
| `publication_finished` | Run again with nothing unfinished | Every Member in this check is finished. |

A check or run that fails in the background is not a refusal: the page shows
the task's failure in its status region, as a refresh's does, and the
background rules decide whether it retries. A check whose contact read is
still unconfirmed is one: its preflight task fails permanently with
`census_publication_read_unconfirmed`, and the status region says
"ParishSoft's contact details cannot be checked yet, so nothing was
planned." A ParishSoft organization that is not the configured one stops
the check with a CRITICAL, as for a refresh.

#### Publish audit and logs

The page adds no new state and only one audit action: viewing the page
records `census_publication_viewed` with the number of rows shown, as
viewing Census changes does. Everything else is recorded by the services the
page calls, each event naming ids, versions and counts, never names or
values: `census_publication_preflight_requested` (who asked and when),
`census_publication_planned` (the plan's counts),
`census_publication_confirmed` (the Administrator and the counts confirmed),
`census_publication_recorded` (each Member's attempt and outcome, whose plan
rows name the fields written), `census_publication_rerun_requested`, and the
refusals `census_publication_switched_off`,
`census_publication_read_unconfirmed` and `census_publication_refused`. A
change marked *Already in ParishSoft* at the check is recorded by the plan's
field rows and its version, not by an event of its own. The
[activity groups](../admin-portal/spec.md#activity-groups) of System logs
say which group these types and the command line's join; the page view
stays ungrouped with the other page views.

The structured log has the background tasks' usual lines (start, progress,
outcome) with ids, counts and sanitized failure words only. Every timeout
kill logs what was stopped, its limit and the time elapsed, and an
ambiguous write after a crash is CRITICAL
([critical errors](../background-processing/spec.md#critical-errors-and-notification)).

On the command line the same steps are `pk-admin census publish-check`,
`publish-plan`, `publish-confirm` and `publish-rerun`, through the same
services ([admin automation](../admin-automation/spec.md#reports-and-exports)).

#### Publish page delivery

Built in slices after this specification, each its own pull request; the
[ADM-09 work package](../../../plans/stewardship/admin-portal.md#adm-09-census-review-and-parishsoft-publication-ui)
lists them and the migration number reserved for it. Only the first needs
schema: a new forward migration lets a Production-mode check mark
already-matching changes *Already in ParishSoft*, as the
[data workflow](../data/spec.md#review-and-publication) says, following the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy).
The page, its command-line commands, the final refresh and the activity
group need no schema change. Every slice ships with publication off.

## Financial stewardship detail

**Access:** Admin and Staff only.

This required report closes the operational path for report/export-only
pledges. One row per currently effective live Family response. Every export
format (CSV, XLSX, PDF) has, in the Admin table
[column order](../admin-portal/spec.md#table-column-order), the
Family name first (surname, then heads, as on the
[active parishioner family directory](#active-parishioner-family-directory);
search matches that name and the Family sort orders by surname, then that
name), Family DUID in its own sortable column
([#960](https://github.com/epiphany40223/parishkit/issues/960)), Annual
pledge, Frequency, Approximate installment, Share methods (selected
share-option labels with any Other text), the ParishSoft comparison pledge
and contribution aggregates, Latest response (the time the Family last
updated its response), First response (when it first responded) and,
as the last column, Entered by Staff (described below), in exactly this
order (the Administrator's decisions B1 and E1, #932), and no format
carries internal references (Family version or response reference).

The page shows the same pledges in fewer, narrower columns, so rows stay
short at laptop width
([#1544](https://github.com/epiphany40223/parishkit/issues/1544), following
the [table fit](../ui-conventions/spec.md#table-fit-and-row-height) rules,
with the Administrator's three width decisions of 2026-10-17):
Family, Family DUID (still its own sortable column, as the
[column order](../ui-conventions/spec.md#table-column-order) requires),
Annual pledge, Installment (a compact "$103/mo", with /wk, /qtr or /yr
for the other frequencies and the unit spoken in full to a screen reader;
the frequency alone, or why there is none, when there is no installment),
Share methods, ParishSoft, and Latest response. Money on the page is in
whole dollars, rounded half away from zero; a screen reader hears the exact
amount, and the exports and the summary keep the cents. Share methods shows
the first chosen method as a chip; with more, a "+N" chip opens a toggletip
listing every method in order, with any Other text after its label ("Other:
stock gift"); a single method with Other text has an "i" toggletip with that
text. The ParishSoft column stacks the comparison pledge total ("Pledged")
with the contribution total ("Contributed") under it in one cell, each named
for a screen reader; its heading carries the comparison years and names
Pledged and Contributed in that order, each with its period toggletip.
Neither ParishSoft total is sortable: like frequency and share methods, the
projection cannot order them without a schema change. Latest response is the
compact date alone, in the browser's time zone, with a "Staff" chip when
Staff entered the current response. The page has no First response column
(its date filter stays) and no Entered by Staff column; the exports keep
both. No cell wraps (the ParishSoft cell has one line per total): a table
still wider than the space beside the Admin menu scrolls in its own box,
and money and DUIDs are right-aligned. The page marks a Family the data no longer has, or marks
inactive, with the shared
[ParishSoft status](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
badge in or beside its Family cell, and the exports split it into a
ParishSoft status column right after Family, blank for a current Family
(#1216). The page's ParishSoft heading carries the
comparison period's years, as the Family form words them ("ParishSoft
(2026)", or "(2026–2027)" for a period spanning two years), and its Pledged
and Contributed lines each have a toggletip naming the period's dates. The
comparison period is the
campaign's configured comparison financial period, usually the giving year
before the upcoming stewardship year that Families pledge for; the pledge
total counts ParishSoft pledges dated in it, and the contribution total gifts
dated in it up to the latest giving data read. Every
export format (CSV, XLSX, PDF) heads those two aggregates "ParishSoft
pledged" and "ParishSoft contributed" with the same years ("ParishSoft
pledged (2026)"), and says "ParishSoft" rather than "Source" in the export
metadata that describes them, so a downloaded file names its figures as the
page does. The exports also name each Family as the page does, from the
snapshot their capture was read from, choosing the name form once per file
as the [Additional information](#additional-information) exports do.
Entered by Staff says Yes when Staff entered the Family's current response
through [Open form](#open-form) (#529), and No when the Family
submitted it, so an earlier response Staff entered and the Family later
replaced shows No; it names no Staff member, and the page's help says so.
On the page the same marker is the Latest response cell's Staff chip.
A file captured before frozen migration 0066 added the marker to
the projection leaves the column blank, never No (#794).

Filters include active/inactive, first/latest submission dates, pledge range,
zero/nonzero/cannot contribute, frequency, and share method. The first and
latest response dates (From, Through) are whole days in the viewer's browser
time zone: the form sends the browser's zone with the dates, and a date
without a known zone is refused, never read in another zone
(schema migration 0110). Filters without a zone, from a release before 0110
or a retained export capture, keep their campaign-zone days. The summary of
the matching pledges comes first, as visuals
([#720](https://github.com/epiphany40223/parishkit/issues/720)): tiles for the
Family count, the annual total and the number of Families that cannot
contribute financially (such a Family's frequency reads "Cannot contribute"),
then two [chart engine](#chart-engine) bar charts, "Pledges by frequency"
(each frequency and No frequency) and "Share methods chosen" (each offered
method in its configured order, even one nobody chose, retired methods
together as Unavailable share method, then No share method chosen), so a
filter changes an offered method's bar length, never whether it shows. Each
bar is labelled after its end with its count and its share of the matching
Families, "4 (50%)", and its tooltip and accessible name carry the same
figures, so the charts have no exact-values table
([chart engine](#chart-engine),
[#1266](https://github.com/epiphany40223/parishkit/issues/1266)); a Family can
choose more than one share method, so those shares can add up past 100%. The
summary follows the applied filters in place, and its charts keep their space
until they are drawn again. Exports are CSV, XLSX, and PDF, queued as an
[active parishioner family directory](#active-parishioner-family-directory) export is. No
Ministry leader receives aggregate or Family financial detail.

The page checks its date and pledge filters before Apply (a date, each
range's order, an amount's form). A request that reaches the server with one
broken is refused at that field
([field errors](../ui-conventions/spec.md#field-errors-and-live-checks),
[#1332](https://github.com/epiphany40223/parishkit/issues/1332)): a range out
of order at its Through date or At most, with both ends marked. Its access
audit ends denied.

A reload keeps the view's closed choices
([reload on a POST report](../ui-conventions/spec.md#pages-that-update-themselves),
[#1586](https://github.com/epiphany40223/parishkit/issues/1586)): after each
in-place answer it accepts, the address bar shows the page with Family status, Pledge
amount, Frequency, Share method (a share option's identity, or none),
the sort and the rows per page, each only when it differs from the default,
and the page's GET draws that view. The search, the response dates and the
pledge range stay in the POST body, so a reload clears them and shows the
first page. A GET with any other parameter, a repeated one or a value
the filter's form does not take is refused (400), as a POST's would be: a
Share method is checked for an option identity's form, so one no longer
offered matches no Family. A POST with a query string is refused too.

This report stays the financial stream's statistics page. Reviewing each
pledge and entering it in ParishSoft is on [Pledges](#pledges).

### Pledges

**Access:** Admin and Staff only; no Ministry leader sees it.

**Pledges** (`/admin/reports/financial/pledges/`,
[#1686](https://github.com/epiphany40223/parishkit/issues/1686)) is the
financial stream's [review worklist](../review-pipeline/spec.md#review-worklist)
and its [manual handling report](../review-pipeline/spec.md#manual-handling-reports)
in one page, a menu entry of its own in Review work after Ministry requests.
It was "Pledges to enter" until the Administrator renamed it (R39 finding 3,
2026-10-19); its address is unchanged. The stream's
stages, decisions and resubmission rule are the
[Financial stream](../review-pipeline/spec.md#financial-stream)'s; this
section is the page.

- **Address.** The page's address carries its Stage alone (To review is
  the plain address; [#1820](https://github.com/epiphany40223/parishkit/issues/1820)),
  as [Additional information's](#additional-information) does, so a reload
  after an in-place Stage change draws the Stage on screen. The search stays
  private POST state.

- **Rows.** One row per Family whose current live response answers the
  financial questions: the rows of
  [Financial stewardship detail](#financial-stewardship-detail) with no filter.
- **Filters**, always open: **Stage** first, To review by default, then
  Conflict, To enter by hand, Entered by hand, Entered incorrectly, Confirmed,
  Ignored, Not seen in ParishSoft (only while some pledge has the flag,
  counted beside the stage it is a flag of) and Every stage, each with its
  count; then the Family name or Family DUID search of Financial stewardship
  detail. The refresh places Conflict, Confirmed and Entered incorrectly and
  sets the flag
  ([pledge confirmation](../review-pipeline/spec.md#financial-stream),
  [#1704](https://github.com/epiphany40223/parishkit/issues/1704),
  [#1747](https://github.com/epiphany40223/parishkit/issues/1747)).
- **Columns**, in the shared order: Family (the Family-name link), Family DUID,
  Family's answer (the annual pledge, then its frequency, or "Cannot
  contribute", then the share methods chosen), Reviewer's value (an Edit's
  annual pledge and frequency), Stage, Submitted (the Family's latest
  response), Last decision and Actions. There is no Baseline column: A is
  ParishSoft's amount when the Family answered, $0.00 when unrecorded
  (#1704, #1753). **ParishSoft now**, after
  Reviewer's value, is ParishSoft's upcoming-year amount the latest refresh
  found, shown on a Conflict, Entered incorrectly or Not seen in ParishSoft
  row, as on every worklist; a page with no such row has no such column.
- **Row and bulk actions** are the role table's for the stream: a To review or
  Conflict row offers Accept, Edit and Ignore (a Conflict's earlier decision
  reads "Earlier: Accepted, not in force." under its stage, and the decision
  taken on it records ParishSoft's amount as the pledge's new baseline; a
  decision posted from a page drawn before any later refresh check of that
  pledge, such as a Conflict found or ParishSoft's amount moving under one,
  is refused as someone else's change and the page is redrawn); an Ignored
  row Back to review, with a required
  note; a To enter by hand row Entered by hand and Back to review; an Entered by
  hand row nothing (the mark is final), and neither does an Entered
  incorrectly row until its fixes land
  ([#1737](https://github.com/epiphany40223/parishkit/issues/1737)) or a
  Confirmed one; a Confirmed row with no decision says "Already in
  ParishSoft." under its stage, or "Nothing to enter: ParishSoft holds no
  pledge." when both the Family's answer and ParishSoft are $0.00. An
  Ignored row ParishSoft now matches says "ParishSoft now matches the
  Family's answer" under its stage. The bulk bar offers Accept, Ignore, Back
  to review and Entered by hand, so the Administrator's flow ("a handful of
  manual edits ... and then bulk approve all the financial pledges") is a few
  Edits, then select all and Accept, then Stage To enter by hand, export, type
  the pledges into ParishSoft, select all and Entered by hand.
- **Edit** opens the row's own dialog with the annual pledge (the Family form's
  format and limit: dollars and cents, under $1,000,000,000) and the frequency
  (Weekly, Monthly, Quarterly, Annual, or No frequency for a zero pledge; a
  pledge above zero needs one), filled from the value carried forward, and the
  optional note. Save records Accepted (edited), the Family's answer stays
  beside it, and the pledge to enter is the reviewer's value. A value the
  server refuses is shown at its field
  ([field errors](../ui-conventions/spec.md#field-errors-and-live-checks)).
  Share methods and Cannot contribute are not edited here: Staff change those
  through [Open form](#open-form), as before.
- **Download.** The shared export buttons below the table download the applied
  Stage and search as CSV, XLSX or PDF in the browser's time zone, rendered on
  request as Roster changes to enter's are, with the page's columns and its
  combined cells split
  ([downloaded files](../ui-conventions/spec.md#downloaded-files)): Family,
  Family DUID, ParishSoft status, Annual pledge, Frequency, Share methods,
  Reviewer's annual pledge, Reviewer's frequency, Annual pledge to enter,
  Frequency to enter, ParishSoft annual pledge, Stage, Decision, Entered by,
  Entered at, Submitted, Last decision, Last decision at and Note. Money keeps
  its cents. The two "to enter" columns are filled only for To enter by hand,
  Entered by hand, Entered incorrectly and Confirmed rows; at the other stages
  nothing is to be entered. ParishSoft annual pledge is the latest refresh
  check's amount, or for a pledge Confirmed at the Family's submit the
  amount ParishSoft held then (#1753), blank before any; a flagged row's
  Stage reads "Entered by
  hand, Not seen in ParishSoft".
- **Audit.** Each decision is one immutable history row (frozen migration
  0127, provisional number) with a `pledge_review_updated` event that names
  only versions; viewing and downloading the page are audited as a count only
  (`pledges_to_enter_viewed`, `pledges_to_enter_exported`).

Pledges are never written to ParishSoft (pledges are read-only in its API), so
the stream has no Ready to update stage.

## Limitations

**Access:** Admin and Staff only; both may record its review decisions.

The page is at `/admin/reports/limitations/`. It was "Talents and
limitations" until the Family form stopped asking for talents
([#970](https://github.com/epiphany40223/parishkit/issues/970)); the old
address is gone, with no redirect.

The page is the cannot-participate stream's
[review worklist](../review-pipeline/spec.md#review-worklist)
([#1685](https://github.com/epiphany40223/parishkit/issues/1685)), at the
same address; its stages, words and per-role actions are the
[review pipeline](../review-pipeline/spec.md#cannot-participate-stream)'s.
From each Family's currently effective live response, one table lists a row
for each Member who cannot participate in any ministries and a row for each
Family that cannot attend Mass or prayer services (see
[Family portal](../parishioner-portal/spec.md#cannot-participate-in-ministries)).
Its columns follow the
[column order](../admin-portal/spec.md#table-column-order): Family (the row
header, named as the
[active parishioner family directory](#active-parishioner-family-directory)
names it, surname then heads, the name opening the Family's Timeline),
Family DUID, Member, Member DUID (blank for a Family's row and for a
proposed Member, who has no DUID yet), the Family's answer ("Cannot
participate in any ministries" or "Cannot attend Mass or prayer
services"), Stage, Submitted (the response's time), Last decision and the
row's actions. There is no Baseline (ParishSoft holds none of these
answers) and no Reviewer's value (no Edit). Testing responses are excluded.

A Family's **Cannot contribute financially** answer (the `cannot_give` flag
on its $0 pledge) is listed here too, as a Family row whose answer reads
"Cannot contribute financially" (R39 finding 11, the Administrator,
2026-10-19), so every limitation a Family gave is on one page. It is not a
Limitations item: it is decided once, on [Pledges](#pledges), so its Stage
is its Pledges stage, its Last decision is Pledges' last decision, it has
no box and no action icons, and its Actions cell is an "Open in Pledges"
link that opens Pledges at that stage, named for screen readers by its row
("Open in Pledges: the answer of" and the Family's name, which starts with the visible
text, so voice control finds it). It is listed for viewers who may
open Pledges (Administrators and Staff) while the campaign has the
financial module, only while Show is everything (the Show choices name the
two Limitations answers only) and matching the search as Pledges' search
does. Its stage counts in the Stage choice whose value it shares (To
review, Ignored); a pledge at a stage Limitations does not name (To enter
by hand, Entered by hand, and any later Pledges stage such as Confirmed) is
listed, and counted, under Every stage only, so a Cannot contribute answer
that needs no work never shows in the default To review view. A Pledges read that cannot be shaped
leaves these rows out with a logged warning rather than failing the page,
and `pk-admin report limitations`, which prints only the summary, does not
read them. The PDF's frame counts each kind on its own (Members, Families
that cannot attend Mass, Families that cannot contribute financially), and
the XLSX's Report information counts the Families table's rows as Family
rows, since one Family can be listed for both.
Talents that older responses recorded are never shown: no talent column,
count or filter appears on the page or in the downloads, and no Member is
listed only for such talents.

The filters are always open: **Stage** first (To review by default, each
choice with its count), then a name or Family DUID search and a Show choice
(`show`) of everything, cannot participate or cannot attend; any other value
is refused. No summary sits above the table: the counts are the Stage
choices'. Staff and Administrators Accept (To handle: the office will act on
it), Ignore, mark Handled, and send a decision Back to review (a note is
needed to reopen an Ignored answer), one row at a time or in bulk, all or
none, as the pipeline's
[role-specific actions](../review-pipeline/spec.md#role-specific-actions)
allow. Handled is final. There is no Add.

No item is stored when a Family submits: each row is a current live answer,
as the report has always read it, so the Family's form and its submission do
not change. Each decision is one immutable row
(`stewardship_limitation_review`, schema migration 0126) naming the Family,
the Member (or the Family itself, for cannot attend), the live response it
was decided on, the decision, the note, who and when, paired with a
`limitation_reviewed` audit event that names only versions. A decision stays
in force until a later live response from the Family answers the other way,
so answering again unchanged keeps it; a response that answers the other way
removes the row from the page, and a later response that gives the answer
again starts a new To review row (the pipeline's
[resubmission rule](../review-pipeline/spec.md#resubmission-while-the-setting-is-off)).
A response that leaves the answer out (a section not shown, a Member out of
scope) changes nothing: the row is shown only while the current response
gives the answer, and its decision is still in force when it does again.
Every decision is checked against the row's version, the number of
decisions recorded for that answer, as the page showed it; a row someone
else changed first is left as it is and the notice says so. The report reads
through `stewardship_limitations_report_v2` (migration 0126), which returns
what `stewardship_limitations_report_v1` (migration 0056) returns plus each
row's answer key, Family record and latest decision, and whether that
decision is still in force; it takes the same `search` and `show` values.

The export pane below the table downloads the filtered list immediately as
CSV (Members, then Families),
XLSX (one sheet each, then a "Report information" sheet stating what the
PDF's page frame states: parish, campaign, download time and zone, both
counts and the privacy line) or PDF (one table each under the table's
heading, the Families table starting on a new page, in the shared page frame
with both counts), with times in the browser's zone
([time zones](../ui-conventions/spec.md#downloaded-files)), applying the
Stage filter too. Each table has the columns Member, Member DUID, Family,
ParishSoft status, Family DUID, Cannot participate in ministries, Latest
response and Stage for Members, and Family, ParishSoft status, Family DUID,
Limitation, Latest response and Stage for Families, whose rows include the
Cannot contribute financially answers the table lists, each with its
Pledges stage. Limitation names the Family's limitation in the page's
words, "Cannot attend Mass or prayer services" or "Cannot contribute
financially", so a reader of the Families sheet sees what each row is about
(R39 finding 11); the Members sheet says it with its Cannot participate in
ministries column, which reads Yes on every row. ParishSoft status splits out the page's
[badge](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
for a Family the data no longer has, whose Family cell is then blank
(#1216); the report reads no activity, so it marks no inactive Family. Every format carries these columns and rows
([downloaded files](../ui-conventions/spec.md#downloaded-files)); the PDF
words Latest response in the parish's date format. A download is rendered in memory on the web
connection under the interactive campaign read guard, not through the
dedicated download pool, whose login cannot read the response and source
data the report needs. `pk-admin export limitations` writes these same
files through the same writer, every stage
([admin automation](../admin-automation/spec.md#reports-and-exports)).

Viewing and downloading are audited with the row count, the Show choice
(`report_filter`, a closed word), whether the search box was used
(`search_used`) and the ParishSoft snapshot read (`snapshot_id`, recorded
only when a refresh did not promote a new one while the report ran)
([#556](https://github.com/epiphany40223/parishkit/issues/556)). The events
keep the names they had as Talents and limitations
(`talents_report_viewed` and `talents_report_exported`), and System logs
still word older events' `option` choice and `talent_option_id`. The search
text itself is never recorded, because it can name a Family. These answers
are never written to ParishSoft. The follow-up queue on Ministry requests
also notes when
a leave comes from a Member who cannot participate in any ministries.

## System logs

**Access:** Admin only.

The report behavior, levels, source filters, timezone export, and CSV, XLSX
and JSON Lines formats (CSV and XLSX with plain headings, JSON Lines with
program keys) are defined by the
[Admin log specification](../admin-portal/spec.md#logs).
It is included in the shared report/export audit pipeline but not offered to
Staff.

## Daily email report parity

A report email's fact blocks use the same query/calculation service and chart
data as the participation/statistics web reports for the report's stored as-of
point ([as of](../report-emails/spec.md#as-of)) and default to Historical as
of day. The report occurrence records the promoted source
snapshot, effective-submission version cutoff, selected population scope,
report parameters, and campaign-local day boundary. That boundary is resolved
using the immutable campaign timezone recorded with the campaign, not the
current Parish default.
The occurrence waits for and records the exact ready `CampaignDailyFactSet`.
The linked report opens in an authorized pinned-snapshot mode using exactly
those inputs and fact generation even when current eligibility later changes.
Separate implementations that can drift are prohibited. The email simplifies
interaction into an image/text table but its values must be reproducible from
those recorded inputs. Its [response funnel](#response-funnel) is reproduced
from durable timestamps instead, at the report's as-of moment.
