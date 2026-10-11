"""The refresh schedule preview's seven-day grid (#632 slice 4).

The grid is a picture of the per-day list: each day's 96 quarter-hour cells
mark the refreshes the preview says run, shade the Family email windows the
preview applies and the hour that does not exist when clocks go forward, and
mark the current quarter hour. These tests build the preview with the pure
``summarize`` (no database) and check the cells, then the rendered page.
"""

import re
from datetime import UTC, date, datetime, timedelta

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.refresh_schedule_forms import (
    ScheduleEditor,
    editor_view,
)
from parishkit.stewardship.accounts.refresh_schedule_grid import (
    CELLS,
    grid_rows,
    legend,
)
from parishkit.stewardship.source.schedule_preview import durations, summarize
from parishkit.stewardship.source.send_windows import Window

from .schedule_rows import rows

ZONE = "America/New_York"
ORDINARY = date(2026, 9, 11)  # EDT, UTC-4
SPRING = date(2027, 3, 14)  # 02:00 EST becomes 03:00 EDT
FALL = date(2026, 11, 1)  # 02:00 EDT becomes 01:00 EST


def utc(day, hour, minute=0):
    """A UTC instant on ``day``."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def document(*rules, skip=False):
    """A ``refresh_rules`` document."""
    return {"rules": list(rules), "skips": [], "skip_around_family_emails": skip}


def at(kind, value):
    return {"kind": kind, "at": value}


def every(kind, step, start, end):
    return {"kind": kind, "every": step, "from": start, "to": end}


def preview(refresh_rules, today, windows=()):
    """The preview of ``refresh_rules`` from ``today``, without a database."""
    return summarize(
        refresh_rules,
        timezone=ZONE,
        today=today,
        windows=windows,
        measured=durations({}),
        margin=timedelta(minutes=30),
    )


def grid(refresh_rules, today, *, now=None, windows=()):
    """The grid rows, with ``now`` at noon UTC of ``today`` by default."""
    return grid_rows(
        preview(refresh_rules, today, windows),
        timezone=ZONE,
        now=now or utc(today, 12),
    )


def marked(row, kind):
    """The indexes of the cells in ``row`` where a refresh of ``kind`` runs."""
    return [index for index, cell in enumerate(row["cells"]) if cell[kind]]


def shaded(row):
    """Each shaded cell's index and shading."""
    return {
        index: cell["shade"] for index, cell in enumerate(row["cells"]) if cell["shade"]
    }


def test_each_day_has_96_cells_marking_the_refreshes_that_run():
    """Seven rows; full at 00:00 and every 4 h from 08:00; quick hourly."""
    shown = grid(
        document(
            at("full", "00:00"),
            every("full", 240, "08:00", "20:00"),
            every("quick", 60, "01:00", "23:00"),
        ),
        ORDINARY,
    )
    assert len(shown) == 7 and all(len(row["cells"]) == CELLS for row in shown)
    first = shown[0]
    assert first["day"] == ORDINARY and first["change"] is None
    assert marked(first, "full") == [0, 32, 48, 64, 80]
    # 08:00, 12:00, 16:00 and 20:00 quick times are covered by the full ones.
    assert marked(first, "quick") == [
        hour * 4 for hour in range(1, 24) if hour not in (8, 12, 16, 20)
    ]
    assert shaded(first) == {}
    assert legend(shown) == []


def test_a_kept_off_quarter_hour_time_is_marked_in_its_cell():
    """A converted 23:50 full time falls in the 23:45 cell."""
    row = grid(document(at("full", "00:00"), at("full", "23:50")), ORDINARY)[0]
    assert marked(row, "full") == [0, 95]
    assert row["cells"][95]["title"] == "23:45 to 00:00, full refresh"


def test_the_spring_day_shades_the_missing_hour_and_runs_it_at_three():
    """02:00–02:45 do not exist; the 02:30 full time runs in the 03:00 cell."""
    row = grid(document(at("full", "02:30")), SPRING)[0]
    assert row["change"] == "forward"
    assert shaded(row) == {index: "gap" for index in range(8, 12)}
    assert marked(row, "full") == [12]
    assert row["cells"][8]["title"] == (
        "02:00 to 02:15, Clocks go forward: this time does not exist"
    )
    assert [key for key, _ in legend([row])] == ["gap"]


def test_the_fall_day_shades_a_window_in_either_repeated_hour():
    """A window in the second 01:30 (EST) shades the 01:30 cell."""
    window = Window(utc(FALL, 6, 30), utc(FALL, 6, 45), "reminder_sending")
    row = grid(document(at("full", "00:00"), skip=True), FALL, windows=(window,))[0]
    assert row["change"] == "back"
    assert shaded(row) == {6: "reminder_sending"}
    # The cell stands for both 01:30s, and its hover text says so.
    assert row["cells"][6]["title"] == (
        "01:30 to 01:45, both times (clocks go back), Reminder: sending"
    )


