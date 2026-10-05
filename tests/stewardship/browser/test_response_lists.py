"""The response lists sort, filter and switch mode in place, and download (#477).

The fixtures (``response_list_components``) serve the submitted list and the
exact pages its heading, filter form and mode switch lead to. The CSV
download is a CSRF POST; each test answers it itself and checks what the
form sent, since the fixture server serves only GET pages.
"""

from urllib.parse import parse_qs

import pytest

from .response_list_components import BY_FAMILY, DATA_QUALITY, SUBMITTED, UNINVITED
from .test_charts import assert_clean, watch
from .waits import has_attribute, has_text, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
})).violations.map(({id, impact, nodes}) => ({
  id, impact, html: nodes.map(node => node.html.slice(0, 300))
}))"""
# A mark outside every region, which only a full page load could lose.
MARK = "document.querySelector('h1').dataset.mark = 'kept'"
MARKED = "document.querySelector('h1').dataset.mark"


def families(page):
    """The Family column (each row's heading cell), in row order."""
    return page.locator("#table tbody th[scope=row]").all_inner_texts()


def hidden_fields(page, form):
    """A form's hidden fields as a dict (the CSRF token left out)."""
    return {
        field.get_attribute("name"): field.input_value()
        for field in page.locator(f"#{form} input[type=hidden]").all()
        if field.get_attribute("name") != "csrfmiddlewaretoken"
    }


@pytest.mark.parametrize("path", [SUBMITTED, DATA_QUALITY, "/response-list-paused"])
def test_list_pages_are_accessible_and_fit_a_phone(
    page, component_origin, axe_source, path
):
    """Help closed, data first, no policy violation, and axe finds nothing."""
    errors = watch(page)
    page.goto(component_origin + path)
    assert not page.locator("details.about-page").evaluate("node => node.open")
    assert page.locator("#table tbody tr").count() > 0
    assert_clean(page, errors)
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_clean(page, errors)


def test_heading_sorts_in_place_and_the_download_follows(page, component_origin):
    """A heading re-sorts without a reload; the download keeps the new order."""
    errors = watch(page)
    page.goto(component_origin + SUBMITTED)
    assert families(page) == ["Adams, Ann", "=Baker, Bob", "Evans, Eve"]
    page.evaluate(MARK)
    with page.expect_request(
        lambda request: "sort=family" in request.url and request.method == "GET"
    ) as fetched:
        page.get_by_role("link", name="Family (sort ascending)").click()
    assert fetched.value.headers.get("x-requested-with") == "fetch"
    has_attribute(
        page.locator("#table th[data-sort-column='family']"), "aria-sort", "ascending"
    )
    assert families(page) == ["=Baker, Bob", "Adams, Ann", "Evans, Eve"]
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + BY_FAMILY + "#table"
    has_text(
        page.get_by_role("status").filter(has_text="Sorted by"),
        "Sorted by Family, ascending",
    )
    assert hidden_fields(page, "table-export") == {"sort": "family"}
    assert hidden_fields(page, "table-filters") == {"size": "50", "sort": "family"}
    assert_clean(page, errors)


def test_filter_applies_in_place_and_the_download_posts_it(page, component_origin):
    """The filter swaps the table in; the download posts the filter and order."""
    errors = watch(page)
    page.goto(component_origin + SUBMITTED)
    page.evaluate(MARK)
    page.get_by_label("Show", exact=True).select_option("uninvited")
    with page.expect_request(lambda request: "show=uninvited" in request.url):
        page.get_by_role("button", name="Apply").click()
    visible(page.get_by_text("Showing 1–1 of 1").first)
    assert families(page) == ["Evans, Eve"]
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + UNINVITED
    # The download's scope and hidden choices followed the filter.
    has_text(
        page.locator("#list-export-scope"),
        "CSV of the 1 Family on this list, with the filter chosen.",
    )
    assert hidden_fields(page, "table-export") == {
        "show": "uninvited",
        "sort": "submitted",
    }
    posted = []

    def download(route):
        """Record the download POST; 204 keeps the page, as an attachment does.

        (Playwright's WebKit renders a route-fulfilled attachment inline, so
        the test answers with No Content rather than a file.)
        """
        posted.append((route.request.method, route.request.post_data))
        route.fulfill(status=204)

    page.route("**/responses/submitted/csv/", download)
    page.get_by_label("Time zone").select_option("America/Chicago")
    page.get_by_role("button", name="Download CSV").click()
    page.wait_for_timeout(500)
    assert len(posted) == 1 and posted[0][0] == "POST"
    body = parse_qs(posted[0][1])
    assert body["show"] == ["uninvited"] and body["sort"] == ["submitted"]
    assert body["timezone"] == ["America/Chicago"]
    assert_clean(page, errors)


def test_mode_switch_refreshes_in_place(page, component_origin):
    """Testing replaces the region, keeps focus on the switch, and says why empty.

    The mode links use the shared in-place link (``data-in-place``, #519), so
    this test needs that ui-v1.js change.
    """
    errors = watch(page)
    page.goto(component_origin + SUBMITTED)
    page.evaluate(MARK)
    link = page.locator('[data-in-place="mode-testing"]')
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**?mode=testing#table")
    visible(page.locator("#table").get_by_text("no Testing responses to show"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement.dataset.inPlace") == ("mode-testing")
    assert page.locator("#table table").count() == 0
    has_text(
        page.locator("#list-export-scope"),
        "Nothing to download: no Families are on this list.",
    )
    assert hidden_fields(page, "table-export") == {
        "mode": "testing",
        "sort": "submitted",
    }
    assert page.get_by_role("button", name="Download CSV").is_disabled()
    # The way back to the dashboard follows the mode chosen.
    dashboard = page.locator("#list-dashboard-link a").get_attribute("href")
    assert dashboard.endswith("/responses/?mode=testing")
    assert_clean(page, errors)


def test_paused_download_is_disabled_with_its_reason(page, component_origin):
    """While the purge gate is closed the button is disabled and says why."""
    page.goto(component_origin + "/response-list-paused")
    visible(page.get_by_text("Downloads are paused while this campaign"))
    assert page.get_by_role("button", name="Download CSV").is_disabled()
