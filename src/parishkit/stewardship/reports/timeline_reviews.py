"""The Timeline's review lines: what reviewers did with one Family's answers (#1774).

The Family hub's Timeline (``timelines``) lists, besides the Family's own
steps and its emails, each review action Staff, Administrators and Ministry
leaders took on the Family's answers (reports spec, "Timeline review
lines"). The lines come from each stream's stored decision history, never
from the audit log, whose context deliberately names no item or value:

- census: ``stewardship_proposal_review`` (an Administrator's Accept,
  Ignore, Back to review, Edit, Clear edit or Use the Family's value
  anyway) and ``stewardship_proposal_resolution`` (Entered by hand, Ignore
  and Back to review), which share their change's version sequence;
- Ministry: ``stewardship_ministry_revision`` (each follow-up edit) and
  ``stewardship_ministry_roster_entry`` (each Entered in ParishSoft tick);
- Additional information: ``stewardship_information_revision``.

Each history row stores the item's state after the step, not the step
itself, so ``steps`` replays one item's rows in order and words each from
the state before and after it (``word``), in the review pipeline's words.
A line never carries a value or a note: the Member tabs show the values,
and notes can hold personal detail.

``read_reviews`` runs one statement (plus the Ministry names when a line
needs them); everything else is pure, so a unit test drives it from plain
values. A stream that stores a new kind of decision (pledges, Limitations,
Update ParishSoft, the refresh checks) adds a branch to ``_HISTORY`` and a
state mapping to ``state_after`` (review-pipeline spec, "Audit").
"""

import json
from dataclasses import dataclass, replace

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.leader_import import ministry_names
from parishkit.stewardship.pipeline import (
    DECISION_LABELS,
    Decision,
    ministry_placement,
)
from parishkit.stewardship.source.catalog_names import ministry_display_name

from .census_changes import LABELS
from .hub_census import member_label
from .ministries import outcome_label

