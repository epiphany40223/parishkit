"""The Family hub's census tabs: the Family tab and one tab per Member (#1678).

Slice 2 of the Family information hub (admin-portal spec, "Family and Member
tabs"). For one Family it reads the latest live census response and the
census change rows in force for it, and shapes one ``Subject`` per tab: the
Family, each existing Member, each current ParishSoft Member the response
does not name (#2319), each Member ParishSoft no longer lists in the
Family whose requests survive, and each Member the Family added. Each field
reads as unchanged or as a change with its review-pipeline stage, derived by
``pipeline.census_placement`` exactly as the review worklist derives it, so
the hub holds no second comparison that could drift from it.

Which change rows are in force mirrors ``responses.effective.proposal_index``
(the rule the Family form itself uses), restated in SQL here because that
function takes the response work lock, which a read-only report must not.

``read_hub_census`` runs the two statements; ``shape`` is pure, so a unit
test drives it from plain values.
"""

import json
import logging
from dataclasses import dataclass

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.observability import Event, emit_failure
from parishkit.stewardship.pipeline import Stage, census_placement
from parishkit.stewardship.responses.census import FAMILY_FIELDS
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS
from parishkit.stewardship.responses.member_requests import REQUEST_FIELDS
from parishkit.stewardship.source.source_status import (
    NAME_NOT_RECORDED,
    NOT_IN_PARISHSOFT,
)

from .census_changes import LABELS, display, family_answer

# The request fields that survive a later response which no longer shows
# the Member (``proposal_index``'s preserved rows).
TERMINAL = tuple(field.name for field in REQUEST_FIELDS)
# A stage's status look on its chip (UX conventions, "Status indicators"):
# a needs-correction stage takes the Error look; every other is plain.
STAGE_LOOK = {Stage.ENTERED_INCORRECTLY: "error"}
# The marks of a Member who is not a current one, and what each tab says.
MARKS = {
    "new": (_("New Member"), _("Added by the Family; not in ParishSoft yet.")),
    "moved": (
        _("No longer in this household"),
        _("The form did not ask about this Member's other details."),
    ),
    "deceased": (
        _("Deceased"),
        _("The form did not ask about this Member's other details."),
    ),
    # A Member of a Family that has not answered yet (R39 finding 8): the
    # tab shows, named from ParishSoft, so the hub lists every Member.
    "unsubmitted": (
        _("No submission yet"),
        _(
            "The Family has not submitted answers for this Member yet. "
            "They appear here once it does."
        ),
    ),
    # A current ParishSoft Member the Family's response does not name
    # (#2319): ParishSoft moved them into the Family after it answered.
    "joined": (
        _("Joined in ParishSoft after the Family answered"),
        _(
            "ParishSoft added this Member to the Family after the Family's "
            "response, so the Family did not answer for them. They appear "
            "with answers once the Family answers again."
        ),
    ),
    # The same, when the Family answered before census joined the campaign:
    # it did submit, so the tab must not say it has not.
    "unanswered": (
        _("No census answers yet"),
        _(
            "The Family answered before the census questions were part of "
            "this campaign, so this Member has no census answers yet. They "
            "appear here once the Family answers again."
        ),
    ),
    # Drawn with the shared {% source_status "missing" %} mark.
    "missing": (
        _(NOT_IN_PARISHSOFT),
        _(
            "ParishSoft no longer lists this Member in this Family, so only "
            "the Family's earlier requests are shown."
        ),
    ),
}

