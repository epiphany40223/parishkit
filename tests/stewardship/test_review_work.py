"""My review work's panels and landing rule, without a database (#1690).

The panels follow the viewer's own menu: a stream is shown only when the
menu has its page, linked while the entry is and greyed with the menu's
reason while it is not. Counts are placed by the shared pipeline mapping.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts import admin_navigation as navigation
from parishkit.stewardship.accounts import review_work
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.pipeline import (
    MINISTRY_OPEN_STATES,
    Stage,
    Stream,
    ministry_placement,
)

STREAM_PAGES = [view.entry for view in review_work.STREAMS]


def chrome(names=STREAM_PAGES, greyed=(), counts=None):
    """The Admin chrome's ``entries`` and ``sections``, as ``admin_context`` has."""
    counts = counts or {}
    items = [
        navigation.MenuItem(
            "reports",
            name,
            str(navigation.PAGES[name].label),
            None if name in greyed else f"/{name}/",
            navigation.NO_CAMPAIGN if name in greyed else None,
        )
        for name in names
    ]
    return {
        "entries": {item.name: item for item in items},
        "sections": [
            {
                "items": [
                    {"name": item.name, "url": item.url, "count": counts.get(item.name)}
                    for item in items
                ]
            }
        ],
    }


def cards(panel):
    """Each card of a panel as (label, url, reason, count, note)."""
    return [
        (str(c.label), c.url, c.reason, c.count, str(c.note) if c.note else None)
        for c in panel.cards
    ]


def staff_counts(**streams):
    """``observe``'s value for Staff, with ``streams`` placed counts."""
    counts = {Stream(name): value for name, value in streams.items()}
    return {"campaign": object(), "counts": counts | {"leader": None}}


@pytest.mark.parametrize(
    ("roles", "lands"),
    [
        ({"staff"}, True),
        ({"ministry_leader"}, True),
        ({"staff", "ministry_leader"}, True),
        # An Administrator holds the other two roles as well and keeps Home.
        ({"administrator", "staff", "ministry_leader"}, False),
        ({"administrator"}, False),
    ],
)
def test_staff_and_leaders_land_on_my_review_work(roles, lands):
    """Only Staff and Ministry leaders who are not Administrators land here."""
    principal = Principal(uuid4(), frozenset(roles), frozenset())
    assert review_work.lands_here(principal) is lands


def test_open_ministry_states_are_to_review():
    """The menu's Ministry count is the queue's Unresolved filter
    (``state IN ('new','assigned','in_progress')``), which is exactly the
    states the pipeline places at To review, so it is the To review count."""
    assert {"new", "assigned", "in_progress"} == MINISTRY_OPEN_STATES
    for state in MINISTRY_OPEN_STATES:
        placement = ministry_placement(
            state=state, outcome=None, source_resolved=False, roster_entered=False
        )
        assert placement.stage is Stage.TO_REVIEW


def test_tally_sums_groups_and_skips_report_only():
    """Groups add up by stage; a report-only census change places nowhere."""
    to_review = ministry_placement(
        state="new", outcome=None, source_resolved=False, roster_entered=False
    )
    assert review_work._tally([(to_review, 2), (None, 5), (to_review, 3)]) == {
        "to_review": 5
    }


def test_staff_panels_in_order_with_every_stage_counted():
    """Every stream Staff may open, in the spec's order, each stage counted
    (a stage with no items reads 0); Ministry To review is the menu's count
    and its To enter by hand opens Roster changes to enter."""
    review = staff_counts(
        census={"to_review": 4, "conflict": 1, "ignored": 9},
        ministry={"to_enter_by_hand": 2, "entered_by_hand": 7},
        cannot_participate={"to_review": 3},
        financial={},
        additional_information={"to_handle": 6},
    )
    panels = review_work.panels(chrome(counts={"ministry_report": 5}), review)
    assert [panel.stream for panel in panels] == [
        "census",
        "ministry",
        "cannot_participate",
        "financial",
        "additional_information",
    ]
    by_stream = {panel.stream: panel for panel in panels}
    assert cards(by_stream["census"]) == [
        ("To review", "/census_changes/", None, 4, "changes"),
        ("Conflict", "/census_changes/", None, 1, "changes"),
        ("To enter by hand", "/census_changes/", None, 0, "changes"),
    ]
    assert cards(by_stream["ministry"]) == [
        ("To review", "/ministry_report/", None, 5, "requests"),
        (
            "To enter by hand",
            "/admin/reports/ministries/follow-up/roster/",
            None,
            2,
            "requests",
        ),
    ]
    assert cards(by_stream["financial"]) == [
        ("To review", "/financial_report/", None, 0, "pledges")
    ]
    assert cards(by_stream["additional_information"]) == [
        ("To review", "/information_queue/", None, 0, "items"),
        ("To handle", "/information_queue/", None, 6, "items"),
    ]


def test_a_stream_outside_the_menu_is_left_out_and_greyed_follows_the_menu():
    """No panel for a page the viewer's menu lacks; a greyed page's cards are
    greyed with the menu's reason, never links."""
    review = staff_counts(census={"to_review": 1}, financial={})
    panels = review_work.panels(
        chrome(names=["census_changes", "financial_report"], greyed=["census_changes"]),
        review,
    )
    assert [panel.stream for panel in panels] == ["census", "financial"]
    assert {card.url for card in panels[0].cards} == {None}
    assert {card.reason for card in panels[0].cards} == {navigation.NO_CAMPAIGN}


def test_ministry_without_a_roster_count_shows_only_to_review():
    """Without Staff's roster count (not read), only To review is shown, and
    with no menu count it has no number rather than a wrong one."""
    review = staff_counts()
    (panel,) = review_work.panels(chrome(names=["ministry_report"]), review)
    assert cards(panel) == [("To review", "/ministry_report/", None, None, "requests")]


