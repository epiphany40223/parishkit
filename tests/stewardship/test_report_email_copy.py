"""A report send's saved copy, drawn from its snapshot's kept data (#1946).

The report-emails spec ("Saved copies") draws each block through its saved
copy renderer from the data the snapshot kept, never current figures. These
cover the renderers and the page's statements about the send without a
database; the PostgreSQL suite covers the page itself.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from parishkit.stewardship.reports.chart_specs import (
    PARTICIPATION_CHART_KEYS,
    participation_chart,
)
from parishkit.stewardship.reports.digest_presentation import saved_total_tiles
from parishkit.stewardship.reports.report_blocks import REGISTRY
from parishkit.stewardship.reports.report_blocks.registry import CopyFrame
from parishkit.stewardship.reports.report_email_copy import (
    CopyFacts,
    copy_blocks,
    recipients_by_kind,
)
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.stat_tiles import UNKNOWN

from .test_participation_rendering import document as participation

TEMPLATES = Path(__file__).parents[2] / "src/parishkit/stewardship/accounts/templates"
SEND = "/admin/mail/report-emails/sends/5d1e0c63-55a5-4b8f-9d36-1d8f0fdf2a11/"
RESPONDED = "Families that have responded"
FIRST = "First submissions that day"
PLEDGES = "Cumulative annual pledges (USD)"


class Snapshot:
    """The snapshot fields ``copy_blocks`` reads."""

    def __init__(self, blocks):
        """Keep ``blocks`` as a snapshot keeps them."""
        self.blocks = blocks


def test_every_block_has_a_saved_copy_renderer_and_template():
    """The page reads only the registry: each block draws its own template."""
    for block in REGISTRY.values():
        assert callable(block.copy), block.key
        assert (TEMPLATES / block.copy_template).is_file(), block.copy_template


def test_totals_keep_the_emails_words_split_into_tiles():
    """The count is the value; "out of N (P%)" its line, with a share bar."""
    tiles = saved_total_tiles(
        [
            [RESPONDED, "10 out of 93 (10.8%)", 10, 93, ""],
            [FIRST, "4", 4, 7, " (busiest day: 7)"],
            [PLEDGES, "$1,234.50", None, None, ""],
        ]
    )
    responded, first, pledges = tiles
    assert (responded.text, responded.details, responded.bar) == (
        "10",
        ("out of 93 (10.8%)",),
        (10, 93),
    )
    assert (first.text, first.details) == ("4", ("Busiest day: 7",))
    assert (pledges.text, pledges.unknown_reason) == ("$1,234.50", "")


def test_an_unavailable_total_reads_a_dash_with_its_reason():
    """No data that day, or (pledges only) an unknown pledge amount."""
    no_data = saved_total_tiles(
        [
            [RESPONDED, "Unavailable", None, 93, ""],
            [FIRST, "Unavailable", None, 0, ""],
            [PLEDGES, "Unavailable", None, None, ""],
        ]
    )
    assert all(tile.text == UNKNOWN for tile in no_data)
    assert {tile.unknown_reason for tile in no_data} == {
        "The ParishSoft data for this day was not available, so this figure is "
        "not known."
    }
    _responded, _first, pledges = saved_total_tiles(
        [
            [RESPONDED, "10 out of 93 (10.8%)", 10, 93, ""],
            [FIRST, "4", 4, 4, " (busiest day: 4)"],
            [PLEDGES, "Unavailable", None, None, ""],
        ]
    )
    assert pledges.text == UNKNOWN
    assert pledges.unknown_reason.startswith("The total is added up only")


def test_recipients_by_kind_hide_outside_addresses_from_staff():
    """Users by name, groups with their reach, outside addresses to Admins."""
    cohort = [
        {"address": "a@example.org", "kind": "group", "ref": "administrators"},
        {"address": "b@example.org", "kind": "group", "ref": "administrators"},
        {"address": "c@example.org", "kind": "user", "ref": "u1"},
        {"address": "d@example.org", "kind": "user", "ref": "gone"},
        {"address": "z@example.net", "kind": "outside", "ref": None},
        {"address": "y@example.net", "kind": "outside", "ref": None},
    ]
    names = {"u1": "Ann Smith"}
    admin = recipients_by_kind(cohort, names, administrator=True)
    assert admin == {
        "users": ["a removed user", "Ann Smith"],
        "groups": [{"name": "All Administrators", "people": 2}],
        "outside": ["y@example.net", "z@example.net"],
        "outside_count": 2,
    }
    staff = recipients_by_kind(cohort, names, administrator=False)
    assert staff["outside"] is None and staff["outside_count"] == 2
    assert "z@example.net" not in repr(staff)


def test_the_as_of_line_names_the_report_day_or_the_capture_moment():
    """A report day is a calendar date; a capture moment a UTC instant in text."""
    made = datetime(2054, 10, 9, 11, 0, tzinfo=UTC)
    day = CopyFacts("R — d", date(2054, 10, 8), made, made, "scheduled", "", "x", {})
    assert day.as_of_line == "All data is as of the end of October 8, 2054."
    now = CopyFacts("R — d", None, made, made, "manual", "", "x", {})
    assert now.as_of_line == "All data is as of 2054-10-09T11:00:00+00:00."
    assert now.how_label == "Send now"


def test_blocks_draw_in_order_from_their_kept_data():
    """Empty blocks draw their statement; a left-out block is not drawn."""
    snapshot = Snapshot(
        [
            {"block": "additional_information", "version": 1, "data": None},
            {
                "block": "day_by_day",
                "version": 1,
                "data": {"headings": ["Campaign date"], "rows": [["Oct 8, 2054"]]},
            },
            {
                "block": "response_funnel",
                "version": 1,
                "data": {"testing": True, "stages": []},
            },
            {"block": "compared_with_past_campaigns", "version": 1, "data": None},
            {"block": "from_a_later_release", "version": 1, "data": {}},
        ]
    )
    drawn = copy_blocks(snapshot, CopyFrame(SEND))
    assert [block["key"] for block in drawn] == [
        "additional_information",
        "day_by_day",
        "response_funnel",
        "from_a_later_release",
    ]
    requests, days, funnel, unknown = drawn
    assert (requests["template"], requests["empty"]) == ("", "No open requests.")
    assert days["context"] == {"headings": ["Campaign date"], "rows": [["Oct 8, 2054"]]}
    assert funnel["context"]["funnel_testing_note"]
    assert funnel["context"]["funnel_stages"] == []
    assert unknown["template"] == "" and "cannot draw" in unknown["empty"]


def test_a_funnel_kept_without_stages_outside_testing_is_not_drawn():
    """The email drew no funnel then, so the copy draws no empty panel."""
    snapshot = Snapshot(
        [
            {
                "block": "response_funnel",
                "version": 1,
                "data": {"testing": False, "stages": []},
            }
        ]
    )
    assert copy_blocks(snapshot, CopyFrame(SEND)) == []


def test_requests_keep_the_emails_numbers_and_link_their_own_pages():
    """Oldest first as kept, the text shortened as the email shortened it."""
    long_text = "word " * 80
    data = {
        "items": [
            ["i1", "1001", "Smith,  Ann", "2054-10-01T12:00:00+00:00", "Call me"],
            ["i2", "1002", "Jones, Bo", "2054-10-02T12:00:00+00:00", long_text],
        ]
    }
    context = REGISTRY["additional_information"].copy(
        data, CopyFrame(SEND, requests={"i1": 42})
    )
    assert context["heading"] == "2 open requests"
    first, second = context["rows"]
    assert (first["number"], first["family_id"], first["family_name"]) == (
        1,
        42,
        "Smith, Ann",
    )
    assert first["url"] == f"{SEND}items/i1/"
    assert (first["text"], first["shortened"]) == ("Call me", False)
    assert second["family_id"] is None
    assert second["shortened"] and second["text"].endswith("…")
    assert second["submitted_at"] == datetime(2054, 10, 2, 12, tzinfo=UTC)


def test_the_charts_need_the_pinned_facts():
    """A kept chart without its pinned generation is a broken copy, not a guess."""
    with pytest.raises(StorageInvariantError):
        REGISTRY["participation_charts"].copy({"alt": "x"}, CopyFrame(SEND))


def test_the_charts_draw_only_those_the_email_drew():
    """A snapshot that recorded its charts (#1956) draws only those panels."""
    facts = participation()
    frame = CopyFrame(SEND, participation=facts)
    block = REGISTRY["participation_charts"]
    chart = block.copy({"alt": "x", "charts": ["pledges"]}, frame)["chart"]
    assert [panel["title"]["text"] for panel in chart.spec["vconcat"]] == [
        "Effective annual pledges"
    ]
    assert chart.table.headings == ("Campaign date", "Effective annual pledges")
    two = block.copy(
        {"alt": "x", "charts": ["first_submissions", "cumulative_families"]}, frame
    )["chart"]
    assert len(two.spec["vconcat"]) == 2
    assert two.table.headings == (
        "Campaign date",
        "First submissions",
        "Cumulative participation",
    )


def test_the_charts_without_a_record_draw_every_panel():
    """Every snapshot before #1956 kept no ``charts``: it drew every panel."""
    facts = participation()
    every = participation_chart(facts)
    kept = REGISTRY["participation_charts"].copy(
        {"alt": "x"}, CopyFrame(SEND, participation=facts)
    )["chart"]
    assert len(every.spec["vconcat"]) == 3
    assert kept == every
    # Naming every panel, or none the campaign has, is the same chart.
    assert participation_chart(facts, list(PARTICIPATION_CHART_KEYS.values())) == every
    plain = replace(facts, financial_enabled=False)
    assert participation_chart(plain, ["pledges"]) == participation_chart(plain)


def test_the_charts_sentence_names_only_the_drawn_charts():
    """The sentence under a copy's chart states the drawn panels' figures only."""
    frame = CopyFrame(SEND, participation=participation())
    block = REGISTRY["participation_charts"]
    span = (
        "Daily participation, Including Families that left, from October 31, 2026 to "
        "November 2, 2026; "
    )
    sentences = {
        "first_submissions": "on the last date first submissions numbered 1.",
        "cumulative_families": "by the last date 3 out of 1,234 (0.2%) "
        "Families had responded.",
        "pledges": "by the last date effective annual pledges totaled $3,235.",
    }
    for key, sentence in sentences.items():
        chart = block.copy({"alt": "x", "charts": [key]}, frame)["chart"]
        assert chart.summary == span + sentence
        assert chart.spec["description"] == chart.summary
    # A snapshot without the key keeps the sentence it always had.
    every = block.copy({"alt": "x"}, frame)["chart"].summary
    assert every == span + (
        "by the last date 3 out of 1,234 (0.2%) Families had responded, 1 of "
        "them for the first time that day, with $3,235 in effective annual "
        "pledges."
    )
