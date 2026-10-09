"""Census changes pages and sorts in place (#528), in Chromium.

The real page is served by ``census_components``; each POST from a heading
or the navigator is answered with the fixture page its body asks for. A mark
outside every region shows the page never reloaded.
"""

import pytest

from .census_components import PATH
from .test_table_sorting import MARK, MARKED, fulfil_post_with, sort_heading
from .waits import has_attribute, has_text

pytestmark = pytest.mark.parametrize("browser_engine", ["chromium"], indirect=True)

ROWS = "#table tbody th[scope=row]"


def test_census_changes_page_and_sort_in_place(page, component_origin):
    """Next shows the second page and a heading re-sorts, both in place,
    with the filters carried in the POST body and never in the address."""
    page.goto(component_origin + "/census-changes")
    page.evaluate(MARK)
    assert page.locator(ROWS).first.inner_text().startswith("Member 00")
    posts = []

    def choose(request):
        """The fixture page the POST body asks for."""
        posts.append(request.post_data)
        if "sort=-submitted" in request.post_data:
            return "/census-changes-newest"
        return "/census-changes-page-2"

    fulfil_post_with(page, "**" + PATH, component_origin, choose)
    page.locator("#table").get_by_role("button", name="Next").first.click()
    has_text(page.locator(ROWS).first, "Member 25")
    assert "page=2" in posts[-1] and "status=open" in posts[-1]
    page.locator("#table").get_by_role(
        "button", name="Submitted (sort descending)"
    ).click()
    has_attribute(sort_heading(page, "submitted"), "aria-sort", "descending")
    has_text(page.locator(ROWS).first, "Member 29")
    assert page.evaluate(MARKED) == "kept"
    assert "status" not in page.url and page.url.endswith("/census-changes")
