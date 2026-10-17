"""The shared schedule preview's include, components/occurrences.html (#1129).

ui-conventions "Schedule previews": these tests render each state and both
layouts, and keep pages from drawing an occurrence list by hand again: the
templates that still do are listed, each with why, and the list fails on a
stale entry too.
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path

from django.template.loader import render_to_string

from parishkit.stewardship.occurrence_preview import Day, Occurrence, build

TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)
COMPONENTS = {"components/occurrences.html", "components/occurrence-line.html"}
NY = "America/New_York"
# What a hand-drawn occurrence list looks like: a mail schedule's resolved
# send times, the refresh list's per-day markup or its browser-time hook, or
# a loop drawing each copied schedule's send times through the review's
# summary.
HAND_DRAWN = re.compile(
    r"resolved_slots|schedule-day-times|data-browser-time\b|data-other-zone-time"
    r"|for schedule in schedules %\}.*?schedule-summary\.html",
    re.S,
)
# Templates that still draw an occurrence list by hand, each with why. A
# template leaves this list when it draws the include.
STILL_HAND_DRAWN = {
    # The mail schedule review's send times move onto the component in
    # ADM-04 slice 7c.
    "schedule-summary.html",
    # Copy campaign's review lists each copied schedule's sends; the server
    # refuses it until #145 removes it.
    "clone-preview.html",
}


def render(preview):
    """The include drawn for ``preview`` (``occurrence_preview.build``'s context)."""
    return render_to_string(
        "stewardship/components/occurrences.html", {"preview": preview}
    )


def at(*parts):
    """A UTC instant."""
    return datetime(*parts, tzinfo=UTC)


def grouped(verb):
    """A two-day grouped list, the second day's clocks going back, built
    with ``verb``."""
    today, tomorrow = date(2026, 10, 9), date(2026, 10, 10)
    return build(
        [
            Occurrence(today, at(2026, 10, 9, 4), "full refresh", "full", wall="00:00"),
            Occurrence(
                tomorrow,
                None,
                "quick update",
                "quick",
                wall="00:00",
                struck=True,
                reason="covered by the 00:00 full refresh",
            ),
        ],
        days=[Day(today, summary="1 full refresh"), Day(tomorrow, change="back")],
        shown_zone=NY,
        schedule_zone=NY,
        now=at(2026, 10, 9, 16),
        layout="grouped",
        verb=verb,
    )


def test_the_grouped_layout_draws_one_section_per_day_today_open():
    """Each day a details/summary keyed by date, today marked data-today;
    schedule-zone lines write the wall time and mark the instant for the
    browser's time; a "sends" list with a clock change carries the general
    daylight-saving sentence."""
    html = render(grouped("sends"))
    assert html == (
        '<details class="schedule-day" data-day="2026-10-09" data-today open>'
        "<summary>Today, "
        "Friday, October 9, 2026: 1 full refresh</summary>\n"
        '<ul class="schedule-day-times"><li class="schedule-time-full">00:00 full '
        'refresh<time class="help" datetime="2026-10-09T04:00:00+00:00" '
        f'data-other-zone-time data-zone="{NY}" data-text="({{time}} your time)">'
        "</time></li></ul></details>\n"
        '<details class="schedule-day" data-day="2026-10-10"><summary>Saturday, '
        "October 10, 2026 (clocks go back)</summary>\n"
        '<ul class="schedule-day-times"><li class="schedule-time-quick"><s>00:00 '
        "quick update covered by the 00:00 full refresh</s></li></ul></details>\n"
        '<p class="help">When clocks change for daylight saving, a time that happens '
        "twice sends once, and a time that is skipped sends right after the "
        "change.</p>\n"
    )


def test_a_runs_list_leaves_out_the_general_daylight_saving_sentence():
    """The sentence speaks of sends; a "runs" list (the refresh schedule's)
    draws the same days without it."""
    sends, runs = render(grouped("sends")), render(grouped("runs"))
    assert "When clocks change" not in runs
    assert runs == sends[: sends.index('<p class="help">When clocks change')]


def test_the_compact_layout_says_a_days_clock_change():
    """As the grouped heading does, a compact line names its day's clock
    change after the date."""
    html = render(
        build(
            [Occurrence(date(2026, 11, 1), at(2026, 11, 1, 15), "Reminder", "r")],
            shown_zone=NY,
            schedule_zone=NY,
            now=at(2026, 10, 31),
        )
    )
    assert html.startswith(
        '<ul class="occurrence-list"><li class="schedule-time-r">Sunday, '
        "November 1, 2026 (clocks go back), <time "
    )


def test_the_compact_layout_names_each_lines_day_and_browser_time():
    """One list; each line its day, a time-only browser instant, what runs,
    and the more line under it."""
    found = [
        Occurrence(date(2026, 10, d), at(2026, 10, d, 14), f"Reminder {d}", "reminder")
        for d in (20, 21)
    ]
    html = render(
        build(
            found,
            shown_zone=NY,
            schedule_zone=NY,
            now=at(2026, 10, 19),
            limit=1,
        )
    )
    assert html == (
        '<ul class="occurrence-list"><li class="schedule-time-reminder">Tuesday, '
        'October 20, 2026, <time datetime="2026-10-20T14:00:00+00:00" '
        "data-local-instant data-time-only>2026-10-20T14:00:00+00:00</time> "
        "Reminder 20</li></ul>\n"
        '<p class="help">Only the next 1 send is shown. Later sends follow the same '
        "pattern.</p>\n"
    )


def test_a_zone_less_list_writes_full_instants():
    """No shown zone: no day names, and each instant is a full local instant."""
    html = render(
        build(
            [Occurrence(None, at(2026, 10, 20, 14), "Reminder 1", "reminder")],
            schedule_zone=NY,
            now=at(2026, 10, 1),
            limit=5,
        )
    )
    assert html == (
        '<ul class="occurrence-list"><li class="schedule-time-reminder"><time '
        'datetime="2026-10-20T14:00:00+00:00" data-local-instant>'
        "2026-10-20T14:00:00+00:00</time> Reminder 1</li></ul>\n"
    )


def test_empty_unavailable_and_paused_states():
    """One help line each for empty and unavailable; paused adds its notice
    with the link above the lines."""
    empty = build(
        [], schedule_zone=NY, shown_zone=NY, now=at(2026, 10, 1), empty="No more sends."
    )
    assert render(empty) == '<p class="help">No more sends.</p>'
    unavailable = build(
        [], schedule_zone=NY, now=None, unavailable="Cannot preview <yet>."
    )
    assert render(unavailable) == '<p class="help">Cannot preview &lt;yet&gt;.</p>'
    paused = build(
        [Occurrence(None, at(2026, 10, 20, 14), "Reminder 1")],
        schedule_zone=NY,
        now=at(2026, 10, 1),
        paused={
            "text": "Mail is paused.",
            "url": "/admin/pause/",
            "label": "Pause and resume mail",
        },
    )
    html = render(paused)
    assert html.startswith(
        '<p class="notice">Mail is paused. <a href="/admin/pause/">Pause and resume '
        "mail</a></p>\n<ul "
    )
    assert "<s>" not in html


def test_no_new_template_draws_an_occurrence_list_by_hand():
    """Only the listed templates draw their own list; a stale entry fails."""
    found = {
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if path.relative_to(TEMPLATES).as_posix() not in COMPONENTS
        and HAND_DRAWN.search(path.read_text())
    }
    assert found == STILL_HAND_DRAWN
