"""Dates and times typed in the browser's local time zone (#558)."""

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from django.http import QueryDict

from parishkit.stewardship.campaigns.intervals import resolve_local
from parishkit.stewardship.reports.ministry_followup_views import change_values
from parishkit.stewardship.web.dates import (
    UnknownZone,
    browser_day_start,
    browser_instant,
)
from parishkit.stewardship.workflows.followup import FollowupRefusal

from .test_ministry_followup import FORM, form

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
# The shared note naming the browser's zone, hidden until the script knows
# it (components/zone-note.html, #1129).
ZONE_NOTE = (
    '{% include "stewardship/components/zone-note.html" with note_text=zone_note'
)


@pytest.mark.parametrize(
    ("day", "clock", "zone", "expected"),
    [
        ("2026-10-04", "09:15", "UTC", datetime(2026, 10, 4, 9, 15, tzinfo=UTC)),
        # Daylight time (UTC-7) and standard time (UTC-8).
        (
            "2026-10-04",
            "09:15",
            "America/Los_Angeles",
            datetime(2026, 10, 4, 16, 15, tzinfo=UTC),
        ),
        (
            "2026-12-04",
            "09:15:30",
            "America/Los_Angeles",
            datetime(2026, 12, 4, 17, 15, 30, tzinfo=UTC),
        ),
        # A local evening can be the next UTC day; a quarter-hour offset.
        (
            "2026-10-04",
            "20:00",
            "America/New_York",
            datetime(2026, 10, 5, 0, tzinfo=UTC),
        ),
        (
            "2026-10-04",
            "06:00",
            "Asia/Kathmandu",
            datetime(2026, 10, 4, 0, 15, tzinfo=UTC),
        ),
        # The representable ends, where the offset keeps them in range.
        (
            "0001-01-01",
            "00:00",
            "UTC",
            datetime(1, 1, 1, tzinfo=UTC),
        ),
        (
            "9999-12-31",
            "23:59:59",
            "Asia/Tokyo",
            datetime(9999, 12, 31, 14, 59, 59, tzinfo=UTC),
        ),
        # An alias some browsers still report.
        ("2026-10-04", "05:30", "Asia/Calcutta", datetime(2026, 10, 4, 0, tzinfo=UTC)),
        # Fall back: 1:30 AM happens twice; the first (daylight) one is used.
        (
            "2026-11-01",
            "01:30",
            "America/Los_Angeles",
            datetime(2026, 11, 1, 8, 30, tzinfo=UTC),
        ),
        # Spring forward: 2:30 AM never happens; it is the first instant
        # after the gap, 3:00 AM daylight time (ui-conventions#schedules).
        (
            "2026-03-08",
            "02:30",
            "America/Los_Angeles",
            datetime(2026, 3, 8, 10, tzinfo=UTC),
        ),
    ],
)
def test_browser_local_time_converts_to_utc(day, clock, zone, expected):
    """A typed local date and time becomes the same instant in UTC."""
    moment = browser_instant(day, clock, zone)
    assert moment == expected and moment.tzinfo is UTC


# One daylight-saving rule for every local time turned into an instant
# (ui-conventions#schedules, #1129), in cases shared with the page script's
# browser test: New York (March and November), Sydney south of the equator
# (October and April), Lord Howe Island, whose clocks move 30 minutes, and
# Santiago, whose spring-forward gap starts at midnight.
DST_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "dst_cases.json").read_text()
)["cases"]


@pytest.mark.parametrize(
    "case", DST_CASES, ids=lambda case: f"{case['zone']} {case['date']} {case['time']}"
)
def test_typed_times_follow_the_one_daylight_saving_rule(case):
    """A skipped time is the gap's end and a repeated one the first, exactly.

    ``browser_instant`` and the schedulers' ``resolve_local`` give the same
    instant, so a time typed into a page and one a schedule evaluates never
    differ at a clock change.
    """
    moment = browser_instant(case["date"], case["time"], case["zone"])
    assert moment == datetime.fromisoformat(case["utc"]) and moment.tzinfo is UTC
    local = datetime.combine(
        date.fromisoformat(case["date"]), time.fromisoformat(case["time"])
    )
    assert resolve_local(local, case["zone"]) == moment


