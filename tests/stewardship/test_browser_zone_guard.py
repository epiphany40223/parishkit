"""Admin pages show and take times in the browser's zone: the guard (#558).

The rule is in ui-conventions/spec.md#dates-and-times. The page-by-page #558
PRs moved each screen; these plain text scans keep a new field or display
from slipping back, including branches no fixture renders:

- every hand-drawn date control in an Admin template carries the browser
  zone (``data-browser-zone-field``, with a ``data-browser-zone`` field on the
  page), or is a recorded calendar date or a recorded pending page;
- every form date field is classified the same way;
- every machine instant a template prints (``|instant``, ``|date:'c'``,
  directly or through a ``{% with %}`` or ``{% blocktranslate with %}``
  name) is either an attribute value for script or the text of a ``<time>``
  that script localizes;
- only recorded modules format an instant on the server: a call to
  ``format_instant``/``parish_instant``, or a date, time or text formatter
  applied to an ``.astimezone(...)`` result;
- no Admin template or script shows "UTC" or "GMT" except the recorded
  export choices; no label, legend, ``<th>`` or ``<summary>`` names a fixed
  zone in parentheses ("(UTC)", "(parish date)", "(campaign zone)",
  "(Eastern)"); and no form field's label, help or error says "UTC" or
  "GMT", nor, on a date or time field, the campaign's or parish's zone.

Limits: the scans read each file's own text, so a heading drawn by a
template tag (``sort_heading``) or wording built in a view is not checked.

Each allow-list says why its entry is allowed. An entry that no longer
matches anything fails too, so the lists cannot go stale.
"""

import re
from pathlib import Path

import pytest
from django import forms

from parishkit.stewardship.web.time_entry_fields import (
    FlexibleTimeField,
    FlexibleTimeListField,
)

from .test_admin_javascript_gate import FAMILY_PAGES
from .test_time_entry_guard import stewardship_form_fields

PACKAGE = Path(__file__).parents[2] / "src/parishkit/stewardship"
TEMPLATES = PACKAGE / "accounts/templates/stewardship"
STATIC = PACKAGE / "accounts/static/stewardship"

# --- Allow-lists -----------------------------------------------------------

