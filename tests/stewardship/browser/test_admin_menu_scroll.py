"""The Admin menu keeps its scroll position across a menu link (#620).

The pages are ``menu_components.py``: an Administrator's real menu on Pages
and emails (near the top of the menu) and on System logs (near its end). On a
wide, short window the sidebar scrolls on its own. Following a menu link
keeps the clicked entry at the same height on screen on the next page, after
the remembered groups are set; with nothing saved the current page's entry
is brought into view; only a plain activation of a menu link saves anything;
and the page works with browser storage blocked (admin-portal spec, "Menu
groups").
"""

import json

import pytest

from .menu_components import PATH
from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

TARGET = "System logs"
KEY = "pk-admin-menu-scroll"

# Scroll the menu so a link sits halfway down the menu's visible part (the
# sidebar can reach past the window's bottom edge), and return the link's
# top edge on screen.
PLACE = """(nav, name) => {
  const link = [...nav.querySelectorAll("a")].find(
    (node) => node.textContent.trim() === name);
  const view = nav.getBoundingClientRect();
  const bottom = Math.min(view.bottom, window.innerHeight);
  const goal = view.top + (bottom - view.top) / 2;
  nav.scrollTop += link.getBoundingClientRect().top - goal;
  return link.getBoundingClientRect().top;
}"""

# The top edge on screen of the link named ``name``.
TOP = """(nav, name) => [...nav.querySelectorAll("a")].find(
  (node) => node.textContent.trim() === name).getBoundingClientRect().top"""

# Whether a link lies wholly inside the menu's visible part and the window.
SHOWN = """(nav, name) => {
  const link = [...nav.querySelectorAll("a")].find(
    (node) => node.textContent.trim() === name);
  const view = nav.getBoundingClientRect();
  const box = link.getBoundingClientRect();
  return box.height > 0 && box.top >= view.top - 1 &&
    box.bottom <= Math.min(view.bottom, window.innerHeight) + 1;
}"""


def _wide(page, origin, path=PATH):
    """Open ``path`` on a wide, short window, where the menu scrolls on its own."""
    page.set_viewport_size({"width": 1280, "height": 600})
    page.goto(origin + path)
    sidebar = page.locator("nav.admin-sidebar")
    # The menu is taller than the window, so it is its own scroll area.
    assert sidebar.evaluate("nav => nav.scrollHeight > nav.clientHeight")
    return sidebar


def _shown(sidebar, name):
    """Whether entry ``name`` is in the visible part of the menu."""
    return sidebar.evaluate(SHOWN, name)


def _follow(page, sidebar, how="click", name=TARGET):
    """Scroll the menu to entry ``name`` and follow it; return its top on screen."""
    top = sidebar.evaluate(PLACE, name)
    assert _shown(sidebar, name)
    target = sidebar.get_by_role("link", name=name)
    href = target.get_attribute("href")
    with page.expect_navigation():
        if how == "click":
            target.click()
        else:
            target.focus()
            page.keyboard.press("Enter")
    assert page.url.endswith(href)
    return top


def _assert_kept(page, top, name=TARGET):
    """Entry ``name`` is current and sits where it was on screen when clicked."""
    from playwright.sync_api import expect

    sidebar = page.locator("nav.admin-sidebar")
    expect(sidebar.locator('a[aria-current="page"]')).to_have_text(name)
    assert abs(sidebar.evaluate(TOP, name) - top) <= 1
    assert _shown(sidebar, name)
    # The page content still starts at its top, and the value is used once.
    assert page.evaluate("window.scrollY") == 0
    assert page.evaluate(f"sessionStorage.getItem('{KEY}')") is None


