"""The shared export row on representative Admin pages (#1679).

Each export is one row of small outlined "Export XLSX" … buttons below the
page's table, quieter than the primary green buttons, wrapping at phone
width without widening the page, in light and dark schemes. No row asks for
a time zone except System logs' "Times in", which starts on the browser's
zone. How each export downloads in place, busy and waiting below its row, is
test_export_in_place.py's (#1778).
"""

import pytest

from .test_charts import assert_clean, watch
from .timeline_components import ADMIN as TIMELINE
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# Representative pages: a direct download with a zone choice (System logs),
# queued report exports below their tables, a direct download below two
# tables, the queue beside the packet's own row, and a document download.
PAGES = {
    "/logs": "Export these log entries",
    "/financial-report": "Export complete results",
    "/family-directory": "Export complete results",
    "/limitations-report": "Export both tables",
    "/followup-queue": "Export these requests",
    "/campaign-comparison": "Download",
    TIMELINE: "Export this timeline",
}
# Read one box's computed look: [height, font size, background, text colour].
LOOK = """node => { const s = getComputedStyle(node);
    return [node.getBoundingClientRect().height, parseFloat(s.fontSize),
            s.backgroundColor, s.color]; }"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("path", sorted(PAGES))
def test_the_row_is_small_secondary_and_fits(
    page, component_origin, path, width, scheme
):
    """Small outlined buttons, one per format, that fit the window."""
    page.set_viewport_size({"width": width, "height": 900})
    page.emulate_media(color_scheme=scheme)
    errors = watch(page)
    page.goto(component_origin + path)
    row = page.get_by_role("group", name=PAGES[path], exact=True)
    visible(row)
    buttons = row.locator(".export-buttons button")
    assert buttons.count() == 3
    labels = buttons.all_inner_texts()
    assert all(label.startswith("Export ") for label in labels), labels
    # Smaller than the portal's primary button (a 44px target) and in the
    # secondary look: never the primary's filled accent background.
    primary = page.evaluate(
        """() => { const b = document.createElement("button");
        document.querySelector("main").append(b); const s = getComputedStyle(b);
        const look = [b.getBoundingClientRect().height, parseFloat(s.fontSize),
        s.backgroundColor]; b.remove(); return look; }"""
    )
    for index in range(3):
        height, size, background, color = buttons.nth(index).evaluate(LOOK)
        assert 24 <= height < primary[0], (labels[index], height)
        assert size < primary[1]
        assert background != primary[2] and background != color
    # Wrapping, never a sideways scroll; one line on a desktop.
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    if width == 1280:
        tops = {round(buttons.nth(index).bounding_box()["y"]) for index in range(3)}
        assert len(tops) == 1
    # Below the page's table, where it has one.
    tables = page.locator("main table")
    if tables.count():
        bottom = max(
            box["y"] + box["height"]
            for box in (tables.nth(i).bounding_box() for i in range(tables.count()))
            if box
        )
        assert row.bounding_box()["y"] >= bottom - 1
    assert_clean(page, errors)


@pytest.mark.parametrize("path", sorted(PAGES))
def test_only_system_logs_has_a_zone_choice(page, component_origin, path):
    """Every row posts the browser's zone unasked; System logs' "Times in"
    starts on it, with UTC the other choice."""
    page.goto(component_origin + path)
    row = page.get_by_role("group", name=PAGES[path], exact=True)
    visible(row)
    form = row.locator("xpath=ancestor::form[1]")
    if path != "/logs":
        assert row.locator("select").count() == 0
        hidden = form.locator("input[name=browser_timezone]")
        assert hidden.count() == 1
        assert hidden.input_value() == "America/Los_Angeles"
        return
    zone = row.get_by_label("Times in")
    assert zone.input_value() == "America/Los_Angeles"
    assert zone.locator("option").all_inner_texts() == [
        "UTC",
        "America/Los_Angeles (this browser)",
    ]
    assert form.locator("input[name=browser_timezone]").count() == 0