# Hand-drawn date controls that need no browser zone, by (template, id).
CALENDAR_INPUTS = {
    # The repeat rule's day range (both mail repeat panels draw it from
    # components/repeat-rule.html): whole days that are expanded into send
    # dates of the zoned schedule form around it, never instants.
    ("repeat-rule.html", "repeat-from"): "Repeat range day",
    ("repeat-rule.html", "repeat-until"): "Repeat range day",
    # A repeating report email's chosen From and Until days (#1786): the
    # campaign's calendar days its rule's bounds name, never instants.
    ("report-email-entry.html", "report-from_date"): "Report repeat bound day",
    ("report-email-entry.html", "report-until_date"): "Report repeat bound day",
}
# Date controls that still filter instants by a campaign-zone day, waiting
# for their own #558 slice. Remove an entry when its page moves. Empty since
# the Financial stewardship response dates moved (migration 0110).
PENDING_INPUTS = {}
# Labels and legends that still name a fixed zone, by template: the
# PENDING_INPUTS fieldsets' legends.
PENDING_LABELS = {}
# Form date fields, by (form class, field): a pure calendar date (never
# shifted, ui-conventions "A pure date with no time stays a calendar date"),
# or a date read with the browser zone the form posts.
FORM_DATES = {
    **{
        (owner, field): "calendar: campaign window and giving periods"
        for owner in ("CampaignForm", "CampaignSettingsForm")
        for field in (
            "start_date",
            "end_date",
            "financial_start",
            "financial_end",
            "comparison_start",
            "comparison_end",
        )
    },
    ("LiveEndDateForm", "end_date"): "calendar: live campaign end",
    ("ScheduleWindow", "start_date"): "calendar: campaign start",
    ("ScheduleWindow", "end_date"): "calendar: campaign end",
    # The Repeat rule's day range (see CALENDAR_INPUTS).
    ("RepeatRule", "start"): "calendar: Repeat range",
    ("RepeatRule", "until"): "calendar: Repeat range",
    # Edit's Change the repeat (#1808, #2235): the same rule's fields.
    ("SeriesRule", "start"): "calendar: Repeat range",
    ("SeriesRule", "until"): "calendar: Repeat range",
    # A send date: read with its time in the browser zone where the formset
    # opts in (schedule_local, browser_times); the retired Copy campaign page
    # keeps campaign-local values (ui-conventions, "Until a page's PR lands").
    ("ScheduleForm", "date"): "browser zone: send date",
    # A report email (#1786): its one-time send date is read with its time
    # in the browser zone; its repeat bounds are campaign calendar days.
    ("EditorForm", "once_date"): "browser zone: report send date",
    ("EditorForm", "from_date"): "calendar: report repeat bound",
    ("EditorForm", "until_date"): "calendar: report repeat bound",
}
# Modules that format an instant as text on the server in a fixed zone.
SERVER_INSTANTS = {
    # Emails name the parish or campaign zone; they cannot know a browser.
    "jobs/receipt_content.py": "email",
    "jobs/operational_content.py": "email",
    "reports/daily_digest.py": "email",
    # The emailed weekly report's requests block (its Admin snapshot page
    # shows it as sent).
    "reports/report_blocks/additional_information.py": "email",
    # The Family portal's last-submitted line (campaign zone, named).
    "responses/presentation.py": "Family portal",
    # The shared helper behind responses/presentation.py.
    "web/presentation.py": "shared helper",
    # Report files in the zone the reader chose (browser_timezone).
    "reports/participation.py": "export in the chosen zone",
    # The participation chart buckets by campaign time until its slice
    # (ui-conventions, "the participation chart still buckets by campaign
    # time").
    "reports/chart_specs.py": "#558 participation chart",
    # Export stamps ("Downloaded …") in the zone the reader chose.
    "reports/census_changes.py": "export in the chosen zone",
    "reports/limitations.py": "export in the chosen zone",
    # The roster export stamp is in the chosen zone.
    "reports/ministry_roster_views.py": "export in the chosen zone",
    # Pledges' PDF (#1686), in the zone the download names.
    "reports/pledge_review_views.py": "export in the chosen zone",
    # The schedule preview's daylight-saving words name the time a shifted
    # send really happens only when the list is shown in its schedule zone,
    # which is then the browser's (ui-conventions "Schedule previews", #1129).
    "occurrence_preview.py": "shown zone is the schedule zone",
}
# Form fields whose label, help or errors name a fixed zone, by (form class,
# field).
FORM_ZONE_TEXT = {
    # The default help, shown only on the retired Copy campaign page; every
    # browser-mode page replaces it with BROWSER_TIME_HELP.
    ("ScheduleForm", "time"): "Copy campaign page only",
}
# Visible "UTC" text allowed in Admin templates, matched exactly.
# None since no export asks for a time zone (#1679): System logs' "Times in"
# menu offers UTC as an option of the shared export row, not as text.
UTC_TEXT = set()

# Script lines that say "UTC" but show nothing, by (script, code fragment).
SCRIPT_UTC = {
    # The zone test treats a browser reporting a UTC alias as UTC.
    ("ui-v1.js", "(UTC|UCT|Universal|Zulu)"): "zone-name test",
}

# --- Scanners ---------------------------------------------------------------

