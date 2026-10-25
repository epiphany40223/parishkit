"""The shared review worklist: one table every stream's review page uses.

The [review pipeline](docs/specs/stewardship/review-pipeline/spec.md#review-worklist)
gives every Family-submitted stream one review page:
``components/review-worklist.html`` draws it from what ``build`` returns
here, on top of the shared Admin table, ``table-selection.html`` and the
confirmation dialog. This module decides, for one reader, what each row
offers; ``parishkit.stewardship.pipeline`` holds the words, the stages and
the role-action table, so nothing here restates them.

- ``build`` turns a stream's rows (each with its ``Placement``) into the
  template's ``worklist``: the row icons (offered or selected), the
  selection boxes and their kinds, the bulk buttons, Add and the Stage
  filter's choices.
- ``read_decision`` parses the POST a row icon, the bulk bar or Add sends,
  the one contract every stream's view and ``pk-admin`` share (#463).
- ``DecisionService`` is the interface each stream's decisions implement;
  ``record`` calls it and words a race as the page's notice.

What a reader is offered never decides what is allowed: each stream's
service and its database guards refuse an action the reader may not take.
"""

from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.utils.html import format_html

from parishkit.stewardship.pipeline import (
    TERMINAL_STAGES,
    TERMS,
    Action,
    Decision,
    Stage,
    Stream,
    allowed_actions,
    may_add,
    role_actions,
    stage_actions,
    undo_needs_note,
    undo_stage,
)
from parishkit.stewardship.web.refusals import Refusal

# The row actions in their one order, so an action keeps its place from row
# to row (UX conventions, "Row actions and confirmation"); ``pk-admin`` lists
# them so too. A row never draws a Back to review icon of its own: its
# pressed icon is the undo (UX conventions, "Decision toggles", #2131), so
# ``row_icons`` skips it; ``pk-admin`` and the bulk bar keep it.
ROW_ORDER = (
    Action.ACCEPT,
    Action.EDIT,
    Action.IGNORE,
    Action.BACK_TO_REVIEW,
    Action.WITHDRAW,
    Action.ENTERED_BY_HAND,
    Action.HANDLED,
)
# The bulk bar's decisions. Edit and Add are one item at a time (spec,
# "Bulk actions"); starting an update is the update page's, not a row's.
BULK = (
    Action.ACCEPT,
    Action.IGNORE,
    Action.BACK_TO_REVIEW,
    Action.WITHDRAW,
    Action.ENTERED_BY_HAND,
    Action.HANDLED,
)
# Most items one bulk decision covers; the page offers the bulk bar only
# while it shows at most this many rows.
MAX_BULK = 100
MAX_NOTE = 2000


@dataclass(frozen=True, slots=True)
class Words:
    """How one action is worded: its icon, its button and its dialog.

    ``label`` names a row's icon ("Accept {name}") and ``button`` the bulk
    bar's; ``selected`` is the tooltip of the icon drawn selected (None
    for an action that records no state, which is never drawn so). Every
    icon tooltip is one short phrase with no full stop (UX conventions,
    "Row actions and confirmation"), as the portal's other icons are.
    ``done`` is what a dialog announces when the answer carries no notice.
    """

    icon: str
    button: str
    label: str
    title_one: str
    title_many: str
    done: str
    selected: str | None


WORDS = {
    Action.ACCEPT: Words(
        "entered",
        "Accept",
        "Accept {name}",
        "Accept {name}?",
        "Accept {count} {items}?",
        "accepted.",
        "Accepted",
    ),
    Action.EDIT: Words(
        "edit",
        "Edit",
        "Edit {name}",
        "Edit {name}",
        "",
        "accepted (edited).",
        # Edit is Accept with a value: an edited item's Accept is drawn
        # selected (spec, "Selected-state icons"), so Edit is no toggle.
        None,
    ),
    Action.IGNORE: Words(
        "ignore",
        "Ignore",
        "Ignore {name}",
        "Ignore {name}?",
        "Ignore {count} {items}?",
        "ignored.",
        "Ignored",
    ),
    Action.BACK_TO_REVIEW: Words(
        "clear",
        "Back to review",
        "Send {name} back to review",
        "Send {name} back to review?",
        "Send {count} {items} back to review?",
        "sent back to review.",
        None,
    ),
    # Only a census change the parish added offers it (#2041), which the
    # census worklist words with the field and the Member.
    Action.WITHDRAW: Words(
        "remove",
        "Withdraw",
        "Withdraw {name}",
        "Withdraw {name}?",
        "Withdraw {count} {items}?",
        "withdrawn.",
        None,
    ),
    Action.ENTERED_BY_HAND: Words(
        "entered_box",
        "Entered by hand",
        "Mark {name} entered by hand",
        "Mark {name} entered by hand?",
        "Mark {count} {items} entered by hand?",
        "marked entered by hand.",
        "Entered by hand",
    ),
    Action.HANDLED: Words(
        "entered_box",
        "Handled",
        "Mark {name} handled",
        "Mark {name} handled?",
        "Mark {count} {items} handled?",
        "marked handled.",
        "Handled",
    ),
}

# Entered incorrectly's two fixes (#1737), worded for that stage: the mark is
# made again once ParishSoft is corrected. Back to review keeps its words;
# its dialog says what follows (``INCORRECT_BODIES``).
INCORRECT_WORDS = {
    Action.ENTERED_BY_HAND: replace(
        WORDS[Action.ENTERED_BY_HAND],
        label="Mark {name} entered by hand again",
        title_one="Mark {name} entered by hand again?",
        done="marked entered by hand again.",
    ),
}

