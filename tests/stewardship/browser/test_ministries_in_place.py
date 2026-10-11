"""Ministries' activity change is reviewed, applied and followed in place (#750).

The real Ministries template is served by ``settings_components`` at
``MINISTRY_TABLE``: a page route answers the selection form's Review POST
with ``MINISTRY_TABLE_REVIEW``, Apply's POST is redirected by the fixture
server to the page naming the change, and a table navigation GET is
answered with the table's second page. Each test sets a mark outside every
region, which only a full page load could lose.
"""

import pytest

from .settings_components import (
    MINISTRY_TABLE,
    MINISTRY_TABLE_PAGE2,
    MINISTRY_TABLE_PENDING,
    MINISTRY_TABLE_REVIEW,
)
from .test_in_place import MARK, MARKED, count_requests
from .test_settings_in_place import FOCUSED, answer_reviews, current_step
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The table region's top edge in the document: focus may scroll the window
# to the review under the table, but nothing may move the table on the page.
TABLE_PLACE = (
    "document.querySelector('[data-table-region]').getBoundingClientRect().top"
    " + window.scrollY"
)
STALE = (
    "You changed the selection after reviewing it. Choose Review activation "
    "or Review inactivation again to see what will change."
)


def row(page, number):
    """The selection checkbox of Ministry ``number``."""
    return page.get_by_label(f"Select Ministry {number:02d}")


def review_first(page, component_origin):
    """Open Ministries, tick Ministry 01 and review inactivating it.

    Returns the Review POST bodies the page route answered.
    """
    reviews = answer_reviews(
        page, component_origin, MINISTRY_TABLE_REVIEW, address=MINISTRY_TABLE
    )
    page.goto(component_origin + MINISTRY_TABLE)
    page.evaluate(MARK)
    row(page, 1).check()
    page.get_by_role("button", name="Review inactivation").first.click()
    visible(page.get_by_role("heading", name="Review your changes"))
    return reviews


def expect_withdrawn(page):
    """The review stays, greyed, without Apply, saying to review again."""
    from playwright.sync_api import expect

    panel = page.locator("#settings-review section[data-review-stale]")
    visible(panel)
    expect(panel.get_by_role("button", name="Apply changes")).to_have_count(0)
    expect(panel.locator("[data-review-note]")).to_have_text(STALE)


def test_review_apply_and_status_happen_in_place(page, component_origin):
    """Review shows the change under the table and takes focus, and Apply
    shows its status there. Nothing reloads, the table never moves on the
    page, the reader's ticks stay, and the indicator follows each step."""
    posts = count_requests(page, "POST", MINISTRY_TABLE)
    page.goto(component_origin + MINISTRY_TABLE)
    top = page.evaluate(TABLE_PLACE)
    reviews = review_first(page, component_origin)
    assert len(reviews) == 1
    assert "ministry_duid=1" in reviews[0] and "active=no" in reviews[0]
    assert page.evaluate(FOCUSED, "#settings-review-title")
    panel = page.locator("#settings-review")
    assert "Inactivate 1 Ministry" in panel.inner_text()
    assert current_step(page) == "Review"
    assert page.url == component_origin + MINISTRY_TABLE
    assert page.evaluate(TABLE_PLACE) == top
    assert row(page, 1).is_checked()
    page.get_by_role("button", name="Apply changes").click()
    visible(panel.get_by_role("heading", name="Change status"))
    assert page.evaluate(FOCUSED, "#settings-review-status-title")
    assert current_step(page) == "Apply"
    assert page.url == f"{component_origin}{MINISTRY_TABLE_PENDING}#settings-review"
    assert page.evaluate(TABLE_PLACE) == top
    assert page.evaluate(MARKED) == "kept"
    assert len(posts) == 2


@pytest.mark.parametrize("edit", ["row", "header", "select-all"])
def test_changing_the_selection_withdraws_the_review(page, component_origin, edit):
    """Ticking another row, the header checkbox or Select all after a review
    withdraws it at once, so Apply can never apply a selection other than
    the one shown; the review region keeps its place."""
    review_first(page, component_origin)
    if edit == "row":
        row(page, 2).check()
    elif edit == "header":
        page.get_by_label("Select every Ministry on this page").check()
    else:
        page.get_by_role("button", name="Select all").click()
    expect_withdrawn(page)
    assert page.evaluate(MARKED) == "kept"


def test_a_tick_while_review_is_in_flight_withdraws_its_review(page, component_origin):
    """A tick made while Review is still running is not in the review that
    answer brings, so that review arrives withdrawn."""
    release = []

    def held(route):
        """Answer Review only once the test has ticked another row."""
        if route.request.method != "POST":
            route.continue_()
            return
        release.append(route)

    page.route(lambda url: url.split("#")[0] == component_origin + MINISTRY_TABLE, held)
    page.goto(component_origin + MINISTRY_TABLE)
    row(page, 1).check()
    page.get_by_role("button", name="Review inactivation").first.click()
    # Route handlers run while the page waits.
    for _ in range(100):
        if release:
            break
        page.wait_for_timeout(50)
    assert len(release) == 1
    row(page, 2).check()
    route = release[0]
    route.fulfill(
        response=route.fetch(url=component_origin + MINISTRY_TABLE_REVIEW, method="GET")
    )
    expect_withdrawn(page)


def test_paging_after_ticking_clears_the_review_without_a_lost_edits_note(
    page, component_origin
):
    """Selection is per page: the next page starts with nothing ticked, and
    its answer draws no review, so the review of the earlier selection goes
    with it. Nothing typed is lost, so no "not kept" line appears."""
    review_first(page, component_origin)

    def second_page(route):
        """The table's second page, as the server draws it for a GET."""
        if route.request.method != "GET":
            route.continue_()
            return
        route.fulfill(
            response=route.fetch(
                url=component_origin + MINISTRY_TABLE_PAGE2, method="GET"
            )
        )

    page.route(
        lambda url: url.startswith(component_origin + MINISTRY_TABLE + "?"),
        second_page,
    )
    row(page, 2).check()
    page.locator(".table-nav a", has_text="Next").first.click()
    visible(row(page, 26))
    assert page.locator("#settings-review").inner_text().strip() == ""
    assert page.locator("[data-edits-lost]").count() == 0
    assert page.evaluate(MARKED) == "kept"
