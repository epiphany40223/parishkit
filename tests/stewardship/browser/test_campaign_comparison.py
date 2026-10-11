"""The Campaign comparison page (#1018) in a browser.

The page is served by ``comparison_components``. It must pass the automated
accessibility checks, fit a phone and a laptop without scrolling sideways,
give each headline tile its full wording as its accessible text, and
explain a "—" with a keyboard-reachable toggletip.
"""

import pytest

from .test_components import axe_violations

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

PAGES = ["/campaign-comparison", "/campaign-comparison-empty"]


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("width", [320, 1280])
def test_page_is_accessible_and_fits(page, component_origin, axe_source, path, width):
    """No axe violation and no sideways page scroll; tables scroll in place."""
    page.set_viewport_size({"width": width, "height": 900})
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + path)
    assert not failures
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_tiles_carry_their_full_wording(page, component_origin):
    """Each tile's accessible text is the whole comparison, arrows and words."""
    page.goto(component_origin + "/campaign-comparison")
    tiles = page.locator(".stat-tile")
    expect(tiles).to_have_count(5)
    families = tiles.first
    expect(families).to_have_attribute(
        "title",
        "Families responded: 270 on day 12. Last year on day 12: 240; this year "
        "is 12.5% more. 2025–2026 average: 230; this year is 17.4% more.",
    )
    expect(families.locator(".stat-detail").first).to_have_text(
        "Last year on day 12: 240 (↑ 12.5% more)"
    )
    # Money keeps its cents; the median sits beside the average.
    expect(tiles.nth(2).locator(".stat-value")).to_have_text("$345,000.50")
    expect(tiles.nth(3)).to_contain_text("Median so far: $1,000.00")


def test_tables_and_notes(page, component_origin):
    """Every number, the pledge bands and escaped data notes are shown."""
    page.goto(component_origin + "/campaign-comparison")
    rows = page.locator("#numbers-caption").locator("xpath=..").locator("tbody tr")
    expect(rows).to_have_count(4)
    expect(rows.first.locator("th")).to_have_text("Stewardship 2027 (so far)")
    # The partial 2024 is unknown on day 12.
    expect(rows.nth(3).locator("td").nth(2)).to_have_text("—")
    expect(
        page.locator("caption", has_text="Stewardship 2027 (so far)")
    ).to_contain_text("median $1,000.00")
    expect(
        page.get_by_text("Converted from <three> invented exports").first
    ).to_be_visible()
    # An Administrator manages the imported years on Past campaigns.
    expect(
        page.get_by_role(
            "link", name="Add, replace or remove imported years on Past campaigns"
        )
    ).to_have_attribute("href", "/admin/parish/past-campaigns/")


def test_an_empty_comparison_says_what_is_missing(page, component_origin):
    """With no imported year the page says so, and shows no pledge tiles."""
    page.goto(component_origin + "/campaign-comparison-empty")
    expect(page.get_by_text("No past campaigns are imported")).to_be_visible()
    expect(
        page.get_by_role("link", name="Import them on Past campaigns")
    ).to_have_attribute("href", "/admin/parish/past-campaigns/")
    expect(page.locator(".stat-tile")).to_have_count(3)
    # The renewal "—" explains itself from the keyboard.
    button = page.locator(".stat-tile .toggletip-button")
    expect(button).to_have_count(1)
    button.focus()
    page.keyboard.press("Enter")
    expect(page.locator(".toggletip-bubble").first).to_have_text("No past year.")
