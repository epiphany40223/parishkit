"""Read a time of day typed in any common form (#631).

Admins type times the way they say them: ``2:00``, ``2pm``, ``0200``,
``2:30 PM``, ``14h30``, ``noon``. Every Admin field that takes a time of day
reads it here, through the form fields in ``web.time_entry_fields``, so the
web pages and the ``pk-stewardship admin`` commands that bind the same forms
accept exactly the same entries. The server is the authority: the page script
(``ui-v1.js``, ``window.ParishTimeEntry``) applies the same rules only to show
the reading live and to refuse an entry at the field before it is sent.

Keep the two in step: ``tests/stewardship/fixtures/time_entry_cases.json`` is
one table of entries, readings and refusals that both the Python unit tests
and the browser tests run, so the parsers cannot drift.

The rules, case-insensitive, with surrounding spaces ignored:

- 24-hour ``h:mm``/``hh:mm``, with ``.`` or ``h`` also accepted as the
  separator (``14.30``, ``14h30``); ``hh:mm:ss`` only when the seconds are
  ``00``, since these fields are minute-precision.
- A bare 1–2 digit number is an hour (``7`` is 07:00); a bare 3–4 digit
  number is 24-hour ``hmm``/``hhmm`` (``830`` is 08:30).
- Any of those (except with seconds) may carry ``am``/``pm``, ``a``/``p`` or
  ``a.m.``/``p.m.``, with or without a space; the hour must then be 1–12, and
  ``12 am`` is 00:00 and ``12 pm`` is 12:00.
- ``noon`` and ``midnight``.

Nothing is guessed: a bare ``7`` is 07:00, never 7 PM, and the reading
("07:00 (7:00 AM)") always shows both clocks so the Admin can see which.
"""

import re
from datetime import time

from django.utils.translation import gettext_lazy as _

# White space, spelled out: exactly JavaScript's \s, so both sides agree.
# Python's own \s and str.split() also take \x1c-\x1f and \x85.
WHITESPACE = " \t\n\v\f\r\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_SPACES = re.compile(f"[{WHITESPACE}]+")
# One entry: an hour, then optionally a separator and two-digit minutes (and,
# after a colon only, two-digit seconds), or 3–4 digits run together; then an
# optional am/pm suffix. ASCII digits only: Python's \d also matches other
# scripts' digits, which the browser's JavaScript \d would not.
_SUFFIX = f"(?:[{WHITESPACE}]*(?P<half>[ap])\\.?(?:m\\.?)?)?"
_CLOCK = re.compile(
    r"(?P<hour>[0-9]{1,2})"
    r"(?:(?P<sep>[:.h])(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2}))?)?" + _SUFFIX,
    re.IGNORECASE,
)
_RUN = re.compile(r"(?P<digits>[0-9]{3,4})" + _SUFFIX, re.IGNORECASE)
_WORDS = {"noon": time(12, 0), "midnight": time(0, 0)}
# A list's entries are separated by commas, semicolons or new lines, or by
# spaces; a suffix standing alone after a space ("2 pm") belongs to the entry
# before it, but not across a comma, semicolon or new line ("2, pm").
_BREAKS = re.compile(r"[,;\n\r\u2028\u2029]+")
_LONE_SUFFIX = re.compile(r"[ap]\.?(?:m\.?)?", re.IGNORECASE)

# The page script shows the same English messages (ui-v1.js); the fixture
# pins them on both sides.
MESSAGES = {
    "empty": _("Enter a time, for example 2:00 PM or 14:00."),
    "unreadable": _("“%(entry)s” isn't a time. Try 2:00 PM, 2pm, 14:00 or 1400."),
    "hour": _("“%(entry)s” has no hour %(hour)s: hours run from 0 to 23."),
    "minute": _("“%(entry)s” has no minute %(minute)s: minutes run from 00 to 59."),
    "twelve_hour": _("“%(entry)s”: with AM or PM the hour runs from 1 to 12."),
    "seconds": _(
        "“%(entry)s” has seconds: these times are to the minute, so "
        "leave the seconds out."
    ),
    "too_many": _("Enter at most %(count)s different times."),
}


