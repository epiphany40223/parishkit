"""Shared wall-clock primitives for every Stewardship schedule (#1129).

Each scheduling feature (mail schedules and digests, the ParishSoft refresh
schedule, the campaign calendar) once defined its own weekday names and its
own ``HH:MM`` helpers. They live here instead, so every editor names the days
and writes the times the same way; see the "Schedules" section of
``docs/specs/stewardship/ui-conventions/spec.md``.

This module has no Django settings dependency (the weekday names are lazy
translations), so the background scheduler's modules can import it too.
"""

import re
from zoneinfo import ZoneInfo

from django.utils.translation import gettext_lazy as _

# The days of the week, Monday first, so ``WEEKDAYS[d.weekday()]`` names a
# date's day. Day pickers list them in this order; a month grid that starts on
# Sunday reorders them itself.
WEEKDAYS = (
    _("Monday"),
    _("Tuesday"),
    _("Wednesday"),
    _("Thursday"),
    _("Friday"),
    _("Saturday"),
    _("Sunday"),
)

# A canonical stored wall time: two-digit 24-hour hour and minute.
HHMM_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


def is_hhmm(value):
    """Whether ``value`` is a canonical local ``HH:MM`` wall time string."""
    return type(value) is str and HHMM_PATTERN.fullmatch(value) is not None


def hhmm(value):
    """A ``time`` or ``datetime`` as ``HH:MM`` text, or None for None."""
    return None if value is None else value.strftime("%H:%M")


def wall_hhmm(instant, timezone):
    """An aware instant's ``HH:MM`` wall time in the IANA zone ``timezone``."""
    return hhmm(instant.astimezone(ZoneInfo(timezone)))


def weekday_choices():
    """Form choices for a day-of-week picker: ``(index, name)``, Monday first.

    The index is ``date.weekday()``'s, as every schedule stores it.
    """
    return [(index, day) for index, day in enumerate(WEEKDAYS)]
