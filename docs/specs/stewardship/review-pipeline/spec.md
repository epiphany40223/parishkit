# Stewardship review pipeline

Status: **draft for the Administrator's review**
([epic #1680](https://github.com/epiphany40223/parishkit/issues/1680)). Nothing
here is built yet. Where this draft and a built page's spec disagree, the
built page's spec describes what runs today until the slice that moves that
page to the pipeline lands and updates it.

This specification defines one review pipeline for everything a Family tells
the parish through the [Family portal](../parishioner-portal/spec.md): census
changes, Ministry requests, the cannot-participate answers, financial
answers, and Additional information. Each stream keeps its own data, rules
and pages; this document defines what they share: the stages a change passes
through, the words for them, the views each role sees, the review actions,
the update phase, and the audit. It links to the stream specs rather than
restating them:

- census: [Proposed changes](../data/spec.md#proposed-changes),
  [Review and publication](../data/spec.md#review-and-publication),
  [Pending census changes](../reports/spec.md#pending-census-changes) and
  [Publish to ParishSoft](../reports/spec.md#publish-to-parishsoft);
- Ministry: [Follow-up records](../data/spec.md#follow-up-records),
  [Ministry change summary](../reports/spec.md#ministry-change-summary),
  [Follow-up workflows](../admin-portal/spec.md#follow-up-workflows) and
  [Roster changes to enter](../admin-portal/spec.md#roster-changes-to-enter);
- financial: [Financial stewardship detail](../reports/spec.md#financial-stewardship-detail);
- cannot participate: [Limitations](../reports/spec.md#limitations);
- free text: [Additional information](../reports/spec.md#additional-information).

What ParishSoft's API can and cannot write is defined by the
[API analysis](../../../parishsoft-api-analysis.md) and the handling
registry in [Proposed changes](../data/spec.md#proposed-changes). General UX
rules (tables, row actions, selection, exports) are in the
[UX conventions](../ui-conventions/spec.md), which take precedence for those
rules; [proposed convention changes](#proposed-convention-changes) lists the
edits this pipeline needs there.

## Administrator's direction

The Administrator asked, on 2026-10-18, for one data flow for Staff and
Ministry leaders applied to everything that comes from the Family portal: a
baseline, the Family's submitted changes, a review step in which Staff and
leaders edit, replace or ignore each change and mark it reviewed (one by one
or in bulk), and an update step split into an automated upload of eligible
changes to ParishSoft, verified by the next download, and manual reports of
everything else. Staff and leaders should have one consistent UX,
terminology and viewpoint across the streams, enforced by common code where
reasonable, and their portal should be streamlined and task-focused: they
sign in to review the submissions of the streams they are responsible for.
The epic quotes the message in full.

## Pipeline stages

Every item in every stream is in exactly one stage. A stage is derived from
the stream's own stored state (the [stream mapping](#stream-mapping) says
how); the pipeline adds no second copy of that state.

```text
Baseline ──> Submitted ──> Reviewed ──┬──> Updated in ParishSoft ──┐
                                      │                            ├──> Confirmed
                                      └──> Reported (by hand) ─────┘
```

### Baseline stage

The baseline is what the parish already knows. For census fields, Ministry
rosters and the ParishSoft pledge and contribution figures it is the
promoted ParishSoft snapshot
([Source snapshot](../data/spec.md#source-snapshot)). For data ParishSoft
does not hold (the upcoming pledge and its frequency and share methods,
cannot participate, cannot attend, the free text) the baseline is empty. The
Family form shows the Family its baseline and the baseline is never edited
by review.

### Submitted stage

An item is **Submitted** when a Family's live response differs from its
baseline: one item per atomic change (one census field, one Ministry join or
leave, one Family's pledge answer, one Member's cannot-participate answer,
one Additional information text). Testing responses never enter the
pipeline. A later response from the same Family replaces its open items as
each stream already does (superseded or cancelled), never rewriting history.
Staff entering a Family's answers through Open form create Submitted items
like the Family's own.

### Reviewed stage

An item is **Reviewed** once someone allowed to review it records one
decision:

| Decision | Meaning | Value carried forward |
| --- | --- | --- |
| **Accept** | the Family's change is right as submitted | the Family's value |
| **Edit** | the change is right but its value needs correcting ("123 Main Street" → "123 Main St.", 100 weekly → 150 annual) | the reviewer's value; the Family's stays visible beside it |
| **Replace** | the Family meant something else (joined Altar Servers by mistake; meant Adult Choir) | nothing from this item; a new reviewer-created item carries the replacement and is itself Reviewed |
| **Ignore** | no change should be made | nothing |

Edit is Accept with a value; it is shown as **Accepted (edited)**. Replace
is Ignore plus a reviewer-created item linked to the one it replaces, so
both stay in the history and the replacement goes through the rest of the
pipeline like any other item. **Back to review** undoes a decision while
the item has not started its update. Every decision takes an optional
note; reopening an ignored item requires one, as
[Manual census resolution](../reports/spec.md#manual-census-resolution)
already requires for Reopen.

Only Reviewed items move on: nothing in the update phase ever reads a
Submitted item. Review is the only stage a reviewer works in day to day.

### Updated in ParishSoft stage

An accepted item whose stream and field ParishSoft's API can write is
**Ready to update**. An Administrator (never automatically, never Staff or a
leader) starts an update run of any Ready subset at any time. The run is
the existing [publication pipeline](../data/spec.md#review-and-publication)
(preflight, plan, confirmation, fenced writes, read-after-write, final
refresh), which this pipeline reuses unchanged. A written and verified item
is **Updated in ParishSoft**.

### Reported stage

An accepted item ParishSoft's API cannot carry is **To enter by hand** (or,
for data that never goes to ParishSoft, **To handle**). It appears in its
stream's manual report until someone marks it **Entered by hand** (or
**Handled**). Manual reports use the shared export component
([#1679](https://github.com/epiphany40223/parishkit/issues/1679)) and the
[downloaded-files rules](../ui-conventions/spec.md#downloaded-files).

### Confirmed stage

An item is **Confirmed** when the promoted baseline itself shows the change:
the next ParishSoft download holds the item's carried-forward value. For
data ParishSoft does not hold, there is no confirmation to wait for, so
**Handled** is final. Confirmed is the pipeline's only fully closed stage
for ParishSoft data; Updated in ParishSoft and Entered by hand wait for it.

## Baseline refresh re-evaluation

This section proposes how the Administrator's unfinished sentence ("since
the baseline data is continually updated …") is completed. Every promoted
refresh re-evaluates every open item of a ParishSoft-backed stream against
the new baseline, in the promotion transaction, as
[refresh reconciliation](../data/spec.md#parishsoft-refresh-reconciliation)
already does for census proposals and Ministry requests:

1. **Already reflected.** The new baseline holds the item's carried-forward
   value (the edit, else the Family's value): the item moves to
   **Confirmed**, whatever stage it was in (Submitted, Reviewed, Ready, To
   enter by hand, Entered by hand, Updated in ParishSoft). This is how a
   by-hand entry is confirmed and how a Family's change that the office
   already made is retired without anyone reviewing it. A Submitted item
   confirmed this way records "Already in ParishSoft" with no reviewer.
2. **Changed underneath.** The baseline's value for the item's field
   changed since the Family answered (or since the reviewer accepted it
   over a conflict) and does not match: the item is flagged **Conflict**
   and returns to Review, keeping its decision visible but not in force,
   as an approved census change does today
   ([Automatic census review](../reports/spec.md#automatic-census-review)).
   An item already queued for update keeps its plan, and the plan meets the
   conflict at its own check.
3. **Not yet reflected.** Nothing changed for the field: the item stays
   where it is. An item marked Entered by hand that a configurable number
   of refreshes (default: two full refreshes) still does not show is
   flagged **Not seen in ParishSoft** on its stream's report, without
   changing its stage, so a missed entry is noticed.
4. **Entity gone.** The Member or Family left the baseline (inactive,
   deceased, moved): the item keeps its stage and gains the shared
   [Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
   badge, as Roster changes to enter does today.

Streams with an empty baseline (pledge, cannot participate, free text) are
not re-evaluated: nothing upstream can confirm or contradict them.

The comparison uses each stream's existing canonical comparison (the census
compare rules, the roster state for Ministry requests), never a raw string
compare, so a formatting difference ParishSoft introduces is not a
conflict.

## Pipeline vocabulary

These words are used, exactly, on every pipeline page, in exports, help
text, notices, audit display and the command line. A stream may not
introduce its own word for a shared stage.

| Term | Use | Replaces today |
| --- | --- | --- |
| Baseline | what ParishSoft (or, for new data, nothing) held when the Family answered | "ParishSoft now" stays as the column for the *current* baseline value |
| Family's answer | the submitted value | unchanged |
| Reviewer's value | the edited value carried forward | "Edited value" |
| To review | Submitted, no decision | "To review" (census), "New" / "In progress" (Ministry), "Needed, not completed" (Additional information) |
| Accepted / Accepted (edited) | Reviewed, carried forward | "Approved" (census), "Resolved: joined / leave confirmed" (Ministry) |
| Ignored | Reviewed, nothing carried forward | "Ignored" (census), "Declined" / "Duplicate" (Ministry) |
| Replaced | Ignored with a reviewer-created replacement | new |
| Conflict | the baseline changed under the item | unchanged |
| Ready to update | accepted, the API can write it | "To do" for an approved Automatic row |
| Updating | in a running update | "Being published" |
| Updated in ParishSoft | written and read back | "Published" |
| To enter by hand | accepted, the API cannot write it | "To do" for a By hand row; "Not yet" on Roster changes to enter |
| Entered by hand | a person recorded entering it | "Entered by hand", "Entered in ParishSoft: Yes" |
| To handle / Handled | accepted data that never goes to ParishSoft | "Follow-up completed" |
| Confirmed | the baseline now shows it | "Already in ParishSoft" |

"Approve" and "publish" are retired as user-facing words: Accept is the
review decision and Update in ParishSoft is the write. This answers the
confusion the Administrator raised on Census changes, where *To do* today
means both "approved, waiting to be published" and "waiting to be typed in
by hand".

## Stream mapping

How each stream's stored state maps onto the stages, and the gap to close.
The child issues of the epic carry the detailed gap analysis.

### Census stream

Stored state: `ProposedChange` decision (`unreviewed`, `approved`,
`ignored`) × execution (`pending`, `conflict`, `queued`, `published`,
`resolved_upstream`, `resolved_external`, `failed`, `superseded`,
`cancelled`). Mapping: unreviewed → To review; approved + pending on an
`api` change → Ready to update; approved/pending on a `manual` change →
To enter by hand; queued → Updating; published → Updated in ParishSoft;
resolved_external → Entered by hand; resolved_upstream → Confirmed;
ignored → Ignored; conflict → Conflict; failed → Ready to update with the
failure shown.

Gaps: *By hand* changes are never reviewed today (Staff go straight to
Entered in ParishSoft or Ignore), so Accept is new for them, and an
Administrator-only review of *Automatic* changes becomes a review by Staff
(see [open decisions](#open-decisions)); Edit value exists only for
Automatic changes; published and resolved_external are final today and are
not later confirmed by the baseline. The page itself mixes review and update
in one table; the Census page layout issue ([#1682](https://github.com/epiphany40223/parishkit/issues/1682)) separates them.

### Ministry stream

Stored state: `MinistryRequest` workflow (`new`, `in_progress`,
`resolved`, `closed_no_response`, …) and outcome (`joined`,
`leave_confirmed`, `declined`, `no_response`, `duplicate`, `other`), then
the Entered in ParishSoft tick on
[Roster changes to enter](../admin-portal/spec.md#roster-changes-to-enter),
then reconciliation resolving a request whose roster state is current.
Mapping: new / in progress → To review; resolved joined or leave confirmed
→ To enter by hand; ticked → Entered by hand; declined, duplicate, no
response → Ignored; roster now current → Confirmed.

Bulk outcomes already exist (Mark as Joined or Declined, Left or Stayed, one
kind at a time) and so does a bulk Entered in ParishSoft tick. Gaps: no
Replace (the request's Ministry, Member and action are immutable, so a
leader cannot turn a mistaken Altar Servers join into an Adult Choir join;
today it needs a Declined outcome and an off-system note); no Edit (a join
or leave has no value to correct); Ministry leaders never see the
Entered-in-ParishSoft step, which is right for the role;
Ministry rosters are read-only in both ParishSoft APIs, so this stream never
has Ready to update; contact attempts are a Ministry-only extra the pipeline
keeps as stream detail.

### Financial stream

Stored state: none beyond the Family's live response; the
[Financial stewardship detail](../reports/spec.md#financial-stewardship-detail)
report lists every current pledge. Baseline is empty for the upcoming
pledge; the ParishSoft comparison pledge is context, not a baseline.
Staff cannot edit a pledge today except by entering a whole new response
through Open form (marked Entered by Staff). Gap: the whole review stage
(per-pledge items, Edit amount and frequency, Accept, bulk Accept) and a To
enter by hand / Entered by hand state. Pledges
and funds are read-only in ParishSoft's API, so the stream ends at Entered
by hand; ParishSoft giving data cannot confirm it in v1 (see
[open decisions](#open-decisions)). Ministry leaders never see it.

### Cannot participate stream

Stored state: none beyond the live response; the
[Limitations](../reports/spec.md#limitations) report lists Members who cannot
participate and Families who cannot attend. Baseline is empty, nothing goes
to ParishSoft. Gap: per-answer items with To review → Accepted → To handle →
Handled, so the office can record that it acted on each.

### Additional information stream

Stored state: `AdditionalInformationItem` disposition and the
`follow_up_needed` / `followed_up_at` workflow with Staff notes
([Follow-up records](../data/spec.md#follow-up-records)). Mapping: current
actionable and not marked → To review; follow-up needed → To handle;
follow-up not needed → Ignored (nothing to do); followed up → Handled;
superseded / withdrawn → history. Gap: closest to the pipeline already, but
per item only (no selection column or bulk action today); it needs the
shared vocabulary, the shared table with bulk Accept / Handled, and its
two booleans expressed as one stage.

## Role views

Who sees which items does not change: it stays as each stream's spec
defines it, enforced in the database where it is today (Ministry leaders
see only the Ministries they lead in ParishSoft and never census, financial,
limitations or free-text data; Staff see every stream). The pipeline
changes what each role lands on:

- **Staff and Ministry leaders** sign in to **My review work**: one panel per
  stream they may review, each with its To review, Conflict and To enter by
  hand (or To handle) counts and a link to that stream's review worklist,
  filtered to the stage. Nothing else is above it. It is the Staff and
  leader form of Home ([#1673](https://github.com/epiphany40223/parishkit/issues/1673),
  [#1590](https://github.com/epiphany40223/parishkit/issues/1590)), and it
  replaces My Ministries for a leader.
- **Administrators** keep the full Home and menu, and also get the same
  panels, plus a Ready to update count per stream with its update link.

## Review worklist

Every stream's review page is one shared component, the **review
worklist**: the shared [Admin table](../ui-conventions/spec.md#admin-tables)
with the stream's columns, under the stream's filters, with the shared
export below it. Its parts:

1. The page heading and help, then the filters, always open (no
   disclosure), with **Stage** first (To review is the default) and the
   stream's own filters after it.
2. The selection bar and table. Columns: the stream's identity columns in
   the shared [column order](../ui-conventions/spec.md#table-column-order),
   Baseline (where a stream has one), Family's answer, Reviewer's value,
   Stage, Submitted, Last decision (who, when, note), Actions.
3. The shared export pane below the table, as on every report
   ([#1679](https://github.com/epiphany40223/parishkit/issues/1679)).

No summary tiles or charts sit above the table on a review worklist: the
counts are in the Stage filter's choices and on My review work. A stream's
statistics stay on its report page.

### Individual actions

Each row offers only the actions its stage and the reader allow, as
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) icons:
Accept (check), Edit (pencil; opens the stream's own value editor, for a
pledge the amount and frequency), Replace (swap arrows; opens the stream's
picker, for a Ministry request the Ministry list scoped to the reader),
Ignore (eye struck through), Back to review (arrow turning back), and,
in the update phase, Entered by hand / Handled (check in a box). Each takes
the optional note in the shared confirmation dialog.

### Selected-state icons

A row's decision is shown on its action icons, not only in the Stage
column: the icon of the decision in force is drawn **selected** (filled
with the decision's status colour, white glyph, `aria-pressed="true"`), the
others unselected (outline). Choosing the selected icon again is not
offered as an undo; Back to review is. This is a
[proposed convention](#proposed-convention-changes) for every table whose
row actions record a state, so Ministry requests and Roster changes to enter
use it too.

### Bulk actions

Every row with at least one action has a selection box (today Census
changes gives a box only to rows offering a review decision, so the By hand
rows have actions but no box). The bulk bar offers Accept, Ignore, Back to
review and Entered by hand / Handled, each available only while every
ticked row offers it ([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)),
with the exact count in the dialog. Edit and Replace are per row only. The
typical flow the Administrator described is a handful of Edits, then select
all on the page and Accept.

### Concurrency

Every decision is checked against the item version the row showed, as
census review and the Ministry follow-up forms already are; a row someone
else changed first is left as it is and the notice says so. Bulk decisions
are all or none.

## Update phase

The update phase has two parts that run independently, each started by a
person, each reading only Reviewed items.

### Automatic update

Only census changes the [handling registry](../data/spec.md#proposed-changes)
marks API-writable can be updated automatically: Member contact fields
(names, birth date, language, email, phones; gender stays by hand while
[#1616](https://github.com/epiphany40223/parishkit/issues/1616) is open).
Family addresses wait for a verified address read. The run is the existing
[Publish to ParishSoft](../reports/spec.md#publish-to-parishsoft) page,
renamed **Update ParishSoft**, Administrator-only, still behind the
`parishsoft_publication` switch. Its result stage is Updated in ParishSoft;
the final refresh then moves each item to Confirmed by the
[re-evaluation rules](#baseline-refresh-re-evaluation).

### Manual handling reports

Every stream has a **To enter by hand** (or To handle) report: the
worklist filtered to that stage, with Entered by hand / Handled as its row
and bulk action, exported through the shared export component in CSV, XLSX
and PDF. Roster changes to enter is the Ministry stream's report today and
becomes the shared component's first user; Census changes' *By hand* view
and a new financial "Pledges to enter" report follow.

## What cannot be automated

- **Ministry rosters**: neither ParishSoft API can add, remove or change a
  minister ([API analysis §3.3](../../../parishsoft-api-analysis.md)).
- **Pledges and share methods**: giving data is read-only.
- **Clearing a value**: the API ignores `null` and refuses `""`, so any
  answer that removes a recorded value is by hand.
- **Deceased status, moved household, new Members, prefix, suffix, marital
  status, email opt-out**: no write endpoint, or (death date) a write that
  does not complete the request.
- **Family addresses**: writable, but no v2 read returns them, so a write
  could not be verified; by hand until a verified read exists.
- **Gender**: the PUT returns success but is not applied
  ([#1616](https://github.com/epiphany40223/parishkit/issues/1616)).
- **Cannot participate, cannot attend, Additional information**: not
  ParishSoft data; they end at Handled.

## Audit

Every decision, edit, replacement, mark and update outcome is one immutable
row (who, when, the note, the resulting state), paired with a stream audit
event that names only versions and counts, never values, as
[Manual census resolution](../reports/spec.md#manual-census-resolution) and
[Automatic census review](../reports/spec.md#automatic-census-review) do
today. A stage change by a refresh is recorded as such, with no actor. Each
stream's history view lists the rows in order. New schema follows the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy).

## Shared code

- one pipeline module mapping each stream's state to a stage and its
  allowed actions (one function per stream, unit tested as a table);
- one review-worklist template and view helper built on
  `table-selection.html`, `table_actions` and the confirmation dialog;
- one decision service per stream behind a shared interface (accept, edit,
  replace, ignore, back to review, mark entered) so the web page, bulk bar
  and `pk-admin` share it ([#463](https://github.com/epiphany40223/parishkit/issues/463));
- the shared export component of
  [#1679](https://github.com/epiphany40223/parishkit/issues/1679).

## Proposed convention changes

Edits to the [UX conventions](../ui-conventions/spec.md), made there (not
here) in the vocabulary slice:

1. [Row actions and confirmation](../ui-conventions/spec.md#row-actions-and-confirmation):
   a recording action whose state is in force is drawn selected, as above.
2. Same section: "Rows without actions have no selection box" stays, and a
   row with any bulk-eligible action has one; a table never gives boxes to
   only some of its actionable rows.
3. [Admin tables](../ui-conventions/spec.md#admin-tables): a worklist's
   filters are always open and its export pane is below the table.
4. A new **Review pipeline terms** entry pointing at the
   [vocabulary](#pipeline-vocabulary).

## Open decisions

Recommended defaults are in bold; the implementation follows them unless the
Administrator chooses otherwise.

1. Who may review census changes that ParishSoft's API can write? Today only
   an Administrator. **Staff may Accept, Edit and Ignore; only an
   Administrator starts an update run.**
2. Do *By hand* census changes need a review before entering?
   **Yes, but Entered by hand on a To review row accepts and marks it in
   one step**, so Staff working through a list are not slowed down.
3. May Ministry leaders Replace (move a request to another Ministry)?
   **Only into a Ministry they also lead; Staff into any.**
4. Does a pledge need review at all, or is it Accepted on submission?
   **Reviewed, with bulk Accept**, as the Administrator described.
5. Confirmation for pledges: ParishSoft giving data could confirm an
   entered pledge. **Not in v1; Entered by hand is final.**
6. Refresh confirmation of an item marked Entered by hand: **yes, and flag
   Not seen in ParishSoft after two full refreshes.**
7. Retire "Approve"/"Publish" for "Accept"/"Update ParishSoft" everywhere,
   including audit display and `pk-admin` (with command aliases kept for
   one release)? **Yes.**
8. Does My review work replace Home for Staff and leaders, or sit at its
   top? **Replace it.**

## Delivery plan

The epic's child issues, each a small pull request:

1. [#1691](https://github.com/epiphany40223/parishkit/issues/1691) Spec and vocabulary (this document, then the convention
   edits) and the shared stage model and state mapping, no UI change.
2. [#1681](https://github.com/epiphany40223/parishkit/issues/1681) Review worklist component, selected-state icons and full
   selection.
3. [#1682](https://github.com/epiphany40223/parishkit/issues/1682) Census changes page layout: the Administrator's concrete
   feedback, which can land first.
4. [#1683](https://github.com/epiphany40223/parishkit/issues/1683) Census changes moves to the worklist; review and update
   separated.
5. [#1684](https://github.com/epiphany40223/parishkit/issues/1684) Ministry requests moves to it, with Replace.
6. [#1685](https://github.com/epiphany40223/parishkit/issues/1685) Additional information and Limitations move to it.
7. [#1686](https://github.com/epiphany40223/parishkit/issues/1686) Financial review: per-pledge items, Edit, bulk Accept.
8. [#1687](https://github.com/epiphany40223/parishkit/issues/1687) Manual handling reports on the shared export component.
9. [#1688](https://github.com/epiphany40223/parishkit/issues/1688) Automatic update: Publish to ParishSoft becomes Update
   ParishSoft over Ready items.
10. [#1689](https://github.com/epiphany40223/parishkit/issues/1689) Baseline refresh re-evaluation.
11. [#1690](https://github.com/epiphany40223/parishkit/issues/1690) My review work for Staff and leaders.
