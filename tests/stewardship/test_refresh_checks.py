"""The refresh checks' pure parts (#1689): run lengths and what is owed.

The writes and reads themselves run against PostgreSQL in
``database/test_refresh_checks_postgresql.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from parishkit.stewardship.responses.comparison import ValueKind
from parishkit.stewardship.responses.merge import KnownValue
from parishkit.stewardship.responses.refresh_checks import (
    CHECKED_FIELDS,
    CheckState,
    _census_same,
    _owed,
    _summaries,
)

START = datetime(2026, 10, 30, tzinfo=UTC)


def at(hours):
    """A check time ``hours`` after the Not seen start."""
    return START + timedelta(hours=hours)


def test_unseen_counts_full_unchanged_checks_after_the_start_and_mark():
    """Only full refreshes after the start and the item's mark count, and
    any other outcome restarts the run."""
    start = SimpleNamespace(started_at=START, not_seen_after=2)
    checks = [
        ("a", at(-2), "unchanged", True, None),
        ("a", at(1), "unchanged", False, None),
        ("a", at(2), "unchanged", True, None),
        ("b", at(1), "unchanged", True, None),
        ("b", at(2), "entered_incorrectly", True, {"value": "x"}),
        ("b", at(3), "unchanged", True, None),
        ("c", at(1), "unchanged", True, None),
        ("c", at(3), "unchanged", True, None),
    ]
    states = _summaries(checks, start, {"c": at(2)})
    assert states["a"] == CheckState("unchanged", None, 1, 2)
    assert states["b"] == CheckState("unchanged", None, 1, 2)
    assert states["c"].unseen == 1
    assert not states["a"].not_seen(True)
    assert _summaries(checks, None, {})["a"].not_seen_after is None


def test_not_seen_needs_a_mark_the_threshold_and_the_start():
    """The flag is a marked item's, at the threshold, while the start row is
    there."""
    state = CheckState("unchanged", None, 2, 2)
    assert state.not_seen(True) and not state.not_seen(False)
    assert not CheckState("unchanged", None, 2, None).not_seen(True)
    assert not CheckState("confirmed", None, 5, 2).not_seen(True)


def test_a_check_is_owed_only_when_something_changed():
    """A changed outcome, a new wrong value, or a marked item's full refresh
    until Not seen; nothing for a quiet refresh."""
    quiet = CheckState(not_seen_after=2)
    assert not _owed("unchanged", quiet, full=False, marked=True)
    assert not _owed("unchanged", quiet, full=True, marked=False)
    assert _owed("unchanged", quiet, full=True, marked=True)
    assert not _owed(
        "unchanged", CheckState("unchanged", None, 2, 2), full=True, marked=True
    )
    assert _owed("confirmed", quiet, full=False, marked=False)
    wrong = CheckState("entered_incorrectly", {"value": "a"}, 0, 2)
    assert not _owed("entered_incorrectly", wrong, full=True, marked=True)
    assert _owed("entered_incorrectly", wrong, full=True, marked=True, moved=True)


def test_census_comparison_is_canonical_and_availability_aware():
    """Emails compare without case, not as raw text; an unread value equals
    only another unread one."""
    same = _census_same(ValueKind.EMAIL)
    assert same(
        KnownValue(True, "Ann@Example.org"), KnownValue(True, "ann@example.org")
    )
    assert not same(
        KnownValue(True, "ann@example.org"), KnownValue(True, "bo@example.org")
    )
    assert same(KnownValue(False), KnownValue(False))
    assert not same(KnownValue(False), KnownValue(True, None))


def test_checked_fields_are_the_contact_read_fields():
    """Moved and deceased need their own reading (#1738)."""
    assert "prefix" in CHECKED_FIELDS and "email" in CHECKED_FIELDS
    assert not CHECKED_FIELDS & {"moved_household", "deceased_status", "new_member"}
