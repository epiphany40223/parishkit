"""Home's Responses tiles with a share bar (#720, #1673).

The page is Home's Next steps component (``home_steps_components``): 400
active Families, 150 of them with a response, and 12 Families who responded
in the past 24 hours. One population only since #1673: the Response
dashboard keeps the choice of Families who can use the form now.
"""

import pytest

from .home_steps_components import PATH
from .test_family_response import expect

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [1280, 390, 320])
def test_responses_are_tiles_with_one_share_bar(page, component_origin, width):
    """Three tiles; only the active Families' share has a bar, hidden from AT."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PATH)
    tiles = page.locator("[data-home-participation] .stat-tile")
    expect(tiles).to_have_count(3)
    expect(tiles.locator(".stat-label")).to_have_text(
        [
            "Active Families",
            "Active Families with a response",
            "Responded in the past 24 hours",
        ]
    )
    expect(tiles.locator(".stat-value")).to_have_text(["400", "150", "12"])
    expect(tiles.nth(1).locator(".stat-detail")).to_have_text("out of 400 (37.5%)")
    bar = page.locator("[data-home-participation] progress")
    expect(bar).to_have_count(1)
    assert bar.evaluate("e => [e.value, e.max]") == [150, 400]
    assert bar.evaluate("e => e.closest('[aria-hidden=true]') !== null")
    # The bar sits inside its tile, at its foot.
    tile = tiles.nth(1).bounding_box()
    box = bar.bounding_box()
    assert (
        tile["y"] < box["y"] and box["y"] + box["height"] <= tile["y"] + tile["height"]
    )
    assert box["width"] > tile["width"] * 0.6
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
