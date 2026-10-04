"""Closed Ministry follow-up change grammar, validated without a database."""

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.base import UpdateError
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.reports.ministry_followup import FollowupQuery
from parishkit.stewardship.reports.ministry_followup_views import (
    QUEUE_STATE,
    QUEUE_STATE_SECONDS,
    _restore_queue,
    assignment_values,
)
from parishkit.stewardship.workflows.followup import (
    MAX_CONTACT_NOTES,
    MAX_NOTES,
    WorkflowChange,
    assignment_state,
)

WHO, WHEN = uuid4(), datetime(2026, 9, 20, 12, tzinfo=UTC)
BASE = dict(assignee_id=None, state="in_progress", outcome=None, notes="")


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"state": "new"},
        {"state": "assigned", "assignee_id": WHO},
        {"assignee_id": WHO, "notes": "n" * MAX_NOTES},
        {"state": "resolved", "outcome": "joined"},
        {"state": "resolved", "outcome": "other", "notes": "Moved parishes"},
        {"state": "closed_no_response", "outcome": "no_response"},
        {"contact_channel": "phone", "contact_at": WHEN},
        {
            "contact_channel": "in_person",
            "contact_at": WHEN,
            "contact_notes": "c" * MAX_CONTACT_NOTES,
        },
    ],
)
def test_complete_workflow_is_accepted(values):
    """Every Staff-owned state, outcome pairing and contact shape round-trips."""
    change = WorkflowChange(**(BASE | values))
    assert all(getattr(change, key) == value for key, value in values.items())


@pytest.mark.parametrize(
    "values",
    [
        # Family- and worker-owned states are never a Staff edit.
        {"state": "cancelled"},
        {"state": "superseded"},
        {"state": "unknown"},
        # An outcome belongs to exactly its closed state.
        {"outcome": "joined"},
        {"state": "resolved"},
        {"state": "resolved", "outcome": "no_response"},
        {"state": "closed_no_response", "outcome": "declined"},
        {"state": "resolved", "outcome": "invented"},
        {"state": "resolved", "outcome": "other"},
        {"state": "resolved", "outcome": "other", "notes": "   "},
        # Assignment follows the state.
        {"state": "new", "assignee_id": WHO},
        {"state": "assigned"},
        {"assignee_id": str(WHO)},
        # A contact attempt is complete, zoned, bounded and from a closed set.
        {"contact_channel": "phone"},
        {"contact_at": WHEN},
        {"contact_notes": "Notes without an attempt"},
        {"contact_channel": "fax", "contact_at": WHEN},
        {"contact_channel": "phone", "contact_at": WHEN.replace(tzinfo=None)},
        {"contact_channel": "phone", "contact_at": "2026-09-20"},
        {
            "contact_channel": "phone",
            "contact_at": WHEN,
            "contact_notes": "c" * (MAX_CONTACT_NOTES + 1),
        },
        {"notes": "n" * (MAX_NOTES + 1)},
        {"notes": "nul\x00byte"},
        {"notes": None},
    ],
)
def test_incomplete_or_foreign_workflow_is_rejected(values):
    """A malformed form is a client error before any lock or query."""
    with pytest.raises(ValueError):
        WorkflowChange(**(BASE | values))


@pytest.mark.parametrize(
    ("state", "assignee", "expected"),
    [
        ("new", WHO, "assigned"),
        ("assigned", None, "new"),
        ("new", None, "new"),
        ("assigned", WHO, "assigned"),
        ("in_progress", None, "in_progress"),
        ("in_progress", WHO, "in_progress"),
        ("resolved", WHO, "resolved"),
        ("cancelled", WHO, "cancelled"),
    ],
)
def test_new_and_assigned_follow_the_assignee(state, assignee, expected):
    """Only the two states that mean "has an assignee" are derived from it."""
    assert assignment_state(state, assignee) == expected


