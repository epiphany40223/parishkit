"""Daily digests reuse exact report observations without database/provider access."""

import re
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, localcontext
from html.parser import HTMLParser
from io import BytesIO
from uuid import UUID

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from parishkit.email.base import Email, build_message
from parishkit.stewardship.reports.charts import (
    DIGEST_PLOT_LAYOUTS,
    EMAIL_PLOT_LAYOUT,
    EMAIL_RENDERER_VERSION,
    LEGACY_PLOT_LAYOUT,
    render_participation,
)
from parishkit.stewardship.reports.daily_digest import (
    CHART_ID,
    DailyDigestDocument,
    bar,
    digest_rows,
    render_daily_digest,
)
from parishkit.stewardship.reports.digest_presentation import chart_layout
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.reports.statistics import (
    CampaignStatistics,
    PopulationStatistics,
)
from parishkit.stewardship.source.data_age import Connection, DataAge
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.buttons import EMAIL_TABLE_STYLE
from parishkit.stewardship.web.digest_content import validate_digest_body
from parishkit.stewardship.web.report_markup import BLUE, STYLES, layout_attribute

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
        active=PopulationStatistics(1000, 2345, 900, 800, 2, MoneyAmount(223456)),
        financial_enabled=True,
        financial=None,
        giving=None,
        comparison_pledge_all=MoneyAmount(100000),
    )
    return DailyDigestDocument(UUID(int=5), chart, statistics, (chart.last_date,))


def render(value):
    """Exercise compilation through the actual retention/delivery boundary."""
    result = render_daily_digest(value, public_origin="https://campaign.example.org")
    validate_digest_body(result.html, result.text, result.chart.data, result.images)
    return result


@pytest.mark.parametrize("separator", ["\u00a0", "'", '"'])
def test_imported_labels_compile_without_mutating_retained_names(separator):
    """Imported names compile unchanged; the subject, not the body, names them."""
    value = document()
    chart = replace(
        value.participation,
        parish_name=f"Example{separator}Parish",
        campaign_name=f"Annual{separator}Campaign",
    )
    result = render(replace(value, participation=chart))
    validate_digest_body(result.html, result.text, result.chart.data)
    for name in (f"Example{separator}Parish", "Example Parish", "Annual Campaign"):
        assert name not in result.html and name not in result.text
    assert chart.parish_name == f"Example{separator}Parish"


def test_daily_digest_keeps_exact_values_and_accessible_inline_chart():
    """MIME chart, table and cards share the report day's end-of-day fact (#721)."""
    value = document()
    with localcontext(prec=2):
        result = render(value)
    assert result.subject == "Daily campaign digest — November 2, 2026"
    for required in (
        "All data is as of the end of November 2, 2026 (EST).",
        "Families that have responded: 3 out of 1,234 (0.2%)",
        "First submissions that day: 1",
        "Cumulative annual pledges (USD): $3,234.56",
        "Nov 2, 2026 | 1 | 3 out of 1,234 (0.2%) | $3,234.56",
        value.report_path,
    ):
        assert required in result.text
    # The send-time statistics (2 of 1,000 Families, $2,234.56) are live, so
    # they never appear: they would disagree with the chart's last point.
    live_figures = ("1,000", "2,345", "Active Members", "comparison")
    # #728 relabelled the comparison card; it must not appear either.
    for live in (*live_figures, "Last year's pledges"):
        assert live not in result.text and live not in result.html
    # The live pledge total equals an earlier day's cumulative pledges in this
    # fixture, and the week table shows that day (#720), so check the totals.
    totals = result.text.split("Campaign date |")[0]
    assert "2,234.56" not in totals
    for jargon in ("Historical as of day.", "Current active population", "Source #"):
        assert jargon not in result.text
    assert ">Day by day</caption>" in result.html
    assert "Historical as of day" not in result.html
    assert ">Campaign date</th>" in result.html
    assert f"cid:{CHART_ID}" in result.html
    # Visuals first (#720): the as-of caption, then one line per total with
    # its bar, then the chart; the ParishSoft line is small print at the end.
    html = result.html
    assert (
        html.index("All data is as of")
        < html.index("Campaign totals")
        < html.index("cid:")
        < html.index("Day by day")
        < html.index("Open this exact report")
    )
    assert html.count(f'bgcolor="{BLUE}"') == 2
    assert "<strong>3 out of 1,234 (0.2%)</strong>" in html
    assert (
        'alt="Daily participation chart, October 31, 2026 to November 2, 2026.' in html
    )
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
    assert result.subject == (
        "Recovery campaign digest — October 31, 2026 through November 2, 2026"
    )
    assert "Oct 31, 2026 | 1 | 1 out of 1,234 (0.1%) | $1,234.56" in result.text
    assert "Nov 1, 2026 | 1 | 2 out of 1,234 (0.2%) | $2,234.56" in result.text
    assert "Nov 2, 2026 | 1 | 3 out of 1,234 (0.2%) | $3,234.56" in result.text