# One Family's decision history in one scope, oldest first: one row per
# stored step, across every stream, each with the submission it belongs to
# (for the Member's name as the Family answered it), the acting user's
# address and the step's own columns (``a`` to ``d``, by stream: see
# ``state_after``). ``item`` keys one change, request or Additional
# information item; ``step`` orders its rows (the change's or request's
# version, a tick's sequence). Every branch reaches the stream's rows
# through the Family's submissions and the history's foreign-key indexes.
# The Member's ParishSoft name comes from the current snapshot, as Census
# changes names a Member.
#
# A census change a resubmission carried over (``responses.proposals``:
# the same answer to a change that was neither resolved nor cancelled)
# starts with its predecessor's decision and Reviewer's value, which no
# history row records. ``props`` restates that rule to seed the replay
# (``seed_decision``, ``seed_set``, ``seed_value``; NULL when nothing was
# carried): the predecessor is the change it superseded, the answers are
# compared as the review pages compare them
# (``stewardship_response_comparison_v1``), and an approval over an active
# accepted ParishSoft base (``accepted_base.active_bases``) is not carried.
# Takes %(family)s, %(campaign)s, %(response_mode)s and %(epoch)s
# (``timelines.scope_values``).
_HISTORY = """
WITH subs AS (
    SELECT s.id, s.answers FROM stewardship_submission s
    WHERE s.family_id=%(family)s AND s.campaign_id=%(campaign)s
      AND s.mode=%(response_mode)s
      AND s.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
), props AS (
    SELECT p.id, p.submission_id, p.entity_kind, p.entity_key, p.field,
        CASE WHEN pp.decision='approved' AND EXISTS(
                SELECT 1 FROM stewardship_proposal_review b
                WHERE b.proposal_id=pp.id AND b.accepted_base
                  AND b.accepted_source_id=pp.current_source_id
                  AND NOT EXISTS(SELECT 1 FROM stewardship_proposal_review u
                      WHERE u.proposal_id=pp.id
                        AND u.expected_version>b.expected_version
                        AND u.decision<>'approved'))
            THEN 'unreviewed' ELSE pp.decision END AS seed_decision,
        pp.admin_value_set::text AS seed_set, pp.admin_value::text AS seed_value
    FROM subs JOIN stewardship_proposed_change p ON p.submission_id=subs.id
    LEFT JOIN stewardship_proposed_change pp ON pp.superseded_by_id=p.id
        AND stewardship_response_comparison_v1(pp.field, pp.submitted_value)
            IS NOT DISTINCT FROM
            stewardship_response_comparison_v1(p.field, p.submitted_value)
), h AS (
    SELECT 'census_review' AS stream, p.id::text AS item, v.expected_version AS step,
        v.created_at AS at, v.actor_id, p.submission_id, p.entity_kind,
        p.entity_key, p.field AS subject, v.decision AS a,
        v.admin_value_set::text AS b, v.admin_value::text AS c,
        v.accepted_base::text AS d, p.seed_decision, p.seed_set, p.seed_value
    FROM props p
    JOIN stewardship_proposal_review v ON v.proposal_id=p.id
    UNION ALL
    SELECT 'census_resolution', p.id::text, r.expected_version, r.created_at,
        r.actor_id, p.submission_id, p.entity_kind, p.entity_key, p.field,
        r.action, NULL, NULL, NULL, p.seed_decision, p.seed_set, p.seed_value
    FROM props p
    JOIN stewardship_proposal_resolution r ON r.proposal_id=p.id
    UNION ALL
    SELECT 'ministry_revision', q.id::text, v.expected_version, v.created_at,
        v.actor_id, q.submission_id, q.entity_kind, q.entity_key,
        q.ministry_duid::text, v.state, v.outcome, q.action, NULL,
        NULL, NULL, NULL
    FROM subs JOIN stewardship_ministry_request q ON q.submission_id=subs.id
    JOIN stewardship_ministry_revision v ON v.request_id=q.id
    UNION ALL
    SELECT 'ministry_roster', q.id::text, e.sequence, e.created_at,
        e.actor_id, q.submission_id, q.entity_kind, q.entity_key,
        q.ministry_duid::text, e.entered::text, NULL, q.action, NULL,
        NULL, NULL, NULL
    FROM subs JOIN stewardship_ministry_request q ON q.submission_id=subs.id
    JOIN stewardship_ministry_roster_entry e ON e.request_id=q.id
    UNION ALL
    SELECT 'information', i.id::text, v.expected_version, v.created_at,
        v.actor_id, i.submission_id, NULL, NULL, NULL,
        v.follow_up_needed::text, v.followed_up::text, NULL, NULL,
        NULL, NULL, NULL
    FROM subs JOIN stewardship_additional_information i ON i.submission_id=subs.id
    JOIN stewardship_information_revision v ON v.item_id=i.id
)
SELECT h.stream, h.item, h.step, h.at, u.email, h.entity_kind, h.entity_key,
    h.subject, h.a, h.b, h.c, h.d, h.seed_decision, h.seed_set, h.seed_value,
    (coalesce(subs.answers->'members'->h.entity_key,
        subs.answers->'proposed_members'->h.entity_key)
        -> 'first_name')::text,
    (coalesce(subs.answers->'members'->h.entity_key,
        subs.answers->'proposed_members'->h.entity_key)
        -> 'last_name')::text,
    sm.canonical::jsonb->>'firstName', sm.canonical::jsonb->>'lastName'
FROM h JOIN subs ON subs.id=h.submission_id
LEFT JOIN stewardship_portal_user u ON u.id=h.actor_id
LEFT JOIN stewardship_source_current sc ON sc.singleton
LEFT JOIN stewardship_snapshot_member mm ON h.entity_kind='member'
    AND mm.snapshot_id=sc.snapshot_id AND mm.source_key=h.entity_key
LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
ORDER BY h.at, h.item, h.step
"""
COLUMNS = (
    "stream",
    "item",
    "step",
    "at",
    "actor",
    "entity_kind",
    "entity_key",
    "subject",
    "a",
    "b",
    "c",
    "d",
    "seed_decision",
    "seed_set",
    "seed_value",
    "answer_first",
    "answer_last",
    "source_first",
    "source_last",
)
# A census change's stored decision, as the replay's decision.
CENSUS_DECISIONS = {"approved": Decision.ACCEPTED, "ignored": Decision.IGNORED}
# Who acted when the user record is gone, as Ministry requests says it.
FORMER_USER = "Former portal user"
# The words of a step that undoes a decision, and of one that changes
# neither a decision nor a stage.
BACK_TO_REVIEW = _("Back to review")
UPDATED = _("Follow-up updated")
# A step that finishes an item's work, and the word for undoing it.
DONE = {"entered": _("Entered by hand"), "handled": _("Handled")}
CLEARED = {"entered": _("Entered by hand cleared"), "handled": _("Handled cleared")}
# Every word ``word`` can give a line, for the timeline PDF's Reviews kind.
WORDS = frozenset(
    str(value)
    for value in (
        *DONE.values(),
        *CLEARED.values(),
        *DECISION_LABELS.values(),
        BACK_TO_REVIEW,
        _("Edited"),
        _("Edit cleared"),
        UPDATED,
    )
)


