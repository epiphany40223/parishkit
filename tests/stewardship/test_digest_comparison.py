"""The daily digest's Compared with past campaigns section (#1018, slice 7).

Without a database: the section document (``digest_comparison.section``)
holds only what may leave the Admin portal, the email carries it charts
first with its own inline images through the closed digest boundary, and
the schedule setting that turns it on is a daily digest's alone. Every
figure here is invented; the comparison is slice 6's test fixture.
"""

import json
from dataclasses import replace
from io import BytesIO

import pytest
from PIL import Image

from parishkit.config import ConfigError
from parishkit.stewardship import observability
from parishkit.stewardship.audit import schemas
from parishkit.stewardship.campaigns.configuration import schedule_values
from parishkit.stewardship.digest_delivery import DigestDeliveryMail
from parishkit.stewardship.reports import chart_rendering, digest_comparison
from parishkit.stewardship.reports.comparison import WITHHELD
from parishkit.stewardship.reports.daily_digest import render_daily_digest
from parishkit.stewardship.reports.digest_comparison import (
    DIGEST_CHART,
    HEADING,
    alt_text,
    data_line,
    section,
)
from parishkit.stewardship.reports.digest_presentation import snapshot_context
from parishkit.stewardship.web.digest_content import (
    CHART_ID,
    COMPARISON_CHART_IDS,
    MAX_ALT_CHARACTERS,
    validate_digest_body,
)

from .test_comparison_exports import NOTE, bands, comparison
from .test_daily_digest_content import document as digest_document

CHECKSUM = "0123456789abcdef" * 4


def found(**options):
    """Slice 6's day-3 comparison, with each imported year's checksum."""
    value = comparison(**options)
    return replace(
        value,
        checksums=tuple((item.year, CHECKSUM) for item in value.imported),
    )


def png(width=1760, height=620):
    """A PNG of the section charts' size (the renderer is not run here)."""
    stream = BytesIO()
    Image.new("RGB", (width, height), "white").save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def renderer(monkeypatch):
    """Stand in for the chart engine's server renderer; record each call."""
    calls = []

    def render_chart(spec, *, format, what, limit_seconds=60):
        """One PNG at the email width; keep the spec and timeout name."""
        calls.append((spec, format, what))
        return chart_rendering.ChartImage(png(), "png", 880, 310)

    monkeypatch.setattr(chart_rendering, "render_chart", render_chart)
    return calls


# ---------------------------------------------------------------- document


def test_no_section_without_a_past_campaign_or_a_day():
    """With no imported year, or before day 1, the section is left out."""
    assert section(None) is None
    assert section(replace(found(), imported=(), last_year=None)) is None
    assert section(replace(found(), day=None, report_date=None)) is None


def test_the_section_is_the_two_day_charts_and_the_outside_tiles():
    """Families and pledges charts only, tiles as they leave the portal."""
    document = json.loads(json.dumps(section(found())))
    assert [chart["measure"] for chart in document["charts"]] == [
        "families",
        "pledged",
    ]
    assert [chart["key"] for chart in document["charts"]] == [
        "comparison-families",
        "comparison-pledged",
    ]
    for chart in document["charts"]:
        assert chart["spec"]["description"] == chart["summary"]
        assert chart["table"]["headings"][0] == "Day"
    assert [tile["label"] for tile in document["tiles"]] == [
        "Families responded",
        "Percent of campaign Families",
        "Annual pledges",
        "Average pledge",
        "Renewal of last year's responders",
    ]
    assert set(document["tiles"][0]) == {"label", "value", "lines"}
    assert document["archives"] == [
        {"year": year, "checksum": CHECKSUM} for year in (2054, 2053, 2052, 2051)
    ]
    assert document["day"] == 3 and document["report_date"] == "2054-10-06"
    assert document["compared_with"] == "2054 and the 2051–2054 average"


def test_without_financial_stewardship_there_is_no_pledges_chart():
    """The pledge figures and chart need the Financial module."""
    document = section(found(financial=False))
    assert [chart["measure"] for chart in document["charts"]] == ["families"]
    assert "Annual pledges" not in {tile["label"] for tile in document["tiles"]}


def test_the_small_count_protection_withholds_averages():
    """A withheld year's average, and any average counting it, are withheld.

    Last year (2054) has a band of 4 Families; 2051 is a final year of 3
    pledging Families (slice 6's later rule), so both withhold.
    """
    tiles = {tile["label"]: tile for tile in section(found())["tiles"]}
    assert tiles["Average pledge"]["lines"] == [
        f"Last year on day 3: {WITHHELD}",
        f"2051–2054 average: {WITHHELD}",
    ]
    # Last year alone unwithheld: 2051's 3 pledging Families still withhold
    # the prior-years average pledge.
    tiles = {
        tile["label"]: tile for tile in section(found(withheld_last=False))["tiles"]
    }
    lines = tiles["Average pledge"]["lines"]
    assert lines[0].startswith("Last year on day 3: $") and WITHHELD not in lines[0]
    assert lines[1] == f"2051–2054 average: {WITHHELD}"


