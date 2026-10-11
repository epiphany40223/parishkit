# Stewardship campaign history and comparison

This specification defines how the system keeps a durable record of past
campaigns and compares the current campaign with them
([#1018](https://github.com/epiphany40223/parishkit/issues/1018)). It has two
parts that share one file format:

- the **campaign archive workbook**, one XLSX file per campaign, defined
  once, here, in [archive workbook format](#archive-workbook-format); and
- the **campaign comparison report**, which imports archive workbooks of past
  campaigns and lines them up with the current campaign day by day.

The importer reads exactly the format the end-of-campaign archive export
will write. That export (#1018 part (a)) is roadmap Tier 3 and comes later
with the campaign close-out wizard
([#527](https://github.com/epiphany40223/parishkit/issues/527)); when it is
specified, it is added to this specification and links the format below
rather than restating it. The comparison report and its import page are
roadmap Tier 2 (Administrator decision, 2026-10-11): leadership wants updated
comparisons before the current campaign ends.

General UX rules come from the [UX conventions](../ui-conventions/spec.md),
which take precedence over this specification for portal-wide rules. Report
behavior shared by every report (headers, privacy of URLs, `no-store`,
download formats, the chart engine) is in
[shared report behavior](../reports/spec.md#shared-report-behavior) and is
not repeated here.

## Purpose and audience

The Administrator asked how the current campaign is doing compared with
prior years. Answering it once took a manual conversion of seven years of
exports from the old system. This feature makes the next answer a page, not a
project.

The readers are parish leadership: the pastor, the stewardship committee and
staff. They are not technical and read pictures before numbers. Staff and
Administrators open the page; the rest of leadership receive the
[one-page summary](#downloads-and-sharing) or the
[digest section](#digest-section) from the Administrator. The report
therefore follows the visual, charts-first style of
[#720](https://github.com/epiphany40223/parishkit/issues/720): a few large
headline figures, line charts with the exact numbers in tooltips and a table,
and short plain-language notes. Every figure the report shows is an aggregate;
no Family is ever named or identified on it.

The importer's users are the Administrator and, through the command line,
the operator working for them. They hold the archive files, which contain
Family-level giving and are private.

## Archive workbook format

This section is the one definition of the archive workbook, version `1.0`.
It is the format of the files the one-time conversion of the 2020–2026
campaigns produced (October 11, 2026), so those files import unchanged.
The reader (import, this specification) and the future writer (#1018 part
(a)) are one module, `parishkit.stewardship.archive.workbook`, that owns a
single table of sheets, columns and metadata keys with their descriptions.
The README sheet's dictionary and the reader's checks are generated from that
table, so they cannot drift; the tables below restate it for reviewers, and
a test keeps the two in step.

### Files and versions

- Schema name `stewardship-campaign-archive`, version `1.0`.
- One workbook per campaign, named `stewardship-<year>.xlsx`, or
  `stewardship-<year>-partial.xlsx` for a mid-campaign export. The name is a
  convention only; the reader trusts the `metadata` sheet, never the file
  name.
- A plain `.xlsx` package with no macros, external links or embedded
  objects: the reader applies the
  [OOXML package checks](../hosted-files/spec.md#accepted-types-and-validation)
  of hosted files and refuses anything else.
- A reader accepts a file whose schema name matches and whose major version
  equals its own. A later minor version may add sheets, metadata keys and
  columns after the last known column of a sheet; the reader ignores those
  and never stores them. Any other difference (a missing or reordered known
  column, a different major version, a wrong schema name) refuses the file.

### Conventions

- The writer orders the sheets `README`, `families`, `submissions`, `daily`,
  `ministry_responses`, `metadata`. The reader finds them by name and
  requires only that `README` is first.
- Row 1 of each data sheet is its header row, exactly the column names
  below in this order. Data starts at row 2 and ends at the first fully
  blank row; a non-blank row after it refuses the file.
- Cells hold literal values. A formula cell refuses the file, because its
  cached value may be stale or absent.
- **Instants** are ISO 8601 UTC text with a `Z` suffix and whole seconds,
  such as `2026-10-03T14:05:09Z`, never Excel date cells, which carry no
  time zone and are changed by locale conversions.
- **Local dates** are ISO 8601 date text (`2026-10-03`) in the campaign's
  time zone (metadata `timezone`).
- **Campaign day** is an integer: day 1 is `campaign_start_date`, the first
  invitation day, and day N is the local date N − 1 days later.
- **Money** is a number cell in US dollars with at most two decimals.
- **Booleans** are boolean cells (`TRUE`/`FALSE`); in `metadata`, where every
  value is text, the text `TRUE` or `FALSE`.
- **Enumerations** are the lowercase words listed below.
- **Blank means unknown or not applicable, never zero.** A zero is written
  as `0`.
- **Metadata values** are text: numbers as plain digits, dates and instants
  in ISO 8601. The reader also accepts a number cell for a numeric key.

### Sheets

| Sheet | One row per | Used by the comparison import |
| --- | --- | --- |
| `README` | Line of text | Presence only |
| `families` | Attributed Family (its last submission) | Totals; Family rows optionally |
| `submissions` | Source submission row, superseded ones included | Cross-checks only |
| `daily` | Campaign day | Stored |
| `ministry_responses` | Ministry answer of a Family's last submission | Header and types only |
| `metadata` | Key | Stored in part |

`README` holds a plain-language description of the file, the privacy
warning, the conventions, and the generated dictionary of every sheet,
column and metadata key. It has no header-row rule.

#### `families` sheet

One row per attributed Family, holding only its last submission. Rows with
no Family identity are not here; they stay in `submissions` with
`attributed` `FALSE`.

| Column | Type | Rule |
| --- | --- | --- |
| `family_key` | Text | Unique in the file: `duid:<n>`, `pds_fid:<n>` or `pds_env:<n>`, matching `family_id_system` |
| `family_id` | Integer or blank | The number in `family_key`; blank for an envelope-only key |
| `family_id_system` | Enumeration | `parishsoft_duid`, `pds_fid` or `pds_envelope`. PDS ids (2020–2024) and ParishSoft DUIDs (2025 on) are different id spaces |
| `envelope_id` | Text or blank | Old PDS envelope number (2020–2024 only) |
| `submission_count` | Integer ≥ 1 | Attributed submissions the Family made, all forms |
| `first_submitted_at` | Instant | First submission |
| `first_local_date` | Local date | Of the first submission |
| `first_campaign_day` | Integer ≥ 1 | Of the first submission |
| `last_submitted_at` | Instant | Last submission, not before the first; only it counts |
| `last_local_date` | Local date | Of the last submission |
| `last_campaign_day` | Integer ≥ 1 | Of the last submission |
| `pledge_status` | Enumeration | `pledged`, `zero`, `cannot_give`, `no_answer` (financial question left blank) or `no_pledge_form` (2020 only: the Family sent only the separate ministry form) |
| `annual_pledge` | Money or blank | Greater than zero exactly when `pledged`; `0` when `zero`; `0` or blank when `cannot_give`; blank otherwise |
| `frequency` | Enumeration or blank | `weekly`, `monthly`, `quarterly`, `annual`, `none` or `other` |
| `frequency_detail` | Text or blank | The choice as the form worded it; free-text answers only as `other (free text omitted)` |
| `installment_amount` | Money or blank | Per-installment amount |
| `installment_source` | Enumeration or blank | `form` (the form's own computed column) or `computed` (annual ÷ 52, 12, 4 or 1); blank exactly when `installment_amount` is blank |
| `cannot_give` | Boolean or blank | The "cannot contribute financially" box; blank where the form had none |
| `prior_year_pledge` | Money or blank | Previous year's pledge as pre-filled on the form (old system only) |
| `prior_year_contributions` | Money or blank | Previous year's contributions as pre-filled on the form (old system only) |
| `pledge_parse_note` | Text or blank | Set when the entered pledge text needed interpretation |

#### `submissions` sheet

Every source row, superseded submissions included, so a day's cumulative
pledge can use each Family's latest answer as of that day.

| Column | Type | Rule |
| --- | --- | --- |
| `row_id` | Integer | 1, 2, 3 …, oldest submission first |
| `family_key` | Text or blank | As in `families`; blank exactly when `attributed` is `FALSE` |
| `form` | Enumeration | `combined`, `pledge` or `ministry` (2020 used two forms) |
| `submitted_at` | Instant | |
| `local_date` | Local date | |
| `campaign_day` | Integer ≥ 1 | |
| `annual_pledge` | Money or blank | The annual pledge in this submission |
| `frequency` | Enumeration or blank | As in `families` |
| `cannot_give` | Boolean or blank | As in `families` |
| `is_last_for_family` | Boolean | `TRUE` for the Family's last submission of this form |
| `attributed` | Boolean | `FALSE` for a row with no Family identity, which no count or total includes |

#### `daily` sheet

The day-by-day series, from day 1 through the day of
`campaign_last_submission_date`. For a campaign run in this system it equals
the [campaign daily report facts](../data/spec.md#campaign-daily-report-facts)
historical series day for day.

| Column | Type | Rule |
| --- | --- | --- |
| `campaign_day` | Integer | 1, 2, 3 … with no gap |
| `local_date` | Local date | `campaign_start_date` + `campaign_day` − 1 |
| `first_submissions` | Integer ≥ 0 | Families whose first submission fell on this day |
| `cumulative_families` | Integer ≥ 0 | Distinct Families that had submitted by the end of this day |
| `cumulative_pct_of_active` | Number or blank | `cumulative_families` ÷ `active_families`, as a fraction from 0 to 1; blank exactly when `active_families` is blank (an estimate never fills it) |
| `cumulative_pledging_families` | Integer ≥ 0 | Families whose latest answer by the end of this day pledged more than zero |
| `cumulative_annual_pledged` | Money ≥ 0 | Sum of each Family's latest annual pledge at the end of this day; a revision replaces, never adds |
| `source` | Text | `derived` (recomputed from `submissions`), or a note naming the system table it equals |

#### `ministry_responses` sheet

| Column | Type | Rule |
| --- | --- | --- |
| `family_key` | Text | A `families` key |
| `member_slot` | Integer ≥ 1 | Member position on the Family's form |
| `member_id` | Integer or blank | Old PDS member id or ParishSoft Member DUID |
| `ministry` | Text | Ministry as labelled on the form (old system) or ParishSoft Ministry id (this system) |
| `ministry_code` | Text or blank | Leading three-digit Ministry number when the label has one |
| `response` | Text | `participate`, `interested` or `not_involved` (old system, a full status per Ministry); `join` or `leave` (this system, which records changes only); other text verbatim |

The comparison report does not use Ministry answers. The reader checks this
sheet's header and the types of `family_key` and `member_slot`, and the
import stores nothing from it.

#### `metadata` sheet

Columns `key`, `value`, `note` (the note is provenance or explanation). Keys
are unique. Required keys, each with a value unless marked:

| Key | Value |
| --- | --- |
| `schema` | `stewardship-campaign-archive` |
| `schema_version` | `1.0` |
| `campaign_name` | Such as `Stewardship 2027`; the name carries the pledge year |
| `campaign_year` | The pledge year (the year the pledges are for), 2000–2100; a campaign held in autumn 2026 is 2027 |
| `is_final` | `TRUE` when the campaign had closed when exported; `FALSE` for a partial export |
| `as_of` | Instant the data describes; for a final file, the last submission. Not before any attributed `submitted_at` |
| `timezone` | IANA zone used for local dates and day numbers |
| `campaign_start_date` | Local date of day 1 |
| `campaign_last_submission_date` | Local date of the last attributed submission |
| `active_families` | Active parishioner Families, the percentage denominator; may be blank when unknown |
| `active_families_provenance` | Where `active_families` came from, or why it is unknown (and where the estimate came from) |
| `active_families_estimate` | Best estimate when `active_families` is blank; blank otherwise |
| `source_system` | The system that collected the responses |
| `source_files` | Names of the original files converted |
| `converted_at` | Instant the file was produced |
| `converter` | The program that produced it |
| `dedup_rule` | How repeat submissions were reduced to one per Family |

Optional, repeatable keys: `column_mapping.<nn>` (one per source column used,
with the archive column it fed), `note.<nn>` (conversion notes and
assumptions) and `count.<name>` (aggregates for quick checks). The reader
checks the counts it knows (`count.families` = `families` rows,
`count.rows_read` = `submissions` rows, `count.unattributed_rows` =
unattributed `submissions` rows, `count.ministry_response_rows` =
`ministry_responses` rows) and keeps any other `count.*` (such as
`count.active_now`) as a note.

### Privacy of the file

The workbook omits names, email addresses, postal addresses, phone numbers,
IP addresses, comments, talents, free-text answers, Family codes and every
credential or edit link. It still holds Family-level giving, so it is private:
it belongs only in the parish's restricted archive, and the README sheet says
so. The reader refuses a data-sheet column that its declared minor version
does not define (a column a later minor version added is ignored, as above),
so a hand-edited file cannot slip a column of personal data into storage.

## Import

### Past campaigns page

**Access:** Administrators only (`CONFIGURE` and `FINANCIAL_DETAIL`). Staff
and Ministry leaders never see the entry or the page.

The page is **Past campaigns** at `/admin/parish/past-campaigns/`, in the
Parish data menu group (a parish record, not part of one campaign; see
[menu groups](../admin-portal/spec.md#menu-groups)). It leads with a table of
imported campaigns, newest year first: Year, Campaign, Final or Partial, Day
1, Days, Families responded, Annual pledges, Active Families (with ≈ for an
estimate), Family rows kept (Yes/No), Imported (local time, by whom), and a
Remove row action. Below it, **Add archive files** takes one or more `.xlsx`
files and a **Keep Family rows** checkbox, ticked by default, whose field tip
says the rows are needed for the renewal figure and never leave the Admin
portal. The About this page panel explains the archive files, where they come
from and the privacy rule.
The slice that adds this page, and the one that adds the report, add their
rows to the admin-portal [menu groups](../admin-portal/spec.md#menu-groups)
table and its page-name placement table.

Adding files follows [Review, Apply and Change
status](../ui-conventions/spec.md#review-apply-and-change-status):

1. **Review** uploads the files and validates each one (below). It shows one
   card per file: the year, campaign name, final or partial, day-1 date,
   last day, Families responded, annual pledges, the active-Family count with
   its provenance, the metadata notes that the report's data notes will show,
   and what Apply will do (Add, Replace year N, or Already imported). A
   refused file shows its problems instead and is left out of Apply. Review
   is unavailable until a file is chosen ([prerequisite
   gates](../ui-conventions/spec.md#prerequisite-gates)).
2. **Apply** stores every accepted file in one transaction per file and
   updates the table [in place](../ui-conventions/spec.md#in-place-controls).
   Replacing a year needs its own acknowledgement checkbox on the card ("This
   replaces the 2025 figures already imported"), so Apply stays unavailable
   until each replacement is acknowledged.

**Remove** is a [row action with
confirmation](../ui-conventions/spec.md#row-actions-and-confirmation): it
deletes that year's stored figures and Family rows, keeps a tombstone (year,
checksum, counts, who and when), and the report stops showing the year. The
file can be imported again later.

### Validation

The import validates a file completely before storing anything. In order:

1. **Size and package.** At most 20 MB uploaded and 300 MB uncompressed,
   checked from the zip directory before parsing; the hosted-files OOXML
   checks; at most 500,000 data rows in all. (The converted 2020–2026 files
   are about 2 MB, 35 MB uncompressed and 100,000 rows each, most of them
   Ministry answers.)
2. **Schema.** The first sheet is `README`; `metadata` has the required keys;
   `schema` and the major version match; every data sheet exists with its
   header row exactly as above.
3. **Cells.** Every cell has its column's type, range and enumeration; no
   formula cells; required cells are not blank; the conditional rules in the
   column tables hold (such as `annual_pledge` against `pledge_status`, and
   `installment_source` against `installment_amount`).
4. **Consistency.**
   - `daily` runs from day 1 with no gap to the day of
     `campaign_last_submission_date`; each `local_date` matches its day;
     `cumulative_families` is the running sum of `first_submissions`;
     cumulative Families never decrease.
   - The last `daily` row equals the totals recomputed from `families`:
     `cumulative_families` = the number of `families` rows;
     `cumulative_pledging_families` = rows with `pledge_status` `pledged`;
     `cumulative_annual_pledged` = the sum of their `annual_pledge`, to the
     cent.
   - `families` keys are unique, and each key matches its `family_id_system`
     and `family_id`. Each Family's `last_submitted_at` is the latest
     `submitted_at` of its attributed `submissions` rows, its
     `submission_count` is their number, and it has at most one
     `is_last_for_family` `TRUE` row per `form`. Every attributed
     `submissions` and `ministry_responses` key is a `families` key.
   - `cumulative_pct_of_active` is present exactly when `active_families`
     is, and equals `cumulative_families` ÷ `active_families` within 0.0005.
     `active_families_estimate` is given only when `active_families` is
     blank.
   - Every `count.*` key the reader knows equals its recomputed value.
   - `as_of` is not before the last attributed submission.
5. **Year.** A file whose `campaign_year` is the current campaign's
   stewardship year is refused: the report reads the current campaign live,
   and a second copy of it would be compared with itself. The current
   campaign's stewardship year is the one campaign-year meaning the Admin
   pages already use (`campaign_year` in `web/presentation.py`: the
   Campaign settings year label, else the upcoming financial period's start
   year, else the start date's year). When that is not a single four-digit
   year (a label such as "2026–2027"), every import is refused with that
   reason, because "last year" cannot be resolved.

Problems are listed per file, up to 50, each with its sheet, cell reference
and rule ("families!J17: annual_pledge must be greater than zero when
pledge_status is pledged"). A problem never repeats a cell's value, which
may be a Family identifier or amount. The list says when more problems were
cut off.

### Idempotency and replacement

The SHA-256 of the uploaded bytes identifies the file:

- A file whose checksum matches a stored, not removed import is **Already
  imported**: Apply changes nothing and says when and by whom it was
  imported. Repeating an Apply, from the page or the command line, is
  therefore harmless.
- One import is active per `campaign_year`. A different file for a year that
  already has one is a **Replace**: Apply removes the old import (as Remove
  does) and stores the new one in the same transaction. A partial file never
  replaces a final one; Review refuses that with the reason.
- Two Applies racing for the same year or checksum are settled by the
  database's unique indexes: the loser is refused as stale and nothing
  changes.

### What is stored

A forward migration adds three tables under the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy),
with one frozen SQL file named by the repository-wide sequence. They are
parish records, not campaign records: a campaign purge never touches them
([retention and deletion](../data/spec.md#retention-and-deletion)).

- **`stewardship_campaign_archive`**, one row per import: UUID, SHA-256,
  cleaned original file name, size, schema version, campaign name and year,
  final flag, `as_of`, time zone, day-1 date, last submission date, active
  count, estimate and provenance, source system, `dedup_rule`, the Family
  id system(s), whether Family rows were kept, the `note.*` rows and unknown
  `count.*` rows (as a bounded JSON object, for the data notes), the
  recomputed totals, the previewing and importing users and times, and state
  `pending`, `active` or `removed` with who removed it and when. A trigger
  refuses every change except `pending` → `active` and `active` →
  `removed`; partial unique indexes keep one active row per checksum and per
  year. `column_mapping.*`, `source_files` and `converter` are conversion
  detail for the file's own readers and are not stored.
- **`stewardship_campaign_archive_day`**, the `daily` sheet: archive, day,
  local date, first submissions, cumulative Families, cumulative pledging
  Families and cumulative annual pledged (`numeric(18,2)`). Percentages are
  derived when read, never stored.
- **`stewardship_campaign_archive_family`**, only when Keep Family rows is
  ticked, for Families with a `family_id` (envelope-only Families cannot be
  matched across years): archive, `family_id_system`, `family_id`, first and
  last campaign day, `pledge_status` and `annual_pledge`. Nothing else from
  the Family row is kept (no envelope number, no amounts shown on the form,
  no installments, no timestamps, no notes), and nothing from `submissions`
  or `ministry_responses`.

Rows are inserted once and never updated; Remove deletes the day and Family
rows and marks the archive row removed. The uploaded bytes are not kept: the
parish's archive folder holds the file, and the checksum identifies it.
Review inserts the validated rows under a `pending` archive row, kept for
the [preview lifetime](../admin-portal/spec.md#preview-lifetimes); Apply
makes it `active`, and housekeeping deletes expired pending rows with their
children. Pending rows are never read by the report.

### Privacy

- Only aggregates leave the Admin portal: report pages, digests, downloads
  and command output carry counts, sums, averages and percentages, never a
  Family row or identifier.
- Stored Family rows are read only inside the database, by the renewal
  calculation, which returns counts.
- The audit trail records each preview, import, replacement and removal with
  the year, checksum, row counts and outcome, never a Family value.

## Comparison report

**Access:** Administrator and Staff (`CAMPAIGN_REPORT`). Pledge figures
appear only when the current campaign has the financial module, as on the
[participation graph](../reports/spec.md#participation-graph). Ministry
leaders never see it.

The page is **Campaign comparison** at `/admin/reports/comparison/`, in the
Responses and reports menu group after Participation. It is unavailable
when there is no current campaign. With no imported years it shows the
current campaign only, with one line saying no past campaigns are imported
and, for an Administrator, a link to Past campaigns.

### Alignment by campaign day

Campaigns are compared by **campaign day**, not calendar date, because past
campaigns started on dates from September 11 to October 5.

- For an imported year, day N is its `daily` row N.
- For the current campaign, day 1 is the campaign's local start date, the
  first date of its daily report facts, and day N comes from the ready
  Historical-as-of-day fact set row for local date start + N − 1.
- **The comparison day** is the current campaign's last complete local day:
  the day before today in the campaign's time zone, as the
  [daily digest's](../background-processing/spec.md#daily-campaign-digest)
  report day is. Before day 1 has ended the page shows the imported years'
  final results only.
- An imported year that ended before day N contributes its final values from
  its last day onward and is marked "(final)" at that point.

### Metrics

At the comparison day N, for the current campaign, the same day last year,
and the average of all imported years:

| Figure | Current campaign | Imported year |
| --- | --- | --- |
| Families responded | Cumulative responses of the fact row for day N | `cumulative_families` of day N |
| Percent of active Families | Cumulative responses ÷ the fact row's cohort denominator | `cumulative_families` ÷ active count, or ÷ estimate (shown ≈) |
| Annual pledges | Pledge total of the fact row | `cumulative_annual_pledged` of day N |
| Average pledge | Annual pledges ÷ pledging Families as of day N's end | `cumulative_annual_pledged` ÷ `cumulative_pledging_families` |
| Renewal of last year's responders | Last year's responding Families (by ParishSoft DUID) with a first live response by day N's end, ÷ last year's responding Families | The same for that year against its previous year, when both kept Family rows in the same id system |

- **Last year** is the imported year one less than the current campaign's
  stewardship year. Without it, the "last year" lines are left out.
- **Prior-years average** is the mean over every active imported year with a
  value at day N. A percentage average uses only years with an active count
  or estimate and is marked ≈ when any estimate went into it. Renewal has
  no average unless at least two imported years can produce it.
- **Estimates are marked ≈** wherever an estimated active count went into a
  figure, on the page, in tooltips, tables, downloads and the digest, with a
  data note saying which years and why.
- Comparisons are shown as a relative change for counts and money ("+36%")
  and as percentage points for percentages ("+4.2 points"), always with an
  arrow and the words "more" or "fewer", so color is never the only signal.
- Money follows [numbers and money](../ui-conventions/spec.md#numbers-and-money);
  the tiles round to whole dollars and the table keeps cents.
- "Pledging Families as of day N's end" and the current renewal count need a
  read of each Family's latest live submission version committed by that
  instant, the rule the participation graph's pledge total already follows.
  They come from one new narrow SQL function in the same migration, not from
  a second implementation in Python.
- A figure that cannot be computed shows "—" with a toggletip giving the
  reason (no active count, no Family rows for last year, different id
  systems, financial module off).

### Page layout

The page follows [page help](../ui-conventions/spec.md#page-help): data
first, explanation in the About this page panel.

1. **Heading and data line:** "Day 8 of Stewardship 2027 · through Saturday,
   October 10", with the last-year and average years named ("compared with
   2026 and the 2020–2026 average").
2. **Headline tiles**, one per metric above, in that order. Each shows the
   current figure large, then two lines: "Last year on day 8: 215 (36% more)"
   and "2020–2026 average: 197 (48% more)". The full wording sits in the
   tile's tooltip and accessible name.
3. **Day-by-day charts**, through the [chart
   engine](../reports/spec.md#chart-engine), one per measure, each with its
   summary and exact-values table: cumulative Families responded, percent of
   active Families, and cumulative annual pledges. The x-axis is campaign day
   ("Day 1 = first invitation day"). The current campaign is drawn in the
   accent color, thick and solid; last year in the second color, dashed;
   every other year thin and muted grey with its year labeled at the line's
   end; the prior-years average dark and dash-dotted. A vertical rule marks
   day N. Tooltips name the year, day, local date and value.
4. **Final results by year:** two bar charts, Families responded and annual
   pledges, one bar per imported year plus the current campaign drawn
   lighter and labeled "so far".
5. **Every number:** a table with one row per year (current first): year,
   day 1 date, days, Families at day N, percent at day N, pledges at day N,
   average pledge at day N, renewal at day N, final Families, final percent,
   final pledges and final average pledge.
6. **Data notes:** for each year, its source system, the active count's
   provenance (or the estimate's), the dedup rule and its `note.*` rows,
   escaped and each cut to 500 characters.

A **Compare on day** control, an [in-place
control](../ui-conventions/spec.md#in-place-controls) over the closed range 1
to the current comparison day, moves N; the URL carries only `day=<integer>`,
a non-identifying value as the [shared report
behavior](../reports/spec.md#shared-report-behavior) allows. Any other value
is refused.

Each view reads under the campaign's
[read guard](../data/spec.md#campaign-read-guards), sends `Cache-Control:
no-store` (the figures are financial), and records one
`campaign_comparison_viewed` audit event with the day and outcome, no
figure. If the current fact set is not ready, the page shows the last ready
one with the participation graph's **Updating** notice.

### Downloads and sharing

The page's data downloads as CSV, XLSX and PDF under the [downloaded files
rule](../ui-conventions/spec.md#downloaded-files): the "Every number" table
in all three, and the PDF adds the tiles and charts on one landscape page in
the shared [file design](../reports/spec.md#file-design), so the
Administrator can send leadership a single summary. Downloads hold
aggregates only and are audited like other exports. No public or
unauthenticated link exists.

### Digest section

The daily and weekly Administrator digests may include a **Compared with
past years** section, off by default and turned on per digest schedule on
[Dates and mail schedules](../admin-portal/spec.md#campaign-configuration).
It follows the digest's #720 style: the cumulative Families chart and the
cumulative pledges chart first (PNG through the chart engine's server
renderer, with alt text carrying the figures), then one line per headline
figure with its last-year and average comparisons, then a link to the
report. Its comparison day is the digest's report day. The occurrence
records the ids and checksums of the imported years it used together with
its pinned fact set, so the saved digest reproduces its figures after a later
import or removal; a year removed since then shows on the saved page as
"removed since this report". With no imported years the section is left
out.

## Command line

The Admin automation interface's
[rules for new Admin actions](../admin-automation/spec.md#rules-for-new-admin-actions)
apply: each page action lands with its service function and command, under
the same checks and audit as the page.

| Command | Scope | What it does |
| --- | --- | --- |
| `pk-admin history list` | read-only | Imported years with the page's columns, checksums and states; no Family data |
| `pk-admin history preview - [--no-family-rows]` | full | Reads one workbook's bytes from standard input (the wrapper forwards input because of the `-`), validates it and prints the Review card's figures, its problems and `preview.token`; `--no-family-rows` is the unticked Keep Family rows box |
| `pk-admin history confirm --token <token>` | full | Applies the preview; a replacement asks the page's acknowledgement at the prompt (`--yes` answers it) |
| `pk-admin history remove <year> --expected-version <n>` | full | Removes a year, after the confirmation prompt |
| `pk-admin report comparison [--day N]` | read-only | The page's figures for day N as JSON |

They follow [preview and confirm](../admin-automation/spec.md#preview-and-confirm)
and [personal data on the command line](../admin-automation/spec.md#personal-data-on-the-command-line):
output holds counts, sums and states only. Validation problems name cells,
never their values.

An operator command, `pk-stewardship archive-check <file>…`, validates
workbooks offline with the same reader and prints each file's summary or
problems. It needs no database or session, so the Administrator can check
converted files before importing them.

## Failure handling

| Situation | Behavior |
| --- | --- |
| File not an `.xlsx`, too large, macro-bearing, or unreadable | Refused at Review with the reason; nothing stored |
| Wrong schema, version, sheet or header | Refused with the sheet and expected header |
| Cell or consistency errors | Refused with up to 50 located problems |
| Same checksum already imported | Already imported; Apply changes nothing |
| Year already imported, different file | Replace, with acknowledgement; a partial never replaces a final |
| Current campaign's year | Refused with the reason |
| Two Applies race | One wins; the other is refused as stale |
| Preview expired | Refused as stale; Review again |
| Apply fails part-way | That file's transaction rolls back; files applied before it stay |
| Fact set not ready | Last ready fact set with the Updating notice |
| No last year, no active count, no Family rows | The affected figure shows "—" with its reason |
| Financial module off | Pledge figures, charts and columns are left out |
| Checking a file exceeds its 60-second limit | Stopped, logged as a durable timeout entry naming the limit and the elapsed time, and refused as too large to check |

## Tests

Normal CI uses synthetic workbooks only, generated by the workbook module's
writer from small invented data sets; no real archive or Family figure is
committed.

- **Format table:** the README dictionary, the reader's header checks and
  this specification's column and metadata tables agree; the test parses the
  tables under [archive workbook format](#archive-workbook-format) and
  compares them with the module's table.
- **Reader round trip:** a written workbook reads back to the same values;
  each convention (ISO text instants, money to the cent, `TRUE`/`FALSE`,
  blank versus zero) has a case.
- **Refusals:** one case per validation rule, including macros, formula
  cells, a missing or reordered column, an unknown column, a later minor
  version's extra column (accepted and ignored), a wrong major version, a
  `daily` gap, totals that disagree with `families`, duplicate Family ids,
  a wrong `count.*`, the current campaign's year, and a size or row limit;
  each asserts that no cell value appears in the problem text.
- **Import service (PostgreSQL):** add, already imported, replace, partial
  refused over final, remove and re-import, racing Applies, expired preview
  cleanup, Keep Family rows off, the immutability trigger, and a campaign
  purge leaving the tables untouched.
- **Migration:** the frozen file's digest, its closing `DO` block, the
  migration chain and upgrade parity, per the post-launch schema policy.
- **Metrics:** day alignment with years of different lengths and start
  dates; final values carried forward; the average over years present at day
  N; ≈ propagation from estimates; percentage points versus relative change;
  average pledge; renewal against a DUID year, and "—" against a PDS-id year
  or a year without Family rows; current figures equal the participation
  graph's for the same day.
- **Pages:** role access (Staff see the report, not Past campaigns; Ministry
  leaders see neither), Review/Apply in place, the replacement
  acknowledgement gate, the Compare on day control without reload, the
  `day` value range, `no-store`, the audit events, and the chart
  accessibility checks of the chart engine in a browser test.
- **Downloads and digest:** the three formats carry the same columns and
  aggregates only; a digest with the section reproduces its figures after a
  later import and a removal.
- **Command line:** each command through the page's service, the catalog
  entries, and the absence of Family values in output.
- **Human-run check, outside CI:** `archive-check` accepts the seven
  converted 2020–2026 files, and an import of them reproduces the day-8
  figures reported on #1018. The files are private and are never committed.

## Implementation slices

Each slice is one PR of the
[automated delivery cycle](../../../plans/stewardship/overall.md#automated-phase-delivery-cycle),
linked to #1018 (the last one closes part (b)).

1. **Workbook module and offline check (first, within days).** The format
   table (taken from the one-time converter's table, which holds no data),
   reader and validator, a minimal writer used by tests (part (a) later
   fills it with real data), and `pk-stewardship archive-check`. No schema
   change. The Administrator can check the seven converted files at once.
2. **Storage and command-line import.** The forward migration, the import
   service with preview, confirm and remove, and the `pk-admin history`
   commands. Past years can be loaded by the operator the same day.
3. **Comparison figures.** The metric service, the SQL function for pledging
   Families and renewal, `pk-admin report comparison`, and the report page
   with tiles, the Every number table and data notes. Leadership gets
   current comparisons here.
4. **Past campaigns page.** The upload page with Review, Apply, replacement
   and Remove.
5. **Charts and day control.** The three day-by-day charts, the final-results
   bars and Compare on day.
6. **Downloads.** CSV, XLSX and the one-page PDF summary.
7. **Digest section.** The daily and weekly digest option, with its own
   forward migration if the digest occurrence needs new columns to record
   the imported years it used.

Slices 4 to 7 depend on 3 but not on each other. UI slices get the
human pk-local check that significant new UI needs.

## Open questions

The Administrator may change these defaults; the implementation proceeds with
them until decided.

1. **Who sees the report.** Default: Administrators and Staff, as for
   Participation. Alternative: Administrators only.
2. **Keep Family rows.** Default: ticked, because the renewal figure needs
   them; only the id, first and last day, pledge status and annual pledge are
   kept.
3. **Keep the uploaded file.** Default: no; the parish's archive folder is
   the file's home and the checksum identifies it.
4. **Removal.** Default: an Administrator may remove an imported year,
   leaving a tombstone. Alternative: imports can never be removed.
5. **Current campaign's own archive.** Default: refused, since the report
   reads the current campaign live.
6. **Day 1 of the current campaign.** Default: the campaign's local start
   date, which is the first invitation day this year. If a later campaign
   starts before its first invitation, day 1 could instead be the first
   Initial invitation send date.
7. **Prior-years average.** Default: every imported year, estimates
   included and marked ≈. Alternative: only years with a known active count,
   or only the last five years.
8. **Average pledge.** Default: annual pledges divided by Families pledging
   more than zero. Alternative: divided by all responding Families.
9. **Digest section.** Default: off; when on, in the daily digest only.
10. **Sending to leadership.** Default: the one-page PDF download, which the
    Administrator emails. No public link.
11. **Upload limits.** Default: 20 MB per file, 300 MB uncompressed and
    500,000 rows, about ten times the largest converted file.
