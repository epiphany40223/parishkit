"""Ministry requests as the Ministry stream's review worklist (#1684).

The page's table is the shared review worklist
(``components/review-worklist.html``, ``web/review_worklist.build``); this
module gives it the Ministry stream's own parts:

- ``WorklistQuery``: the page's view (Stage, search, Ministry, Request, the
  Staff-only Members choice, sort, page and size), private POST state as the
  follow-up queue's always was, read from the follow-up selection as a
  ``FollowupQuery`` (``selection``).
- ``read_rows``: every request the view's filters select, read whole through
  the follow-up selection (its scope and cross-check), each with its stage
  (``workflows.review.placement``) and the reader's word for it. A Ministry
  leader never sees the update phase (#1160), so for a leader an accepted
  request is at the stage Accepted whether or not Staff entered it.
- ``worklist_context``: one page of those rows decorated for the component,
  with the Stage choices' counts, the identity cells, the decision line
  ("Ignored: Stayed in the Ministry", "Family asked again", "Added in its
  place: …") and the Last decision column.

What a reader may do is decided by ``pipeline`` and enforced by
``workflows.review`` and the SQL guards; this module only draws it.
"""

from dataclasses import dataclass, replace

from django.db import connection
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.pipeline import Decision, Stage, Stream
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.review_worklist import Column, build
from parishkit.stewardship.web.tables import PAGE_SIZES, report_table
from parishkit.stewardship.workflows.review import (
    leads,
    ministry_placement_of,
    reader_roles,
)
from parishkit.stewardship.workflows.roster import roster_marks

from .information import parse_page
from .ministries import outcome_label
from .ministry_followup import (
    NOT_IN_SOURCE,
    PAGE_SIZE,
    SORTING,
    FollowupQuery,
    followup_page,
    valid_ministry,
)

# The Stage filter's choices, in order, for Staff and Administrators and for
# a Ministry leader, whose Accepted joins To enter by hand and Entered by
# hand (the update phase is Staff's). Every stage ends with Every stage.
STAFF_STAGES = (
    Stage.TO_REVIEW,
    Stage.TO_ENTER_BY_HAND,
    Stage.ENTERED_BY_HAND,
    Stage.IGNORED,
    Stage.CONFIRMED,
)
ACCEPTED = "accepted"
LEADER_STAGES = (Stage.TO_REVIEW.value, ACCEPTED, Stage.IGNORED.value, "confirmed")
EVERY = "all"
STAGE_VALUES = frozenset({*(stage.value for stage in STAFF_STAGES), ACCEPTED, EVERY})
# The Members filter (Staff only, #1217): the requests for Members ParishSoft
# still lists in their Family (the default), or only those it no longer does.
MEMBERS = {"current": "In ParishSoft", "gone": "No longer in ParishSoft"}
PREFIX = "requests"
COLUMNS = (
    Column("member_cell", "Member", sort="member"),
    Column("duid_cell", "Member DUID", numeric=True),
    Column("ministry_cell", "Ministry", sort="ministry"),
    Column("contact_cell", "Last contact"),
)
FORMER = "Former portal user"


