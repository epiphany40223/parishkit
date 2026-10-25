"""The review wizard's streams: one ``ReviewStream`` each (#2123).

Review pipeline spec, "Wizard shared component": the wizard adds pages, not
review logic. Each stream here names the code its Review work table
already runs, and the wizard's start page, sequence and pages read a
stream only through this module:

- its label (the menu entry's), its page and its access: the stream page's
  own ``_principal``, so a reader may review a stream one at a time exactly
  when its Review work page admits them;
- its filters: the table's own query class and filter template, so the
  Advanced filters are parsed and drawn as the table's are;
- its read: the table's own read and Stage selection, run at Every stage
  and then narrowed to the run's Stage (Waiting, Basic's set, by the same
  placements the Stage filter uses), giving one ``review_wizard.Item`` per
  matching item with its subject (``subject_of``);
- its audit: the stream page's own view event, with the count read.

The streams are in the Review work menu's order.
"""

from dataclasses import dataclass
from functools import cached_property

from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.pipeline import TERMS, Decision, Stage

from .family_links import family_record_ids
from .review_wizard import FAMILY, WAITING, Item, waiting

# Every stage, as each table's Stage filter names it.
EVERY = "all"
NOT_SEEN = "not_seen"


def _stage_list(*stages, not_seen=False):
    """A Stage filter's (value, label) choices, after Waiting, before Every stage.

    Never History: the wizard leaves History out (R52 Part W; the tables'
    own Stage filters still offer it).
    """
    choices = [(stage.value, stage.label) for stage in stages]
    if not_seen:
        choices.append((NOT_SEEN, TERMS["not_seen"]))
    return choices


def _values(parameters):
    """``parameters`` (a dict) as the MultiValueDict the query classes parse."""
    return MultiValueDict({key: [value] for key, value in parameters.items()})


def _read_stage(values):
    """The table read's Stage for the run's: Every stage when it is Waiting."""
    stage = values.get("stage", WAITING)
    return EVERY if stage == WAITING else stage


def _keep(items, stage, *, leader=False):
    """``items`` narrowed to the run's Stage: Waiting's set, else as read,
    and never a History item, which is never a wizard page (R52 Part W)."""
    if stage != WAITING:
        return [item for item in items if item.stage != Stage.HISTORY.value]
    return [item for item in items if waiting(item.stage, leader=leader)]


def _record_ids(campaign_id, rows):
    """Each row's Family record id by DUID, in one read (``family_links``)."""
    return family_record_ids(campaign_id, (row["family_duid"] for row in rows))


def _int(value):
    """A DUID as an int, or None."""
    return int(value) if value not in (None, "") else None


# --------------------------------------------------------------- the reads


def _census(campaign_id, values, principal):
    """Census changes' items: the table's read (``census_changes``) and filters.

    A household change is the Family's (``("family", "")``, as the hub keys
    it); a Member's change or the Member the Family added is that Member's.
    """
    from .census_changes import CensusQuery, census_changes

    query = CensusQuery.parse(_values(values | {"stage": _read_stage(values)}))
    rows = census_changes(campaign_id, query, principal)["rows"]
    ids = _record_ids(campaign_id, rows)
    items = []
    for row in rows:
        family = ids.get(row["family_duid"])
        if family is None:
            continue
        kind = row["entity_kind"]
        items.append(
            Item(
                id=str(row["id"]),
                family=str(family),
                kind=kind,
                key="" if kind == FAMILY else str(row["entity_key"]),
                name=row["family_name"],
                duid=row["family_duid"],
                stage=row["stage"],
                submitted_at=row["submitted_at"],
                member_name=None if kind == FAMILY else row["who"],
            )
        )
    return _keep(items, values.get("stage", WAITING))


