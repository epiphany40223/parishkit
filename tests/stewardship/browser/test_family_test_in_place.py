"""Check these Families and Send these Family tests act in place (#519 PR 7c).

The page is served at its real addresses (family_test_components). A route
answers Check with the page the view renders; the fixture server answers
Send with its redirect back to the page. Each test sets a mark outside every
region, which only a full page load could lose, wheels the page down so the
control is below the fold (the mouse wheel, since Firefox can undo a
scripted scroll, #1088), and checks the mark, that the page was not sent to
its top, focus, the announcement and that one POST was sent per click.
"""

import pytest

from .family_test_components import (
    CHECK,
    DENIED,
    PENDING,
    PENDING_SETTLED,
    REFUSED,
    REVIEW,
    SEND,
    SENT,
)
from .test_in_place import MARK, MARKED, count_requests
from .waits import eventually, has_text, hidden, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Short enough that the review starts below the fold.
VIEWPORT = {"width": 1280, "height": 320}
FOCUSED = "selector => document.activeElement === document.querySelector(selector)"
CHECK_BUTTON = "#family-test-review form[data-in-place='family-test-check'] button"


def wheel_below(page, selector, *, attempts=5):
    """Wheel the page until ``selector`` sits 150 px below the viewport's top,
    or as far as the page scrolls, and holds there; return that scrollY.

    A wheel turn can stop short, so the page is wheeled until it holds across
    two settled checks.
    """
    where = (
        "Math.min(document.documentElement.scrollHeight"
        " - document.documentElement.clientHeight,"
        f" scrollY + document.querySelector({selector!r}).getBoundingClientRect().top"
        " - 150)"
    )
    there = f"Math.abs(({where}) - scrollY) <= 1"
    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 200)
    for _ in range(attempts):
        page.mouse.wheel(0, page.evaluate(f"({where}) - scrollY"))
        painted(page)
        if not page.evaluate(there):
            continue
        painted(page)
        if page.evaluate(there):
            offset = page.evaluate("scrollY")
            assert offset > 0, "the control must start below the fold for this test"
            return offset
    raise AssertionError(f"the page did not stay below {selector}")


def answer_check(page, origin, fixture, status=200):
    """Answer every POST to CHECK with the page at ``fixture`` (as the view
    renders it, with ``status``); return the list of POSTs answered."""
    posts = []

    def answer(route):
        """Fulfil a POST with the fixture; let the page's own load through."""
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.post_data or "")
        route.fulfill(
            response=route.fetch(url=origin + fixture, method="GET"), status=status
        )

    page.route(lambda url: url.split("#")[0] == origin + CHECK, answer)
    return posts


def open_and_check(page, origin, fixture, status=200):
    """Open CHECK, mark it, wheel to Check, type a DUID and choose Check;
    return the scroll offset before the click and the POSTs answered."""
    page.set_viewport_size(VIEWPORT)
    page.goto(origin + CHECK)
    page.evaluate(MARK)
    posts = answer_check(page, origin, fixture, status)
    page.get_by_label("Family DUIDs, one per line (at most ten)").fill("1234")
    offset = wheel_below(page, CHECK_BUTTON)
    page.get_by_role("button", name="Check these Families").click()
    return offset, posts


def test_check_shows_the_review_in_place(page, component_origin):
    """Check posts once and swaps the review in: no reload, the page not sent
    to its top, focus back on Check, "Families checked." announced, and the
    address unchanged (a POST answer is no address of its own)."""
    offset, posts = open_and_check(page, component_origin, REVIEW)
    has_text(
        page.get_by_role("status").filter(has_text="Families checked."),
        "Families checked.",
    )
    visible(page.get_by_role("heading", name="Reviewed Families"))
    visible(page.get_by_role("button", name="Send these Family tests"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("scrollY") >= offset - 1
    assert page.evaluate(FOCUSED, CHECK_BUTTON)
    assert len(posts) == 1 and "action=preview" in posts[0]
    assert page.url == component_origin + CHECK


def test_a_refused_check_is_shown_in_place(page, component_origin):
    """A list the server refuses (400, a bad DUID) is swapped in like the
    page: no reload, the error beside the field and in the summary, which
    takes focus and is read out, and the reader's place kept."""
    from playwright.sync_api import expect

    _, posts = open_and_check(page, component_origin, REFUSED, status=400)
    summary = page.locator("#family-test-review [data-error-summary]")
    visible(summary)
    eventually(page, FOCUSED, arg="#family-test-review [data-error-summary]")
    visible(page.locator("#family-test-review .errorlist"))
    # The live region reads the summary's message, which names the field.
    expect(page.locator("div.visually-hidden[role=status]")).to_contain_text(
        "Family DUIDs, one per line (at most ten):"
    )
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("scrollY") > 0
    assert len(posts) == 1


def test_send_follows_its_redirect_in_place(page, component_origin):
    """Send posts once (a double click included), follows the redirect back
    to the page and swaps it in: no reload, not sent to the top, the requested
    notice focused and read out now that the review is gone, and the
    address the redirect's."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + SEND)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", SEND)
    page.get_by_label("I understand", exact=False).check()
    wheel_below(page, "#family-test-review form[data-in-place='family-test-send']")
    page.get_by_role("button", name="Send these Family tests").dblclick()
    notice = (
        "1 Family test was requested. Its status appears below and updates by itself."
    )
    # The page's live region (ui-v1.js) reads the fresh notice's own words.
    has_text(page.locator("div.visually-hidden[role=status]"), notice)
    hidden(page.get_by_role("heading", name="Reviewed Families"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("scrollY") > 0
    eventually(page, FOCUSED, arg="#family-test-sent")
    assert posts == [component_origin + SEND]
    assert page.url == component_origin + SENT + "#family-test-review"


def test_a_refused_send_shows_the_error_page_whole(page, component_origin):
    """A Send the server refuses (403, its error page, which has no region
    of this page) is shown whole, as a native submission would show it, and
    was sent once."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + DENIED)
    posts = count_requests(page, "POST", DENIED)
    page.get_by_label("I understand", exact=False).check()
    page.get_by_role("button", name="Send these Family tests").click()
    visible(page.get_by_role("heading", name="Not allowed", level=1))
    hidden(page.locator("#family-test-review"))
    assert posts == [component_origin + DENIED]


def test_recent_tests_update_without_touching_families_typed(page, component_origin):
    """The recent tests update by themselves while a test is on its way, and
    the page has no Refresh status link (#1586). Only their status section
    is replaced, so DUIDs typed into Check but not yet checked are kept."""
    from .test_live_status_in_place import answer_reads, refresh_controls

    page.set_viewport_size(VIEWPORT)
    reads = answer_reads(page, component_origin, PENDING, PENDING_SETTLED)
    page.goto(component_origin + PENDING)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    field = page.get_by_label("Family DUIDs, one per line (at most ten)")
    field.fill("5678")
    hidden(page.get_by_text("Sending Family tests…"))
    assert len(reads) == 1
    assert field.input_value() == "5678"
    assert page.evaluate(MARKED) == "kept"
