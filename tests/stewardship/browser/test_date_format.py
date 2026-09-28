"""The browser date formatter matches the server's, for every parish style (#221)."""

from datetime import date, datetime

import pytest

from parishkit.stewardship.web import dates

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
# The page fixture's browser zone; the server table is checked in that zone.
ZONE = "America/Los_Angeles"
INSTANTS = ("2027-01-05T20:04:00+00:00", "2026-07-04T07:30:00+00:00")


def browser_text(page, style, value, *, compact=False):
    """ParishDates.instant() for one style, as a page with that style runs it."""
    return page.evaluate(
        """([style, value, compact]) => {
            document.body.dataset.dateFormat = style;
            return ParishDates.instant(new Date(value), {compact});
        }""",
        [style, value, compact],
    )


def test_every_style_matches_the_python_formatter(page, component_origin):
    """Full and compact timestamps and calendar dates, style by style."""
    page.goto(component_origin + "/logs")
    for style in dates.FORMATS:
        for value in INSTANTS:
            moment = datetime.fromisoformat(value)
            assert browser_text(page, style, value) == dates.format_instant(
                moment, ZONE, style
            )
            assert browser_text(
                page, style, value, compact=True
            ) == dates.format_instant(moment, ZONE, style, compact=True)
        calendar = page.evaluate(
            """(style) => {
                document.body.dataset.dateFormat = style;
                return [ParishDates.date("2027-01-05"),
                    ParishDates.date("2027-01-05", {compact: true})];
            }""",
            style,
        )
        day = date(2027, 1, 5)
        assert calendar == [
            dates.format_date(day, style),
            dates.format_date(day, style, compact=True),
        ]


def test_pages_localize_with_the_body_style_and_compact_tables(page, component_origin):
    """The page's own style drives every <time>; dense log rows use compact."""
    page.goto(component_origin + "/logs")
    assert page.evaluate("document.body.dataset.dateFormat") == "us_long"
    cell = page.locator("time[data-local-instant][data-compact]").first
    moment = datetime.fromisoformat(cell.get_attribute("datetime"))
    assert cell.inner_text() == dates.format_instant(
        moment, ZONE, "us_long", compact=True
    )
    page.evaluate(
        """() => {
            document.body.dataset.dateFormat = "eu_dot";
            ParishDates.localize(document);
        }"""
    )
    assert cell.inner_text() == dates.format_instant(
        moment, ZONE, "eu_dot", compact=True
    )
    # An unknown style falls back to the default rather than failing.
    assert browser_text(page, "fr_FR", INSTANTS[0]) == dates.format_instant(
        datetime.fromisoformat(INSTANTS[0]), ZONE, "us_long"
    )
