"""``export limitations``'s own pieces: its choices and options (#463).

The page's CSV, XLSX and PDF, the admission and the event are the page's
own, proven against the page in
``database/test_admin_limitations_export_cli_postgresql.py``; these tests
cover the shared choice check and the command's parser.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.reports import limitation_views
from parishkit.stewardship.reports.limitations import SHOW_WORDS


def parse(*argv):
    """``export limitations`` parsed by pk-admin's own parser."""
    return admin_cli.build_parser().parse_args(
        ["export", "limitations", *argv, "--config", "web.yaml", "--session-stdin"]
    )


def test_the_command_offers_exactly_the_pages_choices():
    """The parser's formats and Show words are the page's own, so the two
    cannot drift: the parser is built before Django, so it lists them."""
    entry = {
        spec.name: spec for spec in admin_cli.COMMANDS if spec.name.startswith("export")
    }["export limitations"]
    assert entry.streams
    args = parse()
    assert (args.show, args.format, args.timezone) == ("any", "xlsx", "UTC")
    for fmt in limitation_views.FORMATS:
        assert parse("--format", fmt).format == fmt
    for word in SHOW_WORDS:
        assert parse("--show", word).show == word
    for refused in (("--format", "jsonl"), ("--show", "talents"), ("--search", "x")):
        with pytest.raises(admin_cli.UsageError):
            parse(*refused)


def test_the_choice_check_is_the_pages():
    """A known format and UTC or a supported zone pass; nothing else does."""
    assert limitation_views.export_choice("csv", "UTC") == {
        "format": "csv",
        "zone": ZoneInfo("UTC"),
    }
    chosen = limitation_views.export_choice("pdf", "America/New_York")
    assert chosen["zone"] == ZoneInfo("America/New_York")
    for fmt, zone in (("jsonl", "UTC"), ("csv", "Mars/Olympus"), ("csv", "")):
        with pytest.raises(ValueError):
            limitation_views.export_choice(fmt, zone)


def test_the_file_name_is_the_pages():
    """Stamped with the UTC instant, as the page's download was named."""
    moment = datetime(2054, 10, 3, 14, 5, 6, tzinfo=UTC)
    assert (
        limitation_views.export_file_name("xlsx", moment)
        == "stewardship-limitations-20541003-140506Z.xlsx"
    )
