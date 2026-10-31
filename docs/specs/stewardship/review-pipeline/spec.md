# Stewardship review pipeline

Status: **revised after the Administrator's review of 2026-10-18**
([epic #1680](https://github.com/epiphany40223/parishkit/issues/1680)); the
[decisions](#decisions) are settled. Built so far: the shared vocabulary,
stage mapping, role-action table and refresh comparison
(`parishkit.stewardship.pipeline`,
[#1691](https://github.com/epiphany40223/parishkit/issues/1691)), and
the review worklist component with its decision interface
(`components/review-worklist.html`, `web/review_worklist.py`,
[#1681](https://github.com/epiphany40223/parishkit/issues/1681)), which
Census changes uses
([#1683](https://github.com/epiphany40223/parishkit/issues/1683), with its
Add from [#1710](https://github.com/epiphany40223/parishkit/issues/1710),
below), as do the financial stream
([Pledges](../reports/spec.md#pledges),
[#1686](https://github.com/epiphany40223/parishkit/issues/1686)) and
Ministry requests, with Edit, Staff Add and the "Family asked again"
case ([#1684](https://github.com/epiphany40223/parishkit/issues/1684);
[follow-up workflows](../admin-portal/spec.md#follow-up-workflows)), and
the refresh re-evaluation of census and Ministry, with the cases it leaves
for later ([#1689](https://github.com/epiphany40223/parishkit/issues/1689);
[as built](#refresh-checks)); My review work for Staff and leaders
([#1690](https://github.com/epiphany40223/parishkit/issues/1690),
`accounts/review_work.py`), linking to those pages; and the automatic
update on Update ParishSoft
([#1688](https://github.com/epiphany40223/parishkit/issues/1688);
[as built](#automatic-update)), switched off in Production. Every Staff
and Ministry leader part ships during the current campaign. The
Family-facing view of reviewed changes
([#1705](https://github.com/epiphany40223/parishkit/issues/1705)) comes
after all of them, and ships during this campaign only if it is ready in
time ([Rolling out during this
campaign](#rolling-out-during-this-campaign)). Where this
document and a built page's spec disagree, the built page's spec describes
what runs today until the slice that moves that page to the pipeline lands
and updates it.

This specification defines one review pipeline for everything a Family tells
the parish through the [Family portal](../parishioner-portal/spec.md): census
changes, Ministry requests, the cannot-participate answers, financial
answers, and Additional information. Each stream keeps its own data, rules
and pages; this document defines what they share: the stages a change passes
through, the words for them, what each role may decide, what the Family sees
afterwards, the update phase, the confirmation by ParishSoft, and the audit.
It links to the stream specs rather than restating them:

- census: [Proposed changes](../data/spec.md#proposed-changes),
  [Review and publication](../data/spec.md#review-and-publication),
  [Pending census changes](../reports/spec.md#pending-census-changes) and
  [Update ParishSoft](../reports/spec.md#update-parishsoft);
- Ministry: [Follow-up records](../data/spec.md#follow-up-records),
  [Ministry change summary](../reports/spec.md#ministry-change-summary),
  [Follow-up workflows](../admin-portal/spec.md#follow-up-workflows) and
  [roster entry on Ministry requests](../admin-portal/spec.md#roster-entry-on-ministry-requests);
- financial: [Financial stewardship detail](../reports/spec.md#financial-stewardship-detail);
- cannot participate: [Limitations](../reports/spec.md#limitations);
- free text: [Additional information](../reports/spec.md#additional-information).

What ParishSoft's API can and cannot write is defined by the
[API analysis](../../../parishsoft-api-analysis.md) and the handling
registry in [Proposed changes](../data/spec.md#proposed-changes). General UX
rules (tables, row actions, selection, exports) are in the
[UX conventions](../ui-conventions/spec.md), which take precedence for those
rules; [proposed convention changes](#proposed-convention-changes) lists the
rules this pipeline added there.

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

The Administrator's review of the first draft (2026-10-18) added: the
decisions a reviewer may make depend on the role (a Ministry leader only
accepts, edits or ignores changes for the Ministries they lead; Staff can
also add a change of their own); a Family that signs in again sees the
reviewed result, so there is one set of changes per Family that is updated
many times; a refresh conflict is measured against both the value the Family
answered against and the reviewed value; pledges can be edited; and every
hand-entered value that a ParishSoft read returns is confirmed by the
refresh, including pledges, so that a value entered incorrectly in
ParishSoft is caught. The epic quotes both messages in full.

## Pipeline stages

Every item in every stream is in exactly one stage. A stage is derived from
the stream's own stored state (the [stream mapping](#stream-mapping) says
how); the pipeline adds no second copy of that state.

```text
Baseline ──> Submitted ──> Reviewed ──┬──> Updated in ParishSoft ──┐
                                      │                            ├──> Confirmed
                                      └──> Reported (by hand) ─────┤
                                                                   └──> Entered incorrectly
```

### Baseline stage

The baseline is what the parish already knows. For census fields, Ministry
rosters and the ParishSoft pledge and contribution figures it is the
promoted ParishSoft snapshot
([Source snapshot](../data/spec.md#source-snapshot)). For data ParishSoft
does not hold (share methods, cannot participate, cannot attend, the free
text) the baseline is empty. The upcoming pledge's baseline is the Family's
ParishSoft pledge for the upcoming stewardship year, usually none (see
[Financial stream](#financial-stream)). The Family form shows the Family its
baseline overlaid with the [working change set](#one-working-change-set),
and review never edits the baseline.

### Submitted stage

An item is **Submitted** when a Family's live response differs from what the
Family form showed it: one item per atomic change (one census field, one
Ministry join or leave, one Family's pledge answer, one Member's
cannot-participate answer, one Additional information text). Testing
responses never enter the pipeline. A later response from the same Family
follows the resubmission rule: [the rule before the reviewed
form](#resubmission-before-the-reviewed-form) until #1705's last slice
deploys, and [the reviewed rule](#family-resubmission) after it. Staff entering a
Family's answers through Open form create Submitted items like the Family's
own, also after the campaign closes
([Open form after the campaign closes](../reports/spec.md#open-form-after-the-campaign-closes),
[#2294](https://github.com/epiphany40223/parishkit/issues/2294)). An item from a response submitted at or after the campaign's end
is marked **Late** beside its Submitted time in every review worklist; it is
otherwise reviewed exactly like any other item.

### Reviewed stage

An item is **Reviewed** once someone allowed to review it records one
decision, within the limits of [role-specific actions](#role-specific-actions):

| Decision | Meaning | Value carried forward |
| --- | --- | --- |
| **Accept** | the Family's change is right as submitted | the Family's value |
| **Edit** | the change is right but its value needs correcting ("123 Main Street" → "123 Main St.", 100 weekly → 150 annual, a join into Altar Servers that was meant for Adult Choir) | the reviewer's value; the Family's stays visible beside it |
| **Ignore** | no change should be made | nothing (the baseline stands) |
| **Add** | a reviewer-created change not tied to any Family answer (add a Member to a different Ministry altogether) | the reviewer's value |

Edit is Accept with a value; it is shown as **Accepted (edited)**, which
pages draw as Accepted with the shared
[Edited chip](../ui-conventions/spec.md#status-indicators). Add
creates a new item that starts Reviewed (Accepted) under its creator's name,
shown as **Added by the parish**, and goes through the rest of the pipeline
like any other item; it may name the item it was added in place of, so the
history reads "Ignored; Added in its place: …". **Back to review** undoes a
decision while the item has not started its update, and undoes a terminal
step after it ([undoing a terminal stage](#undoing-a-terminal-stage)). An
Added item behaves exactly like a submitted one: undoing its decision
returns it to To review, where it stays listed, and staff drop it with
**Ignore**, which keeps it, marked Added by the parish, as Ignored; choosing
Ignore again undoes that, as for any item (Administrator decision,
2026-10-25, [#2173](https://github.com/epiphany40223/parishkit/issues/2173);
until then Back to review withdrew an Added item, which then left every
list; the requests it withdrew stay withdrawn). There is no separate
Remove action and no withdrawn stage for reviewers. A census addition
starts To enter by hand, has no decision to undo, and is taken back with
its own **Withdraw** ([below](#role-specific-actions)). Every decision
takes an optional note; only undoing a terminal step requires one.
Reopening an ignored item takes an optional note like any other decision
(Administrator decision, 2026-10-21,
[#1889](https://github.com/epiphany40223/parishkit/issues/1889)).

The first draft's **Replace** is dropped. What it covered is now one of two
plain decisions: when the Family meant a different value of the same kind
(another Ministry for the same Member, a corrected address), it is an
**Edit** whose value is the intended one, so the Family's answer stays beside
it in one row; when the parish wants a change the Family did not ask for,
it is an **Add**, optionally linked to an Ignored item. Replace would have
been a third mechanism with its own stage, icon and linked-pair history for
what these two already express, and Add is needed on its own anyway.

Only Reviewed items move on: nothing in the update phase ever reads a
Submitted item. Review is the only stage a reviewer works in day to day.

### Role-specific actions

Who may see an item does not change (see [Role views](#role-views)); this is
what each role may decide on the items it sees. "Leads" means the Ministry
roles configured as leaders in ParishSoft, as today. Entered by hand and
Handled are steps after a decision: offered only on an accepted item (To
enter by hand, To handle, and Entered incorrectly for Entered by hand
again), never on a To review or Conflict one
([#1890](https://github.com/epiphany40223/parishkit/issues/1890)).

| Stream | Ministry leader | Staff | Administrator |
| --- | --- | --- | --- |
| Census | nothing (never sees it) | Accept, Edit, Ignore, Back to review, Add; Entered by hand; Withdraw (an addition) | everything Staff can, plus start an update run |
| Ministry | on requests for Ministries they lead only: Accept, Edit (into another Ministry they lead), Ignore, Back to review | Accept, Edit (into any Ministry), Ignore, Back to review, Add (any Member, any Ministry); Entered by hand | as Staff (rosters are never written automatically) |
| Financial | nothing | Accept, Edit (amount, frequency), Ignore, Back to review; Entered by hand | as Staff |
| Cannot participate | nothing | Accept, Ignore, Back to review; Handled | as Staff |
| Additional information | nothing | Accept, Ignore, Back to review; Handled | as Staff |

Details the table cannot hold:

- A leader's Edit of a Ministry request offers only Ministries that leader
  leads, so an item never leaves a leader's view by that leader's own
  decision. When Staff edit a request into another Ministry, leaders of the
  carried-forward Ministry see it as theirs from then on, and leaders of the
  Family's original Ministry see it read-only as Accepted (edited) into
  that Ministry (for example "Accepted (edited) into Adult Choir", drawn
  with the [Edited chip](../ui-conventions/spec.md#status-indicators)).
- Leaders never Add: adding a Member to a Ministry the Family did not ask
  for is a parish decision. A leader who sees a mistaken request Ignores it
  with a note; Staff Add the right one.
- A census change the parish added
  ([census Add](../reports/spec.md#census-add)) takes plainly named actions
  instead of Back to review, which would read as if it had been in review
  ([#2041](https://github.com/epiphany40223/parishkit/issues/2041)): while
  To enter by hand, **Withdraw** and Entered by hand; once Entered by hand,
  **Not entered yet** (the terminal step's undo, back to To enter by hand,
  with its required note; on its row, choosing the pressed Entered by hand
  again, per [decision toggles](../ui-conventions/spec.md#decision-toggles)) and **Withdraw** (the undo and the withdrawal in
  one action, with the same note). Withdraw moves it to History, and the
  Family no longer sees it. Accepting the Family's own change of the same
  field withdraws its open addition in the same action, so one field never
  has two values waiting to be entered.
- Leaders never see or record Entered by hand, Handled or the update phase,
  as today. A leader's Accept moves the item to To enter by hand for Staff.
- Add is not offered for the financial stream (a pledge the Family did not
  give is recorded through Open form, marked Entered by Staff, as today) or
  for cannot participate and Additional information, which record what the
  Family said.
- An Add on the census stream is checked by the same rules as a Family's
  answer for that field (validation, handling registry) and gets the same
  Automatic or By hand handling. Until the update run is rebuilt
  ([#1688](https://github.com/epiphany40223/parishkit/issues/1688)), every
  added census change is To enter by hand
  ([Census Add](../reports/spec.md#census-add)).
- Every action is enforced on the server and in the database where the
  stream's rules are enforced today, never only by hiding a button.

### Updated in ParishSoft stage

An accepted item whose stream and field ParishSoft's API can write is
**Ready to update**. An Administrator (never automatically, never Staff or a
leader) starts an update run of any Ready subset at any time. The run is
the existing [publication pipeline](../data/spec.md#review-and-publication)
(preflight, plan, confirmation, fenced writes, read-after-write, final
refresh), which this pipeline reuses unchanged. A written and verified item
is **Updated in ParishSoft**, as is one the run's check found ParishSoft
already holding, with nothing written
([automatic update](#automatic-update), #1890).

### Reported stage

An accepted item ParishSoft's API cannot carry is **To enter by hand** (or,
for data that never goes to ParishSoft, **To handle**). It appears in its
stream's manual report until someone marks it **Entered by hand** (or
**Handled**). Manual reports use the shared export component
([#1679](https://github.com/epiphany40223/parishkit/issues/1679)) and the
[downloaded-files rules](../ui-conventions/spec.md#downloaded-files).

### Confirmed stage

An item is **Confirmed** when the promoted baseline itself shows the change
after its processing step: the next ParishSoft download holds the item's
carried-forward value once it is Updated in ParishSoft or Entered by hand.
Every item whose ParishSoft value some ParishSoft read returns is confirmed
this way, whether it was written by the update run or entered by hand,
including pledges ([what is confirmable](#what-is-confirmable)), and never
before its review and step (rule 1 of the
[refresh re-evaluation](#baseline-refresh-re-evaluation), #1890). For an
item no read returns (data ParishSoft does not hold, and the few ParishSoft
values no read exposes), there is nothing to wait for, so **Handled** or
**Entered by hand** is where it rests. Confirmed is the pipeline's only fully
closed stage for confirmable data. None of them is final: Back to review
undoes each ([undoing a terminal stage](#undoing-a-terminal-stage)).

### Entered incorrectly stage

An item is **Entered incorrectly** when it was marked Entered by hand and a
later refresh shows ParishSoft holding, for that field, a value that differs
both from the item's carried-forward value and from the baseline value
recorded when it was marked Entered: someone changed the field in
ParishSoft, but not to the reviewed value (a typo in a pledge amount, a
transposed phone digit). The [refresh rules](#baseline-refresh-re-evaluation)
define it exactly. It is shown:

- with a danger-colour **Entered incorrectly** status chip on the stream's
  To enter by hand report and worklist, with a count on
  [My review work](#role-views) and an Entered incorrectly choice in the
  Stage filter;
- with ParishSoft now and the carried-forward value side by side, so the
  difference is visible without opening anything.

It is fixed by Staff or an Administrator in one of two ways: correct the
value in ParishSoft and mark **Entered by hand** again (the next refresh
re-checks it), or, if ParishSoft's value is right after all, **Back to
review** twice (to To enter by hand, then To review;
[undoing a terminal stage](#undoing-a-terminal-stage)) and Edit to that
value; the row then says ParishSoft already holds it, and Entered by hand
(nothing to type) is confirmed by the next refresh (rule 1). Ministry
leaders never see this stage. An item that ParishSoft's API wrote cannot
reach it: a written item is read back in the run, and a later difference is
a [Conflict](#baseline-refresh-re-evaluation).

Ministry requests have no value that can be wrong: a join or leave either
shows in the roster or does not. For them only Not seen in ParishSoft
applies.

### Undoing a terminal stage

No stage is final (Administrator, 2026-10-20,
[#1840](https://github.com/epiphany40223/parishkit/issues/1840)): every
stream handles its terminal stages the same way. A **terminal stage** is one
an item reaches by a step after its decision: **Entered by hand**,
**Handled**, **Updated in ParishSoft**, and the refresh outcomes
**Confirmed** and **Entered incorrectly** (with the Not seen in ParishSoft
flag, which is Entered by hand still waiting). Updating (an update run in
flight) is not terminal, and History (superseded, cancelled, withdrawn) is
not a stage anyone acts on.

The rule, for every stream and every terminal stage:

- **Back to review undoes the terminal step, and only that step.** The
  decision behind it stays in force, so the item returns to the stage it
  held just before the step: To enter by hand, To handle or Ready to update,
  with its Accept or Edit value kept. To decide the item again, choose Back
  to review once more from its accepted stage. Since
  [#1890](https://github.com/epiphany40223/parishkit/issues/1890) every
  Confirmed item has a decision and a step behind it, so this is the whole
  rule for new items. Two kinds of earlier items remain as history, and
  their undo is unchanged: an item whose terminal step had no decision
  behind it returns to To review (a census change a refresh confirmed
  before anyone decided it, "Already in ParishSoft", and a Ministry request
  the refresh resolved), and a census By hand change that Entered by hand
  accepted and marked in one step (decision 2, reversed on 2026-10-21) is
  shown Accepted, so it returns to To enter by hand, Accepted.
- **A note is required**: the dialog's note is "Note (required)" for such
  a row, and a bulk Back to review needs one while any ticked row is at a
  terminal stage. This is the only Back to review that needs a note:
  reopening an Ignored item takes an optional one
  ([#1889](https://github.com/epiphany40223/parishkit/issues/1889)).
- **Staff and Administrators only.** Ministry leaders never see these
  stages ([role-specific actions](#role-specific-actions)).
- **History keeps everything.** The terminal step, every refresh check of
  it and the undo are immutable rows; the undo advances the item's
  version, so a refresh outcome recorded for the undone step (Confirmed,
  Entered incorrectly, unchanged towards Not seen) no longer places the
  item and stays in its history. A later mark starts afresh: Not seen
  counts from it.
- **Updated in ParishSoft** is undone in the portal only: the change is
  Ready to update again, nothing is written to or read from ParishSoft, and
  the dialog says so. A later update run plans it from a fresh read, like
  any Ready change, so it is written again only if ParishSoft no longer
  holds it.
- **The next refresh re-evaluates the item as an open one**
  ([baseline refresh re-evaluation](#baseline-refresh-re-evaluation)): a
  Ministry request whose roster already shows the change, or a value
  ParishSoft already holds, keeps the stage the undo gave it and says
  "ParishSoft already holds this value." (rule 1); it is Confirmed again
  only after its step is taken again.
- **Entered incorrectly** follows the same rule: Back to review returns it
  to To enter by hand (this replaces the first fix of
  [#1737](https://github.com/epiphany40223/parishkit/issues/1737), which
  sent it to To review); to Edit it to ParishSoft's value, choose Back to
  review again and Edit. Entered by hand again stays its other fix.
- The undo is a stream audit event (versions and counts only), and the
  Family's [Timeline](../reports/spec.md#timeline-review-lines) lists it as
  a Back to review line with its note, shown only to a reader who may open
  that stream's work page, where the same note is on the item's row.
- The same undo is in `pk-admin` wherever the stream's Back to review is
  ([admin automation](../admin-automation/spec.md)), with the required
  `--note`; no command asks "cannot be undone" for these marks any more,
  and none takes `--yes`.
- A Ministry request's own page offers the same undo as **Back to review**,
  with the worklist's icon and dialog
  ([Ministry requests](../admin-portal/spec.md#roster-entry-on-ministry-requests)).

The Administrator confirmed these defaults on 2026-10-21
([#1893](https://github.com/epiphany40223/parishkit/issues/1893)), with
three changes: the request page's "Clear Entered in ParishSoft" became that
page's Back to review; the `pk-admin` decision commands lost `--yes`
entirely, with no backwards compatibility; and the Timeline shows the
undo's note ("If staff can see the changes in Census/Ministry work pages,
it's ok for them to see the changes (including back to review) in the
timeline, too"). The Timeline rule is an exception for Back to review
lines, not a relaxation for every review line: Back to review always
carries a reason, the other lines keep showing no note.

How each stream stores the undo:

| Stream | Terminal step undone | Stored as |
| --- | --- | --- |
| Census | Entered by hand, Entered incorrectly, Confirmed, Updated in ParishSoft | a `reopened` resolution (forward migration 0146): the change's execution returns to `pending` with its decision kept |
| Census (Added by the parish) | Entered by hand | a `reopened` addition resolution (0147) after the `entered` one |
| Ministry | Entered by hand, Confirmed | a Back to review revision with its note (0148), which clears the roster tick or the refresh resolution and advances the request's version |
| Financial | Entered by hand, Entered incorrectly, Confirmed | a `back_to_review` pledge review row that keeps the decision in force (0149) |
| Additional information | Handled | a revision clearing the follow-up with its note, follow-up still needed (0150) |
| Cannot participate | Handled | an `accepted` review row with its note (0151) |

Forward migration 0158 (provisional number,
[#1889](https://github.com/epiphany40223/parishkit/issues/1889)) drops the
SQL guards' required note for reopening an Ignored item, on every stream
(the census review and resolution guards, and the Ministry, pledge,
Additional information and Limitations guards), and keeps every terminal
step's.

Rejected: undoing only until a refresh has checked the mark (a mistake found
after a refresh would stay wrong), keeping the marks final (a mis-click had
no way back), and sending every undo to To review (it would discard the
Accept or Edit value, so one wrong mark would mean deciding the item
again).

## One working change set

Each Family has, per stream item, exactly **one** working change: the
Family's latest answer together with its review decision. There is no
second set of reviewer changes to reconcile against the Family's next
submission. Until the release with #1705's last slice deploys, the form
shows the Family its own last answers, as today, and resubmission follows
[the rule before the reviewed form](#resubmission-before-the-reviewed-form).
From that release on, with no setting to switch
([Families see reviewed changes](#families-see-reviewed-changes)), a live
form shows the Family the baseline overlaid with the reviewed result:

| Item's state | What the Family form shows for it (its **shown value**) |
| --- | --- |
| To review, Conflict | the Family's own latest answer |
| Accepted | the Family's answer |
| Accepted (edited) | the reviewer's value |
| Ignored | the current baseline value, where the form can show it as an answer; otherwise the Family's own latest answer ([per stream](#what-the-reviewed-form-shows)) |
| Added by the parish | the reviewer's value (a Ministry the parish added appears selected) |
| Ready to update, Updating, Updated in ParishSoft, To enter by hand, Entered by hand, Entered incorrectly, To handle, Handled | the carried-forward value |
| Confirmed | the current baseline, which now holds the value |

A shown value is marked changed, with the form's existing marker, exactly
when the field carries change markers today (census fields and Ministry
choices) and the value differs from the current baseline by the stream's
canonical comparison; an Ignored item is never marked. The form never names
the reviewer, says why a value differs from what the Family typed, or
mentions ParishSoft, as the
[Effective-value merge](../data/spec.md#effective-value-merge) already
requires. This changes that merge's first rule ("never an Admin review
edit") for the reviewed form: the census slice of #1705 updates the data
spec's merge to start from the working change set. The financial stream
shows the same way (an edited pledge shows the edited amount and
frequency). The [reviewed Family form](#reviewed-family-form) is the design
that #1705 builds.

### Resubmission before the reviewed form

This is the rule until the release with #1705's last slice deploys, and
afterwards for a form drawn before that release and submitted after it
([reviewed form concurrency](#reviewed-form-concurrency)). The form shows
the Family its own last answers over the baseline, exactly as the
[Effective-value merge](../data/spec.md#effective-value-merge) does today, so
nothing a Family sees changes. When the Family submits again, each item is
compared with **the Family's previous answer for that item** (the answer
of the item's current version), never with a review result the Family has
not been shown:

1. **Unchanged** (the new answer equals the previous answer in the stream's
   canonical comparison): the item keeps its stage, decision, reviewer's
   value, notes and A (the baseline it was answered against), whatever its
   stage: To review, Conflict, Accepted, Accepted (edited), Ignored, Ready
   to update, Updating, To enter by hand, Entered by hand, Updated in
   ParishSoft, Entered incorrectly. History records "Submitted again,
   unchanged", with no review event.
2. **Changed** (the new answer differs from the previous answer and from
   the current baseline): the item reopens as **To review** with the new
   answer, and A becomes the baseline at this submit. The earlier version,
   with its decision and notes, stays in history, not in force. An earlier
   version on the Ready to update or To enter by hand list leaves it; one in
   a running update is met by that run's own checks, as today; one already
   Entered by hand or Updated in ParishSoft stays in history, and the
   [refresh rules](#baseline-refresh-re-evaluation) judge the new version
   only.
3. **Set back to the ParishSoft value** (the new answer equals the current
   baseline, and the previous answer does not): the item is **withdrawn** by
   the Family and leaves every list. When ParishSoft already holds the
   previous answer, the new answer equals both, so rule 1 applies, not this
   one: the item keeps its stage and decision, and its row says "ParishSoft
   already holds this value." ([strict phase order](#baseline-refresh-re-evaluation),
   [#1895](https://github.com/epiphany40223/parishkit/issues/1895); before it, the item was Confirmed at once, as
   `resolved_upstream`, with no review or step). An earlier version already Entered by
   hand or Updated in ParishSoft is not undone: it stays in history with
   "Withdrawn by the Family after it was entered", shown on its stream's To
   enter by hand report, so Staff can decide whether to undo the entry.
4. **New** (no earlier version): Submitted, To review.

   The financial stream places rules 3 and 4 differently for a pledge
   ParishSoft already holds
   ([Financial stream](#financial-stream), #1753).
5. **Not presented** (a section not enabled, a Member out of scope, a
   Ministry no longer offered): the item is left as it is. Leaving something
   out of the form is never a withdrawal, as today.

Rule 1 is what each stream already does for its open items: a census
proposal resubmitted with the same value is replaced by a successor that
carries its decision, edited value and baseline, or, when ParishSoft
already holds that value, stays as it is on its earlier response with no
successor, as an entered change does ([#1895](https://github.com/epiphany40223/parishkit/issues/1895); a successor is
rejected: a new change whose answer ParishSoft already holds would be no
change at all, which the database refuses for every new change); a
Ministry request
resubmitted with the same action keeps its workflow state; repeated
Additional information text creates no new item. Rules 2 to 5 are today's
supersede, cancel and withdraw outcomes
([Proposed changes](../data/spec.md#proposed-changes),
[Follow-up records](../data/spec.md#follow-up-records)). Reading the code
(2026-10-18) found three cases where today's behaviour departs from rule 1,
each reopening or duplicating something already reviewed. The stream's
slice closes each, with a test of today's behaviour first:

- **Census, already entered.** A census proposal that is Entered by hand or
  Updated in ParishSoft, but not yet in the promoted baseline, is not
  "same intent" today (only open rows are), so an unchanged resubmission
  creates a fresh unreviewed row: a duplicate To review item for work
  already done. Under rule 1 the entered item stays as it is and no row is
  created ([#1683](https://github.com/epiphany40223/parishkit/issues/1683)).
- **Census, accepted over a ParishSoft change.** An Administrator's approval
  given over a ParishSoft value they accepted as the new base is not
  carried to the successor today, because that base belongs to the earlier
  row; the successor silently becomes unreviewed. Reopening is right (the
  Family answered again against the original baseline), but it must not be
  silent: the item returns as **Conflict**, keeping the earlier decision
  visible but not in force, and history records "Reopened: the Family
  answered again, so the earlier Accept over ParishSoft's changed value no
  longer counts"
  ([#1683](https://github.com/epiphany40223/parishkit/issues/1683)).
- **Ministry, already decided.** Today's form offers only undecided
  requests as choices and otherwise shows the roster, so an Accepted join
  not yet in the roster, or an Ignored one, appears unticked. Leaving it
  unticked is correctly not a withdrawal, but ticking it again creates a
  second request, To review, beside the decided one. Under rule 1 the same
  action is unchanged: the decided request keeps its decision, and its row
  shows "Family asked again" with the date, so a reviewer sees a renewed
  request without its decision being silently reopened. The reviewer can
  use Back to review
  ([#1684](https://github.com/epiphany40223/parishkit/issues/1684)).

The two streams with no stored items today, financial and cannot
participate, follow the same rule from their first slice
([#1686](https://github.com/epiphany40223/parishkit/issues/1686),
[#1685](https://github.com/epiphany40223/parishkit/issues/1685)): one pledge
item per Family, one cannot-participate item per Member and one
cannot-attend item per Family, each compared with the previous answer.

### Family resubmission

This is the rule for a live form drawn as the
[reviewed Family form](#reviewed-family-form), that is, from the release
with [#1705](https://github.com/epiphany40223/parishkit/issues/1705)'s last
slice on ([Families see reviewed changes](#families-see-reviewed-changes)).
When the
Family submits again, each item is compared with **what the form showed for
it** (its shown value, the table above), not with the Family's earlier
answer. The shown value is the one the form was drawn with, read back from
the form's [shown items](#shown-items), never recomputed from the item's
state at submit. The rules apply in order:

1. **Unchanged** (the Family left the shown value as it was): the item keeps
   its current stage and decision, even one recorded after the form was
   drawn; no new version is made, and nothing is recorded for it beyond
   the response itself. An Ignored item whose shown value is the baseline
   stays Ignored by this rule, not withdrawn by rule 3. The response
   records exactly what the form sent (a reviewer's value, when that was
   shown), and that is the Family's answer for every reader of the
   response (reports, participation facts); the item's own Family's answer
   stays the one its version began with, so a reviewer still sees what the
   Family typed beside the reviewer's value.
2. **Changed again** (the Family's new answer differs from the shown value,
   including re-entering a value that was Ignored, or changing an edited
   value): the item reopens as **To review** with the new answer, against
   the baseline current at this submit. Its earlier answer and decision stay
   in the history, not in force. If the earlier version had already reached
   To enter by hand or Ready to update, it is withdrawn from those lists; if
   it had been Entered by hand or Updated in ParishSoft, that version stays
   in the history and the refresh rules judge the new version only.
3. **Withdrawn** (the Family's new answer equals the baseline): the item is
   closed as withdrawn by the Family, whatever its stage, and leaves every
   list (also when ParishSoft already holds its earlier answer: withdrawn,
   never Confirmed, [#1895](https://github.com/epiphany40223/parishkit/issues/1895)); an item already Entered by hand or Updated is left for the refresh
   to judge, with the withdrawal noted so Staff can undo the entry. An item
   added by the parish is never withdrawn by the Family
   ([Ministry changes added by the parish](#what-the-reviewed-form-shows)).
4. **New** (an item with no version in force: none before, or only
   Confirmed, withdrawn or replaced ones): Submitted, To review. The pledge
   is the exception: it is one item per Family that stays the Family's
   current answer after Confirmed, so a Confirmed pledge is still in force
   and is judged by rules 1 to 3.
5. **Not presented** (a section not enabled, a Member out of scope, a
   Ministry no longer offered): the item is left as it is, as before the
   reviewed form.

The financial stream keeps its own placement of rules 3 and 4 for a pledge
ParishSoft already holds at the submit
([#1753](https://github.com/epiphany40223/parishkit/issues/1753)): rule 1
comes first, so an unchanged pledge keeps its decision, and only a changed
or new pledge is judged by that placement.

A review decision recorded while the Family had the form open does not
reject the Family's submit: the form's
[baseline reference](../data/spec.md#submission-concurrency) records each
item's version as shown ([shown items](#shown-items)), so an item the
Family did not touch keeps the newer decision, and an item the Family did
change follows rule 2. Review decisions are not part of the form's
dependency projection, so they never cause a stale-form refusal; a refresh,
another session's submission or a form-definition change still does, as
today.
History keeps every version of every item, with who (the Family, or the
Staff member using Open form) and when. The same holds under
[the rule before the reviewed form](#resubmission-before-the-reviewed-form):
a decision recorded while the Family had the form open is kept for an
unchanged item by its rule 1.

### Families see reviewed changes

Families see the reviewed result as soon as the release with
[#1705](https://github.com/epiphany40223/parishkit/issues/1705)'s last
slice deploys, mid-campaign or not ([decision 9](#decisions)). There is no
setting, switch or `pk-admin` command for it: the Administrator dropped
the planned on/off setting on 2026-10-19, because Staff edit only to fix
errors or to make updates the Family asked for, so a changed value will not
confuse a Family. Only live forms show the reviewed result: a Testing form
always shows the Family its own Testing answers, since Testing responses
never enter the pipeline.

No Family-facing explanation of reviewed values is needed, because the
Administrator's stated intent (2026-10-18) limits what Staff change in a
Family's submission to two kinds:

1. consistency fixes that a Family will not notice ("123 Main Street" →
   "123 Main St."); and
2. corrections the Family asked for (a typo in the pledge amount, the wrong
   Ministry selected).

So a Family that signs in again sees either what it typed, a value it
would not notice, or the correction it asked for. Ignored answers show the
baseline, as the [table](#one-working-change-set) says. Staff guidance on
Edit and Ignore states this intent
([Staff-facing text](#privacy-and-wording)); the form still
never names the reviewer or gives a reason.

- Codes and links are unaffected
  ([credentials and the submit path](#credentials-and-the-submit-path)).
- Until that release, the form keeps today's merge, and resubmission
  follows [the rule before the reviewed form](#resubmission-before-the-reviewed-form).
  Every other part of the pipeline (stages, decisions, worklists, My review
  work, update and confirmation) does not depend on #1705.
- A form open across that deploy keeps working, because each form records
  the items it showed
  ([reviewed form concurrency](#reviewed-form-concurrency)).
- Rollback is fail-forward, as for every release
  ([Production upgrades](../operations/spec.md#production-upgrades)): a
  problem with the reviewed form is fixed in a new release, never by
  returning live forms to the earlier rule.
- [#1705](https://github.com/epiphany40223/parishkit/issues/1705) comes after every Staff and Ministry leader slice. If it is ready
  before the current campaign closes it deploys then; if not, it waits,
  and nothing else depends on it.

### Reviewed Family form

This is the design of
[#1705](https://github.com/epiphany40223/parishkit/issues/1705): what a
Family sees when it signs in again once it ships
([Families see reviewed changes](#families-see-reviewed-changes)), how the form
remembers what it showed, and how it ships. It builds on each stream's
stored decisions as their slices define them (census
[#1683](https://github.com/epiphany40223/parishkit/issues/1683), Ministry
[#1684](https://github.com/epiphany40223/parishkit/issues/1684),
Additional information and Limitations
[#1685](https://github.com/epiphany40223/parishkit/issues/1685), financial
[#1686](https://github.com/epiphany40223/parishkit/issues/1686) with
[#1704](https://github.com/epiphany40223/parishkit/issues/1704),
[#1747](https://github.com/epiphany40223/parishkit/issues/1747) and
[#1753](https://github.com/epiphany40223/parishkit/issues/1753), and the
refresh checks of
[#1689](https://github.com/epiphany40223/parishkit/issues/1689)) and
changes none of them before its last slice.

**One working change per item.** Every stream already keeps at most one
version of an item in force; the reviewed form makes that the only thing
a Family sees and answers. An item's identity is its stream's: one census
field of the Family or of one Member (a Member's moved, deceased and death
date answers included); one proposed Member, by the key the form gives it
when the Family adds it (a Member removed and added again gets a new key,
so it is a new item); one Ministry request, by the Member, the **Family's
own** Ministry and join or leave, as
[#1684](https://github.com/epiphany40223/parishkit/issues/1684) matches
requests (a request edited into another Ministry keeps the Family's
Ministry as its identity); one Family's pledge answer; one
Member's cannot-participate answer, the Family's cannot-attend answer, and
the Family's Additional information. A Family's resubmission either leaves
that one version as it is (rule 1) or replaces it (rules 2 and 3); it never
adds a second version beside it, and a reviewer's decision always applies
to the version in force. The Family form, the worklist and the stage
counts all read the same item, so they cannot disagree.

#### What the reviewed form shows

The [shown-value table](#one-working-change-set) applied to each stream.
"Decided" means Accepted, Accepted (edited) or Added by the parish, at any
later stage short of Confirmed; "carried-forward" is the reviewer's value
when there is one, else the Family's.

| Stream | Decided | Ignored | Confirmed |
| --- | --- | --- | --- |
| Census field | the carried-forward value in the field, marked changed | the current ParishSoft value, unmarked; for a field whose baseline the form cannot show (the home and mailing addresses, the email opt-out), the Family's own latest answer, unmarked | the current ParishSoft value, unmarked |
| Member moved, deceased, death date | the Member in that state, as the form shows it today, Entered by hand and Updated included | the Member as ParishSoft has them | the Member as ParishSoft has them |
| Proposed Member | the Member's page with the carried-forward names and details, until Entered by hand (ParishSoft then lists the Member itself, so the New Member is not shown again) | not shown; its absence from the submit is rule 1 (it stays Ignored), and a Member added again has a new key, so it is a new item | a current Member, as ParishSoft has them |
| Ministry join | the carried-forward Ministry selected and listed under "Joining:" (a join added by the parish too, [below](#what-the-reviewed-form-shows)) | not selected | a current Ministry, "Continue in this ministry" |
| Ministry leave | "Stop participating in this ministry" selected, in the attention colour (a leave added by the parish too, below) | "Continue in this ministry" | not listed (no longer a current Ministry) |
| Pledge | the carried-forward annual pledge, frequency and share methods (the reviewer's when the Edit changed them, [#2177](https://github.com/epiphany40223/parishkit/issues/2177)), with the Family's own Cannot contribute answer | the Family's own latest answer | the carried-forward answer (ParishSoft holds its amount) |
| Cannot participate, cannot attend | the Family's own answer | the Family's own answer | (not confirmable) |
| Additional information | the Family's own text | the Family's own text | (not confirmable) |

To review and Conflict items show the Family's own latest answer in every
stream, as the table above says. Notes on the table:

- **Ignored where no baseline can be shown.** A pledge must be answered and
  its baseline (usually no ParishSoft pledge) is not an answer the form can
  prefill; cannot participate, cannot attend and Additional information
  record what the Family said, have no baseline and no Edit, and Ignore on
  them means "no action needed", not "wrong"; and an address or email
  opt-out baseline is unavailable to the form today. So each shows the
  Family's own latest answer, unmarked. Rule 1 keeps such an item Ignored
  when the Family leaves it, so showing it does not reopen it. A census
  field gains a shown baseline once the form can show one (the home address
  after [#1749](https://github.com/epiphany40223/parishkit/issues/1749)).
- **An edited pledge set to $0.00** shows a zero pledge, so the frequency
  and share methods are hidden and cleared as the Family form already does
  for a zero pledge; the shown value is compared in that cleared form.
- **A Ministry request edited into another Ministry** draws the request's
  action on the carried-forward Ministry, and that choice maps back to the
  request, whose identity is the Family's own Ministry. Leaving it as shown
  is rule 1. Selecting the Family's original Ministry while the edited
  request is in force is rule 2 on that same request: it reopens To review
  with the Family's Ministry, so no second request is made. If the
  carried-forward Ministry stays selected as well, that selection is then a
  request of its own (rule 4), since the reopened request no longer carries
  it. If the roster makes the edited request impossible to draw (a leave of
  a Ministry the Member is not in, a join of one the Member is already in),
  the form shows the roster state, records no shown item for it, and it is
  not presented (rule 5).
- **A Ministry join or leave added by the parish** is shown as any join
  (selected and listed under "Joining:") or leave ("Stop participating in
  this ministry" selected), with the same marker. Undoing it on the form
  is not a withdrawal (rule 3 does not apply to an Added item): the request
  keeps its decision and its row says "Family unticked" (a join) or "Family
  chose to continue" (a leave) with the date, as "Family asked again" does,
  so Staff see it and can use Back to review and Ignore to drop it
  ([Reviewed stage](#reviewed-stage)). A change the parish added stays
  parish work until Staff decide: one sent back to To review is shown the
  same way and is not withdrawn by an untick either, and one Staff ignored
  is shown as the roster has it, so the Family choosing it again makes a
  new request of its own, To review, which the lists show in its place;
  the Ignored one is not replaced and is listed under History
  ([#2173](https://github.com/epiphany40223/parishkit/issues/2173)).
- **A moved, deceased or death-date request already entered** (Entered by
  hand or Updated in ParishSoft, not yet Confirmed) is presented in its
  state on the reviewed form, and an unchanged resubmission is rule 1. This
  differs from the rule before the reviewed form, where an entered terminal request is no
  longer presented and asking again makes a new request
  ([#1683](https://github.com/epiphany40223/parishkit/issues/1683)), because
  the reviewed form shows the Family the carried-forward state.
- **Cannot participate and Cannot contribute.** Unchecking either restores
  the shown values of the answers it hides, not the Family's older ones.
- **A census change added by the parish**
  ([census Add](../reports/spec.md#census-add),
  [#1710](https://github.com/epiphany40223/parishkit/issues/1710)) is
  drawn in its field as the field's value, editable like any other value,
  marked changed where it differs from ParishSoft's, with an "Added by the
  parish" badge directly under the field (under the address fields for a
  Family address) for as long as the field is drawn with it
  ([#2008](https://github.com/epiphany40223/parishkit/issues/2008); the
  design the Administrator chose on 2026-10-22, which replaced a read-only
  line under the field). The form records it as a shown item of its own
  ([Shown items](#shown-items)). Only a Member's ordinary fields and the
  home and mailing addresses take one, the fields census Add offers but a
  death date. A field shows its addition while it stands: its open one (To
  enter by hand; a field has at most one), or, when it has none, its newest
  Entered by hand one while ParishSoft still has the value it had when the
  change was added; once ParishSoft's value changes the field shows
  ParishSoft's. A withdrawn addition is never shown, so Withdraw (or
  Accept of the Family's own change of the field, which withdraws an open
  addition, #2041) returns the field to what it would otherwise show. A Family change of the
  field that is not Ignored and is newer than the addition, whatever its
  stage (Confirmed included, which records no shown item of its own), or
  still open (a change reopened before Back to review was refused for it,
  [census Add](../reports/spec.md#census-add)), is shown instead, as the
  table says (the Family's later answer); an Ignored change the addition
  was added in place of keeps its own shown item.
  - **Submitted unchanged.** An answer equal to the added value by the
    field's canonical comparison leaves it as shown (rule 1): no new Family
    change; the addition stays as Staff left it, and an earlier change of
    the field stays as it is (an Ignored one stays Ignored).
  - **Edited.** Any other answer is an ordinary Family change of the field
    and goes to review (the Administrator's decision 2 on
    [#2008](https://github.com/epiphany40223/parishkit/issues/2008)): always
    a new change To review, even when it is ParishSoft's value or the value
    of the Ignored change the addition was added in place of, so Staff see
    that the Family turned the added value down. It replaces an open
    earlier change of the field (the Ignored one included), as rule 2 does.
    The addition is left as it is, and Staff decide between them in the
    census list, whose open addition says the Family has an open change of
    this field too ([census Add](../reports/spec.md#census-add)).
  - **While the form is open.** An addition withdrawn after the form was
    drawn makes the submit a stale-form review
    ([Reviewed form concurrency](#reviewed-form-concurrency)); one Entered
    by hand meanwhile is still left as shown.
  - A Testing form never shows an addition.

#### Shown items

When a live form is drawn as the reviewed form, the form's
[baseline reference](../data/spec.md#submission-concurrency)
(`FamilyFormBaseline`) records, beside today's version references:

- `reviewed_view`: true, drawn as the reviewed form (false for a Testing
  form, for every form drawn before the release with #1705's last slice,
  and for every baseline made before the column), so the submit knows which
  rule judges the form; and
- `shown_items`: one entry per census, Ministry or pledge item in force for
  the Family, the pledge always included, Confirmed or not; a census or
  Ministry item that is Confirmed, withdrawn or replaced has none, since
  the form shows the plain baseline for it. Each entry holds the stream,
  the item's identity, the stream's item version (the one the
  [worklist's concurrency check](#concurrency) already uses), the immutable
  decision row in force (or none), and which value was shown (the Family's
  answer, the reviewer's value, or the baseline). A census field with a
  standing change the parish added has one more entry, naming the
  addition, whose shown value is the added value
  ([What the reviewed form shows](#what-the-reviewed-form-shows)). Cannot participate,
  cannot attend and Additional information record no entries: their shown
  value is always the Family's own answer, which the response itself
  holds.

No value is copied: the shown value is read back from the immutable rows
the entry names (the Family's submitted answer, the decision row's
reviewer's value, the baseline in the snapshot the form already pins), by
the same function that drew the form. On a successful submit both fields
are copied, unchanged, to the immutable `Submission`, in the transaction
that already moves the baseline's pins to it, so every stream can read
what the response's form showed. The migration that adds the four columns
is additive (`reviewed_view` not null, false by default; `shown_items`
nullable) and changes no existing row, so every existing baseline and
Submission reads as not drawn as the reviewed form.

The submit then applies the resubmission rule for the form's own
`reviewed_view`: a live form with it false (one drawn before the release
with #1705's last slice) is judged by
[the rule before the reviewed form](#resubmission-before-the-reviewed-form),
and one with it true by [the reviewed rule](#family-resubmission). For
each item under the reviewed rule:

- census and Ministry make no new row for an unchanged item, as the
  entered-census and "Family asked again" cases of the earlier rule
  already do; each stream's database completeness check admits that
  absence only when the response's answer equals the shown value by the
  stream's canonical comparison;
- the pledge stage's selection treats a later response as unchanged, in
  every stage (Confirmed included), when its `shown_items` entry says the
  pledge was shown and its answer equals the shown value (an edited pledge
  sent back as shown), as well as when it equals the decided response's
  answer, as today; #1753's submit-time placement applies only to a pledge
  that is changed by this test;
- cannot participate, cannot attend and Additional information show the
  Family's own answer in every stage, so their earlier comparison is
  already the reviewed one and they change nothing.

#### Reviewed form concurrency

- **A decision while the form is open** is kept for an item the Family did
  not change (rule 1) and replaced for one it did (rule 2). The submit
  never refuses for it.
- **A refresh while the form is open** that changes a relevant ParishSoft
  value (including one that Confirms an item or finds a Conflict) is
  today's stale-form review: the tab fetches a fresh form, keeps its
  unsaved edits, and asks about competing values
  ([submission concurrency](../data/spec.md#submission-concurrency)). The
  tab tracks its edits against the shown values it was drawn with. The
  fresh baseline keeps the old one's `reviewed_view` and carries over the
  old shown item of every item whose relevant ParishSoft inputs did not
  change, so an untouched field keeps the value the Family saw, even if a
  decision was recorded meanwhile (rule 1 then keeps the newer decision).
  Only the affected fields take fresh shown values, and they are listed in
  the review as today.
- **A change the parish added, withdrawn while the form is open**
  (Withdraw, or Accept of the Family's own change of the field, #2041), is
  a stale-form review too, so the Family never sends a value
  Staff took back as if it were still the parish's: the fresh form draws
  the field without it, and the tab keeps an edit the Family made to the
  field. One Entered by hand meanwhile changes nothing.
- **Another session's submission** advances the Family's effective version
  and is refused for review, as today.
- **A form open across the deploy** of #1705's last slice is never refused
  for it: its baseline reference keeps today's dependency projection with
  `reviewed_view` false, so it is judged by
  [the rule before the reviewed form](#resubmission-before-the-reviewed-form),
  and the Family's next sign-in draws a reviewed form. A stale-form review
  after the deploy fetches a fresh baseline that keeps the old one's
  `reviewed_view`, as above, and the fresh form is drawn by the same rule:
  with `reviewed_view` false, today's merge (the Family's own answers) and
  no shown items, as a Testing form is drawn. Otherwise the tab would show
  reviewed values that the earlier rule compares with the Family's previous
  answer, and an edited value sent back as shown would reopen. The deploy
  changes no stored answer or decision.

#### Privacy and wording

- The form payload for an item carries only what it carries today: the
  value and the changed and conflict markers, and, on a census field,
  whether the value is a change the parish added (`added`, true or false;
  [What the reviewed form shows](#what-the-reviewed-form-shows)). It never
  carries the stage, the decision, a note, a reviewer's or Ministry
  leader's name or role, a decision time, a refresh outcome or any mention
  of ParishSoft, and the Family pages render none of them; "Added by the
  parish" appears only as that badge's words. A test lists the payload's
  keys per field and fails on any other, and scans the rendered pages for
  the pipeline's words.
- No new Family-facing text but the parish-added census badge above: the
  existing marker ("Your updated value"),
  Ministry lists, Review page, welcome notice, Thank You page and receipt
  are unchanged, because the Administrator's stated intent limits Staff
  changes to consistency fixes and corrections the Family asked for
  ([Families see reviewed changes](#families-see-reviewed-changes)). The
  receipt still carries no answer values.
- Staff-facing text, always present from the release with #1705's last
  slice (it ships in the same slice that turns the reviewed form on, so it
  is never shown while untrue): each census, Ministry and financial
  worklist's page help adds, in these words: "A Family that signs in again
  sees the values you accepted, edited or added, and, for a change you
  ignored, ParishSoft's value where the form can show it, otherwise the
  Family's own answer. Edit a Family's answer only to make it consistent
  (for example "St." for "Street") or to make a correction the Family asked
  for." The Ministry page help also says that a Family undoing a request
  the parish added, or one already Entered by hand, leaves it decided with
  "Family unticked" or "Family chose to continue" on its row. The Edit,
  Add and Ignore dialogs add one sentence: Edit (every stream) "The Family
  will see this value the next time it signs in."; Ministry Add "The Family
  will see it chosen the next time it signs in: a join as selected, a leave
  as Stop participating. If the Family undoes it, this row will say so."
  (one dialog takes both a join and a leave, so one sentence covers both);
  and Ignore, on Ministry requests and on a Member's census fields and
  requests, whose ParishSoft value the form shows, "The Family will see
  ParishSoft's value the next time it signs in." Census Add: "The Family
  will see this value on its form, marked Added by the parish, the next
  time it signs in." ([What the reviewed form shows](#what-the-reviewed-form-shows)).
  Ignore on a Family address, the email
  opt-out or a New Member says nothing either, as the form shows the
  Family's own answer, or leaves the New Member out.
- Open form shows Staff the same reviewed form, with the same rules, and
  records the Staff member as the actor, as today.

#### Reviewed form accessibility

The reviewed form adds no control or page, and one message only: the
parish-added census badge, text that names its meaning (never colour
alone), drawn with the field when the form is drawn and named by the
field's description (each address control's and the address fieldset's for an address), so it never
appears, disappears or moves while the Family works, typing included. A shown value uses
the same field, the same programmatic state (a selected Ministry is a
checked control, "Stop participating" the selected choice) and the same
non-colour change marker (icon and text label) as a Family's own answer,
so assistive technology reads a reviewed value exactly as it reads one the
Family typed. Nothing appears, disappears or moves while the Family works
([no layout shift](../ui-conventions/spec.md#no-layout-shift)), and the
form's existing keyboard, focus and accessible-name checks run on a reviewed form too.

#### Credentials and the submit path

- No Family code or link is created, revoked, rotated, reissued or
  re-sent; no slice and no migration touches a credential or session.
- The submit stays one transaction under today's locks and lock order,
  with today's validation, refusals, receipt outbox and fact hint. The
  reviewed rule only changes which comparison value each stream's existing
  derivation uses, and reads only Family-scoped rows already locked by the
  work order; there is no provider call.
- Before the last slice, the Family form, its payload and the submit's
  outcomes are unchanged: each of slices 1 to 5 proves it with a render and
  payload comparison against the base. Every slice, the last included,
  keeps the baseline reference's dependency projection and token, so a form
  open across any of their deploys submits without a stale-form refusal.

#### Reviewed form delivery

Small pull requests after every Staff and leader slice. Slice 1 shipped
alone (#1779); slices 2 to 6 ship together in one pull request, since
slices 2 to 5 draw nothing on their own, with each slice's tests as listed.
Slices 1 to 5
build the reviewed form without drawing it for a Family: each stream's
reader and reviewed derivation is called only by tests until slice 6
connects the live form and the submit to them; slice 2's recording of
`shown_items` likewise runs at a live baseline issue only from slice 6, and
slice 3's and 4's relaxed completeness checks admit an absence only where
a shown item exists, so they are inert while every live form records none.
There is no setting
([Families see reviewed changes](#families-see-reviewed-changes)), so
nothing a Family sees changes, and no form records `reviewed_view` true,
until the release with slice 6 deploys. Rollback is fail-forward: a
problem found after that deploy is fixed in a new release.

1. **Shown-items storage.** The additive migration for `reviewed_view` and
   `shown_items` on `FamilyFormBaseline` and `Submission`, with a
   self-verifying SQL block, and the submit copying them. Nothing sets them
   yet. Needs only #1709's base. Tests: the migration's DO block, the copy
   on submit, and a render and payload comparison showing the Family form
   unchanged.
2. **Shown-value function.** One shared `pipeline.shown_value` (stage and
   stream to which value is shown and whether it is marked), unit tested as
   a table mirroring the [shown-value table](#one-working-change-set) and
   the [per-stream table](#what-the-reviewed-form-shows), and the shared
   recording of `shown_items` at baseline issue, driven by per-stream
   readers that slices 3 to 5 add. It needs the stages it names, so it
   follows #1683, #1684, #1685, #1686 (with #1704, #1747 and #1753), #1688
   and #1689. Tests: the unit tables; a PostgreSQL test that a Testing form
   records nothing; a render and payload comparison showing the live Family
   form unchanged.
3. **Census.** The census reader; the census reviewed draw from the working
   change set (the
   [Effective-value merge](../data/spec.md#effective-value-merge) gains its
   reviewed form); the census reviewed derivation compares with the shown
   value; a forward migration lets the completeness check admit an
   unchanged item's absence. Needs slice 2. Tests (PostgreSQL), calling the
   reviewed draw and derivation directly, since slice 6 connects them:
   every census stage times unchanged, changed, set back to ParishSoft's
   value and new; Ignored left as shown stays Ignored; an entered terminal
   request left as shown; a decision recorded while the form is open kept
   for an untouched field; a form with `reviewed_view` false judged by the
   earlier rule and one with it true by the reviewed rule; a refresh while
   open keeping an untouched field's shown value; the live Family payload
   byte-identical to the base; the form-load and submit query counts.
4. **Ministry.** The Ministry reader; reviewed choices drawn from the
   working change set (edited and added requests included); the reviewed
   derivation compares with the shown selection; completeness check as for
   census. Needs slice 2. Tests as for census, plus an edited request left
   as shown, unselected, and with its original Ministry selected (rule 2 on
   the same request); an added join unticked ("Family unticked", decision
   kept); an added leave switched to continue ("Family chose to continue",
   decision kept).
5. **Financial.** The pledge reader; the reviewed pledge drawn from the
   working change set; the pledge stage selection reads `shown_items` in
   every stage; #1753's submit-time placement only for a changed pledge.
   Needs slice 2. Tests: every pledge stage, Confirmed included, times
   unchanged, changed and edited to zero; a Confirmed edited pledge
   resubmitted unchanged stays Confirmed; the share methods kept; a refresh
   check after an unchanged resubmission still counting; the live Family
   payload byte-identical to the base.
6. **Turn on the reviewed form, Staff wording and browser coverage.** The
   live Family form (and Open form) draws the reviewed form and records
   `shown_items` with `reviewed_view` true, and the submit judges each form
   by its `reviewed_view`; a Testing form is unchanged. The page help and
   dialog sentences above, always present from this release. Browser tests
   in Chromium, Firefox and WebKit of a reviewed form (shown values,
   markers, Ministry lists, Review page, unchanged resubmission); the
   privacy test of the payload keys and rendered words; a pk-local
   checklist for the Administrator, who approves this release's deploy as
   for any other. Needs slices 3 to 5. Tests also cover a form drawn before
   the release (`reviewed_view` false) submitted after it, judged by the
   earlier rule without a stale-form refusal, and one refreshed after the
   release (a stale-form review) still showing the Family's own answers and
   judged by the earlier rule; a Testing form showing its
   own Testing answers; and that no credential is touched.

#### Reviewed form implementation

How the design above is built (`responses/reviewed.py` and each stream's
derivation), where the design leaves a choice:

- **Entries.** A census entry is `{stream: "census", entity, key, field,
  item, version, decision, shown}`: the change (`item`, a
  `ProposedChange`) at its version, the `ProposalReview` whose Edit set a
  reviewer's value (`decision`, found on the change or, for a value carried
  to a same-answer successor before the reviewed form, on the change it
  replaced), and `shown` (`family`, `reviewer` or `baseline`). A change
  the parish added has an entry of the same shape with `shown` `parish`,
  whose `item` is the `CensusAddition` and whose `version` and `decision`
  are null; a field may then have two entries (the addition and the
  Ignored change it replaced), and the form draws the addition's. A Ministry
  entry names the request, the Family's own Ministry and action, and, for an
  Edit, the `MinistryWorkflowRevision` that carried another Ministry
  forward. The pledge entry names the Family's current live response and,
  for an edited pledge, the `PledgeReview` in force. Entries are sorted by
  stream and identity. A form drawn as the reviewed form records an empty
  array when nothing is in force.
- **Stages.** Each entry's stage is read as the stream's worklist reads it:
  a census change by `census_placement` with its latest refresh check, a
  Ministry request by its workflow, roster tick and refresh check, the
  pledge by the pledge stage selection. `pipeline.shown_value` maps the
  stage to the shown value; a reviewer's value with no review row to read
  it back from shows the Family's answer.
- **Read back.** The submit compares each answer with the entry's value by
  the stream's canonical comparison (`reviewed.left_as_shown`); an Ignored
  census entry shown as the baseline reads the baseline from the form's own
  snapshot. Census and Ministry rule 1 make no row; the next form finds the
  change kept on its earlier response through the response's shown items
  (`effective.proposal_index`; a Ministry request is already the newest for
  its identity).
- **Refresh while open.** The fresh form keeps the earlier entry of each
  item whose relevant inputs did not change (a census field's ParishSoft
  value, a Member's roster and the offered Ministries, the pledge's
  financial inputs), and only while the Family's response it answers is
  still the latest; otherwise it takes fresh entries. A form drawn before
  the release keeps the earlier rule on a refresh only while it answers the
  same response; after another session's response it is drawn as the
  reviewed form.
- **Ministry withdrawal and replacement.** A decided request the Family
  replaced or withdrew keeps no outcome or Edit on its row, as the table
  requires (its revisions keep the decision's history). One Staff ticked
  Entered in ParishSoft is never replaced or withdrawn by the Family: a
  changed answer adds a new request beside it, and the refresh judges the
  entered one. An undo of a
  request the parish added or Staff ticked Entered in ParishSoft is kept and
  shown on the row ("Family unticked", "Family chose to continue"), at any
  stage, To review included; the submission's completeness check admits an
  open request the parish added left in force. An Ignored request the
  parish added that the Family chooses again is not replaced: it stays
  Ignored, listed under History, and the Family's new request, the latest
  for that Member and Ministry, is the one the other stages show. On a form
  drawn before the reviewed form, an added request back at To review that
  the Family chooses is replaced by the Family's own request, as any open
  request is, since the Family now asks for it itself (frozen file 0180,
  [#2173](https://github.com/epiphany40223/parishkit/issues/2173)). An
  Ignored New Member's Ministry requests are judged as before the reviewed
  form, since the New Member itself is not shown.
- **Pledge.** The pledge stage selection treats a response that sent back
  an edited pledge as shown as unchanged. Its row's Family's answer then
  shows what the response sent (the reviewer's amount), since the stream's
  item is the Family's current response; the Reviewer's value stays beside
  it.
- **Database.** Frozen files 0153 (census: the derived guard and the
  submission completeness check), 0154 (Ministry: the request guard and the
  Ministry completeness check) and 0155 (the pledge item selection), each
  with a self-verifying block. A response not drawn as the reviewed form
  takes exactly the earlier paths, except that it may follow one that was:
  a change that response showed and left as shown (the Family's answer, a
  reviewer's Edit, or the baseline of an Ignored change) stays on its
  earlier response, and the census guard finds it as `proposal_index` does,
  as the predecessor whose decision a same answer carries and as the change
  a new answer supersedes or a set-back withdraws (frozen file 0163,
  [#1909](https://github.com/epiphany40223/parishkit/issues/1909)). Frozen
  file 0170 ([#2008](https://github.com/epiphany40223/parishkit/issues/2008))
  lets the submission completeness check admit an answer left as a shown
  change the parish added: no row for it, and an earlier open change of
  that field left as it is, only while the entry names this Family's
  addition for that field, not withdrawn, with that value. Under the same
  entry any other answer must have its row, and the derived guard admits
  that row even where its value is ParishSoft's (an edited added value is
  always a change To review, [What the reviewed form shows](#what-the-reviewed-form-shows)).

#### Reviewed form decisions

The Administrator settled these on
[#1705](https://github.com/epiphany40223/parishkit/issues/1705) on
2026-10-19. The numbers are those of the questions posted there.

1. **Ignored with no showable baseline** (a pledge, cannot participate,
   cannot attend, Additional information, an address or email opt-out):
   **confirmed:** show the Family's own latest answer, unmarked.
   *Rejected:* show the field empty, which would force a Family to retype a
   required pledge and would read a cleared answer as a withdrawal.
2. *Removed:* where an on/off switch would live. The Administrator dropped
   the switch ([Families see reviewed changes](#families-see-reviewed-changes)).
3. **Testing mode:** **confirmed:** only live forms show the reviewed
   result; a Testing form shows its own Testing answers, since Testing
   responses never enter the pipeline. The reviewed form is rehearsed in
   pk-local with fixture data instead.
4. **The change marker's label for a reviewer's value:** **confirmed:** keep
   "Your updated value". *Rejected:* a neutral "Updated value" on every
   marker, which changes the form for every Family.
5. **An ignored leave** (a leader recorded "Stayed in the Ministry"):
   **confirmed:** show "Continue in this ministry", the parish's decision,
   in line with the Administrator's intent that Staff change only what the
   Family asked for.
6. *Removed:* switching the setting off after on. With no switch, rollback
   is fail-forward.
7. **Staff wording:** the page help with the Administrator's intent and one
   dialog sentence, as above, always present from the release with the last
   slice (the default stands; no answer was given). *Rejected:* page help
   only.
8. **A Ministry join or leave added by the parish:** **confirmed:** show it
   as any join or leave with the usual marker, and treat the Family undoing
   it as "Family unticked" or "Family chose to continue" on the request,
   keeping its decision, never a withdrawal. *Rejected:* do not present
   Added requests (the Family then never sees parish work it may have asked
   for by phone), or let an untick withdraw it (parish work could disappear
   without a Staff decision).

## Baseline refresh re-evaluation

Every promoted refresh re-evaluates every open item of a ParishSoft-backed
stream against the new baseline, in the promotion transaction, as
[refresh reconciliation](../data/spec.md#parishsoft-refresh-reconciliation)
already does for census proposals and Ministry requests. For each item it
compares the refreshed baseline value **R** with two stored values:

- **A**, the baseline value the item was answered against (the ParishSoft
  value when the Family submitted this version, or when Staff added it), and
- **C**, the item's carried-forward value: the reviewer's value if Edited or
  Added, else the Family's answer (for a To review item, the Family's
  answer).

The rules, in order:

1. **R = C after the processing step: Confirmed.** An item that is
   Updated in ParishSoft, Entered by hand or Entered incorrectly becomes
   Confirmed when the refresh finds ParishSoft holding the change. This is
   how a by-hand entry and an update are confirmed. Items go through the
   phases in order (Administrator, 2026-10-21,
   [#1890](https://github.com/epiphany40223/parishkit/issues/1890)):
   review, then update, enter by hand or handle, then confirm. So a refresh
   never confirms an item straight from To review, Conflict, Ready to update
   or To enter by hand, even when ParishSoft already holds the new value
   because someone changed it there separately. Such an item keeps its
   stage (a Conflict returns to the stage its decision gives, as when R
   is back at A). Its row says "ParishSoft already holds this value." under
   its stage, so the reviewer can Accept quickly and Staff know there is
   nothing to type. The pledge words at $0.00 differ
   ([Financial stream](#financial-stream)). A Ready to update item ParishSoft
   already holds is carried by the update run without a write
   ([automatic update](#automatic-update)); a To enter by hand one is marked
   Entered by hand as usual, with nothing to type, and the next refresh
   confirms it. A census change accepted over a Conflict (Use the Family's
   value anyway) that ParishSoft then catches up with keeps its approval
   and stage too: a new ParishSoft value otherwise sends such an approval
   back to To review, because the accepted ParishSoft value it rests on is
   stale, but with ParishSoft holding the change nothing is left to write
   ([automatic census review](../reports/spec.md#automatic-census-review)). Rejected: auto-accepting such an item (recording an Accept
   for the reviewer, a decision nobody made) and keeping the shortcut for
   To review alone (it still skips review). An Ignored item is not
   re-evaluated by this rule: R = C on an Ignored item is recorded as
   "ParishSoft now matches the Family's answer" for the reviewer, and the
   item stays Ignored. Items a refresh confirmed straight from review
   before #1890 ("Already in ParishSoft", with no reviewer, and Ministry
   requests the refresh resolved) stay as they are: no migration rewrites
   them, and Production held none. A pledge's stage is derived from its
   checks, not stored, so such a pledge reads To review again, saying
   ParishSoft already holds it.
2. **R = A: unchanged.** Nothing happened in ParishSoft; the item stays where
   it is. For an item marked Entered by hand (or Entered incorrectly), rules
   2 and 3 compare R with the value recorded at the mark instead of A
   (normally the two are equal), so an item re-marked over an incorrect
   value is not reported again until ParishSoft changes. An item marked
   Entered by hand that a configurable number of full refreshes (default
   two) still finds at that value is flagged **Not seen in ParishSoft** on
   its stream's report, without changing its stage, so a missed entry is
   noticed. An **Updated in ParishSoft** item found back at A is the
   exception: the write was undone in ParishSoft, so it becomes a
   **Conflict** as in rule 3.
3. **R differs from both A and C**:
   - an item marked **Entered by hand** becomes
     [Entered incorrectly](#entered-incorrectly-stage); the entry reached
     ParishSoft wrongly;
   - an **Ignored** item stays Ignored: no change is wanted, so a new
     ParishSoft value does not reopen it;
   - any other open item becomes **Conflict** and returns to Review,
     keeping its decision visible but not in force, as an approved census
     change does today
     ([Automatic census review](../reports/spec.md#automatic-census-review)).
     The reviewer then decides against R, and R becomes the item's new A.
     (An Updated in ParishSoft item instead goes back to To review:
     [refresh checks](#refresh-checks).)
     An item already queued for update keeps its plan, and the plan meets
     the conflict at its own preflight check.
4. **Entity gone.** The Member or Family left the baseline (inactive,
   deceased, moved): the item keeps its stage and gains the shared
   [Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
   badge, as Ministry requests' roster rows do today.

For a Ministry request, R, A and C are the Member's roster state for that
Ministry (in or not in) as of the
[Ministry roster date](#ministry-roster-date), so rule 3 cannot apply: a ticked join is
Confirmed when the Member appears in the roster, and otherwise unchanged;
a join still To review or To enter by hand whose Member the roster already
lists says "ParishSoft already holds this value." (rule 1).

Every equality uses the stream's canonical comparison (the census compare
rules, money in its typed form, the roster state), never a raw string
compare, so a formatting difference ParishSoft introduces is neither a
conflict nor an incorrect entry.

### Refresh checks

How [#1689](https://github.com/epiphany40223/parishkit/issues/1689) builds
these rules. An item still open (a census change To review, Conflict, Ready,
To enter by hand or Ignored; a Ministry request To review) keeps today's
[refresh reconciliation](../data/spec.md#parishsoft-refresh-reconciliation),
which already stores a census change's ParishSoft value, with four
corrections: since [#1890](https://github.com/epiphany40223/parishkit/issues/1890)
it no longer resolves a census change ParishSoft already holds, nor a
Ministry request whose roster already shows it (rule 1: the item keeps its
stage, and its row says so from the stored value or the current roster);
a census change's C is the reviewer's value when it was
Edited; an Ignored census change ParishSoft now matches stays Ignored
(its row says "ParishSoft now matches the Family's answer"); and, from
[#1738](https://github.com/epiphany40223/parishkit/issues/1738), a census
change whose Member is no longer an active, living Member of its Family
keeps its stage (rule 4) instead of being cancelled (or, for a death date,
put in Conflict). That is safe because the update run refuses such a
Member: its preflight leaves the change unplanned and does not read the
Member for it, and the publish worker checks again before it reads or
writes
([review and publication](../data/spec.md#review-and-publication)). The
worklist's Who cell shows the shared
[ParishSoft status badge](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
for such a Member, whatever the row's stage. The Family's own resubmission
still withdraws the ordinary changes of a Member no longer on its form, as
any omitted answer is withdrawn. An item whose
row is terminal (a census change Entered by hand or Updated in ParishSoft) or
decided (a Ministry request a person accepted or ignored) gets a **refresh
check** instead (an accepted request gets a Confirmed check only once it
is ticked Entered by hand, #1890) (a census change a later Family response replaced does not:
it is history, below): an immutable record, written in the promotion transaction,
of the outcome (Confirmed, unchanged, Entered incorrectly, or ParishSoft now
matching an Ignored request), what ParishSoft held, and whether the refresh
was full. A new check is written only when the outcome changes (or when an
Entered incorrectly item's ParishSoft value changes again), and an unchanged
one on a marked item only on a full refresh, until Not seen is reached. The
stage is derived from the item's latest check, never stored on the item, and
a Confirmed item is not checked again, so its ParishSoft value is the one
that confirmed it. A Ministry request Confirmed this way offers only Back to
review ([undoing a terminal stage](#undoing-a-terminal-stage)), which
advances its version past the check and clears the tick; it is then To
enter by hand, saying ParishSoft already holds the value while its roster is
current, and the refresh confirms it again once it is ticked. Roster
changes to enter no longer counts a Confirmed request as not yet
entered.
Pledges get pledge refresh checks of their own
([Financial stream](#financial-stream),
[#1704](https://github.com/epiphany40223/parishkit/issues/1704),
[#1747](https://github.com/epiphany40223/parishkit/issues/1747)).

Nothing already stored is rewritten: a census change today's reconciliation
resolved upstream before #1689 (an Ignored one ParishSoft matched, or an
Edited one that matched the Family's answer) stays Confirmed, and an Edited
change already in Conflict at the reviewer's value stays there until
ParishSoft's value changes again. An Ignored change sent back to review is
To review again even when ParishSoft already holds the answer: it is
re-evaluated when ParishSoft's value next changes.

The value recorded at a census Entered by hand mark is the change's stored
ParishSoft value, which the mark freezes (for a mark made before #1689 too;
normally it equals A), except for a mark made again over Entered incorrectly
(below). Not seen in ParishSoft counts the unchanged full
refresh checks written after the mark and after the start that migration
0132 (provisional number) records once on a row of its own (with an
audit event when the database already holds Family responses); the
threshold (default two) is kept on that row, and deleting the row turns the
flag off. While some item has the flag, the Stage filter
has a Not seen in ParishSoft choice (Staff and Administrators), counted
beside the stages it is a flag of.

Issue #1738 also checks three census fields the Member contact read does not
carry, each against its own reading of the promoted data, with the
snapshot's own evidence (migration 0137, provisional number, widens the
check's guard for them):

- **Moved** (a Member leaving the household): ParishSoft holds it when the
  data no longer lists the Member as an active Member of the change's
  Family (not in the data, in another Family, or inactive). A Member the
  data marks deceased is no evidence of a move and gets no check.
- **Deceased**: ParishSoft holds it when the data lists the Member, still
  in the change's Family, as deceased. A Member no longer in that Family
  gives no evidence and gets no check.
- **Email opt-out**: ParishSoft holds it when the active Family's
  `sendNoMail` flag equals the answer ([below](#what-is-confirmable)). Its
  A is unavailable, so a different flag is unchanged (and Not seen after
  the threshold), never Entered incorrectly.

For moved and deceased, A is "no" and C is "yes", so, as for a Ministry
request, rule 3 cannot apply.

Entered incorrectly's two fixes
([#1737](https://github.com/epiphany40223/parishkit/issues/1737)) are the
row's Entered by hand icon and Back to review (its pressed Accept chosen
again, per [decision toggles](../ui-conventions/spec.md#decision-toggles); one by one or in bulk, each
change recorded as its own icon would record it), for Staff and
Administrators. Correcting ParishSoft to the reviewer's value without either
also works: the next refresh confirms it.

- **Entered by hand again** records a new Entered by hand mark (a resolution
  row, as the first mark is) and advances the change's version, so the
  checks made before it no longer place the change: it is Entered by hand,
  and Not seen in ParishSoft counts from the new mark. The value recorded at
  this mark is ParishSoft's value in the check that found the change Entered
  incorrectly (the latest check at the version the mark was made at). Rules 2
  and 3 compare the next refreshes with it: the change stays Entered by hand
  until ParishSoft changes, is Confirmed once ParishSoft holds the
  carried-forward value, and is Entered incorrectly again at yet another
  value.
- **Back to review** undoes the mark (a resolution row too), as on every
  terminal stage ([undoing a terminal stage](#undoing-a-terminal-stage);
  until #1840 it went to To review with no decision in force): it is To
  enter by hand (or Ready) again with its decision and edited value kept,
  open to today's reconciliation. A refresh before it is marked again finds
  ParishSoft differing from both the stored value and the reviewer's, so the
  change is a Conflict, decided again against ParishSoft's value; to Edit it
  to ParishSoft's value at once, choose its pressed Accept again (Back to
review) and Edit. The
  dialog promises no confirmation.

Entered by hand again is refused unless the change's latest refresh check,
at its current version, says Entered incorrectly. A final change the
Family's later response replaced (the same field answered again, which makes
a change of its own) is history: it gets no refresh check, is placed by its
execution alone, and takes neither fix. The census guard admits a change
leaving Entered by hand only with its paired resolution row. Migration 0133
(provisional number) adds these rules. It also checks every census refresh
check of a change Entered by hand against the value recorded at its mark:
unchanged exactly when ParishSoft holds that value, and Entered incorrectly
only when a value was recorded and ParishSoft holds another one.

[#1749](https://github.com/epiphany40223/parishkit/issues/1749) (migration
0138, provisional number) adds the rest:

- **An Updated in ParishSoft change found back at A, or at a third value,**
  is a Conflict (rule 2's exception and rule 3): the write was undone, or
  ParishSoft changed after it. A full refresh records a check saying so
  (with ParishSoft's value) and, in the same promotion, moves the change to
  Conflict at that value, as reconciliation moves an open change, and back
  to To review: its approval is decided again, as an approval resting on a
  stale accepted ParishSoft value is, so that neither a later refresh nor
  the Family's same answer can carry it back into force over the undo in
  ParishSoft (its row's Last decision still shows it). The census guard
  admits a change leaving Updated in ParishSoft only this way, from the
  source worker at fenced, pinned data, with that check; only a full
  refresh makes it (quick updates, which recorded nothing for such a
  change, were removed in #1147). The row says "ParishSoft no longer holds the
  update." under its
  stage until a review decides it again. A change a refresh already
  confirmed is not checked again, so a write undone after that is not
  caught.
- **An entered New Member** is confirmed through a staff association.
  Marking a New Member row Entered by hand one at a time asks for the new
  Member's ParishSoft Member ID (optional: the DUID ParishSoft gave the
  Member Staff added). The refresh confirms the change once the data lists
  that ID as an active, living Member of the change's Family; until then it
  is unchanged, and Not seen in ParishSoft counts as for any mark, so a
  wrong ID is noticed. It is never Entered incorrectly. The ID may not be
  one of the Members the Family's response listed, nor the ID another New
  Member of the Family is entered as. Without an ID (marked in bulk, or
  left blank) nothing confirms the mark.
  [#1773](https://github.com/epiphany40223/parishkit/issues/1773) (migration
  0139, provisional number) lets Staff and Administrators correct a
  mistyped ID, or give one the mark lacked: while the change is Entered by
  hand and not Confirmed (Not seen or not), its row offers the correction
  ([its icon and dialog](../reports/spec.md#manual-census-resolution)), one
  row at a time. It records a new Entered by
  hand mark (a resolution row at the change's version, as Entered by hand
  again does over Entered incorrectly) naming the new ID, which must differ
  from the one the current mark names and pass the same rules. The
  change's version advances, so the checks made before it no longer place
  it and Not seen counts from the new mark; the refresh then reads the new
  mark's ID. A blank ID is refused (the mark in force stays), and the
  correction is not offered in bulk, on a Confirmed change, or on a change
  a later response replaced. Making a wrong ID Entered incorrectly was
  rejected: a Member not yet listed reads the same as a wrong ID until
  ParishSoft is refreshed after the entry, so it would flag correct
  entries. Nothing
  guesses the association: matching by name and birth date inside the
  Family (a twin, a namesake or a typo would confirm the wrong person or
  none) and confirming on any new Member in the Family (no evidence it is
  this one) were rejected.
- **An entered home address** is confirmed against ParishSoft's primary
  address ([the smoke test's mapping](#family-addresses)): the street lines
  (ParishSoft's second and third lines joined), city, state and postal
  code, ignoring the country and a ZIP+4 suffix, each compared as the
  census address compare does. No read says which block is primary, so
  the refresh takes the primary fields as the home block, except when they
  hold the Family's own mailing answer and not its home answer, which shows
  the primary is the mailing block: then there is no check. A Family whose
  primary is the mailing block may hold any address there, so a different
  value is unchanged (Not seen after the threshold), never Entered
  incorrectly. The mailing address stays Entered by hand (nothing confirms it). The
  Family must be active in the data, as for the email opt-out.
- **The update run's queue step refuses a Member who left the Family** in
  the database too: the census guard lets the publish worker queue a change
  only while the current data lists its Member as an active, living Member
  of the change's Family, the check preflight and the worker already make
  in code (#1738).

### What is confirmable

A hand-entered item is confirmable when some ParishSoft read returns its
field:

- **Confirmable**: Member names, prefix, suffix, birth date, gender, marital
  status, email, phones, language, deceased status and date, a Member
  leaving the household, a new Member appearing (by the ParishSoft Member
  ID recorded at its mark, [above](#refresh-checks)), Ministry joins and
  leaves (the roster lists), the upcoming pledge amount, the Family's email
  opt-out (below), and the Family's home address, against ParishSoft's
  primary address ([address finding](#family-addresses)).
- **Not confirmable** (Entered by hand stays there unless noted): share
  methods, the Family's
  mailing address, a New Member marked without its ParishSoft Member ID
  (until one is given, [above](#refresh-checks)),
  the pledge frequency until its mapping is verified (below), and the
  non-ParishSoft streams.

The census email opt-out answer corresponds to the Family's ParishSoft
`sendNoMail` flag (Administrator, 2026-10-18, recorded on
[#1680](https://github.com/epiphany40223/parishkit/issues/1680): "good
enough"). ParishSoft's API cannot write it, so it stays To enter by hand,
but the Family search reads return it, so the refresh confirms an entered
opt-out against it. This changes only confirmation: the Family form's
baseline for the field stays unavailable until a slice that changes the
shared projection says otherwise.

### Ministry roster date

Staff enter a campaign's Ministry joins and leaves in ParishSoft by hand,
dated for the campaign's
[commitment period](../data/spec.md#commitment-period): for Stewardship
2027, a leave ends on December 31, 2026 and a join starts on January 1,
2027. So every check of a hand entry reads ParishSoft's Ministry
memberships as of the **Ministry roster date**: the later of the refresh's
own date and the commitment period start (the Administrator,
[#2293](https://github.com/epiphany40223/parishkit/issues/2293),
2026-10-27). A membership is held on that day when it has a start or an
end date, its start date (if any) is on or before that day, and its end
date (if any) is after it.

- **Before the period begins** the commitment period start is the later
  day. A join dated the commitment period start is held, and a leave ending
  the day before it is not, so a refresh made today confirms both, months
  before the period begins.
- **Once the period is under way** the refresh's own date is the later
  day, so a join or leave staff enter late, dated after the start, is still
  confirmed by the next refresh instead of reading Not seen in ParishSoft.

The refresh date is the day the refresh started, in the campaign's
time zone (the load's `as_of_date`, the day each roster row's current flag
is judged on). The promotion and the database guard compute the same day,
the guard in SQL (`stewardship_ministry_roster_date_v1`), so they agree on
every check.

The Ministry roster date applies to:

- the [refresh checks](#refresh-checks) of a decided Ministry request
  (Confirmed, Not seen in ParishSoft, and "ParishSoft now matches the
  Family's answer" for an Ignored one), both in the promotion and in the
  database guard that admits each check;
- "ParishSoft already holds this value." on a request still To review or
  To enter by hand (rule 1).

It does not apply to what the Family sees: the Family form's current
Ministries, and the database guard that checks a response's joins and
leaves against them, stay today's roster, as of the refresh date. The
Ministry reports and the leaders read from roles also keep today's roster.
A campaign without a commitment period (Financial stewardship off) reads
every roster as of the refresh date, as before. Nothing stored is
rewritten: the change only decides how later refreshes classify each
request. "ParishSoft already holds this value." on the worklist is read
when the page is drawn, so it reflects the Ministry roster date as soon as
the code is deployed; the stored refresh checks (Confirmed, Not seen in
ParishSoft) change only at the next refresh.

**Staff guidance.** Enter leaves ending the day before the commitment
period starts and joins starting on its first day. A membership dated
later in the period (a join that starts after the start, or a leave that
ends after it) is not what ParishSoft holds until that day: its request is
not confirmed by refreshes made before then, and becomes Not seen in
ParishSoft after the usual full refreshes, until a refresh made on or after
that day confirms it. Once the period is under way, date a join or leave
on or before the day you enter it.

## Pipeline vocabulary

These words are used, exactly, on every pipeline page, in exports, help
text, notices, audit display and the command line. A stream may not
introduce its own word for a shared stage.

| Term | Use | Replaces today |
| --- | --- | --- |
| Baseline | what ParishSoft (or, for new data, nothing) held when the Family answered | "ParishSoft now" stays as the column for the *current* baseline value |
| Family's answer | the submitted value | unchanged |
| Reviewer's value | the edited or added value carried forward | "Edited value" |
| To review | Submitted, no decision | "To review" (census), "New" / "In progress" (Ministry), "Needed, not completed" (Additional information) |
| Accepted / Accepted (edited) | Reviewed, carried forward | "Approved" (census), "Resolved: joined / leave confirmed" (Ministry) |
| Added by the parish | a reviewer-created item | new |
| Ignored | Reviewed, nothing carried forward | "Ignored" (census), "Declined" / "Duplicate" (Ministry) |
| Conflict | ParishSoft changed under the item to a third value | unchanged |
| Ready to update | accepted, the API can write it | "To do" for an approved Automatic row |
| Updating | in a running update | "Being published" |
| Updated in ParishSoft | written and read back | "Published" |
| To enter by hand | accepted, the API cannot write it | "To do" for a By hand row; "Not yet" on the retired Roster changes to enter |
| Entered by hand | a person recorded entering it | "Entered by hand", "Entered in ParishSoft: Yes" |
| Entered incorrectly | ParishSoft shows a different value after Entered by hand | new |
| Not seen in ParishSoft | Entered by hand, but ParishSoft still shows the old value after two refreshes (a flag, not a stage) | new |
| To handle / Handled | accepted data that never goes to ParishSoft | "Follow-up completed" |
| Confirmed | the baseline now shows it | "Already in ParishSoft" |

"Approve" and "publish" are retired as user-facing words: Accept is the
review decision and Update in ParishSoft is the write. This answers the
confusion the Administrator raised on Census changes, where *To do* today
means both "approved, waiting to be published" and "waiting to be typed in
by hand". The `pk-admin` commands are renamed outright with no aliases
(decision 7); the renaming slice updates every test and document that names
the old commands.

## Stream mapping

How each stream's stored state maps onto the stages, and the gap to close.
The child issues of the epic carry the detailed gap analysis.

### Census stream

Stored state: `ProposedChange` decision (`unreviewed`, `approved`,
`ignored`) × execution (`pending`, `conflict`, `queued`, `published`,
`resolved_upstream`, `resolved_external`, `failed`, `superseded`,
`cancelled`). The mapping onto stages is the
[existing state to stage](#existing-state-to-stage) table.

Gaps: *By hand* changes are never reviewed today (Staff go straight to
Entered in ParishSoft or Ignore), so Accept is new for them, and the
Administrator-only review of *Automatic* changes becomes a Staff review
(decision 1); Edit value exists only for Automatic changes; Add does not
exist; published and resolved_external are final today and are not later
confirmed by the baseline, so Entered incorrectly and Not seen in ParishSoft
are new and need the value recorded at the Entered mark. The page itself
mixes review and update in one table; the Census page layout issue
([#1682](https://github.com/epiphany40223/parishkit/issues/1682)) separates
them.

[#1683](https://github.com/epiphany40223/parishkit/issues/1683) closed the
review gaps: Census changes is the shared worklist, Staff and Administrators
Accept, Edit (By hand fields and Family addresses included), Ignore and send
back to review every change, Entered by hand on a To review By hand row
accepted and marked it in one step, and forward migration 0128 (provisional
number) widened the review guard for it
([Automatic census review](../reports/spec.md#automatic-census-review)).
[#1890](https://github.com/epiphany40223/parishkit/issues/1890) removed the one-step mark (decision 2, reversed): Entered by
hand is offered and admitted only on an accepted By hand change (To enter by
hand, and Entered incorrectly for Entered by hand again), never on a To
review or Conflict one, and forward migration 0159 (provisional number)
narrows the census resolution guard to match. Staff working through a list
Accept (in bulk if they like), then mark Entered by hand (in bulk too).
Changes already marked in one step keep their history and are shown
Accepted.
Census **Add** was cut from that slice (7th on the
[cut list](#risks-and-what-to-cut-first)) and built in
[#1710](https://github.com/epiphany40223/parishkit/issues/1710): a change
with no Family answer behind it is a row of its own tables (forward
migration 0129, provisional number), not a proposal, so the Family submit
path and its guards are unchanged, and it is To enter by hand until #1688
([Census Add](../reports/spec.md#census-add)).

### Ministry stream

Stored state: `MinistryRequest` workflow (`new`, `in_progress`,
`resolved`, `closed_no_response`, …) and outcome (`joined`,
`leave_confirmed`, `declined`, `no_response`, `duplicate`, `other`), then
the Entered in ParishSoft tick on
Ministry requests' Stage To enter by hand
([roster entry](../admin-portal/spec.md#roster-entry-on-ministry-requests)),
then a refresh check confirming a ticked request whose roster state is
current (until [#1890](https://github.com/epiphany40223/parishkit/issues/1890)
reconciliation also resolved an open request whose roster state was
current; it no longer does, rule 1).
The mapping onto stages is the
[existing state to stage](#existing-state-to-stage) table; a superseded or
cancelled request is History, which Ministry requests lists under its Stage
filter like the other streams' tables
([#2180](https://github.com/epiphany40223/parishkit/issues/2180),
[Ministry requests](../admin-portal/spec.md#follow-up-workflows)). The review
wizard does not offer History. A decided request is
not offered again in today's form; a Family that ticks it again is handled
by [rule 1](#resubmission-before-the-reviewed-form).

Bulk outcomes already exist (Mark as Joined or Declined, Left or Stayed, one
kind at a time) and so does a bulk Entered in ParishSoft tick. Gaps: no
Edit (the request's Ministry, Member and action are immutable, so a leader
cannot turn a mistaken Altar Servers join into an Adult Choir join; the
Edit's carried-forward Ministry is new, the Family's stays beside it); no
Add (a Staff-created request with no Family response behind it); Not seen
in ParishSoft for ticked requests; Ministry leaders never see the
Entered-in-ParishSoft step, which is right for the role. Ministry rosters
are read-only in both ParishSoft APIs, so this stream never has Ready to
update; contact attempts are a Ministry-only extra the pipeline keeps as
stream detail.

### Financial stream

Stored state (since
[#1686](https://github.com/epiphany40223/parishkit/issues/1686)): the
Family's live response, and `stewardship_pledge_review`, one immutable row
per decision on a Family's pledge (frozen migration 0127, provisional
number): who, when, the note, the response it was made on, that response's
pledge answer (annual pledge, frequency, cannot contribute and share
methods), the decision (Accepted, Accepted (edited) with the reviewer's
annual pledge, frequency and share methods, Ignored, or none after Back to
review of a decision; a Back to review of a terminal step keeps the
decision in force, [undoing a terminal stage](#undoing-a-terminal-stage))
and the Entered by hand mark. The
[Financial stewardship detail](../reports/spec.md#financial-stewardship-detail)
report still lists every current pledge, and
[Pledges](../reports/spec.md#pledges) is the stream's
worklist and manual report. Staff can still change a pledge through Open
form (a whole new response marked Entered by Staff), and Edit (amount,
frequency and share methods, decision 4) is the review's own correction.

**Share methods on an Edit**
([#2177](https://github.com/epiphany40223/parishkit/issues/2177), the
Administrator's option B, 2026-10-25). The pledge editor carries the
reviewer's share methods beside the amount and frequency, stored on the
same `stewardship_pledge_review` row (`reviewed_shares`, frozen migration
0181) next to the Family's answer the row records. They are kept only
when they differ from the Family's own share methods, and only for an
edited pledge above zero: an edited zero pledge has none, as the Family
form clears them for a zero pledge. A row with no reviewer's share
methods (every row before 0181, an Accept, Ignore, or an Edit that left
them alone) carries the Family's own. So the share methods in force are
the reviewer's while that Edit is in force, else the Family's, as for the
amount and frequency; every page and file that shows an Edit's Reviewer's
value shows them beside the Family's answer
([Pledges](../reports/spec.md#pledges), the
[Family hub's Financial stewardship tab](../admin-portal/spec.md#family-and-member-tabs),
the [Timeline](../reports/spec.md#timeline-review-lines) and the
[reviewed Family form](#what-the-reviewed-form-shows)). An Edit is still
one decision: no new response, no Staff resubmission and no email; the
same version check refuses a save over someone else's change or a newer
answer from the Family, and the same `pledge_review_updated` event
records it. The pledge goes where any Edit puts it (To enter by hand). An
Edit whose amount, frequency and share methods all equal the Family's
answer is recorded as the Accept it is. Entered by hand and Back to review
of a terminal step carry the share methods with the rest of the decision.
The share methods have no ParishSoft read, so they change no refresh
outcome: C is still the annual amount carried forward. Cannot contribute is
not edited here; Staff change it through Open form.
[Financial stewardship detail](../reports/spec.md#financial-stewardship-detail)
and its exports show the values in force, an Edit's amount, frequency and
share methods while it is in force and the Family's own answer otherwise,
marking each edited pledge
([values in force](../reports/spec.md#values-in-force-on-financial-stewardship),
[#2196](https://github.com/epiphany40223/parishkit/issues/2196)).

The item is one per Family: its current pledge answer. Its stage is derived
from its latest row and its latest refresh check (below), by one SQL
selection the page, the counts and the row guard share:

- no row, a latest row with no decision (Back to review), or a pledge answer
  changed since the latest row's response: **To review**, or **Conflict**
  when a refresh found so. A To review pledge ParishSoft already holds
  (found so by a refresh, or at the Family's submit: rules 3 and 4, below)
  stays To review and says so under its stage (the
  [page's](../reports/spec.md#pledges) words at $0.00 differ); before
  [#1890](https://github.com/epiphany40223/parishkit/issues/1890) it was
  Confirmed with no reviewer ("Already in ParishSoft");
- Ignored: **Ignored**;
- Accepted or Accepted (edited), not marked: **To enter by hand**, or
  **Conflict** (keeping the decision visible but not in force) when a
  refresh found so; one ParishSoft already holds stays To enter by hand and
  says so (#1890);
- marked Entered by hand: **Entered by hand**, or **Confirmed** or
  **Entered incorrectly** when a refresh found so.

"Changed" follows [rule 2 of the setting-off
rule](#resubmission-before-the-reviewed-form): a live response that answers
the financial questions after the decided one with a different pledge answer
reopens the item, even if a later response returns to the decided answer.
Pledge answers are compared as typed values: money exactly, and the share
methods as a set with their Other text, as the Family form stores them (a
form change that stored share methods differently would need the comparison
changed with it). An unchanged resubmission keeps the decision at any stage
(rule 1). A response without the financial questions changes no decision
(rule 5), but the item is listed only while the Family's current response
answers them, as on Financial stewardship detail: a Family that answers
again while the financial section is switched off leaves the list until it
answers the financial questions again, keeping its history. Each live
response records ParishSoft's upcoming-year pledge at its submit (below),
which places rules 3 and 4 differently from the general rule
([#1753](https://github.com/epiphany40223/parishkit/issues/1753)): a new or
changed pledge whose annual amount equals the amount ParishSoft held at
that submit has nothing to enter. Since [#1890](https://github.com/epiphany40223/parishkit/issues/1890) it stays **To review**
(rule 1: no refresh or submit confirms an item before its review and
entry), saying ParishSoft already holds it (the
[page's](../reports/spec.md#pledges) words, or its $0.00 words); after
Accept it is To enter by hand, Entered by hand (nothing to type), and then
Confirmed by the next refresh. From #1753 until #1890 it was **Confirmed**
at once with no reviewer. Either way that replaces rule 3's withdrawal,
because the pledge item is the Family's current answer and stays listed.
If any earlier version of the Family's
pledge was ever marked Entered by hand (even one later reopened, or a mark
undone with Back to review, since the amount may have been typed before the
undo), the new answer does not say ParishSoft holds it at the submit (its
row says the Family changed its pledge), so Staff see it and can undo the
entry, and the refresh judges it; rule 3's
"Withdrawn by the Family after it was entered" note is not shown. Without a
recorded amount the pledge is To review, as before. Pledges and funds are
read-only in ParishSoft's API, so the stream never has Ready to update.

**Pledge confirmation** (decision 5). ParishSoft's v2 `GET
offering/pledge/list` returns each pledge with `familyID`, `fundID`,
`pledgeDate`, `pledgeStartDate`, `currentPledgeAmount`, `initialPledgeAmount`,
`installmentAmount`, `numberOfMonths` and `fulfillmentMethod`, filterable by
Family, fund and date. The refresh already reads every pledge of the
campaign's configured funds and keeps those whose `pledgeStartDate` falls in
the **upcoming stewardship year** (the campaign's
[commitment period](../data/spec.md#commitment-period) and its pledge
funds, `fund_duids`) or in the comparison period (`source/giving.py` keeps the
Family, fund, `currentPledgeAmount` and date), so confirmation needs no new
read ([#1704](https://github.com/epiphany40223/parishkit/issues/1704)). It
maps a Family's ParishSoft pledge for the upcoming year to the pipeline item:

- R for the amount is the sum of the Family's `currentPledgeAmount` on the
  upcoming year's funds dated in that year, from the promoted snapshot, and
  only when that snapshot's giving read is proven complete for the
  campaign's configured window (the Family form's rule); without the proof
  no pledge is checked. No pledge reads as $0.00, the same as a zero
  pledge, so a Family pledging nothing is Confirmed by a ParishSoft holding
  no pledge for it. A for the Family's answer is ParishSoft's upcoming-year
  pledge when the Family answered: the Family submit (and Open form)
  records it on the response (`stewardship_submission.upcoming_pledge`,
  frozen migration 0136, provisional number), read the same way from the
  current snapshot the submit validates against, and only when that
  snapshot's giving read is proven complete; otherwise it records none and
  the submit goes on. A guard on the response pins a recorded amount to
  that snapshot's own sum; the proof itself is checked by the application,
  as for the mark. An unchanged resubmission keeps A (rule 1), so A
  is the amount recorded at the response where the Family's current pledge
  answer began; a response that recorded none reads as $0.00, as every
  response did before
  ([#1753](https://github.com/epiphany40223/parishkit/issues/1753)). A
  decision taken on a Conflict records that Conflict's R as the item's new
  A (`stewardship_pledge_review.baseline_pledge`, frozen migration 0135,
  provisional number, set by the guard), which Back to review and Entered
  by hand carry while the Family's pledge is unchanged; any other decision
  records the item's A. C is the annual
  amount carried forward (the Edit's, else the Family's). Money compares as
  exact two-decimal amounts. The
  [refresh rules](#baseline-refresh-re-evaluation) then confirm the
  pledge, flag Not seen in ParishSoft, flag Entered incorrectly (a typo at
  entry) or find a Conflict.
- The items are every pledge not yet Confirmed: a decided pledge whose
  decision is in force (To enter by hand, Entered by hand, Ignored) and a
  To review one. A decided pledge gets a **pledge refresh check**, written
  as a census change's [refresh check](#refresh-checks) is: an immutable
  row per refresh and item in `stewardship_pledge_refresh_check` (frozen
  migration 0134, provisional number), naming the decision row in force,
  with the outcome, R and whether the refresh was full. A check counts only
  while its decision row is the item's latest and in force, so any later
  decision, or the Family changing its pledge, leaves it behind. A To review
  pledge has no decision row to name, so its check, in
  `stewardship_pledge_open_check` (0135), names the Family's current
  response and the item's version instead, and counts while no later live
  response changes the pledge answer (an unchanged resubmission keeps it,
  as rule 1 keeps a decision) and no decision is recorded. Both guards
  admit an insert only from the promoting source worker, for a pledge in
  that stage, with R equal to the snapshot's own sum
  (`stewardship_pledge_upcoming_v1`) and an outcome that agrees with R, A
  and C. A Family no longer in the snapshot gets no check (rule 4). A new
  check is written only when the outcome changes, or when ParishSoft's
  amount moves under a Conflict or an Entered incorrectly pledge, and (for a
  marked pledge) once per full refresh until Not seen is reached.
- R = C makes a marked pledge **Confirmed**, keeping its Accepted or
  Accepted (edited), offering only Back to review; on an Ignored pledge it only
  notes "ParishSoft now matches the Family's answer". On a To review or To
  enter by hand pledge it changes no stage ([#1890](https://github.com/epiphany40223/parishkit/issues/1890)): the row says
  "ParishSoft already holds this value." (at $0.00, "Nothing to enter:
  ParishSoft holds no pledge."), and the check is still recorded, so it ends
  a Conflict and the next refresh that finds another amount takes the words
  away. A pledge is Confirmed on the amount alone (frequency below; share
  methods have no read). A marked pledge whose R differs from C and from the
  amount
  recorded at the mark is **Entered incorrectly**, shown with ParishSoft
  now beside the Reviewer's value, offering Back to review (Entered by hand
  again waits for
  [#1737](https://github.com/epiphany40223/parishkit/issues/1737)); one
  still at the mark after the configured number of full refreshes is
  flagged **Not seen in ParishSoft**.
- R differing from A and from C on a To review or To enter by hand pledge
  is a **Conflict**: it returns to review with ParishSoft now beside the
  Family's answer and any decision behind it shown as "Earlier: Accepted,
  not in force.", offering Accept, Edit and Ignore. A later refresh that
  finds R back at A, or at C, returns it to its stage (To review, or To
  enter by hand with its decision in force), at C saying ParishSoft already
  holds it (#1890; before, R = C Confirmed it).
- **Entered by hand** records ParishSoft's upcoming-year amount from the
  current snapshot when its giving read is proven complete (0134 lets the
  pledge review guard admit it, and checks it against the snapshot);
  otherwise the mark records none, and the refresh reads it as A.
- Frequency is compared only after a human-run, read-only smoke check on the
  Test Family records how ParishSoft stores a weekly, monthly, quarterly and
  annual pledge (`installmentAmount`, `numberOfMonths`,
  `fulfillmentMethod`); until then frequency is not confirmable and the
  item is Confirmed on the amount alone.
- Share methods have no ParishSoft read; Entered by hand stays there for them.

Ministry leaders never see this stream.

### Cannot participate stream

Stored state: the live response, and one immutable review row per
decision (`stewardship_limitation_review`,
[#1685](https://github.com/epiphany40223/parishkit/issues/1685)); the
[Limitations](../reports/spec.md#limitations) worklist lists Members who
cannot participate and Families who cannot attend. Baseline is empty,
nothing goes to ParishSoft. An item is one current answer (one Member's
cannot-participate answer, one Family's cannot-attend answer), read from
the Family's effective live response as the report always read it, so the
Family submit path stores nothing new. Its decision is the latest review row
for that answer while every later live response still gives the answer:
none, or Back to review → To review; Accepted → To handle; Ignored →
Ignored; Handled → Handled. Back to review of Handled records Accepted
again, with its required note, so the answer is To handle
([undoing a terminal stage](#undoing-a-terminal-stage)). A response that
answers the other way withdraws the item, and giving it again later is a new item, To review; one
that leaves the answer out changes nothing
([rule 1 to 5](#resubmission-before-the-reviewed-form)).

### Additional information stream

Stored state: `AdditionalInformationItem` disposition and the
`follow_up_needed` / `followed_up_at` workflow with Staff notes
([Follow-up records](../data/spec.md#follow-up-records)), and each Staff
revision's review decision (`accepted`, `ignored` or `none`, from schema
migration 0126,
[#1685](https://github.com/epiphany40223/parishkit/issues/1685)). Mapping:
followed up → Handled; follow-up needed → To handle; current actionable and
not needed → Ignored when the latest revision says so, else To review (the
[existing state](#existing-state-to-stage) rule for revisions saved before
0126); superseded / withdrawn → history. Back to review of Handled is a
revision that clears the follow-up (its time and who) and keeps it needed,
with its required note, so the request is To handle
([undoing a terminal stage](#undoing-a-terminal-stage)). The
[Additional information](../reports/spec.md#additional-information)
worklist expresses the two booleans as one stage, with bulk Accept,
Ignore, Back to review and Handled.

## Role views

Who sees which items does not change: it stays as each stream's spec
defines it, enforced in the database where it is today (Ministry leaders
see only the Ministries they lead in ParishSoft and never census, financial,
limitations or free-text data; Staff see every stream). What each role may
decide is in [role-specific actions](#role-specific-actions). The pipeline
changes what each role lands on:

- **Staff and Ministry leaders** sign in to **My review work**, which
  replaces Home for them (decision 8): one panel per stream they may review,
  each with its To review and Conflict counts and, for Staff, To enter by
  hand (or To handle), Entered incorrectly and Not seen in ParishSoft
  counts, each linking to that stream's review worklist filtered to the
  stage. Nothing else is above it. It is the Staff and leader form of Home
  ([#1673](https://github.com/epiphany40223/parishkit/issues/1673),
  [#1590](https://github.com/epiphany40223/parishkit/issues/1590)), and it
  replaces My Ministries for a leader. As built
  ([#1690](https://github.com/epiphany40223/parishkit/issues/1690)):
  - The panels are named and ordered exactly as the Review work menu
    names and orders their pages, from the menu's own definitions, so the
    two cannot drift (Administrator,
    [#2108](https://github.com/epiphany40223/parishkit/pull/2108)): today
    Census changes, Ministry requests, Pledges, Additional information
    and Limitations. Each is a grid of
    link cards (the UX conventions' Overview pages rule, from
    [#1673](https://github.com/epiphany40223/parishkit/issues/1673): the
    stage, its count, what is counted). A stream is shown only when the viewer's menu
    has its page, greyed with the menu's reason while the menu greys it.
  - A panel shows only the stages its stream can reach, each card with its
    count: Census changes To review, Conflict, To enter by hand, Entered
    incorrectly and Not seen in ParishSoft; Ministry requests To review,
    To enter by hand and Not seen in ParishSoft (rosters are never
    written, so a request has no Conflict and is never Entered
    incorrectly); Limitations To review and To handle; Pledges
    To review, Conflict, To enter by hand, Entered incorrectly and Not
    seen in ParishSoft; Additional information To review and To handle.
    Not seen in ParishSoft is the
    [refresh flag](#baseline-refresh-re-evaluation) on Entered by hand
    items, counted beside their stage as each worklist's Stage choice
    counts it, and its card is shown only while some item carries the
    flag, as that Stage choice is
    ([#2101](https://github.com/epiphany40223/parishkit/issues/2101)).
  - A leader's Ministry panel has one To review card per Ministry they
    lead, linking Ministry requests filtered to it (`?ministry=<DUID>`), as
    My Ministries did. Ministry To enter by hand and Not seen in ParishSoft
    are Staff's and open Ministry requests at that Stage, where Roster
    changes to enter was folded
    ([roster entry](../admin-portal/spec.md#roster-entry-on-ministry-requests)).
  - Each card's count is the count its stream's worklist shows for that
    Stage choice, on the same data
    ([#2101](https://github.com/epiphany40223/parishkit/issues/2101)): a
    card with a count opens a list with that many items, and one at 0 an
    empty list. A stage the menu's statement also reads (Census changes To
    review, Conflict, Ready to update and To enter by hand, Ministry
    requests To review and To enter by hand, Cannot participate To review
    and To handle, Additional information To review and To handle,
    Financial To review) takes
    the menu's own read: that statement places each of those streams'
    stored state by stage with the shared stage mapping, and the cards show
    each stage of the same read. The menu shows one number per list, its
    open stages' total (To review; Census changes To review and Conflict
    together), so that number always equals those cards'. The other counts
    (a Census change the parish added, Financial's other stages, and the
    refresh checks
    behind Entered incorrectly and Not seen in ParishSoft) are one more
    statement, run only for the streams the viewer's role may open, plus
    each stream's refresh-check read when it has an item Entered by hand;
    a leader's counts come from the follow-up selection, which derives
    the leader's scope in SQL. A greyed stream's cards show no number, as
    its menu entry does. The page's About text says each count is its
    list's, and what the menu's one number per list counts.
  - A count that cannot be read (a database error, a stored state the
    mapping does not place, the Limitations inputs unavailable) is logged
    and its cards show no number; the page never fails for it. The page's
    name is My review work in its heading, the menu, the trail's root and
    every "Return to" it, for those who land on it. Its menu entry is the
    first of the Review work group, not a Home entry above the groups
    ([admin portal, menu groups](../admin-portal/spec.md#menu-groups)).
  - Each Staff card links its stream's worklist at its Stage,
    `?stage=<value>` (Not seen in ParishSoft's value is `not_seen`), so
    the page opens on the rows counted (Administrator decision,
    2026-10-19,
    [#1818](https://github.com/epiphany40223/parishkit/issues/1818);
    Cannot participate since
    [#2101](https://github.com/epiphany40223/parishkit/issues/2101)). A
    leader's per-Ministry cards link `?ministry=<DUID>` instead. A stage
    link the page cannot use is refused in plain words, as every
    worklist link is.
  - The counts update themselves while the page is open, card by card
    ([concurrency](#concurrency), live while open, #2174).
- **Administrators** keep the full Home and menu, and also get the same
  panels, plus a Ready to update count per stream with its update link.
  As built (#1690), they are one **Review work** panel on Home, just before
  Next steps, with each stream a group in it; Ready to update is on Census
  changes only (the one stream ParishSoft's API writes) and opens
  [Update ParishSoft](../reports/spec.md#update-parishsoft).

## Review worklist

Every stream's review page is one shared component, the **review
worklist**: the shared [Admin table](../ui-conventions/spec.md#admin-tables)
with the stream's columns, under the stream's filters, with the shared
export below it. Its parts:

1. The page heading and help, then the filters, always open (no
   disclosure), on one compact row at laptop width
   ([worklist filters](../ui-conventions/spec.md#admin-tables), #1653),
   with **Stage** first (To review is the default) and the stream's own
   filters after it. Census changes opens instead on its first choice,
   **To review and Conflict**, which lists both stages together: the menu's
   badge and Home count both, as each needs a person, so the page opens on
   the number they show (the Administrator, 2026-10-31,
   [#2331](https://github.com/epiphany40223/parishkit/issues/2331)). Each
   stage keeps its own choice after it.
2. The selection bar and table. Columns: the stream's identity columns in
   the shared [column order](../ui-conventions/spec.md#table-column-order),
   each name with its DUID under it; the values (Baseline where a stream
   has one, then the Family's answer, the Reviewer's value where a stream
   has Edit, and ParishSoft now on Conflict, Entered incorrectly and Not
   seen rows); Stage; Submitted and Last decision (who, when, note);
   Actions. Values that belong together share a cell, and a narrow box
   shows each row as a card, as
   [review work tables](../ui-conventions/spec.md#review-work-tables) says.
   Rows are in the shared
   [default row order](../ui-conventions/spec.md#default-row-order) (by
   name) until the reader sorts a column, and keep it for ties after.
3. The shared export pane below the table, as on every report
   ([#1679](https://github.com/epiphany40223/parishkit/issues/1679)).

No summary tiles or charts sit above the table on a review worklist: the
counts are in the Stage filter's choices and on My review work. A stream's
statistics stay on its report page. An **Add** button sits in the selection
bar for readers allowed to add on that stream, and opens the stream's own
value editor (for a Ministry, a Member and a Ministry picker scoped to the
reader). **Review these one at a time** sits beside it and opens the
[review wizard](#review-wizard) on the table's view
([wizard entry points](#wizard-entry-points)).

### Individual actions

Each row draws its actions as
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) icons
that are [decision toggles](../ui-conventions/spec.md#decision-toggles): the same icons at every stage (hidden,
not disabled, by [role](#role-specific-actions)), each offered only while
its stage allows it and otherwise unavailable with its reason:
Accept (check), Edit (pencil; opens the stream's own value editor: for a
pledge the amount and frequency, for a Ministry request the Ministry list
scoped to the reader), Ignore (eye struck through), and, in the update
phase, Entered by hand / Handled (check in a box), on accepted rows only
(never To review or Conflict, #1890). Back to review has no icon of its
own: it is the pressed icon of the latest state in force, chosen again
(an item confirmed with no decision behind it, such as a change
ParishSoft already held, draws Accept pressed as "Already in
ParishSoft"). A census addition's Back to review is
worded **Not entered yet**, and it offers **Withdraw** (a circled minus)
in both states (#2041,
[role-specific actions](#role-specific-actions)). A row
whose value ParishSoft already holds before its step says "ParishSoft
already holds this value." on the line under its stage
([rule 1](#baseline-refresh-re-evaluation)). The line appears only on a row
that has something to say, not reserved (an Administrator exemption from
[no layout shift](../ui-conventions/spec.md#no-layout-shift)). A census New
Member Entered by hand also offers Correct (or Add) the ParishSoft Member ID
(pencil, after its selected mark;
[refresh checks](#refresh-checks)). Each takes the optional note in the
shared confirmation dialog; Back to review requires it only on a
[terminal stage](#undoing-a-terminal-stage), whose dialog says where the row
goes (on an Ignored row the note stays optional, #1889). The Family hub
draws the same icons and dialogs on its changed rows, posting to the
stream's page (#2064,
[hub row actions](../admin-portal/spec.md#hub-row-actions)).

### Selected-state icons

A row's decision is shown on its action icons, not only in the Stage
column: Accept, Ignore and Entered by hand / Handled follow the
[selected state](../ui-conventions/spec.md#row-actions-and-confirmation) rule, and choosing the selected icon again is the row's undo, Back to review
([decision toggles](../ui-conventions/spec.md#decision-toggles), #2131).
Ministry requests uses it too.

### Bulk actions

Selection boxes and the bulk bar follow the
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) rules (several rows, by role); today Census changes
breaks the box rule, giving a box only to rows offering a review decision,
so the By hand rows have actions but no box. The bulk bar offers Accept,
Ignore, Back to review and Entered by hand / Handled, and on Census changes
Withdraw while the page lists a change the parish added (#2041)
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)), with
the exact count in the dialog. Edit, Add and a New Member's Member ID
correction are one item at a time. The
typical flow the Administrator described is a handful of Edits, then select
all on the page and Accept; pledges work the same way.

### Concurrency

Every decision is checked against the item version the row showed, as
census review and the Ministry follow-up forms already are; a row someone
else (another reviewer, or the Family resubmitting) changed first is left as
it is and the notice says so. Bulk decisions are all or none. A pledge's
version also advances with every refresh check of it
([Financial stream](#financial-stream)), so a decision is never applied over
a Confirmed or Conflict the reader was not shown.

**Live while open** (Administrator, 2026-10-25,
[#2174](https://github.com/epiphany40223/parishkit/issues/2174)). Two
reviewers, such as two Ministry leaders of one Ministry, or a leader and
Staff, can have the same item open at once. The version check keeps the data
safe, but each open page also shows a decision made elsewhere without a
reload, under the general rule
([pages that update themselves](../ui-conventions/spec.md#pages-that-update-themselves),
its items others change). Each of these is a **review region**: every review
worklist's table (Census changes, Ministry requests, Pledges, Additional
information, Limitations), each Family hub tab's changed rows
([hub row actions](../admin-portal/spec.md#hub-row-actions)) and the Review
work counts on Home and My review work ([role views](#role-views)).

- **One read.** About every 60 seconds, and at once when the tab is shown
  again or the window regains focus, a region sends the items it drew in
  one passive GET of its page's own address (`live=`): each item as a short
  key of its id and a token of what its row shows (its identity cells,
  such as the Family's or Member's name, its version, stage and the
  stream's line under it, values, Submitted time, last decision and offered
  actions; a card's count and link). An identity cell's link is not
  compared: an item page's link carries the page's remembered view
  (Ministry requests' `queue`), which a check may hold differently while
  the cell shows the same name. A
  worklist's read takes those items at every stage (History too, where the
  stream lists one), within the reader's scope, through the stream page's own reader and row
  decoration, not through the page's filters: an item that moved to another
  stage is still found, so its row updates instead of going. A hub tab's
  read is its tab's own address (the Family's items, every stage but
  History); the counts' read is My review work's own counts, which Home's
  panel shows too. Each is one read of its stream, as the page's own, so a
  region costs no more per check than a view, and never one query per item.
  Ministry requests reads its remembered view's filters and Members choice,
  so its rows are decorated as the page decorated them. A region of more
  than 300 items (All on a long list) sends 300 per check, in turn, so every
  item is checked.
- **The answer.** The server answers with the items whose token changed,
  drawn exactly as the page draws them, or with nothing when none did. The
  page redraws each changed item in place: its row (stage chip, values, Last
  decision and icons, enabled or not as its new stage allows) and its own
  Edit and Entered by hand dialogs, whose forms keep the view the page
  shows. A worklist's check sends its table's optional columns (ParishSoft
  now, the selection boxes), and its answer draws them as the page's table
  does whatever the reader may now decide (a campaign held since the page
  was drawn offers no bulk action, so a redrawn row's selection cell is
  empty), so a redrawn row has the cells its table has. A row whose answer
  still has other cells is a misfit. Only an answer that is the whole
  region, a hub tab's own page, redraws a misfit's region whole (when a hub
  tab's columns themselves changed: a Reviewer's value column an Edit
  brings), its open disclosures kept open. A worklist's answer holds only
  its changed rows, so it never replaces the table (that would drop the
  other rows, their sort and the pager): a misfit row keeps its cells, takes
  the new token and shows its tag, and is redrawn at the reader's own next
  redraw. The row carries a short **Changed by someone else** tag under its
  stage until the reader's own next action redraws the table, but only when
  what it shows changed (its identity, values, stage or last decision): a
  row whose offered actions alone changed, such as when the campaign was
  held, is redrawn without one. A card's count changes in place, with no
  tag.
- **Nothing moves.** Rows never move, re-sort, appear or disappear: an item
  no longer at the filtered stage stays where it is with its new stage, and
  an item new since the page was drawn (a Family's later answer, someone
  else's Add) is listed at the reader's own next redraw, as is an item that
  could no longer be found. No card appears or goes. The region waits while
  the reader is busy as the general rule says and, besides, while focus is
  in it or the pointer is over it, so nothing changes under the reader's
  hand; it checks again 5 seconds later.
- **Audit and sign-in.** A check never extends the sign-in: every stream
  view, the hub and the counts read a request carrying `live=` passively,
  whatever its headers say. One that finds nothing changed records nothing.
  One that brings changed items in records the page's own audited view (its
  count the items brought in) marked automatic, as a report's re-read for
  newer figures does. A check that fails records the view's FAILED
  completion, marked automatic too (the counts, whose dashboard view has
  no completion, record nothing for one). Ministry requests and Additional
  information, whose views record a start and a completion, record only the
  completion then, and the counts record the dashboard view.
- **Failures.** As every periodic re-read: a failed check is tried again at
  the region's pace, after three in a row the region says it could not
  check, and a refusal stops the checks and says so.

## Review wizard

Status: **approved; slices 1 to 4 built**
([#2123](https://github.com/epiphany40223/parishkit/issues/2123)); the
flow, behaviour and shared-code requirement below, and the page scope,
waiting set, start page, Limitations pages, navigation rows, keyboard,
menu and dashboard entries, are the Administrator's decisions of
2026-10-24, and the spec was approved on 2026-10-24 with three changes:
Finish opens My review work, runs have no time limit, and a reader may
have several runs (one per tab). The Administrator's pk-local findings of
2026-10-25 (R52 Part W) then removed unfinished runs from the interface:
a run is never listed, offered or resumed, and starting one replaces the
reader's earlier open runs
([wizard run lifetime](#wizard-run-lifetime)). Its first three slices are built: the
run storage, the start page, the page frame with its navigation, the menu
entry and the dashboard card (#2123, slice 1 of the
[delivery plan](#wizard-delivery-plan)), the shared section component and
the Family pages (slice 2), then the Member pages (slice 3), and the
tables' entry buttons (slice 4).

The review wizard is a third view of the same review data, beside each
stream's [review worklist](#review-worklist) (bulk decisions over a table)
and the Family hub's [row actions](../admin-portal/spec.md#hub-row-actions)
(one Family, every stream). It shows one subject (a Family or a Member) per
page, for one stream, with Previous and Next, so a reviewer reads each
changed value at full width and acts on it, or not, before moving on. It
replaces neither view, and it adds no review logic of its own: the
filters, the rows, the section each page draws and every action are the
code the other two views already run
([wizard shared component](#wizard-shared-component)).

### Wizard flow

1. **Choose the stream.** Census changes, Ministry requests, Pledges,
   Limitations or Additional information, in that order (the wizard's own,
   `review_streams.STREAMS`; R52 Part W2 moved Additional information
   last; the Review work menu and tables keep the menu's order), offering
   only the streams the reader may open
   ([wizard streams and access](#wizard-streams-and-access)). With exactly
   one (a Ministry leader: Ministry requests) the choice is not shown.
2. **Filter and sort.** Default: everything waiting, sorted by name
   ([wizard filters and sort](#wizard-filters-and-sort)).
3. **Pages.** **Start** fixes the run's sequence and opens page 1. Each page
   shows the items of one subject that matched the filters, with the
   actions the Family hub offers on them, and no tabs
   ([wizard pages](#wizard-pages)). Previous and Next never need an action,
   and the run can be finished without acting on anything
   ([wizard navigation](#wizard-navigation)).

Steps 1 and 2 are two parts of one start page, **Review one at a time**:
the stream is a radio group, and choosing a stream redraws the filter part
below it in place with that stream's filters
([in-place controls](../ui-conventions/spec.md#in-place-controls),
[conditional fields](../ui-conventions/spec.md#conditional-fields)). The
page opens with the reader's last stream chosen, else the first offered.
The page heading is **Review one at a time**; its help says, in one
paragraph, that the pages show one Family or Member at a time, that acting
is optional, and that the Review work tables remain for bulk decisions.

A started run is one stream, its filters and sort, the fixed sequence of
subjects and the reader's position in it
([wizard run storage](#wizard-run-storage)). Starting a run deletes the
reader's earlier open runs in the campaign, and an
unfinished run is never offered again
([wizard run lifetime](#wizard-run-lifetime)).

### Wizard streams and access

The wizard reuses each stream page's own access, so a reader can review a
stream one at a time exactly when its Review work page is in their menu:

| Stream | Its page (menu entry) | Who | Subject per page |
| --- | --- | --- | --- |
| Census changes | `census_changes` (`VIEW_CENSUS`, census on) | Administrators, Staff | the Family, then each changed Member |
| Ministry requests | `ministry_report` (`MINISTRY_REPORT`, scoped) | Administrators, Staff, Ministry leaders (their Ministries only) | one Member |
| Pledges | `pledges_to_enter` (`FINANCIAL_DETAIL`) | Administrators, Staff | one Family |
| Limitations | `limitations_report` (`CAMPAIGN_REPORT`, Ministry stewardship on) | Administrators, Staff | the Family (only with a Family answer: the financial one, its Pledges page, or Cannot attend Mass), then each Member with a Limitations answer |
| Additional information | `information_queue` (`ADDITIONAL_FOLLOWUP`) | Administrators, Staff | one Family |

- A stream is offered while its menu entry is available
  (`admin_navigation`'s `Entry` for that page, campaign condition
  included); a greyed entry greys its choice with the menu's reason
  ([unavailable menu pages](../ui-conventions/spec.md#unavailable-menu-pages)).
- Every page load and every action re-checks the stream page's own
  principal check (`_principal` of the stream's view), so a role change
  mid-run takes effect on the next page; a reader who lost a stream's
  access gets that page's ordinary
  [denial](../ui-conventions/spec.md#pages-the-role-may-not-open).
- A Ministry leader's pages read through the leader-scoped follow-up
  selection (`ministry_worklist.read_rows`), so a page never names a
  request for a Ministry they do not lead. A Member's name on a leader's
  page is plain text, as Ministry requests draws it; for Staff and
  Administrators every Family name opens its Timeline (`family_link`).
- Each Limitations answer is on the page it belongs to. **Cannot
  contribute financially** is a Family answer, so it is on the Family
  page, which is that Family's Pledges page (the Pledges section, which
  lists the answer as the Pledges table does); a Limitations run's Family
  page also re-checks the Pledges access (`FINANCIAL_DETAIL`), and a
  reader without it never sees the financial answer there. **Cannot attend Mass or
  prayer services** is a Family answer too (the Limitations table's
  Families table), so it is on the Family page as well, for every reader
  of Limitations (#2123 slice 1; it has no Member to go to). Every other
  Limitations answer is per Member and is on that Member's page. While
  Limitations is offered, a Pledges run leaves a Cannot contribute
  financially Family out
  ([wizard filters and sort](#wizard-filters-and-sort)), so that answer
  is reviewed in Limitations only.
- While other campaign work holds the campaign, pages draw no action icons,
  with the stream pages' reason, as the hub does (`hub_actions.mutable`).

### Wizard filters and sort

The filter part has a **Basic** and an **Advanced** mode, the
[additive editor](../ui-conventions/spec.md#basic-and-advanced-modes) form
of that rule (the mode is not stored; the page opens in Basic, or in
Advanced when it is drawn with an Advanced filter set):

- **Basic:** only work still waiting, plus one **Search** box (the name
  search the stream's table has). "Waiting" is every stage at which a
  person still has something to do on the item, among the stages the
  stream reaches: To review, Conflict, To enter by hand, To handle and
  Entered incorrectly (a Ministry leader: To review; Administrator
  decision, 2026-10-24). Ready to update and
  Updating are not waiting on a reviewer: Update ParishSoft owns them.
  Under **Search name** a short help line, per stream, says what the
  stream's table search matches and that a blank search means every
  waiting item (R52 Part W):
  - Census changes: "Family name, a Member's name, or the Family DUID.
    Leave blank for every waiting change."
  - Ministry requests: "Member's name or the Ministry. Leave blank for
    every waiting request."
  - Pledges: "Family name or the Family DUID. Leave blank for every
    waiting pledge." While Limitations is offered, a second line under
    it says "Families who answered Cannot contribute are in
    Limitations." (R52 Part W2; see Pledges below). Only Basic shows
    that line, as the Administrator asked; Advanced leaves the same
    Families out.
  - Limitations: "Family name, a Member's name, or the Family DUID. Leave
    blank for every waiting answer."
  - Additional information: "Family name, the Family DUID, or words in
    the information or notes. Leave blank for every waiting item."
- **Advanced:** every filter the stream's Review work table has, drawn by
  that table's own filter template and parsed by its own query class: for
  Ministry requests the Stage, Search Member or Ministry name, Ministry,
  Request and Members (Staff); for Census changes the Stage, Search, How
  it reaches ParishSoft and Kind; for Pledges the Stage and Search; for
  Limitations the Stage, Search and Show; for Additional information the
  Stage and Search. The Stage filter adds one choice ahead of the table's,
  **Waiting** (Basic's set), which is its default here, and leaves out the
  table's **History**: the wizard never offers History, its last choice
  reads **Every stage except History**, a start posted at History is
  refused as malformed, and a History item is never a wizard page (R52
  Part W; the Administrator, 2026-10-25; the tables' own Stage filters
  keep History). On the tables the
  Stage is drawn by the shared worklist, not by the filter template, so
  the start page draws its own Stage select from the same stage lists the
  tables use (`review_streams`), and the tables do not offer Waiting.
  Waiting is
  applied after the table's own read, which is run at Every stage, by the
  same placements the Stage filter uses.
- **Sort:** **Last name (A–Z)** (the default; R52 Part W renamed it from
  Name A–Z, since the shown names are "*Last*, *First*"), **Oldest
  submitted first**, **Newest submitted first**. Last name (A–Z) is the
  deterministic name order of
  [#2124](https://github.com/epiphany40223/parishkit/issues/2124) applied
  to subjects: the shown name casefolded (the Family's for a Family
  subject and for grouped streams, the Member's for Ministry requests),
  then the secondary name, then the DUID, then the subject's campaign
  record id. A subject's submitted time is the newest submitted time among
  its matched items; equal times fall back to Last name (A–Z).

- **Pledges leave out Cannot contribute:** while the reader is offered
  Limitations (it needs Ministry stewardship), in Basic and Advanced
  alike, a Pledges run implicitly leaves out the pledge rows whose Family answered
  **Cannot contribute financially** (the rows `pledge_rows` marks
  `cannot_give`), with no filter to bring them back: that answer is
  reviewed in the Limitations run, which keeps including it on the
  Family's page ([wizard streams and access](#wizard-streams-and-access)).
  Only the Pledges wizard sequence leaves them out; the Pledges table
  keeps listing them, and its **Review these one at a time** button
  starts a run without them (when it lists only those, the start page
  says "Nothing matches these filters." and starts nothing). Without
  Limitations nothing else offers those Families one at a time, so a
  Pledges run keeps them (R52 Part W2; the Administrator, 2026-10-25).

Nothing a reader types reaches an address: the search travels only in the
POST that starts the run, as on the tables
([Admin tables](../ui-conventions/spec.md#admin-tables)).

### Wizard sequence

**Start** reads the stream's rows exactly as its Review work table does,
with the run's filters, and turns them into the run's sequence of
subjects:

1. The stream's worklist read and Stage selection give the matching items,
   each with its placement (the functions are listed under
   [wizard shared component](#wizard-shared-component)).
2. Each item maps to its subject key, `(family_id, entity_kind,
   entity_key)`: the Family's campaign record id (the hub's address key)
   and, for a Member subject, the Member's entity as its change rows name
   it (`("member", DUID)` or `("proposed_member", local id)`,
   `hub_census.Subject.entity`); a Family subject's entity is
   `("family", "")`.
3. Subjects are ordered by the run's sort. In the grouped streams (Census
   changes, Limitations) the sort orders Families, and each Family's pages
   are adjacent: its Family page first, when the Family itself has a
   matching item (a household field in Census changes; in Limitations,
   the Cannot contribute financially or Cannot attend Mass answer), then
   a page for each Member with a matching item: ParishSoft Members by
   DUID, then the New Members the Family added, by name, as the Family
   hub's Member tabs list them (`hub_census.shape`; a Member the hub
   lists after the others because only earlier requests keep them sorts
   here by DUID with the rest). A Family with only Member items has no
   Family page. A New Member the Family added is a Member page in that
   order. In Limitations the Family's financial answer is read from the
   Pledges stream's read with the run's Stage and Search, and every other
   answer from Limitations' own read, so each answer is in the sequence
   once, on its own page.
4. The sequence, and for each subject the ids of the items that matched,
   are stored with the run and never change afterwards
   ([wizard run storage](#wizard-run-storage)). An item acted on stays in
   place with its new stage, so Previous always returns to what was just
   seen. A subject whose items later move out of the filters (another
   reviewer accepted them, the Family withdrew them) keeps its page.

A run holds at most 5,000 pages. A larger selection is refused on the
start page in plain words, "This selection has more than 5,000 pages. Narrow
the filters, then start again.", with a stable error code, and nothing is
stored. A selection with no items says "Nothing matches these filters."
and starts nothing.

### Wizard pages

Each page has, top to bottom:

- **Header:** one consistent header on every page (R52 Part W). Its
  first line, the page heading, is "Family: *Family name* (DUID *n*,
  envelope *e*)", the Family's name opening the Family hub (for Staff and
  Administrators, `family_link`), and ", envelope *e*" only when the
  Family's ParishSoft envelope number is set and not 0. On a Member page
  only, a second line reads "Member: *Member name* (DUID *n*)", the
  Member named as the hub's tab names them, the name opening the hub's
  tab for that Member (for Staff and Administrators; plain text for a
  Ministry leader); a New Member the Family added has no DUID yet, so the
  parenthetical is left out and the hub's New Member badge follows the
  name. The second line's place is kept, empty, on a Family page, so the
  navigation row below sits at the same spot on every page. The browser title is
  "Review one at a time: *stream*, *progress*", without the name, as the
  Timeline's title leaves it out.
  The last breadcrumb says the same, "*stream*, *progress*", so the trail
  does not repeat its parent crumb, Review one at a time; a run that is
  no longer open reads "Closed review".
- **The navigation row, first copy**, with the progress line
  ([wizard navigation](#wizard-navigation)), under the heading and above
  the section. The progress is one line in the
  [stepper](../ui-conventions/spec.md#step-bars)'s place: "Family 12 of
  87" for a Family stream (Pledges, Additional information), "Member 12 of
  87" for Ministry requests, and for a grouped stream the Family's place
  only, "Family 12 of 87", on the Family page and on each of its Member
  pages (R52 Part W dropped a ", Member 2 of 3" suffix, which could read
  as the Family's number of Members). A
  slim bar under it shows the run's progress by page, with no labels, so it
  never wraps.
- **The section:** the subject's matched items of the run's stream, drawn by the
  shared section component the hub's tab draws
  ([wizard shared component](#wizard-shared-component)): the Changed
  table (Field, Baseline, Family's answer, Reviewer's value while any row
  has one, Stage, Actions while any row offers one), the census Show
  unchanged disclosure, and each row's icons and dialogs, with no tab
  frame. The section shows only the items that matched the filters when
  the run started (the ids stored with the subject, so the search needs no
  storing), each at its live stage, with the stage chips showing what
  still waits. An item a resubmission replaced is shown by its
  replacement. Under the section one quiet line names what the filters
  leave out: "*n* more in this stream for this Family are filtered out." (or
  "…for this Member…"), the subject's other items in the stream at every
  stage but History, and no line when there are none. The line is plain
  text, with no link and no control, so it never moves anything; the full
  subject is on the Family's Timeline.
- **The navigation row, second copy**, at the bottom. The page has no
  footer links (R52 Part W; the Administrator, 2026-10-25): the stream
  page is in the menu, and the header's Family name opens the Family hub.

A page whose subject has nothing left in the stream for this reader (the
Family withdrew every change, a Member left the Family, a Family left the
campaign, or the reader lost access to the subject's items, such as a
Ministry leader who no longer leads the request's Ministry) names nothing:
its heading reads "No longer listed", the Member line is left empty,
and the section says "This Family is no longer listed
for this stream." or "This Member is no longer listed for this stream.".
It keeps Previous and Next. The stored ids are never shown, so a run
started earlier never shows a name the reader may no longer see (#2123
slice 2). No run reaches such a page by its filters alone: the wizard
offers no History stage ([wizard filters and sort](#wizard-filters-and-sort)),
which closes the earlier open point about runs started at History.

### Wizard navigation

- **The rows.** **Previous**, a centered **Finish**, and **Next**, in the
  three fixed slots of the
  [stepper button row](../ui-conventions/spec.md#stepper-button-row), at
  **both the top and the bottom** of the page: the same controls twice,
  each row carrying the progress ("Family 12 of 87", with the slim bar).
  As in the Family form's row, Previous is always on the left, Finish
  always in the middle and Next always on the right: page 1 keeps
  Previous's slot empty, and the last page keeps Next's slot empty while
  Finish stays in the middle (R52 Part W; the Administrator, 2026-10-25),
  where it is then the primary button. The top row sits directly
  under the one-line heading, above the section, so nothing above it
  changes height; the bottom row is pinned to the bottom of the screen as
  the Family form's is (#2063). Neither row moves as the content changes:
  Previous and Next sit at the same screen spots on every page, whatever
  the section's height and whatever an in-place redraw adds, so a reader
  clicking Next repeatedly never has the button move under the pointer
  ([no layout shift](../ui-conventions/spec.md#no-layout-shift)). The
  progress text is fixed-width in its slot and the same in both rows.
- **Decision buttons.** The section's decision buttons follow the
  portal-wide toggle pattern of
  [#2131](https://github.com/epiphany40223/parishkit/issues/2131): a
  chosen decision is pressed, and clicking it again undoes it, so the
  wizard's pages have no separate Back to review buttons.
- **Acting is optional.** Previous and Next never ask, save or discard: a
  decision is recorded only by its own dialog, at once, as on the hub. A
  dialog that is open blocks the row as every modal does.
- **How they move.** Previous and Next are small POST forms (CSRF, the
  run's id and the target position): the server records the new position
  in its own short work transaction and answers 303 to the page's address,
  `reports/review/<position>/?run=<run id>`, so the browser's Back and a
  reload show a page, the position survives a sign-out, and a GET never
  writes. A page reached by Previous or Next puts focus on the control that
  was used (in the top row or the bottom row), without scrolling the
  page, so pressing Enter again moves on and both rows stay at their
  screen spots.
- **Finish** marks the run finished and opens **My review work**, whose
  counts reflect what was done in the run. Leaving by the menu or a link
  leaves the run as it was, but nothing offers it again
  ([wizard run lifetime](#wizard-run-lifetime)).
- **Keyboard.** The buttons are ordinary buttons in reading order. There
  are no keyboard shortcuts (Administrator decision, 2026-10-24).

### Wizard run lifetime

Unfinished runs are not resumed (R52 Part W; the Administrator,
2026-10-25: staff and leaders start the wizard again). Nothing lists or
offers a reader's unfinished runs: the start page has no list of them,
the review tables' button never asks about them, and the dashboard pane
always opens the start page. The run row stays, because Previous and Next
move through its stored sequence and position
([wizard run storage](#wizard-run-storage)), so a page's address, the
browser's Back and a reload keep working while the run is open.

**Start** (on the start page or from a table) deletes the reader's
earlier unfinished runs in the campaign in the same transaction as the
new run's insert (two starts at the same moment may each leave a run, which
nothing lists and the next start deletes); a
refused start (nothing matches, or too many pages) leaves them as they
are. A finished run keeps its row and is never offered again.

When no stream is offered to the reader, the start page answers 403. A
Ministry leader who leads no Ministry gets the plain sentence the
Ministry pages give them ([Ministry leaders from
ParishSoft](../admin-portal/spec.md#ministry-leaders-from-parishsoft),
`reports.ministries.unscoped`, #2165), not the generic sign-in denial;
the page reuses that code, so the wording stays in one place. Any other
reader keeps the generic denial, and a signed-out reader goes to
sign-in.

A page address carries its run's id. A run that was finished, or
replaced by a newer start (in this or another tab), says "This review is
no longer open." with a link to the start page, **Start a review**; it
is never an error page.

### Wizard run storage

One row per run, `stewardship_review_run`; a start deletes the reader's
earlier unfinished runs in the campaign, and finished ones stay:

- `id` (uuid), `user_id` (the portal user, deleted with it),
  `campaign_id` (deleted with it), `stream`, `filters` (jsonb: the
  stream's closed filter values and the Basic or Advanced mode; the
  search text is never stored, only whether one was used), `sort`, `subjects` (jsonb array of
  subject keys, each with the ids of its matched items, at most 5,000
  subjects), `position`, `finished_at`, `created_at`,
  `updated_at`; indexed on `(user_id, campaign_id)`, not unique.
- The keys and item ids are record ids and DUIDs only, never names or
  values, so the row holds nothing the stream tables do not already hold.
- **Retention:** an unfinished run stays, with no time limit, until it is
  finished or its reader starts another run, which deletes its row
  ([wizard run lifetime](#wizard-run-lifetime)). A finished run keeps its row
  (it is not offered again) until the user or campaign is removed, which
  deletes it by the model's cascade (`on_delete`; the foreign keys are
  Django's own, which the model contract compares with the installed
  schema, so the database itself does not cascade, and nothing deletes a
  portal user or campaign row in SQL). The rows are bounded by the runs
  readers start, each at most 5,000 subjects of ids.
- **Migration: yes**, frozen file **0178** (`0178_review_run.sql`,
  assigned centrally), whose guard keeps a started
  run's user, campaign, stream, filters, sort and sequence fixed, its
  position inside the sequence, and a finished run unchanged. A run's
  position must outlive the sign-in, so the Django session (`report_paging.remember_queue`'s store) cannot hold it. The
  table is one new forward migration with its frozen SQL file under the
  [post-launch schema policy](../operations/spec.md#post-launch-schema-policy),
  with the web role's grants, the schema inventory and baseline entries,
  and the restore and purge coverage the schema audit requires.
  It writes nothing to any existing table.
- **Audit:** no new audit action. A page's read is audited as its stream
  page's view (the count of items shown), and a run's start as that
  stream's view of the matching items; each decision is audited by its
  stream page, as from the hub.

### Wizard concurrency

- **Live on load.** The sequence is fixed, but each page reads its
  subject's items when it is drawn, so it shows the stage another reviewer,
  a refresh or the Family's resubmission left. A Family that answered again
  shows its new items in place of the replaced ones.
- **Stale actions refused as today.** Every action posts the item's
  version as the page drew it to the stream's own decision path
  (`review_worklist.record`), so a decision over a change someone else made
  first is refused with the stream's notice
  ([concurrency](#concurrency)), and the section is redrawn in place from
  the page's own address showing the live stage.
- **Two tabs.** A run started in another tab replaces this tab's run,
  whose next page or move says the review is no longer open; two tabs on
  the same run are last-write-wins for the position.
- **Live while open** ([#2174](https://github.com/epiphany40223/parishkit/issues/2174)).
  Each wizard page's sections are review regions as the worklists' are
  ([concurrency](#concurrency), live while open): a section opts in with the
  region's one attribute (`data-live-items`, its page's own address), its
  items already carry their keys and tokens because they are drawn through
  the shared row includes, and the wizard page's view answers a `live=`
  read through the same shared helper (`web/review_live.py`) as the stream
  pages. A changed item is redrawn in place with its tag; the page's
  subject, sequence and position never change under the reader. Until the
  wizard's pages adopt it, they are live on load only.

### Wizard entry points

- **Menu.** **Review one at a time** is the **first** entry of the Review
  work menu group, ahead of My review work and the five streams, shown
  while at least one stream is offered (a new `Entry` whose availability is any of the five pages'),
  and named in the trail and every "Return to".
- **From each table.** Each review worklist's selection bar gets
  **Review these one at a time** (a secondary button, first, beside Add). It
  posts the table's current view fields (`Worklist.fields`, the search
  included, and the table's sort) to the wizard's start, which starts a
  run with them and opens page 1, skipping the choice steps; it never
  asks about unfinished runs, which the start replaces
  ([wizard run lifetime](#wizard-run-lifetime)). The table's
  sort maps to the wizard's: its name column ascending (or any column other
  than Submitted) to Last name (A–Z), Submitted ascending to Oldest submitted
  first, Submitted descending to Newest submitted first. The button is
  unavailable, with its reason ("Nothing is listed to review one at a
  time.") on its own line under the bar's buttons, while the table lists
  nothing
  ([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)).
  The shared table component draws the button
  (`components/review-wizard-entry.html`, from
  `review_wizard_tables.table_entry`) inside the table's region, so every
  in-place redraw of the table gives it the view the table then shows. It
  posts the table's Stage, the stream's own closed filters and the search,
  in Advanced mode, to the start, which parses, checks and reads them as a
  start from the start page does.
- **Dashboard pane.** My review work (for Staff and Ministry leaders) and
  Administrators' Home Review work panel put the wizard **beside** the
  stream groups, so the choice reads plainly: review everything one at a
  time, or open any table to work in bulk (R52 Part W, option B; the
  Administrator, 2026-10-25). Under the page's or panel's heading is one
  row, drawn by `review-work-split.html` for both pages:
  - On the left, a **One at a time** pane about 13rem wide, in the accent's
    soft fill with its line colour as a 1px border. At its top, as normal
    flow: the small uppercase label "One at a time" and the muted line
    "Every stream's waiting items, one Family or Member per page." (the
    wording the Administrator approved, 2026-10-25; it names no position, which would be wrong once
    the parts stack and means nothing to a screen reader). Below them, centred in the height left, the
    portal's primary button **Review one at a time**, a link to the
    wizard's start page, whatever runs the reader has (R52 Part W: no
    Continue label). While the menu greys the wizard out, the pane shows
    the menu's reason as muted text in the button's place, never a link.
  - In the middle, a thin vertical rule with a small muted "or" at its
    centre. It is decoration, hidden from screen readers, which read the
    pane and then the tables.
  - On the right, a small uppercase **In bulk** label above the stream
    groups, unchanged except that they fit the narrower column: narrower
    stream names and cards, so My review work still fits a laptop window
    under the [overview pages](../ui-conventions/spec.md#overview-pages)
    rule and its two-line exception. Beside the pane the stream names are
    smaller, at the heading-3 token, so words such as "information" fit
    the narrow name column; stacked, they take the full heading size.

  The layout follows the room the panel has, not the window: from 48rem
  (a 1280px laptop window with the menu open) the parts sit side by side;
  in less (a phone or a narrow window) they stack, the pane at full width
  with its button after its text (beside it while there is room), then a
  horizontal rule with "or", then the tables at full width, with no
  sideways scroll at 320px. The pane shows no count of its own: the
  waiting set differs per stream, so a total would add unlike things and
  repeat the stream cards' numbers. It is shown to readers for whom the
  wizard is offered; without it, the pane, the rule and the In bulk
  label are left out and the stream groups take the full width. The
  layout is static: nothing moves on hover, focus or a click
  ([no layout shift](../ui-conventions/spec.md#no-layout-shift)), and its
  colours are theme tokens, so dark mode follows.
- My review work's stream cards keep opening the tables.

### Wizard shared component

The wizard adds pages, not review logic. What each view shares, by
requirement:

| Part | Tables | Hub | Wizard |
| --- | --- | --- | --- |
| Filter definitions and parsing | the stream's query class | (none) | same class |
| Worklist read and ordering | the stream's read | narrowed to one Family | same read, then subjects |
| Section (changed values and actions) | (table rows) | hub tab | same section, no tab |
| Actions | stream endpoint | stream endpoint | stream endpoint |

**Filters (shared as is, one extraction).** The query classes are the
tables': `census_changes.CensusQuery`, `ministry_worklist.WorklistQuery`,
`information_worklist.WorklistQuery` and
`limitation_worklist.WorklistQuery`, with their filter templates
(`census-worklist-filters.html`, `ministry-worklist-filters.html`,
`information-worklist-filters.html`, `limitations-worklist-filters.html`).
Pledges parses its Stage and Search inline in
`pledge_review_views._choices`; that part moves into a
`pledge_review_views.PledgeQuery` (`parse`, `form_values`) that the page
and the wizard both call, with `pledges-filters.html`.

**Reads (shared as is).** The matching rows come from each table's own
read and Stage selection: `census_changes.census_changes` (whose `select`
applies the filters), `ministry_worklist.read_rows` with `chosen`,
`pledge_review_views.pledge_rows` with `at_stage`,
`information_worklist.requests`, and `limitations.limitations_report`
with `limitation_worklist.answer_rows` and `at_stage`. A new
`reports/review_streams.py` holds one `ReviewStream` per stream: its
label, page name and access check, query class and filter template, its
read (one of the above), `subject_of(row)` and the section builder. The
start page, the sequence and the pages read streams only through it. The
subject order uses the shared name key of
[#2124](https://github.com/epiphany40223/parishkit/issues/2124) (a
`review_worklist.name_order` key the tables' default sort uses too), so a
table's Name order and a run's cannot drift; whichever of the two lands
first adds it.

**Section (split out of the hub).** Today the hub's per-stream parts are
tied to its tabs: `timeline_views.hub_reads` picks the read by the chosen
tab, the panel templates carry the tab's heading, its link sentence and
`hub_tab_url`, and `hub-census-panel.html` also draws the Member's
Ministry part. The split:

- As built (#2123 slice 2), the hub's per-stream reads and row actions
  stay where they are, in `hub_census`, `hub_streams` and `hub_actions`
  (`census_worklist` with `census_subject`, `financial_tab`, `other_tab`,
  `ministry_worklist`, `limitations_worklist`), and are the one
  implementation. A new `reports/review_sections.py`,
  `read_section(stream, campaign, family, subject, principal, *,
  parish_name)`, composes those same functions for one wizard page and
  then narrows their result to the run's matched items. `hub_reads` and
  the hub's tab functions are unchanged. This replaces the planned
  `census_tab(subject)`, `other_tab(keep=…)` and `ministry_tab(scoped=True)`
  parameters, which the narrowing makes unnecessary. A Ministry
  requests page reads through `hub_actions.ministry_worklist`, whose
  `read_rows` is the leader-scoped read; it takes the run's Members
  choice (`members`, the table's default on the hub and always for a
  Ministry leader), so a run started at Members no longer in ParishSoft
  reads the list it was started from.
- **Member pages** (#2123 slice 3) draw the hub Member tab's parts for
  the run's stream, matching the Member on its entity, never the hub's
  tab key. Census changes' Member and New Member pages draw the Member's
  census subject (`census_subject`); a New Member's page shows its
  "Adding this Member" line and the Family's answers for the Member.
  Ministry requests' page draws the Member's requests
  (`hub_streams.read_member_ministry`) decorated by `ministry_worklist`.
  Limitations' Member page draws the Member's Cannot participate in
  ministries answer from the hub's Additional answers, named as the hub's
  tab names the Member. A request
  or answer the stream's scoped read does not list for the reader is
  neither drawn nor counted in the filtered-out line, so a Ministry
  leader never learns of another Ministry's request for the same Member.
- **Live replacements.** The stored item ids are first followed to their
  live replacements: a census change or Ministry request through
  `superseded_by`, an Additional information item through `replacement`.
  A pledge is the Family's current one. Limitations answer ids do not
  change. The section then keeps the replacements the stream's scoped read
  still lists for the reader, and counts the subject's other listed items
  for the filtered-out line.
- **Scoped, on every page.** Each page, Member pages included, reads its
  subject through the stream's own worklist read under the reader's
  principal. For a Ministry leader that read is `ministry_worklist.read_rows`,
  limited to their Ministries. The subject is named only while one of its
  items is listed ([wizard pages](#wizard-pages)).
- Templates under `components/`: `review-census-section.html` (the
  addition line, the Changed table and Show unchanged, from
  `hub-census-panel.html`; takes `subject`), `review-ministry-section.html`
  (from `hub-ministry.html`; takes `rows` and `w`),
  `review-pledge-section.html` (from `hub-financial-panel.html`, the
  contributions line included; takes `financial` and `pledge`) and
  `review-answers-section.html` (the Answers to handle table, from
  `hub-other-panel.html`; takes `rows` and `actions_column`). The
  wizard's Ministry requests pages pass `review-ministry-section.html`
  Ministry requests' worklist of the drawn rows as `w`, as the hub's
  Member tab does. Each also
  takes a `prefix`, which its ids carry. None of them draws the dialogs.
  They keep
  `hub-changed-table.html`, `hub-stage.html` and `hub-dialogs.html` as they
  are. The hub panels keep their regions, headings, link sentences and
  dialogs (`hub-dialogs.html`) and include the sections, so the hub draws
  exactly what it drew before (every hub render the hub unit tests make is
  identical apart from the random request keys). The wizard page draws
  its parts through `components/review-wizard-section.html`, which
  includes the same section components, the filtered-out line and the
  stream's dialogs.
- The region each section sits in (`data-in-place-region`,
  `data-refresh-url`) redraws from the hub tab's address on the hub and
  from the wizard page's address in the wizard, so an action redraws in
  place on both ([hub row actions](../admin-portal/spec.md#hub-row-actions)).

**Actions (shared as is).** The section's dialogs post to the stream page
(`Worklist.action`, `answer_page` off) through `read_decision` and the
stream's `DecisionService`, with the same version checks, refusals,
audit and notices; the wizard has no decision endpoint.

**New, wizard-only.** `reports/review_wizard.py` (pure: the sequence from
rows, progress words, sort mapping, run limits),
`reports/review_wizard_views.py` (start, page, move, discard), the
`ReviewRun` model, the URLs `reports/review/` (`review_wizard`) and
`reports/review/<position>/` (`review_wizard_page`) in
`admin_urls/reports.py`, the templates `review-wizard-start.html` and
`review-wizard-page.html`, and the navigation entry in
`admin_navigation.py`.

### Wizard delivery plan

Four pull requests, after this spec's review (the slices of #2123; the
start page and the dashboard card come first, the hub's section split
with the first slice that draws a section). Slices 1 to 3 board one
train together, after the Administrator's pk-local check of the whole
flow, since the menu entry and the card open pages whose sections arrive
in slices 2 and 3:

1. **Run storage, start page, page frame, menu entry and card**:
   `ReviewRun` and frozen file 0178, its grants, schema inventory and
   users-swap entries; `review_streams.py` (each stream's label, access,
   query class, filter template, read and audit) and
   `pledge_review_views.PledgeQuery`; `review_wizard.py` (the sequence,
   the sorts, the progress words, the 5,000-page refusal); the views,
   templates and URLs of the start page and the run pages with their two
   navigation rows, Previous, Next and Finish, the resume list with
   Continue and Discard (removed again in R52 Part W), the menu entry and
   the Review one at a time card. The run pages' section region is empty. Tests: unit tests of the
   sequence (name-order ties broken deterministically, grouped Family then
   Member order, a Family with Member items only, the 5,000-page refusal)
   and the progress words; view tests under the web login (exact
   statuses: a run started, moved, finished and no longer open; several
   runs listed, continued and discarded; another reader's run never
   shown; the start refusals; a leader offered only Ministry requests
   with the choice skipped; the run guard); the menu inventory; browser
   tests of the start page, the rows and the card.
2. **Shared section component and Family pages** (built):
   `review_sections.read_section` over the hub's own functions, the four
   `components/review-*-section.html` templates and the hub panels
   including them (the hub draws exactly what it drew: a render
   comparison of every hub render the hub unit tests make, before and
   after); then the Family pages of Pledges, Additional information,
   Census changes and Limitations, each with only the matched items, the
   filtered-out line, and actions posting to the stream pages and
   redrawing in place from the wizard page's address; and every page,
   Member pages included, naming its subject only while it is listed.
   Tests: the hub's tab, row-action and query-count tests unchanged; the
   narrowing to matched items with the filtered-out line; the Limitations
   Family page's answers and pledge parts; a replaced item shown by its
   replacement; a page drawn after a decision shows the live stage and a
   stale action gets the stream's refusal; a leader who lost the Ministry
   sees no name; query-count tests per page.
3. **Member pages** (built): Ministry requests (through
   `hub_actions.ministry_worklist`, so a leader never gets a row for a
   Ministry they do not lead), Census
   Member and New Member pages and Limitations Member pages; browser
   tests (Chromium and WebKit locally, all three in CI) that Previous and
   Next, in both rows, keep their screen position across pages of
   different heights and after an in-place action, and that focus
   returns to the control used.
4. **Table entry buttons** (built): Review these one at a time on the
   five tables. Tests: each table's button posts its view and opens page 1
   with that view's sequence; the sort mapping; the existing-runs dialog (removed again in R52 Part W); the unavailable state on
   an empty table (no label is renamed, so no table test needed a sweep).
   With it, the dashboard card becomes the side-by-side
   [dashboard pane](#wizard-entry-points) (R52 Part W): browser tests of
   the side-by-side row at 1280px (the gap below Home's heading, the text
   at the pane's top and its button centred below it, the greyed
   state), the stacked parts with no sideways scroll at 320px,
   and the full-width tables without the wizard. R52 Part W's findings
   follow in the same pull request: no History in the wizard, Finish
   always in the middle slot,
   each stream's Basic search help, the
   Last name (A–Z) sort label, the Family and Member header lines, the
   Family-only progress words in the grouped streams, no footer links on
   the pages, the pane's shorter help line, and no unfinished
   runs anywhere (a start replaces them); tests of each, with the resume
   tests removed. R52 Part W2's follow in it too: the pane's **In bulk**
   label (was By table), the wizard's own stream order with Additional
   information last, and Pledges runs without the Cannot contribute
   Families; tests of each.

## Update phase

The update phase has two parts that run independently, each started by a
person, each reading only Reviewed items.

### Automatic update

Only census changes the [handling registry](../data/spec.md#proposed-changes)
marks API-writable can be updated automatically: Member contact fields
(names, birth date, language, email, phones; gender stays by hand while
[#1616](https://github.com/epiphany40223/parishkit/issues/1616) is open).
Family addresses stay by hand ([Family addresses](#family-addresses)). The
run is the [Update ParishSoft](../reports/spec.md#update-parishsoft) page,
renamed from Publish to ParishSoft, Administrator-only, still behind the
`parishsoft_publication` switch (off in Production until the Administrator
turns it on). Its result stage is Updated in ParishSoft; the final refresh
the run requests then moves each item to Confirmed by its
[refresh check](#refresh-checks). A Ready item ParishSoft already holds
(rule 1) is listed by the run's check as ParishSoft already holds it, nothing to
write, and marked Updated in ParishSoft there without a write
([#1890](https://github.com/epiphany40223/parishkit/issues/1890); before, it was marked Confirmed at once); the run's own
refresh, or the next one, confirms it. It is marked at the check, as
before, because a check with nothing to write has no Update to confirm
(rejected: leaving such an item Ready for ever).

As built by [#1688](https://github.com/epiphany40223/parishkit/issues/1688):
the page counts and checks exactly the Ready to update items (an accepted
change in Conflict is not counted, but the check still holds its Member
back), counts apart a Ready item whose Member left the Family (rule 4),
and adds the confirmation and run that
[#528](https://github.com/epiphany40223/parishkit/issues/528) had specified
but not built: Update in the review dialog, the run's live status and
per-Member outcomes, Run again and the final refresh. The machinery
underneath is unchanged. The `pk-admin census update-*` commands and the
`--decision accepted` rename (decision 7) are
[#1755](https://github.com/epiphany40223/parishkit/issues/1755).

### Manual handling reports

Every stream has a **To enter by hand** (or To handle) report: the
worklist filtered to that stage, with Entered by hand / Handled as its row
and bulk action, exported through the shared export component in CSV, XLSX
and PDF. The same report has Stage choices for Entered incorrectly and Not
seen in ParishSoft (once [#1689](https://github.com/epiphany40223/parishkit/issues/1689)
lands; until then, To enter by hand and Entered by hand). The report is the
stream's own review worklist at that Stage, never a page of its own (the
Administrator, R39 finding 2, 2026-10-19), and its dialog takes no note
for a mark that records none (`web/review_worklist.build`'s `silent`).
Each stream's report, as it lands:

- **Ministry:** Ministry requests at its To enter by hand Stage, with its
  export carrying the roster-entry columns
  ([roster entry](../admin-portal/spec.md#roster-entry-on-ministry-requests);
  Roster changes to enter, the first such report,
  [#1687](https://github.com/epiphany40223/parishkit/issues/1687), was folded
  into it).
- **Census:** Census changes' worklist at its To enter by hand Stage, with
  its export ([#1683](https://github.com/epiphany40223/parishkit/issues/1683)).
- **Financial:** [Pledges](../reports/spec.md#pledges) at its To enter by
  hand Stage ([#1686](https://github.com/epiphany40223/parishkit/issues/1686)).
- **Cannot participate and Additional information:** their To handle
  view, with Handled ([#1685](https://github.com/epiphany40223/parishkit/issues/1685)).

## What cannot be automated

- **Ministry rosters**: neither ParishSoft API can add, remove or change a
  minister ([API analysis §3.3](../../../parishsoft-api-analysis.md)). The
  roster lists are readable, so entries are confirmed.
- **Pledges and share methods**: giving data is read-only. Pledges are
  readable (`offering/pledge/list`), so an entered pledge amount is
  confirmed; share methods are not.
- **Clearing a value**: the API ignores `null` and refuses `""`, so any
  answer that removes a recorded value is by hand.
- **Deceased status, moved household, new Members, prefix, suffix, marital
  status, email opt-out**: no write endpoint, or (death date) a write that
  does not complete the request. All are readable (the email opt-out as
  the Family's `sendNoMail` flag), so their entries are confirmed.
- **Gender**: the PUT returns success but the value is not applied
  ([#1616](https://github.com/epiphany40223/parishkit/issues/1616)). It is
  readable, so a by-hand entry is confirmed.
- **Family addresses**: a different case from gender, set out below.
- **Cannot participate, cannot attend, Additional information**: not
  ParishSoft data; they end at Handled.

### Family addresses

Checked against the code, the specs and ParishSoft's published v1 and v2
schemas (read 2026-10-18; no ParishSoft data was read):

- **The write exists but has never been tried.** `PUT
  families/{familyId}/contact` accepts separate home, mailing and other
  address blocks (`homeAddressLine1` … `homeAddressPostalCodePlus4`, the
  same for mailing and other) and a `primaryAddress` value. The live write
  test of 2026-10-17 ([#528](https://github.com/epiphany40223/parishkit/issues/528))
  sent only `primaryPhone`; no address has been written. So addresses are
  **not** known to behave like gender (success returned, value not
  applied); whether a write applies is simply unknown.
- **No read in either API returns the home and mailing blocks; this is a
  ParishSoft limitation, not ParishKit's.** Every read that carries a
  Family address, in both published schemas, carries only **one** address,
  ParishSoft's primary one, and no country:
  - v2 `POST families/search`, `POST families/quick-search` and `GET
    families/{familyId}` (all `FamilySearchResponseDto`), and v1 `GET
    Families` and `GET Families/{ID}` (`FamilyResultModel`, the same
    fields): `primaryAddress1`–`3`, `primaryAddressFull`, `primaryCity`,
    `primaryState`, `primaryPostalCode`, `primaryZipPlus`, plus the
    `publish_Address` / `primaryPublishAddress` flags;
  - v2 `POST members/search`, `GET members/{memberId}` and v1 `GET
    Members`, `GET Members/{ID}`: the same address as
    `familyAddres_Primary…` fields;
  - v1 `QuickSearch/Families` and `QuickSearch/Members`: `address_1`–`3`,
    `city`, `postalCode`, `zipPlus`;
  - v2 `GET families/change/list` and v1 `GET Families/FamilyChanges`: a
    log of changes to that address (current and previous lines, city,
    postal code);
  - the giver lists (`primaryAddress` as one string) and the contribution
    summaries (`address_1`, `address_2`, `city`, `postalCode`).

  The home, mailing and other blocks appear only in the write models (v2
  `FamilyContactUpdateRequestModel`, v1 `FamilyContactInfoModel`, used only
  by `POST Families/UpdateFamilyContactInfo`). ParishKit already reads the
  primary address (`families/search` in `source/corpus.py`; `GET
  families/{familyId}` in `parishsoft_households.py` until #1147 removed
  it), so there is no further read to add.
- **Which block is primary is not documented.** The schemas describe the
  read fields only as "the family primary street address", and the write's
  `primaryAddress` only as "Specify the family primary address" (a free
  string, no allowed values), which suggests it selects the primary block
  but does not say so.
- **The Family form shows no address baseline.** The snapshot keeps the
  primary address (`source/corpus.py`; the directories use it), but the
  census form's home and mailing address fields are marked unavailable
  (`responses/inputs.py`, `family_field_value`), so a Family types both
  addresses afresh and every census address answer is a change against an
  unavailable baseline.

The human-run smoke test on the Test Family (2026-10-18, Administrator
approved, recorded on
[#1688](https://github.com/epiphany40223/parishkit/issues/1688); the
Family was restored and the Administrator checked it in ParishSoft's own
screens) wrote a marker into the home block's second line with `PUT
families/{familyDUID}/contact` and read the Family back:

- **The v2 primary fields follow the home block, for a Family whose primary
  address is home.** The marker appeared at once in `primaryAddress2` and
  `primaryAddressFull` (`GET families/{id}`, `families/search`) and in the
  Members' `familyAddres_PrimaryAddressFull`; nothing else changed. The
  read-after-write mapping is `homeAddressLine1` → `primaryAddress1`,
  `homeAddressLine2` → `primaryAddress2`, `homeCity` → `primaryCity`,
  `homeState` → `primaryState`, `homePostalCode` → `primaryPostalCode` and
  `homePostalCodePlus4` → `primaryZipPlus` (line 2 shown by the marker, the
  others inferred from unchanged values). The home country and address
  phone, and every mailing and other field, are not readable.
- **The mailing block was not written**, since no read returns it, so a
  mailing write still cannot be verified. A Family whose primary address
  is mailing (or other) was not tested; a home write probably shows in no
  read for it.
- `families/change/list` showed no row for the write, so it is no
  read-after-write for addresses.

What follows: addresses stay **To enter by hand**, and writing them
through the API stays out, since the mailing block cannot be verified and
no read returns the `primaryAddress` selector, so ParishKit cannot tell
which block a Family's primary fields follow. A hand-entered home address
is **confirmable** for a Family known to have home as its primary address,
comparing street lines, city, state and postal code; the mailing block
stays unconfirmable. No read says which block is primary, so
[#1749](https://github.com/epiphany40223/parishkit/issues/1749) takes the
primary fields as the home block unless they hold the Family's mailing
answer, and never reads a difference as Entered incorrectly
([refresh checks](#refresh-checks)). The same mapping would let the Family
form show the primary address as the home block's baseline.
[#528](https://github.com/epiphany40223/parishkit/issues/528) records the
schema reading above.

## Audit

Every decision, edit, addition, mark and update outcome is one immutable
row (who, when, the note, the resulting state), paired with a stream audit
event that names only versions and counts, never values, as
[Manual census resolution](../reports/spec.md#manual-census-resolution) and
[Automatic census review](../reports/spec.md#automatic-census-review) do
today. A Family resubmission that reopens or withdraws an item is a row with
the Family (or the Staff member using Open form) as actor. A stage change
by a refresh (Confirmed, Conflict, Entered incorrectly, Not seen) is
recorded as such, with no actor. Each stream's history view lists the rows
in order, and the Family hub's Timeline lists one Family's rows across every
stream as its [review lines](../reports/spec.md#timeline-review-lines), so a
stream that stores a new kind of row adds it there too. New schema follows
the
[post-launch schema policy](../operations/spec.md#post-launch-schema-policy).

## Shared code

- one pipeline module mapping each stream's state to a stage and its
  allowed actions per role (one function per stream, unit tested as a
  table that mirrors [role-specific actions](#role-specific-actions));
- one refresh comparison (R, A, C) shared by every ParishSoft-backed
  stream, unit tested as a table of the
  [re-evaluation rules](#baseline-refresh-re-evaluation);
- one review-worklist template and view helper built on
  `table-selection.html`, `table_actions` and the confirmation dialog
  (`components/review-worklist.html`, `web/review_worklist.build`);
- one decision service per stream behind a shared interface (accept, edit,
  ignore, add, back to review, mark entered) so the web page, bulk bar
  and `pk-admin` share it (`web/review_worklist.DecisionService`, with
  `read_decision` reading the one POST every control sends) ([#463](https://github.com/epiphany40223/parishkit/issues/463));
- the shared export component of
  [#1679](https://github.com/epiphany40223/parishkit/issues/1679);
- proposed by [#2123](https://github.com/epiphany40223/parishkit/issues/2123):
  one section component per stream (`reports/review_sections.py` and
  `components/review-*-section.html`), drawn by the Family hub's tabs and
  the [review wizard](#review-wizard)'s pages
  ([wizard shared component](#wizard-shared-component)).

## Proposed convention changes

Done: the general UX rules this pipeline needs were made in the
[UX conventions](../ui-conventions/spec.md) (not here) by the vocabulary
slice ([#1691](https://github.com/epiphany40223/parishkit/issues/1691)),
which are the rules in force:

1. [Row actions and confirmation](../ui-conventions/spec.md#row-actions-and-confirmation):
   the selected state, row actions by role, and selection boxes on every
   actionable row.
2. [Admin tables](../ui-conventions/spec.md#admin-tables): worklist filters
   and export pane, and the create action in the selection bar.
3. [Status indicators](../ui-conventions/spec.md#status-indicators): the
   needs-correction looks.
4. [Review pipeline terms](../ui-conventions/spec.md#review-pipeline-terms),
   pointing at the [vocabulary](#pipeline-vocabulary).

## Decisions

The Administrator accepted every recommended default on 2026-10-18, with
additions to 4–7, and answered 9 the same day:

1. Who may review census changes that ParishSoft's API can write?
   **Staff may Accept, Edit and Ignore; only an Administrator starts an
   update run.**
2. Do *By hand* census changes need a review before entering?
   **Yes, but Entered by hand on a To review row accepts and marks it in
   one step**, so Staff working through a list are not slowed down.
   **Reversed on 2026-10-21** ([#1890](https://github.com/epiphany40223/parishkit/issues/1890)): every item is reviewed
   first, so Entered by hand is offered only on an accepted item, in every
   stream, and Staff Accept (in bulk) and then mark Entered by hand (in
   bulk). Items already marked in one step keep their history, shown
   Accepted.
3. May Ministry leaders move a request to another Ministry? **Only into a
   Ministry they also lead; Staff into any.** With Replace dropped, this is
   the Ministry request's Edit ([role-specific actions](#role-specific-actions)).
4. Does a pledge need review? **Reviewed, with bulk Accept**, and pledges
   can also be **edited** (amount and frequency), so Staff fix a mistyped
   amount before entering it in ParishSoft. **Widened on 2026-10-25**
   ([#2177](https://github.com/epiphany40223/parishkit/issues/2177), option
   B): Edit also carries the reviewer's share methods, stored on the pledge
   review row beside the Family's answer as the amount is, so a reviewer
   need not leave the page for Open form; Cannot contribute stays with
   Open form ([Financial stream](#financial-stream)). A staff resubmission
   through the Family form's submit (option A) was rejected: it would
   rebuild the whole reviewed-form payload on the server, reopen every
   reviewed item it drew, add a response and email a receipt.
5. Confirmation of hand-entered data: **every hand-entered value that a
   ParishSoft read returns is confirmed by the refresh, pledges included**
   ([what is confirmable](#what-is-confirmable)), as a check that it was
   entered correctly.
6. Refresh confirmation of an item marked Entered by hand: **yes; flag Not
   seen in ParishSoft after two full refreshes, and show Entered
   incorrectly** when ParishSoft holds a different value
   ([Entered incorrectly](#entered-incorrectly-stage)).
7. Retire "Approve"/"Publish" for "Accept"/"Update ParishSoft" everywhere,
   including audit display and `pk-admin`: **yes, with no command
   aliases**; backwards compatibility is not needed.
8. Does My review work replace Home for Staff and leaders? **Replace it.**

9. When do Families see reviewed changes, and does the revamp wait for the
   next campaign? **The Staff and Ministry leader pipeline ships during this
   campaign, ahead of everything else. The Family view
   ([#1705](https://github.com/epiphany40223/parishkit/issues/1705)) has
   lower priority and comes after it; if it is ready before this campaign
   closes it is deployed then, and if not, that is fine.** The
   Administrator expects Staff to make only consistency fixes and the
   corrections a Family asked for, so Families will not be surprised by
   what they see. On 2026-10-19 the Administrator dropped the planned
   on/off setting for it: Families see the reviewed result from the
   release with its last slice
   ([Families see reviewed changes](#families-see-reviewed-changes)).

The review also settled the structure: [role-specific
actions](#role-specific-actions) with Add, [one working change
set](#one-working-change-set) per Family, and the exact
[conflict rule](#baseline-refresh-re-evaluation). No decision is open.

## Delivery plan

The epic's child issues, each a small pull request, in delivery order (the
[timeline](#timeline-to-campaign-close) gives target dates and what must
land before the campaign closes):

1. [#1691](https://github.com/epiphany40223/parishkit/issues/1691) Spec and
   vocabulary (this document, then the convention edits) and the shared
   stage mapping, role-action table and refresh comparison, no UI change.
2. [#1681](https://github.com/epiphany40223/parishkit/issues/1681) Review
   worklist component, selected-state icons, role-filtered actions, Add and
   full selection.
3. [#1690](https://github.com/epiphany40223/parishkit/issues/1690) My
   review work for Staff and leaders, linking to today's pages until each
   stream moves.
4. [#1682](https://github.com/epiphany40223/parishkit/issues/1682) Census
   changes page layout: the Administrator's concrete feedback.
5. [#1683](https://github.com/epiphany40223/parishkit/issues/1683) Census
   changes moves to the worklist; review and update separated; Staff review
   of Automatic changes; Add; the census cases of the
   [resubmission rule](#resubmission-before-the-reviewed-form).
6. [#1684](https://github.com/epiphany40223/parishkit/issues/1684) Ministry
   requests moves to it, with Edit (Ministry), Staff Add and the "Family
   asked again" case.
7. [#1687](https://github.com/epiphany40223/parishkit/issues/1687) Manual
   handling reports on the shared export component (Entered incorrectly and
   Not seen in ParishSoft appear once #1689 lands).
8. [#1685](https://github.com/epiphany40223/parishkit/issues/1685)
   Additional information and Limitations move to it, with the one-time
   follow-up migration.
9. [#1686](https://github.com/epiphany40223/parishkit/issues/1686)
   Financial review: per-pledge items, Edit amount and frequency, bulk
   Accept.
10. [#1689](https://github.com/epiphany40223/parishkit/issues/1689) Baseline
    refresh re-evaluation with the R/A/C rule, Entered incorrectly and Not
    seen in ParishSoft.
11. [#1704](https://github.com/epiphany40223/parishkit/issues/1704) Pledge
    confirmation: read the upcoming year's pledges and
    confirm entered pledges; frequency after its smoke check.
12. [#1688](https://github.com/epiphany40223/parishkit/issues/1688)
    Automatic update: Publish to ParishSoft becomes Update ParishSoft over
    Ready items, with the confirmation and run it lacked, and the Family
    address smoke test; the renamed commands with no aliases moved to
    [#1755](https://github.com/epiphany40223/parishkit/issues/1755).

13. [#1705](https://github.com/epiphany40223/parishkit/issues/1705)
    Families see reviewed changes: the working change set in
    the Family form and the resubmission rule, with no on/off setting, in
    six pull requests ([reviewed form delivery](#reviewed-form-delivery)).
    Lowest priority.

## Rolling out during this campaign

The Administrator's goal (2026-10-18) is the Staff and Ministry leader
pipeline in use before the current campaign closes, without harming the
existing database or the running campaign. Every slice except
[#1705](https://github.com/epiphany40223/parishkit/issues/1705) ships to the
current campaign; #1705 comes last and ships when ready
([Families see reviewed changes](#families-see-reviewed-changes)). This section is how
that is done safely.

### Data integrity

- **Forward-only schema.** Every schema change is a new forward migration
  with a frozen SQL file, following the
  [post-launch schema policy](../operations/spec.md#post-launch-schema-policy),
  never an edit to an applied migration. During the campaign the changes
  are additive only: new tables (decision rows for Ministry Edit, Add,
  financial and cannot-participate items, confirmation and mark records)
  and new columns that are nullable or not null with a constant default
  (metadata-only in PostgreSQL, so no row is rewritten; #1705's
  `reviewed_view` is one). No column is dropped, renamed or narrowed, and
  no CHECK is tightened over existing rows (a new column's own check, which
  every existing row meets, is not), before the campaign closes.
- **Stages are derived, not copied.** A stage is computed from each stream's
  stored state by the shared mapping
  ([#1691](https://github.com/epiphany40223/parishkit/issues/1691)), unit
  tested against every stored combination below. No migration updates an
  existing decision, execution, workflow state, outcome, disposition, note,
  contact attempt or audit row; history is never rewritten.
- **Words change, stored values do not.** The vocabulary is display only:
  `approved` stays `approved` in the database, and audit event types keep
  their names. Only what pages, exports, help and `pk-admin` show changes.
- **Today's write guards stay in force.** Each slice's new decisions pass
  through the stream's existing SQL guards and resolution rows (for example
  migration 0060's census resolution rows), extended, not bypassed.

#### Existing state to stage

| Stream | Stored today | Stage |
| --- | --- | --- |
| Census | `unreviewed` + `pending` | To review (By hand rows too: today's *To do*; since #1890 they are accepted before Entered by hand, [decision 2](#decisions)) |
| Census | `unreviewed` or `approved` + `conflict` | Conflict (an approval stays visible, not in force) |
| Census | `approved` + `pending`, `api` / `manual` | Ready to update / To enter by hand; Accepted (edited) when an edited value is set |
| Census | `approved` + `failed` | Ready to update, with the failure shown |
| Census | `queued` / `published` | Updating / Updated in ParishSoft (Confirmed by its latest [refresh check](#refresh-checks)) |
| Census | `resolved_external` / `resolved_upstream` | Entered by hand (Confirmed or Entered incorrectly by its latest [refresh check](#refresh-checks)) / Confirmed (since #1890 only for a queued change a refresh finds ParishSoft already holding; no other refresh or update run stores it, and since #1895 no Family resubmission) |
| Census | `ignored` (any open execution) | Ignored |
| Census | `superseded`, `cancelled` | history |
| Ministry | `new`, `assigned`, `in_progress` | To review |
| Ministry | `resolved` + `joined` or `leave_confirmed`, not ticked / ticked | To enter by hand / Entered by hand |
| Ministry | resolved by refresh reconciliation (roster current; history since #1890), or accepted, ticked and Confirmed by its latest [refresh check](#refresh-checks) | Confirmed |
| Ministry | `resolved` + `declined` (join declined, or "Stayed in the Ministry"), `duplicate`, `other`; `closed_no_response` | Ignored, with the original outcome kept as the decision's label (so "Other" and "No response" still read as such) |
| Ministry | `cancelled`, `superseded` | history |
| Additional information | `current_actionable`, follow-up needed, not followed up | To handle |
| Additional information | `current_actionable`, followed up | Handled |
| Additional information | `current_actionable`, follow-up not needed, not followed up | To review, unless a Staff edit set it (below) |
| Additional information | `superseded`, `withdrawn` | history |
| Financial | the live answer, no `stewardship_pledge_review` row yet | To review |
| Cannot participate | the live answer only | To review |

One state needed a decision. `follow_up_needed` defaults to false, so
"Not needed" cannot be told from "never looked at" by the item alone, and
mapping every such item to Ignored would mark unread text as reviewed.
[#1685](https://github.com/epiphany40223/parishkit/issues/1685) adds a new,
nullable decision column to each Staff revision, which every revision from
then on states; a current item whose latest revision is older (the column
empty) and left follow-up not needed reads as Ignored, since a Staff member
chose that, and an item with no revision is To review. The rule is read,
not written, as the derived values below are: storing it on the item would
have to advance every such item's version (the response guard requires it)
and storing it on the old revisions would rewrite immutable history, so the
migration changes no existing row, needs no audit count and has nothing to
reverse.

No other state needs a migration. Two derived values for items closed
before their slice ships are read, not stored:

- the value "recorded at the Entered mark" for a census item marked before
  [#1689](https://github.com/epiphany40223/parishkit/issues/1689) is its A;
- Not seen in ParishSoft counts only full refreshes promoted after the
  release of #1689, so older marks are not all flagged on its first
  refresh. #1689's migration records that start time once, audited; deleting
  the row turns the flag off.

Existing pledges and cannot-participate answers start as To review: there is
no stored decision to carry, and bulk Accept handles them in a few clicks.

### Continuity

- **Family codes and links** are untouched by every slice: no credential is
  migrated, revoked, rotated, reissued or re-sent.
- **The Family form does not change** until #1705's last slice deploys. Each slice
  that touches the submit path proves it with a render comparison of the
  Family form against the base, and keeps the form's baseline reference
  dependency projection, so a Family whose form was open across a deploy submits without a
  stale-form refusal and is judged by [rule 1 to
  5](#resubmission-before-the-reviewed-form).
- **Existing pages keep working until their stream switches.** Each stream
  moves to the worklist in one release, replacing its page at the same
  address where it can; nothing redirects (no legacy URLs). Until then the
  stream's current page, report, digest and `pk-admin` commands work as
  today, and My review work links to them.
- **No data loss if a later slice is delayed.** Each slice only adds. An item
  a later slice will handle keeps its stored state and today's page;
  confirmation by a refresh slice that lands late confirms earlier entries
  on its first refresh, since it reads stored state. Publication stays
  Administrator-only, behind its switch, unchanged until
  [#1688](https://github.com/epiphany40223/parishkit/issues/1688).
- **Review continues after close.** Staff and leader review, entering by
  hand and the update run do not need the campaign to be open
  ([Review and publication](../data/spec.md#review-and-publication)), so a
  slice that misses the close is still useful the day it ships.

### Timeline to campaign close

The current campaign closes on or about **Monday, November 2, 2026**
([v1 launch](../../../plans/stewardship/v1-launch.md#go-live-date)); the
Campaign page holds the exact date. Releases are nightly, each deployed
only with the Administrator's approval. Target release nights, ordered so
Staff and leaders get value earliest:

| Release night (target) | Slice | When |
| --- | --- | --- |
| Mon Oct 19 | [#1691](https://github.com/epiphany40223/parishkit/issues/1691) vocabulary, stage mapping, role table, R/A/C comparison; convention edits | before close |
| Tue Oct 20 | [#1681](https://github.com/epiphany40223/parishkit/issues/1681) review worklist component | before close |
| Wed Oct 21 | [#1690](https://github.com/epiphany40223/parishkit/issues/1690) My review work (links to today's pages until each stream switches) | before close |
| Thu Oct 22 – Fri Oct 23 | [#1682](https://github.com/epiphany40223/parishkit/issues/1682) Census layout, then [#1683](https://github.com/epiphany40223/parishkit/issues/1683) Census on the worklist | before close |
| Sat Oct 24 – Mon Oct 26 | [#1684](https://github.com/epiphany40223/parishkit/issues/1684) Ministry requests, Edit and Staff Add | before close |
| Tue Oct 27 | [#1687](https://github.com/epiphany40223/parishkit/issues/1687) To enter by hand reports | before close |
| Wed Oct 28 – Thu Oct 29 | [#1685](https://github.com/epiphany40223/parishkit/issues/1685) Additional information and Limitations; [#1686](https://github.com/epiphany40223/parishkit/issues/1686) financial review | before close if possible |
| Fri Oct 30 – Sat Oct 31 | [#1689](https://github.com/epiphany40223/parishkit/issues/1689) refresh re-evaluation; [#1704](https://github.com/epiphany40223/parishkit/issues/1704) pledge confirmation | may follow close |
| Sun Nov 1 | buffer; no schema change the night before close | |
| week of Nov 2 | [#1688](https://github.com/epiphany40223/parishkit/issues/1688) Update ParishSoft, renamed commands, address smoke test | after close |
| when ready | [#1705](https://github.com/epiphany40223/parishkit/issues/1705) Families see reviewed changes | lowest priority; before close only if time allows |

**Must land before close:** #1691, #1681, #1690, #1682, #1683, #1684
and #1687. With them, Staff and leaders sign in to My review work, review
census and Ministry changes on one worklist, and work the To enter by hand
lists. **Should land before close:** #1685 and #1686. **May follow
close:** #1689, #1704 and #1688, because confirmation and the update run act on
entries that mostly happen after close, and today's reconciliation and
Publish page keep working meanwhile; and #1705, last of all.

### Risks and what to cut first

Risks:

- **The Family submit path.** The [rule before the reviewed
  form](#resubmission-before-the-reviewed-form) changes census, Ministry and
  Additional information derivation inside the submit transaction mid-campaign.
  Each slice first adds tests pinning today's outcome for every stage, then
  changes only the three cases listed there, under the existing row-lock
  order.
- **Schema during a live campaign.** Additive migrations only, numbered
  centrally, with self-verifying SQL; a failed migration fails the deploy,
  not the campaign (fail forward).
- **Staff habit.** Census By hand rows move from *To do* to To review, and
  the words change mid-campaign. The release notes and page help say so;
  Entered by hand still works in one step.
- **Release slippage.** Each night needs a deploy approval and one review
  round; a missed night moves everything after it by a day. Because #1689
  and #1704 may follow close, their nights (Oct 30, 31) and the Nov 1
  buffer absorb up to three missed nights for the must-land slices.
- **Query cost.** Derived stages and the My review work counts add queries to
  busy pages; each slice runs the page's query-count test.

Cut first if time runs short, in this order:

1. [#1705](https://github.com/epiphany40223/parishkit/issues/1705), already last; nothing depends on it;
2. the Update ParishSoft rebuild and renaming in #1688 (the Publish page
   keeps working, with the old words, Administrator-only);
3. pledge confirmation in #1704;
4. Entered incorrectly and Not seen in #1689 (today's reconciliation still
   confirms census and Ministry);
5. the Limitations half of #1685 (its report stays as it is);
6. the pledge Edit in #1686 (keep Accept and the Pledges
   report; Staff correct a pledge through Open form, as today);
7. census Add in #1683 (Staff use Open form); cut from #1683 and moved to
   [#1710](https://github.com/epiphany40223/parishkit/issues/1710), which
   built it.

Never cut: #1691, #1681, #1690, #1683, #1684 and #1687, which are what
Staff and Ministry leaders use.