def test_a_leaders_ministry_panel_has_one_card_per_ministry():
    """A leader's cards are their own Ministries, each linking the queue
    filtered to it; a removed one with open requests is marked."""
    mine = {
        "ministries": [
            {
                "duid": 9,
                "name": "Food pantry",
                "in_campaign": True,
                "join": 2,
                "leave": 1,
            },
            {"duid": 4, "name": "Choir", "in_campaign": False, "join": 0, "leave": 1},
        ]
    }
    review = {"campaign": object(), "counts": {"leader": mine}}
    (panel,) = review_work.panels(chrome(names=["ministry_report"]), review)
    assert cards(panel) == [
        ("Food pantry", "/ministry_report/?ministry=9", None, 3, "to review"),
        (
            "Choir (no longer in this campaign)",
            "/ministry_report/?ministry=4",
            None,
            1,
            "to review",
        ),
    ]


def test_a_stage_linked_stream_links_each_stage(monkeypatch):
    """Once a stream's page reads ``?stage=``, each card links its stage."""
    monkeypatch.setattr(review_work, "STAGE_LINKED", frozenset({Stream.CENSUS}))
    review = staff_counts(census={"conflict": 1})
    (panel,) = review_work.panels(chrome(names=["census_changes"]), review)
    assert [card.url for card in panel.cards] == [
        "/census_changes/?stage=to_review",
        "/census_changes/?stage=conflict",
        "/census_changes/?stage=to_enter_by_hand",
    ]


def test_link_cards_read_label_count_then_note():
    """Each card is one link whose name reads the stage, its count and what
    is counted, in that order; a greyed card is muted text with the reason."""
    review = staff_counts(census={"to_review": 1234})
    (panel,) = review_work.panels(chrome(names=["census_changes"]), review)
    html = render_to_string(
        "stewardship/components/link-cards.html",
        {"cards": panel.cards[:1], "cards_labelledby": "x"},
    )
    assert (
        '<a href="/census_changes/"><span class="link-card-label">To review</span> '
        '<span class="link-card-count">1,234</span> '
        '<span class="link-card-note">changes</span></a>'
    ) in html
    (greyed,) = review_work.panels(
        chrome(names=["census_changes"], greyed=["census_changes"]), review
    )
    html = render_to_string(
        "stewardship/components/link-cards.html",
        {"cards": greyed.cards[:1], "cards_labelledby": "x"},
    )
    assert "<a " not in html and str(navigation.NO_CAMPAIGN) in html


def test_an_administrator_also_gets_ready_to_update():
    """Ready to update is the Administrator's alone (only an Administrator
    starts an update run), and opens Publish to ParishSoft."""
    from django.urls import reverse

    review = staff_counts(census={"ready_to_update": 3})
    staff = review_work.panels(chrome(names=["census_changes"]), review)
    assert "Ready to update" not in [card.label for card in staff[0].cards]
    admin = review_work.panels(
        chrome(names=["census_changes"]) | {"admin": True}, review
    )
    assert cards(admin[0])[2] == (
        "Ready to update",
        reverse("admin:census_publication"),
        None,
        3,
        "changes",
    )


def test_home_is_named_my_review_work_for_those_who_land_there():
    """One name: the page, the menu and the trail call it the same."""
    staff = Principal(uuid4(), frozenset({"staff"}), frozenset())
    admin = Principal(uuid4(), frozenset({"administrator"}), frozenset())
    assert str(review_work.home_label(staff)) == "My review work"
    assert str(review_work.home_label(admin)) == "Home"


def test_an_unreadable_count_shows_its_cards_without_numbers(monkeypatch):
    """A failed count is logged and every stream the role may open is shown
    with no number, rather than failing the page."""
    from django.db import DatabaseError

    def fail(*_args):
        """The counts statement failing."""
        raise DatabaseError("boom")

    monkeypatch.setattr(review_work, "_read", fail)
    staff = Principal(uuid4(), frozenset({"staff"}), frozenset())
    counts = review_work.read_counts(staff, uuid4())
    assert counts == {
        Stream.CENSUS: None,
        Stream.ADDITIONAL_INFORMATION: None,
        Stream.FINANCIAL: None,
        Stream.CANNOT_PARTICIPATE: None,
        Stream.MINISTRY: None,
        "leader": None,
    }
    panels = review_work.panels(
        chrome(counts={"ministry_report": 2}), {"campaign": object(), "counts": counts}
    )
    by_stream = {panel.stream: cards(panel) for panel in panels}
    assert [card[3] for card in by_stream["census"]] == [None, None, None]
    # The menu's own Ministry count still shows; the roster count does not.
    assert [card[3] for card in by_stream["ministry"]] == [2, None]


def test_home_takes_its_counts_from_the_menu_statement():
    """Admin Home's panel reads no statement of its own: ``with_counts`` takes
    what the menu's open counts left on the request, or none at all."""
    flags = {"census": True, "information": False}
    home = {"campaign": object(), "flags": flags, "leader": None}
    request = SimpleNamespace(_stewardship_review_counts={Stream.CENSUS: {"x": 1}})
    assert review_work.with_counts(request, home)["counts"] == {
        Stream.CENSUS: {"x": 1},
        "leader": None,
    }
    assert review_work.with_counts(SimpleNamespace(), home)["counts"] == {
        Stream.CENSUS: None,
        "leader": None,
    }
    # My review work's own value already has its counts.
    landing = {"campaign": None, "counts": {}}
    assert review_work.with_counts(request, landing) is landing
    # The menu statement's parameters never collide with its own flags.
    assert review_work.parameters(flags) == {
        "review_census": True,
        "review_information": False,
    }
