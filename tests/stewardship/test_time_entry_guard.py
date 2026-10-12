"""Every Admin time of day uses the one shared time entry (#1117).

The shared code is ``FlexibleTimeField``/``FlexibleTimeListField``
(``web/time_entry_fields.py``), which draw a text box marked
``data-time-entry``, and ``ui-v1.js``, which gives every such field its
"Reads as …" line, refusal, blur rewrite and Save gate. A page that draws its
own time box, or its own reading, would drift from the others, as the Repeat
panel's Send time and the refresh schedule's Edit as text lists once did.

Like the button guard (test_button_guard.py), these are plain text scans, so
they cover branches no fixture reaches:

- no form uses Django's own time fields or widgets, and every form field
  whose name or label says "time" or "times" is a Flexible field;
- every hand-drawn ``<input>`` or ``<textarea>`` in a portal template that
  takes a time of day (its id, name or data hook, or its label, says "time"
  or "times"; or it is a native time control) is marked ``data-time-entry``;
- no page script writes a "Reads as" line except the shared code.
"""

import importlib
import re
from pathlib import Path

import pytest
from django import forms

from parishkit.stewardship.web.time_entry_fields import (
    FlexibleTimeField,
    FlexibleTimeListField,
)

ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "src/parishkit/stewardship"
TEMPLATES = PACKAGE / "accounts/templates/stewardship"
STATIC = PACKAGE / "accounts/static/stewardship"

# Django's own time fields and widgets: a native control, or no reading.
DJANGO_TIME = re.compile(
    r"\bforms\.(?:Split)?(?:Date)?TimeField\b"
    r"|\b(?:Split)?(?:Date)?Time(?:Input|Widget)\b"
    r"|\bSplitDateTimeWidget\b"
)
TEMPLATE_TAG = re.compile(r"{[{%#].*?[%}#]}", re.S)
# An <input> or <textarea> start tag; template tags inside it may hold ">".
CONTROL = re.compile(r"<(input|textarea)\b((?:{[{%#].*?[%}#]}|[^>])*)>", re.S)
ATTRIBUTE = re.compile(r'([\w:-]+)(?:="([^"]*)")?')
LABEL = re.compile(r'<label\b[^>]*\bfor="([^"]+)"[^>]*>(.*?)</label>', re.S)
# "time" or "times" as a word: not "timezone", "time zone" or "timeline".
TIME_WORD = re.compile(r"(?<![a-z])times?(?![a-z]|[\s_-]*zone)", re.I)
# Controls that never take a typed time of day.
NOT_TIME = {"hidden", "checkbox", "radio", "date", "submit", "button", "number"}
# A whole-line // comment in a script, which may quote a reading.
JS_COMMENT = re.compile(r"^\s*//.*$", re.M)


def script(path):
    """A page script's code, without its whole-line comments."""
    return JS_COMMENT.sub("", path.read_text(encoding="utf-8"))


def time_controls_without_entry(text):
    """Each time-of-day control in template ``text`` lacking data-time-entry.

    Returns ``(tag, id or name)`` pairs. A control takes a time of day when
    it is a native time control, or when its id, name, data-* hooks or label
    say "time"; then it must be a shared time entry field.
    """
    labels = {match[1]: match[2] for match in LABEL.finditer(text)}
    missing = []
    for match in CONTROL.finditer(text):
        attrs = {
            name: value or ""
            for name, value in ATTRIBUTE.findall(TEMPLATE_TAG.sub(" ", match[2]))
        }
        kind = attrs.get("type", "text")
        if kind in ("time", "datetime-local"):
            missing.append((match[1], attrs.get("id") or attrs.get("name", "")))
            continue
        if kind in NOT_TIME or "data-time-entry" in attrs:
            continue
        hooks = [attrs.get("id", ""), attrs.get("name", "")] + [
            value for name, value in attrs.items() if name.startswith("data-")
        ]
        label = labels.get(attrs.get("id", ""), "")
        if any(TIME_WORD.search(hook) for hook in hooks) or TIME_WORD.search(label):
            missing.append((match[1], attrs.get("id") or attrs.get("name", "")))
    return missing


def test_no_form_uses_a_django_time_field_or_widget():
    """Times are read by the Flexible fields, never Django's own."""
    found = [
        str(path.relative_to(ROOT))
        for path in sorted(PACKAGE.rglob("*.py"))
        if "migrations" not in path.parts
        and DJANGO_TIME.search(path.read_text(encoding="utf-8"))
    ]
    assert found == []


