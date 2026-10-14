"""The go-live command buttons act in place (#519 PR 7c-3).

Go-live readiness's Check, Testing cleanup's Retry and Cancel, Prepare Family
links' Prepare, Cancel, Retry and Retry disposal, and Production activation's
Retry failed mail preparation each post to their own page. The pages are
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

from .go_live_components import (
    CLEANUP,
    GO_LIVE,
    LINKS,
    LINKS_DISPOSAL,
    LINKS_NEW,
    PRODUCTION,
    PRODUCTION_RETRIED,
)
from .test_in_place import MARK, MARKED, count_requests
from .waits import available, focused, greyed, hidden, painted, visible

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


def test_check_readiness_is_in_place(page, component_origin):
    """Check readiness posts once and the re-rendered page is swapped in: the
    check's result is read out, focus is back on the fresh Check button, the
    address is unchanged, and the Start Testing cleanup form it brings waits
    for its acknowledgment as on a page that loaded with it."""
    offset, posts = command(
        page,
        component_origin,
        GO_LIVE,
        "Check readiness and public web address",
        "/go-live",
    )
    announced(page, "Readiness checked: every check passed.")
    stayed(page, offset)
    focused(page.get_by_role("button", name="Check readiness and public web address"))
    assert len(posts) == 1 and "action=verify" in posts[0]
    assert page.url == component_origin + GO_LIVE
    start = page.get_by_role("button", name="Start Testing cleanup")
    greyed(start)
    page.get_by_label("I acknowledge", exact=False).check()
    available(start)


def test_check_readiness_announces_a_failed_check(page, component_origin):
    """When the public web address passes but another check fails, Check
    readiness reads out why cleanup can't start yet, never only that the
    address passed, so a screen-reader user is not told readiness passed."""
    offset, posts = command(
        page,
        component_origin,
        GO_LIVE,
        "Check readiness and public web address",
        "/go-live-problems",
    )
    announced(page, "Cleanup can't start yet. Fix the configuration and data checks")
    stayed(page, offset)
    from playwright.sync_api import expect

    expect(page.locator(LIVE)).not_to_contain_text("passed")
    visible(page.get_by_text("The public web address passed", exact=False))
    focused(page.get_by_role("button", name="Check readiness and public web address"))
    assert len(posts) == 1


@pytest.mark.parametrize(
    "name, fixture, message, shown",
    [
        (
            "Retry failed cleanup from its checkpoints",
            "/go-live-cleanup-fresh",
            "Cleanup retry started.",
            "Cleanup is complete.",
        ),
        (
            "Cancel cleanup without restoring deleted data",
            "/go-live-cleanup-cancelled",
            "Cleanup cancel requested.",
            "Cleanup was cancelled",
        ),
    ],
)
def test_cleanup_commands_are_in_place(
    page, component_origin, name, fixture, message, shown
):
    """Testing cleanup's Retry and Cancel post once and swap the progress in;
    the button is gone then, so focus falls back to Refresh cleanup progress
    just below rather than up to the panel's heading."""
    offset, posts = command(page, component_origin, CLEANUP, name, fixture)
    announced(page, message)
    stayed(page, offset)
    visible(page.get_by_text(shown, exact=False))
    hidden(page.get_by_role("button", name=name))
    focused(page.get_by_role("link", name="Refresh cleanup progress"))
    assert len(posts) == 1 and "control=synthetic-" in posts[0]


@pytest.mark.parametrize(
    "address, name, fixture, message, shown",
    [
        (
            LINKS_NEW,
            "Prepare inactive Family links",
            "/go-live-links-started",
            "Link preparation started.",
            "Inactive links are prepared.",
        ),
        (
            LINKS,
            "Cancel and discard these inactive links",
            "/go-live-links-cancelled",
            "Discarding these links.",
            "These links were cancelled",
        ),
        (
            LINKS,
            "Retry failed preparation",
            "/go-live-links-fresh",
            "Preparation retry started.",
            "Inactive links are prepared.",
        ),
        (
            LINKS_DISPOSAL,
            "Retry failed disposal",
            "/go-live-links-disposed",
            "Disposal retry started.",
            "Discarding these links:",
        ),
    ],
)
def test_link_commands_are_in_place(
    page, component_origin, address, name, fixture, message, shown
):
    """Prepare Family links' Prepare and each preparation's Cancel, Retry and
    Retry disposal post once and swap the history in; the button is gone
    then, so focus goes to the history's first heading, the preparation the
    command started or acted on."""
    offset, posts = command(page, component_origin, address, name, fixture)
    announced(page, message)
    stayed(page, offset)
    visible(page.get_by_text(shown, exact=False))
    if name == "Prepare inactive Family links":
        # Prepare stays shown, unavailable now that links are being prepared.
        greyed(page.get_by_role("button", name=name))
    else:
        hidden(page.get_by_role("button", name=name))
    focused(page.locator("[id^='preparation-']").first)
    assert len(posts) == 1 and "control=synthetic-" in posts[0]


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
