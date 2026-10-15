"""The shared bulk bar says why its buttons wait, in a reserved line (#879).

Every table built on ``table-selection.html`` (Hosted files, Ministries,
Dates and mail schedules) shows the reason a bulk action button is disabled
as visible text below the buttons, named by the buttons'
``aria-describedby`` and with no tooltip (ui-conventions, Administrator
decision B2, 2026-10-10). The reason line keeps its place while a selection
exists, and the count and Select all button keep their widths, so neither
the bar's height nor any button's position changes as rows are ticked
(#563, #736). Checked at a phone and a desktop width.
"""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

AXE_TAGS = "['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']"


def layout(page):
    """The bar's size and every bar button's page position, to the pixel.

    Positions are rounded because engines can report an unmoved box a
    fraction of a pixel apart.
    """
    return page.locator("[data-select-table] .bulk-bar").first.evaluate(
        """bar => {
            const box = (node) => {
                const r = node.getBoundingClientRect();
                return [Math.round(r.left + scrollX), Math.round(r.top + scrollY)];
            };
            const r = bar.getBoundingClientRect();
            return {
                height: Math.round(r.height),
                hint: box(bar.querySelector('[data-selection-hint]')),
                buttons: [...bar.querySelectorAll('button')].map(box),
            };
        }"""
    )


def described(action, hint):
    """Whether the action names the reason line, with no tooltip of its own."""
    ids = (action.get_attribute("aria-describedby") or "").split()
    return hint.get_attribute("id") in ids and action.get_attribute("title") is None


def assert_accessible(page, axe_source):
    """Axe finds no WCAG 2.2 AA violation in the page as it stands."""
    page.evaluate(axe_source)
    violations = page.evaluate(
        "async () => (await axe.run(document, {runOnly: {type: 'tag', values: "
        + AXE_TAGS
        + "}})).violations.map(({id, nodes}) =>"
        + " ({id, targets: nodes.map(n => n.target)}))"
    )
    assert violations == []


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize(
    "path,button,reason",
    [
        ("/hosted-files", "Delete selected", "Select at least one file to delete."),
        (
            "/schedule-list",
            "Delete selected",
            "Select at least one scheduled email to delete.",
        ),
    ],
)
def test_bulk_reason_is_visible_text_in_a_reserved_line(
    page, component_origin, axe_source, path, button, reason, width
):
    """The reason shows as text, and ticking rows moves and resizes nothing."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    action = page.get_by_role("button", name=button, exact=True)
    hint = page.locator("[data-selection-hint]").first
    # Nothing chosen: the button waits and the reason is plain visible text.
    assert action.is_disabled()
    assert hint.is_visible() and hint.inner_text().strip() == reason
    assert hint.bounding_box()["height"] >= 10
    assert described(action, hint)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_accessible(page, axe_source)
    start = layout(page)
    # One row ticked: the reason clears, its line stays, nothing moves.
    page.locator("input[data-select-row]").first.check()
    assert action.is_enabled()
    assert not hint.is_visible()
    assert not described(action, hint)
    assert layout(page) == start
    assert_accessible(page, axe_source)
    # Every row ticked: Select all turns into Clear selection, still no move.
    page.get_by_role("button", name="Select all").click()
    assert page.get_by_role("button", name="Clear selection").is_visible()
    assert layout(page) == start
    # Cleared again: the reason returns in the same place.
    page.get_by_role("button", name="Clear selection").click()
    assert action.is_disabled() and hint.is_visible() and described(action, hint)
    assert layout(page) == start
    assert not failures
