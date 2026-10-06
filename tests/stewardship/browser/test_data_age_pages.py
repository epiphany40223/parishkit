"""ParishSoft data age and connection on Home and the ParishSoft page (#510).

Home states "ParishSoft data as of" (with the last full refresh when a quick
update brought newer changes) and the connection line. The ParishSoft
settings page names a scheduled full refresh that is past the lateness
margin and puts "Run a full refresh now" beside that notice. Every time is
the browser's local time (the fixture browser is Pacific); none is labeled
UTC, and nothing scrolls sideways on a phone.
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""


def local_times(page, scope):
    """Every time in ``scope``, which must all be Pacific, never UTC."""
    times = page.locator(f"{scope} time[data-local-instant]")
    texts = [times.nth(index).inner_text() for index in range(times.count())]
    assert texts and all("PDT" in text for text in texts), texts
    return texts


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", ["/home-data-age", "/integration-settings-late"])
def test_data_age_pages_are_accessible_and_fit_a_phone(
    page, component_origin, axe_source, path, width
):
    """No accessibility violation and no sideways scroll."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []


def test_home_states_data_age_and_connection_in_local_time(page, component_origin):
    """Data as of, the last full refresh and the connection, browser-local."""
    page.goto(component_origin + "/home-data-age")
    data = page.locator("main p").filter(has_text="ParishSoft data as of")
    visible(data)
    assert ", last full refresh" in data.inner_text()
    visible(page.locator("main").get_by_text("Connection: working", exact=False))
    # Data as of, last full refresh and last answered.
    assert len(local_times(page, "main section.panel")) >= 3
    assert "UTC" not in page.locator("main").inner_text()
    assert page.get_by_role("link", name="Refresh now").count() == 1


def test_late_refresh_notice_carries_the_run_button(page, component_origin):
    """The late notice names the refresh and offers the run right beside it."""
    page.goto(component_origin + "/integration-settings-late")
    notice = page.locator(".notice-error").filter(has_text="minutes late")
    visible(notice)
    assert "is 120 minutes late" in notice.inner_text()
    (due,) = local_times(page, ".notice-error")
    assert "3:00 AM" in due  # 10:00 UTC (two hours before 12:00), Pacific
    button = notice.get_by_role("button", name="Run a full refresh now")
    visible(button)
    # One button on the page: not repeated below the notice.
    assert page.get_by_role("button", name="Run a full refresh now").count() == 1
    assert "UTC" not in page.locator("main").inner_text()
