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

Each convention gives the **rule**, **why** it exists, the **shared code**
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

**Rule.** The Admin portal is desktop-first: it is laid out for a laptop
screen and still works at phone width. The Family portal is mobile-first.
**Why.** Staff work at desks; Families mostly answer on phones; neither may be
locked out on the other kind of device. **Shared code.** `ui-v1.css` (one
theme for both portals); the button-label browser test at 320 px and 1280 px
([button labels](#button-labels)). **Precedent.** The original presentation
rules; #227 (laptop-first Admin layout); #614.

Detail: [global presentation rules](../spec.md#global-presentation-rules).

### Language and localization

Detail: [global presentation rules](../spec.md#global-presentation-rules).

### Numbers and money

Detail: [global presentation rules](../spec.md#global-presentation-rules).

### Dates and times

**Rule.** Every Admin page shows and takes dates and times in the browser's
own time zone, stored as UTC; no page labels a time "UTC". **Why.** Staff read
and type their own wall-clock time; a campaign-zone or UTC time on a page is a
conversion the reader has to do in their head. **Shared code.**
`data-local-instant` elements filled by `date-format-v1.js`;
`web/dates.py` and the `parish_date` and `parish_time` template filters
(`accounts/templatetags/stewardship.py`); the hidden
browser-zone field (`data-browser-zone`) that `ui-v1.js` fills; the
`test_browser_local_times.py` guard. **Precedent.** #558 (Administrator
decision, 2026-10-04).

Detail: [global presentation rules](../spec.md#global-presentation-rules).

### Date format

Detail: [global presentation rules](../spec.md#global-presentation-rules).

### Accessibility and client behavior

Detail: [Accessibility and client behavior](../architecture/spec.md#accessibility-and-client-behavior).

### Charts

Detail: [shared report behavior](../reports/spec.md#shared-report-behavior).

## Admin portal interaction

### JavaScript requirement

**Rule.** The Admin portal requires JavaScript; the Family portal does not.
**Why.** One script-driven behavior per control is simpler and better tested
than a script path plus a no-script fallback; Families use whatever device
they have. **Shared code.** The `js-required` class (`ui-v1.css`) removed by
`admin-gate-v1.js`; `test_admin_javascript_gate.py`. **Precedent.** #565.

Detail: [JavaScript requirement](../admin-portal/spec.md#javascript-requirement).

### In-place controls

**Rule.** An Admin control acts where the reader is: no page reload and no
jump to the top. **Why.** Staff lose their place, their scroll position and
their unsaved typing on a reload. **Shared code.** `ui-v1.js` in-place
regions (`data-in-place`, `data-table-region`, `data-in-place-region`),
`live-status-v1.js` for status regions; `test_in_place_controls.py`.
**Precedent.** #519, #478, #484, #488, #532, #562.

Detail: [In-place controls](../admin-portal/spec.md#in-place-controls).

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
scrollbar's space ([page help](#page-help)). **Precedent.** #736 and its
review (Administrator, 2026-10-08), #563.

### Prerequisite gates

**Rule.** A button is unavailable until its prerequisites are met and there
is something for it to do, and while it is unavailable a short visible reason
says what is missing. A Review button is unavailable until the form differs
from what is saved. The server still checks every submission. **Why.** A
button that is available but refuses afterwards wastes a round trip and reads
as an error; a button greyed out with no reason reads as broken. **Shared
code.** `ui-v1.js`: `data-require-complete` with `data-complete-hint` and
`data-missing-hint`; `data-required-when` and `data-required-when-shown`;
the acknowledgment gate (`data-acknowledgment-gated`); the time-entry gate;
the change gate `data-require-change` (#924); the shared `holdButton` /
`releaseButton` pair and `GATE_MARKS` list (#924), so gates never release a
button another gate still holds; the `pageshow` re-check;
`test_campaign_setup_gates.py`, `test_prerequisite_gates.py` (#946).
**Precedent.** #553, #563 (and its slices, such as #946), #921 / #924.

Known exception: a table's bulk action buttons give their reason as a
visually hidden hint and a tooltip rather than a visible line, so ticking a
row never moves them ([Admin tables](#admin-tables)). Whether that reason
should become visible in reserved space is open on #563.

Detail: [first-Admin wizard](../admin-portal/spec.md#bootstrap-and-first-admin-wizard).

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

Detail: [first-Admin wizard](../admin-portal/spec.md#bootstrap-and-first-admin-wizard).

### Review, Apply and Change status

**Rule.** A change to configuration or data is made by editing, then
**Review** (which shows each changed value, current and proposed), then
**Apply** (or **Save**), after which the page follows the change's status in
place until it is applied. Changes are saved by an explicit action, never
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

Exception being removed: Portal users' role checkboxes autosave through an
intent queue
([portal user management](../admin-portal/spec.md#portal-user-management)),
which #952 replaces them with Edit, Review and Apply.

### Conditional fields

**Rule.** A field is shown only when it is relevant to the choices already
made, and is required only while shown. A hidden field is not sent, and its
errors clear. **Why.** Fields that do not apply invite wrong answers and make
a form look longer than it is. **Shared code.** `ui-v1.js`: `data-show-when`,
`data-required-when`, `data-required-when-shown`; re-applied on `pageshow`
and after every in-place swap. **Precedent.** #563, #736.

### Single-campaign interim

**Rule.** Until the single-campaign change, every control whose only purpose
is working with more than one campaign is shown greyed out with one shared
tip, and the server refuses its action. **Why.** The system becomes
single-campaign after this campaign; removing the controls piecemeal would
leave half-working flows. **Shared code.** The `multi_campaign_control`
template tag (`accounts/templatetags/stewardship.py`) and its tip
`campaigns.single_campaign.TIP`. **Precedent.** #145.

Detail: [Admin navigation](../admin-portal/spec.md#admin-navigation).

### Addresses and legacy URLs

**Rule.** Admin addresses follow the
[URL scheme](../admin-portal/spec.md#url-scheme); an old Admin address is not
kept, aliased or redirected. Family-facing addresses (emailed codes and links,
the Family portal and its sign-in) never break. **Why.** Aliases double every
route to test and secure; Families cannot be asked to find a new link.
**Shared code.** `admin_urls/` (one module per menu group);
`test_admin_url_scheme.py`. **Precedent.** #525, #864.

Detail: [URL scheme](../admin-portal/spec.md#url-scheme).

### Not-found pages

**Rule.** An unknown Admin or Family address gets a styled page in the site
layout ("Page not found") with a next step and a way back, never the plain
text of the security middleware; nothing from the address is shown. Scripts
get the closed JSON error. **Why.** A bare "Not Found" in a fixed-width font
looks like a broken site. **Shared code.** `web/error_pages.py`
(`not_found_response`, `BrowserErrorMiddleware`) and `error.html`.
**Precedent.** #927, #930 (open). The general error-page rule is under
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

Detail: [Page help](../admin-portal/spec.md#page-help).

### Button labels

Detail: [Button labels](../admin-portal/spec.md#button-labels).

### Shared visual style

Detail: [Shared visual style](../admin-portal/spec.md#shared-visual-style).

### Time entry

Detail: [Time entry](../admin-portal/spec.md#time-entry).

## Tables

### Admin tables

**Rule.** Every Admin table uses the one shared table component, so paging,
sorting, selection and styling behave the same everywhere. **Why.** Staff
learn one table once. **Shared code.** `table-navigator.html`,
`table-sort-heading.html` and the `sort_heading` template tag,
`table-selection.html`; `test_admin_tables.py`.
**Precedent.** #478, #484, #488.

Detail: [Admin tables](../admin-portal/spec.md#admin-tables).

### Row actions and confirmation

**Rule.** Row actions are same-size icon buttons in the last column, Delete
asks in a confirmation dialog, a table that allows it acts on several rows
through a selection column, and Edit opens the item's own page (the New page,
filled in). **Why.** One pattern for every list of editable things; a dialog
names exactly what will be removed. **Shared code.** The `table_actions`
template tag, `components/confirm-dialog.html` and `data-confirm-open` in
`ui-v1.js`. **Precedent.** #879, #882 (first user), #947 (second slice).

Detail: [Row actions and confirmation](../admin-portal/spec.md#row-actions-and-confirmation).

### Table column order

**Rule.** Similar tables put their columns in the same order:

1. the selection checkbox, when the table has bulk actions;
2. the most relevant column first: the Family name for a Family worklist or
   report, the Member name for Member rows, the thing's name or address for
   a configuration or account table (Portal users: the email address), and
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
DUID label with a value (#941). **Precedent.** #932 (Administrator,
2026-10-09, corrected 2026-10-10 to "most relevant column first", and the
2026-10-10 rule that the Family name and DUID columns both sort), #941, #950.

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
from the same module. **Precedent.** #471, #932 (Administrator, 2026-10-10).

### Table fit and row height

**Rule.** A table fits the page at laptop width; a table that cannot scrolls
sideways inside its own box, never the whole page. Rows stay short: a cell
holds one value, long text wraps within a sensible width, and detail that
makes rows several lines tall belongs on the item's own page or in a
disclosure. **Why.** A page that scrolls sideways hides its own controls; tall
rows make a table unscannable. **Shared code.** The `table-scroll` class in
`ui-v1.css`; the button-label browser test at 320 px and 1280 px.
**Precedent.** #614, #952 (Portal users' rows wrapping to several lines).

## Downloaded files

**Rule.** Every Admin PDF and XLSX shares one design in the portal's design
tokens. A PDF is the readable view: one table, record cards or address cards
in a shared page frame, leaving out internal reference fields (campaign,
source, item and response references, row versions and similar). CSV and
XLSX stay the complete audit files with every column. A download follows its
page's column order. **Why.** Staff print and share these files; internal
references mean nothing to a reader, but the audit files must stay complete.
**Shared code.** `reports/pdf_design.py` and `reports/xlsx_design.py` (#929);
`web/design_tokens.py`. The format rules for each file type (CSV neutralizing,
money, headers) are in the reports'
[shared report behavior](../reports/spec.md#shared-report-behavior).
**Precedent.** #926, #929 (open).

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