# The status look (ui-v1.css .status-*) a selected icon is filled with: an
# accepted decision or a mark in force is the Working look, Ignored (nothing
# carried forward) the neutral one.
SELECTED_LOOK = {
    Action.ACCEPT: "working",
    Action.IGNORE: "unknown",
    Action.ENTERED_BY_HAND: "working",
    Action.HANDLED: "working",
}
# The decision each decision icon shows in force. Edit is Accept with a
# value and an Added item starts Accepted (spec, "Reviewed stage"), so both
# select Accept; the Stage column's decision word tells them apart.
DECISION_ICON = {
    Decision.ACCEPTED: Action.ACCEPT,
    Decision.ADDED: Action.ACCEPT,
    Decision.ACCEPTED_EDITED: Action.ACCEPT,
    Decision.IGNORED: Action.IGNORE,
}
# The mark each terminal stage that has one shows in force.
MARK_ICON = {
    Stage.ENTERED_BY_HAND: Action.ENTERED_BY_HAND,
    Stage.HANDLED: Action.HANDLED,
}
# A stage's status look in the Stage column (UX conventions, "Status
# indicators": a needs-correction status takes the Error look). Every other
# stage is a plain badge.
STAGE_LOOK = {Stage.ENTERED_INCORRECTLY: "error"}
NOT_SEEN = "not_seen"
# The line an Ignored item gains when a refresh finds ParishSoft holding the
# Family's answer (refresh rule 1, #1689): the reviewer's note, never a
# change of stage.
NOW_MATCHES = "ParishSoft now matches the Family's answer."
# The line an item before its step gains when ParishSoft already holds its
# value (refresh rule 1, strict phase order, #1890): the item keeps its
# stage, so the reviewer can Accept quickly and Staff know there is nothing
# to type. Every ParishSoft-backed stream uses it.
ALREADY_HELD = "ParishSoft already holds this value."
EVERY_STAGE = "all"
# Why an item that withholds its undo cannot be changed on its row.
KEPT_REASON = "it can no longer be changed here"


# What a row's dialog says about each action. Choosing a decision's icon
# again undoes it, and a mark or other terminal step after it (spec,
# "Undoing a terminal stage", #1840; UX conventions, "Decision toggles",
# #2131): nothing is final.
DECIDE_BODY = "Record this decision. Choose its icon again to undo it."
BODIES = {
    Action.ENTERED_BY_HAND: (
        "Record that you typed this into ParishSoft. Check ParishSoft first; "
        "choose Entered by hand again to undo the mark."
    ),
    Action.HANDLED: "Record that this was handled. Choose Handled again to undo it.",
    Action.WITHDRAW: (
        "The item is withdrawn: nothing is entered in ParishSoft, and it moves "
        "to History."
    ),
    Action.BACK_TO_REVIEW: (
        "A decided item goes back to To review, to be decided again; a marked, "
        "handled, updated or confirmed one goes back to the stage before that "
        "step, with its decision kept. Undoing an update writes nothing to "
        "ParishSoft."
    ),
}
# What an Entered incorrectly row's Entered by hand again says (#1737). Its
# Back to review is a terminal undo (#1840), worded by ``undo_body``.
INCORRECT_BODIES = {
    Action.ENTERED_BY_HAND: (
        "Record that you corrected this in ParishSoft. Check ParishSoft first: "
        "the next refresh checks it again."
    ),
}
# What Back to review on an Ignored item says: its note is optional (#1889).
REOPEN_BODY = "The ignored item goes back to To review, to be decided again."
# What Back to review on a terminal stage says (#1840): which step it undoes
# and where the item goes. An undo back to Ready to update (Updated in
# ParishSoft, or a census change Confirmed or Already in ParishSoft that the
# update run carries) changes this portal only, which the dialog must say.
UNDO_BODY = "{step} is undone: the item goes back to {stage}{kept}. Say why."
UNDO_KEPT = ", with its decision kept"
UNDO_PORTAL_ONLY = (
    " Nothing is written to ParishSoft: only this portal's record changes, and "
    "a later update reads ParishSoft again first."
)
# What the reviewed Family form (#1705) shows a Family that signs in again,
# said in the dialogs whose decision it shows: Ignore on an item whose
# ParishSoft value the form shows (``shown_baseline`` rows), and Add.
IGNORE_SEEN = "The Family will see ParishSoft's value the next time it signs in."
ADD_BODY = (
    "Add a change the Family did not ask for. It starts Accepted, shown as "
    "Added by the parish."
)
# The choice of an action whose note is required (a terminal step's undo,
# #1840): the dialog's note is required while it holds (ui-v1.js
# data-required-when).
REOPEN = "reopen"
# The choice of an action whose decision records no note (a Ministry roster
# tick has none): its dialog hides the note (ui-v1.js data-note-field), and
# the stream's service refuses one (``build``'s ``silent``).
NO_NOTE = "no_note"
BULK_BODY = (
    "Each item is recorded as its own icon would record it. If someone "
    "else changed one of them first, nothing is recorded."
)


def words_for(action, stage):
    """How ``action`` is worded on an item at ``stage``: its own words, or
    Entered incorrectly's for the mark made again there (#1737)."""
    if stage is Stage.ENTERED_INCORRECTLY:
        return INCORRECT_WORDS.get(action, WORDS[action])
    return WORDS[action]


def selected_actions(placement):
    """The icons drawn selected for an item: its decision and mark in force.

    A Conflict's held decision is not in force (``Placement.held_decision``),
    so it selects nothing; neither does Entered incorrectly's old mark,
    which must be made again.
    """
    chosen = set()
    if placement.decision in DECISION_ICON:
        chosen.add(DECISION_ICON[placement.decision])
    if placement.stage in MARK_ICON:
        chosen.add(MARK_ICON[placement.stage])
    return chosen


