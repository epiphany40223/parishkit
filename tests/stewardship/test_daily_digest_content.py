"""Daily digests reuse exact report observations without database/provider access."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, localcontext
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image

from parishkit.email.base import Email, build_message
from parishkit.stewardship.reports.daily_digest import (
    CHART_ID,
    DailyDigestDocument,
    render_daily_digest,
)
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.reports.statistics import (
    CampaignStatistics,
    PopulationStatistics,
)

from .test_participation_rendering import document as participation


def document():
    """Different historical/current totals expose accidental population mixing."""
    chart = participation()
    chart = replace(chart, browser_timezone=chart.campaign_timezone)
    statistics = CampaignStatistics(
        campaign_id=chart.campaign_id,
        configuration_id=UUID(int=3),
        observed_at=chart.requested_at,
        source_id=UUID(int=4),
        source_generation=chart.source_generation,
        source_as_of=chart.source_as_of,
        submission_watermark=chart.submission_watermark,
        active=PopulationStatistics(
            1000, 2345, 900, 800, 2, MoneyAmount(223456), MoneyAmount(100000)
        ),
        inactive=None,
        financial_enabled=True,
        financial=None,
        giving=None,
    )
    return DailyDigestDocument(UUID(int=5), chart, statistics, (chart.last_date,))


def render(value):
    """Use a public synthetic origin; no configured credentials or DNS are read."""
    return render_daily_digest(value, public_origin="https://campaign.example.org")


def test_daily_digest_keeps_exact_values_and_accessible_inline_chart():
    """MIME chart and table share the same document; cards stay current-population."""
    value = document()
    with localcontext(prec=2):
        result = render(value)
    assert result.subject == "Daily campaign digest — 2026-11-02"
    for required in (
        "Active Families: 1,000",
        "Active Members: 2,345",
        "Families with eligible email: 900 out of 1,000 (90%)",
        "Families with deliverable email: 800 out of 1,000 (80%)",
        "Families that have responded: 2 out of 1,000 (0.2%)",
        "Current annual pledges: $2,234.56",
        "Configured comparison pledges: $1,000.00",
        "2026-11-02 | 1 | 3 out of 1,234 (0.2%) | $3,234.56",
        "Source #3 as of 2026-11-02T00:00:00-05:00",
        "submission cutoff 1,001",
        value.report_path,
    ):
        assert required in result.text
    assert "<caption>Historical as of day</caption>" in result.html
    assert '<th scope="col">Campaign date</th>' in result.html
    assert f"cid:{CHART_ID}" in result.html
    with Image.open(BytesIO(result.chart.data)) as image:
        assert image.format == "PNG" and image.size == (1440, 840)
        image.verify()
    message = build_message(
        Email(
            subject=result.subject,
            sender="sender@example.org",
            to=("admin@example.org",),
            html=result.html,
            text=result.text,
            inline_images=(result.chart,),
        )
    )
    assert "3 out of 1,234" in message.get_body(preferencelist=("plain",)).get_content()
    image_part = [
        part for part in message.walk() if part.get_content_type() == "image/png"
    ]
    assert len(image_part) == 1 and image_part[0]["Content-ID"] == f"<{CHART_ID}>"


def test_recovery_covers_complete_range_even_with_intervening_success():
    """Coverage identifies missed slots; the displayed range never omits a day."""
    value = document()
    value = replace(
        value,
        covered_dates=(value.participation.first_date, value.participation.last_date),
    )
    result = render(value)
    assert result.subject == "Recovery campaign digest — 2026-10-31 through 2026-11-02"
    assert "2026-10-31 | 1 | 1 out of 1,234 (0.1%) | $1,234.56" in result.text
    assert "2026-11-01 | 1 | 2 out of 1,234 (0.2%) | $2,234.56" in result.text
    assert "2026-11-02 | 1 | 3 out of 1,234 (0.2%) | $3,234.56" in result.text


def test_disabled_financial_is_not_rendered_even_if_inputs_have_amounts():
    """Financial enablement controls chart axes, table columns and cards together."""
    value = document()
    value = replace(
        value,
        participation=replace(value.participation, financial_enabled=False),
        statistics=replace(value.statistics, financial_enabled=False),
    )
    result = render(value)
    for body in (result.text, result.html):
        assert "pledge" not in body.lower() and "$" not in body


def test_missing_observations_remain_unavailable_and_zero_remains_zero():
    """Missing source facts and missing comparison totals never become zero."""
    value = document()
    days = value.participation.days
    missing = replace(
        days[0],
        first_responses=0,
        cumulative_responses=0,
        cohort_denominator=0,
        source_generation=None,
        source_as_of=None,
        population_available=False,
        pledge_available=False,
        pledge_total=None,
    )
    zero = replace(
        days[1],
        first_responses=0,
        cumulative_responses=0,
        cohort_denominator=0,
        pledge_total=Decimal("0.00"),
    )
    value = replace(
        value,
        covered_dates=tuple(day.local_date for day in days),
        participation=replace(value.participation, days=(missing, zero, days[2])),
        statistics=replace(
            value.statistics,
            active=replace(
                value.statistics.active, comparison_pledge=MoneyAmount(None)
            ),
        ),
    )
    result = render(value)
    assert "2026-10-31 | Unavailable | Unavailable | Unavailable" in result.text
    assert "2026-11-01 | 0 | 0 out of 0 (—) | $0.00" in result.text
    assert "Configured comparison pledges: Unavailable" in result.text


@pytest.mark.parametrize(
    "changes",
    [
        {"campaign_id": UUID(int=99)},
        {"source_id": None},
        {"source_generation": 4},
        {"source_as_of": participation().source_as_of - timedelta(seconds=1)},
        {"observed_at": participation().requested_at + timedelta(seconds=1)},
        {"submission_watermark": 1002},
        {"financial_enabled": False},
        {"active": None},
        {"inactive": document().statistics.active},
    ],
)
def test_mixed_observations_are_rejected(changes):
    """An exact chart cannot be paired with newer or differently scoped cards."""
    value = document()
    with pytest.raises(ValueError, match="exact observation"):
        replace(value, statistics=replace(value.statistics, **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"snapshot_id": "not a UUID"},
        {"participation": None},
        {"statistics": None},
        {"covered_dates": []},
        {"covered_dates": ()},
        {"covered_dates": ("2026-11-02",)},
        {"covered_dates": (date(2026, 11, 2), date(2026, 11, 2))},
        {"covered_dates": (date(2026, 11, 2), date(2026, 10, 31))},
        {"covered_dates": (date(2026, 10, 30), date(2026, 11, 2))},
        {"covered_dates": (date(2026, 10, 31),)},
    ],
)
def test_invalid_identity_and_coverage_are_rejected(changes):
    """Malformed coverage cannot silently select the last discovery batch."""
    with pytest.raises(ValueError):
        replace(document(), **changes)


def test_nonhistorical_wrong_zone_and_empty_chart_are_rejected():
    """Email uses the immutable campaign timezone, not a worker/browser default."""
    value = document()
    for changes in (
        {"population_scope": "current"},
        {"browser_timezone": "UTC"},
        {"days": (), "first_date": None, "last_date": None},
    ):
        with pytest.raises(ValueError):
            replace(value, participation=replace(value.participation, **changes))


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "javascript:alert(1)",
        "https://host/path",
        "https://u:p@host",
        "https://host/?secret=1",
        "https://host/#secret",
        "https://host\n",
        "https://host:99999",
        "https://[invalid",
        "https://host\\evil",
    ],
)
def test_report_link_rejects_nonorigins(origin):
    """No arbitrary destination/path/query or secret-bearing URL enters the mail."""
    with pytest.raises(ValueError, match="public HTTP origin"):
        render_daily_digest(document(), public_origin=origin)


def test_branding_is_escaped_and_render_is_reproducible():
    """Parish branding is data, never markup; retries do not use a new clock."""
    value = document()
    value = replace(
        value,
        participation=replace(value.participation, parish_name='<img src="evil">'),
    )
    first = render(value)
    second = render_daily_digest(value, public_origin="https://campaign.example.org/")
    assert first == second
    assert '<img src="evil">' not in first.html
    assert "&lt;img src=&quot;evil&quot;&gt;" in first.html
    assert '<img src="evil">' in first.text
    assert "evil" not in repr(first)
    with pytest.raises(TypeError):
        render_daily_digest(None, public_origin="https://campaign.example.org")
