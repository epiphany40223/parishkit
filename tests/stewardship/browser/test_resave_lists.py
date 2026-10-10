"""Content to re-save on go-live readiness and System health (#838)."""

import pytest

from .go_live_components import RESAVE
from .system_health_components import HEALTHY, PAGE
from .waits import visible

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


def _check_rows(page, container):
    """Each row links to its editor and says whether it can still be sent."""
    rows = page.locator(f"{container} ul.resave-list li")
    expect(rows).to_have_count(2)
    welcome, invitation = RESAVE
    link = rows.nth(0).get_by_role("link", name="Family welcome")
    assert link.get_attribute("href") == welcome["url"]
    assert rows.nth(0).inner_text().endswith("Re-save recommended")
    name = f"Initial invitation: {invitation['subject']}"
    link = rows.nth(1).get_by_role("link", name=name)
    assert link.get_attribute("href") == invitation["url"]
    assert rows.nth(1).inner_text().endswith("Can't be sent until fixed")


def test_go_live_readiness_links_each_page_or_email_to_re_save(page, component_origin):
    """The list sits in the checks panel, with a link to each editor."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/go-live")
    visible(page.get_by_text("These saved pages and emails have markup"))
    _check_rows(page, "#resave-content")
    # The long subject wraps instead of widening the page.
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_system_health_list_stays_put_through_a_poll(page, component_origin):
    """The list is read on page load, outside the polled region, so the
    10-second poll that swaps in the healthy state leaves it untouched."""
    page.goto(component_origin + PAGE)
    summary = page.locator("#resave-summary")
    visible(summary.get_by_text("2 saved pages or emails should be re-saved"))
    visible(summary.get_by_role("link", name="Pages and emails"))
    _check_rows(page, "#resave-summary")
    page.evaluate("document.querySelector('#resave-summary').dataset.original = 'yes'")
    box = summary.bounding_box()
    expect(page.get_by_role("heading", name="Everything is working")).to_be_visible(
        timeout=30000
    )
    # The same element, never rebuilt, still listing both rows.
    assert summary.get_attribute("data-original") == "yes"
    expect(page.locator("#resave-summary ul.resave-list li")).to_have_count(2)
    assert summary.bounding_box()["x"] == box["x"]


def test_system_health_says_when_nothing_needs_re_saving(page, component_origin):
    """With nothing flagged, one sentence says so and no list appears."""
    page.goto(component_origin + HEALTHY)
    summary = page.locator("#resave-summary")
    visible(summary.get_by_text("No saved page or email needs re-saving."))
    expect(summary.locator("ul")).to_have_count(0)
