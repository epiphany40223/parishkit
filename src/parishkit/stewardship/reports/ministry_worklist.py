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
  request is at the stage Accepted whether or not Staff entered it. For
  Staff and Administrators the To enter by hand and Entered by hand Stages
  also hold the roster rows the review stages leave out (``roster_only``):
  Roster changes to enter was folded into this page (R39 finding 2).
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
from parishkit.stewardship.pipeline import TERMS, Action, Decision, Stage, Stream
from parishkit.stewardship.responses.refresh_checks import check_states
from parishkit.stewardship.source.source_status import source_status_label
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.review_worklist import (
    ALREADY_HELD,
    NOT_SEEN,
    NOW_MATCHES,
    Column,
    build,
)
from parishkit.stewardship.web.tables import PAGE_SIZES, report_table
from parishkit.stewardship.workflows.models import MinistryRequest
from parishkit.stewardship.workflows.review import (
    SILENT,
    leads,
    ministry_placement_of,
    reader_roles,
)
from parishkit.stewardship.workflows.roster import eligible, mark, roster_marks

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
# Not seen in ParishSoft (#1689) is a flag on Entered by hand, chosen like a
# stage; only Staff and Administrators see it, as they alone see the update.
STAGE_VALUES = frozenset(
    {*(stage.value for stage in STAFF_STAGES), ACCEPTED, NOT_SEEN, EVERY}
)
# The Members filter (Staff only, #1217): the requests for Members ParishSoft
# still lists in their Family (the default), or only those it no longer does.
MEMBERS = {"current": "In ParishSoft", "gone": "No longer in ParishSoft"}
PREFIX = "requests"
# The Member's name opens the Family hub for the viewers who may open it
# (plain for a Ministry leader), and the Ministry opens the request's own
# page for every viewer (an Administrator decision, R38 finding 13).
COLUMNS = (
    Column("member_cell", "Member", sort="member", family=True),
    Column("duid_cell", "Member DUID", numeric=True),
    Column("ministry_cell", "Ministry", sort="ministry", link="item_url"),
    Column("contact_cell", "Last contact"),
)
FORMER = "Former portal user"
REPLACED = "Replaced by a later response"
# A decided request a Family undid on a reviewed form, kept in force (#1705).
UNTICKED = "Family unticked"
CONTINUED = "Family chose to continue"
# Add's dialog: what a Family's reviewed form (#1705) shows of an added
# request, which the Family cannot withdraw.
ADD_BODY = (
    "Add a request the Family did not ask for. It starts Accepted, shown as "
    "Added by the parish. The Family will see it chosen the next time it "
    "signs in: a join as selected, a leave as Stop participating. If the "
    "Family undoes it, this row will say so."
)
# A current request: the newest revision, neither withdrawn nor replaced
# (the follow-up selection's own "current" history, schema 0131).
NOT_CURRENT = frozenset({"cancelled", "superseded"})


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


def for_reader(query, principal):
    """The view as the reader's Stage filter offers it.

    A Ministry leader has no update phase: their Accepted holds To enter by
    hand and Entered by hand, so a link to either (Home's card is Staff's,
    but a link can be passed on) opens their Accepted rather than an empty
    list under a Stage their filter does not have.
    """
    update_phase = {Stage.TO_ENTER_BY_HAND.value, Stage.ENTERED_BY_HAND.value}
    if query.stage in update_phase and is_leader(principal):
        return replace(query, stage=ACCEPTED)
    return query


def read_rows(campaign_id, query, principal, *, empty=False):
    """Every request the view's filters select, each with its stage.

    One whole read of the follow-up selection (its scope and cross-check)
    and one of the shown requests' roster ticks; a Ministry leader's Members
    choice is always current (the selection refuses the other). Returns the
    selection's result, whose ``rows`` each carry ``placement`` and ``stage``.
    """
    # Only Staff and Administrators may list the requests for Members no
    # longer in ParishSoft (#1217); a leader asking is a malformed form.
    leader = is_leader(principal)
    if leader and query.members != "current":
        raise ValueError("Invalid Ministry request filters.")
    if leader:
        result = followup_page(
            campaign_id, query.selection(), principal, whole=True, empty=empty
        )
        return place(result, principal)
    # Staff and Administrators read every revision of every Member's
    # requests, as Roster changes to enter did, and keep the view's own
    # rows plus the roster rows it would otherwise leave out (``listed``),
    # in the selection's order.
    result = followup_page(
        campaign_id,
        replace(query.selection(), state="any", history="all"),
        principal,
        whole=True,
        empty=empty,
        every_member=True,
    )
    result["rows"] = [row for row in result["rows"] if listed(row, query.members)]
    return place(result, principal)


