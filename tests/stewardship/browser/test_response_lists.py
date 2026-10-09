"""The response lists sort, filter, search and switch mode in place (#477, #849).

The fixtures (``response_list_components``) serve the submitted list and the
exact pages its heading and mode switch lead to. The filter form, the search
and the CSV download are CSRF POSTs; each test answers them itself with a
Playwright route (``answer_posts``) and checks what the form sent, since the
fixture server serves only GET pages.
"""

from urllib.parse import parse_qs

import pytest
from django.urls import reverse

from .response_list_components import (
    BY_FAMILY,
    DATA_QUALITY,
    ENVELOPE_ZERO,
    SEARCHED,
    SEARCHED_BY_FAMILY,
    SEARCHED_ENVELOPE,
    SEARCHED_NONE,
    SUBMITTED,
)
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


def answer_posts(page, origin, path, choose):
    """Answer the list's POSTs at ``path`` with fixture pages; return the bodies.

    ``choose`` maps a parsed POST body to the fixture path whose page is the
    answer. Each answer is read from the fixture server before the route is
    installed, as the view would render it. A GET keeps going to the server.
    """
    pages = {
        fixture: page.request.get(origin + fixture).text()
        for fixture in (SEARCHED, SEARCHED_BY_FAMILY, SEARCHED_ENVELOPE)
        + (SEARCHED_NONE, ENVELOPE_ZERO)
    }
    posted = []

    def answer(route):
        """Record the body and answer with the page the view would render."""
        if route.request.method != "POST":
            route.fallback()
            return
        body = parse_qs(route.request.post_data or "", keep_blank_values=True)
        posted.append(body)
        route.fulfill(status=200, content_type="text/html", body=pages[choose(body)])

    page.route("**" + path, answer)
    return posted


def searched(body):
    """The searched submitted list's page for the POST ``body``."""
    search = body.get("search", [""])[0]
    if search == "101":
        return SEARCHED_ENVELOPE
    if search == "zzz":
        return SEARCHED_NONE
    return SEARCHED_BY_FAMILY if body.get("sort") == ["family"] else SEARCHED


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
    """The filter posts and swaps the table in; the address keeps the choice."""
    errors = watch(page)
    page.goto(component_origin + DATA_QUALITY)
    posted = answer_posts(page, component_origin, DATA_QUALITY, lambda _: ENVELOPE_ZERO)
    page.evaluate(MARK)
    page.get_by_label("Check", exact=True).select_option("envelope")
    page.get_by_role("button", name="Apply").click()
    visible(page.get_by_text("Showing 1–1 of 1").first)
    assert families(page) == ["Diaz, Dee"]
    assert page.evaluate(MARKED) == "kept"
    assert posted[0]["show"] == ["envelope"] and posted[0]["search"] == [""]
    # The address shows the closed choice (data-page-address), so Reload
    # and Back keep it.
    assert page.url == component_origin + DATA_QUALITY + "?show=envelope"
    # The download's scope and hidden choices followed the filter.
    has_text(
        page.locator("#list-export-scope"),
        "The file holds the 1 Family on this list, with the filter chosen.",
    )
    assert hidden_fields(page, "table-export") == {
        "show": "envelope",
        "sort": "family",
    }
    downloads = []

    def download(route):
        """Record the download POST; 204 keeps the page, as an attachment does.

        (Playwright's WebKit renders a route-fulfilled attachment inline, so
        the test answers with No Content rather than a file.)
        """
        downloads.append((route.request.method, route.request.post_data))
        route.fulfill(status=204)

    page.route(
        "**" + reverse("admin:response_list_export", args=["data-quality"]), download
    )
    page.get_by_label("Time zone").select_option("America/Chicago")
    page.get_by_role("button", name="Download", exact=True).click()
    page.wait_for_timeout(500)
    assert len(downloads) == 1 and downloads[0][0] == "POST"
    body = parse_qs(downloads[0][1])
    assert body["show"] == ["envelope"] and body["sort"] == ["family"]
    assert body["timezone"] == ["America/Chicago"]
    assert_clean(page, errors)


