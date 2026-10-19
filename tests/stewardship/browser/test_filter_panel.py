"""Report filter forms sit in a "Filters" panel, closed unless one is applied.

The long filter and export forms of the report pages start collapsed, so a
report's first data is on a laptop's first screen (#227); an applied filter
is never hidden, because the panel then renders open and its summary counts
the filters in use. The Family directory's panel is open by default (#227).
Census changes, a worklist, has an always-open Filters box instead (#1682,
``test_census_layout``); its open box is measured here with the others.
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
# Additional information and Limitations are review worklists (#1685), whose
# filters are always open, as Census changes' are.
UNFILTERED = [
    "/financial-unfiltered",
]
FILTERED = {
    "/family-directory": 1,
    "/financial-report": 3,
}


@pytest.mark.parametrize("path", UNFILTERED)
def test_unfiltered_report_starts_with_filters_and_export_closed(
    page, component_origin, path
):
    """With no filter applied the Filters panel is closed; the export row
    (#1679) is shown."""
    from playwright.sync_api import expect

    page.goto(component_origin + path)
    panel = page.locator("details[data-filter-panel]")
    expect(panel).to_have_count(1)
    expect(panel).not_to_have_attribute("open", "")
    has_text(panel.locator("summary"), "Filters: none applied")
    expect(page.get_by_role("button", name="Apply filters")).to_be_hidden()
    # The export is a row of buttons, not a disclosure (#1679).
    expect(page.locator("details.export-panel")).to_have_count(0)
    visible(page.get_by_role("button", name="Export XLSX"))
    # The panel is a native disclosure: it opens with a click, below its own
    # summary line, which stays where it was on the page (a click first
    # scrolls a summary below the window into view, which is not a shift).
    summary = panel.locator("summary")
    place = "node => node.getBoundingClientRect().top + scrollY"
    before = summary.evaluate(place)
    summary.click()
    visible(page.get_by_role("button", name="Apply filters"))
    assert summary.evaluate(place) == pytest.approx(before, abs=0.5)


def test_family_directory_starts_with_filters_open_and_export_closed(
    page, component_origin
):
    """The Family directory opens Filters by default (#227); its export is
    the row of buttons below the table (#1679)."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/directory-unfiltered")
    panel = page.locator("details[data-filter-panel]")
    expect(panel).to_have_attribute("open", "")
    has_text(panel.locator("summary"), "Filters: none applied")
    visible(page.get_by_role("button", name="Apply filters"))
    expect(page.locator("details.export-panel")).to_have_count(0)
    visible(page.get_by_role("button", name="Export XLSX"))


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
    # The Family directory's panel is already open; no click is needed.
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


# The first data each report page shows (the Family directory and Census
# changes show their filters open, as the Additional information and
# Limitations worklists do since #1685, so they are not here): the summary of
# Financial stewardship, which sits above its filters.
FIRST_DATA = {
    "/financial-unfiltered": ".stat-tiles",
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


# The filtered fixtures arrive with their panel open (an applied filter is
# never hidden), and Census changes' box is always open, so each report's
# open filter form can be measured directly.
OPEN_PANELS = sorted({*FILTERED, "/census-changes-automatic-filtered"})
# The most each open panel may take on a laptop once its filters share the
# rows of a grid (#1411). With one field per row, as before, Additional
# information's took about 690 px and Financial stewardship's about 1,460;
# the grid takes about 310 and 470 (Census changes about 260), and the
# limits leave room for wider fonts. The Family directory keeps its filter
# bar, so it is not here.
OPEN_PANEL_HEIGHT = {
    "/census-changes-automatic-filtered": 340,
    "/financial-report": 700,
}
# The left and bottom edges of each field cell in the open panel's form,
# rounded to whole pixels: the cells of a row align at the bottom.
CELLS = """form => [...form.children].filter((node) => node.matches("div, fieldset"))
    .map((node) => {
        const box = node.getBoundingClientRect();
        return [Math.round(box.left), Math.round(box.bottom)];
    })"""


@pytest.mark.parametrize("path", sorted(OPEN_PANEL_HEIGHT))
def test_open_filters_are_a_compact_grid(page, component_origin, path):
    """On a laptop the filters share rows, so the open panel stays short.

    The panel's form is a grid of the fields (#1411): at least three fields
    share its first row. Where Apply filters and Clear filters sit is
    ``test_filter_actions_place``'s (#1776).
    """
    page.set_viewport_size(LAPTOP_VIEWPORT)
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    panel = page.locator("[data-filter-panel]")
    height = panel.evaluate("node => node.getBoundingClientRect().height")
    assert height <= OPEN_PANEL_HEIGHT[path], height
    form = page.locator("#table-filters")
    cells = form.evaluate(CELLS)
    first_row = [left for left, bottom in cells if abs(bottom - cells[0][1]) <= 1]
    assert len(first_row) >= 3, cells


@pytest.mark.parametrize("path", OPEN_PANELS)
def test_open_filters_take_one_column_on_a_phone(page, component_origin, path):
    """At phone width each filter takes its own row, and nothing scrolls sideways."""
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(component_origin + path)
    page.evaluate("document.fonts.ready.then(() => true)")
    cells = page.locator("#table-filters").evaluate(CELLS)
    assert len({left for left, _top in cells}) == 1, cells
    assert len({bottom for _left, bottom in cells}) == len(cells), cells
    # clientWidth leaves out a classic scrollbar, which innerWidth counts.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
