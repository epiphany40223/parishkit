"""My review work's panels and landing rule, without a database (#1690).

The panels follow the viewer's own menu: a stream is shown only when the
menu has its page, linked while the entry is and greyed with the menu's
reason while it is not. Counts are placed by the shared pipeline mapping,
and a stage the menu counts takes the menu entry's own read (#2101).
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


def chrome(names=STREAM_PAGES, greyed=(), counts=None, stages=None):
    """The Admin chrome's ``entries`` and ``sections``, as ``admin_context`` has.

    ``stages`` are the menu read's counts by stage, by entry name, which
    ``admin_context.attach_counts`` leaves on each linked entry.
    """
    counts = counts or {}
    stages = stages or {}
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
                    {
                        "name": item.name,
                        "url": item.url,
                        "count": counts.get(item.name),
                        "stages": stages.get(item.name) if item.url else None,
                    }
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


def test_each_streams_groups_are_placed_by_the_shared_mapping():
    """The menu's reads (#2101) count every group at the stage the shared
    mapping, which each worklist places its rows with, gives it."""
    from parishkit.stewardship.reports import (
        census_changes,
        information_worklist,
        limitation_worklist,
    )

    assert census_changes.group_stages(
        [
            ["api", "member", "unreviewed", "pending", 2],
            ["api", "member", "approved", "conflict", 1],
            ["api", "member", "approved", "pending", 3],
            ["manual", "member", "approved", "pending", 4],
            ["manual", "member", "approved", "resolved_external", 5],
            ["api", "member", "ignored", "pending", 6],
        ]
    ) == {
        "to_review": 2,
        "conflict": 1,
        "ready_to_update": 3,
        "to_enter_by_hand": 4,
        "entered_by_hand": 5,
        "ignored": 6,
    }
    # A To review request needs neither follow-up nor an Ignore saved.
    assert information_worklist.group_stages(
        [[False, False, False, 2], [False, False, True, 1], [True, False, False, 3]]
        + [[True, True, False, 4]]
    ) == {"to_review": 2, "ignored": 1, "to_handle": 3, "handled": 4}
    # A decision no longer in force, or Back to review, is To review again;
    # the Cannot contribute answers at Pledges' To review count there too.
    assert limitation_worklist.group_stages(
        [[None, False, 1], ["accepted", False, 2], ["none", True, 3]]
        + [["accepted", True, 4], ["handled", True, 5], ["ignored", True, 6]],
        contributions=7,
    ) == {"to_review": 13, "to_handle": 4, "handled": 5, "ignored": 6}
    for group_stages, unknown in (
        (census_changes.group_stages, [["api", "member", "maybe", "pending", 1]]),
        (limitation_worklist.group_stages, [["maybe", True, 1]]),
    ):
        with pytest.raises(ValueError):
            group_stages(unknown)


def test_a_count_adds_its_reads_and_has_no_number_when_one_is_unread():
    """A stage the menu and this module both count (Census changes' To enter
    by hand: the Families' changes and the parish's additions) adds the
    two; either one unread leaves the card with no number."""
    (census, *_) = review_work.STREAMS
    stage = "to_enter_by_hand"
    assert review_work._count(stage, census, {stage: 2}, {stage: 3}) == 5
    assert review_work._count(stage, census, {}, {}) == 0
    assert review_work._count(stage, census, None, {stage: 3}) is None
    assert review_work._count(stage, census, {stage: 2}, None) is None
    assert review_work._count("to_review", census, {"to_review": 4}, None) == 4


MENU_STAGES = {
    "census_changes": {
        "to_review": 4,
        "conflict": 1,
        "ignored": 9,
        "to_enter_by_hand": 2,
    },
    "ministry_report": {"to_review": 5, "to_enter_by_hand": 2},
    "limitations_report": {"to_review": 3, "to_handle": 2},
    "information_queue": {"to_handle": 6},
    "pledges_to_enter": {"to_review": 3},
}


def test_staff_panels_in_order_with_every_stage_counted():
    """Every stream Staff may open, in the Review work menu's order (#2108),
    each stage counted (a stage with no items reads 0). A stage the menu
    counts is the menu's read (#2101), the others this module's. Every card
    opens its stream's page at its Stage (#1818; Limitations since #2101), Not seen in
    ParishSoft at ``not_seen``, which is shown only while some item carries
    the flag (none of Financial's here), as the worklists offer it."""
    review = staff_counts(
        census={"to_enter_by_hand": 1, "entered_incorrectly": 2, "not_seen": 3},
        ministry={"not_seen": 1},
        cannot_participate={},
        financial={"to_review": 99, "conflict": 2, "to_enter_by_hand": 1},
        additional_information={},
    )
    panels = review_work.panels(chrome(stages=MENU_STAGES), review)
    assert [panel.stream for panel in panels] == [
        "census",
        "ministry",
        "financial",
        "additional_information",
        "cannot_participate",
    ]
    by_stream = {panel.stream: panel for panel in panels}
    assert cards(by_stream["census"]) == [
        ("To review", "/census_changes/?stage=to_review", None, 4, "changes"),
        ("Conflict", "/census_changes/?stage=conflict", None, 1, "changes"),
        (
            "To enter by hand",
            "/census_changes/?stage=to_enter_by_hand",
            None,
            3,
            "changes",
        ),
        (
            "Entered incorrectly",
            "/census_changes/?stage=entered_incorrectly",
            None,
            2,
            "changes",
        ),
        (
            "Not seen in ParishSoft",
            "/census_changes/?stage=not_seen",
            None,
            3,
            "changes",
        ),
    ]
    assert cards(by_stream["ministry"]) == [
        ("To review", "/ministry_report/?stage=to_review", None, 5, "requests"),
        (
            "To enter by hand",
            "/ministry_report/?stage=to_enter_by_hand",
            None,
            2,
            "requests",
        ),
        (
            "Not seen in ParishSoft",
            "/ministry_report/?stage=not_seen",
            None,
            1,
            "requests",
        ),
    ]
    # Financial's cards open Pledges at their stage (#1686).
    assert cards(by_stream["financial"]) == [
        ("To review", "/pledges_to_enter/?stage=to_review", None, 3, "pledges"),
        ("Conflict", "/pledges_to_enter/?stage=conflict", None, 2, "pledges"),
        (
            "To enter by hand",
            "/pledges_to_enter/?stage=to_enter_by_hand",
            None,
            1,
            "pledges",
        ),
        (
            "Entered incorrectly",
            "/pledges_to_enter/?stage=entered_incorrectly",
            None,
            0,
            "pledges",
        ),
    ]
    assert cards(by_stream["additional_information"]) == [
        ("To review", "/information_queue/?stage=to_review", None, 0, "items"),
        ("To handle", "/information_queue/?stage=to_handle", None, 6, "items"),
    ]
    assert cards(by_stream["cannot_participate"]) == [
        ("To review", "/limitations_report/?stage=to_review", None, 3, "answers"),
        ("To handle", "/limitations_report/?stage=to_handle", None, 2, "answers"),
    ]


def test_a_stream_outside_the_menu_is_left_out_and_greyed_follows_the_menu():
    """No panel for a page the viewer's menu lacks; a greyed page's cards are
    greyed with the menu's reason, never links."""
    review = staff_counts(census={"to_review": 1}, financial={})
    panels = review_work.panels(
        chrome(names=["census_changes", "pledges_to_enter"], greyed=["census_changes"]),
        review,
    )
    assert [panel.stream for panel in panels] == ["census", "financial"]
    assert {card.url for card in panels[0].cards} == {None}
    assert {card.reason for card in panels[0].cards} == {navigation.NO_CAMPAIGN}


def test_ministry_without_a_roster_count_shows_only_to_review():
    """Without Staff's roster work (not read), only To review is shown, and
    with no menu count it has no number rather than a wrong one."""
    review = staff_counts()
    (panel,) = review_work.panels(chrome(names=["ministry_report"]), review)
    assert cards(panel) == [
        ("To review", "/ministry_report/?stage=to_review", None, None, "requests")
    ]


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


def test_every_stream_links_its_stages():
    """The Administrator's decision (#1818): each stream's cards link its
    stage; Limitations' page takes ``?stage=`` too since #2101."""
    linked = {
        Stream.CENSUS,
        Stream.MINISTRY,
        Stream.CANNOT_PARTICIPATE,
        Stream.ADDITIONAL_INFORMATION,
        Stream.FINANCIAL,
    }
    assert linked == review_work.STAGE_LINKED


def test_a_stream_not_stage_linked_links_its_plain_page(monkeypatch):
    """A stream outside ``STAGE_LINKED`` links its page with no Stage."""
    monkeypatch.setattr(review_work, "STAGE_LINKED", frozenset())
    review = staff_counts(census={})
    (panel,) = review_work.panels(chrome(names=["census_changes"]), review)
    assert {card.url for card in panel.cards} == {"/census_changes/"}


def test_link_cards_read_label_count_then_note():
    """Each card is one link whose name reads the stage, its count and what
    is counted, in that order; a greyed card is muted text with the reason."""
    review = staff_counts(census={})
    stages = {"census_changes": {"to_review": 1234}}
    (panel,) = review_work.panels(
        chrome(names=["census_changes"], stages=stages), review
    )
    html = render_to_string(
        "stewardship/components/link-cards.html",
        {"cards": panel.cards[:1], "cards_labelledby": "x"},
    )
    assert (
        '<a href="/census_changes/?stage=to_review">'
        '<span class="link-card-label">To review</span> '
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
    starts an update run), and opens Update ParishSoft."""
    from django.urls import reverse

    review = staff_counts(census={})
    menu = chrome(
        names=["census_changes"], stages={"census_changes": {"ready_to_update": 3}}
    )
    staff = review_work.panels(menu, review)
    assert "Ready to update" not in [card.label for card in staff[0].cards]
    admin = review_work.panels(menu | {"admin": True}, review)
    assert cards(admin[0])[2] == (
        "Ready to update",
        reverse("admin:update_parishsoft"),
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


def _sections(*keys):
    """Menu sections as ``build`` returns them, each with one plain entry."""
    return [
        {"key": key, "label": key, "current": False, "items": [{"name": key}]}
        for key in keys
    ]


def test_my_review_work_is_the_first_entry_of_review_work():
    """R39 finding 4: the landing is Review work's first entry, current on
    /admin/ (so its group stays open), not a Home entry above the groups."""
    from parishkit.stewardship.accounts import admin_context

    sections = _sections("mail", "review", "reports")
    assert admin_context._home_in_review(sections, "My review work", True)
    review = sections[1]
    assert review["current"] is True
    assert [item["name"] for item in review["items"]] == ["index", "review"]
    first = review["items"][0]
    assert (first["label"], first["url"], first["current"]) == (
        "My review work",
        "/admin/",
        "page",
    )
    # Elsewhere the entry is a plain link and the group's own state stands.
    sections = _sections("review")
    assert admin_context._home_in_review(sections, "My review work", False)
    assert sections[0]["current"] is False
    assert sections[0]["items"][0]["current"] is None
    # With no Review work group the top entry stays (nothing is placed).
    sections = _sections("reports")
    assert not admin_context._home_in_review(sections, "My review work", True)
    assert sections == _sections("reports")


def test_an_unreadable_count_shows_its_cards_without_numbers(monkeypatch):
    """A failed count is logged and every stream the role may open is shown
    with no number for it, rather than failing the page; the stages the
    menu's own read counts still show its numbers."""
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
    stages = {
        "ministry_report": {"to_review": 2, "to_enter_by_hand": 1},
        "census_changes": {"to_review": 4, "to_enter_by_hand": 1},
    }
    panels = review_work.panels(
        chrome(stages=stages), {"campaign": object(), "counts": counts}
    )
    by_stream = {panel.stream: cards(panel) for panel in panels}
    # To review and Conflict are the menu's; To enter by hand adds the
    # unread additions, so it has no number, as the check counts do not.
    # (Not seen in ParishSoft, a choice only while flagged, is left out.)
    assert [card[3] for card in by_stream["census"]] == [4, 0, None, None]
    assert [card[3] for card in by_stream["ministry"]] == [2, 1]
    assert [card[3] for card in by_stream["financial"]] == [None] * 4
    # Additional information's are all the menu's, which was not read here.
    assert [card[3] for card in by_stream["additional_information"]] == [None, None]


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
    # The placed counts' refresh checks are read once, here (``settle``),
    # and kept on the request.
    pending = {Stream.CENSUS: {"to_enter_by_hand": 1}, review_work.CHECKS: {}}
    request = SimpleNamespace(_stewardship_review_counts=pending)
    assert review_work.with_counts(request, home)["counts"] == {
        Stream.CENSUS: {"to_enter_by_hand": 1},
        "leader": None,
    }
    assert request._stewardship_review_counts == {
        Stream.CENSUS: {"to_enter_by_hand": 1}
    }
    # The menu statement's parameters never collide with its own flags, and
    # only the streams with a part in COUNTS have one.
    assert review_work.parameters(flags) == {
        "review_census": True,
        "review_financial": False,
        "review_roster": False,
    }