COMMENTS = re.compile(
    r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}|<!--.*?-->", re.S
)
TEMPLATE_TAG = re.compile(r"{%.*?%}", re.S)
TRANSLATE = re.compile(r"""{%\s*(?:translate|trans)\s+(["'])(.*?)\1.*?%}""", re.S)
CONTROL = re.compile(r"<input\b((?:{[{%#].*?[%}#]}|[^>])*)>", re.S | re.I)
# One attribute: double-quoted, single-quoted, unquoted or bare.
ATTRIBUTE = re.compile(
    r"""([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?"""
)
DATE_TYPES = {"date", "datetime-local", "month", "week"}
# The filters that print a machine instant (ISO 8601 UTC text).
INSTANT_FILTER = r"""\|(?:instant\b|date:(["'])c\1)"""
# A machine instant printed by a template, whatever filters follow.
INSTANT = re.compile(r"{{[^}]*?" + INSTANT_FILTER + r"[^}]*}}")
# A block that binds names, and its body: {% with %} and blocktranslate.
BINDING_BLOCK = r"{%%\s*(%s)\b(.*?)%%}(.*?){%%\s*end\1\s*%%}"
BINDING_KINDS = ("blocktranslate", "blocktrans", "with")
# A <time> whose text script replaces: date-format-v1.js writes the
# browser-local form; live-status-v1.js writes "12 seconds ago" for a running
# time, which names no zone. The Admin portal requires JavaScript (#565).
LOCALIZED = re.compile(r"\bdata-(?:local-instant|live-since)\b")
LABEL = re.compile(r"<(label|legend|th|summary)\b[^>]*>(.*?)</\1>", re.S | re.I)
FIXED_ZONE = re.compile(
    r"\([^)]*\b(?:UTC|GMT|Eastern|Central|Mountain|Pacific"
    r"|campaign(?:'s)? (?:time ?)?zone"
    r"|parish(?:'s)? (?:date|time|(?:time ?)?zone))\b[^)]*\)",
    re.I,
)
UTC_WORD = re.compile(r"\b(?:UTC|GMT)\b")
# On a date or time field: a fixed zone named in words.
ZONE_WORDS = re.compile(
    r"\b(?:UTC|GMT)\b|\b(?:Eastern|Central|Mountain|Pacific) time\b"
    r"|\b(?:campaign|parish)(?:'s)? (?:time ?)?zone\b",
    re.I,
)
# Block comments and // comments that start a line or follow whitespace (so
# "https://" in a string stays).
JS_COMMENT = re.compile(r"/\*.*?\*/|(?:^|(?<=\s))//[^\n]*", re.S | re.M)
# "UTC" as a word, not Date.UTC() or getUTCDate(); a quoted "UTC" alone is a
# zone value (a menu choice or a comparison), not wording.
JS_UTC = re.compile(r"(?<![.\w])(?:UTC|GMT)(?!\w)")
JS_ZONE_VALUE = re.compile(r"""(["'])UTC\1""")
# Formatting an instant as text on the server: the instant formatters, or a
# date, time or text formatter applied to an .astimezone(...) result, such
# as format_date(moment.astimezone(zone).date()). One level of nested
# parentheses is skipped, so the match stays inside the formatter's call.
SERVER_FORMAT = re.compile(
    r"\b(?:format_instant|parish_instant)\("
    r"|\b(?:format_date|format_time|format_local|display_text|time_text)\("
    r"(?:[^()]|\([^()]*\)){0,200}?\.astimezone\("
)


def admin_templates():
    """Every Admin template (Family pages follow the Family rules)."""
    return [
        path
        for path in sorted(TEMPLATES.rglob("*.html"))
        if path.name not in FAMILY_PAGES
    ]


def blank(pattern, text):
    """``text`` with each ``pattern`` match replaced by spaces, keeping offsets."""
    return pattern.sub(lambda match: " " * len(match[0]), text)


def attributes(markup):
    """The attributes of a start tag's inside, names lowercased.

    Template tags are blanked first, since they may hold ">" or quotes.
    """
    found = ATTRIBUTE.findall(TEMPLATE_TAG.sub(" ", markup))
    return {name.lower(): dq or sq or bare for name, dq, sq, bare in found}


def date_controls(text):
    """``(id, zoned)`` for each date control hand-drawn in template ``text``.

    ``zoned`` says the control carries ``data-browser-zone-field`` and the
    page has the hidden ``data-browser-zone`` field the script fills.
    """
    text = COMMENTS.sub("", text)
    zone_field = re.compile(r"\bdata-browser-zone(?=[\s>=])", re.I)
    page_zone = zone_field.search(text) is not None
    found = []
    for match in CONTROL.finditer(text):
        attrs = attributes(match[1])
        if attrs.get("type", "").lower() in DATE_TYPES:
            zoned = "data-browser-zone-field" in attrs and page_zone
            found.append((attrs.get("id") or attrs.get("name", ""), zoned))
    return found


