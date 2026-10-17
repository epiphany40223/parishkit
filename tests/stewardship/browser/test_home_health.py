"""Home's System health lines and the banner's tile links (#1214, slice 7).

Home shows each System health problem with its state icon and word and one
link to its tile; a problem Home also reports in its own notice is shown
once; a critical-problems banner group whose problem is current links that
problem's tile. Checked at phone and desktop widths with axe.
"""

import re

import pytest

from .home_steps_components import HEALTH_PATH, SHARED_PATH
from .test_family_response import expect
from .test_home_next_steps import AXE

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_home_lines_and_banner_link_their_tiles(
    page, component_origin, axe_source, width
):
    """Each line reads its state as a word and links its tile once; the
    failed refresh and off-site copy are said once; the banner's current
    group links its tile and an unmapped group does not."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + HEALTH_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    lines = page.locator("[data-home-health] [data-problem]")
    expect(lines).to_have_count(3)
    for index, (word, tile, name) in enumerate(
        [
            ("Error", "mail", "Mail"),
            ("Warning", "source", "ParishSoft refresh"),
            ("Warning", "backups", "Backups"),
        ]
    ):
        line = lines.nth(index)
        expect(line.locator(".status-word")).to_have_text(word)
        expect(line.locator("svg.level-icon")).to_be_visible()
        link = line.get_by_role("link", name=f"See {name} in System health")
        expect(link).to_have_attribute("href", f"/admin/system/health/#health-{tile}")
    # Said once: the System health line, not the refresh block's notice or
    # the off-site error line; the block keeps its link and its button.
    text = page.locator("main").inner_text()
    assert text.count("did not finish") == 1
    assert "Copying the newest backup failed." not in text
    expect(page.get_by_role("link", name="See what went wrong")).to_be_visible()
    expect(page.get_by_role("button", name="Run a full refresh now")).to_have_count(1)
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
def test_home_lists_the_shared_kinds(page, component_origin, axe_source, width):
    """The former page notices are Home lines too (#1214, slice 1): an
    unknown WorkGroup is an error while Reminders are scheduled; an
    unreadable backup key links no tile; off-site copies not set up replace
    Home's invitation to set them up."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + SHARED_PATH)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    lines = page.locator("[data-home-health] [data-problem]")
    expect(lines).to_have_count(4)
    for index, (kind, word, linked) in enumerate(
        [
            ("workgroup_missing", "Error", "ParishSoft refresh"),
            ("backup_key_unchecked", "Warning", None),
            ("backup_request_failed", "Warning", "Backups"),
            ("offsite_missing", "Warning", "Backups"),
        ]
    ):
        line = lines.nth(index)
        expect(line).to_have_attribute("data-problem", kind)
        expect(line.locator(".status-word")).to_have_text(word)
        tile = line.get_by_role("link", name=re.compile("in System health"))
        if linked is None:
            expect(tile).to_have_count(0)
        else:
            expect(tile).to_have_text(f"See {linked} in System health")
    expect(lines.nth(0)).to_contain_text("Reminders are still scheduled")
    expect(lines.nth(0)).to_contain_text("“Do not remind”")
    expect(
        lines.nth(3).get_by_role("link", name="Set up off-site backups")
    ).to_be_visible()
    # Said once: the line, not Home's own invitation.
    expect(page.get_by_role("link", name="Set up off-site backups")).to_have_count(1)
    assert "Encrypted copies of every backup" not in page.locator("main").inner_text()
    assert errors == []