def undo_body(stream, placement, *, by_hand=False):
    """What Back to review's dialog says on a terminal stage (#1840).

    It names the step undone and the stage the item returns to
    (``pipeline.undo_stage``), and, whenever it returns to Ready to update
    (Updated in ParishSoft, or a census change Confirmed or Already in
    ParishSoft that the update run carries), that nothing is written to
    ParishSoft.
    """
    stage = undo_stage(stream, placement, by_hand=by_hand)
    body = UNDO_BODY.format(
        step=placement.stage.label,
        stage=stage.label,
        kept=UNDO_KEPT if stage is not Stage.TO_REVIEW else "",
    )
    if stage is Stage.READY_TO_UPDATE:
        body += UNDO_PORTAL_ONLY
    return body


# What a pressed Accept says on a Confirmed item no decision was behind
# (ParishSoft already held the change, or a refresh found the Ministry
# request on the roster): the outcome Accept would have reached (#2131).
ALREADY_IN_PARISHSOFT = "Already in ParishSoft"


def _state(action, placement):
    """The state a selected icon shows: Accept names the decision exactly
    (Accepted, Accepted (edited), Added by the parish), or, with no
    decision behind a step, the step's own outcome."""
    if action is Action.ACCEPT and placement.decision is not None:
        return placement.decision.label
    if action is Action.ACCEPT and placement.stage is Stage.CONFIRMED:
        return ALREADY_IN_PARISHSOFT
    if action is Action.ACCEPT and placement.stage is not Stage.TO_REVIEW:
        return placement.stage.label
    return WORDS[action].selected


def undo_phrase(stream, placement, name, *, by_hand=False):
    """What choosing the pressed undo icon does, as its tooltip says it.

    Before a terminal step it sends the item back to review; on a terminal
    stage it undoes only that step (``pipeline.undo_stage``), so the phrase
    names the step.
    """
    if undo_stage(stream, placement, by_hand=by_hand) is Stage.TO_REVIEW:
        return f"select to send {name} back to review"
    return f"select to undo {placement.stage.label} for {name}"


def _unavailable_reason(action, placement, *, undo, kept=False):
    """Why an icon the row draws cannot be chosen now (UX conventions,
    "Decision toggles"): one short phrase, after the icon's own name.

    The stage machine decides it: a mark waits for an accepted decision, and
    a decision already made is changed only through its pressed icon (the
    undo). With no undo on the row, the item can no longer be changed here
    (``kept``: the item withholds its undo, as a census change an open
    addition covers does) or, on a terminal stage, only Staff and
    Administrators may undo it.
    """
    stage = placement.stage
    if stage is Stage.UPDATING:
        return "an update is running"
    if kept and not undo:
        return KEPT_REASON
    if stage in TERMINAL_STAGES and not undo:
        return "only Staff and Administrators can change it now"
    if action in (Action.ENTERED_BY_HAND, Action.HANDLED) and placement.decision in (
        None,
        Decision.IGNORED,
    ):
        return "accept it first"
    if stage in TERMINAL_STAGES:
        return f"undo {stage.label} first"
    return "send it back to review first"


def _drawn_actions(
    stream, placement, allowed, offered, selected, withheld, offers, by_hand
):
    """The decision icons one row draws, offered now or not (#2131).

    A row keeps the same icons whatever its stage, so none appears or
    disappears when a decision is made (UX conventions, "Decision
    toggles"): every action some stage of a Family's item offers and the
    reader's role allows, less those this item can never take
    (``withheld``). A census change the update run carries never takes
    Entered by hand. An item with its own actions (``offers``, a census
    addition, whose states offer the same icons) and an item in History
    draw only what they offer and what is in force.
    """
    if offers is not None or placement.stage is Stage.HISTORY:
        return offered | selected
    ever = frozenset().union(*(stage_actions(stream, stage) for stage in Stage))
    drawn = (ever & allowed) - withheld
    if Stream(stream) is Stream.CENSUS and not by_hand:
        drawn -= {Action.ENTERED_BY_HAND}
    return drawn | offered | selected


def row_icons(
    stream,
    placement,
    roles,
    *,
    name,
    leads=False,
    by_hand=False,
    withheld=(),
    offers=None,
):
    """One row's decision toggles for one reader, in ``ROW_ORDER`` (#2131).

    The row draws the same icons at every stage (``_drawn_actions``). Each
    is offered (``active``: it opens its dialog, ``aria-pressed`` false for
    an action that records a state), selected (the decision or mark in
    force: filled and ``aria-pressed`` true) or unavailable, with its
    tooltip saying why (``_unavailable_reason``). There is no separate Back
    to review icon: the latest state in force (the mark when a decision and
    a mark are both in force) is the undo, so choosing it again opens Back
    to review's own dialog (``undo`` true; ``_decorate`` words it), and an
    earlier selected icon waits for that undo. An item whose step had no
    decision behind it (a change ParishSoft already held, a Ministry
    request a refresh confirmed) draws Accept pressed as its undo, the
    outcome Accept would have reached ("Already in ParishSoft"). An action
    the reader's role does not allow is left out, selected or not (UX
    conventions, "By role"); so are ``withheld`` actions, which this one
    item cannot take whatever its stage (a census field no value can be
    typed for has no Edit), as the stream's service refuses them.
    ``offers`` are an item's own actions in place of its stage's (a census
    addition's, #2041).
    """
    withheld = frozenset(Action(action) for action in withheld)
    offered = (
        allowed_actions(
            stream, placement.stage, roles, leads=leads, by_hand=by_hand, offers=offers
        )
        - withheld
    )
    allowed = role_actions(stream, roles, leads=leads)
    selected = selected_actions(placement) & allowed
    undo = Action.BACK_TO_REVIEW in offered
    kept = Action.BACK_TO_REVIEW in withheld
    drawn = _drawn_actions(
        stream, placement, allowed, offered, selected, withheld, offers, by_hand
    )
    # The undo is the last state in force in ROW_ORDER: a mark after its
    # decision, since Back to review undoes the latest step.
    target = (
        next((a for a in reversed(ROW_ORDER) if a in selected), None) if undo else None
    )
    if undo and target is None and Action.ACCEPT in drawn:
        # Nothing in force to press: Accept stands for the step that had
        # no decision behind it, so the row keeps its icons (no layout
        # shift) and still has its undo.
        target = Action.ACCEPT
        selected = selected | {target}
    icons = []
    for action in ROW_ORDER:
        if action is Action.BACK_TO_REVIEW:
            continue
        if action not in drawn:
            continue
        on = action in selected
        if on and action is target:
            title = f"{_state(action, placement)}; " + undo_phrase(
                stream, placement, name, by_hand=by_hand
            )
        elif on and target is not None:
            title = (
                f"{_state(action, placement)}; undo {_state(target, placement)} first"
            )
        elif on:
            title = _state(action, placement)
            if kept or placement.stage in (*TERMINAL_STAGES, Stage.UPDATING):
                title += "; " + _unavailable_reason(
                    action, placement, undo=undo, kept=kept
                )
        elif action in offered:
            title = None
        else:
            title = (
                words_for(action, placement.stage).label.format(name=name)
                + "; "
                + _unavailable_reason(action, placement, undo=undo, kept=kept)
            )
        icons.append(
            _icon(
                action,
                placement,
                name,
                active=(on and action is target) or (not on and action in offered),
                on=on,
                title=title,
            )
        )
    return icons


