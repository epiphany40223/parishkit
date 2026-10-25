"""The review wizard's pure rules (#2123; review pipeline spec, "Review wizard")."""

from datetime import UTC, datetime, timedelta

import pytest

from parishkit.stewardship.reports import review_wizard
from parishkit.stewardship.reports.review_run_models import MAX_SUBJECTS
from parishkit.stewardship.reports.review_wizard import (
    Item,
    TooManyPages,
    progress,
    sequence,
    waiting,
)

T0 = datetime(2054, 10, 1, 12, tzinfo=UTC)


def item(item_id, family, name, duid, *, kind="family", key="", at=None, **extra):
    """One matching item; a Member's ``key`` is its DUID or local id."""
    return Item(
        id=item_id,
        family=family,
        kind=kind,
        key=key,
        name=name,
        duid=duid,
        stage="to_review",
        submitted_at=at,
        **extra,
    )


def keys(subjects):
    """Each page as ``(family, kind, key)``."""
    return [(s.family, s.kind, s.key) for s in subjects]


def test_name_order_breaks_ties_by_duid_then_record_id():
    """Last name (A–Z) folds accents and case; equal names keep one order by
    DUID, id."""
    items = [
        item("1", "f-b", "Smith, Ann", 20),
        item("2", "f-a", "smith, ann", 10),
        item("3", "f-c", "Étienne, Paul", 5),
        item("4", "f-d", "Smith, Ann", 10),
        item("5", "f-e", None, 1),
    ]
    found = sequence(items, grouped=False, sort="name")
    assert [s.family for s in found] == ["f-c", "f-a", "f-d", "f-b", "f-e"]
    # The same items in another order give the same sequence.
    assert keys(sequence(items[::-1], grouped=False, sort="name")) == keys(found)


def test_submitted_sorts_use_the_newest_item_and_fall_back_to_names():
    """A subject's time is its newest item's; equal times keep name order."""
    items = [
        item("1", "f-a", "Adams", 1, at=T0),
        item("2", "f-a", "Adams", 1, at=T0 + timedelta(days=3)),
        item("3", "f-b", "Baker", 2, at=T0 + timedelta(days=1)),
        item("4", "f-c", "Clark", 3, at=T0 + timedelta(days=1)),
        item("5", "f-d", "Dunn", 4),
    ]
    oldest = sequence(items, grouped=False, sort="oldest")
    assert [s.family for s in oldest] == ["f-b", "f-c", "f-a", "f-d"]
    newest = sequence(items, grouped=False, sort="newest")
    assert [s.family for s in newest] == ["f-a", "f-b", "f-c", "f-d"]
    # A Family stream's page keeps every matched item of its Family.
    assert oldest[2].items == ["1", "2"]


def test_grouped_streams_put_the_family_page_first_then_members_in_tab_order():
    """Census changes: Family page first, Members by DUID, New Members by name."""
    items = [
        item("m9", "f-a", "Adams", 1, kind="member", key="9", member_name="Zed"),
        item(
            "p1", "f-a", "Adams", 1, kind="proposed_member", key="x", member_name="Bo"
        ),
        item("h1", "f-a", "Adams", 1),
        item("m2", "f-a", "Adams", 1, kind="member", key="10", member_name="Al"),
        item(
            "p2", "f-a", "Adams", 1, kind="proposed_member", key="y", member_name="al"
        ),
        item("o1", "f-b", "Baker", 2, kind="member", key="3", member_name="Cy"),
    ]
    found = sequence(items, grouped=True, sort="name")
    assert keys(found) == [
        ("f-a", "family", ""),
        ("f-a", "member", "9"),
        ("f-a", "member", "10"),
        ("f-a", "proposed_member", "y"),
        ("f-a", "proposed_member", "x"),
        # A Family with Member items only has no Family page.
        ("f-b", "member", "3"),
    ]
    assert [s.stored()["items"] for s in found][:2] == [["h1"], ["m9"]]


def test_grouped_families_sort_by_their_newest_item():
    """In a grouped stream the sort orders Families, their pages adjacent."""
    items = [
        item("a1", "f-a", "Adams", 1, kind="member", key="1", at=T0),
        item("b1", "f-b", "Baker", 2, at=T0 + timedelta(days=1)),
        item("a2", "f-a", "Adams", 1, at=T0 + timedelta(days=2)),
    ]
    found = sequence(items, grouped=True, sort="oldest")
    assert keys(found) == [
        ("f-b", "family", ""),
        ("f-a", "family", ""),
        ("f-a", "member", "1"),
    ]


def test_a_member_stream_has_one_page_per_member():
    """Ministry requests: one Member per page, each with its requests."""
    items = [
        item("r1", "f-a", "Lee, Kim", 7, kind="member", key="7"),
        item("r2", "f-a", "Lee, Kim", 7, kind="member", key="7"),
        item("r3", "f-a", "Lee, Al", 8, kind="member", key="8"),
    ]
    found = sequence(items, grouped=False, sort="name")
    assert keys(found) == [("f-a", "member", "8"), ("f-a", "member", "7")]
    assert found[1].items == ["r1", "r2"]


