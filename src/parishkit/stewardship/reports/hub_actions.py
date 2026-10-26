"""The Family hub's row actions (#2064; admin-portal spec, "Hub row actions").

Each hub tab's changed rows offer the actions their stream's own review page
offers for the same item, drawn by the same shared code: the stream's rows
are read and decorated by the stream's own worklist builder, narrowed to the
Family, and each hub row is given its item's decorated row
(``FieldRow.item``), whose icons and dialogs the hub draws through the
review worklist's own includes. Every decision posts to the stream's page,
so permissions, version checks, refusals and audit stay the stream's; the
dialogs do not take that page's answer as theirs (``answer_page`` off), so
the hub's tab is redrawn from its own address instead.
"""

from dataclasses import replace

from django.urls import reverse

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.web.tables import ALL, PAGE_SIZES, paginate

from .census_change_views import SORTING
from .census_changes import EVERY_STAGE, CensusQuery, census_changes
from .census_worklist import worklist_page
from .export_services import admit_campaign
from .financial import share_cell
from .hub_streams import frequency_words
from .information_worklist import EVERY_STAGE as INFORMATION_EVERY
from .information_worklist import SORTING as INFORMATION_SORTING
from .information_worklist import WorklistQuery as InformationWorklist
from .information_worklist import requests as information_requests
from .information_worklist import worklist_page as information_page
from .limitation_worklist import EVERY_STAGE as LIMITATIONS_EVERY
from .limitation_worklist import SORTING as LIMITATION_SORTING
from .limitation_worklist import WorklistQuery as LimitationWorklist
from .limitation_worklist import answer_rows
from .limitation_worklist import at_stage as limitations_at_stage
from .limitation_worklist import worklist_page as limitations_page
from .limitations import LimitationsQuery, limitations_report
from .ministry_followup import can_follow_up
from .ministry_worklist import EVERY, WorklistQuery, read_rows, worklist_context
from .pledge_review import reader_roles
from .pledge_review_views import EVERY as PLEDGE_EVERY
from .pledge_review_views import page_context as pledge_page
from .pledge_review_views import pledge_rows

# Every row of the Family, unpaged: a hub tab is one Family's rows.
ALL_ROWS = {"size": ALL}


def mutable(campaign_id):
    """Whether decisions may be recorded now, as the stream pages check it.

    While other campaign work holds the campaign (a restore review, cleanup,
    purge or go-live step) the pages offer no action, as the services would
    refuse them; so does the hub.
    """
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        return False
    return True


def census_worklist(campaign_id, family_id, principal, *, prefix, writable):
    """The census stream's worklist of one Family's changes, for its hub tabs.

    Read and decorated exactly as Census changes reads and decorates them
    (``census_changes`` and ``census_worklist.worklist_page``), every stage
    but History, unpaged. ``writable`` is ``mutable``'s answer: without it
    no icon is drawn. None for a reader who may not see census changes.
    """
    if not allows(principal, Capability.VIEW_CENSUS):
        return None
    query = CensusQuery(stage=EVERY_STAGE)
    result = census_changes(campaign_id, query, principal, family=family_id)
    worklist, _table = worklist_page(
        result,
        query,
        paginate(SORTING.sort_rows(result["rows"], SORTING.default), ALL_ROWS),
        reverse("admin:census_changes"),
        mutable=writable,
        prefix=prefix,
    )
    # The hub redraws its own tab after a decision (``answer_page`` off).
    worklist.answer_page = False
    return worklist


def items_of(*worklists):
    """Each worklist row by its item id, each given ``hub_row``, the id of
    the hub row (or line) that draws it: its worklist's prefix, as the
    stream page names its rows, so a redraw finds the same row again."""
    found = {}
    for worklist in worklists:
        for item in worklist.rows:
            item["hub_row"] = f"{worklist.prefix}-row-{item['id']}"
            found[item["id"]] = item
    return found


def attach(row, items):
    """``row`` with its item's decorated worklist row, if the worklist has it.

    The stage is then the worklist row's, so a hub row's chip always names
    the stage its icons act on (a refresh check's Conflict, for one).
    """
    item = items.get(row.item_id) if row and row.item_id else None
    if item is None:
        return row
    return replace(row, item=item, placement=item["placement"])


def shown(worklist, rows):
    """``worklist`` narrowed to the items ``rows`` (hub rows) show.

    A tab draws the dialogs of its own rows only: the Family's other
    changes are drawn on their own tabs.
    """
    ids = {row.item_id for row in rows if row and row.item_id}
    return replace(worklist, rows=[item for item in worklist.rows if item["id"] in ids])