def _icon(action, placement, name, *, active, on=False, title=None):
    """One decision icon's drawing: its glyph, name, tooltip and state.

    ``title`` is its accessible name and tooltip alike (one phrase, no
    full stop, R38 finding 5); None names it by its own label. A selected
    icon is filled with its state's status look; ``undo`` marks the pressed
    icon that opens Back to review. Back to review records no state, so its
    own icon (``action`` BACK_TO_REVIEW) is no toggle.
    """
    words = words_for(action, placement.stage)
    title = title or words.label.format(name=name)
    return {
        "action": action.value,
        "icon": words.icon,
        "label": title,
        "title": title,
        "selected": on,
        "active": active,
        "undo": on and active,
        "css": f"icon-button is-selected status-{SELECTED_LOOK[action]}"
        if on
        else "icon-button",
        # Where focus returns once the decision is redrawn: an Edit is
        # shown as its Accept drawn selected; a pressed icon's undo
        # returns to the same icon, now drawn unpressed.
        "focus": Action.ACCEPT.value if action is Action.EDIT else action.value,
        "pressed": on if words.selected else None,
    }


def bulk_buttons(stream, roles, *, items="items", silent=frozenset()):
    """The bulk bar's buttons this reader may use on ``stream``.

    Each is available only while every ticked row offers its action
    (ui-v1.js wireSelection: its value is the kind each row's box lists).
    A Ministry leader's actions count here as on a Ministry they lead; a
    row on another Ministry offers them nothing, so its box lists no kind.
    ``silent`` actions record no note, so their dialog shows none.
    """
    allowed = role_actions(stream, roles, leads=True)
    buttons = []
    for action in BULK:
        if action not in allowed:
            continue
        words = WORDS[action]
        buttons.append(
            {
                "value": action.value,
                "label": words.button,
                "title_one": words.title_one.replace("{items}", items),
                "title_many": words.title_many.replace("{items}", items),
                "done_one": "{name} " + words.done,
                "done_many": f"{{count}} {items} {words.done}",
                "body": BODIES.get(action, BULK_BODY),
                # Undoing a terminal step needs a reason (spec, "Undoing a
                # terminal stage"); reopening an Ignored item does not
                # (#1889): the note is required while a ticked row needs
                # one (its box's data-confirm-choice).
                "choice": NO_NOTE
                if action in silent
                else REOPEN
                if action is Action.BACK_TO_REVIEW
                else "",
            }
        )
    return buttons


def stage_choices(counts, stages):
    """The Stage filter's choices with their counts, To review first.

    ``counts`` maps a stage value (or ``NOT_SEEN``, the Not seen in
    ParishSoft flag) to its number of items; ``stages`` are the stages this
    stream shows, in order. Every stage ends with Every stage.
    """
    labelled = [(stage.value, stage.label) for stage in stages]
    if NOT_SEEN in counts:
        labelled.append((NOT_SEEN, TERMS["not_seen"]))
    choices = [
        (value, f"{label} ({counts.get(value, 0):,})") for value, label in labelled
    ]
    # Not seen in ParishSoft is a flag, not a stage: its items are already
    # counted under their own stage.
    every = sum(count for value, count in counts.items() if value != NOT_SEEN)
    choices.append((EVERY_STAGE, f"Every stage ({every:,})"))
    return choices


def clamp_name(name):
    """A Family's or Member's name for a review work table's cell, as safe
    HTML: it wraps between words to at most two lines, then is clamped, and
    shows whole in the shared clipped-name tip on hover and focus (UX
    conventions, "Table fit and row height", cut-short values, #2124).
    """
    return format_html(
        '<span class="cell-clamp name-clamp" data-clip-tip>{}</span>', name
    )


@dataclass(frozen=True, slots=True)
class Column:
    """One of a stream's identity columns, in the shared column order.

    ``key`` names the row's value; ``sort`` the table's sortable column, if
    any; the first column is the row's header cell. ``family`` marks a
    Family's or Member's name that opens the Family hub for viewers who may
    open it (the row's ``family_id``; a "family" column always does, with
    the shared Family-name link), and ``link`` names the row's key holding
    the item's own page, which its cell opens for every viewer (UX
    conventions, "Admin tables", R38 finding 13).

    ``detail`` names the row's key of a second value stacked on the line
    under the first, muted (a name's DUID), headed ``detail_heading`` and
    sorted by ``detail_sort``; ``dated`` puts the column in the Submitted
    and Last decision cell as a labelled line instead of a column of its
    own (a Ministry request's Last contact). Both keep the review work
    tables narrow (UX conventions, "Review work tables", #2124).
    """

    key: str
    heading: str
    sort: str = ""
    numeric: bool = False
    family: bool = False
    link: str = ""
    detail: str = ""
    detail_heading: str = ""
    detail_sort: str = ""
    dated: bool = False