@dataclass(frozen=True)
class State:
    """One item's review state, as far as the lines tell steps apart.

    ``decision`` is the decision in force (``Decision.ACCEPTED`` or
    ``IGNORED``; None while none is), ``done`` a finishing mark ("entered"
    or "handled", ``DONE``'s keys; None without one) and ``edit`` a census
    change's Reviewer's value as stored text (None without one). The value
    is only compared, never shown. ``forced`` is a step's own word when the
    states alone cannot tell it (Use the Family's value anyway keeps an
    accepted decision accepted).
    """

    decision: Decision | None = None
    done: str | None = None
    edit: str | None = None
    forced: str | None = None


def state_after(row, before):
    """The item's ``State`` after one history row; ``before`` is the one before it.

    The row's ``a`` to ``d`` columns are, by stream: a census review's
    decision, whether it set a Reviewer's value, that value and whether it
    used the Family's value anyway; a census resolution's action; a Ministry
    revision's state and outcome (``c`` the request's join or leave); a
    roster tick's "true" or "false"; an Additional information revision's
    follow-up-needed and followed-up flags.
    """
    stream, a, b = row["stream"], row["a"], row["b"]
    before = replace(before, forced=None)
    if stream == "census_review":
        # Use the Family's value anyway keeps a decision accepted, so its
        # word is forced: Accepted, or Accepted (edited) over an edit.
        forced = None
        if row["d"] == "true":
            edited = Decision.ACCEPTED_EDITED if b == "true" else Decision.ACCEPTED
            forced = DECISION_LABELS[edited]
        return replace(
            before,
            decision=CENSUS_DECISIONS.get(a),
            edit=row["c"] if b == "true" else None,
            forced=forced,
        )
    if stream == "census_resolution":
        if a == "entered":
            return replace(before, done="entered")
        return replace(before, decision=Decision.IGNORED if a == "ignored" else None)
    if stream == "ministry_revision":
        # The roster flags only move a decided join or leave between To
        # enter by hand and Entered by hand; the decision is the same.
        placement = ministry_placement(
            state=a, outcome=b, source_resolved=False, roster_entered=False
        )
        return replace(before, decision=placement.decision)
    if stream == "ministry_roster":
        return replace(before, done="entered" if a == "true" else None)
    # Additional information: followed up is Handled, needed is Accepted
    # (To handle), neither is To review (``pipeline.information_placement``).
    needed, handled = a == "true", b == "true"
    return replace(
        before,
        decision=Decision.ACCEPTED if needed or handled else None,
        done="handled" if handled else None,
    )


def word(before, after):
    """What happened between two states, in the review pipeline's words.

    A finishing mark reads as itself; a new decision reads as its decision
    word (Accepted (edited) when it carries a Reviewer's value), and losing
    one as Back to review; then a cleared mark, an edit set or cleared, and
    otherwise Follow-up updated (a note, a contact attempt or a move to In
    progress, none of which the line shows).
    """
    if after.forced:
        return after.forced
    if after.done and after.done != before.done:
        return DONE[after.done]
    if after.decision != before.decision:
        if after.decision is None:
            return BACK_TO_REVIEW
        if after.decision is Decision.ACCEPTED and after.edit is not None:
            return DECISION_LABELS[Decision.ACCEPTED_EDITED]
        return DECISION_LABELS[after.decision]
    if before.done and not after.done:
        return CLEARED[before.done]
    if after.edit != before.edit:
        return _("Edited") if after.edit is not None else _("Edit cleared")
    return UPDATED


