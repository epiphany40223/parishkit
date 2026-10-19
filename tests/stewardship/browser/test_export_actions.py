"""The shared export row on representative Admin pages (#1679).

Each export is one row of small outlined "Export XLSX" … buttons below the
page's table, quieter than the primary green buttons, wrapping at phone
width without widening the page, in light and dark schemes. No row asks for
a time zone except System logs' "Times in", which starts on the browser's
zone. A slow queued export keeps its in-place progress: the clicked button
is busy and the waiting note appears below the row, not between buttons.
"""

import pytest
from django.urls import reverse

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


def test_a_slow_export_waits_below_its_row(page, component_origin):
    """The clicked button shows it is busy, the row waits with it, and the
    "Still working" note appears after the row, not between its buttons.

    The export's answer is held past the five seconds after which ui-v1.js
    shows the note, then answered 204 No Content, which keeps the page in
    place so its waiting state can be read.
    """
    page.goto(component_origin + "/financial-report")
    held = []
    page.route(
        "**" + reverse("admin:financial_export"), lambda route: held.append(route)
    )
    row = page.get_by_role("group", name="Export complete results", exact=True)
    csv = row.get_by_role("button", name="Export CSV")
    # Clicked from the page, so the test does not wait on the held answer.
    csv.evaluate("button => button.click()")
    page.wait_for_timeout(6000)
    assert len(held) == 1 and "format=csv" in held[0].request.post_data
    held[0].fulfill(status=204)
    assert "is-busy" in csv.get_attribute("class")
    for name in ("Export XLSX", "Export PDF"):
        other = row.get_by_role("button", name=name)
        assert other.get_attribute("aria-disabled") == "true"
    note = page.locator(".submit-wait")
    visible(note)
    assert note.evaluate(
        "node => node.previousElementSibling.matches('.export-actions')"
    )
    assert row.locator(".submit-wait").count() == 0