HELD = "Earlier: {decision}, not in force."
FAILED = "The last update failed."


def decision_label(placement):
    """The line under a row's stage: the decision behind it, worded.

    The decision is shown only when the stage's word does not already say
    it (Ignored, Accepted (edited)). A Conflict keeps an earlier decision
    visible but not in force (spec, "Baseline refresh re-evaluation"), and
    a change whose last update failed says so, since its stage alone
    (Ready to update) reads as if nothing happened.
    """
    parts = []
    if placement.decision and placement.decision.label != placement.stage.label:
        parts.append(f"{placement.decision.label}.")
    if placement.held_decision:
        parts.append(HELD.format(decision=placement.held_decision.label))
    if placement.failed:
        parts.append(FAILED)
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class Remark:
    """How a row words making its Entered by hand mark again (``remark``).

    ``label`` names the icon ("Correct … of {name}"), ``title_one`` the
    dialog, ``done`` what it announces and ``body`` what the dialog says.
    """

    icon: str
    label: str
    title_one: str
    done: str
    body: str


def remark_icon(words, row, prefix, *, name):
    """The icon that makes a row's Entered by hand mark again, in ``words``
    (a ``Remark``).

    It opens the row's own Entered by hand dialog (``entered_template``) and
    posts Entered by hand, so the stream's service tells the mark made again
    from a first one. It records the same state again, so it is no toggle;
    the mark in force stays drawn selected beside it, and focus returns to
    that mark once the row is redrawn (the first ``data-row-action`` match).
    """
    return {
        "action": Action.ENTERED_BY_HAND.value,
        "icon": words.icon,
        "label": words.label.format(name=name),
        "title": words.label.format(name=name),
        "selected": False,
        "active": True,
        "undo": False,
        "decide": Action.ENTERED_BY_HAND.value,
        "css": "icon-button",
        "focus": Action.ENTERED_BY_HAND.value,
        "pressed": None,
        "post": urlencode(
            {
                "decide": Action.ENTERED_BY_HAND.value,
                "version": row["version"],
                "request_key": uuid4(),
            }
        ),
        "dialog": f"{prefix}-entered-dialog-{row['id']}",
        "title_one": words.title_one,
        "done": "{name} " + words.done,
        "choice": "",
        "body": words.body,
        "remark": True,
    }


def _decorate(stream, row, roles, prefix, columns, silent=frozenset()):
    """Give one row what its cells, box and icons need.

    Each offered icon opens the page's one decision dialog (Edit: the
    row's own value editor) with its own title, words and fields, so one
    dialog serves every row. Back to review on a terminal stage carries the
    ``reopen`` choice, which makes the dialog's note required (on an Ignored
    item it stays optional, #1889), and a
    ``silent`` action the ``no_note`` choice, which hides it (ui-v1.js
    data-confirm-choice). A row whose Entered by hand asks for fields of its
    own (``entered_template``: a census New Member's ParishSoft Member ID,
    #1749) opens a dialog of its own for it, drawn per row as Edit's is.
    A row that may make its Entered by hand mark again over the one in force
    (``remark``: a census New Member's Member ID correction, #1773) gains
    that icon (``remark_icon``) after its selected mark.
    """
    placement = row["placement"]
    name = row["name"]
    # A row decided on another page (``elsewhere``: its label and link, a
    # Limitations page's Cannot contribute financially row, decided on
    # Pledges) offers no icon and so no box; its Actions cell is the link.
    icons = (
        []
        if row.get("elsewhere")
        else row_icons(
            stream,
            placement,
            roles,
            name=name,
            leads=row.get("leads", False),
            by_hand=row.get("by_hand", False),
            withheld=row.get("withheld", ()),
            offers=row.get("offers"),
        )
    )
    key = uuid4()
    # Undoing a terminal step needs a note; reopening an Ignored item does
    # not (#1889).
    reopen = undo_needs_note(placement.stage)
    own_entered = bool(row.get("entered_template"))
    for icon in icons:
        if not icon["active"]:
            continue
        # A pressed icon is the row's undo: it posts Back to review, with
        # Back to review's own dialog, words and note (#2131).
        action = Action.BACK_TO_REVIEW if icon["undo"] else Action(icon["action"])
        icon["decide"] = action.value
        words = words_for(action, placement.stage)
        icon["post"] = urlencode(
            {"decide": action.value, "version": row["version"], "request_key": key}
        )
        icon["dialog"] = (
            f"{prefix}-edit-dialog-{row['id']}"
            if action is Action.EDIT
            else f"{prefix}-entered-dialog-{row['id']}"
            if action is Action.ENTERED_BY_HAND and own_entered
            else f"{prefix}-decide-dialog"
        )
        icon["title_one"] = words.title_one
        icon["done"] = "{name} " + words.done
        back = action is Action.BACK_TO_REVIEW
        icon["choice"] = (
            NO_NOTE if action in silent else REOPEN if back and reopen else ""
        )
        bodies = (
            INCORRECT_BODIES if placement.stage is Stage.ENTERED_INCORRECTLY else BODIES
        )
        if back and placement.stage is Stage.IGNORED:
            icon["body"] = REOPEN_BODY
        elif back and reopen:
            icon["body"] = undo_body(
                stream, placement, by_hand=row.get("by_hand", False)
            )
        elif action is Action.EDIT:
            # Edit opens the row's own editor, which draws its own body (the
            # Family will see the reviewer's value); an empty body keeps it.
            icon["body"] = ""
        else:
            icon["body"] = bodies.get(action, DECIDE_BODY)
        if action is Action.IGNORE and row.get("shown_baseline"):
            icon["body"] = f"{icon['body']} {IGNORE_SEEN}"
    remark = row.get("remark")
    if remark is not None and any(
        icon["action"] == Action.ENTERED_BY_HAND and icon["selected"] for icon in icons
    ):
        icons.append(remark_icon(remark, row, prefix, name=name))
    row["icons"] = icons
    # A remark is one row at a time: it asks for the row's own fields. The
    # kinds keep BULK's order whatever order the icons are drawn in, so rows
    # offering the same kinds list them alike (ui-v1.js compares the lists).
    kinds = {
        icon["decide"] for icon in icons if icon["active"] and not icon.get("remark")
    }
    row["kinds"] = " ".join(kind.value for kind in BULK if kind.value in kinds)
    # A bulk Back to review asks for a note while a ticked row is at a
    # terminal stage.
    row["choice"] = REOPEN if reopen else ""
    # Each identity cell: its value, its column, the item page it opens
    # (its column's ``link``), if any, and the value stacked under it (its
    # column's ``detail``). A dated column is a labelled line of the
    # Submitted and Last decision cell instead (#2124).
    row["identity"] = [
        (
            row.get(column.key, ""),
            column,
            row.get(column.link, "") if column.link else "",
            row.get(column.detail, "") if column.detail else "",
        )
        for column in columns
        if not column.dated
    ]
    row["dated"] = [
        (column.heading, row.get(column.key, "")) for column in columns if column.dated
    ]
    row["stage_label"] = placement.stage.label
    row["stage_look"] = STAGE_LOOK.get(placement.stage, "")
    row["decision_label"] = decision_label(placement)
    if row.get("now_matches"):
        row["decision_label"] = f"{row['decision_label']} {NOW_MATCHES}".strip()
    # A stream's own line about the row's stage (a census change a refresh
    # put back in Conflict says why, #1749).
    if row.get("stage_note"):
        row["decision_label"] = f"{row['decision_label']} {row['stage_note']}".strip()
    row["edit_form"] = f"{prefix}-edit-{row['id']}"
    row["edit_dialog"] = f"{prefix}-edit-dialog-{row['id']}"
    row["edit_hint"] = f"{prefix}-edit-hint-{row['id']}"
    row["offers_edit"] = any(icon.get("decide") == Action.EDIT for icon in icons)
    row["entered_form"] = f"{prefix}-entered-{row['id']}"
    row["entered_dialog"] = f"{prefix}-entered-dialog-{row['id']}"
    row["entered_hint"] = f"{prefix}-entered-hint-{row['id']}"
    row["offers_entered"] = own_entered and any(
        icon.get("decide") == Action.ENTERED_BY_HAND for icon in icons
    )
    # The ParishSoft now cell shows only where the value is in question.
    row["shows_now"] = placement.stage in (
        Stage.CONFLICT,
        Stage.ENTERED_INCORRECTLY,
    ) or row.get("not_seen", False)
    return row


