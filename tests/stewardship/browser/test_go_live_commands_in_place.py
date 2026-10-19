"""The go-live command buttons act in place (#519 PR 7c-3).

The Go live page's Prepare step's Retry and Refresh and prepare again
(#1585 slice 3) and Production activation's Retry failed mail preparation
each post to their own page. The pages are
served at their real addresses (go_live_components). A route answers each
POST with the page after the command, as the view's redirect back would show
it; Retry failed mail preparation instead goes through the fixture server's
real 303, so the followed redirect and the replaced address are checked too.

Each test sets a mark outside every region, which only a full page load could
lose, wheels the page so the button is below the fold (the mouse wheel, since
Firefox can undo a scripted scroll, #1088), and checks the mark, that the page
did not jump back to the top, the fresh content, where focus went, the
announcement and that exactly one POST was sent.
"""

import pytest

from .go_live_components import PREPARE, PRODUCTION, PRODUCTION_RETRIED
from .test_in_place import MARK, MARKED, count_requests
from .waits import focused, hidden, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Short enough that every page's command button starts below the fold.
VIEWPORT = {"width": 1280, "height": 320}
# The page's own live region (ui-v1.js), which reads each command's message.
LIVE = "div.visually-hidden[role=status]"


def wheel_to(page, button, *, attempts=20):
    """Wheel the page until ``button`` sits 150 px below the viewport's top,
    or as far as the page scrolls, and holds there; return that scrollY.

    The wheel, not a script: Firefox can re-apply an earlier scroll into view
    and undo a scripted one (#1088). A wheel turn can stop short, and Firefox
    moves at most about a viewport per event, so the page is wheeled until it
    holds across two settled checks.
    """
    where = (
        "node => Math.min(document.documentElement.scrollHeight"
        " - document.documentElement.clientHeight,"
        " scrollY + node.getBoundingClientRect().top - 150)"
    )

    def there():
        """Whether the page already sits where ``where`` puts the button."""
        return abs(button.evaluate(where) - page.evaluate("scrollY")) <= 1

    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 200)
    for _ in range(attempts):
        page.mouse.wheel(0, button.evaluate(where) - page.evaluate("scrollY"))
        painted(page)
        if not there():
            continue
        painted(page)
        if there():
            offset = page.evaluate("scrollY")
            assert offset > 0, "the button must start below the fold for this test"
            return offset
    raise AssertionError("the page did not stay below the button")


def answer_posts(page, origin, address, fixture):
    """Answer every POST to ``address`` with the page at ``fixture``; return
    the list of POSTs answered. The page's own load and reads pass through."""
    posts = []

    def answer(route):
        """Fulfil a POST with the fixture; let everything else through."""
        if route.request.method != "POST":
            route.continue_()
            return
        posts.append(route.request.post_data or "")
        route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))

    page.route(lambda url: url.split("#")[0] == origin + address, answer)
    return posts


def command(page, origin, address, name, fixture):
    """Open ``address``, mark it, wheel to the button named ``name`` and
    choose it, its POST answered with the page at ``fixture``. Returns the
    scroll offset before the click and the POSTs answered."""
    page.set_viewport_size(VIEWPORT)
    page.goto(origin + address)
    page.evaluate(MARK)
    posts = answer_posts(page, origin, address, fixture)
    button = page.get_by_role("button", name=name)
    offset = wheel_to(page, button)
    button.click()
    return offset, posts


def stayed(page, offset):
    """Check that the page did not reload and did not jump back to the top:
    the mark survives, the page sits within a viewport of where the reader
    was (or as far down as a shorter page still goes), and the focused
    element is in view."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    expect(page.locator(LIVE)).not_to_have_text("")
    assert page.evaluate(MARKED) == "kept"
    lowest = page.evaluate(
        "document.documentElement.scrollHeight - document.documentElement.clientHeight"
    )
    assert page.evaluate("scrollY") >= min(offset, lowest) - VIEWPORT["height"]
    assert page.evaluate("scrollY") > 0
    assert page.evaluate(
        "() => { const box = document.activeElement.getBoundingClientRect();"
        " return box.bottom > 0 && box.top < document.documentElement.clientHeight; }"
    )


def announced(page, text):
    """Wait until the page's live region reads ``text`` (and possibly the
    rows now shown after it)."""
    from playwright.sync_api import expect

    expect(page.locator(LIVE)).to_contain_text(text)


@pytest.mark.parametrize(
    "address, name, fixture, message, shown",
    [
        (
            "/go-live-prepare-failed",
            "Retry",
            "/go-live-prepare-refresh",
            "Retry started.",
            "Waiting for the ParishSoft refresh to finish",
        ),
        (
            "/go-live-prepare-again",
            "Refresh and prepare again",
            "/go-live-prepare-refresh",
            "A new ParishSoft refresh was requested.",
            "Waiting for the ParishSoft refresh to finish",
        ),
    ],
)
def test_prepare_commands_are_in_place(
    page, component_origin, address, name, fixture, message, shown
):
    """Prepare's Retry and Refresh and prepare again post once to the step's
    address, follow its redirect back there and swap the step in (#1585
    slice 3): no reload, not sent to the top, the button gone (nothing to do
    while the work runs) and the message read out.

    The step is served at its real address: its first read is the state
    with the button (``address``), every later read the state after the
    command (``fixture``), and the POST goes to the fixture server, which
    redirects back as the view does.
    """
    reads = []

    def states(route):
        """The first read is the state before; later ones the state after."""
        if route.request.method != "GET":
            route.continue_()
            return
        reads.append(route.request.url)
        source = address if len(reads) == 1 else fixture
        route.fulfill(response=route.fetch(url=component_origin + source, method="GET"))

    page.set_viewport_size(VIEWPORT)
    page.route(lambda url: url.split("#")[0] == component_origin + PREPARE, states)
    page.goto(component_origin + PREPARE)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", PREPARE)
    button = page.get_by_role("button", name=name, exact=True)
    offset = wheel_to(page, button)
    button.click()
    announced(page, message)
    stayed(page, offset)
    visible(page.get_by_text(shown, exact=False))
    hidden(page.get_by_role("button", name=name, exact=True))
    assert posts == [component_origin + PREPARE]
    assert page.url.split("#")[0] == component_origin + PREPARE


def test_production_retry_follows_its_redirect_in_place(page, component_origin):
    """Retry failed mail preparation posts once, a double click included,
    follows the view's redirect back to Production activation and swaps it
    in: no reload, not sent to the top, focus on Refresh Production progress
    now that Retry is gone, the message read out, and the address the
    redirect's with the region's fragment."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + PRODUCTION)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", PRODUCTION)
    button = page.get_by_role("button", name="Retry failed mail preparation")
    offset = wheel_to(page, button)
    button.dblclick()
    announced(page, "Mail preparation retry started.")
    stayed(page, offset)
    visible(
        page.get_by_role("heading", name="Initial campaign mail preparation complete")
    )
    hidden(button)
    focused(page.get_by_role("link", name="Refresh Production progress"))
    assert posts == [component_origin + PRODUCTION]
    assert page.url == component_origin + PRODUCTION_RETRIED + "#production-status"