@dataclass(frozen=True, repr=False)
class WorklistQuery:
    """The worklist's view: private POST state, never a URL or audit value.

    ``stage`` is a Stage choice's value; ``members`` the Staff-only Members
    choice. The search, Ministry, Request and sort are the follow-up
    selection's own filters (``selection``).
    """

    stage: str = Stage.TO_REVIEW.value
    search: str = ""
    ministry: str = ""
    # The Request filter (join, leave or any). Not named "action": a form
    # field of that name hides the form's own action in the browser.
    kind: str = "any"
    members: str = "current"
    sort: str = "newest"
    page: int = 1
    size: str = str(PAGE_SIZE)

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values from closed vocabularies."""
        if not hasattr(parameters, "getlist"):
            parameters = MultiValueDict(
                {key: [value] for key, value in dict(parameters).items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        query = cls(**(values | {"page": parse_page(values.get("page", "1"))}))
        if (
            query.stage not in STAGE_VALUES
            or query.kind not in {"any", "join", "leave"}
            or query.members not in MEMBERS
            or query.sort not in SORTING.tokens
            or query.size not in {str(size) for size in PAGE_SIZES}
            or (query.ministry and not valid_ministry(query.ministry))
        ):
            raise ValueError("Invalid Ministry request filters.")
        bounded_text(query.search)
        return query

    def form_values(self):
        """The view's filters and sort; page and size only window it."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in {"page", "size"}
        }

    def view_values(self):
        """Everything a remembered view keeps: the filters, sort, page and size."""
        return self.form_values() | {"page": str(self.page), "size": self.size}

    @property
    def page_size(self):
        """The validated rows-per-page choice as an integer."""
        return int(self.size)

    def selection(self):
        """The follow-up selection's query for every stage of this view.

        The stage is chosen afterwards, from each row's roster tick; the
        Members choice is the selection's own Status choice for Members no
        longer in ParishSoft (#1217).
        """
        return FollowupQuery(
            search=self.search,
            ministry=self.ministry,
            action=self.kind,
            state=NOT_IN_SOURCE if self.members == "gone" else "any",
            sort=self.sort,
            size=self.size,
        )

    def open_query(self):
        """The same view's open requests (To review), for Save and next."""
        return replace(self.selection(), state="unresolved")


def stage_of(placement, leader):
    """The Stage choice a row is counted and filtered under, for its reader."""
    if leader and placement.stage in (Stage.TO_ENTER_BY_HAND, Stage.ENTERED_BY_HAND):
        return ACCEPTED
    return placement.stage.value


def is_leader(principal):
    """Whether the reader is a Ministry leader only (not Staff)."""
    return not allows(principal, Capability.MINISTRY_FOLLOWUP)


def read_rows(campaign_id, query, principal, *, empty=False):
    """Every request the view's filters select, each with its stage.

    One whole read of the follow-up selection (its scope and cross-check)
    and one of the shown requests' roster ticks; a Ministry leader's Members
    choice is always current (the selection refuses the other). Returns the
    selection's result, whose ``rows`` each carry ``placement`` and ``stage``.
    """
    # Only Staff and Administrators may list the requests for Members no
    # longer in ParishSoft (#1217); a leader asking is a malformed form.
    if is_leader(principal) and query.members != "current":
        raise ValueError("Invalid Ministry request filters.")
    result = followup_page(
        campaign_id, query.selection(), principal, whole=True, empty=empty
    )
    return place(result, principal)


def place(result, principal, *, marks=None):
    """Give each row of a follow-up selection result its stage, in place.

    One read of the rows' roster ticks, unless ``marks`` gives them (a
    detached page). Each row gains ``placement`` (the pipeline's),
    ``stage`` (the reader's Stage choice) and the words a file shows for
    them (``stage_label``, ``decision_label``). Returns ``result``.
    """
    if marks is None:
        marks = roster_marks(row["id"] for row in result["rows"])
    leader = is_leader(principal)
    for row in result["rows"]:
        placement = ministry_placement_of(row, marks)
        row["placement"] = placement
        row["stage"] = stage_of(placement, leader)
        row["stage_label"] = (
            placement.decision.label
            if row["stage"] == ACCEPTED
            else placement.stage.label
        )
        if placement.stage is Stage.IGNORED:
            outcome = outcome_label(row["outcome"], row["action"], "")
            row["decision_label"] = f"Ignored: {outcome}" if outcome else "Ignored"
        else:
            row["decision_label"] = (
                placement.decision.label if placement.decision else ""
            )
    return result


def stage_counts(rows):
    """How many of ``rows`` are at each Stage choice's value."""
    counts = {}
    for row in rows:
        counts[row["stage"]] = counts.get(row["stage"], 0) + 1
    return counts


def stage_choices(counts, leader):
    """The Stage filter's (value, label) choices with their counts."""
    labels = (
        [(stage, Stage(stage).label) for stage in (Stage.TO_REVIEW.value,)]
        + [(ACCEPTED, Decision.ACCEPTED.label)]
        + [(stage.value, stage.label) for stage in (Stage.IGNORED, Stage.CONFIRMED)]
        if leader
        else [(stage.value, stage.label) for stage in STAFF_STAGES]
    )
    choices = [
        (value, f"{label} ({counts.get(value, 0):,})") for value, label in labels
    ]
    choices.append((EVERY, f"Every stage ({sum(counts.values()):,})"))
    return choices


