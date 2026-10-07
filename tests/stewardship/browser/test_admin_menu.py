"""The Admin menu's greyed-out entries and collapsible groups (ADM-12.02 to .04).

The page is ``menu_components.py``: an Administrator's real menu for a Testing
draft, on Pages and emails. Production activation (in the current Campaign
setup group) and Pause and resume mail (in Mail and Family portal) are greyed out.
An unavailable entry's reason shows on hover, keyboard focus and tap, and
Escape or a tap elsewhere hides it (admin-portal spec, "Stable menu shape").
Groups collapse, each browser remembers the collapsed ones, and the current
page's group always opens (spec, "Menu groups"). The rendered markup and its
ARIA are also checked without a browser in ``test_admin_navigation.py``.
"""

import pytest

from .menu_components import PATH
from .waits import eventually, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

REASON = "Available in Production mode"


def _sidebar(page):
    """The Admin sidebar, with the narrow screens' Menu disclosure opened."""
    sidebar = page.get_by_role("navigation", name="Administration")
    menu = sidebar.locator("details[data-admin-menu]")
    if not menu.evaluate("details => details.open"):
        sidebar.locator("details[data-admin-menu] > summary").click()
    return sidebar


def _greyed(page):
    """Pause and resume mail, greyed out in Testing mode, and its tip."""
    entry = _sidebar(page).get_by_role("link", name="Pause and resume mail")
    return entry, page.locator("#" + entry.get_attribute("aria-describedby"))


def test_greyed_entry_is_a_described_link_that_goes_nowhere(page, component_origin):
    """No href; keyboard-reachable; its reason is its accessible description."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    entry, tip = _greyed(page)
    assert entry.get_attribute("href") is None
    expect(entry).to_have_attribute("aria-disabled", "true")
    expect(entry).to_be_disabled()
    expect(entry).to_have_accessible_description(REASON)
    hidden(tip)
    # Activating it does nothing: no navigation, the tip only. (Playwright
    # treats aria-disabled as disabled, so its click must be forced.)
    entry.focus()
    entry.press("Enter")
    entry.click(force=True)
    assert page.url == component_origin + PATH
    # Its grey still reads clearly, and it does not look like a link.
    assert entry.evaluate("node => getComputedStyle(node).cursor") == "not-allowed"


def _box(locator):
    """The element's box as (left, top, right, bottom)."""
    box = locator.bounding_box()
    return box["x"], box["y"], box["x"] + box["width"], box["y"] + box["height"]


def _overlaps(first, second):
    """Whether two (left, top, right, bottom) boxes share any area."""
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


