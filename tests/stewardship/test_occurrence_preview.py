"""The shared schedule preview's server half, ``occurrence_preview.build`` (#1129).

ui-conventions "Schedule previews": grouping by day in the shown zone
(including both daylight-saving days), covered times placed by their
``day``, a window of whole days that keeps today's earlier times, the count
limit, the zone-less ungrouped list, both shifted-line wordings, and each
state.
"""

from datetime import UTC, date, datetime

from parishkit.stewardship.occurrence_preview import Day, Occurrence, build

NY = "America/New_York"
LA = "America/Los_Angeles"


def at(*parts):
    """A UTC instant."""
    return datetime(*parts, tzinfo=UTC)


def lines(context):
    """Each day's (date, [(what, struck, shift_text)])."""
    return [
        (
            day["date"],
            [
                (item["text"], item["struck"], item["shift_text"])
                for item in day["items"]
            ],
        )
        for day in context["days"]
    ]


def test_a_count_list_groups_by_the_shown_zone_across_both_clock_changes():
    """Days come from the occurrences, each with its clock change worked out
    in the shown zone; Today is today there, and opens."""
    found = [
        Occurrence(date(2026, 10, 31), at(2026, 11, 1, 3), "Reminder 1"),
        Occurrence(date(2026, 11, 1), at(2026, 11, 1, 8), "Reminder 2"),
        Occurrence(date(2027, 3, 14), at(2027, 3, 14, 12), "Reminder 3"),
    ]
    context = build(
        found,
        shown_zone=LA,
        schedule_zone=LA,
        now=at(2026, 10, 31, 20),
        limit=5,
        layout="grouped",
    )
    assert context["state"] == "shown" and context["grouped"]
    assert not context["browser_zone"]
    days = context["days"]
    assert [day["date"] for day in days] == [
        date(2026, 10, 31),
        date(2026, 11, 1),
        date(2027, 3, 14),
    ]
    assert [day["change"] for day in days] == [None, "back", "forward"]
    assert [day["today"] for day in days] == [True, False, False]
    assert [day["open"] for day in days] == [True, False, False]
    assert [str(day["weekday"]) for day in days] == ["Saturday", "Sunday", "Sunday"]
    # A clock change on a listed day brings the general sentence.
    assert context["dst_note"] and context["more"] is None


def test_a_window_list_keeps_todays_earlier_times_and_empty_days():
    """Every passed Day is listed, today's earlier times included and not
    marked; a time with no instant (covered) is placed by its day."""
    today, tomorrow = date(2026, 10, 9), date(2026, 10, 10)
    found = [
        Occurrence(today, at(2026, 10, 9, 4), "full refresh", "full", wall="00:00"),
        Occurrence(
            today,
            None,
            "quick update",
            "quick",
            wall="00:00",
            struck=True,
            reason="covered by the 00:00 full refresh",
        ),
        Occurrence(today, at(2026, 10, 9, 20), "full refresh", "full", wall="16:00"),
    ]
    days = [
        Day(today, summary="2 full refreshes", extra={"cells": ["a"]}),
        Day(tomorrow, summary="0 full refreshes", extra={"cells": ["b"]}),
    ]
    context = build(
        found,
        days=days,
        shown_zone=NY,
        schedule_zone=NY,
        now=at(2026, 10, 9, 16),
        layout="grouped",
        verb="runs",
    )
    assert lines(context) == [
        (
            today,
            [
                ("full refresh", False, None),
                ("quick update", True, None),
                ("full refresh", False, None),
            ],
        ),
        (tomorrow, []),
    ]
    first = context["days"][0]
    assert first["summary"] == "2 full refreshes" and first["cells"] == ["a"]
    assert first["items"][0]["at"] == "2026-10-09T04:00:00+00:00"
    assert first["items"][1]["at"] is None
    assert first["items"][1]["reason"] == "covered by the 00:00 full refresh"
    assert first["items"][0]["wall"] == "00:00"
    assert not context["dst_note"]