def _keeps(row, stage):
    """Whether a row is at the chosen Stage (Every stage keeps every row)."""
    if stage == EVERY:
        return True
    if stage == ACCEPTED:
        return row["stage"] in (
            ACCEPTED,
            Stage.TO_ENTER_BY_HAND.value,
            Stage.ENTERED_BY_HAND.value,
        )
    return row["stage"] == stage


def chosen(rows, stage):
    """The rows at the chosen Stage, in the selection's order."""
    return [row for row in rows if _keeps(row, stage)]


def _labels(identities):
    """Map retained actor UUIDs to current emails; removed users stay anonymous."""
    return {
        str(key): email
        for key, email in PortalUser.objects.filter(
            pk__in=set(identities) - {None}
        ).values_list("id", "email")
    }


def _last_decisions(rows):
    """Each row's latest revision (who, when, its note), across its chain.

    One statement for the rows shown, through the same same-intent chain the
    request page's history reads, so a Family's resubmission keeps the
    reviewer's last word.
    """
    ids = [row["id"] for row in rows]
    if not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT ON (t.id) t.id::text,v.actor_id,v.created_at,"
            "v.decision_note FROM unnest(%s::uuid[]) t(id) "
            "CROSS JOIN LATERAL stewardship_ministry_workflow_chain_v1(t.id) c "
            "JOIN stewardship_ministry_revision v ON v.request_id=c.request_id "
            "ORDER BY t.id,c.depth,v.expected_version DESC",
            [ids],
        )
        found = cursor.fetchall()
    labels = _labels(actor for _key, actor, _at, _note in found)
    return {
        key: (labels.get(str(actor), FORMER), at, note)
        for key, actor, at, note in found
    }


def _asked_again(rows):
    """When the Family last asked again for each decided request shown.

    A Family's later response that repeats a decided request's action for
    the same Member and Ministry creates no request (#1684); it is found
    here, read for the rows shown: the latest such response after the
    decision in force, by request id.
    """
    ids = [
        row["id"] for row in rows if row["state"] in ("resolved", "closed_no_response")
    ]
    if not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT r.id::text,max(later.submitted_at) "
            "FROM stewardship_ministry_request r "
            "JOIN stewardship_submission s ON s.id=r.submission_id "
            "JOIN stewardship_submission later ON later.family_id=s.family_id "
            "AND later.campaign_id=s.campaign_id AND later.mode='live' "
            "AND later.family_version>s.family_version "
            "WHERE r.id=ANY(%s::uuid[]) AND later.submitted_at>r.resolved_at "
            "AND later.answers#>ARRAY['ministries',CASE WHEN r.entity_kind='member' "
            "THEN 'members' ELSE 'proposed_members' END,r.entity_key,r.action] "
            "@> to_jsonb(r.ministry_duid) GROUP BY r.id",
            [ids],
        )
        return dict(cursor.fetchall())


def _time(value):
    """A browser-local compact time element for an instant, on one line."""
    return format_html(
        '<time class="nowrap" datetime="{}" data-local-instant data-compact>{}</time>',
        value.isoformat(),
        value.isoformat(),
    )


def _request_words(row):
    """A request's kind as the Family's answer column says it."""
    if row["action"] == "join":
        return "Join"
    if row.get("cannot_serve"):
        return format_html(
            '<span class="nowrap">Leave <span title="{}">({})</span></span>',
            "Cannot participate in any ministries (physical limitations)",
            "cannot participate",
        )
    return "Leave"


def _member_cell(row, token):
    """The Member cell: the request page's link, with the ParishSoft badge.

    A Member the data no longer has in this Family has no name, so the badge
    alone is the link (#1216).
    """
    url = reverse("admin:ministry_followup_item", args=[row["id"]])
    if token:
        url = f"{url}?queue={token}"
    badge = (
        format_html(
            '<span class="status-badge" data-source-status="{}">{}</span>',
            row["member_status"],
            source_status_label(row["member_status"]),
        )
        if row["source_mark"]
        else ""
    )
    link = format_html(
        '<a href="{}" class="cell-ellipsis" data-clip-tip>{}</a>',
        url,
        row["member_name"] or badge,
    )
    if not row["source_mark"]:
        return link
    return format_html(
        '<span class="cell-with-badge">{}{}</span>',
        link,
        format_html(" {}", badge) if row["member_name"] else "",
    )