def roster_only(row):
    """Whether a row is listed only for its roster work: an accepted join or
    leave for a Member no longer in ParishSoft, or one a later Family
    response replaced. It offers only Entered by hand (admin-portal spec,
    "Roster entry on Ministry requests")."""
    return eligible(row) and (bool(row["source_mark"]) or row.get("latest") is False)


def listed(row, members):
    """Whether a row of the every-revision read belongs in a Staff view.

    ``members`` is the Members choice. The view's own rows are the current
    requests (the newest revision, not withdrawn or replaced) for Members
    ParishSoft still lists ("current") or no longer lists ("gone"); the
    roster rows, accepted joins and leaves with no resolution source, are
    listed under the default choice whatever their Member's standing or
    revision, since the roster work may still be real, and under "gone"
    for such a Member.
    """
    gone = bool(row["source_mark"])
    current = row.get("latest") is not False and row["state"] not in NOT_CURRENT
    if members == "gone":
        return gone and (current or eligible(row))
    return (current and not gone) or eligible(row)


# Whether the current ParishSoft data lists each request's Member in the
# Ministry carried forward (the Edit's, else the Family's), by the same SQL
# roster read the guards use (NULL when the Member or Ministry gives no
# evidence), for the rows ParishSoft may already hold (#1890).
HELD = """
SELECT r.id, stewardship_response_ministry_current_v1(sc.snapshot_id, s.family_id,
    r.entity_key, coalesce(r.reviewed_ministry_duid, r.ministry_duid))
FROM stewardship_ministry_request r
JOIN stewardship_submission s ON s.id=r.submission_id
JOIN stewardship_source_current sc ON sc.singleton AND sc.snapshot_id IS NOT NULL
WHERE r.id = ANY(%s::uuid[]) AND r.entity_kind='member'
"""
# The stages before a request's step: a roster that already shows it leaves
# the request there (strict phase order, #1890), and its row says so.
BEFORE_STEP = frozenset({Stage.TO_REVIEW, Stage.TO_ENTER_BY_HAND})


def held(rows):
    """The ids of ``rows`` (placed) whose roster already shows their request.

    Refresh rule 1 (#1890): a refresh no longer resolves a request before
    its decision and tick, so a To review or To enter by hand request whose
    Member the current roster already lists (a join) or no longer lists (a
    leave) keeps its stage and says ParishSoft already holds it. One query,
    none when no row is at such a stage.
    """
    wanted = {
        str(row["id"]): row["action"] == "join"
        for row in rows
        if row["placement"].stage in BEFORE_STEP
    }
    if not wanted:
        return set()
    with connection.cursor() as cursor:
        cursor.execute(HELD, [list(wanted)])
        return {
            str(key)
            for key, current in cursor.fetchall()
            if current is not None and current == wanted[str(key)]
        }