def long_document(days):
    """A campaign of ``days`` days, one first submission each, ending Nov 2."""
    value = document()
    chart = value.participation
    last = chart.days[-1]
    first_date = chart.last_date - timedelta(days=days - 1)
    series = tuple(
        replace(
            last,
            local_date=first_date + timedelta(days=index),
            first_responses=1,
            cumulative_responses=index + 1,
            pledge_total=Decimal(index + 1),
        )
        for index in range(days)
    )
    chart = replace(chart, first_date=first_date, days=series)
    return replace(value, participation=chart)


def test_table_shows_the_last_week_and_every_recovered_day():
    """The table has at least the last 7 days, and all of a longer recovery (#720)."""
    value = long_document(12)
    dates_shown = [row[0] for row in digest_rows(value)]
    assert dates_shown[0] == "Oct 27, 2026" and len(dates_shown) == 7
    first = value.participation.first_date
    recovery = replace(
        value,
        covered_dates=tuple(first + timedelta(days=index) for index in range(12)),
    )
    assert len(digest_rows(recovery)) == 12
    assert "Oct 22, 2026 | 1 | 1 out of 1,234" in render(recovery).text


@pytest.mark.parametrize(
    ("part", "whole", "cells"),
    [
        (None, 10, None),
        (3, 0, None),
        (0, 10, ["100%"]),
        (1, 1000, ["1%", "99%"]),
        (5, 10, ["50%", "50%"]),
        (10, 10, ["100%"]),
        (999, 1000, ["99%", "1%"]),
    ],
)
def test_bars_never_hide_a_nonzero_share_or_draw_without_a_base(part, whole, cells):
    """A bar is table cells; a tiny share keeps a sliver, no base draws nothing."""
    html = bar(part, whole)
    if cells is None:
        assert html == ""
    else:
        assert re.findall(r'width="(\d+%)" bgcolor', html) == cells


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
    """Missing source facts never become zero, in the table or the cards."""
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
    )
    result = render(value)
    assert "Oct 31, 2026 | Unavailable | Unavailable | Unavailable" in result.text
    assert "Nov 1, 2026 | 0 | 0 out of 0 (—) | $0.00" in result.text
    # The report day's own unavailable fact stays unavailable in the cards.
    last = replace(missing, local_date=days[2].local_date)
    gap = render(
        replace(
            value, participation=replace(value.participation, days=(*days[:2], last))
        )
    )
    assert "Families that have responded: Unavailable" in gap.text
    assert "First submissions that day: Unavailable" in gap.text
    assert "Cumulative annual pledges (USD): Unavailable" in gap.text


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
    # The body no longer names the parish (#720); the subject does.
    assert "evil" not in first.html and "evil" not in first.text
    validate_digest_body(first.html, first.text, first.chart.data)
    assert "evil" not in repr(first)
    with pytest.raises(TypeError):
        render_daily_digest(None, public_origin="https://campaign.example.org")


def test_pinned_date_format_overrides_the_ambient_style():
    """The snapshot's style wins over a worker's active one; None means default."""
    last = document().participation.last_date
    with dates.using("iso"):
        pinned = render(replace(document(), date_format="eu_dot"))
        unset = render(document())
    assert pinned.subject.endswith(dates.format_date(last, "eu_dot"))
    assert dates.format_date(last, "eu_dot", compact=True) in pinned.text
    assert unset.subject.endswith(dates.format_date(last, "us_long"))
    assert dates.format_date(last, "iso") not in pinned.text + unset.text


