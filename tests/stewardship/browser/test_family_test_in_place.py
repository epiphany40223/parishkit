"""Check these Families and Send these Family tests act in place (#519 PR 7c).

The page is served at its real addresses (family_test_components). A route
answers Check with the page the view renders; the fixture server answers
Send with its redirect back to the page. Each test sets a mark outside every
region, which only a full page load could lose, wheels the page down so the
control is below the fold (the mouse wheel, since Firefox can undo a
scripted scroll, #1088), and checks the mark, that the page was not sent to
its top, focus, the announcement and that one POST was sent per click.
"""

import json

import pytest

from .family_test_components import (
    CHECK,
    REFUSED,
    REVIEW,
    REVIEW_LOCAL,
    REVIEW_STALE,
    SEND,
    SENT,
    STALE,
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


def test_a_stale_review_still_steps_up(page, component_origin):
    """A review shown in place with a stale sign-in keeps its step-up: Send
    is unavailable with why, and Confirm with Google is an ordinary
    submission to the sign-in page, never sent in place."""
    open_and_check(page, component_origin, REVIEW_STALE)
    visible(page.get_by_text("sending tests to real Families needs a Google sign-in"))
    assert page.evaluate(MARKED) == "kept"
    assert page.get_by_role("button", name="Send these Family tests").is_disabled()
    with page.expect_request(
        lambda request: (
            request.method == "POST"
            and "/login" in request.url
            and request.is_navigation_request()
        )
    ):
        page.get_by_role("button", name="Confirm with Google").click()


def test_a_local_step_up_in_place_records_its_return(page, component_origin):
    """In LOCAL the step-up is a sign-in link opened by hand, so the page
    records where to return (ui-v1.js). A review swapped in place records it
    too, as one that loaded with the page would."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + CHECK)
    page.evaluate("localStorage.removeItem('pk-local-step-up')")
    answer_check(page, component_origin, REVIEW_LOCAL)
    page.get_by_label("Family DUIDs, one per line (at most ten)").fill("1234")
    page.get_by_role("button", name="Check these Families").click()
    visible(page.locator("#family-test-review [data-local-step-up]"))
    saved = page.evaluate("localStorage.getItem('pk-local-step-up')")
    assert json.loads(saved)["next"] == CHECK


def test_a_send_refused_for_a_stale_sign_in_shows_the_step_up(page, component_origin):
    """A Send refused because the sign-in went stale (403, the step-up page,
    which has no region of this page) is shown whole, as a native submission
    would show it, and was sent once."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + STALE)
    posts = count_requests(page, "POST", STALE)
    page.get_by_label("I understand", exact=False).check()
    page.get_by_role("button", name="Send these Family tests").click()
    visible(page.get_by_role("button", name="Confirm with Google"))
    hidden(page.locator("#family-test-review"))
    assert posts == [component_origin + STALE]


def test_refresh_keeps_families_typed_but_not_checked(page, component_origin):
    """Refresh status replaces only the recent tests (data-in-place-only), so
    DUIDs typed into Check but not yet checked are kept."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + CHECK)
    page.evaluate(MARK)
    field = page.get_by_label("Family DUIDs, one per line (at most ten)")
    field.fill("5678")
    page.get_by_role("link", name="Refresh status").click()
    has_text(
        page.get_by_role("status").filter(has_text="Status refreshed."),
        "Status refreshed.",
    )
    assert field.input_value() == "5678"
    assert page.evaluate(MARKED) == "kept"
