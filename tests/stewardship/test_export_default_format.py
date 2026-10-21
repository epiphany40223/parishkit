"""Every export defaults to XLSX (#1190): forms, page fallbacks and pk-admin."""

import re
from pathlib import Path

from django.template import Context, Template

import parishkit.stewardship.accounts as accounts
from parishkit.stewardship import admin_cli
from parishkit.stewardship.web.exports import DEFAULT_EXPORT_FORMAT

TEMPLATES = Path(accounts.__file__).parent / "templates"
# Each export row's name and arguments (the export_actions tag, #1679).
ROW = re.compile(r'{% export_actions [^%]*?name="([^"]+)"([^%]*)%}')
# Rows that keep their own first choice, with why:
# - comparison, ministry-packet: documents (a PDF summary and a printable
#   packet), not data, so PDF stays first.
OWN_DEFAULT = {"comparison", "ministry-packet"}


def test_the_default_is_xlsx_and_pk_admin_spells_out_the_same():
    """One rule, one value; the parser's copy cannot drift from the pages'."""
    assert DEFAULT_EXPORT_FORMAT == "xlsx"
    assert admin_cli.DEFAULT_EXPORT_FORMAT == DEFAULT_EXPORT_FORMAT


def test_every_export_row_puts_the_default_first():
    """Every row lets the tag order its buttons, so XLSX comes first; only
    the recorded documents keep their own order (own_order).
    """
    rows = [
        (name, arguments)
        for path in TEMPLATES.rglob("*.html")
        for name, arguments in ROW.findall(path.read_text())
    ]
    assert len(rows) >= 12
    own = {name for name, arguments in rows if "own_order=True" in arguments}
    assert own == OWN_DEFAULT


def test_the_row_puts_the_default_first():
    """XLSX first; the rest keep their order (test_export_actions.py has the
    row's other rules)."""
    html = Template(
        '{% load stewardship %}{% export_actions "csv" "xlsx" "jsonl" name="x" %}'
    ).render(Context())
    assert re.findall(r'value="(\w+)"', html) == ["xlsx", "csv", "jsonl"]


def parse(*argv):
    """``argv`` parsed by pk-admin's own parser, with the session options."""
    return admin_cli.build_parser().parse_args(
        [*argv, "--config", "web.yaml", "--session-stdin"]
    )


def test_pk_admin_file_commands_default_to_xlsx_and_still_take_csv():
    """The file commands write XLSX unless told otherwise."""
    for command in (
        ("logs", "export"),
        ("export", "census"),
        ("export", "limitations"),
        ("export", "roster"),
        ("followup", "export"),
    ):
        assert parse(*command).format == "xlsx"
        assert parse(*command, "--format", "csv").format == "csv"


def test_no_template_explains_how_formats_write_dates_or_money():
    """An export is its row of export buttons only (#1538, #1679).

    The shared "Dates in CSV ..." help is gone from every Admin export form;
    the rules live in the ui-conventions spec, not on the page.
    """
    assert not (TEMPLATES / "stewardship" / "export-dates-help.html").exists()
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text()
        assert "export-dates-help" not in text, path
        assert "Dates in CSV" not in text, path
