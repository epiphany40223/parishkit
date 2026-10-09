"""Families the form cannot open pages and sorts in place (#774), in Chromium."""

import pytest

from .source_form_components import PATH
from .test_table_sorting import MARK, MARKED, sort_heading
from .waits import has_attribute, has_text

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

FAMILIES = "#table tbody th[scope=row]"


def test_the_list_pages_in_place(page, component_origin):
    """Next refreshes the table in place; the page never reloads."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    assert page.locator(FAMILIES).first.inner_text() == "Family 00"
    page.locator("#table").get_by_role("link", name="Next").first.click()
    has_text(page.locator(FAMILIES).first, "Family 25")
    assert page.evaluate(MARKED) == "kept"


def test_the_list_sorts_in_place(page, component_origin):
    """A heading re-sorts the table in place; the page never reloads."""
    page.goto(component_origin + PATH)
    page.evaluate(MARK)
    page.locator("#table").get_by_role("link", name="Family (sort descending)").click()
    has_text(page.locator(FAMILIES).first, "Family 29")
    has_attribute(sort_heading(page, "family"), "aria-sort", "descending")
    assert page.evaluate(MARKED) == "kept"
