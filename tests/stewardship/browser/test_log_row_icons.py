"""System logs' cross-links as row icons (#1065).

Each entry's cross-links are small icon buttons in the Related column's five
fixed slots (related, actor, subject, campaign, task), each named and
titled for what it shows, present only when it has something to link to,
and the rows no longer hold a Technical details disclosure.
"""

from uuid import UUID

import pytest

from .keyboard import tab_to_next

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Every icon's accessible name, which its tooltip repeats, in slot order.
NAMES = (
    "Show entries related to this entry",
    "Show entries by the same actor",
    "Show audit records about the same subject",
    "Show audit records for the same campaign",
    "Open this task",
)
# Each icon in one row's Related cell: its id's kind, name, tooltip and box.
ICONS = """(id) => [...document.querySelector(`[id^="log-${id}-"]`)
    .closest(".log-link-slots").querySelectorAll(".icon-button")].map(icon => {
    const box = icon.getBoundingClientRect();
    return {kind: icon.id.split("-").pop(), name: icon.getAttribute("aria-label"),
            title: icon.title, left: box.left, top: box.top, width: box.width,
            height: box.height, svg: !!icon.querySelector("svg.action-icon")};
})"""
# The slots' height, each other cell's content height (its contents' box,
# not the cell, which stretches to the row) and whether the page fits its
# width, for one entry.
BOXES = """(id) => {
    const slots = document.querySelector(`[id^="log-${id}-"]`)
        .closest(".log-link-slots");
    const cell = slots.closest("td");
    const others = [...cell.closest("tr").children].filter(other => other !== cell)
        .map(other => {
            const range = document.createRange();
            range.selectNodeContents(other);
            return range.getBoundingClientRect().height;
        });
    return {slots: slots.getBoundingClientRect().height,
            tallest: Math.max(...others),
            scroll: document.documentElement.scrollWidth <= innerWidth};
}"""
AUDIT = f"audit-{UUID(int=300)}"
LOGIN = f"audit-{UUID(int=310)}"
WORKER = f"debug-{UUID(int=100)}"


@pytest.mark.parametrize("width", [320, 1280])
def test_icons_are_named_aligned_and_one_row_tall(page, component_origin, width):
    """Each icon has a matching name and tooltip and an inline icon; a row's
    icons sit on one line, the height of one row-action button, and each
    kind keeps its slot down the column, so an absent icon leaves a gap
    rather than moving the others. The page never scrolls sideways."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/logs")
    full = page.evaluate(ICONS, AUDIT)
    assert [icon["name"] for icon in full] == list(NAMES)
    assert all(icon["title"] == icon["name"] and icon["svg"] for icon in full)
    assert [icon["kind"] for icon in full] == [
        "related",
        "actor",
        "subject",
        "campaign",
        "task",
    ]
    # One line of same-size icons, the row-action buttons' 44 px height.
    assert {icon["top"] for icon in full} == {full[0]["top"]}
    assert {(icon["width"], icon["height"]) for icon in full} == {(36, 44)}
    boxes = page.evaluate(BOXES, AUDIT)
    assert boxes["slots"] == 44 and boxes["scroll"]
    # The operational entry shows related and actor in the same slots as
    # the full row, its other slots empty.
    worker = page.evaluate(ICONS, WORKER)
    assert [icon["kind"] for icon in worker] == ["related", "actor"]
    assert [icon["left"] for icon in worker] == [icon["left"] for icon in full[:2]]
    # The bare login has no related entries: only its actor icon, in the
    # actor slot.
    login = page.evaluate(ICONS, LOGIN)
    assert [icon["kind"] for icon in login] == ["actor"]
    assert login[0]["left"] == full[1]["left"]
    # No row holds identifiers or a Technical details disclosure.
    assert page.locator("#table details.technical-details").count() == 0


def test_the_icons_never_set_the_row_height(page, component_origin):
    """At 1280 px the Related cell is one icon tall, so it no longer makes a
    row taller than its other cells' contents do (#1065): every row's icons
    are no taller than its tallest other cell's contents."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/logs")
    for entry in (AUDIT, LOGIN, WORKER):
        boxes = page.evaluate(BOXES, entry)
        assert boxes["slots"] == 44
        assert boxes["slots"] <= boxes["tallest"], (entry, boxes)


def test_icons_are_reached_and_used_by_keyboard(page, component_origin):
    """Tab moves through one row's icons in slot order; the task icon is a
    link to the task's page."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/logs")
    page.locator(f"#log-{AUDIT}-related").focus()
    for kind in ("actor", "subject", "campaign", "task"):
        tab_to_next(page)
        assert page.evaluate("document.activeElement.id") == f"log-{AUDIT}-{kind}"
    task = page.locator(f"#log-{AUDIT}-task")
    assert task.evaluate("element => element.tagName") == "A"
    assert task.get_attribute("href").endswith(f"/background/{UUID(int=304)}/")