def _ministry(campaign_id, values, principal):
    """Ministry requests' items: the leader-scoped follow-up read (``read_rows``).

    One Member per page: each request's Member, as its request row names it,
    in its Family (one read of the shown requests' Families and entities).
    """
    from parishkit.stewardship.workflows.models import MinistryRequest

    from .ministry_worklist import (
        WorklistQuery,
        chosen,
        for_reader,
        is_leader,
        read_rows,
    )

    stage = values.get("stage", WAITING)
    query = for_reader(
        WorklistQuery.parse(_values(values | {"stage": _read_stage(values)})),
        principal,
    )
    rows = read_rows(campaign_id, query, principal)["rows"]
    if stage != WAITING:
        rows = chosen(rows, query.stage)
    subjects = {
        str(pk): (str(family), kind, key)
        for pk, family, kind, key in MinistryRequest.objects.filter(
            pk__in=[row["id"] for row in rows]
        ).values_list("pk", "submission__family_id", "entity_kind", "entity_key")
    }
    items = []
    for row in rows:
        found = subjects.get(str(row["id"]))
        if found is None:
            continue
        family, kind, key = found
        items.append(
            Item(
                id=str(row["id"]),
                family=family,
                kind=kind,
                key=str(key),
                name=row["member_label"],
                duid=_int(row.get("member_duid")),
                stage=row["stage"],
                submitted_at=row.get("submitted_at"),
            )
        )
    return _keep(items, stage, leader=is_leader(principal))


def _pledges(campaign_id, values, principal):
    """Pledges' items: one Family each (``pledge_rows``, ``at_stage``)."""
    from .pledge_review_views import PledgeQuery, at_stage, pledge_rows

    query = PledgeQuery.parse(_values(values | {"stage": _read_stage(values)}))
    rows, _metadata = pledge_rows(campaign_id, principal, query.search)
    items = [
        Item(
            id=str(row["id"]),
            family=str(row["item"].family_id),
            kind=FAMILY,
            key="",
            name=row["family_name"],
            duid=row["family_duid"],
            stage=row["stage"],
            submitted_at=row.get("submitted_at"),
        )
        for row in at_stage(rows, query.stage)
    ]
    return _keep(items, values.get("stage", WAITING))


def _information(campaign_id, values, principal):
    """Additional information's items: one Family each (``requests``)."""
    from .information_worklist import WorklistQuery, requests

    query = WorklistQuery.parse(_values(values | {"stage": _read_stage(values)}))
    _metadata, rows, _counts = requests(campaign_id, query)
    ids = _record_ids(campaign_id, rows)
    items = [
        Item(
            id=str(row["id"]),
            family=str(ids[row["family_duid"]]),
            kind=FAMILY,
            key="",
            name=row["family_name"],
            duid=row["family_duid"],
            stage=row["placement"].stage.value,
            submitted_at=row.get("submitted_at"),
        )
        for row in rows
        if ids.get(row["family_duid"]) is not None
    ]
    return _keep(items, values.get("stage", WAITING))


def _limitations(campaign_id, values, principal):
    """Limitations' items: the report's answers (``answer_rows``, ``at_stage``).

    Each answer is on the page it belongs to: a Family's own answers (Cannot
    contribute financially, read from Pledges' rows by the report itself,
    and Cannot attend Mass) on the Family page, every other on its
    Member's page.
    """
    from .limitation_worklist import WorklistQuery, answer_rows, at_stage
    from .limitations import limitations_report

    query = WorklistQuery.parse(_values(values | {"stage": _read_stage(values)}))
    # The financial answer is read only for Pledges' readers (the report's
    # own ``cannot_contribute`` checks), as the Limitations table lists it.
    result = limitations_report(campaign_id, query.limitations, principal)
    rows, _counts = at_stage(answer_rows(result), query.stage)
    ids = _record_ids(campaign_id, rows)
    items = []
    for row in rows:
        family = ids.get(row["family_duid"])
        if family is None:
            continue
        member = row["kind"] in ("member", "proposed_member")
        items.append(
            Item(
                id=str(row["id"]),
                family=str(family),
                kind=row["kind"] if member else FAMILY,
                key=str(row["member_key"]) if member else "",
                name=row["family_name"],
                duid=row["family_duid"],
                stage=row["placement"].stage.value,
                submitted_at=row.get("submitted_at"),
                member_name=row.get("member_name") if member else None,
            )
        )
    return _keep(items, values.get("stage", WAITING))


# --------------------------------------------------------------- the access


def _census_principal(request, store, *, read_only=False):
    """Census changes' own principal check."""
    from .census_change_views import _principal

    return _principal(request, store, read_only=read_only)


def _ministry_principal(request, store, *, read_only=False):
    """Ministry requests' own principal check (Staff or a leader with scope)."""
    from .ministry_followup_views import _principal

    return _principal(request, store, read_only=read_only)


def _pledges_principal(request, store, *, read_only=False):
    """Pledges' own principal check."""
    from .pledge_review_views import _principal

    return _principal(request, store, read_only=read_only)


def _information_principal(request, store, *, read_only=False):
    """Additional information's own principal check."""
    from .information_views import _principal

    return _principal(request, store, read_only=read_only)