@pytest.mark.parametrize(
    ("day", "clock", "zone"),
    [
        # The Admin portal requires JavaScript (#565): no zone is no guess.
        ("2026-10-04", "09:15", ""),
        ("2026-10-04", "09:15", "Mars/Olympus_Mons"),
        ("2026-10-04", "09:15", "America"),  # a directory, not a zone
        ("2026-10-04", "09:15", "../../etc/passwd"),
        ("2026-10-04", "09:15", "/usr/share/zoneinfo/UTC"),
        ("2026-10-04", "09:15", "america/los_angeles"),
        ("2026-10-04", "09:15", " UTC"),
        ("2026-10-04", "09:15", "localtime"),
        # Text carrying its own offset is never silently re-zoned.
        ("2026-10-04", "09:15+05:00", "UTC"),
        ("2026-10-04", "09:15Z", "America/Los_Angeles"),
        ("2026-10-04T09:15", "09:15", "UTC"),
        ("2026-02-30", "09:15", "UTC"),
        ("2026-10-04", "24:00", "UTC"),  # Python 3.14 reads it as 00:00.
        # Other ISO spellings fromisoformat() accepts but no control sends.
        ("20261004", "09:15", "UTC"),
        ("2026-W40-7", "09:15", "UTC"),
        ("２０２６-10-04", "09:15", "UTC"),
        ("2026-10-04", "0915", "UTC"),
        ("2026-10-04", "T09:15", "UTC"),
        ("2026-10-04", "09", "UTC"),
        ("2026-10-04", "09:15:30.5", "UTC"),
        ("2026-10-04", "9:15", "UTC"),
        ("2026-10-04", "09:15 ", "UTC"),
        # Instants beyond the representable years.
        ("0001-01-01", "00:00", "Asia/Tokyo"),
        ("9999-12-31", "23:59", "America/Los_Angeles"),
        ("", "09:15", "UTC"),
        ("2026-10-04", "", "UTC"),
    ],
)
def test_invalid_browser_local_values_are_rejected(day, clock, zone):
    """Unknown zones, offsets and malformed values never guess an instant."""
    with pytest.raises(ValueError):
        browser_instant(day, clock, zone)


CONTACT = FORM | {
    "contact_channel": "phone",
    "contact_date": "2026-09-19",
    "contact_time": "17:30",
}


def test_follow_up_contact_time_uses_the_browser_zone():
    """The contact attempt is read in the zone the browser sent with it."""
    local = change_values(form(CONTACT | {"contact_zone": "America/Los_Angeles"}))
    assert local["change"].contact_at == datetime(2026, 9, 20, 0, 30, tzinfo=UTC)
    utc = change_values(form(CONTACT | {"contact_zone": "UTC"}))
    assert utc["change"].contact_at == datetime(2026, 9, 19, 17, 30, tzinfo=UTC)
    # The time is typed in any common form (#398) and read in the same zone.
    for typed in ("5:30 pm", "1730", "17h30", " 5:30PM "):
        typed_form = form(CONTACT | {"contact_time": typed, "contact_zone": "UTC"})
        assert (
            change_values(typed_form)["change"].contact_at == utc["change"].contact_at
        )
    # Without a contact attempt the zone, like the date and time, is ignored.
    assert change_values(form(FORM | {"contact_zone": "Not/A_Zone"}))


@pytest.mark.parametrize(
    ("values", "code"),
    [
        # A page rendered before #558 sends no zone, and a browser may report
        # none: refused in place (#553), never guessed.
        (CONTACT, "contact_zone"),
        (
            {key: value for key, value in CONTACT.items() if key != "contact_zone"},
            "contact_zone",
        ),
        (CONTACT | {"contact_zone": "Not/A_Zone"}, "contact_zone"),
        (CONTACT | {"contact_zone": "../UTC"}, "contact_zone"),
        # A time the shared time-of-day parser cannot read (#398) is refused
        # at the Time field; a malformed date is an incomplete attempt.
        (CONTACT | {"contact_time": "17:30-07:00"}, "contact_time"),
        (CONTACT | {"contact_time": "24:00"}, "contact_time"),
        (
            CONTACT | {"contact_date": "2026-13-40", "contact_zone": "UTC"},
            "contact_incomplete",
        ),
    ],
)
def test_follow_up_contact_zone_is_a_correctable_refusal(values, code):
    """A missing or unknown zone, or a malformed time, is shown in place."""
    with pytest.raises(FollowupRefusal) as refused:
        change_values(form(values))
    assert refused.value.code == code


def test_repeated_zone_is_a_malformed_form():
    """Two zones are not a form the page sends: the plain 400 page."""
    values = CONTACT | {"contact_zone": ["UTC", "Asia/Tokyo"]}
    with pytest.raises(ValueError) as refused:
        change_values(QueryDict(urlencode(values, doseq=True)))
    assert not isinstance(refused.value, FollowupRefusal)


def test_unknown_zone_is_distinguishable():
    """Callers tell a zone problem from a malformed date or time."""
    with pytest.raises(UnknownZone):
        browser_instant("2026-10-04", "09:15", "")
    with pytest.raises(ValueError) as malformed:
        browser_instant("2026-10-04", "9:15", "")
    assert not isinstance(malformed.value, UnknownZone)


