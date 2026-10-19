"""The daily report email itself fits a 320px phone (#1559, #1564).

Mail programs cannot scroll a table sideways, so the compiled email's own
layout must fit. The tests load the complete HTML part (``email_document``
at the report width) with realistic worst-case values: four-digit Family
counts, a four-digit busiest day and a seven-figure pledge total.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The widest realistic report day: 1,234 of 2,345 Families have responded,
# the report day was the busiest so far (1,088 first submissions), and
# pledges total over a million dollars.
FIRST = (23, 123, 1088)
CUMULATIVE = (23, 146, 1234)
DENOMINATOR = 2345
PLEDGES = (Decimal("12345.67"), Decimal("123456.78"), Decimal("1234567.89"))


def worst_case_html(*, styles=True):
    """The daily report email's HTML part with the widest realistic values.

    ``styles`` False drops the ``<style>`` block, as mail programs that strip
    media queries do, so the card keeps its full 32px side padding.
    """
    from parishkit.stewardship.mail_layout import REPORT_WIDTH, email_document
    from parishkit.stewardship.reports.daily_digest import render_daily_digest

    from ..test_daily_digest_content import document, funnel_metrics

    value = document()
    chart = value.participation
    days = tuple(
        replace(
            day,
            first_responses=FIRST[index],
            cumulative_responses=CUMULATIVE[index],
            cohort_denominator=DENOMINATOR,
            pledge_total=PLEDGES[index],
        )
        for index, day in enumerate(chart.days)
    )
    value = replace(
        value,
        participation=replace(chart, days=days),
        funnel=funnel_metrics(),
        mode="production",
    )
    # Four-digit funnel counts too, in the same rows layout.
    funnel = value.funnel
    stages = tuple(
        replace(stage, count=stage.count + 1345 if stage.count else 0)
        for stage in funnel.stages
    )
    value = replace(value, funnel=replace(funnel, stages=stages))
    content = render_daily_digest(value, public_origin="https://parish.example")
    html = email_document(content.html, family=False, width=REPORT_WIDTH)
    if not styles:
        start, end = html.index("<style>"), html.index("</style>") + len("</style>")
        html = html[:start] + html[end:]
    return html


def assert_fits(page):
    """Neither the page nor any table is wider than the window."""
    widths = page.evaluate("""() => ({
      page: document.documentElement.scrollWidth,
      window: window.innerWidth,
      right: Math.max(...[...document.querySelectorAll("table")].map(
        t => t.getBoundingClientRect().right)),
    })""")
    assert widths["page"] <= widths["window"], widths
    assert widths["right"] <= widths["window"], widths


@pytest.mark.parametrize("width", [320, 375, 1280])
def test_the_email_fits_a_phone_with_four_digit_counts(page, width):
    """The whole email, Campaign totals included, never scrolls sideways (#1564).

    The totals show their exact values: the Families value and the busiest
    day stay readable on one line each when the window allows.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(worst_case_html())
    text = page.inner_text("body")
    assert "1,234 out of 2,345 (52.6%)" in " ".join(text.split())
    assert "1,088 (busiest day: 1,088)" in " ".join(text.split())
    assert "$1,234,567.89" in text
    assert_fits(page)


def test_the_totals_fit_without_the_style_block(page):
    """A mail program that drops the ``<style>`` block keeps 32px card padding.

    The Campaign totals and the funnel still fit a 320px window then. The
    day-by-day table is taken out first: it needs the media query's narrower
    padding (#1559), so it would hide what this test measures.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.set_content(worst_case_html(styles=False))
    page.locator("table:has(> caption)").evaluate("table => table.remove()")
    assert page.locator("h2:text-is('Campaign totals') + table").count() == 1
    assert page.locator("h2:text-is('Response funnel') + table").count() == 1
    assert_fits(page)