def stewardship_form_fields():
    """Every declared field of every stewardship form class.

    Imports each package module that mentions ``forms.`` (so every form
    class is defined), then walks Django's form class tree. Returns
    ``(class name, field name, field)`` triples.
    """
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts or "forms." not in path.read_text("utf-8"):
            continue
        parts = path.relative_to(PACKAGE).with_suffix("").parts
        importlib.import_module(".".join(("parishkit.stewardship", *parts)))
    found, pending = [], [forms.BaseForm]
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.__module__.startswith("parishkit.stewardship."):
            for name, field in getattr(cls, "base_fields", {}).items():
                found.append((cls.__qualname__, name, field))
    return found


def plain_time_field(name, field):
    """Whether form field ``name`` takes a time but is not a Flexible field."""
    says_time = TIME_WORD.search(name) or TIME_WORD.search(str(field.label or ""))
    shared = (FlexibleTimeField, FlexibleTimeListField, forms.DateField)
    return bool(says_time) and not isinstance(field, shared)


@pytest.mark.parametrize(
    ("name", "field", "plain"),
    [
        ("send_at", forms.CharField(label="Send time"), True),
        ("contact_time", forms.CharField(), True),
        ("times", forms.CharField(label="Refresh at"), True),
        ("send_time", FlexibleTimeField(), False),
        ("times", FlexibleTimeListField(), False),
        ("contact_date", forms.DateField(label="Date and time"), False),
        ("timezone", forms.CharField(label="Time zone"), False),
    ],
)
def test_the_form_guard_catches_a_plain_time_field(name, field, plain):
    """The check fails a CharField that takes a time, and nothing else."""
    assert plain_time_field(name, field) is plain


def test_every_form_time_field_is_a_flexible_field():
    """A form field named or labelled as a time reads like every other time.

    A plain CharField labelled "Send time" and drawn with {{ form.x }} would
    get no data-time-entry, so no reading. Date fields take no time of day.
    """
    fields = stewardship_form_fields()
    plain = [
        f"{owner}.{name}"
        for owner, name, field in fields
        if plain_time_field(name, field)
    ]
    assert plain == []
    # The walk is not vacuous: it reaches the mail schedule's Send time.
    assert ("ScheduleForm", "time") in {(owner, name) for owner, name, _ in fields}


@pytest.mark.parametrize(
    "path",
    sorted(TEMPLATES.rglob("*.html")),
    ids=lambda path: str(path.relative_to(TEMPLATES)),
)
def test_every_hand_drawn_time_control_is_a_shared_time_entry(path):
    """A template's own time box is marked data-time-entry."""
    assert time_controls_without_entry(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "markup",
    [
        # The Repeat panel's Send time before #1117: its label says so.
        '<label for="repeat-time">{% translate "Send time" %}</label>'
        '<input id="repeat-time" type="text" data-repeat="time">',
        # Edit as text before #1117: a list of times in a textarea.
        '<label for="t">{% translate "Full refresh times" %}</label>'
        '<textarea id="t" rows="2"></textarea>',
        # A name that says so, with a template tag inside the tag.
        '<input name="contact_time"{% if x > 1 %} aria-invalid="true"{% endif %}>',
        # A native time control, whatever it is called.
        '<input type="time" name="at">',
        '<input type="datetime-local" name="when">',
    ],
)
def test_the_guard_catches_a_time_control_without_the_shared_entry(markup):
    """The scan itself fails a new time box that skips data-time-entry."""
    assert time_controls_without_entry(markup)


@pytest.mark.parametrize(
    "markup",
    [
        '<input id="t" type="text" data-time-entry="time" name="send_time">',
        '<textarea id="t" data-time-entry="list" name="times"></textarea>',
        '<label for="z">Time zone</label><input id="z" name="timezone">',
        '<input type="date" name="contact_date" data-when="contact_time">',
        '<input type="checkbox" name="send_times_now">',
    ],
)
def test_the_guard_passes_shared_entries_and_other_controls(markup):
    """Shared entries, dates, zones and choices are not flagged."""
    assert time_controls_without_entry(markup) == []


def test_only_the_shared_time_entry_writes_a_reading():
    """No page script draws its own "Reads as" line (#1117).

    In ui-v1.js the words belong to readTimeField, the shared reading; any
    other script, or any other place in ui-v1.js, is a second copy.
    """
    shared = script(STATIC / "ui-v1.js")
    start = shared.index("const readTimeField = ")
    end = shared.index("\n  };\n", start)
    outside = shared[:start] + shared[end:]
    assert "Reads as" in shared[start:end]
    assert "Reads as" not in outside
    others = [
        path.name
        for path in sorted(STATIC.rglob("*.js"))
        if path.name != "ui-v1.js" and "Reads as" in script(path)
    ]
    assert others == []
    assert "data-repeat-reading" not in shared