def place(result, principal, *, marks=None, checks=None):
    """Give each row of a follow-up selection result its stage, in place.

    One read of the rows' roster ticks and one of their refresh checks
    (#1689), unless ``marks`` gives the ticks (a detached page, which then
    gives its ``checks`` too, or has none). Each row gains
    ``placement`` (the pipeline's), ``stage`` (the reader's Stage choice),
    the Not seen in ParishSoft flag (Staff only), whether ParishSoft now
    matches an ignored request and whether it already holds a request
    before its step (``not_seen``, ``now_matches``, ``already_held``; the
    last only when this reads the ticks, with one more query, ``held``),
    and the words a file shows for them (``stage_label``,
    ``decision_label``). Returns ``result``.
    """
    reading = marks is None
    if reading:
        marks = roster_marks(row["id"] for row in result["rows"])
        checks = check_states(
            requests=[row["id"] for row in result["rows"]],
            marks=marks,
            versions={str(row["id"]): row["version"] for row in result["rows"]},
        )
    checks = checks or {}
    leader = is_leader(principal)
    for row in result["rows"]:
        # The tick's who and when, for the decision line and the file.
        mark(row, marks)
        placement = ministry_placement_of(row, marks, checks)
        check = checks.get(str(row["id"]))
        row["placement"] = placement
        row["stage"] = stage_of(placement, leader)
        row["not_seen"] = bool(
            not leader
            and check
            and check.not_seen(placement.stage is Stage.ENTERED_BY_HAND)
        )
        row["now_matches"] = bool(
            placement.stage is Stage.IGNORED
            and check
            and check.outcome == "ignored_now_matches"
        )
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
    found = held(result["rows"]) if reading else set()
    for row in result["rows"]:
        # A leader sees To enter by hand as Accepted, with no update phase.
        row["already_held"] = str(row["id"]) in found and not (
            leader and row["placement"].stage is Stage.TO_ENTER_BY_HAND
        )
    return result


def stage_counts(rows):
    """How many of ``rows`` are at each Stage choice's value.

    Not seen in ParishSoft is counted beside the stage its rows are at.
    """
    counts = {}
    for row in rows:
        counts[row["stage"]] = counts.get(row["stage"], 0) + 1
        if row.get("not_seen"):
            counts[NOT_SEEN] = counts.get(NOT_SEEN, 0) + 1
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
    if NOT_SEEN in counts and not leader:
        labels.append((NOT_SEEN, TERMS["not_seen"]))
    choices = [
        (value, f"{label} ({counts.get(value, 0):,})") for value, label in labels
    ]
    every = sum(count for value, count in counts.items() if value != NOT_SEEN)
    choices.append((EVERY, f"Every stage ({every:,})"))
    return choices


def _keeps(row, stage):
    """Whether a row is at the chosen Stage (Every stage keeps every row)."""
    if stage == EVERY:
        return True
    if stage == NOT_SEEN:
        return bool(row.get("not_seen"))
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
    """When the Family last asked again for, or undid, each decided request shown.

    Returns ``{request id: (asked, undone)}``, each the time of the latest
    such live response after the decision in force, or None. Before the
    reviewed Family form, a response that repeats a decided request's
    action for the same Member and Ministry creates no request (#1684): it
    "asked again". A reviewed form (#1705) shows a decided request chosen
    instead, so a response drawn as one never asks again; one whose form
    showed the request chosen (on the Ministry it carries forward) and that
    no longer chooses it "undid" it, which keeps the request in force only
    when the parish added it or Staff entered it in ParishSoft (Family
    unticked, Family chose to continue). One statement, for the rows shown.
    """
    ids = [
        row["id"] for row in rows if row["state"] in ("resolved", "closed_no_response")
    ]
    if not ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT r.id::text,"
            "max(later.submitted_at) FILTER (WHERE NOT later.reviewed_view "
            "AND later.answers#>ARRAY['ministries',g.name,r.entity_key,r.action] "
            "@> to_jsonb(r.ministry_duid)),"
            "max(later.submitted_at) FILTER (WHERE later.reviewed_view AND EXISTS ("
            "SELECT 1 FROM jsonb_array_elements(later.shown_items) e "
            "WHERE e->>'stream'='ministry' AND e->>'item'=r.id::text "
            "AND e->>'shown' IN ('family','reviewer') "
            "AND NOT coalesce(later.answers#>ARRAY['ministries',g.name,r.entity_key,"
            "r.action] @> to_jsonb(CASE WHEN e->>'decision' IS NOT NULL THEN "
            "(SELECT v.reviewed_ministry_duid FROM stewardship_ministry_revision v "
            "WHERE v.id::text=e->>'decision' AND v.request_id=r.id) "
            "ELSE r.ministry_duid END),false))) "
            "FROM stewardship_ministry_request r "
            "CROSS JOIN LATERAL (SELECT CASE WHEN r.entity_kind='member' "
            "THEN 'members' ELSE 'proposed_members' END AS name) g "
            "JOIN stewardship_submission s ON s.id=r.submission_id "
            "JOIN stewardship_submission later ON later.family_id=s.family_id "
            "AND later.campaign_id=s.campaign_id AND later.mode='live' "
            "AND later.family_version>s.family_version "
            "WHERE r.id=ANY(%s::uuid[]) AND later.submitted_at>r.resolved_at "
            "GROUP BY r.id",
            [ids],
        )
        return {
            key: (asked, undone)
            for key, asked, undone in cursor.fetchall()
            if asked or undone
        }


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


