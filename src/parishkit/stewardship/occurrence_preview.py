"""The one schedule preview component's server half (#1129, ADM-04.07).

Every list of when a schedule runs or sends is drawn by one component: this
helper and the template ``components/occurrences.html`` (ui-conventions
spec, "Schedule previews"). A feature computes its occurrences with its own
evaluator (the refresh schedule's ``schedule_preview``, a mail schedule's
slots) and hands them to :func:`build` as :class:`Occurrence` records,
optionally with one :class:`Day` per day of its window; ``build`` groups
them by day, names the days, words the daylight-saving shifts, applies the
count limit and picks the component's state. The template reads only the
context ``build`` returns, so a feature never draws its own list.

Nothing here evaluates a schedule or reads the database.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils.translation import gettext, ngettext

from parishkit.stewardship.wall_clock import WEEKDAYS, wall_hhmm


@dataclass(frozen=True)
class Occurrence:
    """One time a schedule runs or sends, or would and will not.

    ``day`` is its local date in the shown zone (None in a zone-less list);
    it places the occurrence even when ``at``, the UTC instant, is None (a
    time that will not run, such as a covered quick time). ``what`` is what
    runs ("full refresh", "Reminder 2"), ``kind`` a class hook, ``wall``
    the schedule-zone wall time a schedule-zone list writes on the server.
    A ``struck`` occurrence will not run, and ``reason`` says why in words.
    ``shift`` is "forward" or "back" when a clock change moves its wall
    time in the zone its schedule is evaluated in.
    """

    day: date | None
    at: datetime | None
    what: str
    kind: str = ""
    wall: str | None = None
    struck: bool = False
    reason: str | None = None
    shift: str | None = None


@dataclass(frozen=True)
class Day:
    """One day's own data: its clock change, counts and feature extras.

    ``change`` is "forward" or "back" when clocks change that day in the
    shown zone; ``summary`` is the feature's counts for the day's heading;
    ``extra`` maps feature keys copied into the day's context (the refresh
    grid's ``cells``).
    """

    date: date
    change: str | None = None
    summary: str | None = None
    extra: dict | None = None


def _shift_text(shift, at, *, same_zone, schedule_zone, verb):
    """The words for an occurrence a clock change moves, or None.

    When the list is shown in the schedule's own zone the line names the
    wall time it really happens at; otherwise it never names the
    campaign's wall time (ui-conventions "Dates and times") and says only
    which way the campaign's clocks moved it.
    """
    if shift is None:
        return None
    if same_zone:
        if shift == "forward":
            moved = wall_hhmm(at, schedule_zone) if at is not None else ""
            words = {
                "runs": gettext("runs at %(time)s, clocks go forward"),
                "sends": gettext("sends at %(time)s, clocks go forward"),
            }[verb]
            return words % {"time": moved}
        return {
            "runs": gettext("runs once, clocks go back"),
            "sends": gettext("sends once, clocks go back"),
        }[verb]
    if shift == "forward":
        return {
            "runs": gettext(
                "runs later than set because the campaign's clocks go forward"
            ),
            "sends": gettext(
                "sends later than set because the campaign's clocks go forward"
            ),
        }[verb]
    return {
        "runs": gettext("runs once because the campaign's clocks go back"),
        "sends": gettext("sends once because the campaign's clocks go back"),
    }[verb]


def _change(day, zone):
    """Whether clocks go "forward" or "back" on local ``day`` in ``zone``, else None."""
    start = datetime.combine(day, time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=zone)
    before, after = start.utcoffset(), end.utcoffset()
    if after > before:
        return "forward"
    return "back" if after < before else None


def _item(occurrence, **wording):
    """One line's context: the occurrence's words and its UTC instant as text."""
    return {
        "at": (
            occurrence.at.astimezone(UTC).isoformat()
            if occurrence.at is not None
            else None
        ),
        "wall": occurrence.wall,
        "text": occurrence.what,
        "kind": occurrence.kind,
        "struck": occurrence.struck,
        "reason": occurrence.reason,
        "shift_text": _shift_text(occurrence.shift, occurrence.at, **wording),
    }


def build(
    occurrences,
    *,
    days=(),
    shown_zone=None,
    schedule_zone,
    now,
    limit=None,
    more=False,
    paused=None,
    layout="compact",
    empty=None,
    unavailable=None,
    verb="sends",
):
    """The context ``components/occurrences.html`` draws.

    ``occurrences`` are :class:`Occurrence` records in time order. A list
    with a ``limit`` is a count from now: an occurrence at or before
    ``now`` is dropped and the first ``limit`` are kept, with the "more"
    line when others were cut (or ``more`` says the feature cut some
    already). A window list passes one :class:`Day` per day of its window
    in ``days``, so a day with nothing on it is listed too, and keeps
    today's earlier times. With no ``days``, each day is made from its
    occurrences and its clock change worked out from ``shown_zone``.

    ``shown_zone`` (an IANA name) is the zone the list is grouped and shown
    in; without one the list is not grouped and never refuses: one
    day-less list of lines. ``unavailable`` (the words naming what is
    missing) gives the unavailable state, ``empty`` the words for a valid
    schedule with nothing ahead, ``paused`` a ``{"text", "url", "label"}``
    notice above the lines. ``verb`` is "sends" or "runs", for the words a
    shifted line and the more line use; a "runs" list (the refresh
    schedule's) never shows the general daylight-saving sentence, which
    speaks of sends, since its lines already say how each shifted time
    runs. See the ui-conventions spec,
    "Schedule previews", for the context's keys.
    """
    context = {
        "state": "shown",
        "layout": layout,
        "shown_zone": shown_zone,
        "schedule_zone": schedule_zone,
        "browser_zone": shown_zone is not None and shown_zone != schedule_zone,
        "grouped": shown_zone is not None,
        "paused": paused,
        "message": None,
        "days": [],
        "more": None,
        "dst_note": False,
    }
    if unavailable is not None:
        return context | {"state": "unavailable", "message": unavailable}
    found = list(occurrences)
    if limit is not None:
        found = [item for item in found if item.at is None or item.at > now]
        more = more or len(found) > limit
        found = found[:limit]
    if not found and not days:
        return context | {"state": "empty", "message": empty}
    wording = {
        "same_zone": shown_zone is not None and shown_zone == schedule_zone,
        "schedule_zone": schedule_zone,
        "verb": verb,
    }
    if more:
        count = limit or len(found)
        context["more"] = (
            ngettext(
                "Only the next %(count)d send is shown. Later sends follow the "
                "same pattern.",
                "Only the next %(count)d sends are shown. Later sends follow the "
                "same pattern.",
                count,
            )
            if verb == "sends"
            else ngettext(
                "Only the next %(count)d run is shown. Later runs follow the "
                "same pattern.",
                "Only the next %(count)d runs are shown. Later runs follow the "
                "same pattern.",
                count,
            )
        ) % {"count": count}
    # The general daylight-saving sentence speaks of sends, so only a
    # "sends" list carries it.
    shifted = verb == "sends" and any(item.shift for item in found)
    if shown_zone is None:
        # Zone-less: one list of lines, each a full browser-local instant.
        context["days"] = [
            {
                "date": None,
                "weekday": None,
                "today": False,
                "change": None,
                "summary": None,
                "open": True,
                "items": [_item(item, **wording) for item in found],
            }
        ]
        context["dst_note"] = shifted
        return context
    zone = ZoneInfo(shown_zone)
    today = now.astimezone(zone).date()
    if not days:
        days = [
            Day(value, change=_change(value, zone))
            for value in dict.fromkeys(item.day for item in found)
        ]
    by_day = {}
    for item in found:
        by_day.setdefault(item.day, []).append(_item(item, **wording))
    for day in days:
        context["days"].append(
            {
                "date": day.date,
                "weekday": WEEKDAYS[day.date.weekday()],
                "today": day.date == today,
                "change": day.change,
                "summary": day.summary,
                "open": day.date == today,
                **(day.extra or {}),
                "items": by_day.get(day.date, []),
            }
        )
    context["dst_note"] = shifted or (
        verb == "sends" and any(day.change for day in days)
    )
    return context