def _ministry_cell(row):
    """The Ministry cell, with the Removed badge for one no longer asked about."""
    removed = (
        format_html(
            ' <span class="status-badge"><span aria-hidden="true" title="{}">{}'
            '</span><span class="visually-hidden">{}</span></span>',
            "No longer in this campaign",
            "Removed",
            "No longer in this campaign",
        )
        if row["in_campaign"] is False
        else ""
    )
    return format_html(
        '<span class="cell-with-badge"><span class="cell-clamp" data-clip-tip>'
        "{}</span>{}</span>",
        row["ministry_name"],
        removed,
    )


def _decision_line(row, leader, asked, replaced_by, reader_leads):
    """The line under a row's stage, as safe HTML (or "").

    An ignored request names its stored outcome ("Stayed in the
    Ministry"); one the Family asked for again says when; an ignored one the
    parish added a request in place of names it; an edited request read by
    a leader of the Family's Ministry only says where it went. Accepted
    stays under To enter by hand and Entered by hand, as the component
    words it.
    """
    placement = row["placement"]
    parts = []
    if placement.stage is Stage.IGNORED:
        parts.append(
            format_html("{}", outcome_label(row["outcome"], row["action"], "Ignored"))
        )
    elif row["stage"] == ACCEPTED and row.get("reviewed_ministry_name"):
        parts.append(format_html("into {}", row["reviewed_ministry_name"]))
    elif (
        placement.decision
        and placement.decision.label != placement.stage.label
        and not (leader and row["stage"] == ACCEPTED)
    ):
        parts.append(format_html("{}", placement.decision.label))
    if not reader_leads and leader and row.get("reviewed_ministry_name"):
        parts.append(format_html("{}", "Read only: edited into another Ministry"))
    if replaced_by:
        parts.append(format_html("Added in its place: {}", replaced_by))
    if asked:
        parts.append(format_html("Family asked again {}", _time(asked)))
    return format_html_join(mark_safe("<br>"), "{}", ((part,) for part in parts))


def _ministry_choices(result, row=None):
    """The campaign Ministries in the reader's scope, but the row's own.

    The follow-up selection lists exactly the reader's Ministries, so a
    leader's Edit offers only the Ministries they lead (spec, "Role-specific
    actions") and Staff's every Ministry of the campaign.
    """
    own = row["ministry_duid"] if row else None
    return [
        (str(ministry["duid"]), ministry["name"])
        for ministry in result["ministries"]
        if ministry["in_campaign"] and ministry["duid"] != own
    ]


def decorate(rows, result, principal, *, token, every, decisions=None, asked=None):
    """Give each shown row what the worklist component reads.

    ``every`` is all the view's rows (any page), so a request added in place
    of an ignored one is named without another read. ``decisions`` and
    ``asked`` are the rows' last decisions and Family asked again times,
    read here unless given (a detached page).
    """
    leader = is_leader(principal)
    decisions = _last_decisions(rows) if decisions is None else decisions
    asked = _asked_again(rows) if asked is None else asked
    replacements = {}
    for other in every:
        if other.get("added_in_place_of_id"):
            replacements[str(other["added_in_place_of_id"])] = (
                f"{'Join' if other['action'] == 'join' else 'Leave'} "
                f"{other['ministry_name']}"
            )
    decorated = []
    for row in rows:
        key = str(row["id"])
        current = row.get("reviewed_ministry_duid") or row["ministry_duid"]
        reader_leads = leads(principal, current)
        decided_by, decided_at, note = decisions.get(key, ("", None, ""))
        reviewed = row.get("reviewed_ministry_name")
        kind = "Join" if row["action"] == "join" else "Leave"
        decorated.append(
            {
                "id": key,
                "version": row["version"],
                "name": f"{row['member_label']} — {row['ministry_name']}",
                # Nothing is recorded for a Member no longer in ParishSoft
                # (#1217), so such a row is drawn with no actions at all.
                "placement": row["placement"],
                "leads": reader_leads and not row["source_mark"],
                "member_cell": _member_cell(row, token),
                "duid_cell": str(row["member_duid"])
                if row["member_duid"]
                else "New Member",
                "ministry_cell": _ministry_cell(row),
                "contact_cell": _time(row["last_contact_at"])
                if row["last_contact_at"]
                else "None recorded",
                "baseline": "",
                "family_answer": "" if row.get("parish_added") else _request_words(row),
                "reviewer_value": f"{kind} {reviewed}"
                if reviewed
                else (
                    f"{kind} {row['ministry_name']}" if row.get("parish_added") else ""
                ),
                "parishsoft_now": "",
                "submitted_at": row["submitted_at"],
                "decided_by": decided_by,
                "decided_at": decided_at,
                "note": note,
                "edit_choices": _ministry_choices(result, row),
                "_decision_line": _decision_line(
                    row, leader, asked.get(key), replacements.get(key), reader_leads
                ),
                "_leader_stage": row["stage"] == ACCEPTED,
            }
        )
    return decorated


