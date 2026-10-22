"""A report email's source, capture and subject (report-emails spec, #1786).

A ``ReportDocument`` is drawn by the one compiler. Built from the same data
as a Daily or Weekly digest, it compiles to the digest's bytes, except the
Weekly's documented scope change ("N open requests", no corrections),
which is the conversion's gate (spec, "Gating tests") checked early. It
also draws what only a report email can: the opening line without a fact
block, a report as of when it is made, and each block's empty statement.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.jobs.digest_content import identified_subject
from parishkit.stewardship.reports.daily_digest import (
    DAILY_BLOCKS,
    render_daily_digest,
)
from parishkit.stewardship.reports.report_blocks.compiler import (
    capture_report,
    compile_report,
    has_content,
)
from parishkit.stewardship.reports.report_document import (
    PREVIOUS_DAY_END,
    SEND_TIME,
    ReportDocument,
    report_subject,
    send_path,
)
from parishkit.stewardship.reports.weekly_digest import (
    WEEKLY_BLOCKS,
    render_weekly_digest,
)

from .test_daily_digest_content import document as daily_document
from .test_daily_digest_content import funnel_metrics
from .test_report_blocks import (  # noqa: F401
    ORIGIN,
    recovery,
    renderer,
    with_comparison,
)
from .test_weekly_digest_content import INSTANT
from .test_weekly_digest_content import document as weekly_document

SEND = UUID(int=77)
CAMPAIGN = "Annual campaign"
PARISH = "Example Parish"


def from_daily(daily, **changes):
    """A report as of the end of the previous day, from a Daily's data."""
    last = daily.covered_dates[-1]
    zone = daily.participation.campaign_timezone
    end = datetime.combine(last + timedelta(days=1), time.min, ZoneInfo(zone))
    values = dict(
        send_id=SEND,
        name="Daily report",
        as_of=PREVIOUS_DAY_END,
        as_of_at=end,
        campaign_timezone=zone,
        mode=daily.mode or "production",
        covered_dates=daily.covered_dates,
        participation=daily.participation,
        funnel=daily.funnel,
        comparison=daily.comparison,
        recovery=len(daily.covered_dates) > 1,
        date_format=daily.date_format,
    )
    return ReportDocument(**(values | changes))


def from_weekly(weekly, **changes):
    """A report as of when it is made, from a Weekly's requests."""
    values = dict(
        send_id=SEND,
        name="Weekly report",
        as_of=SEND_TIME,
        as_of_at=weekly.observed_at,
        campaign_timezone=weekly.campaign_timezone,
        mode=weekly.mode or "production",
        funnel=weekly.funnel,
        information=weekly.information,
        manual=weekly.manual,
        date_format=weekly.date_format,
    )
    return ReportDocument(**(values | changes))


DAILY_CASES = {
    "single day": lambda: replace(
        daily_document(), funnel=funnel_metrics(), mode="production"
    ),
    "testing": lambda: replace(daily_document(), mode="testing"),
    "recovery range": lambda: replace(recovery(), mode="production"),
    "comparison": with_comparison,
    "pinned date format": lambda: replace(
        daily_document(), mode="production", date_format="eu_dot"
    ),
}


@pytest.mark.parametrize("case", DAILY_CASES)
def test_a_daily_report_draws_the_daily_digests_bytes(case, renderer):  # noqa: F811
    """The converted Daily's blocks draw today's Daily body, byte for byte."""
    daily = DAILY_CASES[case]()
    digest = render_daily_digest(daily, public_origin=ORIGIN)
    compiled = compile_report(from_daily(daily), DAILY_BLOCKS, public_origin=ORIGIN)
    assert (compiled.html, compiled.text) == (digest.html, digest.text)
    images = [(image.content_id, image.data) for image in compiled.images]
    assert images == [(digest.chart.content_id, digest.chart.data)] + [
        (image.content_id, image.data) for image in digest.comparison
    ]


@pytest.mark.parametrize("manual", [False, True])
def test_a_weekly_report_changes_only_the_documented_scope(manual):
    """The converted Weekly's body is the digest's with "N open requests".

    The corrections section is gone and the heading counts open requests
    (decisions 14 and 15); every other byte is the Weekly digest's.
    """
    weekly = replace(weekly_document(), manual=manual)
    expected = render_weekly_digest(
        replace(weekly, corrections=()), public_origin=ORIGIN
    )
    report = from_weekly(weekly)
    compiled = compile_report(
        report, WEEKLY_BLOCKS, public_origin=ORIGIN, copy_path=report.report_path
    )
    old = "1 current actionable request" if manual else "1 new actionable request"
    path = weekly.report_path
    assert compiled.html == expected.html.replace(old, "1 open request").replace(
        path, send_path(SEND)
    )
    assert compiled.text == expected.text.replace(old, "1 open request").replace(
        path, send_path(SEND)
    )