def test_windows_are_shaded_and_their_skipped_refreshes_not_marked():
    """Preparing 06:00–08:00 then sending 08:00–09:10 (EDT): skipped quick
    updates are not marked, the nightly runs even inside a window, and a
    window shades every cell it touches."""
    windows = (
        Window(utc(ORDINARY, 10), utc(ORDINARY, 12), "reminder_preparing"),
        Window(utc(ORDINARY, 12), utc(ORDINARY, 13, 10), "reminder_sending"),
        Window(
            utc(ORDINARY, 4) - timedelta(minutes=5),
            utc(ORDINARY, 4, 5),
            "initial_sending",
        ),
    )
    row = grid(
        document(at("full", "00:00"), every("quick", 60, "01:00", "23:00"), skip=True),
        ORDINARY,
        windows=windows,
    )[0]
    shading = shaded(row)
    assert {i: shading[i] for i in range(24, 32)} == dict.fromkeys(
        range(24, 32), "reminder_preparing"
    )
    # 09:00–09:15 holds the window's last ten minutes.
    assert [shading[i] for i in range(32, 37)] == ["reminder_sending"] * 5
    assert shading[0] == "initial_sending" and 37 not in shading
    assert 0 in marked(row, "full")
    assert not {24, 28, 32, 36} & set(marked(row, "quick"))
    assert 40 in marked(row, "quick")
    assert [key for key, _ in legend([row])] == [
        "reminder_preparing",
        "reminder_sending",
        "initial_sending",
    ]


def test_the_earliest_window_names_a_cell_two_windows_touch():
    """As ``send_windows.window_cause``, the earliest-starting window wins."""
    windows = (
        Window(utc(ORDINARY, 12, 10), utc(ORDINARY, 13), "reminder_sending"),
        Window(utc(ORDINARY, 11), utc(ORDINARY, 12, 5), "initial_sending"),
    )
    row = grid(document(at("full", "00:00"), skip=True), ORDINARY, windows=windows)[0]
    assert row["cells"][32]["shade"] == "initial_sending"
    assert row["cells"][33]["shade"] == "reminder_sending"


def test_only_the_current_quarter_hour_is_marked_now_and_blank_cells_have_no_title():
    """Now at 12:20 EDT marks the 12:15 cell of today alone."""
    shown = grid(document(at("full", "00:00")), ORDINARY, now=utc(ORDINARY, 16, 20))
    now = [
        (day, index)
        for day, row in enumerate(shown)
        for index, cell in enumerate(row["cells"])
        if cell["now"]
    ]
    assert now == [(0, 49)]
    assert shown[0]["cells"][49]["title"] == "12:15 to 12:30, now"
    assert shown[0]["cells"][50]["title"] == ""


def test_the_page_draws_the_grid_by_class_with_a_text_alternative():
    """Seven rows of 96 cells, hour labels, a legend of what is shown, and
    no inline style (the page's content security policy forbids them)."""

    def reader(refresh_rules, *, now, timezone):
        return preview(refresh_rules, now.astimezone(UTC).date())

    now = utc(ORDINARY, 16)
    editor = ScheduleEditor(
        rows([at("full", "02:00"), every("quick", 60, "00:00", "23:00")]),
        stored={"organization_id": "1"},
    )
    shown = editor_view(editor, timezone=ZONE, now=now, preview_reader=reader)
    html = render_to_string(
        "stewardship/refresh-schedule-preview.html", {"schedule": shown}
    )
    grid_html = html.split('class="schedule-grid-scroll"', 1)[1].split("</ul>", 1)[0]
    assert 'role="img"' in grid_html and 'tabindex="0"' in grid_html
    assert "The list below gives the same refresh times in words." in grid_html
    assert '<ul class="schedule-grid-legend" aria-hidden="true">' in grid_html
    assert grid_html.count('<div class="schedule-grid-row">') == 7
    assert (
        len(re.findall(r'<span(?: class="[^"]*" title="[^"]*")?></span>', grid_html))
        == 7 * CELLS
    )
    assert grid_html.count("grid-full") == 7 + 1  # one a day, and the legend
    assert "00:00</span>" in grid_html and "21:00</span>" in grid_html
    assert "Today" in grid_html and "Saturday" in grid_html
    assert "style=" not in html
    # Nothing is shaded, so the legend names only the marks and now.
    assert "Reminder:" not in grid_html and "Now</li>" in grid_html
    # The list still follows the grid.
    assert html.index("schedule-grid-scroll") < html.index('class="schedule-day"')
