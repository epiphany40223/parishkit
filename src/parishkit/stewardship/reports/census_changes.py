"""Census changes: the worklist of reported census changes for ParishSoft (#528).

Every census change a Family reported becomes a proposed-change row (one per
atomic request, ``responses/proposals.py``). This report reads those rows for
one campaign's live responses, as they are, and words them for the Admin and
Staff who carry them into ParishSoft, by hand or by publication: who and what
changed, ParishSoft's value now, the Family's answer and any Administrator
edit, how the change reaches ParishSoft, and a status derived from the row's
decision and execution (reports spec, "Census change rows"). It adds no state
of its own and changes nothing.

The rows are read with one parameterised query on the web login, which may
read the proposal, submission and source tables (``responses/grants.py``);
filtering, sorting and wording happen here, in memory, as the Limitations report
does, so they are plain functions a unit test can call.
"""

import csv
import io
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.pipeline import (
    Decision,
    Placement,
    Stage,
    census_addition_placement,
    census_placement,
)
from parishkit.stewardship.responses.census import (
    FAMILY_FIELDS,
    address_editor,
)
from parishkit.stewardship.responses.census_additions import ADDED_PREFIX
from parishkit.stewardship.responses.census_resolution import allowed
from parishkit.stewardship.responses.census_review import (
    ADDRESS_FIELDS,
    FIELDS,
    edit_limit,
    editable,
    resolvable,
    reviewable,
)
from parishkit.stewardship.responses.comparison import (
    ADDRESS_COMPONENTS,
    ValueKind,
    canonical_value,
)
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS, browser_value
from parishkit.stewardship.responses.member_requests import REQUEST_FIELDS
from parishkit.stewardship.responses.merge import KnownValue
from parishkit.stewardship.responses.refresh_checks import (
    FINAL_EXECUTIONS,
    check_states,
)
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.source_status import (
    NAME_NOT_RECORDED,
    STATUS_HEADING,
    family_status,
    source_status_label,
)
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone, browser_day_start
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.review_worklist import NOT_SEEN

# Plain labels for "What changed", from the census forms' own field labels.
LABELS = {
    **{field.name: field.label for field in FAMILY_FIELDS},
    **{field.name: field.label for field in MEMBER_FIELDS},
    **{field.name: field.label for field in REQUEST_FIELDS},
    "new_member": "New Member",
}
MEMBER_LABELS = {field.name: field.label for field in MEMBER_FIELDS}

# Statuses in the order the filter lists them; the first three are the work
# still open. History rows (superseded or cancelled) appear only on request.
STATUSES = {
    "to_review": "To review",
    "to_do": "To do",
    "conflict": "Conflict",
    "being_published": "Being published",
    "published": "Published",
    "already": "Already in ParishSoft",
    "entered": "Entered by hand",
    "ignored": "Ignored",
    "superseded": "Superseded",
    "cancelled": "Cancelled",
}
HISTORY = frozenset({"superseded", "cancelled"})
# The review worklist's Stage filter (#1683): the stages a census change
# reaches, in the order the filter lists them, then History (superseded and
# cancelled rows) and Every stage, which counts everything but History.
# Entered incorrectly and the Not seen in ParishSoft flag come from the
# refresh checks (#1689); the flag is a choice of its own, after the stages.
STAGES = (
    Stage.TO_REVIEW,
    Stage.CONFLICT,
    Stage.READY_TO_UPDATE,
    Stage.UPDATING,
    Stage.TO_ENTER_BY_HAND,
    Stage.ENTERED_BY_HAND,
    Stage.ENTERED_INCORRECTLY,
    Stage.UPDATED,
    Stage.CONFIRMED,
    Stage.IGNORED,
)
EVERY_STAGE = "all"
STAGE_CHOICES = (
    *(stage.value for stage in STAGES),
    NOT_SEEN,
    Stage.HISTORY.value,
    EVERY_STAGE,
)
# The history line of a change a Family's same answer reopened over an
# accepted ParishSoft value (the review pipeline's resubmission rule, #1683).
REOPENED = (
    "Reopened: the Family answered again, so the earlier Accept over"
    " ParishSoft's changed value no longer counts."
)
# A terminal execution is the outcome, whatever the decision says.
TERMINAL = {
    "published": "published",
    "resolved_upstream": "already",
    "resolved_external": "entered",
    "superseded": "superseded",
    "cancelled": "cancelled",
}
STATUS_CHOICES = ("open", *[key for key in STATUSES if key not in HISTORY], "all")
ROUTES = ("any", "automatic", "by_hand")
KINDS = ("any", "contact", "moved", "deceased", "new_member")
EARLIEST, LATEST = date(2000, 1, 1), date(2999, 12, 31)
# The download's columns: the page's columns, names and order (#932), so
# Member DUID sits right after Who in every format. ParishSoft status follows
# Family, blank unless the data no longer has it: the page's badge beside the
# name has a column of its own in a file (#1216). The latest Entered in
# ParishSoft, Ignore or Reopen (#528) follows Submitted, as on the page: who,
# when and its note.
HEADINGS = (
    "Family",
    STATUS_HEADING,
    "Family DUID",
    "Who",
    "Member DUID",
    "What changed",
    "ParishSoft now",
    "Family's answer",
    "Reviewer's value",
    "How it reaches ParishSoft",
    "Stage",
    "Submitted",
    "Recorded by",
    "Recorded at",
    "Note",
)
# Relative PDF column widths, in percent of the page's body width. Fifteen
# columns share one landscape page, so a compact time ("Sep 30, 2026
# 12:04 PM") keeps its date on one line and its time on the next, and a
# status may take two lines ("Being" / "published"); a whole DUID and
# "Automatic" stay on one line, and ParishSoft status, blank on most rows,
# takes Family's narrow width. The resolution's who and note get room of
# their own, and the values Families typed and ParishSoft holds share the
# rest, long ones wrapping within their column.
PDF_WEIGHTS = {
    "Family": 5,
    STATUS_HEADING: 5,
    "Family DUID": 7,
    "Who": 5,
    "Member DUID": 7,
    "What changed": 5,
    "ParishSoft now": 6,
    "Family's answer": 6,
    "Reviewer's value": 4,
    "How it reaches ParishSoft": 8,
    "Stage": 8,
    "Submitted": 10,
    "Recorded by": 7,
    "Recorded at": 10,
    "Note": 7,
}
PRIVACY = "Sensitive parish report. Share only with authorized recipients."
# The report's name: the PDF's page title, the XLSX sheet and workbook title.
TITLE = "Census changes"


