"""The Ministry follow-up queue page is compact (#1064).

Its six filters take two rows at laptop width and one row each at phone
width; every row of a full page of requests is one line, with the long
explanations kept to tooltips, screen-reader text and the request's own
page; and applying the filters in place moves no filter control.
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

PAGE = "/followup-page"
FILTERS = "#table-filters"
# The filter form's height at 1280 px. Before #1064 each of its six fields
# had its own label line and row, about 625 px; in the filter bar it is
# about 160 px.
MAX_FILTER_HEIGHT = 220
# A page of 25 requests at 1280 px. Before #1064 the rows were from about
# 320 to 1,170 px tall each (about 19,400 px in all), since the cells were
# squeezed until they broke between letters; one line each, they are about
# 43 px each (about 1,075 px in all).
MAX_ROWS_HEIGHT = 1400
FIRST_ROW = ("#followup-search", "#filter-ministry", "#filter-action")
SECOND_ROW = ("#filter-state", "#filter-outcome", "#filter-history")
CONTROLS = FIRST_ROW + SECOND_ROW + (f"{FILTERS} button[type=submit]",)
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Each row's height, and the most a one-line row may be: its cells'
# vertical padding plus under two lines of text, measured on the one-word
# "Join" in a join request's Request cell, whatever the engine's font.
ROWS = """() => {
    const rows = [...document.querySelectorAll('tbody tr')];
    const cell = [...document.querySelectorAll('tbody td')].find(
        (td) => td.textContent.trim() === 'Join');
    const range = document.createRange();
    range.selectNodeContents(cell);
    const style = getComputedStyle(cell);
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    return {
        heights: rows.map((row) => row.getBoundingClientRect().height),
        oneLine: padding + 2 * range.getBoundingClientRect().height,
    };
}"""


def boxes(page, selectors):
    """The bounding boxes of the controls ``selectors`` names, in order."""
    return [page.locator(selector).bounding_box() for selector in selectors]


def test_queue_is_compact_at_laptop_width(page, component_origin, axe_source):
    """At 1280 px Search, Ministry and Request share the first filter row
    and Status, Outcome, Request history and Apply filters the second; the
    filter form stays within its height bound; and a page of 25 requests
    with long names, every status and both markers is one line per row."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.locator(FILTERS))
    assert page.locator(FILTERS).bounding_box()["height"] <= MAX_FILTER_HEIGHT
    first, second = boxes(page, FIRST_ROW), boxes(page, SECOND_ROW)
    for row in (first, second):
        assert max(box["y"] for box in row) - min(box["y"] for box in row) <= 1, row
    assert min(box["y"] for box in second) > max(
        box["y"] + box["height"] for box in first
    )
    # Apply filters ends the second row, its bottom level with the fields'.
    apply = page.locator(f"{FILTERS} button[type=submit]").bounding_box()
    assert apply["x"] > max(box["x"] for box in second)
    assert (
        abs((apply["y"] + apply["height"]) - (second[0]["y"] + second[0]["height"]))
        <= 1
    )
    rows = page.evaluate(ROWS)
    assert len(rows["heights"]) == 25
    assert max(rows["heights"]) < rows["oneLine"], rows
    assert sum(rows["heights"]) <= MAX_ROWS_HEIGHT, rows["heights"]
    # The page itself never scrolls sideways; the table may, in its box.
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_queue_cells_hold_one_short_value(page, component_origin):
    """Request, Status and Ministry hold one short value each; the long
    explanations stay available as tooltips and screen-reader text."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    cells = page.locator("tbody tr").nth(0).locator("th, td")
    assert cells.nth(4).inner_text() == "Leave (cannot participate)"
    marker = cells.nth(4).locator("span[title]")
    assert marker.get_attribute("title") == (
        "Cannot participate in any ministries (physical limitations)"
    )
    statuses = page.locator("tbody tr td:nth-child(6)").all_inner_texts()
    assert "Resolved: Declined / no longer interested" in statuses
    assert "Resolved: Joined ministry" in statuses
    # Closed: no response always records No response; it is not repeated.
    assert "Closed: no response" in statuses
    assert not any("No response" in text for text in statuses)
    # The Ministry removed from the campaign (row 7) carries its badge,
    # whose words a screen reader hears in full.
    removed = page.locator("tbody tr").nth(6).locator("td").nth(1)
    badge = removed.locator(".status-badge")
    assert badge.get_attribute("title") == "No longer in this campaign"
    assert badge.locator(".visually-hidden").text_content() == (
        "No longer in this campaign"
    )
    # The dates use the compact form: no "at" and no time zone name.
    submitted = page.locator("tbody tr td:nth-child(7)").first.inner_text()
    assert " at " not in submitted and not submitted.endswith(("PDT", "PST"))


def test_queue_request_page_states_the_whole_reason(page, component_origin):
    """The request page writes out what the queue's marker abbreviates."""
    page.goto(component_origin + "/followup-item-leave")
    visible(
        page.get_by_text(
            "Asked to leave (cannot participate in any ministries "
            "(physical limitations))",
            exact=False,
        )
    )


def test_queue_is_one_column_at_phone_width(page, component_origin, axe_source):
    """At 320 px each filter takes its own row, one under another with
    nothing overlapping, the page never scrolls sideways, and the rows stay
    one line, the table scrolling in its own box."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.locator(FILTERS))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    found = boxes(page, CONTROLS)
    for upper, lower in zip(found, found[1:], strict=False):
        assert lower["y"] >= upper["y"] + upper["height"], (upper, lower)
    rows = page.evaluate(ROWS)
    assert max(rows["heights"]) < rows["oneLine"], rows
    assert page.locator(".table-scroll").evaluate("e => e.scrollWidth > e.clientWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_queue_filters_stay_put_when_applied_in_place(page, component_origin):
    """Applying the filters replaces the table in place (the queue's server
    answer is the one-request fixture page) and moves no filter label or
    control: no layout shift."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    layout = f"""() => [...document.querySelectorAll(
        '{FILTERS} :is(label, input:not([type=hidden]), select, button)')]
        .map((item) => {{
            const box = item.getBoundingClientRect();
            return [item.id || item.textContent.trim(), box.x + scrollX,
                box.y + scrollY, box.width, box.height];
        }})"""
    before = page.evaluate(layout)
    body = page.request.get(component_origin + "/followup-queue").text()
    page.route(
        "**/follow-up/",
        lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=body),
    )
    page.evaluate("document.querySelector('h1').dataset.mark = 'kept'")
    page.get_by_role("button", name="Apply filters").click()
    page.wait_for_function("document.querySelectorAll('tbody tr').length === 1")
    # Applied in place: a full page load would have lost the mark.
    assert page.evaluate("document.querySelector('h1').dataset.mark") == "kept"
    after = page.evaluate(layout)
    assert [entry[0] for entry in after] == [entry[0] for entry in before]
    for old, new in zip(before, after, strict=True):
        assert all(abs(a - b) <= 1 for a, b in zip(old[1:], new[1:], strict=True)), (
            old,
            new,
        )