def _limitations_principal(request, store, *, read_only=False):
    """Limitations' own principal check."""
    from .limitation_views import _principal

    return _principal(request, store, read_only=read_only)


# ---------------------------------------------------------------- the audit


def _census_audit(principal, campaign_id, outcome, count, values):
    """Audited as a view of Census changes: a count only."""
    from parishkit.stewardship.audit.schemas import Action

    from .census_change_views import _audit

    _audit(Action.CENSUS_CHANGES_VIEWED, principal, campaign_id, outcome, count)


def _ministry_audit(principal, campaign_id, outcome, count, values):
    """Audited as a view of Ministry requests (whose view has no count)."""
    from .ministry_followup_views import _audit

    _audit(principal, campaign_id, outcome)


def _pledges_audit(principal, campaign_id, outcome, count, values):
    """Audited as a view of Pledges: a count only."""
    from parishkit.stewardship.audit.schemas import Action

    from .pledge_review_views import _audit

    _audit(Action.PLEDGES_TO_ENTER_VIEWED, principal, campaign_id, outcome, count)


def _information_audit(principal, campaign_id, outcome, count, values):
    """Audited as a view of Additional information (whose view has no count)."""
    from .information_views import _audit

    _audit(principal, campaign_id, outcome)


def _limitations_audit(principal, campaign_id, outcome, count, values):
    """Audited as a view of Limitations: a count and its closed choices
    (``limitation_views.audit_choices``' keys, never the search text)."""
    from parishkit.stewardship.audit.schemas import Action

    from .limitation_views import _audit

    choices = {
        "search_used": bool(values.get("search_used")),
        "report_filter": values.get("show", "any"),
    }
    _audit(
        Action.TALENTS_REPORT_VIEWED, principal, campaign_id, outcome, count, choices
    )


# ------------------------------------------------------- the Advanced filters


def _census_context(principal, campaign_id, values):
    """What Census changes' filter template draws: its route and kind choices."""
    from .census_change_views import KIND_LABELS, ROUTE_LABELS
    from .census_changes import CensusQuery

    return {
        "query": CensusQuery(
            search=values.get("search", ""),
            route=values.get("route", "any"),
            kind=values.get("kind", "any"),
        ),
        "route_choices": list(ROUTE_LABELS.items()),
        "kind_choices": list(KIND_LABELS.items()),
    }


def _ministry_context(principal, campaign_id, values):
    """What Ministry requests' filter template draws: the reader's Ministries.

    The follow-up selection's own empty read lists exactly the Ministries in
    the reader's scope, as the table's Ministry filter does.
    """
    from .ministry_worklist import MEMBERS, WorklistQuery, is_leader, read_rows

    query = WorklistQuery(
        search=values.get("search", ""),
        ministry=values.get("ministry", ""),
        kind=values.get("kind", "any"),
        members=values.get("members", "current"),
    )
    result = read_rows(campaign_id, WorklistQuery(), principal, empty=True)
    return {
        "query": query,
        "ministries": result["ministries"],
        "members_choices": None if is_leader(principal) else MEMBERS,
    }


def _pledges_context(principal, campaign_id, values):
    """What Pledges' filter template draws: the search."""
    return {"search": values.get("search", "")}


def _information_context(principal, campaign_id, values):
    """What Additional information's filter template draws: the search."""
    from .information import InformationQuery
    from .information_worklist import WorklistQuery

    return {
        "query": WorklistQuery(
            information=InformationQuery(
                disposition="all", search=values.get("search", "")
            )
        )
    }


def _limitations_context(principal, campaign_id, values):
    """What Limitations' filter template draws: the search and Show."""
    from .limitation_worklist import WorklistQuery
    from .limitations import LimitationsQuery

    return {
        "query": WorklistQuery(
            limitations=LimitationsQuery(
                search=values.get("search", ""), show=values.get("show", "any")
            )
        )
    }


def _ministry_stages(principal):
    """Ministry requests' Stage choices: a leader's or Staff's, as the table's."""
    from .ministry_worklist import ACCEPTED, LEADER_STAGES, STAFF_STAGES, is_leader

    if is_leader(principal):
        # The leader's own choices (``LEADER_STAGES``), worded as the
        # table's: Accepted is a decision's word, the rest are stages.
        return [
            (
                value,
                Decision.ACCEPTED.label if value == ACCEPTED else Stage(value).label,
            )
            for value in LEADER_STAGES
        ]
    return _stage_list(*STAFF_STAGES, not_seen=True)


