"""Every Admin export is the shared row of export buttons (#1679).

The ui-conventions "Downloaded files" rule (Export actions, Time zones):
each export form draws its formats with the ``export_actions`` tag, one
small "Export XLSX" … button per format, never its own format menu or
Download button; no export asks for a time zone, except on a page that lets
the reader view its times in UTC (System logs); and no row has an
explanatory line on its count, scope or columns (R37). Like the button guard, the
template checks are a plain text scan, so they cover branches no fixture
reaches; a new export form fails until it is listed in EXPORTS.
"""

import re
from pathlib import Path

import pytest
from django.template import Context, Template, TemplateSyntaxError

import parishkit.stewardship.accounts as accounts

TEMPLATES = Path(accounts.__file__).parent / "templates" / "stewardship"
COMPONENT = "components/export-actions.html"

# Every Admin export form, by template and form action: the formats its row
# offers, in its tag's order (the tag puts XLSX first unless own_order).
EXPORTS = {
    ("campaign-comparison.html", "admin:campaign_comparison_export"): "pdf xlsx csv",
    ("census-changes.html", "admin:census_changes_export"): "csv xlsx pdf",
    ("directory.html", "admin:family_directory_export"): "csv xlsx pdf",
    ("timeline.html", "export.url"): "csv xlsx pdf",
    ("financial-report.html", "admin:financial_export"): "csv xlsx pdf",
    ("pledges-export.html", "admin:pledges_to_enter_export"): "csv xlsx pdf",
    ("information-worklist-export.html", "admin:information_export"): "csv xlsx pdf",
    ("limitations-worklist-export.html", "admin:limitations_export"): "csv xlsx pdf",
    ("logs.html", "admin:logs_export"): "csv xlsx jsonl",
    ("ministry-followup.html", "admin:ministry_followup_export"): "csv xlsx pdf",
    ("ministry-packet.html", "admin:ministry_packet"): "pdf xlsx csv",
    ("ministry-roster.html", "admin:ministry_roster_export"): "csv xlsx pdf",
    ("response-dashboard.html", "admin:report_export_create"): "csv xlsx pdf",
}
# The pages that let the reader view times in UTC, so their export keeps a
# time zone choice (preselected to the page's zone).
ZONE_CHOICE = {("logs.html", "admin:logs_export")}
# Not export rows: one ready file or archive each, with no choice of format
# (ui-conventions "Downloaded files", Export actions, "Not covered").
SINGLE_FILE = {
    ("report-export.html", "admin:report_export_download"),
    ("past-campaigns.html", "admin:past_campaign_archive"),
}

COMMENT = re.compile(r"{% comment %}.*?{% endcomment %}|{#.*?#}", re.S)
FORM = re.compile(r"<form\b.*?</form>", re.S)
ACTION = re.compile(r'action="(?:\{%\s*url\s*\'|\{\{\s*)([^\' }]+)')
TAG = re.compile(r"{% export_actions ((?:\"\w+\" )+)")
# A format or zone the template draws itself rather than through the row.
OWN_FORMAT = re.compile(r'name="format"|>Export (?:XLSX|CSV|PDF)<')
ZONE_MENU = re.compile(r'<select\b[^>]*\bname="(?:browser_)?timezone"')
# The retired explanatory lines above an export row (pk-local R37): its
# count ("Exports all 7 estimated matching Families, every page."), its scope
# ("The file holds the list these choices show") and its column list.
EXPLAINS = re.compile(
    r"Exports (?:the|all) |The file holds (?:the|all|both)|has these columns|"
    r"-export-(?:count|columns)\b"
)


def template_forms():
    """Every template form: {(template, action): body}, comments removed."""
    forms = {}
    for path in sorted(TEMPLATES.rglob("*.html")):
        name = str(path.relative_to(TEMPLATES))
        for body in FORM.findall(COMMENT.sub("", path.read_text())):
            action = ACTION.search(body)
            if action:
                forms.setdefault((name, action.group(1)), body)
    return forms


def test_every_export_form_uses_the_shared_row():
    """A form posting a format draws it with the tag, and is listed."""
    exports = {
        key
        for key, body in template_forms().items()
        if "{% export_actions " in body or OWN_FORMAT.search(body)
    }
    assert exports == set(EXPORTS)
    for key, formats in EXPORTS.items():
        body = template_forms()[key]
        assert not OWN_FORMAT.search(body), key
        expected = [f'"{value}"' for value in formats.split()]
        calls = [match.split() for match in TAG.findall(body)]
        assert calls and all(call == expected for call in calls), key


def test_no_template_draws_its_own_format_choice():
    """No format menu, format button or zone menu outside the shared row."""
    for path in TEMPLATES.rglob("*.html"):
        if str(path.relative_to(TEMPLATES)) == COMPONENT:
            continue
        text = COMMENT.sub("", path.read_text())
        assert not OWN_FORMAT.search(text), path.name
        assert "data-information-timezone" not in text, path.name
        assert "export_format_options" not in text, path.name
        forms = [body for body in FORM.findall(text) if "{% export_actions " in body]
        assert not any(ZONE_MENU.search(body) for body in forms), path.name


def test_no_export_row_explains_itself():
    """No export page draws a count, scope or column-list line (R37)."""
    for name in sorted({name for name, _ in EXPORTS}):
        text = COMMENT.sub("", (TEMPLATES / name).read_text())
        assert not EXPLAINS.search(text), name


