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
  ``stewardship_ministry_roster_entry`` (each Entered in ParishSoft tick,
  except the clearing a Back to review undo writes with its revision);
- Additional information: ``stewardship_information_revision``;
- pledges: ``stewardship_pledge_review`` (#2071), listed only for a reader
  who may open Pledges (``Capability.FINANCIAL_DETAIL``, ``steps``);
- Limitations: ``stewardship_limitation_review`` (#2071);
- Added by the parish (#2023): a Ministry request Staff added (its first
  revision reads Added by the parish) and a census change Staff added,
  ``stewardship_census_addition`` with its later steps
  (``stewardship_census_addition_resolution``).

The same statement also reads the Timeline's request lines (#2023, reports
spec, "Timeline request lines"): each census change and Ministry join or
leave a response asked for, which ``requests`` words apart from the
review replay (``read_reviews`` returns both).

A census, Ministry or Additional information history row stores the
item's state after the step, not the step itself, so ``steps`` replays one
item's rows in order and words each from the state before and after it
(``word``), in the review pipeline's words. A pledge or Limitations row
records the step's own decision, so its word is that decision
(``state_after``). A line never carries a value (the Member tabs show the
values), except a pledge Edit's Reviewer's value, which only a reader of
Pledges sees; and only a Back to review line carries its note, to a reader
who may open that stream's work page, where the same note is on the item's
row (``note_visible``, #1893): notes can hold personal detail, so every
other line shows none.

``read_reviews`` runs one statement (plus the Ministry names when a line
needs them); everything else is pure, so a unit test drives it from plain
values. A stream that stores a new kind of decision (Update ParishSoft,
the refresh checks) adds a branch to ``_HISTORY`` and a state mapping to
``state_after`` (review-pipeline spec, "Audit").
"""

import json
from dataclasses import dataclass, replace
from decimal import Decimal

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.actor_names import (
    ACTOR_USER_JOIN,
    WHO,
    ActorSentence,
    from_parts,
)
from parishkit.stewardship.accounts.leader_import import ministry_names
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.pipeline import (
    DECISION_LABELS,
    Decision,
    ministry_placement,
)
from parishkit.stewardship.source.catalog_names import ministry_display_name

from .census_changes import LABELS
from .financial import (
    FREQUENCY_LABELS,
    UNAVAILABLE_SHARE,
    in_offered_order,
    share_labels,
)
from .hub_census import member_label
from .limitations import ANSWERS as LIMITATION_ANSWERS
from .limitations import proposed_label
from .ministries import outcome_label
from .money import MoneyAmount

# The key under which ``names`` carries the campaign's share-method labels
# (option id to label) for a pledge Edit's share methods (#2177); Ministry
# names are keyed by DUID, an int, so the two never meet.
SHARE_LABELS = "share_labels"

# One Family's decision history in one scope, oldest first: one row per
# stored step, across every stream, each with the submission it belongs to
# (for the Member's name as the Family answered it), the acting user's
# address, the step's own columns (``a`` to ``d``, by stream: see
# ``state_after``) and the note it was recorded with (``note``: a census
# review's or resolution's note, a Ministry revision's decision note, an
# Additional information revision's Staff notes; shown only on a Back to
# review line, ``note_visible``; a pledge review's or Limitations review's
# note). ``item`` keys one change, request, Additional information item,
# pledge (the Family's campaign record) or Limitations answer (its kind and
# Member key); ``step`` orders its rows (the change's, request's or
# answer's version, a tick's or pledge review's sequence). Every branch
# reaches the stream's rows through the Family's submissions and the
# history's foreign-key indexes.
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
# Only a response by the rule before the reviewed form carries anything: a
# reviewed-form response (``reviewed_view``) never carries a decision, so
# its new change with the same answer (one answered again over the value
# the form showed for it) starts from no decision (#2028).
#
# A Limitations ``accepted`` row is either an Accept at To review or the
# undo of a Handled answer (#1840), which records Accepted again; ``d``
# says it is the undo: the answer's previous row is a Handled still in
# force when this row was written, that is, no live response from the one
# it was decided on up to this row's own answered the other way, the
# rule of ``stewardship_limitation_in_force_v1`` bounded at this row.
# Takes %(family)s, %(campaign)s, %(response_mode)s and %(epoch)s
# (``timelines.scope_values``).
_HISTORY = (
    """
WITH subs AS (
    SELECT s.id, s.answers, s.reviewed_view, s.family_version, s.submitted_at
    FROM stewardship_submission s
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
        pp.admin_value_set::text AS seed_set, pp.admin_value::text AS seed_value,
        pp.id AS carried_from
    FROM subs JOIN stewardship_proposed_change p ON p.submission_id=subs.id
    LEFT JOIN stewardship_proposed_change pp ON pp.superseded_by_id=p.id
        AND NOT subs.reviewed_view
        AND stewardship_response_comparison_v1(pp.field, pp.submitted_value)
            IS NOT DISTINCT FROM
            stewardship_response_comparison_v1(p.field, p.submitted_value)
), h AS (
    SELECT 'census_review' AS stream, p.id::text AS item, v.expected_version AS step,
        v.created_at AS at, v.actor_id, p.submission_id, p.entity_kind,
        p.entity_key, p.field AS subject, v.decision AS a,
        v.admin_value_set::text AS b, v.admin_value::text AS c,
        v.accepted_base::text AS d, p.seed_decision, p.seed_set, p.seed_value,
        v.note, NULL AS added
    FROM props p
    JOIN stewardship_proposal_review v ON v.proposal_id=p.id
    UNION ALL
    SELECT 'census_resolution', p.id::text, r.expected_version, r.created_at,
        r.actor_id, p.submission_id, p.entity_kind, p.entity_key, p.field,
        r.action, NULL, NULL, NULL, p.seed_decision, p.seed_set, p.seed_value,
        r.note, NULL
    FROM props p
    JOIN stewardship_proposal_resolution r ON r.proposal_id=p.id
    UNION ALL
    SELECT 'ministry_revision', q.id::text, v.expected_version, v.created_at,
        v.actor_id, q.submission_id, q.entity_kind, q.entity_key,
        q.ministry_duid::text, v.state, v.outcome, q.action,
        (v.decision_note<>'')::text, NULL, NULL, NULL, v.decision_note,
        q.parish_added::text
    FROM subs JOIN stewardship_ministry_request q ON q.submission_id=subs.id
    JOIN stewardship_ministry_revision v ON v.request_id=q.id
    UNION ALL
    SELECT 'ministry_roster', q.id::text, e.sequence, e.created_at,
        e.actor_id, q.submission_id, q.entity_kind, q.entity_key,
        q.ministry_duid::text, e.entered::text, NULL, q.action, NULL,
        NULL, NULL, NULL, NULL, NULL
    FROM subs JOIN stewardship_ministry_request q ON q.submission_id=subs.id
    JOIN stewardship_ministry_roster_entry e ON e.request_id=q.id
    -- A tick cleared by Back to review's undo (#1840) is part of that undo,
    -- written with its revision (the same actor and request key), which
    -- reads as the one Back to review line; only a clearing without one
    -- (made before #1840) is a line of its own.
    WHERE e.entered OR NOT EXISTS (SELECT 1 FROM stewardship_ministry_revision r
        WHERE r.request_id=q.id AND r.actor_id=e.actor_id
          AND r.request_key=e.request_key)
    UNION ALL
    SELECT 'information', i.id::text, v.expected_version, v.created_at,
        v.actor_id, i.submission_id, NULL, NULL, NULL,
        v.follow_up_needed::text, v.followed_up::text, NULL, NULL,
        NULL, NULL, NULL, v.notes, NULL
    FROM subs JOIN stewardship_additional_information i ON i.submission_id=subs.id
    JOIN stewardship_information_revision v ON v.item_id=i.id
    UNION ALL
    SELECT 'pledge', g.family_id::text, g.sequence, g.created_at, g.actor_id,
        g.submission_id, NULL, NULL, g.reviewed_shares::text, g.action, g.decision,
        g.reviewed_annual_pledge::text, g.reviewed_frequency,
        NULL, NULL, NULL, g.note, NULL
    FROM subs JOIN stewardship_pledge_review g ON g.submission_id=subs.id
    UNION ALL
    SELECT 'limitation', l.kind || ':' || l.member_key, l.expected_version,
        l.created_at, l.actor_id, l.submission_id, l.kind,
        nullif(l.member_key, ''), l.kind, l.decision, NULL, NULL,
        (l.decision='accepted' AND EXISTS(
            SELECT 1 FROM stewardship_limitation_review p
            JOIN stewardship_submission decided ON decided.id=p.submission_id
            WHERE p.family_id=l.family_id AND p.kind=l.kind
              AND p.member_key=l.member_key
              AND p.expected_version=l.expected_version-1
              AND p.decision='handled'
              AND NOT EXISTS(SELECT 1 FROM stewardship_submission later
                  WHERE later.family_id=decided.family_id
                    AND later.campaign_id=decided.campaign_id
                    AND later.mode='live'
                    AND later.family_version>decided.family_version
                    AND later.family_version<=subs.family_version
                    AND stewardship_limitation_stated_v1(
                        later.answers, p.kind, p.member_key)
                    AND NOT stewardship_limitation_answer_v1(
                        later.answers, p.kind, p.member_key))))::text,
        NULL, NULL, NULL, l.note, NULL
    FROM subs JOIN stewardship_limitation_review l ON l.submission_id=subs.id
    -- What each response asked for (#2023): a census change it did not
    -- just carry over, and a Ministry join or leave that is neither the
    -- parish's own nor a same-intent successor of one still in progress.
    -- A reviewed form's request chosen again over a decided one (Ignored,
    -- or edited into another Ministry) supersedes it with the same action
    -- (``ministry_requests._derive_reviewed``, rule 2): the Family asked
    -- again, so it is listed. A request's latest revision says whether it
    -- was decided; one with none was never decided.
    UNION ALL
    SELECT 'census_asked', p.id::text, 0, subs.submitted_at, NULL,
        p.submission_id, p.entity_kind, p.entity_key, p.field,
        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
    FROM props p JOIN subs ON subs.id=p.submission_id
    WHERE p.carried_from IS NULL
    UNION ALL
    SELECT 'ministry_asked', q.id::text, 0, subs.submitted_at, NULL,
        q.submission_id, q.entity_kind, q.entity_key, q.ministry_duid::text,
        NULL, NULL, q.action, NULL, NULL, NULL, NULL, NULL, NULL
    FROM subs JOIN stewardship_ministry_request q ON q.submission_id=subs.id
    WHERE NOT q.parish_added AND NOT EXISTS (
        SELECT 1 FROM stewardship_ministry_request old
        WHERE old.superseded_by_id=q.id AND old.action=q.action
          AND coalesce((SELECT v.state FROM stewardship_ministry_revision v
              WHERE v.request_id=old.id
              ORDER BY v.expected_version DESC LIMIT 1), 'new')
              NOT IN ('resolved', 'closed_no_response'))
    -- A census change the parish added (#1710) belongs to the Family, not
    -- to a response: it is named through the Family's latest live response
    -- (none in a Testing scope, whose responses never enter the pipeline).
    UNION ALL
    SELECT 'census_addition', 'add:' || a.id, 0, a.created_at, a.actor_id,
        (SELECT id FROM subs ORDER BY family_version DESC LIMIT 1),
        a.entity_kind, a.entity_key, a.field, NULL, NULL, NULL, NULL,
        NULL, NULL, NULL, a.note, NULL
    FROM stewardship_census_addition a
    WHERE a.family_id=%(family)s AND %(response_mode)s='live'
    UNION ALL
    SELECT 'census_addition_resolution', 'add:' || a.id, r.sequence,
        r.created_at, r.actor_id,
        (SELECT id FROM subs ORDER BY family_version DESC LIMIT 1),
        a.entity_kind, a.entity_key, a.field, r.action, NULL, NULL, NULL,
        NULL, NULL, NULL, r.note, NULL
    FROM stewardship_census_addition a
    JOIN stewardship_census_addition_resolution r ON r.addition_id=a.id
    WHERE a.family_id=%(family)s AND %(response_mode)s='live'
    -- Withdraw of an entered addition stores a reopening and then the
    -- withdrawal in one request (#2041): one Withdrawn line. Each row's
    -- time is its own statement's, so the pair is matched by its order
    -- and the request's correlation id, which both rows carry
    -- (``census_additions.resolve_census_additions`` binds one).
      AND NOT (r.action='reopened' AND EXISTS (
          SELECT 1 FROM stewardship_census_addition_resolution w
          WHERE w.addition_id=a.id AND w.sequence=r.sequence+1
            AND w.action='withdrawn'
            AND w.correlation_id=r.correlation_id))
)
SELECT h.stream, h.item, h.step, h.at, u.email, uu.record_id, uu.name,
    h.entity_kind, h.entity_key,
    h.subject, h.a, h.b, h.c, h.d, h.seed_decision, h.seed_set, h.seed_value,
    h.note, h.added,
    (coalesce(subs.answers->'members'->h.entity_key,
        subs.answers->'proposed_members'->h.entity_key)
        -> 'first_name')::text,
    (coalesce(subs.answers->'members'->h.entity_key,
        subs.answers->'proposed_members'->h.entity_key)
        -> 'last_name')::text,
    sm.canonical::jsonb->>'firstName', sm.canonical::jsonb->>'lastName'
FROM h JOIN subs ON subs.id=h.submission_id
LEFT JOIN stewardship_portal_user u ON u.id=h.actor_id
-- The reviewer's current user row, for their Name and Timeline (#2259).
"""
    + ACTOR_USER_JOIN.format(alias="uu", user="u")
    + """
LEFT JOIN stewardship_source_current sc ON sc.singleton
LEFT JOIN stewardship_snapshot_member mm ON h.entity_kind='member'
    AND mm.snapshot_id=sc.snapshot_id AND mm.source_key=h.entity_key
LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
-- A response's request lines share its instant: keep them in a steady
-- order (the Family's, then each Member's, by field) rather than by
-- their random ids.
ORDER BY h.at,
    CASE WHEN h.stream LIKE '%%asked' THEN
        concat_ws(':', h.entity_kind, h.entity_key, h.subject) END,
    h.item, h.step
"""
)
COLUMNS = (
    "stream",
    "item",
    "step",
    "at",
    "actor",
    "actor_user",
    "actor_name",
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
    "note",
    "added",
    "answer_first",
    "answer_last",
    "source_first",
    "source_last",
)
# A census change's stored decision, as the replay's decision.
CENSUS_DECISIONS = {"approved": Decision.ACCEPTED, "ignored": Decision.IGNORED}
# The capability of each stream's work page, whose readers see the note
# of that stream's Back to review lines (#1893): Census changes, Ministry
# requests (scoped to the request's Ministry), Additional information,
# Pledges and Limitations.
NOTE_READERS = {
    "census": Capability.VIEW_CENSUS,
    "ministry": Capability.MINISTRY_FOLLOWUP,
    "information": Capability.ADDITIONAL_FOLLOWUP,
    "pledge": Capability.FINANCIAL_DETAIL,
    "limitation": Capability.CAMPAIGN_REPORT,
}
# Who acted when the user record is gone, as Ministry requests says it.
FORMER_USER = "Former portal user"
# The words of a step that undoes a decision, and of one that changes
# neither a decision nor a stage.
BACK_TO_REVIEW = _("Back to review")
UPDATED = _("Follow-up updated")
# A reviewer-created item's first step, and a census addition taken back.
ADDED = DECISION_LABELS[Decision.ADDED]
WITHDRAWN = _("Withdrawn")
# The request lines' words, by ``_HISTORY`` stream (#2023).
ASKED = {
    "census_asked": _("Asked for a census change"),
    "ministry_asked": _("Asked for a Ministry change"),
}
# A step that finishes an item's work, and the word for undoing it.
DONE = {"entered": _("Entered by hand"), "handled": _("Handled")}
CLEARED = {"entered": _("Entered by hand cleared"), "handled": _("Handled cleared")}
# A pledge review's action, and a Limitations review's decision, as its
# line's word: each row records the step itself (#2071). A Limitations
# ``accepted`` row that undoes Handled reads Back to review instead
# (``_HISTORY``'s ``d``).
PLEDGE_WORDS = {
    "accept": DECISION_LABELS[Decision.ACCEPTED],
    "edit": DECISION_LABELS[Decision.ACCEPTED_EDITED],
    "ignore": DECISION_LABELS[Decision.IGNORED],
    "back_to_review": BACK_TO_REVIEW,
    "entered_by_hand": DONE["entered"],
}
LIMITATION_WORDS = {
    "accepted": DECISION_LABELS[Decision.ACCEPTED],
    "ignored": DECISION_LABELS[Decision.IGNORED],
    "none": BACK_TO_REVIEW,
    "handled": DONE["handled"],
}
# Every word ``word`` can give a line, for the timeline PDF's Reviews kind.
WORDS = frozenset(
    str(value)
    for value in (
        *DONE.values(),
        *CLEARED.values(),
        *DECISION_LABELS.values(),
        BACK_TO_REVIEW,
        WITHDRAWN,
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
    revision's state and outcome (``c`` the request's join or leave, ``d``
    whether it has a decision note); a
    roster tick's "true" or "false"; an Additional information revision's
    follow-up-needed and followed-up flags; a pledge review's action
    (``b`` to ``d`` its decision and Reviewer's amount and frequency); a
    Limitations review's decision (``d`` whether it undoes Handled).
    A pledge or Limitations row records its step's own decision, so its
    state is only that word (``forced``): no later step reads from it.
    """
    stream, a, b = row["stream"], row["a"], row["b"]
    if stream == "pledge":
        return State(forced=PLEDGE_WORDS[a])
    if stream == "limitation":
        undo = a == "accepted" and row["d"] == "true"
        return State(forced=BACK_TO_REVIEW if undo else LIMITATION_WORDS[a])
    before = replace(before, forced=None)
    if stream == "census_addition":
        # An Add starts accepted, under its creator's name (#1710).
        return State(decision=Decision.ACCEPTED, forced=ADDED)
    if stream == "census_addition_resolution":
        if a == "entered":
            return replace(before, done="entered")
        if a == "withdrawn":
            return State(forced=WITHDRAWN)
        # Not entered yet: the mark is undone, the addition stays accepted.
        return replace(before, done=None, forced=BACK_TO_REVIEW)
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
        if a == "ignored":
            return replace(before, decision=Decision.IGNORED)
        if before.decision is Decision.IGNORED:
            # Reopening an Ignore: no decision is in force.
            return replace(before, decision=None)
        # Back to review of a terminal step (#1840) keeps the decision behind
        # it: a mark that accepted in one step (decision 2, history since
        # #1890) leaves the change Accepted.
        # Updated in ParishSoft and Already in ParishSoft have no step of
        # their own here, so the states alone cannot tell this line apart
        # and its word is forced.
        decision = before.decision
        if before.done == "entered" and decision is None:
            decision = Decision.ACCEPTED
        return replace(before, decision=decision, done=None, forced=BACK_TO_REVIEW)
    if stream == "ministry_revision":
        # The roster flags only move a decided join or leave between To
        # enter by hand and Entered by hand; the decision is the same.
        placement = ministry_placement(
            state=a, outcome=b, source_resolved=False, roster_entered=False
        )
        # Back to review of a terminal step (#1840) records the decision
        # again, keeping it in force, and clears the tick if one was ticked
        # (that clearing, written with it, is no line of its own:
        # ``_HISTORY``), or, for a request a refresh resolved with no
        # decision behind it, reopens it with the required note.
        kept = (
            a == "resolved"
            and before.decision is not None
            and placement.decision == before.decision
        )
        undone = kept or (
            a == "in_progress" and before.decision is None and row["d"] == "true"
        )
        return replace(
            before,
            decision=placement.decision,
            done=None if kept else before.done,
            forced=BACK_TO_REVIEW if undone else None,
        )
    if stream == "ministry_roster":
        return replace(before, done="entered" if a == "true" else None)
    # Additional information: followed up is Handled, needed is Accepted
    # (To handle), neither is To review (``pipeline.information_placement``).
    # Leaving Handled with the decision kept is Handled's undo, Back to
    # review (review pipeline spec, "Undoing a terminal stage", #1840).
    needed, handled = a == "true", b == "true"
    decision = Decision.ACCEPTED if needed or handled else None
    undone = before.done == "handled" and not handled and decision is not None
    return replace(
        before,
        decision=decision,
        done="handled" if handled else None,
        forced=BACK_TO_REVIEW if undone else None,
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

    ``names`` maps a Ministry DUID to its display name, and ``SHARE_LABELS``
    to the campaign's share-method labels when a pledge line needs them
    (#2177). A census change
    names its field and Member (or the Family); a Ministry request its join
    or leave, Ministry and Member, plus a decided request's outcome; a
    pledge only itself, plus an Edit's Reviewer's value (``pledge_words``);
    a Limitations answer the answer and its Member (or the Family).
    """
    stream = row["stream"]
    if stream == "information":
        return str(_("Additional information"))
    if stream == "pledge":
        return pledge_words(row, names.get(SHARE_LABELS))
    if stream == "limitation":
        # A proposed Member is named as the Limitations downloads name them.
        subject = (
            str(_("the Family"))
            if row["entity_kind"] == "family"
            else proposed_label(
                member_name(row), row["entity_kind"] == "proposed_member"
            )
        )
        words = _("%(answer)s for %(subject)s")
        return str(
            words % {"answer": LIMITATION_ANSWERS[row["subject"]], "subject": subject}
        )
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


def pledge_words(row, labels=None):
    """A pledge line's item: "Pledge", and an Edit's Reviewer's value.

    The value reads as the Pledges page's Reviewer's value cell does
    (``pledge_review_views.reviewer_value``): the annual pledge, then the
    frequency's word ("No frequency" for a zero pledge without one).
    It repeats ``reviewed_parts`` and ``frequency_label`` there rather than
    import a views module; keep the two in step. Only an Edit records an
    Accepted (edited) decision (``pledge_review_action_state``). An Edit
    that changed the Family's share methods (#2177; ``subject`` holds them
    as stored) adds them, each by the label ``labels`` gives its option
    (``share_labels_for``), with its Other text, in the configured order.
    """
    if row["a"] != "edit":
        return str(_("Pledge"))
    amount = MoneyAmount(int(Decimal(row["c"]) * 100)).display
    frequency = FREQUENCY_LABELS[row["d"] or "none"]
    words = _("Pledge; Reviewer's value: %(amount)s, %(frequency)s")
    words = str(words % {"amount": amount, "frequency": frequency})
    if not row.get("subject"):
        return words
    labels = labels or {}
    shares = ", ".join(
        f"{labels.get(key, UNAVAILABLE_SHARE)}: {text}"
        if text
        else labels.get(key, UNAVAILABLE_SHARE)
        for key, text in in_offered_order(json.loads(row["subject"]), labels)
    )
    return str(
        _("%(words)s; share methods: %(shares)s") % {"words": words, "shares": shares}
    )


def share_labels_for(campaign_id):
    """The campaign's share methods by option id, as Pledges words them (#2177).

    Each by its current full label (``share_labels``), the Family form's
    wording for a household with no listed Member, in configured order.
    """
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    system = SystemConfiguration.objects.select_related(
        "active_configuration__parish"
    ).get()
    return share_labels(
        campaign.active_configuration.values,
        campaign_id=campaign_id,
        parish_name=system.active_configuration.parish.name,
    )


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


def note_visible(principal, row):
    """Whether ``principal`` may read the note of this history row's line.

    The reader of the line's stream's work page (``NOTE_READERS``) already
    sees the note there, on the item's row, so the Timeline may show it too
    (the Administrator, #1893); anyone else may not. A Ministry line is
    judged for the request's own Ministry, as its page's scope is. No
    principal (a caller that names no reader) sees none.
    """
    stream = row["stream"].split("_")[0]
    ministry = int(row["subject"]) if stream == "ministry" else None
    return principal is not None and allows(
        principal, NOTE_READERS[stream], ministry_id=ministry
    )


def steps(rows, names=None, reader=None):
    """The review lines, oldest first: ``(at, what, detail)`` per history row.

    ``rows`` are ``_HISTORY``'s rows as dicts, oldest first. Each item's
    rows are replayed from its first state (``first_state``), so each line
    is worded from the state before and after its step. A Back to review
    line's detail ends with its note, when it has one and ``reader`` may
    read it (``note_visible``); no other line carries one. A pledge line is
    listed only for a ``reader`` who may open Pledges (financial detail,
    #2071), as that page requires; a caller that names no reader sees no
    pledge line.

    Request rows (``ASKED``, #2023) are no review step: ``requests``
    words them, and they are skipped here.
    """
    names = names or {}
    states, notes, lines = {}, {}, []
    pledges = allows(reader, Capability.FINANCIAL_DETAIL)
    for row in rows:
        if row["stream"] == "pledge" and not pledges:
            continue
        if row["stream"] in ASKED:
            continue
        key = (row["stream"].split("_")[0], row["item"])
        first = key not in states
        before = states.get(key) or first_state(row)
        after = state_after(row, before)
        if (
            first
            and row["stream"] == "ministry_revision"
            and row.get("added") == "true"
        ):
            # The revision that added a parish request (#2023) records it
            # accepted; its line reads as the Add it was.
            after = replace(after, forced=ADDED)
        states[key] = after
        # An Additional information revision stores the item's whole Staff
        # notes, carried over when a step adds none, so only notes that
        # changed with this step are its own; the other streams store each
        # step's own note.
        note = (row["note"] or "").strip()
        own = row["stream"] != "information" or note != notes.get(key)
        notes[key] = note
        # The reviewer by name on the page, linked to their Timeline for a
        # reader who may open it (#2259); the export and pk-admin keep the
        # address (``ActorSentence``), as before.
        who = from_parts(
            row["actor"], row.get("actor_user"), row.get("actor_name"), FORMER_USER
        )
        detail = str(
            _("%(item)s; by %(who)s") % {"item": item_words(row, names), "who": WHO}
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
        shown = own and note and str(what) == str(BACK_TO_REVIEW)
        if shown and note_visible(reader, row):
            detail = str(
                _("%(detail)s; note: %(note)s") % {"detail": detail, "note": note}
            )
        lines.append((row["at"], what, ActorSentence(detail, who)))
    return lines


def requests(rows, names=None):
    """The request lines, oldest first: ``(at, what, detail)`` per asked item.

    ``rows`` are ``_HISTORY``'s rows as dicts; only its ``ASKED`` streams
    are lines here (#2023). Each names what a response asked for, as a
    review line names its item, never who acted or the value.
    """
    names = names or {}
    return [
        (row["at"], ASKED[row["stream"]], item_words(row, names))
        for row in rows
        if row["stream"] in ASKED
    ]


def read_reviews(values, reader=None):
    """One Family's review lines in a scope: ``steps`` over ``_HISTORY``.

    ``values`` are ``timelines.scope_values``; ``reader`` is the principal
    the lines are for, who sees the Back to review notes their roles allow
    (``note_visible``). One statement, plus the
    current catalog's Ministry names (``ministry_names``) only when a
    Ministry line needs them, and the campaign's share-method labels only
    when an Edit's share methods do. Runs inside the page's read guard.
    """
    with connection.cursor() as cursor:
        cursor.execute(_HISTORY, values)
        rows = [dict(zip(COLUMNS, found, strict=True)) for found in cursor]
    duids = {
        int(row["subject"]) for row in rows if row["stream"].startswith("ministry")
    }
    names = ministry_names(duids) if duids else {}
    if any(row["stream"] == "pledge" and row["subject"] for row in rows):
        # An Edit's share methods (#2177) are worded from the campaign's
        # configuration: two more statements, only when a line needs them.
        names = {**names, SHARE_LABELS: share_labels_for(values["campaign"])}
    return steps(rows, names, reader), requests(rows, names)
