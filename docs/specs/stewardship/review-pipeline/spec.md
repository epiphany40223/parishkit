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
[as built](#refresh-checks)), and the automatic update on Update
ParishSoft
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
edit") while the setting is on: the census slice of #1705 updates the data
spec's merge to start from the working change set. The financial stream
shows the same way (an edited pledge shows the edited amount and
frequency). The [reviewed Family form](#reviewed-family-form) is the design
that #1705 builds.

### Resubmission while the setting is off

This is the rule until #1705 ships and the setting is on. The form shows the
Family its own last answers over the baseline, exactly as the
[Effective-value merge](../data/spec.md#effective-value-merge) does today, so
nothing a Family sees changes. When the Family submits again, each item is
compared with **the Family's previous answer for that item** (the answer
of the item's current version), never with a review result the Family has
not been shown. Until #1705 the two are always the same; once a reviewed
form has been used and the setting is switched off again, the comparison
value is the item's last sent answer
([after switching off](#family-resubmission)):

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

   The financial stream places rules 3 and 4 differently for a pledge
   ParishSoft already holds
   ([Financial stream](#financial-stream), #1753).
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
   list; an item already Entered by hand or Updated is left for the refresh
   to judge, with the withdrawal noted so Staff can undo the entry. An item
   added by the parish is never withdrawn by the Family
   ([Ministry changes added by the parish](#what-the-reviewed-form-shows)).
4. **New** (an item with no version in force: none before, or only
   Confirmed, withdrawn or replaced ones): Submitted, To review. The pledge
   is the exception: it is one item per Family that stays the Family's
   current answer after Confirmed, so a Confirmed pledge is still in force
   and is judged by rules 1 to 3.
5. **Not presented** (a section not enabled, a Member out of scope, a
   Ministry no longer offered): the item is left as it is, as with the
   setting off.

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
Staff member using Open form) and when. The same holds with the setting
off: a decision recorded while the Family had the form open is kept for an
unchanged item by rule 1 above.

**After switching off.** A response from a reviewed form may have sent a
reviewer's value (or the baseline, for an Ignored census field) back
unchanged, so its answer for an item can differ from the item's own
Family's answer. Once the setting is off, the setting-off form shows each
item's **last sent answer** (the latest response's answer for it, which is
the shown value that response sent), and
[the setting-off rule](#resubmission-while-the-setting-is-off) compares the
next answer with that, not with the item's own Family's answer. So a Family
that leaves such an item as shown keeps its decision after a rollback: an
Accepted (edited) item stays Accepted (edited), and an Ignored census field
stays Ignored. Each stream's matching (the census effective-value index
of #1683, the "Family asked again" match of #1684, the pledge stage
selection) reads the last sent answer the same way. This is reachable only after #1705, and only
when an Administrator switches the setting off again; the Family then sees
only values it sent itself, never an edit it was not shown.

### Families see reviewed changes setting

The reviewed overlay ships behind a per-campaign setting, **Families see
reviewed changes**, default off, which an Administrator switches on when
[#1705](https://github.com/epiphany40223/parishkit/issues/1705) has shipped.
It may be switched on mid-campaign ([decision 9](#decisions)); the setting
is a rollout switch, not a campaign boundary.

It is an audited switch, like
[Family portal maintenance](../admin-portal/spec.md#family-portal-maintenance),
not a campaign configuration value: an Administrator changes it on the
Family portal availability page (or with `pk-admin`), each change is an
audit event naming the campaign, the actor and on or off, and the
campaign's newest such event is its state, so a campaign without one is
off. A change locks the campaign row before reading the current state, so
two Administrators switching at once cannot lose one change, and web
processes reuse the state they read for up to 3 seconds, as the maintenance
switch does; a stale read is harmless, because each form records the rule
it was drawn with. Keeping it out of the campaign configuration means
switching it makes no configuration version and changes no form-definition
digest, so no open form is refused for it. Only live forms follow it: a Testing form always
shows the Family its own Testing answers, since Testing responses never
enter the pipeline.

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
- Until the setting is on, the form keeps today's merge, and resubmission
  follows [the rule for the setting off](#resubmission-while-the-setting-is-off).
  Every other part of the pipeline (stages, decisions, worklists, My review
  work, update and confirmation) does not depend on the setting.
- Switching it either way, mid-campaign or while a form is open, follows
  [concurrency and switching](#concurrency-and-switching).
- [#1705](https://github.com/epiphany40223/parishkit/issues/1705) comes after every Staff and Ministry leader slice. If it is ready
  before the current campaign closes it may be switched on then; if not,
  it waits, and nothing else depends on it.

### Reviewed Family form

This is the design of
[#1705](https://github.com/epiphany40223/parishkit/issues/1705): what a
Family sees when it signs in again with
[the setting](#families-see-reviewed-changes-setting) on, how the form
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
changes none of them while the setting is off.

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
| Proposed Member | the Member's page with the carried-forward names and details | not shown; its absence from the submit is rule 1 (it stays Ignored), and a Member added again has a new key, so it is a new item | a current Member, as ParishSoft has them |
| Ministry join | the carried-forward Ministry selected and listed under "Joining:" (a join added by the parish too, [below](#what-the-reviewed-form-shows)) | not selected | a current Ministry, "Continue in this ministry" |
| Ministry leave | "Stop participating in this ministry" selected, in the attention colour (a leave added by the parish too, below) | "Continue in this ministry" | not listed (no longer a current Ministry) |
| Pledge | the carried-forward annual pledge and frequency, with the Family's own share methods and Cannot contribute answer | the Family's own latest answer | the carried-forward answer (ParishSoft holds its amount) |
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
  so Staff see it and can use Back to review, which withdraws an Added
  item. A change the parish added stays parish work until Staff decide.
- **A moved, deceased or death-date request already entered** (Entered by
  hand or Updated in ParishSoft, not yet Confirmed) is presented in its
  state with the setting on, and an unchanged resubmission is rule 1. This
  differs from the setting-off rule, where an entered terminal request is no
  longer presented and asking again makes a new request
  ([#1683](https://github.com/epiphany40223/parishkit/issues/1683)), because
  the reviewed form shows the Family the carried-forward state.
- **Cannot participate and Cannot contribute.** Unchecking either restores
  the shown values of the answers it hides, not the Family's older ones.
- **Not yet in this design:** census Add
  ([#1710](https://github.com/epiphany40223/parishkit/issues/1710)); once it
  lands, an Added census change shows like any decided census field.

#### Shown items

When the form is drawn with the setting on, the form's
[baseline reference](../data/spec.md#submission-concurrency)
(`FamilyFormBaseline`) records, beside today's version references:

- `reviewed_view`: true, drawn with the setting on (false for every form
  drawn with it off, and for every baseline made before the column); and
- `shown_items`: one entry per census, Ministry or pledge item in force for
  the Family, the pledge always included, Confirmed or not; a census or
  Ministry item that is Confirmed, withdrawn or replaced has none, since
  the form shows the plain baseline for it. Each entry holds the stream,
  the item's identity, the stream's item version (the one the
  [worklist's concurrency check](#concurrency) already uses), the immutable
  decision row in force (or none), and which value was shown (the Family's
  answer, the reviewer's value, or the baseline). Cannot participate,
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
is additive (nullable, or false by default) and changes no existing row.

The submit then applies [the resubmission rule](#family-resubmission) for
the form's own `reviewed_view`: a form drawn with the setting off is judged
by [the setting-off rule](#resubmission-while-the-setting-is-off) whatever
the setting is at submit, and one drawn with it on by the reviewed rule,
even if the setting was switched off since. For each item:

- census and Ministry make no new row for an unchanged item, as the
  entered-census and "Family asked again" cases of the setting-off rule
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
  Family's own answer in every stage, so their setting-off comparison is
  already the reviewed one and they change nothing.

#### Concurrency and switching

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
  decision was recorded or the setting switched meanwhile (rule 1 then
  keeps the newer decision). Only the affected fields take fresh shown
  values, and they are listed in the review as today.
- **Another session's submission** advances the Family's effective version
  and is refused for review, as today.
- **Switching the setting while the form is open** is never a refusal: the
  form is judged by the rule it was drawn with, recorded in its
  `reviewed_view`, so a form open across a switch on is judged by the
  setting-off rule, and one open across a switch off by the reviewed rule.
  The switch changes no stored answer or decision. After a switch off, the
  next form shows each item's last sent answer and compares with it
  ([after switching off](#family-resubmission)).

#### Privacy and wording

- The form payload for an item carries only what it carries today: the
  value and the changed and conflict markers. It never carries the stage,
  the decision, a note, a reviewer's or Ministry leader's name or role, a
  decision time, "Added by the parish", a refresh outcome or any mention of
  ParishSoft, and the Family pages render none of them. A test lists the
  payload's keys per field and fails on any other, and scans the rendered
  pages for the pipeline's words.
- No new Family-facing text: the existing marker ("Your updated value"),
  Ministry lists, Review page, welcome notice, Thank You page and receipt
  are unchanged, because the Administrator's stated intent limits Staff
  changes to consistency fixes and corrections the Family asked for
  ([the setting](#families-see-reviewed-changes-setting)). The receipt
  still carries no answer values.
- Staff-facing text: while the setting is on, each census, Ministry and
  financial worklist's page help adds, in these words: "Families see
  reviewed changes is on. A Family that signs in again sees the values you
  accepted, edited or added, and ParishSoft's value for a change you
  ignored. Edit a Family's answer only to make it consistent (for example
  "St." for "Street") or to make a correction the Family asked for." The
  Edit, Add and Ignore dialogs add one sentence: "The Family will see this
  value the next time it signs in." (Edit, Add; for a Ministry join Add:
  "The Family will see this Ministry selected the next time it signs in. If
  it unticks it, this row will say so."; for a Ministry leave Add: "The
  Family will see 'Stop participating' chosen for this Ministry the next
  time it signs in. If it chooses to continue, this row will say so.") or "The Family will see ParishSoft's
  value the next time it signs in." (Ignore, on Ministry requests and on
  census fields whose ParishSoft value the form shows).
- Open form shows Staff the same reviewed form, with the same rules, and
  records the Staff member as the actor, as today.

#### Reviewed form accessibility

The reviewed form adds no control, page or message. A shown value uses
the same field, the same programmatic state (a selected Ministry is a
checked control, "Stop participating" the selected choice) and the same
non-colour change marker (icon and text label) as a Family's own answer,
so assistive technology reads a reviewed value exactly as it reads one the
Family typed. Nothing appears, disappears or moves while the Family works
([no layout shift](../ui-conventions/spec.md#no-layout-shift)), and the
form's existing keyboard, focus and accessible-name checks run on a reviewed form too.

#### Credentials and the submit path

- No Family code or link is created, revoked, rotated, reissued or
  re-sent; the switch and the migration touch no credential or session.
- The submit stays one transaction under today's locks and lock order,
  with today's validation, refusals, receipt outbox and fact hint. The
  reviewed rule only changes which comparison value each stream's existing
  derivation uses, and reads only Family-scoped rows already locked by the
  work order; there is no provider call.
- With the setting off, the Family form, its payload and the submit's
  outcomes are unchanged: each slice proves it with a render and payload
  comparison against the base and keeps the baseline reference format, so a
  form open across the deploy submits without a stale-form refusal.

#### Reviewed form delivery

Small pull requests after every Staff and leader slice, each behind the
setting. The switch itself ships in the last one, so the setting cannot be
turned on while any stream still draws the setting-off form; until then no
form records `reviewed_view` true and nothing a Family sees changes.

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
   and #1689. Tests: the unit tables; a PostgreSQL test that a Testing or
   setting-off form records nothing.
3. **Census.** The census reader; the census form draws from the working
   change set (the
   [Effective-value merge](../data/spec.md#effective-value-merge) gains its
   setting-on form); the census derivation compares with the shown value,
   and with the last sent answer after a switch off; a forward migration
   lets the completeness check admit an unchanged item's absence. Needs
   slice 2. Tests (PostgreSQL), with the reviewed rule forced on in the
   test since the switch is not built yet: every census stage times
   unchanged, changed, set back to ParishSoft's value and new; Ignored left
   as shown stays Ignored; an entered terminal request left as shown; a
   decision recorded while the form is open kept for an untouched field; a
   form drawn with each rule judged by it; a refresh while open keeping an
   untouched field's shown value; on, unchanged resubmit of an edited
   field, off, unchanged resubmit, still Accepted (edited); setting-off
   payload byte-identical to the base; the form-load and submit query
   counts.
4. **Ministry.** The Ministry reader; choices drawn from the working change
   set (edited and added requests included); the derivation compares with
   the shown selection; completeness check as for census. Needs slice 2.
   Tests as for census, plus an edited request left as shown, unselected,
   and with its original Ministry selected (rule 2 on the same request);
   an added join unticked ("Family unticked", decision kept); an added
   leave switched to continue ("Family chose to continue", decision kept);
   the off round trip for an edited request.
5. **Financial.** The pledge reader; the pledge drawn from the working
   change set; the pledge stage selection reads `shown_items` in every
   stage; #1753's submit-time placement only for a changed pledge. Needs
   slice 2. Tests: every pledge stage, Confirmed included, times unchanged,
   changed and edited to zero; a Confirmed edited pledge resubmitted
   unchanged stays Confirmed; the share methods kept; a refresh check after
   an unchanged resubmission still counting; the off round trip.
6. **Switch, Staff wording and browser coverage.** The audited per-campaign
   switch on Family portal availability and its `pk-admin` command
   (Administrator only, CSRF, campaign-row lock, audit event with no
   values); the page help and dialog sentences above; browser tests in
   Chromium, Firefox and WebKit of a reviewed form (shown values, markers,
   Ministry lists, Review page, unchanged resubmission); the privacy test
   of the payload keys and rendered words; a pk-local checklist for the
   Administrator, who then decides when to switch the setting on. Needs
   slices 3 to 5. Tests also cover roles, CSRF, the audit rows, the state
   from the newest event, and that switching touches no credential.

#### Reviewed form open questions

Each has a default this design already follows; the Administrator may
change any of them on
[#1705](https://github.com/epiphany40223/parishkit/issues/1705).

1. **Ignored with no showable baseline** (a pledge, cannot participate,
   cannot attend, Additional information, an address or email opt-out):
   show the Family's own latest answer, unmarked. *Alternative:* show the
   field empty, which would force a Family to retype a required pledge and
   would read a cleared answer as a withdrawal.
2. **Where the switch lives:** an audited switch on Family portal
   availability plus `pk-admin`, outside the campaign configuration, so
   switching never refuses an open form. *Alternative:* a Campaign settings
   value, which is locked once the campaign is live and would make a
   configuration version.
3. **Testing mode:** only live forms follow the setting; a Testing form
   shows its own Testing answers, since Testing responses never enter the
   pipeline. The reviewed form is rehearsed in pk-local with fixture data
   instead.
4. **The change marker's label for a reviewer's value:** keep "Your updated
   value". *Alternative:* a neutral "Updated value" on every marker, which
   changes the form for every Family, setting on or off.
5. **An ignored leave** (a leader recorded "Stayed in the Ministry"): show
   "Continue in this ministry", the parish's decision, in line with the
   Administrator's intent that Staff change only what the Family asked for.
6. **Switching off after on:** an emergency rollback only; no stored answer
   or decision changes, and the next form shows each item's last sent
   answer and compares with it, so nothing a Family left as shown is
   reopened ([after switching off](#family-resubmission)). *Alternative:*
   show the item's own Family's answer again, which would reopen every
   edited item the Family had sent back unchanged.
7. **Staff wording** while the setting is on: the page help with the
   Administrator's intent and one dialog sentence, as above.
   *Alternative:* page help only.
8. **A Ministry join or leave added by the parish:** show it as any join or
   leave with the usual marker, and treat the Family undoing it as "Family
   unticked" or "Family chose to continue" on the request, keeping its
   decision, never a withdrawal. *Alternatives:* do not present Added
   requests (the Family then never sees parish work it may have asked for by
   phone), or let an untick withdraw it (parish work could disappear
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
     (An Updated in ParishSoft item instead goes back to To review:
     [refresh checks](#refresh-checks).)
     An item already queued for update keeps its plan, and the plan meets
     the conflict at its own preflight check.
4. **Entity gone.** The Member or Family left the baseline (inactive,
   deceased, moved): the item keeps its stage and gains the shared
   [Not in ParishSoft](../ui-conventions/spec.md#members-and-families-no-longer-in-parishsoft)
   badge, as Ministry requests' roster rows do today.

For a Ministry request, R, A and C are the Member's roster state for that
Ministry (in or not in), so rule 3 cannot apply: a join is Confirmed when
the Member appears in the roster, and otherwise unchanged.

Every equality uses the stream's canonical comparison (the census compare
rules, money in its typed form, the roster state), never a raw string
compare, so a formatting difference ParishSoft introduces is neither a
conflict nor an incorrect entry.

### Refresh checks

How [#1689](https://github.com/epiphany40223/parishkit/issues/1689) builds
these rules. An item still open (a census change To review, Conflict, Ready,
To enter by hand or Ignored; a Ministry request To review) keeps today's
[refresh reconciliation](../data/spec.md#parishsoft-refresh-reconciliation),
which already stores ParishSoft's value or resolves the request, with three
corrections: a census change's C is the reviewer's value when it was
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
row is final (a census change Entered by hand or Updated in ParishSoft) or
decided (a Ministry request a person accepted or ignored) gets a **refresh
check** instead (a census change a later Family response replaced does not:
it is history, below): an immutable record, written in the promotion transaction,
of the outcome (Confirmed, unchanged, Entered incorrectly, or ParishSoft now
matching an Ignored request), what ParishSoft held, and whether the refresh
was full. A new check is written only when the outcome changes (or when an
Entered incorrectly item's ParishSoft value changes again), and an unchanged
one on a marked item only on a full refresh, until Not seen is reached. The
stage is derived from the item's latest check, never stored on the item, and
a Confirmed item is not checked again, so its ParishSoft value is the one
that confirmed it. A Ministry request Confirmed this way offers no action, so
the review service refuses Back to review on it; its revision guard does not
read the checks, and a request reopened anyway would be resolved as
Confirmed by the next refresh, as an open request whose roster is current is
today. Roster changes to enter no longer counts it as not yet entered.
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
row's Entered by hand and Back to review icons (one by one or in bulk, each
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
- **Back to review** reopens the change (a resolution row too): it is To
  review again, open to today's reconciliation, with its edited value kept
  and no decision in force. The reviewer then Edits it to ParishSoft's value,
  which the next refresh Confirms. A refresh before the Edit finds ParishSoft
  differing from both the stored value and the reviewer's, so the change is
  a Conflict; an Edit then accepts ParishSoft's value and the change waits
  To enter by hand (or Ready), since reconciliation looks again only when
  ParishSoft's value changes, so the dialog promises no confirmation.

Both are refused unless the change's latest refresh check, at its current
version, says Entered incorrectly. A final change the Family's later
response replaced (the same field answered again, which makes a change of
its own) is history: it gets no refresh check, is placed by its execution
alone, and takes neither fix. The census guard admits a change leaving
Entered by hand only this way, with its paired resolution row. Migration 0133
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
  source worker at fenced, pinned data, with that check. A quick update
  records nothing for such a change, since its change list could lag the
  write. The row says "ParishSoft no longer holds the update." under its
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
  incorrectly. The mailing address stays final when Entered by hand. The
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
- **Not confirmable** (Entered by hand is final unless noted): share
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
accepts and marks it in one step, and forward migration 0128 (provisional
number) widens the review guard for it
([Automatic census review](../reports/spec.md#automatic-census-review)).
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
from its latest row and its latest refresh check (below), by one SQL
selection the page, the counts and the row guard share:

- no row, a latest row with no decision (Back to review), or a pledge answer
  changed since the latest row's response: **To review**, or **Confirmed**
  ("Already in ParishSoft", no reviewer; the
  [page's](../reports/spec.md#pledges) words at $0.00 differ) or
  **Conflict** when a refresh found so, or Confirmed at the Family's submit
  (rules 3 and 4, below);
- Ignored: **Ignored**;
- Accepted or Accepted (edited), not marked: **To enter by hand**, or
  **Confirmed** or **Conflict** (keeping the decision visible but not in
  force) when a refresh found so;
- marked Entered by hand: **Entered by hand**, or **Confirmed** or
  **Entered incorrectly** when a refresh found so.

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
answers the financial questions again, keeping its history. Each live
response records ParishSoft's upcoming-year pledge at its submit (below),
which places rules 3 and 4 differently from the general rule
([#1753](https://github.com/epiphany40223/parishkit/issues/1753)): a new or
changed pledge whose annual amount equals the amount ParishSoft held at
that submit has nothing to enter, so it is **Confirmed** at once with no
reviewer, the same as a refresh finding R = C (the
[page's](../reports/spec.md#pledges) "Already in ParishSoft", or
its $0.00 words). That replaces rule 3's withdrawal, because the pledge item
is the Family's current answer and stays listed, and rule 4's To review for
a first answer ParishSoft already holds, so no reviewer accepts a pledge
that would then be entered twice. If any earlier version of the Family's
pledge was ever marked Entered by hand (even one later reopened), the new
answer stays To review instead (its row says the Family changed its pledge),
so Staff see it and can undo the entry, and the refresh judges it; rule 3's
"Withdrawn by the Family after it was entered" note is not shown. Without a
recorded amount the pledge is To review, as before. Pledges and funds are
read-only in ParishSoft's API, so the stream never has Ready to update.

**Pledge confirmation** (decision 5). ParishSoft's v2 `GET
offering/pledge/list` returns each pledge with `familyID`, `fundID`,
`pledgeDate`, `pledgeStartDate`, `currentPledgeAmount`, `initialPledgeAmount`,
`installmentAmount`, `numberOfMonths` and `fulfillmentMethod`, filterable by
Family, fund and date. The refresh already reads every pledge of the
campaign's configured funds and keeps those whose `pledgeStartDate` falls in
the **upcoming stewardship year** (the campaign's financial period, its
`fund_duids`) or in the comparison period (`source/giving.py` keeps the
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
- R = C makes an accepted pledge **Confirmed**, keeping its Accepted or
  Accepted (edited), with nothing offered; on an Ignored pledge it only
  notes "ParishSoft now matches the Family's answer". On a To review pledge
  it is **Confirmed** with no reviewer, its row saying "Already in
  ParishSoft", with nothing offered: like a decided pledge, it is Confirmed
  on the amount alone (frequency below; share methods have no read). A
  marked pledge whose R differs from C and from the amount
  recorded at the mark is **Entered incorrectly**, shown with ParishSoft
  now beside the Reviewer's value and no action until
  [#1737](https://github.com/epiphany40223/parishkit/issues/1737); one
  still at the mark after the configured number of full refreshes is
  flagged **Not seen in ParishSoft**.
- R differing from A and from C on a To review or To enter by hand pledge
  is a **Conflict**: it returns to review with ParishSoft now beside the
  Family's answer and any decision behind it shown as "Earlier: Accepted,
  not in force.", offering Accept, Edit and Ignore. A later refresh that
  finds R back at A returns it to its stage (To review, or To enter by
  hand with its decision in force); one that finds R = C Confirms it.
- **Entered by hand** records ParishSoft's upcoming-year amount from the
  current snapshot when its giving read is proven complete (0134 lets the
  pledge review guard admit it, and checks it against the snapshot);
  otherwise the mark records none, and the refresh reads it as A.
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
a box). A census New Member Entered by hand also offers Correct (or Add) the
ParishSoft Member ID (pencil, after its selected mark;
[refresh checks](#refresh-checks)). Each takes the optional note in the
shared confirmation dialog.

### Selected-state icons

A row's decision is shown on its action icons, not only in the Stage
column: Accept, Ignore and Entered by hand / Handled follow the
[selected state](../ui-conventions/spec.md#row-actions-and-confirmation) rule, and Back to review is the row's undo.
Ministry requests uses it too.

### Bulk actions

Selection boxes and the bulk bar follow the
[row action](../ui-conventions/spec.md#row-actions-and-confirmation) rules (several rows, by role); today Census changes
breaks the box rule, giving a box only to rows offering a review decision,
so the By hand rows have actions but no box. The bulk bar offers Accept,
Ignore, Back to review and Entered by hand / Handled
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
[refresh check](#refresh-checks).

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
    Ready items, with the confirmation and run it lacked, and the Family
    address smoke test; the renamed commands with no aliases moved to
    [#1755](https://github.com/epiphany40223/parishkit/issues/1755).

13. [#1705](https://github.com/epiphany40223/parishkit/issues/1705)
    Families see reviewed changes: the working change set in
    the Family form, the resubmission rule, the per-campaign setting, in
    six pull requests ([reviewed form delivery](#reviewed-form-delivery)).
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
| Census | `queued` / `published` | Updating / Updated in ParishSoft (Confirmed by its latest [refresh check](#refresh-checks)) |
| Census | `resolved_external` / `resolved_upstream` | Entered by hand (Confirmed or Entered incorrectly by its latest [refresh check](#refresh-checks)) / Confirmed |
| Census | `ignored` (any open execution) | Ignored |
| Census | `superseded`, `cancelled` | history |
| Ministry | `new`, `assigned`, `in_progress` | To review |
| Ministry | `resolved` + `joined` or `leave_confirmed`, not ticked / ticked | To enter by hand / Entered by hand |
| Ministry | resolved by refresh reconciliation (roster current), or accepted and Confirmed by its latest [refresh check](#refresh-checks) | Confirmed |
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
   [#1710](https://github.com/epiphany40223/parishkit/issues/1710), which
   built it.

Never cut: #1691, #1681, #1690, #1683, #1684 and #1687, which are what
Staff and Ministry leaders use.
