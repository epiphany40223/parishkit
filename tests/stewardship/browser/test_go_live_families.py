"""Testing submissions names Families "Surname, heads" and still fits (#932)."""

import pytest

from .go_live_families_components import PATH
from .test_table_sorting import MARK, MARKED, sort_heading
from .waits import has_attribute, has_text

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NAMES = "#table tbody th[scope=row]"


@pytest.mark.parametrize("width", [320, 1280])
def test_long_family_names_fit_the_page(page, component_origin, width):
    """The heads make names long; the page itself never scrolls sideways."""
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + PATH)
    assert page.locator(NAMES).nth(1).inner_text().startswith("Nguyen, ")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_the_name_heading_sorts_in_place(page, component_origin):
    """Family name sorts A-Z, then Z-A, in place; the page never reloads."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    region = page.locator("#table")
    region.get_by_role("link", name="Family name (sort ascending)").click()
    has_attribute(sort_heading(page, "name"), "aria-sort", "ascending")
    has_text(page.locator(NAMES).nth(1), "Abbott, Christopher and Mary-Katherine")
    region.get_by_role("link", name="Family name (sort descending)").click()
    has_text(page.locator(NAMES).first, "Squyres, Tracy and Jeff")
    assert page.evaluate(MARKED) == "kept"