def expand_bindings(text):
    """``text`` with each name bound to an instant replaced by the instant.

    For example ``{% blocktranslate with when=x|instant trimmed %}ended
    {{ when }}{% endblocktranslate %}`` becomes ``… ended {{ x|instant }}``,
    so the scan sees where the instant is printed. Both binding spellings
    (``name=value`` and ``value as name``), several bindings and options
    such as ``trimmed`` or ``count`` are read. Each kind is expanded in its
    own pass, so a blocktranslate inside a ``{% with %}`` is handled too.
    """

    def expand(block):
        """One block, its instant-bound names replaced in its body."""
        args, body = block[2], block[3]
        bound = re.findall(r"(\w+)=(\S+)", args) + [
            (name, value) for value, name in re.findall(r"(\S+)\s+as\s+(\w+)", args)
        ]
        for name, value in bound:
            if re.search(INSTANT_FILTER, value):
                use = r"{{\s*" + re.escape(name) + r"(?![\w.])[^}]*}}"
                body = re.sub(use, lambda _, v=value: "{{ " + v + " }}", body)
        head = block[0][: block.start(3) - block.start()]
        tail = block[0][block.end(3) - block.start() :]
        return head + body + tail

    for kind in BINDING_KINDS:
        text = re.sub(BINDING_BLOCK % kind, expand, text, flags=re.S)
    return text


def unlocalized_instants(text):
    """Each machine instant ``text`` prints outside a tag and a local <time>.

    An instant inside a start tag is an attribute value (``datetime=``, a
    ``data-*`` hook) that script reads. As element text it must be inside a
    ``<time>`` whose text script replaces (``LOCALIZED``); anywhere else it
    would show raw ISO UTC text.
    """
    text = expand_bindings(COMMENTS.sub("", text))
    # Template tags may hold ">" (comparisons); blank them, keeping offsets.
    code = blank(TEMPLATE_TAG, text)
    found = []
    for match in INSTANT.finditer(code):
        start = match.start()
        if code.rfind("<", 0, start) > code.rfind(">", 0, start):
            continue  # inside a start tag: an attribute value
        opened = code.rfind("<time", 0, start)
        closed = code.rfind("</time>", 0, start)
        tag = code[opened : code.find(">", opened) + 1] if opened > closed else ""
        if not LOCALIZED.search(tag):
            found.append(match[0])
    return found


def visible_text(text):
    """Template ``text`` as a reader sees it: translated strings and markup.

    Comments and template code go, a translate tag becomes its string, and
    an export menu's ``<option value="UTC">`` choice goes (it is a zone the
    reader picks for a file). Attribute values stay: hints and tips show.
    """
    text = COMMENTS.sub("", text)
    text = TRANSLATE.sub(lambda match: match[2], text)
    text = TEMPLATE_TAG.sub(" ", text)
    text = re.sub(r"{{.*?}}", " ", text)
    return re.sub(r'<option value="UTC">UTC</option>', " ", text)


def utc_text(text):
    """Each visible sentence or attribute of ``text`` that says "UTC"."""
    shown = visible_text(text)
    for allowed in UTC_TEXT:
        shown = shown.replace(allowed, " ")
    return [
        line.strip()[:120]
        for line in re.split(r"[\n<>]", shown)
        if UTC_WORD.search(line)
    ]


def fixed_zone_labels(text):
    """Each label, legend, ``<th>`` or ``<summary>`` in ``text`` that names a
    fixed zone, such as "Date (UTC)" or "Submitted on or after (parish
    date)"."""
    return [
        body.strip()
        for _, body in LABEL.findall(visible_text(text))
        if FIXED_ZONE.search(body)
    ]


def form_zone_text(owner, name, field):
    """Whether a form field's label, help or errors name a fixed zone.

    Any field saying "UTC" or "GMT" counts; a date or time field also may
    not ask the reader to think in the campaign's or the parish's zone.
    """
    texts = " ".join(
        str(text)
        for text in (field.label, field.help_text, *field.error_messages.values())
        if text
    )
    timed = (owner, name) in FORM_DATES or isinstance(
        field, (FlexibleTimeField, FlexibleTimeListField)
    )
    return bool((ZONE_WORDS if timed else UTC_WORD).search(texts))


