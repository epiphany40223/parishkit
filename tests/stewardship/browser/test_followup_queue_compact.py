"""Ministry requests' review worklist stays compact (#1064, #1684).

Its five filters take two rows at laptop width and one row each at phone
width; a row to review with a short Ministry name is one line, and no row
is taller than four (a decided row's stage and decision, a long Ministry
name wrapping onto two, #1136), with the long explanations kept to
tooltips, screen-reader text and the request's own page; the table
scrolls sideways in its own box, never the page; and applying the filters
in place moves no filter control.
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
# 43 px each (about 1,075 px in all). Since #1136 the 12 rows with a long
# Ministry name take two lines, about 67 px each.
MAX_ROWS_HEIGHT = 1800
FIELDS = (
    "#requests-stage",
    "#followup-search",
    "#filter-ministry",
    "#filter-action",
    "#filter-members",
)
# Apply filters, not the Clear filters beside it (#1651).
APPLY = f"{FILTERS} button[type=submit]:not([data-clear-filters])"
CONTROLS = FIELDS + (APPLY,)
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
# Each row's height and Ministry name, and the most a one-line row may be:
# its cells' vertical padding plus under two lines of text, measured on the
# one-word "Join" in a join request's Request cell, whatever the engine's
# font; a two-line row stays under three.
ROWS = """() => {
    const rows = [...document.querySelectorAll('#table table tbody tr')];
    const cell = [...document.querySelectorAll('#table table tbody td')].find(
        (td) => td.textContent.trim() === 'Join');
    const range = document.createRange();
    range.selectNodeContents(cell);
    const style = getComputedStyle(cell);
    const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const line = range.getBoundingClientRect().height;
    return {
        heights: rows.map((row) => row.getBoundingClientRect().height),
        ministries: rows.map((row) => row.querySelector('.cell-clamp').textContent),
        // One line beside the row's action icons, which are taller than text.
        oneLine: padding + 3 * line,
        twoLines: padding + 5 * line,
    };
}"""
# Each Ministry name's text and whether it is cut short (#1136).
MINISTRY_NAMES = """() => [
    ...document.querySelectorAll('#table table tbody .cell-clamp')
].map((name) => [
        name.textContent,
        name.scrollHeight > name.clientHeight + 1
            || name.scrollWidth > name.clientWidth + 1,
    ])"""


def short_rows(rows):
    """The heights of the rows to review whose Ministry name is one short word
    (every 7th row from the first is New, #1684's To review)."""
    return [
        height
        for index, (height, name) in enumerate(
            zip(rows["heights"], rows["ministries"], strict=True)
        )
        if name == "Choir" and index % 7 == 0
    ]


def boxes(page, selectors):
    """The bounding boxes of the controls ``selectors`` names, in order."""
    return [page.locator(selector).bounding_box() for selector in selectors]


def test_queue_is_compact_at_laptop_width(page, component_origin, axe_source):
    """At 1280 px the five filters take at most two rows, Apply filters
    ending the last; the filter form stays within its height bound; and a
    page of 25 requests with long names, every status and both markers is
    one line per row, or two where a long Ministry name wraps (#1136)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.locator(FILTERS))
    assert page.locator(FILTERS).bounding_box()["height"] <= MAX_FILTER_HEIGHT
    fields = boxes(page, FIELDS)
    # Rows of fields, told apart by their tops (within a pixel).
    tops = sorted({round(box["y"]) for box in fields})
    rows_of_fields = [tops[0]] + [
        top for previous, top in zip(tops, tops[1:], strict=False) if top - previous > 1
    ]
    assert len(rows_of_fields) <= 2, tops
    # Apply filters ends the last row, its bottom level with the fields'.
    last = [box for box in fields if box["y"] >= rows_of_fields[-1] - 1]
    apply = page.locator(APPLY).bounding_box()
    assert apply["x"] > max(box["x"] for box in last)
    assert abs((apply["y"] + apply["height"]) - (last[0]["y"] + last[0]["height"])) <= 1
    rows = page.evaluate(ROWS)
    assert len(rows["heights"]) == 25
    assert max(short_rows(rows)) < rows["oneLine"], rows
    assert max(rows["heights"]) < rows["twoLines"], rows
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
    # The first cell is the bulk selection box (#1131); the Family's answer
    # follows Last contact (#1684).
    cells = page.locator("#table table tbody tr").nth(0).locator("th, td")
    assert cells.nth(5).inner_text() == "Leave (cannot participate)"
    marker = cells.nth(5).locator("span[title]")
    assert marker.get_attribute("title") == (
        "Cannot participate in any ministries (physical limitations)"
    )
    stages = [
        " ".join(text.split())
        for text in page.locator(
            "#table table tbody tr td:nth-child(8)"
        ).all_inner_texts()
    ]
    assert "Ignored Declined / no longer interested" in stages
    assert "To enter by hand Accepted" in stages
    # A leave request resolved as declined reads Stayed (#1132), and Closed:
    # no response keeps its outcome as the Ignored decision's label.
    assert "Ignored Stayed in the Ministry" in stages
    assert "Ignored No response" in stages
    # The Ministry removed from the campaign (row 7) carries its badge,
    # whose words a screen reader hears in full.
    removed = page.locator("#table table tbody tr").nth(6).locator("td").nth(2)
    badge = removed.locator(".status-badge")
    # The tooltip is on the visible word, which screen readers skip, so the
    # sentence is announced once.
    assert badge.locator("[aria-hidden=true]").get_attribute("title") == (
        "No longer in this campaign"
    )
    assert badge.locator(".visually-hidden").text_content() == (
        "No longer in this campaign"
    )
    # Last contact uses the compact form: no "at" and no time zone name.
    contact = (
        page.locator("#table table tbody tr").nth(1).locator("td").nth(3).inner_text()
    )
    assert contact.startswith("Sep") and " at " not in contact
    assert not contact.endswith(("PDT", "PST"))


@pytest.mark.parametrize("width", [1280, 390])
def test_queue_shows_long_ministry_names_whole(page, component_origin, width):
    """A long Ministry name, even one with a digit prefix as long as the
    longest in use, wraps whole onto at most two lines, with no ellipsis
    and no sideways page scroll, at laptop and phone width (#1136)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.locator(FILTERS))
    names = page.evaluate(MINISTRY_NAMES)
    assert "105-Religious Education Catechists and Aides" in [n for n, _ in names]
    assert not [name for name, cut in names if cut], names
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_queue_clipped_member_name_tip(page, component_origin):
    """A Member name its column cuts short shows whole in a tip on keyboard
    focus, which Escape hides; the name stays the link's whole text, so
    screen readers need no tip (#1136)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    link = page.locator("#table table tbody tr").nth(0).locator("th a")
    whole = "Member0 Bartholomew-Fitzgerald <Example>"
    assert link.text_content() == whole
    assert link.get_attribute("title") is None
    assert link.evaluate("e => e.scrollWidth > e.clientWidth")
    link.focus()
    tip = page.locator(".admin-tip.is-shown")
    expect(tip).to_have_text(whole)
    assert tip.get_attribute("aria-hidden") == "true"
    page.keyboard.press("Escape")
    expect(tip).to_have_count(0)


def test_queue_clipped_name_tip_can_be_hovered(page, component_origin):
    """The pointer can move from a clipped name onto its tip without the
    tip vanishing (WCAG 1.4.13), and leaving both hides it (#1136)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PAGE)
    link = page.locator("#table table tbody tr").nth(0).locator("th a")
    link.hover()
    tip = page.locator(".admin-tip.is-shown")
    expect(tip).to_have_text("Member0 Bartholomew-Fitzgerald <Example>")
    box = tip.bounding_box()
    # Through the gap between the name and the tip, then rest on the tip
    # past the hide grace.
    page.mouse.move(box["x"] + 10, box["y"] + box["height"] / 2, steps=8)
    page.wait_for_timeout(400)
    expect(tip).to_have_count(1)
    page.mouse.move(5, 5, steps=4)
    expect(tip).to_have_count(0)


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
    one line, or two for a long Ministry name, the table scrolling in its
    own box."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + PAGE)
    visible(page.locator(FILTERS))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    found = boxes(page, CONTROLS)
    for upper, lower in zip(found, found[1:], strict=False):
        assert lower["y"] >= upper["y"] + upper["height"], (upper, lower)
    rows = page.evaluate(ROWS)
    assert max(short_rows(rows)) < rows["oneLine"], rows
    assert max(rows["heights"]) < rows["twoLines"], rows
    assert page.locator("#table .table-scroll").evaluate(
        "e => e.scrollWidth > e.clientWidth"
    )
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_queue_filters_stay_put_when_applied_in_place(page, component_origin):
    """Applying the filters replaces the table in place (the queue's server
    answer is the one-request fixture page) and moves no filter label or
    control: no layout shift."""
    from playwright.sync_api import expect

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
        "**/reports/ministries/",
        lambda route: route.fulfill(content_type="text/html; charset=utf-8", body=body),
    )
    page.evaluate("document.querySelector('h1').dataset.mark = 'kept'")
    page.get_by_role("button", name="Apply filters").click()
    # A locator wait, not wait_for_function: the page's CSP refuses the eval
    # that wait_for_function re-polls with when the answer is not in yet.
    expect(page.locator("#table table tbody tr")).to_have_count(1)
    # Applied in place: a full page load would have lost the mark.
    assert page.evaluate("document.querySelector('h1').dataset.mark") == "kept"
    after = page.evaluate(layout)
    assert [entry[0] for entry in after] == [entry[0] for entry in before]
    for old, new in zip(before, after, strict=True):
        assert all(abs(a - b) <= 1 for a, b in zip(old[1:], new[1:], strict=True)), (
            old,
            new,
        )


def test_queue_help_explains_removed_to_staff_only(page, component_origin):
    """The page help explains the Removed badge to Staff and Administrators;
    a Ministry leader, who leads only Ministries in the campaign (#922),
    reads nothing about Ministries leaving it (#1135)."""
    page.goto(component_origin + PAGE)
    assert "Removed beside a Ministry" in page.locator("main").text_content()
    page.goto(component_origin + "/followup-leader")
    text = page.locator("main").text_content()
    assert "Leave (cannot participate) marks" in text
    assert "Removed beside a Ministry" not in text