# The change rows in force (``effective.proposal_index``): the census
# response's own rows, for a Member's request fields the newest row of any
# live response, the census response's first, and the changes a response
# drawn as the reviewed form (#1705) showed and left as shown, which stay
# on their earlier response (``effective._shown_still_answered``): an
# Ignored one shown as the baseline whatever the answer, any other only
# while the answer is the value shown. Report-only rows belong to other
# streams. Takes %(family)s, %(campaign)s, %(submission)s (a UUID) and
# %(terminal)s (``TERMINAL``, a list).
PROPOSALS = """
SELECT DISTINCT ON (p.entity_kind, p.entity_key, p.field)
    p.id::text, p.entity_kind, p.entity_key, p.field, p.baseline_available,
    p.baseline_value::text, p.submitted_value::text, p.admin_value_set,
    p.admin_value::text, p.handling, p.decision, p.execution
FROM stewardship_proposed_change p
JOIN stewardship_submission s ON s.id=p.submission_id
WHERE s.family_id=%(family)s AND s.campaign_id=%(campaign)s AND s.mode='live'
    AND p.handling<>'report-only'
    AND (s.id=%(submission)s OR (p.entity_kind='member'
        AND p.field = ANY(%(terminal)s))
        OR EXISTS (SELECT 1 FROM stewardship_submission c
            CROSS JOIN LATERAL jsonb_array_elements(c.shown_items) e
            WHERE c.id=%(submission)s AND c.reviewed_view
              AND e->>'stream'='census' AND e->>'item'=p.id::text
              AND p.execution NOT IN ('cancelled','superseded')
              AND (e->>'shown'='baseline'
                OR (c.answers #> CASE p.entity_kind
                        WHEN 'family' THEN ARRAY['family',p.field]
                        WHEN 'member' THEN ARRAY['members',p.entity_key,p.field]
                        ELSE ARRAY['proposed_members',p.entity_key] END) IS NOT NULL
                AND stewardship_response_comparison_v1(p.field,
                    c.answers #> CASE p.entity_kind
                        WHEN 'family' THEN ARRAY['family',p.field]
                        WHEN 'member' THEN ARRAY['members',p.entity_key,p.field]
                        ELSE ARRAY['proposed_members',p.entity_key] END)
                  IS NOT DISTINCT FROM stewardship_response_comparison_v1(p.field,
                    CASE WHEN e->>'shown'='family' THEN p.submitted_value
                    ELSE (SELECT v.admin_value FROM stewardship_proposal_review v
                        WHERE v.id::text=e->>'decision') END))))
ORDER BY p.entity_kind, p.entity_key, p.field,
    s.id=%(submission)s DESC, s.family_version DESC
"""
# A Family's active, living Members in the current ParishSoft data, as the
# Family form lists them (``source_inputs``): the Member tabs of a Family
# with no census answers yet (R39 finding 8), and of a current Member the
# response does not name (#2319). Takes %(family)s.
CURRENT_MEMBERS = """
FROM stewardship_source_current c
JOIN stewardship_family_campaign f ON f.id=%(family)s
JOIN stewardship_snapshot_member m ON m.snapshot_id=c.snapshot_id
JOIN stewardship_source_member p ON p.id=m.payload_id
    AND p.family_key=f.family_duid::text
WHERE c.singleton AND p.canonical::jsonb->>'active'='true'
    AND p.canonical::jsonb->>'deceased' IS DISTINCT FROM 'true'
"""
# ``CURRENT_MEMBERS`` as ``(DUID, first, last)`` rows by Member DUID, for a
# Family with no live response.
ROSTER = f"""
SELECT m.source_key, p.canonical::jsonb->>'firstName',
    p.canonical::jsonb->>'lastName'
{CURRENT_MEMBERS}ORDER BY m.source_key::bigint
"""
# The Family's latest live response, preferring the latest that carries
# census answers (``effective.census_submission``: the household's
# home_address key is present exactly when census was answered), with only
# the census parts of its answers. The Member names come from the
# ParishSoft records the Family answered from (each live response's
# reviewed snapshot, the newest naming each Member), so a Member the
# Family no longer shows is still named. The last column is
# ``CURRENT_MEMBERS`` as a JSON array of ``[DUID, first, last]`` by DUID,
# read in the same statement so the page's statement count holds (#2319).
# Takes %(family)s and %(campaign)s.
SUBMISSION = f"""
SELECT s.id,
    jsonb_build_object('family', s.answers->'family',
        'members', s.answers->'members',
        'proposed_members', s.answers->'proposed_members')::text,
    (SELECT coalesce(jsonb_object_agg(n.source_key,
            jsonb_build_array(n.first_name, n.last_name)), '{{}}')::text
     FROM (
        SELECT DISTINCT ON (mm.source_key) mm.source_key,
            sm.canonical::jsonb->>'firstName' AS first_name,
            sm.canonical::jsonb->>'lastName' AS last_name
        FROM stewardship_submission r
        JOIN stewardship_snapshot_member mm ON mm.snapshot_id=r.reviewed_source_id
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
            AND sm.family_key=f.family_duid::text
        WHERE r.family_id=s.family_id AND r.campaign_id=s.campaign_id
            AND r.mode='live'
        ORDER BY mm.source_key, r.family_version DESC
     ) n),
    (SELECT coalesce(jsonb_agg(jsonb_build_array(m.source_key,
            p.canonical::jsonb->>'firstName', p.canonical::jsonb->>'lastName')
        ORDER BY m.source_key::bigint), '[]')::text
     {CURRENT_MEMBERS})
FROM stewardship_submission s
JOIN stewardship_family_campaign f ON f.id=s.family_id
WHERE s.family_id=%(family)s AND s.campaign_id=%(campaign)s AND s.mode='live'
ORDER BY (s.answers->'family'->'home_address') IS NOT NULL DESC,
    s.family_version DESC
LIMIT 1
"""
COLUMNS = (
    "id",
    "entity_kind",
    "entity_key",
    "field",
    "baseline_available",
    "baseline_value",
    "submitted_value",
    "admin_value_set",
    "admin_value",
    "handling",
    "decision",
    "execution",
)
JSON_COLUMNS = ("baseline_value", "submitted_value", "admin_value")