def test_date_format_must_be_text_or_unset():
    """A non-string style is rejected before anything is rendered."""
    with pytest.raises(ValueError, match="typed immutable"):
        replace(document(), date_format=5)


def test_recovery_range_states_its_last_covered_day():
    """A recovery digest's as-of line and totals use its last covered day.

    The fixture's days straddle the end of daylight saving time (November 1,
    2026), so the zone abbreviation follows the report day: EST, not EDT.
    """
    value = document()
    days = value.participation.days
    result = render(replace(value, covered_dates=tuple(day.local_date for day in days)))
    assert result.subject == (
        "Recovery campaign digest — October 31, 2026 through November 2, 2026"
    )
    for required in (
        "All data is as of the end of November 2, 2026 (EST).",
        "Families that have responded: 3 out of 1,234 (0.2%)",
        "First submissions that day: 1",
        "Cumulative annual pledges (USD): $3,234.56",
        "Oct 31, 2026 | 1 | 1 out of 1,234 (0.1%) | $1,234.56",
    ):
        assert required in result.text
    # A report day before the change still says EDT.
    first = replace(
        value.participation,
        days=days[:1],
        last_date=days[0].local_date,
    )
    earlier = render(
        replace(value, participation=first, covered_dates=(days[0].local_date,))
    )
    assert "as of the end of October 31, 2026 (EDT)." in earlier.text
    assert "Families that have responded: 1 out of 1,234 (0.1%)" in earlier.text


def test_saved_page_hit_tests_the_drawing_the_digest_retained():
    """New digests carry the email drawing; earlier retained ones keep theirs.

    A digest from before the separate panels (#720) holds the email drawing
    with a second dollar axis, and one from before #720 the page drawing;
    each keeps the plot box it was drawn with.
    """
    result = render(document())
    assert chart_layout(result.chart.data) == EMAIL_PLOT_LAYOUT
    with Image.open(BytesIO(result.chart.data)) as image:
        assert image.size == (1440, 840)
        assert image.text["Creator"] == EMAIL_RENDERER_VERSION
    dual_axis = BytesIO()
    info = PngInfo()
    info.add_text("Creator", "participation-email-v1")
    Image.new("RGB", (4, 4)).save(dual_axis, format="png", pnginfo=info)
    assert chart_layout(dual_axis.getvalue()) == {
        "left": 0.09,
        "right": 0.89,
        "top": 0.90,
        "bottom": 0.17,
    }
    assert DIGEST_PLOT_LAYOUTS["participation-email-v1"] != EMAIL_PLOT_LAYOUT
    older = BytesIO()
    render_participation(document().participation, older, format="png")
    assert chart_layout(older.getvalue()) == LEGACY_PLOT_LAYOUT
    assert chart_layout(b"not a png") == LEGACY_PLOT_LAYOUT


class _Visible(HTMLParser):
    """Collect the text a reader sees: text nodes, not attribute values."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def visible(html):
    """Return an HTML body's visible text (alt text excluded)."""
    parser = _Visible()
    parser.feed(html)
    return " ".join(parser.parts)


def test_each_header_fact_appears_once():
    """The body opens on the as-of line; the subject names everything else.

    The Administrator trimmed the header and small print (#720): the parish
    and campaign are left to the subject, the as-of line is the only place
    naming the report day's zone, and no ParishSoft or sign-in note remains.
    """
    instant = document().participation.requested_at
    value = replace(
        document(), source_age=DataAge(instant, instant, Connection("working", instant))
    )
    result = render(value)
    for body in (visible(result.html), result.text):
        assert "Example Parish" not in body and "Annual stewardship" not in body
        assert body.count("EST") == 1 and "America/New_York" not in body
        assert "Daily campaign digest" not in body
        assert body.count("November 2, 2026") == 1
        assert "ParishSoft" not in body and "sign-in" not in body
    assert visible(result.html).lstrip().startswith("All data is as of")
    # Prominent (#1016): body size and colour, not the muted caption style.
    assert result.html.startswith(f'<p style="{STYLES["as-of"]}">All data is')
    assert "font-size:16px" in STYLES["as-of"] and "#1b2a31" in STYLES["as-of"]
    assert result.subject == "Daily campaign digest — November 2, 2026"


