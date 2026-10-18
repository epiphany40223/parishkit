"""Census changes is laid out as a worklist (#1682).

In Chromium and WebKit, light and dark, on a laptop and a 390 px phone. The
real page is served by ``census_components``. There is no summary above the
table; the filters are an always-open box (no disclosure) whose Status
choices carry the counts the summary showed; and the download is a pane
below the table, always shown. An in-place Apply keeps the box and the page,
and its count follows.
"""

import pytest

from .census_components import AUTOMATIC, PATH
from .test_table_sorting import MARK, MARKED
from .waits import has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# The page's top-to-bottom order: the filters' box, the table's box and the
# download pane, by the top and bottom of each in page coordinates (the
# last of each: the table's navigator below it).
ORDER = """() => {
  const box = (selector) => {
    const all = document.querySelectorAll(selector);
    const edges = all[all.length - 1].getBoundingClientRect();
    return [edges.top + scrollY, edges.bottom + scrollY];
  };
  return {
    filters: box('[data-filter-panel]'),
    table: box('#table .table-scroll'),
    navigator: box('#table nav.table-nav'),
    download: box('section.export-pane'),
  };
}"""
# The computed background of an element and of the page, so the boxes are
# drawn in the scheme's own paper colour rather than a fixed light one.
BACKGROUNDS = """(selector) => [
  getComputedStyle(document.querySelector(selector)).backgroundColor,
  getComputedStyle(document.body).backgroundColor,
]"""


def luminance(color):
    """The rough brightness (0 to 255) of a computed ``rgb(...)`` colour."""
    red, green, blue = (
        float(part) for part in color.split("(", 1)[1].split(")")[0].split(",")[:3]
    )
    return 0.299 * red + 0.587 * green + 0.114 * blue


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 1366])
def test_filters_open_and_download_below_the_table(
    page, component_origin, scheme, width
):
    """No summary; filters always open; the download pane below the table."""
    from playwright.sync_api import expect

    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + AUTOMATIC)
    page.evaluate("document.fonts.ready.then(() => true)")
    expect(page.locator("#census-summary")).to_have_count(0)
    # The filters are a titled box, not a disclosure: nothing to open, and
    # Apply is in view from the start.
    panel = page.locator("section[data-filter-panel]")
    expect(panel).to_have_count(1)
    expect(page.locator("details[data-filter-panel]")).to_have_count(0)
    expect(panel.locator("summary")).to_have_count(0)
    has_text(panel.get_by_role("heading", level=2), "Filters: none applied")
    visible(page.get_by_role("button", name="Apply filters"))
    # Each Status choice carries its count (#1682), the counts the summary
    # showed; the default list is Work to do.
    status = page.get_by_label("Status", exact=True)
    expect(status).to_have_value("open")
    choices = status.locator("option").all_inner_texts()
    assert choices[0].startswith("Work to do (") and choices[-1].startswith(
        "Every status ("
    ), choices
    assert all(choice.endswith(")") for choice in choices), choices
    # The download is always shown, below the table and its page navigator.
    download = page.locator("section.export-pane")
    expect(page.locator("details.export-panel")).to_have_count(0)
    has_text(download.get_by_role("heading", level=2), "Download (CSV, XLSX or PDF)")
    visible(page.get_by_role("button", name="Download with these filters"))
    order = page.evaluate(ORDER)
    assert order["filters"][1] <= order["table"][0], order
    assert order["table"][1] <= order["navigator"][0] + 1, order
    assert order["navigator"][1] <= order["download"][0], order
    # Both boxes take the scheme's own colours: darker than light paper in
    # dark mode, light in light mode.
    for selector in ("section[data-filter-panel]", "section.export-pane"):
        box, _body = page.evaluate(BACKGROUNDS, selector)
        if scheme == "dark":
            assert luminance(box) < 100, (selector, box)
        else:
            assert luminance(box) > 200, (selector, box)
    # Nothing scrolls the page sideways at a phone's width.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


def test_in_place_apply_keeps_the_open_box(page, component_origin):
    """Apply answers in place: the box stays, open, and its count follows."""
    from playwright.sync_api import expect

    page.goto(component_origin + AUTOMATIC)
    page.evaluate(MARK)
    panel = page.locator("section[data-filter-panel]")
    panel.evaluate("node => { node.dataset.kept = 'yes'; }")
    page.get_by_label("How it reaches ParishSoft").select_option("automatic")

    def answer(route):
        """Answer the filters' post with the page they ask for."""
        if route.request.method != "POST":
            route.continue_()
            return
        route.fulfill(
            response=route.fetch(
                url=component_origin + "/census-changes-automatic-filtered",
                method="GET",
            )
        )

    page.route("**" + PATH, answer)
    page.get_by_role("button", name="Apply filters").click()
    has_text(page.locator("#table-filters-applied"), "1 applied")
    expect(panel).to_have_attribute("data-kept", "yes")
    visible(page.get_by_role("button", name="Apply filters"))
    # The download's hidden filters follow the applied view.
    expect(page.locator("#table-export input[name=route]")).to_have_value("automatic")
    assert page.evaluate(MARKED) == "kept"
