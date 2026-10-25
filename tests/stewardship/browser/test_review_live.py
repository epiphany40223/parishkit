"""Review regions that update themselves, in the browser (#2174).

A review worklist's table and the Review work cards each check their drawn
items about once a minute (ui-v1.js, [data-live-items]). Each test installs
a fake clock, answers the region's passive check (``?live=``) with the same
page after someone else's decision (review_worklist_components' ACCEPTED,
whose row 0 another reader accepted; review_work_components' LATER_PATH,
one census change fewer To review), and checks that only the changed item
is redrawn, in place, with its tag, and that no check is made while focus
is in the region or the tab is hidden.
"""

import pytest

from .conftest import NOW
from .review_work_components import LATER_PATH
from .review_work_components import PATH as REVIEW_WORK
from .review_worklist_components import ACCEPTED, HELD, MISFIT, PATH
from .test_in_place import MARK, MARKED, VIEWPORT
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A little more than the regions' 60-second pace.
TICK = 61 * 1000
HIDE = """() => {
    Object.defineProperty(document, "visibilityState",
        {configurable: true, get: () => "hidden"});
    document.dispatchEvent(new Event("visibilitychange"));
}"""
SHOW = """() => {
    delete document.visibilityState;
    document.dispatchEvent(new Event("visibilitychange"));
}"""
# The table's row ids, in order.
ORDER = "() => [...document.querySelectorAll('#table tbody tr')].map((row) => row.id)"


def answer_checks(page, origin, address, fixture):
    """Answer each check of ``address`` with ``fixture``; returns the checks,
    each as (its live value, its X-ParishKit-Read header)."""
    checks = []

    def answer(route):
        """Serve the fixture to a region's check."""
        request = route.request
        live = request.url.split("live=", 1)[1]
        checks.append((live, request.headers.get("x-parishkit-read")))
        route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))

    page.route(lambda url: url.startswith(f"{origin}{address}?live="), answer)
    return checks


