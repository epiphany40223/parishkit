"""The Admin "About this page" panel starts open and remembers being closed."""

import pytest

from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def test_about_panel_remembers_closed_and_reopened(page, component_origin):
    """Closing the panel survives a reload; reopening clears the stored choice."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/content-settings")
    panel = page.locator("details[data-about-page]")
    summary = panel.locator("summary")
    placeholders = panel.get_by_text("Use a placeholder name")
    visible(placeholders)
    # Opening the page must not write anything; only closing a panel does.
    assert page.evaluate("localStorage.length") == 0

    summary.click()
    expect(placeholders).to_be_hidden()
    # The choice is saved from the asynchronous "toggle" event.
    eventually(page, "() => localStorage.length", 1)
    page.reload()
    expect(panel).not_to_have_attribute("open", "")
    expect(placeholders).to_be_hidden()

    summary.click()
    visible(placeholders)
    eventually(page, "() => localStorage.length", 0)
    page.reload()
    visible(placeholders)
    assert not failures