def census_subject(subject, worklist):
    """``subject`` (a ``hub_census.Subject``) with its changes' row actions."""
    if subject is None or worklist is None:
        return subject
    items = items_of(worklist)
    return replace(
        subject,
        rows=tuple(attach(row, items) for row in subject.rows),
        addition=attach(subject.addition, items),
        actions=shown(worklist, (*subject.rows, subject.addition)),
    )


def census_tab(campaign_id, family_id, census, chosen, principal):
    """``census`` (the page's ``HubCensus``) with the chosen tab's row actions.

    Only the chosen Family or Member tab is read (one census read narrowed
    to the Family), and only when that tab shows a change: a tab with no
    change, a tab whose Member has no census answers yet, and every other
    tab read nothing more.
    """
    subject = census.subject(chosen)
    if subject is None or subject.awaiting or not census.census:
        return census
    if not allows(principal, Capability.VIEW_CENSUS):
        return census
    if not subject.changed and subject.addition is None:
        return census
    worklist = census_worklist(
        campaign_id,
        family_id,
        principal,
        prefix=f"hub-{subject.key}-census",
        writable=mutable(campaign_id),
    )
    acted = census_subject(subject, worklist)
    if subject is census.family:
        return replace(census, family=acted)
    return replace(
        census,
        members=tuple(acted if item is subject else item for item in census.members),
    )


def ministry_worklist(
    campaign_id, principal, ids, *, prefix, writable, members="current"
):
    """The Ministry stream's worklist of the requests ``ids``, for a Member tab.

    Read and decorated exactly as Ministry requests reads and decorates its
    rows (``ministry_worklist.read_rows`` and ``worklist_context``), every
    stage, then narrowed to the Member's requests the tab shows (``ids``).
    A request Ministry requests does not list for this reader (one whose
    Member ParishSoft no longer lists, which offers nothing) has no row, so
    the hub draws no icon for it either. ``members`` is Ministry requests'
    Members choice: the hub's is the table's default; a review wizard run
    reads with the choice it was started with (#2123).
    """
    query = WorklistQuery(stage=EVERY, size=str(max(PAGE_SIZES)), members=members)
    result = read_rows(campaign_id, query, principal)
    result = dict(result, rows=[row for row in result["rows"] if str(row["id"]) in ids])
    worklist = worklist_context(
        campaign_id,
        query,
        principal,
        result,
        token="",
        mutable=writable,
        action=reverse("admin:ministry_report"),
        prefix=prefix,
    )["worklist"]
    worklist.answer_page = False
    return worklist


def ministry_tab(campaign_id, rows, key, principal):
    """A Member tab's Ministry rows with their row actions, and the worklist.

    ``rows`` are ``hub_streams.read_member_ministry``'s and ``key`` the
    tab's key. Returns ``(rows, worklist)``; the worklist is None when the
    tab shows no request, or for a reader Ministry requests would refuse
    (nothing more is read then).
    """
    ids = {row.item_id for row in rows if row.item_id}
    if not ids or not can_follow_up(principal):
        return rows, None
    worklist = ministry_worklist(
        campaign_id,
        principal,
        ids,
        prefix=f"hub-{key}-ministry",
        writable=mutable(campaign_id),
    )
    items = items_of(worklist)
    return tuple(attach(row, items) for row in rows), worklist


def financial_tab(campaign_id, family_duid, financial, principal):
    """The Financial tab (``HubFinancial``) with its pledge's row actions.

    The pledge is read and decorated as Pledges reads and decorates it
    (``pledge_rows`` narrowed by the Family's DUID, then ``page_context``,
    every stage), and the tab's pledge is given that row and its fields an
    Edit's Reviewer's value (``reviewed_rows``). Nothing more is read when
    the Family has no pledge.
    """
    pledge = financial.pledge if financial else None
    if pledge is None or pledge.addition is None or not pledge.addition.item_id:
        return financial
    rows, _metadata = pledge_rows(campaign_id, principal, search=str(family_duid))
    # The search also matches longer DUIDs and names: keep the Family's own.
    rows = [row for row in rows if row["family_duid"] == family_duid]
    writable = mutable(campaign_id)
    worklist = pledge_page(
        rows,
        PLEDGE_EVERY,
        {"paging": {}, "search": ""},
        reader_roles(principal) if writable else frozenset(),
        mutable=writable,
        notice=None,
        prefix="hub-financial-pledges",
    )["worklist"]
    worklist.answer_page = False
    items = items_of(worklist)
    addition = attach(pledge.addition, items)
    return replace(
        financial,
        pledge=replace(
            pledge,
            rows=reviewed_rows(pledge.rows, addition.item),
            addition=addition,
            actions=shown(worklist, (addition,)),
        ),
    )


