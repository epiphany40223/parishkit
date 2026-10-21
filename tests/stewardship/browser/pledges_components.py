"""Pledges (#1686), served from its real page context.

The page is ``pledges-to-enter.html`` drawn by the view's own
``page_context`` from sample rows at every stage a Staff reader meets, so
the browser checks the real worklist, editor and export pane. Each test
answers the page's POST with one of these pages (``fulfil_post_with``), as
the view answers with its page redrawn.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.reports import pledge_review_views as views
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.reports.pledge_review import placement
from parishkit.stewardship.responses.refresh_checks import CheckState

from ..test_pledge_review import item, pledge_row, review

PATH = "/pledges"
# Pledges a refresh checked (#1704): Entered incorrectly, Not seen in
# ParishSoft, Confirmed and an Ignored one ParishSoft now matches.
CHECKED = "/pledges-checked"
EVERY = "/pledges-every"
ACCEPTED = "/pledges-accepted"
EDITED = "/pledges-edited"
# The page's own addresses (#1820): the plain one (To review) and Every
# stage's, which a reload fetches. The browser only puts an address in its
# bar when the path is the page's own.
ADDRESS = "/admin/reports/financial/pledges/"
EVERY_ADDRESS = ADDRESS + "?stage=all"
ACCEPTED_NOTICE = "2 pledges accepted."
EDITED_NOTICE = "Family 03 accepted (edited)."
STAFF = frozenset({"staff"})


def _row(index, stage="to_review", latest=None, check=None, **extra):
    """Family ``index``'s pledge at ``stage``, with a fixed response id.

    ``check`` is its latest refresh check (a ``CheckState``), as
    ``pledge_rows`` reads it (#1704).
    """
    found = replace(
        item(stage, latest), submission_id=UUID(int=index), family_duid=1000 + index
    )
    row = pledge_row(found, family_name=f"Family {index:02d}", **extra)
    row["family_duid"] = 1000 + index
    row["check"] = check
    row["placement"] = placement(found, check)
    row["stage"] = row["placement"].stage.value
    row["not_seen"] = bool(
        check and check.not_seen(row["placement"].stage is Stage.ENTERED_BY_HAND)
    )
    return row


def checked_rows():
    """One pledge at each outcome a refresh check gives (#1704)."""

    def check(outcome, amount, unseen=0):
        """A latest check finding ``amount`` in ParishSoft."""
        return CheckState(outcome, Decimal(amount), unseen, 2)

    marked = review("accepted", entered=True)
    return [
        _row(7, "entered_by_hand", marked, check("entered_incorrectly", "1100.00")),
        _row(8, "entered_by_hand", marked, check("unchanged", "0.00", unseen=2)),
        _row(9, "entered_by_hand", marked, check("confirmed", "1200.00")),
        _row(6, "ignored", review("ignored"), check("ignored_now_matches", "1200.00")),
    ]


def rows(edited=False, accepted=False):
    """Every stage a Staff reader meets; ``edited`` and ``accepted`` apply the
    decisions the tests take (an Edit of Family 03, a bulk Accept of 01, 02)."""
    edit = review("accepted_edited", amount=Decimal("1500.00"), frequency="annual")
    found = [
        _row(1),
        _row(2, annual=MoneyAmount(0), frequency=None, shares=[]),
        _row(3),
        _row(4, "to_enter_by_hand", review("accepted")),
        _row(5, "entered_by_hand", review("accepted", entered=True)),
        _row(6, "ignored", review("ignored")),
    ]
    if accepted:
        found[0] = _row(1, "to_enter_by_hand", review("accepted"))
        found[1] = _row(
            2,
            "to_enter_by_hand",
            review("accepted"),
            annual=MoneyAmount(0),
            frequency=None,
            shares=[],
        )
    if edited:
        found[2] = _row(3, "to_enter_by_hand", edit)
    return found


def _page(context, admin, found, stage, notice=None):
    """The real page for ``found`` at ``stage``, for a Staff reader."""
    extra = {"paging": {"size": "25"}, "search": ""}
    page = views.page_context(found, stage, extra, STAFF, mutable=True, notice=notice)
    return (
        "text/html",
        render_to_string(
            "stewardship/pledges-to-enter.html",
            context | {"admin_chrome": admin} | page,
        ),
    )


def components(context, admin):
    """To review, Every stage, and the pages after a bulk Accept and an Edit."""
    return {
        PATH: _page(context, admin, rows(), "to_review"),
        EVERY: _page(context, admin, rows(), "all"),
        ADDRESS: _page(context, admin, rows(), "to_review"),
        EVERY_ADDRESS: _page(context, admin, rows(), "all"),
        ACCEPTED: _page(
            context, admin, rows(accepted=True), "to_review", ACCEPTED_NOTICE
        ),
        EDITED: _page(context, admin, rows(edited=True), "to_review", EDITED_NOTICE),
        CHECKED: _page(context, admin, checked_rows(), "all"),
    }
