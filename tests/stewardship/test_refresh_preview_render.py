"""The refresh schedule's seven-day list draws as it did before the shared
schedule preview (#1129, ADM-04 slice 7b).

The list moved onto ``occurrence_preview.build`` and
``components/occurrences.html``. The admin-portal spec ("Seven-day preview")
keeps its days, counts, struck times and daylight-saving words, and changes
only two pieces of markup: each day's ``details`` carries ``data-day`` and
today's ``data-today`` (so the page script keeps the days a reader opened or
closed by date), and the browser-time hook is a
``<time data-other-zone-time>`` that date-format-v1.js fills, where the
refresh script used to fill a ``<span data-browser-time>``.

The expected values below were rendered from the code before the move
(this branch's base) for each scenario: daylight-saving days going forward and back in
New York, London and Lord Howe (whose clocks move by half an hour), joined,
covered and skipped times, and the "cannot preview yet" line. Each test maps
the two intended markup changes back and compares the result: the readable
goldens exactly, the pins (the list, and the seven-day grid above it, which
reads the same days) as a hash of the markup with its whitespace collapsed,
so reformatting a template does not fail them but any other change does.
"""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.refresh_schedule_forms import (
    ScheduleEditor,
    editor_view,
)
from parishkit.stewardship.source.schedule_preview import durations, summarize
from parishkit.stewardship.source.send_windows import Window


def stored(*rules, switch=False):
    """Stored ParishSoft settings holding a ``refresh_rules`` document."""
    return {
        "organization_id": "1",
        "refresh_rules": {
            "rules": list(rules),
            "skips": [],
            "skip_around_family_emails": switch,
        },
    }


def full(value):
    """A full refresh rule at one wall time."""
    return {"kind": "full", "at": value}


def quick(value):
    """A stored quick update rule at one wall time."""
    return {"kind": "quick", "at": value}


def every(kind, step, start, end):
    """A repeating rule."""
    return {"kind": kind, "every": step, "from": start, "to": end}


def utc(*parts):
    """A UTC instant."""
    return datetime(*parts, tzinfo=UTC)


SKIP_WINDOWS = (
    Window(utc(2026, 10, 10, 13), utc(2026, 10, 10, 15, 30), "reminder_sending"),
    Window(utc(2026, 10, 9, 23, 50), utc(2026, 10, 10, 4, 10), "reminder_preparing"),
)
# name: (schedule zone, now, stored settings, Family email windows)
SCENARIOS = {
    "ny-ordinary": (
        "America/New_York",
        utc(2026, 10, 9, 16),
        stored(full("00:00"), every("full", 120, "08:00", "20:00")),
        (),
    ),
    "ny-back": (
        "America/New_York",
        utc(2026, 10, 29, 14),
        stored(full("01:30"), full("06:00"), every("quick", 60, "00:00", "23:00")),
        (),
    ),
    "ny-forward-joined": (
        "America/New_York",
        utc(2027, 3, 10, 3),
        stored(
            full("02:00"),
            full("02:30"),
            full("03:00"),
            quick("02:15"),
            quick("05:00"),
            every("quick", 30, "06:00", "08:00"),
        ),
        (),
    ),
    "london-forward": (
        "Europe/London",
        utc(2027, 3, 25, 23, 30),
        stored(full("01:00"), full("01:30"), full("12:00")),
        (),
    ),
    "lordhowe-back": (
        "Australia/Lord_Howe",
        utc(2027, 4, 1, 12),
        stored(full("01:45"), full("13:00")),
        (),
    ),
    "ny-skipped": (
        "America/New_York",
        utc(2026, 10, 9, 16),
        stored(full("00:00"), every("full", 60, "08:00", "20:00"), switch=True),
        SKIP_WINDOWS,
    ),
}
# SHA-256 of each scenario's list (from its first day section to the end)
# and of the part above it (the seven-day grid and its legend), whitespace
# collapsed (``collapsed``), as the code before the move rendered them.
BEFORE = {
    "ny-ordinary": "84164922c4229b9bc76de9f0acd39e319d46a7ca4c3f61be4543a1a0ee71f78e",
    "ny-back": "86876626f4fa1fc9ef278cd450633d4003fa0eb9284b0a7007b49909de29beeb",
    "ny-forward-joined": (
        "32833f37d69bc66de357523c6568f40e47800481208da51957a4f879afc7553e"
    ),
    "london-forward": (
        "43d532fb5e70b7c56397156755fd414ad842169df26a3fcacc09b3e3deddf2f4"
    ),
    "lordhowe-back": "f3e970dd96d16a80c73dfcccfa41493b93fa608c65f02a00c66c4bdda5f04f81",
    "ny-skipped": "7f95bf2c423bd6d732776dae467cdc21638eaa4273a6d60978535e917cce7e6a",
}
GRID_BEFORE = {
    "ny-ordinary": "79c4bc7624c3a0ab20b80ecebb9f497c66f33ffaa7436c50990a11623c815ff6",
    "ny-back": "7c62fcc3defd8905ee0e3d26a3bfdcadd5e8c2ce003c200470cd75df91d48f36",
    "ny-forward-joined": (
        "6169649f38f02986208805afc04a4c0610dfd08c1d6aaff29088a7f0cf327976"
    ),
    "london-forward": (
        "2ce29ee19bbac581233df83284227b5ee1b1867e8eb72c782b75289029872351"
    ),
    "lordhowe-back": "acc32139898d08cad9ef636b0e1bba0fb7b9d0b8832ef099ff419fb1ec3ce211",
    "ny-skipped": "ff00ab1a31d2ee74a9943d2084d666ed7d338d40c2874672285b70d1ba7b19e5",
}
UNAVAILABLE = (
    '<p class="help">The preview shows once every rule can be read and the '
    "schedule has a full refresh.</p>\n"
)


