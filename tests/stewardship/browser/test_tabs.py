"""The shared tab component in the browser (#1712; ui-conventions "Tabs").

The fixture page (tabs_components.py) draws the component the way the Family
hub (#1678) will: a header strip, then Timeline, Family, two Members,
Financial and Cannot participate and Additional information, with an
unavailable Census tab. These tests check the WAI-ARIA keyboard model
(arrows, Home, End, manual activation with Enter or Space), that choosing a
tab acts in place (no reload, no scroll jump, focus on the tab, the tab row
held still), the address (``?tab=``, Back, Forward, reload and a link),
lazy panels (one request the first time, none after), the narrow-screen row
that scrolls in its own box, and both colour schemes. The component's markup
and helper are unit-tested in tests/stewardship/test_tabs.py.
"""

import pytest

from parishkit.stewardship.web.design_tokens import COLORS, DARK_COLORS

from .tabs_components import PAGE, UNAVAILABLE
from .test_dark_scheme import rgb
from .waits import eventually, focused, has_attribute, has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"
# Where the tab row is: page-relative (so a scroll of the page itself is
# told apart from the row moving) and in the window.
ROW_TOP = (
    "document.querySelector('[role=tablist]').getBoundingClientRect().top"
    " + window.scrollY"
)
ROW_IN_VIEW = "document.querySelector('[role=tablist]').getBoundingClientRect().top"
ADDRESS = "location.pathname + location.search + location.hash"


def tab(page, key):
    """The tab with ``key``."""
    return page.locator(f"#family-tabs-tab-{key}")


def panel(page, key):
    """The panel of the tab with ``key``."""
    return page.locator(f"#family-tabs-panel-{key}")


def chosen(page, key):
    """Assert that ``key`` is the one chosen tab and only its panel shows."""
    has_attribute(tab(page, key), "aria-selected", "true")
    assert tab(page, key).get_attribute("tabindex") == "0"
    visible(panel(page, key).locator("[data-panel-text]"))
    assert page.locator("[role=tab][aria-selected=true]").count() == 1
    assert page.locator("[role=tabpanel]:not([hidden])").count() == 1
    assert page.locator("[role=tab][tabindex='0']").count() == 1


def tab_requests(page):
    """Every GET the page makes for one of the fixture's tab addresses."""
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.url)
            if request.method == "GET" and "tab=" in request.url
            else None
        ),
    )
    return requests


def open_page(page, origin, address=PAGE, *, width=1280, height=700):
    """Open ``address`` at the size given and mark the document."""
    page.set_viewport_size({"width": width, "height": height})
    page.goto(origin + address)
    visible(page.locator("[role=tablist]"))
    page.evaluate(MARK)


def test_arrows_home_and_end_move_focus_and_enter_or_space_chooses(
    page, component_origin
):
    """Manual activation: the arrows (wrapping), Home and End only move
    focus, reaching the unavailable tab too; Enter or Space chooses."""
    open_page(page, component_origin)
    chosen(page, "timeline")
    tab(page, "timeline").focus()
    page.keyboard.press("ArrowRight")
    focused(tab(page, "family"))
    page.keyboard.press("ArrowRight")
    focused(tab(page, "census"))
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("ArrowLeft")
    focused(tab(page, "timeline"))
    page.keyboard.press("ArrowLeft")
    focused(tab(page, "other"))
    page.keyboard.press("Home")
    focused(tab(page, "timeline"))
    page.keyboard.press("End")
    focused(tab(page, "other"))
    page.keyboard.press("ArrowRight")
    focused(tab(page, "timeline"))
    # Moving focus chose nothing and loaded nothing.
    chosen(page, "timeline")
    assert page.evaluate(ADDRESS) == PAGE
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    chosen(page, "family")
    focused(tab(page, "family"))
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    focused(tab(page, "member-2"))
    page.keyboard.press(" ")
    chosen(page, "member-2")
    focused(tab(page, "member-2"))
    assert page.evaluate(MARKED) == "kept"


def test_tab_from_the_row_moves_into_the_panel(page, component_origin):
    """Only the chosen tab is in the Tab order, and the panel comes next."""
    open_page(page, component_origin)
    tab(page, "timeline").focus()
    page.keyboard.press("Tab")
    focused(panel(page, "timeline"))


def test_an_unavailable_tab_shows_its_reason_and_does_nothing(page, component_origin):
    """Focus shows its tip; Enter, Space and a click leave the chosen tab
    and the address alone and send nothing."""
    requests = tab_requests(page)
    open_page(page, component_origin)
    tab(page, "family").focus()
    page.keyboard.press("ArrowRight")
    focused(tab(page, "census"))
    tip = page.locator("#family-tabs-tip-census")
    visible(tip)
    assert tip.text_content() == UNAVAILABLE
    page.keyboard.press("Enter")
    page.keyboard.press(" ")
    # click() waits for an aria-disabled element to be enabled; force it to
    # prove the click does nothing.
    tab(page, "census").click(force=True)
    page.wait_for_timeout(300)
    chosen(page, "timeline")
    assert tab(page, "census").get_attribute("aria-selected") == "false"
    assert page.evaluate(ADDRESS) == PAGE
    assert requests == []