def test_follow_up_page_labels_no_utc_entry():
    """The contact fields name no fixed zone; a note names the browser's."""
    text = (TEMPLATES / "ministry-followup.html").read_text()
    assert "(UTC)" not in text
    # The hidden zone field carries the browser's zone (its other markers,
    # such as when it is sent, may change around it).
    zone = re.search(r'<input type="hidden" name="contact_zone"[^>]*>', text)[0]
    assert 'value="{{ form.contact_zone }}"' in zone
    assert " data-browser-zone " in zone
    # The shared note stays hidden until the script names a known zone, and
    # both fields carry the gate marker for an unknown one.
    assert ZONE_NOTE in text
    assert text.count("data-browser-zone-field>") == 2
    # The queue's Export these requests (#1157) offers UTC among its zone
    # choices, as every export asking for a zone does; the follow-up itself
    # names no fixed zone (the shared note names the browser's).
    followup = text[: text.index('<section aria-labelledby="followup-export-heading">')]
    assert "UTC" not in followup
    # Every follow-up instant shown (the request's submitted time, history,
    # contact, the queue's last contact, source as of) is converted in the
    # browser. The queue has no Submitted column (#1064).
    shown = [line for line in text.splitlines() if "|date:'c'" in line]
    assert len(shown) == 5
    assert all("data-local-instant" in line for line in shown)


@pytest.mark.parametrize(
    ("day", "zone", "start", "hours"),
    [
        # An ordinary day: UTC, and west of UTC, where it starts in the
        # UTC morning.
        (date(2026, 10, 4), "UTC", datetime(2026, 10, 4, tzinfo=UTC), 24),
        (
            date(2026, 10, 4),
            "America/New_York",
            datetime(2026, 10, 4, 4, tzinfo=UTC),
            24,
        ),
        # Spring forward (2:00 becomes 3:00): a 23-hour day.
        (
            date(2026, 3, 8),
            "America/Los_Angeles",
            datetime(2026, 3, 8, 8, tzinfo=UTC),
            23,
        ),
        # Fall back (2:00 becomes 1:00 again): a 25-hour day.
        (
            date(2026, 11, 1),
            "America/Los_Angeles",
            datetime(2026, 11, 1, 7, tzinfo=UTC),
            25,
        ),
        # East of UTC the local day starts on the previous UTC day; a
        # quarter-hour offset.
        (
            date(2026, 10, 4),
            "Asia/Kathmandu",
            datetime(2026, 10, 3, 18, 15, tzinfo=UTC),
            24,
        ),
        # East of UTC with daylight saving: New Zealand springs forward on
        # 27 September 2026 and falls back on 5 April 2026.
        (
            date(2026, 9, 27),
            "Pacific/Auckland",
            datetime(2026, 9, 26, 12, tzinfo=UTC),
            23,
        ),
        (
            date(2026, 4, 5),
            "Pacific/Auckland",
            datetime(2026, 4, 4, 11, tzinfo=UTC),
            25,
        ),
        # Clocks that spring forward at midnight (Chile, 6 September 2026), so
        # the day has no 00:00: 00:00 at the old offset is the change itself,
        # shown as 01:00.
        (
            date(2026, 9, 6),
            "America/Santiago",
            datetime(2026, 9, 6, 4, tzinfo=UTC),
            23,
        ),
        # Clocks that fall back at midnight (Chile, 4 April 2026): the
        # repeated 23:00 hour belongs to the Saturday, not the Sunday.
        (
            date(2026, 4, 4),
            "America/Santiago",
            datetime(2026, 4, 4, 3, tzinfo=UTC),
            25,
        ),
    ],
)
def test_browser_day_start_places_local_days(day, zone, start, hours):
    """A local calendar day is the UTC interval from its midnight to the next.

    The System logs From and Through filters use these bounds (#558); a
    daylight-saving day is 23 or 25 hours long, never a fixed 24.
    """
    assert browser_day_start(day, zone) == start
    end = browser_day_start(day + timedelta(days=1), zone)
    assert end - start == timedelta(hours=hours)


@pytest.mark.parametrize("zone", ["", "Etc/Unknown", "Mars/Olympus", "../etc"])
def test_browser_day_start_needs_a_known_zone(zone):
    """A blank or unknown zone is a zone problem, never read as UTC."""
    with pytest.raises(UnknownZone):
        browser_day_start(date(2026, 10, 4), zone)


def test_log_page_dates_are_browser_local():
    """The System logs filters name no fixed zone (#558): From and Through
    carry the browser zone field and its note, and the only "UTC" left is the
    export's own time zone choice (the reader picks the file's zone)."""
    text = (TEMPLATES / "logs.html").read_text()
    assert [line for line in text.splitlines() if "UTC" in line] == [
        line for line in text.splitlines() if '<option value="UTC">UTC</option>' in line
    ]
    assert 'name="zone" value="{{ query.zone }}" data-browser-zone ' in text
    assert ZONE_NOTE in text
    assert text.count("data-browser-zone-field>") == 2
    assert (
        '<form id="table-filters"' in text
        and "data-require-complete data-missing-hint" in text
    )
    error = (TEMPLATES / "logs-error.html").read_text()
    assert "UTC" not in error
    # The critical-events banner's link sends the browser zone with its day.
    banner = (TEMPLATES / "admin-banners.html").read_text()
    assert '<input type="hidden" name="zone" value="" data-browser-zone>' in banner
    # Without a zone the day is left out rather than refused (ui-v1.js).
    assert (
        'value="{{ admin_chrome.critical_since_day }}" data-zone-dependent>' in banner
    )