def test_blocks_with_nothing_to_show_draw_their_empty_statements():
    """No campaign day ended yet, no open request: one line each.

    The opening line still opens a report as of the end of the previous
    day, with no fact block too; Compared with past campaigns is left out.
    """
    report = ReportDocument(
        send_id=SEND,
        name="Morning report",
        as_of=PREVIOUS_DAY_END,
        as_of_at=datetime(2026, 10, 5, 4, tzinfo=UTC),
        campaign_timezone="America/New_York",
        mode="production",
    )
    keys = (
        "additional_information",
        "campaign_totals",
        "day_by_day",
        "compared_with_past_campaigns",
    )
    compiled = compile_report(
        report, keys, public_origin=ORIGIN, copy_path=report.report_path
    )
    assert compiled.text.startswith(
        "Here is yesterday’s report; all data is as of the end of October 4, "
        "2026 (EDT).\nOpen the Response dashboard"
    )
    assert "Open additional information requests\nNo open requests." in compiled.text
    assert compiled.text.count("No campaign day has ended yet.") == 2
    assert "Compared with past campaigns" not in compiled.text
    assert compiled.html.count("No campaign day has ended yet.") == 2
    captured = capture_report(
        report, [{"block": key, "version": 1, "options": {}} for key in keys]
    )
    assert [item["data"] for item in captured] == [None] * 4
    assert has_content(captured)
    assert not has_content(captured[:1]) and not has_content(captured[3:])


def test_a_report_as_of_when_it_is_made_opens_with_that_moment():
    """The opening line names the capture instant and its zone."""
    report = from_weekly(weekly_document(), as_of=SEND_TIME)
    assert report.opening == (
        "Here is the report; all data is as of 12:15 AM, November 2, 2026 (EST)."
    )
    held = from_daily(daily_document(), held=True)
    assert held.opening.startswith("Here is the report; all data is as of the end")


def test_captured_blocks_are_json_and_count_content(renderer):  # noqa: F811
    """Each block's data survives JSON, and list blocks count their items."""
    daily = with_comparison()
    entries = [
        {
            "block": key,
            "version": 1,
            "options": {"days": 3} if key == "day_by_day" else {},
        }
        for key in DAILY_BLOCKS
    ]
    captured = capture_report(from_daily(daily), entries)
    assert json.loads(json.dumps(captured)) == captured
    assert [item["block"] for item in captured] == list(DAILY_BLOCKS)
    assert len(captured[3]["data"]["rows"]) == 3
    weekly = from_weekly(weekly_document())
    info = capture_report(
        weekly, [{"block": "additional_information", "version": 1, "options": {}}]
    )
    assert info[0]["data"]["items"][0][:3] == [str(UUID(int=3)), 1234, "A & B"]
    assert has_content(info)
    assert not has_content(
        capture_report(
            from_weekly(weekly_document(), information=()),
            [{"block": "additional_information", "version": 1, "options": {}}],
        )
    )


@pytest.mark.parametrize(
    "authored",
    [
        "{{ campaign_name }}",
        "",
        "Annual campaign daily",
        "Morning summary",
        "Our weekly update",
        "x" * 260,
    ],
)
def test_the_subject_rule_matches_todays_digests(authored, renderer):  # noqa: F811
    """For the converted names the report subject is today's digest subject."""
    authored = authored.replace("{{ campaign_name }}", CAMPAIGN)
    daily = replace(daily_document(), mode="production")
    for value, report in (
        (daily, from_daily(daily)),
        (replace(recovery(), mode="production"), from_daily(recovery())),
    ):
        title = render_daily_digest(value, public_origin=ORIGIN).subject
        assert report_subject(
            authored, report, campaign=CAMPAIGN, parish=PARISH
        ) == identified_subject(authored, title, campaign=CAMPAIGN, parish=PARISH)
    for manual in (False, True):
        weekly = replace(weekly_document(), manual=manual)
        title = render_weekly_digest(weekly, public_origin=ORIGIN).subject
        assert report_subject(
            authored, from_weekly(weekly), campaign=CAMPAIGN, parish=PARISH
        ) == identified_subject(authored, title, campaign=CAMPAIGN, parish=PARISH)


def test_other_names_and_a_test_send():
    """A new report's name is appended in lower case; a test send says so."""
    report = from_weekly(weekly_document(), name="Finance council report")
    assert report_subject(CAMPAIGN, report, campaign=CAMPAIGN, parish=PARISH) == (
        "Annual campaign — finance council report, November 2, 2026 (Example Parish)"
    )
    assert report_subject(
        "Finance update", report, campaign=CAMPAIGN, parish=PARISH, test=True
    ) == (
        "Finance update — council report, November 2, 2026 (test, Annual "
        "campaign, Example Parish)"
    )
    assert INSTANT.date().isoformat() == "2026-11-02"