def test_only_the_utc_pages_have_a_zone_choice():
    """System logs alone keeps "Times in"; every other row posts the zone."""
    for key, body in template_forms().items():
        if key in EXPORTS:
            assert ("zone_choice=True" in body) is (key in ZONE_CHOICE), key
    choosing = [
        path.name
        for path in TEMPLATES.rglob("*.html")
        if "zone_choice=True" in path.read_text()
    ]
    assert choosing == ["logs.html"]


def test_the_single_file_downloads_are_not_export_rows():
    """A ready file and the archive keep their one Download button."""
    forms = template_forms()
    for key in SINGLE_FILE:
        assert key in forms and "{% export_actions " not in forms[key], key


def render(arguments, **context):
    """The tag's output for ``arguments`` (template syntax) in ``context``."""
    return Template(
        "{% load stewardship %}{% export_actions " + arguments + " %}"
    ).render(Context(context))


def buttons(html):
    """(value, label, disabled) of each export button, in order."""
    return [
        (
            re.search(r'value="(\w+)"', attributes).group(1),
            label,
            " disabled" in attributes,
        )
        for attributes, label in re.findall(r"<button([^>]*)>([^<]*)</button>", html)
        if 'name="format"' in attributes
    ]


def test_the_row_puts_xlsx_first_and_labels_each_format():
    """One small secondary submit button per format, XLSX first (#1190)."""
    html = render('"csv" "xlsx" "jsonl" name="log"')
    assert buttons(html) == [
        ("xlsx", "Export XLSX", False),
        ("csv", "Export CSV", False),
        ("jsonl", "Export JSON Lines", False),
    ]
    assert html.count('type="submit" class="button-secondary button-small"') == 3
    assert '<div class="export-actions" role="group" aria-label="Export">' in html
    assert 'id="log-export-buttons"' in html and "data-table-sync" not in html


def test_a_document_keeps_its_own_order():
    """The PDF summary and the packet are documents: PDF stays first."""
    html = render('"pdf" "xlsx" "csv" name="packet" own_order=True')
    assert [value for value, _label, _off in buttons(html)] == ["pdf", "xlsx", "csv"]


def test_a_format_or_the_row_can_be_unavailable_with_its_reason():
    """``disabled`` greys one format, ``enabled=False`` them all (#563)."""
    html = render(
        '"csv" "xlsx" "pdf" name="f" disabled=off describedby="f-count"', off="pdf"
    )
    assert buttons(html) == [
        ("xlsx", "Export XLSX", False),
        ("csv", "Export CSV", False),
        ("pdf", "Export PDF", True),
    ]
    assert html.count('aria-describedby="f-count"') == 3
    assert "data-server-disabled" in html
    html = render('"csv" "pdf" name="f" enabled=held describedby=""', held=False)
    assert [off for _value, _label, off in buttons(html)] == [True, True]
    assert "aria-describedby" not in html


def test_the_row_posts_the_browsers_zone_unasked():
    """No zone menu: a hidden browser zone, or the page's drawn zone."""
    html = render('"csv" "xlsx" name="f"')
    assert '<input type="hidden" name="browser_timezone" data-browser-zone>' in html
    assert not ZONE_MENU.search(html) and 'name="timezone"' not in html
    html = render('"csv" "xlsx" name="f" zone_value=zone', zone="Asia/Tokyo")
    assert '<input type="hidden" name="browser_timezone" value="Asia/Tokyo">' in html
    assert "data-browser-zone" not in html


def test_a_utc_page_keeps_its_times_in_choice():
    """System logs' "Times in": UTC, plus the browser's zone ui-v1.js adds."""
    html = render('"csv" "xlsx" "jsonl" name="log" zone_choice=True')
    assert "browser_timezone" not in html
    assert (
        '<label for="log-export-timezone">Times in</label><select '
        'id="log-export-timezone" name="timezone" data-browser-timezone>'
        '<option value="UTC">UTC</option></select>'
    ) in html
    # The menu follows the buttons, so its growing never moves them.
    assert html.index("export-buttons") < html.index("export-zone")


def test_the_tip_sync_and_label_options():
    """The row's toggletip, its sync node and its group name."""
    html = render(
        '"csv" "pdf" name="q" label="Export these requests" tip=tip'
        " tip_more=more sync=True",
        tip="Holds every page.",
        more="Has codes.",
    )
    assert 'aria-label="Export these requests"' in html
    assert 'id="q-export-buttons" data-table-sync' in html
    assert 'id="q-export-tip" hidden>Holds every page. Has codes.</span>' in html


def test_the_row_escapes_what_it_writes():
    """Values come from templates, but the tag still escapes them."""
    html = render('"csv" name="f" label=odd zone_value=odd', odd='a"><script>')
    assert "<script>" not in html and "a&quot;&gt;&lt;script&gt;" in html


@pytest.mark.parametrize(
    "arguments", ['name="f"', '"csv" "csv" name="f"', '"csv" name="f" disabled="pdf"']
)
def test_a_malformed_row_is_refused(arguments):
    """No formats, a repeated one or an unknown disabled one fails loudly."""
    with pytest.raises(TemplateSyntaxError):
        render(arguments)
