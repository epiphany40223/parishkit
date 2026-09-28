"""The one parish-selected date format shared by pages, email, PDFs and exports.

An Admin picks one ``date_format`` code in Parish settings. Every server-side
date and timestamp goes through :func:`format_date` / :func:`format_instant`;
``date-format-v1.js`` mirrors the same table for ``data-local-instant``
elements, reading the code from ``<body data-date-format>``. Keep the two in
step: ``tests/stewardship/test_date_format.py`` pins the Python table and the
browser tests pin the JS one against the same examples.

US styles pair with a 12-hour clock; European and ISO styles with a 24-hour
clock. Each style has a compact variant for dense tables (logs, background
work, deliveries): long month names shorten and numeric years drop to two
digits, while ISO stays ISO.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

DEFAULT = "us_long"
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# code: (Admin label, full date pattern, compact date pattern). Patterns use
# {month} (full name), {mon} (abbreviation), {day}, {dd}, {mm}, {yyyy} and {yy}.
# The clock and the date/time separator follow from the code: see _clock().
_STYLES = {
    "us_long": ("January 1, 2027", "{month} {day}, {yyyy}", "{mon} {day}, {yyyy}"),
    "us_medium": ("Jan 1, 2027", "{mon} {day}, {yyyy}", "{mon} {day}, {yyyy}"),
    "us_numeric": ("01/01/2027 (month first)", "{mm}/{dd}/{yyyy}", "{mm}/{dd}/{yy}"),
    "eu_long": ("1 January 2027", "{day} {month} {yyyy}", "{day} {mon} {yyyy}"),
    "eu_medium": ("1 Jan 2027", "{day} {mon} {yyyy}", "{day} {mon} {yyyy}"),
    "eu_numeric": ("01/01/2027 (day first)", "{dd}/{mm}/{yyyy}", "{dd}/{mm}/{yy}"),
    "eu_dot": ("01.01.2027", "{dd}.{mm}.{yyyy}", "{dd}.{mm}.{yy}"),
    "iso": ("2027-01-01 (ISO 8601)", "{yyyy}-{mm}-{dd}", "{yyyy}-{mm}-{dd}"),
}
FORMATS = tuple(_STYLES)
CHOICES = tuple((code, style[0]) for code, style in _STYLES.items())

# A request (or worker job) may lend a zero-argument callable that returns
# the active code; it is only called when something actually formats a date,
# so JSON endpoints and redirects never pay for a configuration lookup.
_resolver = ContextVar("stewardship_date_format", default=None)


def use(resolver):
    """Install a lazy format resolver for this context; returns a reset token."""
    return _resolver.set(resolver)


def reset(token):
    """Undo :func:`use` so one request's choice never leaks into the next."""
    _resolver.reset(token)


def current():
    """The active format code, falling back to the default when none is known."""
    resolver = _resolver.get()
    return normalized(resolver() if resolver is not None else None)


def normalized(style):
    """Unset or unknown codes (older snapshots, bootstrap pages) mean the default."""
    return style if style in _STYLES else DEFAULT


def format_date(value, style=None, *, compact=False):
    """Render a calendar date in the chosen style, e.g. "January 5, 2027"."""
    if type(value) is not date:
        raise ValueError("Date formatting requires a calendar date.")
    entry = _STYLES[normalized(style) if style is not None else current()]
    return entry[2 if compact else 1].format(
        month=MONTHS[value.month - 1],
        mon=MONTHS[value.month - 1][:3],
        day=value.day,
        dd=f"{value.day:02d}",
        mm=f"{value.month:02d}",
        yyyy=f"{value.year:04d}",
        yy=f"{value.year % 100:02d}",
    )


def _clock(style):
    """(12-hour clock?, date/time separator) paired with a style code.

    Worded dates read as prose ("January 5, 2027 at 3:04 PM", "5 January
    2027, 15:04"); numeric dates are followed directly by the time.
    """
    worded = style.endswith(("_long", "_medium"))
    separator = (" at " if style.startswith("us_") else ", ") if worded else " "
    return style.startswith("us_"), separator


def format_time(value, style=None):
    """Render a wall-clock time with the style's paired 12- or 24-hour clock."""
    style = normalized(style) if style is not None else current()
    if _clock(style)[0]:
        meridiem = "AM" if value.hour < 12 else "PM"
        return f"{value.hour % 12 or 12}:{value.minute:02d} {meridiem}"
    return f"{value.hour:02d}:{value.minute:02d}"


def format_instant(value, timezone, style=None, *, compact=False):
    """Render an aware instant in ``timezone``, e.g. "January 5, 2027 at 3:04 PM EST".

    Compact timestamps (dense table cells) drop the time-zone abbreviation.
    """
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamp formatting requires a timezone-aware instant.")
    style = normalized(style) if style is not None else current()
    zone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    local = value.astimezone(zone)
    text = format_date(local.date(), style, compact=compact)
    if compact:
        return f"{text} {format_time(local, style)}"
    return f"{text}{_clock(style)[1]}{format_time(local, style)} {local.tzname()}"


@contextmanager
def using(style):
    """Format with one known parish style for the duration of a block.

    Exports pin the style of the configuration they were requested under, so
    a render never depends on a later settings change.
    """
    token = use(lambda: style)
    try:
        yield
    finally:
        reset(token)


def format_local(value, style=None, *, compact=False):
    """Render an aware datetime already converted to its display time zone."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamp formatting requires a timezone-aware instant.")
    return format_instant(value, value.tzinfo, style, compact=compact)


# Exports: CSV is ISO 8601 for every parish ("2027-01-31", and timestamps as
# "2027-01-31 14:05" in the export's stated display time zone), so files sort
# and import predictably. XLSX cells are native dates using Excel's built-in
# locale-aware formats, so each viewer's Excel shows its own regional style.
# PDFs, which people read rather than process, use the parish's choice.
XLSX_DATE = 14  # Excel's built-in short date
XLSX_DATETIME = 22  # Excel's built-in short date and time


class Span(NamedTuple):
    """An inclusive calendar period shown as one value, e.g. a comparison year."""

    start: date
    end: date


def csv_text(value):
    """ISO text for a date or display-zone timestamp; other values unchanged."""
    if isinstance(value, Span):
        return f"{value.start.isoformat()} through {value.end.isoformat()}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return value


def display_text(value, *, compact=False):
    """The parish's chosen format for a date or timestamp; others unchanged."""
    if isinstance(value, Span):
        return f"{format_date(value.start)} through {format_date(value.end)}"
    if isinstance(value, datetime):
        return format_local(value, compact=compact)
    if isinstance(value, date):
        return format_date(value, compact=compact)
    return value
