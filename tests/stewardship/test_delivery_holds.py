"""Outgoing mail's sending limit note, without a database (#382 M3a)."""

from datetime import UTC, datetime, timedelta

from django.template.loader import render_to_string

from parishkit.stewardship.jobs.delivery_reads import _long_retry
from parishkit.stewardship.jobs.family_mail_dispatch import (
    LIMIT_RETRY_SECONDS,
    RECIPIENT_RETRY_SECONDS,
    retry_delay,
)

NOW = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)


def note(**holds):
    """Render the note for ``holds`` (None draws nothing)."""
    values = dict(
        daily_limit=False,
        gmail_held=False,
        gmail_until=None,
        throttled=0,
        throttled_due=None,
    )
    return render_to_string(
        "stewardship/delivery-holds.html",
        {"holds": values | holds if holds else None},
    )


def test_a_long_retry_is_longer_than_any_ordinary_one():
    """Only a limit or throttle backoff waits longer than the threshold."""
    threshold = _long_retry().total_seconds()
    assert all(retry_delay(attempt) < threshold for attempt in range(1, 20))
    assert all(seconds > threshold for seconds in RECIPIENT_RETRY_SECONDS)
    assert all(seconds > threshold for seconds in LIMIT_RETRY_SECONDS.values())


def test_no_note_while_nothing_is_held():
    """The ordinary page has no note at all."""
    assert "delivery-holds" not in note()


def test_the_note_names_each_hold_and_links_system_health():
    """Each reason in plain words; times are localized in the browser."""
    until = NOW + timedelta(minutes=40)
    due = NOW + timedelta(minutes=15)
    html = note(
        daily_limit=True,
        gmail_held=True,
        gmail_until=until,
        throttled=3,
        throttled_due=due,
    )
    assert "daily limit for Family email" in html
    assert "Gmail's own sending limit" in html
    assert f'<time datetime="{until.isoformat()}" data-local-instant>' in html
    assert "3 Family emails are waiting longer than usual to retry" in html
    assert f'<time datetime="{due.isoformat()}" data-local-instant>' in html
    assert 'href="/admin/system/health/"' in html
    assert "No action is needed" in html
    # Internal state names never reach the words.
    assert "gmail_held" not in html and "daily_limit" not in html


def test_one_throttled_email_and_a_hold_without_a_time():
    """Singular wording; a Gmail hold with no reported end names no time."""
    html = note(gmail_held=True, throttled=1)
    assert "1 Family email is waiting longer than usual" in html
    assert "<time" not in html
    assert "daily limit for Family email" not in html
