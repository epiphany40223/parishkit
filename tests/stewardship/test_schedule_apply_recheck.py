"""Apply re-checks a reviewed send time against the database clock (#1464).

No database: the clock (``admin_editing.database_now``) is replaced, so
these tests pin what the signed review carries (``reviewed_sends``) and what
the confirmation refuses (``refuse_passed_sends``). The page and command
line, through real PostgreSQL, are in
``database/test_schedule_apply_recheck_postgresql.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core import signing

from parishkit.stewardship.accounts import admin_editing
from parishkit.stewardship.accounts.schedule_changes import reviewed_sends
from parishkit.stewardship.storage import StaleRecordError

NEW_YORK = "America/New_York"
# 8:00 AM in New York on October 25, 2054.
NOW = datetime(2054, 10, 25, 12, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    """The database clock, pinned to ``NOW``; set ``clock.now`` to move it."""
    pinned = SimpleNamespace(now=NOW)
    monkeypatch.setattr(admin_editing, "database_now", lambda: pinned.now)
    return pinned


def reminder(time, day="2054-10-25"):
    """A dated reminder's campaign-local values."""
    return {"kind": "reminder", "date": day, "time": time, "weekday": None}


def row(operation, values, identifier=None):
    """One schedule row of a change, as ``build_preview`` lists them."""
    return {
        "operation": operation,
        "id": identifier or str(uuid4()),
        **({"values": values} if values is not None else {}),
    }


def test_only_changed_send_times_still_ahead_are_carried(clock):
    """An add or a moved schedule ahead of the clock is carried; one already
    past (an Edit into the past, a missed reminder added again), one left as
    it was, a delete and a digest are not."""
    kept, moved = str(uuid4()), str(uuid4())
    prior = {kept: reminder("09:00:00"), moved: reminder("09:00:00")}
    changes = [
        row("add", reminder("08:05:00")),
        row("add", reminder("07:00:00")),
        row("update", reminder("09:00:00") | {"template_version": "x"}, kept),
        row("update", reminder("08:30:00"), moved),
        row("remove", None),
        row("add", {"kind": "daily_digest", "date": None, "time": "08:10:00"}),
    ]
    assert reviewed_sends(changes, prior, NEW_YORK, NEW_YORK) == [
        "2054-10-25T12:05:00+00:00",
        "2054-10-25T12:30:00+00:00",
    ]


def test_a_new_zone_moves_an_unchanged_wall_clock_time(clock):
    """The same wall-clock time in a new campaign zone is a new moment."""
    kept = str(uuid4())
    prior = {kept: reminder("09:00:00")}
    changes = [row("update", reminder("09:00:00"), kept)]
    assert reviewed_sends(changes, prior, NEW_YORK, "America/Chicago") == [
        "2054-10-25T14:00:00+00:00"
    ]
    assert reviewed_sends(changes, prior, NEW_YORK, NEW_YORK) == []


def test_a_passed_send_time_refuses_and_a_later_one_does_not(clock):
    """At or before the clock refuses, as a stale preview with its own
    reason and a way back; still ahead, or none, admits."""
    due = ["2054-10-25T12:05:00+00:00", "2054-10-25T13:00:00+00:00"]
    admin_editing.refuse_passed_sends(due, "/admin/campaign/schedules/")
    admin_editing.refuse_passed_sends(None, None)
    admin_editing.refuse_passed_sends([], None)
    clock.now = NOW + timedelta(minutes=5)
    with pytest.raises(StaleRecordError) as caught:
        admin_editing.refuse_passed_sends(due, "/admin/campaign/schedules/")
    assert caught.value.reason == "send_time_passed"
    refusal = caught.value.refusal.as_dict()
    assert refusal == {
        "message": (
            "A send time in this change has passed since it was reviewed, so "
            "that email would send at once."
        ),
        "fix": "Nothing was changed. Choose a later time, then review the changes.",
        "link": {
            "url": "/admin/campaign/schedules/",
            "label": "Review the changes again",
        },
    }


def test_the_signed_review_carries_its_send_times_only_when_it_has_some():
    """``due`` is signed with the intent; a preview without any has no key,
    so every other editor's preview is unchanged."""
    actor = SimpleNamespace(identity=uuid4())
    configuration = SimpleNamespace(
        active_configuration=SimpleNamespace(digest="a" * 64)
    )
    signed = [
        signing.loads(
            admin_editing.sign_preview(
                actor=actor, configuration=configuration, patch=[], salt="s", **due
            ),
            salt="s",
        )
        for due in ({}, {"due": ["2054-10-25T12:05:00+00:00"]})
    ]
    assert "due" not in signed[0]
    assert signed[1]["due"] == ["2054-10-25T12:05:00+00:00"]