def opened(page, origin, address):
    """Open ``address`` under a fake clock, the pointer away from the page's
    regions, and mark the page so a reload would show."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + address)
    page.mouse.move(1, 1)
    page.evaluate(MARK)


def row(page, index):
    """Row ``index`` of the worklist."""
    return page.locator(f"#worklist-row-{index}")


def test_a_change_made_elsewhere_appears_in_place_with_its_tag(page, component_origin):
    """A minute on, the region checks its eight rows once, passively; only
    the row another reader accepted is redrawn, now Ready to update with
    Changed by someone else, and no row moves."""
    opened(page, component_origin, PATH)
    checks = answer_checks(page, component_origin, PATH, ACCEPTED)
    order = page.evaluate(ORDER)
    top = row(page, 0).bounding_box()["y"]
    page.evaluate("document.querySelector('#worklist-row-3').dataset.kept = 'yes'")
    page.clock.fast_forward(TICK)
    visible(row(page, 0).locator("[data-live-tag]"))
    has_text(row(page, 0).locator(".stage-chip .status-word").first, "Ready to update")
    assert len(checks) == 1
    pairs, header = checks[0]
    assert header == "passive" and len(pairs.replace("%2C", ",").split(",")) == 8
    assert page.evaluate(ORDER) == order
    assert abs(row(page, 0).bounding_box()["y"] - top) < 1
    # The unchanged rows were not redrawn, and the page was not reloaded.
    assert page.evaluate("document.querySelector('#worklist-row-3').dataset.kept")
    assert page.evaluate(MARKED) == "kept"
    hidden(row(page, 1).locator("[data-live-tag]"))
    # The redrawn row's own icon still opens the decision dialog.
    row(page, 0).locator("[data-row-action=accept]").click()
    visible(page.locator("#worklist-decide-dialog"))


def test_no_check_while_focus_is_in_the_table(page, component_origin):
    """While an icon in the table has focus the region waits; once focus
    leaves it, it checks within a tick."""
    opened(page, component_origin, PATH)
    checks = answer_checks(page, component_origin, PATH, ACCEPTED)
    row(page, 2).locator("[data-row-action=accept]").focus()
    page.clock.fast_forward(2 * TICK)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert checks == []
    hidden(row(page, 0).locator("[data-live-tag]"))
    page.evaluate("document.activeElement.blur()")
    page.clock.fast_forward(6 * 1000)
    visible(row(page, 0).locator("[data-live-tag]"))
    assert len(checks) == 1


def test_a_hidden_tab_is_not_checked_and_catches_up_when_shown(page, component_origin):
    """A hidden tab makes no check; shown again, the region checks at once."""
    opened(page, component_origin, PATH)
    checks = answer_checks(page, component_origin, PATH, ACCEPTED)
    page.evaluate(HIDE)
    page.clock.fast_forward(3 * TICK)
    page.evaluate("() => true")
    assert checks == []
    page.evaluate(SHOW)
    visible(row(page, 0).locator("[data-live-tag]"))
    assert len(checks) == 1


def test_the_review_work_counts_update_in_place(page, component_origin):
    """My review work's census To review card counts one fewer a minute
    after someone else accepted a change; its other cards stay as drawn."""
    opened(page, component_origin, REVIEW_WORK)
    checks = answer_checks(page, component_origin, "/admin/", LATER_PATH)
    census = page.locator('[data-review-stream="census"]')
    card = census.locator("[data-link-card]").filter(has_text="To review")
    has_text(card.locator(".link-card-count"), "1,234")
    page.clock.fast_forward(TICK)
    has_text(card.locator(".link-card-count"), "1,233")
    assert len(checks) == 1
    assert page.evaluate(MARKED) == "kept"
    # A card counts quietly: no tag.
    assert census.locator("[data-live-tag]").count() == 0


def test_a_held_campaigns_answer_redraws_its_rows_and_keeps_the_table(
    page, component_origin
):
    """The campaign was held between page load and check: the answer holds
    rows 0 and 2 alone, offering nothing, with the page's selection column
    (empty). Both are redrawn in place; the table keeps all eight rows in
    order, its selection column and its pager. Row 0, accepted too, shows
    its tag; row 2, whose offered actions alone changed, shows none."""
    opened(page, component_origin, PATH)
    checks = answer_checks(page, component_origin, PATH, HELD)
    order = page.evaluate(ORDER)
    columns = page.evaluate("document.querySelector('#table thead tr').cells.length")
    page.evaluate("document.querySelector('#worklist-row-3').dataset.kept = 'yes'")
    assert row(page, 2).locator("[data-row-action]").count() > 0
    page.clock.fast_forward(TICK)
    visible(row(page, 0).locator("[data-live-tag]"))
    has_text(row(page, 0).locator(".stage-chip .status-word").first, "Ready to update")
    assert len(checks) == 1
    assert page.evaluate(ORDER) == order
    assert page.evaluate("document.querySelector('#worklist-row-3').dataset.kept")
    assert page.evaluate(MARKED) == "kept"
    # Row 2 was redrawn (it offers nothing now), untagged, with its cells.
    assert row(page, 2).locator("[data-row-action]:not([disabled])").count() == 0
    hidden(row(page, 2).locator("[data-live-tag]"))
    for index in (0, 2):
        cells = row(page, index).evaluate("(node) => node.cells.length")
        assert cells == columns, index
        assert row(page, index).locator("td.select-col").count() == 1
    # The selection column's header and the other rows' boxes are kept.
    assert page.locator("#table thead [data-select-all]").count() == 1
    assert row(page, 1).locator("[data-select-row]").count() == 1


def test_a_misfit_worklist_answer_never_replaces_the_table(page, component_origin):
    """An answer whose rows have other cells than the table's (here, no
    selection column) is only the changed rows: the table keeps its eight
    rows, in order, with its cells; row 0 shows its tag and waits for the
    reader's next redraw."""
    opened(page, component_origin, PATH)
    checks = answer_checks(page, component_origin, PATH, MISFIT)
    order = page.evaluate(ORDER)
    page.clock.fast_forward(TICK)
    visible(row(page, 0).locator("[data-live-tag]"))
    assert len(checks) == 1
    assert page.evaluate(ORDER) == order
    assert page.evaluate(MARKED) == "kept"
    assert row(page, 0).locator("td.select-col").count() == 1
    assert page.locator("#table thead [data-select-all]").count() == 1
    # Row 2's offered actions alone changed: no tag.
    hidden(row(page, 2).locator("[data-live-tag]"))
