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

The campaign selector, the campaign UUID in report URLs and the default to the
most recent retained campaign are superseded for the Admin portal by the
[navigation decisions](../admin-portal/spec.md#navigation-decisions) 10, 15
and 19: reports show the current campaign, report URLs name no campaign, and
the selector links stay greyed out until the single-campaign change (#145).
Exports keep their own identifiers.

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

CSV is UTF-8 with a header row and CRLF-compatible output. Cells beginning with
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
zone on the right. Directory and [response list](#response-lists) PDFs add
short count and filter lines under the title. The footer holds the privacy line, any Testing-mode note and "Page N
of M". Text is set in proportional DejaVu Sans with a DejaVu Serif title,
fonts matplotlib already bundles. Wrapping measures the fonts' glyph widths
and never drops a character of a value it draws, and characters the fonts
cannot draw become visible escapes. Inside the frame, the Family-code
directory is a zebra-striped table whose heading row repeats on every page,
with each phone number and each head's emails on its own line; each response
list is the same table; the postal mail merge is a grid of address cards.

A field/value report (additional information, Ministry, financial, Family
test names, packets) prints one card per record. The
PDF is the readable view; CSV and XLSX stay the complete audit files with
every column (except as a report's section notes). So that a page is easy to
scan:

- The first card, "About this report", shows counts and amounts as large
  tiles, then the remaining details. It leaves out what the page frame
  already states (parish, campaign, capture time, privacy and Testing-mode
  lines). Filters and sort read as words ("Search: none · Sort: newest").
  The note on escaped characters appears only when the file contains one.
- Each record card is titled "Name (DUID N)", with its disposition, state or
  status in a chip on the right.
- Internal references and notes for software are left out: campaign, source
  and response references, the row type, the source generation, row
  versions, the email revision, the digest-resolution note and the display
  and date-filter time zones.
- Blank fields are left out. A packet is completed by hand, so its blank
  fields print as write-in rules instead.
- A workflow revision nests under its item, headed by when and by whom it
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
with it: the Family-code directory, financial detail, census changes and
talents, and the response list of ParishSoft data to check; the postal mail
merge starts with the DUID and freezes only its
heading row), filters, light zebra banding as a
display rule, widths fitted to the content within limits with long text
wrapped, and a landscape print setup that fits one page wide, repeats the
heading row and numbers the pages. Label/value blocks ("Report information"
sheets and a packet's Ministry details) use one tinted label style; a
packet's Ministry detail values span the width of the member table below
them. Styling never changes a cell's value or type.

All charts have title, legend, labeled axes with units, accessible color/line
patterns, hover/focus values, and equivalent data tables. They download as PNG
or PDF. Dollar/count series on one chart use separate labeled axes rather than
comparing unlike units on one scale.

### Chart engine

Every Admin chart from the [response funnel](#response-funnel) on is one
Vega-Lite specification, built by a pure function of the report's query
result (`reports/chart_specs.py`) together with a plain-language summary and
a table of its exact values. The same JSON is drawn two ways, so the portal
and the emails cannot disagree (the
[participation graph](#participation-graph) predates the engine and keeps its
own renderer):

- **In the browser**, `chart-v1.js` reads the specification embedded in the
  page and draws it as SVG with the vendored Vega runtime, within the
  [Content Security Policy](../architecture/spec.md#web-security-and-privacy):
  Vega runs in its interpreter mode (no `eval`), nothing injects a stylesheet
  (the tooltip's is served as a static file), there is no actions menu, and
  no asset comes from a third party; the runtime's data loader refuses every
  load, since a specification carries its data inline. A chart follows its
  panel's width and is measured again whenever the panel changes width after
  it was drawn; charts in a region refreshed in place are drawn again, keeping
  their space until they are. The Vega, Vega-Lite
  and vega-embed bundles are vendored under the static files, each pinned by
  SHA-256 with its version and source URL, with the license notices of
  everything they bundle (`reports/chart_assets.py`). vl-convert reports the
  same Vega and vega-embed releases and the same Vega-Lite minor version (no
  interface reports its patch release; the locked build embeds the vendored
  6.4.1), so the two renderers run the same code. A browser test on the component
  asserts that both charts render, take a pointer, open their table and
  follow a resize with zero policy violations or script errors.
- **On the server**, vl-convert-python renders the same specification to PNG
  (at twice its CSS size, for the digests' `cid:` images) and SVG, without
  Node or a browser, and reports the image's CSS width and height. These are
  the engine's two formats; how a report page offers the downloads above is
  specified in a later increment (the [response dashboard](#response-dashboard)
  does not offer them yet). No task calls this renderer yet: the daily
  digest's chart is the participation graph's own renderer, and its funnel
  is drawn with table cells. Rendering happens only in workers and
  [export tasks](../background-processing/spec.md#exports-and-graph-rendering),
  never on a page view, and always in a short-lived helper process isolated
  like the mail helpers (no environment, no error text back): the worker
  kills it at its time limit and refuses an oversized specification or
  image. A kill leaves a `helper_timed_out`
  [durable timeout entry](../operations/spec.md#observability-and-health)
  under a timeout name the first task that renders a chart adds.

Each chart is accessible in every rendering. On a page the chart is a figure
whose view has the chart's name and is described by its summary, and is
followed by the collapsed exact-values table, a region named by the table's
caption. A pointer shows exact values in tooltips; the view takes keyboard
focus so a keyboard or screen-reader user hears its name and summary, and gets
the exact values from the table, not the tooltips. Series are told apart by
dash pattern as well as color. A layer with nothing to draw (no activity yet,
or no send to mark) is hidden from assistive technology rather than announced
as an unnamed graphic. In an email the PNG carries the summary as its alt text
and the table follows it. Times on an axis are the campaign's wall clock
whatever time zone draws the chart, and the axis names the campaign's zone.

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
The statistics cards have no inactive subtotal; the Participation page ignores
the retired `inactive` query parameter so old bookmarks still load. Digest
parameters record whichever single population scope applies, so digest parity
never combines the controls.

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

Both axes are linear. The Families axis is scaled to the plotted counts: its
top is the largest daily or cumulative count with about 10% headroom, and its
ticks step by round 1, 2 or 5 multiples. It does not stretch to the eligible
Family total, which would flatten the bars and line; the eligible total is not
drawn (the table's participation column still states it per day).

The chart image draws no source snapshot, request time or other provenance
text. By Administrator decision
([#575](https://github.com/epiphany40223/parishkit/issues/575)) the file
carries that provenance as metadata instead: the PNG's `Description` text
chunk and the PDF's Info `Subject`, both holding the same "Source … as of …;
submission cutoff …; Requested …" text. CSV and XLSX exports keep it as rows.
The daily digest email states a plain as-of line instead
([daily campaign digest](../background-processing/spec.md#daily-campaign-digest)),
and its chart PNG keeps the metadata. The same drawing is used on
the Participation page, in PNG and PDF exports, and in the daily digest email.

Each process draws one matplotlib file at a time, because matplotlib's style
settings are process-global, so a web process's chart image can wait behind a
PDF that the same process is rendering. The Participation page's chart is drawn
from an immutable fact set, so each web process keeps its most recent page
chart PNGs in memory, keyed by the exact drawing inputs (the document and the
date format). A repeat view returns the stored image without waiting. Only the
first view of a newly calculated chart can wait, behind the other renderings
in that process, within the read guard's deadline
([#905](https://github.com/epiphany40223/parishkit/issues/905)).

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

If the exact current input generation is not ready, the interactive report shows
the last complete generation only together with a conspicuous, non-blocking
**Updating** notice saying that the chart may not yet include the newest data;
it never labels stale facts current. By Administrator decision
([#575](https://github.com/epiphany40223/parishkit/issues/575)), the
Participation page's chart block shows no "data as of", campaign time zone or
technical-details header, and no instruction line above the date slider; the
chart's as-of values are in the chart file's metadata and in its exports, and
the x-axis label names the campaign time zone. The user can refresh after the
queued generation publishes. A pinned export or digest waits/retries for its
exact generation and fails visibly rather than substituting a different cutoff.
Recalculation from the pinned source/submission inputs must reproduce every
stored fact, and a verification job detects drift.

Superseded, unpinned calculated generations are automatically compacted under
[derived fact retention](../data/spec.md#derived-fact-retention). Current,
pinned, building/recoverable, and actively consumed generations stay protected.
Report selection, pinning, rendering, and drift verification use that policy's
atomic reference/read guards; no consumer assumes an unpinned old generation
will remain available indefinitely.

The UI defaults to Historical as of day and offers a clearly labeled scope
toggle. Changing scope updates every series together. Pointing at or tapping a
date, or choosing it with the date slider, shows a short tooltip: the local date
as a small heading, then "Families" (cumulative count), "New today" (daily
count) and, when financial is enabled, "Pledges", aligned on the colon and with
no explanatory text. The tooltip's pledge is whole dollars (halves round up,
such as $760,410); everything else keeps exact cents. On a narrow screen the
tooltip sits under the chart in reserved space, so the slider does not move. The
screen-reader live text and the slider's value text keep the fuller form: scope,
local date, daily count, cumulative count out of the scoped population with
percentage, and exact pledge. Report URLs, pinned digest inputs, equivalent data
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
- "Last year's pledges (all Families)": every Family-linked ParishSoft pledge
  record in the mapped comparison funds and period (pledges with no Family are
  not stored), across all Families in the source
  snapshot whatever their status, when available. Every record counts; there
  is no de-duplication. It is the only comparison figure: there is no
  per-population comparison subtotal, and only this one aggregate is captured,
  never any Family's own pledges.

Giving cards include mapped funds/period and source as-of. Missing/incomplete
source displays Unavailable, not zero.

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
reproduced uses a cutoff in the past, as the digests do. Mail
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
lists of Families behind any count all come from the same rows. The
[chart engine](#chart-engine) draws the funnel and the activity series on the
[response dashboard](#response-dashboard), and the
[response lists](#response-lists) show the Families behind the counts; the
[Family timeline](#family-timeline) shows what happened with one Family; and
a Production [daily digest](../background-processing/spec.md#daily-campaign-digest)
states the funnel at the end of its report day.

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
one result.

### Response dashboard

**Access:** Admin and Staff (`CAMPAIGN_REPORT`); the Testing view is Admin
only.

`/admin/reports/responses/` shows the funnel of the current campaign at the
database's current instant, labelled **Counted at**. Data comes first: a
tile per [funnel stage](#funnel-stages) with its count and its share compared
with Invited, the three figures reported beside the funnel, then the funnel
chart and the [activity chart](#response-activity-over-time) with its send
markers, each with its summary and exact-values table. The explanation sits
in the [About this page](../admin-portal/spec.md#page-help) panel after the
data. Each chart is named by its panel's heading rather than a title drawn
inside it. Until an invitation has been delivered the tiles leave out their
shares and the page says so in one sentence. A **Lists of Families** panel
links to each of the [response lists](#response-lists) in the mode shown,
with the number of Families on it (counted from the same read; the list of
ParishSoft data to check has no number here, since counting it needs the
ParishSoft read). The page shows counts only, never a Family's name or
identifier, and sends `Cache-Control: no-store`.

The URL carries only two closed choices. `mode` is `production` (the default)
or `testing`: Production reads live responses and Production mail; Testing
reads the campaign's active rehearsal epoch, is labelled as Testing, and is
offered and admitted for Administrators only, since Testing responses appear
only in explicit Admin testing views (see
[population and calculation rules](#population-and-calculation-rules)).
With no active rehearsal, the Testing view says there is nothing to show.
`grain` is `hour` or `day`; without it the activity chart is hourly while
everything it shows (the first link follow, form open or submission, or the
first marked send) lies within three days of the cutoff, and daily after
that. Both grains come from the one read, so switching never changes a
total. Both choices are [in-place controls](../admin-portal/spec.md#in-place-controls):
no reload, the reader's scroll position and focus on the chosen link kept,
the charts drawn again, and the address replaced (so Back does not step
through the choices). The Admin portal
[requires JavaScript](../admin-portal/spec.md#javascript-requirement), so
there is no no-script form.

Each view reads the funnel once, with the report's
[campaign read guard](../data/spec.md#campaign-read-guards) and role recheck,
and records one audit event with its outcome and no reported value. The
browser draws the charts; a page view never renders an image on the server.
The charts' PNG and PDF downloads follow in a later increment.

### Response lists

**Access:** Admin and Staff (`CAMPAIGN_REPORT`; the downloads also
`REPORT_EXPORT`); the Testing view is Admin only.

`/admin/reports/responses/<list>/` lists the Families behind one count,
at the database's current instant (labelled **Counted at**), from the same
per-Family rows as the [funnel](#response-funnel):

| List | Families listed | Columns | Filter |
| --- | --- | --- | --- |
| `submitted` | with a submission (Submitted) | Family, Family DUID, Envelope number, Submissions, First submitted | none |
| `started` | form opened, nothing submitted (Form opened minus Submitted) | Family, Family DUID, Envelope number, Form opened, Got past the first step | got past the first step, or opened the form only |
| `not-opened` | a delivered invitation, form never opened | Family, Family DUID, Envelope number, Invitation delivered, Link followed | link followed or not |
| `more-than-once` | more than one submission (Submitted more than once) | Family, Family DUID, Envelope number, Submissions, First submitted, Last submitted | none |
| `data-quality` | active Families of the campaign whose current ParishSoft record has a blank mailing name or envelope number 0 | Family, Family DUID, Envelope number, Mailing name, What to check, First submitted | blank mailing name, or envelope number 0 |

Each of the first four lists has exactly the Families its count on the
[response dashboard](#response-dashboard) counts, with the instants the
[funnel stages](#funnel-stages) define (Link followed includes mail-scanner
prefetches). A value not reached yet reads in words on the page (Got past the
first step "Not yet", Link followed "No") and is blank in the CSV and XLSX.
**ParishSoft data to check** is a live view of the latest ParishSoft data
rather than a reproducible count, for the launch-day data problems that made
Family names misleading; the record itself is fixed in ParishSoft, and a
Family the latest ParishSoft data no longer has is left out (the About panel
says so). The Family column is the
[active parishioner family directory](#active-parishioner-family-directory)'s name (surname, then active heads),
and the envelope number and mailing name come from the same latest
ParishSoft data, read in two queries for the listed Families; a Family no
longer in that data says so instead of a name.

The Administrator removed the `submitted` list's filter (it offered Families
with or without a delivered invitation), so that list simply shows every
Family that submitted
([#860](https://github.com/epiphany40223/parishkit/issues/860)). Its old
`show` values (`invited`, `uninvited`) are unknown values now and are refused
like any other.

Each list is a [shared Admin table](../admin-portal/spec.md#admin-tables):
every column sorts (times and counts newest or largest first on the first
click, missing values last), the default is chronological for `submitted`,
most submissions first for `more-than-once` and Family name otherwise, and the
filter, search, sort headings, rows per page and paging refresh the table in
place. The Production and Testing rehearsal links are
[in-place controls](../admin-portal/spec.md#in-place-controls) of the table's
region: they keep the filter and order and refresh the list without a reload.
The URL carries only closed choices: `mode` (as on the dashboard: Production
by default, the active Testing rehearsal for Administrators only), `show` (the
filter), `sort`, `size` and `page`; nothing identifying.

Every list has a **Search by Family name, DUID or envelope number** box
([#849](https://github.com/epiphany40223/parishkit/issues/849)). It keeps a
Family whose Family column (the directory's name: surname, then active heads)
contains the text, ignoring case, or, for a search of digits only, whose
Family DUID or envelope number is exactly that number (leading zeros are
ignored, so `0012` finds 12); a part of a DUID or envelope number matches
nothing by number. The search runs on the list's rows after its filter, before
sorting and paging, so the row count, the pages and the download all follow
it. A search can name a Family, so it is sent only in the filter form's
CSRF-protected POST body, never in a URL, as the
[active parishioner family directory](#active-parishioner-family-directory)'s
search is: the list page accepts that POST as a read, and refuses a POST with
a query string and a `search` in a URL (400). While a search is applied the
table is a POST table, whose sort headings and navigators carry the search as
hidden fields. After each in-place answer the address bar shows the view's
closed choices only (the page's `data-page-address`, as
[System logs](#system-logs) does), so Back and Reload keep the filter, order,
page size and page, and a reload clears the search. The mode links are plain
links, so switching mode clears the search too, and the search box then shows
it cleared.

**Download** posts the list's filter, search and order (CSRF-protected) with
a **Format** (CSV, the default, XLSX or PDF) and a time zone chosen beside the
button (the browser's by default), and downloads the complete filtered list,
not just the page, rendered on request in that order with the table's columns
([#850](https://github.com/epiphany40223/parishkit/issues/850)). The button's
label names no format, so choosing one changes nothing else on the panel, and
an in-place refresh keeps the format and time zone chosen. The formats hold
the same rows and columns:

- **CSV** is exactly the table in the
  [shared CSV format](#shared-report-behavior): times as ISO text in the
  chosen time zone, a missing value blank, every cell neutralized.
- **XLSX** is the shared, [styled](#file-design) report workbook: the list's
  sheet, with native date and time cells in the chosen time zone, counts as
  numbers, identifiers and names as literal text (never a formula, characters XLSX cannot hold
  escaped) and a missing value blank, plus the shared "Report information"
  sheet.
- **PDF** is the [shared design](#file-design)'s zebra table, as for the
  [Family-code directory](#active-parishioner-family-directory): each cell as
  the page shows it (a missing value in the page's words, counts grouped,
  times in the parish's compact date format in the chosen time zone), with
  text the font cannot draw escaped.

The XLSX information sheet and every PDF page carry the list's title, parish,
campaign, Production or Testing, the filter choice, whether a search was
applied (never its text), **Counted at** and the time zone, the number of
Families, and the sensitive-data line. The filename is
`stewardship-responses-<list>-<UTC time>.<format>`, with no search, name or
choice in it. Before the download the panel states how many Families the file
holds and the sensitive-data warning; with none, the button is disabled. The
file is built in memory on the web connection, as the
[System logs](#system-logs) download is, and read under the interactive
campaign read guard (see
[campaign read guards](../data/spec.md#campaign-read-guards) for small
downloads rendered on request). XLSX and PDF are rendered on request too,
rather than through the shared export lifecycle (queued, retained,
regenerable) the issue first proposed: that would need a new export kind and
snapshot, so a migration, for a list that is a live view anyway, and the
largest list (about 1,100 Families at launch scale, some 34 pages) renders
as PDF in about 8 seconds on a development machine, within the read guard's
60-second deadline. Downloading again regenerates the file. While the campaign's purge gate is closed
(any purge work gate not released) the download is refused with an
explanation (409), including when the gate closes as the download starts,
and its button is disabled with a notice; the list itself stays readable
under the read guard, as [shared report behavior](#shared-report-behavior)
allows.

Each view and each download reads the list once under the report's
[campaign read guard](../data/spec.md#campaign-read-guards) with the role
recheck (a download also rechecks the purge gate), sends
`Cache-Control: no-store`, and records one audit event whose type names the
list and the action (such as `response_submitted_list_exported`), with its
outcome and row count and no Family name or DUID; a download the purge gate
refuses is recorded as failed. As the export audit rule in
[shared report behavior](#shared-report-behavior) asks, the event also records
the mode (`report_mode`), the Show choice's closed key (`report_filter`;
`all` for `submitted`, whose old values are ignored), whether a search was
applied (`search_used`, never its text, #849) and the ParishSoft snapshot the
names were read from (`snapshot_id`; left out when nothing was read, as for
Testing with no rehearsal)
([#556](https://github.com/epiphany40223/parishkit/issues/556)), and the sort
order as its closed token (`report_sort`: a column key such as `family`, or
`-` and one for the other direction, such as `-submitted`; the list's default
when none was chosen)
([#851](https://github.com/epiphany40223/parishkit/issues/851)). The page size
and page number are not recorded: they pick which part of the same list is on
screen, and the row count with these choices already says which Families the
list held. System logs shows the mode, choice and order in the page's own
words, the order as the column heading followed by "(ascending)" or
"(descending)". A download's format is not recorded either: the audit context
allowlist has no key for it, so recording it would need a migration.

### Family timeline

**Access:** Administrators see the full timeline; Staff see the summary only
(`CAMPAIGN_REPORT`; the Administrator's decision of 2026-10-04 on
[#523](https://github.com/epiphany40223/parishkit/issues/523)). The
Testing view is Administrator only.

`/admin/reports/families/<family>/` (**Family timeline**) answers "what
happened with this Family?" and "did you get my response?" for one Family of
the current campaign. `<family>` is the Family's campaign record id, an
opaque random identifier like a Mail message's; the Family's name, DUID,
envelope number and code never enter the URL, and the browser title leaves the
name out so browser history does not keep it. The Family's name opens the page from each
[response list](#response-lists) row (in the list's mode) and each
[active parishioner family directory](#active-parishioner-family-directory) row, and from each match in the header's
Find a Family box (see
[Admin navigation](../admin-portal/spec.md#admin-navigation)). Its breadcrumb
parent is the active parishioner family directory. The page reads the Family's records at the
database's current instant (**Counted at**).

Data comes first: the heading, then the Family's name (the directory's
surname-and-heads name from the latest ParishSoft data, or a note that the
data no longer has the Family), DUID and envelope number, then a summary that
both roles see:

- **Submitted:** Yes, with the first submission's time and, after more than
  one, how many and the latest; or "Not yet".
- **Last email:** the last email sent to the Family (Invitation, Reminder N,
  Submission receipt or a chosen-Family test email), planned last among those
  not cancelled (a cancelled email was never sent), with its time and outcome
  in plain words: Delivered (the mail service accepted it, which does not
  prove it reached the inbox), Failed, Not sure it arrived, or Still sending.
  No email content is shown. Outgoing mail, its state filter and the Mail
  message page name each email's state in the same words, from one shared
  table, adding where a Still sending email is (queued, waiting to retry, or
  being handed to the mail service) and Not sent (cancelled). The Admin
  status bar, the pause and resume pages, Family email progress and sends,
  and the test-email results use the same words.
- **Family code**, for roles that may see Family codes (`FAMILY_CODES`; the
  code is neither decrypted nor shown otherwise), as the directory shows it,
  with **Open form**, which opens the Family form in a new tab with the code
  in the URL fragment only, as the directory's link does. Unlike the
  directory, the page shows no notice beside it (the Administrator's choice
  on #590). Open form is a
  disabled button with its reason beside it while the system is in Testing
  mode (the Family sign-in then accepts only rehearsal codes) or when the
  Family has no code for the campaign. In Testing mode the reason links "Try
  the Family form as a chosen Family" for roles that may open that page
  (Administrators).

While the Family is in the campaign's Reminder WorkGroup, both roles see
"Reminders skipped: in ParishSoft WorkGroup" with its name.
Administrators also see whether campaign email can reach the Family (or why
not), the furthest form step reached and when the Family was last seen on the
form (live values of the
[Family engagement record](../data/spec.md#family-engagement)), and the
**Timeline**: a [shared Admin table](../admin-portal/spec.md#admin-tables)
with one row per event and the columns When, What happened and Details. It
is newest first (the Administrator's decision on #590); When is its one sort
heading, which reverses the order in place, and rows at the same instant keep
the order the Family took them in. A Family has few events, so the table
shows them all, with no paging or row navigator. Rows are each Family email
with its outcome (including Not sent (cancelled)), linked to its Mail message page; the invitation skipped because
the Family had already responded (the [funnel's](#funnel-stages) skip), which
stands in for that invitation's cancelled email, so the one planned email is
listed once; likewise each reminder not sent because the Family is in the
campaign's [Reminder WorkGroup](../background-processing/spec.md#family-invitations-and-reminders)
(#861); each sign-in, labelled as possibly a mail scanner checking the
link; each form open; getting past the first step and the furthest step
reached; and each submission, noting when no receipt was sent because the
Family had no email address. Staff get none of
these, and the server does not read them for a Staff view.

As on the [response lists](#response-lists), the URL carries only closed
choices: the timeline's `sort` (and the `size=all` its heading carries), and
`mode`, Production by default or the campaign's active Testing rehearsal for
Administrators. Both are
[in-place controls](../admin-portal/spec.md#in-place-controls) of the page's
one table region, and switching mode keeps the order chosen. Emails,
submissions, form opens and the engagement record carry their mode (a Testing
record also its rehearsal epoch; a Testing receipt is found through the
submission it answers). A sign-in is the durable `family_login` audit event,
which carries no mode, so each is counted in the mode the system was in at
that instant (from the runtime mode transitions), and a Testing view keeps
only those within the rehearsal epoch's lifetime. With no active rehearsal
the Testing view says there is nothing to show. Every time is shown in the
browser's time zone.

Each view admits the report read, reads the Family under the campaign read
guard with the role recheck (a Family of another campaign is refused like a
missing one), decrypts the code under the credential key-set lock as the
directory does, sends `Cache-Control: no-store` and records one
`family_timeline_viewed` audit event whose subject is the Family's opaque
campaign record id, with the campaign and the outcome and no name, DUID, code
or shown value, as a Mail message view names its message. A Family that is
not found in the campaign (an unknown id, or another campaign's Family) is
refused without an audit row, as a Mail message that is not found is, so no
row pairs a campaign with a Family that is not in it. Recording which
Family was viewed lets a later review see who looked at whom, and a view left
unrecorded could never be filled in; the Administrator confirmed it on
2026-10-05 ([#477](https://github.com/epiphany40223/parishkit/issues/477)),
with the page's name, its URL and its place under the active parishioner family directory. The
subject
is a soft reference with no foreign key, so, like other report access audit,
the event is outside the purge inventory (see
[audit records](../data/spec.md#job-outbox-audit-and-purge-records)). The
reads are a handful of small statements and none runs per row: the emails,
form opens and submissions through their Family indexes, the skips through
the campaign's invitation schedules, and the sign-ins through the audit's
event-type and time index, which scans the campaign's sign-in events rather
than one Family's.

## Additional information

**Access:** Admin and Staff; both may edit its workflow.

One row per distinct `AdditionalInformationItem` shows submission time, Family
name, Family DUID (its own column), text excerpt/full detail,
follow-up-needed, followed-up time/actor, disposition, replacement/withdrawal
link, and Staff notes, in the Admin table
[column order](../admin-portal/spec.md#table-column-order): Family (surname,
then heads, as on the
[active parishioner family directory](#active-parishioner-family-directory)),
Family DUID, Submitted, then the rest. The default queue shows only
`current_actionable`; history filters expose superseded/withdrawn items.
Search covers authorized text, Family name/DUID, notes, date, disposition,
and workflow state. The installed SQL selection searches and sorts by the
Family's surname, which leads the shown name, so a head's first name does
not match the Family name and Families that share a surname keep the
selection's order rather than sorting by their heads; the Family DUID
column does not sort yet
([#960](https://github.com/epiphany40223/parishkit/issues/960)). Exports
include complete text and workflow history option, and name each Family as
the page does, from the snapshot their capture was read from; a Family
without a surname is "Family", as the selection names it. A file rendered
after that snapshot is compacted keeps the captured surname.
The CSV and XLSX share one set of columns and carry no internal item
references (the Administrator, 2026-10-09). A "Row type" column says whether a
row is the "Item" or one of its "Earlier workflow" revisions. Earlier workflow
rows are listed directly under their item and repeat its Family name, Family
DUID, Submitted time, disposition and submitted text, which identify the item
to a reader even after the sheet is sorted or filtered. A superseded item's
"Replaced by request submitted" value is the Submitted time of the later
request that replaced it, which finds that request's row, or "Not in this
export" when the export's filters left that request out.

Editing is audited and uses optimistic concurrency. This report is also the
source for the weekly Admin digest.

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
Family DUID (in its own column, headed "Family DUID"), manual code, current
email eligibility/deliverability, and response status; the name is the row
header. The name is the Family's
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
query string. The code
is directly visible to Admin and Staff; there is no per-row reveal action,
reauthentication ceremony, distinct-Family reveal budget, or Valkey dependency.
The server rechecks the report role and campaign scope on each request and uses
`Cache-Control: no-store` for interactive responses.

In Testing mode the Family sign-in accepts only rehearsal codes, so the page,
and a directory export's page, says the listed codes work only after go-live.
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
neither. It and the mailing-columns preset (`?mailing=yes`) are the only
values accepted in a link, since neither carries a private value. The page
with mailing columns (unless it already lists them) and the Admin home page
show how many active Families across the campaign no campaign mail can reach,
linking to that list.

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
one header row plus one row per Family, with exactly the columns Family (the
same surname-and-heads name as the page), ParishSoft DUID, Family code and
Family head emails. The list filtered to Families no campaign mail can reach
adds Phone numbers for follow-up calls, before Family head emails. Family
head emails reads like the Contact details: "Anna Example and Ben Example:
family@example.org; Cara Example: (no email)", with invalid source text
followed by "(not a valid address; fix in ParishSoft)". The emails are not
part of the export's captured selection; they are read when the file is
rendered, from the captured ParishSoft data while it is still kept.
ParishSoft data is refreshed often (every 15 minutes under the default
[refresh schedule](../background-processing/spec.md#refresh-schedule)) and
superseded data is soon compacted, so a render that comes later (a retry,
or regenerating an expired
file) reads the same heads' emails from the current ParishSoft data instead,
and the file's report details add "Head emails as of" that data's refresh
time. CSV files carry no report details, so that note appears only in XLSX
and PDF files. A head the current data no longer has as a Member at all
reads "(not in current ParishSoft data)" rather than "(no email)". A regenerated or retried file therefore has the same Families, names,
codes and other columns as the capture, but its head emails may be newer. Only
when no ParishSoft data is available at all does the render fail, with its
own failure kind (`directory_head_emails_unavailable`) in the logs; the
export is retried like any failed render. Report details (parish, campaign, capture time, "Head emails as of"
when it applies, Families in the file, filters applied and the privacy line "Sensitive: Family codes.
Authorized recipients only.") are in the PDF header and footer and the XLSX
"Report information" sheet, never in columns. The page's export panel names
the file's columns for the current filters and mailing-columns choice. The
export's retained capture keeps only the private contact columns its file
renders (#388 L6): the address for the mailing-columns file, phones for the
list filtered to Families no campaign mail can reach, and never the envelope
number. The rest are stored empty, and captures made before this rule are
kept as they were until their exports expire. Exports use the standard asynchronous, short-lived, requester-authorized export
pipeline, including its explicitly accepted plaintext storage and owner-only
permissions under the
[export retention policy](../operations/spec.md#temporary-retention-and-housekeeping).
Opening the page needs no fresh sign-in, but queuing an export, or
regenerating an expired one, needs a Google sign-in within the last five
minutes, because the file holds every listed Family's code
([#547](https://github.com/epiphany40223/parishkit/issues/547)). A stale
sign-in gets the
[step-up](../architecture/spec.md#identity-and-session-security), which
queues nothing and returns to the directory (or the export's status page);
the filters, being private form state, are applied again. The export panel
says so before the Administrator or Staff member queues one. Interactive report execution and exports are audited at
report, campaign, actor, filter, and row-count granularity without copying
codes into the audit payload. Exact-code-search audit records omit the raw filter and store
only a keyed fingerprint when correlation is operationally necessary.

### Mailing columns

An **Include mailing columns** checkbox, off by default, adds Addressee and
Mailing address columns to the table and makes the export a postal mail
merge. It is independent of every filter: it never changes which Families
are listed, so any listed Family, including one that email reaches, shows its
addressee and mailing address, and the mail merge covers exactly the listed
Families, including those without a usable mailing address (as below). To
list the Families postal mail
is for, filter reach to "By postal mail only" (or email availability to a
reason). Families without deliverable email are the
complement of the deliverable-email
statistics card, not of the syntactic eligible-email card. The Addressee
column names the Family as the mail-merge file does; a Family without a
usable mailing address has no addressee, and its Mailing address column says
its address columns are blank in the file. Mailing columns show
nothing the active parishioner family directory's contact details do not already show Admin and
Staff, and Ministry leaders are denied either way. Viewing with mailing
columns is audited as postal outreach, and its export is a `postal_outreach`
export request.

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
rows the filters list on the page. A Family without a usable mailing address
cannot be mailed, but it keeps its row: its Addressee, Address line 1–3,
City, State and ZIP are blank (never a partial address), and ParishSoft DUID,
Family, Family heads and Family code stay for follow-up. The page's export
panel says so, and the file's report details count the rows with no usable
mailing address. Existing columns keep their names and order (Family head
emails was added at the end), so existing mail-merge templates keep working.
The PDF lays the same content out as address blocks (a block without an
address says "No usable mailing address"), leaving out the head emails,
with the report details in its header and footer. It
never includes the opaque email-link token.

## Ministry change summary

**Access:** Admin/Staff for all selected campaign Ministries; leaders for
assigned campaign Ministries only.

The database enforces this scope, not only the application. The summary, its
lists and the
[Ministry follow-up](../admin-portal/spec.md#follow-up-workflows) queue read
through `stewardship_ministry_report_v2` and
`stewardship_ministry_followup_v2` (migration 0040). These take the signed-in
user and derive the scope from that user's current roles and assignments with
`stewardship_ministry_scope_v1`, as export captures do. A user with no current
scope reads nothing. The application's own view of the user's roles and
Ministries is a pre-check and a cross-check. If the database's scope is wider,
the page is refused. If it is narrower, the narrower scope applies
([#389](https://github.com/epiphany40223/parishkit/issues/389) L3). A user the
application treats as Admin or Staff but the database gives no scope is refused
rather than shown an empty report. The Admin Home's My Ministries panel and the
follow-up menu count read through the same function and cross-check, so neither
shows more than the queue would.

The sorted summary has Ministry name/DUID, join-request count, leave-request
count, unresolved count, and follow-up progress. Counts use latest live request
state while retaining links to superseded/history views.

Each Ministry links to:

- prospective joiners: Member name/DUID, gender, age as of report date,
  publishable email/phones for leaders, operational contact for Admin/Staff,
  Family mailing address, request/submission date, status/outcome; and
- requested leavers: Member name/DUID, current role where known, request date,
  status/outcome.

Each list opens by a POST from its Ministry's form on the summary. A GET of
either list's address (typed, bookmarked, refreshed or reached with Back)
carries no Ministry selection, so it answers with a 303 redirect to the
summary rather than an invalid-request error
([#867](https://github.com/epiphany40223/parishkit/issues/867)).

Follow-up has no assignee, so neither list nor its exports show one; see
[Follow-up workflows](../admin-portal/spec.md#follow-up-workflows).

Leader phone/email columns honor ParishSoft publish flags and show "Not
published" rather than leaking a value. Admin/Staff may see operational source
contact. Mailing address/demographics are visible to assigned leaders as
explicitly authorized for this workflow. Birth date itself is not shown when
age suffices.

Lists are searchable/filterable/sortable and exportable as CSV, XLSX, or PDF.
Every leader view/export is audited with Ministry scope.
An export audit event retains the sorted Ministry DUID set actually included
after filtering, plus whether the captured contact projection was operational
or publish-restricted. This result scope is distinct from the potentially wider
authorization scope used for lifecycle checks. An empty summary has an empty
result scope; a named detail section retains its Ministry even with no matching
Members. One event per action is sufficient; per-Ministry audit fan-out is not
required. These non-sensitive identifiers survive campaign-detail purge with
the audit event and contain no names, contacts, filters or Member values.
The [Admin log owner](../admin-portal/spec.md#logs) includes this set when
filtering audit events by Ministry; it must not depend on retained export rows.
This representation does not change current-policy checks or report columns.

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
completion. Every format carries the report's privacy line. The XLSX is one workbook with a sheet per Ministry. PDF starts each
Ministry on a new page. CSV is one file with a blank row and repeated headings
between Ministries. No ZIP/per-Ministry files are required.

## Pending census changes

**Access:** Admin and Staff; never Ministry leaders. Staff edits only
manual-resolution state; Admin has the full review/publication controls.

The page is **Census changes** under Responses and reports
([#528](https://github.com/epiphany40223/parishkit/issues/528)): the
worklist of every census change Families reported that someone must carry
into ParishSoft, by hand or by
[publication](../data/spec.md#review-and-publication). It reads the existing
[proposed-change rows](../data/spec.md#proposed-changes) (one per atomic
Family request) and the handling registry there that classifies each field;
it adds no state of its own. Testing responses are excluded, as in the other
reports. Pledges, share methods and Ministry rosters are not census
changes; the
[data workflow](../data/spec.md#review-and-publication) says where they go.

### Census change rows

Rows are grouped by Family, one row per change. The columns follow the
Admin table [column order](../admin-portal/spec.md#table-column-order):
Family (surname, then heads, as on the
[active parishioner family directory](#active-parishioner-family-directory);
the search, sort and downloads use the same name), Family DUID (sortable),
then:

- **Who**: the Member, "New Member" with the proposed Member's name, or the
  Family for its household fields (home and mailing address, email opt-out).
- **What changed**: the field's plain label (Mobile phone, Home address,
  Moved to another household, Deceased, New Member, and so on).
- **ParishSoft now**, **Family's answer**, and **Edited value** (only when an
  Administrator edited the proposed value). ParishSoft now is blank where no
  verified ParishSoft read exists for the field (today every Family
  household field), so a Family address never shows a ParishSoft value or a
  conflict. Values appear in full: Admin and Staff need them to type into
  ParishSoft, and every view and download is audited.
- **How it reaches ParishSoft**: *Automatic* for a field the
  [handling registry](../data/spec.md#proposed-changes) marks API-writable,
  *By hand* for every other row. A Family address is *By hand* until
  publication may write it (see the
  [data workflow](../data/spec.md#review-and-publication)).
- **Status**, derived from the row's decision and execution as below.
- **Submitted**, in browser-local time.

A terminal execution decides the status first, since it is the outcome
whatever the decision says: *published* is **Published**,
*resolved_upstream* is **Already in ParishSoft**, *resolved_external* is
**Entered by hand**, and *superseded* or *cancelled* rows appear, as
**Superseded** and **Cancelled**, only with *Include history*. Otherwise an *ignored* decision is **Ignored**. Otherwise
the execution decides: *queued* is **Being published**, *conflict* is
**Conflict** (ParishSoft changed since the Family answered, shown as the
conflict detail below), and *failed* is **To do** (a failed publication).
A *pending* row is **To review** when it is an *unreviewed* automatic
change, waiting for an Administrator's decision, and **To do** otherwise
(manual work not yet entered, or an approved automatic change not yet
published).

Conflict detail shows baseline, current upstream, Family submitted, and
Admin-edited proposed values. It never resolves by silent last-write-wins.

### Census change filters and downloads

The default shows *To do* and *Conflict*, and, for an Administrator, *To
review* as well. Filters are status (with *Include history*), how the change
reaches ParishSoft (with *Hide automatic changes* for staff working by
hand), kind of change (contact details, moved, deceased, new Member), a
Family name or DUID search, and submitted date. Sorting, paging and filters
act [in place](../admin-portal/spec.md#in-place-controls). The filtered list
downloads as CSV or XLSX (PDF later) with the page's values,
audited with a count like the other report downloads. The files keep their
earlier column order (Family first, Submitted last) until the export slice
of [#932](https://github.com/epiphany40223/parishkit/issues/932) reorders
them, so a spreadsheet that reads columns by position keeps working.

The read-only page and its downloads are enough for staff to apply every
change by hand if publication is not ready; that is why it comes first.

### Manual census resolution

Not built yet ([#528](https://github.com/epiphany40223/parishkit/issues/528)):
the Census changes page and its downloads are read-only today.

On *By hand* rows in *To do* or *Conflict*, Staff and Admin may mark
**Entered in ParishSoft** (execution *resolved_external*) or **Ignore**
(decision *ignored*), each with an optional note, acting in place. A
*Conflict* row shows its conflict detail beside the controls, so the person
checks ParishSoft before ticking it. *Entered in ParishSoft* is final, as
every resolved outcome is: a mistaken tick is corrected in ParishSoft, and a
later Family answer creates new rows. An Administrator may reopen an
*Ignored* row (decision back to *unreviewed*) with a note. Who, when and the
note are kept as history. Automatic rows are view-only for Staff. Admin
review/publish actions are defined by the
[data](../data/spec.md#review-and-publication) and
[Admin](../admin-portal/spec.md#follow-up-workflows) specifications.

Today the web role may change a proposal only after a later Family
response, to *superseded*, *cancelled* or *resolved_upstream*, and the
database guard refuses *resolved_external* from it, so the tick, Ignore and
reopen ship with a forward migration that lets the web role make exactly
these changes, following the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy).

Since Family addresses are not yet written by publication (see the
[data workflow](../data/spec.md#review-and-publication)), an address entered
in ParishSoft stays *To do* until it is ticked.

## Financial stewardship detail

**Access:** Admin and Staff only.

This required report closes the operational path for report/export-only
pledges. One row per currently effective live Family response shows, in the
Admin table [column order](../admin-portal/spec.md#table-column-order), the
Family name first (surname, then heads, as on the
[active parishioner family directory](#active-parishioner-family-directory);
search and the Family sort still use the surname), Family DUID in its own
column (not sortable: the installed selection does not order by it), annual
pledge, frequency, approximate installment, selected share-option labels,
Other text, active status, source comparison pledge/contribution aggregates
with as-of time, and last the Latest response (the time the Family last
updated its response). The page's two aggregate headings carry the
comparison period's years, as the Family form words them ("ParishSoft
pledged (2026)", or "(2026–2027)" for a period spanning two years), each with
a toggletip naming the period's dates. The comparison period is the
campaign's configured comparison financial period, usually the giving year
before the upcoming stewardship year that Families pledge for; the pledge
total counts ParishSoft pledges dated in it, and the contribution total gifts
dated in it up to the latest giving data read. The page and
every export format (CSV, XLSX, PDF) head those two aggregates "ParishSoft
pledged" and "ParishSoft contributed" with the same years ("ParishSoft
pledged (2026)"), and say "ParishSoft" rather than "Source" in the export
metadata that describes them, so a downloaded file names its figures as the
page does. The exports also name each Family as the page does, from the
snapshot their capture was read from; a file rendered after that snapshot
is compacted keeps the captured surname.

Filters include active/inactive, first/latest submission dates, pledge range,
zero/nonzero/cannot contribute, frequency, and share method. Summary shows
Family count, annual total, frequency distribution, share-method counts and the
number of Families that cannot contribute financially; such a Family's
frequency reads "Cannot contribute". Exports are CSV, XLSX, and PDF; queuing
or regenerating one needs the same fresh sign-in as a
[active parishioner family directory](#active-parishioner-family-directory) export, returning to this report. No
Ministry leader receives aggregate or Family financial detail.

## Talents and limitations

**Access:** Admin and Staff only.

From each Family's currently effective live response, one table lists Members
who shared a talent (with any Other text, worded from the campaign's current
talent list) or who cannot participate in any ministries, and a second lists
Families who cannot attend Mass or prayer services (see
[Family portal](../parishioner-portal/spec.md#talents-and-cannot-participate)),
with the columns Family, Family DUID and Latest response, each sortable.
Both tables name each Family as the
[active parishioner family directory](#active-parishioner-family-directory)
does (surname, then heads), on the page and in the downloads.
Testing responses are excluded. Filters are a name or Family DUID search and
one choice of everything, cannot participate, cannot attend, or a single
talent; a summary counts each. The filtered result downloads immediately as CSV
(Members, then Families) or XLSX (one sheet each), in a chosen display
timezone. A download is rendered in memory on the web connection under the
interactive campaign read guard, not through the dedicated download pool, whose
login cannot read the response and source data the report needs. Viewing and
downloading are audited with the row count, the Show choice (`report_filter`:
a closed word, or `option` with the chosen talent's settings id in
`talent_option_id`), whether the search box was used (`search_used`) and the
ParishSoft snapshot read (`snapshot_id`, recorded only when a refresh did not
promote a new one while the report ran)
([#556](https://github.com/epiphany40223/parishkit/issues/556)). The search
text itself is never recorded, because it can name a Family. These answers are
never written to
ParishSoft. When the campaign offers no talents, the
page says so plainly and lists only the limitations: no Talents column, talent
counts or talent filters appear on the page or in the downloads, and Members
listed only for talents an earlier response chose are left out. The Ministry
follow-up queue also notes when a
leave comes from a Member who cannot participate in any ministries.

## System logs

**Access:** Admin only.

The report behavior, levels, source filters, timezone export, and CSV and
JSON Lines formats are defined by the
[Admin log specification](../admin-portal/spec.md#logs).
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
those recorded inputs. Its [response funnel](#response-funnel) is reproduced
from durable timestamps instead, at the end of the report day.
