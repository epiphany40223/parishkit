"""The Ministry requests counts' links to the follow-up queue (#1133 step 1).

Each count that is not zero links to the queue filtered to its Ministry with
the queue's own closed Request and Status values, and only for a Ministry the
reader may follow up. The PostgreSQL tests follow the links end to end.
"""

from uuid import uuid4

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.ministry_views import queue_links

QUEUE = "/admin/reports/ministries/follow-up/"


def row(duid, joining=0, leaving=0, unresolved=0):
    """One summary row as the report's selection returns it."""
    return {
        "duid": duid,
        "joining": joining,
        "leaving": leaving,
        "unresolved": unresolved,
    }


def test_each_count_links_the_queue_with_its_own_filters():
    """Joining and Leaving choose Any status; Unresolved is the queue default."""
    staff = Principal(uuid4(), frozenset({"staff"}))
    assert queue_links(staff, row(9, 2, 1, 3)) == {
        "joining": f"{QUEUE}?ministry=9&action=join&state=any",
        "leaving": f"{QUEUE}?ministry=9&action=leave&state=any",
        "unresolved": f"{QUEUE}?ministry=9",
    }


def test_a_zero_count_has_no_link():
    """A count of zero is plain text: there is nothing for the queue to show."""
    staff = Principal(uuid4(), frozenset({"staff"}))
    assert queue_links(staff, row(9, joining=1)) == {
        "joining": f"{QUEUE}?ministry=9&action=join&state=any"
    }
    assert queue_links(staff, row(9)) == {}


def test_a_leader_gets_links_only_for_the_ministries_they_lead():
    """The same scope check the queue makes on the link."""
    leader = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
    assert set(queue_links(leader, row(9, 1, 1, 1))) == {
        "joining",
        "leaving",
        "unresolved",
    }
    assert queue_links(leader, row(4, 1, 1, 1)) == {}
    assert queue_links(Principal(uuid4()), row(9, 1, 1, 1)) == {}