@dataclass(frozen=True)
class FieldRow:
    """One field of a tab: its words and, for a change, its stage.

    ``baseline`` and ``answer`` are display text ("" for none); a False
    ``baseline_read`` says ParishSoft held no value to answer from, which the
    page words "(not read)". ``reviewer`` is the Reviewer's value, "" when
    no reviewer set one. ``placement`` is the change's ``Placement`` (None
    for a report-only one); ``changed`` is False for an unchanged field,
    whose one value is ``answer``. ``note`` is any words shown after the
    stage chip (a decided Ministry request's outcome, ``hub_streams``).
    ``item_id`` is the change's own id (its stream's worklist item) and
    ``item`` that item's row as its stream's review worklist decorates it
    (``hub_actions``, #2064): the icons and dialogs its stream page draws,
    or None while the reader has none.
    """

    label: str
    answer: str
    changed: bool = False
    baseline: str = ""
    baseline_read: bool = True
    reviewer: str = ""
    placement: object = None
    note: str = ""
    item_id: str = ""
    item: object = None

    @property
    def stage_label(self):
        """The stage's shared word, or "" when the change has no stage."""
        return self.placement.stage.label if self.placement else ""

    @property
    def stage_look(self):
        """The chip's status look ("" for the plain chip)."""
        return STAGE_LOOK.get(self.placement.stage, "") if self.placement else ""

    @property
    def failed(self):
        """Whether the change's last update attempt failed."""
        return bool(self.placement and self.placement.failed)


@dataclass(frozen=True)
class Subject:
    """One tab's subject: the Family or one Member.

    ``key`` is the tab's key, ``label`` its name, ``mark`` a ``MARKS`` key
    ("" for a current Member or the Family) and ``rows`` its fields in form
    order. A Member the Family added has ``addition``, the stage of the
    addition itself, and its rows are the Family's answers. ``entity`` is a
    Member's ``(entity_kind, entity_key)`` as its change rows and Ministry
    requests name it (``("member", DUID)`` or ``("proposed_member",
    local id)``); it never enters the address. ``duid`` (a ParishSoft
    Member's DUID, from ``entity``) is shown beside the panel's heading.
    ``actions`` is the census worklist whose dialogs the tab's row actions
    open (``hub_actions``, #2064), None until the tab's actions are read.
    """

    key: str
    label: str
    rows: tuple = ()
    mark: str = ""
    addition: FieldRow | None = None
    entity: tuple = ()
    actions: object = None

    @property
    def duid(self):
        """A ParishSoft Member's DUID for the heading; None for the Family
        (the heading shows the page's Family DUID) and for a Member the
        Family added, who has none yet."""
        kind, key = self.entity or ("", "")
        return int(key) if kind == "member" else None

    @property
    def changed(self):
        """The fields with a change in force."""
        return tuple(row for row in self.rows if row.changed)

    @property
    def unchanged(self):
        """The fields whose answer matched ParishSoft."""
        return tuple(row for row in self.rows if not row.changed)

    @property
    def actions_column(self):
        """Whether the Changed table draws an Actions column: a change on
        the tab offers or shows an action for this reader."""
        return any(row.item and row.item["icons"] for row in self.changed)

    @property
    def acting(self):
        """Whether the tab offers or shows any row action (its changes' or
        a New Member's addition's), so it draws its stream's dialogs."""
        addition = self.addition
        return self.actions_column or bool(
            addition and addition.item and addition.item["icons"]
        )

    @property
    def reviewer_column(self):
        """Whether any change on the tab has a Reviewer's value."""
        return any(row.reviewer for row in self.changed)

    @property
    def mark_label(self):
        """The mark's badge word."""
        return MARKS[self.mark][0] if self.mark else ""

    @property
    def mark_note(self):
        """What the mark means for the tab's fields."""
        return MARKS[self.mark][1] if self.mark else ""

    @property
    def awaiting(self):
        """Whether the tab is a ParishSoft Member with no census answers yet
        (no response, one from before census, or one from before ParishSoft
        added the Member to the Family)."""
        return self.mark in {"unsubmitted", "unanswered", "joined"}