def script_utc_lines(name, text):
    """Each code line of page script ``name`` that says "UTC" in words.

    Comments go first (they explain UTC arithmetic); a line holding a
    recorded SCRIPT_UTC fragment is allowed. Scanning lines rather than
    string literals keeps regular expression literals from confusing it.
    """
    allowed = [fragment for script, fragment in SCRIPT_UTC if script == name]
    return [
        line.strip()
        for line in JS_COMMENT.sub("", text).splitlines()
        if JS_UTC.search(JS_ZONE_VALUE.sub("", line))
        and not any(fragment in line for fragment in allowed)
    ]


# --- Self-tests of the scanners --------------------------------------------


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        # A day filter with no browser zone (the follow-up before #567).
        ('<input id="d" type="date" name="contact_date">', [("d", False)]),
        ('<input type="datetime-local" name="when">', [("when", False)]),
        # The field marker alone is not enough: the page needs the zone field.
        ('<input id="d" type="date" data-browser-zone-field>', [("d", False)]),
        (
            '<input type="hidden" name="zone" data-browser-zone>'
            '<input id="d" type="date"{% if x > 1 %} required{% endif %}'
            " data-browser-zone-field>",
            [("d", True)],
        ),
        ('<input id="t" name="contact_time" data-time-entry="time">', []),
        # Other attribute spellings.
        ("<input id='a' type='date'>", [("a", False)]),
        ("<input id=b type=date>", [("b", False)]),
        ('<INPUT ID="c" TYPE="Date">', [("c", False)]),
        ('{# <input type="date" id="x"> #}', []),
    ],
)
def test_the_date_control_scan(markup, expected):
    """The scan finds date controls and tells zoned ones from bare ones."""
    assert date_controls(markup) == expected


@pytest.mark.parametrize(
    ("markup", "flagged"),
    [
        # Raw ISO text in a sentence, a cell, or a plain <time>.
        ("<p>As of {{ x|instant }}</p>", 1),
        ("<td>{{ x.sent_at|date:'c' }}</td>", 1),
        ('<p>{{ x|date:"c" }}</p>', 1),
        ("<p>{{ x|instant|default:'-' }}</p>", 1),
        (
            "{% blocktranslate with when=x.y|instant trimmed %}ended {{ when }}"
            "{% endblocktranslate %}",
            1,
        ),
        (
            "{% blocktranslate with a=b when=x|instant %}{{ a }} ended {{ when }}"
            "{% endblocktranslate %}",
            1,
        ),
        ("{% with when=x|instant %}<p>{{ when }}</p>{% endwith %}", 1),
        ("{% with x|instant as when %}<p>{{ when }}</p>{% endwith %}", 1),
        ('<time datetime="{{ x|instant }}">{{ x|instant }}</time>', 1),
        (
            "{% blocktranslate with when=x|instant %}ended {{ when }}"
            "{% endblocktranslate %}",
            1,
        ),
        # Localized text, attributes for script, and a bound localized use.
        (
            '<time datetime="{{ x|instant }}" data-local-instant>'
            "{{ x|instant }}</time>",
            0,
        ),
        (
            "<div data-live-since=\"{{ x|date:'c' }}\""
            "{% if a > b %} hidden{% endif %}>",
            0,
        ),
        (
            "{% blocktranslate with when=x|instant %}ended <time data-local-instant"
            ' datetime="{{ when }}">{{ when }}</time>{% endblocktranslate %}',
            0,
        ),
        # A name bound to something else, and a name that only starts alike.
        ("{% with when=x.label %}<p>{{ when }}</p>{% endwith %}", 0),
        ("{% with when=x|instant %}<p>{{ when_label }}</p>{% endwith %}", 0),
    ],
)
def test_the_instant_scan(markup, flagged):
    """The scan fails raw instants and passes localized ones."""
    assert len(unlocalized_instants(markup)) == flagged


@pytest.mark.parametrize(
    "markup",
    [
        '<label for="d">{% translate "Date (UTC)" %}</label>',
        '<p>{% translate "Times use your computer\'s time zone, otherwise UTC." %}</p>',
        '<p class="help" data-missing-hint="Dates are UTC days.">x</p>',
        "<p>Times are UTC.</p>",
        "<p>Times are GMT.</p>",
    ],
)
def test_the_utc_scan_catches_utc_wording(markup):
    """A label, sentence, hint or plain text saying "UTC" is caught."""
    assert utc_text(markup)