def _census_stages(principal):
    """Census changes' Stage choices, as the table's."""
    from .census_changes import STAGES

    return _stage_list(*STAGES, not_seen=True)


def _pledges_stages(principal):
    """Pledges' Stage choices, as the table's."""
    from .pledge_review_views import STAGES

    return _stage_list(*STAGES, not_seen=True)


def _information_stages(principal):
    """Additional information's Stage choices, as the table's."""
    from .information_worklist import STAGES

    return _stage_list(*STAGES)


def _limitations_stages(principal):
    """Limitations' Stage choices, as the table's."""
    from .limitations import STAGES

    return _stage_list(*STAGES)


@dataclass(frozen=True)
class ReviewStream:
    """One stream as the wizard sees it; see the module docstring.

    ``key`` is the run's stream value (``review_run_models.STREAMS``),
    ``entry`` its Review work page and menu entry. ``grouped`` streams
    (Census changes, Limitations) have a Family page and Member pages;
    ``member`` streams (Ministry requests) one Member per page; the others
    one Family per page. ``closed`` are the Advanced filters besides Stage
    and search, each with its default, as the table's query class reads
    them. ``read(campaign_id, values, principal)`` returns the matching
    ``Item`` rows; ``principal(request, store, read_only=...)`` is the
    stream page's own check; ``audit(principal, campaign_id, outcome,
    count, values)`` records the stream page's view event; ``context`` and
    ``stages`` draw the Advanced filters. ``search_help`` is Basic's help
    under Search name: what the stream's search matches (its table's own
    search) and that a blank search means every waiting item.
    """

    key: str
    entry: str
    filters_template: str
    read: object
    principal: object
    audit: object
    context: object
    stages: object
    search_help: str = ""
    grouped: bool = False
    member: bool = False
    closed: tuple = ()

    @cached_property
    def label(self):
        """The stream's name: its Review work menu entry's label."""
        from parishkit.stewardship.accounts.admin_navigation import PAGES

        return PAGES[self.entry].label


STREAMS = (
    ReviewStream(
        "census",
        "census_changes",
        "stewardship/census-worklist-filters.html",
        _census,
        _census_principal,
        _census_audit,
        _census_context,
        _census_stages,
        # census_changes.select's search: names and Family DUID.
        _(
            "Family name, a Member's name, or the Family DUID. Leave blank for "
            "every waiting change."
        ),
        grouped=True,
        closed=(("route", "any"), ("kind", "any")),
    ),
    ReviewStream(
        "ministry",
        "ministry_report",
        "stewardship/ministry-worklist-filters.html",
        _ministry,
        _ministry_principal,
        _ministry_audit,
        _ministry_context,
        _ministry_stages,
        # The Ministry follow-up search (0131): Member and Ministry names.
        _("Member's name or the Ministry. Leave blank for every waiting request."),
        member=True,
        closed=(("ministry", ""), ("kind", "any"), ("members", "current")),
    ),
    ReviewStream(
        "pledges",
        "pledges_to_enter",
        "stewardship/pledges-filters.html",
        _pledges,
        _pledges_principal,
        _pledges_audit,
        _pledges_context,
        _pledges_stages,
        # The pledge rows' search (0175): Family name and DUID.
        _("Family name or the Family DUID. Leave blank for every waiting pledge."),
    ),
    ReviewStream(
        "information",
        "information_queue",
        "stewardship/information-worklist-filters.html",
        _information,
        _information_principal,
        _information_audit,
        _information_context,
        _information_stages,
        # The information queue's search (0175): Family, DUID and the text.
        _(
            "Family name, the Family DUID, or words in the information or notes. "
            "Leave blank for every waiting item."
        ),
    ),
    ReviewStream(
        "limitations",
        "limitations_report",
        "stewardship/limitations-worklist-filters.html",
        _limitations,
        _limitations_principal,
        _limitations_audit,
        _limitations_context,
        _limitations_stages,
        # The Limitations search (0126): names and Family DUID.
        _(
            "Family name, a Member's name, or the Family DUID. Leave blank for "
            "every waiting answer."
        ),
        grouped=True,
        closed=(("show", "any"),),
    ),
)
BY_KEY = {stream.key: stream for stream in STREAMS}
BY_ENTRY = {stream.entry: stream for stream in STREAMS}
# The menu entry and page of the wizard itself.
ENTRY = "review_wizard"
NAME = _("Review one at a time")
