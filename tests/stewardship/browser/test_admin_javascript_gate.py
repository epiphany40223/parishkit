"""The Admin portal requires JavaScript; the Family portal does not (#565).

Without JavaScript an Admin page, including the sign-in and setup pages, shows
only the plain-language panel and offers nothing to click, focus or submit.
With JavaScript the normal page shows and the panel never does. The template
guard is ``tests/stewardship/test_admin_javascript_gate.py``.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

HEADING = "The Admin portal needs JavaScript."
INSTRUCTION = "Turn it on in your browser settings, then reload this page."
# A dashboard page, a page full of form controls, the sign-in page and a setup
# wizard step.
ADMIN_PAGES = ("/home", "/campaign-settings", "/login", "/setup-shares")
CONTROLS = "a, button, input, select, textarea, summary, [tabindex]"


def _assert_only_panel(page):
    """Only the panel shows: no visible control, form, or focusable element."""
    visible(page.get_by_text(HEADING, exact=True))
    visible(page.get_by_text(INSTRUCTION))
    hidden(page.locator("main"))
    hidden(page.locator(".site-header"))
    controls = page.locator(CONTROLS)
    assert controls.count() > 0
    assert not any(
        controls.nth(index).is_visible() for index in range(controls.count())
    )
    forms = page.locator("form")
    assert not any(forms.nth(index).is_visible() for index in range(forms.count()))
    # Keyboard users cannot reach a hidden control either.
    for _ in range(3):
        page.keyboard.press("Tab")
    assert page.locator(f":is({CONTROLS}):focus").count() == 0


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_page_without_javascript_shows_only_the_panel(
    browser_engine, component_origin, path
):
    """With script off, the panel explains what to do and nothing is usable."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + path)
        _assert_only_panel(page)
    finally:
        context.close()


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_page_with_javascript_shows_the_normal_page(page, component_origin, path):
    """With script on, the class is gone, the page shows and the panel never does."""
    page.goto(component_origin + path)
    visible(page.locator("main"))
    visible(page.locator("main a, main button").first)
    hidden(page.locator(".js-required-panel"))
    # The hidden panel adds no heading, so the page keeps exactly one h1.
    assert page.locator("h1").count() == 1
    assert "js-required" not in (page.locator("html").get_attribute("class") or "")


def test_admin_page_whose_gate_script_fails_still_explains(page, component_origin):
    """A blocked or failed gate script leaves the panel, not a blank page."""
    page.route("**/admin-gate-v1.js", lambda route: route.abort())
    page.goto(component_origin + "/home")
    _assert_only_panel(page)


def test_admin_page_whose_stylesheet_fails_hides_the_panel(page, component_origin):
    """With script on and no stylesheet, the hidden attribute keeps the panel out."""
    page.route("**/ui-v1.css", lambda route: route.abort())
    page.goto(component_origin + "/home")
    visible(page.locator("main"))
    hidden(page.locator(".js-required-panel"))


def test_family_page_without_javascript_is_not_gated(browser_engine, component_origin):
    """The Family portal keeps working without script: no panel, the form shows."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/family-login")
        visible(page.locator("main"))
        visible(page.locator("main form").first)
        assert page.locator(".js-required-panel").count() == 0
    finally:
        context.close()
