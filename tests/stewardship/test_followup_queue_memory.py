"""Remembered queue views and Save and next's helpers (#534), without a database."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.http import QueryDict

from parishkit.stewardship.reports import report_paging as paging


class Session(dict):
    """A session store stand-in that counts saves (or fails them)."""

    def __init__(self, fail=False):
        super().__init__()
        self.saves, self.fail, self.modified = 0, fail, False

    def save(self):
        """Count a save, or fail as a session ended elsewhere does."""
        if self.fail:
            raise UpdateError
        self.saves += 1


def request(session=None):
    """A request carrying ``session`` (a fresh one by default)."""
    return SimpleNamespace(session=Session() if session is None else session)


def test_remembered_views_keep_their_token_and_only_the_latest_few():
    campaign = uuid4()
    caller = request()
    first = paging.remember_queue(caller, "q", campaign, {"search": "Smith"})
    assert len(first) == 32 and caller.session.saves == 1
    # The session is saved before the guarded response, not by middleware.
    assert caller.session.modified is False
    assert caller.session.stewardship_persisted is True
    # The latest view again: same token, nothing to save.
    assert paging.remember_queue(caller, "q", campaign, {"search": "Smith"}) == first
    assert caller.session.saves == 1
    second = paging.remember_queue(caller, "q", campaign, {"search": "Jones"})
    assert second != first
    # An earlier view keeps its token and becomes the latest.
    assert paging.remember_queue(caller, "q", campaign, {"search": "Smith"}) == first
    assert list(caller.session[paging.QUEUE_VIEWS_KEY])[-1] == first
    assert paging.recall_queue(caller, "q", campaign, first) == {"search": "Smith"}
    # Another queue, campaign or unknown token finds nothing.
    assert paging.recall_queue(caller, "other", campaign, first) is None
    assert paging.recall_queue(caller, "q", uuid4(), first) is None
    assert paging.recall_queue(caller, "q", campaign, "0" * 32) is None
    assert paging.recall_queue(caller, "q", campaign, "") is None
    for number in range(paging.QUEUE_VIEWS_KEPT):
        paging.remember_queue(caller, "q", campaign, {"page": str(number)})
    assert len(caller.session[paging.QUEUE_VIEWS_KEY]) == paging.QUEUE_VIEWS_KEPT
    assert paging.recall_queue(caller, "q", campaign, first) is None


def test_no_session_remembers_nothing_and_an_ended_one_is_interrupted():
    assert paging.remember_queue(SimpleNamespace(), "q", uuid4(), {}) == ""
    assert paging.recall_queue(SimpleNamespace(), "q", uuid4(), "a" * 32) is None
    with pytest.raises(SessionInterrupted):
        paging.remember_queue(request(Session(fail=True)), "q", uuid4(), {})


def test_queue_token_accepts_only_the_opaque_form():
    assert paging.queue_token("") == ""
    assert paging.queue_token("a" * 32) == "a" * 32
    for invalid in ("A" * 32, "a" * 31, "Smith", "a" * 32 + "\n"):
        with pytest.raises(ValueError):
            paging.queue_token(invalid)


def rows(*ids, closed=()):
    """Queue rows in order; ``closed`` ids are not open."""
    return [{"id": value, "open": value not in closed} for value in ids]


def scan(ordered, current, size=2):
    """next_open over ``ordered`` rows read ``size`` at a time."""
    reads = []

    def page(number):
        reads.append(number)
        return ordered[(number - 1) * size : number * size]

    return paging.next_open(page, size, current, lambda row: row["open"]), reads


def test_next_open_skips_the_saved_item_and_items_not_open():
    ordered = rows("a", "b", "c", "d", "e", closed={"c", "d"})
    assert scan(ordered, "a") == ("b", [1])
    # Across pages, past items that are not open.
    assert scan(ordered, "b") == ("e", [1, 2, 3])
    # Nothing open follows the last one: Save and next returns to the queue.
    assert scan(ordered, "e") == (None, [1, 2, 3])
    # An item opened from elsewhere leads to the queue's first open item.
    assert scan(ordered, "z")[0] == "a"
    assert scan(rows("a", closed={"a"}), "z")[0] is None
    # A full last page reads one more (empty) page, then stops.
    assert scan(rows("a", "b"), "b") == (None, [1, 2])


def test_pop_navigation_splits_save_and_next_from_the_form():
    following = uuid4()
    form = QueryDict(mutable=True)
    form.update(
        {"notes": "x", "queue": "a" * 32, "then": "next", "next": str(following)}
    )
    assert paging.pop_navigation(form) == ("a" * 32, True, following)
    assert dict(form) == {"notes": ["x"]}
    # Plain Save ignores the next id it carries; an absent field is empty.
    form = QueryDict(f"next={following}", mutable=True)
    assert paging.pop_navigation(form) == ("", False, None)
    form = QueryDict("then=next&next=", mutable=True)
    assert paging.pop_navigation(form) == ("", True, None)
    for invalid in (
        "then=back",
        "then=next&then=next",
        "next=not-a-uuid",
        "queue=Private",
    ):
        with pytest.raises(ValueError):
            paging.pop_navigation(QueryDict(invalid, mutable=True))


def test_with_queue_adds_only_the_token():
    assert paging.with_queue("/q/", "") == "/q/"
    assert paging.with_queue("/q/", "a" * 32) == "/q/?queue=" + "a" * 32