@dataclass
class Worklist:
    """What ``components/review-worklist.html`` draws for one reader.

    ``prefix`` keeps the page's element ids apart; ``action`` is the page
    the filters and every decision post back to, whose answer is the page
    again, redrawn in place. ``baseline`` is whether the stream has a
    Baseline column and ``reviewer`` whether it has a Reviewer's value
    column (a stream with no Edit has none). ``fields`` are the page's view
    (its filters, Stage included), which every form posts so its answer
    shows the same view. The templates are the stream's own: its filters
    after Stage, its value editor (Edit and Add), and its export pane
    below the table.
    """

    stream: Stream
    prefix: str
    action: str
    rows: list
    columns: tuple
    stage: str
    stage_choices: list
    bulk: list
    add: bool
    items: str
    caption: str
    fields: dict = field(default_factory=dict)
    baseline: bool = True
    reviewer: bool = True
    shows_now: bool = False
    bulk_too_many: bool = False
    bulk_key: UUID = field(default_factory=uuid4)
    add_key: UUID = field(default_factory=uuid4)
    filters_template: object = None
    editor_template: object = "stewardship/components/review-worklist-value.html"
    add_template: object = None
    export_template: object = None
    # The view's own address (``page_address``), which ui-v1.js puts in the
    # address bar after each in-place answer, so a reload shows the same
    # view; empty for a stream that keeps none.
    address: str = ""
    # What Add's dialog says (a stream whose Add a Family sees says so).
    add_body: str = ADD_BODY
    # Whether the answer to a decision is this page, drawn from directly
    # (data-confirm-answer-page): a review worklist's is. The Family hub
    # (#2064) posts a row's decision to the stream's page and then redraws
    # its own tab from the tab's address, so its dialogs set it false.
    answer_page: bool = True
    # Whether the Actions cell holds its icons two to a line (Pledges,
    # #2131): a stream whose table fits its box only by wrapping its text
    # columns keeps the four 44px decision toggles from taking that room.
    paired_actions: bool = False

    max_bulk = MAX_BULK

    @property
    def pages_label(self):
        """The table navigator's name."""
        return f"{self.caption} pages"

    @property
    def notice_id(self):
        """The notice line, where focus goes when a decided row is gone."""
        return f"{self.prefix}-notice"

    @property
    def bulk_dialog(self):
        """The bulk dialog's id, which the bulk buttons open."""
        return f"{self.prefix}-bulk-dialog"

    @property
    def add_dialog(self):
        """Add's dialog id, which the Add button opens."""
        return f"{self.prefix}-add-dialog"

    @property
    def add_post(self):
        """Add's own fields: a request key, so a repeated Add is a replay."""
        return urlencode({"request_key": self.add_key})

    @property
    def decide_hint(self):
        """The decision dialog's reserved reason line."""
        return f"{self.prefix}-decide-hint"

    @property
    def bulk_hint(self):
        """The bulk dialog's reserved reason line."""
        return f"{self.prefix}-bulk-hint"

    @property
    def add_hint(self):
        """Add's reserved reason line."""
        return f"{self.prefix}-add-hint"

    @property
    def dialogs(self):
        """The page's shared dialogs: (kind, form id, dialog id) each.

        The decision dialog always; the bulk dialog with the selection
        column; Add's for a reader allowed to add.
        """
        kinds = ["decide"]
        if self.selectable:
            kinds.append("bulk")
        if self.add:
            kinds.append("add")
        return [
            (kind, f"{self.prefix}-{kind}", f"{self.prefix}-{kind}-dialog")
            for kind in kinds
        ]

    @property
    def acting(self):
        """Whether any row offers or shows an action to this reader, so a
        page drawing only some rows (the Family hub, #2064) draws an Actions
        column and the rows' dialogs."""
        return any(row["icons"] for row in self.rows)

    @property
    def selectable(self):
        """Whether the page draws the selection column and bulk bar."""
        return bool(self.bulk) and not self.bulk_too_many

    @property
    def shown_columns(self):
        """The identity columns drawn as columns of their own (not dated)."""
        return tuple(column for column in self.columns if not column.dated)

    @property
    def span(self):
        """How many columns a full-width row (the empty table's) spans: the
        identity columns, then the values, Stage, Submitted and Last
        decision, and Actions cells (#2124), and the selection column."""
        return len(self.shown_columns) + 4 + int(self.selectable)


