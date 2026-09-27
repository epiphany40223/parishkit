"""Campaign form conveniences: period end dates, overlap and long selections."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_long_multi_selects_scroll_and_count_the_selection(
    page, component_origin, width
):
    """Hundreds of Ministries stay usable: a scroll bar, a count, no page scroll."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/setup-campaign")
    page.get_by_label("Ministry stewardship").check()
    ministries = page.locator('select[name="ministry_duids"]')
    metrics = ministries.evaluate(
        """select => ({
            scroll: select.scrollHeight, client: select.clientHeight,
            overflow: getComputedStyle(select).overflowY,
        })"""
    )
    assert metrics["scroll"] > metrics["client"]
    assert metrics["overflow"] != "hidden"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    count = ministries.locator("xpath=following-sibling::p[@aria-live]")
    assert count.inner_text() == "0 of 213 selected"
    ministries.select_option(["1", "5"])
    assert count.inner_text() == "2 of 213 selected"
    assert "Ctrl" in page.locator("#id_ministry_duids_helptext").inner_text()