def test_the_utc_scan_passes_zone_values_and_template_code():
    """An export's UTC choice and template code are not wording."""
    markup = (
        '<option value="UTC">UTC</option>'
        "{% for zone in zones %}<option{% if zone == 'UTC' %} selected{% endif %}>"
        "{{ zone }}</option>{% endfor %}"
        "{# times are UTC here #}"
    )
    assert utc_text(markup) == []


@pytest.mark.parametrize(
    ("markup", "flagged"),
    [
        (
            "<legend>"
            '{% translate "First response date (campaign timezone)" %}</legend>',
            1,
        ),
        (
            '<label for="s">'
            '{% translate "Submitted on or after (parish date)" %}</label>',
            1,
        ),
        ('<label for="t">Time (UTC)</label>', 1),
        ('<label for="t">Send time (campaign zone)</label>', 1),
        ("<legend>Send time (Eastern)</legend>", 1),
        ('<th scope="col">Sent (UTC)</th>', 1),
        ("<summary>Times (parish time zone)</summary>", 1),
        # The campaign's own time zone setting is not a fixed-zone entry.
        ('<label for="z">{% translate "Campaign timezone" %}</label>', 0),
        ('<label for="d">{% translate "Send date" %}</label>', 0),
    ],
)
def test_the_label_scan(markup, flagged):
    """Labels, legends and headings naming a fixed zone are caught."""
    assert len(fixed_zone_labels(markup)) == flagged


def test_the_script_scan():
    """Script text saying "UTC" is caught; zone values, Date.UTC and comments
    pass."""
    code = (
        '// Times shown in UTC\n/* UTC */ if (zone === "UTC") {}\n'
        "const day = new Date(Date.UTC(2026, 0, 1)).getUTCDate();\n"
        "const re = /^(Etc\\/)?(UTC|UCT|Universal|Zulu)$/;\n"
        'note.textContent = "Times are in UTC";\n'
        "label.textContent = `${day} UTC`;\n"
        "const step = 1; // times are UTC\n"
        'const url = "https://example.org/";\n'
    )
    assert script_utc_lines("ui-v1.js", code) == [
        'note.textContent = "Times are in UTC";',
        "label.textContent = `${day} UTC`;",
    ]


@pytest.mark.parametrize(
    ("code", "flagged"),
    [
        ("stamp = dates.format_instant(at, zone)", True),
        ("line = parish_instant(at, zone)", True),
        # resolved_on(): a day formatted from an instant converted to a zone.
        (
            "return dates.format_date(moment.astimezone(ZoneInfo(zone)).date())",
            True,
        ),
        ("text = format_local(\n    row.at.astimezone(zone), compact=True\n)", True),
        # Converting for arithmetic, or formatting a calendar date, is not.
        ("day = now.astimezone(ZoneInfo(zone)).date()", False),
        ("shown = dates.format_date(campaign.start_date)", False),
        ("x = format_date(day)\ny = now.astimezone(zone)", False),
    ],
)
def test_the_server_format_scan(code, flagged):
    """The scan finds an instant formatted as text in a fixed zone."""
    assert bool(SERVER_FORMAT.search(code)) is flagged


@pytest.mark.parametrize(
    ("owner", "name", "field", "flagged"),
    [
        ("F", "send_time", FlexibleTimeField(label="Send time (UTC)"), True),
        ("F", "note", forms.CharField(help_text="Stored as GMT."), True),
        (
            "F",
            "send_time",
            FlexibleTimeField(help_text="In the campaign's time zone."),
            True,
        ),
        ("CampaignForm", "start_date", forms.DateField(help_text="Eastern time"), True),
        # The campaign's own time zone setting names it, and is not a time.
        ("F", "timezone", forms.CharField(label="Campaign timezone"), False),
        (
            "F",
            "send_time",
            FlexibleTimeField(help_text="In this computer's time zone."),
            False,
        ),
    ],
)
def test_the_form_wording_scan(owner, name, field, flagged):
    """Form wording naming a fixed zone is caught; zone settings are not."""
    assert form_zone_text(owner, name, field) is flagged