def rendered(zone, now, settings, windows):
    """The preview part of the settings page for one scenario."""

    def reader(document, *, now, timezone):
        """The preview without a database: typical durations, given windows."""
        return summarize(
            document,
            timezone=timezone,
            today=now.astimezone(ZoneInfo(timezone)).date(),
            windows=windows,
            measured=durations({}),
            margin=timedelta(minutes=30),
        )

    view = editor_view(
        ScheduleEditor(stored=settings), timezone=zone, now=now, preview_reader=reader
    )
    return render_to_string(
        "stewardship/refresh-schedule-preview.html", {"schedule": view}
    )


def as_before(html):
    """``html`` with the two intended markup changes mapped back."""
    html = re.sub(r' data-day="\d{4}-\d{2}-\d{2}"( data-today)?', "", html)
    return re.sub(
        r'<time class="help" datetime="([^"]+)" data-other-zone-time '
        r'data-zone="[^"]+" data-text="\(\{time\} your time\)"></time>',
        r'<span class="help" data-browser-time="\1"></span>',
        html,
    )


def listed(html):
    """The list part: from the first day section to the end."""
    return html[html.index('<details class="schedule-day"') :]


def gridded(html):
    """The part above the list: the seven-day grid and its legend."""
    return html[: html.index('<details class="schedule-day"')]


def collapsed(html):
    """``html`` with each run of whitespace one space and none between tags,
    so a pin survives a template reformatted without changing what it draws."""
    return re.sub(r">\s+<", "><", re.sub(r"\s+", " ", html)).strip()


def pinned(html, expected, what):
    """Assert ``html``, whitespace collapsed, hashes to ``expected``; on a
    mismatch, show the markup one tag per line so it can be compared with
    the base's render (or the readable goldens below)."""
    html = collapsed(html)
    digest = hashlib.sha256(html.encode()).hexdigest()
    assert digest == expected, (
        f"{what} no longer draws as before the move (sha256 {digest}). Render "
        "the same scenario on the base and diff it with this one, tag per "
        "line:\n" + html.replace("><", ">\n<")
    )


def day_section(html, heading):
    """The one day section whose summary starts with ``heading``."""
    return re.search(
        rf'<details class="schedule-day"( open)?><summary>{re.escape(heading)}.*?'
        r"</details>",
        html,
        re.S,
    ).group(0)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_list_draws_exactly_as_before(name):
    """The days, counts, lines and struck reasons are unchanged."""
    html = as_before(listed(rendered(*SCENARIOS[name])))
    pinned(html, BEFORE[name], f"The {name} list")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_seven_day_grid_draws_exactly_as_before(name):
    """The grid, which reads the same days as the list, is unchanged."""
    pinned(gridded(rendered(*SCENARIOS[name])), GRID_BEFORE[name], f"The {name} grid")