@pytest.mark.parametrize("how", ["click", "keyboard"])
def test_following_a_menu_link_keeps_the_entry_in_place(page, component_origin, how):
    """A click or Enter on an entry low in the menu keeps it under the pointer."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sidebar = _wide(page, component_origin)
    top = _follow(page, sidebar, how)
    # The new page's menu is scrolled, not left at its top.
    assert page.locator("nav.admin-sidebar").evaluate("nav => nav.scrollTop") > 0
    _assert_kept(page, top)
    # Nothing went to the browser's lasting storage.
    assert page.evaluate("localStorage.length") == 0
    assert not failures


def test_the_entry_stays_in_place_when_the_page_was_scrolled(page, component_origin):
    """The page scrolled down (header off screen) still keeps the entry in place.

    The sticky sidebar then starts at the window's top, while on the new page
    it starts below the header, so restoring the menu's scrollTop alone would
    move the entry by the header's height. The walk goes from System logs up
    to Pages and emails: an entry near the menu's end cannot rise that far,
    since the new page's menu then reaches past the window's bottom edge.
    """
    sidebar = _wide(page, component_origin)
    href = sidebar.get_by_role("link", name=TARGET).get_attribute("href")
    sidebar = _wide(page, component_origin, href)
    # Make the page tall enough to scroll, then scroll it past the header.
    page.evaluate("document.querySelector('main').style.minHeight = '3000px'")
    page.evaluate("window.scrollTo(0, 400)")
    assert page.evaluate("window.scrollY") == 400
    assert sidebar.evaluate("nav => nav.getBoundingClientRect().top") == 0
    top = _follow(page, sidebar, name="Pages and emails")
    _assert_kept(page, top, "Pages and emails")
    # Here only a scrolled menu puts the entry that high on screen.
    assert page.locator("nav.admin-sidebar").evaluate("nav => nav.scrollTop") > 0


def test_the_entry_stays_in_place_after_collapsed_groups_are_set(
    page, component_origin
):
    """Groups that collapse on the new page are applied before the restore.

    Campaign setup, above the entry, is stored collapsed but forced open on
    Pages and emails, which it holds; on System logs it collapses.
    """
    sidebar = _wide(page, component_origin)
    page.evaluate(
        "for (const key of ['campaign', 'mail'])"
        " localStorage.setItem(`pk-admin-menu-group:${key}`, 'closed')"
    )
    page.reload()
    visible(sidebar.get_by_role("link", name="Pages and emails"))
    hidden(sidebar.get_by_role("link", name="Outgoing mail"))
    top = _follow(page, sidebar)
    hidden(sidebar.get_by_role("link", name="Pages and emails"))
    _assert_kept(page, top)


def test_a_fresh_load_shows_the_current_entry(page, component_origin):
    """With nothing saved, the menu scrolls only to bring its entry into view."""
    sidebar = _wide(page, component_origin)
    # Pages and emails is already in view: the menu stays at its top.
    assert sidebar.evaluate("nav => nav.scrollTop") == 0
    assert _shown(sidebar, "Pages and emails")
    href = sidebar.get_by_role("link", name=TARGET).get_attribute("href")
    # Opening System logs directly (a new tab, a link from elsewhere) brings
    # its entry, at the menu's end, into view, and scrolls nothing else.
    sidebar = _wide(page, component_origin, href)
    assert sidebar.evaluate("nav => nav.scrollTop") > 0
    assert _shown(sidebar, TARGET)
    assert page.evaluate("window.scrollY") == 0


def test_a_value_saved_for_another_page_is_ignored(page, component_origin):
    """A value left by a cancelled navigation is dropped, not applied."""
    sidebar = _wide(page, component_origin)
    href = sidebar.get_by_role("link", name=TARGET).get_attribute("href")
    stale = json.dumps({"href": href, "top": -5000})
    page.evaluate(f"sessionStorage.setItem('{KEY}', {json.dumps(stale)})")
    page.reload()
    assert sidebar.evaluate("nav => nav.scrollTop") == 0
    assert page.evaluate(f"sessionStorage.getItem('{KEY}')") is None


@pytest.mark.parametrize(
    "value", ["{not json", json.dumps({"href": "http://[", "top": 0}), "null"]
)
def test_a_malformed_saved_value_is_removed_and_harmless(page, component_origin, value):
    """Bad JSON or an unparseable href is dropped; the menu still saves."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    sidebar = _wide(page, component_origin)
    page.evaluate(f"sessionStorage.setItem('{KEY}', {json.dumps(value)})")
    page.reload()
    saved = f"sessionStorage.getItem('{KEY}')"
    assert page.evaluate(saved) is None
    assert sidebar.evaluate("nav => nav.scrollTop") == 0
    # The click handler still attached: a plain click saves.
    page.evaluate("document.addEventListener('click', (e) => e.preventDefault())")
    target = sidebar.get_by_role("link", name=TARGET)
    target.click()
    assert json.loads(page.evaluate(saved))["href"] == target.get_attribute("href")
    assert not failures


