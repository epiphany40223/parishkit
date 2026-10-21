"""The daily report email's own layout, on a desktop and a phone (#1844).

On a desktop each Campaign total and each day is one line: label, bar and
value side by side, the date leading its day's row. Mail programs cannot
scroll a table sideways, so on a 320px phone the email must still fit
(#1559, #1564); mail programs that keep the email's <style> block restack
the rows there. The tests load the complete HTML part (``email_document``
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


# The number of lines each Campaign total's label and value, and each
# visible date in the day table, are drawn on, with the top of the label's
# and the value's first line of text, so a test can tell one-line rows
# from stacked ones.
LINES = """() => {
  const tops = element => {
    const range = document.createRange();
    range.selectNodeContents(element);
    return [...new Set([...range.getClientRects()].filter(r => r.width).map(
      r => Math.round(r.top)))];
  };
  const lines = element => tops(element).length;
  const totals = document.evaluate(
    "//h2[.='Campaign totals']/following-sibling::table[1]",
    document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null,
  ).singleNodeValue;
  const rows = [...totals.tBodies[0].rows].map(row => {
    const [label, figure] = row.cells;
    const value = figure.querySelector(".pk-wrap");
    return {
      label: lines(label), value: lines(value),
      top: [label, value].map(cell => tops(cell)[0]),
    };
  });
  const dates = [...document.querySelectorAll(
    "table:has(> caption) tbody th")].filter(th => th.offsetParent).map(
    th => ({scope: th.scope, lines: lines(th)}));
  return {rows, dates};
}"""


@pytest.mark.parametrize("styles", [True, False])
@pytest.mark.parametrize("width", [640, 1024])
def test_each_total_and_day_is_one_line_on_a_desktop(page, styles, width):
    """On a desktop every figure and every day is one line (#1844).

    Label, bar and value share their row; the date leads its day's row and
    does not wrap. 640px is just above the phone breakpoint (620px). The
    same holds where a mail program drops the <style> block, since the
    desktop layout is the default.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.set_content(worst_case_html(styles=styles))
    lines = page.evaluate(LINES)
    assert len(lines["rows"]) == 3
    for row in lines["rows"]:
        assert row["label"] == row["value"] == 1, lines
        assert row["top"][0] == row["top"][1], lines
    assert lines["dates"] == [{"scope": "row", "lines": 1}] * 3, lines
    assert_fits(page)


def test_a_phone_stacks_each_total_and_heads_each_day_with_its_date(page):
    """At 320px the label sits above its bar and value; the date row shows.

    The date column is hidden, so each date is shown once, on its own row
    across the values (#1559).
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.set_content(worst_case_html())
    lines = page.evaluate(LINES)
    for row in lines["rows"]:
        assert row["top"][0] < row["top"][1], lines
    assert lines["dates"] == [{"scope": "rowgroup", "lines": 1}] * 3, lines
    assert_fits(page)


def test_without_the_style_block_the_desktop_layout_stays_bounded(page):
    """A mail program that drops the <style> block shows the desktop layout.

    It cannot restack for a phone, so a 320px window scrolls sideways (the
    spec records this trade-off, #1844), but the email stays within about
    560px, the one-line totals with four-digit values. This keeps a later
    change from widening it quietly.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.set_content(worst_case_html(styles=False))
    assert page.evaluate("document.documentElement.scrollWidth") <= 580