def reviewed_rows(rows, item):
    """The pledge's field rows with an Edit's values as their Reviewer's value.

    ``rows`` are ``hub_streams.financial_tab``'s, in its order (Annual
    pledge, Frequency, Share methods), and ``item`` the pledge's decorated
    Pledges row (None without one). An Edit replaces the amount and the
    frequency, and Share methods has a Reviewer's value only when the Edit
    changed them (#2177); without an Edit in force the rows are as they
    were.
    """
    amount, frequency, installment = item["reviewer_parts"] if item else ("", "", "")
    if not amount:
        return rows
    # The frequency is worded as the Family's answer beside it is; the share
    # methods as the Family's are, one after another, by short label (#2198).
    shares = share_cell(item["reviewer_shares"], "; ")
    values = (amount, frequency_words(frequency, installment), shares)
    return tuple(
        replace(row, reviewer=value) for row, value in zip(rows, values, strict=True)
    )


def limitations_worklist(campaign_id, family_duid, principal, *, writable):
    """The cannot participate stream's worklist of one Family's answers.

    Read and decorated as Limitations reads and decorates them
    (``limitations_report`` narrowed by the Family's DUID, without the
    Cannot contribute financially rows, which are decided on Pledges, then
    ``limitation_worklist.worklist_page``), every stage, unpaged.
    """
    query = LimitationWorklist(
        stage=LIMITATIONS_EVERY, limitations=LimitationsQuery(search=str(family_duid))
    )
    result = limitations_report(
        campaign_id, query.limitations, principal, contributions=False
    )
    rows = [row for row in answer_rows(result) if row["family_duid"] == family_duid]
    rows, counts = limitations_at_stage(rows, LIMITATIONS_EVERY)
    worklist, _table = limitations_page(
        counts,
        query,
        paginate(
            LIMITATION_SORTING.sort_rows(rows, LIMITATION_SORTING.default), ALL_ROWS
        ),
        reverse("admin:limitations_report"),
        mutable=writable,
        prefix="hub-other-limitations",
    )
    return worklist


def information_worklist(campaign_id, family_duid, item, *, writable):
    """The Additional information stream's worklist of the Family's item.

    Read and decorated as Additional information reads and decorates its
    requests (``information_worklist.requests``, narrowed by the Family's
    DUID, then ``worklist_page``), every stage but History; only the
    current item the tab shows is kept.
    """
    query = InformationWorklist(stage=INFORMATION_EVERY)
    query = replace(
        query, information=replace(query.information, search=str(family_duid))
    )
    _metadata, rows, counts = information_requests(campaign_id, query)
    rows = [row for row in rows if str(row["id"]) == str(item)]
    worklist, _table = information_page(
        counts,
        query,
        paginate(
            INFORMATION_SORTING.sort_rows(rows, INFORMATION_SORTING.default), ALL_ROWS
        ),
        reverse("admin:information_queue"),
        mutable=writable,
        prefix="hub-other-information",
    )
    return worklist


def other_tab(campaign_id, family_duid, other, principal):
    """The Additional tab (``HubOther``) with its answers' row actions.

    Each Yes to a cannot answer takes its Limitations row's actions, and the
    current Additional information item its own; each stream is read only
    when the tab shows one of its items and the reader may open that
    stream's page. ``HubOther.actions`` holds the worklists whose dialogs
    the tab draws.
    """
    subject = other.subject if other else None
    if subject is None:
        return other
    information = str(other.item) if other.item else ""
    ids = {row.item_id for row in subject.changed if row.item_id}
    limitations = bool(ids - {information}) and allows(
        principal, Capability.CAMPAIGN_REPORT
    )
    followed = information in ids and allows(principal, Capability.ADDITIONAL_FOLLOWUP)
    if not limitations and not followed:
        return other
    writable = mutable(campaign_id)
    worklists = []
    if limitations:
        worklists.append(
            limitations_worklist(campaign_id, family_duid, principal, writable=writable)
        )
    if followed:
        found = information_worklist(
            campaign_id, family_duid, information, writable=writable
        )
        worklists.append(found)
    items = items_of(*worklists)
    rows = tuple(attach(row, items) for row in subject.rows)
    for worklist in worklists:
        worklist.answer_page = False
    return replace(
        other,
        subject=replace(subject, rows=rows),
        actions=tuple(shown(worklist, rows) for worklist in worklists),
    )