def reviewer_value(row):
    """The Ministry a request carries forward in place of the Family's
    answer: an Edit's, or one the parish added (#1684); else blank. The
    page and Export these requests share it."""
    kind = "Join" if row["action"] == "join" else "Leave"
    if row.get("reviewed_ministry_name"):
        return f"{kind} {row['reviewed_ministry_name']}"
    return f"{kind} {row['ministry_name']}" if row.get("parish_added") else ""


def _item_url(row, token):
    """The request's own page, keeping the view's queue token, if any."""
    url = reverse("admin:ministry_followup_item", args=[row["id"]])
    return f"{url}?queue={token}" if token else url


def _family_ids(rows):
    """Each shown request's Family record id, by request id: one read.

    The selection gives no Family, and the Member's name opens the Family
    hub (R38 finding 13), so the shown rows' Families are read here.
    """
    return {
        str(key): family
        for key, family in MinistryRequest.objects.filter(
            pk__in=[row["id"] for row in rows]
        ).values_list("pk", "submission__family_id")
    }


def _member_cell(row):
    """The Member cell: the name with the ParishSoft badge.

    A Member the data no longer has in this Family has no name, so the badge
    stands alone (#1216). The worklist links it to the Family hub for the
    viewers who may open it; the request's page is the Ministry cell's link.
    """
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
        '<span class="cell-ellipsis" data-clip-tip>{}</span>',
        row["member_name"] or badge,
    )
    if not row["source_mark"]:
        return link
    return format_html(
        '<span class="cell-with-badge">{}{}</span>',
        link,
        format_html(" {}", badge) if row["member_name"] else "",
    )