@pytest.mark.parametrize("width", [320, 700, 1280])
def test_choosing_a_tab_acts_in_place(page, component_origin, width):
    """No reload and no jump: the mark survives, the page and the tab row
    stay where they were, focus is on the tab, the address names the tab
    without the panel's fragment, and the fetched panel replaces nothing
    else."""
    open_page(page, component_origin, width=width)
    page.evaluate(
        "window.scrollTo(0, document.querySelector('[role=tablist]')"
        ".getBoundingClientRect().top + window.scrollY - 150)"
    )
    offset = page.evaluate("window.scrollY")
    assert offset > 0, "the tab row must sit below the fold for this test"
    row = page.evaluate(ROW_TOP)
    strip = page.locator("#header-strip").text_content()
    tab(page, "financial").click()
    chosen(page, "financial")
    focused(tab(page, "financial"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("window.scrollY") == pytest.approx(offset, abs=1)
    assert page.evaluate(ROW_TOP) == pytest.approx(row, abs=1)
    assert page.evaluate(ADDRESS) == f"{PAGE}?family=1234&tab=financial"
    assert page.locator("#header-strip").text_content() == strip
    assert panel(page, "timeline").inner_text().startswith("Timeline panel")


@pytest.mark.parametrize("width", [700, 1280])
def test_a_shorter_panel_never_pulls_the_tab_row(page, component_origin, width):
    """Scrolled so the tall Timeline fills the window under the tab row,
    choosing a short panel keeps the row where it is in the window: the
    panel area holds the height the reader's place needs."""
    open_page(page, component_origin, width=width, height=600)
    page.evaluate(
        "window.scrollTo(0, document.querySelector('[role=tablist]')"
        ".getBoundingClientRect().top + window.scrollY - 40)"
    )
    before = page.evaluate(ROW_IN_VIEW)
    tab(page, "family").click()
    chosen(page, "family")
    assert page.evaluate(ROW_IN_VIEW) == pytest.approx(before, abs=1)
    # Back to the (already drawn) Timeline and to the short panel again.
    tab(page, "timeline").click()
    chosen(page, "timeline")
    tab(page, "member-1").click()
    chosen(page, "member-1")
    assert page.evaluate(ROW_IN_VIEW) == pytest.approx(before, abs=1)


def test_panels_load_once_and_only_when_chosen(page, component_origin):
    """The page draws only the chosen panel; another is fetched the first
    time its tab is chosen and shown again with no request."""
    requests = tab_requests(page)
    open_page(page, component_origin)
    for key in ("family", "member-1", "financial", "other"):
        assert panel(page, key).inner_html() == ""
        hidden(panel(page, key))
    tab(page, "family").click()
    chosen(page, "family")
    assert requests == [f"{component_origin}{PAGE}?family=1234&tab=family"]
    assert panel(page, "member-1").inner_html() == ""
    tab(page, "timeline").click()
    chosen(page, "timeline")
    tab(page, "family").click()
    chosen(page, "family")
    page.wait_for_timeout(300)
    assert len(requests) == 1
    assert page.evaluate(MARKED) == "kept"


def test_back_forward_reload_and_links_restore_the_tab(page, component_origin):
    """Each choice is a history entry: Back and Forward show its tab in
    place, a reload draws it from the address, and so does a link."""
    open_page(page, component_origin)
    tab(page, "family").click()
    chosen(page, "family")
    tab(page, "financial").click()
    chosen(page, "financial")
    page.go_back()
    chosen(page, "family")
    assert page.evaluate(ADDRESS) == f"{PAGE}?family=1234&tab=family"
    page.go_back()
    chosen(page, "timeline")
    assert page.evaluate(ADDRESS) == PAGE
    assert page.evaluate(MARKED) == "kept"
    page.go_forward()
    chosen(page, "family")
    page.go_forward()
    chosen(page, "financial")
    assert page.evaluate(MARKED) == "kept"
    page.reload()
    chosen(page, "financial")
    assert page.evaluate(MARKED) is None
    assert panel(page, "timeline").inner_html() == ""
    # Back after the reload is a quiet fetch of a panel not drawn yet.
    page.evaluate(MARK)
    page.go_back()
    chosen(page, "family")
    assert page.evaluate(MARKED) == "kept"
    open_page(page, component_origin, f"{PAGE}?family=1234&tab=member-2")
    chosen(page, "member-2")
    eventually(page, "window.scrollY === 0")


def test_the_row_scrolls_in_its_own_box_on_a_narrow_screen(page, component_origin):
    """At phone width the tabs stay on one line inside a box that scrolls
    sideways; the page never does, and End scrolls the row to the last tab
    (whose long label is wider than a phone's row, so its start is shown)."""
    open_page(page, component_origin, width=320)
    sizes = page.evaluate(
        """() => {
            const row = document.querySelector('[role=tablist]');
            const root = document.documentElement;
            return {row: [row.scrollWidth, row.clientWidth],
                    page: [root.scrollWidth, root.clientWidth],
                    tops: [...row.children].map((tab) => tab.offsetTop)};
        }"""
    )
    assert sizes["row"][0] > sizes["row"][1]
    assert sizes["page"][0] <= sizes["page"][1]
    assert len(set(sizes["tops"])) == 1
    tab(page, "timeline").focus()
    page.keyboard.press("End")
    focused(tab(page, "other"))
    eventually(
        page,
        """() => {
            const row = document.querySelector('[role=tablist]')
                .getBoundingClientRect();
            const last = document.activeElement.getBoundingClientRect();
            const scrolled = document.querySelector('[role=tablist]').scrollLeft;
            return scrolled > 0 && last.left >= row.left - 1 && last.left < row.right;
        }""",
    )
    assert page.evaluate("window.scrollX") == 0


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_row_uses_the_theme_tokens(page, component_origin, axe_source, scheme):
    """The chosen tab is filled in the accent colour with paper text, the
    others are accent text, and axe finds no violation in the component."""
    colours = COLORS if scheme == "light" else DARK_COLORS
    page.emulate_media(color_scheme=scheme)
    open_page(page, component_origin)
    style = (
        "(node) => [getComputedStyle(node).backgroundColor,"
        " getComputedStyle(node).color]"
    )
    assert tab(page, "timeline").evaluate(style) == [
        rgb(colours["accent"]),
        rgb(colours["paper"]),
    ]
    assert tab(page, "family").evaluate(style)[1] == rgb(colours["accent"])
    assert tab(page, "census").evaluate(style)[1] == rgb(colours["unavailable"])
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(
        {include: [['.tabs']]},
        {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}}
    )).violations.map(({id, nodes}) => ({id, targets: nodes.map(n => n.target)}))""")
    assert violations == []


def live_region(page):
    """The in-place mechanism's one polite live region (ui-v1.js)."""
    region = page.locator("main > div.visually-hidden[role=status]")
    assert region.count() == 1
    return region


