"""System logs' cross-links as row icons (#1065).

Each entry's cross-links are small icon buttons in the Related column's five
fixed slots (related, actor, subject, campaign, task; three across and two
down since #2280), each named and
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
def test_icons_are_named_and_aligned(page, component_origin, width):
    """Each icon has a matching name and tooltip and an inline icon; a row's
    icons sit in a grid three across (related, actor, subject) and two down
    (campaign, task), each the height of one row-action button (#2280), and
    each kind keeps its slot down the column, so an absent icon leaves a gap
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
    # Two lines of same-size icons, the row-action buttons' 44 px height:
    # campaign and task under related and actor.
    assert {icon["top"] for icon in full[:3]} == {full[0]["top"]}
    assert full[3]["top"] == full[4]["top"] == full[0]["top"] + 48
    assert [icon["left"] for icon in full[3:]] == [icon["left"] for icon in full[:2]]
    assert {(icon["width"], icon["height"]) for icon in full} == {(36, 44)}
    boxes = page.evaluate(BOXES, AUDIT)
    assert boxes["slots"] == 92 and boxes["scroll"]
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
    """At 1280 px a Related cell without a campaign or task icon is one icon
    tall, so it never makes a row taller than its other cells' contents do
    (#1065). A row with either is two icons tall (#2280: the slots are three
    across so the table fits at 1280 px), which may make a row whose other
    cells are short a little taller; those icons appear only on audit
    records with a campaign or a task."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/logs")
    assert page.evaluate(BOXES, AUDIT)["slots"] == 92
    for entry in (LOGIN, WORKER):
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
