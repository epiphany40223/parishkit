"""Every export defaults to XLSX (#1190): forms, page fallbacks and pk-admin."""

import re
from pathlib import Path

from django.template import Context, Template

import parishkit.stewardship.accounts as accounts
from parishkit.stewardship import admin_cli
from parishkit.stewardship.web.exports import DEFAULT_EXPORT_FORMAT

TEMPLATES = Path(accounts.__file__).parent / "templates"
SELECT = re.compile(r'<select id="([^"]+)" name="format"[^>]*>(.*?)</select>', re.S)
# Format menus that keep their own first choice, with why:
# - comparison-format, ministry-packet-format: documents (a PDF summary and a
#   printable packet), not data, so PDF stays first.
OWN_DEFAULT = {"comparison-format", "ministry-packet-format"}


def test_the_default_is_xlsx_and_pk_admin_spells_out_the_same():
    """One rule, one value; the parser's copy cannot drift from the pages'."""
    assert DEFAULT_EXPORT_FORMAT == "xlsx"
    assert admin_cli.DEFAULT_EXPORT_FORMAT == DEFAULT_EXPORT_FORMAT


def test_every_export_format_menu_uses_the_shared_options():
    """A format menu renders its options through the one tag, so it
    preselects the default; only the recorded exceptions spell out their own.
    """
    menus = {
        menu_id: options
        for path in TEMPLATES.rglob("*.html")
        for menu_id, options in SELECT.findall(path.read_text())
    }
    assert len(menus) >= 12
    own = {
        menu_id
        for menu_id, options in menus.items()
        if "{% export_format_options " not in options
    }
    assert own == OWN_DEFAULT


def test_the_options_put_the_default_first_and_select_it():
    """XLSX first and selected; the rest keep their order and labels."""
    html = Template(
        '{% load stewardship %}{% export_format_options "csv" "xlsx" "jsonl" %}'
    ).render(Context())
    assert html == (
        '<option value="xlsx" selected>XLSX</option>'
        '<option value="csv">CSV</option>'
        '<option value="jsonl">JSON Lines</option>'
    )


def render_options(arguments, **context):
    """The tag's output for ``arguments`` (template syntax) in ``context``."""
    return Template(
        "{% load stewardship %}{% export_format_options " + arguments + " %}"
    ).render(Context(context))


def test_a_disabled_format_is_shown_but_unavailable():
    """A menu may offer a format it cannot take right now (a PDF above its
    row limit, #1179); empty disables none, and a disabled default is not
    selected so the browser picks the first available option."""
    assert render_options('"csv" "xlsx" "pdf" disabled=off', off="pdf") == (
        '<option value="xlsx" selected>XLSX</option>'
        '<option value="csv">CSV</option>'
        '<option value="pdf" disabled>PDF</option>'
    )
    assert render_options('"csv" "pdf" disabled=off', off="") == (
        '<option value="csv">CSV</option><option value="pdf">PDF</option>'
    )
    assert render_options('"csv" "xlsx" disabled="xlsx"') == (
        '<option value="xlsx" disabled>XLSX</option><option value="csv">CSV</option>'
    )


def test_the_options_escape_their_values():
    """Values come from templates, but the tag still escapes what it writes."""
    html = render_options("odd", odd='a"><script>')
    assert "<script>" not in html
    assert 'value="a&quot;&gt;&lt;script&gt;"' in html


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
    ):
        assert parse(*command).format == "xlsx"
        assert parse(*command, "--format", "csv").format == "csv"