def build(
    stream,
    rows,
    roles,
    *,
    prefix,
    action,
    columns,
    stage,
    counts,
    stages,
    items="items",
    caption="Review worklist",
    baseline=True,
    reviewer=True,
    fields=None,
    address="",
    silent=frozenset(),
    add_body=ADD_BODY,
    paired_actions=False,
    **templates,
):
    """The ``worklist`` for one reader of one page of ``stream``'s rows.

    Each row is a dict with ``id``, ``version``, ``name`` (how dialogs and
    icons name it), ``placement``, the identity values ``columns`` name,
    ``baseline``, ``family_answer``, ``reviewer_value``, ``parishsoft_now``,
    ``submitted_at`` and the last decision (``decided_by``, ``decided_at``,
    ``note``); ``items`` is the plural the bulk dialogs count ("changes")
    and ``caption`` the table's name; optionally ``leads`` (the reader
    leads its Ministry), ``by_hand`` (a By hand census change),
    ``not_seen`` (the Not seen in ParishSoft flag), ``now_matches``
    (ParishSoft now holds an Ignored item's Family answer), ``stage_note``
    (the stream's own line under the stage), ``entered_template`` (the
    fields its Entered by hand asks for, in a dialog of its own), ``remark``
    (a ``Remark``: the row may make its Entered by hand mark again, in that
    dialog), ``withheld`` (actions this item cannot take) and
    ``shown_baseline`` (a Family that signs in again sees ParishSoft's value
    for it once it is Ignored, #1705, which its Ignore dialog says). ``roles``
    is the reader's role set. ``add_body`` is what Add's dialog says;
    ``paired_actions`` draws the Actions cell's icons two to a line.
    ``stage`` is the Stage filter's value and ``fields`` the stream's other
    filters, posted by every form; ``baseline`` and ``reviewer`` say whether
    the stream has those columns. ``address`` is the view's own address
    (``page_address``). ``silent`` names the actions whose decision records
    no note (a Ministry roster tick, R39 finding 2): their dialogs show no
    note, and the stream's service refuses one. ``templates`` takes the
    stream's ``filters_template``, ``editor_template``, ``add_template`` and
    ``export_template``.
    """
    stream = Stream(stream)
    columns = tuple(columns)
    silent = frozenset(silent)
    decorated = [_decorate(stream, row, roles, prefix, columns, silent) for row in rows]
    add = may_add(stream, roles) and templates.get("add_template") is not None
    return Worklist(
        stream=stream,
        prefix=prefix,
        action=action,
        rows=decorated,
        columns=columns,
        stage=stage,
        stage_choices=stage_choices(counts, stages),
        bulk=bulk_buttons(stream, roles, items=items, silent=silent),
        add=add,
        items=items,
        caption=caption,
        fields={"stage": stage} | dict(fields or {}),
        baseline=baseline,
        reviewer=reviewer,
        shows_now=any(row["shows_now"] for row in decorated),
        bulk_too_many=len(decorated) > MAX_BULK,
        address=address,
        add_body=add_body,
        paired_actions=paired_actions,
        **templates,
    )


def page_address(action, pairs):
    """A worklist view's own address, for the address bar (R38 finding 6).

    ``pairs`` are the ``(name, value)`` choices a link may carry: the Stage,
    the stream's closed filters and the table's sort, size and page, never
    a private value such as a name search, which stays in POST bodies (UX
    conventions, "Admin tables"). Empty values are left out, so the default
    view is the page's plain address. After every in-place answer ui-v1.js
    puts it in the address bar (``a[data-page-address]``), and the stream's
    view draws the same view from it on a reload.
    """
    kept = [(name, value) for name, value in pairs if value not in ("", None)]
    return f"{action}?{urlencode(kept)}" if kept else action


# The Submitted From / Through filter Census changes and Additional
# information offered until #1888, with its browser zone. A page drawn
# before then, a bookmark or a typed address may still send them: the
# pages drop them unread (no error page, no redirect), while the
# ``pk-admin`` commands keep their own date options.
RETIRED_DATES = frozenset({"start", "end", "zone"})


def drop_retired_dates(parameters):
    """Remove the retired date filter's fields from a mutable QueryDict copy."""
    for name in RETIRED_DATES:
        parameters.pop(name, None)
    return parameters


def page_fields(values):
    """A query's ``form_values`` without the retired date fields.

    The pages' forms carry no date field (#1888); the shared query classes
    keep theirs for ``pk-admin``, whose output is unchanged.
    """
    return {name: value for name, value in values.items() if name not in RETIRED_DATES}