@dataclass(frozen=True)
class HubCensus:
    """The census tabs of one Family's hub.

    ``family`` is the Family tab's subject (None before any census
    response) and ``members`` the Member tabs' subjects in tab order.
    ``census`` is whether census is part of the campaign: without it the
    Member tabs hold no census rows, only their names and entities (their
    panels show Ministry joins and leaves, ``hub_streams``).
    """

    family: Subject | None = None
    members: tuple = ()
    census: bool = True

    def subject(self, key):
        """The subject a tab key names, or None."""
        return next(
            (item for item in (self.family, *self.members) if item and item.key == key),
            None,
        )


def read_hub_census(campaign_id, family_id, *, census):
    """Read one Family's census tabs, in one or two statements.

    The caller holds the page's read guard and has checked the reader. The
    first statement reads the latest live response with the Family's
    current ParishSoft Members; with ``census`` and a response, a second
    reads its changes in force. With no live response, the second reads
    the current Members (``ROSTER``) for ``unsubmitted``'s tabs. A response
    that gives no Member tab (none with census answers) takes its current
    Members from the first statement.
    """
    parameters = {"family": family_id, "campaign": campaign_id}
    with connection.cursor() as cursor:
        cursor.execute(SUBMISSION, parameters)
        found = cursor.fetchone()
        if found is None:
            cursor.execute(ROSTER, parameters)
            return unsubmitted(list(cursor), census=census, responded=False)
        submission, answers, names, roster = found
        rows = []
        if census:
            cursor.execute(
                PROPOSALS,
                parameters | {"submission": submission, "terminal": list(TERMINAL)},
            )
            rows = [dict(zip(COLUMNS, values, strict=True)) for values in cursor]
    for row in rows:
        # JSON values arrive as text (cast in PROPOSALS) and are decoded once.
        for name in JSON_COLUMNS:
            row[name] = None if row[name] is None else json.loads(row[name])
    roster = [tuple(row) for row in json.loads(roster)]
    result = shape(json.loads(answers), json.loads(names), rows, roster, census=census)
    if result.members:
        return result
    return unsubmitted(roster, family=result.family, census=census, responded=True)


def unsubmitted(roster, *, family=None, census=True, responded=False):
    """The hub's tabs for a Family whose response gives no Member tab.

    ``roster`` holds ``ROSTER``'s ``(DUID, first, last)`` rows. Each Member
    gets a tab of its own, in DUID order and named from ParishSoft, holding
    no fields. It is marked No submission yet when the Family has no live
    response (``responded`` False), else No census answers yet: the Family
    answered before census joined the campaign. ``family`` is the Family
    tab's subject, kept as the response gave it (None before any census
    answers).
    """
    mark = "unanswered" if responded else "unsubmitted"
    return HubCensus(
        family,
        tuple(
            Subject(
                f"member-{place}",
                member_label(None, [first, last]),
                mark=mark,
                entity=("member", key),
            )
            for place, (key, first, last) in enumerate(roster, 1)
        ),
        census,
    )


def member_label(answer, source):
    """A Member's name, "Last, first", from the Family's answer, else ParishSoft's.

    ``answer`` is the Member's census answer (a dict, possibly without
    names, as a moved or deceased Member's is) and ``source`` the
    ``[first, last]`` pair of the ParishSoft record, or None.
    """
    first, last = (answer or {}).get("first_name"), (answer or {}).get("last_name")
    if not (first or last) and source:
        first, last = source
    parts = [part.strip() for part in (last, first) if part and part.strip()]
    return ", ".join(parts) or str(_(NAME_NOT_RECORDED))


def placed(row):
    """A change row's ``Placement``, the review worklist's own mapping.

    ``census_placement`` refuses a state the SQL guards never store. One
    such row must not take the whole Family page (its Timeline tab
    included) down with it, so it is logged and shown with no stage.
    """
    try:
        return census_placement(
            decision=row["decision"],
            execution=row["execution"],
            handling=row["handling"],
            entity_kind=row["entity_kind"],
            admin_value_set=row["admin_value_set"],
        )
    except ValueError as error:
        emit_failure(
            error,
            event=Event.REPORT_SHAPING_FAILED,
            level=logging.WARNING,
            shaping="hub_census_stage",
        )
        return None


def changed_row(row, placement):
    """A field with a change in force, worded as Census changes words it."""
    return FieldRow(
        label=LABELS.get(row["field"], row["field"]),
        answer=family_answer(row),
        changed=True,
        baseline=display(row["baseline_value"]) if row["baseline_available"] else "",
        baseline_read=bool(row["baseline_available"]),
        reviewer=display(row["admin_value"]) if row["admin_value_set"] else "",
        placement=placement,
        item_id=row.get("id", ""),
    )


