"""Send a weekly report now is easy to find on Emailed reports (ADM-12.17)."""

import pytest

from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NAME = "Send a weekly report now"


@pytest.mark.parametrize("width", [320, 1280])
def test_send_a_weekly_report_now_is_in_view_on_arrival(
    page, component_origin, axe_source, width
):
    """Above sixty daily reports, the button is in the first screen.

    With a Weekly Admin digest schedule it links the request page.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/emailed-reports")
    button = page.get_by_role("link", name=NAME)
    visible(button)
    assert button.get_attribute("href") == "/admin/reports/emailed/weekly/new/"
    assert button.is_visible() and button.bounding_box()["y"] < 900
    assert page.get_by_role("row").count() > 60
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


@pytest.mark.parametrize("width", [320, 1280])
def test_without_a_weekly_schedule_the_button_says_what_it_needs(
    page, component_origin, axe_source, width
):
    """Unavailable, not hidden: the hint after it links Dates and mail schedules."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/emailed-reports-no-schedule")
    button = page.get_by_role("button", name=NAME)
    visible(button)
    assert button.is_disabled()
    assert button.bounding_box()["y"] < 900
    hint = page.locator("#weekly-now-reason")
    assert button.get_attribute("aria-describedby") == "weekly-now-reason"
    assert "Available once the campaign has a Weekly Admin digest schedule." in (
        hint.inner_text()
    )
    link = hint.get_by_role("link", name="Add one under Dates and mail schedules")
    assert link.get_attribute("href") == "/admin/campaign/schedules/"
    assert page.get_by_role("link", name=NAME).count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