@dataclass(frozen=True, repr=False)
class CensusQuery:
    """The page's filters; identifying search text travels only in POST bodies.

    ``stage`` is the review worklist's Stage filter (#1683), which the page
    always sends (To review by default); when it is set, ``status`` and
    ``history`` are not used. ``pk-admin report census`` and ``export
    census`` still filter by ``status``, whose "open" default is To do and
    Conflict, plus To review for an Administrator, until #1688 renames
    them. ``start`` and ``end`` are whole days in ``zone``, as on System
    logs; only those commands set them, since the page has no date filter
    (#1888) and drops a stale one unread.
    """

    search: str = ""
    stage: str = ""
    status: str = "open"
    history: str = ""
    route: str = "any"
    kind: str = "any"
    start: str = ""
    end: str = ""
    zone: str = ""

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values from the closed choices."""
        if type(parameters) is dict:
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        query = cls(**filters(parameters, allowed=set(cls.__dataclass_fields__)))
        bounded_text(query.search)
        if (
            query.status not in STATUS_CHOICES
            or (query.stage and query.stage not in STAGE_CHOICES)
            or query.history not in {"", "yes"}
            or query.route not in ROUTES
            or query.kind not in KINDS
        ):
            raise ValueError("Invalid census change filter.")
        for value in (query.start, query.end):
            if not value:
                continue
            day = date.fromisoformat(value)
            if day.isoformat() != value or not EARLIEST <= day <= LATEST:
                raise ValueError("Invalid census change date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid census change date interval.")
        if query.zone not in timezone_names():
            # Days cannot be placed without the CLI's --zone; without days
            # the zone is unused, so an unknown one is simply dropped.
            if query.start or query.end:
                raise UnknownZone("Census change dates need the browser's zone.")
            query = replace(query, zone="")
        return query

    def form_values(self):
        """Values for CSRF-protected re-submission and downloads."""
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def bounds(self):
        """The From and Through days as a UTC interval; None where unset."""
        start, end = (
            date.fromisoformat(value) if value else None
            for value in (self.start, self.end)
        )
        return (
            browser_day_start(start, self.zone) if start else None,
            browser_day_start(end + timedelta(days=1), self.zone) if end else None,
        )


# One row per proposal of this campaign's live responses. Family names come
# from the current ParishSoft source as "Surname, heads", built by the shared
# SQL lookup (migration 0045's stewardship_family_display_name_v1, the twin of
# snapshot_family_names), so the page, its search and sort and the downloads
# name a Family as every Admin table does (#932); a
# Member's name is the Family's own corrected one first, then the parish
# record's. A Family the source no longer has reads as NULL, which the page
# marks "Not in ParishSoft"; one without a usable name reads as the shared
# "Name not recorded in ParishSoft" (the first parameter, #1216).
ROWS = """
SELECT p.id::text, p.entity_kind, p.entity_key, p.field,
    p.baseline_available, p.baseline_value::text, p.submitted_value::text,
    p.current_available, p.current_value::text, p.admin_value_set,
    p.admin_value::text,
    p.handling, p.decision, p.execution, s.submitted_at, f.family_duid,
    stewardship_family_display_name_v1(sc.snapshot_id, f.family_duid, %s)
        AS family_name,
    coalesce(nullif(btrim(concat_ws(' ',
            nullif(btrim(s.answers->'members'->p.entity_key->>'first_name'),''),
            nullif(btrim(s.answers->'members'->p.entity_key->>'last_name'),''))),''),
        nullif(btrim(concat_ws(' ',
            nullif(btrim(sm.canonical::jsonb->>'firstName'),''),
            nullif(btrim(sm.canonical::jsonb->>'lastName'),''))),'')) AS member_name,
    sc.snapshot_id,
    p.version, latest.action, latest.note, latest.created_at, latest.email,
    -- A Family's same answer that replaced an approval resting on an
    -- accepted ParishSoft value (#528, ADM-09.03): the base was not carried,
    -- so the change came back to review, which the page says (#1683).
    EXISTS (SELECT 1 FROM stewardship_proposed_change pr
        JOIN stewardship_proposal_review base ON base.proposal_id=pr.id
            AND base.accepted_base AND base.accepted_source_id=pr.current_source_id
        WHERE pr.superseded_by_id=p.id AND pr.decision='approved'
          -- The same answer again: a different one is a new request.
          AND stewardship_response_comparison_v1(p.field,pr.submitted_value)
              IS NOT DISTINCT FROM
              stewardship_response_comparison_v1(p.field,p.submitted_value)
          AND NOT EXISTS (SELECT 1 FROM stewardship_proposal_review later
              WHERE later.proposal_id=pr.id
                AND later.expected_version>base.expected_version
                AND later.decision<>'approved')) AS reopened,
    -- A Member change's Member standing in the current data, by the shared
    -- status (#1217): a change whose Member left the Family keeps its stage
    -- (rule 4, #1738), and the Who cell shows the shared badge for it.
    CASE WHEN p.entity_kind='member' THEN stewardship_member_source_status_v1(
        sc.snapshot_id, p.entity_key, f.family_duid) END AS member_status,
    -- The response's place in its Family's order, which tells a final change
    -- a later response replaced (#1737).
    s.family_version
FROM stewardship_campaign c
LEFT JOIN stewardship_source_current sc ON sc.singleton
JOIN stewardship_submission s ON s.campaign_id=c.id AND s.mode='live'
JOIN stewardship_proposed_change p ON p.submission_id=s.id
JOIN stewardship_family_campaign f ON f.id=s.family_id
LEFT JOIN stewardship_snapshot_member mm ON p.entity_kind='member'
    AND mm.snapshot_id=sc.snapshot_id AND mm.source_key=p.entity_key
LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
-- The latest Staff or Admin resolution or Administrator review (#528),
-- with who made it. Each advances the proposal's version, so the two
-- histories never share one.
LEFT JOIN LATERAL (
    SELECT h.action, h.note, h.created_at, u.email
    FROM (
        SELECT r.action, r.note, r.created_at, r.actor_id, r.expected_version
        FROM stewardship_proposal_resolution r WHERE r.proposal_id=p.id
        UNION ALL
        SELECT v.decision, v.note, v.created_at, v.actor_id, v.expected_version
        FROM stewardship_proposal_review v WHERE v.proposal_id=p.id
    ) h
    LEFT JOIN stewardship_portal_user u ON u.id=h.actor_id
    ORDER BY h.expected_version DESC LIMIT 1
) latest ON true
WHERE c.id=%s AND p.handling<>'report-only'
ORDER BY f.family_duid, s.submitted_at, p.entity_kind,
    p.entity_key, p.field
"""
# One row per census change the parish added (#1710), named as ROWS names
# a change: the Family from the shared lookup, a Member from the current
# ParishSoft source (an addition has no Family answer to take a name from).
# Its creator, the note and its later step (Entered by hand or withdrawn)
# come with it. Takes the unnamed-Family words, then the campaign.
ADDITION_ROWS = """
SELECT a.id::text, a.entity_kind, a.entity_key, a.field, a.value::text,
    a.in_place_of_id::text, a.created_at, a.baseline::text, f.family_duid,
    stewardship_family_display_name_v1(sc.snapshot_id, f.family_duid, %s)
        AS family_name,
    nullif(btrim(concat_ws(' ',
        nullif(btrim(sm.canonical::jsonb->>'firstName'),''),
        nullif(btrim(sm.canonical::jsonb->>'lastName'),''))),'') AS member_name,
    sc.snapshot_id, u.email, a.note, r.action, r.note, r.created_at, ru.email
FROM stewardship_census_addition a
JOIN stewardship_family_campaign f ON f.id=a.family_id
LEFT JOIN stewardship_source_current sc ON sc.singleton
LEFT JOIN stewardship_snapshot_member mm ON a.entity_kind='member'
    AND mm.snapshot_id=sc.snapshot_id AND mm.source_key=a.entity_key
LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
LEFT JOIN stewardship_census_addition_resolution r ON r.addition_id=a.id
LEFT JOIN stewardship_portal_user u ON u.id=a.actor_id
LEFT JOIN stewardship_portal_user ru ON ru.id=r.actor_id
WHERE f.campaign_id=%s
ORDER BY f.family_duid, a.created_at, a.entity_kind, a.entity_key, a.field
"""
ADDITION_COLUMNS = (
    "id",
    "entity_kind",
    "entity_key",
    "field",
    "value",
    "in_place_of",
    "created_at",
    "baseline",
    "family_duid",
    "family_name",
    "member_name",
    "snapshot_id",
    "added_by",
    "added_note",
    "step",
    "step_note",
    "step_at",
    "step_by",
)
# How an addition's state reads as a proposal's execution, so the shared
# shaping words it (``status``: To do, Entered by hand, Cancelled).
ADDITION_EXECUTION = {
    "open": "pending",
    "entered": "resolved_external",
    "withdrawn": "cancelled",
}
COLUMNS = (
    "id",
    "entity_kind",
    "entity_key",
    "field",
    "baseline_available",
    "baseline_value",
    "submitted_value",
    "current_available",
    "current_value",
    "admin_value_set",
    "admin_value",
    "handling",
    "decision",
    "execution",
    "submitted_at",
    "family_duid",
    "family_name",
    "member_name",
    "snapshot_id",
    "version",
    "resolution_action",
    "resolution_note",
    "resolution_at",
    "resolution_by",
    "reopened",
    "member_status",
    "family_version",
)

# The menu's open count (#585, #1590; admin_context.OPEN_COUNTS embeds it):
# ROWS's rows, by the four columns ``status`` reads, each group with its
# size, as JSON text. Its FROM and WHERE are ROWS's without the joins that
# only name a row (they are LEFT joins, so they never add or drop one), so
# ``open_count`` places exactly the rows the page places.
# Takes ``%(campaign)s``.
OPEN_GROUPS = """
SELECT coalesce(jsonb_agg(jsonb_build_array(
        g.handling, g.entity_kind, g.decision, g.execution, g.n)), '[]')::text
FROM (
    SELECT p.handling, p.entity_kind, p.decision, p.execution, count(*) AS n
    FROM stewardship_campaign c
    JOIN stewardship_submission s ON s.campaign_id=c.id AND s.mode='live'
    JOIN stewardship_proposed_change p ON p.submission_id=s.id
    JOIN stewardship_family_campaign f ON f.id=s.family_id
    WHERE c.id=%(campaign)s AND p.handling<>'report-only'
    GROUP BY 1, 2, 3, 4
) g
"""


# The stages at which a Family's change is still open: decided, or still
# to be, but not yet in ParishSoft. An addition of the same field beside one
# says so (#1710).
OPEN_CHANGE_STAGES = frozenset(
    stage.value
    for stage in (
        Stage.TO_REVIEW,
        Stage.CONFLICT,
        Stage.READY_TO_UPDATE,
        Stage.UPDATING,
        Stage.TO_ENTER_BY_HAND,
    )
)
# The stages the menu and Home count (``open_count``).
OPEN_STAGES = frozenset({Stage.TO_REVIEW, Stage.CONFLICT})


def open_count(groups):
    """The rows waiting on a reviewer, To review and Conflict, from ``OPEN_GROUPS``.

    ``groups`` is the decoded JSON: ``[handling, entity_kind, decision,
    execution, count]`` lists. The menu and Home count the groups
    ``census_placement`` puts at To review or Conflict, for every role that
    may read the page (Administrator decision, 2026-10-18). It is the
    mapping the page itself places rows with, so the count cannot drift
    from the page's stages. Ready to update is not counted: Publish to
    ParishSoft and the manual-entry report own it. An edited value never
    changes a stage, so the groups need not carry it.
    """
    return sum(
        count
        for handling, entity_kind, decision, execution, count in groups
        if census_placement(
            decision=decision,
            execution=execution,
            handling=handling,
            entity_kind=entity_kind,
            admin_value_set=False,
        ).stage
        in OPEN_STAGES
    )


# The Review buttons' form values and the decision each records, in order.
REVIEW_CHOICES = (
    ("approve", "approved"),
    ("ignore_automatic", "ignored"),
    ("to_review", "unreviewed"),
)

JSON_COLUMNS = ("baseline_value", "submitted_value", "current_value", "admin_value")


def census_changes(campaign_id, query, principal):
    """Read, word and filter the campaign's census changes for Admin or Staff."""
    if not allows(principal, Capability.VIEW_CENSUS):
        raise PermissionError("Census changes are unavailable.")
    administrator = allows(principal, Capability.PUBLISH_CENSUS)
    changes = census_rows(campaign_id)
    added = addition_rows(campaign_id)
    result = select(changes + added, query, administrator=administrator)
    # What the worklist's Add needs from every row, whatever the filters:
    # the ignored changes an addition may be added in place of, and each
    # one's addition, which its history line names (#1710).
    result["replaceable"] = [
        (
            row["id"],
            f"{row['label']} for {row['who']}, Family DUID {row['family_duid']}",
        )
        for row in changes
        if row["stage"] == Stage.IGNORED.value
    ]
    result["replacements"] = link_additions(changes, added)
    # The reader's pipeline roles on the census stream (the review
    # worklist's icons and bulk bar follow them): Staff hold the census
    # capability, an Administrator also publishes.
    result["roles"] = frozenset(
        role
        for role, capability in (
            ("staff", Capability.MANUAL_CENSUS),
            ("administrator", Capability.PUBLISH_CENSUS),
        )
        if allows(principal, capability)
    )
    return result


def _key(row):
    """A row's Family, Member (or Family) and field."""
    return (row["family_duid"], row["entity_kind"], row["entity_key"], row["field"])


def link_additions(changes, added):
    """Tie the parish's additions to the Family's changes of the same field.

    Returns each ignored change's additions made in place of it (its
    history line names them), and marks rows in place: an open addition
    whose field the Family has an open change of too (``overlap``, from a
    later response), and an ignored change an open addition of its field
    covers (``covered``: it is not sent back to review while that addition
    is open, so the field never has two open changes, #1710).
    """
    replacements = {}
    for row in added:
        if row["in_place_of"] and row["stage"] != Stage.HISTORY.value:
            replacements.setdefault(row["in_place_of"], []).append(
                f"{row['label']}: {row['edited']}"
            )
    # An open addition whose field the Family has an open change of too (a
    # later response asked again): the row says so, so one is withdrawn.
    asked = {_key(row) for row in changes if row["stage"] in OPEN_CHANGE_STAGES}
    for row in added:
        key = _key(row)
        row["overlap"] = row["stage"] == Stage.TO_ENTER_BY_HAND.value and key in asked
    # An ignored change an open addition of its field covers is not sent
    # back to review while it is open (``census_resolution.
    # covered_by_addition`` refuses it too), so the field never has two.
    covering = {
        _key(row) for row in added if row["stage"] == Stage.TO_ENTER_BY_HAND.value
    }
    for row in changes:
        key = _key(row)
        row["covered"] = row["stage"] == Stage.IGNORED.value and key in covering
    return replacements


def census_rows(campaign_id):
    """Every census change of the campaign, read and worded, unfiltered.

    The caller has checked who may read them. Publish to ParishSoft (#528)
    reads its plan's values from these rows, so both pages word a change
    the same way.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM stewardship_campaign WHERE id=%s", [campaign_id])
        if cursor.fetchone() is None:
            raise ReadUnavailable("Census change inputs are unavailable.")
        cursor.execute(ROWS, [NAME_NOT_RECORDED, campaign_id])
        rows = [dict(zip(COLUMNS, values, strict=True)) for values in cursor]

    # The refresh checks (#1689) of the final changes, which place them: only
    # those at the change's current version, since a mark made again over
    # Entered incorrectly moves it on and the checks before no longer count
    # (#1737).
    # A final change a later response of the Family replaced (the same field
    # answered again; ``refresh_checks.replaced``) is history and placed by
    # its execution alone.
    def key(row):
        """The Family's field a change answers."""
        return (row["family_duid"], row["entity_kind"], row["entity_key"], row["field"])

    latest = {}
    for row in rows:
        latest[key(row)] = max(latest.get(key(row), 0), row["family_version"])
    final = [
        row
        for row in rows
        if row["execution"] in FINAL_EXECUTIONS
        and row["family_version"] == latest[key(row)]
    ]
    checks = check_states(
        proposals=[row["id"] for row in final],
        versions={row["id"]: row["version"] for row in final},
    )
    for row in rows:
        # JSON values arrive as text (cast in ROWS), whatever the driver's
        # own JSON handling, and are decoded once here.
        for name in JSON_COLUMNS:
            row[name] = None if row[name] is None else json.loads(row[name])
        row["check"] = checks.get(row["id"])
    return [shape(row) for row in rows]


def addition_rows(campaign_id):
    """Every census change the parish added in the campaign, worded (#1710).

    The caller has checked who may read them. Each is shaped as a change
    is (``shape_addition``), so the page, its filters and the downloads
    treat both alike.
    """
    with connection.cursor() as cursor:
        cursor.execute(ADDITION_ROWS, [NAME_NOT_RECORDED, campaign_id])
        rows = [dict(zip(ADDITION_COLUMNS, values, strict=True)) for values in cursor]
    return [shape_addition(row) for row in rows]


def shape_addition(raw):
    """Word one parish-added change as ``shape`` words a Family's change.

    It reads as a change that is accepted with a reviewer's value and no
    Family's answer, By hand (nothing writes an addition to ParishSoft
    until #1688), in the stage ``census_addition_placement`` gives it:
    To enter by hand while open, then Entered by hand, or History once
    withdrawn. Its version is 1 while open and 2 after its one later step.
    Its last decision is that step, else the Add itself (who, when, the
    note). Its Submitted time is when it was added, and its Baseline
    ParishSoft's value then, where the source could read it.
    """
    state = raw["step"] or "open"
    value = json.loads(raw["value"])
    # ParishSoft's value when the change was added, where it could be read.
    baseline = json.loads(raw["baseline"]) if raw["baseline"] else {}
    known = baseline.get("available") is True
    shown = shape(
        {
            "id": ADDED_PREFIX + raw["id"],
            "entity_kind": raw["entity_kind"],
            "entity_key": raw["entity_key"],
            "field": raw["field"],
            "baseline_available": known,
            "baseline_value": baseline.get("value") if known else None,
            "submitted_value": None,
            "current_available": False,
            "current_value": None,
            "admin_value_set": True,
            "admin_value": value,
            "handling": "manual",
            "decision": "approved",
            "execution": ADDITION_EXECUTION[state],
            "submitted_at": raw["created_at"],
            "family_duid": raw["family_duid"],
            "family_name": raw["family_name"],
            "member_name": raw["member_name"],
            "snapshot_id": raw["snapshot_id"],
            "version": 1 if state == "open" else 2,
            "resolution_action": raw["step"] or "added",
            "resolution_note": raw["step_note"] if raw["step"] else raw["added_note"],
            "resolution_at": raw["step_at"] if raw["step"] else raw["created_at"],
            "resolution_by": raw["step_by"] if raw["step"] else raw["added_by"],
            "reopened": False,
        }
    )
    placement = census_addition_placement(state)
    return shown | {
        "added": True,
        "in_place_of": raw["in_place_of"],
        "placement": placement,
        "stage": placement.stage.value,
        "stage_label": placement.stage.label,
        # Nothing but its own later step: no review, edit or conflict.
        "family_answer": "",
        "editor": None,
        "edit": None,
        "review_actions": [],
        "resolve_conflict": False,
        "resolvable": False,
        "can_reopen": False,
        "can_resolve": state == "open",
    }


def automatic(row):
    """Whether publication may carry this change to ParishSoft.

    Only fields the handling registry marks API-writable, and never a Family
    address: no verified ParishSoft address read exists, so publication could
    neither detect a conflict nor confirm the write (reports spec, "Manual
    census resolution"). Those are worked by hand until one does. The
    resolution service applies the same rule (``census_resolution.by_hand``).
    """
    return row["handling"] == "api" and row["entity_kind"] != "family"


def status(row):
    """The row's status key, from its decision and execution.

    A terminal execution decides first, since it is the outcome; then an
    ignored decision; then the execution. A pending automatic change no
    Administrator has decided yet is To review; other pending rows, and a
    failed publication, are To do.
    """
    execution, decision = row["execution"], row["decision"]
    if execution in TERMINAL:
        return TERMINAL[execution]
    if decision == "ignored":
        return "ignored"
    if execution == "queued":
        return "being_published"
    if execution == "conflict":
        return "conflict"
    if execution == "pending" and decision == "unreviewed" and automatic(row):
        return "to_review"
    return "to_do"


def kind(row):
    """The kind-of-change filter's group for the row."""
    if row["field"] == "new_member":
        return "new_member"
    if row["field"] == "moved_household":
        return "moved"
    if row["field"] in {"deceased_status", "death_date"}:
        return "deceased"
    return "contact"


def display(value):
    """A stored value as plain text: an address on one line, a Member's name
    and details, Yes or No for a choice, and an empty string for none."""
    if value is None:
        return ""
    if type(value) is bool:
        return "Yes" if value else "No"
    if isinstance(value, list):
        return "; ".join(display(item) for item in value)
    if not isinstance(value, dict):
        return str(value)
    if "display" in value:
        # A phone keeps its own display alongside its comparison key.
        return display(value["display"])
    if set(value) <= set(ADDRESS_COMPONENTS):
        return ", ".join(
            display(value[part]) for part in ADDRESS_COMPONENTS if value.get(part)
        )
    if set(value) <= set(MEMBER_LABELS):
        name = " ".join(
            display(value[part])
            for part in ("first_name", "last_name")
            if value.get(part)
        )
        details = [
            f"{MEMBER_LABELS[key]}: {display(item)}"
            for key, item in value.items()
            if key not in {"first_name", "last_name"} and display(item)
        ]
        return "; ".join(part for part in (name, *details) if part)
    return "; ".join(f"{key}: {display(item)}" for key, item in value.items())


def member_duid(row):
    """The ParishSoft Member DUID a Member row is about, else None.

    A Member change's entity key is the Member's DUID (the snapshot's source
    key). A proposed Member has no DUID yet and a household change is about
    the Family, so both give None; a key that is not a plain integer (which
    the schema does not produce) gives None rather than a broken page.
    """
    key = row["entity_key"]
    if row["entity_kind"] != "member" or not (key.isascii() and key.isdecimal()):
        return None
    return int(key)


def edit_control(row):
    """What a row's Edit value field draws (#528, ADM-09.04).

    The field starts with the value publication would write now: the
    Administrator's edit when there is one, else the Family's answer, in
    the form the Family form shows it (a phone's display). A date is a date
    field, a field with closed choices those choices (no such field is
    Automatic since gender became By hand, #1603), and anything else text no
    longer than ParishSoft takes (``census_review.edit_limit``).
    """
    definition = FIELDS[row["field"]]
    value = row["admin_value"] if row["admin_value_set"] else row["submitted_value"]
    return {
        "value": browser_value(definition, KnownValue(True, value)) or "",
        "type": "date" if definition.kind is ValueKind.DATE else "text",
        "choices": definition.choices,
        "maxlength": edit_limit(row["field"]),
        "edited": row["admin_value_set"],
    }


def editor(row):
    """What a row's Edit draws on the review worklist (#1683), or None.

    The editor starts with the value carried forward (the reviewer's value,
    else the Family's answer), in the form the Family form shows it. A
    Family address is ``census.address_editor``'s (its parts, the country
    shown and both State controls' values); a Member field is a date, a
    choice or text no longer than the field takes (for an Automatic change,
    ParishSoft's request schema; ``census_review.edit_limit``). None for a
    field no value can be typed for.
    """
    value = row["admin_value"] if row["admin_value_set"] else row["submitted_value"]
    if row["entity_kind"] == "family":
        if row["field"] not in ADDRESS_FIELDS:
            return None
        # Country first, with the US State drop-down (#1785).
        known = value if isinstance(value, dict) else None
        return {"kind": "address", **address_editor(known)}
    definition = FIELDS.get(row["field"]) if row["entity_kind"] == "member" else None
    if definition is None or definition.kind is ValueKind.BOOLEAN:
        return None
    return {
        "kind": "date" if definition.kind is ValueKind.DATE else "text",
        "value": browser_value(definition, KnownValue(True, value)) or "",
        "choices": definition.choices,
        "maxlength": edit_limit(row["field"], row["handling"]),
    }


def placement(row):
    """The row's pipeline stage, from its stored decision and execution.

    ``pipeline.census_placement`` derives it, as every stream's mapping
    does. A change a Family's same answer reopened over an accepted
    ParishSoft value comes back as a Conflict that keeps the earlier
    Accepted visible, not in force (the resubmission rule's census case,
    #1683).
    """
    check = row.get("check")
    found = census_placement(
        decision=row["decision"],
        execution=row["execution"],
        handling=row["handling"],
        entity_kind=row["entity_kind"],
        admin_value_set=row["admin_value_set"],
        check=check.outcome if check else None,
    )
    if row.get("reopened") and found.stage is Stage.CONFLICT and not found.decision:
        held = Decision.ACCEPTED_EDITED if row["admin_value_set"] else Decision.ACCEPTED
        return Placement(Stage.CONFLICT, held_decision=held)
    return found


# Each Member field's comparison kind, for the Ignored rows' check below.
MEMBER_KINDS = {field.name: field.kind for field in MEMBER_FIELDS}


def now_matches(row):
    """Whether ParishSoft now holds an Ignored change's Family answer.

    Refresh rule 1 leaves an Ignored change Ignored and only notes this for
    the reviewer (#1689). Its stored ParishSoft value is the latest refresh's
    (reconciliation keeps it current while the change is open), compared by
    the census compare rules. Only a Member field has a verified read.
    """
    kind = MEMBER_KINDS.get(row["field"])
    if (
        row["decision"] != "ignored"
        or row["entity_kind"] != "member"
        or kind is None
        or not row["current_available"]
    ):
        return False
    return canonical_value(kind, row["current_value"]) == canonical_value(
        kind, row["submitted_value"]
    )


def family_answer(row):
    """The Family's answer as text. A birth date given as Unknown is stored
    as None, which would otherwise show blank like a removed value."""
    if row["submitted_value"] is None and row["field"] == "birth_date":
        return "Unknown"
    return display(row["submitted_value"])


def shape(row):
    """Word one proposal row for the page and the downloads."""
    # Whether ParishSoft's current value was read and verified: never for a
    # Family household field today. The page tells a field never read
    # ("(not read)") from one read empty ("(blank)") (#1472); the downloads
    # leave both blank.
    read = bool(row["current_available"]) and row["entity_kind"] != "family"
    shown = row | {
        "status": status(row),
        "automatic": automatic(row),
        "kind": kind(row),
        "label": LABELS.get(row["field"], row["field"]),
        "family_answer": family_answer(row),
        "edited": display(row["admin_value"]) if row["admin_value_set"] else "",
        # Blank where no verified ParishSoft read exists (every Family
        # household field today) rather than a value that is not there.
        "parishsoft_read": read,
        "parishsoft_now": display(row["current_value"]) if read else "",
        "baseline": (
            display(row["baseline_value"]) if row["baseline_available"] else ""
        ),
    }
    shown["member_duid"] = member_duid(row)
    if row["entity_kind"] == "family":
        shown["who"] = "Family"
    elif row["entity_kind"] == "proposed_member":
        name = display(
            {
                key: (row["submitted_value"] or {}).get(key)
                for key in ("first_name", "last_name")
            }
        )
        shown["who"] = f"New Member: {name}" if name else "New Member"
    else:
        shown["who"] = row["member_name"] or "Unavailable name"
    shown["status_label"] = STATUSES[shown["status"]]
    shown["placement"] = placement(row)
    shown["stage"] = shown["placement"].stage.value
    shown["stage_label"] = shown["placement"].stage.label
    # What the refresh checks found for a final change (#1689): ParishSoft's
    # value now (the change's own stored value is frozen at its mark), and
    # whether an Entered by hand change is still not seen there.
    check = row.get("check")
    if check is not None and check.observed:
        shown["parishsoft_read"] = bool(check.observed.get("available"))
        shown["parishsoft_now"] = display(check.observed.get("value"))
    shown["not_seen"] = bool(
        check is not None
        and check.not_seen(shown["placement"].stage is Stage.ENTERED_BY_HAND)
    )
    shown["now_matches"] = shown["placement"].stage is Stage.IGNORED and now_matches(
        row
    )
    # What a person may record on this row now (#528): the page offers only
    # these, and the service and SQL check them again. Reopen is the
    # Administrator's; the page shows it only to them.
    state = SimpleNamespace(
        handling=row["handling"],
        entity_kind=row["entity_kind"],
        field=row["field"],
        execution=row["execution"],
        decision=row["decision"],
        current_available=row["current_available"],
    )
    shown["can_resolve"] = allowed(state, "entered")
    shown["can_reopen"] = allowed(state, "reopened")
    # An Administrator's Review of an Automatic row (ADM-09): the form values
    # (census_change_views.REVIEWS) it may record now, in the page's order.
    shown["review_actions"] = [
        action
        for action, decision in REVIEW_CHOICES
        if shown["automatic"] and reviewable(state, decision)
    ]
    # Use the Family's value anyway (ADM-09.03): on a Conflict an
    # Administrator may approve over ParishSoft's current value.
    shown["resolve_conflict"] = shown["automatic"] and resolvable(state)
    # Edit value (ADM-09.04): on the same rows a Review is offered on. This
    # is pk-admin's offer, an Automatic change's only, until #1688; the
    # review worklist's Edit is wider (``editor``, #1683).
    shown["edit"] = (
        edit_control(row) if shown["automatic"] and editable(state) else None
    )
    shown["editor"] = editor(row) if editable(state) else None
    # A Conflict can be accepted only against a known ParishSoft value.
    shown["resolvable"] = resolvable(state)
    if isinstance(shown.get("resolution_at"), str):
        shown["resolution_at"] = datetime.fromisoformat(shown["resolution_at"])
    if shown.get("resolution_action") and not shown.get("resolution_by"):
        # The actor's portal user row is gone; the Additional information
        # queue words its completer the same way.
        shown["resolution_by"] = "Former portal user"
    shown["route_label"] = "Automatic" if shown["automatic"] else "By hand"
    if isinstance(shown["submitted_at"], str):
        shown["submitted_at"] = datetime.fromisoformat(shown["submitted_at"])
    return shown


def wanted(row, query, *, administrator):
    """Whether the Stage filter (or pk-admin's status filter) keeps ``row``."""
    if query.stage == NOT_SEEN:
        return row["not_seen"]
    if query.stage:
        return query.stage == row["stage"] or (
            query.stage == EVERY_STAGE and row["stage"] != Stage.HISTORY.value
        )
    status_key = row["status"]
    if status_key in HISTORY:
        return bool(query.history)
    if query.status == "all":
        return True
    if query.status == "open":
        return status_key in {"to_do", "conflict"} or (
            administrator and status_key == "to_review"
        )
    return status_key == query.status


def select(rows, query, *, administrator):
    """The rows the filters keep, with every change's count by status and stage.

    ``status_counts`` counts every row of the campaign by status key,
    whatever the other filters, with the history statuses only when
    ``history`` is on (zero otherwise): ``report census`` reads it.
    ``stage_counts`` counts every row by review stage, for the Stage
    filter's choices.
    """
    lower, upper = query.bounds
    needle = query.search.casefold()
    kept = [
        row
        for row in rows
        if wanted(row, query, administrator=administrator)
        and (query.route == "any" or (query.route == "automatic") == row["automatic"])
        and (query.kind == "any" or query.kind == row["kind"])
        and (lower is None or row["submitted_at"] >= lower)
        and (upper is None or row["submitted_at"] < upper)
        and (
            not needle
            or needle in (row["family_name"] or "").casefold()
            or needle in row["who"].casefold()
            or needle in str(row["family_duid"])
        )
    ]
    # Every status is counted, and history only when it is shown.
    counts = {key: 0 for key in STATUSES}
    for row in rows:
        if row["status"] not in HISTORY or query.history:
            counts[row["status"]] += 1
    # The Stage filter's counts: every row at each stage, whatever the
    # other filters, as the review worklist's choices count them.
    stages = {}
    for row in rows:
        stages[row["stage"]] = stages.get(row["stage"], 0) + 1
        if row["not_seen"]:
            # A flag on Entered by hand, counted beside it.
            stages[NOT_SEEN] = stages.get(NOT_SEEN, 0) + 1
    return {
        "rows": kept,
        "status_counts": counts,
        "stage_counts": stages,
        "administrator": administrator,
    }


def export_member_duid(row):
    """The Member DUID cell as the page words it.

    The Member's DUID, "New Member" for a Member added on the form, and
    blank for a household change.
    """
    if row["member_duid"] is not None:
        return str(row["member_duid"])
    return "New Member" if row["entity_kind"] == "proposed_member" else ""


def iso_time(moment):
    """A file's machine-readable time: ISO 8601 to the second, with offset."""
    return moment.isoformat(timespec="seconds")


def export_rows(result, zone, *, time_text=iso_time):
    """The filtered rows as plain text, times in the requested timezone.

    ``time_text`` words the Submitted and Recorded at times already placed
    in ``zone``: ISO 8601 for CSV and XLSX, the parish's compact date format
    for the PDF.
    """
    return [
        (
            # A Family the data no longer has: a blank name and the shared
            # words in their own column (#1216).
            row["family_name"] or "",
            source_status_label(family_status(row["family_name"])),
            str(row["family_duid"]),
            row["who"],
            export_member_duid(row),
            row["label"],
            row["parishsoft_now"],
            row["family_answer"],
            row["edited"],
            row["route_label"],
            row["stage_label"],
            time_text(row["submitted_at"].astimezone(zone)),
            # The latest Entered in ParishSoft, Ignore or Reopen (#528).
            row.get("resolution_by") or "",
            time_text(row["resolution_at"].astimezone(zone))
            if row.get("resolution_at")
            else "",
            row.get("resolution_note") or "",
        )
        for row in result["rows"]
    ]


def census_csv(result, zone):
    """One CSV of the filtered rows: the page's columns in the page's order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADINGS)
    writer.writerows(
        [csv_cell(value) for value in row] for row in export_rows(result, zone)
    )
    return buffer.getvalue().encode("utf-8")


def report_information(count, zone, *, parish, campaign, as_of):
    """The XLSX "Report information" rows: what the PDF's page frame states.

    The parish and campaign, the download time ``as_of`` in ``zone`` (a
    native date-time cell), the row count and the privacy line, labeled as
    the Limitations download labels them (#926). Like the PDF, it does not
    list the filters. The CSV holds only the table (#966).
    """
    return (
        ("Report", TITLE),
        ("Parish", parish),
        ("Campaign", campaign),
        ("Downloaded at", as_of.astimezone(zone)),
        ("Display time zone", zone.key),
        ("Changes in this file", f"{count:,}"),
        ("Privacy", PRIVACY),
    )


def census_xlsx(result, zone, *, parish, campaign, as_of):
    """A literal-text sheet of the filtered rows, then report information.

    ``parish`` and ``campaign`` are names and ``as_of`` the download time,
    as for ``census_pdf``; the "Report information" sheet and the
    workbook's created time state them.
    """
    # Imported here, like the other export writers, to keep web startup light.
    from .xlsx_design import table_workbook

    return table_workbook(
        HEADINGS,
        export_rows(result, zone),
        sheet_title=TITLE,
        freeze_first_column=True,
        information=report_information(
            len(result["rows"]), zone, parish=parish, campaign=campaign, as_of=as_of
        ),
        created=as_of,
    )


def census_pdf(result, zone, *, parish, campaign, as_of):
    """The filtered rows as the shared framed zebra table (``pdf_design``).

    The table is ``HEADINGS`` over ``export_rows``: the CSV's and XLSX's
    columns and values, so the three files never disagree (#966); only the
    Submitted and Recorded at times read in the parish's compact date format.
    The page frame names the parish and campaign, the download time
    ``as_of`` in ``zone`` and the row count; values are drawn in full, long
    ones wrapping within their column. The caller lends the parish's date
    format (``dates.using``) for the header's time.
    """
    from .pdf_design import PdfFrame, Table, draw_table, table_pages

    count = len(result["rows"])
    frame = PdfFrame(
        title=TITLE,
        eyebrow=f"{parish} · {campaign}",
        stamp=f"Downloaded {dates.display_text(as_of.astimezone(zone))} ({zone.key})",
        details=(f"{count:,} {'change' if count == 1 else 'changes'} in this file.",),
        notice=PRIVACY,
    )
    table = Table.weighted(HEADINGS, [PDF_WEIGHTS[name] for name in HEADINGS])
    rows = export_rows(
        result, zone, time_text=lambda moment: dates.display_text(moment, compact=True)
    )
    pages = list(table_pages(table, rows, frame))
    output = io.BytesIO()
    frame.write(output, pages, len(pages), draw_table(table, frame), requested_at=as_of)
    return output.getvalue()