@pytest.mark.parametrize(
    "markup",
    [
        '<table><tbody><tr><td bgcolor="#ff0000">x</td></tr></tbody></table>',
        '<table><tbody><tr><td width="40px">x</td></tr></tbody></table>',
        '<table><tbody><tr><td align="center">x</td></tr></tbody></table>',
        f'<ul><li style="{STYLES["caption"]}">x</li></ul>',
        '<p style="color:red">x</p>',
    ],
)
def test_validator_rejects_layout_markup_outside_the_closed_set(markup):
    """Colours, widths, alignments and styles must be the compiler's own."""
    result = render(document())
    with pytest.raises(ValueError):
        validate_digest_body(result.html + markup, result.text, result.chart.data)


def test_report_button_is_padded_for_outlook():
    """Outlook ignores a link's padding, so the button's cell carries it too."""
    result = render(document())
    assert 'bgcolor="#115e56"' in result.html
    assert "mso-padding-alt:10px 20px;" in result.html
    assert "Open this exact report</a>" in result.html


# The report button exactly as #720 compiled it, before it became the shared
# email button (#732).
PRE_732_BUTTON = (
    '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
    ' style="border-collapse:separate;margin:24px 0 0;"><tbody><tr>'
    '<td bgcolor="#115e56" style="border-radius:8px;background-color:#115e56;'
    'mso-padding-alt:10px 20px;"><a href="https://example.org/r"'
    ' style="display:inline-block;padding:10px 20px;border:2px solid #115e56;'
    "border-radius:8px;background-color:#115e56;color:#ffffff;font-size:16px;"
    'font-weight:bold;line-height:1.3;text-decoration:none;"'
    ' rel="noopener noreferrer">Open this exact report</a></td></tr></tbody></table>'
)


def test_retained_reports_with_the_earlier_button_still_validate():
    """A report compiled before #732 and retained for a retry still delivers."""
    result = render(document())
    start = result.html.rindex("<table", 0, result.html.index(EMAIL_TABLE_STYLE))
    end = result.html.index("</table>", start) + len("</table>")
    retained = result.html[:start] + PRE_732_BUTTON + result.html[end:]
    assert retained != result.html and EMAIL_TABLE_STYLE not in retained
    validate_digest_body(retained, result.text, result.chart.data)
    validate_digest_body(result.html, result.text, result.chart.data)


# The bar fill and the table heading style a report carried before they took
# the design tokens (#732).
PRE_TOKEN_BAR = "#1f6fae"
PRE_TOKEN_TH = (
    "padding:8px 12px 8px 0;font-size:14px;color:#4f5e66;"
    "border-bottom:2px solid #d0d5dd;"
)


def test_retained_reports_with_the_earlier_bar_and_rule_still_validate():
    """A report compiled before the bar and rule took the tokens still delivers."""
    result = render(document())
    assert BLUE != PRE_TOKEN_BAR and STYLES["th"] != PRE_TOKEN_TH
    retained = result.html.replace(f'bgcolor="{BLUE}"', f'bgcolor="{PRE_TOKEN_BAR}"')
    assert retained != result.html
    validate_digest_body(retained, result.text, result.chart.data)
    assert layout_attribute("th", "style", PRE_TOKEN_TH) == PRE_TOKEN_TH
    assert layout_attribute("th", "style", STYLES["th"]) == STYLES["th"]
    # Only those two earlier values: an unrelated colour is still refused.
    assert layout_attribute("td", "bgcolor", "#b8620a") is None


def funnel_metrics():
    """A funnel as response_metrics returns it, without the rows behind it."""
    from datetime import UTC, datetime

    from parishkit.stewardship.reports.response_metrics import (
        LINK_FOLLOWED_NOTE,
        ResponseMetrics,
        ResponseScope,
        Stage,
    )

    counts = (
        ("invited", 1000),
        ("link_followed", 640),
        ("form_opened", 512),
        ("progressed", 480),
        ("submitted", 401),
    )
    return ResponseMetrics(
        scope=ResponseScope(UUID(int=1)),
        as_of=datetime(2026, 11, 3, 5, tzinfo=UTC),
        timezone="America/New_York",
        grain="hour",
        families=(),
        stages=tuple(
            Stage(key, count, LINK_FOLLOWED_NOTE if key == "link_followed" else "")
            for key, count in counts
        ),
        skipped_responded=12,
        submitted_uninvited=3,
        submitted_again=7,
        activity=(),
        sends=(),
    )