def test_more_than_the_limit_is_refused(monkeypatch):
    """A selection beyond the run's page limit raises; nothing is cut short."""
    monkeypatch.setattr(review_wizard, "MAX_SUBJECTS", 3)
    items = [item(str(n), f"f-{n}", f"Name {n}", n) for n in range(4)]
    with pytest.raises(TooManyPages):
        sequence(items, grouped=False, sort="name")
    assert len(sequence(items[:3], grouped=False, sort="name")) == 3
    assert MAX_SUBJECTS == 5000


def test_progress_words():
    """Family, Member, and a grouped stream's Family place alone (R52 Part W)."""
    flat = [{"family": f, "kind": "family", "key": ""} for f in "abc"]
    assert progress(flat, 2, grouped=False, member_stream=False) == "Family 2 of 3"
    assert progress(flat, 3, grouped=False, member_stream=True) == "Member 3 of 3"
    grouped = [
        {"family": "a", "kind": "family", "key": ""},
        {"family": "a", "kind": "member", "key": "1"},
        {"family": "a", "kind": "member", "key": "2"},
        {"family": "b", "kind": "member", "key": "3"},
    ]
    words = [progress(grouped, n, grouped=True, member_stream=False) for n in (1, 3, 4)]
    assert words == ["Family 1 of 2", "Family 1 of 2", "Family 2 of 2"]


def test_waiting_is_every_stage_a_person_still_acts_on():
    """Basic's set; a Ministry leader's is To review alone."""
    for stage in ("to_review", "conflict", "to_enter_by_hand", "to_handle"):
        assert waiting(stage)
    assert waiting("entered_incorrectly")
    for stage in ("ready_to_update", "updating", "ignored", "confirmed", "handled"):
        assert not waiting(stage)
    assert waiting("to_review", leader=True)
    assert not waiting("to_enter_by_hand", leader=True)


def test_the_card_follows_the_menu_entry():
    """No entry, no card; greyed with the menu's reason; never a Continue."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.review_work import wizard_cards

    review = {"campaign": object()}
    assert wizard_cards({"entries": {}}, review) == []
    item = SimpleNamespace(label="Review one at a time", url="/start/", reason=None)
    chrome = {"entries": {"review_wizard": item}}
    [card] = wizard_cards(chrome, review)
    assert (card.label, card.url) == ("Review one at a time", "/start/")
    greyed = SimpleNamespace(label="Review one at a time", url=None, reason="No")
    [card] = wizard_cards({"entries": {"review_wizard": greyed}}, review)
    assert (card.url, card.reason) == (None, "No")
    assert wizard_cards(chrome, {"campaign": None}) == []


@pytest.mark.parametrize(
    ("roles", "says"),
    [
        (("ministry_leader",), "You don't lead any Ministry"),
        (("staff",), None),
    ],
)
def test_start_with_nothing_offered_is_the_plain_refusal(monkeypatch, roles, says):
    """No offered stream: a Ministry leader gets the #939 sentence (#2165).

    Any other reader keeps the generic denial. Both are 403s.
    """
    from types import SimpleNamespace

    from django.test import RequestFactory

    from parishkit.stewardship.reports import review_wizard_views as views

    principal = SimpleNamespace(roles=roles, identity=1)
    monkeypatch.setattr(views, "runtime", lambda: SimpleNamespace(store=None))
    monkeypatch.setattr(views, "_reader", lambda *args, **kwargs: principal)
    monkeypatch.setattr(views, "admit_report_read", lambda campaign_id: None)
    monkeypatch.setattr(views, "_offered", lambda principal: [])
    response = views.start(RequestFactory().get("/admin/reports/review/"), 1)
    body = response.content.decode()
    assert response.status_code == 403
    assert ("You don't lead any Ministry" in body.replace("&#x27;", "'")) is bool(says)
    assert "Try signing in again" not in body or not says


def test_the_header_shows_an_envelope_only_when_set_and_not_zero():
    """ "envelope e" is in the header only for a set, non-zero number."""
    from parishkit.stewardship.reports.review_wizard_views import envelope_shown
    from parishkit.stewardship.source.snapshot_names import FamilyFacts

    assert envelope_shown(FamilyFacts("A", 312, "")) == 312
    assert envelope_shown(FamilyFacts("A", 0, "")) is None
    assert envelope_shown(FamilyFacts("A", None, "")) is None
    assert envelope_shown(None) is None


def test_the_wizard_offers_no_history():
    """No stream's Stage list offers History (the tables' filters still do),
    and an Every stage read keeps no History item, so a History item is
    never a page (R52 Part W)."""
    from uuid import UUID

    from parishkit.stewardship.accounts.policy import Principal
    from parishkit.stewardship.reports import review_streams
    from parishkit.stewardship.reports.review_wizard import Item

    staff = Principal(UUID(int=1), frozenset({"staff"}))
    for stream in review_streams.STREAMS:
        assert "history" not in dict(stream.stages(staff)), stream.key
    items = [
        Item(str(n), "f", "family", "", "A", 1, stage)
        for n, stage in enumerate(("history", "to_review", "confirmed"))
    ]
    kept = review_streams._keep(items, review_streams.EVERY)
    assert [item.stage for item in kept] == ["to_review", "confirmed"]
    assert [i.stage for i in review_streams._keep(items, "waiting")] == ["to_review"]