def test_a_count_list_drops_passed_times_and_says_when_it_cut_some():
    """Only the next ``limit`` after now; the more line names the limit."""
    found = [
        Occurrence(date(2026, 10, d), at(2026, 10, d, 14), f"Send {d}")
        for d in range(1, 10)
    ]
    context = build(
        found, shown_zone=NY, schedule_zone=NY, now=at(2026, 10, 2), limit=5
    )
    whats = [item["text"] for day in context["days"] for item in day["items"]]
    assert whats == ["Send 2", "Send 3", "Send 4", "Send 5", "Send 6"]
    assert context["more"] == (
        "Only the next 5 sends are shown. Later sends follow the same pattern."
    )
    exact = build(
        found[:6], shown_zone=NY, schedule_zone=NY, now=at(2026, 10, 2), limit=5
    )
    assert exact["more"] is None
    # A feature that cut the list itself says so through ``more``.
    told = build(
        found[1:3],
        shown_zone=NY,
        schedule_zone=NY,
        now=at(2026, 10, 1),
        limit=5,
        more=True,
    )
    assert told["more"].startswith("Only the next 5 sends are shown.")


def test_a_zone_less_list_is_one_ungrouped_list_and_never_refuses():
    """No shown zone: no day headings, one list of full instants."""
    found = [
        Occurrence(None, at(2026, 10, 20, 14), "Reminder 1", shift="forward"),
        Occurrence(None, at(2026, 10, 21, 14), "Reminder 2"),
    ]
    context = build(found, schedule_zone=NY, now=at(2026, 10, 1), limit=5)
    assert context["state"] == "shown" and not context["grouped"]
    assert len(context["days"]) == 1 and context["days"][0]["date"] is None
    items = context["days"][0]["items"]
    assert [item["text"] for item in items] == ["Reminder 1", "Reminder 2"]
    # It never names the campaign's wall time.
    assert items[0]["shift_text"] == (
        "sends later than set because the campaign's clocks go forward"
    )
    assert context["dst_note"]


def test_both_shifted_line_wordings():
    """In the schedule zone the line names the real wall time; in another
    zone it says only which way the campaign's clocks moved it."""
    forward = Occurrence(
        date(2027, 3, 14), at(2027, 3, 14, 7), "Reminder 1", shift="forward"
    )
    back = Occurrence(
        date(2026, 11, 1), at(2026, 11, 1, 5, 30), "Reminder 2", shift="back"
    )

    def words(shown_zone, verb="sends"):
        """The two lines' shift words when shown in ``shown_zone``."""
        context = build(
            [back, forward],
            shown_zone=shown_zone,
            schedule_zone=NY,
            now=at(2026, 1, 1),
            verb=verb,
        )
        return [item["shift_text"] for day in context["days"] for item in day["items"]]

    # A send list writes the time in the parish's clock, as its "set for"
    # time; a refresh list in its 24-hour HH:MM.
    assert words(NY) == [
        "sends once, clocks go back",
        "sends at 3:00 AM, clocks go forward",
    ]
    assert words(NY, "runs") == [
        "runs once, clocks go back",
        "runs at 03:00, clocks go forward",
    ]
    assert words(LA) == [
        "sends once because the campaign's clocks go back",
        "sends later than set because the campaign's clocks go forward",
    ]
    assert build([forward], shown_zone=LA, schedule_zone=NY, now=at(2026, 1, 1))[
        "browser_zone"
    ]


def test_the_empty_unavailable_and_paused_states():
    """Each state carries its feature's words; paused keeps the lines."""
    empty = build(
        [Occurrence(date(2026, 10, 1), at(2026, 10, 1, 14), "Reminder 1")],
        shown_zone=NY,
        schedule_zone=NY,
        now=at(2026, 10, 2),
        limit=5,
        empty="No more sends: this reminder's time has passed.",
    )
    assert empty["state"] == "empty" and empty["days"] == []
    assert empty["message"] == "No more sends: this reminder's time has passed."
    unavailable = build(
        [Occurrence(date(2026, 10, 3), at(2026, 10, 3, 14), "Reminder 1")],
        shown_zone=NY,
        schedule_zone=NY,
        now=None,
        unavailable="The preview shows once every rule can be read.",
    )
    assert unavailable["state"] == "unavailable" and unavailable["days"] == []
    assert unavailable["message"] == "The preview shows once every rule can be read."
    notice = {
        "text": "Live campaign email is paused.",
        "url": "/admin/pause",
        "label": "Pause and resume mail",
    }
    paused = build(
        [Occurrence(date(2026, 10, 3), at(2026, 10, 3, 14), "Reminder 1")],
        shown_zone=NY,
        schedule_zone=NY,
        now=at(2026, 10, 2),
        limit=5,
        paused=notice,
    )
    assert paused["state"] == "shown" and paused["paused"] == notice
    assert [item["struck"] for item in paused["days"][0]["items"]] == [False]
