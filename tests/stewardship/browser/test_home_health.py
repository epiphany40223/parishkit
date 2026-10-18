"""Home's System health count line and the banner's tile links (#1214, #1673).

Since #1673 Home counts the System health problems in one short line with
its state icon and word and one link to System health; the problems'
sentences, the refresh block and the off-site lines are on System health.
A critical-problems banner group whose problem is current links that
problem's tile. Checked at phone and desktop widths with axe.
"""

import pytest

from .home_steps_components import HEALTH_PATH, SHARED_PATH
from .test_family_response import expect
from .test_home_next_steps import AXE

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_home_counts_problems_and_banner_links_tiles(
    page, component_origin, axe_source, width
):
    """One error-look count line links System health; no problem's sentence,
    refresh block or off-site line is repeated on Home; the banner's
    current group links its tile and an unmapped group does not."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + HEALTH_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    line = page.locator("[data-home-health]")
    expect(line).to_have_text("Error 3 system problems. See System health")
    expect(line).to_have_class("notice notice-error")
    expect(line.locator("svg.level-icon")).to_be_visible()
    expect(line.get_by_role("link")).to_have_attribute("href", "/admin/system/health/")
    expect(page.locator("[data-problem]")).to_have_count(0)
    text = page.locator("main").inner_text()
    assert "did not finish" not in text
    assert "Copying the newest backup failed." not in text
    expect(page.get_by_role("button", name="Run a full refresh now")).to_have_count(0)
    banner = page.locator("#critical-events")
    refused = banner.locator("li").filter(
        has_text="ParishSoft refresh refused: too large a change"
    )
    expect(
        refused.get_by_role("link", name="See ParishSoft refresh in System health")
    ).to_have_attribute("href", "/admin/system/health/#health-source")
    task = banner.locator("li").filter(has_text="Background task failed")
    expect(task.get_by_role("link")).to_have_count(0)
    assert errors == []


@pytest.mark.parametrize("width", [320, 1280])
def test_home_counts_the_shared_kinds(page, component_origin, axe_source, width):
    """The former page notices (#1214, slice 1) are counted too, and Home
    drops its own off-site invitation: System health says it. An unreadable
    backup key keeps its own sentence line, and while it shows the count
    links nothing, since System health cannot be shown (#1673)."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + SHARED_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    expect(page.locator("[data-home-health]")).to_have_text("Error 3 system problems.")
    key = page.locator('[data-problem="backup_key_unchecked"]')
    expect(key).to_contain_text("Warning The backup key could not be checked")
    expect(key.locator("a")).to_have_count(0)
    expect(page.locator(".home-alerts a")).to_have_count(0)
    expect(page.get_by_role("link", name="Set up off-site backups")).to_have_count(0)
    assert "Encrypted copies of every backup" not in page.locator("main").inner_text()
    assert errors == []