def test_a_production_digest_shows_the_response_funnel():
    """Four stages with counts and shares, and one figure with its share."""
    result = render(replace(document(), funnel=funnel_metrics(), mode="production"))
    for body in (visible(result.html), result.text):
        assert "Response funnel" in body
        # Named for what it counts (#1016), not "Invited".
        assert "Email invites sent" in body and "100%" in body
        assert "Invited" not in body
        assert "Submitted" in body and "401" in body and "40%" in body
        # "Link followed" is left out of the digest (#1016).
        assert "Link followed" not in body and "640" not in body
        assert "mail-scanner" not in body
        assert "::" not in body
        # Trimmed by the Administrator (#969): no caption and no "already
        # responded" or "without a delivered invitation" figures.
        assert "Families at each stage" not in body
        assert "Percentages are of" not in body
        assert "already responded" not in body
        assert "without a delivered invitation" not in body
    assert "Email invites sent: 1,000 (100%)" in result.text
    # The figure has a share of Submitted, named as such, and a bar (#1016).
    assert "Submitted more than once: 7 (2% of Submitted)" in result.text
    assert ">Submitted more than once</td>" in result.html
    assert "<strong>7</strong> (2% of Submitted)</td>" in result.html
    figure = result.html[result.html.index(">Submitted more than once</td>") :]
    assert figure.index(f'<td width="2%" bgcolor="{BLUE}"') < figure.index("</tr>")
    # Four stage rows and one figure row, each with a bar.
    funnel = result.html[
        result.html.index("Response funnel") : result.html.index("Daily participation")
    ]
    assert funnel.count(f'style="{STYLES["bar"]}"') == 5
    # After the campaign totals, before the participation chart.
    html = result.html
    assert html.index("Campaign totals") < html.index("Response funnel")
    assert html.index("Response funnel") < html.index("Daily participation")


def test_the_digest_funnel_figure_shares_handle_no_submissions():
    """With nothing submitted, the figure's share is a dash and has no bar."""
    from parishkit.stewardship.reports.digest_funnel import figure_rows
    from parishkit.stewardship.reports.response_metrics import Stage

    metrics = funnel_metrics()
    empty = replace(
        metrics,
        stages=tuple(
            Stage(stage.key, 0, stage.note) if stage.key == "submitted" else stage
            for stage in metrics.stages
        ),
        submitted_again=0,
    )
    assert figure_rows(empty) == (("Submitted more than once", 0, 0, "— of Submitted"),)
    result = render(replace(document(), funnel=empty, mode="production"))
    assert "Submitted more than once: 0 (— of Submitted)" in result.text


def test_a_testing_digest_says_where_the_funnel_is():
    """No Testing funnel: rehearsal data is cleaned up, so it cannot be recounted."""
    result = render(replace(document(), mode="testing"))
    for body in (visible(result.html), result.text):
        assert "Response funnel" not in body
        assert "Testing digests leave out the response funnel." in body
    # A document with no mode (older callers) shows neither.
    plain = render(document())
    assert "Response funnel" not in plain.text and "Testing digests" not in plain.text


def test_the_saved_page_shows_the_same_funnel():
    """The saved report page renders the funnel from the same document."""
    from parishkit.stewardship.reports.digest_presentation import snapshot_context

    value = replace(document(), funnel=funnel_metrics(), mode="production")
    context = snapshot_context(
        value, mode="production", chart_url="/chart", download_url="/download"
    )
    assert [row[:2] for row in context["funnel_stages"]] == [
        ("Email invites sent", 1000),
        ("Form opened", 512),
        ("Progressed past the first step", 480),
        ("Submitted", 401),
    ]
    assert context["funnel_testing_note"] == ""
    # Trimmed like the email (#969): one figure, with its share (#1016).
    assert context["funnel_figures"] == (
        ("Submitted more than once", 7, 401, "2% of Submitted"),
    )
    assert "funnel_caption" not in context
    testing = snapshot_context(
        replace(document(), mode="testing"),
        mode="testing",
        chart_url="/chart",
        download_url="/download",
    )
    assert testing["funnel_stages"] == ()
    assert "Testing digests" in testing["funnel_testing_note"]