def field_rows(names, values, changes):
    """The rows for ``names`` (field names in form order), then any other change.

    ``values`` are the Family's answers by field and ``changes`` the
    ``(row, placement)`` pairs in force by field. A field with a change
    shows it; any other answered field is unchanged.
    """
    rows = []
    for name in [*names, *(name for name in changes if name not in names)]:
        if name in changes:
            rows.append(changed_row(*changes[name]))
        elif name in values:
            answer = family_answer({"field": name, "submitted_value": values[name]})
            rows.append(FieldRow(LABELS.get(name, name), answer))
    return tuple(rows)


def shape(answers, names, rows, roster=(), *, census=True):
    """The tabs' subjects from a response's census answers and its changes.

    ``answers`` holds the response's ``family``, ``members`` and
    ``proposed_members``; ``names`` maps a Member DUID to its ParishSoft
    ``[first, last]``; ``rows`` are the decoded ``PROPOSALS`` rows. A row
    kept only for history (superseded or cancelled) is no change.
    ``roster`` holds the Family's current ParishSoft Members as ``ROSTER``'s
    ``(DUID, first, last)`` rows: each one the response does not name
    joined the Family after it answered (#2319) and gets a fieldless
    Joined tab, named from ParishSoft, after the response's own Members and
    before those only earlier requests keep.
    Member tabs are keyed by their place (``member-1``, ...) so no Member
    identifier enters the address.
    """
    changes = {}
    for row in rows:
        placement = placed(row)
        if placement is not None and placement.stage is Stage.HISTORY:
            continue
        kind = row["entity_kind"]
        # A household change is about the Family, whatever its key.
        key = "" if kind == "family" else row["entity_key"]
        changes.setdefault((kind, key), {})[row["field"]] = (row, placement)
    members = answers.get("members") or {}
    proposed = answers.get("proposed_members") or {}
    if census and "home_address" not in (answers.get("family") or {}):
        # No response carries census answers yet (census joined the
        # campaign after the Family answered): nothing to compare.
        return HubCensus(census=census)
    family = None
    if census:
        family = Subject(
            "family",
            _("Family"),
            field_rows(
                [field.name for field in FAMILY_FIELDS],
                answers["family"],
                changes.get(("family", ""), {}),
            ),
        )
    subjects = []
    # The response's Members by DUID, then current ones it does not name,
    # then those only earlier requests keep.
    kept = {key for kind, key in changes if kind == "member"} - set(members)
    # A current Member the response does not name, when the response gives
    # Member tabs at all; one that gives none falls back to ``unsubmitted``
    # (No census answers yet), as before #2319.
    joined = {}
    if members or kept or proposed:
        joined = {
            key: [first, last]
            for key, first, last in roster
            if key not in members and key not in kept
        }
    order = [*sorted(members, key=int), *sorted(joined, key=int)]
    for key in [*order, *sorted(kept, key=int)]:
        if key in joined:
            subjects.append(
                (member_label(None, joined[key]), "joined", (), None, ("member", key))
            )
            continue
        answer = members.get(key)
        mine = changes.get(("member", key), {})
        if answer is None:
            mark, fields = "missing", list(TERMINAL)
        elif answer.get("moved_household") is True:
            mark, fields = "moved", ["moved_household"]
        elif answer.get("deceased_status") is True:
            mark, fields = "deceased", ["deceased_status", "death_date"]
        else:
            mark, fields = "", [field.name for field in MEMBER_FIELDS]
        subjects.append(
            (
                member_label(answer, names.get(key)),
                mark,
                field_rows(fields, answer or {}, mine) if census else (),
                None,
                ("member", key),
            )
        )
    added = []
    for key, answer in proposed.items():
        row = changes.get(("proposed_member", key), {}).get("new_member")
        addition = changed_row(*row) if row else None
        values = tuple(
            FieldRow(
                field.label,
                family_answer(
                    {"field": field.name, "submitted_value": answer.get(field.name)}
                ),
            )
            for field in MEMBER_FIELDS
        )
        added.append(
            (
                member_label(answer, None),
                "new",
                values,
                addition,
                ("proposed_member", key),
            )
        )
    subjects += sorted(added, key=lambda item: item[0].casefold())
    return HubCensus(
        family,
        tuple(
            Subject(f"member-{place}", label, fields, mark, addition, entity)
            for place, (label, mark, fields, addition, entity) in enumerate(subjects, 1)
        ),
        census,
    )
