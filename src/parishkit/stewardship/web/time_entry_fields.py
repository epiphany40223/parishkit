"""Form fields for a time of day typed in any common form (#631).

Both read entries with ``parishkit.stewardship.time_entry``, the one parser
the web pages and the ``pk-stewardship admin`` commands share. Each renders a
plain text box marked ``data-time-entry`` that ``ui-v1.js`` enhances: it
shows the reading live beside the field ("Reads as 14:00 (2:00 PM)"), shows a
refusal at the field, normalises the entry when focus leaves it, and keeps
the form's Save unavailable while an entry cannot be read. The server still
reads every entry itself.

The fields only read a wall-clock time; which time zone it belongs to is the
form's own rule (a mail schedule's campaign zone, the parish zone for
ParishSoft refresh times).
"""

import re
from datetime import time

from django import forms

from parishkit.stewardship.time_entry import (
    TimeEntryError,
    canonical,
    is_blank,
    parse_time,
    parse_times,
)


def _invalid(error):
    """The field-level ValidationError for one parser refusal."""
    return forms.ValidationError(error.message, code=error.code)


# A saved time's ISO text, as configuration stores it ("09:00:00", "02:00").
_ISO = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?")


def _as_time(value):
    """A saved value (a ``time`` or its ISO text) as a ``time``, or None."""
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


class TimeEntryInput(forms.TextInput):
    """A text box sized for a time, without autocomplete or spellcheck."""

    def __init__(self, attrs=None):
        """Mark the box for ui-v1.js's time entry, keeping any caller attrs."""
        super().__init__(
            {
                "autocomplete": "off",
                "spellcheck": "false",
                "autocapitalize": "off",
            }
            | (attrs or {})
        )


class _TypedText(forms.CharField):
    """A text box whose length bounds apply to the typed text.

    CharField runs ``max_length`` on the cleaned value, which here is a time
    or a list of times; the bounds are checked on the entry instead.
    """

    def __init__(self, **kwargs):
        """Move CharField's length validators onto the typed text.

        CharField's own stripping is off: the parser trims the white space
        that the page script trims too (``time_entry.WHITESPACE``), and no
        other, so both read an entry alike.
        """
        kwargs.setdefault("strip", False)
        super().__init__(**kwargs)
        self.text_validators, self.validators = self.validators, []

    def typed(self, value):
        """The entry after its length checks; None when it is blank."""
        text = super().to_python(value)
        for validator in self.text_validators:
            validator(text)
        return None if is_blank(text) else text


class FlexibleTimeField(_TypedText):
    """One time of day, cleaned to a naive ``datetime.time``.

    ``kept`` (set with :meth:`keep`) is a saved time with seconds, which
    these minute-precision fields otherwise refuse: an unchanged saved
    schedule must still save. Empty is ``None`` (``required`` decides).
    """

    widget = TimeEntryInput

    def __init__(self, **kwargs):
        """A short text box; the parser bounds what it accepts."""
        kwargs.setdefault("max_length", 32)
        super().__init__(**kwargs)
        self.kept = None
        self.widget.attrs["data-time-entry"] = "time"

    def keep(self, value):
        """Accept the saved ``value`` unchanged even when it has seconds."""
        kept = _as_time(value)
        self.kept = kept if kept is not None and kept.second else None
        if self.kept is not None:
            self.widget.attrs["data-time-kept"] = canonical(self.kept)
        else:
            self.widget.attrs.pop("data-time-kept", None)

    def prepare_value(self, value):
        """Show a saved time canonically; a posted entry is shown as typed.

        Only a ``time`` or ISO text (a saved value) is converted; an entry
        such as "2pm", or a refused "25:00", stays exactly as typed so the
        Admin can see and fix it.
        """
        if isinstance(value, str) and not _ISO.fullmatch(value):
            return value
        saved = _as_time(value)
        return value if saved is None else canonical(saved)

    def to_python(self, value):
        """Read the entry; empty is None."""
        text = self.typed(value)
        if not text:
            return None
        try:
            return parse_time(text, kept=self.kept)
        except TimeEntryError as error:
            raise _invalid(error) from None

    def has_changed(self, initial, data):
        """Compare times, not text: "9am" for a saved 09:00:00 is no change."""
        try:
            value = self.to_python(data)
        except forms.ValidationError:
            return True
        return _as_time(initial) != value


class FlexibleTimeListField(_TypedText):
    """Several times of day, cleaned to a sorted list of unique ``HH:MM`` texts.

    Entries may be separated by commas, semicolons, spaces or new lines. A
    time listed twice is saved once (the page script reports the repeat as
    it is typed). ``blank`` is the list an empty entry stands for, and
    ``max_times`` bounds the number of different times.
    """

    widget = TimeEntryInput

    def __init__(self, *, blank=(), max_times=None, **kwargs):
        """Mark the box as a list for the page script, with its blank and bound."""
        super().__init__(**kwargs)
        self.blank = list(blank)
        self.max_times = max_times
        self.widget.attrs["data-time-entry"] = "list"
        if self.blank:
            self.widget.attrs["data-time-blank"] = ", ".join(self.blank)
        if max_times is not None:
            self.widget.attrs["data-time-max"] = str(max_times)

    def prepare_value(self, value):
        """Show a saved list as text; a posted entry is shown as typed."""
        if isinstance(value, list | tuple):
            return ", ".join(value)
        return value

    def to_python(self, value):
        """Read every entry; empty is the ``blank`` list."""
        text = self.typed(value)
        try:
            values = parse_times(text)
        except TimeEntryError as error:
            raise _invalid(error) from None
        if not values:
            # Blank, or only separators such as ",": the stated blank list.
            return list(self.blank)
        distinct = sorted({canonical(value) for value in values})
        if self.max_times is not None and len(distinct) > self.max_times:
            error = TimeEntryError("too_many", count=self.max_times)
            raise _invalid(error)
        return distinct