@pytest.mark.parametrize("width", [390, 1280])
def test_tip_shows_on_hover_beside_the_entry_after_a_short_delay(
    page, component_origin, width
):
    """Resting the pointer shows the reason beside the entry, never over others.

    On a wide screen the tip opens to the right of the sidebar, level with
    the entry; on a phone, where there is no room beside it, it opens above.
    Either way it covers no other menu entry.
    """
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    entry, tip = _greyed(page)
    entry.hover()
    # Passing over an entry pops nothing: the tip waits about a third of a
    # second.
    page.wait_for_timeout(150)
    hidden(tip)
    visible(tip)
    assert tip.inner_text() == REASON
    tip_box, entry_box = _box(tip), _box(entry)
    if width >= 1280:
        sidebar = _box(_sidebar(page))
        assert tip_box[0] >= sidebar[2] - 1
        assert tip_box[1] < entry_box[3] and entry_box[1] < tip_box[3]
    else:
        assert tip_box[3] <= entry_box[1]
    links = _sidebar(page).get_by_role("link")
    for index in range(links.count()):
        other = links.nth(index)
        if other.is_visible() and other.inner_text() != "Pause and resume mail":
            assert not _overlaps(tip_box, _box(other)), other.inner_text()
    # The tip is hoverable: moving onto it keeps it.
    tip.hover()
    page.wait_for_timeout(300)
    visible(tip)
    # Moving onto another entry hides it at once.
    _sidebar(page).get_by_role("link", name="Family email progress").hover()
    expect(tip).to_be_hidden(timeout=100)
    # Escape dismisses a hover tip without moving the pointer.
    entry.hover()
    visible(tip)
    page.keyboard.press("Escape")
    hidden(tip)
    # The tip never widens the page on a phone.
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_tip_shows_on_keyboard_focus_and_escape_dismisses_it(page, component_origin):
    """Tabbing onto the entry shows the reason; Escape hides it, focus stays."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    entry, tip = _greyed(page)
    # Its group's title comes just before it, so the next Tab reaches it.
    _sidebar(page).locator('details[data-menu-group="mail"] > summary').focus()
    page.keyboard.press("Tab")
    assert entry.evaluate("node => node === document.activeElement")
    visible(tip)
    page.keyboard.press("Escape")
    hidden(tip)
    assert entry.evaluate("node => node === document.activeElement")
    # Tabbing away and back shows it again; leaving hides it.
    page.keyboard.press("Tab")
    hidden(tip)
    page.keyboard.press("Shift+Tab")
    visible(tip)


def test_tip_shows_on_tap_and_a_tap_elsewhere_dismisses_it(
    browser_engine, component_origin
):
    """On a touch screen a tap shows the reason; tapping elsewhere hides it."""
    context = browser_engine.new_context(
        has_touch=True, viewport={"width": 390, "height": 844}
    )
    try:
        page = context.new_page()
        page.goto(component_origin + PATH)
        entry, tip = _greyed(page)
        # Playwright treats aria-disabled as disabled, so taps are forced.
        entry.tap(force=True)
        visible(tip)
        assert page.url == component_origin + PATH
        page.locator("main h1").tap()
        hidden(tip)
        # Tapping one greyed entry, then another, shows only the second tip.
        other = _sidebar(page).get_by_role("link", name="Production activation")
        entry.tap(force=True)
        visible(tip)
        other.tap(force=True)
        hidden(tip)
        visible(page.locator("#" + other.get_attribute("aria-describedby")))
        # A shown tip never swallows the next tap: tapping an available entry
        # (even where the tip is drawn) both hides the tip and opens it.
        entry.tap(force=True)
        visible(tip)
        target = _sidebar(page).get_by_role("link", name="Family email progress")
        href = target.get_attribute("href")
        with page.expect_navigation():
            target.tap()
        assert page.url.endswith(href)
    finally:
        context.close()


MAIL_TITLE = 'details[data-menu-group="mail"] > summary'


def _expanded(page, selector):
    """Whether the browser exposes the summary as expanded to assistive tech.

    Playwright's role engine does not report a summary's state, so Chromium's
    own accessibility tree is read over its debugging protocol. WebKit has no
    such protocol here; the platform maps the native open state, so that is
    read instead (and a manual VoiceOver check covers the announcement).
    """
    if page.context.browser.browser_type.name != "chromium":
        return page.locator(selector).evaluate("node => node.parentElement.open")
    cdp = page.context.new_cdp_session(page)
    try:
        root = cdp.send("DOM.getDocument")["root"]["nodeId"]
        node = cdp.send("DOM.querySelector", {"nodeId": root, "selector": selector})
        (summary, *_) = cdp.send(
            "Accessibility.getPartialAXTree",
            {"nodeId": node["nodeId"], "fetchRelatives": False},
        )["nodes"]
        states = {
            item["name"]: item["value"]["value"] for item in summary["properties"]
        }
        return states["expanded"]
    finally:
        cdp.detach()


def test_collapsed_groups_are_remembered_and_reopening_forgets_them(
    page, component_origin
):
    """Collapsing a group survives a reload; opening it clears the stored key."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    sidebar = _sidebar(page)
    group = sidebar.locator('details[data-menu-group="mail"]')
    title = group.locator("summary")
    outgoing = sidebar.get_by_role("link", name="Outgoing mail")
    # Every group starts open, and loading the page stores nothing.
    expect(group).to_have_attribute("open", "")
    visible(outgoing)
    assert page.evaluate("localStorage.length") == 0
    # The summary is a keyboard control that names its group and exposes
    # whether it is expanded.
    expect(title).to_have_accessible_name("Mail and Family portal")
    assert _expanded(page, MAIL_TITLE) is True
    title.focus()
    page.keyboard.press("Enter")
    hidden(outgoing)
    assert _expanded(page, MAIL_TITLE) is False
    eventually(page, "() => localStorage.getItem('pk-admin-menu-group:mail')", "closed")
    page.reload()
    hidden(_sidebar(page).get_by_role("link", name="Outgoing mail"))
    # Only the group key and its state are stored.
    assert page.evaluate("Object.keys(localStorage)") == ["pk-admin-menu-group:mail"]
    _sidebar(page).locator('details[data-menu-group="mail"] > summary').click()
    visible(outgoing)
    eventually(page, "() => localStorage.length", 0)
    page.reload()
    visible(_sidebar(page).get_by_role("link", name="Outgoing mail"))
    assert not failures


def test_the_current_pages_group_always_opens(page, component_origin):
    """A stored collapse never hides the aria-current entry."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + PATH)
    page.evaluate(
        "for (const key of ['campaign', 'system'])"
        " localStorage.setItem(`pk-admin-menu-group:${key}`, 'closed')"
    )
    page.reload()
    sidebar = _sidebar(page)
    current = sidebar.locator('details[data-menu-group="campaign"]')
    expect(current).to_have_attribute("open", "")
    visible(sidebar.locator('a[aria-current="page"]'))
    # Another group the Admin collapsed stays collapsed.
    expect(sidebar.locator('details[data-menu-group="system"]')).not_to_have_attribute(
        "open", ""
    )
    hidden(sidebar.get_by_role("link", name="System logs"))
    # Forcing it open does not forget the stored choice.
    assert page.evaluate("localStorage.getItem('pk-admin-menu-group:campaign')") == (
        "closed"
    )
