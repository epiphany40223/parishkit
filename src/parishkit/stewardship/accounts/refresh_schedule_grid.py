"""The refresh schedule preview's seven-day grid (#632 slice 4).

The settings page draws the next seven days as a read-only grid: one row per
local day of the refresh schedule's time zone and 96 quarter-hour cells
(see the admin-portal spec, "Seven-day preview"). A cell carries:

- the refreshes that run in it, full and quick (a kept off-quarter-hour time
  is marked in the cell it falls in);
- its shading: the Family email window that overlaps it, or "gap" for a
  wall time that does not exist because clocks go forward;
- whether it holds the current time.

Everything is computed from the same ``SchedulePreview`` the per-day list
uses, so the grid and the list never disagree. The grid is a picture of the
list, not a second source: the list stays the keyboard and screen-reader
equivalent, and the template hides the grid's cells from assistive
technology.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.wall_clock import hhmm

# Cells per day row: one per quarter hour of the wall clock.
CELLS = 96
QUARTER = timedelta(minutes=15)
# How a shaded cell is labelled in the legend and on hover.
SHADE_LABELS = {
    "reminder_preparing": _("Reminder: preparing"),
    "reminder_sending": _("Reminder: sending"),
    "initial_sending": _("Invitation: sending"),
    "gap": _("Clocks go forward: this time does not exist"),
}


def _cell_start(index):
    """The wall time cell ``index`` starts at, as a ``time``.

    Only the grid numbers its cells; ``wall_clock.hhmm`` writes the result as
    ``HH:MM`` text, so this is not a second ``HH:MM`` helper (#1129).
    """
    return time(index // 4, index % 4 * 15)


def _spans(day, index, zone):
    """The UTC instants the wall cell ``index`` of ``day`` starts at.

    One instant on an ordinary day, two in the hour repeated when clocks go
    back, none in the hour that does not exist when clocks go forward. Each
    instant starts a quarter hour of real time the cell stands for.
    """
    wall = datetime.combine(day, _cell_start(index))
    found = {wall.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)}
    return sorted(
        instant
        for instant in found
        if instant.astimezone(zone).replace(tzinfo=None) == wall
    )


def _shade(spans, windows):
    """The cell's shading: "gap", the cause of a window overlapping it, or None.

    ``windows`` are sorted earliest-starting first (``grid_rows`` sorts them
    once), so, like ``send_windows.window_cause``, the earliest-starting
    window wins when several overlap the cell.
    """
    if not spans:
        return "gap"
    for window in windows:
        if any(
            window.start < start + QUARTER and window.end > start for start in spans
        ):
            return window.cause
    return None


def _cell_index(instant, day, zone):
    """The cell of ``day`` an instant falls in, or None on another day."""
    local = instant.astimezone(zone)
    if local.date() != day:
        return None
    return (local.hour * 60 + local.minute) // 15


def _title(index, cell, repeated):
    """What hovering over a marked or shaded cell says.

    ``repeated`` says the quarter hour happens twice because clocks go back:
    the cell then stands for both, and the words say so.
    """
    times = {
        "start": hhmm(_cell_start(index)),
        "end": hhmm(_cell_start((index + 1) % CELLS)),
    }
    if repeated:
        words = [gettext("%(start)s to %(end)s, both times (clocks go back)") % times]
    else:
        words = [gettext("%(start)s to %(end)s") % times]
    if cell["full"]:
        words.append(gettext("full refresh"))
    if cell["quick"]:
        words.append(gettext("quick update"))
    if cell["shade"]:
        words.append(str(SHADE_LABELS[cell["shade"]]))
    if cell["now"]:
        words.append(gettext("now"))
    return ", ".join(words)


def grid_rows(preview, *, timezone, now):
    """The grid's rows for a ``SchedulePreview``: one dict per preview day.

    Each row has ``day``, ``change`` (as the list's day) and ``cells``: 96
    dicts with ``full`` and ``quick`` (a refresh of that kind runs in the
    cell), ``shade`` (a ``SHADE_LABELS`` key or None), ``now`` and ``title``
    (empty for a blank cell, so the page stays small).
    """
    zone = ZoneInfo(timezone)
    windows = sorted(preview.windows, key=lambda item: (item.start, item.cause))
    rows = []
    for day in preview.days:
        cells, repeated = [], set()
        for index in range(CELLS):
            spans = _spans(day.day, index, zone)
            if len(spans) > 1:
                repeated.add(index)
            cells.append(
                {
                    "full": False,
                    "quick": False,
                    "shade": _shade(spans, windows),
                    "now": any(start <= now < start + QUARTER for start in spans),
                }
            )
        for entry in day.times:
            if entry.status != "runs" or entry.due_at is None:
                continue
            index = _cell_index(entry.due_at, day.day, zone)
            if index is not None:
                cells[index][entry.kind] = True
        for index, cell in enumerate(cells):
            marked = cell["full"] or cell["quick"] or cell["shade"] or cell["now"]
            cell["title"] = _title(index, cell, index in repeated) if marked else ""
        rows.append({"day": day.day, "change": day.change, "cells": cells})
    return rows


def legend(rows):
    """The shadings the grid shows, in a fixed order, as (key, label) pairs."""
    shown = {cell["shade"] for row in rows for cell in row["cells"]}
    return [(key, label) for key, label in SHADE_LABELS.items() if key in shown]


# Hour labels across the top: every third hour, each spanning 12 cells.
HOURS = tuple(f"{hour:02d}:00" for hour in range(0, 24, 3))