class TimeEntryError(ValueError):
    """An entry that cannot be read as a time of day.

    ``code`` names the rule (a key of ``MESSAGES``) and ``params`` fill its
    message. A ``seconds`` refusal also carries the ``value`` it would have
    read, so a field can keep an unchanged saved time that has seconds.
    """

    def __init__(self, code, **params):
        """Keep the code and parameters; the message is resolved only when read.

        Resolving a translated message needs Django's settings, which a
        caller that only catches the error (or reads ``code``) need not have.
        """
        self.code = code
        self.params = params
        self.value = params.pop("value", None)
        super().__init__(code)

    @property
    def message(self):
        """The plain message shown at the field."""
        return str(MESSAGES[self.code]) % self.params

    def __str__(self):
        """The message, so the error reads as the field shows it."""
        return self.message


def _twelve_hour(entry, hour, half):
    """Convert a 1–12 hour with its am/pm half to a 0–23 hour."""
    if half is None:
        return hour
    if not 1 <= hour <= 12:
        raise TimeEntryError("twelve_hour", entry=entry)
    return hour % 12 + (12 if half.lower() == "p" else 0)


def parse_time(text, *, kept=None):
    """Read one typed time of day as a naive ``datetime.time``.

    Raises :class:`TimeEntryError` for an empty or unreadable entry or an
    impossible hour, minute or second. ``kept`` is a saved time that may have
    seconds (a mail schedule saved before #631): typed back unchanged, it is
    read as itself rather than refused.
    """
    entry = " ".join(part for part in _SPACES.split(str(text or "")) if part)
    if not entry:
        raise TimeEntryError("empty")
    lowered = entry.lower()
    if lowered in _WORDS:
        return _WORDS[lowered]
    match = _CLOCK.fullmatch(entry)
    if match:
        hour = int(match["hour"])
        minute = int(match["minute"] or 0)
        second = int(match["second"] or 0)
    elif match := _RUN.fullmatch(entry):
        digits = match["digits"]
        hour, minute, second = int(digits[:-2]), int(digits[-2:]), 0
    else:
        raise TimeEntryError("unreadable", entry=entry)
    half = match["half"]
    if match.groupdict().get("second") is not None and (
        half is not None or match["sep"] != ":"
    ):
        # Seconds belong to the 24-hour colon form only: "2:30:00 pm",
        # "14.30:00" and "14h30:00" are refused.
        raise TimeEntryError("unreadable", entry=entry)
    hour = _twelve_hour(entry, hour, half)
    if hour > 23:
        raise TimeEntryError("hour", entry=entry, hour=hour)
    if minute > 59:
        raise TimeEntryError("minute", entry=entry, minute=f"{minute:02d}")
    if second > 59:
        raise TimeEntryError("unreadable", entry=entry)
    value = time(hour, minute, second)
    if second and value != kept:
        raise TimeEntryError("seconds", entry=entry, value=value)
    return value


def is_blank(text):
    """Whether an entry holds nothing but white space."""
    return not _SPACES.sub("", str(text or ""))


def split_times(text):
    """Split a typed list into its entries, keeping "2 pm" together."""
    items = []
    for chunk in _BREAKS.split(str(text or "")):
        joinable = False
        for part in _SPACES.split(chunk):
            if not part:
                continue
            if joinable and _LONE_SUFFIX.fullmatch(part):
                items[-1] = f"{items[-1]} {part}"
            else:
                items.append(part)
            joinable = True
    return items


def parse_times(text):
    """Read a typed list of times, in the order typed, repeats included.

    Raises the first entry's :class:`TimeEntryError`. A list with no entries
    (blank, or only separators such as ",") is an empty result, which the
    field reads as its blank value.
    """
    return [parse_time(item) for item in split_times(text)]


def canonical(value):
    """The stored and displayed form: ``HH:MM``, or ``HH:MM:SS`` with seconds."""
    return value.isoformat(timespec="seconds" if value.second else "minutes")


def twelve_hour(value):
    """The same time on a 12-hour clock, for example ``7:00 AM``."""
    hour = value.hour % 12 or 12
    seconds = f":{value.second:02d}" if value.second else ""
    half = "PM" if value.hour >= 12 else "AM"
    return f"{hour}:{value.minute:02d}{seconds} {half}"


def reading(value):
    """How an entry was read, on both clocks: ``07:00 (7:00 AM)``."""
    return f"{canonical(value)} ({twelve_hour(value)})"


def duplicates(values):
    """The canonical times listed more than once, in time order."""
    seen, repeated = set(), set()
    for value in values:
        (repeated if value in seen else seen).add(value)
    return [canonical(value) for value in sorted(repeated)]
