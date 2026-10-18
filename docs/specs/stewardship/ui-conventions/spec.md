# Stewardship UX conventions

This specification is the home and the index of the general, portal-wide user
experience conventions of the Admin portal and the Family portal: the rules
that apply to every page, table, form and download rather than to one feature.
Every new page, every UX design decision and every piece of shared UI code
starts here.

## Precedence and scope

This specification has the highest precedence for general, portal-wide UX
rules (Administrator decision, 2026-10-10,
[#956](https://github.com/epiphany40223/parishkit/issues/956)). When another
specification disagrees with it on a general UX rule, this one wins, and the
other is corrected to match.

- **General rules live here only.** A change to a general or portal-wide UX
  principle is made in this specification, never in another one. A feature
  specification links the convention it follows instead of restating it, as
  the repository's specification rules require.
- **Feature rules stay with their feature.** What one page shows, which
  fields one form has, or what one page's Review lists ("this page's Review
  shows X") belongs to that feature's specification, such as the
  [Admin portal](../admin-portal/spec.md), the
  [reports](../reports/spec.md) or the
  [Family portal](../parishioner-portal/spec.md).
- **Exceptions are recorded.** A page that departs from a convention says so
  in its own specification, with the decision that allows it, and the
  convention below names the exception.

Each convention gives the **rule** (a short bold summary, or the section's
text when that states it directly), **why** it exists, the **shared code**
that implements it (a file and function, template tag or CSS class; new pages
use that code rather than building their own), and its **precedent** (the
issue or pull request that decided it). Where a convention is still being
built by an open pull request, the precedent names it; its detail moves here
when it lands.

## Convention index

- Portal-wide presentation: [devices and browsers](#devices-and-browsers),
  [language](#language-and-localization),
  [numbers and money](#numbers-and-money),
  [dates and times](#dates-and-times), [date format](#date-format),
  [accessibility and client behavior](#accessibility-and-client-behavior),
  [step bars](#step-bars), [charts](#charts).
- Admin interaction: [JavaScript requirement](#javascript-requirement),
  [in-place controls](#in-place-controls),
  [no layout shift](#no-layout-shift),
  [disclosures](#disclosures),
  [prerequisite gates](#prerequisite-gates),
  [field errors and live checks](#field-errors-and-live-checks),
  [secret fields](#secret-fields),
  [Review, Apply and Change status](#review-apply-and-change-status),
  [review dialog](#review-dialog),
  [conditional fields](#conditional-fields),
  [binary choices](#binary-choices),
  [Basic and Advanced modes](#basic-and-advanced-modes),
  [single-campaign interim](#single-campaign-interim),
  [addresses and legacy URLs](#addresses-and-legacy-urls),
  [not-found pages](#not-found-pages),
  [unavailable menu pages](#unavailable-menu-pages),
  [navigation rules](#navigation-rules), [wizards](#wizards).
- Admin page layout: [page help](#page-help),
  [button labels](#button-labels), [shared visual style](#shared-visual-style),
  [colour scheme](#colour-scheme),
  [status indicators](#status-indicators), [stat tiles](#stat-tiles),
  [time entry](#time-entry),
  [schedules](#schedules), [schedule previews](#schedule-previews).
- Tables: [Admin tables](#admin-tables),
  [empty tables and lists](#empty-tables-and-lists),
  [row actions](#row-actions-and-confirmation),
  [column order](#table-column-order),
  [Family and Member names](#family-and-member-names),
  [Members and Families no longer in ParishSoft](#members-and-families-no-longer-in-parishsoft),
  [table fit and row height](#table-fit-and-row-height).
- [Downloaded files](#downloaded-files) and the
  [Family portal](#family-portal).

## Portal-wide presentation

### Devices and browsers

**Why.** Staff work at desks; Families mostly answer on phones; neither may be
locked out on the other kind of device. **Shared code.** `ui-v1.css` (one
theme for both portals); the button-label browser test at 320 px and 1280 px
([button labels](#button-labels)). **Precedent.** The original presentation
rules; #227 (laptop-first Admin layout); #614.

The Admin portal is desktop-first, laid out for a laptop screen, but fully
functional on tablets and phones. The Family portal is mobile-first with equivalent desktop fidelity.
Both use one design system and parish branding, meet WCAG 2.2 AA, support the
current and previous major versions of Chrome, Edge, Firefox, and Safari, and
remain keyboard operable.

### Language and localization

Application strings ship in English but use a localization framework from the
start. Admin-authored content may use any language. The application does not
claim a translated Spanish UI in the first release.

### Numbers and money

Displayed ordinary numbers use US grouping separators. Money is USD with two
fractional digits. Counts written as "X out of Y" also show a percentage;
percentages use one fractional digit unless they are exact integers. A zero
denominator displays an em dash rather than a misleading percentage.

### Dates and times

**Rule.** Every Admin page shows and takes dates and times in the browser's
own time zone, stored as UTC; no page labels a time "UTC" or asks the reader
to think in the campaign's or the parish's zone. Exception: the ParishSoft
refresh times stay in the refresh schedule's time zone (permanent, below). The
date-change review's dated exception (Administrator decision, 2026-10-10)
ended when it moved to the browser's zone under
[#558](https://github.com/epiphany40223/parishkit/issues/558). **Why.** Staff read
and type their own wall-clock time; a campaign-zone or UTC time on a page is a
conversion the reader has to do in their head. **Shared code.**
`data-local-instant` elements filled by `date-format-v1.js`;
`web/dates.py` and the `parish_date` and `parish_time` template filters
(`accounts/templatetags/stewardship.py`); the hidden
browser-zone field (`data-browser-zone`) that `ui-v1.js` fills; the
`test_browser_local_times.py` and `test_browser_zone_guard.py` guards.
**Precedent.** #558 (Administrator decision, 2026-10-04).

The rule covers schedule send times, campaign and financial date-times and
report day buckets too (Administrator decision, 2026-10-04,
[#558](https://github.com/epiphany40223/parishkit/issues/558)). A pure date
with no time stays a calendar date and is never shifted. A download asks for a
time zone only when its rows hold exact action times
([downloaded files](#downloaded-files)). The rule governs how
Admin pages show and take times; system definitions such as the campaign-local
day and the active window keep their campaign-zone definitions. Shown
timestamps include a time zone abbreviation in detail views. A form that
accepts a date and time carries the browser's IANA zone in a hidden field that
the page script fills, and the server converts the typed wall-clock time to
UTC; the page names the zone next to the fields. The Admin portal requires
JavaScript ([#565](https://github.com/epiphany40223/parishkit/issues/565)), so
a missing or unknown zone is refused rather than guessed, and when the browser
reports none the page keeps Save unavailable, saying why. A typed time that
occurs twice or is skipped at a daylight-saving change follows the one
[daylight-saving rule](#schedules), so the form is never refused for it. Emails,
which cannot know a reader's browser, use the parish time zone and name it
("9:15 PM Eastern"), never "UTC". Operational and security alert emails
follow the same rule (Administrator decision, 2026-10-10,
[#967](https://github.com/epiphany40223/parishkit/issues/967)): their times
use the parish profile's time zone and date format, with the zone
abbreviation, such as "October 10, 2026 at 9:15 PM EDT"
([alert times](../background-processing/spec.md#critical-errors-and-notification)).
Pages and emails move to these rules one
group at a time in the #558 PRs; the Ministry follow-up contact attempt is the
first ([Admin portal](../admin-portal/spec.md#follow-up-workflows)), the System
logs date filters the second ([Admin portal](../admin-portal/spec.md#logs)),
New and Edit scheduled email the third ([Admin
portal](../admin-portal/spec.md#new-and-edit-scheduled-email)), and the
submitted-date filters of the Additional information and Ministry reports the
fourth ([Reports](../reports/spec.md#additional-information)), then the
date-change review ([Admin
portal](../admin-portal/spec.md#dates-and-mail-schedules)) and the Financial
stewardship response-date filters
([Reports](../reports/spec.md#financial-stewardship-detail)). A schedule review
shows each send only as a moment in the browser's zone, never as the saved
campaign-local date and time with the campaign's zone.
Until a page's PR lands, it keeps its current zone, including the campaign
zone for the retired Copy campaign page, campaign date-times and report day buckets (the
participation chart still buckets by campaign time). One field is a recorded
exception that stays in the refresh schedule's own time zone (the current
campaign's, else the parish's, as the scheduler reads it) after every #558 PR:
the ParishSoft full-refresh times (Administrator decision). A refresh schedule is
a recurring wall-clock schedule: the parish's own daylight-saving changes
decide when each refresh runs and when the reminders it avoids are due, and a
time entered in another zone and converted with today's offset would move
against the parish's clock twice a year when the two zones change on
different dates. The settings page names that zone, and the
[time entry](#time-entry) reads them as it does any
other. This is decision 18 of the
[refresh schedule plan](../../../plans/stewardship/refresh-schedule.md#open-decisions),
which designs the whole refresh schedule. Local-day conversion must
handle daylight-saving gaps and folds without running an occurrence twice
([schedules](#schedules)).

### Date format

Dates and times follow one parish date format that an Admin chooses in Parish
settings (issue #221): US long ("January 1, 2027", the default), US medium,
US numeric (month first), European long, European medium, European numeric
(day first, with slashes or dots) or ISO 8601. US styles pair with a 12-hour
clock and the others with a 24-hour clock. Admin pages, Family pages, email
and page placeholders, and PDF exports all use it. Every date or time inside
an Admin table cell (row headers included) uses its compact variant (short
month names or two-digit years, no time-zone abbreviation), still in the
browser's time zone, so rows stay short and columns narrow (Administrator,
2026-10-11, #1093). The full form stays in headings, sentences, summaries and
detail panels, and downloads keep their own formats (below). System logs rows
add seconds and the time-zone abbreviation to the compact form, with no UTC
offset, so entries can be compared exactly
([Logs](../admin-portal/spec.md#logs)). In templates a table cell's
`<time data-local-instant>` carries `data-compact` (and an in-place refresh
re-renders it the same way); a calendar date uses the `parish_date_compact`
filter; `tests/stewardship/test_date_format.py` guards both. Background output
uses the format of the configuration it pinned, not whichever is active when
it runs (issue #280): a PDF export uses its requesting configuration, a daily
or weekly digest its snapshot's configuration, and a receipt the configuration
its render records, so a retry after an Admin changes the format keeps the
captured style. The Admin pages for a retained daily or weekly digest are
ordinary Admin pages and use the live format, so after a change they can show
the same retained data in a different style from the emailed copy. One Python
formatter (`web/dates.py`) and one browser script (`date-format-v1.js`, which
reads `<body data-date-format>`) implement the same table. Exports that
programs read are exempt: CSV files always use ISO 8601 (`2027-01-31`, and
timestamps as `2027-01-31 14:05:00-05:00` in the export's stated display time
zone, with the UTC offset so the repeated hour when clocks fall back stays
unambiguous; Excel may treat such offset timestamps as text), and XLSX, the
spreadsheet-native export, stores native dates in Excel's built-in
locale-aware formats (14 for dates, 22 for timestamps). Operational and
security alert emails use the parish date format in the parish's time zone,
with the zone named, like every other email
([dates and times](#dates-and-times); open
[#967](https://github.com/epiphany40223/parishkit/issues/967)).

### Accessibility and client behavior

The UI meets WCAG 2.2 AA: semantic landmarks, labels and instructions, keyboard
operation, visible focus, sufficient contrast, error summaries with field
links, non-color-only change indicators, reduced-motion support, and accessible
table/chart alternatives. Generated PDFs use tagged structure where the chosen
renderer supports it; every chart has an equivalent data table or draws
its values in the chart ([chart engine](../reports/spec.md#chart-engine)).

A person never sees raw JSON. Request errors use closed, server-owned
messages. Scripts that ask for JSON (`Accept: application/json`, or any
non-navigation fetch) receive the machine-readable error codes. A browser
navigation or HTML form submission (`Sec-Fetch-Mode: navigate`, or an
explicit `text/html` Accept) instead receives an ordinary page in the site
layout with the same status code. That page shows the message, the next step
(correct and resubmit, reload, sign in again, or try later), a link back to
the same-origin Admin page the person came from, and the home link. A missing
fresh authentication offers **Confirm with Google** as described under
[identity and session security](../architecture/spec.md#identity-and-session-security). Forms that
can re-render with inline field errors still do so.

A form posted with a field it does not have, a field repeated, an unknown
action, or a hidden or always-sent field left out is still refused (400, code
`invalid`), but it is not treated as a wrong entry (#1401). The Admin portal
requires JavaScript, so even a required checkbox behind a gate is always
sent; such a post means a page left open while it changed, a stale copy of
it, or a hand-made post. Views raise the shared unexpected-fields refusal for
this, not a plain invalid-value error, and it says: "Nothing was done: this
page sent something the server did not expect, which usually means the page
changed after you opened it." What comes next depends on where it shows:

- In a dialog or an in-place region, which is still on the page, it adds
  "Reload the page, then try again."
- On the full error page that answers a form's POST, where the browser's
  Reload would send the same refused form again, it adds "Open the page
  again, then try again." with a **Return to the previous page** link to the
  form's page (on the delivery pages too). The page is titled **This page is
  out of date**, never "Check your entries".

An in-place control or dialog whose refusal has no explanation of its own
names what its status means (#1400): a bare 400 says the page sent something
unexpected and to reload, a 404 or 410 that the item no longer exists, and
only an outage says to try again in a moment.

An Admin (`/admin/…`) or Family (`/family/…`) address that no page answers,
because no route matches or a view raises the framework's not-found error, gets
the same treatment (#927): the page is titled **Page not found** and shows only
fixed text, never anything from the address. A signed-in Admin sees the usual
header and menu, from a read-only session check that renews nothing; a
signed-out visitor sees the layout without the menu, and so does everyone
during a restore review, when every real Admin page is closed. A Family address
gets the Family layout and its home link. A script gets the closed JSON error.
A not-found error a view reports itself keeps its own, more specific title and
message. Every other unknown address, including static assets, images, hosted
files and health probes, keeps the plain not-found response.

Dragging is only a pointer convenience. Every drag has a single-pointer,
keyboard-operable equivalent that does the same thing, such as a **Move…**
action with a date field (WCAG 2.2 SC 2.5.7). A drop never applies a change by
itself: it fills in the same review the equivalent opens, and only Apply
changes anything ([review dialog](#review-dialog)). Drag uses pointer events
with a few-pixel threshold, so a click still follows a link, is offered on
fine pointers only (touch uses the equivalent), and Escape cancels it. The
first user is the planned
[Campaign calendar editing](../admin-portal/spec.md#editing-from-the-calendar)
([#1521](https://github.com/epiphany40223/parishkit/issues/1521)).

Client validation improves feedback but never replaces server validation.
Browser-local timezone conversion uses UTC ISO timestamps supplied by the
server. The Admin portal [requires
JavaScript](#javascript-requirement) and shows a plain
notice without it. The Family multi-step flow may require JavaScript but must
show a clear supported-browser message rather than silently fail. A browser
too old for the Family flow's JavaScript likewise gets a plain notice asking
the Family to update the device's software or use another device or browser; a
small ES5 feature check reveals it and never alters the form.

### Step bars

**Rule.** A multi-step flow's segmented step bar is its quickest
navigation, in both portals. Each segment is one step: a 44px-tall
control drawing a slim bar, named "Step N of M: name" plus its status,
with `aria-current="step"` on the current step and a floating tooltip
that repeats the name on hover and keyboard focus and moves nothing
([no layout shift](#no-layout-shift)). A segment whose step can be
opened opens it, and doing so only opens that step: it saves, discards
and submits nothing, like Back and Next. A segment whose step cannot open
yet stays focusable but is `aria-disabled`, and its name and tooltip say
why; clicking or tapping it only shows that tooltip. Color is never the
only signal: the "Step N of M" line and `aria-current` name the current
step, and each status is text. **Why.** Readers expect to click a step
to reach it (Administrator, 2026-10-15, on
[#1402](https://github.com/epiphany40223/parishkit/issues/1402): the
setup wizard's bars were not clickable, unlike the Family portal's); a
step that silently refuses a click, or a hover-only explanation, leaves
touch and keyboard users guessing. **Shared code.** The segment's bar,
tap target, tooltip and anchoring (first, middle or last third) are one
set of rules in `ui-v1.css`, shared by the Family form's bar (drawn by
`family-v1.js`, whose segments switch pages inside the one form) and the
Admin steppers' bar (`stepper-steps.html`, shared by the
[setup wizard](../admin-portal/spec.md#bootstrap-and-first-admin-wizard)
and the [Campaign setup stepper](../admin-portal/spec.md#create-the-campaign),
whose segments are links to separate pages); `ui-v1.js` focuses an
unavailable Admin segment on click. **Step list.** Beside the bar, an Admin
stepper's full ordered step list is shown open on screens 64rem wide or
wider and is a collapsed disclosure on narrower ones
([#1404](https://github.com/epiphany40223/parishkit/issues/1404)). The page
draws both and CSS shows exactly one, so nothing moves after the page loads
and the hidden copy is met by neither Tab nor assistive technology. Which steps can open is each flow's
own rule, in its spec ([Family steps](../parishioner-portal/spec.md#form-state-and-navigation),
the setup wizard's and Campaign setup's).

### Charts

**Shared code.** `chart-v1.js` and `chart-v1.css`, described with the
[chart engine](../reports/spec.md#chart-engine).

All charts have a title, a legend (or, for a chart of separate panels, a
title on each panel naming its series), labeled axes with units, accessible
color/line patterns, hover/focus values, and equivalent data tables or values
drawn in the chart ([chart engine](../reports/spec.md#chart-engine)). They download as
PNG or PDF. A chart never has a second y-axis: dollar and count series, or two
series of very different size, go in separate panels on a shared axis, each
with its own labeled scale
([participation graph](../reports/spec.md#participation-graph)).
A chart's caption never repeats figures its table, legend or drawn values
already show; it keeps only what they don't, such as a scope note, and the
chart keeps its accessible name and description
([chart engine](../reports/spec.md#chart-engine)).
A stat tile that states a share may also draw it as a
[share bar](#stat-tiles).

**Hover guide line.** A chart whose x axis is dates, times or campaign days
and that shows tooltips draws a faint vertical guide line at the x value the
pointer, a tap or (where the chart has one) its slider is on, through every
panel of the chart, and that x value's tooltip lists its figures. Pointing
anywhere over the plot picks the nearest x value (each x value has an
invisible full-height hover band), so no one has to find a thin line or a
small point. The line is one shared colour (the ink colour at half opacity,
following the colour scheme), appears only while an x value is inspected, is
drawn over the chart without moving anything, is hidden from assistive
technology, is cleared when the page starts to print (so a pointer resting on
a chart does not leave it in the printout) and never appears in a download or
email image. Category and bar charts, whose rows or bars react as a whole, have no guide
line ([#1536](https://github.com/epiphany40223/parishkit/issues/1536)).

## Admin portal interaction

### JavaScript requirement

**Why.** One script-driven behavior per control is simpler and better tested
than a script path plus a no-script fallback; Families use whatever device
they have. **Shared code.** The `js-required` class (`ui-v1.css`) removed by
`admin-gate-v1.js`; `test_admin_javascript_gate.py`. **Precedent.** #565.

The Admin portal requires JavaScript and has no no-script fallback (#565).
Every Admin page, including the sign-in pages, renders its content hidden
behind a `js-required` class beside a plain-language panel: "The Admin portal
needs JavaScript. Turn it on in your browser settings, then reload this page."
The first-party Admin script removes the class at startup, and the hiding rule
lives in the shared stylesheet, so the content security policy still allows
no inline script or style. With script off, a page shows only the panel and
offers no action. Links and forms keep real `href` and `action` attributes,
because they are the targets the scripts request. Every action is still
validated on the server, in the same service code the
[Admin automation interface](../admin-automation/spec.md) calls; client-side
checks are a convenience, never the only guard. The Family portal is not
gated and keeps working without script
([client behavior](#accessibility-and-client-behavior)).

### In-place controls

**Why.** Staff lose their place, their scroll position and
their unsaved typing on a reload. **Shared code.** `ui-v1.js` in-place
regions (`data-in-place`, `data-table-region`, `data-in-place-region`),
`live-status-v1.js` for status regions; `test_in_place_controls.py`.
**Precedent.** #519, #478, #484, #488, #532, #562.

An Admin control acts where the reader is: it never reloads the page or sends
the reader back to its top (#519). The [table controls](#admin-tables) are one case;
the same shared mechanism (`ui-v1.js`) serves any control a page opts in.

A status line appears next to the control that caused it: under the form or
button it answers, or inside the pane that holds that control on a page with
several panes (each pane its own line), never in one line elsewhere on the
page. So the reader sees what happened where they are looking, and the page
never scrolls to show it (Administrator, pk-local review of
[#750](https://github.com/epiphany40223/parishkit/issues/750), 2026-10-15).

- **Regions.** A page marks each part a control can change as a region, an
  element with a stable id (a table's region, or `data-in-place-region`). The
  id is also the control's URL fragment, which is required: the script finds
  the region by it, and an ordinary load lands on the region instead of at
  the top. The fragment must name a region in the same template as the
  control (not one a base or included template draws); a template test
  refuses an in-place control whose fragment names no region there.
- **Controls.** A link that shows another view of the page (`a[data-in-place]`:
  each segment of a [binary choice](#binary-choices), such as the
  [Response dashboard](../reports/spec.md#response-dashboard)'s mode and
  Chart population, each of which refreshes the page's one region; the
  [Family timeline](../reports/spec.md#family-timeline)'s mode and When sort,
  "Refresh current work" on "Background work", "Refresh list" on
  "Families on the form now", the page links of a mail delivery's
  evidence and attempt history, an information item's and a Ministry
  follow-up request's history, and a weekly information report, and the
  Refresh links of the key replacement status, Family email progress,
  Family test, Background task, Change status, report export and
  latest-data export pages; Refresh task also keeps the task history's
  sort and page) or a form whose answer is the page again
  (`form[data-in-place]`, such as a POST whose server redirects back to the
  page: Save follow-up on an information item and on a
  [Ministry follow-up request](../admin-portal/spec.md#follow-up-workflows); System logs'
  cross-links, whose answer is the filtered page; Acknowledge on a security
  event; Clear verified refusal on a
  [refused address](../admin-portal/spec.md#refused-addresses); Dismiss on an
  integration's finished key change; the
  [Campaign comparison](../campaign-history/spec.md#page-layout)'s
  Compare on day, which refreshes everything below it)
  names its region by its
  URL's fragment. A POST form saves a change unless it is marked
  `data-in-place-read` (the System logs cross-links only read): a read may
  be cancelled by a newer choice and, with no answer at all, falls back to
  the ordinary submission. A form or link marked `data-in-place-filters`
  sets the page's filters from outside its filter form (a System logs
  cross-link), so after the swap the filter
  form's visible fields, and any disclosure in it, show what the fresh page
  applied, and the next Apply, sort or page keeps them; every other swap
  leaves filters typed but not yet applied alone. A region a
  control can empty (the security events, the critical-problems banner) is
  drawn even when it has nothing to show, so the answer that empties it
  still carries it. A form marked `data-in-place-anywhere` changes a region
  every Admin page draws (the
  [critical-problems banner](../admin-portal/spec.md#navigation-and-home)'s Acknowledge, which the
  server answers with Home from any page): any same-origin answer will do,
  and only that region is taken from it, never the answer's other regions,
  synced controls or address. A refused acknowledgement shows its error
  page whole, with its own explanation: that page draws the banner too, but
  its banner says nothing about the refusal. Because the banner's region is
  on every Admin page, any other in-place control that refreshes every
  region (a Save, a filter, a view switch) also refreshes the banner from
  its answer, so the banner shows what that answer's page shows. A report whose address has no
  time zone loads the same address with this browser's zone added in place,
  as it loads and again after any in-place refresh that brought a page
  without one (a daily-table link followed before the zone was applied):
  every other parameter and the address's own fragment are kept, focus
  stays where it is and nothing is announced (the request is a link marked
  `data-in-place-quiet`), and if it gets no answer the same address is
  loaded the ordinary way, without a jump to a panel. Because the chart,
  statistics and export panels are all regions, paging or sorting the daily
  table refreshes all three from the same answer: an export format chosen
  but not yet used and an open "Technical details" return to their
  defaults, as an ordinary load would leave them. A
  disclosure marked `details[data-keep-open][id]` is the exception: one the
  reader opened stays open in the fresh copy of its region, so a refresh
  does not fold away what they were reading (the
  [Campaign comparison](../campaign-history/spec.md#page-layout)'s data
  notes,
  [#1161](https://github.com/epiphany40223/parishkit/issues/1161)). A chart
  inside a closed disclosure is drawn when the disclosure opens, since its
  panel has no width before then
  ([chart engine](../reports/spec.md#chart-engine)). A pane selector
  (`select[data-pane-select][id]` in a `[data-pane-group]`) shows the
  `[data-pane]` whose value it names, with no request; the panes share one
  grid cell, so the group is as tall as its tallest pane and a choice
  moves nothing, and the selector keeps its choice in the fresh copy of
  its region (Campaign comparison's pledge distribution year,
  [#1221](https://github.com/epiphany40223/parishkit/issues/1221)). A checkbox marked
  `data-submit-on-change` submits its own `form[data-in-place]` as soon as it
  changes, with no Apply button (the Admin portal requires script), and keeps
  focus. Each page records the state it shows on the box. The box submits
  again only when its own change got no answer of its own and the page
  shows another state once nothing is in flight: it changed again while its
  request ran, a newer request overtook that one, or a save held it back
  (it then says "Still saving…" beside it too). It never resubmits after
  its request fell back to a full load or got a page shown as returned
  (no answer, a refusal, another page), and never sends a state that
  failed again until the reader changes the box or its own request succeeds, so
  a server that cannot answer gets one attempt and the fallback, not a
  loop (Automation access's "Include ended sessions"). "Refresh current work" keeps
  the reader's state filter, sort, rows per page and page; "Refresh list"
  keeps sort and rows per page and returns to the first page. A form's
  submit button belongs to its form even outside it (`form="…"`); a table's
  sort heading or navigator inside a `form[data-in-place]` (a selection form
  around its table) is still a table control. A POST form should carry a
  `data-in-place-message` ("Saved.") for the live region; without one the
  clicked button's text is announced.
- **Request.** The browser fetches exactly the request the control would
  have made, follows the server's Post/Redirect/Get redirect, and replaces
  every region the fetched page shares with this one, plus the counts,
  summaries, links and form state outside them that follow the view; views
  keep rendering whole pages, so no partial-page endpoint exists. A form
  marked `data-csrf-header` also sends its CSRF token in the `X-CSRFToken`
  header, for a route that checks only the header so that it can refuse a
  request before reading its body (Past campaigns' archive Review,
  [campaign history](../campaign-history/spec.md#validation)). A link
  marked `data-in-place-only` replaces only the region it names: the history
  pages of an information item and a Ministry follow-up request sit inside
  the item's panel, and paging them must not replace the Save follow-up form
  above or discard notes typed but not yet saved; a save still replaces the
  whole panel, history included. The attribute's value may list further
  region ids to replace as well: a Mail delivery resolution also redraws
  the email's state and history. A region marked `data-in-place-own` is replaced only by the answer
  to a control that names it (or lists it in `data-in-place-only`), never by
  another control's answer or a redraw: Hosted files' Upload panel keeps a
  chosen file and a typed name across a sort, a page or a deletion, and a
  Mail message's resolution forms keep typed evidence across paging its
  history. A resolution posts in place: it lands on the Admin evidence,
  whose heading takes focus, and lists the state and recipient lines,
  history, forms and bounce panel to replace too. An export's Retry and
  Cancel swap its status panel and hand focus to Refresh status below.
  Preview and test email's Send this test email posts in place too: its
  notices and form are one region and Recent test results another, whose
  heading takes focus. The list updates by itself while a test is on its
  way, and once the test settles a quiet follow-up re-reads only the
  form's region, so Send, the acknowledgement and a fresh preview come
  back without moving focus; the page has no manual refresh link.
  Off-site backups' Test access posts in place as well: its status line
  is swapped in and watched while the check runs, focus stays on Test
  access, and the settled check re-reads that line in place, so a folder
  link typed but not saved is kept. Save in the same form opens the
  [review dialog](#review-dialog), and Test access is marked
  `data-review-skip`, so it is no Review; the integration settings form's
  region is `data-in-place-own`, so only its own Review's Apply redraws it
  ([#750](https://github.com/epiphany40223/parishkit/issues/750)). One
  request is in flight at a time: a newer choice cancels an older
  read, but a POST that saves a change is never cancelled, and other
  in-place controls and repeats are ignored (the live region says "Still
  saving…") until it settles.
- **Place, focus and announcement.** The reader keeps their scroll position;
  focus returns to the control (or its fresh copy; when that is gone, the
  link its `data-in-place-fallback` key names, so Next page on the last page
  hands focus to Previous page; when that is gone too or disabled, the
  region's first heading, else the region, or the page's own heading when
  the region is now empty); the region
  is marked busy while the request runs, and a polite live region says what
  happened ("By day", "List refreshed." and the rows now shown). A view
  choice replaces the address, and a followed redirect sets it to the
  redirect's page, so reload, bookmarks and Back never re-send a POST and do
  not step through choices. The Refresh links of Go-live readiness
  ("Readiness preview refreshed."), Testing cleanup, Prepare Family links
  and Production activation ("Progress refreshed.") re-read their page's
  own address this way rather than reloading it; Prepare Family links keeps
  the history page shown, and a disclosure such as Technical details stays
  open because only the list inside it is a region. Their command buttons
  post in place too: Check readiness and public web address (focus returns
  to Check, and the overall result is read out: why cleanup can't start yet
  when any check failed, else that every check passed), Testing cleanup's Retry and
  Cancel, Prepare inactive Family links and each preparation's Cancel, Retry
  and Retry disposal, and Retry failed mail preparation. A button that is
  gone once its request is taken hands focus to the page's Refresh link just
  below (cleanup, Production), or, on Prepare Family links, to the newest
  preparation's heading; Start Testing cleanup stays a navigation to the
  cleanup page.
- **Errors and fallback.** A POST that got an answer is never sent again; a
  saving POST is never re-sent at all; a read-only table POST with no answer
  falls back to the ordinary submission. A refused POST (an error answer, or
  a page with an error summary, such as a form re-rendered with its errors
  and the values the reader sent) that carries the control's region and was
  not redirected to another page is swapped in like a success (#562): every
  shared region and the data outside them that follows the view are
  replaced, and the address is unchanged. The error summary, which the base
  template draws above the page content, moves to the top of the region when
  it is drawn outside the region; this page's own older summaries outside
  the regions are removed then (a successful swap leaves them). The reader
  keeps their place, focus moves to the summary (or to the control when
  there is none), the fields it names are marked in error beside their
  message as the fetched page draws them, and the live region reads the
  summary's messages, or says
  the server did not accept the change when there is no summary. A refused
  POST answered as JSON (a typed error, which the view sends to every
  script's fetch: a user-facing refusal, a stale page, a denial or an
  outage) is never shown as the page, since that would be raw JSON (#519).
  It is shown in place as one alert line after the control's buttons, so
  nothing above it moves: the refusal's message and fix in plain words
  (without them, the confirm dialogs' wording for its status) and its link
  when it has one. The page, the address and focus stay, and the next
  answer to that form replaces the line or removes it. Any other
  refusal, and a POST's own answer that is not this page, is shown as the
  whole page, as a native submission would show it. That page keeps the
  window, so the old page is first told it is going away (a `pagehide` that
  is not persisted: the pollers that listen for it stop, and a reply already
  in flight can neither re-arm them nor act), and every timer id in the
  window is cleared, so no old timer runs beside the new page's own. The
  sweep relies on browsers numbering timers in sequence, as current engines
  do. A saving POST that got no answer at all may have been saved, so an
  alert at the top of its form says the server could not be reached and to
  reload the page to check. A redirect to another page (elsewhere, or the
  sign-in page) is followed by loading that page's address. A read (a GET)
  that fails falls back to the ordinary load, except when the browser cut
  it off because the reader is leaving the page: their own navigation goes
  ahead. A save with no answer still shows its note then, since a download
  link also starts leaving the page and the page stays. A step-up stays an
  ordinary navigation even inside a region: on Send to chosen Families, Check
  these Families and Send these Family tests act in place, and a review
  swapped in with a stale sign-in keeps its Confirm with Google form, which
  loads the sign-in page (in LOCAL, the page records the return path for a
  swapped-in step-up as it does for one it loaded with). A Send refused for a
  stale sign-in is answered with the step-up page, shown whole. That page's
  Refresh status replaces only the recent tests (and the critical-problems
  banner), so Family DUIDs typed but not yet checked are kept, and after
  Send the requested notice takes focus. A file upload is
  sent in place as the multipart body a native submission sends: on
  [Hosted files](../hosted-files/spec.md#admin-page), Upload follows its
  redirect, so the new file's row, the notice (read out) and the usage line
  appear and the Upload panel comes back empty, with focus on its file
  field, since Upload waits for a file. A refused file (a wrong type, or too
  large for the server) is swapped in with its error beside the field and
  in the summary, which takes focus. On the setup wizard's Pages and
  emails step, Fill in and Reset each land on their own region, so focus
  stays where the reader is (the notice that replaces Fill in; Reset's
  heading, since its box comes back unticked), and the stepper and each
  entry's state update. Neither needs a fresh sign-in; an answer without
  the region (the step-up page, a stale version's error page) is shown
  whole.
- **Real targets.** Every control is a real link or form with its `href`
  or `action` and fragment, which the script requests; when an ordinary load
  happens instead (a fallback above), the fragment, kept across a
  fragment-less redirect, lands it on the region. The Admin portal
  [requires JavaScript](#javascript-requirement), so in-place work builds and
  tests no no-script fallback.
- **Policy.** The mechanism runs under the strict content security policy:
  first-party script and same-origin requests only; it changes attributes and
  classes and creates elements with text content, never inline script or
  style, and never uses `eval`. Swapped content is enhanced
  again as the page's own was (dates, selections, copy buttons, charts, and
  a form's conditional fields and its
  [complete-before-submit](#prerequisite-gates) gate), and a
  `parishkit:swap` event on it lets any other script do the same. A page
  that watches background work (`live-status-v1.js`) stops watching a
  status region an in-place refresh replaced, and watches the fresh one
  while it is still pending, so a Refresh or Dismiss that brings back work
  in progress keeps updating by itself; there is always one watcher per
  status region. A quiet follow-up refresh (a settled status re-reading its
  page in place) keeps focus: a focused element in a region it replaces is
  focused again in the fresh copy, by its id or else by its link and text,
  without scrolling ([#1354](https://github.com/epiphany40223/parishkit/issues/1354)). A control that must sit inside a status region, next to
  the status it answers, goes in an element marked `data-live-keep`: once
  it has contents, each status update puts the live element back in place
  of its fresh copy and ignores its contents when comparing, so an open
  preview, its ticks and focus survive; focus in it never holds an update,
  since the focused control is moved and focused again. An empty one takes
  the update's contents. When the update names something else than an open
  preview reviews (`data-live-keep-key` against `data-live-keep-for`), the
  preview says "This changed since you looked; review it again" on a line
  reserved for it, and its confirming button is held for good. A control
  outside the status region can be checked the same way: the region names
  the current thing (`data-live-key`), and a preview inside an element
  marked `data-live-keyed` with that name is marked changed the same way
  (System health's
  [Accept this change once](../admin-portal/spec.md#accept-a-large-parishsoft-change-once),
  in its tile's details under the refused change it answers). A Refresh reads out the region's coarse status sentence
  when it changed, as a poll does. Dismiss refreshes only an integration's
  status line: if it brings back a new key change in progress (another
  Administrator started one), the settings form and notes below it keep the
  state the page was loaded with until the next load.
- **Withdrawn reviews and held heights (#532, #736).** The in-place
  settings review panel of #532 is retired
  ([#1429](https://github.com/epiphany40223/parishkit/issues/1429)): every
  settings editor reviews in the [review dialog](#review-dialog) (#750), and
  Change status never follows back to a settings page. Two of its rules stay
  for the in-place reviews that remain (Outgoing mail's bulk review and Past
  campaigns' import). Editing the form after a review, or while its Review is
  in flight, withdraws the review (`data-review-of`), so Apply never applies
  values other than those shown: Apply is removed, the rest stays in place
  greyed out at no less than its height (so nothing moves under the pointer),
  and its last line says to review again (in the review's own words when it
  has them, `data-stale-note`). Whenever a review or status region marked
  `data-keep-height` changes (its own swaps, a withdrawn review and live
  status updates alike: Restore review, Past campaigns, Held emails and
  Refresh from ParishSoft's latest run), it is held at just the height that
  keeps the page's foot at or below the bottom of the window where the
  reader is: a reader scrolled down to it at the foot of the page would
  otherwise see the page pulled down under the pointer when a shorter status
  (Applied) replaces a longer one. The trade-off is blank space below a
  shorter status for a reader at the foot, which stays until the next change
  to the region (or a reload); a reader higher up the page needs little or
  no hold, so a long review followed by a short status leaves no large gap.
  Share options' form, a region of its own, is redrawn after Apply with the
  applied list and a fresh blank row, which is then the saved list its Save
  compares with ("Change an option to save it.").
- **Selection tables keep their ticks (#750).** Ministries once reviewed an
  activity change of its row selection in place; it lists the catalog and
  changes nothing now (#1267), so no page reviews a table selection in
  place. Every redrawn selection table (Hosted files and Schedule settings)
  keeps the ticks of the rows it still shows (selection is per page, so a
  row on another page loses its tick). `ui-v1.js` shows the "were not kept"
  line only when an answer the reader did not choose as a table view (the
  quiet refresh once a change is applied) drops a ticked row because it is
  no longer listed; a re-sort, page or filter they chose says nothing.
  Select all / Clear selection withdraws a review on any page that has one.
- **Review dialog: No-reminders WorkGroup (#750, #1356).** The No-reminders
  WorkGroup editor confirms in the [review dialog](#review-dialog): its one
  name field is in a form, a region of its own, whose **Save** waits for a
  change (hint "Change the WorkGroup name to save it.") and opens a
  confirmation, since the new name is the form's and the current one is on
  the page under "Now": one sentence says what Apply does, from the next
  ParishSoft refresh. What the newest ParishSoft refresh found is an
  in-place region of its own, so the redraw once a change is applied brings
  it up to date; it sits below the form and its status line, so a status
  whose length changes never moves the form.
- **Review dialog: Campaign images (#750).** Campaign images reviews in the
  [review dialog](#review-dialog). It has no settings form: each slot's
  upload form (multipart, sent as
  [preparing the review](#review-dialog) says) and its
  Remove this image (a text-styled button in a form of its own, drawn only
  when the slot has an image) open the dialog with the image as it will
  appear, or with a [confirmation](#review-dialog) of the removal ("Remove
  this image?", #1356). The slots are one in-place region, redrawn after
  Apply with the new current image. Each slot is the same height with or
  without an image (its image box keeps the slot's image size, its caption
  is one line, and the removal line keeps the button's space), so that
  redraw never moves a slot. Each slot ends with its own reserved status
  line, which its upload and its Remove this image name, so the line saying
  what was applied is in the slot where the change was made and the page
  never jumps to it. A refused upload is said in that same line
  ([field errors stay on the form](#review-dialog)), which keeps two
  lines of space (three on a phone), so the whole reason for a file that
  is no usable image wraps there and nothing moves when it appears; that
  refusal names only what the slot takes, so it fits. Each upload form has
  an id, so a file chosen while the slots are redrawn is said not to be
  kept.
- **Review dialog: Parish logos (#750).** Parish logos reviews in the
  [review dialog](#review-dialog). It has no settings form: its upload form
  (multipart, sent as [preparing the review](#review-dialog) says, with its
  [Basic and Advanced modes](#basic-and-advanced-modes)) opens the dialog
  with every generated size, each marked changed or unchanged. The form is
  an in-place region, redrawn after Apply (cleared, and opened in the mode
  that saved the new logos), and the status line sits under it. The current
  logos are a region of their own below the status line, so logos appearing
  never move the form or the status line. The form has an id, so a file
  chosen while it is redrawn is said not to be kept.
- **Review dialog: Parish settings (#750).** Parish settings reviews in the
  [review dialog](#review-dialog) as a compact review (#1356): its button
  reads **Save** and the dialog, "Save the Parish settings?", lists one
  "Setting: old → new" line per changed setting (every Parish setting is
  short, so no change keeps a full review), with a new timezone's note; an
  unset online giving address reads "Not set". Its form is an in-place
  region, redrawn after Apply at the applied version, with the status line
  under it. A field's refusal
  (the Parish website's included) stays on the form; a stale page or an
  out-of-date preview is refused in the dialog, and the form is not
  redrawn, so it is refused again until the page is reloaded.

### No layout shift

**Rule.** Nothing appears, disappears or changes size under the pointer. A
hint, reading, error or status that can come and go has its space reserved
(one line's height kept while empty), or sits after the controls it explains,
so a click never moves the next control the reader is about to use. **Why.**
A control that jumps as the reader reaches for it causes mis-clicks on the
wrong action. **Shared code.** The patterns already in `ui-v1.js` and
`ui-v1.css`: the time entry's reading line keeps its height while empty
([time entry](#time-entry)); a withdrawn in-place review stays greyed at no
less than its height ([in-place controls](#in-place-controls)); a gate's hint
sits after the button it holds, never on its line
([prerequisite gates](#prerequisite-gates)); the selection count and Select
all sit after the bulk action buttons ([Admin tables](#admin-tables)); a
confirmation dialog shows a refusal below its buttons
([row actions](#row-actions-and-confirmation)); the theme always reserves the
scrollbar's space and every "i" toggletip opens a floating bubble that moves
nothing ([page help](#page-help)); `data-complete-hint="reserve"`
on a complete-gate hint keeps its one line's height while empty instead of
hiding it (#959); a field that a choice makes inapplicable stays in
place, greyed, rather than disappearing, through `data-enabled-when` (#959)
or `data-locked-when` (open #957) ([conditional
fields](#conditional-fields)). **Precedent.** #736 and its review
(Administrator, 2026-10-08), #563, open #957, #959.

Clarification for disclosures. A disclosure moves only content after
its toggle when it opens or closes, which this rule allows: the toggle
and everything before it stay put. In a grid of disclosures, content
beside the toggle counts as before it, so an opened disclosure's content
never grows the toggle's own grid cell; it opens as a full-width band
after the toggle's row, and the toggles of that row keep their places.
The page source keeps each band directly after its toggle, so keyboard
and screen-reader order goes from the toggle into its content; the next
toggle of the same row can sit above that band on screen. A live update
that would change an open band's height waits while the pointer is over
the grid. First user: the
[System health dashboard's tile grid](../admin-portal/spec.md#tile-grid)
(Administrator, 2026-10-13, on
[#1214](https://github.com/epiphany40223/parishkit/issues/1214)).

Recorded exception (Administrator, 2026-10-11, on
[#1015](https://github.com/epiphany40223/parishkit/issues/1015)): on the
[Mail message page](../admin-portal/spec.md#mail-message-page), the
attempt history sits above the resolution forms, so paging it with the
upper pager can change its height and move the forms and the evidence list
below it. The Administrator accepted this so the page reads in the order
asked for.

### Disclosures

**Rule.** Two rules for Admin disclosures (Administrator, pk-local batch
24c, on [#1214](https://github.com/epiphany40223/parishkit/issues/1214)).

- **A fragment opens only its target.** An address whose fragment names a
  disclosure, or the card that holds one, opens exactly that disclosure
  when the page loads, and every other disclosure in its group starts
  closed, whatever its default (a troubled tile that would start open
  included). The page then brings the target into view, since closing
  what was above it moves it. Without a fragment, or with one that names
  no disclosure (an [in-place control](#in-place-controls)'s region, for
  example), every disclosure keeps its default. After the load, a link on
  the page to a disclosure, or a changed fragment, opens that disclosure
  and closes nothing: the reader's own choices stand, and nothing moves
  under the pointer.
- **A click anywhere on a disclosure card toggles it.** Where a disclosure
  is drawn as a card (a summary row with its details beside or below it),
  the whole summary row is the toggle, and a click on the open details'
  blank space or plain text closes it too. A click on or inside a link,
  button, form field, label, `summary`, dialog, toggletip or other
  focusable element does that element's own job and never toggles. A
  click that makes a text selection, clears one, or is part of a double
  or triple click does not toggle either: a details click waits out the
  second click before it closes. Closing from the details moves focus to
  the toggle, so focus is never left in hidden content.
- **Accessibility is unchanged.** The toggle stays one real `button`
  (with `aria-expanded` and `aria-controls`) or `summary`, the card's only
  tab stop and the only control a screen reader announces; Enter and
  Space work as before. The details get no extra role or tab stop: the
  details click is a pointer convenience. The details keep the text
  cursor, so selection still looks possible.

**Why.** A link to one area should show that area, not every area that
starts open; and a card that looks like one thing should answer a click
anywhere on it, not only on its "Show details" cue. **Shared code.**
`system-health-v1.js`; a page that adds fragment-opened disclosures or
disclosure cards follows the same rules. Opening and closing still moves
only content after the toggle ([no layout shift](#no-layout-shift)).
**Precedent.** The [System health tiles](../admin-portal/spec.md#opening-and-closing-tiles).

### Prerequisite gates

**Rule.** A button is unavailable until its prerequisites are met and there
is something for it to do, and while it is unavailable a short visible reason
says what is missing. A Review button is unavailable until the form differs
from what is saved. The server still checks every submission. **Why.** A
button that is available but refuses afterwards wastes a round trip and reads
as an error; a button greyed out with no reason reads as broken. **Shared
code.** `ui-v1.js`: `data-require-complete` with `data-complete-hint` and
`data-missing-hint`, and `data-complete-hint="reserve"` to keep the hint's
line while it is empty (#959); `data-required-when` and
`data-required-when-shown`; `data-enabled-when` and `data-locked-when`
([conditional fields](#conditional-fields));
the acknowledgment gate (`data-acknowledgment-gated`); the time-entry gate;
the change gate `data-require-change` (#924) and, on such a form,
`data-change-alone` with `data-change-alone-hint`, a group of field names
that must change on their own: while one of them and any field outside the
group differ from the saved values, Review waits and the hint line gives the
words (Campaign settings: a live campaign's Ministries; a draft's dates and
time zone while it has mail schedules, #1330); the shared `holdButton` /
`releaseButton` pair and `GATE_MARKS` list (#924), so gates never release a
button another gate still holds; the `pageshow` re-check;
`test_campaign_setup_gates.py`, `test_prerequisite_gates.py` (#946).
**Precedent.** #553, #563 (and its slices, such as #946), #921 / #924.

This includes a table's bulk action buttons: while they are unavailable,
their reason is visible text in a reserved line beside or below them, so
ticking a row never moves anything ([Admin tables](#admin-tables);
Administrator decision, 2026-10-10;
[#879](https://github.com/epiphany40223/parishkit/issues/879)).

A file field marked `data-max-bytes` (with its message in
`data-too-large`) counts as missing while the chosen file is larger, so the
complete-before-submit gate below holds the form's button with that message
as its hint; first used by Past campaigns' archive file (20 MiB). The server
checks the size again.

Wherever a form requires an acknowledgment checkbox (finishing setup, a test
that may already have arrived, chosen-Family tests, Testing cleanup,
withdrawal, refusal removal, bounce reports, manual reports, duplicate
resends, resetting all pages and emails to their default text), the page
script keeps the form's primary button disabled until the box is checked,
and the server refuses a missing acknowledgment. While the button waits, a
short hint directly after it (named by the button's `aria-describedby`) says
to tick the confirmation, so only content below the button moves when it
clears. A form that arrives
through an in-place update is gated the same way (#563).

More generally, a form whose fields depend on other choices keeps its submit
button unavailable until every visible required field is complete, with a
short hint by the button saying what is missing (`data-require-complete` in
the page script; first used by
[Ministry follow-up](../admin-portal/spec.md#follow-up-workflows), #553). Fields required only in
some states are required only while shown (not hidden or greyed). A required text field holding
only spaces counts as empty, as the server trims it: the reason for
cancelling go-live and the evidence notes on Mail delivery and delivery
refusal pages use this gate, as does the typed "Production" confirmation
(which, like the server, ignores spaces around the word). The Admin portal
requires JavaScript ([#565](https://github.com/epiphany40223/parishkit/issues/565));
server validation is unchanged and still refuses an incomplete submission.
Likewise, a settings form's Review changes button is unavailable
until the form differs from the saved settings, with the short hint "Change
a setting to review it." after it; undoing the edit makes it unavailable
again (`data-require-change`, naming the form's version field,
[#921](https://github.com/epiphany40223/parishkit/issues/921)). The saved
settings are the values the server drew for the form's version, so values a
browser restores are not mistaken for them; once this page's change is
applied, the reviewed values are the saved ones while the form itself is
kept, and after an applied change a redrawn form compares with its new
defaults. A control marked `data-change-ignore` is no change: one that only
shows other fields (Edit scheduled email's "Edit this schedule's email",
whose fields count instead), and plain text generated from the HTML, which is
not sent. A row a page script adds or removes counts, and a hidden field
counts only when it is marked as carrying a setting
(`data-change-setting`): a saved row's identity, or a saved setting the page
shows only as words, such as ParishSoft's stored quick updates, whose
removal is a change (#1334 review). Every other hidden field is no change:
the version and security tokens, a formset's row counts, a one-time request
key or signed intent, the browser's time zone, a table's filters, size and
sort (so a form redrawn with the table at other view settings still waits),
and the fields a script writes from the visible controls it follows. A form
redrawn at the same version takes its hidden settings from the new drawing.
The hint keeps its space while hidden, so it never moves the page.
The server still refuses an unchanged Review ("No settings have changed."),
only as a safeguard for requests that bypass the page script: no page draws
a "nothing has changed" state ([review dialog](#review-dialog)).
Gates that share a button release it only when none of them holds it, and
never enable a button the server drew disabled (marked
`data-server-disabled`, so a disabled state Firefox restores on a reload
is not mistaken for the server's).
A browser can restore a page from its history (Back or Forward) with the
reader's values but without the events that set the page up, so these
states (shown and hidden fields, unavailable buttons and their hints, a
table's selection, the campaign modules and mail schedule rows) are worked
out again when the page is shown (`pageshow`, #563).

An action the server would refuse for a reason the page already knows is
shown unavailable, named by that visible reason (`aria-describedby`), rather
than left enabled or left out (#563). This covers Save and continue on a
setup credential step while an earlier step is not done, Finish setup while
setup is not ready, and Send on a campaign test email while a test is on its
way. On Send to chosen Families, Send is unavailable with the first reason
the reviewed Families cannot be sent: test codes not ready, campaign work in
progress, a Family that cannot be sent a test, or more Families than the
remaining allowance. Check these Families waits for a DUID. Prepare inactive
Family links is unavailable, with its specific reason, while the campaign is
no longer a draft, Testing cleanup is unfinished, other inputs (such as a
ParishSoft refresh) are not ready, earlier links still need Cancel, or
cancelled links are still being discarded. On the Off-site backups page,
Test access is unavailable until Google Workspace mail is set up, since the
check signs in as its mailbox user, and always in the local environment,
which never reaches Google Drive. In the Pages and emails editor, "Remove
this selected content" is unavailable, with its reason as the box's help,
while nothing is saved there or while mail schedules still send the email.
The box is an action, not a setting, so its reason stays visible rather than
in a tip; it takes the help line's place, adding no line.
The server still checks each of these.

The integration settings pages (other than ParishSoft, whose refresh
schedule editor gates Save itself) and both steps of the backup encryption
key change use the complete gate too. While Slack is not set up its token is
required, as the server requires it. The new public key and the code from
`backup-prove` carry a `pattern` the server's rule also admits (the code read
as leniently as the server reads it), and a refusal of either, or of a
missing Slack token or a new ParishSoft key sent with a refresh schedule
change, is shown at that field rather than as a notice or an error page
(#563).

The settings editors follow the same gates (#1330). Save on every
integration settings page, ParishSoft's included, waits for a change, and a
pasted key counts as one; Off-site backups' Test access, which can test the
saved folder, never waits for one (`data-change-exempt`), and a folder link
that isn't a Drive folder is refused at its field, as Save refuses it. While
an integration that isn't set up yet can't store its key, its Save is shown
unavailable, named by the notice that says why. The Pages and emails editor's
Preview changes waits for a change, except while the page offers saving what
it shows as it is (the default text filled in, a saved text that needs
re-saving, a confirmation email with a retired receipt closing note folded
in, a refused Preview drawn again). On Campaign settings the Ministry
leader roles need one ticked while Ministry stewardship is on, and on a live
campaign Save waits while the Ministries and another setting both changed
(`data-change-alone`), with the server's own words. The automation
approval's user code and New and Edit user's Email and Name are checked in
the browser, with their refusals at the field, and Continue, Add the user
or Save waits for them (a Name the Email lookup fills in or clears counts
as an entry).
The Parish website carries a pattern too: on Parish settings, which has no
complete gate, the browser marks a website with a user name, query or
fragment at the field when it is left, and the server's refusal is shown
there; Save still waits only for a change. An integration page drawn
again after a refused Save shows what was posted, so its Save does not
wait for a change then.

The same holds for the rest of the Admin portal (#563, the audit's last
slice): Start Testing cleanup on Go-live readiness and Confirm Production
before a passing check (named by the step still needed); Confirm cancelling
go-live while mail blocks it; every Confirm on Pause and resume mail, Send on
chosen-Family tests, and Close or Reopen on Family portal availability
without a recent sign-in; the retry the Mail message page would offer while
the resend admission refuses it; Queue new manual report while an earlier
Weekly Admin digest is still being prepared or delivered (checked when the
page opens); and Upload on Hosted files while the library is full. A button
the server draws disabled because other campaign work holds the page (report
exports, follow-up saves, the Response dashboard) or because the sign-in is not
recent (automation approval) names that notice too.

The same applies wherever the page already knows the refusal, whatever the
reason: other campaign work holding the action, a missing prerequisite the
server checks first, or a sign-in that is not recent (named by the shared
step-up notice). When that notice can appear on an in-place answer, it is
drawn after the controls it disables, so nothing moves
([#1331](https://github.com/epiphany40223/parishkit/issues/1331)). Each
feature spec lists its own cases, such as
[Restore review](../admin-portal/spec.md#restore-release) and the
[Family timeline](../reports/spec.md#family-timeline).

### Field errors and live checks

**Rule.** An error is shown at its field: the message beside it, the field
marked `aria-invalid="true"` and described by the message, and the page's
error summary linking to it. A rule the browser can check is checked live; a
condition only the server can decide (such as whether a refresh schedule
would collide with Family email) is checked live by a read-only request that
saves nothing. **Why.** A summary at the top of a long page, or an error found
only on Save, sends the reader hunting. **Shared code.** `ui-v1.js` field
errors (`data-field-error`, `clearFieldError`), `ui-v1.css` error styles;
live no-save checks such as `refresh_schedule_check` in
`accounts/integration_views.py` (#920). **Precedent.** #592, #562, #920.

A field error is shown at that field: its message sits directly beside it,
and the field is marked in error (`aria-invalid="true"`, described by the
message, with the shared error border and error-coloured message of
`ui-v1.css`, the same markup Django forms render on the settings and setup
pages). An error that concerns two fields (a date and a time) marks both and
shows its message once, after them. Where a page also shows the error
summary, the summary links to the first marked field and takes focus, as
every Admin refusal summary does (#592). A review dialog's pane with its own
status line says the refusal in that line instead
([field errors stay on the form](#review-dialog), #1410).

Where a rule can be checked in the browser, the page checks it too, and
shows its error at the field the same way. The browser's own checks (a
required field, a format) mark a field when the reader leaves it, and clear
once the value is valid. A page can also check a rule live, as the value is
entered (a follow-up contact time in the future), or have the server check
one field as it is typed (`data-live-check` in the page script, a
single-field check first used by a live campaign's
[end date](../admin-portal/spec.md#campaign-configuration), #944; the ParishSoft refresh
schedule's multi-row editor, #920, checks its whole formset with its own
mechanism for now). Save is held while such an error stands only on forms
that use the complete-before-submit gate above. Three shared checks in
`ui-v1.js` cover the common cases (#563): `data-not-before="<start id>"` on
an end date (or a text amount) keeps it no earlier than its start, as the
report and System logs filters and the campaign and financial period end
dates on Campaign settings do (an end on its
start's own day and a financial period that isn't exactly one year are
refused by the server, at the end field); `data-max-bytes` on a file input
refuses a file larger than the limit, or than the room left, at once (Hosted
files);
and `data-pattern` gives a textarea, which has no `pattern` of its own, a
rule to match (no "@" in the Family portal maintenance message).
The server stays the authority: it checks every save, and its refusal is
shown at the field as above, even when the browser would have allowed the
value (a wrong computer clock, for example).

A report's filters follow the same rule (#1332). A filter the server
refuses at one field (a request built by hand, or a browser whose check did
not run) draws the report again, answered 400: the filters show the values
sent, with the error at that field and in the summary, and the results are
those for no filters, as Campaign comparison shows its current day for a
refused day. A refusal no field can show (an unknown or repeated field, a
choice no form offers, a sort or page size the table does not accept) keeps
the report's error page. A range out of order is refused at its end with
both ends marked, and its message is shown once, after them, in a line
the form reserves so that a refusal shown in place moves nothing. Where
that line is apart from the complete gate's hint row (the Additional
information queue's Submitted dates, Financial stewardship's dates and
amounts), the gate's hint, in the same words,
is held hidden in its row while the message shows, so the form says it
once. A form whose complete gate already has a hint row (System logs, the
Follow-up packet, an Additional information item's Save) shows the message
in that row instead, over the gate's hint, which it hides while shown, so
the page is no taller for a refusal only a request built by hand gets and
says it once. The same holds for a report's own forms that a field can
fix (the Follow-up packet's limit, an Additional information item's
confirmation): the page is drawn again, 400, with the values sent. An
in-place Apply keeps the filter form and what was typed, and takes the
marks and message from the answer. The server's mark clears, with its
message and summary item, on the first edit of a field it marks (either
end of a range), or when an in-place Apply is answered without it.
**Shared code.** `web/filter_errors.py` (`FilterError`), the `filter_mark`
and `filter_line` template tags; used by Financial stewardship, the
Additional information queue and item, System logs, the Follow-up packet
and the Family directory's Exact Family code (shown in its gate's hint
row).

A file field marked `data-max-bytes` (with `data-max-bytes-message`, the
server's own words) is refused at the field as soon as a file over that
size is chosen, and the form is not sent until a smaller file is chosen; a
form marked `data-max-total-bytes` refuses files that together exceed it
the same way. The message shows where the server's errors for that field
go, in a reserved line that keeps its height once the error clears and is
announced to screen readers (`file-size-field.html`; Off-site backups'
folder link keeps the same line, so Test access refused there in place
moves nothing, #1334 review). A field that a
choice hides drops its refusal, and shows it again if it reappears with
the same file. The reverse proxy refuses an oversized request before the
page could explain it, so this check is what keeps such a refusal on the
form. First use: Parish logos (#1062).

An in-place form refused with a typed JSON answer shows the refusal as one
line after its buttons ([in-place controls](#in-place-controls)). When the
answer's errors name a field of that form (`FieldError` field identifiers),
that field is marked the same way: `aria-invalid="true"` and described by
the line. Like any server-only error, the mark and the line clear on the
field's first edit (`ui-v1.js` `showRefusal`; first used by Census changes'
Edit value,
[#528](https://github.com/epiphany40223/parishkit/issues/528)).

A mark clears as soon as its error does, without waiting for Save (#592). An
error the browser checks clears once the value is valid. One only the
server can check (an outcome that doesn't fit the request, for example)
clears from its field, with its message, on the first edit of that field,
since the server checks again on save; an error that marks two fields (a
date and a time) clears from both when either is edited. A field that a
choice hides or greys is not sent, so its marks clear too. Leaving a field without
changing it clears nothing. The summary loses the item for a field that
clears, and goes once it lists nothing. This applies to every Admin form,
Django-rendered ones included, through the shared page script.

Every Admin refusal of input is logged with its reason
([#1403](https://github.com/epiphany40223/parishkit/issues/1403)), whether
it is shown at a field, in a summary or on a refusal page. The server
records it through one shared hook, described under
[Admin input refusals](../operations/spec.md#admin-input-refusals), so a
page adds no logging call of its own. What a page must do is make its
refusal carry a closed reason code or error codes: refuse through the
shared refusal types and builders, give a new kind of refusal its own
reason code, and build forms on the shared Admin form base. A refusal never
logs the value it refused.

### Secret fields

**Rule.** Every single-line secret an Admin types (an API key or a bot
token) is masked by default and has the one shared Show/Hide control beside
it. **Why.** Keys are pasted, and an Admin needs to check what was pasted
without giving up the default protection against someone looking at the
screen or a shared call. **Shared code.** The `SecretInput` widget
(`accounts/secret_input.py`, template `stewardship/widgets/secret-input.html`)
and the `data-secret-reveal` handler in `ui-v1.js`;
`tests/stewardship/test_secret_input.py` fails on any other masked input in
the Admin portal. **Precedent.**
[#1475](https://github.com/epiphany40223/parishkit/issues/1475).

The control is a real button, drawn by the server next to the field so it is
in place on first paint. Tab reaches it, and Enter, Space or a click switch
the field between masked and plain text; focus stays on the button. Its
visible text reads Show or Hide, and its width is reserved for the longer
word, so pressing it moves nothing ([no layout shift](#no-layout-shift)).
Its accessible name is the visible word followed by the field's name, held
in the button as visually hidden text ("Show the ParishSoft API key", "Hide
the ParishSoft API key"), so the name always starts with the word on screen
(WCAG 2.5.3, Label in Name) and the field's label names only the field. It
has no `aria-label` and no `aria-pressed`, because the name already says the
state. A polite status line beside it announces each change ("Your
ParishSoft API key is shown."). It is always available, also while the field
is empty.

The control changes only what is shown, never what is submitted: the field's
name and value post exactly as when masked. It masks the field again by
itself when the form is submitted and when the reader leaves or returns to
the page (including a page restored from the back-forward cache). A saved
secret is never sent back to the browser, so the field starts empty, a
refused value is not drawn again, and the control shows only what was typed
in this page view. The page script never logs, stores or sends the value.
A multi-line secret (the Google service-account JSON key) stays a visible
text area.

### Review, Apply and Change status

**Rule.** A change to configuration or data is made by editing, then
**Review** (which shows what will change), then
**Apply** (or **Save**), after which the change's status is followed until it
is applied. Changes are saved by an explicit action, never
autosaved as the reader types or ticks. A checkbox may refresh a *view* on
change (a filter such as "Include ended sessions"), because that saves
nothing. **Why.** Review lets the reader see exactly what will change before
it changes, and the configuration-change path gives every change one audit
record, one signed preview and one concurrency check; autosave hides
mistakes and needs its own conflict handling. **Shared code.** The
configuration-change request path and Change status's passive read; the
[review dialog](#review-dialog), which every editor moves to under #750 (until
then, the separate Review and Change status pages); `data-review-of` in
`ui-v1.js` for the [in-place reviews](#in-place-controls) that remain;
`data-submit-on-change` for view filters only.
**Precedent.** #750 (review dialog, Administrator decision, 2026-10-11), #532
(in-place review, retired by #1429), #523
([preview lifetimes](../admin-portal/spec.md#preview-lifetimes)), #882 (New and
Edit pages follow it). **Exception.** A [wizard](#wizards) step that edits a
draft nobody can see yet saves directly, with no Review step (wizard rule 5;
built in the setup wizard, planned for the campaign wizard in #1404 PR 4).

A status that can no longer be read never looks as if it were still
waiting. When the follow-up's status read is refused (the sign-in ended, or
the change removed the reader's own access), Change status replaces its
running indicator with a plain final "Status unavailable" message
(`template[data-live-refused]`, `live-status-v1.js`), and a table's Delete
dialog says the change was saved but its status can't be checked (#952).

Review and Apply happen in the shared [review dialog](#review-dialog)
(Administrator decision, 2026-10-11), which says how every editor's Review
opens, applies, refuses and follows its change.

### Review dialog

**Rule.** Every Admin editor reviews and applies its change in one shared
modal dialog over the page: **Review changes** (or the action's own label,
such as **Create the campaign and continue**) opens the dialog with the
change and its impact (or, when that would only repeat the page, a
one-sentence confirmation), the dialog offers **Apply** and **Cancel**, and Apply's
progress and outcome show inside it. Once the change is applied the dialog
closes, the page refreshes in place (or a New or Edit page returns to its
table) and a short status line says what was applied. That button is
unavailable until the form holds a real change (for a new item, until every
required field is filled in), so no page ever says that
nothing has changed. **Why.** One way to review and apply across the portal,
instead of some editors using a second screen and others a panel under the
form; the reader stays on the page and never loses their place, and a dialog
keeps the form from changing while its review is shown. **Shared code.** The
[confirmation dialog](#row-actions-and-confirmation)
(`components/confirm-dialog.html` and `data-confirm-open` in `ui-v1.js`),
extended to show a server-drawn review; the change gate
(`data-require-change`) and `components/review-changes-button.html`
([prerequisite gates](#prerequisite-gates)); the configuration-change request
path and Change status's passive read (`live-status-v1.js`). The dialog is
`confirm-dialog.html` with `review=True`, naming where Apply posts, the page
a New or Edit page returns to (none: redraw in place) and the page's status
line, `components/review-outcome.html`. An editor's form names the dialog
with `data-review-open`; its Review answers a fragment marked
`data-review-answer` (with `data-review-done`, the start of the status
line), whose hidden fields Apply posts, the signed preview marked
`data-review-preview`. A field-value refusal answers the form again (status
400); any other refusal is the closed JSON refusal, or a fragment marked
`data-review-refusal` that the dialog shows as drawn. A page whose own
address records a view when read (System health) marks its dialog
`data-review-redraw="body"`: Apply's answer then holds the regions the
change redraws, and the page is not read again. **Precedent.**
Administrator decision, 2026-10-11, on
[#750](https://github.com/epiphany40223/parishkit/issues/750), which replaces
the in-place review panel of #532 and the separate Review and Change status
pages.

An editor is an Admin page whose form changes configuration or saved data
through Review and Apply: a settings page, a list editor, a New or Edit page,
an upload editor, or a table's bulk action that is reviewed before it is
applied. Each feature specification says what its own Review shows; this
section says how every Review behaves. The exception is a
[wizard](#wizards) step that edits a draft nobody can see yet: it saves
directly, without the dialog (rule 5; built in the setup wizard, planned for
the campaign wizard in #1404 PR 4).

- **Review is available only for a real change.** Review changes is
  unavailable until the form differs from the saved values, and unavailable
  again when the reader's edits return to them, with the gate's short hint
  after the button ([prerequisite gates](#prerequisite-gates)). A table's
  bulk Review action is likewise unavailable until the selection would change
  something, with its reason in the bulk bar's reserved line. No page draws a
  "nothing has changed" review, refusal, heading or page. The server still
  refuses an unchanged Review, as a safeguard for requests that bypass the
  page script.
- **Preparing the review.** Review changes posts the form without a page
  load, sent as a native submit would send it: multipart when the form's
  enctype is `multipart/form-data` (an upload editor's file), otherwise
  URL-encoded. A form's other submit button marked `data-review-skip`
  (Off-site backups' Test access) is no Review: it takes the form's other
  way. A form marked `data-review-native="<field>"` submits natively,
  without the dialog, while that field holds a value: an integration's
  pasted new key, whose slow install is followed on the page (a recorded
  exception, below). Until the answer arrives the form is inert and marked
  busy, and Review changes says it is working, so nothing can be edited
  while the review is being prepared.
- **Field errors stay on the form.** A Review refused for the values in the
  form (a required field, a value out of range) does not open the dialog: the
  form shows its error summary and marks each field, as the
  [field errors](#field-errors-and-live-checks) convention says, and becomes
  editable again. Where panes share one dialog and each pane has its own
  status line (see After Apply below), the refusal is said in that pane's
  line instead, which takes focus without scrolling, and the field is marked
  and described by it: no summary is placed above the heading, since moving
  focus there would jump the page to the top, and no error line is added
  that would move the pane's controls. The line clears on the form's next
  edit or accepted Review (#1410).
- **Opening the dialog.** An accepted Review opens the dialog. Its title
  names the change ("Review campaign settings") and labels the dialog
  (`aria-labelledby`); focus moves to that title, never to Apply, so the
  reader hears what is being reviewed before reaching the buttons. Its body
  shows the review the feature specification describes: a full review (the
  changed values with their current and proposed values, and the change's
  notes and impact: affected Families, counts, a generated preview) or a
  confirmation (below).
- **A confirmation when Review would only repeat the page.** When a Review
  would show nothing the reader can't already see on the page (a new item,
  where nothing existed before; a change of one or two fields, where the new
  values are the form itself; or a fixed explanation), the dialog does not
  list the values again. It states the change's effect in one sentence
  ("This creates the campaign as a draft in Testing mode; no email is
  sent." / "Parish telephone changes from (212) 555-1234 to
  (212) 555-9876."), adds only what the server worked out that the page
  doesn't show (a warning, such as granting Administrator; an affected
  count; a refusal), and offers **Apply** and **Cancel**; the sentence
  describes the dialog (`aria-describedby`), and its title asks the
  question ("Create the campaign?"). The form's button then carries the
  action's own label ("Create the campaign and continue", "Save") rather than **Review
  changes**, since it opens a dialog that does not review, and the dialog's
  button stays **Apply**. Review still posts the form, so field errors stay
  on the form and the server still validates and signs the preview, which
  Apply posts as before: the signed preview and its lifetime, concurrency,
  the rechecks under the work lock, the audit and the command line's
  `preview` and `confirm` are unchanged. A Review that changes many fields,
  computes impact (affected Families, re-planned schedules, counts), or
  renders what Families will see (an email sample, staged images) keeps its
  full review. Each feature specification says which kind its Review is.
  An editor whose Review is a confirmation for some changes and a full
  review for others (a one-time scheduled email against a repeat; leaders
  ticked on the page against Import all) answers with whichever the change
  needs, and the answer sets the dialog's question and description; its
  form's button carries the action's own label ("Add the scheduled email",
  "Import selected") either way.
  **Precedent.** Administrator decision, 2026-10-14, on
  [#1356](https://github.com/epiphany40223/parishkit/issues/1356); the
  [Delete dialogs](#row-actions-and-confirmation) already confirm over a
  hidden server-signed preview.
- **A compact review when Review adds only the old value.** When a Review
  changes one or a few short fields of something that already exists, the
  one thing it shows that the page no longer does is each field's old value.
  The dialog is then a confirmation as above (its title asks the question,
  the form's button carries the action's own label, such as **Save**), but
  instead of one sentence it lists one line per changed field, "Field: old
  → new" ("Role: Staff → Ministry leader"), with no Now and After table.
  The lines describe the dialog (`aria-describedby`); the arrow is hidden
  from screen readers, which hear "old, becomes new". A change to an
  ordered list marks only what changed, one line each (an option added,
  removed, relabelled or moved), never both whole lists. The dialog adds
  only what the server worked out (a warning, such as granting or removing
  Administrator), and everything else is as for a confirmation: field
  errors stay on the form, and the signed preview, Apply and the command
  line are unchanged. A change of many fields, or of a long value such as
  a list of Ministries, keeps its full review under the same button. One
  shared partial draws the lines
  (`components/compact-review.html`). **Precedent.** The default proposed
  on [#1356](https://github.com/epiphany40223/parishkit/issues/1356) for
  its class-A2 Reviews, which the Administrator can override.
- **While the dialog is open** it is a native modal `<dialog>`: the rest of
  the page, the form included, is inert and cannot be edited, and Tab stays
  inside the dialog. So the review shown is always the review of the form as
  it stands, and no edit ever has to withdraw it. Escape or Cancel closes the
  dialog, changes nothing, and returns focus to Review changes.
- **Apply** posts the dialog's signed preview without a page load, as the
  [Delete dialog](#row-actions-and-confirmation) does: from then on the
  dialog cannot be dismissed (no Escape, no Cancel) until the answer
  arrives. Once the change is recorded Apply is hidden, and the dialog's
  reserved status line below the buttons shows its progress (Applying…, then
  Applied), polled from Change status's passive read, which never renews
  idle time. Once the change is applied the dialog closes.
- **After Apply.** On a page that stays (a settings page, a list editor, a
  table's bulk action), every region of the page is redrawn in place from
  the page's own address at the version the change made, with no reload and
  no jump to the top. That includes the region holding the form that opened
  the dialog even when it is marked `data-in-place-own` (so other in-place
  controls on the page never redraw it): the Apply is that form's own
  answer. A New or Edit page (New user, New scheduled email)
  returns instead to the table page it was opened from (Users, Dates and
  mail schedules), as [flows end where the reader
  started](#navigation-rules) says. A Review that the
  server answers with another page (Campaign settings' dates-only change,
  which leads to Dates and mail schedules) ends on that page. A page whose
  own address could only be refused once the change is applied ([Create the
  campaign](../admin-portal/spec.md#create-the-campaign), once the campaign
  exists) is redrawn from the address Apply's answer ended on, the page
  naming the change, which becomes the page's address. Wherever the
  reader ends, a short status line under the form or above the table says
  what was applied and when ("Applied at 3:42 PM."), and focus moves to it.
  The line is a polite live region (`role="status"`), so a screen reader
  hears it, and its space is reserved, so it never moves a control ([no
  layout shift](#no-layout-shift)). It sits
  [next to the control that caused it](#in-place-controls): where panes
  share one dialog, each pane has its own line, which the pane's forms name
  with `data-review-outcome` in place of the dialog's line, so the page
  never scrolls to a line elsewhere.
- **Refusals stay in the dialog.** A Review or Apply refused because the
  settings changed elsewhere (another tab or another Administrator), an
  out-of-date or expired preview, a change that failed to apply or was
  refused when it ran (`stale_base`), a lock held by background work, or any
  other refusal that is not about a field's value is shown inside the
  dialog, below its buttons so they never move, with the server's
  explanation and fix (such as reloading to start from the current
  settings); the dialog can then be closed. The page behind is not redrawn,
  and the form keeps the reader's values at the version they came from,
  never the current one, so a stale page can never quietly propose undoing a
  change made elsewhere. A refusal that needs a fresh sign-in offers
  **Confirm with Google** and its step-up page, as it does today. A
  refusal the server draws itself (a fragment marked `data-review-refusal`,
  such as the shared step-up notice with its **Confirm with Google**) is
  shown in the dialog as drawn.
- **Uploads.** In an upload editor (Campaign images, Parish logos), choosing
  the file and selecting Review uploads it as the first step of Review: the
  server stages it and the dialog shows its preview (each generated size, the
  image as Families will see it). Cancel leaves the staged file to expire
  unused. A refused upload (wrong type, too large) is a field error, shown on
  the form.
- **Long reviews scroll inside the dialog.** The dialog fits the window at
  every width the portal supports, phones included
  ([devices and browsers](#devices-and-browsers)); its body scrolls while
  its title and its Apply and Cancel buttons stay in view.
- **Unchanged guarantees.** The signed preview and its
  [lifetime](../admin-portal/spec.md#preview-lifetimes), optimistic
  concurrency, the capability and session rechecks under the work lock, the
  durable configuration-change request and its audit are the same whichever
  way the review is shown.

Recorded exceptions (Administrator decision, 2026-10-11):

- **The setup wizard** keeps its stepwise Next and Back, with its own review
  step ([Bootstrap and first-Admin
  wizard](../admin-portal/spec.md#bootstrap-and-first-admin-wizard)).
- **Integrations' slow key install** shows its status inside the
  Integrations page while the key is installed, not in a dialog or on a
  second page ([#456](https://github.com/epiphany40223/parishkit/issues/456)):
  Save with a new key pasted submits natively (`data-review-native`).
  Integrations' other changes use the dialog.

Operational actions that are not editors (going live, Confirm Production,
cancelling go-live, pausing and resuming mail, fixing many emails at once,
test email sends, a manual ParishSoft refresh, Family portal maintenance) keep
the flows their own sections describe; whether any of them moves to the
dialog is decided on [#750](https://github.com/epiphany40223/parishkit/issues/750).
System health's Take a backup now, Turn off debug logging and Allow debug
logging again confirm in the dialog
([#1356](https://github.com/epiphany40223/parishkit/issues/1356)), since
their previews would only repeat the page; Accept this change once keeps its
in-place preview of the counts.

Current state, to be converted: Users' New user, Edit user and Import
Ministry leaders, New and Edit scheduled email,
[Campaign settings](../admin-portal/spec.md#campaign-settings-page) (#1119),
Share options, Campaign images, Parish logos, Parish settings, Create the
campaign and No-reminders WorkGroup use the dialog; Create the campaign, New
user and No-reminders WorkGroup are confirmations under #1356, and so are a
one-time New scheduled email, Import selected with nobody skipped and
Campaign images' Remove this image. Edit user, Share options, a small
Campaign settings change and Parish settings are compact reviews under the
same issue. The
[integration settings pages](../admin-portal/spec.md#parish-and-integration-configuration)
use the dialog too (#750): an ordinary settings edit is a compact review, a
refresh schedule change keeps its full review, and setting up Off-site
backups by its folder, removing Slack and turning off off-site copies are
confirmations; the Backup encryption key change still uses its separate
Review page. The in-place review panel of #532 is retired
([#1429](https://github.com/epiphany40223/parishkit/issues/1429),
[in-place controls](#in-place-controls)); the other editors use separate
Review and Change status pages.
[#750](https://github.com/epiphany40223/parishkit/issues/750) tracks the
conversion.

### Conditional fields

**Rule.** A field is shown only when it is relevant to the choices already
made, and is required only while shown. A hidden or greyed field is not
sent, and its errors clear. **Why.** Fields that do not apply invite wrong answers and make
a form look longer than it is. **Shared code.** `ui-v1.js`: `data-show-when`,
`data-required-when`, `data-required-when-shown`; re-applied on `pageshow`
and after every in-place swap. A rule may name a radio group as well as a
single control. Where hiding a field would move the controls
around it, two gates keep it in place instead:

- `data-enabled-when="name=value"` (#959) works like `data-show-when`
  but never hides: while the rule does not hold, the field stays, disabled
  (greyed and not sent). It is re-checked on `pageshow` and after in-place
  filter syncs. First use: System logs' level boxes while Sign-in activity is
  chosen.
- A rule's value may list alternatives separated by `|` (#953):
  `activity=|family_mail|parishsoft` holds while the control is empty or
  either value. A negated rule `name!=a|b` means "none of a or b": it holds
  while the control has neither value. It applies to `data-show-when`,
  `data-enabled-when` and `data-required-when` alike. First use: System
  logs' level boxes and Audit record across the
  [activity groups](../admin-portal/spec.md#activity-groups).
  `data-locked-when` (#957) must match its rule the same way (`anyValue` in
  `ui-v1.js`) when it lands.
- Every gate compares a value the same way (`sameValue` in `ui-v1.js`): a
  number field's value is read as a number, as the server reads it, so
  `04321` matches a rule's `4321` and an empty number field matches no
  number; any other control compares its value as text. Until #953 the
  show and enabled gates compared a number field as text, which no rule in
  use then depended on.
- `data-locked-when` (open #957) shows a fixed value, disabled, while its
  rule holds, with the reason in the field's toggletip (never a line under
  the field), and gives back the reader's own value (kept in a hidden field)
  when the rule stops holding. First use:
  the directory's "Campaign mail can reach" while Include mailing columns is
  ticked.

The two overlap, and are to be merged into one shared gate once both have
landed. The Ministry follow-up request form greys its outcome and contact
details with `data-enabled-when` (#1014); as with #959, a greyed field keeps
what it shows, unsent, and its tip says when it is available and that a
greyed value is not saved. **Precedent.** #563, #736,
open #957, #959, #1014.

Recorded exception: the hidden Advanced panel of an editor with
[Basic and Advanced modes](#basic-and-advanced-modes) is still sent, with
the rules that section gives (#1121).

A Share options row ticked Delete greys its label, free-text box and order
in place (`data-enabled-when` on the row's own Delete box), so the rows below
never move; a redrawn form shows a deleted row's saved values, still greyed,
and unticking Delete gives them back (#563).

### Binary choices

**Rule.** A single choice between exactly two mutually exclusive ways an
Admin page behaves or what it shows (two views of the same page) is a
**segmented pair**: a short visible label and two equal segments, the
chosen one filled. Choosing a segment applies it at once, in place; there
is no Apply button. It is never a two-option select, a pair of radio
buttons with an Apply button, a checkbox or a toggle switch. **Why.** One
choice should take one action, and every such choice should look and work
the same, so a reader learns it once; a switch reads as on and off, while
these are two named views. **Shared code.**
`components/segmented-choice.html` (the pair, its label and its two links)
and the shared segmented style in `ui-v1.css`, which the
[Basic and Advanced modes](#basic-and-advanced-modes) toggle uses too;
`test_segmented_choice.py` (the template and the guard below) and the
browser tests of each page that draws one. **Precedent.**
[#1535](https://github.com/epiphany40223/parishkit/issues/1535)
(Administrator, 2026-10-17), from the Response dashboard's Production |
Testing rehearsal.

- **Where.** The Response dashboard's Responses shown (Production |
  Testing rehearsal) and Chart population (Historical as of day | Current
  population), and the Family timeline's Records shown (Production |
  Testing rehearsal). The family directory's Responses shown and the roster
  changes' Show are still menus in their filter forms; their conversion is
  tracked in #1535.
- **Links.** Each segment is a link to the page's own address with that
  choice (`a[data-in-place]`, marked `aria-current="page"` when chosen), so
  each view has a real address that a reload or bookmark keeps. The link
  carries the page's other view state (a table's sort and page size, the
  browser's time zone), and the pair is redrawn with every in-place answer
  (`data-table-sync` or inside a region), so its links always match the
  page shown.
- **In place.** Choosing a segment refreshes the page's regions without a
  reload or a scroll jump ([in-place controls](#in-place-controls)); the
  address is replaced, focus stays on the segment just chosen, and the view
  now shown is announced by its segment's text ("Current population").
- **Look.** Both segments keep one size and weight and the pair keeps its
  place, so choosing one changes only colours
  ([no layout shift](#no-layout-shift)). The chosen segment is filled in the
  accent colour; the focus ring sits inside the segment, drawn in the paper
  colour on the chosen one, as on Basic and Advanced.
- **Accessibility.** The pair is a group (`role="group"`) named by its
  visible label; Tab reaches each segment and Enter follows it, and a
  screen reader names the chosen one as the current page.
- **Not this pattern.** A binary filter among several applied together
  (the Census changes' Include history box stays in its form), tri-state
  filters (Any, Yes or No), export options sent with an action ("Include
  mailing columns" for an export), stored settings saved through Review and
  Apply (a checkbox there is a setting, not a view), and choices of three
  or more (Appearance).
- **Guard.** A template test refuses an Admin template's `<select>` with
  exactly two written options, unless it is listed as an exception with
  its reason. A menu built from a loop is checked in review.

### Basic and Advanced modes

**Rule.** A settings editor whose full form is much harder than what most
parishes need offers a page-level **Basic** / **Advanced** toggle. Basic
offers a few plain choices that cover most parishes; Advanced offers the full
configuration. The two modes edit one configuration: Basic → Advanced is
always lossless, and Advanced → Basic never silently drops a configuration
Basic cannot show. **Why.** Progressive disclosure: most Administrators
need only the common case, and the full editor every time makes a simple
setting look hard. One shared toggle means a reader learns it once.
**Shared code.** `components/editor-mode.html` (the toggle and its dialog,
through `components/confirm-dialog.html`) and `ParishEditorMode` in
`ui-v1.js`, with an adapter each editor registers for its own mapping
(`toAdvanced`, `toBasic`, optional `clearBasic`); `data-not-a-change` keeps
the toggle and the Basic panel out of the change gate; `test_editor_mode.py`
tests it on a component page. Each editor registers its adapter by the name
in `data-editor-modes="<name>"`, so it still applies after an in-place
answer redraws the editor. **Precedent.** #1121 (Administrator, 2026-10-12); first
adopters #1122 (the
[ParishSoft refresh schedule](../admin-portal/spec.md#parishsoft-refresh-schedule-settings))
and #1062 (Parish logos), then the scheduling editors of #1129.

- **Where and how it looks.** The toggle sits at the top of the editor,
  above both modes, so switching never moves it. It is a segmented pair,
  drawn as every [binary choice](#binary-choices) is but with radios and
  this section's own behaviour; it has a short visible label ("Mode") and
  exactly two segments, **Basic**
  and **Advanced**, never "Simple" (Administrator decision, 2026-10-12). It is
  never a checkbox, a link to another page or a second settings page. The
  chosen segment is filled; both segments keep one size and weight, so
  choosing one changes only colours. Settings that make sense in both
  modes (a single switch such as "Skip refreshes around Family emails")
  stay outside the two modes, below the toggle.
- **In place.** Switching redraws the editor in place, with no reload and no
  scroll jump ([in-place controls](#in-place-controls)). Only what is below
  the toggle changes; nothing above it moves
  ([no layout shift](#no-layout-shift)).
- **Basic** offers a few presets or one simple input, each described in
  plain words ("Nightly at 2:00 AM", "Every 4 hours", "One image, resized
  for every size"). One Basic choice must be chosen before Review: while
  none is, Review is unavailable with the panel's reason, such as "Choose a
  basic schedule to review it." ([prerequisite gates](#prerequisite-gates)).
- **Advanced** offers the full configuration: every rule, exception and
  per-item value, and "Edit as text" where the editor has it.
- **Mapping.** Every Basic choice maps to exactly one Advanced
  configuration, written by the editor's own code. **Basic → Advanced is
  always lossless:** Advanced shows the configuration of the chosen Basic
  choice, ready to refine (and, when no Basic choice has been chosen since
  switching, the configuration Advanced already held). Advanced may have no
  Basic equivalent: when the Advanced configuration equals a Basic choice,
  Basic opens with that choice chosen.
- **Advanced → Basic with no Basic equivalent** first opens a modal dialog,
  the shared confirmation dialog of [row actions](#row-actions-and-confirmation).
  Its title names the setting: "Your custom schedule has no basic
  equivalent." for a schedule (Administrator decision, 2026-10-12), "Your
  custom logos have no basic equivalent." for Parish logos, and so on, as
  "Your custom *setting* has (or have) no basic equivalent." Its body says
  that Basic cannot show the custom setup, that switching leaves no basic
  choice chosen, and that nothing changes until a basic choice is reviewed
  and applied. Its buttons are **Keep Advanced** (the safe choice, which
  takes focus; it closes the dialog and changes nothing, as do Escape and
  closing the dialog) and **Switch to Basic** (a primary button, not a
  destructive one: it switches with no Basic choice chosen, so Review stays
  unavailable until one is). Until a change is applied, the saved
  configuration is untouched: reloading the page brings it back.
- **Which mode opens.** Basic when nothing has been set yet; otherwise the
  mode last used to save, stored with the setting (for example
  `editor_mode: basic|advanced`, a key of the setting itself, not a database
  column); and Advanced whenever the stored configuration has no Basic
  equivalent, whatever mode was last saved. The page is drawn by the server
  in that mode, so it never flashes the other one. The mode is never
  remembered per viewer in browser storage: it belongs to the setting, so
  every Administrator sees the same view.
- **Review and Apply.** The mode is presentation only. The server validates
  and stores only the full (Advanced) configuration, so the two modes cannot
  disagree: the Advanced fields always hold the configuration, rewritten
  from the Basic choice on every Basic edit, or, where a browser cannot copy
  a value (a chosen file), the server expands the Basic choice with the same
  mapping before validating it. The mode is sent with the save, but
  switching modes alone is not a change: Review stays unavailable until the
  configuration differs from the saved one
  ([Review, Apply and Change status](#review-apply-and-change-status)). The
  review shows the resulting configuration in plain words, whichever mode
  produced it, in the shared [review dialog](#review-dialog). A Basic
  panel's controls are not sent while Advanced is shown.
- **The hidden Advanced panel is still sent.** This is the recorded
  exception to [conditional fields](#conditional-fields): while Basic is
  shown, the Advanced panel is hidden but never disabled, because its
  fields carry the configuration the server validates. Two rules keep a
  hidden field from hiding a problem:
  - **Basic matches only a valid configuration.** Advanced → Basic shows a
    matching Basic choice only while every Advanced field is valid (none
    unreadable, refused by a live check or marked by the server); otherwise
    it counts as having no Basic equivalent and asks, as above. A Basic
    choice rewrites the Advanced fields, and clears the errors marked on
    the values it replaced.
  - **Errors open Advanced.** A server answer with an error on an Advanced
    field (a Review refused for its values, which
    [stays on the form](#review-dialog)) is drawn in Advanced mode, where
    the field and its message can be seen, whatever mode was sent; the page
    script does the same for a page drawn otherwise or redrawn in place. An
    editor whose Basic panel could hold such an error instead shows the
    error summary in its Basic panel, naming the setting to change.
- **Accessibility.** The toggle is a labelled radio group (a `fieldset` and
  `legend` around two native radios), so arrow keys move between the modes
  and a screen reader names the group and the chosen mode. Switching
  announces the new mode in the shared live region ("Basic mode: the common
  choices are shown."). Focus stays on the toggle after a switch; after the
  dialog it returns to the toggle's chosen segment. The focus ring is drawn
  on the chosen or focused segment and keeps the
  [theme's contrast](#accessibility-and-client-behavior).

A setting that can't change in the page's present state (rather than one
that does not apply to the choices made) stays where it is, disabled, with
the reason in its tip, never a line under it or a line for its section;
the page shows each setting once, in the same place whatever can change.
First use: [Campaign settings](../admin-portal/spec.md#campaign-settings-page)
for a live or locked campaign (#1119).

### Single-campaign interim

**Rule.** Copy campaign, the one remaining multi-campaign-only control, is
greyed out with one shared tip until #145 removes it; no report page shows a
campaign chooser or a line that only names the campaign. **Why.** The system
becomes single-campaign after this campaign. Copy campaign stays until #145
removes the clone code behind it, so a half-removed flow is never reachable;
a chooser or a campaign-name line has no flow behind it and tells the reader
nothing when there is one campaign, so the report pages dropped them during
the campaign (Administrator, 2026-10-12, #1125). **Shared code.** The `multi_campaign_control`
template tag (`accounts/templatetags/stewardship.py`) and its tip
`campaigns.single_campaign.TIP`. **Precedent.** #145, #1125.

The Admin portal serves one current campaign. The system moves to a single
campaign after this campaign (#145), so navigation already assumes it: there
is no campaign chooser and no New campaign control (the one campaign is
created by [Create the campaign](../admin-portal/spec.md#create-the-campaign), offered only while the
deployment has never had a campaign), and no Admin URL names a campaign. The
"Choose a retained campaign" links that Participation and Ministry requests
once showed greyed out are removed, and report pages do not open with the
campaign's name (#1125). **Copy campaign** on Campaign settings, the one
remaining control whose only purpose is working with more than one campaign,
is shown greyed out until #145 removes it: an unavailable control, not a link
or action, with the tip "Disabled; will be removed with the single-campaign
change (#145)", shown and announced the same way as an unavailable menu
entry's reason. The server refuses the matching actions too, so a greyed
control cannot be bypassed
([navigation rule 10](#navigation-rules), [decisions 18 and
19](../admin-portal/spec.md#navigation-decisions)).

### Addresses and legacy URLs

**Rule.** Admin addresses follow the
[URL scheme](../admin-portal/spec.md#url-scheme); an old Admin address is not
kept, aliased or redirected. Family-facing addresses (emailed codes and links,
the Family portal and its sign-in) never break. **Why.** Aliases double every
route to test and secure; Families cannot be asked to find a new link.
**Shared code.** `admin_urls/` (one module per menu group);
`test_admin_url_scheme.py`. **Precedent.** #525, #864.

- **Old URLs are not kept:** the Administrator dropped the old Admin
  addresses ([#864](https://github.com/epiphany40223/parishkit/issues/864)),
  so each answers 404: the addresses from before the scheme, those that named
  a campaign, the retired campaign choosers and the daily and weekly report
  links in report emails sent before NAV-12. A page for one record (an export, a digest
  snapshot, a cleanup request) refuses a record whose campaign is not current
  ("This campaign is no longer the current campaign", 410 Gone) until the
  single-campaign change (#145).
- **No campaign in report URLs:** no Admin address names a campaign, report
  pages included; the reports specification's former allowance for a
  campaign UUID in report URLs is removed (Administrator decision,
  2026-10-10, [#956](https://github.com/epiphany40223/parishkit/issues/956)).

### Not-found pages

**Rule.** An unknown Admin or Family address gets a styled page in the site
layout ("Page not found") with a next step and a way back, never the plain
text of the security middleware; nothing from the address is shown. Scripts
get the closed JSON error. **Why.** A bare "Not Found" in a fixed-width font
looks like a broken site. **Shared code.** `web/error_pages.py`
(`BrowserErrorMiddleware`, and `not_found_response` (#930)) and
`error.html`. **Precedent.** #927, #930. The general error-page rule is under
[accessibility and client behavior](#accessibility-and-client-behavior).

### Unavailable menu pages

**Rule.** A page opened while its menu entry is greyed out (from a bookmark,
an old tab or a typed address) says why, in the menu entry's own words, and
offers a way forward; never a generic error such as "This information
changed". Its heading names the page ("Share options isn't available right
now"), its message is the entry's reason, and it links the entry that
changes that reason when the viewer's menu offers it (Campaign settings for a missing module, Go-live
readiness while Production is not confirmed or in Testing mode, Production
activation once the campaign has gone live, Refresh from ParishSoft before
the first ParishSoft download), besides the usual way back and
Home. The answer is HTTP 409 Conflict (the state does not allow it now,
never 404 or 403), and scripts get the closed `not_available` code (not
`unavailable`, which means an outage to retry) with the same refusal. **Why.** Nothing changed and reloading does not help; the
menu already knows why, so the page must not drift from it. **Shared
code.** `admin_navigation.py` (each reason a named string, `FORWARD`,
`greyed_entry`, `Greyed.refusal`), `admin_context.greyed_page` (run by the
access gate), `web/refusals.py` (`UserFacingUnavailable`,
`unavailable_response`); `test_admin_navigation.py` requires every entry
with a reason check to be covered, and `test_greyed_pages_postgresql.py`
opens one page per kind of reason. **Precedent.** #1128.

- **One check, for every greyable entry.** The access gate runs it once,
  before the view, for a GET or HEAD of any menu page whose reason check
  returns a reason; a new entry with a reason check is covered without
  further code. A POST keeps its page's own refusal; the Share options editor
  refuses a form sent after the campaign went live with the same reason;
  for an archived campaign, Pages and emails refuses its editors, revisions
  and Delete (#1255), and Dates and mail schedules every page under it (New,
  Edit, the calendar, date changes, Delete and their confirmations, #1258),
  with the same reason, by any method (`admin_navigation.refuse_greyed`).
- **Only for a viewer who sees the entry greyed.** The role still hides an
  entry ([stable menu shape](../admin-portal/spec.md#stable-menu-shape)), so
  a page the viewer's role may not open is refused by its own view (403) as
  before, without the reason; a signed-out visitor, or a session the page
  would rotate or end, also goes to the view. The session is checked
  read-only, renewing no idle time.
- **Server checks stay.** The page shows only the menu's words and grants
  nothing; every view keeps its own checks, which still refuse anything the
  gate lets through.
- **Links grey out with their pages.** A page's own link to a menu page the
  viewer's menu greys out is the shared greyed-out control with the same
  reason, never a link to the reason page; a page outside the menu that
  refuses for a menu reason (Families the form cannot open before the first
  ParishSoft download) is linked and refused the same way. **Shared code.**
  the `menu_link` template tag (`components/menu-link.html`),
  `admin_navigation.PAGE_REASONS`, `admin_context.page_unavailable`.
  **Precedent.** #1242.

### Navigation rules

**Rule.** Every Admin page follows the numbered rules below: it is
reachable, has a way back, has one name, and ends a flow where the reader
started. **Why.** One registry and one set of rules keep the menu, the
trails and the page names from drifting. **Shared code.** The registry in
`accounts/admin_navigation.py`; `test_admin_navigation.py`,
`test_admin_page_names.py`, `test_admin_ways_back.py`,
`test_admin_message_links.py` and `test_admin_reachability_postgresql.py`.
**Precedent.** #520, #521, #522. Where each page sits (its menu group,
parent, name and URL) is feature placement and stays in the Admin portal's
[Admin navigation](../admin-portal/spec.md#admin-navigation).

These rules are requirements; a navigation test checks each one against the
registry and the rendered pages.

1. **Reachable.** Every registered page is a menu entry or names a parent in
   the registry, and following parents always ends at a menu entry or Home.
   Every child page is linked from its parent page in at least one state the
   test renders, or is the result of a form on its parent that the registry
   lists. No page is reachable only from an email, an error page or another
   group's page.
2. **Every report has a menu entry**, and every page listed under a report
   entry is reached from that report.
3. **A way back.** Every page except Home, the sign-in, the setup wizard and
   the pages the access gate shows instead of a page (maintenance, not
   configured yet, and error pages) shows a breadcrumb whose parent crumb is a
   link the viewer may open, or a "Return to" link when the parent cannot be
   linked. An error page offers a way back to the page the reader came from,
   or to Home; the Restore review, maintenance and not-configured pages
   offer sign-out. Templates do not hand-write back links that
   repeat the trail.
4. **One name.** A menu entry's page heading equals its menu label; a child
   page's heading equals its breadcrumb label; the browser title starts with
   the same name. The only exceptions are the object-named pages in the Admin portal's
   [page names and placement](../admin-portal/spec.md#page-names-and-placement)
   table.
   Every "Return to" link names its target by that target's name. The
   shared error page cannot know which page the reader came from, so its
   link says "Return to the previous page"; the export error page reads
   nothing from the database, so its link says "Return to the export status
   page".
5. **Flows end where the reader started.** After an action, the reader lands
   on the page whose task started it: a settings change ends on the settings
   page, and a New or Edit page's change on the table it was opened from
   (Users, Dates and mail schedules), with the
   [review dialog](#review-dialog)'s short status line there (until #750
   converts a page, its separate status page returns there); Preview and test email returns to the page it was
   opened from, remembered in the signed-in session as a change's origin is
   (never in the URL); a manual ParishSoft refresh stays on Refresh from
   ParishSoft and shows its progress there; sending a weekly report now ends
   on Emailed reports with a link to the report; Cancel returns to the page it
   was pressed on, never Home unless the flow started there. In a
   [wizard](#wizards), a completed step opens the next step not done yet
   instead (rule 8; in the campaign wizard built for Continue, and planned
   for its saves in #1404 PR 4).
6. **Messages link what they name.** A message that tells the reader to use
   another page links that page when the viewer may open it. A page is linked
   only when the viewer may open it now: an in-page link to a page the menu
   greys out is greyed out the same way, with the menu's reason (#1242), and
   the navigation test fails on any followed link that answers an error,
   apart from a short documented allow-list of fixture limits that may only
   shrink.
7. **Stable shape.** For each role, the menu's groups and entries are the same
   across modes and campaign states (setup pending excepted); an unavailable
   entry is greyed out with its reason, never removed, and stays reachable by
   keyboard with its reason announced.
8. **One home per concept.** A page appears under one menu entry only;
   another page may link it, but its breadcrumb always runs through its home
   (Content history runs through Campaign settings).
9. **Every route classified.** Every Admin route is either a registered page
   or listed as a non-page (form actions, downloads, images, status
   fragments, sign-in and the setup wizard), and a test requires every new
   route to be classified. A route that answers only POST or only JSON is a
   non-page, not a page.
10. **Multi-campaign controls are greyed out.** Every reachable or visible
    control whose only purpose is multi-campaign work is an unavailable
    control with the tip "Disabled; will be removed with the single-campaign
    change (#145)", reachable by keyboard and announced as its description.
    The controls are: Copy campaign on Campaign settings, and
    the "create the successor draft" choice in the checklist after [Return to
    Testing](../admin-portal/spec.md#return-to-testing-after-archive), which does not exist yet
    (#527), so until it does the server's refusal alone covers it. The server
    refuses the matching actions: cloning, creating a successor draft, and
    choosing a campaign other than the current one for a report. The retired
    chooser addresses are gone (404, #864). New campaign is removed outright ([decision
    11](../admin-portal/spec.md#navigation-decisions)), not greyed, and so are the "Choose a retained
    campaign" links once greyed on Participation and Ministry requests (#1125).

### Wizards

**Rule.** An Admin wizard is an ordered set of step pages that always says
where the reader is and what comes next, has one way forward on every step,
lets a step be kept as is, saves a draft directly, and never asks a question
twice. The numbered rules below apply to the
[setup wizard](../admin-portal/spec.md#bootstrap-and-first-admin-wizard), the
[campaign wizard](../admin-portal/spec.md#create-the-campaign) and any later
Admin wizard. **Why.** The Administrator found the campaign flow "very
un-intuitive" (2026-10-15, on
[#1404](https://github.com/epiphany40223/parishkit/issues/1404)): Create the
campaign and its first step asked the same questions, a step whose defaults
were acceptable had only an unavailable Save, defaults never completed a step,
every save of an unseen draft opened a review dialog, and an editor opened from
a step dropped the wizard. The setup wizard and the Family portal's steps did
not have those faults, so one set of rules makes every wizard behave like
them. **Shared code.** The step model (`setup_wizard.Step` and `Wizard`, which
`campaign_wizard.py` reuses), the stepper (`stepper-steps.html`, see
[step bars](#step-bars)) and the action rows (`setup-navigation.html`,
`campaign-step-actions.html`, which #1404 makes one shared row).
**Precedent.** #1404 (Administrator, 2026-10-15), #142 (the setup and
campaign split), #1322 (the campaign stepper), #1402 (the shared stepper).
The Family portal keeps its own [step rules](#family-portal), which already
match.

1. **Where you are and what's next.** Every step page starts with the
   stepper: "Step N of M: *label*", "K of M steps completed", the
   [step bar](#step-bars) and "Next: *label*" as text, naming the next step
   that is not done yet, with the full step list as
   [step bars](#step-bars) describes it.
2. **One primary action per step**, at the bottom of the page in one action
   row: **Back** (secondary, first) and one primary control (last). A step
   never offers only an unavailable Save. Going on is never only in the
   stepper's summary or its list.
3. **Accept as is.** While the step's form holds no change, the primary
   control reads **Continue to *next step***: it records the step as
   reviewed and opens the next step. Once the form holds a change, the same
   control reads **Save and continue**. The control's width is reserved for
   the longer label, so the switch moves nothing
   ([no layout shift](#no-layout-shift)). A list step with no form of its own
   always offers Continue to *next step*; its items are edited on its
   sub-pages (rule 10).
4. **Save and continue saves and moves on.** It validates; on errors it stays
   on the page, with the errors at the fields and in the summary
   ([field errors](#field-errors-and-live-checks)). Otherwise it saves,
   follows the change's status in place ("Saving…", then the result) and
   opens the next step once the change is applied. A refusal after
   validation, such as a stale page or held work, stays on the page with its
   reason.
5. **No modal for reversible draft edits.** While what the wizard edits is
   not yet live (the setup draft, or a campaign that is a Testing draft with
   no go-live started), Save and continue saves directly, without the
   [review dialog](#review-dialog). The review dialog, or its
   confirmation, stays where a change reaches people or cannot be undone:
   creating the campaign (one campaign only, in the
   [single-campaign interim](#single-campaign-interim)) and removing a file
   (confirmations), going live, Confirm Production, and every change to a
   scheduled, live or closed campaign. A direct save keeps every server
   check of a confirmed Review except the signed preview, and records the
   same audited configuration-change request; a request key drawn with the
   page makes a repeated submit return the same receipt.
6. **Back never saves.** Back is a link to the previous step. When the form
   holds unsaved changes, Back first asks in the shared
   [confirmation dialog](#row-actions-and-confirmation) "Discard your changes
   and go back?", with **Discard and go back** and **Stay**. Following a
   step in the bar or the list asks the same.
7. **Completed means saved or reviewed.** A step is completed once its own
   save has been applied or its Continue has been pressed (reviewed and kept
   as is), or, on a step whose purpose is a check, once that check passes (a
   load finished, a test accepted). The completion is stored, so a reload,
   another browser or another Administrator sees the same. A step completed
   by a save or by Continue stays completed: a later change elsewhere never
   undoes it. A step whose purpose is a check (a final Review, a parish-data
   load, a test email) is completed only while its check holds for the
   current state, so it is required again when what it checked changes (a
   later edit to the draft, a changed credential, a new revision of the
   tested email). Defaults that another step or the creation wrote never
   complete a step by themselves; pressing Continue on them does.
8. **Never land on a completed step.** Once a step completes, the wizard
   opens the next step that is not done yet (the next step when every later
   one is done), and after the last step that step's own outcome. This is the
   wizard's exception to "flows end where the reader started"
   ([navigation rule 5](#navigation-rules)).
9. **Every step can be accepted.** No step needs a change before the wizard
   lets the reader go on. A step that may be left empty for now says so on
   its Continue (**Continue without schedules for now**), and the wizard's
   final readiness check names what is still missing. A step whose purpose is
   a check (a test email) continues only once its check has passed. A
   wizard's tests come before its final Review, so Review is the last step
   before finishing and shows their results, and fixing a failed test never
   passes through Review first
   ([#1476](https://github.com/epiphany40223/parishkit/issues/1476)).
10. **Sub-pages stay in the wizard.** A page opened from a step (an editor, a
    new or edited item, a calendar, a history) draws the same stepper with
    its parent step current, and its action row starts with **Return to
    *step*** where Back sits (every Admin way back is named "Return to",
    [navigation rule 4](#navigation-rules)). Its primary control follows
    rule 3 for the item: **Keep as is and go back** while unchanged, **Save
    and go back** once it holds a change. Either returns to the step page
    with the item's new state, focus on its row and a status line saying
    what was saved or kept, without a reload unless the page needs one.
11. **Leaving is explicit.** The stepper's summary offers **Leave campaign
    setup** (or **Leave setup**), which opens Home; Home then offers
    **Continue campaign setup:** and the next step not done yet while the
    wizard applies. A wizard finishes at its last step (Finish setup, or
    Go-live readiness). The Admin menu works as always, and a menu page that
    is neither a step nor a sub-page draws no stepper.
12. **Outside the wizard nothing changes.** The same page outside its wizard
    (setup has finished, the campaign is no longer a Testing draft, a go-live
    has started, or the deployment is in Production) has its ordinary Save,
    unavailable until something changes, and its ordinary review dialog.

Status: these rules are the target; the wizards reach them in steps, as #1404's
plan lays out. The setup wizard meets rules 2, 4, 5, 7, 8 and 12, and
rule 1 except the "Next:" line: a saved page stays completed, while its
Review is asked for again after any later edit of the draft and its parish
data load goes stale when the ParishSoft credential changes. Its button
keeps the single label Save and continue on an unchanged page (rule 3), and
it has no Leave setup in its summary (rule 11) and no sub-pages. The
campaign wizard meets rules 1, 7, 9, 11 and 12 and the Return to *step*
link of rule 10 ([#1407](https://github.com/epiphany40223/parishkit/pull/1407)):
Create the campaign is its step 1 before the campaign exists (#1404 PR 3);
an applied change or a Continue stays completed, while Preview and test
email counts only for a test of the invitation's current revision. It meets
rule 8 for Continue, which opens the next step not done yet; after a save,
the page redraws in place and stays on the step it completed until the
direct save (planned, #1404 PR 4). Until the later #1404 pull requests
land, its Continue is unavailable while the form holds a change, so the
page's own Save, with its review dialog, is the way on (rules 3 to 5);
loading the campaign's ParishSoft data is a notice on every step rather than
a step of its own; and Back asking before it discards (rule 6) and the
sub-pages' Keep as is and go back and Save and go back (rule 10) are not
built in either wizard. Each part's precedent moves into the rule above when
it lands.

## Admin page layout and help

### Page help

**Rule.** Help is plain language for a mid-level IT administrator: a page
leads with its data; explanation sits in the "About this page" panel and in
field tips, with only short visible text. **Why.** Staff come to a page to do
a task; long visible prose pushes the data below the fold and is skipped.
**Shared code.** The `{% aboutpage %}` template tag, the field toggletip
(`field-tip.html`), the "Technical details" disclosure;
`test_admin_help_guards.py`. **Precedent.** #227.

Admin pages put the page's data first and keep explanation one deliberate
click away, without removing any of it. A page leads with its heading, its own
data line (such as counts or the ParishSoft data date), safety notices (such as
Testing mode) and then its data or form; at most a one-line hint that prevents
a likely mistake stays visible above the data. A line of cross-links to
related pages may sit above the heading instead, when an About panel left
open would otherwise push it out of view (#938). An ordinary
settings or editor page's body does not repeat a menu entry as a plain
navigation link: the menu is how an Admin moves between pages, so such a body
links only pages the menu does not offer (such as a child page whose
breadcrumb parent it is) and contextual actions
([#1118](https://github.com/epiphany40223/parishkit/issues/1118), Campaign
settings). Home's state-driven
[Next steps](../admin-portal/spec.md#home-page) list is an exception, and
a cross-link line (#938), a remedy link in a message and a "Return to" link are
outside this rule; a broader sweep of other pages belongs to #522. A caution that prevents a
likely mistake (what an action cannot undo or stop, or a lasting consequence
it has) is never only in the panel: it stays visible beside its control as a
one-line hint or a notice. The page's introduction and longer explanation of
how it behaves (how a credential is kept, how mail schedules work, the
placeholder reference) sit in an "About this page" panel beside the heading: a
native disclosure that starts closed, and that each browser remembers open,
per page type, once an Admin opens it. Every page with a panel has exactly
one, placed directly after its heading, so its control always sits on the
heading's line, open or closed, and stays in place when it is toggled, even
when the open help makes the page scroll (the theme always reserves the
scrollbar's space, so a centred page never moves sideways); opened, the help
appears on its own full-width line below. Only that open choice is
stored in the browser, and closing the panel removes it; with nothing stored
the panel starts closed and opens with a click or the keyboard. Help under a field is a short hint; longer
field explanations belong in the About panel or a click-to-open field tip,
never in hover-only tooltips, which touch and keyboard users cannot reach.

Admin pages are laid out for a laptop screen: compact headings, panels,
notices and table cells, and short filter forms in one row. The target is that,
with help closed, a page's first data starts inside the browser window of a
typical laptop (a 1366×768 screen, about 650 pixels of page after the
browser's own toolbars). Pages with short filters put them in one row. A
report whose filter form is longer than a row puts it in a "Filters" panel (the
`filterpanel` template tag): a native disclosure that starts closed and renders
open whenever the shown results use any filter other than the page's default,
so an applied filter is never hidden. Open, a long form is a compact grid of
its fields, each label tight above its box and Apply beside the last field
when the row has room, in one column on a phone (#1411); the Family
directory's short fields keep their filter bar. The Family directory is also
the one exception to the closed start: its Filters panel is open by default
(an Administrator decision, #227), and the other reports keep theirs closed.
A panel's summary says how many are applied
("Filters: 2 applied", or "none applied"), and that count follows an in-place
Apply while the panel and its form keep their elements. A report's export form
is likewise a closed disclosure (for example, "Export complete results (CSV,
XLSX or PDF)"). The Family directory,
Additional information, Financial stewardship, Limitations and Census
changes follow this (#227); System logs, Ministry follow-up and the
remaining report pages are still to convert.
An edit form's short fields (a status, an outcome,
a date and a time) likewise share a row on desktop (`.field-row` in
`ui-v1.css`), top-aligned so a time's reading line fits under its box, while
notes span the form; at phone width each field takes its own row (#1014). The
Family portal keeps its own spacing.

Field help longer than about one line opens from an "i" button beside the
field's label (the shared toggletip). Only a one-line hint stays visible under
the label, for fields whose format or rule is needed every time (an example
address, "one per line", a key's paste rule), and a warning that blocks the
field, such as having no emails to schedule, always stays visible. The field
remains described by its full help, so screen readers announce it without
opening the tip. Checkbox help is a toggletip beside the box. A toggletip in a
table heading is described by its bubble alone, and it closes when the table's
box scrolls its button out of view. An export form carries no
help about its format menu (see [Downloaded files](#downloaded-files)).

An "i" button always opens a floating toggletip, on Family and Admin pages
alike and wherever the button sits: a label row, a filter bar, a table heading
or beside a checkbox. A click or tap opens a small bubble anchored below the
button, or above it when there is no room below, kept inside the browser
window at every width. The bubble floats over the page, so opening it never
moves a label or control, never changes the size or scroll of a table's box
and never scrolls the page ([no layout shift](#no-layout-shift)). It may cover
the field while open, since a second click on the button, Escape, a click
elsewhere or a click or tap on the bubble itself closes it (unless the reader
is selecting its text). Escape returns focus to the button; the other ways
leave focus where it is. Help that expands inline and pushes
the page down is reserved for text-labelled disclosures, such as About this
page, never an "i" (#1289).

Internal identifiers and bookkeeping fields that matter only for
troubleshooting (delivery, refusal and test references, a retained
configuration version, an export's requester reference and data-load
numbers) sit in a "Technical details" disclosure, closed by default, instead
of in the page's main text. Nothing is removed from the page; downloads leave
these internal fields out of every format
([downloaded files](#downloaded-files)). System logs' rows are the
exception: they show no identifiers at all, since a task's identifiers are on its
own page, which the row's task icon opens, and the log download keeps every
entry's (Administrator decision, 2026-10-11,
[#1065](https://github.com/epiphany40223/parishkit/issues/1065);
[System logs](../admin-portal/spec.md#logs)). An empty list says what to do
next rather than only that it is empty
([empty tables and lists](#empty-tables-and-lists)).

A hint that an action needs a sign-in from the last five minutes (the
step-up) shows only when re-authentication would actually be asked for: the
server, drawing the page, finds the sign-in older than that. Pages ask with
the shared `{% fresh_sign_in %}` template tag, or use their view's `fresh`
value, both built on the same check every gated action makes; a fresh reader
sees no such hint. A sign-in that ages after the page is drawn gets the
step-up answer when the form is sent, which explains itself, so no timer
reveals the hint later ([no layout shift](#no-layout-shift); Administrator
decision, 2026-10-12,
[#1159](https://github.com/epiphany40223/parishkit/issues/1159)). A page
shows such a hint at most once: an integration page with a key field says
once that what was entered, the pasted key included, is not kept, rather
than repeating the hint for the key. Every step-up hint, before an action
or after one refused for a stale sign-in, is the one shared notice
(`components/step-up-notice.html`): the same "For security, … needs a
sign-in from the last five minutes." sentence and markup on every page,
naming the action in a short phrase. No page words its own, and a template
test fails when one does
([#1192](https://github.com/epiphany40223/parishkit/issues/1192)). The idle
sign-out notice is not a step-up hint and is unaffected.

Template tests guard these rules: one fails when a paragraph shown without a
click holds a message longer than about two sentences (50 words), one fails
when more than about one line (15 words) of help sentences shows between a
page's heading and its data outside the About panel, notices and links, one
fails unless each page's single About panel directly follows its heading, and
one fails when a label for an internal identifier or worker field (such as a
heartbeat, lease, data-load number or request ID) appears outside a Technical
details disclosure. The long-paragraph, introduction and internal-field
checks each keep a short, reviewed list of exceptions, and the long-paragraph
and introduction exceptions must shrink as their pages are converted. A
browser test checks on every component fixture with an About panel that the
closed control is drawn on the heading's line, to its right.

### Button labels

A button's label never breaks inside a word, and a one-word label never
wraps at all ([#614](https://github.com/epiphany40223/parishkit/issues/614)).
This covers buttons, links styled as buttons, submit inputs and sortable
column headings, whether they are POST buttons or GET links, so both kinds of
heading wrap alike. A longer label may wrap between words, so it never makes
a phone-width page scroll sideways; a table cell grows to fit its buttons,
and a wide table scrolls inside its own region. A browser test checks the
buttons and headings on representative pages at 320 px and 1280 px.

Button labels don't end in an ellipsis, even when a review step follows
(Administrator, 2026-10-13,
[#1268](https://github.com/epiphany40223/parishkit/issues/1268)). A template
test fails when a template's button label ends in "…", "..." or `&hellip;`.
It reads each branch of a shared button, a raw button or a link styled as a
button, and the text that templates give a variable a button shows as its
whole label, such as the confirm dialog's `accept`. Labels built in Python
or written by JavaScript are not covered. Progress text such as "Saving…"
and truncated names are not labels, so they are unaffected.

### Shared visual style

The portal and the emails share one visual style
([#732](https://github.com/epiphany40223/parishkit/issues/732)):

- **Design tokens:** `web/design_tokens.py` is the one source of the
  colours, corner radii, fonts and button shape. The portal stylesheet
  declares them as CSS custom properties, and a unit test fails if the two
  disagree, including any `var()` fallback in another stylesheet. The other
  stylesheets (the setup wizard, the stepper it shares with the Campaign
  setup pages, the report pages and the session dialog)
  load after it and read the tokens directly, with no hex colour and no
  fallback; only the vendored chart tooltip rules are exempt. The setup
  wizard's Back and Continue buttons take the shared button shape. Email
  components read the same module and inline the values, because mail
  programs cannot load the stylesheet. So does the maintenance page shown
  while the site is updating, which loads no stylesheet: it inlines the
  light page, panel, ink and muted colours, the panel's corners and shadow,
  and the sans font stack.
- **Buttons:** templates draw every button, and every link styled as one,
  with the `{% button %}` tag. Its variants are primary (the default),
  secondary, large and link. It renders the stylesheet's classes and keeps
  each attribute in the order the template gives it, so in-place controls
  keep their `data-` attributes. A guard test counts raw button markup in
  every template against a list of reviewed exceptions that may only
  shrink. A button is secondary only when it is drawn so (the secondary
  variant, or the `button-secondary` class on a button a script builds),
  never because of where it sits or what attributes it carries, and a test
  holds the stylesheet's secondary rule to that class alone.
- **Email buttons:** `email_button()` renders the same primary and
  secondary buttons for email: a one-cell table with inline styles from the
  tokens, `bgcolor`, and Outlook padding. The label is real text, so the
  button reads with images off and in an inverted dark mode. It links only
  to absolute http(s) addresses. The daily and weekly report emails, and
  Family mail's call to action, draw their button with it, and a guard
  test keeps any other module from building an email button by hand. Its markup is already in the
  sanitizer's canonical form, so the report validators admit it unchanged;
  they still admit the earlier report button, so a report compiled before
  the change and retained for a retry still delivers.
- **Email layout:** the shared email layout takes the portal's ink, page,
  panel border and corner radius, link colour and warning notice from the
  tokens for every email: the report emails, the operational and security
  alerts, and Family mail (invitations, reminders, confirmations, scheduled
  emails, Testing-routed copies and every readiness and test-send sample).
  In Family mail, a paragraph that holds only one bold link to an absolute
  http(s) address is the email's call to action and draws as the primary
  email button, with the same label and link; every other link stays a
  link, and Admin mail is drawn as compiled. Content, links and codes never
  change for styling, and the plain-text alternative is unaffected. Every
  sender says whether it sends Family mail; there is no default. A change
  to Family mail's look ships after a Testing send to real mail programs,
  on a night with no reminder the next morning.
- **Notices:** a portal notice is one of three kinds, each a class on
  ordinary markup. A warning (`notice`, the default) says something to be
  careful of, or why something is unavailable; an error (`notice
  notice-error`) says something failed or was refused; an information
  notice (`notice notice-info`) is neutral, with nothing to beware of, such
  as confirming what the reader just did. Testing and sample notices are
  warnings, as the email notice is. All three share one shape and the ink
  text colour, and take the background and border tokens of a
  [status indicator](#status-indicators): the Warning and Error looks, and
  the Working look for information. A test checks their contrast in the
  light and dark schemes. Plain notices that are neutral
  move to the information kind page by page.

Cards and headings follow in a later slice of #732.

### Colour scheme

**Rule.** The Admin portal, including sign-in and the setup wizard, is light
or dark on screen. By default it follows the operating system's setting
(`prefers-color-scheme`), and it changes as soon as that setting does. In
dark, every colour token takes its dark value, and the browser's own form
controls and scrollbars turn dark too.

The **Appearance** switch at the top right of every Admin page's header,
sign-in and setup included, lets a person override that default in this
browser. It is a small segmented control of three icons: a sun for "Light",
a moon for "Dark" and a monitor for "Match this device".

- It is a radio group named "Appearance": one Tab stop, the arrow keys move
  the choice, and the chosen icon is drawn selected (and checked for screen
  readers). Each icon has its name as its accessible name and as a matching
  tooltip, like the [row action](#row-actions-and-confirmation) icons, and
  is drawn inline in the current text colour.
- Each segment is a 36-pixel square with the shared focus ring, in both
  schemes. Choosing one changes only colours, so nothing moves.
- Wider than a phone, it is pinned to the header's top right corner, and
  the header's other items (Find a Family, the status pills) wrap before
  reaching it. On a phone it shares the first row with the parish name, at
  the right, and everything else in the header moves below it; the page
  never scrolls sideways. The Menu button stays below the header.
- "Match this device" is the default, and choosing it saves nothing.
- "Light" or "Dark" is saved in the browser's `localStorage`, so every
  Admin page in that browser uses it, sign-in included.
- The choice is not tied to the Admin user or stored on the server.

A change applies in place: the page does not reload, nothing moves, and
other open Admin tabs follow. A saved choice applies before the page's body
is drawn, and the switch shows it selected from the first paint, so a page
never shows the other scheme or the wrong choice first. When the browser
refuses storage (a private window, blocked site data), the choice lasts
only for the page it was made on.

Everything else stays light:

- the [Family portal](#family-portal);
- a printed Admin page;
- emails, which keep the light tokens and rely on the
  [email button](#shared-visual-style) to survive a mail program's inverted
  dark mode;
- [downloaded files](#downloaded-files);
- each email sample preview (the content sample, the setup text editor's
  saved sample and the new campaign's copy preview), a light card that
  shows the email as it is sent.

Both schemes meet the same contrast minimums: text 4.5:1 on every surface
it is drawn on, and control borders, the focus ring and status and level
icons 3:1; where the focus ring sits on an accent surface (the checked
Basic or Advanced segment), it is drawn in the paper colour. The
[status indicators](#status-indicators) keep their per-state tokens in
both. Every colour a scheme changes is a token, so the dark scheme reaches
each rule with no per-component CSS. An image that draws in the ink
colour, such as the select arrow, has a dark twin.

An uploaded image was drawn for the light Family pages and emails, so in
dark it gets a white backing behind its transparent parts, which keeps a
dark logo visible and adds no size. That covers the parish logo in the
header and every Admin preview of an uploaded image: Parish logos (the
current logos and their review), the setup wizard's logo step, Campaign
images (the current images and their review) and the Hosted files
thumbnails.

**No literal colours.** Outside the token declarations, the portal's
stylesheets, scripts, templates and images name no colour: a rule reads a
token. The only exceptions are shadows and dialog backdrops (a translucent
dark shade that works in both schemes), print rules (print is always
light), the white image backing above, and the select arrow and its dark
twin (each drawn in its scheme's ink). A comment may still mention an
issue number such as #565.

**Charts.** A chart drawn on an Admin page takes the page's scheme. Its
background, axes, gridlines, labels, titles, legend and tooltip are drawn in
the portal tokens (paper, ink, muted text, border and control border), and
its marks in the chart palette, a token set with a light and a dark value
for each colour. A chart's specification names its colours by their light
values, which an emailed or downloaded chart keeps; the page draws each in
the matching token's value for its scheme. In dark, the chart's lines, bars
and markers keep 3:1 against the paper and its text 4.5:1, and the pledge
bands' ramp turns round (dark bands under light labels for small pledges,
bright bands under dark labels for large ones), so each band's label keeps
4.5:1. A change of scheme redraws every drawn chart in place, holding its
height, so nothing on the page moves. A chart reads the scheme from
`data-theme`, never from the device's setting, so it always agrees with the
Appearance choice.

**Why.** Administrators who work in a dark desktop get a portal that
matches it, without a second stylesheet that would drift from the first.
Parishioners are the larger and less technical audience and have not asked
for dark, so the Family portal is left as it is. **Shared code.**
`DARK_COLORS` in `web/design_tokens.py` is declared by `ui-v1.css` for
an `admin-document` root (set by `admin-base.html`) with
`data-theme="dark"`. `admin-gate-v1.js` sets `data-theme` in the head from
the saved choice or the operating system, keeps the choice as
`data-theme-choice` (from which `ui-v1.css` draws the selected icon), and
runs the switch in `admin-appearance.html`, which `admin-base.html` puts at
the end of the header. `test_design_tokens.py` checks both token sets and
their contrast, and `CHART_COLORS` and `DARK_CHART_COLORS` (declared by
`chart-v1.css`) with theirs; `chart-v1.js` draws each chart in its scheme.
The browser test `test_dark_scheme.py` checks that key Admin
pages leave no light surface or light ink in dark. It also checks the
choice: it overrides the device in place, holds across a reload from the
first paint, reaches other tabs, and leaves Family pages and print light;
and the switch: keyboard use, focus ring, axe in both schemes, and its
place at phone and laptop widths. `test_chart_scheme.py` checks that every
chart page draws its charts' text and marks in the tokens of each scheme
and redraws them in place when the scheme changes. `test_literal_colours.py`
fails on a literal colour anywhere else in the portal's stylesheets, scripts'
strings, templates' styles and static images, and on an exception that no
longer matches anything; `test_dark_scheme.py` checks the image backings.
**Precedent.** #732 (tokens), #1264 (the switch's place: Administrator,
pk-local batch 24a).

### Status indicators

**Rule.** A status that says whether something is working shows a word, an
icon whose shape differs per state, and a colour, never colour alone. Its
look per state:

| State | Icon | Colours (design tokens) | Start bar |
| --- | --- | --- | --- |
| Working | `ok`: a check mark in a circle | icon and border `--accent`, background `--accent-soft`, text `--ink` | none |
| Warning | `warning`: a triangle with an exclamation mark | icon and border `--warning-border`, background `--warning-bg`, text `--warning-ink` | .35 rem, as a notice has today |
| Error | `error`: a cross in a circle | icon and border `--error-border`, background `--error-bg`, text `--error-ink`, word in bold | .6 rem, new, so an error stands out more than a warning |
| Not current | `unknown`: a question mark in a dashed circle | icon and border `--control-border`, background `--paper-soft`, text `--muted` | none |

- **Icons.** The icon is hidden from assistive technology, because the
  word carries the state. Every mark inside an icon (the check, the
  exclamation mark, the cross, the question mark) is cut out of the shape, or drawn as an
  outline, in `currentColor`. No icon has a fixed colour, so forced-colours
  mode keeps every shape and mark. Icon colours come only from the
  [design tokens](#shared-visual-style).
- **Contrast.** Text meets 4.5:1, and icons and borders 3:1, against their
  background, checked by a contrast test over the token pairs above. Not
  current uses `--control-border` (3.3:1 on `--paper-soft`), because the
  fainter `--border` (1.2:1) fails the 3:1 minimum.

**Why.** A colour-only dot fails colour-blind readers, forced-colours mode
and print, and one set of shapes lets a reader learn it once. **Shared
code.** The severity icon set in `components/level-icon.html`, with the
`ok` and `unknown` shapes, and the `.status-working`, `.status-warning`,
`.status-error` and `.status-unknown` classes in `ui-v1.css`, which read
the tokens; the level colours are tokens too, and `test_design_tokens.py`
holds the contrast test. The first dashboard pull request of
[#1214](https://github.com/epiphany40223/parishkit/issues/1214) reworked
the set to these rules. System logs' level icons keep their shapes; their
warning and error marks are now cut out, so they take the status border
colours, which are dark enough for that. **Precedent.** #601 (log level icons), #1214 (the
[System health dashboard](../admin-portal/spec.md#health-dashboard), its
first user).

### Stat tiles

**Rule.** A page's headline figures (a few counts, money totals or shares
that sum up the page) are **stat tiles**, all drawn by one component: the
server helper `parishkit.stewardship.web.stat_tiles` and the template
`components/stat-tiles.html`. Each tile has a short label, one big value
and up to two short explanation lines. A long list of figures, or facts
that are not numbers (file names, dates, yes or no), stays a key/value
list (`<dl>`). A state (Working, Warning, Error) is a
[status indicator](#status-indicators), not a stat tile.
**Why.** The same card had been drawn by hand in several templates, with
different markup, three ways of formatting numbers, three ways of showing
a missing value (the word "Unavailable", "—", or a misleading zero) and
two ways of reading to a screen reader. **Precedent.**
[#1540](https://github.com/epiphany40223/parishkit/issues/1540)
(Administrator, 2026-10-17), built like the
[schedule previews](#schedule-previews) component.

- **Look.** A row of flat cards (`--paper-soft` background, `--border`
  edge, `--radius-small` corners) that wraps on narrow screens: the label
  above the value, the value large and in tabular figures, the
  explanation lines below in the muted text colour. Every colour and
  radius comes from the [design tokens](#shared-visual-style), so the
  [colour scheme](#colour-scheme) applies.
- **Numbers.** The helper formats every value from its type, following
  [numbers and money](#numbers-and-money): a count is grouped, money is
  USD with cents and a share is a percentage. Templates never format a
  tile's value. A tile that states a share prints the count and what it
  is compared with as well as the percentage.
- **Unknown values.** A value that is not known reads "—" with a
  [toggletip](#page-help) saying why, in plain language. The toggletip's
  button is described by its reason, so a screen reader announces it when
  the button gets focus. A value never reads zero, and never a word such
  as "Unavailable" in the value's place. The helper refuses an unknown
  value without a reason, and the include refuses a row with no
  accessible name.
- **Words.** The label is short plain language naming what is counted.
  An explanation line says what the value is compared with or what it
  leaves out; it never repeats a figure another tile or the page already
  shows, as for [chart captions](#charts). A tile has no heading of its
  own: the label is not a heading, and the row is named by its panel's
  heading. A panel with more than one row names each row with its own short
  label instead, so a screen reader tells the rows apart
  ([#1537](https://github.com/epiphany40223/parishkit/issues/1537)).
- **Trend.** Where a comparison exists, a tile may end with one trend
  line, written in words with an arrow ("↑ 12.5% more"), never colour
  alone.
- **Share bar.** A tile that states a share may also draw it as a plain
  progress bar at the tile's foot, so the bars of a row of tiles line up.
  Its exact figures are always printed in the tile, so it has no tooltip,
  and it is hidden from assistive technology, which reads the text instead
  ([#720](https://github.com/epiphany40223/parishkit/issues/720)).
- **Reading order.** The row is a list with an accessible name. Each tile
  reads its label, then its value, then its explanation, in that order;
  no visible text is hidden from assistive technology, and nothing uses a
  mouse-only `title` tooltip.
- **Times.** A time in a tile is shown in the browser's time zone, with no
  "UTC" label ([dates and times](#dates-and-times)).
- **No layout shift.** The tiles of a row stretch to one height. An
  in-place update replaces their contents and keeps the row's tiles, so
  nothing below moves ([no layout shift](#no-layout-shift)). A value is
  always one line of one height: it never wraps, a long value (ten
  characters or more) is drawn smaller instead, and on a phone the tiles
  stack, so a wider figure in a fresh answer cannot make its row taller
  ([#1537](https://github.com/epiphany40223/parishkit/issues/1537)). A
  value also always stays inside its tile, at every window width and
  font size: a row puts fewer tiles across rather than make a tile too
  narrow for its value. Every row of tiles stacks on a phone, including
  those not yet drawn by the shared code. A row of four or five tiles
  never leaves one tile alone on a line: four go 4 or 2 + 2 across, five go
  5 or 3 + 2, and either stacks when even that is too narrow
  ([#1537](https://github.com/epiphany40223/parishkit/issues/1537)).
- **Shared code.** The helper's `tile()` builds each tile and the include
  draws a row of them. A guard test fails when a template outside
  `components/` draws stat-tile markup by hand; the templates not yet
  moved onto the component are listed in it, each with why, and the list
  may only shrink. Email and PDF reports cannot load the stylesheet and
  keep their own layouts.

### Time entry

Every Admin field that takes a time of day accepts it in any common form,
shows how it read the entry, and stores the same canonical value as before
([#631](https://github.com/epiphany40223/parishkit/issues/631)). Admins type
`2:00`, `2am`, `0200` or `2:30 PM`, and a native time control's typed entry
differs per browser and locale (Linux WebKit ignores typing, #605), so these
fields are plain text boxes. The cost is the phone's own time picker, which a
text box does not offer; typing a short form such as `9p` is as quick.

**What is read.** Case is ignored, and so is white space around the entry
(the same set of white-space characters on the server and in the page).

- 24-hour, with a colon, `.` or `h` between hour and minutes: `02:00`,
  `2:00`, `14.30`, `14h30`. Seconds are accepted only in the colon form
  (`hh:mm:ss`) and only when they are `00`, since these fields are to the
  minute.
- A bare 1–2 digit number is an hour (`7` is 07:00); a bare 3–4 digit number
  is 24-hour `hmm`/`hhmm` (`830` is 08:30, `1430` is 14:30).
- Any of those, without seconds, followed by `am`/`pm`, `a`/`p` or
  `a.m.`/`p.m.`, with or without a space: `2pm`, `2:30 PM`, `2p`,
  `11 a.m.`, `830pm`. The hour is then 1–12; `12 am` is 00:00 and `12 pm` is
  12:00.
- `noon` and `midnight`.

Refused, with a plain message at the field: an hour above 23 (`24:00`), a
minute above 59 (`7:60`), an hour outside 1–12 with AM or PM (`13pm`,
`0am`), seconds other than `00`, and anything else, including digits from
other scripts. Nothing is guessed: a bare `7` is 07:00, never 7 PM, and the
reading shows both clocks so the Admin can see which. One exception keeps
saved work saving: a mail schedule saved before this change with seconds in
its time keeps that time when it is posted back unchanged.

**On the page** (`ui-v1.js`, fields marked `data-time-entry`):

- A readable entry's reading shows under the field at once, as the Admin
  types, on both clocks: "Reads as 07:00 (7:00 AM)".
- An entry that cannot be read shows its refusal there once typing pauses
  (about half a second) or focus leaves, so a half-typed `2:` is not flashed
  red; the field is marked invalid only once the refusal shows. Save is
  unavailable at once, and the hint that explains it (below) appears with
  the refusal. The refusal clears as soon as the entry reads. A server error
  under the field is removed on the first edit, and the live check then
  speaks for the field.
- A visually hidden live region beside the field announces its reading or
  refusal, only for that field's own typing (once typing pauses) or as focus
  leaves it, and only when the message changed. Loading the page, the page
  being shown again, and another control's change (a mail type, the refresh
  frequency) update the line silently. The reading line keeps one line's
  height while empty, so a first reading does not move the controls below
  it.
- When focus leaves a readable entry it is rewritten in the canonical form
  (`2pm` becomes `14:00`).
- While a shown entry cannot be read the form's submit buttons are
  unavailable, with a one-line hint below them ("Fix the time that can't be
  read to continue."), which the buttons name while it shows. The hint never
  shares the buttons' line, so their labels do not wrap. A field hidden by
  another choice (a mail type that takes no time, a refresh frequency without
  set times) does not hold Save, and a `formnovalidate` button stays usable.
- A list field (the refresh schedule's Edit as text lists, a multi-line
  `textarea`) reads each entry. Entries are separated by commas, semicolons,
  spaces or new lines (a one-line list box drops line breaks from pasted
  text). A suffix standing alone after a space (`2 pm`) belongs to the entry
  before it, but not across a comma, semicolon or new line (`2, pm` is
  refused). A time listed twice is reported in the reading and saved once.
  The reading names each time for a list of up to eight; a longer list reads
  as its count and range ("Reads as 91 times, from 00:15 (12:15 AM) to 23:45
  (11:45 PM)"), so the line stays short as the Admin types. A list typed one
  time per line keeps one time per line when focus leaves it.
- A field out of view does not hold Save: one hidden by another choice, or
  inside a closed `details` panel (Edit as text).
- A list field whose blank stands for a value says so: a blank entry, or
  one of only separators such as `,`, reads "Blank reads as 02:00 (2:00 AM)"
  and saves that value. A blank single time has no reading; whether it is
  required is the form's rule.

The Admin portal [requires JavaScript](#javascript-requirement), so there is
no no-script fallback. The server stays the authority: one parser
(`parishkit.stewardship.time_entry`, through the form fields
`FlexibleTimeField` and `FlexibleTimeListField`) reads every post and every
`pk-stewardship admin` command that binds the same forms, such as
[`schedule preview`](../admin-automation/spec.md#schedules-and-configuration).
The page script applies the same rules, and one shared table of cases
(`tests/stewardship/fixtures/time_entry_cases.json`) runs against both, so
the two cannot drift.

**Time zones.** The fields read wall-clock times; each keeps its page's zone
rule under the [global presentation rules](#dates-and-times).
New and Edit scheduled email and the date-change review take send times in
the browser's zone (see [New and Edit scheduled
email](../admin-portal/spec.md#new-and-edit-scheduled-email)). The ParishSoft refresh times
are in the refresh schedule's time zone (the current campaign's, else the
parish's), the recorded exception to the browser-local rule that those rules
describe (see also
[ParishSoft refresh schedule settings](../admin-portal/spec.md#parishsoft-refresh-schedule-settings)).

**Fields.** Every Admin time of day is a shared time entry field: a form's
`FlexibleTimeField` or `FlexibleTimeListField`, or a hand-drawn `input` or
`textarea` marked `data-time-entry`. No page draws its own reading; a guard
test (`tests/stewardship/test_time_entry_guard.py`) fails on a new time box
without the mark ([#1117](https://github.com/epiphany40223/parishkit/issues/1117)).

| Page | Field | Entry |
| --- | --- | --- |
| ParishSoft refresh schedule | Each rule's At, From and To; each skip's At, From and To | One time, refresh schedule time |
| ParishSoft refresh schedule, Edit as text | Full refresh times | List, refresh schedule time |
| New and Edit scheduled email | Send time | One time, browser time |
| The date-change review | Send time | One time, browser time |
| Ministry requests' follow-up request | Contact attempt time | One time, browser time zone |
| Logs, reports | Date filters | Dates only, no time of day |

The Ministry follow-up contact attempt moved to this entry after its in-place
save work ([#592](https://github.com/epiphany40223/parishkit/pull/592))
merged ([#398](https://github.com/epiphany40223/parishkit/issues/398)); its
date stays a date control, and its not-in-the-future check reads the typed
time as the server does. The
[refresh schedule editor](../admin-portal/spec.md#parishsoft-refresh-schedule-settings)
([#632](https://github.com/epiphany40223/parishkit/issues/632)) uses this entry
for its rule and skip times, and its Edit as text list holds only full
refresh times, since quick updates left the Admin UI
([#1122](https://github.com/epiphany40223/parishkit/issues/1122)).

### Schedules

**Rule.** Every Admin editor that sets a future or repeating event looks and
behaves the same way. These editors are the mail schedules (with their
repeats and the daily and weekly Admin digests), the ParishSoft refresh
schedule, and the campaign dates. They share plain words with no cron text,
the [Basic and Advanced modes](#basic-and-advanced-modes), one time input,
one day-of-week picker, one zone note, one preview pattern and one
daylight-saving rule. **Why.** Each scheduler was built on its own, so an
Administrator had to learn a different editor for each. One pattern is
learned once. **Shared code.**

- `parishkit.stewardship.wall_clock`: the weekday names, Monday first
  (`WEEKDAYS`, `weekday_choices`), the stored `HH:MM` check (`is_hhmm`) and
  its formatters (`hhmm`, `wall_hhmm`).
- [Time entry](#time-entry): `FlexibleTimeField` and `data-time-entry`.
- `campaigns/recurrence.py`: repeat rules. Its cases
  (`tests/stewardship/fixtures/recurrence_cases.json`) also pin the page
  script's `ParishRecurrence`.
- `components/repeat-rule.html`: the one repeat rule's controls (how often,
  the days, the monthly choices, From and Until), with its choices from
  New scheduled email's `RepeatRule` form.
- `campaigns/intervals.resolve_local`: the daylight-saving rule.
- `ParishEditorMode`: the Basic and Advanced modes.
- `components/zone-note.html`: the note that names the browser's zone
  beside typed dates and times, hidden until the page script knows the
  zone. `test_zone_note_component.py` lists the pages that still write it
  by hand.

**Precedent.** [#1129](https://github.com/epiphany40223/parishkit/issues/1129)
(Administrator, 2026-10-12).

- **Plain words.** A schedule is described in a sentence wherever it is
  shown: in a Basic choice, the preview, the review and a table row, such
  as "Every Tuesday at 7:00 PM", "Daily at 6:00 AM" or "Every 4 hours from
  12:00 AM". It is never shown as a cron expression, an ISO duration or a
  raw stored rule.
- **Basic and Advanced.** An editor whose full rule set is much harder than
  the common case follows [Basic and Advanced modes](#basic-and-advanced-modes):
  Basic has a few presets, and Advanced has the rules, the exceptions, and
  "Edit as text" where the editor has it. An editor of one or two plain
  fields, such as the campaign dates, has no modes.
- **Times of day** are [time entry](#time-entry) fields. No schedule editor
  draws its own reading or uses a native time control.
- **Days of the week** use one picker that lists Monday to Sunday with the
  full names from `WEEKDAYS`, and stores `date.weekday()`'s index (Monday is
  0). One day is a select; several days are checkboxes in a `fieldset` with a
  `legend`. A month grid, such as the
  [Campaign calendar](../admin-portal/spec.md#campaign-calendar), starts on
  Sunday, the parish-calendar convention, and reorders the same names.
- **Dates** are native date controls. A date with no time is a calendar date
  and is never shifted ([dates and times](#dates-and-times)).
- **Time zone.** Times are entered and shown in the browser's zone
  ([dates and times](#dates-and-times)). A repeating schedule is stored and
  evaluated in its schedule zone: the current campaign's, or the parish's
  when there is no campaign. The page converts a typed time through the
  schedule's next occurrence, as
  [New and Edit scheduled email](../admin-portal/spec.md#new-and-edit-scheduled-email)
  does. The shared zone note beside the fields names the zone the times
  are typed in. The
  [ParishSoft refresh schedule](../admin-portal/spec.md#parishsoft-refresh-schedule-settings)
  is the recorded exception: its times are typed and shown in its schedule
  zone (decision 18).
- **Daylight saving.** One rule turns every local wall time into an instant,
  whether a schedule is evaluated or a one-off time is typed into a page
  ([dates and times](#dates-and-times)), on the server (`resolve_local`, and
  `stewardship_resolve_local_v1` in the database) and in the page script
  (`ParishLocalTime.moment`). A wall time that occurs twice (clocks fall
  back) is its first occurrence, so it runs once. A wall time that the
  spring-forward change skips is the first instant after the gap, so 2:30
  AM is 3:00 AM, not 3:30 AM. No occurrence runs twice or is dropped, and a
  form is never refused for a skipped time. The shared cases
  (`tests/stewardship/fixtures/dst_cases.json`) pin the server and the page
  script to the same instants
  ([#1129](https://github.com/epiphany40223/parishkit/issues/1129)).
- **Preview.** Before Review, an editor shows the schedule's next
  occurrences in plain words, each with its weekday and date, in the
  browser's zone only, as every review does
  ([#558](https://github.com/epiphany40223/parishkit/issues/558)); the
  refresh schedule is the one list shown in its schedule zone
  ([schedule previews](#schedule-previews)). The
  preview is redrawn in place as the editor changes
  ([in-place controls](#in-place-controls)), in a region that keeps its
  place ([no layout shift](#no-layout-shift)). The review lists every
  occurrence a change adds. A feature-specific view, such as the refresh
  schedule's seven-day preview or the Campaign calendar, is an extra view of
  the same evaluation, not a second preview. Every list of a schedule's
  occurrences, live or in a review, is drawn by the one
  [schedule preview](#schedule-previews) component.
- **Limits** are refused at the field in plain words, with the limit named,
  such as the 30-date limit on a reminder repeat.
- **Host schedules.** Off-site backups are not edited in the Admin portal.
  The host's scheduled job runs them, and
  [System health](https://github.com/epiphany40223/parishkit/issues/530)
  shows that schedule read-only, in the same plain words (the #1129 default).

**Adoption.** The ParishSoft refresh schedule follows all of these rules
except the browser-zone entry, which is its recorded exception
([#1122](https://github.com/epiphany40223/parishkit/issues/1122)), so its
note names its schedule zone instead. The date-change review draws the
shared zone note, as do System logs and Ministry requests; New and Edit
scheduled email follows once its open changes land. The two mail
repeat panels, New scheduled email's Repeat and the date-change review's
Repeat a reminder, draw one repeat rule, so they offer the same choices in
the same order and one page script expands both. Later
[#1129](https://github.com/epiphany40223/parishkit/issues/1129) slices bring
the shared preview and Basic and Advanced modes to the mail schedule
editors, reminder repeats and digests. Until each change lands, an
editor keeps the behavior its feature spec describes.

### Schedule previews

**Rule.** Every list of when a schedule runs or sends is drawn by one
component: the server helper `parishkit.stewardship.occurrence_preview`
and the template `components/occurrences.html`. It lists the schedule's
occurrences, grouped by day, in plain words. It is read-only.
**Why.** The refresh schedule's seven-day list and the mail schedule
review's send times were built separately, with different words, time
forms, empty states and daylight-saving notes. One component reads the
same everywhere and is tested once. **Precedent.**
[#1129](https://github.com/epiphany40223/parishkit/issues/1129)
(Administrator, 2026-10-12). Each feature's own words and limits stay in
its feature spec: the
[seven-day preview](../admin-portal/spec.md#seven-day-preview) and the
[mail schedule review](../admin-portal/spec.md#dates-and-mail-schedules).

- **What it lists.** Each feature chooses one of two scopes, and its
  feature spec names it:
  - *a count from now*: the next occurrences after now, earliest first, up
    to the feature's limit (the mail review's next five sends). Such a
    list never shows an occurrence that has already passed; a review that
    needs a past send (a missed reminder sent at once) says so in its own
    words.
  - *a window of whole days*: every occurrence on each local day of the
    window, starting with today (the refresh schedule's seven days). Today
    is listed whole, so its earlier times are shown with the rest of the
    day's schedule. They are not marked as already run: the list shows
    what the schedule does, not what has run.

  Each occurrence is one line: its time, then what runs ("Full refresh",
  "Reminder 2", "Daily Admin digest for Monday, October 19"). An
  occurrence that will not run stays listed, struck through, with its
  reason in words ("covered by the 06:00 full refresh"), so a struck line
  never relies on the strike alone.
- **Days.** Occurrences are grouped by their calendar day in the zone the
  list is shown in. The day is named with its weekday and its date in the
  [parish date format](#date-format), with "Today" for today.
- **Time zone.** A list is shown in the browser's zone
  ([dates and times](#dates-and-times)). The server groups it, so the
  request that draws it carries the browser's zone (the hidden
  `data-browser-zone` field New and Edit scheduled email already post);
  where a page posts a zone and it is not usable, the preview is not drawn
  and the page says why, as New scheduled email's missing-zone notice
  does. A caller with no browser at all (the command line, or a review
  built for a request that posts no zone) passes no zone: the list is then
  not grouped by day, and each line is a full `data-local-instant` that the
  page script writes as a day and time, if a page shows it at all. The
  [refresh schedule](../admin-portal/spec.md#parishsoft-refresh-schedule-settings)
  is the recorded exception (decision 18): it is grouped and shown in its
  schedule zone, and the page script adds each time in the browser's zone
  when the zones differ ("07:00, 13:00 your time"). No other list shows a
  schedule-zone time.
- **Times** are `<time datetime="…" data-local-instant data-time-only>`
  elements with the UTC instant, so the one browser formatter
  (`date-format-v1.js`) writes them as the time alone, since the day
  heading or the line names the day. A schedule-zone list writes the wall
  time on the server and marks the instant `data-other-zone-time`, which
  the same script fills with the browser's time when the zones differ, in
  the list's own 24-hour `HH:MM` form so the two times read alike.
- **Daylight saving** follows the [one rule](#schedules). A day whose
  clocks change in the shown zone says so beside its name ("clocks go
  forward", "clocks go back"). An occurrence whose wall time the change
  moves, in the zone its schedule is evaluated in, says so in words. When
  the shown zone is the schedule zone (the refresh list, or a mail review
  whose browser shares the campaign's zone), the line names the wall time:
  "Reminder 1, set for 2:30 AM: sends at 3:00 AM, clocks go forward", or
  "sends once, clocks go back". When the zones differ, the line never
  names the campaign's wall time ([dates and times](#dates-and-times)):
  "sends later than set because the campaign's clocks go forward", or
  "sends once because the campaign's clocks go back". The day note always
  speaks of the shown zone and the line of the campaign's clocks, so a
  shifted line on a day with no note, or a note with no shifted line, does
  not disagree. The general sentence ("a time that happens twice sends
  once, and a time that is skipped sends right after the change") is shown
  once, under the list, only when a listed day or occurrence is affected.
  It speaks of sends, so a list of runs (the refresh list, whose lines
  already say how each time runs; `verb="runs"` below) does not show it.
- **More.** When the feature's limit cut a count list short, one line under
  it says so with the limit named ("Only the next 5 sends are shown. Later
  sends follow the same pattern."). A window list names its window in its
  feature's words instead.
- **States.** The component has four, so no feature writes its own:
  - *shown*: the days and their occurrences;
  - *empty*: the schedule is valid and nothing is ahead, in the feature's
    words ("No more sends: this reminder's time has passed.");
  - *unavailable*: the schedule cannot be evaluated yet (a rule that
    cannot be read, a missing zone), in words naming what is missing
    ("The preview shows once every rule can be read and the schedule has a
    full refresh.");
  - *paused*: the occurrences are listed as *shown*, with one notice above
    them naming why and linking where to see more ("Live campaign email is
    paused, so these sends are held. Pause and resume mail shows what sends
    when mail resumes." with a link to Pause and resume mail). Paused sends
    are not struck: the schedule is unchanged, and resume's recovery plan,
    not the preview, decides what each held send becomes.
- **Layouts.** *Grouped*: one collapsible section (`details` and
  `summary`) per day, the summary giving the day and the feature's counts,
  today open; for a list of many occurrences a day, such as the refresh
  schedule. *Compact*: one list, one line per occurrence, each line naming
  its day and, as a grouped summary does, the day's clock change; for a few
  occurrences, such as a mail review. Both layouts take
  the same context, so a feature changes layout without changing its
  helper. The server groups by day rather than the page script, which
  could name each line's day from `data-local-day`, because a grouped list
  also carries per-day counts and clock changes that only the server knows,
  and one grouping path serves both layouts; a zone-less caller gets the
  ungrouped lines above.
- **In place.** A live preview is redrawn in place as its editor changes
  ([in-place controls](#in-place-controls)), inside a region that keeps its
  place ([no layout shift](#no-layout-shift)), and the days a reader opened
  or closed stay so, matched by date, not by position. Only a day whose
  open state differs from what the server drew is the reader's choice;
  every other day keeps the drawn state, so after midnight an untouched
  list opens its new today. The preview is not a live
  region: the editor's one status line announces a check's result.
- **Accessibility.** The list is the text equivalent of any picture of the
  same schedule (the refresh grid, the Campaign calendar's month grids),
  which points to it. Each day's name is the `summary` (grouped) or begins
  its line (compact), so a screen reader hears the day with each group.
  Struck lines carry their reason in words. Color and shape are never the
  only signal. The component draws no control besides the day sections.
- **Extra views** read the same day data. The refresh schedule's seven-day
  grid draws its cells from the component's days, so the two never
  disagree.

**API.** `occurrence_preview.build(occurrences, *, days=(), shown_zone=None,
schedule_zone, now, limit=None, more=False, paused=None, layout="compact",
empty=None, unavailable=None, verb="sends")` returns the include's context.
`verb` ("sends" or "runs", the refresh list) picks the words of a shifted
line and of the more line; a "runs" list leaves out the general
daylight-saving sentence (`dst_note` is false), which speaks of sends.

- Each occurrence is an `Occurrence(day, at, what, kind, wall=None,
  struck=False, reason=None, shift=None)`. `day` is its local date in the
  shown zone, which places it even when `at`, the UTC instant, is None (a
  time that will not run, such as a covered quick time); a zone-less list
  passes None. `wall` is the
  schedule-zone wall time for a schedule-zone list, `kind` a class hook,
  and `shift` "forward" or "back" from the daylight-saving rule.
- Each `Day(date, change=None, summary=None, extra=None)` carries one
  day's own data: its clock change in the shown zone, the feature's counts
  for its summary, and `extra`, a mapping of feature keys copied into the
  day's context (the refresh grid's `cells`). A window list passes one
  `Day` per day of its window, so a day with no occurrences is still
  listed; a count list may pass none, and `build` makes each day from its
  occurrences, working out its clock change from `shown_zone`.
- With no `shown_zone`, `build` does not group, makes no day headings and
  never refuses: it returns one day-less list of lines (above).

The context is:

```text
{"state": "shown" | "empty" | "unavailable",
 "layout": "grouped" | "compact",
 "shown_zone", "schedule_zone", "browser_zone": bool, "grouped": bool,
 "paused": None | {"text", "url", "label"},
 "message": text for empty or unavailable,
 "days": [{"date", "weekday", "today", "change", "summary", "open",
           ...the Day's extra keys,
           "items": [{"at", "wall", "text", "kind", "struck",
                      "reason", "shift_text"}]}],
 "more": None | text, "dst_note": bool}
```

The include reads only its own keys; each line is drawn by
`components/occurrence-line.html`, which only the include uses. A page
draws it with
`{% include 'stewardship/components/occurrences.html' with preview=… %}`;
the include draws no title, so a feature that gives the list one draws it
itself. In the grouped layout each day's `details` carries `data-day` (its
ISO date) and today's carries `data-today`, which the page script uses to
keep a reader's open days by date. `test_occurrences_component.py`
renders each state and layout, and lists the templates that still draw an
occurrence list by hand, so no new one is added.

**Adoption.** The refresh schedule's seven-day list draws it (ADM-04
slice 7b); the next
[ADM-04](../../../plans/stewardship/admin-portal.md#adm-04-campaign-editor-content-schedules-and-previews)
slice brings it to the mail schedule review. Until that lands, the review's
feature spec stands.

## Tables

### Admin tables

**Why.** Staff learn one table once. **Shared code.** `table-navigator.html`,
`table-sort-heading.html` and the `sort_heading` template tag,
`table-selection.html`; `test_admin_tables.py`.
**Precedent.** #478, #484, #488.

Admin tables share one component, so paging, sorting, selection and styling
behave the same everywhere. A long table has a row navigator above and below
it: the rows shown and "Page N of M", a rows-per-page choice (25, 50, 100, 250
or All, where the table's source allows it), Previous and Next, and a
page-number field. A page number past the end shows the last page. A summary
table that follows its chart may offer 5 as a smaller first size and default
to it, since the chart already shows the whole series (the Response
dashboard's daily table,
[#1537](https://github.com/epiphany40223/parishkit/issues/1537)).

Column headings sort the table on the server. Each sortable heading is a
control that sorts by that column; choosing the sorted column again reverses
it, and times and counts sort newest or largest first on the first choice.
The sorted heading carries `aria-sort`, a small arrow marks it, and each
control's accessible name says which direction it will choose. A table accepts
only its own whitelisted sort tokens, each mapped to server-owned ordering, and
appends a unique tiebreak so rows with equal values never move between pages.
Rows with no value in the sorted column (a task without a heartbeat, a user
who never signed in) sort last in either direction.
A new sort starts again at page 1. The headings are the only sort control: no
page offers a separate sort menu, and a page's filter and export forms carry
the heading's current sort as a hidden field, so applying filters or
exporting keeps the order the table shows.

Page, size and sort are query parameters (`page`, `size`, `sort`), optionally
prefixed so two tables on one page keep their own place, and every navigator
link, heading and filter form keeps the page's filters and the others' choices.
Reports whose filters are private (the active parishioner family directory, System logs and the
campaign reports) keep them in POST state: their navigator and headings are
small CSRF-protected forms that carry the filters as hidden fields, so no
private value reaches a URL. System logs also accept a link carrying only its
non-private filters ([Logs](../admin-portal/spec.md#logs)). A control on
another page that opens such a report narrowed by a private value (Edit
user's "Sign-in activity", narrowed to one Google identity) is a link-styled
button posting a small CSRF form with that value as a hidden field, never a
link carrying it. A count in a table cell that opens the list it counts is
a **count link**: the list opens filtered so it shows exactly the rows
counted, its filter controls showing that choice; a zero count, or one the
reader may not open, is plain text, not a link; and the link's accessible
name names its row ("3 join requests for Choir"), since the visible number
alone is ambiguous out of context. So far only
[Ministry requests](../reports/spec.md#ministry-change-summary) has them
(#1133): its By Ministry counts filter the queue on the same page in place. A report whose rows an installed SQL selection
orders offers that selection's sort orders on the columns they order and its
page sizes; its other columns do not sort, since the schema owns those
orders. In v1 that covers:

- the active parishioner family directory (Family, Family DUID and the
  response columns
  ([#933](https://github.com/epiphany40223/parishkit/issues/933)), 50 rows;
  Family code would need every code decrypted per view);
- Financial stewardship detail (Family, Family DUID, Annual pledge and
  latest response);
- the Additional information queue (Family, Family DUID and Submitted);
- the follow-up queue on Ministry requests (Member and Ministry; newest
  first by default, with no Submitted column to show it, #1064).

Extending those vocabularies is a schema change; the DUID sorts of the
financial, Additional information and Ministry tables came with one
([#960](https://github.com/epiphany40223/parishkit/issues/960)). Columns that are only
controls (selection, actions, previews) never sort. Two short before/after
lists of pending setting changes (credential selection and integration
preview), the campaign mail test's at most ten reviewed Families and link
preparation history (panels, not columns) have no sortable columns.

A short table shown whole has no navigator: its headings carry only its sort
token (no page or size), and the page accepts nothing else for it. The two
session tables on Automation access are such tables (see
[portal user management](../admin-portal/spec.md#portal-user-management)).

A table's navigators and rows sit in one region with a stable id (the table's
anchor, derived from its parameter prefix so two tables on one page differ),
and every heading and navigator control names that id as its URL fragment.
Choosing a heading, Previous, Next, a page number or a
rows-per-page value re-sorts or re-pages the table in place (#478): the
browser fetches the page the control would have loaded, requested as the
control would have requested it (a GET table's link or query, a POST table's
CSRF form with its private filters), and replaces that region, every other
table region on the page and the hidden state fields (sort, size, applied
filters, request keys) of the page's filter and export forms from the fetched
page, so no control is left carrying a choice the table no longer shows; the
reader's visible choices in those forms are kept. The reader keeps their
scroll position, rows still shown keep their selection, focus returns to the
chosen heading or control, the heading's `aria-sort` and a polite live region
announce the new order or the rows now shown, and a GET table's choice
replaces the address so reload, bookmarks and returning to the page keep it; a
POST table's address never changes. Applying a page's filters works the same
way (#484): the filter form is sent as it would have been (a GET query, or a
CSRF POST body), every table region is replaced, and so are the counts,
summaries and filter-dependent panels outside the tables (a report's matching
count and summary, the active parishioner family directory's export column list); focus stays on
the filter button and the live region announces the rows now shown in each
table, including none. A response the
server refuses (a malformed filter's 400, a denial, an unavailable report) is
shown as returned; a POST is never sent twice, since each report read is
audited. While a request is in flight, repeating the same submission is
ignored. The Response dashboard's Chart population reshapes its chart,
table and export rather than one table, so it is a
[binary choice](#binary-choices) whose links refresh the page's region
instead. When
the fetch fails or returns another page (a sign-in), the ordinary page load happens and its fragment lands on the table rather than at
the top. The link preparation history keeps only the fragment and always
loads in full. A table region is one kind of region that
[in-place controls](#in-place-controls) refresh; that section states the
shared rules (one request at a time, when a POST may be sent again, the
fallback, focus and announcements).

Lists read straight from a growing database table page on the server with one
extra row to learn whether a next page exists, and count matching rows only up
to 10,000. Past that the navigator says "more than 10,000", omits the page
count and keeps paging by the extra row. They offer 25, 50 or 100 rows. On the
largest tables only indexed columns sort, so a page view never sorts or counts
a whole log.

A table with bulk actions has a selection column. Its header checkbox and a
Select all button choose every row on the current page; the bar above the
table shows how many rows are selected and enables its action buttons only
while at least one is. While none is, a short visible hint in a reserved
line beside or below the action buttons says what to select, and the buttons
name it (`aria-describedby`), with no tooltip; the line keeps its height
when the hint clears (Administrator decision, 2026-10-10;
[#879](https://github.com/epiphany40223/parishkit/issues/879)). Ticking a row
never moves a control under the pointer: the hint's line is reserved, and the
count and Select all button sit after the action buttons and keep reserved
widths, so their changing text cannot push them or rewrap the bar (#563).
With no rows at all the line is blank, since the table says it is empty,
unless the table gives an empty-table reason (such as why there is nothing
to import), which the line then shows and the unavailable buttons name.
When rows are shown but none can be selected (every file in use), the line
says why none can be ticked where the table gives that reason, and otherwise
stays blank as for an empty table
([#1332](https://github.com/epiphany40223/parishkit/issues/1332)).
The shared `table-selection.html` include and its `ui-v1.js` wiring give
every such table this behavior. The server validates every submitted
selection.

### Row actions and confirmation

**Rule.** Row actions are same-size icon buttons in the last column, Delete
asks in a confirmation dialog, a table that allows it acts on several rows
through a selection column, and Edit opens the item's own page (the New page,
filled in). Row links, icons that only read (filter the same table or open a
related page), take the same look in a column of their own, never the
Actions column. **Why.** One pattern for every list of editable things; a dialog
names exactly what will be removed. **Shared code.** The `table_actions`
template tag, `components/confirm-dialog.html` and `data-confirm-open` in
`ui-v1.js`. **Precedent.** #879, #882 (first user), #947 (second slice),
the Pages and emails email tables (third slice), Roster changes to enter
(a recording action); #1065 (row links, System logs).

A table whose rows can be acted on follows one pattern
([#879](https://github.com/epiphany40223/parishkit/issues/879)), built once
and shared. Its users so far are
[Dates and mail schedules](../admin-portal/spec.md#dates-and-mail-schedules), the
[Hosted files](../hosted-files/spec.md#admin-page) library, the live
sessions of
[Automation access](../admin-automation/spec.md#revocation-and-listing),
[Users](../admin-portal/spec.md#users-table), the email template tables of
[Pages and emails](../admin-portal/spec.md#default-content) and the bulk
outcomes of [Ministry follow-up](../admin-portal/spec.md#follow-up-workflows)
([#1131](https://github.com/epiphany40223/parishkit/issues/1131)) and the
Entered in ParishSoft tick of
[Roster changes to enter](../admin-portal/spec.md#roster-changes-to-enter); the
other Admin tables with per-row actions move to it in later changes, as the
inventory on #879 lists.

- **Actions column.** The last column holds each row's actions as small icon
  buttons (`table_actions`), all the same square size: Edit, a link to the
  item's own page, and Delete. Each has an accessible name and a matching
  tooltip that name the row ("Edit Reminder 2", "Delete Reminder 2"), and the
  icons are drawn inline, so they need no request and stay inside the
  content security policy. A destructive action with another name uses its
  own word and icon in Delete's place and works the same way; so far only
  Revoke ("Revoke nightly export") on Automation access. An action that
  records rather than removes, and that the row's other action undoes, also
  takes an icon of its own and acts [in place](#in-place-controls) without a
  dialog; so far only Roster changes to enter's tick, "Mark … entered in
  ParishSoft" (a check mark) and "Clear Entered in ParishSoft for …" (an
  undo arrow). A row shows only the actions that apply to it; a row with
  none shows nothing there.
- **Edit** opens the item's edit page, which is the same page as New, filled
  in. Saving it goes through the [review dialog](#review-dialog), after which
  the reader returns to the table (until #750 converts the page, through its
  separate review and status pages). Where only part of an item can change (a hosted
  file's placeholder name), Edit opens that part's own page, which returns to
  the table on save.
- **Delete** opens a confirmation dialog in the page: a native modal
  `<dialog>` that names what it removes and says what cannot be undone, with
  the destructive action and Cancel. While it is open the rest of the page is
  inert and Tab stays inside it; Escape or Cancel closes it and changes
  nothing; focus returns to the control that opened it, or, when that row is
  gone, to the table's heading. Confirming sends the table form's POST with
  the chosen rows, without a page load: the confirm button says it is working
  and the dialog cannot be dismissed until the answer arrives. When the
  server records a configuration change, the page follows that change's status
  until it is applied; when it acts at once (Hosted files, Automation access)
  it redirects back to the page. Either way the page then redraws every
  region from the table's own address (keeping its sort and page) and closes
  the dialog, announcing what was removed. A
  refusal, or a change that failed to apply, is shown inside the dialog, below
  its buttons so they never move, and nothing is redrawn; the server's
  explanation is shown when it gives one. A page-wide `confirm()` is never
  used.
- **Several rows.** A table that allows acting on several rows has the
  [selection column and bulk bar](#admin-tables) above; its only bulk action
  is usually Delete selected, which opens the same dialog naming the count.
  Rows without actions have no selection box. Several bulk buttons may share
  one dialog, each setting its title and text. A bulk action that applies
  only to one kind of row is a [prerequisite gate](#prerequisite-gates): it
  is available only while every ticked row is of its kind, and the bulk
  bar's reason line says why otherwise (Administrator, 2026-10-13,
  [#1131](https://github.com/epiphany40223/parishkit/issues/1131)). A row may
  be of several kinds at once (a census change whose Review offers both
  Approve and Ignore,
  [#528](https://github.com/epiphany40223/parishkit/issues/528)); a button
  then fits it when its kind is one of them. On a page whose view travels
  only in POST bodies, the dialog posts with the view's fields to the page
  itself and redraws from that answer, since its address would show the
  default view. A dialog
  may hold fields, such as an
  optional note applied to each row; its confirm button then waits for them
  as a form's Save does, and they return to their defaults each time it
  opens. An action that records rather than removes uses an ordinary
  primary confirm button.
- **Server checks** are those of the action itself: the dialog only asks, and
  the server validates every chosen row and refuses the whole request when
  any one cannot be acted on.
- **Row links** (Administrator decision, 2026-10-11,
  [#1065](https://github.com/epiphany40223/parishkit/issues/1065)) are icon
  buttons that only read: they filter the same table or open a related
  page, and never change anything, so they never confirm. They have the
  Actions column's height and look (`icon-button`, drawn by
  `components/action-icon.html`), may be narrower, and sit in a column of
  their own, not the last one, so no table mistakes them for Edit or
  Delete. Each kind keeps a fixed slot, so the icons line up down the
  column and an absent one leaves its slot empty; a row shows an icon only
  when it has something to link to. Each has an accessible name and a
  matching tooltip saying what it shows ("Show entries by the same actor",
  "Open this task"), and the page's help says what each icon means. A
  filtering link is an
  [in-place](#in-place-controls) read whose identifiers travel only in a
  POST body, and focus returns to the same row's icon after it filters. So
  far only [System logs](../admin-portal/spec.md#logs) has them: related
  entries, same actor, same subject, same campaign and the task's page.

### Table column order

**Rule.** Similar tables put their columns in the same order:

1. the selection checkbox, when the table has bulk actions;
2. the most relevant column first: the Family name for a Family worklist or
   report, the Member name for Member rows, the thing's name or address for
   a configuration or account table (the
   [Users table](../admin-portal/spec.md#users-table): the email address), and
   the event time only for tables whose rows are events or log entries;
3. the Family name (the row header), then **Family DUID** in its own column;
4. the envelope number, when shown;
5. the Member name, then **Member DUID** in its own column next to it, on the
   pages listed below;
6. the Ministry name (never a Ministry DUID);
7. everything else, including dates that are not the most relevant column;
8. actions last.

**Which DUIDs show.** The Admin portal and its downloads show only two kinds
of ParishSoft DUID:

- **Family DUID:** wherever a Family is named, in its own column; staff
  search and match Families by it.
- **Member DUID:** only where staff act on a specific Member in ParishSoft:
  Census changes, Roster changes to enter, the follow-up queue on Ministry
  requests and its Export these requests, the host's join and leave lists
  and the packets, the Limitations report, Families the form cannot open, and the
  Users page's ParishSoft Ministry leaders table and its import review
  (Administrator, 2026-10-12, so staff can find each leader in ParishSoft).
  Elsewhere a Member is named without it.

A Ministry, fund, WorkGroup, role or any other ParishSoft record is
identified by its name, never its DUID: no column, inline "(DUID n)", detail
line, picker label, help sentence or download column shows one. Where two
Ministries can share a name in one list, a detail ParishSoft users recognise
tells them apart, not the DUID. Two Ministries with the same name keep the
same label (System logs' Ministry menu, for one), since the DUID is never
the tie-breaker; there is no "(inactive)" mark any more (#1267). A saved Ministry or fund the
current ParishSoft data no longer has shows its name from the latest
promoted ParishSoft data that still held it, and that it is no longer in
ParishSoft. Internal identifiers stay in URLs,
form values and hidden fields, which are not shown. The command line, its
JSON output and audit and log entries keep DUIDs: operators and scripts
match on them, and the command line is host-only
([Admin automation](../admin-automation/spec.md)). The one display fallback
left is the shared "Ministry n" or "Fund n" label for a record whose
ParishSoft name is blank.

A DUID never shares a cell with a name. When a table has both a Family name
column and a DUID column, both sort. **Why.** ParishSoft users never see
Ministry, fund or other record DUIDs in ParishSoft, so those numbers mean
nothing to staff; Family and Member DUIDs are how staff find a record there.
A combined cell cannot be sorted or scanned by DUID, reads badly with a
screen reader and copies badly into a spreadsheet; staff moving between
pages should not re-learn each one. **Shared code.**
`test_admin_table_columns.py` refuses a cell that prints a DUID label with a
value (#941), checks that every DUID heading is "Family DUID" or "Member
DUID" and follows the name it identifies, refuses a Ministry, fund or bare
"DUID" label in any Admin template, and allows a Member DUID only in the
templates of the pages listed above. **Precedent.** #932 (Administrator,
2026-10-09, corrected 2026-10-10 to "most relevant column first", and the
2026-10-10 rule that the Family name and DUID columns both sort), #941, #950,
and [#1123](https://github.com/epiphany40223/parishkit/issues/1123)
(Administrator, 2026-10-12: no DUIDs but the Family DUID and, where staff
act on a Member, the Member DUID).

The guard test reads every Admin template, components included. In each
`<td>` and each `<th>` whose attributes include `scope="row"`, it refuses a
DUID label followed by a printed value, and a printed `*duid` variable
beside any other printed value (values inside tag attributes or `<option>`
choices do not count). It allows only the tables not yet split, which it
lists by name with the exact number of such cells each holds. Every DUID
column heading and every cell that holds only a DUID carries the shared
`numeric` class, so DUIDs line up as numbers wherever a table aligns them; a
cell that holds words instead (such as "New Member" with its note) does not.
A guard test in the same file checks every Admin template for this: a DUID
sort heading passes `numeric`, and a plain DUID heading or a cell that prints
only a DUID has it in its `class`.

The same file also reads every table's column headings: each heading that
names a DUID is one of the two above and comes right after its name's
heading (Family or Member; Census changes heads its Member column Who). The
one DUID column apart from its name is the recorded exception below.

The follow-up queue on Ministry requests also has no Submitted column (Administrator
decision, 2026-10-11,
[#1064](https://github.com/epiphany40223/parishkit/issues/1064)), so its
one-line rows take less width; the request page shows the submission time
([Follow-up workflows](../admin-portal/spec.md#follow-up-workflows)).

Recorded exception: Families the form cannot open shows a Member DUID with
no Member name on purpose: the name itself may be the value the form
refuses, and that page never shows one
([System health page](../admin-portal/spec.md#system-health-page)).

The three report tables an installed SQL selection orders
([Admin tables](#admin-tables)) sort their DUID columns through that
selection: Financial stewardship detail and the Additional information queue
(Family DUID), and the Ministry report (Member DUID in one Ministry's view)
([#960](https://github.com/epiphany40223/parishkit/issues/960)).

### Family and Member names

**Rule.** Everywhere a table shows a Family's name, it is the Family's heads
of household with the surname first ("Squyres, Tracy and Jeff"), so the
column sorts by surname, built by the shared helper rather than by each page.
A Member's name is shown with that Member's DUID beside it on the pages
[Table column order](#table-column-order) lists. **Why.** The
surname alone does not tell two Families apart, and the free-text mailing
name is sometimes one person's given name. **Shared code.**
`family_heads_name` in `source/family_names.py` (the rule); for a set of
DUIDs, `snapshot_family_names` in `source/snapshot_names.py`; the SQL
equivalent in `schema/directory_reports.sql` for the directory's search and
order. Migration 0045 adds the shared SQL functions the report selections
use, each matching its Python counterpart, Unicode trim included:
`stewardship_name_strip_v1` (`str.strip()`), `stewardship_family_surname_v1`
(`family_display_name`), `stewardship_family_head_part_v1` and
`stewardship_family_heads_name_v1` (`family_heads_name`), and the lookup
`stewardship_family_display_name_v1` (`snapshot_family_names` for one
Family); later steps of
[#932](https://github.com/epiphany40223/parishkit/issues/932) move the other
SQL copies onto them. Salutations use `heads_salutation_name` ("Tracy and Jeff Squyres")
from the same module. Where a table sorts in the database, the
`SnapshotFamilyName` ORM expression (open #965, in
`source/snapshot_name_sql.py`) builds the same name per row, so the sort
orders by surname, then the whole name. **Precedent.** #471, #932 (Administrator, 2026-10-10), open #965.

A Member's own name is shown "Last, first" ("Smith, Ann"), so a column of
Member or person names sorts by surname too (Administrator, 2026-10-10, on
[#952](https://github.com/epiphany40223/parishkit/issues/952)); first used by
the Users page's
[leaders table](../admin-portal/spec.md#parishsoft-ministry-leaders-without-a-user)
and [user Names](../admin-portal/spec.md#name-from-parishsoft) (#963), then by
the [Ministry follow-up](../admin-portal/spec.md#follow-up-workflows) queue
(migration 0069, #932).

Recorded exception: System health's "Families the form cannot open" shows the
Family's surname only (from the shared `family_display_name`), never the
heads' names. The refused field can itself be a head's name, and that page
never shows Member values (open #965).

A Member or Family that the current ParishSoft data no longer has, or marks
inactive or deceased, is named and marked as
[the next convention](#members-and-families-no-longer-in-parishsoft) says.

### Members and Families no longer in ParishSoft

**Rule.** Every page and download says one thing, in the same words and with
the same look, when the current ParishSoft data no longer has a Member or
Family, or marks one inactive or deceased:

- **Not in ParishSoft**: the data has no such Member or Family (deleted or
  merged there), or the Member now belongs to another Family.
- **Inactive in ParishSoft**: present, but marked inactive (for a Family:
  no longer active and registered).
- **Deceased in ParishSoft**: present, but marked deceased.
- **Name not recorded in ParishSoft**: present, but with no usable name. This
  is a gap in the data, not a removal, so it is worded as the name and has
  no mark.

The mark is the shared status badge. It sits beside the name, which stays in
its cell; it never takes the place of a name that is known, so it never sorts
among the names. Only when no name is known (the data no longer has the
Member or Family) is the badge alone in the name cell, and the DUID beside
it identifies the row. The badge's visible text is the whole phrase, so it
needs no tooltip, and screen readers read the same words. A download splits
the name cell into its parts, as [downloaded files](#downloaded-files)
allows: the name stays in its column (blank when no name is known) and a
**ParishSoft status** column right after it holds the same words, blank when
there is nothing to mark. A file whose Family is a report detail rather than
a column, such as the Family timeline's, has a ParishSoft status detail
instead. Older phrases ("Unavailable Member", "Unavailable Family", "Not in
the latest ParishSoft data", "Not in current ParishSoft data", "Name not in
the current ParishSoft data", "No longer active in ParishSoft") are
retired. This convention is about the
ParishSoft data only; a Family's own report on the form ("No longer in this
household", deceased) is the Family's answer and is worded where it is
shown.

**Why.** Pages had six different phrases for the same fact, some looking
exactly like a real name, so Staff could not tell a missing Member from one
named "Unavailable" and could not tell whether two pages meant the same thing
(Administrator, 2026-10-13).

**Shared code.** The phrases and `source_status_label(status)` in
`source/source_status.py`; the `{% source_status status %}` template tag in
`accounts/templatetags/stewardship.py`, which renders the `status-badge`
(nothing for a current Member or Family); the downloads' `STATUS_HEADING`
and `family_status(name, active=...)`, which reads a Family row's key. The
status keys are those of the SQL `stewardship_member_source_status_v1`
(#1217): `current`,
`missing`, `moved`, `inactive` and `deceased`. A selection that builds a
name in installed SQL gives no name (NULL) for a Member or Family the data
no longer has, and spells "Name not recorded in ParishSoft" as the Python
constant does. A guard test keeps the retired phrases out of templates,
Python and the SQL baseline files, and a PostgreSQL test out of every
installed function body.

**Precedent.** [#1216](https://github.com/epiphany40223/parishkit/issues/1216)
(Administrator, 2026-10-13, on #1131). Family pages use it since #1216's
first change, and the Family-code directory, Census changes and Family
timeline downloads have the ParishSoft status column (or detail) since its
second. The Family timeline, Outgoing mail, Refused addresses, Testing
submissions, the chosen-Family send page and "Families on the form now"
word a blank surname "Name not recorded in ParishSoft" since that second
change. Since its third (frozen migration 0094), the installed selections
do too: the follow-up queue on Ministry requests, its request page and Roster changes
to enter mark a Member with the shared badge (no longer "Unavailable
Member"); the Financial, Limitations and Additional information reports
mark a Family the data no longer has (no longer "Unavailable Family"), and
the Financial and Additional information reports an inactive one, with a
ParishSoft status column in their downloads; and the Family codes
directory, the information reports and the weekly report no longer stand
"Family" in for a blank name. Packets and Ministry requests list only
current Members (#1217), so they have nothing to mark. The weekly report is
an email with no badge, so it names a Family the data no longer has "Not
in ParishSoft".

### Table fit and row height

**Rule.** A table fits the page at laptop width; a table that cannot scrolls
sideways inside its own box, never the whole page. Rows stay short: a cell
holds one value, long text wraps within a sensible width, and detail that
makes rows several lines tall belongs on the item's own page or in a
disclosure. Dates and times in cells use the compact
[date format](#date-format). **Why.** A page that scrolls sideways hides its
own controls; tall rows make a table unscannable. **Shared code.** The `table-scroll` class in
`ui-v1.css`; the button-label browser test at 320 px and 1280 px.
**Precedent.** #614, #952 (Portal users' rows wrapping to several lines).

Recorded exception: the Users page keeps every row to one line (the
Administrator asked for one-line rows on #952). A long email or Name is cut
off with "…"; the full value stays the cell's text, appears in a `title`
tooltip, and is shown whole on the user's Edit page
([Users table](../admin-portal/spec.md#users-table), #952, #963).
The follow-up queue on Ministry requests does the same for a long Member name, with the
shared `cell-ellipsis` class
([Follow-up workflows](../admin-portal/spec.md#follow-up-workflows), #932).
Its Ministry name, which a leader scans to tell similar Ministries apart,
wraps instead to at most two lines (`cell-clamp`), so a name as long as any
in use shows whole; only a name too long even for two lines ends in "…"
([#1136](https://github.com/epiphany40223/parishkit/issues/1136)). In the
queue a name cut short shows whole in a tip on hover, keyboard focus and
tap (a cut Ministry name joins the tab order for that), not in a bare
`title`, which keyboard and touch cannot reach; the whole name stays the
cell's text for screen readers. Since
[#1064](https://github.com/epiphany40223/parishkit/issues/1064) the queue's
other cells do not wrap, so a row is one line, or two where a Ministry name
wraps, and the table scrolls sideways in its own box when it is wider than
the page.

### Empty tables and lists

**Rule.** A table or list with nothing to show says, in plain words, why it
is empty and what the reader can do next, never only that it is empty ("No
users.", "No matching records."). The first sentence says why, in the
reader's terms ("No Ministries match these filters."); the second names the
next step by its visible label: the filter to widen or clear ("Clear Search
by name, or choose All under Activity"), the button that adds the first item
("Select New user to add one"), or when items appear by themselves ("Each
send appears here as soon as it starts"). Where a filter could be the cause,
the filtered and unfiltered cases have their own messages, since "clear the
filters" does not help a list that is empty for another reason. An empty
list that is good news or needs nothing done says so ("That is good news: no
address is being skipped"). A table keeps its caption and headings and puts
the message in one row spanning every column, and a list puts it in its one
item, so a filter that empties the table and one that fills it again move
nothing ([no layout shift](#no-layout-shift)). **Why.** "No users." leaves a
mid-level IT administrator guessing whether something is broken, filtered
out or not set up yet. **Shared code.** The `{% for %}…{% empty %}` row or
item in each template; `components/table.html` shows its caller's
`table_empty` message. `tests/stewardship/test_admin_empty_states.py` checks
that every such message in an Admin template has a reason or next step after
its first sentence; its allowlist names the pages still to convert and two
reviewed exceptions: the "None" value of a before-and-after review list, and
the shared chart's "Nothing to show yet." row, which sits in the collapsed
Exact values table under a chart whose visible summary and page say what it
covers.
Empty states drawn by `{% if %}…{% else %}` follow the same rule and are
reviewed by hand. **Precedent.** Outgoing mail, Background work and Hosted
files' empty rows;
[#227](https://github.com/epiphany40223/parishkit/issues/227).

## Downloaded files

**Rule.** Every format of a download (CSV, XLSX and PDF) carries the same
columns, and those columns match its page: the same columns, names and order.
Internal references and identifiers (row versions, campaign, source, item and
response reference UUIDs, record ids and similar) are left out of every
format, CSV included (Administrator decision, 2026-10-10,
[#956](https://github.com/epiphany40223/parishkit/issues/956)). Every Admin
PDF and XLSX shares one design in the portal's design tokens; a PDF lays the
columns out as one table, record cards, address cards or an infographic
sheet (the [Family timeline](../reports/spec.md#file-design)) in a shared
page frame. Identifiers a reader uses, such as Family and Member DUIDs and
envelope numbers, are not internal references and stay. **Why.** Staff print
and share these files; internal references mean nothing to a reader, and a
column that is in one format and not another makes the files disagree about
what the report holds. Troubleshooting uses the page's Technical details and
the audit log, not a download; the recorded exceptions are the System logs
export and the command line's Census changes JSON Lines below. Every data export is offered as CSV, XLSX and PDF unless a
recorded technical or design reason says otherwise (Administrator decision,
2026-10-10); a page may add a format, such as System logs' JSON Lines. The
Response dashboard's export drops the participation chart's PNG
([#1537](https://github.com/epiphany40223/parishkit/issues/1537)): its page
holds more than one chart, and the PNG is still accepted from the command
line. An export form is its format menu and its
button; it carries no explanation of how each format writes dates or money
(Administrator decision, 2026-10-17,
[#1538](https://github.com/epiphany40223/parishkit/issues/1538)). **Shared code.** `reports/pdf_design.py` and
`reports/xlsx_design.py`; `web/design_tokens.py`. The format rules for each file type (CSV neutralizing,
money, headers) are in the reports'
[shared report behavior](../reports/spec.md#shared-report-behavior).
**Precedent.** #926, #929, and the Administrator's decisions of 2026-10-10
on [#958](https://github.com/epiphany40223/parishkit/pull/958).

Export forms and export status pages carry no sensitive-data banner
(Administrator decision, 2026-10-17,
[#1539](https://github.com/epiphany40223/parishkit/issues/1539)). A sentence
that says what the file holds or leaves out (for example, that a Family
link's private email-link part is not included) stays as plain help in the
export form. The privacy line inside each downloaded file stays: it travels
with the file to readers who never saw the page
([file design](../reports/spec.md#file-design)). **Why.** The banner was a
reminder that gated nothing; the protections are role checks, `no-store`,
download windows and audit records.

A download may split one combined page cell into its parts, because a
spreadsheet needs one value per cell to sort, filter or merge on: the parts
take the cell's place, in the order the page shows them, each headed with the
page's own words for it (the cell's heading or the label the page shows
beside that part). The Ministry report's lists were the first case: the page's
"Status and outcome" cell was Status and Outcome in the files, and "Contact and
demographics" was Gender, Age, Email, Phones and Known primary Family address
(Administrator decision, 2026-10-11, on
[#932](https://github.com/epiphany40223/parishkit/issues/932)). Those lists
were retired (#1157); Ministry requests' Export these requests splits its
Status cell ("Resolved: Joined the Ministry") into Status and Outcome the
same way, and a Family cell with its
[ParishSoft status](#members-and-families-no-longer-in-parishsoft) badge is
Family and ParishSoft status (#1216).

Recorded exceptions to the rule above:

- **System logs:** CSV, XLSX and JSON Lines, no PDF. Its rows are wide raw
  data (structured details up to the export limit) that do not print as a
  readable page. It is also the one exception to leaving out internal
  identifiers: every format keeps the log entries' cross-link identifiers
  (request, correlation and subject IDs), because the export is itself the
  troubleshooting record and only the Administrator can download it
  (Administrator decision, 2026-10-11, on
  [#966](https://github.com/epiphany40223/parishkit/issues/966)). Its CSV
  and XLSX headings are still plain names; JSON Lines
  keeps program keys.
- **Ministry requests, Export these requests:** the queue's columns, then
  each request's contact details from its request page (Email, Phones, Known
  primary Family address, Gender, Age, Current Ministry role) and Submitted.
  The contact details are what the export is for: they replaced the retired
  Members joining and leaving exports, and the queue keeps them to each
  request's page so its rows stay one line (Administrator decision,
  2026-10-12, on
  [#1157](https://github.com/epiphany40223/parishkit/issues/1157);
  [follow-up workflows](../admin-portal/spec.md#follow-up-workflows)).
- **Postal mail merge PDF:** a sheet of address labels holding the mailing
  columns (addressee, address, Family, DUID and Family code), not the
  Family heads and head emails its CSV and XLSX carry. Labels are what the
  PDF is printed for; the CSV is the word processor's merge source
  (Administrator decision, 2026-10-11, on #966).
- **Census changes JSON Lines (command line only):** `pk-admin export
  census --format jsonl`
  ([#463](https://github.com/epiphany40223/parishkit/issues/463)) keeps
  each change's `proposal_id` and `version`. They are not personal data;
  Administrators and Staff already receive them in the page's hidden
  Record outcome fields; and `census resolve` needs them to name the change
  and its version. The format is offered only on the command line, not on
  the page, whose CSV, XLSX and PDF follow the rule above. Pending
  Administrator confirmation.

Not data exports, so outside the rule: the deployment configuration
document, hosted files (served as uploaded) and chart images.

**Time zone question.** A download asks for a time zone only when its rows
hold exact times of actions: submissions, log entries, staff actions,
deliveries. Any other download (campaign dates, contact dates, no times at
all) asks nothing: its form sends the browser's zone in a hidden
`data-browser-zone` field, which sets the file's frame times (Captured at,
Requested at, Source as of), and the file names that zone. The one exception
is the Response dashboard: its export form posts the page's own `query.timezone`, the
zone the page was drawn in, which `report-v1.js` fills with the browser's
(the [participation graph](../reports/spec.md#participation-graph)). The
server falls back to the parish's time zone rather than refusing a missing or
unknown zone there, since the reader was never asked. **Why.** A zone question beside a file with no times
in it is a choice that changes nothing the reader cares about (Administrator
feedback, 2026-10-12). Log and timeline exports keep the choice because they
are shared and compared against records kept in other zones. **Shared
code.** `web.dates.unasked_zone` and `web.dates.parish_zone`; the `test_export_time_zones.py` guard lists
which forms ask. **Precedent.**
[#1127](https://github.com/epiphany40223/parishkit/issues/1127).

Asking: System logs, Response lists, Financial stewardship, Limitations,
Census changes, Additional information, Ministry requests' Export these
requests (which replaced one Ministry's joining or leaving lists, #1157),
Roster changes to enter, the Family timeline, and the Active parishioner
family directory code list only while its response columns are included
(the field comes and
goes with that applied choice, as the last field of its row, so nothing
moves). Not asking: the Ministry follow-up packet, the participation exports,
the Campaign comparison downloads
([#1161](https://github.com/epiphany40223/parishkit/issues/1161)), the
directory code list without response columns, the postal mail merge, and
the Family test names file (`test families-preview --names`, which uses the
parish's zone unless `--timezone` is given, as a host command has no
browser).

**Default format.** Every data export's format menu offers XLSX first and
preselects it; the other formats it offers (CSV, PDF, and a page's own such as
PNG or JSON Lines) follow, so CSV stays available wherever it was. A download
or `pk-admin` command posted or run without a format produces XLSX too.
Document downloads keep their own first choice: the Campaign comparison's PDF
summary and the Ministry follow-up packet, which are read rather than sorted.
**Why.** Most readers open the file in a spreadsheet, and one rule for every
form means no page picks its own default (Administrator request, 2026-10-12).
**Shared code.** `web.exports.DEFAULT_EXPORT_FORMAT`, the `export_format_options`
template tag (which orders and selects the options, and with `disabled=` shows
one offered format as unavailable, never selected), and
`admin_cli.DEFAULT_EXPORT_FORMAT` (spelled out, kept equal by a test);
`tests/stewardship/test_export_default_format.py` guards every format menu.
**Precedent.** [#1190](https://github.com/epiphany40223/parishkit/issues/1190).

Current state: since the
[#966](https://github.com/epiphany40223/parishkit/issues/966) column sweep,
every export's CSV, XLSX and PDF carry one column set with no internal
references (`tests/stewardship/test_export_columns.py` guards it), except the
recorded exceptions above. A download written straight from its page registers
with `web.exports.page_download`; the guard finds every registered and routed
one, and fails until a new one has a case there; a case without a PDF table
must be one of the exceptions above (or the participation chart). The
Limitations report (formerly Talents and limitations,
[#970](https://github.com/epiphany40223/parishkit/issues/970)) offers all
three formats; the guard checks its Members and Families tables in each.

### Point-in-time downloads

**Rule.** A download is a point-in-time copy of what the report shows when
it is asked for; the portal keeps no copies and no history of earlier
downloads, and no page lists them. A download made in the background (an
export) has a status page only for its short life: it says the file is
being prepared, starts the download once when the file is ready, offers
Cancel while preparing and Retry after a failed run (until the export
ends), and says until when the file can be downloaded again, in
[browser-local time](#dates-and-times): the window's computed end, never a
later recorded `expires_at`. There is no Regenerate. Once the
export has ended (its window is over, it was cancelled, or a failed run
can no longer be retried; see
[point-in-time exports](../operations/spec.md#point-in-time-exports)), its
status page and its download answer one fixed page, "This export is no
longer available. Export it again from _report_.", whose link returns to
the report the export came from. The page answers 410 Gone, not 404, since
an open tab or the Back button still lands there. Its checks run in this
order:

1. the existing owner and capability check, so another user's export or an
   unknown request id gets the existing denial or 404 and learns nothing of
   the export or its report;
2. the [not-current-campaign](#addresses-and-legacy-urls) 410;
3. this page.

When a ready file's window ends while its status page is open, Download
becomes unavailable in place, with no reload, and the "until" line, which
sits directly below Download with nothing between them, says instead "This
download expired at _time_. Export it again from _report_.", in
browser-local time, with the report linked
([prerequisite gates](#prerequisite-gates)). The line keeps the height of
the longer of its two sentences from the start, so nothing moves
([no layout shift](#no-layout-shift)). The page counts to the end by the
server's clock, and checks again when it is shown again (such as by Back)
and whenever its status is redrawn, so a copy shown at or after the end
shows Download unavailable at once. The server stays authoritative: after
the end, the page's next load, Refresh status, Download or Retry answers
the fixed page, as the in-place controls show any other refusal, so the
server never draws the status page in that state. A failed export's Retry
is not held this way; its "Retry is available until" time says when it
ends. The window's length and setting,
and what is deleted when an export ends, are the
[point-in-time exports](../operations/spec.md#point-in-time-exports) rule.
**Why.** The downloads are a point-in-time function: keeping their own
copies is up to the people who downloaded them (the Administrator,
2026-10-15). **Precedent.**
[#1398](https://github.com/epiphany40223/parishkit/issues/1398).

## Family portal

**Rule.** The Family portal is mobile-first, is not behind the Admin
JavaScript gate (where its flow needs script, an unsupported browser gets a
plain notice rather than a broken page), and keeps every link it has emailed
working. It shares the theme and design tokens with the Admin portal but
keeps its own spacing and stays light (no
[dark colour scheme](#colour-scheme)), and the Admin-only conventions above
(in-place controls, the JavaScript gate, Admin tables) do not apply to it. **Why.** Families use
whatever device and browser they have, and an emailed link cannot be
re-sent. **Shared code.** `family-v1.js`, `ui-v1.css`, `web/design_tokens.py`.
**Precedent.** #466, #565, #864. The Family flow's own behavior is in the
[Family portal specification](../parishioner-portal/spec.md).