# --- The guards -------------------------------------------------------------


def test_every_admin_date_control_takes_the_browser_zone():
    """A hand-drawn date control is zoned, a calendar date, or pending.

    A new day filter without the browser zone fails here: add the zone field
    (ui-conventions "Dates and times"), or record why the date is a pure
    calendar date in CALENDAR_INPUTS.
    """
    bare, unzoned, zoned_seen = [], set(), set()
    for path in admin_templates():
        for control, zoned in date_controls(path.read_text(encoding="utf-8")):
            key = (path.name, control)
            (zoned_seen if zoned else unzoned).add(key)
            if not zoned and key not in CALENDAR_INPUTS | PENDING_INPUTS:
                bare.append(key)
    assert bare == []
    # An entry whose control is gone, or now zoned, is stale: remove it.
    assert set(CALENDAR_INPUTS) | set(PENDING_INPUTS) <= unzoned, "stale entries"
    # The scan is not vacuous: it reaches a zoned filter.
    assert ("logs.html", "log-start") in zoned_seen


def test_every_form_date_field_is_classified():
    """Each form date field is a recorded calendar date or zoned date."""
    found = {
        (owner, name)
        for owner, name, field in stewardship_form_fields()
        if isinstance(
            field, (forms.DateField, forms.DateTimeField, forms.SplitDateTimeField)
        )
        or getattr(field.widget, "input_type", "") in DATE_TYPES
    }
    assert found - set(FORM_DATES) == set()
    assert set(FORM_DATES) - found == set(), "stale entries"


@pytest.mark.parametrize(
    "path", admin_templates(), ids=lambda path: str(path.relative_to(TEMPLATES))
)
def test_every_shown_instant_is_localized(path):
    """Instants a template prints are localized by date-format-v1.js."""
    assert unlocalized_instants(path.read_text(encoding="utf-8")) == []


def test_form_wording_names_no_fixed_zone():
    """No form field's label, help or errors name a fixed zone, except the
    recorded FORM_ZONE_TEXT fields."""
    found = {
        (owner, name)
        for owner, name, field in stewardship_form_fields()
        if form_zone_text(owner, name, field)
    }
    assert found == set(FORM_ZONE_TEXT)


def test_only_recorded_modules_format_instants_on_the_server():
    """An instant formatted as text on the server is in a fixed zone, which an
    Admin page must not show; only emails, Family text, chosen-zone exports
    and the recorded pending displays do it."""
    callers = {
        str(path.relative_to(PACKAGE))
        for path in sorted(PACKAGE.rglob("*.py"))
        if "migrations" not in path.parts
        and path.name != "dates.py"
        and SERVER_FORMAT.search(path.read_text("utf-8"))
    }
    assert callers == set(SERVER_INSTANTS)


@pytest.mark.parametrize(
    "path", admin_templates(), ids=lambda path: str(path.relative_to(TEMPLATES))
)
def test_admin_templates_show_no_utc_or_fixed_zone_labels(path):
    """No Admin page says "UTC" or labels a field with a fixed zone."""
    text = path.read_text(encoding="utf-8")
    assert utc_text(text) == []
    assert fixed_zone_labels(text) == PENDING_LABELS.get(path.name, [])


def test_utc_allow_list_is_current():
    """Each allowed "UTC" sentence is still shown on some Admin page."""
    shown = " ".join(
        visible_text(path.read_text(encoding="utf-8")) for path in admin_templates()
    )
    assert [sentence for sentence in UTC_TEXT if sentence not in shown] == []


@pytest.mark.parametrize("path", sorted(STATIC.glob("*.js")), ids=lambda p: p.name)
def test_admin_scripts_show_no_utc(path):
    """No page script writes "UTC" into a page."""
    assert script_utc_lines(path.name, path.read_text(encoding="utf-8")) == []


def test_script_allow_list_is_current():
    """Each recorded script fragment still exists."""
    for script, fragment in SCRIPT_UTC:
        assert fragment in (STATIC / script).read_text(encoding="utf-8")
