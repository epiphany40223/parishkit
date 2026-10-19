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
([#1683](https://github.com/epiphany40223/parishkit/issues/1683); its Add is
deferred, below), as does the financial stream
([Pledges](../reports/spec.md#pledges),
[#1686](https://github.com/epiphany40223/parishkit/issues/1686)). Every Staff
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
follows the resubmission rule: [with the setting
off](#resubmission-while-the-setting-is-off) in this campaign, and
[with it on](#family-resubmission) once #1705 ships. Staff entering a
Family's answers through Open form create Submitted items like the Family's
own.

### Reviewed stage

An item is **Reviewed** once someone allowed to review it records one
decision, within the limits of [role-specific actions](#role-specific-actions):

| Decision | Meaning | Value carried forward |
| --- | --- | --- |
| **Accept** | the Family's change is right as submitted | the Family's value |
| **Edit** | the change is right but its value needs correcting ("123 Main Street" → "123 Main St.", 100 weekly → 150 annual, a join into Altar Servers that was meant for Adult Choir) | the reviewer's value; the Family's stays visible beside it |
| **Ignore** | no change should be made | nothing (the baseline stands) |
| **Add** | a reviewer-created change not tied to any Family answer (add a Member to a different Ministry altogether) | the reviewer's value |

Edit is Accept with a value; it is shown as **Accepted (edited)**. Add
creates a new item that starts Reviewed (Accepted) under its creator's name,
shown as **Added by the parish**, and goes through the rest of the pipeline
like any other item; it may name the item it was added in place of, so the
history reads "Ignored; Added in its place: …". **Back to review** undoes a
decision while the item has not started its update (for an Added item, it
withdraws the item). Every decision takes an optional note; reopening an
ignored item requires one, as
[Manual census resolution](../reports/spec.md#manual-census-resolution)
already requires for Reopen.

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
roles configured as leaders in ParishSoft, as today.

| Stream | Ministry leader | Staff | Administrator |
| --- | --- | --- | --- |
| Census | nothing (never sees it) | Accept, Edit, Ignore, Back to review, Add; Entered by hand | everything Staff can, plus start an update run |
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
  that Ministry (for example "Accepted (edited) into Adult Choir").
- Leaders never Add: adding a Member to a Ministry the Family did not ask
  for is a parish decision. A leader who sees a mistaken request Ignores it
  with a note; Staff Add the right one.
- Leaders never see or record Entered by hand, Handled or the update phase,
  as today. A leader's Accept moves the item to To enter by hand for Staff.
- Add is not offered for the financial stream (a pledge the Family did not
  give is recorded through Open form, marked Entered by Staff, as today) or
  for cannot participate and Additional information, which record what the
  Family said.
- An Add on the census stream is checked by the same rules as a Family's
  answer for that field (validation, handling registry) and gets the same
  Automatic or By hand handling.
- Every action is enforced on the server and in the database where the
  stream's rules are enforced today, never only by hiding a button.

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
the next ParishSoft download holds the item's carried-forward value. Every
item whose ParishSoft value some ParishSoft read returns is confirmed this
way, whether it was written by the update run or entered by hand,
including pledges ([what is confirmable](#what-is-confirmable)). For an
item no read returns (data ParishSoft does not hold, and the few ParishSoft
values no read exposes), there is nothing to wait for, so **Handled** or
**Entered by hand** is final. Confirmed is the pipeline's only fully closed
stage for confirmable data.

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
review** and Edit to that value, which the next refresh Confirms. Ministry
leaders never see this stage. An item that ParishSoft's API wrote cannot
reach it: a written item is read back in the run, and a later difference is
a [Conflict](#baseline-refresh-re-evaluation).

Ministry requests have no value that can be wrong: a join or leave either
shows in the roster or does not. For them only Not seen in ParishSoft
applies.

## One working change set

Each Family has, per stream item, exactly **one** working change: the
Family's latest answer together with its review decision. There is no
second set of reviewer changes to reconcile against the Family's next
submission. The Family form follows the per-campaign setting **Families see
reviewed changes** ([the setting](#families-see-reviewed-changes-setting)).
While it is off, as it is until #1705 ships, the form shows the
Family its own last answers, as today, and resubmission follows
[the rule for the setting off](#resubmission-while-the-setting-is-off).
When it is on, the form shows the Family the baseline overlaid with the
reviewed result:

| Item's state | What the Family form shows for it |
| --- | --- |
| To review, Conflict | the Family's own latest answer, marked changed |
| Accepted | the Family's answer, marked changed |
| Accepted (edited) | the reviewer's value, marked changed |
| Ignored | the baseline value, not marked changed |
| Added by the parish | the reviewer's value, marked changed (a Ministry the parish added appears selected) |
| Updated in ParishSoft, Entered by hand, Entered incorrectly | the carried-forward value, marked changed |
| Confirmed | the baseline, which now holds the value, not marked changed |

The form never names the reviewer, says why a value differs from what the
Family typed, or mentions ParishSoft, as the
[Effective-value merge](../data/spec.md#effective-value-merge) already
requires. This changes that merge's first rule ("never an Admin review
edit"): the slice that ships this updates the data spec's merge to start
from the working change set. The financial and free-text streams show the
same way (an edited pledge shows the edited amount and frequency).

### Resubmission while the setting is off

This is the rule until #1705 ships and the setting is on. The form shows the
Family its own last answers over the baseline, exactly as the
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
   baseline): the item is **withdrawn** by the Family and leaves every list.
   If ParishSoft already holds the previous answer, it is Confirmed instead,
   as today's `resolved_upstream`. An earlier version already Entered by
   hand or Updated in ParishSoft is not undone: it stays in history with
   "Withdrawn by the Family after it was entered", shown on its stream's To
   enter by hand report, so Staff can decide whether to undo the entry.
4. **New** (no earlier version): Submitted, To review.
5. **Not presented** (a section not enabled, a Member out of scope, a
   Ministry no longer offered): the item is left as it is. Leaving something
   out of the form is never a withdrawal, as today.

Rule 1 is what each stream already does for its open items: a census
proposal resubmitted with the same value is replaced by a successor that
carries its decision, edited value and baseline; a Ministry request
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

This is the rule once **Families see reviewed changes** is on (after
[#1705](https://github.com/epiphany40223/parishkit/issues/1705)). When the
Family submits again, each item is compared with **what the form showed for
it** (the table above), not with the Family's earlier
answer:

1. **Unchanged** (the Family left the shown value as it was): the item keeps
   its stage and decision; nothing is recorded for it.
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
   list; an item already Entered by hand or Updated is left for the refresh
   to judge, with the withdrawal noted so Staff can undo the entry.
4. **New** (an item with no earlier version): Submitted, To review.

A review decision recorded while the Family had the form open does not
reject the Family's submit: the form's
[baseline reference](../data/spec.md#submission-concurrency) records each
item's version as shown, so an item the Family did not touch keeps the
newer decision, and an item the Family did change follows rule 2.
History keeps every version of every item, with who (the Family, or the
Staff member using Open form) and when. The same holds with the setting
off: a decision recorded while the Family had the form open is kept for an
unchanged item by rule 1 above.

### Families see reviewed changes setting

The reviewed overlay ships behind a per-campaign setting, **Families see
reviewed changes**, default off, which an Administrator switches on when
[#1705](https://github.com/epiphany40223/parishkit/issues/1705) has shipped.
It may be switched on mid-campaign ([decision 9](#decisions)); the setting
is a rollout switch, not a campaign boundary.

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
Edit and Ignore states this intent; the form still never names the reviewer
or gives a reason.

- Codes and links are unaffected: the change is in what the form prefills,
  not in any credential. No code or link is revoked, rotated, reissued or
  re-sent, as the
  [honor-sent-credentials](../data/spec.md#review-and-publication) rule
  requires.
- Until the setting is on, the form keeps today's merge, and resubmission
  follows [the rule for the setting off](#resubmission-while-the-setting-is-off).
  Every other part of the pipeline (stages, decisions, worklists, My review
  work, update and confirmation) does not depend on the setting.
- Switching it on mid-campaign: each submit is compared by the rule of the
  form the Family was shown. The form's
  [baseline reference](../data/spec.md#submission-concurrency) records
  whether it was drawn with the setting on, so a Family whose form was open
  across the switch is compared with its own previous answers (rule for the
  setting off) and is never refused for the switch itself. The switch is
  audited (who, when, on or off) and changes no stored answer or decision,
  so switching it off again returns every Family's form to its own answers.
- [#1705](https://github.com/epiphany40223/parishkit/issues/1705) comes after every Staff and Ministry leader slice. If it is ready
  before the current campaign closes it may be switched on then; if not,
  it waits, and nothing else depends on it.

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

1. **R = C: Confirmed.** ParishSoft holds the change, whatever stage the
   item was in (To review, Reviewed, Ready, To enter by hand, Entered by
   hand, Entered incorrectly, Updated in ParishSoft). This is how a by-hand
   entry is confirmed and how a change the office already made is retired
   without review. A To review item confirmed this way records "Already in
   ParishSoft" with no reviewer. An Ignored item is not re-evaluated by this
   rule: R = C on an Ignored item is recorded as "ParishSoft now matches the
   Family's answer" for the reviewer, and the item stays Ignored.
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
     An item already queued for update keeps its plan, and the plan meets
     the conflict at its own preflight check.
4. **Entity gone.** The Member or Family left the baseline (inactive,
   deceased, moved): the item keeps its stage and gains the shared
   [Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
   badge, as Roster changes to enter does today.

For a Ministry request, R, A and C are the Member's roster state for that
Ministry (in or not in), so rule 3 cannot apply: a join is Confirmed when
the Member appears in the roster, and otherwise unchanged.

Every equality uses the stream's canonical comparison (the census compare
rules, money in its typed form, the roster state), never a raw string
compare, so a formatting difference ParishSoft introduces is neither a
conflict nor an incorrect entry.

### What is confirmable

A hand-entered item is confirmable when some ParishSoft read returns its
field:

- **Confirmable**: Member names, prefix, suffix, birth date, gender, marital
  status, email, phones, language, deceased status and date, a Member
  leaving the household, a new Member appearing, Ministry joins and leaves
  (the roster lists), the upcoming pledge amount, the Family's email
  opt-out (below), and the Family's primary address
  ([address finding](#family-addresses)).
- **Not confirmable** (Entered by hand is final): share methods, the Family
  address block ParishSoft does not report as primary, the pledge frequency
  until its mapping is verified (below), and the non-ParishSoft streams.

The census email opt-out answer corresponds to the Family's ParishSoft
`sendNoMail` flag (Administrator, 2026-10-18, recorded on
[#1680](https://github.com/epiphany40223/parishkit/issues/1680): "good
enough"). ParishSoft's API cannot write it, so it stays To enter by hand,
but the Family search reads return it, so the refresh confirms an entered
opt-out against it. This changes only confirmation: the Family form's
baseline for the field stays unavailable until a slice that changes the
shared projection says otherwise.

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
| To enter by hand | accepted, the API cannot write it | "To do" for a By hand row; "Not yet" on Roster changes to enter |
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
accepts and marks it in one step, and forward migration 0128 (provisional
number) widens the review guard for it
([Automatic census review](../reports/spec.md#automatic-census-review)).
Census **Add** was cut from that slice (7th on the
[cut list](#risks-and-what-to-cut-first)): a change with no Family answer
behind it needs either a table of its own or a wider Family-submit guard, so
it has an issue of its own
([#1710](https://github.com/epiphany40223/parishkit/issues/1710)); Staff use
Open form meanwhile.

### Ministry stream

Stored state: `MinistryRequest` workflow (`new`, `in_progress`,
`resolved`, `closed_no_response`, …) and outcome (`joined`,
`leave_confirmed`, `declined`, `no_response`, `duplicate`, `other`), then
the Entered in ParishSoft tick on
[Roster changes to enter](../admin-portal/spec.md#roster-changes-to-enter),
then reconciliation resolving a request whose roster state is current.
The mapping onto stages is the
[existing state to stage](#existing-state-to-stage) table. A decided request is
not offered again in today's form; a Family that ticks it again is handled
by [rule 1](#resubmission-while-the-setting-is-off).

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
annual pledge and frequency, Ignored, or none after Back to review) and the
Entered by hand mark. The
[Financial stewardship detail](../reports/spec.md#financial-stewardship-detail)
report still lists every current pledge, and
[Pledges](../reports/spec.md#pledges) is the stream's
worklist and manual report. Staff can still change a pledge through Open
form (a whole new response marked Entered by Staff), and Edit (amount and
frequency, decision 4) is the review's own correction.

The item is one per Family: its current pledge answer. Its stage is derived
from its latest row, by one SQL selection the page, the counts and the
row guard share:

- no row, a latest row with no decision (Back to review), or a pledge answer
  changed since the latest row's response: **To review**;
- Ignored: **Ignored**;
- Accepted or Accepted (edited), not marked: **To enter by hand**;
- marked Entered by hand: **Entered by hand**.

"Changed" follows [rule 2 of the setting-off
rule](#resubmission-while-the-setting-is-off): a live response that answers
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
answers the financial questions again, keeping its history. Rules 3 and 4
need ParishSoft's upcoming-year pledge, which no read returns yet (#1704). An Entered by hand mark records ParishSoft's pledge for
the upcoming year when a read gives it; until #1704 adds that read the mark
records none, and #1704 reads such a mark against A, which is then no pledge.
Pledges and funds are read-only in ParishSoft's API, so the stream never has
Ready to update.

Gaps after #1686: confirmation (below, #1704).

**Pledge confirmation** (decision 5). ParishSoft's v2 `GET
offering/pledge/list` returns each pledge with `familyID`, `fundID`,
`pledgeDate`, `pledgeStartDate`, `currentPledgeAmount`, `initialPledgeAmount`,
`installmentAmount`, `numberOfMonths` and `fulfillmentMethod`, filterable by
Family, fund and date. ParishKit already reads it for the comparison period
(`source/giving.py` keeps the Family, fund, `currentPledgeAmount` and date).
The refresh also reads the **upcoming stewardship year** for the campaign's
configured pledge fund or funds, and maps a Family's ParishSoft pledge for
that year to the pipeline item:

- R for the amount is the sum of the Family's `currentPledgeAmount` on the
  configured funds dated in the upcoming year (none read as no pledge);
  A is that value when the Family answered (usually no pledge); C is the
  annual amount carried forward. The [refresh rules](#baseline-refresh-re-evaluation)
  then confirm the pledge, flag Not seen in ParishSoft, or flag Entered
  incorrectly (a typo at entry).
- Frequency is compared only after a human-run, read-only smoke check on the
  Test Family records how ParishSoft stores a weekly, monthly, quarterly and
  annual pledge (`installmentAmount`, `numberOfMonths`,
  `fulfillmentMethod`); until then frequency is not confirmable and the
  item is Confirmed on the amount alone.
- Share methods have no ParishSoft read; Entered by hand is final for them.

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
Ignored; Handled → Handled. A response that answers the other way
withdraws the item, and giving it again later is a new item, To review; one
that leaves the answer out changes nothing
([rule 1 to 5](#resubmission-while-the-setting-is-off)).

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
0126); superseded / withdrawn → history. The
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
   Baseline (where a stream has one), Family's answer, Reviewer's value
   (where a stream has Edit), ParishSoft now (on Conflict, Entered incorrectly and Not seen rows),
   Stage, Submitted, Last decision (who, when, note), Actions.
3. The shared export pane below the table, as on every report
   ([#1679](https://github.com/epiphany40223/parishkit/issues/1679)).

No summary tiles or charts sit above the table on a review worklist: the
counts are in the Stage filter's choices and on My review work. A stream's
statistics stay on its report page. An **Add** button sits in the selection
bar for readers allowed to add on that stream, and opens the stream's own
value editor (for a Ministry, a Member and a Ministry picker scoped to the
reader).

### Individual actions

Each row offers only the actions its stage and the reader's
[role](#role-specific-actions) allow, as
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) icons
(hidden, not disabled, by role):
Accept (check), Edit (pencil; opens the stream's own value editor: for a
pledge the amount and frequency, for a Ministry request the Ministry list
scoped to the reader), Ignore (eye struck through), Back to review (arrow
turning back), and, in the update phase, Entered by hand / Handled (check in
a box). Each takes the optional note in the shared confirmation dialog.

### Selected-state icons

A row's decision is shown on its action icons, not only in the Stage
column: Accept, Ignore and Entered by hand / Handled follow the
[selected state](../ui-conventions/spec.md#row-actions-and-confirmation) rule, and Back to review is the row's undo.
Ministry requests and Roster changes to enter use it too.

### Bulk actions

Selection boxes and the bulk bar follow the
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) rules (several rows, by role); today Census changes
breaks the box rule, giving a box only to rows offering a review decision,
so the By hand rows have actions but no box. The bulk bar offers Accept,
Ignore, Back to review and Entered by hand / Handled
([prerequisite gates](../ui-conventions/spec.md#prerequisite-gates)), with
the exact count in the dialog. Edit and Add are one item at a time. The
typical flow the Administrator described is a handful of Edits, then select
all on the page and Accept; pledges work the same way.

### Concurrency

Every decision is checked against the item version the row showed, as
census review and the Ministry follow-up forms already are; a row someone
else (another reviewer, or the Family resubmitting) changed first is left as
it is and the notice says so. Bulk decisions are all or none.

## Update phase

The update phase has two parts that run independently, each started by a
person, each reading only Reviewed items.

### Automatic update

Only census changes the [handling registry](../data/spec.md#proposed-changes)
marks API-writable can be updated automatically: Member contact fields
(names, birth date, language, email, phones; gender stays by hand while
[#1616](https://github.com/epiphany40223/parishkit/issues/1616) is open).
Family addresses stay by hand ([Family addresses](#family-addresses)). The
run is the existing
[Publish to ParishSoft](../reports/spec.md#publish-to-parishsoft) page,
renamed **Update ParishSoft**, Administrator-only, still behind the
`parishsoft_publication` switch. Its result stage is Updated in ParishSoft;
the final refresh then moves each item to Confirmed by the
[re-evaluation rules](#baseline-refresh-re-evaluation).

### Manual handling reports

Every stream has a **To enter by hand** (or To handle) report: the
worklist filtered to that stage, with Entered by hand / Handled as its row
and bulk action, exported through the shared export component in CSV, XLSX
and PDF. The same report has Stage choices for Entered incorrectly and Not
seen in ParishSoft. Roster changes to enter is the Ministry stream's report
today and becomes the shared component's first user; Census changes'
*By hand* view and a new financial "Pledges" report follow.

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
  primary address (`families/search` in `source/corpus.py`, `GET
  families/{familyId}` in `parishsoft_households.py`), so there is no
  further read to add.
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

What follows: addresses stay **To enter by hand** for now, but they become
**confirmable** in part once a human-run smoke test on the Test Family
writes a known home and a different mailing address, reads the Family
back, records which block (and which `primaryAddress` value) the primary
fields follow, and restores the Family. With that mapping, the refresh
confirms (or flags Entered incorrectly for) the block ParishSoft reports as
primary, comparing street lines, city, state and postal code; the other
block stays unconfirmable. The same mapping would let the Family form show
the primary address as that block's baseline. Writing addresses through the
API stays out until a write is shown to apply, since the non-primary block
could still not be verified.
[#1688](https://github.com/epiphany40223/parishkit/issues/1688) carries the
smoke test and
[#528](https://github.com/epiphany40223/parishkit/issues/528) records the
finding.

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
in order. New schema follows the
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
  [#1679](https://github.com/epiphany40223/parishkit/issues/1679).

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
3. May Ministry leaders move a request to another Ministry? **Only into a
   Ministry they also lead; Staff into any.** With Replace dropped, this is
   the Ministry request's Edit ([role-specific actions](#role-specific-actions)).
4. Does a pledge need review? **Reviewed, with bulk Accept**, and pledges
   can also be **edited** (amount and frequency), so Staff fix a mistyped
   amount before entering it in ParishSoft.
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
   closes it is switched on then, and if not, that is fine.** The
   Administrator expects Staff to make only consistency fixes and the
   corrections a Family asked for, so Families will not be surprised by
   what they see ([the setting](#families-see-reviewed-changes-setting)).

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
   [resubmission rule](#resubmission-while-the-setting-is-off).
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
    Ready items, commands renamed with no aliases, and the Family address
    smoke test.
13. [#1705](https://github.com/epiphany40223/parishkit/issues/1705)
    Families see reviewed changes: the working change set in
    the Family form, the resubmission rule, the per-campaign setting.
    Lowest priority.

## Rolling out during this campaign

The Administrator's goal (2026-10-18) is the Staff and Ministry leader
pipeline in use before the current campaign closes, without harming the
existing database or the running campaign. Every slice except
[#1705](https://github.com/epiphany40223/parishkit/issues/1705) ships to the
current campaign; #1705 comes last and ships when ready
([the setting](#families-see-reviewed-changes-setting)). This section is how
that is done safely.

### Data integrity

- **Forward-only schema.** Every schema change is a new forward migration
  with a frozen SQL file, following the
  [post-launch schema policy](../operations/spec.md#post-launch-schema-policy),
  never an edit to an applied migration. During the campaign the changes
  are additive only: new tables (decision rows for Ministry Edit, Add,
  financial and cannot-participate items, confirmation and mark records)
  and new nullable columns. No column is dropped, renamed or narrowed, and
  no CHECK is tightened over existing rows, before the campaign closes.
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
| Census | `unreviewed` + `pending` | To review (By hand rows too: today's *To do*; Entered by hand on them still accepts and marks in one step, [decision 2](#decisions)) |
| Census | `unreviewed` or `approved` + `conflict` | Conflict (an approval stays visible, not in force) |
| Census | `approved` + `pending`, `api` / `manual` | Ready to update / To enter by hand; Accepted (edited) when an edited value is set |
| Census | `approved` + `failed` | Ready to update, with the failure shown |
| Census | `queued` / `published` | Updating / Updated in ParishSoft |
| Census | `resolved_external` / `resolved_upstream` | Entered by hand / Confirmed |
| Census | `ignored` (any open execution) | Ignored |
| Census | `superseded`, `cancelled` | history |
| Ministry | `new`, `assigned`, `in_progress` | To review |
| Ministry | `resolved` + `joined` or `leave_confirmed`, not ticked / ticked | To enter by hand / Entered by hand |
| Ministry | resolved by refresh reconciliation (roster current) | Confirmed |
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
- **The Family form does not change** until #1705's setting is on. Each slice
  that touches the submit path proves it with a render comparison of the
  Family form against the base, and keeps the form's baseline reference
  format, so a Family whose form was open across a deploy submits without a
  stale-form refusal and is judged by [rule 1 to
  5](#resubmission-while-the-setting-is-off).
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

- **The Family submit path.** The [rule for the setting
  off](#resubmission-while-the-setting-is-off) changes census, Ministry and
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
   [#1710](https://github.com/epiphany40223/parishkit/issues/1710).

Never cut: #1691, #1681, #1690, #1683, #1684 and #1687, which are what
Staff and Ministry leaders use.