def bulk_form(**extra):
    """A bulk-assignment body: one selected request plus any extra fields."""
    form = {
        "request_key": [str(uuid4())],
        "ministry": ["9"],
        "assignee": [""],
        "selected": [f"{uuid4()}:3"],
    }
    return MultiValueDict(form | {key: [value] for key, value in extra.items()})


def test_bulk_assignment_carries_the_queue_view():
    """`queue-` fields name the view to return to (#518); without them, none."""
    _, ministry, queue = assignment_values(bulk_form())
    assert ministry == 9 and queue is None
    _, _, queue = assignment_values(
        bulk_form(
            **{"queue-search": "Private", "queue-ministry": "9", "queue-page": "2"}
        )
    )
    assert queue == FollowupQuery(search="Private", ministry="9", page=2)


@pytest.mark.parametrize(
    "extra",
    [
        {"queue-sort": "random"},
        {"queue-unknown": "x"},
        {"queue-size": "all"},
        {"queue-search": "x" * 201},
        {"unexpected": "field"},
    ],
)
def test_bulk_assignment_refuses_a_malformed_view(extra):
    """A crafted view field is refused before any assignment is attempted."""
    with pytest.raises(ValueError):
        assignment_values(bulk_form(**extra))


def test_bulk_assignment_refuses_a_repeated_view_field():
    """Each view field is single-valued, like every other filter."""
    form = bulk_form()
    form.setlist("queue-state", ["any", "new"])
    with pytest.raises(ValueError):
        assignment_values(form)


class Session(dict):
    """A session stand-in that records saves and can fail like an ended one."""

    def __init__(self, values, *, ended=False):
        super().__init__(values)
        self.saves, self.ended, self.modified = 0, ended, False

    def save(self):
        """Count the explicit save; an ended session raises UpdateError."""
        if self.ended:
            raise UpdateError
        self.saves += 1


CAMPAIGN = uuid4()
QUERY = {"search": "Private", "ministry": "9", "page": "2", "size": "25"}


def entry(**changes):
    """A fresh one-time queue entry for CAMPAIGN, as assign stores it."""
    return {"campaign": str(CAMPAIGN), "at": time.time(), "query": QUERY} | changes


def restore(value, method="GET", **session):
    """Run _restore_queue on a request whose session holds ``value``."""
    request = SimpleNamespace(
        method=method, session=Session({QUEUE_STATE: value}, **session)
    )
    return _restore_queue(request, CAMPAIGN), request.session


def test_queue_view_is_restored_once_for_its_campaign():
    """The redirect GET gets the view; the entry is gone and saved at once."""
    restored, session = restore(entry())
    assert restored == QUERY and QUEUE_STATE not in session
    assert session.saves == 1 and not session.modified
    empty = SimpleNamespace(method="GET", session=Session({}))
    assert _restore_queue(empty, CAMPAIGN) == {} and empty.session.saves == 0


@pytest.mark.parametrize(
    "value",
    [
        entry(campaign=str(uuid4())),
        entry(at=time.time() - QUEUE_STATE_SECONDS - 5),
        entry(at="soon"),
        entry(query="search=Private"),
        ["not", "a", "dict"],
        "text",
    ],
)
def test_foreign_stale_or_malformed_queue_view_is_discarded(value):
    """Another campaign's, an expired or a malformed entry is dropped unused."""
    restored, session = restore(value)
    assert restored == {} and QUEUE_STATE not in session and session.saves == 1


def test_queue_post_discards_the_queue_view():
    """A filter POST brings its own filters; the entry is dropped, not used."""
    restored, session = restore(entry(), method="POST")
    assert restored == {} and QUEUE_STATE not in session


def test_invalid_restored_view_is_refused_by_the_parser():
    """A tampered view reaches FollowupQuery.parse, which refuses it (400)."""
    restored, _ = restore(entry(query={"sort": "random"}))
    with pytest.raises(ValueError):
        FollowupQuery.parse(restored)


def test_ended_session_is_a_denial():
    """A session that ended before the removal saved is denied, not a 500."""
    with pytest.raises(PermissionError):
        restore(entry(), ended=True)
