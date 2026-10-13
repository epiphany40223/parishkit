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
  [charts](#charts).
- Admin interaction: [JavaScript requirement](#javascript-requirement),
  [in-place controls](#in-place-controls),
  [no layout shift](#no-layout-shift),
  [prerequisite gates](#prerequisite-gates),
  [field errors and live checks](#field-errors-and-live-checks),
  [Review, Apply and Change status](#review-apply-and-change-status),
  [review dialog](#review-dialog),
  [conditional fields](#conditional-fields),
  [Basic and Advanced modes](#basic-and-advanced-modes),
  [single-campaign interim](#single-campaign-interim),
  [addresses and legacy URLs](#addresses-and-legacy-urls),
  [not-found pages](#not-found-pages),
  [unavailable menu pages](#unavailable-menu-pages).
- Admin page layout: [page help](#page-help),
  [button labels](#button-labels), [shared visual style](#shared-visual-style),
  [time entry](#time-entry).
- Tables: [Admin tables](#admin-tables),
  [row actions](#row-actions-and-confirmation),
  [column order](#table-column-order),
  [Family and Member names](#family-and-member-names),
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
to think in the campaign's or the parish's zone. Exceptions: the ParishSoft
refresh times stay in the parish's time zone (permanent, below); and, as of
2026-10-10, two older screens still take send times in the campaign's time
zone, the first-campaign step's Mail schedules and the date-change review,
until they are fixed under
[#558](https://github.com/epiphany40223/parishkit/issues/558) (Administrator
decision, 2026-10-10). **Why.** Staff read
and type their own wall-clock time; a campaign-zone or UTC time on a page is a
conversion the reader has to do in their head. **Shared code.**
`data-local-instant` elements filled by `date-format-v1.js`;
`web/dates.py` and the `parish_date` and `parish_time` template filters
(`accounts/templatetags/stewardship.py`); the hidden
browser-zone field (`data-browser-zone`) that `ui-v1.js` fills; the
`test_browser_local_times.py` guard. **Precedent.** #558 (Administrator
decision, 2026-10-04).

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
reports none the page keeps Save unavailable, saying why. A time that occurs
twice when clocks fall back is the first occurrence; a time skipped when clocks
spring forward is read with the offset in force before the change (2:30 AM
becomes 3:30 AM daylight time), so the form is never refused for it. Emails,
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
fourth ([Reports](../reports/spec.md#additional-information)).
Until a page's PR lands, it keeps its current zone, including the campaign zone
for the other schedule pages, campaign dates and report day buckets (the
participation chart still buckets by campaign time). One field is a recorded
exception that stays in the parish's time zone after every #558 PR: the
ParishSoft full-refresh times (Administrator decision). A refresh schedule is
a recurring wall-clock schedule: the parish's own daylight-saving changes
decide when each refresh runs and when the reminders it avoids are due, and a
time entered in another zone and converted with today's offset would move
against the parish's clock twice a year when the two zones change on
different dates. The settings page says the times are parish-local, and the
[time entry](#time-entry) reads them as it does any
other. This is decision 18 of the
[refresh schedule plan](../../../plans/stewardship/refresh-schedule.md#open-decisions),
which designs the whole refresh schedule. Local-day conversion must
handle daylight-saving gaps and folds without running an occurrence twice.

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
renderer supports it; every chart has an equivalent data table.

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

Client validation improves feedback but never replaces server validation.
Browser-local timezone conversion uses UTC ISO timestamps supplied by the
server. The Admin portal [requires
JavaScript](#javascript-requirement) and shows a plain
notice without it. The Family multi-step flow may require JavaScript but must
show a clear supported-browser message rather than silently fail. A browser
too old for the Family flow's JavaScript likewise gets a plain notice asking
the Family to update the device's software or use another device or browser; a
small ES5 feature check reveals it and never alters the form.

### Charts

**Shared code.** `chart-v1.js` and `chart-v1.css`, described with the
[chart engine](../reports/spec.md#chart-engine).

All charts have title, legend, labeled axes with units, accessible color/line
patterns, hover/focus values, and equivalent data tables. They download as PNG
or PDF. Dollar/count series on one chart use separate labeled axes rather than
comparing unlike units on one scale.

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

- **Regions.** A page marks each part a control can change as a region, an
  element with a stable id (a table's region, or `data-in-place-region`). The
  id is also the control's URL fragment, which is required: the script finds
  the region by it, and an ordinary load lands on the region instead of at
  the top. The fragment must name a region in the same template as the
  control (not one a base or included template draws); a template test
  refuses an in-place control whose fragment names no region there.
- **Controls.** A link that shows another view of the page (`a[data-in-place]`:
  the [response dashboard](../reports/spec.md#response-dashboard)'s mode and
  grain, the [response lists](../reports/spec.md#response-lists)' mode, the
  [Family timeline](../reports/spec.md#family-timeline)'s mode and When sort,
  "Refresh current work" on "Background work", "Refresh list" on
  "Families on the form now", the page links of a mail delivery's
  evidence and attempt history, an information item's and a Ministry
  follow-up request's history, and a weekly information report, and the
  Refresh links of the key replacement status, Family email progress and
  Family test pages) or a form whose answer is the page again
  (`form[data-in-place]`, such as a POST whose server redirects back to the
  page: Save follow-up on an information item and on a
  [Ministry follow-up request](../admin-portal/spec.md#follow-up-workflows); System logs'
  cross-links, whose answer is the filtered page; Acknowledge on a security
  event; Clear verified refusal on a
  [refused address](../admin-portal/spec.md#refused-addresses); Dismiss on an
  integration's finished key change; the
  [participation report](../reports/spec.md#campaign-statistics)'s Apply
  report options, which refreshes its statistics, chart and export panels;
  the [Campaign comparison](../campaign-history/spec.md#page-layout)'s
  Compare on day, which refreshes everything below it)
  names its region by its
  URL's fragment. A POST form saves a change unless it is marked
  `data-in-place-read` (the System logs cross-links only read): a read may
  be cancelled by a newer choice and, with no answer at all, falls back to
  the ordinary submission. A form or link marked `data-in-place-filters`
  sets the page's filters from outside its filter form (a response list's
  mode link clears its private search), so after the swap the filter
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
  but not yet used, an open "Technical details" and the chart's inspected
  date return to their defaults, as an ordinary load would leave them. A checkbox marked
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
  region ids to replace as well: a settings page's Apply
  (`data-in-place-only="flow-steps"`) also moves the step indicator. A
  region the answer itself marks `data-in-place-always` is replaced even
  then. One request is in flight at a time: a newer choice cancels an older
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
  not step through choices.
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
  the server did not accept the change when there is no summary. Any other
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
  link also starts leaving the page and the page stays.
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
  status region. A Refresh reads out the region's coarse status sentence
  when it changed, as a poll does. Dismiss refreshes only an integration's
  status line: if it brings back a new key change in progress (another
  Administrator started one), the settings form and notes below it keep the
  state the page was loaded with until the next load.
- **In-place review (#532, #750).** This panel is being replaced by the
  [review dialog](#review-dialog) (Administrator decision, 2026-10-11); it
  describes the pages that still use it until #750 converts them.
  Parish settings, Campaign settings, Share options and Campaign
  Ministries review, apply and follow a change on the page itself, not on
  separate Review and Change status pages. The settings form is a
  `form[data-in-place]` marked
  `data-table-sync`: its answer fills the review region under it, while the
  form keeps the reader's typing and every script bound to it and takes only
  its hidden fields (the version it edits) from the answer. The review shows
  what the review page showed (each changed setting with its current and
  proposed value, and the change's notes) and Apply, which posts the same
  signed preview; its heading (`data-in-place-focus`) takes focus. Editing
  the form after a review, or while its Review is in flight, withdraws the
  review (`data-review-of`), so Apply never applies values other than those
  shown: Apply is removed, the rest stays in place greyed out at no less
  than its height (so nothing moves under the pointer), and its last line
  says to review again. Confirmation answers with the
  page again, naming the request (`?request=<id>#settings-review`), and the
  region shows the change's live status, polled from Change status's passive
  read (named with `in_place`, which only the in-place pages may be). Once the
  change is applied, that read carries a hidden follow-up link marked
  `data-in-place` and `data-in-place-quiet`, which the status script clicks,
  so the page refreshes in place without moving focus and the form starts
  from the applied version; reading a change's status this way never renews
  idle time. A change that was not applied leaves the page alone. A Review
  made while an earlier change is still being applied is checked against the
  settings that change replaces, so once the earlier change applies, Apply
  of the later one is refused when it runs (`stale_base`) and its status
  says to start again from the current settings; nothing is changed. A
  refused review (invalid values, nothing changed, a page changed elsewhere)
  or a refused Apply (an out-of-date preview) is the page again with an
  error summary in the review region, each field error linked to its field,
  since the form itself is not replaced. A refusal keeps the form at the
  version the reader's values came from (the one posted, or the one the
  refused preview was reviewed at), never the current one, so a page whose
  settings changed elsewhere is refused again until it is reloaded and can
  never quietly propose undoing that change. For the same reason, Apply's
  answer and the status refreshes after it draw the form at the version the
  change was reviewed at until the change is applied, and then at the
  version the change made; a full load of that address (a reload) draws the
  current settings at the current version. A Review the server redirects to
  another page (Campaign settings' dates-only change) loads it without this
  page's region fragment. The step indicator is a region too, so it follows
  Make changes, Review and Apply. The signed preview, optimistic
  concurrency, capability and session rechecks, the durable request and its
  audit are unchanged; a refusal that needs a fresh sign-in still shows its
  page whole. Apply is marked `data-in-place-only="flow-steps"`: its answer
  replaces only the review region and the step indicator. Whenever the
  review region (`data-keep-height`) changes (its own swaps, a withdrawn
  review and Change status's live updates alike), it is held at just the
  height that keeps the page's foot at or below the bottom of the window
  where the reader is: a reader scrolled down to it at the foot of the
  page would otherwise see the form pulled down under the pointer when a
  shorter status (Applied) replaces a longer one. The trade-off is blank
  space below a shorter status for a reader at the foot, which stays until
  the next change to the region (or a reload); a reader higher up the page
  needs little or no hold, so a long review followed by Applied leaves no
  large gap. Apply still moves focus to Change status's heading, so on a
  window too short to show the whole region, focus may scroll the status
  into view. Share options
  (#750) work the same way, except that their form, to which
  no script is bound, is an in-place region of its own: a Review's answer
  redraws it with the values sent and their field errors. Edits typed into
  that form since it was last sent are lost whenever an answer redraws it (a
  Review's, accepted or refused, or the quiet refresh after Apply), so a
  line at the top of the review region, below the form, says they were not
  kept (never above the form, which would move it under the pointer); a
  review that arrives with it matches the redrawn form, so it stays. Apply never redraws the
  form unless its answer finds the change already applied, when it marks the
  form `data-in-place-always` and the form is redrawn at the applied
  version; otherwise a refused Apply keeps the reader's rows at the version
  they were reviewed at (the next Review is refused until a reload), and the
  quiet follow-up refresh draws the applied list. Their Review changes
  waits for a change like the other settings forms (#921), with the hint
  "Change an option to review it."; a form redrawn at the same version keeps
  the saved list it is compared with, and the list an applied change is
  redrawn with (its fresh blank row included) is the saved one. Campaign
  Ministries (#750), a live campaign's Ministry selections, works as Parish
  and Campaign settings do: its form is `data-table-sync`, its review
  (the Ministries to add and remove, with what each touches, and the
  Families with a form open now) fills the review region, and its Review
  changes waits for a change with the hint "Change a Ministry selection to
  review it." Once its campaign can no longer change them (closed, or held by
  background work), an in-place Review, Apply or follow-up says so in the
  review region, beside the change's status when there is one; a plain load
  shows the refusal page. Parish and Campaign settings' and Campaign
  Ministries' forms are never redrawn; until the change is applied, Apply's
  answer hands them the version it was reviewed at.
  [Create the campaign](../admin-portal/spec.md#create-the-campaign) (#142)
  reviews and confirms the same way, except that its status names no
  `in_place` page, so it never refreshes the page once applied. The other
  editors keep their review and Change status pages until issue 750 reaches
  them.
- **In-place review of a table selection (#750).** Ministries reviews,
  applies and follows an activity change in place too, in the review region
  under its table (so a review appearing never moves the table or its bulk
  buttons). Its form is the table's row selection
  (`form#ministry-selection[data-select-table]`), not a settings form:
  Review activation and Review inactivation send the ticked rows, and the
  answer replaces only the review region and the step indicator
  (`data-in-place-only="flow-steps"`), so the ticks, page and sort stay.
  Ticking or unticking a row, the header checkbox, or Select all / Clear
  selection (which ticks rows from script, so it withdraws the review
  itself) withdraws a shown review, whose last line then says to choose
  Review activation or Review inactivation again (the review's
  `data-stale-note`). A selection that would change nothing ("already
  active") is refused in the region, as is an empty or oversized one.
  Paging, sorting or filtering the table draws an empty review region, so
  the review (or a settled status) of the earlier selection goes with it.
  Every redrawn selection table keeps the ticks of the rows it still shows
  (selection is per page, so a row on another page loses its tick). For
  every selection table (Ministries, Hosted files and Schedule settings
  alike), `ui-v1.js` shows the "were not kept" line only when an answer the
  reader did not choose as a table view (the quiet refresh once a change is
  applied) drops a ticked row because it is no longer listed; a re-sort,
  page or filter they chose says nothing. Select all / Clear selection
  withdraws a review on any page that has one. The page's view (its
  filters, sort and page; `admin_editing.IN_PLACE_VIEWS`) is kept in Apply's
  answer, handed through Change status's read and carried by the quiet
  follow-up, so the refreshed table is the one the Administrator was
  looking at.
- **In-place review: No-reminders WorkGroup (#750).** The No-reminders WorkGroup
  editor works as Parish and Campaign settings do: its one name field is a
  `data-table-sync` form whose Review changes waits for a change (hint
  "Change the WorkGroup name to review it."), and its review (the WorkGroup
  now and the one proposed), refusals and the change's status fill the
  review region under it. What the newest ParishSoft refresh found is an
  in-place region of its own, so the quiet refresh once a change is applied
  brings it up to date; it sits below the review region, so a status whose
  length changes never moves the form or the review.
- **In-place review: Campaign images (#750).** Campaign images has no
  settings form: each slot's upload form (`data-in-place`, multipart) and
  its Remove this image link (`a[data-in-place]`) answer with the page
  naming the change (`?review=<slot>&bundle=<id>` or `?remove=<slot>`), whose
  review region under every slot shows the image (or the removal) with
  Apply, so a review appearing never moves a slot. Apply posts to that
  address. The slots are one in-place region, redrawn by an upload's answer
  and by the quiet refresh once a change is applied; a refused upload's
  field error shows in its slot and, linked to that field, in the review
  region, which takes focus. Each slot is the same height with or without
  an image (its image box keeps the slot's image size, its caption is one
  line, and the removal line keeps its space), so the quiet refresh never
  moves the review region below the slots. A stale upload (the page's
  settings changed elsewhere) is refused in the region too; a stale Apply
  says to upload the image again, or for a removal, to choose Remove this
  image again. A refused Apply's answer marks the slots
  `data-in-place-always`, so their forms are redrawn at the current
  version and the next upload is accepted. Loading an address whose change
  can no longer be reviewed (an image already applied, expired, another
  sign-in's or stale; a slot with nothing left to remove) shows the page
  with that explanation in the review region. Each upload form has an id,
  so a file chosen while the quiet refresh redraws it is said not to be
  kept.
- **In-place review: Parish logos (#750).** Parish logos has no settings
  form: its upload form (`data-in-place`, multipart, a region of its own)
  answers with the page naming the staged logos (`?review=<bundle>`), whose
  review region under the form shows every generated size with Apply, which
  posts to that address. The current logos are a region of their own below
  the review region, so the quiet refresh once a change is applied shows
  the new ones without moving the form or the review. A refused upload's
  field error shows in the form and, linked to that field, in the review
  region, which takes focus; a stale upload or Apply is refused there too.
  A refused Apply's answer marks the form and the current logos
  `data-in-place-always`, so both are redrawn at the current version and
  the next upload is accepted. Loading the address of logos that can no
  longer be reviewed (already applied, expired, another sign-in's, or
  staged against settings changed since) shows the page with that
  explanation in the review region. The form has an id, so a file chosen
  while the quiet refresh redraws it is said not to be kept.

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
scrollbar's space ([page help](#page-help)); `data-complete-hint="reserve"`
on a complete-gate hint keeps its one line's height while empty instead of
hiding it (#959); a field that a choice makes inapplicable stays in
place, greyed, rather than disappearing, through `data-enabled-when` (#959)
or `data-locked-when` (open #957) ([conditional
fields](#conditional-fields)). **Precedent.** #736 and its review
(Administrator, 2026-10-08), #563, open #957, #959.

Recorded exception (Administrator, 2026-10-11, on
[#1015](https://github.com/epiphany40223/parishkit/issues/1015)): on the
[Mail message page](../admin-portal/spec.md#mail-message-page), the
attempt history sits above the resolution forms, so paging it with the
upper pager can change its height and move the forms and the evidence list
below it. The Administrator accepted this so the page reads in the order
asked for.

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
the change gate `data-require-change` (#924); the shared `holdButton` /
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
Likewise, an in-place settings form's Review changes button is unavailable
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
not sent. The hint
keeps its space while hidden, so it never moves the page.
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
check signs in as its mailbox user. The server still checks each of these.

The integration settings pages (other than ParishSoft, whose refresh
schedule editor gates Save itself) and both steps of the backup encryption
key change use the complete gate too. While Slack is not set up its token is
required, as the server requires it. The new public key and the code from
`backup-prove` carry a `pattern` the server's rule also admits (the code read
as leniently as the server reads it), and a refusal of either, or of a
missing Slack token or a new ParishSoft key sent with a refresh schedule
change, is shown at that field rather than as a notice or an error page
(#563).

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
exports, follow-up saves, Participation) or because the sign-in is not
recent (automation approval) names that notice too.

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
every Admin refusal summary does (#592).

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
report and System logs filters do; `data-max-bytes` on a file input refuses
a file larger than the limit, or than the room left, at once (Hosted files);
and `data-pattern` gives a textarea, which has no `pattern` of its own, a
rule to match (no "@" in the Family portal maintenance message).
The server stays the authority: it checks every save, and its refusal is
shown at the field as above, even when the browser would have allowed the
value (a wrong computer clock, for example).

A file field marked `data-max-bytes` (with `data-max-bytes-message`, the
server's own words) is refused at the field as soon as a file over that
size is chosen, and the form is not sent until a smaller file is chosen; a
form marked `data-max-total-bytes` refuses files that together exceed it
the same way. The message shows where the server's errors for that field
go, in a reserved line that keeps its height once the error clears and is
announced to screen readers (`file-size-field.html`). A field that a
choice hides drops its refusal, and shows it again if it reappears with
the same file. The reverse proxy refuses an oversized request before the
page could explain it, so this check is what keeps such a refusal on the
form. First use: Parish logos (#1062).

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

### Review, Apply and Change status

**Rule.** A change to configuration or data is made by editing, then
**Review** (which shows each changed value, current and proposed), then
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
then, the #532 in-place review, `data-table-sync` and `data-review-of` in
`ui-v1.js`, [in-place controls](#in-place-controls), and the separate Review
and Change status pages); `data-submit-on-change` for view filters only.
**Precedent.** #750 (review dialog, Administrator decision, 2026-10-11), #532
(in-place review, being replaced), #523
([preview lifetimes](../admin-portal/spec.md#preview-lifetimes)), #882 (New and
Edit pages follow it).

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
modal dialog over the page: **Review changes** opens the dialog with the
change and its impact, the dialog offers **Apply** and **Cancel**, and Apply's
progress and outcome show inside it. Once the change is applied the dialog
closes, the page refreshes in place (or a New or Edit page returns to its
table) and a short status line says what was applied. Review changes is
unavailable until the form holds a real change, so no page ever says that
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
400); any other refusal is the closed JSON refusal. **Precedent.**
Administrator decision, 2026-10-11, on
[#750](https://github.com/epiphany40223/parishkit/issues/750), which replaces
the in-place review panel of #532 and the separate Review and Change status
pages.

An editor is an Admin page whose form changes configuration or saved data
through Review and Apply: a settings page, a list editor, a New or Edit page,
an upload editor, or a table's bulk action that is reviewed before it is
applied. Each feature specification says what its own Review shows; this
section says how every Review behaves.

- **Review is available only for a real change.** Review changes is
  unavailable until the form differs from the saved values, and unavailable
  again when the reader's edits return to them, with the gate's short hint
  after the button ([prerequisite gates](#prerequisite-gates)). A table's
  bulk Review action is likewise unavailable until the selection would change
  something (for example, Review activation while every ticked row is already
  active), with its reason in the bulk bar's reserved line. No page draws a
  "nothing has changed" review, refusal, heading or page. The server still
  refuses an unchanged Review, as a safeguard for requests that bypass the
  page script.
- **Preparing the review.** Review changes posts the form without a page
  load. Until the answer arrives the form is inert and marked busy, and
  Review changes says it is working, so nothing can be edited while the
  review is being prepared.
- **Field errors stay on the form.** A Review refused for the values in the
  form (a required field, a value out of range) does not open the dialog: the
  form shows its error summary and marks each field, as the
  [field errors](#field-errors-and-live-checks) convention says, and becomes
  editable again.
- **Opening the dialog.** An accepted Review opens the dialog. Its title
  names the change ("Review Parish settings") and labels the dialog
  (`aria-labelledby`); focus moves to that title, never to Apply, so the
  reader hears what is being reviewed before reaching the buttons. Its body
  shows the review the feature specification describes: each changed value
  with its current and proposed value, and the change's notes and impact
  (affected Families, counts, a generated preview).
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
  no jump to the top. A New or Edit page (New user, New scheduled email)
  returns instead to the table page it was opened from (Users, Dates and
  mail schedules), as [flows end where the reader
  started](../admin-portal/spec.md#navigation-rules) says. A Review that the
  server answers with another page (Campaign settings' dates-only change,
  which leads to Dates and mail schedules) ends on that page. Wherever the
  reader ends, a short status line under the form or above the table says
  what was applied and when ("Applied at 3:42 PM."), and focus moves to it.
  The line is a polite live region (`role="status"`), so a screen reader
  hears it, and its space is reserved, so it never moves a control ([no
  layout shift](#no-layout-shift)).
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
  **Confirm with Google** and its step-up page, as it does today.
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
  second page ([#456](https://github.com/epiphany40223/parishkit/issues/456)).
  Integrations' other changes use the dialog.

Operational actions that are not editors (going live, Confirm Production,
cancelling go-live, pausing and resuming mail, fixing many emails at once,
test email sends, a manual ParishSoft refresh, Family portal maintenance) keep
the flows their own sections describe; whether any of them moves to the
dialog is decided on [#750](https://github.com/epiphany40223/parishkit/issues/750).

Current state, to be converted: Users' New user, Edit user and Import
Ministry leaders, and New and Edit scheduled email, use the dialog. Parish
settings, Campaign settings, Share options, Campaign Ministries, Ministries,
No-reminders WorkGroup, Campaign images and Parish logos show the in-place
review panel of #532 and #750
([in-place controls](#in-place-controls)); the other editors use separate
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
single control (Parish logos' upload mode, #1062). Where hiding a field would move the controls
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
  above both modes, so switching never moves it. It is a segmented pair
  with a short visible label ("Mode") and exactly two segments, **Basic**
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
([navigation rule 10](../admin-portal/spec.md#navigation-rules), [decisions 18 and
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
activation once the campaign has gone live), besides the usual way back and
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
  refuses a form sent after the campaign went live with the same reason.
- **Only for a viewer who sees the entry greyed.** The role still hides an
  entry ([stable menu shape](../admin-portal/spec.md#stable-menu-shape)), so
  a page the viewer's role may not open is refused by its own view (403) as
  before, without the reason; a signed-out visitor, or a session the page
  would rotate or end, also goes to the view. The session is checked
  read-only, renewing no idle time.
- **Server checks stay.** The page shows only the menu's words and grants
  nothing; every view keeps its own checks, which still refuse anything the
  gate lets through.

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
browser's own toolbars). Pages with short filters meet it now; report pages
with long filter and export forms meet it once those forms are collapsed by
default (a #227 follow-up). An edit form's short fields (a status, an outcome,
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
table heading is described by its bubble alone, and its bubble floats over the
page, so opening it never changes the size or scroll of the table's box;
it closes when that box scrolls its button out of view.

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
next rather than only that it is empty.

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

### Shared visual style

The portal and the emails share one visual style
([#732](https://github.com/epiphany40223/parishkit/issues/732)):

- **Design tokens:** `web/design_tokens.py` is the one source of the
  colours, corner radii, fonts and button shape. The portal stylesheet
  declares them as CSS custom properties, and a unit test fails if the two
  disagree, including any `var()` fallback in another stylesheet. Email
  components read the same module and inline the values, because mail
  programs cannot load the stylesheet.
- **Buttons:** templates draw every button, and every link styled as one,
  with the `{% button %}` tag. Its variants are primary (the default),
  secondary, large and link. It renders the stylesheet's classes and keeps
  each attribute in the order the template gives it, so in-place controls
  keep their `data-` attributes. A guard test counts raw button markup in
  every template against a list of reviewed exceptions that may only
  shrink.
- **Email buttons:** `email_button()` renders the same primary and
  secondary buttons for email: a one-cell table with inline styles from the
  tokens, `bgcolor`, and Outlook padding. The label is real text, so the
  button reads with images off and in an inverted dark mode. It links only
  to absolute http(s) addresses. The daily and weekly report emails draw
  their button with it, and a guard test keeps any other module from
  building an email button by hand. Its markup is already in the
  sanitizer's canonical form, so the report validators admit it unchanged;
  they still admit the earlier report button, so a report compiled before
  the change and retained for a retry still delivers.
- **Email layout:** the shared email layout takes the portal's ink, page,
  panel border and corner radius, link colour and warning notice from the
  tokens for every Admin email: the report emails and the operational and
  security alerts. Family emails, including receipts, and all readiness
  and test-send samples keep the earlier look until their own slice of
  [#732](https://github.com/epiphany40223/parishkit/issues/732), which
  follows a Testing send to real mail programs on a night with no reminder.
  Their content, links and codes never change for styling. Every sender
  names its look; there is no default.

Portal notices, cards and headings, and the Family emails' move to the
shared look, follow in later slices of #732.

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
New and Edit scheduled email take send times in the browser's zone (see [New
and Edit scheduled email](../admin-portal/spec.md#new-and-edit-scheduled-email)); the first-campaign
step and the date-change review still take them in the campaign's time zone,
the dated exception (2026-10-10) in [dates and times](#dates-and-times), until
they are fixed under
[#558](https://github.com/epiphany40223/parishkit/issues/558). The ParishSoft refresh times
are in the parish's time zone, the recorded exception to the browser-local
rule that those rules describe (see also
[ParishSoft refresh schedule settings](../admin-portal/spec.md#parishsoft-refresh-schedule-settings)).

**Fields.** Every Admin time of day is a shared time entry field: a form's
`FlexibleTimeField` or `FlexibleTimeListField`, or a hand-drawn `input` or
`textarea` marked `data-time-entry`. No page draws its own reading; a guard
test (`tests/stewardship/test_time_entry_guard.py`) fails on a new time box
without the mark ([#1117](https://github.com/epiphany40223/parishkit/issues/1117)).

| Page | Field | Entry |
| --- | --- | --- |
| ParishSoft refresh schedule | Each rule's At, From and To; each skip's At, From and To | One time, refresh schedule time |
| ParishSoft refresh schedule, Edit as text | Full refresh times; Quick update times | List, refresh schedule time |
| New and Edit scheduled email | Send time | One time, browser time |
| The date-change review; first-campaign Mail schedules | Send time | One time, campaign time |
| First-campaign Mail schedules, Repeat a reminder | Send time | One time, campaign time |
| Ministry follow-up | Contact attempt time | One time, browser time zone |
| Logs, reports | Date filters | Dates only, no time of day |

The Ministry follow-up contact attempt moved to this entry after its in-place
save work ([#592](https://github.com/epiphany40223/parishkit/pull/592))
merged ([#398](https://github.com/epiphany40223/parishkit/issues/398)); its
date stays a date control, and its not-in-the-future check reads the typed
time as the server does. The planned
[refresh schedule editor](../admin-portal/spec.md#parishsoft-refresh-schedule-settings)
([#632](https://github.com/epiphany40223/parishkit/issues/632)) replaces the
"At these times" list and uses this entry for its rule and exception times.

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
page-number field. A page number past the end shows the last page.

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
(#1133). A report whose rows an installed SQL selection
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
- the Ministry report (Ministry; Member, Member DUID and Submitted in one
  Ministry's view);
- the Ministry follow-up queue (Member and Ministry; newest first by default,
  with no Submitted column to show it, #1064).

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
ignored. The participation report's options reshape its statistics, chart and
exports rather than one table, so its options form is an
[in-place control](#in-place-controls) of all three panels instead. When
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
the Pages and emails email tables (third slice); #1065 (row links, System
logs).

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
([#1131](https://github.com/epiphany40223/parishkit/issues/1131)); the
other Admin tables with per-row actions move to it in later changes, as the
inventory on #879 lists.

- **Actions column.** The last column holds each row's actions as small icon
  buttons (`table_actions`), all the same square size: Edit, a link to the
  item's own page, and Delete. Each has an accessible name and a matching
  tooltip that name the row ("Edit Reminder 2", "Delete Reminder 2"), and the
  icons are drawn inline, so they need no request and stay inside the
  content security policy. A destructive action with another name uses its
  own word and icon in Delete's place and works the same way; so far only
  Revoke ("Revoke nightly export") on Automation access. A row shows only the
  actions that apply to it; a row with none shows nothing there.
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
  [#1131](https://github.com/epiphany40223/parishkit/issues/1131)). A dialog
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
  Census changes, Roster changes to enter, the Ministry follow-up queue, the
  Ministry report's request details (and its join and leave downloads and
  packets), the Limitations report, Families the form cannot open, and the
  Users page's ParishSoft Ministry leaders table and its import review
  (Administrator, 2026-10-12, so staff can find each leader in ParishSoft).
  Elsewhere a Member is named without it.

A Ministry, fund, WorkGroup, role or any other ParishSoft record is
identified by its name, never its DUID: no column, inline "(DUID n)", detail
line, picker label, help sentence or download column shows one. Where two
Ministries can share a name in one list, a detail ParishSoft users recognise
tells them apart, not the DUID: System logs' Ministry menu marks an inactive
Ministry "(inactive)". Two active Ministries with the same name keep the
same label, since the DUID is never the tie-breaker. A saved Ministry or fund the
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

The Ministry follow-up queue also has no Submitted column (Administrator
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
The Ministry follow-up queue does the same for a long Member name, with the
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

## Downloaded files

**Rule.** Every format of a download (CSV, XLSX and PDF) carries the same
columns, and those columns match its page: the same columns, names and order.
Internal references and identifiers (row versions, campaign, source, item and
response reference UUIDs, record ids and similar) are left out of every
format, CSV included (Administrator decision, 2026-10-10,
[#956](https://github.com/epiphany40223/parishkit/issues/956)). Every Admin
PDF and XLSX shares one design in the portal's design tokens; a PDF lays the
columns out as one table, record cards or address cards in a shared page
frame. Identifiers a reader uses, such as Family and Member DUIDs and
envelope numbers, are not internal references and stay. **Why.** Staff print
and share these files; internal references mean nothing to a reader, and a
column that is in one format and not another makes the files disagree about
what the report holds. Troubleshooting uses the page's Technical details and
the audit log, not a download; the recorded exceptions are the System logs
export and the command line's Census changes JSON Lines below. Every data export is offered as CSV, XLSX and PDF unless a
recorded technical or design reason says otherwise (Administrator decision,
2026-10-10); a page may add a format, such as the participation chart's PNG
or System logs' JSON Lines. **Shared code.** `reports/pdf_design.py` and
`reports/xlsx_design.py`; `web/design_tokens.py`. The format rules for each file type (CSV neutralizing,
money, headers) are in the reports'
[shared report behavior](../reports/spec.md#shared-report-behavior).
**Precedent.** #926, #929, and the Administrator's decisions of 2026-10-10
on [#958](https://github.com/epiphany40223/parishkit/pull/958).

A download may split one combined page cell into its parts, because a
spreadsheet needs one value per cell to sort, filter or merge on: the parts
take the cell's place, in the order the page shows them, each headed with the
page's own words for it (the cell's heading or the label the page shows
beside that part). The Ministry report's lists are the first case: the page's
"Status and outcome" cell is Status and Outcome in the files, and "Contact and
demographics" is Gender, Age, Email, Phones and Known primary Family address
([Ministry change summary](../reports/spec.md#ministry-change-summary))
(Administrator decision, 2026-10-11, on
[#932](https://github.com/epiphany40223/parishkit/issues/932)).

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
is Participation: its export forms post the page's own `query.timezone`, the
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
Census changes, Additional information, one Ministry's joining or leaving
lists, Roster changes to enter, the Family timeline, and the Family directory
code list only while its response columns are included (the field comes and
goes with that applied choice, as the last field of its row, so nothing
moves). Not asking: the Ministry follow-up packet, the participation exports,
the directory code list without response columns, the postal mail merge, and
the Family test names file (`test families-preview --names`, which uses the
parish's zone unless `--timezone` is given, as a host command has no
browser).

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

## Family portal

**Rule.** The Family portal is mobile-first, is not behind the Admin
JavaScript gate (where its flow needs script, an unsupported browser gets a
plain notice rather than a broken page), and keeps every link it has emailed
working. It
shares the theme and design tokens with the Admin portal but keeps its own
spacing, and the Admin-only conventions above (in-place controls, the
JavaScript gate, Admin tables) do not apply to it. **Why.** Families use
whatever device and browser they have, and an emailed link cannot be
re-sent. **Shared code.** `family-v1.js`, `ui-v1.css`, `web/design_tokens.py`.
**Precedent.** #466, #565, #864. The Family flow's own behavior is in the
[Family portal specification](../parishioner-portal/spec.md).