def test_a_small_final_year_with_older_bands_still_withholds():
    """A final year under 5 pledging Families withholds whatever its bands."""
    value = found(withheld_last=False)
    small = replace(
        value.imported[3],
        distribution=replace(bands(1, 1, 1), reason="older band set"),
    )
    value = replace(value, imported=(*value.imported[:3], small))
    tiles = {tile["label"]: tile for tile in section(value)["tiles"]}
    assert tiles["Average pledge"]["lines"][1] == f"2051–2054 average: {WITHHELD}"


def test_nothing_inside_the_portal_leaves_in_the_section():
    """No note, distribution, quartile, band or median so far."""
    text = json.dumps(section(found()), ensure_ascii=False)
    assert NOTE not in text and "Private conversion note" not in text
    for inside in ("Median", "median", "quartile", "band", "Band", "under $250"):
        assert inside not in text, inside


def test_alt_text_carries_the_figures_within_its_limit():
    """The summary names the day's values, cut to the boundary's limit."""
    chart = section(found())["charts"][0]
    assert "On day 3: 2055 (so far) 36" in alt_text(chart)
    long = chart | {"summary": "x" * (MAX_ALT_CHARACTERS + 50)}
    assert len(alt_text(long)) == MAX_ALT_CHARACTERS
    assert alt_text(long).endswith("…")


# ------------------------------------------------------------------ email


def with_section(value=None):
    """The daily digest fixture carrying the section."""
    return replace(digest_document(), comparison=section(value or found()))


def test_the_email_carries_the_section_charts_first(renderer):
    """Charts, then one line per figure, then the report on the same day."""
    result = render_daily_digest(
        with_section(), public_origin="https://campaign.example.org"
    )
    images = result.comparison_images
    assert [name for name, _ in images] == list(COMPARISON_CHART_IDS)
    validate_digest_body(result.html, result.text, result.chart.data, images)
    html = result.html
    start = html.index(HEADING)
    # After the Response dashboard button at the report's end (#1532).
    assert start > html.rindex("Open the Response dashboard</a>")
    assert (
        html.index(f"cid:{COMPARISON_CHART_IDS[0]}", start)
        < html.index(f"cid:{COMPARISON_CHART_IDS[1]}", start)
        < html.index(">Families responded</td>", start)
        < html.index("/admin/reports/comparison/?day=3", start)
    )
    assert "Last year on day 3: 30 (↑ 20% more)" in html
    assert HEADING in result.text
    assert "  Last year on day 3: 30 (↑ 20% more)" in result.text
    assert (
        "Open the Campaign comparison report (staff login required): "
        "https://campaign.example.org/admin/reports/comparison/?day=3"
    ) in result.text
    # Each chart is drawn by the chart engine under the reviewed timeout
    # name, at the email image's width.
    assert [(spec["width"], what) for spec, _f, what in renderer] == [
        (880, DIGEST_CHART),
        (880, DIGEST_CHART),
    ]


def test_without_the_section_the_email_is_unchanged(renderer):
    """No section: no comparison heading, image or renderer call."""
    result = render_daily_digest(
        digest_document(), public_origin="https://campaign.example.org"
    )
    assert result.comparison == ()
    assert HEADING not in result.html and HEADING not in result.text
    assert renderer == []


def test_section_values_are_escaped(renderer):
    """A campaign name with markup stays text in the email."""
    value = replace(found(), campaign_name="<b>Stewardship</b> & 2055")
    result = render_daily_digest(
        with_section(value), public_origin="https://campaign.example.org"
    )
    validate_digest_body(
        result.html, result.text, result.chart.data, result.comparison_images
    )
    assert "&lt;b&gt;Stewardship&lt;/b&gt; &amp; 2055" in result.html


def test_the_boundary_admits_exactly_the_section_images(renderer):
    """Each section image once, in order, at its size, and nothing else."""
    result = render_daily_digest(
        with_section(), public_origin="https://campaign.example.org"
    )
    images = result.comparison_images
    body = (result.html, result.text, result.chart.data)
    validate_digest_body(*body, images)
    for wrong in (
        (),  # images the body shows but the mail does not carry
        images[:1],
        tuple(reversed(images)),
        images + images[:1],
        ((images[0][0], png(width=1440)), images[1]),
        ((images[0][0], png(height=100)), images[1]),
        (("other@parishkit", images[0][1]), images[1]),
        [images[0], images[1]],
    ):
        with pytest.raises(ValueError):
            validate_digest_body(*body, wrong)
    # A body without the section refuses section images it does not show.
    plain = render_daily_digest(
        digest_document(), public_origin="https://campaign.example.org"
    )
    with pytest.raises(ValueError):
        validate_digest_body(plain.html, plain.text, plain.chart.data, images)