def member_name(row):
    """The Member a row is about, named as the Family answered, else ParishSoft."""
    answer = {
        "first_name": _decoded(row["answer_first"]),
        "last_name": _decoded(row["answer_last"]),
    }
    return member_label(answer, [row["source_first"], row["source_last"]])


def _decoded(value):
    """A JSON text value as a string; None for JSON null, a non-string or none."""
    value = json.loads(value) if value is not None else None
    return value if isinstance(value, str) else None


def item_words(row, names):
    """The item a row is about, for its Details: what and whom.

    ``names`` maps a Ministry DUID to its display name. A census change
    names its field and Member (or the Family); a Ministry request its join
    or leave, Ministry and Member, plus a decided request's outcome.
    """
    stream = row["stream"]
    if stream == "information":
        return str(_("Additional information"))
    if stream.startswith("census"):
        if row["entity_kind"] == "family":
            subject = str(_("the Family"))
        else:
            subject = member_name(row)
        if row["subject"] == "new_member":
            return str(_("New Member: %(name)s") % {"name": subject})
        field = LABELS.get(row["subject"], row["subject"])
        words = _("%(field)s for %(subject)s")
        return str(words % {"field": field, "subject": subject})
    action = row["c"]
    words = str(
        _("%(action)s %(ministry)s for %(member)s")
        % {
            "action": _("Join") if action == "join" else _("Leave"),
            "ministry": names.get(int(row["subject"]))
            or ministry_display_name(int(row["subject"]), None),
            "member": member_name(row),
        }
    )
    outcome = outcome_label(row["b"], action) if stream == "ministry_revision" else ""
    return f"{words}; {outcome}" if outcome else words


def first_state(row):
    """An item's ``State`` before its first history row.

    Nothing is decided, except for a census change a resubmission carried
    over, which starts with its predecessor's decision and Reviewer's value
    (``_HISTORY``'s seed columns).
    """
    if row["seed_decision"] is None:
        return State()
    return State(
        decision=CENSUS_DECISIONS.get(row["seed_decision"]),
        edit=row["seed_value"] if row["seed_set"] == "true" else None,
    )


def steps(rows, names=None):
    """The review lines, oldest first: ``(at, what, detail)`` per history row.

    ``rows`` are ``_HISTORY``'s rows as dicts, oldest first. Each item's
    rows are replayed from its first state (``first_state``), so each line
    is worded from the state before and after its step.
    """
    names = names or {}
    states, lines = {}, []
    for row in rows:
        key = (row["stream"].split("_")[0], row["item"])
        before = states.get(key) or first_state(row)
        after = state_after(row, before)
        states[key] = after
        who = row["actor"] or FORMER_USER
        detail = str(
            _("%(item)s; by %(who)s") % {"item": item_words(row, names), "who": who}
        )
        what = word(before, after)
        if row["stream"] == "census_review" and str(what) == str(UPDATED):
            # A census review always decides, but reconciliation may have
            # reset the decision with no history row (ParishSoft's value
            # moved), so a re-decision can look like no change: name the
            # decision it records instead.
            what = (
                word(replace(after, decision=None), after)
                if after.decision
                else BACK_TO_REVIEW
            )
        lines.append((row["at"], what, detail))
    return lines


def read_reviews(values):
    """One Family's review lines in a scope: ``steps`` over ``_HISTORY``.

    ``values`` are ``timelines.scope_values``. One statement, plus the
    current catalog's Ministry names (``ministry_names``) only when a
    Ministry line needs them. Runs inside the page's read guard.
    """
    with connection.cursor() as cursor:
        cursor.execute(_HISTORY, values)
        rows = [dict(zip(COLUMNS, found, strict=True)) for found in cursor]
    duids = {
        int(row["subject"]) for row in rows if row["stream"].startswith("ministry")
    }
    return steps(rows, ministry_names(duids) if duids else {})