def test_a_fetched_tab_is_announced_and_back_is_quiet(page, component_origin):
    """The live region names a fetched tab; Back and Forward say nothing
    and leave focus where it was."""
    open_page(page, component_origin)
    tab(page, "financial").click()
    chosen(page, "financial")
    has_text(live_region(page), "Financial 3")
    page.evaluate("document.querySelector('main > div[role=status]').textContent = ''")
    page.locator("#save").focus()
    page.go_back()
    chosen(page, "timeline")
    page.go_forward()
    chosen(page, "financial")
    page.wait_for_timeout(300)
    assert live_region(page).text_content() == ""
    focused(page.locator("#save"))


def test_a_tab_is_not_switched_while_a_save_is_in_flight(page, component_origin):
    """With a save held open, choosing a tab changes neither the tab nor the
    address, and says "Still saving…"; once the save settles, tabs work."""
    held = []
    page.route("**/tabs/save", lambda route: held.append(route))
    open_page(page, component_origin)
    page.locator("#save").click()
    page.wait_for_timeout(300)
    assert len(held) == 1
    tab(page, "financial").click()
    has_text(live_region(page), "Still saving…")
    chosen(page, "timeline")
    assert page.evaluate(ADDRESS) == PAGE
    # No answer at all: the form says so, and the save is over.
    held[0].abort()
    visible(page.locator("#saved [data-in-place-error]"))
    tab(page, "financial").click()
    chosen(page, "financial")
    assert page.evaluate(MARKED) == "kept"


def test_a_panel_that_cannot_be_fetched_reloads_the_page(page, component_origin):
    """A refused panel fetch falls back to an ordinary load of the tab's
    address, which draws that tab."""
    refused = []

    def refuse_once(route):
        """Refuse the first fetch of the Family tab, then serve it."""
        if refused:
            route.continue_()
            return
        refused.append(route.request.url)
        route.fulfill(status=500, body="Server error")

    page.route("**/tabs?family=1234&tab=family", refuse_once)
    open_page(page, component_origin)
    with page.expect_navigation():
        tab(page, "family").click()
    chosen(page, "family")
    assert page.evaluate(MARKED) is None
    assert len(refused) == 1
    assert page.evaluate(ADDRESS) == f"{PAGE}?family=1234&tab=family"


def test_the_unavailable_tip_stays_while_the_pointer_is_on_it(page, component_origin):
    """The pointer can move from the unavailable tab onto its tip, which
    stays (WCAG 1.4.13), and leaving both hides it."""
    open_page(page, component_origin)
    tab(page, "census").hover()
    tip = page.locator("#family-tabs-tip-census")
    visible(tip)
    box = tip.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(600)
    visible(tip)
    page.mouse.move(5, 5)
    hidden(tip)