def _withhold(row, actionable):
    """Redraw a decorated row's stage words for its reader after ``build``.

    The component words a row by its pipeline stage; a leader's accepted
    request reads by its decision instead, since leaders never see the
    update phase (#1160). And a Staff reader's rows for Members no longer in
    ParishSoft offer nothing (#1217).
    """
    if row.pop("_leader_stage"):
        row["stage_label"] = row["placement"].decision.label
    row["decision_label"] = row.pop("_decision_line")
    if actionable and not row["leads"]:
        row["icons"] = []
        row["kinds"] = ""
        row["offers_edit"] = False
    return row


def worklist_context(
    campaign_id,
    query,
    principal,
    result,
    *,
    token,
    mutable,
    action,
    decisions=None,
    asked=None,
):
    """The worklist and its table for one page of the view.

    ``result`` is ``read_rows``' answer. While other campaign work holds the
    page (not ``mutable``), every row is drawn with no actions and the
    selection bar and Add are gone; the page's notice says why.
    ``decisions`` and ``asked`` are given only to a detached page
    (``decorate``).
    """
    leader = is_leader(principal)
    rows = result["rows"]
    counts = stage_counts(rows)
    shown = chosen(rows, query.stage)
    last = max(1, -(-len(shown) // query.page_size))
    number = min(query.page, last)
    start = (number - 1) * query.page_size
    page_rows = shown[start : start + query.page_size]
    roles = reader_roles(principal) if mutable else frozenset()
    decorated = decorate(
        page_rows,
        result,
        principal,
        token=token,
        every=rows,
        decisions=decisions,
        asked=asked,
    )
    table = report_table(
        decorated,
        number=number,
        size=query.page_size,
        total=len(shown),
        carry=[
            (key, value) for key, value in query.form_values().items() if key != "sort"
        ],
        sorting=SORTING,
        sort=query.sort,
        action=action,
    )
    fields = {
        key: value
        for key, value in query.form_values().items()
        if key not in {"stage", "sort"}
    } | {"page": str(number)}
    worklist = build(
        Stream.MINISTRY,
        table.rows,
        roles,
        prefix=PREFIX,
        action=action,
        columns=COLUMNS,
        stage=query.stage,
        counts={},
        stages=(),
        items="requests",
        caption="Ministry requests to review",
        baseline=False,
        fields=fields,
        filters_template="stewardship/ministry-worklist-filters.html",
        editor_template="stewardship/ministry-worklist-edit.html",
        add_template="stewardship/ministry-worklist-add.html",
        export_template="stewardship/ministry-worklist-export.html",
    )
    worklist.stage_choices = stage_choices(counts, leader)
    for row in worklist.rows:
        _withhold(row, bool(roles))
    return {
        "worklist": worklist,
        "table": table,
        "total": len(shown),
        "add_ministries": _ministry_choices(result),
        # Every ignored request of the view, whatever its stage or page,
        # since Add is used from any of them (#1684).
        "add_replaceable": [
            (str(row["id"]), f"{row['member_label']} — {row['ministry_name']}")
            for row in rows
            if row["placement"].stage is Stage.IGNORED
        ],
        "members_choices": MEMBERS if not leader else None,
        "query": query,
        "ministries": result["ministries"],
        "metadata": result["metadata"],
    }