def test_the_cannot_preview_line_is_unchanged():
    """A schedule with no full refresh shows the unavailable state's line."""
    zone, now, _, _ = SCENARIOS["ny-ordinary"]
    assert rendered(zone, now, stored(quick("05:00")), ()) == UNAVAILABLE


def test_the_clocks_go_forward_day_with_joined_times():
    """New York: the nightly runs an hour late, the others join it."""
    html = as_before(rendered(*SCENARIOS["ny-forward-joined"]))
    section = day_section(html, "Sunday, March 14, 2027 (clocks go forward)")
    assert section == (
        '<details class="schedule-day"><summary>Sunday, March 14, 2027 (clocks go '
        "forward): 1 full refresh, 6 quick updates, about 20 min of ParishSoft "
        'time</summary>\n<ul class="schedule-day-times"><li class="schedule-time-full">'
        "02:00 nightly full refresh: runs at 03:00, clocks go forward"
        '<span class="help" data-browser-time="2027-03-14T07:00:00+00:00"></span></li>'
        '<li class="schedule-time-quick"><s>02:15 quick update runs with the 02:00 '
        'one: clocks go forward</s></li><li class="schedule-time-full"><s>02:30 full '
        "refresh runs with the 02:00 one: clocks go forward</s></li>"
        '<li class="schedule-time-full"><s>03:00 full refresh runs with the 02:00 '
        "one: clocks go forward</s></li>"
        + "".join(
            f'<li class="schedule-time-quick">{wall} quick update<span class="help" '
            f'data-browser-time="2027-03-14T{instant}:00+00:00"></span></li>'
            for wall, instant in (
                ("05:00", "09:00"),
                ("06:00", "10:00"),
                ("06:30", "10:30"),
                ("07:00", "11:00"),
                ("07:30", "11:30"),
                ("08:00", "12:00"),
            )
        )
        + "</ul></details>"
    )


def test_the_half_hour_clock_change_days():
    """London goes forward an hour; Lord Howe goes back half an hour."""
    london = as_before(rendered(*SCENARIOS["london-forward"]))
    assert day_section(london, "Sunday, March 28, 2027 (clocks go forward)") == (
        '<details class="schedule-day"><summary>Sunday, March 28, 2027 (clocks go '
        "forward): 2 full refreshes, about 15 min of ParishSoft time</summary>\n"
        '<ul class="schedule-day-times"><li class="schedule-time-full">01:00 nightly '
        "full refresh: runs at 02:00, clocks go forward"
        '<span class="help" data-browser-time="2027-03-28T01:00:00+00:00"></span></li>'
        '<li class="schedule-time-full"><s>01:30 full refresh runs with the 01:00 '
        'one: clocks go forward</s></li><li class="schedule-time-full">12:00 full '
        'refresh<span class="help" data-browser-time="2027-03-28T11:00:00+00:00">'
        "</span></li></ul></details>"
    )
    howe = as_before(rendered(*SCENARIOS["lordhowe-back"]))
    assert day_section(howe, "Sunday, April 4, 2027 (clocks go back)") == (
        '<details class="schedule-day"><summary>Sunday, April 4, 2027 (clocks go '
        "back): 2 full refreshes, about 15 min of ParishSoft time</summary>\n"
        '<ul class="schedule-day-times"><li class="schedule-time-full">01:45 nightly '
        "full refresh: runs once, clocks go back"
        '<span class="help" data-browser-time="2027-04-03T14:45:00+00:00"></span></li>'
        '<li class="schedule-time-full">13:00 full refresh<span class="help" '
        'data-browser-time="2027-04-04T02:30:00+00:00"></span></li></ul></details>'
    )


def test_today_is_listed_whole_and_open():
    """Today's earlier times are listed, unmarked; only today opens."""
    html = rendered(*SCENARIOS["ny-back"])  # 10:00 in New York
    assert html.count("<details ") == 7 and html.count(" open>") == 1
    today = day_section(as_before(html), "Today, Thursday, October 29, 2026")
    assert today.startswith('<details class="schedule-day" open>')
    assert '<li class="schedule-time-quick">00:00 quick update<span' in today
    assert '<li class="schedule-time-full">01:30 nightly full refresh<span' in today
    assert 'data-day="2026-10-29" data-today open>' in html
    assert html.count(" data-today") == 1
