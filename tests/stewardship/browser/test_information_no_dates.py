"""Additional information has no submitted-date filter (#1888).

The Administrator judged a submitted date range not useful, so the page
offers the same kinds of filter as the other review worklists: Apply posts
no dates and no zone, and the export pane carries none either.
"""

from urllib.parse import parse_qs

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

DATE_FIELDS = {"start", "end", "zone"}


def test_the_filters_and_the_export_carry_no_dates(browser_engine, component_origin):
    """No date input, zone field or zone note, and Apply posts none of them."""
    context = browser_engine.new_context(timezone_id="Asia/Kathmandu")
    try:
        page = context.new_page()
        page.goto(component_origin + "/information")
        form = page.locator("#table-filters")
        visible(
            form.get_by_label("Search Family name, DUID, submitted text or Staff notes")
        )
        assert form.locator("input[type=date]").count() == 0
        assert form.locator("[data-browser-zone-note]").count() == 0
        for name in DATE_FIELDS:
            assert page.locator(f"#table-filters [name={name}]").count() == 0
            assert page.locator(f"#table-export [name={name}]").count() == 0
        page.route(
            "**/*",
            lambda route: (
                route.fulfill(body="Filtered")
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Apply filters").click()
        assert not DATE_FIELDS & set(
            parse_qs(sent.value.post_data, keep_blank_values=True)
        )
    finally:
        context.close()
