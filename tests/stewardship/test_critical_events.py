"""Plain-language grouping for the critical-events banner."""

from datetime import UTC, datetime
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
    assert label(Event.SOURCE_INVALID.value) == "ParishSoft refresh failed"
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
        {
            "event": Event.SOURCE_INVALID.value,
            "label": "ParishSoft refresh failed",
            "count": 3,
            "ended_at": None,
            "tile": "source",
            "tile_name": "ParishSoft refresh",
        },
        {
            "event": Event.TASK_FAILED.value,
            "label": "Background task failed",
            "count": 1,
            "ended_at": None,
            "tile": None,
            "tile_name": None,
        },
        {
            "event": Event.MAIL_PROVIDER_FAILED.value,
            "label": "Email sending failed",
            "count": 1,
            "ended_at": None,
            "tile": None,
            "tile_name": None,
        },
    ]


def test_summary_says_when_a_problem_ended():
    """A group whose incidents all resolved carries its end time (#633)."""
    ended = datetime(2026, 10, 6, 10, 30, 32, tzinfo=UTC)
    counts = {Event.DUE_WORK_LAG.value: 1, Event.TASK_FAILED.value: 2}
    assert summary(counts, {Event.DUE_WORK_LAG.value: ended}) == [
        {
            "event": Event.TASK_FAILED.value,
            "label": "Background task failed",
            "count": 2,
            "ended_at": None,
            "tile": None,
            "tile_name": None,
        },
        {
            "event": Event.DUE_WORK_LAG.value,
            "label": "Scheduled work is running late",
            "count": 1,
            "ended_at": ended,
            "tile": None,
            "tile_name": None,
        },
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
    # Both standing problem banners lead to System health (ADM-13).
    assert 'href="/admin/system/health/"' in html
    assert 'href="/admin/system/health/#health-debug"' in html


def test_every_mapped_banner_event_is_critical_and_names_a_registry_kind():
    """The registry's banner mapping (#1214) uses real events and kinds, and
    leaves out the events with no problem kind, ``web_unhealthy`` too."""
    from parishkit.stewardship.audit.critical_events import LABELS
    from parishkit.stewardship.system_health import (
        BANNER_PROBLEMS,
        REGISTRY,
        UNLINKED_EVENTS,
        banner_events,
    )

    assert set(BANNER_PROBLEMS) <= set(LABELS)
    assert set(BANNER_PROBLEMS.values()) <= set(REGISTRY)
    assert set(BANNER_PROBLEMS) >= UNLINKED_EVENTS
    for event in (
        Event.SOURCE_HELD.value,
        Event.TASK_FAILED.value,
        Event.FACT_DRIFT.value,
        Event.WEB_UNHEALTHY.value,
    ):
        assert event not in BANNER_PROBLEMS
    assert banner_events("source_failing") == (
        "source_provider_failed",
        "source_credential_failed",
    )
    assert banner_events("source_destructive_change") == ("source_destructive_change",)


def test_mapped_events_share_the_tiles_words():
    """The two banner labels that change take the tiles' shared words."""
    from parishkit.stewardship.system_health import Problem

    for event, kind in (
        (Event.SOURCE_DESTRUCTIVE_CHANGE.value, "source_destructive_change"),
        (Event.SOURCE_TENANT_MISMATCH.value, "source_tenant_mismatch"),
        (Event.SOURCE_INVALID.value, "source_failed"),
        (Event.SOURCE_PROVIDER_FAILED.value, "source_failing"),
    ):
        assert label(event) == Problem(kind).summary, event


@pytest.mark.parametrize(
    ("event", "ended", "tile"),
    [
        (Event.SOURCE_DESTRUCTIVE_CHANGE.value, None, "source"),
        (Event.SOURCE_CREDENTIAL_FAILED.value, None, "source"),
        (Event.RETRY_REFUSAL_UNRECORDED.value, None, "mail"),
        # Ended: the group reads "ended" and links no tile.
        (Event.SOURCE_INVALID.value, datetime(2026, 10, 6, tzinfo=UTC), None),
        # No halt identity yet (ADM-13 PR 4), and an unmapped event.
        (Event.MAIL_PROVIDER_FAILED.value, None, None),
        (Event.TASK_FAILED.value, None, None),
    ],
)
def test_a_banner_group_links_its_current_problems_tile(event, ended, tile):
    """A mapped group links the problem's tile while it has not ended, ahead
    of the System logs button; others keep only the log link."""
    [group] = summary({event: 1}, {event: ended} if ended else {})
    assert group["tile"] == tile
    html = render_to_string(
        "stewardship/admin-banners.html",
        {
            "suppress_admin_session_chrome": True,
            "admin_chrome": {
                "admin": True,
                "critical_count": 1,
                "critical_limit": 50,
                "critical_shown": "shown",
                "critical_events": [group],
            },
        },
    )
    item = html.split("<li>", 1)[1].split("</li>", 1)[0]
    if tile is None:
        assert "<a " not in item
    else:
        assert item.count("<a ") == 1
        link = f'href="/admin/system/health/#health-{tile}">See {group["tile_name"]}'
        assert link in item
        assert html.index(link) < html.index("View these events in System logs")


def test_the_banners_tile_is_the_tile_homes_line_links():
    """``banner_tile`` reads the registry's tile; Home's line reads
    ``Problem.tile``. They agree because no mapped kind is split."""
    from parishkit.stewardship.system_health import (
        BANNER_PROBLEMS,
        PROCESS_KINDS,
        REGISTRY,
        Problem,
    )

    for kind in BANNER_PROBLEMS.values():
        assert kind not in PROCESS_KINDS, kind
        assert Problem(kind).tile == REGISTRY[kind][0], kind