def charted_digest():
    """A Production digest whose funnel has activity and sends to chart."""
    from .test_chart_specs import metrics

    return replace(document(), funnel=metrics(), mode="production")


def test_a_production_digest_has_no_funnel_charts():
    """The funnel section is its rows only (#1370): no images, no captions."""
    result = render(charted_digest())
    assert result.funnel == () and result.images == ()
    html = result.html
    assert "cid:response-" not in html and "Response activity" not in html
    assert (
        html.index(">Response funnel</h2>")
        < html.index(">Email invites sent</td>")
        < html.index("Daily participation")
    )
    assert html.count("<img ") == 1
    assert "Response activity" not in result.text


def legacy_funnel_body(result):
    """A body as a pre-#1370 compile wrote it: the two funnel images first."""
    from parishkit.stewardship.web.digest_content import (
        COMPARISON_PIXEL_WIDTH,
        EMAIL_CHART_WIDTH,
        FUNNEL_CHART_IDS,
    )

    stream = BytesIO()
    Image.new("RGB", (COMPARISON_PIXEL_WIDTH, 400), "white").save(stream, "PNG")
    images = tuple((name, stream.getvalue()) for name in FUNNEL_CHART_IDS)
    tags = "".join(
        f'<img src="cid:{name}" alt="Chart." width="{EMAIL_CHART_WIDTH}">'
        for name in FUNNEL_CHART_IDS
    )
    heading = result.html.index("Response funnel</h2>") + len("Response funnel</h2>")
    return result.html[:heading] + tags + result.html[heading:], images


def test_a_retained_digest_with_funnel_charts_is_still_delivered():
    """A ready row compiled before #1370 still sends its two funnel images."""
    import json

    from parishkit.stewardship.digest_delivery import DigestDeliveryMail
    from parishkit.stewardship.web.digest_content import FUNNEL_CHART_IDS

    result = render(charted_digest())
    html, images = legacy_funnel_body(result)
    funnel, activity = images
    body = (html, result.text, result.chart.data)
    validate_digest_body(*body, images)
    # The two still travel together, in order, each referenced once.
    for wrong in ((funnel,), (activity, funnel), (funnel, funnel), ()):
        with pytest.raises(ValueError):
            validate_digest_body(*body, wrong)
    swapped = html.replace(f"cid:{FUNNEL_CHART_IDS[1]}", f"cid:{FUNNEL_CHART_IDS[0]}")
    with pytest.raises(ValueError):
        validate_digest_body(swapped, *body[1:], images)
    mail = DigestDeliveryMail(
        UUID(int=9),
        "reports@example.org",
        "reports@example.org",
        ("admin@example.org",),
        result.subject,
        html,
        result.text,
        result.chart.data,
        images,
    )
    again = DigestDeliveryMail.from_payload(json.loads(json.dumps(mail.payload())))
    assert again.images == mail.images == images
    related = next(
        part
        for part in mail.message().walk()
        if part.get_content_type() == "multipart/related"
    )
    names = {
        part["Content-ID"].strip("<>")
        for part in related.walk()
        if part.get_content_type() == "image/png"
    }
    assert set(FUNNEL_CHART_IDS) <= names


def test_a_testing_digest_has_no_funnel_charts():
    """No funnel, so no funnel images; older documents without a mode neither."""
    for value in (replace(document(), mode="testing"), document()):
        result = render(value)
        assert result.funnel == () and result.images == ()
        assert "cid:response-" not in result.html


def test_the_saved_page_draws_no_funnel_charts():
    """The daily page's funnel panel is its table and figure only (#1370)."""
    from parishkit.stewardship.reports.digest_presentation import snapshot_context

    context = snapshot_context(
        charted_digest(),
        mode="production",
        chart_url="/chart",
        download_url="/download",
    )
    assert "funnel_charts" not in context and context["funnel_stages"]
