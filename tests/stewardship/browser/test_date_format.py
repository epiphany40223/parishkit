"""The browser date formatter matches the server's, for every parish style (#221)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

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


def precise(moment, style):
    """A System logs time: compact date, clock with seconds, zone and UTC offset."""
    local = moment.astimezone(ZoneInfo(ZONE))
    if style.startswith("us_"):
        clock = local.strftime(f"{local.hour % 12 or 12}:%M:%S %p")
    else:
        clock = local.strftime("%H:%M:%S")
    offset = local.strftime("%z")
    return (
        f"{dates.format_date(local.date(), style, compact=True)} {clock} "
        f"{local.tzname()} (UTC{offset[:3]}:{offset[3:]})"
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


@pytest.mark.parametrize("value", INSTANTS, ids=["winter", "summer"])
def test_precise_instants_carry_seconds_zone_and_offset(page, component_origin, value):
    """Standard and daylight time both show their own offset, for each style."""
    page.goto(component_origin + "/logs")
    moment = datetime.fromisoformat(value).replace(second=7)
    for style in dates.FORMATS:
        text = page.evaluate(
            """([style, value]) => {
                document.body.dataset.dateFormat = style;
                const options = {compact: true, precise: true};
                return ParishDates.instant(new Date(value), options);
            }""",
            [style, moment.isoformat()],
        )
        assert text == precise(moment, style)


# The 2026 Los Angeles transitions: the repeated 1:30 AM in November (daylight,
# then standard) and the last standard second before the March jump to 3 AM.
TRANSITIONS = {
    "fall-back-first": ("2026-11-01T08:30:05+00:00", "1:30:05 AM PDT (UTC-07:00)"),
    "fall-back-second": ("2026-11-01T09:30:05+00:00", "1:30:05 AM PST (UTC-08:00)"),
    "spring-forward-before": (
        "2026-03-08T09:59:59+00:00",
        "1:59:59 AM PST (UTC-08:00)",
    ),
    "spring-forward-after": ("2026-03-08T10:00:00+00:00", "3:00:00 AM PDT (UTC-07:00)"),
}


@pytest.mark.parametrize("moment", sorted(TRANSITIONS))
def test_precise_instants_across_daylight_saving_transitions(
    page, component_origin, moment
):
    """The ambiguous and skipped hours name their own zone and offset."""
    value, clock = TRANSITIONS[moment]
    page.goto(component_origin + "/logs")
    text = page.evaluate(
        """(value) => {
            document.body.dataset.dateFormat = "us_long";
            return ParishDates.instant(new Date(value), {compact: true, precise: true});
        }""",
        value,
    )
    assert text.endswith(" " + clock)
    assert text == precise(datetime.fromisoformat(value), "us_long")


def test_pages_localize_with_the_body_style_and_compact_tables(page, component_origin):
    """The page's own style drives every <time>; dense log rows use compact."""
    page.goto(component_origin + "/logs")
    assert page.evaluate("document.body.dataset.dateFormat") == "us_long"
    cell = page.locator("time[data-local-instant][data-compact]").first
    moment = datetime.fromisoformat(cell.get_attribute("datetime"))
    # Log rows are also precise: seconds, zone name and UTC offset (#391 M3).
    assert cell.get_attribute("data-precise") is not None
    assert cell.inner_text() == precise(moment, "us_long")
    page.evaluate(
        """() => {
            document.body.dataset.dateFormat = "eu_dot";
            ParishDates.localize(document);
        }"""
    )
    assert cell.inner_text() == precise(moment, "eu_dot")
    # Without data-precise, a compact cell keeps the short form.
    for style in ("us_long", "eu_dot"):
        assert browser_text(
            page, style, INSTANTS[0], compact=True
        ) == dates.format_instant(
            datetime.fromisoformat(INSTANTS[0]), ZONE, style, compact=True
        )
    # An unknown style falls back to the default rather than failing.
    assert browser_text(page, "fr_FR", INSTANTS[0]) == dates.format_instant(
        datetime.fromisoformat(INSTANTS[0]), ZONE, "us_long"
    )
