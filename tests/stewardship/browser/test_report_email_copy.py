"""A report send's saved copy in the browser (#1946).

The copy draws its blocks as web components: the totals as stat tiles, the
funnel and the day by day as tables, the participation graph and the
Compared with past campaigns charts through the chart engine, and the
requests as a table linking each request's own page. It fits a 320 px
phone and a desktop with no page scroll, in light and dark, with no
accessibility violation; drawing the charts and opening the email as it
was sent move nothing above them; Staff see no outside address.
"""

import pytest

from .report_email_copy_components import ITEM, PAGE, STAFF
from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

OUTSIDE = "finance.council.chair@example.net"


def drawn(page):
    """Wait until every chart on the page is drawn by the chart engine."""
    from playwright.sync_api import expect

    charts = page.locator("[data-chart]")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(
        charts.count()
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [320, 1280])
def test_the_copy_fits_and_passes_axe(
    page, component_origin, axe_source, width, scheme
):
    """No page scroll at any width, and no accessibility violation."""
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.get_by_role("heading", name="Daily report — October 8, 2054"))
    drawn(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    # The email as it was sent, opened, still fits and passes.
    page.locator("[data-copy-email] > summary").click()
    visible(page.locator("[data-copy-email] .report-email-copy"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [320, 1280])
def test_the_blocks_draw_in_order_as_components(page, component_origin, width):
    """Tiles, tables and charts, in the send's order, linking each request."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    drawn(page)
    order = page.locator("[data-copy-block]").evaluate_all(
        "nodes => nodes.map(node => node.dataset.copyBlock)"
    )
    assert order == [
        "additional_information",
        "campaign_totals",
        "response_funnel",
        "participation_charts",
        "day_by_day",
    ]
    # The Compared with past campaigns panel is the saved daily report's own.
    visible(page.locator("[data-digest-comparison]"))
    # The funnel panel is the saved report pages' own (data-digest-funnel).
    visible(page.locator("[data-digest-funnel] table"))
    visible(page.locator("[data-copy-block=campaign_totals] [data-stat=responded]"))
    request = page.get_by_role("link", name="Request 2")
    assert request.get_attribute("href").endswith(
        "/items/00000000-0000-0000-0000-0000000000a2/"
    )
    visible(page.get_by_text("Hernández-Villanueva, María José and Juan Pablo"))
    assert "All data is as of the end of October 8, 2054." in page.inner_text("main")
    assert OUTSIDE in page.inner_text("[data-copy-recipients]")


def test_nothing_moves_when_charts_draw_or_the_email_opens(page, component_origin):
    """Charts draw in their reserved place; the disclosure opens below itself."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    tiles = page.locator("[data-copy-block=campaign_totals]")
    visible(tiles)
    before = tiles.bounding_box()["y"]
    drawn(page)
    assert tiles.bounding_box()["y"] == before
    summary = page.locator("[data-copy-email] > summary")
    summary.scroll_into_view_if_needed()
    above = summary.bounding_box()["y"]
    summary.click()
    visible(page.locator("[data-copy-email] .report-email-copy"))
    assert summary.bounding_box()["y"] == above


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_emails_bars_keep_their_colours(page, component_origin, scheme):
    """Only the closing button takes the portal's colours; each bar segment
    keeps its own bgcolor, so a share bar never looks full."""
    from playwright.sync_api import expect

    from parishkit.stewardship.web.report_markup import BLUE, TRACK

    page.emulate_media(color_scheme=scheme)
    page.goto(component_origin + PAGE)
    page.locator("[data-copy-email] > summary").click()
    email = page.locator("[data-copy-email] .report-email-copy")
    visible(email)
    background = "cell => getComputedStyle(cell).backgroundColor"
    accent = email.locator("td[bgcolor]:has(> a)").first.evaluate(background)
    for colour in (BLUE, TRACK):
        segment = email.locator(f'td[bgcolor="{colour}"]:not(:has(> a))').first
        expect(segment).to_be_attached()
        red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        assert segment.evaluate(background) == f"rgb({red}, {green}, {blue})"
        assert segment.evaluate(background) != accent


def test_staff_see_how_many_outside_addresses_but_not_which(page, component_origin):
    """Outside addresses are an Administrator's to see (spec, "Saved copies")."""
    page.goto(component_origin + STAFF)
    recipients = page.locator("[data-copy-recipients]")
    visible(recipients)
    text = recipients.inner_text()
    assert OUTSIDE not in text
    assert "1 address; only Administrators see outside addresses." in text


@pytest.mark.parametrize("width", [320, 1280])
def test_a_request_page_fits_and_links_the_live_request(
    page, component_origin, axe_source, width
):
    """The kept request in full, its state, and its live page's link."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + ITEM)
    visible(page.get_by_role("heading", name="Saved request", level=1))
    live = "Open this request on Additional information"
    visible(page.get_by_role("link", name=live))
    assert "please call us in the evening." in page.inner_text("main")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
