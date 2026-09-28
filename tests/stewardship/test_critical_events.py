"""Plain-language grouping for the critical-events banner."""

from parishkit.stewardship.audit.critical_events import label, summary
from parishkit.stewardship.observability import Event


def test_known_events_read_as_plain_language():
    """Staff see what failed, not internal identifiers."""
    assert label(Event.SOURCE_INVALID.value) == "ParishSoft data refresh failed"
    assert label(Event.MAIL_PROVIDER_FAILED.value) == "Email sending failed"


def test_unknown_events_fall_back_to_words():
    """An event without a label still reads as words rather than a code."""
    assert label("some_new_event") == "Some new event"


def test_summary_orders_by_frequency_then_name():
    """The most frequent problem is listed first; ties sort by label."""
    counts = {
        Event.TASK_FAILED.value: 1,
        Event.SOURCE_INVALID.value: 3,
        Event.MAIL_PROVIDER_FAILED.value: 1,
    }
    assert summary(counts) == [
        {"label": "ParishSoft data refresh failed", "count": 3},
        {"label": "Background task failed", "count": 1},
        {"label": "Email sending failed", "count": 1},
    ]
