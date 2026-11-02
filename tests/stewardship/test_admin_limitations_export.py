"""``export limitations``'s own pieces: its choices and options (#463).

The page's CSV, XLSX and PDF, the admission and the event are the page's
own, proven against the page in
``database/test_admin_limitations_export_cli_postgresql.py``; these tests
cover the shared choice check and the command's parser.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.admin_worklists import LIMITATION_STAGES
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
    # The download's Stage is the page's, every stage by default (#1735).
    assert args.stage == "all"
    assert set(LIMITATION_STAGES) == limitation_views.FILE_STAGES
    for stage in LIMITATION_STAGES:
        assert parse("--stage", stage).stage == stage
    # The page's formats, and the command's own JSON Lines (#1735).
    for fmt in (*limitation_views.FORMATS, "jsonl"):
        assert parse("--format", fmt).format == fmt
    for word in SHOW_WORDS:
        assert parse("--show", word).show == word
    for refused in (
        ("--format", "json"),
        ("--show", "talents"),
        ("--search", "x"),
        ("--stage", "history"),
    ):
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


def test_the_download_says_a_family_left_the_campaign(monkeypatch):
    """Every format reads the in-campaign flag of every table's rows in one
    read, so both tables' ParishSoft status cells say "No longer in this
    campaign" where the page draws that badge (#2331)."""
    placement = SimpleNamespace(stage=SimpleNamespace(label="To review"))
    common = {
        "family_name": "Example, Pat",
        "family_duid": 1234,
        "submitted_at": datetime(2054, 10, 3, 14, 5, tzinfo=UTC),
        "placement": placement,
    }
    member = common | {"member_name": "Pat", "proposed": False, "member_duid": 5}
    family = common | {"kind": "family"}
    giving = common | {"kind": "cannot_give"}
    read = []

    def left(campaign_id, rows):
        """A ``link_families`` stand-in: every Family has left the campaign."""
        read.append((campaign_id, len(rows)))
        for each in rows:
            each["family_active"] = False

    monkeypatch.setattr(limitation_views, "link_families", left)
    result = {"members": [member], "families": [family], "contributions": [giving]}
    lines = limitation_views.export_body(7, result, "csv", ZoneInfo("UTC"))
    lines = lines.decode().splitlines()
    assert read == [(7, 3)]
    found = [line for line in lines if line.startswith(("Pat,", '"Example, Pat"'))]
    assert len(found) == 3
    assert all("No longer in this campaign" in line for line in found)