def test_the_delivery_mail_carries_every_image(renderer):
    """The private pipe's payload round-trips the section images into MIME."""
    result = render_daily_digest(
        with_section(), public_origin="https://campaign.example.org"
    )
    from uuid import UUID

    mail = DigestDeliveryMail(
        UUID(int=9),
        "reports@example.org",
        "reports@example.org",
        ("admin@example.org",),
        result.subject,
        result.html,
        result.text,
        result.chart.data,
        result.comparison_images,
    )
    again = DigestDeliveryMail.from_payload(json.loads(json.dumps(mail.payload())))
    assert again.images == mail.images
    ids = [
        part["Content-ID"]
        for part in mail.message().walk()
        if part.get_content_type() == "image/png"
    ]
    assert ids == [f"<{CHART_ID}>", *(f"<{name}>" for name in COMPARISON_CHART_IDS)]
    payload = mail.payload()
    for broken in (
        payload | {"images": [["other@parishkit", payload["images"][0][1]]]},
        payload | {"images": "none"},
        {key: value for key, value in payload.items() if key != "images"},
    ):
        with pytest.raises(ValueError):
            DigestDeliveryMail.from_payload(broken)


def test_the_renderer_timeout_name_is_reviewed():
    """A kill while drawing the section reaches the durable log."""
    assert DIGEST_CHART in schemas.TIMEOUT_KINDS
    assert DIGEST_CHART in observability.TIMEOUT_LIMITS
    assert chart_rendering.HELPER in schemas.TIMEOUT_HELPERS
    assert digest_comparison.DIGEST_CHART == "digest_chart"


# --------------------------------------------------------------- saved page


def test_the_saved_page_shows_the_retained_section():
    """The page's context is the stored section, with the report's day."""
    value = with_section()
    context = snapshot_context(
        value, mode="production", chart_url="/c", download_url="/d"
    )
    assert context["comparison"] is value.comparison
    assert context["comparison_line"] == data_line(value.comparison)
    assert context["comparison_url"] == "/admin/reports/comparison/?day=3"
    plain = snapshot_context(
        digest_document(), mode="production", chart_url="/c", download_url="/d"
    )
    assert plain["comparison"] is None and plain["comparison_url"] == ""


# ----------------------------------------------------------------- setting


CAMPAIGN = {
    "timezone": "America/New_York",
    "start_date": "2054-10-04",
    "end_date": "2054-11-30",
}


def schedule(kind="daily_digest", **extra):
    """One digest schedule's values."""
    return {
        "campaign_id": "00000000-0000-4000-8000-000000000001",
        "kind": kind,
        "date": None,
        "time": "07:00:00",
        "weekday": 0 if kind == "weekly_digest" else None,
        "subject": "Daily report",
        "template_version": "00000000-0000-4000-8000-000000000002",
        **extra,
    }


def test_only_a_daily_digest_may_turn_the_section_on():
    """``comparison`` is True on a daily digest, or absent (off)."""
    assert schedule_values(schedule(), CAMPAIGN) is None
    assert schedule_values(schedule(comparison=True), CAMPAIGN) is None
    for refused in (
        schedule(comparison=False),
        schedule(comparison="yes"),
        schedule("weekly_digest", comparison=True),
    ):
        with pytest.raises(ConfigError):
            schedule_values(refused, CAMPAIGN)


def test_a_withheld_render_sends_every_number_without_server_charts(renderer):
    """Charts withheld (#477): no server-drawn image, no dangling cid: reference.

    The funnel and the section keep their tables and text lines; only the
    participation chart, drawn in process, stays an image.
    """
    import re

    from parishkit.stewardship.reports.digest_comparison import withheld

    from .test_chart_specs import metrics

    value = replace(with_section(), funnel=metrics(), mode="production")
    result = render_daily_digest(
        value, public_origin="https://campaign.example.org", charts=False
    )
    assert renderer == []
    assert result.charts_withheld and result.images == ()
    assert result.funnel == () and result.comparison == ()
    validate_digest_body(result.html, result.text, result.chart.data, ())
    assert re.findall(r'src="cid:([^"]+)"', result.html) == [CHART_ID]
    for body in (result.html, result.text):
        assert "Email invites sent" in body and HEADING in body
        assert "Last year on day 3: 30 (↑ 20% more)" in body
    assert withheld(value.comparison)["charts"] == []
    assert withheld(None) is None

    def broken(spec, *, format, what, limit_seconds=60):
        """The renderer refusing every chart."""
        raise chart_rendering.ChartRenderError("Chart rendering failed.")

    # A render that fails names the chart it could not draw; a funnel has
    # no server-drawn charts since #1370, so it renders regardless.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(chart_rendering, "render_chart", broken)
        render_daily_digest(
            replace(digest_document(), funnel=metrics(), mode="production"),
            public_origin="https://campaign.example.org",
        )
        with pytest.raises(chart_rendering.ChartRenderError) as raised:
            render_daily_digest(
                with_section(), public_origin="https://campaign.example.org"
            )
        assert raised.value.chart == "digest_comparison_chart"
    # The funnel's words stay readable for entries logged before #1370.
    assert {"digest_funnel_chart", "digest_activity_chart"} <= schemas.FAILURES
    assert "digest_comparison_chart" in schemas.FAILURES