def test_search_narrows_in_place_and_never_enters_the_address(page, component_origin):
    """The search posts privately; sort and the download keep it, the URL never.

    Families that submitted has no Show selector (#860), only the search.
    """
    errors = watch(page)
    page.goto(component_origin + SUBMITTED)
    assert page.locator("#list-show").count() == 0
    posted = answer_posts(page, component_origin, SUBMITTED, searched)
    page.evaluate(MARK)
    box = page.get_by_label("Search by Family name, DUID or envelope number")
    box.fill("e")
    box.press("Enter")
    visible(page.get_by_text("Showing 1–2 of 2").first)
    assert families(page) == ["=Baker, Bob", "Evans, Eve"]
    assert page.evaluate(MARKED) == "kept"
    assert posted[-1]["search"] == ["e"]
    assert page.url == component_origin + SUBMITTED
    # The download follows the search, as a hidden POST field.
    has_text(
        page.locator("#list-export-scope"),
        "The file holds the 2 Families on this list that match the search.",
    )
    assert hidden_fields(page, "table-export") == {
        "search": "e",
        "sort": "submitted",
    }
    # A heading is now a POST form carrying the search; the order goes in
    # the address, the search does not.
    page.get_by_role("button", name="Family (sort ascending)").click()
    has_attribute(
        page.locator("#table th[data-sort-column='family']"), "aria-sort", "ascending"
    )
    assert posted[-1]["search"] == ["e"] and posted[-1]["sort"] == ["family"]
    assert families(page) == ["=Baker, Bob", "Evans, Eve"]
    assert page.url == component_origin + SUBMITTED + "?sort=family"
    assert box.input_value() == "e"
    assert page.evaluate(MARKED) == "kept"
    # An envelope number finds that Family; nothing found says so.
    box.fill("101")
    page.get_by_role("button", name="Search").click()
    visible(page.get_by_text("Showing 1–1 of 1").first)
    assert families(page) == ["Adams, Ann"]
    box.fill("zzz")
    page.get_by_role("button", name="Search").click()
    visible(page.locator("#table td").get_by_text("No Families on this list match"))
    assert page.get_by_role("button", name="Download", exact=True).is_disabled()
    assert page.evaluate(MARKED) == "kept"
    for body in posted:
        assert "search" in body
    assert_clean(page, errors)


def test_mode_switch_clears_the_search_box(page, component_origin):
    """The mode link leaves the search behind, and the box shows that."""
    errors = watch(page)
    page.goto(component_origin + SUBMITTED)
    answer_posts(page, component_origin, SUBMITTED, searched)
    box = page.get_by_label("Search by Family name, DUID or envelope number")
    box.fill("e")
    box.press("Enter")
    visible(page.get_by_text("Showing 1–2 of 2").first)
    page.locator('[data-in-place="mode-testing"]').click()
    page.wait_for_url("**?mode=testing#table")
    visible(page.locator("#table").get_by_text("no Testing responses to show"))
    assert box.input_value() == ""
    assert "search" not in hidden_fields(page, "table-export")
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
    assert page.get_by_role("button", name="Download", exact=True).is_disabled()
    # The way back to the dashboard follows the mode chosen.
    dashboard = page.get_by_role("link", name="Response dashboard").get_attribute(
        "href"
    )
    assert dashboard.endswith("/responses/?mode=testing")
    assert_clean(page, errors)


def test_paused_download_is_disabled_with_its_reason(page, component_origin):
    """While the purge gate is closed the button is disabled and says why."""
    page.goto(component_origin + "/response-list-paused")
    visible(page.get_by_text("Downloads are paused while this campaign"))
    assert page.get_by_role("button", name="Download", exact=True).is_disabled()


def test_format_choice_is_kept_in_place_and_posted(page, component_origin):
    """The format (#850) is kept in place, posted, and never moves the button.

    It is a visible choice of the export form, which an in-place refresh
    keeps (only hidden fields are synced), and the button's label names no
    format, so choosing one changes nothing else on the page.
    """
    errors = watch(page)
    page.goto(component_origin + DATA_QUALITY)
    choice = page.get_by_label("Format")
    assert choice.input_value() == "csv"
    assert choice.locator("option").all_inner_texts() == [
        "CSV",
        "XLSX",
        "PDF",
    ]
    button = page.get_by_role("button", name="Download", exact=True)
    before = button.bounding_box()
    page.evaluate(MARK)
    choice.select_option("pdf")
    # Choosing a format changes nothing else on the page (#736).
    assert button.bounding_box() == before
    assert button.inner_text() == "Download"
    # An in-place filter refresh keeps the chosen format.
    answer_posts(page, component_origin, DATA_QUALITY, lambda _: ENVELOPE_ZERO)
    page.get_by_label("Check", exact=True).select_option("envelope")
    page.get_by_role("button", name="Apply").click()
    visible(page.get_by_text("Showing 1–1 of 1").first)
    assert page.evaluate(MARKED) == "kept"
    assert choice.input_value() == "pdf"
    downloads = []

    def download(route):
        """Record the download POST; 204 keeps the page (see above)."""
        downloads.append(parse_qs(route.request.post_data))
        route.fulfill(status=204)

    page.route(
        "**" + reverse("admin:response_list_export", args=["data-quality"]), download
    )
    button.click()
    page.wait_for_timeout(500)
    assert len(downloads) == 1
    assert downloads[0]["format"] == ["pdf"] and downloads[0]["show"] == ["envelope"]
    # The format travels in the POST body only, never the address.
    assert "format" not in page.url
    assert_clean(page, errors)