def test_only_a_plain_menu_link_activation_saves(page, component_origin):
    """Modified clicks, greyed entries and Sign out save nothing."""
    sidebar = _wide(page, component_origin)
    # Stop every activation from leaving the page. The listener is on the
    # document, so the menu's own listener (on the sidebar) runs first.
    page.evaluate("document.addEventListener('click', (e) => e.preventDefault())")
    saved = f"sessionStorage.getItem('{KEY}')"
    target = sidebar.get_by_role("link", name=TARGET)
    for modifier in ("ControlOrMeta", "Shift", "Alt"):
        target.click(modifiers=[modifier])
        assert page.evaluate(saved) is None, modifier
    # Playwright treats aria-disabled as disabled, so the click is forced.
    sidebar.get_by_role("link", name="Pause and resume mail").click(force=True)
    assert page.evaluate(saved) is None
    sidebar.get_by_role("button", name="Sign out").click()
    assert page.evaluate(saved) is None
    # A plain click does save, so the checks above are not vacuous.
    target.click()
    assert json.loads(page.evaluate(saved))["href"] == target.get_attribute("href")


def test_a_narrow_screen_menu_link_opens_the_page_at_its_top(page, component_origin):
    """On a phone the menu is a disclosure and the page is never scrolled."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(component_origin + PATH)
    sidebar = page.get_by_role("navigation", name="Administration")
    menu = sidebar.locator("details[data-admin-menu]")
    expect(menu).not_to_have_attribute("open", "")
    sidebar.locator("details[data-admin-menu] > summary").click()
    target = sidebar.get_by_role("link", name=TARGET)
    with page.expect_navigation():
        target.click()
    expect(sidebar.locator("details[data-admin-menu]")).not_to_have_attribute(
        "open", ""
    )
    visible(page.locator("main h1"))
    assert page.evaluate("window.scrollY") == 0


def test_the_menu_works_with_storage_blocked(browser_engine, component_origin):
    """Blocked browser storage loses only what the menu would remember."""
    context = browser_engine.new_context(viewport={"width": 1280, "height": 600})
    context.add_init_script(
        """for (const name of ["localStorage", "sessionStorage"]) {
          Object.defineProperty(window, name, {
            configurable: true,
            get() { throw new DOMException("Storage is blocked.", "SecurityError"); },
          });
        }"""
    )
    try:
        page = context.new_page()
        failures = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        page.goto(component_origin + PATH)
        sidebar = page.locator("nav.admin-sidebar")
        # The test really blocks storage.
        assert page.evaluate(
            "(() => { try { sessionStorage; return false; } catch { return true; } })()"
        )
        _follow(page, sidebar)
        visible(page.locator("main h1"))
        # Nothing could be saved, so the new page shows its own entry.
        assert _shown(sidebar, TARGET)
        # Groups still collapse.
        sidebar.locator('details[data-menu-group="mail"] > summary').click()
        hidden(sidebar.get_by_role("link", name="Outgoing mail"))
        assert not failures
    finally:
        context.close()
