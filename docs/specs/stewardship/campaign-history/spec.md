# Stewardship campaign history and comparison

This specification defines how the system keeps a durable record of past
campaigns and compares the current campaign with them
([#1018](https://github.com/epiphany40223/parishkit/issues/1018)). It has two
parts that share one file format:

- the **campaign archive workbook**, one XLSX file per campaign, defined
  once, here, in [archive workbook format](#archive-workbook-format); and
- the **campaign comparison report**, which imports archive workbooks of past
  campaigns and lines them up with the current campaign day by day.

The importer reads exactly the format the end-of-campaign
[archive export](#archive-export) writes. That export (#1018 part (a)) is
roadmap Tier 3: its Download action and command come first, and the campaign
close-out wizard
([#527](https://github.com/epiphany40223/parishkit/issues/527)) later uses
them to produce the final file. The comparison report and its import page
are roadmap Tier 2 (Administrator decision, 2026-10-11): leadership wants
updated comparisons before the current campaign ends.

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
[report email block](#digest-section) from the Administrator. The report
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
campaigns produced (October 11, 2026), with one change: `active_families`
is required and the `active_families_estimate` key is gone. Those files
are regenerated once to match (all seven carry the old key, and 2020–2022
gain their measured counts), after which they import unchanged.
The reader ([import](#import)) and the writer
([archive export](#archive-export)) are one module, `parishkit.stewardship.archive.workbook`, that owns a
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
- A plain `.xlsx` package of cell data only. The reader applies the
  [OOXML package checks](../hosted-files/spec.md#accepted-types-and-validation)
  of hosted files, which refuse macros, ActiveX and OLE objects, and then its
  own stricter rule, because those checks do not cover workbook links and
  data connections: every part must be on an allowlist, and every
  relationship must be internal. The allowlist is the parts a spreadsheet
  program writes for plain cell data: `[Content_Types].xml`, `_rels/.rels`,
  `docProps/app.xml`, `docProps/core.xml`, `docProps/custom.xml`,
  `customXml/` items, `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`,
  `xl/worksheets/sheetN.xml`, `xl/worksheets/_rels/sheetN.xml.rels` (only
  for printer settings), `xl/printerSettings/printerSettingsN.bin`,
  `xl/styles.xml`, `xl/theme/themeN.xml`, `xl/sharedStrings.xml` and
  `xl/calcChain.xml`. Everything else is refused, including external
  workbook links (`xl/externalLinks/`), data connections
  (`xl/connections.xml`) and query tables (`xl/queryTables/`), embedded
  objects and packages (`xl/embeddings/`), drawings, charts, images, pivot
  tables, slicers and comments, as is any relationship with
  `TargetMode="External"` (a hyperlink or a remote source). A file corrected
  by hand and saved again by Excel, LibreOffice or Google Sheets stays within
  the allowlist.
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
- **Integers** are number cells with a whole value (a stored `12.0` reads as
  12); text cells of digits are refused.
- **Text** columns take text cells, or number cells with a whole value read
  as their digits (`envelope_id`, `member_id`, `ministry` and
  `ministry_code` may hold either). Text, metadata values and notes
  included, holds no C0 control character other than tab and newline (NUL
  cannot be stored); one is a located problem.
- **Instants** are ISO 8601 UTC text with a `Z` suffix and whole seconds,
  such as `2026-10-03T14:05:09Z`, never Excel date cells, which carry no
  time zone and are changed by locale conversions.
- **Local dates** are ISO 8601 date text (`2026-10-03`) in the campaign's
  time zone (metadata `timezone`).
- **Campaign day** is an integer: day 1 is `campaign_start_date`, the first
  invitation day, and day N is the local date N − 1 days later.
- **Money** is a number cell in US dollars with at most two decimals: the
  shortest decimal text of the stored number (Python's `repr` of the
  double) has at most two digits after the point, and the reader converts
  that text to an exact decimal, never through binary arithmetic.
- **Booleans** in data sheets are boolean cells (`TRUE`/`FALSE`); in
  `metadata`, where values are text, the text `TRUE` or `FALSE` or a
  boolean cell.
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

**Last submission.** Submissions are ordered by `submitted_at`, then by
`row_id` to break a tie. A Family's `last_submitted_at` is its latest
attributed submission of any form. Its financial answer (`pledge_status`,
`annual_pledge`, `frequency`, the installment, `cannot_give`) comes from
its latest attributed submission of a form that asked the financial
question (`combined` or `pledge`); a Family with none is `no_pledge_form`.
Only 2020 had two forms, so in every other year both are the same
submission. The `daily` sheet's "latest answer by the end of a day" uses the
same ordering over submissions with `campaign_day` up to that day.

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
| `installment_amount` | Money ≥ 0 or blank | Per-installment amount |
| `installment_source` | Enumeration or blank | `form` (the form's own computed column) or `computed` (annual ÷ 52, 12, 4 or 1); blank exactly when `installment_amount` is blank |
| `cannot_give` | Boolean or blank | The "cannot contribute financially" box; blank where the form had none |
| `prior_year_pledge` | Money ≥ 0 or blank | Previous year's pledge as pre-filled on the form (old system only) |
| `prior_year_contributions` | Money or blank | Previous year's contributions as pre-filled on the form (old system only); may be negative after refunds or adjustments |
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
| `annual_pledge` | Money ≥ 0 or blank | The annual pledge in this submission |
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
| `cumulative_pct_of_active` | Number | `cumulative_families` ÷ `active_families`, as a fraction from 0 to 1 |
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
| `active_families` | Active parishioner Families at the campaign, the percentage denominator: a measured count, an integer ≥ 1. Required; an estimate is never accepted |
| `active_families_provenance` | The source the count was measured from (such as a database snapshot of the old system, or this system's Family cohort) |
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

## Archive export

The archive export (#1018 part (a), roadmap Tier 3) writes the
[archive workbook](#archive-workbook-format) of a campaign run in this
system, so the next comparison needs no conversion. The writer is the
reader's module: the README, sheet order, headers and conventions all come
from its one table, and every file is read back by the reader before it is
offered ([checks before a file is offered](#checks-before-a-file-is-offered)).
Nothing here changes the format; a file this system writes imports like any
converted one, once its campaign is no longer the current one
([decision 5](#administrator-decisions)).

Only the current campaign can be exported. Until the single-campaign model
([#145](https://github.com/epiphany40223/parishkit/issues/145)) and the
close-out wizard ([#527](https://github.com/epiphany40223/parishkit/issues/527))
settle what happens to a finished campaign, the Administrator exports the
final file before the next campaign is created.

### What the export reads

One campaign's live submissions (Test submissions never) and its ready
historical [daily facts](../data/spec.md#campaign-daily-report-facts), read
inside one read-only, repeatable-read transaction up to a **cutoff**:

- The end of the last complete local day, the comparison report's
  [comparison day](#alignment-by-campaign-day), never today's unfinished
  day, and never past the end date.
- For a `closed` or `archived` campaign the file is final: `is_final`
  `TRUE`, named `stewardship-<year>.xlsx`. Once the end date has passed,
  the cutoff is the end of the end date. A campaign closed before its end
  date gets its final file cut off at its last full day, rather than
  waiting for the end date (Administrator decision, 2026-10-13, on
  [#1018](https://github.com/epiphany40223/parishkit/issues/1018); it
  replaces the earlier default that the final file waits for the end
  date). A final file is offered only while no live submission falls after
  its cutoff; otherwise Download is unavailable with "Available tomorrow:
  the campaign closed today, and today's responses are not in a complete
  day yet." This refines that decision (review, 2026-10-13) so that a
  "final" file never leaves out the closing day's responses and then
  changes the next day. No close time is stored, so the submission test
  is the exact rule: a campaign closed with no response that day is final
  at once, and any other waits at most until the next day.
- Otherwise the file is partial: `is_final` `FALSE`, named
  `stewardship-<year>-partial.xlsx`.

The request pins two things, which every attempt and retry use: the cutoff
instant, and the ready historical fact set checked against it, with that
set's submission watermark (the highest campaign sequence it read). The
export reads only live versions with a campaign sequence up to the
watermark **and** submitted before the cutoff, as the fact calculation
does. The watermark must cover every live version submitted before the
cutoff, so a download is offered only with a fact set whose watermark
does (the newest such set); while none does (a submission the facts have
not yet absorbed), the download is unavailable with that reason, since a
request pinned to a set that misses a response could never succeed. The
worker checks the coverage again and fails rather than write a file
without it. The request's pin on that fact set protects it from
[fact compaction](../data/spec.md#derived-fact-retention) until the
request's [download window](../operations/spec.md#point-in-time-exports) ends,
like any export's.

The Families are the [participation graph's](../reports/spec.md#participation-graph)
respondents: Families of the campaign's historical cohort with a live
version under those pins, the population the fact rows count, so `daily`
matches the fact series day for day. A live version of a Family outside
that cohort (none is expected) is left out of every sheet, counted in
`count.outside_cohort` and explained in a `note.*` row, which the reader
keeps as a note.

### What the export writes

| Sheet | Column | Source in this system |
| --- | --- | --- |
| `families` | `family_key`, `family_id`, `family_id_system` | `duid:<Family DUID>`, the DUID, `parishsoft_duid` |
| `families` | `envelope_id` | Blank (old PDS numbers only) |
| `families` | `submission_count`, first and last columns | The Family's live versions under the pins, in campaign-sequence order; dates and days in the campaign's time zone |
| `families` | `pledge_status`, `annual_pledge` | The last version's financial answer: `cannot_give` (with `0`) when the "cannot contribute" box was ticked, `pledged` above zero, `zero` at zero; `no_answer`, with every financial column blank, when the version holds no financial answer (its `annual_pledge` is null, as for every version when the campaign has no financial module) |
| `families` | `frequency`, `frequency_detail` | The answer's frequency word, blank when the answer has none; the detail is blank, since this system offers only the fixed choices |
| `families` | `installment_amount`, `installment_source` | The installment the Family form showed (annual ÷ 52, 12, 4 or 1, rounded half up) and `computed`; both blank without a frequency |
| `families` | `cannot_give` | The box's value, `TRUE` or `FALSE`; blank when the version holds no financial answer |
| `families` | `prior_year_pledge`, `prior_year_contributions`, `pledge_parse_note` | Blank (old-system columns; [open question 2](#open-questions)) |
| `submissions` | every column | One row per live version under the pins; `row_id` ordered by `submitted_at`, then campaign sequence; `form` `combined`; `attributed` `TRUE`, since every version has its Family |
| `daily` | every column | Derived from `submissions` by the format's own rule, with `source` naming `stewardship_daily_fact` once [checked](#checks-before-a-file-is-offered) against it |
| `ministry_responses` | every column | The Ministry join and leave choices of each Family's last version: `member_slot` is the Member's position on that version's form (existing Members in ascending Member DUID, the order the form's inputs carry, then proposed Members sorted by their local key; a person with no choice still takes a slot), `member_id` the Member DUID (blank for a proposed Member), `ministry` the ParishSoft Ministry DUID, `ministry_code` the leading three-digit number of the Ministry's name in the version's reviewed baseline source snapshot when it has one, and `response` `join` or `leave` |
| `metadata` | `campaign_name`, `campaign_year`, `timezone`, `campaign_start_date` | The campaign's name, stewardship year, immutable time zone and local start date |
| `metadata` | `as_of`, `campaign_last_submission_date` | For a final file the last submission's instant, for a partial file the cutoff; the local date of the last submission |
| `metadata` | `active_families`, `active_families_provenance` | The cohort denominator of the cutoff day's fact row, with the provenance "ParishKit Stewardship historical cohort: Families Portal-eligible at any time through day N" |
| `metadata` | `source_system`, `source_files`, `converter`, `converted_at` | "ParishKit Stewardship"; "none (read from the campaign database)"; the program and version; the request's time |
| `metadata` | `dedup_rule` | "Each Family's latest live submission version counts; earlier versions stay in submissions." |
| `metadata` | `count.*`, `note.*` | The four known counts, and `count.outside_cohort` when not zero; a note for each condition below that left a day's figure unchecked; no `column_mapping.*` rows, since no source columns were read |

The percentage column `cumulative_pct_of_active` divides by the one
`active_families` count, as the format requires, so it differs from the
participation graph's daily percentage, whose denominator grows through the
campaign; the [metrics](#metrics) note on denominators applies.

The format requires a number in `cumulative_annual_pledged`, so the file
always holds the sum of the Families' latest known pledges, where `no_answer`
Families add nothing. Without the financial module that is `0` on every
day, and a `note.*` row says the campaign collected no pledges.

### Checks before a file is offered

- **Facts.** For every day through the last `daily` day:
  `first_submissions` and `cumulative_families` equal the fact row's first
  responses and cumulative responses when the row's population is
  available; `cumulative_annual_pledged` equals the row's pledge total when
  that total is available (it is unavailable without the financial module,
  or when any responding Family's latest version holds no pledge); and no
  later fact row through the cutoff adds a response. An unavailable
  population or pledge total is not a disagreement: that figure is left
  unchecked for the day and a `note.*` row says how many days were. A
  disagreement fails the generation as a fact failure is failed (retried,
  then failed with its reason), so a file that disagrees with the report
  is never offered. The check also catches a Family whose first submission
  precedes its entry into the cohort, which the fact rows count from entry.
- **Reader.** The finished bytes are read back with the import's reader,
  limits and consistency checks included, in its isolated helper process,
  which never touches the database: after the read's transaction for the
  standalone writer, and, in the export worker, while the campaign read
  guard is still held (the guard must outlast every file write). A refusal
  fails the generation, naming the located problems in the log (never a
  cell value).
- **Reproducible bytes.** Every attempt and retry of one request writes the
  same bytes. The writer re-packs the workbook openpyxl saves, because
  openpyxl stamps the save time into `docProps/core.xml` and every ZIP
  entry: the parts are written in a fixed order with fixed compression, each
  ZIP entry's `date_time` is `as_of` in UTC, and `core.xml`'s created and
  modified times are rewritten to `as_of`. Exporting again is a new
  request with a new request time, so its `converted_at` and SHA-256
  differ; its publication records its own checksum.

### Download campaign archive

**Access:** Administrators only, as for Past campaigns (`CONFIGURE` and
`FINANCIAL_DETAIL`). The file holds Family-level giving, so creating it is
audited as the
[financial stewardship detail](../reports/spec.md#financial-stewardship-detail)
export is.

Past campaigns gets a section, **This campaign's archive**, below the
imported table. It names the current campaign, says whether a download now
would be final or partial and through which day, and offers **Download
campaign archive**. The About this page panel says that the file holds
Family-level giving, belongs only in the parish's restricted archive folder
in Google Drive, can be imported here once the next campaign is current,
and must be downloaded before the next campaign is created, since only the
current campaign can be exported. The action is unavailable, with the reason
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)), with
no current campaign, before day 1 has ended, or while no Family has
responded by the cutoff (an archive needs at least one Family).

The action queues the shared export lifecycle's `campaign_archive` kind,
XLSX only, and downloads in place, with the button busy until the file
arrives ([downloads happen in place](../ui-conventions/spec.md#downloads-happen-in-place));
its status address keeps the shared lifecycle (download, Cancel and
Retry as for every export, within the shared
[download window](../operations/spec.md#point-in-time-exports)). The worker
reads the submissions itself at generation, under the request's
[pins](#what-the-export-reads), so no Family-level capture table exists.
The request records `campaign_archive_exported` with the cutoff's campaign
day as `count`. No separate event records the file: the shared publication
row already holds its SHA-256 and row count (the `families` rows), and the
file's own `count.*` rows carry the per-sheet counts. Like every export,
the file is [point-in-time](../operations/spec.md#point-in-time-exports): the
parish keeps no copy, and the Drive
archive folder is the file's durable home.

The campaign close-out wizard (#527) produces the final file through this
action and reminds the Administrator to upload it to the archive folder.
Uploading it straight to Google Drive is a possible later addition with its
own issue.

### Open questions

Proposed on #1018; the work follows each default unless the Administrator
changes it:

1. **Where the action lives:** Past campaigns (default), next to the files
   it produces for later years; alternatively Campaign settings.
2. **Prior-year columns:** blank (default), as the format's rule says. The
   alternative fills them from the ParishSoft comparison-period aggregates
   the financial detail shows, which changes the rule and needs format
   version 1.1.
3. **When a file is final:** once the campaign is `closed` or `archived`
   (default); alternatively only after #527's close-out. A campaign closed
   before its end date is final through its last full day, once no
   response falls after it (Administrator decision, 2026-10-13, refined
   the same day; see [what the export reads](#what-the-export-reads)).
4. **Partial cutoff:** the last complete local day (default), as the
   comparison report uses.
5. **Proposed Members' Ministry choices:** included with a blank
   `member_id` (default).
6. **Retention:** the shared
   [point-in-time](../operations/spec.md#point-in-time-exports) download window
   (the Administrator's 2026-10-15 decision on
   [#1398](https://github.com/epiphany40223/parishkit/issues/1398) replaced
   the earlier shared retention); the Drive folder keeps the file, as
   [decision 3](#administrator-decisions) holds for imports.
7. **Which campaign:** only the current one (default) until #145 and #527;
   the final file must be downloaded before the next campaign is created.

## Import

### Past campaigns page

**Access:** Administrators only (`CONFIGURE` and `FINANCIAL_DETAIL`). Staff
and Ministry leaders never see the entry or the page.

The page is **Past campaigns** at `/admin/parish/past-campaigns/`, in the
Parish data menu group (a parish record, not part of one campaign; see
[menu groups](../admin-portal/spec.md#menu-groups)). It leads with a table of
imported campaigns, newest year first: Year, Campaign, Final or Partial, Day
1, Days, Families responded, Annual pledges, Active Families, Family rows
kept (Yes/No), Imported (local time, by whom), and a
Remove row action. Below it, **Add an archive file** takes one `.xlsx` file
and a **Keep Family rows** checkbox, ticked by default, whose field tip
says the rows are needed for the renewal figure and never leave the Admin
portal. The About this page panel explains the archive files, where they come
from and the privacy rule, and that a file of the full 20 MiB needs about
6 Mbit/s of upload speed (a typical 2 MB file needs well under 1 Mbit/s).
Several years are added one file at a time.
The slice that adds this page, and the one that adds the report, add their
rows to the admin-portal [menu groups](../admin-portal/spec.md#menu-groups)
table and its page-name placement table.

Adding a file follows the [review dialog](../ui-conventions/spec.md#review-dialog)
([#750](https://github.com/epiphany40223/parishkit/issues/750)):

1. **Review** uploads the file and validates it (below), without a page
   load, and opens the dialog with a card: the year, campaign name, final
   or partial, day-1 date,
   last day, Families responded, annual pledges, the active-Family count with
   its provenance, the metadata notes that the report's data notes will show,
   and what Apply will do (Add, Replace year N, or Already imported); the
   dialog's title asks it ("Add 2025?", "Replace 2025?"), or, when Apply is
   unavailable (below), states why ("2025 is already imported"). A
   refused file shows its problems in the dialog instead and offers no Apply,
   and the form keeps the chosen file; a file too large to read is instead
   a field error on the file field. Review
   is unavailable until a file is chosen ([prerequisite
   gates](../ui-conventions/spec.md#prerequisite-gates)).
2. **Apply** stores the file in one transaction; the dialog then closes and
   the table is redrawn [in place](../ui-conventions/spec.md#in-place-controls).
   Replacing a
   year needs an acknowledgement checkbox on the card ("This replaces the
   2025 figures already imported"), so Apply stays unavailable until it is
   ticked. For a file already imported Apply has nothing to do, so it is
   unavailable, named by the visible reason `imported by <who> on
   <when>` (the earlier import's Administrator and local time;
   [prerequisite gates](../ui-conventions/spec.md#prerequisite-gates);
   [Administrator decision 12](#administrator-decisions)), which the
   dialog's status line under its buttons shows; likewise for a partial
   file against a final import, whose reason is that a partial file can't
   replace the final import.
   Once Apply has answered, the upload form is drawn again as on a fresh
   page (the file choice cleared, Keep Family rows ticked), so Review is
   unavailable with its hint until the next file is chosen, and the
   reserved status line under the form says what was applied and when
   ("Imported 2025. Applied at …"). The hint's and status's lines are
   reserved, so neither moves anything ([no
   layout shift](../ui-conventions/spec.md#no-layout-shift)).

**Remove** is a [row action with
confirmation](../ui-conventions/spec.md#row-actions-and-confirmation): it
deletes that year's stored figures and Family rows, keeps a tombstone (year,
checksum, counts, who and when), and the report stops showing the year. The
file can be imported again later.

### Validation

The import validates a file completely before storing anything.

**Where it runs.** The workbook is untrusted input, so it is read only in a
short-lived helper process isolated like the chart engine's renderer (no
environment, no database connection, no error text back; see
[chart engine](../reports/spec.md#chart-engine)). The web process passes
the bytes in and receives either the validated, typed rows or a list of
located problems. The helper runs with a 1 GiB address-space limit and is
killed at 60 seconds; a kill is refused as "too large to check" and leaves a
`helper_timed_out` [durable timeout
entry](../operations/spec.md#observability-and-health), under the new
timeout name `archive_check`, naming the limit and the elapsed time. The
`archive-check` command runs the same helper.

**The upload route.** One Review request carries one file, at most 20 MiB.
`POST /admin/parish/past-campaigns/review/` is the second route allowed a
body larger than the ingress default: Caddy admits up to 21 MiB there (the
file plus form overhead; see [production
ingress](../operations/spec.md#production-ingress-and-tls), set in
`runtime_ingress.py` beside the hosted-file upload route). The view
follows the hosted-file upload's protections and adds two:

- it accepts POST only, and refuses a missing `Content-Length` or one over
  21 MiB before reading any of the body;
- before anything reads the body (`request.POST` or `request.FILES`), it
  checks the user's role (an Administrator with `CONFIGURE` and
  `FINANCIAL_DETAIL`) and the CSRF token, which the page script sends in
  the `X-CSRFToken` request header (the Admin portal [requires
  JavaScript](../ui-conventions/spec.md#javascript-requirement)), so an
  unauthorized request never makes the server read or spool 21 MiB;
- it then takes a deployment-wide, non-blocking lock for archive Reviews;
  while another Review holds it, the request is refused at once with "Another
  archive is being checked; try again in a minute". One archive at a time
  bounds this route's spool in the web container's `/tmp` tmpfs to one
  21 MiB file, where Django's upload settings spool files over 5 MB, as for
  hosted files;
- the proxy's 30-second body-read timeout is shared by every route and stays
  unchanged, so a 20 MiB file needs about 6 Mbit/s of upload speed, which
  the About panel says.

The whole request fits well inside the web server's 370-second request
timeout (`runtime_budget.server_timeout_seconds`): at most 30 seconds to
receive the body, at most 60 seconds in the helper, and one short
transaction that inserts the pending rows (a few hundred day, band and
Family rows).

The checks, in order:

1. **Size and package.** The file size; the zip directory's declared sizes
   as an early refusal only (they can lie); then every part is read once as
   a stream that counts the bytes actually decompressed and stops at 100 MiB
   for one part or 300 MiB in all, and, for each part once it has
   decompressed more than 1 MiB, at more than 100 decompressed bytes per
   compressed byte of that part (the converted files' highest is about 15,
   in the Ministry answers sheet). The hosted-files OOXML checks and the part allowlist above apply.
   Every XML part is refused if it contains a document type declaration or
   an entity declaration, checked on that stream before any XML parser sees
   the part. Worksheets are then parsed in streaming read-only mode, never
   loaded whole; at most 500,000 data rows in all. (The converted 2020–2026
   files are about 2 MB, 35 MB uncompressed and 100,000 rows each, most of
   them Ministry answers.)
2. **Schema.** The first sheet is `README`; `metadata` has the required keys
   and every other key is one the reader's version defines, except that a
   file declaring a later minor version (1.x) may carry keys that version
   added, which are ignored and never stored; `schema` and the major
   version match; every data sheet exists with its header row exactly as
   above.
3. **Cells.** Every cell has its column's type, range and enumeration; no
   formula cells; required cells are not blank; the conditional rules in the
   column tables hold (such as `annual_pledge` against `pledge_status`, and
   `installment_source` against `installment_amount`). Stored values fit
   their columns, so an accepted file never fails on save:
   `campaign_name` is at most 200 characters, `timezone` at most 64,
   `active_families_provenance`, `source_system` and `dedup_rule` at most
   10,000 each, `active_families` at most 2,147,483,647, the `note.*` and
   kept `count.*` values at most 256 KB together (as their stored JSON
   object), and the pledged total below $10,000,000,000,000,000 (a
   `numeric(18, 2)` column). The isolated helper's reply is checked to the
   same limits before anything is stored; a reply that breaks one is
   refused as "could not be checked".
4. **Consistency.** Each rule below holds for every one of the seven
   converted 2020–2026 files (the percentage rule once they are regenerated
   with measured counts).
   - `daily` runs from day 1 with no gap to the day of
     `campaign_last_submission_date`; each `local_date` matches its day;
     `cumulative_families` is the running sum of `first_submissions`;
     each day's `first_submissions` equals the number of `families` rows
     whose `first_campaign_day` is that day.
   - Every `submitted_at`, `first_submitted_at` and `last_submitted_at`,
     converted to `timezone`, gives its row's local date, and that date
     gives its campaign day from `campaign_start_date`.
   - Each Family's financial columns equal those of its last financial
     submission and its `last_submitted_at` its latest attributed
     submission, both by the [last submission](#families-sheet) rule; its
     `submission_count` is the number of its attributed submissions; it has
     at most one `is_last_for_family` `TRUE` row per `form`, on that form's
     last submission.
   - Every `daily` row's `cumulative_pledging_families` and
     `cumulative_annual_pledged` equal the values recomputed from
     `submissions` for that day by the same rule, to the cent; the last row
     also equals the totals of `families` (`cumulative_families` its row
     count, pledging Families its `pledged` rows, the pledged total their
     sum).
   - `families` keys are unique, and each key matches its `family_id_system`
     and `family_id`. Every attributed `submissions` and
     `ministry_responses` key is a `families` key.
   - Every `cumulative_pct_of_active` equals `cumulative_families` ÷
     `active_families` within 0.0005.
   - Every `count.*` key the reader knows equals its recomputed value.
   - `as_of` is not before the last attributed submission.
5. **Year.** A file whose `campaign_year` is the current campaign's
   stewardship year is refused: the report reads the current campaign live,
   and a second copy of it would be compared with itself. The current
   campaign's stewardship year is the year Families pledge for, as the
   archive's `campaign_year` is:
   - the Campaign settings year label, when it is one four-digit year and
     the campaign has no financial period or the label is one of the
     calendar years the financial period covers (a July–June period starting
     in 2026 may be labelled 2027);
   - otherwise the start year of the campaign's upcoming financial period,
     when there is no label, or when a four-digit label lies outside a period
     that falls within one calendar year. Such a label names something else,
     such as the year the campaign runs in (2026 on a 2027-01-01 to
     2027-12-31 period), so Production's Stewardship 2027, held in autumn
     2026 with label 2027 and a 2027 calendar-year period, refuses only a
     2027 file.

   This follows `campaign_year` in `web/presentation.py` except in two
   ways. The start-date fallback that function uses for a campaign without
   a financial period is not used here, since it names the year the
   campaign runs in (2026 for Stewardship 2027). And a label outside the
   financial period gives way to the period, as described above. When
   no single year results (no label and no financial period, a label such
   as "2026–2027", or a four-digit label outside a period that spans two
   calendar years), every import is refused with that reason and a link to
   Campaign settings, because "last year" cannot be resolved.
6. **Notes.** The `note.*` values and the provenance and dedup texts are
   shown on the Review card in full, because they become the report's data
   notes. A value longer than 500 characters, or one containing a
   dollar amount or a run of five or more digits (which could be a pledge or
   an identifier), is flagged on the card for the Administrator to check
   before Apply; it is still accepted, since those notes never leave the
   Admin portal (see [privacy](#privacy)).

Problems are listed per file, up to 50, each with its sheet, cell reference
and rule ("families!J17: annual_pledge must be greater than zero when
pledge_status is pledged"). A problem never repeats a cell's value, which
may be a Family identifier or amount, nor the text it found where a header
or metadata key was expected: a wrong header names the expected column, and
an unknown key names only its cell ("metadata!A12: key not defined in
version 1.0"). The list says when more problems were cut off.

### Idempotency and replacement

The SHA-256 of the uploaded bytes identifies the file:

- A file whose checksum matches a stored, not removed import is **Already
  imported**: the review dialog shows Apply unavailable, saying by whom
  and when it was imported (Administrator decision 12). An Apply of it that is posted
  anyway (a crafted request, or `history confirm`) still answers
  `already` and changes nothing, so repeating an Apply, from the page or
  the command line, is harmless.
- One import is active per `campaign_year`. A different file for a year that
  already has one is a **Replace**: Apply removes the old import (as Remove
  does) and stores the new one in the same transaction. A partial file never
  replaces a final one; Review refuses that with the reason.
- Replace is also how an archive is corrected: for example, when a better
  measurement of a year's active-Family count is found, the Administrator
  corrects `active_families` and `active_families_provenance` in the
  file's `metadata` sheet (and the `cumulative_pct_of_active` column that
  follows from it) and imports it again. The new checksum makes
  it a Replace.
- Two Applies racing for the same year or checksum are settled by the
  database's unique indexes: the loser is refused as stale and nothing
  changes.

### What is stored

A forward migration adds four tables under the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy),
with one frozen SQL file named by the repository-wide sequence. They are
parish records, not campaign records: a campaign purge never touches them,
and they are kept until an Administrator removes the year
([retention and deletion](../data/spec.md#retention-and-deletion)).

- **`stewardship_campaign_archive`**, one row per import: UUID, `version`
  (for `--expected-version`), SHA-256, cleaned original file name, size,
  schema version, campaign name and year, final flag, `as_of`, time zone,
  day-1 date, last submission date, active count and its provenance,
  source system, `dedup_rule`, the Family id system(s), whether Family rows
  were kept, the `note.*` rows and unknown `count.*` rows (as a bounded JSON
  object, for the data notes), the recomputed totals, the pledging Families'
  first quartile, median and third quartile, the previewing and importing
  users and times, and state `pending`, `active` or `removed` with who
  removed it and when. `column_mapping.*`, `source_files` and `converter`
  are conversion detail for the file's own readers and are not stored.
- **`stewardship_campaign_archive_day`**, the `daily` sheet: archive, day,
  local date, first submissions, cumulative Families, cumulative pledging
  Families and cumulative annual pledged (`numeric(18,2)`). Percentages are
  derived when read, never stored.
- **`stewardship_campaign_archive_band`**, the [pledge
  distribution](#pledge-distribution) computed by the helper from the
  `families` sheet at import: archive, band-set version, band, Families and
  total pledged.
- **`stewardship_campaign_archive_family`**, only when Keep Family rows is
  ticked, for Families with a `family_id` (envelope-only Families cannot be
  matched across years): archive, `family_id_system`, `family_id` and
  `first_campaign_day`, the only fields the renewal figure uses. No pledge,
  status, envelope number, amount, timestamp or note is kept, and nothing
  from `submissions` or `ministry_responses`.

Every per-Family pledge figure (bands, quartiles, the daily pledge series)
is computed in the helper from the file and stored only as an aggregate, so
no Family's pledge is stored. The cost is that a later band-set version
needs the year imported again.

Rows are inserted once and never updated. A trigger refuses every update
except `pending` → `active` and `active` → `removed` (each adding one to
`version`), and refuses deletes except of a `pending` archive row and its
children (expired-preview housekeeping) and of the day, band and Family
rows of a row being removed. Partial unique indexes keep one active row
per checksum and per year. Remove deletes the day, band and Family rows and
marks the archive row removed. The uploaded bytes are not kept: the
parish's archive folder holds the file, and the checksum identifies it.

Review inserts the validated rows under a `pending` archive row, kept for
the fifteen-minute [preview lifetime](../admin-portal/spec.md#preview-lifetimes)
of configuration changes; Apply makes it `active`, and housekeeping deletes
expired pending rows with their children. Pending rows are never read by
the report.

### Privacy

- Only aggregates leave the Admin portal: report pages, report emails, downloads
  and command output carry counts, sums, averages, quartiles and
  percentages, never a Family row or identifier. Downloads and report emails also
  apply the [small-count protection](#pledge-distribution).
- The free-text `note.*`, provenance and dedup texts appear only on the
  Admin portal page (the Review card and the report's data notes). Downloads
  and report emails carry none of them.
- Stored Family rows are read only inside the database, by the renewal
  calculation, which returns counts.
- The audit trail records `campaign_archive_previewed`,
  `campaign_archive_imported`, `campaign_archive_replaced` and
  `campaign_archive_removed` with the year, checksum, row counts and
  outcome, never a Family value or note text.

## Comparison report

**Access:** Administrator and Staff (`CAMPAIGN_REPORT`). Pledge figures
appear only when the current campaign has the financial module, as on the
[participation graph](../reports/spec.md#participation-graph). Ministry
leaders never see it.

The page is **Campaign comparison** at `/admin/reports/comparison/`, in the
Reports and queries menu group after the Response dashboard. It is unavailable
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
  report day of a [report email](../report-emails/spec.md#as-of) as of the
  end of the previous day is. Before day 1 has ended the page shows the imported years'
  final results only. After the current campaign's end date the comparison
  day stays at the day of its end date.
- A final imported year (`is_final` `TRUE`) whose `daily` sheet ends before
  day N contributes its last day's values from then on, marked "(final)".
  A partial imported year has no values after its last day: its `daily`
  sheet ends at its last submission, not at its `as_of`, so a later day is
  unknown and shows "—", and the year is left out of the average there.

### Metrics

At the comparison day N, for the current campaign, the same day last year,
and the average of all imported years:

| Figure | Current campaign | Imported year |
| --- | --- | --- |
| Families responded | Cumulative responses of the fact row for day N | `cumulative_families` of day N |
| Percent of active Families | Cumulative responses ÷ the fact row's cohort denominator (the Families Portal-eligible at any time through that day, as on the participation graph) | `cumulative_families` ÷ `active_families` (as the archive's provenance defines it) |
| Annual pledges | Pledge total of the fact row | `cumulative_annual_pledged` of day N |
| Average pledge | Annual pledges ÷ pledging Families as of day N's end, with the median beside it | `cumulative_annual_pledged` ÷ `cumulative_pledging_families`, with the final median beside it |
| Renewal of last year's responders | Last year's responding Families (by ParishSoft DUID) counted among the fact row's cumulative responses for day N (so a Family must be in this campaign's cohort, as for the participation numerator), ÷ last year's responding Families | The same for that year against its previous year, when both kept Family rows in the same id system |

- **The two denominators differ in kind.** This system's denominator is
  the cohort of Families ever Portal-eligible through the day; an archive's
  `active_families` is the measured count its provenance names (for the old
  system, such as the Families issued campaign codes or a database snapshot
  of active Families; for a file this system writes, the same cohort as
  here). A permanent data note under the report
  says so, and each year's provenance is listed with it.
- **Last year** is the imported year one less than the current campaign's
  stewardship year. Without it, the "last year" lines are left out.
- **Prior-years average** is the mean over every active imported year with a
  value at day N. Every imported year has a measured active count, so every
  year counts toward the percentage average too. Renewal has no average
  unless at least two imported years can produce it.
- Comparisons are shown as a relative change for counts and money
  ("12.5% more") and as percentage points for percentages ("4.2 points
  more"), always with an arrow and the words "more" or "fewer" ("less" for
  money), so color is never the only signal.
- Every number follows [numbers and
  money](../ui-conventions/spec.md#numbers-and-money): money in dollars
  and cents, percentages and percentage points with one decimal unless
  exact integers, US grouping, and an em dash for a zero denominator. The
  one exception is the Annual pledges headline tile, whose value and
  comparison lines leave out the cents as
  [stat tiles](../ui-conventions/spec.md#stat-tiles) set out, on the page,
  the PDF summary and the report emails alike
  ([#2090](https://github.com/epiphany40223/parishkit/issues/2090)); the
  Every number table, the downloads and the charts' exact values keep the
  cents.
- The median beside the current campaign's average is as of day N's end;
  an imported year's median is its final one, labelled "final", since the
  archive stores quartiles only for its final result.
- "Pledging Families as of day N's end", the current median and pledge
  distribution, and the current renewal count need a read of each Family's
  latest live submission version committed by that instant, the rule the
  participation graph's pledge total already follows. They come from one
  new narrow SQL function, returning aggregates only, not from a second
  implementation in Python. It is added by its own forward migration in the
  slice that uses it, since the import slice's migration is frozen once it
  is released.
- A figure that cannot be computed shows "—" with a toggletip giving the
  reason (no Family rows for last year, different id systems, financial
  module off, no responders last year, no active Families on the day, or no
  figure for the day yet). Every "—" has a reason.

### Pledge distribution

Pledges are heavily skewed (many small, a few large), so an average alone
hides how giving is spread. The report compares the spread of annual pledges
across years with fixed dollar bands and with quartiles.

**Population.** Families pledging more than zero, each counted once at its
last submission: an imported year's `families` rows with `pledge_status`
`pledged` (its final result), and for the current campaign each Family's
latest live submission as of the comparison day's end, labelled "so far".
The existing version 1.0 archives support this with no format change.

**Bands, version 1.** Each band includes its lower edge and excludes its
upper edge:

| Band | Annual pledge |
| --- | --- |
| 1 | under $250 |
| 2 | $250 to under $500 |
| 3 | $500 to under $1,000 |
| 4 | $1,000 to under $1,500 |
| 5 | $1,500 to under $2,500 |
| 6 | $2,500 to under $5,000 |
| 7 | $5,000 to under $10,000 |
| 8 | $10,000 and more |

The edges are round amounts, roughly evenly spaced on a logarithmic scale
(each band about one and a half to two times the one before), and were chosen
from the 2020–2026 archives so that every band held well over five Families
in every year: the busiest middle range is split at $1,500 and $2,500 so no
band dominates, and the top band starts at $10,000 because a band above
$20,000 would hold fewer than five Families in most years. The band set is
defined once, in the workbook module beside the format table, with this
version number. Changing an edge is a new version: it is recorded here,
stored band rows keep the version they were computed with, and the report
uses one version for every year, asking for a year computed with an older
version to be imported again (no Family's pledge is stored to recompute
from).

**Figures.** Per band and year: the number of Families, their share of
pledging Families, the total they pledged and its share of the total
pledged. Per year: the first quartile, median and third quartile of the
annual pledge, beside the mean. Quartiles use the inclusive method
(Excel's `QUARTILE.INC`): for the n pledges sorted ascending as x₁ … xₙ and
p = 0.25, 0.5 or 0.75, h = (n − 1)·p + 1, and the quartile is
x⌊h⌋ + (h − ⌊h⌋)·(x⌊h⌋₊₁ − x⌊h⌋), all in exact decimal arithmetic, then
rounded to the cent half up (`ROUND_HALF_UP`, as Excel's `ROUND` does).
Excel computes in binary floating point, so it may differ from the stored
value by a cent in rare cases. Amounts are
nominal dollars with no inflation adjustment, and the report says so.

**Small-count protection.** In anything that leaves the Admin portal (the
PDF, CSV and XLSX downloads, report emails and command output), a band with
fewer than 5 Families shows "fewer than 5" in place of its count and share,
and no total or share of total. When any band of a year is withheld, that
year's outside outputs also withhold:

- the count, share and total of the band next to it (the next lower band,
  or the next higher one for the lowest band), so neither the withheld
  count nor its total can be worked out from the year's totals by
  subtraction;
- the year's number of pledging Families and every average pledge of that
  year, at any day, since the year's total pledged divided by the average
  gives that number; and
- the bands' shares of pledging Families, since a visible count divided by
  its share gives it too.

The year's total pledged and its shares of the total stay, because without
the counts they reveal no band's count. A year with fewer than 5 pledging
Families shows no quartiles and no bands there.

**The current campaign stays inside the portal.** Its distribution and
quartiles change day by day, and the difference between two days could
expose one Family's pledge. They therefore appear only on the Admin portal
page. Outside the portal (downloads, report emails, command output) distributions
and quartiles are shown only for final imported years. The Admin portal
page itself shows every figure, since only Administrators and Staff open
it.

The current campaign's day-level totals, counts and average pledge (with
the comparison to past years) do leave the portal, deliberately. The
[participation graph's](../reports/spec.md#participation-graph) downloads
already carry its daily Families and pledge totals to the same
Administrators and Staff, who may also see every named pledge in the
[financial report](../reports/spec.md#financial-stewardship-detail); a
day's total and average add nothing they cannot already read. What stays
inside is what the distribution adds: bands, quartiles and the median,
whose day-to-day change could isolate one Family's pledge for a reader of
the file who has no portal access.

### Page layout

The page follows [page help](../ui-conventions/spec.md#page-help): data
first, explanation in the About this page panel.

1. **Heading and data line** (the figures in these examples are invented):
   "Day 12 of Stewardship 2027 · through Wednesday, October 14", with the
   last-year and average years named ("compared with 2026 and the
   2020–2026 average").
2. **Headline tiles**, one per metric above, in that order, drawn as
   [stat tiles](../ui-conventions/spec.md#stat-tiles). Each shows the
   current figure large, then two lines: "Last year on day 12: 240 (↑ 12.5%
   more)" and "2020–2026 average: 220 (↑ 22.7% more)"; the average pledge
   first adds its median so far. A screen reader reads the label, value
   and lines in that order, as shown.
3. **Day-by-day charts**, through the [chart
   engine](../reports/spec.md#chart-engine), one per measure, each with its
   summary and exact-values table: cumulative Families responded, percent of
   active Families, and cumulative annual pledges. The x-axis is campaign day
   ("Day 1 = first invitation day"). The current campaign is drawn in the
   accent color, thick and solid; last year in the second color, dashed;
   every other year thin and muted grey with its year labeled at the line's
   end; the prior-years average dark and dash-dotted. A vertical rule marks
   day N. Pointing anywhere over the plot (or tapping it) picks the nearest
   campaign day: the [hover guide line](../ui-conventions/spec.md#charts)
   and each line's point on that day appear, and
   one tooltip headed "Day 15" lists every line with a value that day, in
   the legend's order, each with its value and local date (a final year
   carried past its last day reads "(final)" with no date; the average has
   no date).
4. **Final results by year:** two bar charts, Families responded and annual
   pledges, one bar per imported year plus the current campaign drawn
   lighter and labeled "so far". Pointing anywhere on a year's row, not
   only at its bar, shows the year, whether its figures are final, partial
   or so far, and the value, so a short bar still reads. Each bar carries
   its value, so these charts have no exact-values table
   ([chart engine](../reports/spec.md#chart-engine),
   [#1266](https://github.com/epiphany40223/parishkit/issues/1266)).
5. **Pledge distribution:** one year at a time (Administrator feedback,
   2026-10-13, [#1221](https://github.com/epiphany40223/parishkit/issues/1221)).
   A labelled Year selector lists the current campaign, then each
   imported year newest first, and starts on the current campaign (on the
   newest imported year when the current campaign has no bands). The
   chosen year's chart is two 100% stacked bars, one of its pledging
   Families by band and one of its total pledged by band, both ordered
   from the smallest band (the banded style the Administrator chose,
   2026-10-13). Each band's segment is labelled with its Families or
   dollars over its share wherever the segment is wide enough at the drawn
   width, and each band's legend entry gives both its Families and its
   dollars with their shares, so every figure is in the chart at any width
   and it has no exact-values table of its own
   ([chart engine](../reports/spec.md#chart-engine),
   [#1266](https://github.com/epiphany40223/parishkit/issues/1266)). Under it, the band
   table (Families, share of Families, total, share of total) with the
   quartile line (first quartile, median, third quartile, mean) in its
   caption lists every figure as text. Choosing another year swaps the
   chart and table in place, with no request: every year's pane is on the
   page, all in one grid cell, so the section is as tall as the tallest
   year's and the choice moves nothing, and each chart is drawn at once
   to the panel's width. The choice stays through Compare on day ([in-place
   controls](../ui-conventions/spec.md#in-place-controls)). A year
   imported with an older band set shows its "import the year again" note
   in place of the chart and bands. With a single year there is nothing
   to choose, so no selector. Every year's quartiles stay in the Every
   number table, and downloads are unchanged: they leave the bands and
   quartiles out (see [Pledge distribution](#pledge-distribution)).
   The section's note says amounts are nominal dollars.
6. **Every number:** a table with one row per year (current first): year,
   day 1 date, days, Families at day N, percent at day N, pledges at day N,
   average pledge at day N, renewal at day N, final Families, final percent,
   final pledges, final average pledge, and the final first quartile, median
   and third quartile.
7. **Data notes:** a disclosure whose summary, "Data notes", names the
   section, closed by default since the notes are long
   ([#1161](https://github.com/epiphany40223/parishkit/issues/1161)), and
   last on the page, so opening it moves nothing above it. It holds the
   permanent note on the two kinds of denominator, then each imported
   year's notes: its source system, the active count's provenance, the
   dedup rule and its `note.*` rows, escaped and each cut to 500
   characters. The notes are consolidated (Administrator decision,
   2026-10-13, on
   [#1161](https://github.com/epiphany40223/parishkit/issues/1161)): a
   note that every imported year has alike, the same heading and text, is
   shown once under "Every imported year", and each year, newest first,
   then lists only its own notes; a year with none of its own is left out,
   and a lone imported year's notes are all its own. These texts stay on
   this page (see [privacy](#privacy)), and an opened disclosure stays
   open through Compare on day ([in-place
   controls](../ui-conventions/spec.md#in-place-controls)).

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
figure; a view the page made by itself to bring newer figures in is marked
`automatic: true`. If the current fact set is not ready, the page shows the
last ready one with the participation graph's **Updating** notice, and
brings the new figures in by itself once they are ready, as the Response
dashboard does
([pages that update themselves](../ui-conventions/spec.md#pages-that-update-themselves)).
A page that refuses the day asked for (a 400) still shows the notice but
does not follow, since its own re-read would only be refused again.

### Downloads and sharing

The page's data downloads as CSV, XLSX and PDF under the [downloaded files
rule](../ui-conventions/spec.md#downloaded-files): the "Every number" table
in all three, and the PDF adds the tiles and charts on landscape pages in
the shared [file design](../reports/spec.md#file-design), so the
Administrator can send leadership a single summary. Each day-by-day chart
is as wide as the page body, as on the page, under one legend row per
page: page 1 holds the tiles and the charts that fit below them, and the
rest follow two to a page before the table. Downloads hold
aggregates only, apply the [small-count
protection](#pledge-distribution), carry none of the free-text notes, and
record
`campaign_comparison_exported` like other exports. No public or
unauthenticated link exists. The download asks for no time zone: the
browser's sets the file's request time, else the parish's, under the
[time zones](../ui-conventions/spec.md#downloaded-files) rule
([#1161](https://github.com/epiphany40223/parishkit/issues/1161)).

### Digest section

A [report email](../report-emails/spec.md) may include the **Compared with
past campaigns** block
([its block](../report-emails/spec.md#compared-with-past-campaigns-block)),
chosen per report on
[Report emails](../report-emails/spec.md#blocks-field); it was the daily
digest's optional section before #1786. It follows the report emails' #720
style: the cumulative Families chart and the
cumulative pledges chart first (PNG through the chart engine's server
renderer, with alt text carrying the figures), then one line per headline
figure with its last-year and average comparisons, then a link to the
report. Its comparison day is the report's report day for a report as of
the end of the previous day, or the last complete campaign day before the
capture instant for a report as of when it is made (a partial day is not
compared with past campaigns' whole days), and its current figures come
from the report's pinned fact set. When the report is compiled, the
occurrence stores the section's computed aggregates (the figures, the chart
specifications and each imported year's checksum), so the saved copy shows
exactly what was sent, whatever is imported or
removed later; that page draws the stored figures as
[stat tiles](../ui-conventions/spec.md#stat-tiles), word for word. The
section applies the [small-count protection](#pledge-distribution) and
carries no note text. With no imported years the section is left out. It
is the first task to use the chart engine's server renderer, so its slice
adds that renderer's timeout name, as the
[chart engine](../reports/spec.md#chart-engine) requires.

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
| `pk-admin export archive` | full | Requests [Download campaign archive](#download-campaign-archive) through the page's service and prints the export's id and cutoff; `export fetch` saves the file. A fresh-gated action, as `export financial` is |

They follow [preview and confirm](../admin-automation/spec.md#preview-and-confirm)
and [personal data on the command line](../admin-automation/spec.md#personal-data-on-the-command-line):
output holds counts, sums, quartiles and states only (an archive's
Family-level rows reach the host only as the owner-only export file), with the
[small-count protection](#pledge-distribution) applied, since command
output leaves the Admin portal. `history preview` reports which notes are
flagged but prints no note text. Validation problems name cells, never
their values.

An operator command, `pk-stewardship archive-check <file>…`, validates
workbooks offline with the same reader and prints each file's summary or
problems (counts, totals and located problems, no note text). It needs no
database or session, so the Administrator can check converted files before
importing them.

## Failure handling

| Situation | Behavior |
| --- | --- |
| File not an `.xlsx`, too large, macro-bearing, with a part off the allowlist or an external relationship, or unreadable | Refused at Review with the reason; nothing stored |
| More than one file, no `Content-Length`, or a body over 21 MiB | Refused before the body is read |
| No role or CSRF header | Refused before the body is read |
| Another archive Review in progress | Refused at once; try again shortly |
| Decompression limit, compression ratio, document type or entity declaration | Refused as unsafe to read |
| The helper exceeds its memory limit | Refused as too large to check |
| Wrong schema, version, sheet or header | Refused naming the sheet and the expected header, never the text found |
| Current campaign's stewardship year cannot be resolved | Every import refused, linking Campaign settings |
| Cell or consistency errors | Refused with up to 50 located problems |
| Same checksum already imported | Already imported: Apply unavailable, naming who imported it and when; a posted Apply answers `already` and changes nothing |
| Year already imported, different file | Replace, with acknowledgement; a partial never replaces a final |
| Current campaign's year | Refused with the reason |
| Two Applies race | One wins; the other is refused as stale |
| Preview expired | Refused as stale; Review again |
| Apply fails part-way | The transaction rolls back; nothing is stored |
| Fact set not ready | Last ready fact set with the Updating notice |
| No last year, no Family rows | The affected figure shows "—" with its reason |
| Financial module off | Pledge figures, charts and columns are left out |
| Checking a file exceeds its 60-second limit | Stopped, logged as a durable timeout entry naming the limit and the elapsed time, and refused as too large to check |
| Archive download with no current campaign, before day 1 has ended, for a closed campaign with a live submission after the cutoff (closed today with a response today), or with no Family responded by the cutoff | Download campaign archive unavailable with the reason; the command refuses |
| Archive's derived `daily` disagrees with an available fact figure | The generation fails as a fact failure (retried, then failed with its reason); no file is offered |
| A fact row's population or pledge total is unavailable | Not a disagreement: that figure is left unchecked for the day, and a `note.*` row counts such days |
| No ready fact set's watermark yet covers every version submitted before the cutoff | Download campaign archive unavailable with that reason (the facts are recalculated automatically after responses); the worker's own coverage check fails a request rather than write a file without it |
| The written archive fails the reader's checks | The generation fails, logging the located problems without cell values; no file is offered |

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
- **Package safety:** macros; an external workbook link, a data
  connection, a query table, an embedded object, a drawing and a chart
  (each refused by the allowlist); an external hyperlink relationship; a
  zip whose directory understates a part's size (stopped while reading);
  a high compression ratio; a document type and an entity declaration in
  a worksheet and in `sharedStrings.xml`; the helper's time and memory
  limits, each leaving its timeout entry; the route's POST-only rule, its
  refusal of a missing or oversize `Content-Length`, a missing role and a
  missing CSRF header before the body is read (asserting the body stream
  is untouched), the one-at-a-time lock, and one file per request. A synthetic workbook saved again by LibreOffice in CI passes the
  allowlist.
- **Cell types:** whole-valued float integers accepted and digit text
  refused; numeric cells in text columns; money with three decimals refused
  and binary-inexact two-decimal values accepted exactly; boolean cells and
  `TRUE`/`FALSE` text where each is allowed.
- **Refusals:** one case per validation rule, including formula cells, a
  missing or reordered column, an unknown column or metadata key (including
  `active_families_estimate`), a blank or missing `active_families`, a
  blank `cumulative_pct_of_active`, a later
  minor version's extra column (accepted and ignored), a wrong major
  version, a `daily` gap, a `first_submissions` count that disagrees with
  `families`, a local date or campaign day that disagrees with its instant,
  a Family whose financial columns disagree with its last financial
  submission (including a 2020-style two-form Family and a `submitted_at`
  tie settled by `row_id`), a middle `daily` row whose pledges disagree with
  `submissions`, totals that disagree with `families`, duplicate Family
  ids, a wrong `count.*`, the current campaign's year and an unresolvable
  one (including the start-date fallback not being used), and a row limit;
  each asserts that no cell value, found header text or unknown key name
  appears in the problem text.
- **Import service (PostgreSQL):** add, already imported, replace, partial
  refused over final, remove and re-import, a corrected file replacing its
  year, racing Applies, expired preview cleanup through the trigger's
  allowed delete, Keep Family rows off, the stored Family columns limited to
  the renewal fields, the immutability trigger refusing other updates and
  deletes, `version` and `--expected-version`, and a campaign purge leaving
  the tables untouched.
- **Migration:** the frozen file's digest, its closing `DO` block, the
  migration chain and upgrade parity, per the post-launch schema policy.
- **Metrics:** day alignment with years of different lengths and start
  dates; final values carried forward, and a partial year showing "—"
  after its last day; the comparison day held at the end date once the
  current campaign has closed; the renewal numerator limited to this
  campaign's cohort; the average over years present at day
  N; percentage points versus relative change;
  average pledge; renewal against a DUID year, and "—" against a PDS-id year
  or a year without Family rows; current figures equal the participation
  graph's for the same day.
- **Pledge distribution:** band edges (a pledge exactly on an edge falls in
  the higher band), Families pledging zero left out, totals and shares per
  band, inclusive quartiles against hand-computed values, the band-set
  version recorded and a mixed-version comparison refused, and the
  small-count protection in every output that leaves the portal (a band of
  4 shows "fewer than 5" and no total, its neighbour's count and total
  withheld, the year's pledging-Family count, averages and shares of
  pledging Families left out, no quartiles or bands under 5 pledging
  Families, and no current-campaign distribution or quartiles outside the
  portal) while the page shows every figure; quartiles in exact decimals,
  rounded half up, against hand-computed values.
- **Pages:** role access (Staff see the report, not Past campaigns; Ministry
  leaders see neither), Review/Apply in place, the replacement
  acknowledgement gate, the Compare on day control without reload, the
  `day` value range, `no-store`, the audit events, the chart
  accessibility checks of the chart engine in a browser test, and (also a
  browser test) one pledge distribution chart and table whose year
  selector swaps them in place, its band labels and legend fitting with no
  overlap at phone and laptop widths, a year with an older band set showing its
  note, the data notes starting closed and opening on request,
  the shared notes shown once and each year's own after them.
- **Downloads and report emails:** the three formats carry the same columns
  and aggregates only, with no note text; a report email's saved copy shows the stored
  section unchanged after a later import and a removal; the chart
  renderer's timeout name is recorded on a kill.
- **Command line:** each command through the page's service, the catalog
  entries, and the absence of Family values in output.
- **Archive export:** a synthetic campaign (PostgreSQL) with repeat
  submissions, a cannot-give answer, a zero pledge, a campaign without the
  financial module, a proposed Member's Ministry choice, a Test submission
  and a submission after the cutoff (both left out) writes a file that the
  reader accepts and whose `daily` equals the fact series; the partial and
  final cutoffs and names, including a campaign closed before its end
  date: unavailable on its closing day after a response that day, final
  through the day before without one, and final through the closing day
  the next day; a forced fact disagreement and a forced reader
  refusal fail the generation with no file, while an unavailable pledge
  total (financial module off, or a responding Family without a pledge)
  and an unavailable population are left unchecked with their note; a
  version above the watermark or after the cutoff is left out, and a
  fact set whose watermark is short of the cutoff is never offered (the
  newest covering one is); a retry of one request gives the
  same SHA-256, and exporting again a new `converted_at` and checksum; no
  respondent leaves the action unavailable; access (Staff, a Ministry
  leader and a stale sign-in refused, nothing queued); the request event
  carries the day only; and an exported file imports once the next
  campaign is current, with the same day-N Families responded and annual
  pledges the report showed.
- **Human-run check, outside CI:** a synthetic workbook corrected and saved
  again in Excel and in Google Sheets passes the allowlist; `archive-check`
  accepts the seven regenerated 2020–2026 files, and an import of them
  reproduces the day-8
  figures reported on #1018. The files are private and are never committed.

## Implementation slices

Each slice is one PR of the
[automated delivery cycle](../../../plans/stewardship/overall.md#automated-phase-delivery-cycle),
linked to #1018 (slice 7 completed part (b)).

1. **Workbook module and offline check (first, within days).** The format
   table (taken from the one-time converter's table, which holds no data),
   reader and validator with the part allowlist and streaming limits, run in
   the isolated helper process; the pledge band set and quartile
   calculation; a minimal writer used by tests (part (a) later fills it with
   real data); and `pk-stewardship archive-check`. No schema
   change. The Administrator can check the seven converted files at once.
2. **Storage and command-line import.** The forward migration (including
   the pledge-distribution band rows and quartiles), the import service with
   preview, confirm and remove, and the `pk-admin history` commands. Past
   years can be loaded by the operator the same day. The ingress route
   exception for Review moves to slice 4, with the upload route it serves.
   As built: a preview's `preview.token` is its pending archive row,
   bound to the Administrator who reviewed it; each Review first deletes
   expired previews (the housekeeping the trigger admits), skipping any an
   Apply holds, and the child rows' guard locks their archive before
   admitting a delete, so an Apply and housekeeping never split an import
   from its rows; and a
   Review killed at its time limit leaves a `helper_timed_out` entry naming
   `archive_check` and the helper `archive_check_worker`. The command line
   needed no page route yet: the route-parity ledger listed the `history`
   commands as ahead of their page (`admin_parity.AHEAD_OF_PAGE`) until
   slice 4, whose routes they now cover.
3. **Comparison figures.** The metric service, the SQL function (in its own
   forward migration) for pledging Families, renewal, the current median
   and the current campaign's pledge distribution,
   `pk-admin report comparison`, and the report page with tiles, the
   pledge-distribution and quartile tables, the Every number table and data
   notes. Leadership gets
   current comparisons here.
   As built: frozen file 0067 adds `stewardship_campaign_comparison_v1`
   (the current figures of one fact-set day, whose Families responded and
   pledge total the report checks against the fact row before showing
   anything; it derives the day's end itself from the fact set's time zone
   and refuses unless its Families responded equal the fact row's, so no
   caller can move the cutoff to probe one Family's renewal) and, since the web login reads only the id and archive of a
   kept archive Family row, a second definer function,
   `stewardship_campaign_archive_renewal_v1`, for one imported year's
   renewal of the year before it. Both return aggregates only, and only the
   web login may execute them. A renewal's denominator is the previous
   year's kept Family rows (every responding Family with an id; an
   envelope-only Family could never be matched). The audit event records
   the comparison day as `count`, an existing context field, so the
   context allowlist needs no change. A comparison in dollars says "less"
   rather than "fewer". The page takes no query parameter until Compare on
   day (slice 5), and names Past campaigns without linking it until that
   page exists (slice 4).
4. **Past campaigns page.** The upload page with Review, Apply, replacement
   and Remove.
   As built: Review posts to `review/` and, once the preview is stored,
   answers with the review dialog's card (`past-campaigns-review.html`),
   which carries the preview's id (an import's own id, never a Family's)
   for Apply; a refused file answers with its problems as the dialog's
   refusal, and a file over 20 MiB with the page again (status 400), its
   file field marked in error. A card with nothing to apply marks its
   reason `data-review-unavailable`, so the dialog shows Apply greyed with
   that reason. The view is marked `csrf_header_only`, so the
   middleware takes the token from the `X-CSRFToken` header alone (the
   form is marked `data-csrf-header`, which the review dialog's Review
   honors too), and it closes the spooled upload
   inside the Review lock, before the helper runs. Apply posts to
   `imports/` and answers with a redirect to the page, which takes no
   query but its table's sort (no `?preview=` or `?applied=`, [#750](https://github.com/epiphany40223/parishkit/issues/750));
   Remove posts the confirmation dialog's
   `<year>:<version>` to `removal/`. For a file already imported Apply is
   unavailable, as decision 12 records. The table's Active Families
   cell carries the count's provenance in a toggletip, and every import's
   file name and SHA-256, tombstones included, are under Technical
   details. Review is unavailable, with the reason and a link to Campaign
   settings, while the current campaign's year cannot be resolved.
5. **Charts and day control.** The three day-by-day charts, the final-results
   bars, the pledge-distribution stacked bars and Compare on day.
   As built: `reports/comparison_charts.py` builds the seven charts from
   the comparison's figures, each year's day-by-day series coming from its
   stored `daily` rows and the current campaign's from the same fact rows
   as its figures (no schema change). A final year shorter than the longest
   is drawn flat to the last day, its points there marked "(final)", and
   the average counts it there, as the tiles do; a partial year's line
   stops at its last day. The average line is named "Prior-years
   average" in the legend and table, since the years it averages vary by
   day; each chart's summary names the years averaged on day N, as the
   tiles do. A day of the current campaign whose population is unavailable
   breaks its line and shows "—" in the table. The chart engine's colors are the portal's design
   tokens (accent, gold, ink and the control border for the grey years);
   the portal has one light theme, so a browser in dark mode gets the same
   colors on white. Lessons of the published infographic at phone width:
   the grey years' end labels are spaced in pixels so none overlap, the
   "Day N" label sits on the side of its rule away from them, the bars are
   horizontal with each value inside a long bar's end or after a short
   one, a legend's column count follows the panel's width, and the x-axis
   title is "Campaign day", the "day 1 = first invitation day" explanation
   moving to the line above the charts and each chart's summary (an axis
   title wider than a phone's plot would be cut off). A browser test
   checks at 320 and 1280 pixels that no chart text overlaps other text or
   runs past its panel. The page's tables wrap whole words and scroll
   sideways in their own box on a phone. Compare on day is a number field
   (1 to the comparison day, checked by the browser) with a Compare button
   above the report's one in-place region (`comparison-report`), so the
   control never moves; a `day` that is not a whole number in that range
   answers 400 with the comparison day's page and the error at the field,
   and records the view as `denied`; any other parameter is the plain
   report-options refusal.
6. **Downloads.** CSV, XLSX and the one-page PDF summary, with the
   small-count protection.
   As built: the page's **Download** section (in the in-place region, so
   it follows Compare on day) posts the day shown to
   `reports/comparison/exports/`, which reads the page's comparison under
   the read guard and stores a protected capture
   (`comparison_exports.capture`) in the immutable
   `stewardship_comparison_export_snapshot` of frozen file 0071: report
   kind `campaign_comparison` in the shared
   [point-in-time](../operations/spec.md#point-in-time-exports) export
   lifecycle (status page, download, cancel and retry; a retry renders the
   same capture, so never newer figures). Its SQL guard admits
   Administrators and Staff only, from the web, and only the document's closed set of keys, as defense in
   depth; it cannot tell text from text, so keeping notes out is the web
   capture's job.
   The request records `campaign_comparison_exported` with the day as
   `count`; download and cancel are the lifecycle's shared events.
   `pk-admin export comparison [--day N]` is the same form. All three
   formats carry the Every number table with the page's columns: counts
   are numbers, money exact amounts, day 1 a date, a "—" a blank cell, and
   a value carried "(final)" keeps that mark as text (as an unavailable
   amount stays a word), so no cell is split. The PDF is the summary page,
   with the headline tiles and the three day-by-day charts (full width,
   flowing onto following pages) drawn in the shared page frame, then the
   table in the frame's table style set at 80% of body size so fifteen
   columns fit with no amount broken; the
   final-results and pledge-distribution charts stay on the page, as do
   the distribution tables. Outside the portal: a year with a withheld
   band shows Withheld for every average pledge, and the tiles' last-year
   and prior-years average pledge read Withheld when they would use such a
   year; this campaign's and a partial year's quartiles read "On the page
   only", and a final year with fewer than 5 pledging Families shows
   Withheld quartiles; the tiles carry no median so far. This campaign's
   own average pledge stays, as `report comparison` prints it since slice
   3: none of its bands leave the portal, so none can be worked out from
   it. A final year with fewer than 5 pledging Families withholds its
   averages whatever its distribution's reason (an older band set).
7. **Digest section.** The daily digest option, with its own
   forward migration if the digest occurrence needs new columns to record
   the imported years it used. (Since
   [#1786](https://github.com/epiphany40223/parishkit/issues/1786) the
   section is the
   [Compared with past campaigns block](../report-emails/spec.md#compared-with-past-campaigns-block)
   of report emails; the record below is what was built for the daily
   digest.)
   As built: the setting is the daily digest schedule's **Include Compared
   with past campaigns** checkbox on New and Edit scheduled email (and
   each schedule's editor on Dates and mail schedules), saved in the
   schedule as `comparison: true` and left out when off, so schedules
   saved before it are unchanged; only a daily digest takes it, and the
   schedule commands carry it as `comparison`. When the schedule revision
   a digest runs has it on, the compiling worker reads the comparison on
   the report day (held at the campaign's end date) from the digest's own
   fact set and the campaign configuration its snapshot pinned, with that
   configuration's stewardship year, and reduces it at once to the
   section (`reports/digest_comparison.py`): the outside-the-portal tiles
   of [Downloads and sharing](#downloads-and-sharing) (slice 6's protected
   averages, Withheld included), the Families and pledges day-by-day
   charts as chart engine specifications with their summaries and
   exact-values tables, and each imported year with its file's SHA-256;
   no distribution, quartile, median so far or note. Frozen file 0072
   adds the digest ready row's nullable `comparison` (that document) and
   the two charts' PNG columns, kept together by a check, and lets the
   general worker's login call the two comparison functions (it reads the
   archive, day and band tables but never a kept Family row). The email
   puts the section after the report's own button: its heading, the
   data line, each chart's title and image (`cid:` images beside the
   participation chart, which the digest boundary admits only as
   compiled), the figures, and a link to the report on the same day
   (`?day=N`). The saved digest page draws the stored specifications with
   the chart engine in the browser and lists the files compared under
   Past campaigns compared; `pk-admin report-email copy` prints the
   section without the specifications (`digest daily` until #1786). The renderer's timeout name is
   `digest_chart` with the helper `chart_render_worker`. A comparison
   that disagrees with its facts fails that compile (it is retried like a
   fact failure), rather than sending a digest without the section it was
   set to carry. A chart the renderer cannot draw is retried too, but on
   the last attempt the digest is sent with the section's figures and no
   charts ([#477](https://github.com/epiphany40223/parishkit/issues/477);
   see the reports spec's
   [chart engine](../reports/spec.md#chart-engine)).

Slices 4 to 7 depend on 3 but not on each other. UI slices get the
human pk-local check that significant new UI needs.

Part (a), the [archive export](#archive-export), follows in its own slices
(the last one closes #1018):

- **A1. Writer and its read.** The campaign read under the cutoff, the
  column mapping, the fact comparison, the read-back through the reader and
  the reproducible bytes (`write_archive` and `save_workbook` gain the
  re-pack with fixed ZIP times, compression, part order and `core.xml`
  times), as a service function with unit and PostgreSQL tests on
  synthetic data. No page, command or schema change.
- **A2. Download and command.** The `campaign_archive` export kind (a
  forward migration for its request arm, which stores the cutoff instant and
  the submission watermark and pins the checked fact-set generation against
  compaction, at the next free number when it is built), the Past campaigns
  section, `pk-admin export archive`, its rows in the
  admin automation spec's
  [fresh-gated actions](../admin-automation/spec.md#fresh-gated-actions-from-the-command-line)
  and [reports and exports](../admin-automation/spec.md#reports-and-exports)
  catalog, and the request event. It needs the human pk-local check.
  As built: frozen file 0084 adds the arm to `export_report_known` (XLSX,
  a fact set, no capture), a `campaign_archive` branch to the export
  request and publication guards, the fact-pin guard (as for
  Participation) and the request authorization (Administrators only),
  and the general worker's definer read
  `stewardship_campaign_archive_answers_v1`, which returns only the
  archive's parts of each live answer under a fact set's pins (frequency,
  the cannot-give box, the Ministry choices and the chosen Ministries'
  names), so the worker needs no grant on the answers column. The request
  pins the checked fact set (and so its submission watermark) through
  `fact_set_id` and a fact pin, and its parameters are exactly the cutoff
  date, the final mark and the stewardship year, which names the file.
  The worker's campaign read guard runs this kind's read in a
  repeatable-read transaction. A campaign closed before its end date is
  offered its final file cut off at its last full day (Administrator decision,
  2026-10-13, replacing the default posted on #1018 that the final file
  waits for the end date). Download is unavailable, with the reason,
  before day 1 has ended, for a closed campaign with a live submission
  after the cutoff (until the next day), with no Family
  responded, with no stewardship year, or while no ready fact set covers
  the cutoff (holds its day and a watermark reaching every response
  before it). A final request is admitted by the SQL guard only for a
  closed or archived campaign; the stewardship year in its parameters is
  the Python rule's (`year_from_values`), not bound in SQL.
- **A3. Close-out.** The #527 wizard's archive step, built with #527 (after
  [#145](https://github.com/epiphany40223/parishkit/issues/145)).

## Administrator decisions

The Administrator accepted every proposed default on October 11, 2026
(recorded on #1018):

1. **Who sees the report:** Administrators and Staff, as for Participation.
2. **Keep Family rows:** ticked by default, for the renewal figure. The
   default proposed keeping the id, first and last day, pledge status and
   annual pledge; the independent review of this specification narrowed it
   to the fields the figure uses (id system, id and first campaign day),
   with every pledge figure computed from the file at import, which keeps
   less and loses nothing the report shows.
3. **Keep the uploaded file:** no; the parish's archive folder is the file's
   home and the checksum identifies it.
4. **Removal:** an Administrator may remove an imported year, leaving a
   tombstone.
5. **Current campaign's own archive:** refused, since the report reads the
   current campaign live.
6. **Day 1 of the current campaign:** the campaign's local start date, which
   is the first invitation day.
7. **Prior-years average:** every imported year. A measured active-Family
   count is required in every archive and its provenance records the
   source; estimates are not supported (Administrator direction, 2026-10-11,
   after firm counts were found for every year, 2020–2022 from database
   snapshots of the old system). If a better measurement turns up, the
   archive's `active_families` and `active_families_provenance` are
   corrected and the year is re-imported with
   [Replace](#idempotency-and-replacement).
8. **Average pledge:** annual pledges divided by Families pledging more than
   zero. Because pledges are heavily skewed, the report adds the median,
   the quartiles and the [pledge distribution](#pledge-distribution) beside
   it (new requirement from this decision).
9. **Digest section:** off by default; when on, in the daily digest only.
   (Since #1786 it is a block any report email may include; see
   [report emails](../report-emails/spec.md#compared-with-past-campaigns-block).)
10. **Sending to leadership:** the one-page PDF download, which the
    Administrator emails. No public link.
11. **Upload limits:** 20 MiB per file, 300 MiB uncompressed and 500,000
    rows, about ten times the largest converted file.
12. **A file already imported** (Administrator decision, 2026-10-11, on
    the slice 4 review): the Review card shows Apply unavailable with the
    reason `imported by <who> on <when>`, rather than an Apply that does
    nothing; the server's Apply still answers `already` with nothing
    changed if one is posted ([idempotency](#idempotency-and-replacement)).
    The card's move into the review dialog
    ([#750](https://github.com/epiphany40223/parishkit/issues/750)) keeps
    this: the dialog shows Apply unavailable, with the reason in its status
    line.