def row_name(row):
    """How the worklist names a request: "Member — Ministry". One a later
    Family response replaced (a Staff roster row) adds ", replaced by a later
    response", since the same change can then be listed twice, and each
    control, dialog and notice names its own row."""
    name = f"{row['member_label']} — {row['ministry_name']}"
    if row.get("latest") is False:
        name += f", {REPLACED.lower()}"
    return name


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
    Ministry"); one the Family asked for again, or undid on a reviewed form
    ("Family unticked", "Family chose to continue"), says when; an ignored one the
    parish added a request in place of names it; an edited request read by
    a leader of the Family's Ministry only says where it went. Accepted
    stays under To enter by hand and Entered by hand, as the component
    words it. A request whose roster already shows it before its step says
    ParishSoft already holds it (#1890). For Staff, an accepted request a
    later response replaced says so, and an entered one says who marked it
    and when.
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
    if row.get("now_matches"):
        parts.append(format_html("{}", NOW_MATCHES))
    if row.get("already_held"):
        parts.append(format_html("{}", ALREADY_HELD))
    if replaced_by:
        parts.append(format_html("Added in its place: {}", replaced_by))
    again, undone = asked or (None, None)
    if again:
        parts.append(format_html("Family asked again {}", _time(again)))
    if undone:
        # A request the parish added, or Staff entered in ParishSoft, that a
        # Family's reviewed form showed chosen and the Family undid (#1705):
        # it keeps its decision for Staff to decide.
        undid = UNTICKED if row["action"] == "join" else CONTINUED
        parts.append(format_html("{} {}", undid, _time(undone)))
    if not leader and row.get("latest") is False:
        parts.append(format_html("{}", REPLACED))
    if not leader and placement.stage is Stage.ENTERED_BY_HAND:
        by = format_html("Entered by {}", row["roster_by"] or FORMER)
        parts.append(
            format_html("{}, {}", by, _time(row["roster_at"]))
            if row["roster_at"]
            else by
        )
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


def decorate(
    rows, result, principal, *, token, every, decisions=None, asked=None, families=None
):
    """Give each shown row what the worklist component reads.

    ``every`` is all the view's rows (any page), so a request added in place
    of an ignored one is named without another read. ``decisions`` and
    ``asked`` are the rows' last decisions and Family asked again times,
    read here unless given (a detached page), as are ``families``, the
    shown requests' Family record ids (read only for a viewer who may open
    the Family hub).
    """
    leader = is_leader(principal)
    # Only a viewer who may open the Family hub needs the Families.
    if families is None:
        hub = allows(principal, Capability.CAMPAIGN_REPORT)
        families = _family_ids(rows) if hub else {}
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
        entry = not leader and roster_only(row)
        decided_by, decided_at, note = decisions.get(key, ("", None, ""))
        decorated.append(
            {
                "id": key,
                "version": row["version"],
                "name": row_name(row),
                # Nothing is recorded for a Member no longer in ParishSoft
                # (#1217), so such a row is drawn with no actions at all.
                "placement": row["placement"],
                # A roster row keeps its Entered by hand (``_withhold``).
                "leads": reader_leads and (entry or not row["source_mark"]),
                "member_cell": _member_cell(row),
                "family_id": families.get(key),
                "item_url": _item_url(row, token),
                "duid_cell": str(row["member_duid"])
                if row["member_duid"]
                else "New Member",
                "ministry_cell": _ministry_cell(row),
                "contact_cell": _time(row["last_contact_at"])
                if row["last_contact_at"]
                else "None recorded",
                "baseline": "",
                "family_answer": "" if row.get("parish_added") else _request_words(row),
                # A Family's reviewed form (#1705) shows an Ignored request
                # as the roster has it, which its Ignore dialog says.
                "shown_baseline": True,
                "reviewer_value": reviewer_value(row),
                # Shown only beside Not seen in ParishSoft: the roster still
                # shows the Member as before the entry.
                "not_seen": row.get("not_seen", False),
                "parishsoft_now": (
                    (
                        "Not in the roster"
                        if row["action"] == "join"
                        else "In the roster"
                    )
                    if row.get("not_seen")
                    else ""
                ),
                "submitted_at": row["submitted_at"],
                "decided_by": decided_by,
                "decided_at": decided_at,
                "note": note,
                "edit_choices": _ministry_choices(result, row),
                "_decision_line": _decision_line(
                    row, leader, asked.get(key), replacements.get(key), reader_leads
                ),
                "_leader_stage": row["stage"] == ACCEPTED,
                "_entry_only": entry,
            }
        )
    return decorated


def _withhold(row, actionable):
    """Redraw a decorated row's stage words for its reader after ``build``.

    The component words a row by its pipeline stage; a leader's accepted
    request reads by its decision instead, since leaders never see the
    update phase (#1160). And a Staff reader's rows for Members no longer in
    ParishSoft offer nothing (#1217), but for a roster row's Entered by
    hand, which is all such a row and one a later response replaced offer
    (``roster_only``).
    """
    if row.pop("_leader_stage"):
        row["stage_label"] = row["placement"].decision.label
    row["decision_label"] = row.pop("_decision_line")
    if row.pop("_entry_only"):
        entered = Action.ENTERED_BY_HAND.value
        row["icons"] = [icon for icon in row["icons"] if icon["action"] == entered]
        offered = any(not icon["selected"] for icon in row["icons"])
        row["kinds"] = entered if offered else ""
        row["offers_edit"] = False
    elif actionable and not row["leads"]:
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
    families=None,
    prefix=PREFIX,
):
    """The worklist and its table for one page of the view.

    ``result`` is ``read_rows``' answer. While other campaign work holds the
    page (not ``mutable``), every row is drawn with no actions and the
    selection bar and Add are gone; the page's notice says why.
    ``decisions`` and ``asked`` are given only to a detached page
    (``decorate``). ``prefix`` keeps the element ids apart where another
    page draws these rows (the Family hub's Member tabs, #2064).
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
        families=families,
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
        prefix=prefix,
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
        # A roster tick records no note, so its dialog shows none.
        silent=SILENT,
        add_body=ADD_BODY,
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
