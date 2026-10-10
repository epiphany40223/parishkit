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
  [conditional fields](#conditional-fields),
  [single-campaign interim](#single-campaign-interim),
  [addresses and legacy URLs](#addresses-and-legacy-urls),
  [not-found pages](#not-found-pages).
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
with no time stays a calendar date and is never shifted. The rule governs how
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
follow the same rule: parish local time with the zone named (Administrator
decision, 2026-10-10; until
[#967](https://github.com/epiphany40223/parishkit/issues/967) lands they
still print a UTC stamp). Pages and emails move to these rules one
group at a time in the #558 PRs; the Ministry follow-up contact attempt is the
first ([Admin portal](../admin-portal/spec.md#follow-up-workflows)), the System
logs date filters the second ([Admin portal](../admin-portal/spec.md#logs)), and
New and Edit scheduled email the third ([Admin
portal](../admin-portal/spec.md#new-and-edit-scheduled-email)).
Until a page's PR lands, it keeps its current zone, including the campaign zone
for the other schedule pages, campaign dates and report day buckets. One field is a recorded
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
and page placeholders, and PDF exports all use it; dense tables such as
System logs, background work and deliveries use its compact variant (short
month names or two-digit years, no time-zone abbreviation), except that System
logs rows add seconds and the time-zone abbreviation, with no UTC offset, so
entries can be compared exactly
([Logs](../admin-portal/spec.md#logs)). Background output
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
  event; Dismiss on an integration's finished key change; the
  [participation report](../reports/spec.md#campaign-statistics)'s Apply
  report options, which refreshes its statistics, chart and export panels)
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
- **Request.** The browser fetches exactly the request the control would have
  made, follows the server's Post/Redirect/Get redirect, and replaces every
  region the fetched page shares with this one, plus the counts, summaries,
  links and form state outside them that follow the view; views keep
  rendering whole pages, so no partial-page endpoint exists. A link marked
  `data-in-place-only` replaces only the region it names: the history
  pages of an information item and a Ministry follow-up request sit inside
  the item's panel, and paging them must not replace the Save follow-up form
  above or discard notes typed but not yet saved; a save still replaces the
  whole panel, history included. One request is
  in flight at a time: a newer choice cancels an older read, but a POST that
  saves a change is never cancelled, and other in-place controls and repeats
  are ignored (the live region says "Still saving…") until it settles.
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
- **In-place review (#532).** Parish settings and Campaign settings review,
  apply and follow a change on the page itself, not on separate Review and
  Change status pages. The settings form is a `form[data-in-place]` marked
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
  read (named with `in_place`, which only these two pages may be). Once the
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
  page whole. Other editors keep their review and Change status pages.

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
hiding it (open #959); a field that a choice makes inapplicable stays in
place, greyed, rather than disappearing, through `data-enabled-when` (open
#959) or `data-locked-when` (open #957) ([conditional
fields](#conditional-fields)). **Precedent.** #736 and its review
(Administrator, 2026-10-08), #563, open #957, open #959.

### Prerequisite gates

**Rule.** A button is unavailable until its prerequisites are met and there
is something for it to do, and while it is unavailable a short visible reason
says what is missing. A Review button is unavailable until the form differs
from what is saved. The server still checks every submission. **Why.** A
button that is available but refuses afterwards wastes a round trip and reads
as an error; a button greyed out with no reason reads as broken. **Shared
code.** `ui-v1.js`: `data-require-complete` with `data-complete-hint` and
`data-missing-hint`, and `data-complete-hint="reserve"` to keep the hint's
line while it is empty (open #959); `data-required-when` and
`data-required-when-shown`; `data-enabled-when` and `data-locked-when`
([conditional fields](#conditional-fields));
the acknowledgment gate (`data-acknowledgment-gated`); the time-entry gate;
the change gate `data-require-change` (open #924); the shared `holdButton` /
`releaseButton` pair and `GATE_MARKS` list (open #924), so gates never release a
button another gate still holds; the `pageshow` re-check;
`test_campaign_setup_gates.py`, `test_prerequisite_gates.py` (open #946).
**Precedent.** #553, #563 (and its slices, such as open #946), #921 / open #924.

This includes a table's bulk action buttons: while they are unavailable,
their reason is visible text in a reserved line beside or below them, so
ticking a row never moves anything ([Admin tables](#admin-tables);
Administrator decision, 2026-10-10). The existing bulk bars still give it as
a visually hidden hint with a tooltip until they change under
[#879](https://github.com/epiphany40223/parishkit/issues/879).

Wherever a form requires an acknowledgment checkbox (finishing setup, a test
that may already have arrived, chosen-Family tests, Testing cleanup,
withdrawal, refusal removal, manual reports, duplicate resends, resetting all
pages and emails to their default text), the page script keeps the form's
primary button disabled until the box is checked, and the server refuses a
missing acknowledgment. While the button waits, a short hint directly after
it (named by the button's `aria-describedby`) says to tick the confirmation,
so only content below the button moves when it clears. A form that arrives
through an in-place update is gated the same way (#563).

More generally, a form whose fields depend on other choices keeps its submit
button unavailable until every visible required field is complete, with a
short hint by the button saying what is missing (`data-require-complete` in
the page script; first used by
[Ministry follow-up](../admin-portal/spec.md#follow-up-workflows), #553). Fields required only in
some states are required only while shown. A required text field holding
only spaces counts as empty, as the server trims it: the reason for
cancelling go-live and the evidence notes on Mail delivery and delivery
refusal pages use this gate, as does the typed "Production" confirmation
(which, like the server, ignores spaces around the word). The Admin portal
requires JavaScript ([#565](https://github.com/epiphany40223/parishkit/issues/565));
server validation is unchanged and still refuses an incomplete submission.
A browser can restore a page from its history (Back or Forward) with the
reader's values but without the events that set the page up, so these
states (shown and hidden fields, unavailable buttons and their hints, a
table's selection, the campaign modules and mail schedule rows) are worked
out again when the page is shown (`pageshow`, #563).

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
`accounts/integration_views.py` (open #920). **Precedent.** #592, #562, open #920.

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
entered (a follow-up contact time in the future). Save is held while such an
error stands only on forms that use the complete-before-submit gate above.
The server stays the authority: it checks every save, and its refusal is
shown at the field as above, even when the browser would have allowed the
value (a wrong computer clock, for example).

A mark clears as soon as its error does, without waiting for Save (#592). An
error the browser checks clears once the value is valid. One only the
server can check (an outcome that doesn't fit the request, for example)
clears from its field, with its message, on the first edit of that field,
since the server checks again on save; an error that marks two fields (a
date and a time) clears from both when either is edited. A field that a
choice hides is not sent, so its marks clear too. Leaving a field without
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
configuration-change request path and Change status page; the in-place
review (`data-table-sync`, `data-review-of`) in `ui-v1.js`
([in-place controls](#in-place-controls)); `data-submit-on-change` for view
filters only. **Precedent.** #532 (in-place review), #523
([preview lifetimes](../admin-portal/spec.md#preview-lifetimes)), #882 (New and
Edit pages follow it).

Current state, to be made consistent: only Parish settings and Campaign
settings review, apply and follow the change in place
([in-place review](#in-place-controls), #532); every other editor still uses
its separate Review and Change status pages.

### Conditional fields

**Rule.** A field is shown only when it is relevant to the choices already
made, and is required only while shown. A hidden field is not sent, and its
errors clear. **Why.** Fields that do not apply invite wrong answers and make
a form look longer than it is. **Shared code.** `ui-v1.js`: `data-show-when`,
`data-required-when`, `data-required-when-shown`; re-applied on `pageshow`
and after every in-place swap. Where hiding a field would move the controls
around it, two gates keep it in place instead:

- `data-enabled-when="name=value"` (open #959) works like `data-show-when`
  but never hides: while the rule does not hold, the field stays, disabled
  (greyed and not sent). It is re-checked on `pageshow` and after in-place
  filter syncs. First use: System logs' level boxes while Sign-in activity is
  chosen.
- `data-locked-when` (open #957) shows a fixed value, disabled, while its
  rule holds, with the reason in reserved space, and gives back the reader's
  own value (kept in a hidden field) when the rule stops holding. First use:
  the directory's "Campaign mail can reach" while Include mailing columns is
  ticked.

The two overlap, and are to be merged into one shared gate once both have
landed. **Precedent.** #563, #736, open #957, open #959.

### Single-campaign interim

**Rule.** Multi-campaign-only controls are greyed out with one shared tip
until #145 removes them. **Why.** The system becomes
single-campaign after this campaign; removing the controls piecemeal would
leave half-working flows. **Shared code.** The `multi_campaign_control`
template tag (`accounts/templatetags/stewardship.py`) and its tip
`campaigns.single_campaign.TIP`. **Precedent.** #145.

The Admin portal serves one current campaign. The system moves to a single
campaign after this campaign (#145), so navigation already assumes it: there
is no campaign chooser and no New campaign control (the one campaign is
created by [Create the campaign](../admin-portal/spec.md#create-the-campaign), offered only while the
deployment has never had a campaign), and no Admin URL names a campaign. Every
remaining control whose only purpose is working with more than one campaign,
such as **Copy campaign** on Campaign settings and the "Choose a retained
campaign" links on Participation and Ministry requests, is shown greyed out
until #145 removes it: an unavailable control, not a link or action, with the
tip "Disabled; will be removed with the single-campaign change (#145)", shown
and announced the same way as an unavailable menu entry's reason. The server
refuses the matching actions too, so a greyed control cannot be bypassed
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
(`BrowserErrorMiddleware`, and `not_found_response` (open #930)) and
`error.html`. **Precedent.** #927, open #930. The general error-page rule is under
[accessibility and client behavior](#accessibility-and-client-behavior).

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
data line (such as the campaign name or counts), safety notices (such as
Testing mode) and then its data or form; at most a one-line hint that prevents
a likely mistake stays visible above the data. A caution that prevents a
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
default (a #227 follow-up). The Family portal keeps its own spacing.

Field help longer than about one line opens from an "i" button beside the
field's label (the shared toggletip). Only a one-line hint stays visible under
the label, for fields whose format or rule is needed every time (an example
address, "one per line", a key's paste rule), and a warning that blocks the
field, such as having no emails to schedule, always stays visible. The field
remains described by its full help, so screen readers announce it without
opening the tip. Checkbox help stays beside the box.

Internal identifiers and bookkeeping fields that matter only for
troubleshooting (delivery, refusal and test references, a retained
configuration version, an export's requester reference and data-load
numbers, and the log cross-link identifiers) sit in a "Technical details"
disclosure, closed by default, instead of in the page's main text. Nothing
is removed from the page; downloads leave these internal fields out of every
format ([downloaded files](#downloaded-files)). An empty list says what to do
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
  to absolute http(s) addresses.

Notices, cards and headings, and each email's move to the shared
components, follow in later slices of #732.

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
- A list field (the ParishSoft refresh times) reads each entry. Entries are
  separated by commas, semicolons or spaces, and by new lines in a value
  posted without the page (a one-line text box drops line breaks from pasted
  text). A suffix standing alone after a space (`2 pm`) belongs to the entry
  before it, but not across a comma or semicolon (`2, pm` is refused). A time
  listed twice is reported in the reading and saved once.
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

**Fields.**

| Page | Field | Entry |
| --- | --- | --- |
| ParishSoft settings | At these times (full refresh) | List, parish time |
| New and Edit scheduled email | Send time | One time, browser time |
| The date-change review; first-campaign Mail schedules | Send time | One time, campaign time |
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
non-private filters ([Logs](../admin-portal/spec.md#logs)). A report whose rows an installed SQL selection
orders offers that selection's sort orders on the columns they order and its
page sizes; its other columns do not sort, since the schema owns those
orders. In v1 that covers:

- the active parishioner family directory (Family and DUID, 50 rows; Family code would need
  every code decrypted per view);
- Financial stewardship detail (Family, Annual pledge and latest response);
- the Additional information queue (Family and Submitted);
- the Ministry report (Ministry; Member and Submitted in one Ministry's view);
- the Ministry follow-up queue (Request, Member and Ministry).

Extending those vocabularies is a schema change; the DUID columns of the
financial, Additional information and Ministry tables are the known gap
([table column order](#table-column-order),
[#960](https://github.com/epiphany40223/parishkit/issues/960)). Columns that are only
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
name it (`aria-describedby`); the line keeps its height when the hint clears
(Administrator decision, 2026-10-10; the existing bars move from a visually
hidden hint and tooltip under
[#879](https://github.com/epiphany40223/parishkit/issues/879)). Ticking a row
never moves a control under the pointer: the hint's line is reserved, and the
count and Select all button sit
after the action buttons, so their changing text cannot push them (#563). The
server validates every submitted selection.

### Row actions and confirmation

**Rule.** Row actions are same-size icon buttons in the last column, Delete
asks in a confirmation dialog, a table that allows it acts on several rows
through a selection column, and Edit opens the item's own page (the New page,
filled in). **Why.** One pattern for every list of editable things; a dialog
names exactly what will be removed. **Shared code.** The `table_actions`
template tag, `components/confirm-dialog.html` and `data-confirm-open` in
`ui-v1.js`. **Precedent.** #879, #882 (first user), open #947 (second slice).

A table whose rows can be acted on follows one pattern
([#879](https://github.com/epiphany40223/parishkit/issues/879)), built once
and shared; [Dates and mail schedules](../admin-portal/spec.md#dates-and-mail-schedules) is its first
user, and the other Admin tables with per-row actions move to it in later
changes.

- **Actions column.** The last column holds each row's actions as small icon
  buttons (`table_actions`): Edit, a link to the item's own page, and Delete.
  Each has an accessible name and a matching tooltip that name the row ("Edit
  Reminder 2", "Delete Reminder 2"), and the icons are drawn inline, so they
  need no request and stay inside the content security policy. A row shows
  only the actions that apply to it; a row with none shows nothing there.
- **Edit** opens the item's edit page, which is the same page as New, filled
  in. Saving it goes through that page's usual review, and its status page
  returns to the table.
- **Delete** opens a confirmation dialog in the page: a native modal
  `<dialog>` that names what it removes and says what cannot be undone, with
  the destructive action and Cancel. While it is open the rest of the page is
  inert and Tab stays inside it; Escape or Cancel closes it and changes
  nothing; focus returns to the control that opened it, or, when that row is
  gone, to the table's heading. Confirming sends the table form's POST with
  the chosen rows, without a page load: the confirm button says it is working
  and the dialog cannot be dismissed until the answer arrives. When the
  server records a configuration change, the page follows that change's status
  until it is applied, then redraws every region from the table's own address
  (keeping its sort) and closes the dialog, announcing what was removed. A
  refusal, or a change that failed to apply, is shown inside the dialog, below
  its buttons so they never move, and nothing is redrawn; the server's
  explanation is shown when it gives one. A page-wide `confirm()` is never
  used.
- **Several rows.** A table that allows acting on several rows has the
  [selection column and bulk bar](#admin-tables) above; its only bulk action
  is usually Delete selected, which opens the same dialog naming the count.
  Rows without actions have no selection box.
- **Server checks** are those of the action itself: the dialog only asks, and
  the server validates every chosen row and refuses the whole request when
  any one cannot be acted on.

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
5. the Member name, then **Member DUID** in its own column next to it;
6. the Ministry name, then **Ministry DUID** in its own column;
7. everything else, including dates that are not the most relevant column;
8. actions last.

A DUID never shares a cell with a name. When a table has both a Family name
column and a DUID column, both sort. **Why.** A combined cell cannot be
sorted or scanned by DUID, reads badly with a screen reader and copies badly
into a spreadsheet; staff moving between pages should not re-learn each one.
**Shared code.** `test_admin_table_columns.py` refuses a cell that prints a
DUID label with a value (open #941). **Precedent.** #932 (Administrator,
2026-10-09, corrected 2026-10-10 to "most relevant column first", and the
2026-10-10 rule that the Family name and DUID columns both sort), open #941,
open #950.

Known exceptions, which need a schema change: three report tables are ordered
by an installed SQL selection with no DUID sort
([Admin tables](#admin-tables)), so their DUID columns do not sort yet. They
are Financial stewardship detail and the Additional information queue (Family
DUID), and the Ministry report (Ministry DUID, and Member DUID in one
Ministry's view). One forward migration adds those sorts
([#960](https://github.com/epiphany40223/parishkit/issues/960)).

### Family and Member names

**Rule.** Everywhere a table shows a Family's name, it is the Family's heads
of household with the surname first ("Squyres, Tracy and Jeff"), so the
column sorts by surname, built by the shared helper rather than by each page.
A Member's name is shown with that Member's DUID beside it. **Why.** The
surname alone does not tell two Families apart, and the free-text mailing
name is sometimes one person's given name. **Shared code.**
`family_heads_name` in `source/family_names.py` (the rule); for a set of
DUIDs, `snapshot_family_names` in `source/snapshot_names.py`; the SQL
equivalent in `schema/directory_reports.sql` for the directory's search and
order. Salutations use `heads_salutation_name` ("Tracy and Jeff Squyres")
from the same module. Where a table sorts in the database, the
`SnapshotFamilyName` ORM expression (open #965, in
`source/snapshot_name_sql.py`) builds the same name per row, so the sort
orders by surname, then the whole name. **Precedent.** #471, #932 (Administrator, 2026-10-10), open #965.

A Member's own name is shown "Last, first" ("Smith, Ann"), so a column of
Member or person names sorts by surname too (Administrator, 2026-10-10, on
[#952](https://github.com/epiphany40223/parishkit/issues/952)); first used by
the Users page's
[leaders table](../admin-portal/spec.md#parishsoft-ministry-leaders-without-a-user)
and [user Names](../admin-portal/spec.md#name-from-parishsoft) (#963).

Recorded exception: System health's "Families the form cannot open" shows the
Family's surname only (from the shared `family_display_name`), never the
heads' names. The refused field can itself be a head's name, and that page
never shows Member values (open #965).

### Table fit and row height

**Rule.** A table fits the page at laptop width; a table that cannot scrolls
sideways inside its own box, never the whole page. Rows stay short: a cell
holds one value, long text wraps within a sensible width, and detail that
makes rows several lines tall belongs on the item's own page or in a
disclosure. **Why.** A page that scrolls sideways hides its own controls; tall
rows make a table unscannable. **Shared code.** The `table-scroll` class in
`ui-v1.css`; the button-label browser test at 320 px and 1280 px.
**Precedent.** #614, #952 (Portal users' rows wrapping to several lines).

Recorded exception: the Users page keeps every row to one line (the
Administrator asked for one-line rows on #952). A long email or Name is cut
off with "…"; the full value stays the cell's text, appears in a `title`
tooltip, and is shown whole on the user's Edit page
([Users table](../admin-portal/spec.md#users-table), #952, #963).

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
the audit log, not a download; the one recorded exception is the System logs
export below. Every data export is offered as CSV, XLSX and PDF unless a
recorded technical or design reason says otherwise (Administrator decision,
2026-10-10); a page may add a format, such as the participation chart's PNG
or System logs' JSON Lines. **Shared code.** `reports/pdf_design.py` and
`reports/xlsx_design.py`; `web/design_tokens.py`. The format rules for each file type (CSV neutralizing,
money, headers) are in the reports'
[shared report behavior](../reports/spec.md#shared-report-behavior).
**Precedent.** #926, #929, and the Administrator's decisions of 2026-10-10
on [#958](https://github.com/epiphany40223/parishkit/pull/958).

Recorded exceptions to the three formats:

- **System logs:** CSV, XLSX and JSON Lines, no PDF. Its rows are wide raw
  data (structured details up to the export limit) that do not print as a
  readable page. It is also the one exception to leaving out internal
  identifiers: every format keeps the log entries' cross-link identifiers
  (request, correlation and subject IDs), because the export is itself the
  troubleshooting record and only the Administrator can download it
  (default, pending Administrator confirmation on
  [#966](https://github.com/epiphany40223/parishkit/issues/966)).

Not data exports, so outside the rule: the deployment configuration
document, hosted files (served as uploaded) and chart images.

Current state, to be made consistent under
[#966](https://github.com/epiphany40223/parishkit/issues/966): the Financial
detail, Additional information and Ministry report CSV and XLSX files still
carry internal columns (Family version, Response reference, Row type,
Version, Proposed Member reference), and several files' report information
still lists campaign and source references; the participation and System
logs CSVs carry raw ids; Census changes and Talents lack PDF; Family test
names is CSV only; and System logs lacks XLSX.

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
