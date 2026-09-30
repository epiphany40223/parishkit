"""Plain-language grouping for the critical-events banner."""

from uuid import UUID

import pytest
from django.core import signing
from django.template.loader import render_to_string

from parishkit.stewardship.audit.critical_events import (
    ACKNOWLEDGE_LIMIT,
    SALT,
    label,
    shown,
    sign,
    summary,
)
from parishkit.stewardship.observability import Event

IDS = [UUID(int=1), UUID(int=2)]


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


def test_signed_ids_round_trip():
    """The form carries exactly the ids the banner counted, in order."""
    assert shown(sign(IDS)) == IDS
    assert shown(sign([UUID(int=n) for n in range(ACKNOWLEDGE_LIMIT)]))


# Each forged token is built inside the test, once settings are loaded.
FORGED = {
    "missing": lambda: None,
    "empty": lambda: "",
    "garbage": lambda: "not-a-token",
    "other-salt": lambda: signing.dumps([IDS[0].hex], salt="other"),
    "empty-list": lambda: signing.dumps([], salt=SALT),
    "not-a-list": lambda: signing.dumps({"id": IDS[0].hex}, salt=SALT),
    "not-an-id": lambda: signing.dumps(["not-an-id"], salt=SALT),
    "over-limit": lambda: signing.dumps(
        [UUID(int=n).hex for n in range(ACKNOWLEDGE_LIMIT + 1)], salt=SALT
    ),
}


@pytest.mark.parametrize("forged", sorted(FORGED))
def test_shown_refuses_anything_but_a_genuine_list(forged):
    """Every forged, altered or malformed list is a bad signature."""
    with pytest.raises(signing.BadSignature):
        shown(FORGED[forged]())


def test_an_edited_token_is_refused():
    """Changing the signed payload by one character breaks the signature."""
    token = sign(IDS)
    edited = token[:1] + ("A" if token[1] != "A" else "B") + token[2:]
    with pytest.raises(signing.BadSignature):
        shown(edited)


def test_standing_banners_are_labelled_not_announced_on_every_page():
    """role="alert" would be read out again on every Admin page load (#391 L8)."""
    html = render_to_string(
        "stewardship/admin-banners.html",
        {
            "suppress_admin_session_chrome": True,
            "admin_chrome": {
                "admin": True,
                "debug_in_production": True,
                "critical_count": 1,
                "critical_limit": 50,
                "critical_shown": "shown",
                "critical_events": [{"label": "A backup failed", "count": 1}],
            },
        },
    )
    assert "A backup failed" in html and "Debug logging is on" in html
    assert 'role="alert"' not in html
    for name in ("critical-events-title", "debug-in-production-title"):
        assert f'aria-labelledby="{name}"' in html and f'id="{name}"' in html
