"""Families following an emailed link after close see the campaign-ended page."""

import pytest

from .test_family_maintenance import AXE
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_the_ended_page_is_friendly_and_accessible(
    page, component_origin, axe_source, width
):
    """#1793: the ended message, parish content and Check again, no denial text."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/family-ended")
    expect(
        page.get_by_role("heading", name="Sorry, the campaign is over")
    ).to_be_visible()
    expect(
        page.get_by_text("This parish's census / stewardship campaign has ended.")
    ).to_be_visible()
    expect(page.get_by_text("Thank you for taking part.")).to_be_visible()
    expect(page.get_by_role("link", name="Check again")).to_have_attribute("href", "/")
    expect(page.get_by_text("cannot be found or used")).to_have_count(0)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
