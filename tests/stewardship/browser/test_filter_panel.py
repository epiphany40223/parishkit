"""Report filter forms sit in a "Filters" panel, closed unless one is applied.

The long filter and export forms of the report pages start collapsed, so a
report's first data is on a laptop's first screen (#227); an applied filter
is never hidden, because the panel then renders open and its summary counts
the filters in use.
"""

import pytest

from .waits import has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"

# Each report as first opened, with no filter applied, and the same report
# answering an applied filter (the fixtures' searches and choices), with the
# number of filters each fixture applies.
UNFILTERED = [
    "/directory-unfiltered",
    "/information-unfiltered",
    "/financial-unfiltered",
]
FILTERED = {"/family-directory": 1, "/information": 2, "/financial-report": 3}


@pytest.mark.parametrize("path", UNFILTERED)
def test_unfiltered_report_starts_with_filters_and_export_closed(
    page, component_origin, path
):
    """With no filter applied the Filters and export panels are both closed."""
    from playwright.sync_api import expect

    page.goto(component_origin + path)
    panel = page.locator("details[data-filter-panel]")
    expect(panel).to_have_count(1)
    expect(panel).not_to_have_attribute("open", "")
    has_text(panel.locator("summary"), "Filters: none applied")
    expect(page.get_by_role("button", name="Apply filters")).to_be_hidden()
    expect(page.locator("details.export-panel")).not_to_have_attribute("open", "")
    # The panel is a native disclosure: it opens with a click, below its own
    # summary line, which stays where it was on the page (a click first
    # scrolls a summary below the window into view, which is not a shift).
    summary = panel.locator("summary")
    place = "node => node.getBoundingClientRect().top + scrollY"
    before = summary.evaluate(place)
    summary.click()
    visible(page.get_by_role("button", name="Apply filters"))
    assert summary.evaluate(place) == pytest.approx(before, abs=0.5)


@pytest.mark.parametrize("path", sorted(FILTERED))
def test_applied_filters_keep_the_panel_open_with_their_count(
    page, component_origin, path
):
    """A filter in use is never hidden: the panel arrives open and counted."""
    from playwright.sync_api import expect

    page.goto(component_origin + path)
    panel = page.locator("details[data-filter-panel]")
    expect(panel).to_have_attribute("open", "")
    has_text(panel.locator("summary"), f"Filters: {FILTERED[path]} applied")
    visible(page.get_by_role("button", name="Apply filters"))


def test_in_place_apply_updates_the_count_and_keeps_the_panel(page, component_origin):
    """Apply answers in place: the open panel stays open and its count follows."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/directory-unfiltered")
    page.evaluate(MARK)
    panel = page.locator("details[data-filter-panel]")
    panel.locator("summary").click()
    page.get_by_label("Search by Family name", exact=False).fill("Example")
    # The answer is the fixture for a search, which applies one filter.
    page.route(
        "**/families/",
        lambda route: route.fulfill(
            response=route.fetch(
                url=component_origin + "/family-directory", method="GET"
            )
        ),
    )
    page.get_by_role("button", name="Apply filters").click()
    has_text(page.locator("#table-filters-applied"), "1 applied")
    expect(panel).to_have_attribute("open", "")
    assert page.evaluate(MARKED) == "kept"


# The first data each report page shows: the directory's and Additional
# information's matching count, and the summary of Financial stewardship and
# Limitations, which sits above their filters.
FIRST_DATA = {
    "/directory-unfiltered": "#directory-summary",
    "/information-unfiltered": "#information-matching",
    "/financial-unfiltered": ".stat-tiles",
    "/limitations-report": "#limitations-summary dl",
}
LAPTOP_VIEWPORT = {"width": 1366, "height": 650}
# Fonts differ between platforms (Linux CI's are wider), so the check is
# measured from the page heading, not the window's top: the Admin chrome
# above the heading (breadcrumbs, the Testing banner) takes about 200 px of
# the 650, so the first data must start within 400 px of the heading.
BELOW_HEADING = 400


@pytest.mark.parametrize("path", sorted(FIRST_DATA))
def test_report_first_data_is_near_the_heading(page, component_origin, path):
    """With help, filters and export collapsed, a report's data comes first."""
    page.set_viewport_size(LAPTOP_VIEWPORT)
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    heading = page.locator("main h1").bounding_box()
    data = page.locator(FIRST_DATA[path]).first.bounding_box()
    assert data is not None and heading is not None
    assert data["y"] - heading["y"] <= BELOW_HEADING, (path, heading, data)


# The width of a select's chosen text as drawn in its own font, and the room
# its box leaves for it (inside its padding, which holds the arrow).
CHOSEN_TEXT_FITS = """select => {
    const style = getComputedStyle(select);
    const context = document.createElement("canvas").getContext("2d");
    context.font = style.font;
    const text = context.measureText(select.selectedOptions[0].text).width;
    const room = select.clientWidth - parseFloat(style.paddingLeft)
        - parseFloat(style.paddingRight);
    return [text, room];
}"""


@pytest.mark.parametrize("width", [320, 1366])
def test_limitations_show_choice_is_not_clipped(page, component_origin, width):
    """The Show menu is as wide as its longest choice, so each reads whole.

    On a 320 px phone the row itself is narrower than "Cannot participate in
    ministries" with the menu's arrow (as it was before the filter bar), so
    there the menu must take the whole row and the short choice must fit;
    the long choices are the report's own terms and keep their wording.
    """
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + "/limitations-report")
    show = page.get_by_label("Show", exact=True)
    row = page.locator("#table-filters").evaluate("node => node.clientWidth")
    for value in ("any", "cannot_serve", "cannot_attend"):
        show.select_option(value)
        text, room = show.evaluate(CHOSEN_TEXT_FITS)
        if width >= 1024 or value == "any":
            assert text <= room, (value, text, room)
        else:
            assert show.evaluate("node => node.offsetWidth") >= row - 1