# --- The decision POST ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorklistDecision:
    """One decision a page or command asks a stream's service to record.

    ``versions`` maps each item's id to the version the reader saw (empty
    for Add); ``key`` makes a repeated post a replay (a row's or Add's
    request key, the page's bulk key); ``fields`` are the stream editor's
    own fields (Edit's and Add's value), which the service checks.
    """

    action: Action
    versions: dict
    note: str
    key: UUID
    bulk: bool = False
    fields: dict = field(default_factory=dict)


ROW_FIELDS = ("decide", "version", "request_key", "note")
BULK_FIELDS = ("bulk", "bulk_key", "bulk_note")
DECIDED = frozenset(ROW_ORDER) | {Action.ADD}


def _one(found, name):
    """The single value of ``name`` in a popped field list, else ValueError."""
    values = found.get(name)
    if values is None or len(values) != 1:
        raise ValueError(f"A worklist decision needs {name} once.")
    return values[0]


def _note(text):
    """A decision's note, trimmed; too long is a malformed form."""
    note = text.strip()
    if len(note) > MAX_NOTE:
        raise ValueError("A worklist note is too long.")
    return note


def _item(text):
    """One item id as the page drew it: short printable ASCII."""
    if not text or len(text) > 64 or not text.isascii() or not text.isprintable():
        raise ValueError("Invalid worklist item.")
    return text


def _version(text):
    """A version the page drew: a non-negative decimal number."""
    if not text.isascii() or not text.isdecimal():
        raise ValueError("Invalid worklist version.")
    return int(text)


def read_decision(parameters):
    """Pop a decision's fields from a POST's ``parameters`` (a dict of lists).

    Returns None when there is none (the filters or a table control
    posted), leaving the page's own fields for the view. A row's icon
    sends ``decide``, ``item``, ``version``, ``request_key`` and ``note``;
    Add sends ``decide=add`` with no item; the bulk bar sends ``bulk``,
    ``bulk_key``, ``bulk_note`` and each ticked ``item`` as
    ``<id>:<version>``. Edit's and Add's editor fields (``value``,
    ``value_<part>``) are the decision's ``fields``. Anything malformed
    raises ValueError (a 400).
    """
    row = {name: parameters.pop(name) for name in ROW_FIELDS if name in parameters}
    bulk = {name: parameters.pop(name) for name in BULK_FIELDS if name in parameters}
    items = parameters.pop("item", None)
    if not row and not bulk:
        if items is not None:
            raise ValueError("A worklist item needs a decision.")
        return None
    if row and bulk:
        raise ValueError("A worklist post is one decision.")
    if bulk:
        action = Action(_one(bulk, "bulk"))
        if action not in BULK or not items or len(items) > MAX_BULK:
            raise ValueError("Invalid bulk worklist decision.")
        versions = {}
        for value in items:
            identity, separator, version = value.partition(":")
            if not separator:
                raise ValueError("Invalid worklist selection.")
            versions[_item(identity)] = _version(version)
        if len(versions) != len(items):
            raise ValueError("A worklist item was chosen twice.")
        return WorklistDecision(
            action,
            versions,
            _note(_one(bulk, "bulk_note")),
            UUID(_one(bulk, "bulk_key")),
            bulk=True,
        )
    action = Action(_one(row, "decide"))
    if action not in DECIDED:
        raise ValueError("Invalid worklist decision.")
    if action is Action.ADD:
        if items is not None or "version" in row:
            raise ValueError("Add names no item.")
        versions = {}
    else:
        if items is None or len(items) != 1:
            raise ValueError("A worklist decision names one item.")
        versions = {_item(items[0]): _version(_one(row, "version"))}
    # The editor's own fields are named value or value_<part> (a pledge's
    # amount and frequency); every other field is the page's view.
    fields = {
        name: parameters.pop(name)
        for name in list(parameters)
        if name == "value" or name.startswith("value_")
    }
    return WorklistDecision(
        action,
        versions,
        _note(_one(row, "note")),
        UUID(_one(row, "request_key")),
        fields=fields,
    )


class StaleDecision(Exception):
    """Someone else changed an item first; nothing was recorded.

    ``name`` is how the notice names the item (None when the race is only
    known to have happened, as a database's unique violation says).
    """

    def __init__(self, name=None):
        super().__init__(name)
        self.name = name


class DecisionService(Protocol):
    """The decisions of one stream, shared by its page, bulk bar and pk-admin.

    ``decide`` records ``decision`` for ``principal`` and returns the
    notice the page shows. It checks every item's version and the
    principal's role itself, and records a bulk decision all or none. It
    raises ``StaleDecision`` when an item changed first, PermissionError
    for an item the principal may not decide, and ValueError for an
    action the item's stage does not offer.
    """

    def decide(self, principal, decision): ...


# Short, so a long item name still fits the notice's one reserved line on
# a desktop (R38 finding 10); the list redrawn with it shows the item's
# current state. The name leads, as in every other notice, because each
# stream words its names to start a sentence ("The request from ...").
STALE = "{name}: not recorded, changed by someone else."


def record(service, principal, decision):
    """Record ``decision`` through ``service``; the notice the page shows.

    A race is not the reader's mistake: the page answers with its current
    state and says so, naming the item when the service can.
    """
    try:
        return service.decide(principal, decision)
    except StaleDecision as error:
        return STALE.format(name=error.name or "A chosen item")


# A drawn form sent again with other choices after its first answer was
# lost (a request key already bound to another decision): nothing was
# recorded, and a reload draws fresh forms with fresh keys. Each stream's
# view answers it as a 409 with this refusal.
REBOUND = Refusal(
    "This page was already used to record different choices, so nothing was recorded.",
    "Reload the page, then try again.",
)
